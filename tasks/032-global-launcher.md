# Task 032: Global Launcher and Release-Based Update

## Goal

Allow users to run `devbot` from any directory and keep DevBot current through verified GitHub Release artifacts rather than by updating a source checkout with `git pull`.

## Scope

1. Provide a thin global launcher named `devbot` on `PATH`.
2. Persist and discover the user installation independently of the caller's current directory.
3. Forward CLI arguments, environment, signals, standard streams, and exit status exactly.
4. Check the latest stable GitHub Release before normal startup.
5. Download and atomically activate the matching macOS/Linux release artifact when a newer version exists.
6. Verify release integrity before activation.
7. Restart into the new version once and prevent update loops.
8. Preserve user configuration, credentials, managed repositories, Task branches, PR branches, and PreparedWorkspaces.
9. Provide safe rollback when download, verification, extraction, activation, or restart fails.
10. Keep a documented developer/source-checkout mode separate from the normal user release-installation mode.

## Out of Scope

- Full interactive `devbot init` wizard
- Homebrew, pipx, package-index, or OS package distribution
- systemd or Docker service management
- Multiple installations or profile selection
- Per-Agent model configuration
- Admin API or UI
- Automatic repository cloning
- Automatic merge
- Delta/binary patch updates

## Planner Artifacts

- Task Issue: #66
- Branch: `task/032-global-launcher`
- Contract: `tasks/032-global-launcher.md`
- Pull Request: #67
- Result: `results/032-global-launcher.md`

## Checkpoints

### CP-032-1 — Versioned installation metadata

Store secret-free, versioned metadata for the active installation, including installation root, current version, release channel, platform, architecture, and active executable path.

Required test: `test_install_metadata_round_trips_release_installation`

### CP-032-2 — Current-directory-independent launcher

The PATH launcher resolves the installed DevBot from metadata and works outside any Git repository.

Required test: `test_global_launcher_runs_from_arbitrary_directory`

### CP-032-3 — Thin stable bootstrap

The launcher contains only discovery, update bootstrap, delegation, restart, and recovery logic. Planner and Agent business logic remain in DevBot.

Required test: `test_launcher_delegates_to_installed_devbot_entrypoint`

### CP-032-4 — Exact process parity

Forward all arguments and standard streams, preserve signals, replace the launcher process where supported, and return the delegated exit code.

Required tests:
- `test_launcher_forwards_all_arguments_exactly`
- `test_launcher_preserves_delegated_exit_code`

### CP-032-5 — Release discovery

Query the configured GitHub repository's latest stable Release, compare semantic versions, ignore drafts and prereleases on the stable channel, and skip downloading when already current.

Required tests:
- `test_release_update_detects_newer_stable_version`
- `test_release_update_skips_current_older_prerelease_and_draft_versions`

### CP-032-6 — Platform artifact selection

Select exactly one supported artifact for the current OS and architecture. Unsupported or ambiguous platforms fail before changing the active installation.

Required test: `test_release_update_selects_exact_platform_artifact`

### CP-032-7 — Integrity and authenticity verification

Every release must include a checksum manifest. Verify the downloaded artifact before extraction or activation. A missing or mismatched checksum fails closed and leaves the current version active.

Required tests:
- `test_release_update_verifies_checksum_before_activation`
- `test_release_update_rejects_missing_or_mismatched_checksum`

### CP-032-8 — Atomic activation and rollback

Download and extract into a staging directory, validate the staged executable, atomically switch the active version, retain the previous version until successful restart, and roll back on failure. Never overwrite the running executable in place.

Required tests:
- `test_release_update_activates_atomically`
- `test_release_update_rolls_back_after_activation_failure`

### CP-032-9 — Restart semantics

After successful activation, exec-restart once with the original argv and a loop-prevention marker. The restarted process must report the new version before normal startup.

Required tests:
- `test_release_update_restarts_into_new_version`
- `test_release_update_does_not_restart_twice`

### CP-032-10 — User-data isolation

Updates must not modify user configuration, secrets, managed repositories, worktrees, Task/PR branches, or workspace data. Installation binaries and mutable user data use separate roots.

Required test: `test_release_update_preserves_user_data_and_managed_repositories`

### CP-032-11 — Installation and launcher refresh

Provide an idempotent user-scoped install/refresh command that records metadata, creates the PATH launcher without root privileges, and reports the exact PATH action when required.

Required tests:
- `test_launcher_install_is_idempotent`
- `test_launcher_install_reports_missing_path_entry`

### CP-032-12 — Safe offline and API-failure behavior

If release checking fails because of network, rate limit, or GitHub API failure, continue with the current verified installation by default and emit a concise warning. Do not partially update. Explicit update commands may return failure.

Required test: `test_startup_uses_current_verified_version_when_release_check_fails`

### CP-032-13 — Source/developer mode separation

A source checkout may be used only through an explicit developer mode. Normal user startup must not run `git fetch`, `git pull`, `git switch`, `git merge`, or mutate a source checkout.

Required tests:
- `test_user_release_mode_never_invokes_git_update_commands`
- `test_developer_mode_is_explicit_and_separate`

### CP-032-14 — Release build workflow

Add a reproducible release workflow that creates supported platform artifacts, embeds the version, emits checksums, and attaches them to a tagged GitHub Release. The update client and workflow must share deterministic artifact naming.

Required tests or evidence:
- artifact naming unit test
- workflow configuration validation
- local deterministic package/checksum demonstration

### CP-032-15 — Safe diagnostics

Display current version, latest version, channel, platform, update result, and recovery action without exposing tokens, release API credentials, prompts, or unrelated environment values.

Required test: `test_release_update_diagnostics_are_actionable_and_secret_safe`

### CP-032-16 — Existing CLI compatibility

`devbot`, `devbot doctor`, `devbot --once --dry-run`, `devbot --version`, and worktree commands behave identically through the global launcher.

Required test: `test_existing_commands_work_through_global_launcher`

## Validation Gate

Run from the Task PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Also demonstrate without using a source checkout as cwd:

```bash
devbot --version
devbot doctor
```

Demonstrate deterministic tests for no-update, successful update, checksum failure, unsupported platform, API/network failure, atomic rollback, and restart-loop prevention without modifying a real user installation.

## Definition of Done

- All checkpoints and required tests pass.
- `devbot` runs from arbitrary directories.
- Normal user updates consume verified GitHub Release artifacts and never use `git pull`.
- Activation is atomic and rollback-safe.
- User data and managed repositories remain isolated from installation binaries.
- Release packaging and updater artifact names are deterministic and compatible.
- Result and PR Evidence record actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #66 and PR #67.
- Final merge remains manual.

## Branch and PR Policy

Use exactly one Issue, one Branch, one Contract, and one Pull Request. Continue all implementation, review, and rework on `task/032-global-launcher` and PR #67. Do not create a separate Issue, branch, contract, or PR.