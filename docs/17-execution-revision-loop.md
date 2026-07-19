# Execution/Revision Loop and State Transitions

Architecture document for devbot/devbot#116. Defines the **Goal State
Machine** referenced by [ADR-006](adr/ADR-006-goal-driven-execution.md),
with an explicit owner, entry condition, output, and failure/escalation
path per transition (Issue #116 Definition of Done). This state machine
sits **above**, and does not replace, the existing per-Issue Task state
machine (`docs/03-state-machine.md`) - every Task Graph node still runs the
unchanged `IDLE -> WORKING -> REVIEW -> ... -> done` flow; what is new is
the aggregate view across every node in a Goal's Task Graph.

## Summary table

| State | Owner | Entry condition | Output | Failure / escalation |
|---|---|---|---|---|
| `GOAL_PROPOSED` | Human | intent exists | draft Goal Specification | none (may sit indefinitely) |
| `GOAL_APPROVED` | Human ("다음") | Specification complete (DoD, Scope, Non-goals present) | frozen Specification + Resource Strategy + Budget | incomplete Specification -> back to `GOAL_PROPOSED` |
| `PLANNING` | DevBot (Planner, extends Task 038) | `GOAL_APPROVED` | Task Graph (nodes, `order`, `depends_on`) | `ambiguous` decision -> Goal-level `manual-action`; `already_completed`/`duplicate_open_work` -> short-circuit to `RELEASE_REPORTED`-adjacent no-op |
| `EXECUTING` | DevBot (Goal Executor, extends Task 040 + existing daemon) | valid Task Graph, >=1 materializable node | node materialized (Task 040's 3-write set) + existing per-Issue autonomous loop runs | node stuck `blocked`/`manual-action` -> Goal-level `manual-action` if unrecoverable, else `REVISING` |
| `VERIFYING` | DevBot (ADR-005's four gates) | a node's Task reaches a review outcome, or all required nodes reach `done` | per-node `gate_results`; Completion Report once all required nodes pass | recoverable gate failure -> `REVISING`; missing/stale evidence -> re-run the gate, never presented as current |
| `REVISING` | DevBot (existing rework loop, Task 010/027) | `VERIFYING` found a recoverable failure | node returns to `EXECUTING`, then back to `VERIFYING` | Budget exhaustion (ADR-007) -> `exhaustion_behavior` (`stop`/`fallback`/`escalate`) |
| `REVIEW_REQUESTED` | DevBot (automatic) | Goal gate passed for every required node | Completion Report, optional final AI audit queued | evidence goes stale before human reviews -> re-verify affected node, return to `VERIFYING` |
| `GOAL_ACCEPTED` | Human ("리뷰") | human reviews Completion Report (+ optional final audit) | Goal marked accepted | human rejects -> `REVISING` (implementation issue) or `PLANNING` (scope issue) |
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

Extends Task 038's `plan_goal`/`fetch_goal_plan` unchanged in decision
logic, persisting its output as a Task Graph
(`docs/15-goal-and-task-graph.md`) instead of returning it fresh each call.
An `ambiguous` decision - Task 038's existing fail-closed behavior when the
goal string does not clearly match the capability catalog - escalates to a
human exactly as it does today when a human runs `devbot goal plan`
directly; the only change is that this now happens as an automatic Goal
-state transition instead of a manually-invoked CLI command. `already_
completed`/`duplicate_open_work` are not failures - they are valid planning
outcomes that mean no Task Graph is needed, and the Goal proceeds directly
toward `RELEASE_REPORTED` with an empty graph and that reasoning recorded
in the Completion Report.

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
invocations, and the Architecture gate *is* the node's existing Task-level
review (Task 053/054), just now recorded as structured evidence attached
to the Task Graph node rather than only living on the PR. The Goal gate
alone waits for every required node to finish. Evidence staleness is
treated the same way Task 054 already treats a stale review head: a
gate result is only valid for the exact artifact version it was computed
against, and `VERIFYING` re-runs rather than reuses evidence for a node
whose artifact moved since its last gate result.

### REVISING

Extends the existing rework loop (Task 010's `ReworkService`, Task 027's
autonomous relay) unchanged - a node in `REVISING` returns to its own
Issue/Branch/PR's `rework` state, not a new one. What Goal-level
`REVISING` adds is budget accounting: each rework cycle consumes
`max_implementation_retries`/`max_ai_review_calls`
(`docs/18-resource-strategy.md`), generalizing the existing single hardcoded
`REVIEW_LOOP_LIMIT` into a per-Goal configured ceiling. Exhausting budget
does not silently keep retrying - it follows the configured
`exhaustion_behavior` exactly as ADR-007 defines it.

### REVIEW_REQUESTED

Reached automatically, not by a human command - this is the state a human's
`리뷰` command finds the Goal already in, per Issue #116's own interaction
target ("`리뷰` - perform the final Goal audit **after** the Goal reaches
`REVIEW_REQUESTED`"). Entry requires the Goal gate to have passed for every
*required* Task Graph node (optional nodes may still be incomplete without
blocking this). If `require_final_goal_audit` is set
(`docs/18-resource-strategy.md`), one bounded subscription-assisted or API
audit pass runs here, using the Completion Report as its input rather than
re-reading every node's diff - the same "aggregate, don't re-derive"
discipline as the Goal gate itself, just with a human-quality second look
before presenting to the actual human.

### GOAL_ACCEPTED

The human's `리뷰` command's outcome. The human reviews the Completion
Report (and any final audit result) and accepts or rejects. A rejection is
not a dead end - it routes to `REVISING` if the human's objection is about
implementation quality within the existing Scope, or back to `PLANNING`
if it reveals the Task Graph itself needs to change (which, per ADR-006,
may in turn require a new `GOAL_APPROVED` if the needed change is actually
a scope change, not merely a different decomposition of the same scope).

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
