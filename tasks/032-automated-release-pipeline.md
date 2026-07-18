# Task 032: Automated Release Pipeline

## Goal

Automatically create a verified stable GitHub Release after an eligible change reaches `main`, so later user installations can discover and update from versioned Release artifacts instead of a source checkout.

## Scope

1. Establish one authoritative semantic version source for DevBot.
2. Automatically determine the next release version from merged PR metadata.
3. Create the release tag and GitHub Release only after required `main` validation passes.
4. Build deterministic platform-specific release artifacts that Task 033 can install/update from.
5. Generate and publish SHA-256 checksums for every artifact.
6. Generate release notes from merged PR metadata.
7. Prevent duplicate tags, duplicate releases, and partially published stable releases.
8. Support an explicit manual workflow dispatch for recovery and controlled release operations.
9. Keep release creation independent from DevBot runtime, managed repositories, Task worktrees, and user configuration.

## Version Policy

Use semantic versioning with these merged-PR labels:

- `release:patch`: increment patch
- `release:minor`: increment minor
- `release:major`: increment major
- `release:none`: do not create a release for that merge

Rules:

- Exactly one release label is required for an eligible merged Task PR.
- Missing or conflicting release labels fail closed and do not publish a release.
- Until DevBot reaches `1.0.0`, breaking changes may use `release:minor` unless `release:major` is explicitly chosen.
- The first automated release may be bootstrapped from a documented initial version when no prior release tag exists.

## Out of Scope

- Global PATH launcher
- Runtime Release update client
- Automatic installation or upgrade on user machines
- Full interactive `devbot init`
- Homebrew, pipx, or OS package repositories
- systemd or Docker deployment
- Per-Agent model configuration
- Admin API or UI
- Automatic merge
- Delta updates

These user-runtime functions will be implemented in Task 033 after this Release contract is available.

## Planner Artifacts

- Task Issue: #66
- Branch: `task/032-global-launcher`
- Contract: `tasks/032-automated-release-pipeline.md`
- Pull Request: #67
- Result: `results/032-automated-release-pipeline.md`

## Checkpoints

### CP-032-1 — Authoritative version model

Define one authoritative version source used consistently by `devbot --version`, package metadata, artifact metadata, Git tags, and GitHub Releases.

Required tests:
- `test_runtime_and_package_version_use_authoritative_version_source`
- `test_release_tag_and_embedded_version_must_match`

### CP-032-2 — Release intent from merged PR metadata

Determine the release increment from exactly one release label on the merged PR. Ignore non-merged PRs and changes not reaching `main`.

Required tests:
- `test_release_increment_is_selected_from_merged_pr_label`
- `test_missing_or_conflicting_release_labels_fail_closed`
- `test_release_none_skips_publication`

### CP-032-3 — Deterministic next-version calculation

Calculate the next semantic version from the latest stable DevBot release tag. Drafts, prereleases, malformed tags, and releases from unrelated refs must not become the stable version base.

Required tests:
- `test_next_semantic_version_is_calculated_from_latest_stable_tag`
- `test_prerelease_draft_and_malformed_tags_are_ignored`

### CP-032-4 — Main validation gate

Release publication may begin only for the exact `main` commit after all required validation succeeds:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

A failed or cancelled validation must create no tag and no stable Release.

Required test/evidence:
- workflow dependency proves publication runs only after the validation job succeeds
- failure-path test or workflow fixture proves no publish step runs after validation failure

### CP-032-5 — Safe tag and Release creation

Create an immutable `v<version>` tag for the validated `main` commit and one matching GitHub Release. Refuse to overwrite or move an existing tag and refuse duplicate Release publication.

Required tests:
- `test_release_tag_targets_validated_main_commit`
- `test_duplicate_tag_or_release_is_rejected_without_mutation`

### CP-032-6 — Reproducible platform-specific artifact

Build deterministic platform-specific artifacts for the initial release contract:

- `devbot-<version>-linux-x86_64.tar.gz`
- `devbot-<version>-macos-arm64.tar.gz`

The artifact must include the real DevBot package source, locked dependency metadata, release metadata, and a launcher that Task 033 can consume. Platform-specific native launchers/installers are out of scope for this Task and will be handled by the installer/updater contract.

