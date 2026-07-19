# ADR-005: Verification-Driven Workflow

- **Status:** Accepted
- **Date:** 2026-07-20
- **Decision owners:** DevBot Architecture
- **Origin:** devbot/devbot#116

## Context

DevBot already runs an automated Task-level review loop (`AGENTS.md` §12-13,
`docs/06-review-policy.md`): every Task Pull Request receives an automatic
`MERGE READY` or `REQUEST CHANGES` decision, and `REQUEST CHANGES` relays
into an automatic rework cycle without a human in that loop (Task 027, the
Autonomous Review Loop). That review is one undifferentiated pass - it mixes
things a linter or test runner already proved (does it build, do the tests
pass) with things that require judgment (does this respect the architecture,
did scope creep in) into a single AI-executed check.

devbot/devbot#116 asks DevBot to compress human involvement further, from
one checkpoint per Task down to two checkpoints per Goal (`다음` to approve,
`리뷰` to accept). Removing per-Task human oversight only stays safe if
something else keeps catching the same class of mistakes that oversight used
to catch - and that something must not itself become an unbounded AI cost
center, because reducing "AI/token consumption per Goal" is one of this
redesign's own stated differentiators (Issue #116, "Product intent").

Today's single-pass review already conflates two different cost profiles:
checks that are deterministic and nearly free (a test suite either passes or
it doesn't) and checks that require judgment and cost real AI calls (does
this violate an accepted ADR). Collapsing four checkpoints (Task review,
Task merge, Goal review, Goal acceptance) into fewer human touches makes this
conflation actively harmful - every deterministic check that still runs
through AI judgment is both slower and, cumulatively across every Task in a
Goal's Task Graph, a meaningful fraction of the token budget this redesign is
supposed to shrink.

## Decision

Verification is decomposed into four gates, each independently satisfiable
and each explicit about how much of it is deterministic versus AI judgment.
A Goal only reaches `REVIEW_REQUESTED` once every Task Graph node it depends
on has passed all four gates for its own artifacts, and a Goal only reaches
`GOAL_ACCEPTED` once the Goal gate itself passes.

```text
Technical gate     -> fully deterministic, zero AI cost
Contract gate      -> mostly deterministic, narrow AI judgment where noted
Architecture gate  -> primarily AI judgment, evidence-backed and typed
Goal gate          -> deterministic aggregation over the other three gates
```

### Technical gate (deterministic)

Build succeeds, automated tests pass, lint/static checks pass where
configured, required CI checks complete. No AI call is required to decide
this gate - it is the existing `uv run ruff check .` / `uv run pytest` /
required-CI-check machinery (`AGENTS.md` §9 "Validation Gate", Task 039's
Workflow Runs API check) reused unchanged. A Task Graph node that fails this
gate never reaches the Contract or Architecture gate - there is nothing for
AI judgment to usefully evaluate in code that does not build.

### Contract gate (mostly deterministic)

Every acceptance criterion has evidence, implementation is traceable to the
approved Specification, required artifacts are present, out-of-scope changes
are rejected or escalated. The structural half of this is already
deterministic and already built: Task 043's Specification Validator
(`SPV-001` through `SPV-013`) checks section presence, Acceptance Criteria
format, and Contract-quotation integrity without any AI call, and
`docs/06-review-policy.md`'s existing checkpoint-to-test evidence table is a
mechanical cross-reference, not a judgment call. The one genuinely
judgment-shaped part of this gate - deciding whether a change that touches
files outside the Specification's declared scope is a reasonable
implementation detail or actual scope creep - is where this gate spends its
(bounded, see ADR-007) AI budget; everything else in it is free.

### Architecture gate (AI judgment, evidence-backed)

Accepted ADRs are not violated, core/adapter boundaries remain intact,
dependency and module rules remain intact, unapproved public API, security,
or persistence changes are escalated. This is exactly what today's Task
review already does, and it is the one gate that structurally cannot be made
fully deterministic - "does this respect ADR-001's core/adapter boundary" is
a judgment call by construction. What changes is the evidence format: this
gate must produce a Task 053 `ReviewReport` (typed findings, severity,
location, a decision that is *derived from* the findings rather than
asserted independently) rather than free-text approval, and submission goes
through Task 054's `github_review_submission` so the same stale-head and
self-approval protections that already exist for Task-level review keep
applying unchanged.

### Goal gate (deterministic aggregation)

Definition of Done is fully satisfied, all required Task Graph nodes are
complete, no blocking findings remain, completion and release reports can be
generated. This gate does not re-run AI judgment of its own - it is a
deterministic aggregation over the other three gates' already-produced,
already-evidenced results for every required Task Graph node (`docs/15
-goal-and-task-graph.md`), the same way Task 052's release recommendation
aggregation deterministically aggregates merged Task PRs' Contracts without
re-reading their diffs. A Goal cannot reach `REVIEW_REQUESTED` by any node
silently skipping a gate - an incomplete gate result is a blocking finding
for the Goal gate by construction.

