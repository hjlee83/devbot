# Task 143: Run DevBot Independently of the Current Working Directory

## Status

IMPLEMENTED

## Goal

Allow daemon and read-only operational commands to run from an arbitrary
runtime directory after repositories have been registered with `devbot init`.

## Scope

- Remove hidden dependence on CWD-relative `config/repositories.yaml` when
  registry repositories are available.
- Keep explicit legacy repository configuration strict and fail-closed.
- Preserve merging of registry and legacy repository sources.
- Document runtime-directory execution.

## Out of Scope

- Packaging or installer changes.
- systemd/launchd setup.
- Repository registration format changes.
- Scheduler or worker behavior changes.

## Acceptance Criteria

1. Registry-only operation works from a directory with no `config/`.
2. `WORKSPACE_ROOT` may be present without making default legacy config fatal
   when registry repositories are valid.
3. Explicit legacy repository paths remain strict.
4. Duplicate detection across legacy and registry repositories remains intact.
5. `devbot --once --dry-run` can run from an unrelated runtime directory.
6. README and repository-registration docs describe runtime-directory usage.
7. Existing tests pass.

## Validation Commands

- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

## Git Rules

- Task Issue: #143
- Branch: `task/143-cwd-independent-runtime-config`
- Pull Request: to be opened after validation
- Do not create another Issue, Branch, or Pull Request for this Task.
