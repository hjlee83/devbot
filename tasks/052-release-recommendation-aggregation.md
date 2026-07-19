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