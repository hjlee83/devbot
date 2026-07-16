# Task 026: Agent Resume & Timeout Recovery

## Goal

Make Agent execution safely resumable after timeout or interruption so unfinished worktree changes are preserved and the next bounded retry continues the existing implementation instead of restarting or discarding work.

## Background

During Task 024, the Agent worked in the correct Planner branch and host-managed worktree but exceeded the 1800-second CLI timeout. The worktree retained substantial uncommitted changes, while the Job was classified as a generic failure with no explicit resume contract. DevBot needs deterministic timeout classification, preserved workspace state, bounded resume attempts, visible diagnostics, and a continuation prompt.

## Scope

1. Distinguish Agent timeout/interruption from ordinary Agent failure.
2. Preserve the prepared worktree and its uncommitted changes after a resumable interruption.
3. Detect an existing dirty Task worktree as resumable when branch, Issue, PR, and contract metadata still match.
4. Reuse that worktree and branch without creating a fallback branch or another PR.
5. Add a resume prompt that instructs the Agent to inspect the current diff, preserve completed work, finish remaining work, validate, commit, and push.
6. Add bounded retry metadata and prevent infinite resume loops.
7. Record timeout and resume lifecycle evidence in diagnostics and Timeline without replacing the primary Job outcome.
8. Preserve existing behavior for non-timeout Agent failures, approval-required/manual-action cases, workspace conflicts, review, rework, delivery, and state transitions.
9. Update documentation, roadmap, PR Evidence, and Result.

## Out of Scope

- Automatic merge
- Multi-agent failover
- Distributed execution or remote checkpoint storage
- Resuming a different branch, PR, Issue, or contract
- Historical backfill
- Dashboard/UI and notifications
- Changing provider session or usage limits

## Checkpoints

### CP-026-1 — Resumable interruption classification

Agent CLI timeout and explicitly supported interruption outcomes are classified as resumable rather than a generic blocked/iteration error.

Required test: `test_agent_timeout_is_classified_resumable`

### CP-026-2 — Workspace preservation

A resumable interruption leaves the prepared worktree and uncommitted changes intact for the next execution.

Required test: `test_timeout_preserves_unfinished_worktree`

### CP-026-3 — Resume workspace detection

The next execution detects the matching dirty worktree as resumable only when repository, Issue, PR, branch, and contract metadata agree.

Required test: `test_matching_dirty_worktree_is_detected_as_resumable`

### CP-026-4 — Resume prompt

A resumed execution receives an explicit continuation prompt that requires inspecting the existing diff and forbids recreating or discarding completed work.

Required test: `test_resume_prompt_includes_existing_work_instructions`

### CP-026-5 — Existing branch and PR reuse

Resume reuses the Planner branch, worktree, and PR and never generates a `devbot/devbot-*` fallback branch or a second PR.

Required test: `test_resume_reuses_existing_branch_and_pr`

### CP-026-6 — Bounded resume attempts

Resume attempts are counted and capped. Exceeding the configured/default cap transitions to manual action with clear diagnostics rather than looping forever.

Required test: `test_resume_attempt_limit_requires_manual_action`

### CP-026-7 — Timeline and diagnostics

Timeout and resume events are recorded idempotently with actor, attempt number, branch, PR, changed-file summary, and reason. Timeline write failure remains best-effort and cannot replace the primary outcome.

Required test: `test_timeout_and_resume_are_recorded_idempotently`

### CP-026-8 — Successful resumed delivery

A resumed execution can validate, commit, push to the existing branch, update the existing PR, and transition the Issue to review.

Required test: `test_resumed_execution_completes_existing_pr_delivery`

### CP-026-9 — Unsafe resume rejection

Branch/PR mismatch, missing contract, unrelated dirty worktree, or corrupt workspace metadata is rejected without deleting user work and is routed to manual action with recovery guidance.

Required test: `test_unsafe_resume_is_rejected_without_deleting_work`

### CP-026-10 — Regression safety

Existing daemon, Planner, worktree, Timeline, review, rework, delivery, retry, state-machine, and manual CLI behavior remains compatible.

Required test: `test_existing_workflows_compatible_with_resume_recovery`

### CP-026-11 — Result and documentation

Create `results/026-agent-resume-timeout-recovery.md` and update relevant reliability/worktree/Timeline documentation and `docs/00-roadmap.md`.

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

Manual verification:

- simulate an Agent timeout after writing multiple files
- verify the worktree and diff remain intact
- rerun and verify the continuation prompt is used
- verify no new branch or PR is created
- verify the resumed run commits and pushes to the existing PR
- verify the attempt cap routes repeated timeouts to manual action

## Branch and PR Policy

- Branch: `task/026-agent-resume-timeout-recovery`
- One Planner-created PR for contract, implementation, tests, Result, and rework
- Do not create another branch or PR
- Do not merge automatically

## Result

- `results/026-agent-resume-timeout-recovery.md`

## Definition of Done

A timed-out or interrupted Agent run can be resumed safely from the preserved matching worktree, with bounded retries, clear evidence, no branch/PR duplication, and successful delivery through the existing Planner workflow.
