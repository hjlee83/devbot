# Task 001: Bootstrap DevBot MVP

## Goal
Create a minimal, executable Python 3.13 DevBot project that loads configuration, acquires a single-process lock, validates configured repositories, prints startup information, and exits cleanly.

## Context
The first runtime is macOS at `/Users/luna/workspace/devbot`.
The design must remain portable to a Linux VPS.

## In scope
- `pyproject.toml` using `uv`
- package under `src/devbot`
- configuration models and loader
- file-based process lock
- repository configuration parsing
- queue and agent interfaces as non-networked skeletons
- runnable CLI
- unit tests
- initial README and `.env.example`

## Out of scope
- Real GitHub API calls
- Actual Codex execution
- Git push
- PR creation
- Continuous polling

## Required structure

```text
pyproject.toml
README.md
.env.example
config/repositories.yaml
prompts/issue-task.md
src/devbot/
  __init__.py
  main.py
  config.py
  lock.py
  models.py
  queue.py
  github_client.py
  workspace.py
  agents/
    __init__.py
    base.py
    codex.py
tests/
```

## Functional requirements

### Configuration
- Load `.env`.
- Load `config/repositories.yaml`.
- Calculate local repository path as `workspace_root / repo`.
- Reject missing or invalid required values with clear errors.
- Include defaults:
  - `POLL_INTERVAL_SECONDS=60`
  - `DEVBOT_LOCK_FILE=/tmp/devbot.lock`
  - `DEFAULT_AGENT=codex`
  - `MAX_CONCURRENT_JOBS=1`
  - `DRY_RUN=true`

### Lock
- Allow one DevBot process.
- Reject a second owner while the lock is held.
- Release the lock on normal and exceptional exits.
- Work on macOS and Linux.

### Queue skeleton
- Model one global queue across repositories.
- Refuse new work if any `working` or `review` task exists.
- Define priority order high > medium > low > none > oldest.
- No network access in this Task.

### Agent abstraction
- Define an abstract `AgentRunner`.
- Define a dry-run `CodexRunner` skeleton.
- Document that target repositories supply their own root `AGENTS.md`.

### CLI
`uv run devbot` must:
1. load configuration,
2. acquire the lock,
3. validate enabled repository paths,
4. print the managed repositories,
5. print `DevBot started`,
6. exit successfully and release the lock.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-001-1 | Valid YAML and environment values load correctly | `test_load_valid_config` |
| CP-001-2 | Missing required configuration fails clearly | `test_missing_required_config_raises` |
| CP-001-3 | Repository paths derive from workspace root and repo name | `test_repository_path_is_derived_from_workspace_root` |
| CP-001-4 | First process can acquire and release the lock | `test_lock_acquire_and_release` |
| CP-001-5 | A second process cannot acquire the active lock | `test_lock_rejects_second_owner` |
| CP-001-6 | Lock is released after an exception | `test_lock_released_after_exception` |
| CP-001-7 | Active working or review state blocks ready selection | `test_active_task_blocks_ready_selection` |
| CP-001-8 | Ready selection honors priority then oldest Issue | `test_ready_selection_orders_by_priority_then_age` |
| CP-001-9 | Codex runner defaults to dry-run and does not execute a process | `test_codex_runner_dry_run_does_not_execute` |
| CP-001-10 | CLI loads configuration and exits successfully | `test_main_starts_and_exits_successfully` |

## Verification gates

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot
```

All commands must pass before completion.

## Result and PR evidence
- Create `results/001-bootstrap.md`.
- Include every checkpoint and the concrete test name(s).
- Create a task branch such as `feature/task-001-bootstrap`.
- Commit after all gates pass.
- Push and create a PR.
- PR body must use the DevBot Task template and contain the checkpoint-to-test table.
