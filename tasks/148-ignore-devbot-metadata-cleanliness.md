# Task 148: Ignore DevBot Metadata During Workspace Cleanliness Validation

## Issue

- GitHub Issue: #148
- Branch: `task/148-ignore-devbot-metadata-cleanliness`
- Pull Request: TBD

## Goal

DevBot runtime metadata under `.devbot/` must not make a managed repository
look dirty during workspace cleanliness validation, while real source changes
continue to block execution.

## Scope

- Ignore `.devbot/` and its contents when checking workspace cleanliness.
- Keep `git status --porcelain` as the source of truth so `.gitignore` remains
  respected.
- Preserve detection of tracked and untracked source-code changes.
- Log ignored DevBot metadata paths at debug level.

## Acceptance Criteria

1. A freshly initialized repository with only `.devbot/` metadata is reported as clean.
2. Changes under `.devbot/` do not block `ensure_git_workspace_ready()`.
3. Source file changes are still detected even when `.devbot/` also changed.
4. `.gitignore`-ignored files remain ignored by cleanliness validation.
5. Regression tests cover ignored metadata and real code changes.

## Required Tests

- `test_devbot_metadata_changes_do_not_dirty_workspace`
- `test_devbot_metadata_filter_preserves_real_source_changes`
- `test_workspace_cleanliness_respects_gitignore`
- `test_workspace_cleanliness_ignores_devbot_metadata`
- `test_workspace_cleanliness_still_reports_source_changes_with_devbot_metadata`

