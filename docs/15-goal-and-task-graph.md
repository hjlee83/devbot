# Goal Aggregate and Task Graph

Architecture document for devbot/devbot#116. Defines the **Goal**, **Goal
Specification**, **Definition of Done**, **Task Graph**, and **Completion
Report** models required by that Goal's Scope. See
[ADR-006](adr/ADR-006-goal-driven-execution.md) for why Goal is the
user-facing unit while Task stays the internal execution unit unchanged, and
`docs/17-execution-revision-loop.md` for the Goal State Machine these models
live inside.

## Relationship to existing code

Nothing here replaces Task 038's Goal Planner (`src/devbot/goal_planner.py`)
or Task 040's Goal Executor (`src/devbot/goal_executor.py`) - both are
extended in place:

- Task 038's `GoalDecision` (`already_completed` / `duplicate_open_work` /
  `single_task` / `multi_task` / `ambiguous`) becomes the output of this
  Goal's `PLANNING` state.
- Task 038's `PlannedTask.depends_on` and `dependency_order_is_valid()`
  become the Task Graph's dependency edges, unchanged in meaning, now
  persisted as part of a Goal instead of recomputed fresh on every call.
- Task 040's exact three-write materialization
  (`create_issue`/`create_branch`/`create_file`, no PR, no Agent
  invocation) becomes what `EXECUTING` does per Task Graph node - the only
  change is removing the per-node `--confirm` gate, since `GOAL_APPROVED`
  is now that gate, once, for the whole Graph.

## Goal

The persistent object a human approves once and DevBot advances
automatically. Unlike Task 038/040's stateless recomputation, a Goal's
identity and progress must survive process restarts and multi-day Task
Graphs.

```text
Goal
  id                    - stable identifier (e.g. the originating Issue/
                           request that proposed it)
  title                  - short human-facing summary
  specification          - Goal Specification (below)
  state                   - current Goal State Machine state
                            (docs/17-execution-revision-loop.md)
  task_graph              - Task Graph (below)
  resource_strategy        - Resource Strategy (docs/18-resource-strategy.md)
  budget                   - AI/Token Budget (docs/18-resource-strategy.md)
  verification_plan        - Verification Plan (docs/16-verification-model.md)
  completion_report         - Completion Report (below), present only once
                              REVIEW_REQUESTED is reached
  created_at, updated_at
```

A Goal owns exactly one Task Graph. It does not own Tasks directly - Tasks
are owned the same way they always have been (by their Issue/Branch/
Contract/PR), and the Task Graph is only a typed index over which existing
Tasks belong to this Goal and in what order.

## Goal Specification

The Goal-grain analogue of a Task's Specification (Task 042), and built on
the same principle: [ADR-002](adr/ADR-002-specification-first-architecture.md)'s
"the Specification is the authoritative contract for development work"
applies unchanged at Goal grain. A Goal Specification is what `GOAL_APPROVED`
actually approves, and what `PLANNING`'s decomposition is bounded by (per
ADR-006's "decomposition is bounded by the Goal's own approved Scope").

```text
Goal Specification
  objective              - what the Goal accomplishes and why
  scope                   - what is in bounds; PLANNING may only produce
                            Task Graph nodes that trace to this
  non_goals                - explicit exclusions (mirrors Issue #116's own
                             "Non-goals" section - a Goal Specification
                             without this is incomplete)
  definition_of_done        - Definition of Done (below)
  constraints               - inputs/limits that bound acceptable
                              implementations (mirrors Task Specification's
                              existing "Inputs and constraints",
                              ADR-002)
  verification_requirements   - which of the four ADR-005 gates apply and
                                at what strictness per node (`required` |
                                `advisory`, docs/16-verification-model.md's
                                Verification Plan) - `AUDITING`
                                (docs/17-execution-revision-loop.md) is not
                                configurable here: it is the Goal's
                                mandatory `리뷰` checkpoint, not one more
                                gate a Goal can opt out of
```

Unlike Task 042's Specification, a Goal Specification does not have a fixed
8-section rendering format of its own - it is deliberately kept as a plain
data model here, because Issue #116 explicitly scopes this Goal to
architecture and contracts, not to a Goal-level document generator (that
would duplicate Task 042/044's machinery at a second grain; whether it is
worth building a `devbot goal specification generate` analogous to `devbot
specification generate` is left to a later implementation Goal).

