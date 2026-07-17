# Task 030: External Dependency Reliability

## Goal

Make DevBot resilient to temporary GitHub API failures and interactive Agent execution so external dependencies do not incorrectly block Tasks, mutate workflow state, or require avoidable operator intervention.

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
10. Run Codex implementer/reviewer processes non-interactively with approval disabled and workspace-scoped write access.
11. Ensure Codex network access is enabled for GitHub reads required by autonomous implementation and review.
12. Detect interactive approval prompts such as `needs your approval`, `Should I proceed`, or equivalent confirmation requests.
13. Classify interactive Agent output as `agent_configuration_invalid` rather than `review_failed` or an invalid review summary.
14. Preserve Task state and provide an actionable recovery message when Agent configuration is interactive.
15. Log the effective Agent execution policy without exposing secrets.
16. Resolve PreparedWorkspace Git metadata paths with `git rev-parse` and pass only the PreparedWorkspace, worktree Git directory, and required Git common directory as writable roots.
17. Before IMPLEMENT and REWORK, fetch `origin/main` and the canonical Task branch, verify the Task branch and PR head, reject dirty worktrees, and synchronize with latest main using rebase plus `--force-with-lease` only.
18. Preserve the original Task branch/worktree on synchronization conflicts and route to `task_branch_conflict` manual action diagnostics.
19. Before REVIEW, fetch latest main and PR head, review the exact PR head, and validate latest-main compatibility without mutating or pushing the PR branch.

## Out of Scope

- GitHub App migration
- PAT creation or rotation
- General AI Agent retry/failover policy
- package registry retry policy
- Admin UI
- automatic merge

## Planner Artifacts

- Task Issue: #62
- Branch: `task/030-github-api-transient-retry`
- Contract: `tasks/030-github-api-transient-retry.md`
- Pull Request: #63
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

### CP-030-9 — Non-interactive Codex execution

DevBot invokes Codex with approval disabled, workspace-scoped write access, network access enabled, and the PreparedWorkspace as cwd for both implementer and reviewer roles.

Required test: `test_codex_runner_uses_non_interactive_workspace_policy`

### CP-030-10 — Interactive prompt detection

Codex output requesting approval or confirmation is detected before review-summary parsing and classified as `agent_configuration_invalid`.

Required test: `test_interactive_codex_output_is_classified_before_review_summary_parsing`

### CP-030-11 — Interactive failure recovery

Interactive Agent configuration does not produce a generic `review_failed` transition; the current Task state is preserved or restored and diagnostics identify the required non-interactive policy.

Required test: `test_interactive_agent_configuration_preserves_task_state_and_reports_recovery`

### CP-030-12 — Effective policy diagnostics

Startup or Agent-run diagnostics expose the effective Agent name, role, approval policy, sandbox mode, network policy, and workspace path without exposing credentials.

Required test: `test_agent_execution_policy_diagnostics_are_safe_and_complete`

### CP-030-13 — PreparedWorkspace Git metadata access

DevBot resolves `git rev-parse --git-dir`, `git rev-parse --git-common-dir`, and `git rev-parse --show-toplevel` from the PreparedWorkspace and grants Codex only the minimum required writable roots.

Required tests: `test_prepared_workspace_resolves_git_metadata_paths`, `test_codex_runner_builds_unattended_workspace_scoped_command`

### CP-030-14 — IMPLEMENT/REWORK main synchronization

IMPLEMENT and REWORK fetch the canonical Task branch and latest `origin/main`, verify the worktree is clean and on the canonical branch, verify PR-head metadata, rebase onto latest main, and push rewritten history only with `--force-with-lease`.

Required tests: `test_implement_prepare_rebases_latest_main_and_force_pushes_with_lease`, `test_dirty_worktree_is_not_rebased_or_overwritten`, `test_stale_pr_head_metadata_stops_execution`

### CP-030-15 — Conflict-safe recovery

If main synchronization conflicts, DevBot aborts the rebase, preserves the Task branch/worktree contents, does not partially deliver, and reports `task_branch_conflict` with conflicted files and recovery instructions.

Required test: `test_rebase_conflict_preserves_original_branch`

### CP-030-16 — Non-mutating latest-main review validation

REVIEW uses the exact current PR HEAD, does not rebase, merge, commit, or push the PR branch, and performs latest-main compatibility validation in an isolated/non-mutating path.

Required test: `test_review_prepare_does_not_change_pr_head`

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

Demonstrate that a reviewer requiring `gh pr view` runs without an approval prompt and returns exactly one valid terminal review decision: `MERGE READY` or `REQUEST CHANGES`.

## Definition of Done

- All checkpoints and required tests pass.
- Transient GitHub failures are retried safely and boundedly.
- Permanent authentication, permission, and resource failures remain actionable.
- Transient failures do not mutate Task state or create duplicate side effects.
- Codex implementer and reviewer execution is non-interactive in Local and VPS-style environments.
- Interactive approval output is classified explicitly and never misreported as an invalid review summary.
- Result and PR Evidence record the actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #62 and PR #63.
- Final merge remains manual.

## Branch and PR Policy

This Task uses exactly one Issue, one Branch, one Contract, and one Pull Request. Continue all implementation, review, and rework on `task/030-github-api-transient-retry` and PR #63. Do not create a separate Execution Issue, branch, contract, or PR.
