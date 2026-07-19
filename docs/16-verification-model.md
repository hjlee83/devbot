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
| Architecture | Selective judgment (evidence-backed, typed; zero-cost for nodes fully covered by deterministic rules) | Invariant Classification (below) + `ReviewReport` for flagged nodes only | Task 053 `review_decision.py`, submitted via Task 054 `github_review_submission.py` |
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
  per-node/per-Goal Architecture-gate budget): deciding whether a file
  changed outside the Specification's declared scope is a reasonable
  implementation detail or actual creep. This is the only part of the
  Contract gate that spends budget; it should be a narrow, single-purpose
  classification (in scope / out of scope / escalate), not a general
  review pass duplicating the Architecture gate's job.

### Architecture gate detail

**Correction (2026-07-20, CTO review on PR #117):** the first version of
this section described the Architecture gate as running a full AI review,
identical to today's Task-level review, on every Task Graph node
unconditionally - for a Goal with several nodes that reproduces the exact
per-Task review cost this whole redesign exists to reduce (see ADR-005's
matching correction). The gate is now split into a classification step and
a conditional review step.

**Invariant Classification** (produced once, during `PLANNING`, per node -
`docs/17-execution-revision-loop.md`):

```text
Invariant Classification
  node_ref
  invariants: list of
    description             - e.g. "no unapproved public API change",
                              "respects ADR-001 core/adapter boundary"
    verifiable_by             - deterministic_rule | ai_review
    rule_ref                   - if deterministic_rule: which check enforces
                                 it (an import-boundary linter, a file-path
                                 allowlist derived from the node's
                                 Specification scope, an existing CI check) -
                                 this rule then runs as part of the
                                 Technical or Contract gate, not here
  ai_review_required           - bool; true only if >=1 invariant above is
                                 `ai_review`
```

A node whose invariants are *all* `deterministic_rule` has
`ai_review_required: false` and spends **zero** Architecture-gate AI calls -
its architecture evidence is the deterministic rules' pass results,
attached to the Technical/Contract gate evidence it already produces, not a
separate `ReviewReport`. Only a node with `ai_review_required: true` runs
an actual AI review, and only for the specific invariants classified
`ai_review` - not a general re-review of the whole diff.

For a flagged node, the output contract is unchanged from the first version
of this ADR/document: a Task 053 `ReviewReport` (typed findings, severity,
location) instead of free text, whose `decision` and severity counts are
*derived from* its `findings` rather than asserted independently
(`ReviewReport.__post_init__`'s invariant, enforced even against direct
construction) - so a Goal-level aggregation can trust a node's recorded
decision without re-reading the findings that produced it. Submission of
that report as an actual GitHub PR review goes through Task 054's
stale-head and self-approval protections unchanged - a Task Graph node's PR
is still a real PR, reviewed the real way when it is reviewed at all.

Classification itself (which invariants are rule-checkable versus
judgment-required) is a one-time cost paid once per Goal's `PLANNING`, not
per node re-evaluated later - and classifying "this invariant needs a rule"
is far cheaper than running the AI review that rule replaces, especially
across every subsequent Goal that reuses the same rule once it exists.

#### Power boundary (added 2026-07-20, second CTO review round on PR #117)

**The Architecture gate detects and reports. It never decides.** For a
flagged node, its AI review call returns exactly one of four typed
outcomes - it does not, and structurally cannot, edit the approved Goal
Specification, a Task Specification, or any `docs/adr/` decision itself:

```text
Architecture Gate Outcome
  PASS       - no blocking findings; node's Architecture gate satisfied
  FAIL       - blocking findings, all within the node's already-approved
              Scope; routes to REVISING (an implementation fix is expected
              to resolve it, same as today's REQUEST CHANGES)
  RETRY      - the review call itself was inconclusive (errored, produced
              an invalid ReviewReport) - re-attempt the call, bounded by
              the same Budget as any other Architecture-gate call; not a
              finding about the code, a finding about the check itself
  ESCALATE   - a finding implies the approved Specification, an accepted
              ADR, or the Goal's Scope itself would need to change to
              resolve it. The gate does not decide this - it cannot
              approve a scope change, and it must not silently treat the
              implementation as wrong when the actual problem is that the
              approved contract no longer fits. ESCALATE routes to a Goal
              Amendment (`docs/17-execution-revision-loop.md`), never to
              REVISING.
```

This applies to every AI-judgment call this document defines, not only the
Architecture gate proper - the Contract gate's scope-creep judgment uses
the same four outcomes for the same reason: a classification call is
read-only with respect to the Specification it is checking against. No
gate anywhere in this Verification Plan has write access to the artifact
it verifies.

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
- **A missing gate result is a blocking finding, not a pass - unless the
  gate was never required for that node.** The Goal gate must never
  interpret "this node has no recorded Architecture gate `ReviewReport`" as
  "this node has no architecture problems" *when that node's Invariant
  Classification marked it `ai_review_required: true`* - absence of
  required evidence blocks `REVIEW_REQUESTED` the same way a failed gate
  does. A node classified `ai_review_required: false` legitimately has no
  `ReviewReport`, and that absence is not a finding - its Architecture
  evidence is the deterministic rules' own pass results instead. The
  Invariant Classification record itself is what distinguishes "correctly
  skipped" from "missing" - a node with no Invariant Classification at all
  is the actual missing-evidence case and blocks the same as any other gate.
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
