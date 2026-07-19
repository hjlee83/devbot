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

Every `AgentDescriptor` gains an `execution_mode`, one of:

```text
subscription_assisted   - runs inside a channel the user already pays for
                           (ChatGPT Plus, Claude Pro/Code); reasoning cost is
                           sunk, not metered per call by DevBot
local_runtime            - runs on infrastructure DevBot's operator controls
                           directly (a local model, a self-hosted runtime)
api                       - a metered API call DevBot's own credentials pay
                           for per token/request
deterministic             - not an AI call at all (lint, tests, schema
                           validation); zero marginal cost, zero token budget
```

This is the same distinction ADR-003 already drew between "subscription
-assisted mode" and "autonomous mode" for input channels, now attached to
the *execution* side (an Agent filling a Role), not the *input* side (where
a request originated) - the two are independent, per ADR-003's core claim
that "the originating channel... does not determine role assignment."
ADR-005's Technical gate is `deterministic` by definition; its Contract gate
is `deterministic` for structural checks and whichever mode is configured
for its narrow scope-creep judgment; its Architecture gate is
`subscription_assisted`, `local_runtime`, or `api` depending on operator
configuration - never hardcoded to one.

### AI/Token Budget

A Goal (ADR-006) carries a Budget, expressed per axis a Goal can actually
exhaust:

```text
max_planner_calls            - PLANNING invocations before escalating
max_implementation_retries   - per Task Graph node, before escalating
max_ai_review_calls          - Architecture-gate invocations per node
                                (generalizes REVIEW_LOOP_LIMIT from a single
                                hardcoded loop to a per-Goal configured value)
require_final_goal_audit     - bool; whether GOAL_ACCEPTED requires one more
                                subscription-assisted or api review pass
                                even if every node already passed its gates
fallback_activation           - conditions under which primary -> fallback
                                triggers (primary exhausted its own limit,
                                errored, or is unavailable)
api_usage                     - allowed | forbidden (a Goal can require
                                staying entirely subscription_assisted/
                                deterministic, e.g. for a cost-sensitive
                                deployment)
exhaustion_behavior            - stop | fallback | escalate
```

`exhaustion_behavior` is evaluated the same way at every axis: `stop` ends
the Goal in a blocked-equivalent state preserving all work (mirroring
`CONSTITUTION.md` §8's "preservation of work on failure"); `fallback`
switches to the Role's configured fallback Agent and continues; `escalate`
moves the Goal to `manual-action`, the same discipline already used at Task
grain (`docs/03-state-machine.md`).

`deterministic` checks never consume any budget axis - only
`subscription_assisted`, `local_runtime`, and `api` calls do. This is what
makes ADR-005's gate split actually reduce cost rather than just relabel it.

### Primary/Fallback Routing (unchanged, now Role x Execution Mode)

Task 041's `RoleRoutingConfig(name, routing="priority")` and its
`_SUPPORTED_ROUTING_STRATEGIES = frozenset({"priority"})` remain exactly the
routing behavior: within a Role, Agents are tried in priority order.
Execution Mode is an attribute of *which* Agent sits at which priority for a
Role, not a second routing axis - "GPT Plus plans, Codex implements" is
expressed as `planner` role's priority-1 Agent having
`execution_mode: subscription_assisted` (backed by ChatGPT Plus) and
`implementer` role's priority-1 Agent having `execution_mode: api` (backed
by Codex's API), with each Role's own fallback chain configured
independently. No core code branches on "is this the GPT+Codex
configuration" - it only ever reads Role -> priority-ordered Agents ->
Execution Mode, exactly as ADR-001 requires.

```yaml
# example: subscription-first configuration (GPT Plus + Codex)
roles:
  planner:
    primary: { agent: gpt_plus, execution_mode: subscription_assisted }
    fallback: { agent: claude_pro, execution_mode: subscription_assisted }
  implementer:
    primary: { agent: codex, execution_mode: api }
    fallback: { agent: claude_code, execution_mode: subscription_assisted }
  reviewer:
    primary: { agent: codex, execution_mode: api }
    fallback: { agent: gpt_plus, execution_mode: subscription_assisted }
budget:
  max_planner_calls: 3
  max_implementation_retries: 3
  max_ai_review_calls: 3
  require_final_goal_audit: true
  api_usage: allowed
  exhaustion_behavior: escalate

# example: Claude-first configuration
roles:
  planner:   { primary: { agent: claude_pro, execution_mode: subscription_assisted } }
  implementer: { primary: { agent: claude_code, execution_mode: subscription_assisted } }
  reviewer:  { primary: { agent: claude_code, execution_mode: subscription_assisted } }
budget:
  api_usage: forbidden
  exhaustion_behavior: stop

# example: API-first configuration (no subscription product involved)
roles:
  planner:     { primary: { agent: gpt_api, execution_mode: api } }
  implementer: { primary: { agent: claude_api, execution_mode: api } }
  reviewer:    { primary: { agent: claude_api, execution_mode: api } }
budget:
  api_usage: allowed
  max_planner_calls: 5
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
  sensible default) generalizes cleanly into `max_ai_review_calls` instead
  of being reinvented.
- A Goal can be run entirely `subscription_assisted` (a user with only
  ChatGPT Plus and Claude Pro, no API keys) or entirely `api` (a headless
  CI-style deployment) or mixed, with the same core and the same ADR-005
  gates.

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