Required tests/evidence:
- `test_release_artifact_names_are_deterministic`
- workflow fixture proves the release job uses an OS/architecture matrix for `ubuntu-latest / linux / x86_64` and `macos-14 / macos / arm64`
- local package build demonstration

### CP-032-7 — Embedded artifact metadata

Each artifact must expose its DevBot version and supported platform without executing untrusted installation logic. Running the packaged CLI with `--version` must report the Release version.

Required test:
- `test_packaged_cli_reports_release_version`

### CP-032-8 — Checksum manifest

Generate a deterministic SHA-256 manifest covering every published artifact. The manifest must not be published until all expected artifacts are present.

Required tests:
- `test_checksum_manifest_covers_every_release_artifact`
- `test_checksum_manifest_is_deterministic`

### CP-032-9 — Atomic publication semantics

Upload build outputs to a draft Release first. Publish it as stable only after all artifacts, checksums, and validation evidence are complete. On failure, no incomplete stable Release may remain.

Required evidence:
- workflow creates or uses a draft during assembly
- publication occurs only after complete artifact verification
- failure cleanup or retained-draft behavior is documented and tested

### CP-032-10 — Automated release notes

Generate release notes from merged PR metadata, including PR number, title, and categorized change type without exposing secrets or internal prompts.

Required test/evidence:
- deterministic release-note generation test
- sample generated notes in the Task result

### CP-032-11 — Permissions and secret minimization

The workflow must use minimum GitHub Actions permissions. Release creation may use `GITHUB_TOKEN`; no long-lived PAT may be required. Untrusted PR code must not execute with release-write permission.

Required evidence:
- explicit minimal workflow permissions
- release job runs only from trusted `main` or manual dispatch context

### CP-032-12 — Concurrency and idempotency

Serialize releases per repository. Re-running the workflow for the same validated commit must either resume safely or report that the identical release already exists; it must never produce a second version.

Required tests/evidence:
- workflow concurrency group
- `test_release_rerun_is_idempotent_for_same_commit`

### CP-032-13 — Manual recovery dispatch

Provide `workflow_dispatch` inputs for an authorized operator to retry or create a controlled patch/minor/major release from a validated `main` commit. Manual release must obey the same validation, tag, checksum, and publication rules.

Required evidence:
- documented dispatch inputs and validation
- invalid or non-main commit is rejected

### CP-032-14 — Release result and auditability

Record in workflow summary and Task evidence:

- source commit SHA
- previous version
- selected increment
- new version
- tag
- artifact names
- checksums
- Release URL

Never log credentials or authorization headers.

Required test/evidence:
- safe summary fixture or workflow evidence

### CP-032-15 — Existing workflow compatibility

Normal PR CI, DevBot Task processing, worktree management, and manual merge remain unchanged. This Task must not make ordinary PRs publish Releases.

Required test/evidence:
- existing test suite passes
- release workflow trigger conditions exclude ordinary PR events

## Validation Gate

Run from the Task PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Additionally demonstrate without publishing a real stable Release:

- next-version calculation for patch, minor, major, and none
- deterministic platform-specific artifact generation
- complete checksum manifest generation
- release-note generation
- duplicate-release rejection
- draft-to-publish workflow structure

A real first stable Release remains a post-merge operational verification and must use the merged `main` commit.

## Definition of Done

- All checkpoints and required tests pass.
- Merging an eligible PR to `main` can automatically produce exactly one versioned stable Release after validation.
- Tag, runtime version, package version, artifact metadata, and Release version agree.
- The expected platform-specific artifact and its SHA-256 manifest are published atomically.
- Failed validation or packaging creates no incomplete stable Release.
- Release creation uses minimal permissions and no long-lived PAT.
- Result and PR Evidence record actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #66 and PR #67.
- Final merge remains manual.

## Branch and PR Policy

Use exactly one Issue, one Branch, one Contract, and one Pull Request. Continue implementation, review, and rework on the existing `task/032-global-launcher` branch and PR #67 despite the retained branch name. Do not create another Task 032 Issue, branch, contract, or PR.