## Definition of Done

A Goal's Definition of Done is **not** folded into Acceptance Criteria the
way it is at Task grain today (Task 042's alias table treats "Definition of
Done" as a legacy heading synonym for Contract content, with no first-class
model - confirmed against `src/devbot/specification.py`). At Goal grain it
is promoted to its own explicit, checkable model, because the Goal gate
(ADR-005) needs something concrete to evaluate deterministically:

```text
Definition of Done
  criteria: list of
    description            - human-readable condition
    verifiable_by            - which gate proves it (technical | contract |
                               architecture | goal-aggregate)
    required                  - bool; an unmet required criterion blocks
                                REVIEW_REQUESTED, an unmet optional one is a
                                non-blocking finding in the Completion Report
```

This is intentionally the same shape as Task Contract's existing Acceptance
Criteria (`docs/09-task-contract-standard.md`) - a list of checkable
conditions, not free text - so the same "checkpoint has evidence" discipline
(`docs/06-review-policy.md`'s checkpoint-to-test table) generalizes upward
instead of being reinvented.

## Task Graph and dependencies

```text
Task Graph
  nodes: list of
    task_ref                - the real Task's Contract path / Issue number,
                              once materialized (null before EXECUTING
                              creates it)
    order                     - Task 038's existing `order` field, unchanged
    depends_on                 - Task 038's existing `depends_on` tuple,
                                unchanged: dependency order must be valid
                                per `dependency_order_is_valid()`
    status                      - not_materialized | materializing | ready |
                                  in_review | rework | blocked | manual_action
                                  | done (mirrors docs/03-state-machine.md's
                                  Issue-label states, kept in sync with them
                                  rather than duplicating a second source of
                                  truth - see docs/17)
    gate_results                 - per-gate evidence once VERIFYING has run
                                  for this node (docs/16)
    required                     - bool; mirrors Definition of Done's
                                    `required` flag at node grain - a Goal
                                    may include optional, best-effort Tasks
                                    that do not block REVIEW_REQUESTED
```

A Task Graph is a DAG, not merely an ordered list - Task 038's current
`depends_on`/`order` pair already validates a linear ordering consistent
with a DAG constraint (`dependency_order_is_valid()`,
`src/devbot/goal_planner.py`); this model keeps that validation and adds
per-node status/evidence tracking that Task 038 never needed because it was
read-only. Materializing a node still goes through Task 040's exact
three-write path node by node, in dependency order, never skipping ahead of
an unmet dependency - the same "first not-yet-materialized task only" rule
Task 040 already enforces for its single-node case, now applied across the
whole Graph instead of requiring a human to invoke it once per node.

## Completion Report

The Goal gate's evidence artifact (ADR-005's evidence contract table). Not
a new judgment - a deterministic aggregation of every required node's
already-produced gate results (`docs/16-verification-model.md`), the same
"aggregate, don't re-derive" principle Task 052's release recommendation
aggregation already uses for merged Task PRs.

```text
Completion Report
  goal_id
  definition_of_done_results      - each DoD criterion -> met/unmet +
                                    citation to the node/gate that proves it
  nodes: list of
    task_ref, final_status, gate_results (as recorded on the Task Graph node)
  unresolved_findings              - any blocking finding across any
                                     required node's gate_results; non-empty
                                     means REVIEW_REQUESTED cannot be reached
  release_readiness                 - whether Task 037/048-052's release
                                      machinery can run against this Goal's
                                      merged Tasks (reused unchanged, see
                                      docs/17)
```

This is what the conversation agent reads at `AUDITING`, triggered by the
human's `리뷰` command (`docs/17-execution-revision-loop.md`), instead of
re-reading every Task PR's diff - and what the human sees alongside it in
that same conversation. Its legibility is load-bearing for the whole
two-checkpoint model, not a nice-to-have: `AUDITING`'s PASS/FAIL verdict is
only as trustworthy as this report is complete.

## Non-goals of this document

Building a persistence layer, a CLI (`devbot goal ...` beyond what Task
038/040 already expose), or a rendering format for any of the above is
explicitly out of scope - per devbot/devbot#116's own "full implementation
of the execution engine in this Goal" Non-goal. This document defines the
shape a later implementation Goal must satisfy.
