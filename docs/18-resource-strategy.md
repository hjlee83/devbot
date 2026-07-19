# Resource Strategy and Token-Budget Model

Architecture document for devbot/devbot#116. Defines the **Execution
Policy**, **Resource Strategy**, and **AI/Token Budget** models behind
[ADR-007](adr/ADR-007-ai-resource-subscription-strategy.md). See ADR-007 for
why these extend Task 041's Role/Agent model rather than replacing it, and
for the rejected alternatives (weighted routing, a single global budget,
overloading `capabilities`).

## Execution Policy

The configuration a Role resolves against - the Goal-grain equivalent of
what Task 041's `RoleRoutingConfig` already does per-Role today, now with
Execution Mode and per-Role budget attached:

```text
Execution Policy
  roles: map of Role -> RoleExecutionPolicy
    RoleExecutionPolicy
      primary: AgentSelection
      fallback: AgentSelection | none
      AgentSelection
        agent               - Task 041 AgentDescriptor.id, unchanged
        execution_mode        - subscription_assisted | subscription_runtime
                                | local_runtime | api | deterministic
                                (new, ADR-007)
  routing_strategy            - "priority" (Task 041's only implemented
                                value; kept as the only required strategy,
                                weighted/performance-based explicitly
                                out of scope per ADR-001 and Issue #116)
```

