# Specification: Task 051 — Release Orchestration

## Provenance

- GitHub Issue: #107
- Branch: `task/051-release-orchestration`
- Generation Source: Architect-authored Specification for Task 051.
- Task Contract: `tasks/051-release-orchestration.md`

# Overview

## Goal

Provide one operator-facing release command that composes the existing Task 047–050 capabilities in a deterministic, fail-closed sequence.

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

## Background

Tasks 047–050 now provide four separate capabilities:

1. `ReleaseRecommendation` (`major`, `minor`, `patch`, `none`).
2. Safe version preparation in `pyproject.toml` and `uv.lock`.
3. Repository-level selection of exactly one publish strategy.
4. Existing workflow and direct publish implementations.

The operator still has to remember and sequence multiple commands. Task 051 introduces the first unified release command while preserving every existing safety boundary.

## Roadmap Context

Task 051 is the integration boundary after:

- Task 047: release recommendation policy.
- Task 048: release preparation.
- Task 049: direct release publish.
- Task 050: repository publish strategy.

It does not replace those components. It coordinates them.

# Functional Requirements

## Required Behaviour

**Public API.** Add `src/devbot/release_orchestration.py` with public APIs equivalent to `build_release_run_plan(...)` and `run_release(...)`. Exact parameter shapes may follow existing repository conventions, but orchestration must use typed inputs and outputs.

**Typed domain model.** Define immutable models for at least `ReleaseRunStage`, `ReleaseRunPlan`, `ReleaseRunResult`, `ReleaseRunOutcome`. Stable stage values must distinguish recommendation, preparation, strategy_resolution, workflow_publish, and direct_publish.

**Recommendation input.** The command accepts an explicit recommendation (`major | minor | patch | none`) and must convert this through the existing `ReleaseRecommendation` type. This Task must not derive a recommendation from Git history, labels, Contracts, or PR aggregation. `none` must raise a dedicated orchestration error before any write.

**Planning.** The read-only planning operation must determine the current project version, target semantic version, recommendation, effective publish strategy, whether preparation would modify files, selected publish route, and whether direct-route notes are available. Planning must reuse Task 048's planning function and Task 050's resolver.

**Dry run.** `devbot release run --level patch --dry-run` must render the complete plan and perform no write, including no modification of `pyproject.toml` or `uv.lock`, no Git tag creation, no Git push, no GitHub Release creation, no workflow dispatch, and no package publication.

**Real execution order.** Real execution must follow this order: (1) validate recommendation, (2) build and validate the preparation plan, (3) resolve the repository publish strategy, (4) execute release preparation exactly once, (5) invoke exactly one selected publish path, (6) return a typed result. No publish function may run if preparation fails.

**Workflow strategy.** When the effective strategy is `workflow`, use the existing Task 037 workflow publish orchestration, do not call Task 049 direct publish, and preserve existing workflow readiness, CI validation, notes, dispatch, polling, and publication validation behavior. The implementation may adapt existing function inputs but must not duplicate their policy.

**Direct strategy.** When the effective strategy is `direct`, use the existing Task 049 direct publish orchestration, do not call workflow dispatch, require explicit non-empty release notes from a CLI notes file, and preserve Task 049 tag/Release conflict, partial publication, and retry semantics.

**CLI.** Add `devbot release run --level major|minor|patch|none [--repo owner/repo] [--notes-file PATH] [--dry-run]`. `--level` is required. `--repo` follows existing repository resolution behavior. `--notes-file` is required for real direct publication and must be validated before preparation writes. Dry-run may report missing direct notes as a blocker rather than performing a write. Errors return non-zero exit status and name the failed stage.

**Error model.** Add a base orchestration error and dedicated errors for at least: no release required, missing direct release notes, and stage failure. Stage failure must preserve the original exception using exception chaining and expose the failed stable stage.

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

## Out of Scope

- Aggregating multiple Contracts, Issues, PRs, or labels into a recommendation.
- Release-note generation from Git history.
- Creating, approving, or merging a release PR.
- Package publication.
- Removing or changing the public behavior of existing release commands.
- Automatically retrying a failed release.
- Review-loop, workflow-engine, or agent-dispatch changes.

# Technical Design

## Architecture

### Public API

```python
build_release_run_plan(...)
run_release(...)
```

### Typed Domain Model

Immutable models for at least `ReleaseRunStage`, `ReleaseRunPlan`, `ReleaseRunResult`, `ReleaseRunOutcome`. Stable stage values must distinguish:

