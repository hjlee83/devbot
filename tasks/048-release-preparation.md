# Task 048 — Release Preparation

## Contract Version

1

## Provenance

- GitHub Issue: #100
- Branch: `task/048-release-preparation`
- Epic: Release Platform
- Current Release: `v0.1.2`

## Task Identity

- id: 048
- title: Release Preparation

## Metadata

- specification_type: feature
- release_impact: feature
- risk_level: medium
- compatibility: backward
- migration: none

## Goal

Implement a safe, deterministic release-preparation boundary that calculates the next concrete version and updates the repository's version sources into a reviewable change set.

## Context

Task 047 produces a typed release recommendation but does not calculate or apply a concrete version. The v0.1.2 release was prepared manually by updating `pyproject.toml` and `uv.lock`. Task 048 automates that preparation step while keeping tagging, publishing, merging, and GitHub Release creation outside the boundary.

## Scope

- Consume a typed `ReleaseRecommendation` of `major`, `minor`, or `patch`.
- Read the current version from `pyproject.toml` as the authoritative source.
- Calculate the next semantic version deterministically.
- Update `pyproject.toml` and the DevBot package entry in `uv.lock` consistently.
- Fail closed on unsupported, malformed, or inconsistent version state.
- Produce a typed preparation result describing old version, new version, recommendation, and changed files.
- Provide a minimal local CLI command for reviewable preparation if consistent with repository conventions.
- Add tests and `results/048-release-preparation.md`.

## Out of Scope

- Git tags or GitHub Releases.
- Package publication.
- Merging or approving release PRs.
- GitHub API writes.
- Full release-note generation from commits or PRs.
- Multi-task recommendation aggregation.
- Review Loop, Workflow Engine, or agent dispatch changes.

## Deliverables

- Release version calculation and preparation module.
- Consistent `pyproject.toml` and `uv.lock` updates.
- Stable typed results and errors.
- Focused unit and integration tests.
- Result document and roadmap update.

## Acceptance Criteria

1. `major`, `minor`, and `patch` recommendations calculate deterministic next versions.
2. The current version is read from `pyproject.toml`; no second authoritative version source is introduced.
3. `pyproject.toml` and the DevBot entry in `uv.lock` are updated to the same target version.
4. A pre-existing mismatch between the two files fails closed before mutation.
5. `none` is rejected as not requiring release preparation.
6. Malformed or unsupported version strings fail with stable typed errors.
7. Preparation is idempotent and does not partially write files on validation failure.
8. The result reports old version, new version, recommendation, and changed paths.
9. No tag, GitHub Release, publication, merge, or network operation occurs.
10. Existing Task 042–047 behavior remains compatible.
11. Specification validation, lint, and the full test suite pass.

## Quality Gates

- `uv run devbot specification validate --task 48`
- `uv run ruff check .`
- `uv run pytest`
- Tests cover all recommendation levels, `none`, malformed versions, mismatched version sources, atomic failure, and repeated preparation.

## Handoff

Use `specifications/048-release-preparation.md` as the implementation authority. Work only on `task/048-release-preparation`, create the Result document, run all quality gates, and open a PR targeting `main`.

## References

- Issue #100
- `src/devbot/release_classification.py`
- `pyproject.toml`
- `uv.lock`
