# Task 006: Issue State Writes

## Goal
Add safe GitHub writes for claiming, blocking, and advancing Issue state.

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-006-1 | Ready is replaced by working when claimed | `test_claim_replaces_ready_with_working` |
| CP-006-2 | Dry-run performs no GitHub write | `test_claim_dry_run_performs_no_write` |
| CP-006-3 | Working moves to blocked with an explanatory comment | `test_blocked_transition_adds_reason_comment` |
| CP-006-4 | Working moves to review after successful implementation | `test_success_transition_moves_working_to_review` |
| CP-006-5 | Review moves back to working for requested changes | `test_review_feedback_returns_issue_to_working` |
| CP-006-6 | Invalid transition is rejected | `test_invalid_state_transition_is_rejected` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
Create a Task branch, result file, commit, push, and PR with checkpoint evidence.
