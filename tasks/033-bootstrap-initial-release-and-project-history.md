# Task 033: Bootstrap Initial Release and Project History

## Goal

Create DevBot's first official stable GitHub Release from the validated Task 032 `main`
commit, and establish canonical project history plus the standard future Release Notes
format.

## Context

Task 032 added the automated release pipeline. Task 033 is the bootstrap operation for the
first stable Release. The authoritative package version is `0.1.0`, no prior stable Release is present in local tag history, and the source commit is the final `main` commit produced after Task 034 is merged and the release workflow is dispatched. Do not hard-code a future merge SHA before it exists.

The only existing local version tag before this Task is prerelease tag `v0.1.0-alpha.1`.
No retrospective stable tags may be added to older commits.

## In scope

- Use the Task 032 release pipeline contract for the first stable Release.
- Target stable tag `v0.1.0` at the final `main` commit produced after Task 034 is merged.
- Build platform-specific artifacts: `devbot-0.1.0-linux-x86_64.tar.gz` and `devbot-0.1.0-macos-arm64.tar.gz`.
- Generate and verify `SHA256SUMS`.
- Smoke-test the packaged `devbot --version`.
- Write initial Release Notes summarizing work completed through Task 032.
- Add `docs/history.md` as the canonical project history through Task 032.
- Define the future generated Release Notes structure.
- Record source commit, version, tag, artifact, checksum, Release URL, and validation
  evidence in the Result.

## Out of scope

- Retrospective stable tags or Releases for older commits.
- Runtime automatic update discovery or update client.
- Package-manager distribution.
- Automatic merge.
- Per-Agent model configuration.

## Deliverables

- `tasks/033-bootstrap-initial-release-and-project-history.md`
- `results/033-bootstrap-initial-release-and-project-history.md`
- `docs/history.md`
- Initial stable Release Notes content for `v0.1.0`
- Platform-specific artifact names and `SHA256SUMS` evidence

## Functional requirements

1. The first stable Release version must be `0.1.0` only when the authoritative package
   version is `0.1.0` and no prior stable Release exists.
2. The first official stable tag must be `v0.1.0` and must target exactly
   the final `main` commit produced after Task 034 is merged.
3. Stable Release creation must fail safely if stable Release `v0.1.0` already exists or
   if tag `v0.1.0` points anywhere else.
4. The platform-specific artifacts must be named `devbot-0.1.0-linux-x86_64.tar.gz` and `devbot-0.1.0-macos-arm64.tar.gz`.
5. `SHA256SUMS` must cover both platform-specific artifacts exactly.
6. Packaged `devbot --version` must print `devbot 0.1.0`.
7. Initial Release Notes and future generated Release Notes must use these sections:
   - What's New
   - Improvements
   - Fixes
   - Operational Changes
   - Upgrade Notes
   - Known Limitations
8. `docs/history.md` must preserve meaningful milestones through Task 032 and must not
   imply old commits received stable Releases.

## Quality gates

### CP-033-1 — Initial stable version selection

Verify that the authoritative package version is `0.1.0`, the target tag is `v0.1.0`,
and prerelease history does not become a prior stable Release.

Required test:
- `test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract`
- `test_release_plan_bootstraps_first_stable_release_from_authoritative_initial_version`
- `test_manual_release_plan_bootstraps_first_stable_from_initial_version`
- `test_release_pipeline_plan_command_bootstraps_first_stable_release`

### CP-033-2 — Stable Release safety

Verify that an existing stable Release or a same-name tag on another commit is rejected
without mutation.

Required test:
- `test_initial_release_rejects_prior_stable_release_or_moved_tag`

### CP-033-3 — Artifact and checksum evidence

Verify deterministic platform-specific artifact naming, complete checksum coverage, and packaged
CLI version agreement.

Required tests:
- `test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract`
- `test_packaged_cli_reports_release_version`
- `test_checksum_manifest_covers_every_release_artifact`

### CP-033-4 — Release Notes standard

Verify the first Release Notes use the canonical future section order.

Required test:
- `test_initial_release_notes_use_standard_future_sections`

### CP-033-5 — Canonical project history

Verify `docs/history.md` records the development history through Task 032 and clearly
defines future Release Notes sections.

Required test:
- `test_initial_release_notes_use_standard_future_sections`

## Verification commands

```bash
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv sync
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run ruff check .
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run pytest
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run devbot doctor --ci
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run devbot --once --dry-run
```

Additional local release evidence commands:

```bash
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py version --project-root .
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py build-artifact --version 0.1.0 --os-name linux --architecture x86_64 --output-dir /tmp/devbot-task033-release --project-root .
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py build-artifact --version 0.1.0 --os-name macos --architecture arm64 --output-dir /tmp/devbot-task033-release --project-root .
UV_CACHE_DIR=/tmp/devbot-task033-uv-cache uv run python scripts/release_pipeline.py checksum-manifest --version 0.1.0 --artifact-dir /tmp/devbot-task033-release --output /tmp/devbot-task033-release/SHA256SUMS
/tmp/devbot-task033-smoke/devbot-release/bin/devbot --version
```

## Git rules

- Use the existing Task 033 Issue, branch, contract, Result, and Pull Request.
- Do not create another Issue, branch, contract, or Pull Request.
- Do not create retrospective stable tags.
- Do not move an existing tag.
- Final merge and real stable publication remain operator-controlled.

## PR evidence requirements

The PR evidence and Result must include:

- Source commit SHA
- Version and tag
- Artifact name
- SHA-256 checksum
- Release URL or intended Release URL
- Validation command results
- Manual release evidence
- Remaining operator-controlled publication step, if the environment prevents actual
  GitHub Release publication
