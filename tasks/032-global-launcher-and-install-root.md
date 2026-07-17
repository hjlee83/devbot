# Task 032: Global Launcher and Install Root Discovery

## Goal

Allow operators to run `devbot` from any directory without manually changing into the DevBot operator checkout.

## Scope

1. Provide a PATH-installable `devbot` launcher.
2. Persist and resolve the DevBot operator checkout as an installation root.
3. Make default `.env` and `config/repositories.yaml` loading independent of the current directory.
4. Forward all CLI arguments and preserve the wrapped command's exit code.
5. Keep Task 031 startup self-update and restart behavior compatible with global execution.
6. Emit safe diagnostics and recovery guidance when installation metadata is missing or invalid.

## Out of Scope

- Full interactive `devbot init` wizard
- Homebrew or pipx distribution
- systemd or Docker deployment
- Multi-installation selection
- Per-Agent model configuration
- Admin UI

## Planner Artifacts

- Task Issue: #66
- Branch: `devbot/devbot-66-task-032-global-launcher-and-install-roo`
- Contract: `tasks/032-global-launcher-and-install-root.md`
- Pull Request: existing PR for Issue #66
- Result: `results/032-global-launcher-and-install-root.md`

## Checkpoints

### CP-032-1 - PATH launcher

`devbot install-launcher --bin-dir <PATH>` creates an executable `devbot` launcher in the requested directory.

Required test: `test_install_launcher_writes_path_command_that_forwards_arguments`

### CP-032-2 - Persistent install root

The launcher records the operator checkout in persistent installation metadata and runtime discovery can recover it later.

Required test: `test_install_metadata_persists_operator_checkout`

### CP-032-3 - CWD-independent config loading

Default `.env` and `config/repositories.yaml` are resolved from the installation root, not the caller's current directory.

Required test: `test_config_defaults_are_install_root_relative_from_any_cwd`

### CP-032-4 - Argument and exit-code forwarding

The global launcher forwards all CLI arguments and returns the wrapped command's exit code.

Required test: `test_launcher_forwards_cli_arguments_and_exit_code`

### CP-032-5 - Startup self-update compatibility

Startup self-update receives the resolved install root as the operator checkout, preserving Task 031 behavior when launched from another directory.

Required test: `test_main_uses_install_root_for_startup_self_update`

### CP-032-6 - Safe diagnostics and recovery

Missing or invalid installation metadata fails safely with a recovery hint and does not select an arbitrary checkout.

Required test: `test_missing_install_metadata_has_safe_recovery_hint`

### CP-032-7 - Existing CLI compatibility

`--version` still bypasses runtime config, and `install-launcher` does not require `.env`, repositories config, GitHub, lock, polling, or Agent setup.

Required tests:
- `test_cli_version_does_not_load_runtime_config`
- `test_install_launcher_command_does_not_load_runtime_config`

## Validation Gate

Run from the Task PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

## Definition of Done

- All checkpoints and required tests pass.
- Operators can install a PATH launcher from the operator checkout.
- Runtime configuration and startup self-update use the installation root when invoked globally.
- Result and PR Evidence record actual implementation and validation outcomes.
- Final merge remains manual.
