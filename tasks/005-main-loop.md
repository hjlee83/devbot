# Task 005: Polling Main Loop

## Goal
Implement one safe polling iteration and an optional continuous loop.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-005-1 | Active task prevents claim attempt | `test_iteration_skips_when_active_task_exists` |
| CP-005-2 | Eligible ready Issue is selected once | `test_iteration_selects_one_ready_issue` |
| CP-005-3 | No eligible task sleeps without error | `test_iteration_handles_empty_queue` |
| CP-005-4 | Lock prevents concurrent bot process | `test_main_loop_respects_process_lock` |
| CP-005-5 | One iteration can be executed for testing | `test_run_once_exits_after_single_iteration` |
| CP-005-6 | Unexpected error is logged and does not corrupt state | `test_iteration_error_is_reported_without_state_corruption` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot --once`

## Git and PR
Create a Task branch, result file, commit, push, and PR with checkpoint evidence.
