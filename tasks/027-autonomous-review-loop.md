# Task 027: Autonomous Review Loop

## Status

Planned

## Background

DevBot can already prepare an isolated Planner-linked worktree, run an Implementer, deliver changes to an existing PR, invoke a Reviewer, process rework, record Timeline events, and recover from interrupted Agent execution.

The remaining operator burden is orchestration. After implementation, a human still has to ask for review, ask the Implementer to address feedback, request re-review, and inspect long PR comments to decide whether the PR is merge-ready.

## Goal

Make the review lifecycle autonomous from successful implementation delivery until one of two terminal outcomes:

1. the PR is ready to merge; or
2. DevBot requires explicit manual action.

## Scope

- Automatically dispatch REVIEW after successful IMPLEMENT delivery.
- Automatically dispatch REWORK after a valid `REQUEST CHANGES` review result.
- Automatically return to REVIEW after successful REWORK delivery.
- Continue the REVIEW/REWORK loop without human commands.
- Bound review-loop attempts and prevent infinite cycling.
- Preserve existing Issue state-machine semantics and host-managed worktrees.
- Make each transition idempotent across polling retries and daemon restarts.
- Synchronize concise PR state labels:
  - `devbot:implementing`
  - `devbot:review`
  - `devbot:rework`
  - `devbot:ready-to-merge`
- Mark `devbot:ready-to-merge` only after the merge-readiness conditions defined by this Task pass.
- Record configured Implementer/Reviewer actors and cycle transitions in Timeline.
- Route unsafe, contradictory, or exhausted loops to `devbot:manual-action` without discarding work.
- Produce diagnostic output that explains the current loop cycle, last outcome, retry count, and next action.
- Establish a single prepared-workspace invariant: after `WorktreeManager.prepare()` returns a
  `PreparedWorkspace`, every Agent role must execute exclusively against
  `PreparedWorkspace.repository` rather than the configured host checkout.

## Out of Scope

- Automatic merge or auto-merge enablement.
- External push, email, Slack, Telegram, or Kakao notifications.
- GitHub Bot account or GitHub App identity migration.
- Dashboard/UI.
- Multi-agent failover or reviewer quorum.
- Changes to Planner object topology.
- Historical Timeline backfill.

## Required Behavior

### Successful implementation

After IMPLEMENT successfully delivers to the linked PR, DevBot must transition the execution Issue to review and make REVIEW runnable without a human comment or label edit.

### Review result: request changes

When the Reviewer returns exactly one valid `REQUEST CHANGES` result:

- publish the review result once;
- transition the execution Issue to rework;
- make REWORK runnable automatically;
- preserve the linked PR, branch, contract, and worktree;
- update the PR state label to `devbot:rework`.

### Successful rework

After REWORK validates and pushes successfully:

- mark the consumed feedback as processed;
- transition the execution Issue back to review;
- make REVIEW runnable automatically;
- update the PR state label to `devbot:review`.

### Review result: merge ready

When the Reviewer returns exactly one valid `MERGE READY` result, DevBot must verify at least:

- the review result belongs to the current PR head SHA;
- required CI/check status is successful when available;
- no unprocessed DevBot review feedback remains;
- the Issue/PR/branch linkage is consistent;
- the execution Issue is not blocked or manual-action.

If those conditions pass:

- retain the execution Issue in its terminal review state;
- add `devbot:ready-to-merge` to the PR;
- remove mutually exclusive DevBot PR state labels;
- record `review:end` with result `merge-ready` idempotently;
- expose a concise `READY_TO_MERGE` diagnostic result.

If they do not pass, do not mark the PR ready-to-merge; transition to the appropriate retry, blocked, or manual-action path with an explicit reason.

### Bounded loop

The number of consecutive REVIEW/REWORK cycles must be bounded by configuration or a documented default. Exceeding the limit must require manual action and must not delete or reset the worktree.

## Checkpoints

### CP-027-1 — Automatic review dispatch

A successful IMPLEMENT delivery makes REVIEW runnable without human intervention.

Required test: `test_successful_implement_delivery_automatically_dispatches_review`

### CP-027-2 — Request changes dispatches rework

A valid `REQUEST CHANGES` outcome automatically makes REWORK runnable.

