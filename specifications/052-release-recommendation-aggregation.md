# Specification: Task 052 — Release Recommendation Aggregation

## Provenance

- GitHub Issue: #109
- Branch: `task/052-release-recommendation-aggregation`
- Task Contract: `tasks/052-release-recommendation-aggregation.md`
- Generation Source: Architect-authored Specification for Task 052.

# Overview

## Goal

Add a read-only aggregation layer that determines one release recommendation from all merged Task Contracts after the latest stable GitHub Release.

## Scope

- Determine the latest stable Release boundary.
- Enumerate merged PRs after that boundary.
- Resolve each PR to exactly one authoritative Task Contract.
- Parse and validate Contract metadata through existing components.
- Classify each Contract through Task 047 policy.
- Aggregate using `major > minor > patch > none`.
- Return typed evidence for every included PR and Contract.
- Add read-only CLI `devbot release recommend`.
- Fail closed on incomplete or ambiguous evidence.

## Background

Task 047 classifies one Contract. Task 051 can run a release when an operator supplies a level. The missing boundary is collecting all unreleased merged work and computing the authoritative level without guessing from PR labels or titles.

## Roadmap Context

Task 052 sits after Task 047 (single-Contract classification policy) and Task 051 (orchestrated release execution given an explicit, operator-supplied `--level`). It closes the gap between them: computing the authoritative level automatically from all unreleased merged work, rather than requiring the operator to already know it. This Task produces the recommendation only - it does not call Task 051 or add an `--auto` mode to it; that composition is explicitly left to a later Task.

# Functional Requirements

## Required Behaviour

**Release boundary.** Use the latest stable, non-draft, non-prerelease GitHub Release as the lower boundary, preferring its target commit SHA as authoritative. When no stable Release exists, use repository history from the initial reachable commit and expose that no-release baseline explicitly in the result. Only PRs merged into the repository default branch after the boundary are considered. Ordering must be deterministic: merge timestamp ascending, then PR number ascending.

**PR-to-Contract resolution.** Resolve each merged PR to exactly one Task Contract. The primary evidence is an explicit Task identity or Contract path already represented in repository artifacts or PR metadata - never inferred from PR title, labels, commit messages, or diff contents. Zero matching Contracts is an error for that PR. Multiple matching Contracts is an ambiguity error. Non-Task administrative PRs may only be excluded through an explicit, typed exclusion rule; they are never silently skipped.

**Classification and aggregation.** Load each Contract from the merged commit or default-branch state appropriate to the PR evidence. Reuse the existing Contract parser, typed metadata engine, and Task 047 classification function - do not duplicate their policy. Preserve every individual classification reason. Aggregate with the strict precedence `major > minor > patch > none`. No eligible merged work returns `none` with an empty evidence set, not an error. Any unresolved, malformed, legacy-unsupported, or invalid Contract causes the complete aggregation to fail closed.

**Typed models.** Add immutable typed models equivalent to `ReleaseRecommendationEvidence`, `ReleaseRecommendationAggregation`, `ReleaseRecommendationAggregationError`, and typed resolution/failure reasons. Each evidence item must contain at least: PR number and URL, merge commit SHA and merged timestamp, Task number, Contract path, individual recommendation, and classification reasons. The aggregate must contain at least: repository, release boundary version/tag/SHA (or explicit no-release baseline), inspected PR count, ordered evidence items, and the final recommendation.

**CLI.** Add `devbot release recommend [--repo owner/repo]`. The command must be entirely read-only; print the boundary, each contributing PR/Contract, individual recommendation, reasons, and the final recommendation; exit 0 for valid results including `none`; exit non-zero for incomplete or ambiguous evidence; create no write client and perform no local file mutation.

**Public API.** Add a dedicated module, preferably `src/devbot/release_recommendation_aggregation.py`, exposing APIs equivalent to `build_release_recommendation_aggregation(...)` and `render_release_recommendation_aggregation(...)`. Exact signatures may follow repository conventions. Policy must not be duplicated from Tasks 045-047.

## Acceptance Criteria

