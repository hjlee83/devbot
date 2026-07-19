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
        execution_mode        - subscription_assisted | local_runtime |
                                api | deterministic (new, ADR-007)
  routing_strategy            - "priority" (Task 041's only implemented
                                value; kept as the only required strategy,
                                weighted/performance-based explicitly
                                out of scope per ADR-001 and Issue #116)
```

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

```text
AI/Token Budget
  max_planner_calls                - int; PLANNING invocations before
                                     escalating (ADR-006's PLANNING state)
  max_implementation_retries         - int; per Task Graph node, before
                                       escalating (REVISING state)
  max_ai_review_calls                 - int; Architecture-gate invocations
                                        per node - generalizes the existing
                                        single hardcoded `REVIEW_LOOP_LIMIT`
                                        (`src/devbot/config.py`,
                                        `devbot.review.
                                        DEFAULT_REVIEW_LOOP_LIMIT = 3`) from
                                        one loop to a per-Goal configured
                                        value; a Goal with no explicit value
                                        should default to the same `3` so
                                        existing Task-level behavior is
                                        unchanged for any Goal wrapping a
                                        single Task
  require_final_goal_audit             - bool; whether REVIEW_REQUESTED
                                         (docs/17) runs one bounded final
                                         audit pass before presenting to
                                         the human
  api_usage                             - allowed | forbidden
  fallback_activation                    - conditions triggering primary ->
                                          fallback (primary exhausted its
                                          own limit, errored, or reported
                                          unavailable)
  exhaustion_behavior                     - stop | fallback | escalate
                                           (evaluated identically at every
                                           axis above)
```

Only `subscription_assisted`, `local_runtime`, and `api` calls (Execution
Policy's `execution_mode`) consume any budget axis - `deterministic` checks
(the Technical gate in full, the Contract gate's structural half) are free
and uncounted, which is what makes ADR-005's gate split actually reduce
cost rather than merely relabel where the cost is spent.

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

**Subscription-first (GPT Plus plans, Codex implements)** - `planner`'s
primary Agent has `execution_mode: subscription_assisted`, `implementer`'s
primary has `execution_mode: api`; each Role's fallback is configured
independently, so a Codex outage does not require a GPT Plus outage to also
be true before falling back.

**Claude-first (Claude Pro/Code fills every Role)** - every Role's primary
Agent has `execution_mode: subscription_assisted`, and `api_usage:
forbidden` on the Budget means the Goal structurally cannot spend metered
API credits even if a Role's fallback chain would otherwise reach for one -
`api_usage: forbidden` is a hard ceiling, not a preference, evaluated the
same way `exhaustion_behavior` is.

**API-first (no subscription product involved, e.g. a headless CI-style
deployment)** - every Role's primary Agent has `execution_mode: api`, and
the Budget's numeric limits matter more here than in the other two shapes,
since every call has a direct metered cost.

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
