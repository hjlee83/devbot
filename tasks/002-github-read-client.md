# Task 002: GitHub Read Client

## Goal
Implement authenticated GitHub read operations required by the queue without performing writes.

## In scope
- authenticated user check
- Issue retrieval by repository and label/state
- pagination
- conversion into typed models
- deterministic error handling
- mocked unit tests

## Out of scope
- label changes
- comments
- PR creation
- real network calls in unit tests

## Quality gates

| ID | Checkpoint | Required test name(s) |
|---|---|---|
| CP-002-1 | Authenticated user response is parsed | `test_get_authenticated_user` |
| CP-002-2 | Issues are fetched with pagination | `test_list_issues_follows_pagination` |
| CP-002-3 | Labels and state filters are sent correctly | `test_list_issues_applies_state_and_label_filters` |
| CP-002-4 | API errors become clear domain errors | `test_github_error_is_translated` |
| CP-002-5 | No GitHub write request is available in this Task | `test_client_exposes_read_operations_only` |

## Verification gates
- `uv run ruff check .`
- `uv run pytest`

## Git and PR
Create `results/002-github-read-client.md`, commit, push, and open a PR with checkpoint evidence.