**Correction (2026-07-20, CTO review on PR #117):** `execution_mode` gained
a fifth value, `subscription_runtime`, and `subscription_assisted`'s
definition tightened - see ADR-007's matching correction for the full
reasoning. The load-bearing consequence for this document: `subscription
_assisted` is valid **only** for a Role's binding at the two human-triggered
checkpoints (`GOAL_PROPOSED`'s conversation, `AUDITING`'s Goal audit -
`docs/17-execution-revision-loop.md`); it is never a legal `execution_mode`
for a call `EXECUTING`/`VERIFYING`/`REVISING` makes, because nothing in the
autonomous loop can wait for a human to be present in a subscription
conversation. A Role that only has a `subscription_assisted` Agent
configured, with no `subscription_runtime`/`local_runtime`/`api` fallback,
simply cannot be dispatched from inside the autonomous loop - this should
be a configuration-time validation error, not a runtime stall.

This is additive over `src/devbot/agent_registry.py`'s existing
`RoleRoutingConfig`/`AgentDescriptor` - an Execution Policy with no
`execution_mode` set for any Agent defaults every Agent to `api` (the
safest, fully-budgeted assumption), so a pre-ADR-007 `config/agents.yaml`
resolves identically to how Task 041 already resolves it, per ADR-007's
Implementation Guidance.

## Resource Strategy

The Goal-attached bundle of Execution Policy plus the axes Issue #116's
Scope requires DevBot to distinguish:

```text
Resource Strategy
  input_channel                - where the Goal's `다음`/`리뷰` commands
                                 arrive from (ChatGPT, Claude, Slack, PWA) -
                                 ADR-003's existing input-channel concept,
                                 recorded for traceability, never used to
                                 select a Role's Agent (ADR-003: "the
                                 originating channel... does not determine
                                 role assignment")
  execution_policy               - Execution Policy (above)
  budget                          - AI/Token Budget (below)
```

A Resource Strategy is attached once at `GOAL_APPROVED`
(`docs/17-execution-revision-loop.md`) and does not change mid-Goal - the
same immutability-after-approval discipline the Goal Specification itself
follows (`docs/15-goal-and-task-graph.md`).

## AI/Token Budget

**Correction (2026-07-20, CTO review on PR #117):** the first version of
this Budget defaulted its Architecture-gate axis to `3` *per node* with no
total ceiling - for a multi-node Goal this reproduces the exact per-Task
review cost the whole redesign exists to reduce. The Architecture gate is
now selective (ADR-005/`docs/16-verification-model.md`'s Invariant
Classification), and the Budget adds an explicit per-Goal total alongside
the per-node limit so "selective" cannot silently become "unbounded in
aggregate." `require_final_goal_audit` is also removed: `AUDITING` is the
mandatory second checkpoint (`docs/17`), not an optional add-on a Budget
could skip.

```text
AI/Token Budget
  max_planner_calls                     - int; AI-assisted PLANNING
                                         invocations before escalating; 0
                                         while PLANNING stays Task
                                         038-style deterministic (the
                                         default today - see ADR-007)
  max_implementation_retries             - int; per Task Graph node, before
                                          escalating (REVISING state)
  max_architecture_review_calls_per_node  - int; Architecture-gate AI
                                            invocations for a single node;
                                            defaults to `0` in Goal mode - a
                                            node spends this only if
                                            PLANNING's Invariant
                                            Classification flagged it
                                            `ai_review_required`
  max_architecture_review_calls_per_goal   - int; total across every node in
                                             the Goal; the ceiling that
                                             keeps per-node selectivity from
                                             becoming unbounded in aggregate
  api_usage                                 - allowed | forbidden
  fallback_activation                        - conditions triggering
                                              primary -> fallback (primary
                                              exhausted its own limit,
                                              errored, or reported
                                              unavailable)
  exhaustion_behavior                         - stop | fallback | escalate
                                               (evaluated identically at
                                               every axis above)
```

**Task-mode legacy default.** A Goal wrapping exactly one Task (or a bare
Task run outside any Goal, today's status quo) keeps
`max_architecture_review_calls_per_node` defaulting to `3` - the existing
`REVIEW_LOOP_LIMIT` (`src/devbot/config.py`,
`devbot.review.DEFAULT_REVIEW_LOOP_LIMIT = 3`) behavior, unchanged, so no
existing single-Task deployment sees different behavior without opting in.
That `3` is a backward-compatibility constant for that one case - the
general Goal-mode default is `0` per node (opt-in via classification) plus
an explicit `max_architecture_review_calls_per_goal` ceiling.

Only `subscription_runtime`, `local_runtime`, and `api` calls (Execution
Policy's `execution_mode`) consume any budget axis - `deterministic` checks
(the Technical gate in full, the Contract gate's structural half, and any
node whose Architecture gate is fully satisfied by deterministic rules) are
free and uncounted. `subscription_assisted` calls (`GOAL_PROPOSED`,
`AUDITING`) are not counted against this Budget either - they are bounded
by human availability, not by a call limit DevBot enforces on itself. This,
combined with the Architecture gate's selectivity, is what makes ADR-005's
gate split actually reduce cost rather than merely relabel or relocate it.

### `exhaustion_behavior` semantics

| Value | Effect |
|---|---|
| `stop` | Goal moves to a blocked-equivalent state, all work preserved (`CONSTITUTION.md` §8) |
| `fallback` | The exhausted Role's fallback Agent (Execution Policy) takes over, Goal continues |
| `escalate` | Goal moves to Goal-level `manual-action` (`docs/17-execution-revision-loop.md`) |

## Configuration examples

The three shapes below are all valid against the same core - none is a
special case in dispatch code, matching Issue #116's Definition of Done
("supports GPT Plus/Codex as one configuration without hard-coding that
combination" and "explains how Claude-first and API-first users configure
the same workflow"):

**Subscription-first (GPT Plus plans, Codex CLI implements)** - DevBot's
actual current default shape. `planner`'s primary Agent has `execution
_mode: subscription_assisted`, but that binding applies only to
`GOAL_PROPOSED`'s design conversation and `AUDITING`'s Goal audit - the
autonomous `PLANNING` state itself needs no execution_mode call at all
(Task 038 is deterministic today). `implementer`'s primary has
`execution_mode: subscription_runtime` (**corrected** from `api` - Codex
CLI under a Plus/Team plan is subscription-backed, not metered, and this is
what Task 011's `CodexRunner` already dispatches today); each Role's
fallback is configured independently, so a Codex outage does not require a
GPT Plus outage to also be true before falling back.

**Claude-first (Claude Pro/Code fills every Role)** - `planner`'s primary
Agent has `execution_mode: subscription_assisted` for the same two
checkpoints as above; `implementer`/`reviewer` use `execution_mode:
subscription_runtime` (Claude Code CLI run non-interactively under a Pro/
Max plan - Task 011's `ClaudeRunner`). `api_usage: forbidden` on the Budget
means the Goal structurally cannot spend metered API credits even if a
Role's fallback chain would otherwise reach for one - a hard ceiling, not a
preference, evaluated the same way `exhaustion_behavior` is.

**API-first (no subscription product involved, e.g. a headless CI-style
deployment)** - `implementer`/`reviewer` have `execution_mode: api`; note
that `planner` still needs a `subscription_assisted` Agent for `GOAL
_PROPOSED`/`AUDITING` regardless of this shape - those two checkpoints are
inherently human-triggered conversations, not something an API-first
deployment can make unattended, since Issue #116's own interaction model
requires a human at both. The Budget's numeric limits matter more here than
in the other two shapes, since every autonomous-loop call has a direct
metered cost.

Full YAML shapes for all three are given in
[ADR-007](adr/ADR-007-ai-resource-subscription-strategy.md#primaryfallback-routing-unchanged-now-role-x-execution-mode).

## Relationship to existing configuration

`config/agents.yaml` (Task 041) remains the file this extends, not a
parallel file - `execution_mode` is a new optional field on the existing
`AgentDescriptor` shape, and `budget` is a new top-level key alongside the
existing `roles` key. A deployment with neither key present resolves
identically to Task 041's current behavior; this is the same
backward-compatibility bar Task 041 itself set for deployments predating
`config/agents.yaml` entirely.
