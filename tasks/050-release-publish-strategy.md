# Task 050: Release Publish Strategy

## Contract Version

1

## Provenance

- GitHub Issue: #105
- Branch: `task/050-release-publish-strategy`

## Task Identity

- id: 050
- title: Release Publish Strategy

## Metadata

- specification_type: feature
- release_impact: internal
- risk_level: high
- compatibility: backward
- migration: none

## Goal

Define one authoritative release-publish strategy per repository so DevBot never ambiguously chooses between the workflow-dispatch path introduced by Task 037 and the direct tag/GitHub Release path introduced by Task 049.

## Context

DevBot currently exposes two valid release publication mechanisms:

1. `devbot release publish`, which dispatches `.github/workflows/release.yml`.
2. `devbot release publish-prepared`, which creates and pushes a Git tag and then creates a GitHub Release directly.

Both mechanisms are individually guarded, but there is no single repository policy selecting which mechanism is authoritative. Task 050 introduces that policy boundary before any future orchestration combines classification, preparation, and publication.

## Scope

- Define a typed `ReleasePublishStrategy` with canonical values `workflow` and `direct`.
- Add one optional repository-level publish strategy configuration field.
- Resolve omitted configuration to `workflow` for backward-compatible safety.
- Reject unknown, malformed, duplicated, or ambiguous strategy configuration.
- Centralize strategy resolution in a dedicated policy component.
- Add a read-only inspection API and CLI output showing configured and effective strategy.
- Gate the existing workflow publish command so it refuses repositories resolved to `direct`.
- Gate the existing direct publish command so it refuses repositories resolved to `workflow`.
- Keep dry-run operations read-only.
- Add regression tests proving only one publish path is valid for a repository at a time.
- Update architecture decisions, roadmap, and Result documentation.

## Out of Scope

- Release recommendation calculation.
- Version calculation or mutation.
- Release preparation.
- Creating or pushing tags as part of Task 050 tests or implementation flow.
- Creating GitHub Releases as part of Task 050 tests or implementation flow.
- Dispatching a real GitHub Actions release workflow.
- Combining Tasks 047–050 into one command.
- Release-note generation or aggregation.
- Package publication.
- Automatic PR review, approval, merge, or agent dispatch.
- Removing either existing publish implementation.

## Deliverables

- Typed publish-strategy domain model.
- Repository configuration parsing and safe defaulting.
- Strategy resolver/policy boundary.
- Strategy mismatch error taxonomy.
- Read-only CLI strategy inspection.
- Guards on both existing publish entry points.
- Unit and CLI regression tests.
- `results/050-release-publish-strategy.md`.
- Roadmap and ADR updates.

## Acceptance Criteria

1. A repository with no explicit publish strategy resolves to `workflow`.
2. A repository configured with `workflow` resolves only to the workflow path.
3. A repository configured with `direct` resolves only to the direct path.
4. Unknown or malformed strategy values fail closed before any GitHub or Git write.
5. `devbot release publish` refuses a repository whose effective strategy is `direct`.
6. `devbot release publish-prepared` refuses a repository whose effective strategy is `workflow`.
7. Dry-run and strategy inspection perform no write operations.
8. Existing repositories without the new field remain backward compatible.
9. Tests prove the two write paths cannot both be accepted for the same repository configuration.
10. Task 050 does not calculate or mutate versions and does not publish a real release.

## Quality Gates

- `uv run devbot specification validate --task 50`
- `uv run ruff check .`
- `uv run pytest`
- Dedicated tests for omitted, `workflow`, `direct`, invalid, and ambiguous configuration.
- Dedicated tests proving each command refuses the non-selected strategy before any write client operation.
- Static or behavioral evidence that Task 050 does not create tags, Releases, or workflow dispatches during tests.

## Handoff

- Continue all implementation and rework on `task/050-release-publish-strategy`.
- Use Issue #105 as the only Task Issue.
- Open one PR to `main` and keep review fixes on the same PR.
- Create `results/050-release-publish-strategy.md` before requesting review.
- Do not begin release orchestration or Task 051 scope in this branch.

## References

- `src/devbot/release_ops.py`
- `src/devbot/release_publish.py`
- `src/devbot/models.py`
- `docs/07-decisions.md`
- `tasks/049-release-publish.md`
