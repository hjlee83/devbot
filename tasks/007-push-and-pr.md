# Task 007: Commit, Push, and Pull Request

## Goal
After all quality gates pass, commit changes, push the task branch, and create a PR with checkpoint evidence.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-007-1 | No commit occurs when verification fails | `test_failed_verification_prevents_commit` |
| CP-007-2 | Commit message references the Issue | `test_commit_message_references_issue` |
| CP-007-3 | Push targets only the task branch | `test_push_targets_task_branch_only` |
| CP-007-4 | PR targets the configured default branch | `test_pr_targets_default_branch` |
| CP-007-5 | PR body contains every checkpoint and test mapping | `test_pr_body_contains_checkpoint_evidence` |
| CP-007-6 | Issue receives the PR link | `test_issue_receives_pr_link_comment` |
| CP-007-7 | Dry-run performs no commit, push, or PR creation | `test_delivery_dry_run_has_no_side_effects` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
This Task explicitly permits commit, push, and PR creation after all gates pass.
