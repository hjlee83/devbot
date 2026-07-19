# Execution/Revision Loop and State Transitions

Architecture document for devbot/devbot#116. Defines the **Goal State
Machine** referenced by [ADR-006](adr/ADR-006-goal-driven-execution.md),
with an explicit owner, entry condition, output, and failure/escalation
path per transition (Issue #116 Definition of Done). This state machine
sits **above**, and does not replace, the existing per-Issue Task state
machine (`docs/03-state-machine.md`) - every Task Graph node still runs the
unchanged `IDLE -> WORKING -> REVIEW -> ... -> done` flow; what is new is
the aggregate view across every node in a Goal's Task Graph.

**Correction (2026-07-20, CTO review on PR #117):** the first version of
this document modeled `REVIEW_REQUESTED` as automatically queuing a
"subscription-assisted final audit" and `GOAL_ACCEPTED` as a human
personally reading the Completion Report and judging it. Both are wrong for
the same underlying reason: a `subscription_assisted` Agent (ChatGPT Plus,
Claude Pro/Code) cannot be queued or invoked in the background - it only
runs while a human is actually present in that conversation. The agreed UX
is narrower and clearer than either of those: `다음` starts a Goal, `리뷰`
is what actually *runs* the Goal audit - via the conversation agent the
human is already talking to - not a separate thing the human does after an
automatic audit already ran, and not a personal line-by-line read of every
node's evidence. This adds one state, `AUDITING`, between `REVIEW_REQUESTED`
and `GOAL_ACCEPTED`, and changes what those two states mean.

## Architectural Invariant

**Added 2026-07-20, second CTO review round on PR #117**, at the reviewer's
explicit request to pin this as an architecture invariant rather than
leave it as descriptive narrative:

> `다음` means, and only means, Goal approval and start (`GOAL_PROPOSED ->
> GOAL_APPROVED`). `리뷰` means, and only means, the final Goal audit,
> triggered after `REVIEW_REQUESTED` (`AUDITING`). **No other user
> approval point exists in this state machine.** An implementation that
> reintroduces a Task- or Pull-Request-level user approval step - a human
> individually approving, merging, or blocking a specific Task Graph node's
> PR as a required part of the Goal's forward progress, rather than as an
> already-autonomous per-Task mechanism this ADR set reuses unchanged - is
> a violation of this architecture, not a valid alternative implementation
> of it.

This does not forbid a human from *looking at* a Task PR - nothing here
restricts visibility (`CONSTITUTION.md` §7's Traceability requirement is
unaffected either direction) - it forbids making that look a *required
gate* the Goal's forward progress depends on. The existing, unchanged
per-Task autonomous review (`AGENTS.md` §12-13, reused via ADR-005's
Architecture gate) is not this violation, because it was never a human
approval point to begin with - `MERGE READY`/`REQUEST CHANGES` there is
already Agent-executed today, independent of this redesign.

## Summary table

| State | Owner | Entry condition | Output | Failure / escalation |
|---|---|---|---|---|
| `GOAL_PROPOSED` | Human | intent exists | draft Goal Specification | none (may sit indefinitely) |
| `GOAL_APPROVED` | Human ("다음") | Specification complete (DoD, Scope, Non-goals present) | frozen Specification + Resource Strategy + Budget | incomplete Specification -> back to `GOAL_PROPOSED` |
| `PLANNING` | DevBot (Planner, extends Task 038; `deterministic` execution mode today) | `GOAL_APPROVED` | one Goal Execution Plan (Task Graph + Verification Plan + Execution Policy + Resource Strategy + Budget + DoD, `docs/15`) - not a Task Graph alone | `ambiguous` decision -> Goal-level `manual-action`; inconsistent plan (e.g. Budget can't cover its own Verification Plan) -> `manual-action` before `EXECUTING` starts; `already_completed`/`duplicate_open_work` -> short-circuit to `RELEASE_REPORTED`-adjacent no-op |
| `EXECUTING` | DevBot (Goal Executor, extends Task 040 + existing daemon; `subscription_runtime`/`local_runtime`/`api` only) | valid Task Graph, >=1 materializable node | node materialized (Task 040's 3-write set) + existing per-Issue autonomous loop runs | node stuck `blocked`/`manual-action` -> Goal-level `manual-action` if unrecoverable, else `REVISING` |
| `VERIFYING` | DevBot (ADR-005's four gates; Architecture gate only for `ai_review_required` nodes) | a node's Task reaches a review outcome, or all required nodes reach `done` | per-node `gate_results`; Completion Report once all required nodes pass | `FAIL` -> `REVISING`; `RETRY` -> re-run the same gate call, bounded by Budget; `ESCALATE` -> Goal Amendment (below), never silently absorbed as an implementation fix; missing/stale evidence -> re-run the gate, never presented as current |
| `REVISING` | DevBot (existing rework loop, Task 010/027) | `VERIFYING` found a recoverable failure | node returns to `EXECUTING`, then back to `VERIFYING` | Budget exhaustion (ADR-007) -> `exhaustion_behavior` (`stop`/`fallback`/`escalate`) |
| `REVIEW_REQUESTED` | DevBot (automatic) | Goal gate passed for every required node | Completion Report, waiting for `리뷰` | evidence goes stale before `리뷰` arrives -> re-verify affected node, return to `VERIFYING` |
| `AUDITING` | Human triggers ("리뷰"); conversation agent (`subscription_assisted`) performs | `REVIEW_REQUESTED` + human sends `리뷰` | audit verdict (PASS/FAIL/RETRY/ESCALATE) + findings, read from the Completion Report, not a diff re-read | FAIL -> `REVISING` (implementation issue, in-scope); ESCALATE -> Goal Amendment (below, scope issue) |
| `GOAL_ACCEPTED` | Conversation agent's verdict (human present throughout via `리뷰`) | `AUDITING` produced PASS | Goal marked accepted | (none - a FAIL never reaches this state) |
| `RELEASE_REPORTED` | DevBot (reuses Task 037/048-052) | `GOAL_ACCEPTED` | Release Report (GitHub Release, per repository's Task 050 `publish_strategy`) | release-step failure -> Goal-level `manual-action`; does not revert `GOAL_ACCEPTED`, since the work itself is already merged |

## State detail

### GOAL_PROPOSED

A Goal Specification draft exists but has not been approved. Mirrors
`CONSTITUTION.md` §1's "ideas, architecture, scope, risk, and Acceptance
Criteria decided collaboratively... before execution artifacts are
created" - nothing here creates an Issue, Branch, or Contract yet, the same
way today's pre-Planner design conversation creates nothing. A Goal may be
abandoned here with no cleanup required, since no artifact exists.

### GOAL_APPROVED

The human's `다음` command. Entry requires a complete Goal Specification -
this is a deterministic completeness check (Definition of Done present,
Scope and Non-goals both present), not a judgment call, so it can reject
back to `GOAL_PROPOSED` without spending any AI budget. On success, the
Specification is frozen (an amendment after this point is a new proposal,
the same discipline a Task Contract already applies once `devbot:ready`)
and the Resource Strategy and Budget (`docs/18-resource-strategy.md`) are
attached and become the ceiling for every later state.

### PLANNING

**Correction (2026-07-20, second CTO review round on PR #117):** `PLANNING`'s
output is not a Task Graph alone - it is one Goal Execution Plan
(`docs/15-goal-and-task-graph.md`) bundling the Task Graph together with
the Verification Plan, Execution Policy, Resource Strategy, Budget, and
Definition of Done, produced and validated as a single atomic unit. The
first version of this document under-specified this by describing `PLANNING`
as producing "a Task Graph" and treating the other four as independently
-attached Goal fields populated by unspecified other mechanisms - review
flagged that as leaving `PLANNING`'s actual responsibility unclear.

Extends Task 038's `plan_goal`/`fetch_goal_plan` unchanged in decision
logic for the Task Graph portion, persisting the full result as a Goal
Execution Plan instead of returning a plan fresh each call. An `ambiguous`
decision - Task 038's existing fail-closed behavior when the goal string
does not clearly match the capability catalog - escalates to a human
exactly as it does today when a human runs `devbot goal plan` directly; the
only change is that this now happens as an automatic Goal-state transition
instead of a manually-invoked CLI command. `already_completed`/`duplicate
_open_work` are not failures - they are valid planning outcomes that mean
no Task Graph is needed, and the Goal proceeds directly toward
`RELEASE_REPORTED` with an empty graph and that reasoning recorded in the
Completion Report.

`PLANNING` runs unattended - it must never depend on a `subscription
_assisted` call, since nothing in this state can wait for a human to be
present (`docs/adr/ADR-007-ai-resource-subscription-strategy.md`'s
Execution Mode correction). Today this is automatic: Task 038 makes zero
AI/LLM calls, so `PLANNING` is `deterministic` as currently implemented. A
future, more general-purpose decomposition step could use
`subscription_runtime`/`api` assistance, but never `subscription_assisted`.

`PLANNING` also produces each node's Invariant Classification
(`docs/16-verification-model.md`) as part of the Verification Plan - which
of `VERIFYING`'s later Architecture-gate invariants each node can satisfy
with a deterministic rule versus genuinely needs AI judgment for - so that
classification cost is paid once, up front, per Goal, not repeated per node
later. Because the Goal Execution Plan is one atomic unit, `PLANNING` must
validate cross-field consistency before the Goal is allowed into
`EXECUTING` - most importantly, that the Budget's
`max_architecture_review_calls_per_goal` actually covers however many
nodes the Verification Plan just flagged `ai_review_required`. An
inconsistent plan is a `PLANNING`-time `manual-action`, not a problem
discovered midway through `EXECUTING`.

### EXECUTING

Extends Task 040's exact three-write materialization
(`create_issue`/`create_branch`/`create_file`), node by node, in
`depends_on` order, with the per-node `--confirm` gate removed (superseded
by `GOAL_APPROVED` already having happened once for the whole Graph - see
ADR-006). Once a node is materialized and labeled `devbot:ready`, it is
picked up by the existing, completely unchanged daemon loop
(`docs/03-state-machine.md`, Task 012/027's Autonomous Review Loop, B2's
auto-merge gate) - `EXECUTING` does not reimplement any of that, it only
decides *which node materializes next* and *whether the Goal as a whole
should keep going*. A node that lands in the existing `blocked` or
`manual-action` per-Issue state is not automatically a Goal-level failure -
whether it is recoverable (retry, fall back to another Agent per ADR-007)
or requires human attention determines whether the Goal moves to
`REVISING` or escalates.

### VERIFYING

Runs the four ADR-005 gates. In practice this overlaps in time with
`EXECUTING`, not strictly after it - the Technical and Contract gates run
as part of a node's existing Validation Gate and Specification Validator
invocations. The Architecture gate is selective, per its Invariant
Classification from `PLANNING`: a node with `ai_review_required: false`
satisfies it entirely through deterministic rules (no AI call, no
`ReviewReport`); only a node with `ai_review_required: true` runs the
node's existing Task-level review (Task 053/054) for the specific flagged
invariants, recorded as structured evidence attached to the Task Graph node
rather than only living on the PR (`docs/16-verification-model.md`). The
Goal gate alone waits for every required node to finish. Evidence
staleness is treated the same way Task 054 already treats a stale review
head: a gate result is only valid for the exact artifact version it was
computed against, and `VERIFYING` re-runs rather than reuses evidence for a
node whose artifact moved since its last gate result.

Every AI-judgment gate call returns one of the four typed outcomes defined
in `docs/16-verification-model.md`'s "Power boundary": `PASS`, `FAIL`
(routes to `REVISING`), `RETRY` (re-attempt the same call, bounded by
Budget), or `ESCALATE` (routes to Goal Amendment, below - never silently
treated as an implementation problem when the actual finding is that the
approved Specification, an ADR, or the Goal's Scope no longer fits).

### REVISING

Extends the existing rework loop (Task 010's `ReworkService`, Task 027's
autonomous relay) unchanged - a node in `REVISING` returns to its own
Issue/Branch/PR's `rework` state, not a new one. What Goal-level
`REVISING` adds is budget accounting: each rework cycle consumes
`max_implementation_retries` and, only for nodes flagged `ai_review
_required`, `max_architecture_review_calls_per_node`/`_per_goal`
(`docs/18-resource-strategy.md`), generalizing the existing single
hardcoded `REVIEW_LOOP_LIMIT` into per-Goal configured ceilings. Exhausting
budget does not silently keep retrying - it follows the configured
`exhaustion_behavior` exactly as ADR-007 defines it. Every call this state
makes is `subscription_runtime`/`local_runtime`/`api`/`deterministic` -
never `subscription_assisted`, for the same reason as `EXECUTING`.

### REVIEW_REQUESTED

Reached automatically, not by a human command. Entry requires the Goal
gate to have passed for every *required* Task Graph node (optional nodes
may still be incomplete without blocking this). Its only output is the
Completion Report - **no audit is queued or run here.** This state exists
purely so `AUDITING` (below) has something ready to act on the moment a
human sends `리뷰`; if evidence goes stale (a node's artifact moves) while
the Goal is waiting here, the affected node returns to `VERIFYING` rather
than presenting stale evidence when `리뷰` eventually arrives.

### AUDITING

The state a human's `리뷰` command both enters *and drives* - per Issue
#116's own interaction target ("`리뷰` - perform the final Goal audit
**after** the Goal reaches `REVIEW_REQUESTED`"), `리뷰` is not a query
about a Goal already judged; it is what runs the judgment. Entry requires
`REVIEW_REQUESTED`. The conversation agent the human is already talking to
(whichever Role/Agent is configured `subscription_assisted` for this
checkpoint, `docs/18-resource-strategy.md`) reads the Completion Report and
its cited evidence - not a fresh re-read of every node's diff, the same
"aggregate, don't re-derive" discipline the Goal gate itself already
follows - and produces one of the same four typed outcomes
(`docs/16-verification-model.md`): `PASS`, `FAIL`, `RETRY`, or `ESCALATE`.
This is a human-triggered, human-present checkpoint: the human sees the
audit happen in the same conversation they typed `리뷰` into, and retains
the ability to redirect it, but the state transition itself is driven by
the audit's verdict, not a separate manual accept/reject click layered on
top of it. "사람은 audit을 직접 수행하거나 매 PR을 판정하지 않고, 방향
결정과 audit 호출만 담당한다" - the human decides *when* to audit and can
redirect what they see, the conversation agent produces the verdict.

### GOAL_ACCEPTED

Reached by `AUDITING`'s `PASS` verdict, not by a separate human action
beyond having sent `리뷰`. `FAIL` never reaches this state - it routes to
`REVISING`, since a `FAIL` is by definition a finding within the Goal's
already-approved Scope (an implementation problem an already-approved
contract can still resolve). `ESCALATE` never reaches `REVISING` either -
it routes to a Goal Amendment (below), because an `ESCALATE` finding means
the approved Specification, an ADR, or the Scope itself does not fit what
was actually needed, and no gate is authorized to decide that change on
its own (`docs/16-verification-model.md`'s Power boundary).

### Goal Amendment

Not a state in the state machine - a **named sub-flow** any `ESCALATE`
outcome (`VERIFYING` or `AUDITING`) routes into, added at the reviewer's
explicit request so scope-changing findings have an unambiguous, visible
path instead of being informally absorbed into an ordinary re-`PLANNING`
pass. A Goal Amendment proposes a specific, delta-scoped change to the
frozen Goal Specification (`docs/15-goal-and-task-graph.md`) - not a new
Goal, not a silent edit to the existing one - and requires the same kind
of human approval `GOAL_APPROVED` already required for the original Goal,
scoped to just the delta. Concretely: the Goal returns to a
`GOAL_PROPOSED`-equivalent state carrying the `ESCALATE` finding and a
proposed Specification delta; a human either approves the amendment
(re-entering `GOAL_APPROVED` with the amended Specification, from which
`PLANNING` runs again against the new Scope) or rejects it (the Goal ends
without reaching `GOAL_ACCEPTED`, all work preserved per `CONSTITUTION.md`
§8). No gate, and no state in this machine other than a human's explicit
approval, can move a Goal Amendment forward on its own - this is the same
`GOAL_APPROVED`-grade checkpoint as Goal start, applied to a change instead
of a new proposal, and it is the mechanism `docs/adr/ADR-006
-goal-driven-execution.md`'s Architectural Invariant relies on to keep
"decomposition is bounded by the Goal's own approved Scope" true even when
a genuine scope gap is discovered mid-Goal.

### RELEASE_REPORTED

Reuses Task 037/048-052's release machinery entirely unchanged, triggered
automatically instead of by a human's "다음 stable release를 게시해줘"
(Task 037's own operator-UX target). Which publish path runs
(`workflow` or `direct`) is still governed by Task 050's per-repository
`publish_strategy` gate, untouched by this redesign. A failure at this
stage - release classification computing `none`, or a Task 048/049
validation failing - does not revert `GOAL_ACCEPTED`, since the underlying
Tasks are already merged; it escalates to Goal-level `manual-action` scoped
specifically to "release did not complete," the same way a stuck Task
today can be `done` at the Issue level while release remains a separate,
later concern.

## Persistence: an open implementation question

This document defines the state machine's shape, not its storage. Two
candidate approaches exist and are both left to a later implementation
Goal (per Issue #116's "full implementation of the execution engine" being
explicitly out of scope here):

1. A tracking GitHub Issue per Goal, with `devbot:goal-*` labels mirroring
   the existing `devbot:*` per-Task label convention
   (`docs/03-state-machine.md`) - consistent with `AGENTS.md` §16's rule
   that current-state questions are answered from GitHub, not a private
   store.
2. A dedicated Goal store, with Task Graph nodes cross-referencing GitHub
   Issue/PR numbers the way `docs/15-goal-and-task-graph.md`'s `task_ref`
   already assumes.

Whichever is chosen must preserve `CONSTITUTION.md` §7's Traceability
requirement - a Goal's state, and why it transitioned, must remain
inspectable the same way a Task's is today.
