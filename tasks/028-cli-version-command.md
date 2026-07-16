# Task 028: CLI Version Command and End-to-End Workflow Validation

## Goal

Add a minimal `devbot --version` command and use this small, deterministic change to validate the current single-Task Planner workflow and autonomous implementation/review loop in a real run.

## Background

Task 027 completed the autonomous IMPLEMENT → REVIEW → REWORK → REVIEW loop and established the PreparedWorkspace-only execution contract. The next step is to validate that flow with a small user-visible feature whose correctness is easy to verify independently.

## Scope

1. Add `devbot --version`.
2. Report the installed DevBot package version from one authoritative source.
3. Ensure the command exits with status 0 without:
   - loading `.env` or repository configuration;
   - acquiring the daemon lock;
   - contacting GitHub;
   - starting the polling loop;
   - initializing Agent runners.
4. Preserve all existing CLI behavior.
5. Add focused automated tests for the new command and regressions.
6. Update README usage, Result, roadmap, PR Evidence, and Timeline evidence where required by repository policy.

## Out of Scope

- Release automation, tags, changelog generation, or version bump policy
- Packaging backend redesign
- Planner automation
- Automatic merge
- Notifications
- Unrelated CLI or configuration refactoring

## Dependencies

- Task 027 Autonomous Review Loop
- Root `CONSTITUTION.md`, `AGENTS.md`, and current single-Task Planner policy

## Planner Artifacts

- Task Issue: #57
- Branch: `task/028-cli-version-command`
- Contract: `tasks/028-cli-version-command.md`
- Pull Request: this Task's single Planner PR
- Result: `results/028-cli-version-command.md`

## Files Expected to Change

- `src/devbot/main.py` or a small version helper/module
- CLI tests under `tests/`
- `README.md`
- `docs/00-roadmap.md`
- `results/028-cli-version-command.md`

## Checkpoints

### CP-028-1 — Version output

`devbot --version` prints a stable version string containing the DevBot package version and exits successfully.

Required test: `test_cli_version_prints_package_version`

### CP-028-2 — Single source of truth

The displayed value comes from one authoritative package-version source and is not duplicated as an independent CLI literal.

Required test: `test_cli_version_uses_authoritative_version_source`

### CP-028-3 — Configuration independence

The command works when `.env` and repository configuration are absent or invalid because version reporting does not require runtime configuration.

Required test: `test_cli_version_does_not_load_runtime_config`

### CP-028-4 — Lock independence

The command does not acquire or probe the daemon process lock.

Required test: `test_cli_version_does_not_acquire_daemon_lock`

### CP-028-5 — Network independence

The command does not initialize GitHub clients or make network calls.

Required test: `test_cli_version_does_not_contact_github`

### CP-028-6 — Polling independence

The command does not construct or run the polling service or Agent runners.

Required test: `test_cli_version_does_not_start_polling_or_agents`

### CP-028-7 — Existing CLI compatibility

Existing `--once`, `--dry-run`, `--verbose`, `doctor`, `timeline`, and `worktree` behavior and tests remain compatible.

Required test: `test_existing_cli_workflows_remain_compatible_with_version_command`

### CP-028-8 — Documentation and evidence

README usage, roadmap, Result, and PR Evidence accurately describe the implementation and validation results.

## Validation Gate

Run sequentially:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --version
uv run devbot --once --dry-run
```

The implementation must also demonstrate that `--version` succeeds without valid runtime configuration and without acquiring the daemon lock or contacting GitHub.

## Risk

- Accidentally routing `--version` through normal startup could make a harmless metadata command depend on secrets, configuration, lock availability, or network access.
- Duplicating the version in CLI code could drift from package metadata.

## Rollback Strategy

Remove the version argument/helper and its focused tests/documentation. Existing CLI paths must remain unchanged.

## Reviewer Focus

- Verify early exit occurs before all runtime initialization.
- Verify the version has one source of truth.
- Verify tests prove absence of configuration, lock, GitHub, polling, and Agent initialization.
- Verify no unrelated CLI redesign was introduced.
- Verify the existing Issue #57, branch, PR, contract, and PreparedWorkspace are reused throughout IMPLEMENT/REVIEW/REWORK.

## Definition of Done

- All checkpoints and required tests pass.
- Full regression suite passes.
- Result and PR Evidence are current and truthful.
- DevBot reaches `devbot:ready-to-merge` through the autonomous review workflow on the same Issue, branch, PR, and worktree.
- Final merge remains manual.

## Branch and PR Policy

This Task uses exactly one Issue, one Branch, one Contract, and one Pull Request. Do not create a separate Execution Issue, implementation branch, contract, or PR. Continue all implementation and rework on `task/028-cli-version-command` and its linked Planner PR.