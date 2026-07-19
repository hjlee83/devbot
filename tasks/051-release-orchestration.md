# Task 051: Release Orchestration

## Contract Version

1

## Provenance

- GitHub Issue: #107
- Branch: `task/051-release-orchestration`

## Task Identity

- id: 051
- title: Release Orchestration

## Metadata

- specification_type: feature
- release_impact: feature
- risk_level: high
- compatibility: backward
- migration: none

## Goal

Create one safe orchestration boundary that connects the existing release recommendation, preparation, publish-strategy resolution, and selected publish implementation without duplicating their policy logic.

## Context

Tasks 047–050 now provide four separate capabilities:

1. `ReleaseRecommendation` (`major`, `minor`, `patch`, `none`).
2. Safe version preparation in `pyproject.toml` and `uv.lock`.
3. Repository-level selection of exactly one publish strategy.
4. Existing workflow and direct publish implementations.

The operator still has to remember and sequence multiple commands. Task 051 introduces the first unified release command while preserving every existing safety boundary.

## Scope

- Add a dedicated release orchestration module.
- Define immutable typed plan, stage, outcome, and result models.
- Accept an explicit `ReleaseRecommendation`; do not infer it from history in this Task.
- Reuse Task 048 planning/preparation functions rather than reimplementing version logic.
- Reuse Task 050 strategy resolution rather than reading raw configuration directly.
- Route to exactly one existing publish path.
- Add `devbot release run --level major|minor|patch|none`.
- Add complete read-only `--dry-run` support.
- Support explicit release notes input for the direct path.
- Report the exact failed stage and preserve the original cause.
- Add tests and Result documentation.

## Out of Scope

- Aggregating multiple Contracts, Issues, PRs, or labels into a recommendation.
- Release-note generation from Git history.
- Creating, approving, or merging a release PR.
- Package publication.
- Removing or changing the public behavior of existing release commands.
- Automatically retrying a failed release.
- Review-loop, workflow-engine, or agent-dispatch changes.

## Deliverables

- `src/devbot/release_orchestration.py`
- CLI wiring in `src/devbot/main.py`
- Tests covering dry-run, both strategies, failures, and no-op recommendation.
- `results/051-release-orchestration.md`
- Roadmap and ADR updates where required.

## Acceptance Criteria

1. A typed orchestration plan includes current version, target version, recommendation, effective strategy, preparation requirement, and selected publish route.
2. `--dry-run` performs no filesystem, Git, GitHub, workflow-dispatch, tag, or Release write.
3. Recommendation `none` fails before any write and returns a dedicated error.
4. Real execution calls Task 048 preparation exactly once.
5. Strategy resolution occurs through Task 050's central resolver.
6. Workflow strategy invokes only the existing workflow publish path.
7. Direct strategy invokes only the existing direct publish path.
8. No execution can invoke both publish paths.
9. A failure is reported with a stable stage identifier and original exception chaining.
10. Existing release commands and tests remain backward compatible.
11. The Specification validates with zero errors and warnings.
12. Ruff and the complete test suite pass.

## Quality Gates

- `uv run devbot specification validate --task 51`
- `uv run ruff check .`
- `uv run pytest`

Required regression scenarios:

- Dry-run with workflow strategy.
- Dry-run with direct strategy.
- `none` recommendation.
- Preparation failure.
- Invalid strategy.
- Workflow publication failure.
- Direct publication failure.
- Direct strategy without required notes.
- Exactly one publish route called.
- Existing release CLI behavior unchanged.

## Handoff

- Continue all implementation and rework on `task/051-release-orchestration`.
- Use Issue #107 and create only one implementation PR.
- Produce `results/051-release-orchestration.md` before requesting review.
- Do not create a real tag, GitHub Release, workflow dispatch, or version change while implementing or testing this Task.

## References

- `src/devbot/release_classification.py`
- `src/devbot/release_preparation.py`
- `src/devbot/release_publish_strategy.py`
- `src/devbot/release_ops.py`
- `src/devbot/release_publish.py`
- `tasks/047-release-classification-policy.md`
- `tasks/048-release-preparation.md`
- `tasks/049-release-publish.md`
- `tasks/050-release-publish-strategy.md`
