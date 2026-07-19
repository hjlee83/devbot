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
