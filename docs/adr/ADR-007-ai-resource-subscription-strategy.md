# ADR-007: AI Resource and Subscription-First Strategy

- **Status:** Accepted
- **Date:** 2026-07-20
- **Decision owners:** DevBot Architecture
- **Origin:** devbot/devbot#116

## Context

Task 041 already separates *what role* an Agent fills from *which product*
fills it (`src/devbot/agent_registry.py`): a `RoleRoutingConfig` picks a
priority-ordered `AgentDescriptor` per Role, and dispatch code never
hardcodes a vendor name. ADR-003 already separates *where a request comes
from* (an input channel - ChatGPT, Claude, Slack, PWA) from *what executes
it* (a Role). Neither of these tracks two things devbot/devbot#116 requires:

1. **Execution mode.** An `AgentDescriptor` today has no field distinguishing
   "this Agent runs inside a subscription conversation the user is already
   paying for" from "this Agent is an API call devbot's own credentials
   pay for per token" from "this check is deterministic and costs nothing."
   These have fundamentally different cost profiles and failure modes, but
   the registry treats every entry identically.
2. **Budget.** There is exactly one precedent for bounding AI usage anywhere
   in the codebase: `REVIEW_LOOP_LIMIT` (`src/devbot/config.py`,
   `devbot.review.DEFAULT_REVIEW_LOOP_LIMIT = 3`), a single hardcoded retry
   cap on one loop. There is no general concept of a planner-call limit, an
   implementation-retry limit, an AI-review-call limit, or an
   exhaustion-behavior policy anywhere else. ADR-005 and ADR-006 both
   depend on such a budget existing - "reducing unnecessary AI calls" only
   means something if calls are counted and capped.