## Evidence Contract

Every gate result must be traceable to a concrete artifact, never to an
unaudited claim:

| Gate | Evidence artifact | Producer |
|---|---|---|
| Technical | command output + exit code (build/test/lint/CI) | existing Validation Gate, Task 039 CI check |
| Contract | `SpecificationValidationResult` + checkpoint-to-test table | Task 043, `docs/06-review-policy.md` |
| Architecture | `ReviewReport` (typed findings) | Task 053, submitted via Task 054 |
| Goal | Completion Report citing every node's above evidence | new, `docs/16-verification-model.md` |

This mirrors the existing principle that a Task's `Result` document is
"handoff and Evidence" (`AGENTS.md` §17) and extends it one level up: a
Goal's Completion Report is handoff and Evidence for the whole Task Graph,
not a new, separately-asserted judgment.

## Consequences

### Positive

- AI calls are spent only where judgment is structurally required
  (Architecture gate, and the narrow scope-creep judgment inside the
  Contract gate) - every other gate is free, which is what makes removing
  per-Task human review affordable in token terms.
- The evidence contract makes every gate result independently auditable
  after the fact, including by a human during the Goal-level `리뷰`
  checkpoint - nothing is "trust the agent said so."
- Reuses four already-shipped, already-tested subsystems
  (Validation Gate, Task 043 Specification Validator, Task 053/054 review
  decision and submission, Task 052-style deterministic aggregation) instead
  of building a fifth verification mechanism from scratch.
- Bounds the autonomous loop: a gate that cannot be satisfied deterministically
  or within its AI budget (ADR-007) fails closed into a blocking finding
  rather than looping indefinitely.

### Negative

- Four gates are more moving parts than one review pass; a Task Graph node
  must now track four independent results instead of one.
- The Contract gate's scope-creep judgment and the Architecture gate remain
  AI calls, not free checks - this decision reduces AI cost, it does not
  eliminate it.
- Gate results must be persisted and correctly attributed to a Task Graph
  node's specific artifacts (a specific PR head, a specific Specification
  version) or the Goal gate's aggregation is meaningless; this requires the
  Goal aggregate itself to track that mapping (`docs/15-goal-and-task-graph.md`).

## Rejected Alternatives

### One undifferentiated AI review pass, run less often

Keeping today's single-pass review but only invoking it once per Goal
instead of once per Task was considered and rejected: a review pass that
tries to judge build success, Contract adherence, *and* architecture fit in
one AI call cannot cleanly report which part failed, cannot be bounded the
same way a deterministic check can, and wastes AI judgment re-deriving facts
(does it build) that a compiler already knows for free.

### Skip the Contract/Architecture gates for high-trust Agents

Rejected as a direct violation of `CONSTITUTION.md` §8: "No automation may
... merge an unverified change." Trust in a particular Agent or model is not
a substitute for evidence: this ADR's evidence contract applies identically
regardless of which Agent produced the artifact, matching `CONSTITUTION.md`
§9's Agent neutrality.

### Human review per Task Pull Request, only Goal acceptance is new

Rejected as unnecessary, not merely undesirable: `AGENTS.md` §13's
Autonomous Review Loop already performs Task-level `MERGE READY` /
`REQUEST CHANGES` without a human in that loop today. The actual gap this
Goal closes is not "make Task review autonomous" (already true) - it is
"make gate results legible and boundable enough that a human can trust a
Goal-level summary instead of reading every Task PR." See ADR-006 for the
distinction between what is already autonomous and what this redesign adds.

## Implementation Guidance

The Technical and Contract gates should be wired first, since they require
no new AI-calling code - only aggregation over existing Validation Gate and
Specification Validator output. The Architecture gate requires no new
judgment logic either, only routing existing Task 053/054 output into the
Goal aggregate. The Goal gate is the only genuinely new logic, and it should
be implemented as a pure aggregation function in the same style as Task 052's
`release_recommendation_aggregation` - reading already-produced typed
results, never re-deriving them.

## Related Decisions

- [ADR-002](ADR-002-specification-first-architecture.md) - the Specification
  is what the Contract gate validates against.
- [ADR-006](ADR-006-goal-driven-execution.md) - defines the Goal State
  Machine these gates sit inside.
- [ADR-007](ADR-007-ai-resource-subscription-strategy.md) - the AI/Token
  Budget that bounds the Contract and Architecture gates' AI calls.
- `docs/16-verification-model.md` - the full Verification Plan and evidence
  contract data model.