1. The latest stable Release establishes the lower boundary.
2. Every later merged PR is deterministically included or explicitly rejected with a typed reason.
3. Every included PR resolves to exactly one Task Contract.
4. Existing metadata parsing and release classification policies are reused.
5. The aggregate recommendation is the maximum of all individual recommendations.
6. Empty eligible work returns `none` with explicit evidence, not an error.
7. Missing, malformed, ambiguous, or invalid Contract evidence fails closed.
8. The CLI performs no filesystem or GitHub write.
9. Results contain PR number, merge SHA/date, Contract path, individual recommendation, and classification reasons.
10. Specification validation, Ruff, and the full test suite pass.

## Out of Scope

- Version-file writes.
- Release preparation or publication.
- `release run --auto`.
- Release-note generation.
- Guessing from PR labels, titles, commits, or diffs when Contract evidence is unavailable.
- Multi-repository aggregation in one invocation.

# Technical Design

## Architecture

### Typed Models

- `ReleaseRecommendationEvidence` - PR number and URL, merge commit SHA and merged timestamp, Task number, Contract path, individual recommendation, classification reasons.
- `ReleaseRecommendationAggregation` - repository, release boundary version/tag/SHA (or explicit no-release baseline), inspected PR count, ordered evidence items, final recommendation.
- `ReleaseRecommendationAggregationError` and typed resolution/failure reasons - covering zero-match, ambiguous-match, malformed metadata, and legacy-unsupported Contracts.

### Public API

```python
build_release_recommendation_aggregation(...)
render_release_recommendation_aggregation(...)
```

### CLI

```bash
devbot release recommend [--repo owner/repo]
```

## Files Expected to Change

- `src/devbot/release_recommendation_aggregation.py`
- `src/devbot/main.py`
- GitHub read-client/models only when necessary for typed pagination or merged-PR data
- `tests/test_release_recommendation_aggregation.py`
- `tests/test_main.py`
- `results/052-release-recommendation-aggregation.md`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` when an architectural decision is required

## Dependencies

- `src/devbot/contract_metadata.py`
- `src/devbot/release_classification.py`
- `src/devbot/release_ops.py`

## Constraints

- Policy must not be duplicated from Tasks 045-047 - the Contract parser, typed metadata engine, and `classify_release` are always reused, never reimplemented.
- Zero matching Contracts for a PR is an error for that PR, not a silent skip.
- Multiple matching Contracts for a PR is an ambiguity error.
- Non-Task administrative PRs may only be excluded through an explicit, typed exclusion rule.
- Release impact is never inferred from PR title, labels, commit messages, or diff contents.

## Migration Notes

No configuration migration is required. This is a new, additive, read-only command; existing release commands (`prepare`, `publish`, `publish-prepared`, `strategy`, `run`, `preview`, `status`) remain unaffected (AC #10).

# Validation

## Required Tests and Quality Gates

Tests must cover:

- no prior Release;
- no merged PRs after the boundary;
- one PR for each recommendation level;
- mixed recommendations and precedence;
- deterministic ordering;
- PR merged before/on/after boundary;
- PR merged to a non-default branch exclusion;
- missing Contract;
- ambiguous Contract linkage;
- malformed or invalid metadata;
- legacy Contract fail-closed behavior;
- classification reason preservation;
- GitHub pagination where existing client APIs support it;
- CLI rendering and exit codes;
- proof that no write client or write operation occurs.

Use mocks and temporary repositories only. Do not create a real Release, tag, workflow dispatch, issue, or PR during tests.

## Validation Commands

```bash
uv run devbot specification validate --task 52
uv run ruff check .
uv run pytest
```

## Success Criteria

1. Latest stable Release defines the aggregation boundary.
2. Only PRs merged into the default branch after that boundary are considered.
3. PR ordering and output are deterministic.
4. Each considered PR resolves to exactly one authoritative Contract or the entire operation fails closed.
5. Individual recommendations are produced only through existing metadata/classification components.
6. Aggregate precedence is `major > minor > patch > none`.
7. An empty set yields a valid `none` result.
8. The result includes complete explainable evidence.
9. CLI is fully read-only and creates no write client.
10. Existing release commands remain backward compatible.
11. Specification validation completes with zero errors and warnings.
12. Ruff and the complete test suite pass.

# Safety

## Things the Implementation Agent Must NOT Do

- Must not create a real GitHub Release, tag, workflow dispatch, Issue, or PR during implementation or testing.
- Must not construct a write client (`GitHubWriteClient`) anywhere in this Task's code path.
- Must not perform any local filesystem mutation (no `pyproject.toml`/`uv.lock` writes).
- Must not infer release impact from PR title, labels, commit messages, or diff contents when Contract evidence is unavailable.
- Must not silently skip a PR that fails to resolve to exactly one Contract - fail closed instead.
- Must not duplicate Task 045/046's Contract parsing/metadata logic or Task 047's classification policy anywhere else.
- Use mocks and temporary repositories only in tests.

# Completion

## Expected Deliverables

- Recommendation aggregation module.
- CLI wiring and typed output.
- Unit and integration tests.
- Result document.
- Roadmap/ADR updates where required.

## Result Document

Create `results/052-release-recommendation-aggregation.md` containing: boundary resolution approach, PR-to-Contract resolution mechanism, aggregation algorithm and precedence, evidence structure, fail-closed behaviors, tests and command results, scope exclusions, any deviations from this Specification.

## PR Expectations

- One PR from `task/052-release-recommendation-aggregation` to `main`.
- Reference and close Issue #109.
- Report exact validation commands and results.
- Explain the fail-closed guarantees and how PR-to-Contract resolution works.

# Handoff

## Required Handoff Procedure

1. Implement only on `task/052-release-recommendation-aggregation`.
2. Keep Issue #109 as the task identity.
3. Reuse existing Contract metadata and classification policy.
4. Record deviations and evidence in `results/052-release-recommendation-aggregation.md`.
5. Open one PR to `main`, closing Issue #109.
6. Apply review fixes to the same branch and PR.

## Token-Limit Behaviour

When implementation context is constrained, prioritize in this order:

1. Fail-closed correctness (never silently guess, skip, or infer from unreliable signals).
2. Reuse of Tasks 045-047 policy.
3. Deterministic ordering.
4. Tests.
5. Documentation.

Do not silently reduce acceptance criteria or expand scope.

# Full Task Contract Reference

```markdown
# Task 052 — Release Recommendation Aggregation

