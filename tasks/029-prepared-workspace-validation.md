# Task 029: Prepared Workspace Validation Environment

## Goal

Ensure every validation command runs reproducibly inside the Task
PreparedWorkspace without falling back to the host checkout or its `.venv`.

## Scope

1. Prepare a usable validation environment for each PreparedWorkspace.
2. Run required validation commands from that workspace.
3. Prevent host-checkout `.venv` and path fallback.
4. Record workspace path, commands, and outcomes in Result/PR evidence.
5. Classify environment failures precisely and preserve work.
6. Keep existing IMPLEMENT/REVIEW/REWORK behavior compatible.

## Out of Scope

- Automatic merge
- External notifications
- Planner automation
- General packaging redesign
- Unrestricted Agent network access

## Planner Artifacts

- Task Issue: #59
- Branch: `devbot/devbot-59-task-029-prepared-workspace-validation-e`
- Contract: `tasks/029-prepared-workspace-validation.md`
- Pull Request: host-managed existing PR for Issue #59
- Result: `results/029-prepared-workspace-validation.md`

## Checkpoints

### CP-029-1 — Workspace environment preparation

Validation prepares the workspace environment before running validation commands.

Required test: `test_verification_prepares_workspace_environment_and_ignores_host_venv`

### CP-029-2 — Host virtualenv isolation

Validation must not inherit the operator checkout's active `VIRTUAL_ENV`, and PATH must prefer
the PreparedWorkspace `.venv/bin`.

Required test: `test_verification_prepares_workspace_environment_and_ignores_host_venv`

### CP-029-3 — Environment failure classification

`uv sync` failure is classified separately from ordinary validation command failure.

Required test: `test_uv_sync_failure_is_classified_as_environment_failure`

### CP-029-4 — Existing workflow compatibility

Existing delivery and rework behavior remains compatible.

Required test: `tests/test_delivery.py`, `tests/test_rework.py`, and full regression suite.

### CP-029-5 — Evidence

Result evidence records workspace path, commands, outcomes, and limitations.

Required evidence: `results/029-prepared-workspace-validation.md`

## Validation Gate

Run sequentially:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

If the environment cannot be prepared because network access is unavailable, record that outcome
as an environment failure rather than a code failure.

## Result

Produce `results/029-prepared-workspace-validation.md`.
