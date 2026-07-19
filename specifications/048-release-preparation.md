# Specification: Task 048 — Release Preparation

## Provenance

- Task Issue: [#100](https://github.com/hjlee83/devbot/issues/100)
- Task Contract: `tasks/048-release-preparation.md`
- Runtime Dependency: `src/devbot/release_classification.py`
- Version Sources: `pyproject.toml`, `uv.lock`
- Epic: Release Platform
- Current Release: `v0.1.2`
- Generation: authored directly as the implementation-ready Specification for Task 048.

# Overview

## Goal

Implement a reviewable release-preparation boundary that turns the current project version and a typed release recommendation into a consistent next-version change set without performing release execution.

## Scope

In scope:

- Semantic-version parsing and bump calculation.
- Reading the authoritative current version from `pyproject.toml`.
- Verifying the DevBot package version in `uv.lock` matches before mutation.
- Updating both version sources consistently.
- Typed preparation results and stable errors.
- A minimal local command or public API for preparing changes.
- Tests, roadmap update, and Result documentation.

Out of scope:

- Tags, GitHub Releases, publishing, merging, or network writes.
- Release-note generation from repository history.
- Aggregating multiple Contracts or PRs.

## Background

Task 047 produces `ReleaseRecommendation` values but deliberately stops before calculating or changing a concrete version. The v0.1.2 release required the user to run `uv version 0.1.2` and refresh `uv.lock` manually. This task automates that local preparation while preserving a human-reviewable PR boundary.

## Roadmap Context

Task 048 begins the Release Platform by connecting release classification to a concrete, reviewable repository change. Release execution remains a later task.

# Functional Requirements

## Required Behaviour

Expose a pure calculation operation equivalent to:

```python
calculate_next_version(current: str, recommendation: ReleaseRecommendation) -> str
```

and a preparation operation equivalent to:

```python
prepare_release(project_root: Path, recommendation: ReleaseRecommendation) -> ReleasePreparationResult
```

Exact names may follow repository conventions.

Rules:

- `major`: `X.Y.Z -> X+1.0.0`
- `minor`: `X.Y.Z -> X.Y+1.0`
- `patch`: `X.Y.Z -> X.Y.Z+1`
- `none`: fail with a dedicated no-release-required error.
- Only stable three-component numeric versions are supported in Task 048.
- `pyproject.toml` is authoritative, but `uv.lock` must match it before any write.
- Both files must be prepared to the same target version.
- Validation must complete before either file is changed.

## Acceptance Criteria

1. Major, minor, and patch bumps are deterministic.
2. `none` is rejected explicitly.
3. Malformed versions are rejected with a stable typed error.
4. A `pyproject.toml` / `uv.lock` mismatch fails before mutation.
5. Successful preparation updates only the intended DevBot version entries.
6. Both files contain the same target version afterward.
7. The result reports old version, new version, recommendation, and changed paths.
8. Repeating preparation with an already-prepared target does not create corrupt or partial state; behavior is explicit and tested.
9. No network, GitHub, tag, release, publish, merge, or PR creation behavior is added.
10. Existing release-classification behavior remains the source of recommendation types.
11. All quality gates pass.

## Out of Scope

- Pre-release identifiers and build metadata.
- Pre-1.0 alternate SemVer policy.
- Automatically choosing a recommendation from repository history.
- Creating a release branch or pull request through GitHub.
- Executing `git`, creating tags, or publishing artifacts.

# Technical Design

## Architecture

```text
Contract Metadata
      |
      v
Task 047 Release Classification
      |
      v
ReleaseRecommendation
      |
      v
Task 048 Release Preparation
      |
      +-- validate current version sources
      +-- calculate target version
      +-- prepare pyproject.toml
      +-- prepare uv.lock
      v
ReleasePreparationResult
```

The preparation layer consumes Task 047 types and must not duplicate release-classification policy.

## Domain Model

Recommended public types:

```python
@dataclass(frozen=True)
class ReleasePreparationResult:
    recommendation: ReleaseRecommendation
    old_version: str
    new_version: str
    changed_paths: tuple[str, ...]

class ReleasePreparationError(RuntimeError): ...
class NoReleaseRequiredError(ReleasePreparationError): ...
class MalformedProjectVersionError(ReleasePreparationError): ...
class VersionSourceMismatchError(ReleasePreparationError): ...
class VersionSourceNotFoundError(ReleasePreparationError): ...
```

Tests should assert error types and structured fields rather than brittle full prose messages.

## Version Source Rules

- Read `[project].version` from `pyproject.toml`.
- Find the editable `[[package]]` entry for `name = "devbot"` in `uv.lock`.
- Reject missing or duplicate authoritative entries.
- Reject mismatched current versions.
- Change only the matched version values.
- Preserve unrelated file content and formatting as much as practical.
- Complete all parsing and validation before writing.
- Use temporary files plus atomic replacement, or an equivalent repository-consistent strategy, to avoid partial state.

## CLI

A minimal read-write local command is allowed and recommended:

```bash
uv run devbot release prepare --level patch
```

It should:

- accept `major`, `minor`, or `patch`;
- reject `none`;
- print old and new versions plus changed paths;
- operate only on the local repository;
- not commit, push, tag, create a PR, or contact GitHub.

The public preparation API remains the architectural boundary; the CLI should be a thin adapter.

## Files Expected to Change

Likely files:

- `src/devbot/release_preparation.py`
- `src/devbot/main.py` or the existing CLI command boundary
- focused tests under `tests/`
- `results/048-release-preparation.md`
- `docs/00-roadmap.md`

Avoid unrelated refactors and do not modify the repository's actual version as part of implementing this feature.

## Dependencies

- `src/devbot/release_classification.py`
- Python standard library TOML reading facilities or existing repository utilities
- `pyproject.toml`
- `uv.lock`

No new third-party dependency should be introduced unless unavoidable and explicitly justified.

## Constraints

- Do not call `uv version` or `uv lock` as opaque subprocesses from the core domain layer.
- Do not duplicate Task 047 classification logic.
- Do not infer release level from commits, labels, or PR titles.
- Do not introduce model-specific naming.
- Do not mutate Git or remote state.

## Migration Notes

Purely additive. Existing release operations remain manual until callers opt into the new preparation command.

# Validation

## Required Tests and Quality Gates

Required tests include:

- patch calculation (`0.1.2 -> 0.1.3`)
- minor calculation (`0.1.2 -> 0.2.0`)
- major calculation (`0.1.2 -> 1.0.0`)
- `none` rejection
- malformed and unsupported version rejection
- missing project version
- missing or duplicate DevBot lock entry
- version-source mismatch
- successful two-file preparation
- unrelated content preservation
- validation failure leaves both files unchanged
- repeated preparation behavior
- CLI success and failure paths if CLI is added

## Validation Commands

```bash
uv run devbot specification validate --task 48
uv run ruff check .
uv run pytest
```

## Success Criteria

All Acceptance Criteria pass, the full suite remains green, and no release-execution side effect is introduced.

# Safety

## Things the Implementation Agent Must NOT Do

- Do not change the repository's current version while implementing Task 048.
- Do not create Git tags or GitHub Releases.
- Do not commit, push, merge, publish, or call GitHub.
- Do not generate release notes from Git history.
- Do not silently repair mismatched version sources.
- Do not partially write one version file when validation or the second write fails.

# Completion

## Expected Deliverables

- Version calculation and release-preparation module.
- Optional thin local CLI adapter.
- Typed result and stable errors.
- Tests covering success, mismatch, malformed input, and atomic failure.
- Result document and roadmap update.

## Result Document

`results/048-release-preparation.md`

## PR Expectations

Implement Issue #100 on `task/048-release-preparation`, run all quality gates, write the Result document, and open a PR targeting `main`.

## Definition of Done

- Task 047 recommendation types are consumed directly.
- Version calculation is deterministic.
- Version-source consistency is enforced before mutation.
- Both files are prepared consistently and safely.
- No release execution or remote state mutation is present.
- Specification validation, lint, and full tests pass.

# Handoff

## Required Handoff Procedure

Implement Issue #100 using this Specification and Task Contract as authoritative sources. Keep work on the existing branch, do not bump the real project version during implementation, create the Result document, run all gates, and open a PR.

## Token-Limit Behaviour

If interrupted, leave a coherent committed state and record incomplete validation honestly. Do not weaken atomicity or tests to claim completion.

# Full Task Contract Reference

```markdown
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
```