ADR-001 already rejected hardcoding one AI combination ("Use one fixed AI
combination... reflects one user's subscription and cost constraints rather
than the product's general purpose"). This ADR has to define a strategy
model expressive enough to configure "ChatGPT Plus plans, Codex implements"
as *one* valid configuration, "Claude Pro plans and reviews, Claude Code
implements" as another, and "everything through metered APIs" as a third -
without any of the three being privileged in the core.

## Decision

Extend, rather than replace, Task 041's Role/Agent model with two new axes -
**Execution Mode** and **Budget** - and formalize **primary/fallback
selection** (already Task 041's only routing strategy, and already ADR-001's
"Implementation Guidance") as the Resource Strategy's one required routing
behavior. Weighted or performance-based routing remain explicitly out of
scope, per both ADR-001 and Issue #116's own "Non-goals."

### Execution Mode

**Correction (2026-07-20, CTO review on PR #117):** the first version of
this ADR treated `subscription_assisted` as one more autonomously-invokable
execution mode, interchangeable with `api`/`local_runtime` wherever a Role
needed a call. That is wrong and was flagged as a blocking review finding:
a ChatGPT Plus or Claude Pro/Code *conversation* cannot be invoked by
DevBot in the background - it only runs while a human is actually present
typing in it. Modeling it as a generic autonomous mode would have let
`EXECUTING`/`VERIFYING`/`REVISING` silently depend on a channel that
structurally cannot answer when DevBot calls it unattended. The corrected
model below distinguishes *who can trigger a call* as a first-class split,
not just *who pays for it*, and adds a fifth mode - `subscription_runtime` -
for the case the first version conflated with `api`: a subscription-backed
CLI tool DevBot **can** invoke unattended (Codex CLI under a ChatGPT Plus/
Team plan, Claude Code CLI run non-interactively under a Claude Pro/Max
plan) - this is DevBot's actual current implementer/reviewer dispatch
today (Task 011's `ClaudeRunner`/`CodexRunner`, both CLI-based), and it was
being mislabeled as metered `api` usage in the first version's own example.

Every `AgentDescriptor` gains an `execution_mode`, one of:

```text
subscription_assisted   - HUMAN-TRIGGERED ONLY. Runs inside a conversation
                           a human is actively present in (ChatGPT Plus,
                           Claude Pro/Code chat/app). DevBot cannot invoke
                           this in the background - it starts exactly when
                           a human utterance ("다음", "리뷰") starts it, and
                           only there. Never a valid execution_mode for a
                           Role call made from EXECUTING/VERIFYING/REVISING's
                           autonomous loop.
subscription_runtime     - autonomously invokable by DevBot, no human needs
                           to be present, billed against a subscription
                           entitlement rather than metered API tokens (Codex
                           CLI under a Plus/Team plan, Claude Code CLI run
                           non-interactively under a Pro/Max plan). This is
                           where DevBot's actual current implementer/
                           reviewer dispatch already lives.
local_runtime             - operator-controlled infrastructure with no
                           vendor subscription involved at all (a
                           self-hosted model). Narrower than the first
                           version of this ADR - a subscription-backed CLI
                           tool is `subscription_runtime`, not this.
api                       - a metered API call DevBot's own credentials pay
                           for per token/request; autonomously invokable.
deterministic             - not an AI call at all (lint, tests, schema
                           validation); zero marginal cost, zero token budget.
```

This is the same distinction ADR-003 already drew between "subscription
-assisted mode" and "autonomous mode" for input channels, now attached to
the *execution* side (an Agent filling a Role), not the *input* side (where
a request originated) - the two are independent, per ADR-003's core claim
that "the originating channel... does not determine role assignment." The
autonomous loop (`PLANNING`/`EXECUTING`/`VERIFYING`/`REVISING`,
`docs/17-execution-revision-loop.md`) may only use `subscription_runtime`,
`local_runtime`, `api`, or `deterministic` - `subscription_assisted` is
reserved exclusively for the two states that are themselves human-triggered
checkpoints: `GOAL_PROPOSED`'s design conversation and `AUDITING`'s Goal
audit, both entered only by a human utterance, never by DevBot deciding to
call out on its own. ADR-005's Technical gate is `deterministic` by
definition; its Contract gate is `deterministic` for structural checks and
`subscription_runtime`/`api` for its narrow scope-creep judgment; its
Architecture gate, per the correction below, is invoked selectively rather
than for every node, and when it is invoked it uses `subscription_runtime`
or `api` - never `subscription_assisted`, since it must run without
requiring a human to be present in a chat.

**`PLANNING` itself needs no execution mode at all today.** Task 038's
`goal_planner.py` makes zero AI/LLM calls - it is pure deterministic
matching against a hand-curated capability catalog. The Goal State
Machine's `PLANNING` state is therefore `deterministic` as currently
implemented; a future, more general-purpose decomposition step (for
arbitrary target repos beyond devbot's own hand-curated catalog) might
need `subscription_runtime`/`api` assistance, but never
`subscription_assisted` - it must still complete without a human present,
the same constraint as every other autonomous-loop call. Do not confuse
this with the human-facing **Planner Role** (`CONSTITUTION.md` §2), which
*is* `subscription_assisted` - that role's conversation happens at
`GOAL_PROPOSED`, before `GOAL_APPROVED`, not inside the autonomous
`PLANNING` state that runs after it.

### AI/Token Budget

**Correction (2026-07-20, CTO review on PR #117):** the first version of
this ADR defaulted `max_ai_review_calls` to `3` *per node*, inherited
directly from `REVIEW_LOOP_LIMIT` with no total cap - for a Goal with 5
Task Graph nodes that is up to 15 Architecture-gate AI calls, which does
not reduce AI/token consumption at all versus today's per-Task review, it
just relocates the same cost under a Goal wrapper. The fix has two parts:
the Architecture gate itself becomes selective rather than mandatory-per
-node (see ADR-005's corrected "Architecture gate" section and
`docs/16-verification-model.md`), and the Budget gains an explicit
per-Goal total ceiling so a selective-but-unbounded gate can't reintroduce
the same problem a different way.

A Goal (ADR-006) carries a Budget, expressed per axis a Goal can actually
exhaust:

```text
max_planner_calls                     - AI-assisted PLANNING invocations
                                        before escalating; 0 is a valid and
                                        expected value while PLANNING stays
                                        Task 038-style deterministic (see
                                        Execution Mode above) - this axis
                                        exists for the future, not today
max_implementation_retries             - per Task Graph node, before
                                        escalating
max_architecture_review_calls_per_node  - Architecture-gate AI invocations
                                          for a single node; defaults to 0 -
                                          a node consumes this only if
                                          PLANNING flagged it as needing
                                          judgment (docs/16's invariant
                                          classification), never by default
max_architecture_review_calls_per_goal   - total Architecture-gate AI
                                           invocations across every node in
                                           the Goal; the ceiling that keeps
                                           "selective per node" from
                                           silently becoming "unbounded in
                                           aggregate"
fallback_activation                       - conditions under which primary
                                           -> fallback triggers (primary
                                           exhausted its own limit, errored,
                                           or is unavailable)
api_usage                                  - allowed | forbidden (a Goal can
                                            require staying entirely
                                            subscription_runtime/
                                            local_runtime/deterministic,
                                            e.g. for a cost-sensitive
                                            deployment)
exhaustion_behavior                         - stop | fallback | escalate
```

`require_final_goal_audit` (present in the first version of this ADR) is
**removed**: `AUDITING` (`docs/17-execution-revision-loop.md`) is no longer
an optional add-on a Budget can skip - it is the mandatory second
checkpoint Issue #116's own interaction model requires ("`리뷰` - perform
the final Goal audit"), so a bypass flag would contradict the two
-checkpoint model this whole redesign exists to deliver.

`exhaustion_behavior` is evaluated the same way at every axis: `stop` ends
the Goal in a blocked-equivalent state preserving all work (mirroring
`CONSTITUTION.md` §8's "preservation of work on failure"); `fallback`
switches to the Role's configured fallback Agent and continues; `escalate`
moves the Goal to `manual-action`, the same discipline already used at Task
grain (`docs/03-state-machine.md`).

`deterministic` checks never consume any budget axis - only
`subscription_runtime`, `local_runtime`, and `api` calls do.
`subscription_assisted` calls (`GOAL_PROPOSED`, `AUDITING`) are not counted
against this Budget either - they are bounded by human availability, not by
a call limit DevBot enforces on itself. This is what makes ADR-005's gate
split, combined with the Architecture gate's selective invocation, actually
reduce cost rather than just relabel or relocate it.

**A Task-mode legacy default exists separately from the Goal-mode
default.** A Goal that wraps exactly one Task (or a bare Task run outside
any Goal at all, today's status quo) keeps `max_architecture_review_calls
_per_node` defaulting to `3` - the existing `REVIEW_LOOP_LIMIT` behavior,
unchanged, so no existing single-Task deployment sees different behavior
without opting in. That default of `3` is a backward-compatibility
constant for that one case, not the general Goal-mode policy - a
multi-node Goal's default is `0` per node (opt-in via PLANNING's
classification) plus an explicit `max_architecture_review_calls_per_goal`
ceiling, exactly to avoid the per-node-times-node-count blowup this
correction exists to close.

### Primary/Fallback Routing (unchanged, now Role x Execution Mode)

Task 041's `RoleRoutingConfig(name, routing="priority")` and its
`_SUPPORTED_ROUTING_STRATEGIES = frozenset({"priority"})` remain exactly the
routing behavior: within a Role, Agents are tried in priority order.
Execution Mode is an attribute of *which* Agent sits at which priority for a
Role, not a second routing axis. The `planner` Role is special only in that
its `subscription_assisted` binding applies to the `GOAL_PROPOSED`
conversation and `AUDITING` (`docs/17-execution-revision-loop.md`), never to
the autonomous `PLANNING` state itself - see the Execution Mode section
above. "GPT Plus plans, Codex implements" (**corrected**, per the CTO
review: Codex CLI under a Plus/Team plan is `subscription_runtime`, not
`api`) is expressed as `planner`'s priority-1 Agent having
`execution_mode: subscription_assisted` and `implementer`'s priority-1 Agent
having `execution_mode: subscription_runtime`, with each Role's own
fallback chain configured independently. No core code branches on "is this
the GPT+Codex configuration" - it only ever reads Role -> priority-ordered
Agents -> Execution Mode, exactly as ADR-001 requires.

```yaml
# example: subscription-first configuration (GPT Plus + Codex CLI) -
# DevBot's actual current default shape (Task 011's ClaudeRunner/CodexRunner
# are both CLI tools run under a subscription plan, not metered API calls)
roles:
  planner:
    # subscription_assisted here binds GOAL_PROPOSED's design conversation
    # and AUDITING's Goal audit - never PLANNING itself, which stays
    # deterministic (Task 038) and needs no execution_mode call at all
    primary: { agent: gpt_plus, execution_mode: subscription_assisted }
    fallback: { agent: claude_pro, execution_mode: subscription_assisted }
  implementer:
    primary: { agent: codex, execution_mode: subscription_runtime }
    fallback: { agent: claude_code, execution_mode: subscription_runtime }
  reviewer:
    primary: { agent: codex, execution_mode: subscription_runtime }
    fallback: { agent: gpt_api, execution_mode: api }
budget:
  max_planner_calls: 0
  max_implementation_retries: 3
  max_architecture_review_calls_per_node: 0
  max_architecture_review_calls_per_goal: 6
  api_usage: allowed
  exhaustion_behavior: escalate

# example: Claude-first configuration
roles:
  planner:   { primary: { agent: claude_pro, execution_mode: subscription_assisted } }
  implementer: { primary: { agent: claude_code, execution_mode: subscription_runtime } }
  reviewer:  { primary: { agent: claude_code, execution_mode: subscription_runtime } }
budget:
  api_usage: forbidden
  exhaustion_behavior: stop

# example: API-first configuration (no subscription product involved,
# e.g. headless CI-style deployment - planner conversation still needs a
# human at GOAL_PROPOSED/AUDITING; nothing here makes that step unattended)
roles:
  planner:     { primary: { agent: gpt_plus, execution_mode: subscription_assisted } }
  implementer: { primary: { agent: claude_api, execution_mode: api } }
  reviewer:    { primary: { agent: claude_api, execution_mode: api } }
budget:
  api_usage: allowed
  max_architecture_review_calls_per_goal: 10
  exhaustion_behavior: fallback
```

All three are valid `config/agents.yaml`-shaped configurations against the
same core, satisfying Issue #116's Definition of Done ("Resource strategy
supports GPT Plus/Codex as one configuration without hard-coding that
combination" and "explains how Claude-first and API-first users configure
the same workflow") without any of the three being a special case in code.

## Consequences

### Positive

- Extends Task 041 in place rather than replacing it - every existing
  deployment without an `execution_mode`/`budget` block keeps dispatching
  exactly as before, the same backward-compatibility discipline Task 041
  itself already established for `config/agents.yaml`.
- `REVIEW_LOOP_LIMIT`'s pattern (a bounded, configurable retry cap with a
  sensible default) generalizes cleanly into `max_architecture_review
  _calls_per_node`/`_per_goal` instead of being reinvented.
- A Goal's autonomous loop can run entirely on `subscription_runtime`
  Agents (a user with only Codex CLI/Claude Code CLI under existing
  subscriptions, no API keys) or entirely `api` (a headless CI-style
  deployment) or mixed - `subscription_assisted` still requires a human
  present at `GOAL_PROPOSED`/`AUDITING` regardless of which of these is
  chosen, since that is a property of the checkpoint, not of the
  deployment's cost preference.

### Negative

- Budget tracking requires the Goal aggregate (ADR-006) to persist call
  counts across `PLANNING`/`EXECUTING`/`REVISING` - stateless recomputation
  (today's Task 038/040 model) cannot enforce a budget that must decrease
  monotonically across separate invocations.
- Execution Mode is operator-declared, not measured - nothing here detects
  that a "subscription_assisted" Agent is actually being metered
  differently by its vendor; this ADR trusts the operator's configuration,
  same as ADR-001 already accepts for capability declarations generally.
- Three configuration shapes (subscription-first, Claude-first, API-first)
  must all be kept valid as the schema evolves - more surface area to keep
  consistent than a single hardcoded path.

## Rejected Alternatives

### Weighted or performance-based routing

Rejected, per ADR-001's existing "Implementation Guidance" and Issue #116's
own "Non-goals": "Weighted and performance-based routing are out of scope."
Priority-ordered primary/fallback, already Task 041's only implemented
strategy, remains the only required routing behavior.

### A single global budget instead of per-Goal

Rejected because a global budget cannot express "this cost-sensitive Goal
must stay `api_usage: forbidden`" alongside "this other Goal may spend
freely" within the same deployment - budgets are a property of the work
being approved (`GOAL_APPROVED`), not of the deployment as a whole.

### Fold Execution Mode into Capability instead of a new axis

Task 041's `AgentDescriptor.capabilities` was considered as the place to
record execution mode. Rejected: `capabilities` is Task 041's admittedly
unused, free-form capability tag (recorded but not read by routing, per
`src/devbot/agent_registry.py`); overloading it with a load-bearing routing
and budget-accounting field would be a second, incompatible meaning for the
same attribute rather than a clean extension.

## Implementation Guidance

`execution_mode` should be added to `AgentDescriptor` as an optional field
defaulting to `api` when absent (the safest assumption - metered and
budget-counted - for any Agent not explicitly declared otherwise), so
existing `config/agents.yaml` files without the field keep working exactly
as Task 041 left them. Budget should live on the Goal aggregate
(`docs/15-goal-and-task-graph.md`), read once at `GOAL_APPROVED` and
decremented through `EXECUTING`/`REVISING`, never recomputed from scratch
mid-Goal.

## Related Decisions

- [ADR-001](ADR-001-devbot-core-philosophy.md) - rejects hardcoding one AI
  combination; this ADR is that rejection made concrete.
- [ADR-003](ADR-003-input-channel-execution-agent-separation.md) - the
  channel/execution-agent split this ADR's Execution Mode axis extends.
- [ADR-005](ADR-005-verification-driven-workflow.md) - the gates whose AI
  calls this Budget bounds.
- [ADR-006](ADR-006-goal-driven-execution.md) - the Goal that owns a Budget.
- `docs/18-resource-strategy.md` - the full Execution Policy/Resource
  Strategy/Budget data model.
