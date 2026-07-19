# ADR-006: Goal-Driven Execution and Goal-Level Interaction

- **Status:** Accepted
- **Date:** 2026-07-20
- **Decision owners:** DevBot Architecture
- **Origin:** devbot/devbot#116

## Context

DevBot's current interaction unit is the Task: the project owner identifies
one piece of work, a Planner converts it into one Issue/Branch/Contract/PR
(`CONSTITUTION.md` §2-3), and the owner separately triggers and checks each
Task ("Task-XXX 수행해", "Review PR #N", `AGENTS.md` §14). Most of the
*execution* of a single Task is already unattended - `AGENTS.md` §13's
Autonomous Review Loop and B2's automatic-merge safety gate mean a Task, once
`devbot:ready`, can reach `devbot:done` without a human touching it again.
What is not unattended is the *sequencing*: a human still decides, and says
out loud, which Task runs next, and still separately checks in on each PR's
review outcome. For a multi-Task piece of work this means one human
touchpoint per Task, not one per unit of intent.

Two building blocks already exist that this decision builds directly on top
of, without replacing:

- Task 038's Goal Planner (`src/devbot/goal_planner.py`) already decomposes a
  Goal string into a `single_task` / `multi_task` / `already_completed` /
  `duplicate_open_work` / `ambiguous` decision, purely deterministically
  against a hand-curated capability catalog and the real roadmap/Issue/PR
  state - it never invents a capability that is not already cataloged, and
  it already tracks per-task `depends_on` ordering.
- Task 040's Goal Executor (`src/devbot/goal_executor.py`) already
  materializes one approved Task from that plan into a real Issue, Branch,
  and draft Contract - but only one at a time, gated behind an explicit
  `--confirm` per Task, and it never creates a PR or invokes an Agent itself.

Neither of these tracks a persistent "Goal" as its own object. Each
invocation recomputes the plan fresh from GitHub/filesystem state. There is
no state machine above the Issue-label state machine (`docs/03
-state-machine.md`), and no aggregate view of "is this whole piece of work
done."

`CONSTITUTION.md` §1 ("Human-first Planning") and `AGENTS.md` §1 require
that "아이디어, 아키텍처, 범위, 위험, Acceptance Criteria는 프로젝트
소유자와 ChatGPT가 대화로 결정한다" and that a Planner "does not invent
requirements or expand scope" without the owner's explicit approval, applied
today at Task grain. devbot/devbot#116 asks for approval to move to Goal
grain instead - Tasks inside an approved Goal decompose, execute, verify,
and revise without a separate approval per Task. This is a real change in
where the human checkpoint sits, and this ADR has to show why it does not
weaken §1's actual guarantee (no unapproved requirement or scope reaches an
artifact) even though it changes its grain.

## Decision

**Goal is the user-facing unit of intent. Task remains the internal unit of
execution, unchanged.** A Goal does not replace `CONSTITUTION.md`'s Single
Task model (§3) - it is a container that a Task Graph of ordinary,
fully-compliant Tasks lives inside. Every node in a Task Graph is still
exactly 1 Issue = 1 Branch = 1 Contract = 1 Pull Request, still goes through
the unchanged Task-level state machine (`docs/03-state-machine.md`), still
gets an unchanged automatic Task-level review (`AGENTS.md` §12-13). Nothing
about how a single Task is built, reviewed, or merged changes. What changes
is who decides *when the next Task starts* and *who checks the result*.

```text
GOAL_PROPOSED                     <- Goal Specification + DoD drafted
  -> GOAL_APPROVED                <- "다음": the FIRST human checkpoint
  -> PLANNING                     <- decompose into a Task Graph, unattended
                                      (deterministic today - ADR-007);
                                      "invent nothing outside the approved
                                      Goal Scope"
  -> EXECUTING                    <- materialize + run Task Graph nodes,
                                      unattended, in dependency order
  -> VERIFYING                    <- ADR-005's four gates per node
                                      (Architecture gate selective, not
                                      per-node-mandatory - ADR-005 §Architecture)
  -> REVISING (recoverable failure)
  -> EXECUTING / VERIFYING        <- bounded revision loop (ADR-007 budget)
  -> REVIEW_REQUESTED             <- Goal gate passed for every required node
  -> AUDITING                     <- "리뷰": the SECOND human checkpoint -
                                      starts AND is performed by the
                                      conversation agent the human is
                                      already talking to, not queued
                                      automatically and not a personal
                                      line-by-line human read
  -> GOAL_ACCEPTED                <- AUDITING's PASS verdict
  -> RELEASE_REPORTED
```