## Provenance

- GitHub Issue: #109
- Branch: `task/052-release-recommendation-aggregation`
- Specification: `specifications/052-release-recommendation-aggregation.md`

## Goal

Aggregate the typed release recommendations of every merged Task since the latest stable GitHub Release into one authoritative, explainable recommendation.

## Contract Metadata

- Specification Type: feature
- Release Impact: feature
- Risk Level: medium
- Migration Required: false
- Backward Compatible: true

## Scope

- Determine the latest stable Release boundary.
- Enumerate merged PRs after that boundary.
- Resolve each PR to exactly one authoritative Task Contract.
- Parse and validate Contract metadata through existing components.
- Classify each Contract through Task 047 policy.
- Aggregate using `major > minor > patch > none`.
- Return typed evidence for every included PR and Contract.
- Add read-only CLI `devbot release recommend`.
- Fail closed on incomplete or ambiguous evidence.

## Out of Scope

- Version-file writes.
- Release preparation or publication.
- `release run --auto`.
- Release-note generation.
- Guessing from PR labels, titles, commits, or diffs when Contract evidence is unavailable.
- Multi-repository aggregation in one invocation.

## Acceptance Criteria

1. The latest stable Release establishes the lower boundary.
2. Every later merged PR is deterministically included or explicitly rejected with a typed reason.
3. Every included PR resolves to exactly one Task Contract.
4. Existing metadata parsing and release classification policies are reused.
5. The aggregate recommendation is the maximum of all individual recommendations.
6. Empty eligible work returns `none` with explicit evidence, not an error.
7. Missing, malformed, ambiguous, or invalid Contract evidence fails closed.
8. The CLI performs no filesystem or GitHub write.
9. Results contain PR number, merge SHA/date, Contract path, individual recommendation, and classification reasons.
10. Specification validation, Ruff, and the full test suite pass.

## Deliverables

- Recommendation aggregation module.
- CLI wiring and typed output.
- Unit and integration tests.
- Result document.
- Roadmap/ADR updates where required.
```