Required test: `test_request_changes_automatically_dispatches_rework`

### CP-027-3 — Rework returns to review

Successful REWORK automatically returns the same Issue and PR to REVIEW.

Required test: `test_successful_rework_automatically_dispatches_rereview`

### CP-027-4 — Multi-cycle autonomy

At least two REVIEW/REWORK cycles can complete without manual commands while preserving the same branch and PR.

Required test: `test_autonomous_review_loop_supports_multiple_cycles`

### CP-027-5 — Idempotent retries

Repeated polling, daemon restart, or duplicate GitHub reads do not publish duplicate review results, consume feedback twice, or enqueue duplicate jobs.

Required test: `test_autonomous_review_loop_is_idempotent_across_retries`

### CP-027-6 — Bounded attempts

The configured/default loop limit prevents infinite REVIEW/REWORK cycling and routes the Issue to manual action while preserving work.

Required test: `test_review_loop_attempt_limit_requires_manual_action`

### CP-027-7 — Current-head merge readiness

`MERGE READY` for an obsolete head SHA cannot mark the PR ready-to-merge.

Required test: `test_stale_merge_ready_result_does_not_mark_pr_ready`

### CP-027-8 — Ready-to-merge PR label

A valid current-head merge-ready result atomically replaces other DevBot PR state labels with `devbot:ready-to-merge`.

Required test: `test_merge_ready_applies_exclusive_ready_to_merge_label`

### CP-027-9 — No premature ready state

Failing CI, pending feedback, inconsistent metadata, or blocked/manual Issue state prevents `devbot:ready-to-merge`.

Required test: `test_merge_readiness_gate_rejects_incomplete_pr`

### CP-027-10 — Timeline actors and cycles

Timeline records configured Implementer/Reviewer actors and every review/rework cycle without duplicate markers.

Required test: `test_review_loop_records_actors_and_cycles_idempotently`

### CP-027-11 — Failure isolation

PR label or Timeline write failures do not hide the primary job outcome; they produce visible diagnostics and a safe state.

Required test: `test_review_loop_metadata_failure_preserves_primary_outcome`

### CP-027-12 — Existing behavior compatibility

Planner, worktree, delivery, resume, manual-action, dry-run, manual Timeline CLI, and non-review jobs retain their existing behavior unless explicitly changed above.

Required test: `test_existing_workflows_remain_compatible_with_autonomous_review_loop`

### CP-027-13 — Evidence and documentation

Update relevant workflow/state/Timeline documentation, `docs/00-roadmap.md`, PR Evidence, and produce `results/027-autonomous-review-loop.md`.

### CP-027-14 — Prepared workspace invariant

All Agent roles that run after `WorktreeManager.prepare()` use `PreparedWorkspace.repository`.
Dirty host checkout with a clean prepared worktree succeeds; clean host checkout with a dirty
prepared worktree fails before Agent execution.

Required tests: `test_review_uses_prepared_pr_worktree_for_workspace_validation`,
`test_review_rejects_dirty_prepared_worktree_even_when_host_is_clean`,
`test_autonomous_review_rework_rereview_loop_runs_without_manual_commands`

## Validation Gate

Run and record:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Manual verification must demonstrate:

1. IMPLEMENT → REVIEW without a human command.
2. REQUEST CHANGES → REWORK → REVIEW without a human command.
3. A second review cycle uses the same PR and branch.
4. Current-head MERGE READY applies `devbot:ready-to-merge` exactly once.
5. A stale review or failed gate does not apply `devbot:ready-to-merge`.
6. Loop exhaustion routes to manual action and preserves the worktree.

## Result Requirements

Create `results/027-autonomous-review-loop.md` containing:

- implementation summary;
- root architectural decisions;
- changed files;
- CP-027-1 through CP-027-13 evidence;
- validation command output;
- manual verification evidence;
- remaining limitations and risks;
- confirmation that automatic merge remains out of scope.

## Definition of Done

- CP-027-1 through CP-027-13 pass.
- The review/rework/re-review loop no longer requires human orchestration.
- `devbot:ready-to-merge` is concise, exclusive, current-head-safe, and idempotent.
- Unsafe or exhausted loops require manual action without losing work.
- CI passes and PR Evidence matches the implementation and Result.
- No automatic merge is introduced.
