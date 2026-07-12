# Task 003: Global Repository Queue

## Goal
Select at most one eligible `devbot:ready` Issue across every enabled repository.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-003-1 | Any working Issue globally blocks selection | `test_working_issue_in_any_repo_blocks_selection` |
| CP-003-2 | Any review Issue globally blocks selection | `test_review_issue_in_any_repo_blocks_selection` |
| CP-003-3 | Highest priority wins across repositories | `test_global_queue_prefers_highest_priority` |
| CP-003-4 | Oldest Issue wins within equal priority | `test_global_queue_prefers_oldest_with_same_priority` |
| CP-003-5 | Disabled repositories are ignored | `test_disabled_repository_is_ignored` |
| CP-003-6 | No ready Issue returns no task | `test_no_ready_issue_returns_none` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
Create a Task branch, result file, commit, push, and PR with checkpoint evidence.
