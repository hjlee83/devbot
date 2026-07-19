# Specification: Task 049 — Release Publish

## Provenance

- Task Issue: [#103](https://github.com/hjlee83/devbot/issues/103)
- Task Contract: `tasks/049-release-publish.md`
- Runtime Dependencies: `src/devbot/release_preparation.py`, `src/devbot/release_ops.py`
- Epic: Release Platform
- Current Official Release: `v0.1.1`
- Prepared Project Version: read at runtime from `pyproject.toml`
- Generation: authored directly as the implementation authority for Task 049.

# Overview

## Goal

Implement the explicit publish boundary that turns an already-prepared project version on `main` into the corresponding Git tag and GitHub Release.

## Scope

The implementation must validate publication readiness, provide a side-effect-free preview, and then create the tag and GitHub Release only after all preconditions pass.

## Background

Task 047 recommends a release level. Task 048 calculates and writes the next project version. Task 049 must not repeat either responsibility. Its only purpose is to promote the version already committed to `main` into an official release.

## Roadmap Context

This task completes the first end-to-end release pipeline boundary:

```text
Contract Metadata
  -> Release Classification
  -> Release Preparation
  -> Release Publish
```

# Functional Requirements

## Required Behaviour

Expose focused operations equivalent to:

```python
preview_release_publish(...) -> ReleasePublishPreview
publish_prepared_release(...) -> ReleasePublishResult
```

Exact names may follow repository conventions.

The preview must be read-only and include at minimum:

- repository
- project version
- canonical tag
- release title
- target branch
- target commit SHA
- whether tag and release names are available

The publish operation must use the validated preview as its source of truth and must not recalculate or mutate the project version.

## Publication Preconditions

Before any write, verify all of the following:

1. `pyproject.toml` contains one valid stable `X.Y.Z` project version.
2. `uv.lock` contains the same `devbot` package version.
3. The canonical tag is `vX.Y.Z`.
4. The checked-out branch is `main`, unless the existing repository abstraction publishes directly from an explicitly verified `main` SHA without relying on the working branch.
5. The worktree has no uncommitted changes relevant to publication.
6. Local `main` and the intended remote `main` target are aligned or the exact remote commit is explicitly selected and verified.
7. Release notes are non-empty.
8. The tag does not already exist locally or remotely.
9. A GitHub Release for the tag does not already exist.

Every validation failure must occur before tag or Release creation.

## Publication Order and Partial Failure

Preferred order:

1. Create tag at the verified commit.
2. Push tag.
3. Create GitHub Release using that tag.

External publication is not a cross-system transaction. If tag push succeeds but GitHub Release creation fails:

- return or raise a dedicated partial-publication result/error that identifies the pushed tag;
- do not silently report full success;
- do not automatically delete the remote tag in Task 049;
- make retry behaviour explicit and safe so a subsequent invocation can complete the missing GitHub Release without moving or recreating the tag.

## Idempotency

- If both matching tag and GitHub Release already exist and point to the expected commit/tag, report an explicit already-published outcome.
- If only the expected tag exists and the Release is absent, permit a safe completion path that creates only the missing Release after verifying the tag target.
- If an existing tag points to another commit, fail closed.
- Never overwrite or force-move a tag.

## Acceptance Criteria

1. A prepared version can be previewed without external writes.
2. Publish creates one canonical tag and one matching GitHub Release.
3. The tag points to the verified `main` commit.
4. Version files are not modified.
5. Invalid repository state fails before publication.
6. Duplicate and partial states are handled deterministically and safely.
7. Repeated successful invocation never creates duplicate resources or moves tags.
8. Release notes are passed through exactly as supplied, apart from repository-standard newline normalization if needed.
9. Existing release abstractions are reused where appropriate rather than introducing a second unrelated GitHub release client.
10. Full validation passes.

## Out of Scope

- Version calculation or mutation.
- Automatic changelog or release-note generation.
- PR merge, approval, or branch deletion.
- Package/artifact publication.
- Multi-repository release orchestration.
- Automatic rollback of a pushed remote tag.
- A single command that runs Tasks 047–049 together.

# Technical Design

## Architecture

```text
pyproject.toml + uv.lock
        |
        v
Prepared Version Validation
        |
        v
Git / GitHub State Preview
        |
        v
ReleasePublishPreview
        |
        v
Explicit Publish
        |
        +--> Git tag at verified main SHA
        +--> Push tag
        +--> GitHub Release for same tag
```

## Domain Model

Recommended immutable types:

```python
@dataclass(frozen=True)
class ReleasePublishPreview:
    repository: str
    version: str
    tag: str
    title: str
    target_branch: str
    target_sha: str
    tag_state: TagState
    release_state: ReleaseState

@dataclass(frozen=True)
class ReleasePublishResult:
    version: str
    tag: str
    target_sha: str
    release_url: str | None
    outcome: PublishOutcome
```

Stable error categories should distinguish at least:

- malformed or mismatched version sources
- unsafe Git state
- non-main or stale-main state
- missing release notes
- conflicting existing tag
- conflicting existing GitHub Release
- partial publication

## CLI

Add or extend a command consistent with the existing release CLI, for example:

```bash
devbot release publish --notes-file RELEASE_NOTES.md --dry-run
devbot release publish --notes-file RELEASE_NOTES.md
```

Requirements:

- `--dry-run` performs no tag, push, or GitHub Release write.
- Release notes must come from an explicit argument or file; do not generate them.
- The CLI must present the target version, tag, and SHA before publishing.
- Existing repository selection conventions should be reused.

## Dependencies

- Existing authoritative version reader and Task 048 version consistency logic.
- Existing GitHub release operations under `src/devbot/release_ops.py` where compatible.
- Existing Git/GitHub adapters; avoid shell-command parsing inside domain policy where testable abstractions already exist.

## Constraints

- Never force-push or force-move tags.
- Never modify version files.
- Never publish from an unverified commit.
- Never claim atomicity across Git and GitHub.
- No model-specific naming.

## Migration Notes

Additive. Existing preview/publish release commands must remain compatible unless a narrowly scoped correction is required and regression-tested.

# Validation

## Required Tests and Quality Gates

Tests must cover:

- valid dry-run
- successful tag and Release publication
- version-source mismatch
- malformed version
- dirty worktree
- non-main or stale-main state
- empty release notes
- existing matching tag and matching Release
- existing matching tag with missing Release
- existing tag pointing to another SHA
- GitHub Release creation failure after successful tag push
- retry after partial publication
- proof that `pyproject.toml` and `uv.lock` remain unchanged
- proof that no force tag operation is used

All external Git and GitHub effects must use fakes or mocks.

## Validation Commands

```bash
uv run devbot specification validate --task 49
uv run ruff check .
uv run pytest
```

## Success Criteria

All Acceptance Criteria and quality gates pass, and no version preparation, release-note generation, merge, or package publication behaviour is introduced.

# Safety

## Things the Implementation Agent Must NOT Do

- Do not create a real tag or GitHub Release during tests or implementation validation.
- Do not change the current project version.
- Do not force-update tags.
- Do not automatically delete a pushed remote tag after partial failure.
- Do not generate release notes from history.
- Do not merge PRs or publish packages.

# Completion

## Expected Deliverables

- Publish preview and result models.
- Safe Git/GitHub publication service.
- CLI wiring.
- Focused tests.
- `results/049-release-publish.md`.
- Roadmap update.

## Result Document

`results/049-release-publish.md`

## PR Expectations

Implement Issue #103 on `task/049-release-publish`, validate with fakes/mocks only, and open a PR targeting `main`.

## Definition of Done

- Prepared version is published without mutation.
- Tag and Release share the same canonical version.
- Unsafe, duplicate, conflicting, and partial states are explicit.
- Dry-run is side-effect free.
- No unrelated release automation is introduced.
- Specification validation, lint, and tests pass.

# Handoff

## Required Handoff Procedure

Implement Issue #103 using this Specification and its Task Contract as authoritative sources. Commit to the existing branch and open a PR after validation.

## Token-Limit Behaviour

If interrupted, leave a coherent committed branch and record incomplete external-state tests honestly. Do not create real publication resources to prove completion.

# Full Task Contract Reference

```markdown
# Task 049 — Release Publish

## Contract Version

1

## Provenance

- GitHub Issue: #103
- Branch: `task/049-release-publish`
- Epic: Release Platform
- Current Release: `v0.1.1`

## Task Identity

- id: 049
- title: Release Publish

## Metadata

- specification_type: feature
- release_impact: feature
- risk_level: high
- compatibility: backward
- migration: none

## Goal

Publish an already-prepared project version as the matching Git tag and GitHub Release through a safe, explicit, and reviewable operation.

## Context

Task 047 classifies release impact and Task 048 prepares the concrete project version in `pyproject.toml` and `uv.lock`. DevBot still needs a separate publish boundary that promotes the prepared version to an official release without recalculating or modifying it.

## Scope

- Read the authoritative version from `pyproject.toml`.
- Derive the canonical tag `vX.Y.Z`.
- Require publishing from the intended `main` commit.
- Reject dirty worktrees and version/tag mismatches.
- Reject existing tags or existing GitHub Releases rather than overwriting them.
- Create and push the Git tag.
- Create the GitHub Release using explicit release notes supplied by the caller.
- Return a typed publish result.
- Provide a dry-run or preview path that performs no writes.
- Add focused tests and `results/049-release-publish.md`.

## Out of Scope

- Calculating the next release level or version.
- Modifying `pyproject.toml` or `uv.lock`.
- Merging or approving release PRs.
- Generating release notes from Git history, PRs, or Contracts.
- Publishing Python packages or other artifacts.
- Workflow, Review Loop, merge, or agent-dispatch automation.
- Automatically recovering or deleting a remotely created tag or Release after partial external failure.

## Deliverables

- Release publish domain/service implementation.
- Typed publish preview/result and stable errors.
- CLI integration consistent with existing release commands.
- Tests covering safe publication and failure cases.
- Result document and roadmap update.

## Acceptance Criteria

1. The publish operation consumes the current prepared version and never increments it.
2. The canonical tag is exactly `v{project_version}`.
3. Dry-run returns the intended target commit, tag, and release title without external writes.
4. A dirty worktree, non-main branch, mismatched local/remote main, malformed version, or missing release notes fails closed before publication.
5. Existing local/remote tags or existing GitHub Releases fail with dedicated errors and are not overwritten.
6. The tag points to the verified target commit.
7. The GitHub Release uses the exact same tag.
8. Re-running after successful publication reports an explicit duplicate/already-published outcome rather than creating another release.
9. Version files remain byte-for-byte unchanged.
10. No package publication, version preparation, merge, or release-note generation is introduced.
11. Specification validation, lint, and the full test suite pass.

## Quality Gates

- `uv run devbot specification validate --task 49`
- `uv run ruff check .`
- `uv run pytest`
- Tests use fakes/mocks for Git and GitHub writes and must not create a real tag or Release.

## Handoff

Use `specifications/049-release-publish.md` as the implementation authority. Work only on `task/049-release-publish`, create the Result document, run all quality gates, and open a PR targeting `main`.

## References

- Issue #103
- `src/devbot/release_preparation.py`
- `src/devbot/release_ops.py`
- `pyproject.toml`
```
