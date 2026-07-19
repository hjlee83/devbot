# Specification: Task 052 — Release Recommendation Aggregation

## Provenance

- GitHub Issue: #109
- Branch: `task/052-release-recommendation-aggregation`
- Task Contract: `tasks/052-release-recommendation-aggregation.md`
- Generation Source: Architect-authored Specification for Task 052.

# Overview

## Goal

Add a read-only aggregation layer that determines one release recommendation from all merged Task Contracts after the latest stable GitHub Release.

## Background

Task 047 classifies one Contract. Task 051 can run a release when an operator supplies a level. The missing boundary is collecting all unreleased merged work and computing the authoritative level without guessing from PR labels or titles.

# Functional Requirements

## Release Boundary

- Use the latest stable, non-draft, non-prerelease GitHub Release as the lower boundary.
- Prefer the Release target commit SHA as the authoritative boundary.
- When no stable Release exists, use repository history from the initial reachable commit and expose that no-release baseline in the result.
- Include only PRs merged into the repository default branch after the boundary.
- Ordering must be deterministic: merge timestamp ascending, then PR number ascending.

## PR-to-Contract Resolution

- Resolve each merged PR to exactly one Task Contract.
- The primary evidence is an explicit Task identity or Contract path already represented in repository artifacts or PR metadata.
- Do not infer release impact from PR title, labels, commit messages, or diff contents.
- Zero matching Contracts is an error for that PR.
- Multiple matching Contracts is an ambiguity error.
- Non-Task administrative PRs may only be excluded through an explicit, typed exclusion rule; do not silently skip them.

## Classification and Aggregation

- Load each Contract from the merged commit or default-branch state appropriate to the PR evidence.
- Reuse the existing Contract parser, typed metadata engine, and Task 047 classification function.
- Preserve every individual classification reason.
- Aggregate with the strict precedence `major > minor > patch > none`.
- No eligible merged work returns `none` with an empty evidence set.
- Any unresolved, malformed, legacy-unsupported, or invalid Contract causes the complete aggregation to fail closed.

## Typed Models

Add immutable typed models equivalent to:

- `ReleaseRecommendationEvidence`
- `ReleaseRecommendationAggregation`
- `ReleaseRecommendationAggregationError`
- Typed resolution/failure reasons

Each evidence item must contain at least:

- PR number and URL
- merge commit SHA and merged timestamp
- Task number
- Contract path
- individual recommendation
- classification reasons

The aggregate must contain at least:

- repository
- release boundary version/tag/SHA, or explicit no-release baseline
- inspected PR count
- ordered evidence items
- final recommendation

## CLI

Add:

```bash
devbot release recommend [--repo owner/repo]
```

The command must:

- be entirely read-only;
- print the boundary, each contributing PR/Contract, individual recommendation, reasons, and final recommendation;
- exit 0 for valid results including `none`;
- exit non-zero for incomplete or ambiguous evidence;
- create no write client and perform no local file mutation.

# Public API

Add a dedicated module, preferably `src/devbot/release_recommendation_aggregation.py`, exposing APIs equivalent to:

```python
build_release_recommendation_aggregation(...)
render_release_recommendation_aggregation(...)
```

Exact signatures may follow repository conventions. Policy must not be duplicated from Tasks 045–047.

# Acceptance Criteria

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

# Required Tests

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

# Files Expected to Change

- `src/devbot/release_recommendation_aggregation.py`
- `src/devbot/main.py`
- GitHub read-client/models only when necessary for typed pagination or merged-PR data
- `tests/test_release_recommendation_aggregation.py`
- `tests/test_main.py`
- `results/052-release-recommendation-aggregation.md`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` when an architectural decision is required

# Out of Scope

- `devbot release run --auto`
- Version calculation or version-file mutation
- Release preparation, tagging, workflow dispatch, or GitHub Release creation
- Release-note generation
- Automatic exclusion based on heuristics
- Multi-repository rollups

# Validation

```bash
uv run devbot specification validate --task 52
uv run ruff check .
uv run pytest
```

# Handoff

1. Implement only on `task/052-release-recommendation-aggregation`.
2. Keep Issue #109 as the task identity.
3. Reuse existing Contract metadata and classification policy.
4. Record deviations and evidence in `results/052-release-recommendation-aggregation.md`.
5. Open one PR to `main`, closing Issue #109.
6. Apply review fixes to the same branch and PR.