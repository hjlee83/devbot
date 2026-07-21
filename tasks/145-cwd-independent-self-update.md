# Task 145: Resolve Startup Self-Update Checkout Independently of CWD

## Status

IMPLEMENTED

## Goal

Allow startup self-update to resolve the DevBot operator checkout correctly
when the daemon is launched from an unrelated runtime directory.

## Scope

- Remove process-CWD dependence from `resolve_operator_checkout()`.
- Resolve the operator checkout from explicit environment configuration or the
  running DevBot module path.
- Preserve dirty checkout, branch, fetch, and fast-forward safety checks.
- Return controlled startup diagnostics when no Git checkout can be resolved.
- Cover `doctor`/daemon startup behavior from a non-Git runtime directory.

## Out of Scope

- Packaged installer behavior.
- Redesigning non-Git self-update policy.
- Repository registration or scheduler changes.
- systemd/launchd setup.

## Acceptance Criteria

1. Startup self-update does not run Git commands against an unrelated runtime
   directory.
2. `devbot --once --dry-run --verbose` works from a non-Git runtime directory
   with registered repositories.
3. The resolved operator checkout is the DevBot source checkout used by the
   running command.
4. Dirty checkout and non-main branch checks still run against the resolved
   checkout.
5. Checkout resolution failure returns a controlled startup result.
6. README/runbook runtime commands remain valid.
7. Existing tests pass.

## Validation Commands

- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

## Git Rules

- Task Issue: #145
- Branch: `task/145-cwd-independent-self-update`
- Pull Request: to be opened after validation
- Do not create another Issue, Branch, or Pull Request for this Task.