- recommendation
- preparation
- strategy_resolution
- workflow_publish
- direct_publish

### CLI

```bash
devbot release run --level major|minor|patch|none [--repo owner/repo] [--notes-file PATH] [--dry-run]
```

### Error Model

- A base orchestration error.
- No release required (`none` recommendation).
- Missing direct release notes.
- Stage failure — preserves the original exception via exception chaining and exposes the failed stable stage.

## Files Expected to Change

Expected additions or modifications include:

- `src/devbot/release_orchestration.py`
- `src/devbot/main.py`
- `tests/test_release_orchestration.py`
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` when an architectural decision requires recording
- `results/051-release-orchestration.md`

Other files may change only when necessary for typed reuse or regression coverage and must be explained in the Result document.

## Dependencies

- `src/devbot/release_classification.py`
- `src/devbot/release_preparation.py`
- `src/devbot/release_publish_strategy.py`
- `src/devbot/release_ops.py`
- `src/devbot/release_publish.py`

## Constraints

- Do not parse raw `publish_strategy` outside Task 050's resolver.
- Do not duplicate semantic-version calculation.
- Do not reimplement Git tag, Release, or workflow-dispatch logic.
- Do not create network clients during a dry-run unless an existing read-only plan absolutely requires one; any such reads must not write.
- Never invoke both publish strategies in one execution.

## Migration Notes

All existing release commands remain available and retain their current behavior: `release prepare`, `release publish`, `release publish-prepared`, `release strategy`, and the existing preview/status commands.

No configuration migration is required. Repositories without `publish_strategy` continue to resolve to `workflow` through Task 050.

# Validation

## Required Tests and Quality Gates

Tests must cover:

- Plan construction for all recommendations.
- Workflow and direct strategy dry-runs.
- `none` rejection.
- Invalid strategy.
- Preparation mismatch or malformed version.
- Missing direct notes before write.
- Workflow route success/failure.
- Direct route success/failure.
- Exactly-one-route invariant.
- CLI exit codes and output.
- Existing CLI regressions.

## Validation Commands

```bash
uv run devbot specification validate --task 51
uv run ruff check .
uv run pytest
```

## Success Criteria

1. The complete dry-run plan is deterministic and write-free.
2. `none` fails before preparation.
3. Preparation is reused and invoked exactly once in real execution.
4. Strategy is resolved centrally.
5. Workflow strategy invokes only workflow publish.
6. Direct strategy invokes only direct publish.
7. Missing direct notes fail before version files are modified.
8. Any downstream failure identifies its stage and retains its original cause.
9. Existing release command behavior is unchanged.
10. No real external release side effect occurs during testing.

# Safety

## Things the Implementation Agent Must NOT Do

- Must not change the repository's real version, create a real tag, dispatch a real workflow, or create a real GitHub Release, during implementation or testing.
- Must not derive a recommendation from Git history, labels, Contracts, or PR aggregation in this Task.
- Must not duplicate Task 048's version-calculation logic or Task 050's strategy-parsing logic anywhere else.
- Must not invoke both publish strategies in one execution.
- Use temporary repositories and mocks consistent with Tasks 048–050.

# Completion

## Expected Deliverables

- `src/devbot/release_orchestration.py`
- CLI wiring in `src/devbot/main.py`
- Tests covering dry-run, both strategies, failures, and no-op recommendation.
- `results/051-release-orchestration.md`
- Roadmap and ADR updates where required.

## Result Document

Create `results/051-release-orchestration.md` containing:

- Architecture and reuse decisions.
- Execution order.
- Dry-run evidence.
- Both strategy paths.
- Failure and recovery behavior.
- Test results.
- Any deviations from this Specification.

## PR Expectations

- One PR from `task/051-release-orchestration` to `main`.
- Reference and close Issue #107.
- Explain how dry-run proves zero writes.
- Explain how exactly one publish route is guaranteed.
- List full validation results.

# Handoff

## Required Handoff Procedure

1. Implement only on `task/051-release-orchestration`.
2. Keep Issue #107 as the task identity.
3. Run all required validation commands.
4. Create the Result document.
5. Open one PR to `main`.
6. Apply all review fixes to the same branch and PR.

## Token-Limit Behaviour

When implementation context is constrained, prioritize in this order:

1. Safety boundaries and no-write dry-run.
2. Reuse of Tasks 047–050.
3. Exactly-one-route invariant.
4. Tests.
5. Documentation.

Do not silently reduce acceptance criteria or expand scope.

# Full Task Contract Reference

```markdown
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
```
