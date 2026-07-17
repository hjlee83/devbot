# Task 032: Global Launcher and Install Root Discovery

## Goal

Allow users to run `devbot` from any directory without manually changing into the DevBot operator checkout, while preserving Task 031 startup self-update and restart guarantees.

## Scope

1. Provide a thin global launcher named `devbot` that can be placed on `PATH`.
2. Persist and discover the canonical DevBot operator checkout independently of the caller's current directory.
3. Forward all CLI arguments, environment, signals, standard streams, and exit status to the canonical DevBot executable.
4. Ensure Task 031 startup self-update operates on the discovered operator checkout only.
5. Provide an explicit installation command for creating or refreshing the launcher and installation metadata.
6. Fail safely with actionable diagnostics when installation metadata or the operator checkout is invalid.
7. Keep Local and VPS-style filesystem layouts compatible.

## Out of Scope

- Full interactive `devbot init` wizard
- Homebrew, pipx, package-index, or OS package distribution
- systemd or Docker service management
- Multiple DevBot installations or profile selection
- Per-Agent model configuration
- Admin API or UI
- Automatic repository cloning
- Automatic merge

## Planner Artifacts

- Task Issue: #66
- Branch: `task/032-global-launcher`
- Contract: `tasks/032-global-launcher.md`
- Pull Request: this Task's single Planner PR
- Result: `results/032-global-launcher.md`

## Checkpoints

### CP-032-1 — Persistent install metadata

Store the canonical operator checkout in a user-scoped configuration location following platform conventions. The metadata format must be versioned, deterministic, and contain no secrets.

Required test: `test_install_metadata_round_trips_operator_checkout`

### CP-032-2 — Current-directory-independent discovery

The launcher resolves the operator checkout from install metadata and works from arbitrary directories, including outside any Git repository.

Required test: `test_global_launcher_runs_from_arbitrary_directory`

### CP-032-3 — Thin launcher boundary

The PATH launcher remains a small, stable bootstrap layer. It must not duplicate DevBot business logic, Git update logic, planner logic, or Agent logic.

Required test: `test_launcher_delegates_to_canonical_devbot_entrypoint`

### CP-032-4 — Argument, stream, signal, and exit-code parity

The launcher forwards CLI arguments exactly, preserves stdin/stdout/stderr behavior, replaces itself with the delegated process where supported, and returns the delegated exit code.

Required tests:
- `test_launcher_forwards_all_arguments_exactly`
- `test_launcher_preserves_delegated_exit_code`

### CP-032-5 — Explicit launcher installation

Provide an idempotent command such as `devbot launcher install` or an equivalent documented command that:

- records the current canonical operator checkout
- creates or refreshes the PATH launcher
- does not require manual editing of shell files when the selected user bin directory is already on PATH
- reports the exact PATH action required when it is not on PATH
- never requires root privileges for the default installation

Required tests:
- `test_launcher_install_is_idempotent`
- `test_launcher_install_reports_missing_path_entry`

### CP-032-6 — Operator checkout validation

Before delegation, validate that the recorded path exists, is the DevBot operator checkout, contains the expected executable environment or recoverable source installation, and is not a Task worktree.

Required test: `test_launcher_rejects_missing_invalid_or_worktree_install_root`

### CP-032-7 — Task 031 self-update compatibility

Running the global launcher must preserve Task 031 behavior:

- update only the DevBot operator checkout `main`
- use fast-forward-only semantics
- restart into updated code when HEAD changes
- never update managed repositories, Task branches, PR branches, or PreparedWorkspaces
- avoid launcher/self-update restart loops

Required test: `test_global_launcher_preserves_startup_self_update_and_restart_semantics`

### CP-032-8 — Safe environment and diagnostics

Installation metadata and launcher diagnostics must not expose tokens, credentials, prompts, Authorization headers, or unrelated environment values. Errors must identify the failed path and recovery command without dumping the full environment.

Required test: `test_launcher_diagnostics_are_actionable_and_secret_safe`

### CP-032-9 — Local and VPS path compatibility

Support macOS and Linux user-scoped configuration/bin conventions without hard-coding `/Users/luna`, `~/workspace/devbot`, or one shell.

Required test: `test_launcher_paths_follow_platform_and_environment_overrides`

### CP-032-10 — Existing CLI compatibility

Existing commands including `devbot`, `devbot doctor`, `devbot --once --dry-run`, `devbot --version`, and worktree-related commands behave the same when invoked through the global launcher.

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

Also demonstrate from at least two directories outside the operator checkout that the global launcher:

```bash
devbot --version
devbot doctor
```

resolves and delegates to the same canonical operator checkout.

## Definition of Done

- All checkpoints and required tests pass.
- `devbot` can be invoked without `cd` from arbitrary directories.
- The operator checkout is discovered from persistent, secret-free install metadata.
- The launcher remains a thin bootstrap and preserves arguments, streams, signals, and exit codes.
- Task 031 startup self-update/restart behavior remains intact.
- Missing or stale installations fail safely with clear recovery instructions.
- Result and PR Evidence record actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #66 and the canonical PR.
- Final merge remains manual.

## Branch and PR Policy

Use exactly one Issue, one Branch, one Contract, and one Pull Request. Continue all implementation, review, and rework on `task/032-global-launcher` and its linked Planner PR. Do not create a separate Execution Issue, branch, contract, or PR.
