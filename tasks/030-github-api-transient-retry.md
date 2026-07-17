# Task 030: GitHub API Transient Retry

## Goal

Make DevBot resilient to temporary GitHub API failures so transient outages do not incorrectly block Tasks, mutate workflow state, or require operator intervention.

## Scope

1. Classify HTTP 429, request timeout, connection errors, and HTTP 5xx as transient GitHub failures.
2. Retry transient read and write requests with bounded exponential backoff and jitter.
3. Honor `Retry-After` when GitHub supplies it.
4. Keep 401/403 authentication or permission failures actionable and non-transient.
5. Keep 404 resource errors distinct from transient failures.
6. Preserve Issue, PR, branch, worktree, and label state when a cycle fails only because of a transient GitHub error.
7. Resume safely on a later retry or poll cycle without duplicate comments, labels, branches, PRs, or delivery.
8. Emit structured retry diagnostics without exposing credentials or Authorization headers.
9. Apply the same policy to GitHub read and write clients.

## Out of Scope

- GitHub App migration
- PAT creation or rotation
- AI Agent retry policy
- package registry retry policy
- Admin UI
- automatic merge

## Planner Artifacts

- Task Issue: #62
- Branch: `task/030-github-api-transient-retry`
- Contract: `tasks/030-github-api-transient-retry.md`
- Pull Request: this Task's single Planner PR
- Result: `results/030-github-api-transient-retry.md`

## Checkpoints

### CP-030-1 — Transient classification

429, timeout, connection, and 5xx failures are classified as transient; 401/403/404 remain distinct.

Required test: `test_github_failure_classification_distinguishes_transient_and_permanent_errors`

### CP-030-2 — Bounded retry

Transient failures use bounded exponential backoff with jitter and stop after the configured attempt cap.

Required test: `test_github_transient_failure_retries_with_bounded_backoff`

### CP-030-3 — Retry-After

A valid GitHub `Retry-After` value takes precedence over calculated backoff without exceeding configured safety limits.

Required test: `test_github_retry_after_header_is_honored`

### CP-030-4 — Read/write parity

Read and write clients use the same retry policy and error classification.

Required test: `test_github_read_and_write_clients_share_retry_policy`

### CP-030-5 — State preservation

A transient GitHub failure does not move an Issue to blocked/manual-action or otherwise change workflow labels.

Required test: `test_transient_github_failure_preserves_task_state`

### CP-030-6 — Idempotent recovery

Recovery after a transient failure does not duplicate comments, label transitions, branches, PRs, or delivery actions.

Required test: `test_github_retry_recovery_does_not_duplicate_side_effects`

### CP-030-7 — Safe diagnostics

Logs and diagnostic reports include status, attempt, delay, endpoint category, and final outcome without secrets.

Required test: `test_github_retry_diagnostics_are_structured_and_redacted`

### CP-030-8 — Existing workflow compatibility

Existing polling, delivery, review, rework, timeline, doctor, and reliability behavior remains compatible.

Required test: `test_existing_workflows_remain_compatible_with_github_retry`

## Validation Gate

Run from the Task PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Also demonstrate deterministic tests for 429, 500, 502, 503, 504, timeout, connection failure, 401, 403, and 404 without real sleeping or external network dependency.

## Definition of Done

- All checkpoints and required tests pass.
- Transient GitHub failures are retried safely and boundedly.
- Permanent authentication, permission, and resource failures remain actionable.
- Transient failures do not mutate Task state or create duplicate side effects.
- Result and PR Evidence record the actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #62 and the canonical PR.
- Final merge remains manual.

## Branch and PR Policy

This Task uses exactly one Issue, one Branch, one Contract, and one Pull Request. Continue all implementation, review, and rework on `task/030-github-api-transient-retry` and its linked Planner PR. Do not create a separate Execution Issue, branch, contract, or PR.
