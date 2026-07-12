# Task 004: Workspace and Agent Runner

## Goal
Prepare a safe local Git workspace and execute an AgentRunner in dry-run mode.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-004-1 | Nonexistent repository path fails clearly | `test_missing_repository_path_raises` |
| CP-004-2 | Non-Git directory is rejected | `test_non_git_directory_is_rejected` |
| CP-004-3 | Dirty workspace is detected | `test_dirty_workspace_is_detected` |
| CP-004-4 | Branch name is deterministic and safe | `test_branch_name_is_safe_and_deterministic` |
| CP-004-5 | Prompt includes Issue and unprocessed comments | `test_prompt_contains_issue_and_unprocessed_comments` |
| CP-004-6 | Runner executes in target working directory | `test_runner_uses_target_repository_directory` |
| CP-004-7 | Dry-run performs no external process execution | `test_agent_dry_run_does_not_execute_process` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
Create a Task branch, result file, commit, push, and PR with checkpoint evidence.
