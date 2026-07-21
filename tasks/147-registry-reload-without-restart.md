# Task 147: Reload Repository Registry Without Restarting the Daemon

## Status

IMPLEMENTED

## Goal

Allow a running DevBot daemon to detect repository registry changes and reload
managed repositories without requiring a process restart after `devbot init` or
`devbot init --unregister`.

## Scope

- Track registry file metadata with a lightweight polling mechanism.
- Check for registry changes at polling-cycle boundaries.
- Reload effective config through the existing legacy + registry merge path.
- Support additions and removals from subsequent queue discovery.
- Preserve previous valid config when reload fails.
- Log reload success/failure and added/removed/unchanged repository counts.

## Out of Scope

- Reloading `.env` values or agent/provider configuration beyond repository
  config reconstruction through `load_config()`.
- Filesystem watcher dependencies.
- Remote registry storage.
- Daemon process restart/replacement.
- Registry file format changes.

## Acceptance Criteria

1. Registry additions are detected within a polling cycle.
2. Added repositories become eligible for queue discovery without restart.
3. Registry removals are excluded from subsequent queue discovery.
4. No reload is performed when registry metadata is unchanged.
5. Legacy + registry merge and duplicate validation are reused.
6. Malformed registry reload keeps the previous valid configuration.
7. Active jobs use the repository snapshot selected for their cycle.
8. Logs report added, removed, and unchanged counts.
9. Tests cover addition, removal, unchanged registry, invalid reload, and
   runtime-directory operation.
10. Existing tests pass.

## Validation Commands

- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

## Git Rules

- Task Issue: #147
- Branch: `task/147-registry-reload-without-restart`
- Pull Request: to be opened after validation
- Do not create another Issue, Branch, or Pull Request for this Task.
