# Task 008: PR Feedback Rework Loop

## Goal
Detect new `@devbot` PR comments, return the Issue to working, apply changes on the same branch, rerun gates, and update the same PR.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-008-1 | Only unprocessed `@devbot` comments trigger work | `test_only_unprocessed_devbot_comments_trigger_rework` |
| CP-008-2 | Review returns to working before agent execution | `test_rework_moves_review_to_working` |
| CP-008-3 | Existing branch and PR are reused | `test_rework_reuses_existing_branch_and_pr` |
| CP-008-4 | Comment is marked processed after successful update | `test_successful_rework_marks_comment_processed` |
| CP-008-5 | Failed rework becomes blocked with evidence | `test_failed_rework_moves_to_blocked` |
| CP-008-6 | Successful rework returns to review | `test_successful_rework_returns_to_review` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
Commit and push additional changes to the existing task branch and update the existing PR evidence.