Full transition ownership, entry conditions, and failure/escalation paths
are defined in `docs/17-execution-revision-loop.md`; this ADR fixes only the
two-checkpoint shape and why it is safe. **Correction (2026-07-20, CTO
review on PR #117):** the first version of this diagram put `GOAL_ACCEPTED`
directly after `REVIEW_REQUESTED`, implying a human personally reads and
judges the Completion Report. The corrected shape still has exactly two
human checkpoints (`다음`, `리뷰`) - `AUDITING` does not add a third, it is
what `리뷰` *means*: the conversation agent, not the human line-by-line,
produces the verdict that lands `GOAL_ACCEPTED`. See `docs/17
-execution-revision-loop.md`'s matching correction for full detail, and
ADR-007's Execution Mode correction for why `subscription_assisted` (the
mode this checkpoint necessarily runs in) cannot be queued automatically
the way the first version of `REVIEW_REQUESTED` assumed.

### Why this does not violate Human-first Planning

`GOAL_APPROVED` is the same kind of gate `CONSTITUTION.md` §1 already
requires - a human approves scope, risk, and Definition of Done *before* any
execution artifact is created - just applied once per Goal instead of once
per Task. What used to require a fresh human conversation per Task
(deciding *that* Task's scope) is replaced by two things acting together,
not by trust in an Agent's judgment alone:

1. **Decomposition is bounded by the Goal's own approved Scope and
   Non-goals**, the same discipline Task 038's planner already enforces
   against its hand-curated catalog ("unmatched intent -> `ambiguous`,
   never guessed" - `src/devbot/goal_planner.py`). `PLANNING` may only
   produce Task Graph nodes that trace to something the approved Goal
   Specification already declared in scope; anything that would require
   inventing new scope is, by the same fail-closed discipline, not silently
   decided - it is surfaced back to `GOAL_PROPOSED`-level human decision
   rather than guessed.
2. **ADR-005's Contract and Architecture gates are the safety net that used
   to be a human reading the diff.** "Out-of-scope changes are rejected or
   escalated" (Contract gate) and "unapproved public API, security, or
   persistence changes are escalated" (Architecture gate) are not new
   invented checks for this ADR - they are exactly the class of thing a
   human reviewer would have caught in the old per-Task conversation, now
   made explicit, typed, and evidence-backed instead of implicit.

In other words: the checkpoint moved, but the thing it protects against
(unapproved requirements or scope reaching a merged artifact) is still
caught, at the same or finer grain, just by verification instead of by a
second human conversation. A Goal whose decomposition or execution would
require deciding something outside its approved Scope does not proceed
silently - it escalates back to a human, the same `manual-action` discipline
`docs/03-state-machine.md` already uses at Task grain, now also available at
Goal grain.

### What is genuinely new, honestly stated

This ADR does ask the project owner to trust DevBot's own decomposition and
verification machinery, for Tasks inside an approved Goal, to the same
degree previously placed in a human reading each Task's design in
conversation. That trust is not free, and it is not this ADR's place to
declare it earned - it is earned incrementally, by `docs/14
-autonomy-first-roadmap.md`'s own explicit ordering: Phase C (which this
redesign belongs to) is not to be opened "until the unmanned loop is
trusted" at Task grain first. This ADR is documentation and architecture
only (per Issue #116's own scope) precisely so that trust question is
answered by a later, separate implementation Goal, evaluated against real
running behavior - not asserted here.

### Constitution and AGENTS.md status

Per `CONSTITUTION.md` §10, "Changes to this Constitution require an explicit
architecture decision by the project owner." This ADR is that explicit
architecture decision - devbot/devbot#116 was authored by the project owner
and directly requests this change of grain. This ADR does not itself edit
`CONSTITUTION.md` or `AGENTS.md` (out of scope for a documentation-only
architecture Goal, and `AGENTS.md` §13's "자동 Merge와 자동 Issue Close는
현재 범위가 아니다" is already stale relative to shipped B2 automatic-merge
behavior, which is a pre-existing drift this ADR did not introduce). A
follow-up amendment - adding a Goal-level companion to §1's "Human-first
Planning" and correcting §13's stale automatic-merge line - is recommended
before an implementation Goal building on this ADR begins, so the executable
rulebook Agents actually read stays accurate.

## Consequences

### Positive

- One human touch per Goal ("다음", "리뷰") instead of one per Task,
  without discarding any existing per-Task safety mechanism - every Task
  inside a Goal is still a fully compliant, individually reviewed,
  individually revertable Task.
- Reuses Task 038/040 nearly whole: the Goal Planner's decision model and
  dependency ordering, and the Goal Executor's exact three-artifact write
  set, become the `PLANNING` and node-materialization steps of `EXECUTING`
  respectively - see `docs/15-goal-and-task-graph.md` for the extension
  needed (removing the per-Task `--confirm` gate in favor of the Goal-level
  one).
- A Goal is independently abandonable or revisable at `GOAL_PROPOSED` before
  anything is created, same as today's design conversation, just written
  down instead of implicit.

### Negative

- A persistent Goal state does not exist today; Task 038/040 are stateless
  by design (recomputed from GitHub each call). Introducing Goal state is
  new surface area with its own failure modes (drift between recorded Goal
  state and actual GitHub state) that Task-level state does not have to the
  same degree, because Task state already lives entirely in GitHub labels.
- The `PLANNING` step's "bounded by approved Scope" discipline is only as
  good as how precisely a Goal Specification's Scope and Non-goals are
  written - an underspecified Goal risks either false escalation (annoying)
  or, if the discipline is implemented loosely, genuine scope creep
  (dangerous). `docs/16-verification-model.md`'s Contract gate is the
  concrete mechanism this risk is delegated to.
- Two checkpoints instead of many is only actually safer if the Completion
  Report gives `AUDITING`'s conversation agent (and the human watching that
  same conversation) enough aggregated evidence to make an informed PASS/
  FAIL verdict without re-reading every Task PR - this places real weight
  on the Completion Report (ADR-005's Goal gate evidence) being genuinely
  legible, not a rubber stamp the audit agent waves through.

## Rejected Alternatives

### Keep per-Task approval, only automate execution *within* a Task

This is closest to the status quo (already true per `AGENTS.md` §13) and
was rejected as insufficient - it does not address Issue #116's actual
target, which is sequencing across Tasks, not execution within one.

### No Goal-level state; treat a Goal as just "a batch of Tasks planned once"

Considered, since Task 038 already computes a multi-task plan in one call.
Rejected because a batch with no persistent state cannot support `RETRYING`,
`REVISING`, or a Goal-level `REVIEW_REQUESTED` gate that survives a
process restart or a days-long Task Graph - some of `docs/15-goal-and
-task-graph.md`'s Goal aggregate is genuinely necessary state, not
convenience.

### Fully autonomous Goal execution, no `GOAL_APPROVED` gate

Rejected outright - this would violate `CONSTITUTION.md` §1 in letter, not
just risk it in spirit, and nothing in Issue #116 asks for it: the Issue's
own interaction target explicitly starts with "`다음` - approve and start
the proposed Goal."

## Implementation Guidance

`PLANNING` should be built as a direct extension of `src/devbot/
goal_planner.py`'s existing decision model, not a parallel implementation -
its `depends_on`/`dependency_order_is_valid()` machinery is the seed of the
Task Graph's dependency edges. `EXECUTING`'s node materialization should
reuse `src/devbot/goal_executor.py`'s exact three-write set
(`create_issue`/`create_branch`/`create_file`) per node, removing only the
per-node `--confirm` requirement in favor of the Goal-level
`GOAL_APPROVED` gate having already happened once. Neither module's existing
read-only/single-write behavior should be duplicated - extended in place,
per `docs/14-autonomy-first-roadmap.md`'s "제자리 리팩터" principle.

## Related Decisions

- [ADR-005](ADR-005-verification-driven-workflow.md) - the four gates that
  substitute for per-Task human review inside `VERIFYING`.
- [ADR-007](ADR-007-ai-resource-subscription-strategy.md) - bounds
  `EXECUTING`/`REVISING`'s AI usage per Goal.
- `docs/15-goal-and-task-graph.md` - the Goal, Goal Specification, DoD, and
  Task Graph data models.
- `docs/17-execution-revision-loop.md` - full state transition table.
