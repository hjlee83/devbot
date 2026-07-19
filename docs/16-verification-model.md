# Verification Model and Evidence Contract

Architecture document for devbot/devbot#116. Defines the **Verification
Plan** data model behind [ADR-005](adr/ADR-005-verification-driven-workflow.md)'s
four gates, and the evidence contract each gate must satisfy. See ADR-005
for why the four gates exist and their deterministic-versus-AI-judgment
split; this document defines their concrete shape and exactly which
existing DevBot subsystem produces each gate's evidence.

## Verification Plan

Part of a Goal Specification (`docs/15-goal-and-task-graph.md`'s
`verification_requirements`), instantiated per Goal and evaluated per Task
Graph node:

```text
Verification Plan
  gates: list of
    kind                  - technical | contract | architecture | goal
    applies_to             - all_nodes | node_ids (a Goal may exempt a
                             specific optional node from a gate it doesn't
                             need, e.g. a docs-only node skipping the
                             Technical gate's build step)
    evidence_producer        - which existing subsystem runs this gate
                               (table below)
    execution_mode             - which Resource Strategy execution mode
                                 (docs/18-resource-strategy.md) this gate's
                                 AI calls, if any, use
  strictness                  - required | advisory (an advisory gate
                                produces findings for the Completion Report
                                but does not block REVIEW_REQUESTED by
                                itself - useful for a Goal piloting a new
                                gate before trusting it as a blocker)
```

## The four gates, evidence, and producers

| Gate | Deterministic? | Evidence artifact | Producer (existing, reused) |
|---|---|---|---|
| Technical | Fully | command output + exit code | Validation Gate (`AGENTS.md` §9), Task 039 CI Workflow Runs check |
| Contract | Mostly (scope-creep judgment is the exception) | `SpecificationValidationResult` (`SPV-001..013`) + checkpoint-to-test table | Task 043 `specification_validation.py`, `docs/06-review-policy.md` |
| Architecture | Judgment (evidence-backed, typed) | `ReviewReport` (typed findings, severity, location) | Task 053 `review_decision.py`, submitted via Task 054 `github_review_submission.py` |
| Goal | Fully (aggregation only) | Completion Report | new (`docs/15-goal-and-task-graph.md`), same aggregation style as Task 052 |

No gate's evidence is a free-text claim. Every row above already exists as
a typed, tested DevBot subsystem except the Goal gate's aggregation itself,
which is new but follows an already-proven pattern (Task 052's
`release_recommendation_aggregation`: read already-produced typed results,
never re-derive them from source).

### Technical gate detail

Runs first, blocks the rest of the pipeline on failure - there is nothing
for the Contract or Architecture gate to usefully evaluate in code that does
not build. Reuses the Validation Gate's existing command execution and exit
-code evidence model unchanged; a Task Graph node's Technical gate result is
exactly what `PreparedWorkspace` validation (Task 029) already produces for
a single Task today, attached to that node.

### Contract gate detail

Two parts, one deterministic and one not:

- **Structural** (deterministic): Task 043's `SPV-001` through `SPV-013`
  rule codes already check section presence, Acceptance Criteria format,
  and Contract-quotation integrity without any AI call - reused verbatim,
  the same `specification_validation.py` invocation a human would run via
  `devbot specification validate --task <N>` today, just invoked
  automatically per node.
- **Scope-creep judgment** (AI, bounded by `docs/18-resource-strategy.md`'s
  `max_ai_review_calls`): deciding whether a file changed outside the
  Specification's declared scope is a reasonable implementation detail or
  actual creep. This is the only part of the Contract gate that spends
  budget; it should be a narrow, single-purpose classification (in scope /
  out of scope / escalate), not a general review pass duplicating the
  Architecture gate's job.

### Architecture gate detail

Identical in substance to today's Task-level review
(`docs/06-review-policy.md`, `AGENTS.md` §11-12) - "does this respect
`docs/adr/`'s accepted decisions, are core/adapter boundaries intact, is
any public API/security/persistence change unapproved" is exactly what a
Task reviewer already evaluates. What this gate changes is only the output
contract: a Task 053 `ReviewReport` instead of free text, whose `decision`
and severity counts are *derived from* its `findings` rather than asserted
independently (`ReviewReport.__post_init__`'s invariant, enforced even
against direct construction) - so a Goal-level aggregation can trust a
node's recorded decision without re-reading the findings that produced it.
Submission of that report as an actual GitHub PR review goes through Task
054's stale-head and self-approval protections unchanged - a Task Graph
node's PR is still a real PR, reviewed the real way.

### Goal gate detail

Purely an aggregation function over every *required* node's already
-recorded gate results (`docs/15-goal-and-task-graph.md`'s Task Graph
`gate_results` field) plus the Definition of Done's `criteria`, producing
the Completion Report. It never re-invokes the Technical, Contract, or
Architecture gate itself - by the time the Goal gate runs, every required
node's evidence already exists or the Goal gate immediately reports that
node's gate as an unresolved finding. This mirrors ADR-005's "Goal gate:
deterministic aggregation" decision exactly.

## Evidence integrity requirements

- **Evidence must be attached to a specific artifact version.** A Technical
  gate result is only valid for the exact commit it ran against; an
  Architecture gate result is only valid for the exact PR head `ReviewReport`
  recorded as `reviewed_head_sha` (Task 054's existing stale-head
  discipline). A Task Graph node whose artifact has moved since its last
  recorded gate result must re-run that gate, not reuse stale evidence -
  the same principle Task 054's `_fetch_and_verify_pull_request` re
  -verification already enforces at Task grain, generalized to every gate.
- **A missing gate result is a blocking finding, not a pass.** The Goal gate
  must never interpret "this node has no recorded Architecture gate result"
  as "this node has no architecture problems" - absence of evidence blocks
  `REVIEW_REQUESTED` the same way a failed gate does.
- **Findings are never silently dropped.** Task 054's existing rule that
  "every finding is always also written into the top-level review body"
  generalizes: every gate finding attached to a node must appear in the
  Completion Report, whether or not it ended up blocking - matching
  `CONSTITUTION.md` §7's Traceability requirement that automation must not
  reduce visibility into "why a state transition occurred."

## Relationship to the existing binary review outcome

`AGENTS.md` §12 and `CONSTITUTION.md` §6 currently define a Task-level
review outcome as exactly `MERGE READY` or `REQUEST CHANGES`. This document
does not change that - a Task Graph node's Architecture gate still resolves
to one of those two outcomes at the individual PR level (Task 053's third
`comment_only` decision value is additive housekeeping for non-blocking
notes, not a third *merge* outcome). What is new is only that this binary
outcome is now also recorded as structured, addressable evidence
(`ReviewReport`) that a Goal-level aggregation can read, rather than being
the terminal fact it is today.
