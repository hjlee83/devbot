# Task 155: Standardize Blocked Job Diagnostics and Resume

## Issue

- GitHub Issue: #155
- Branch: `task/155-blocked-job-diagnostics-resume`
- Pull Request: TBD

## Goal

Blocked jobs should leave enough deterministic context on the GitHub Issue for an
operator to repair the environment and resume the preserved task safely.

## Scope

- Render structured blocked diagnostics for unexpected implementation/delivery failures.
- Include stage, exception type/message, missing executable/path when Python exposes it,
  repository, Issue, branch, worktree, changed files, recovery steps, and traceback excerpt.
- Add hidden marker support for stable blocked diagnostics.
- Add `devbot resume <repository> <issue-number>`.
- Validate blocked Issue state, preserved worktree, and Task Contract presence before resume.
- Move safe resume attempts from `devbot:blocked` to `devbot:ready`.
- Record resume attempts through timeline helpers.

## Acceptance Criteria

1. `FileNotFoundError` diagnostics identify the missing executable or path.
2. Blocked comments include failed stage, worktree path, branch, command/context, and recovery steps.
3. Duplicate polling does not create duplicate comments because blocked Issues are not rescheduled.
4. `devbot resume <repository> <issue-number>` restores a safe blocked Issue to `devbot:ready`.
5. Safe resume reuses the existing worktree/branch context for the next polling cycle.
6. Missing or unsafe worktree state fails closed with exact instructions.
7. Resume writes labels and timeline entries consistently.
8. Transient GitHub failures remain on the existing non-blocked retry path.
9. Diagnostics redact common secret environment values from traceback excerpts.
10. Regression tests cover diagnostics, resume success, refusal, and blocked transition wiring.

## Required Tests

- `test_blocked_diagnostic_comment_extracts_missing_path`
- `test_validate_blocked_resume_accepts_preserved_contract`
- `test_validate_blocked_resume_rejects_missing_worktree`
- `test_unexpected_exception_never_leaves_issue_working`
- `test_resume_command_restores_blocked_issue_when_worktree_is_safe`
- `test_resume_command_refuses_missing_worktree`

