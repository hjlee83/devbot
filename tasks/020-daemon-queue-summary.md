# Task 020: Daemon Queue Summary and Cycle Logging

## Goal

Make daemon runtime logs concise, structured, and immediately useful to an operator without changing queue selection or workflow behavior.

The daemon should report the complete workflow queue once per cycle and clearly separate queue state, selected work, and cycle result.

## Background

Task 013 added structured, correlatable cycle/candidate/Job logging
(`cycle_id`, DEBUG search/candidate diagnostics, Job start/finish timing).
In practice `PollingService.run_cycle()` still interleaves that structured
logging with several free-form `self.logger.info(...)` narration lines
("폴링을 시작합니다.", "관리 저장소 수: %d", "ready 상태 Issue 수: %d",
"선택 가능한 ready Issue가 없습니다.", ...) that restate the same facts the
structured logs (or the eventual `PollingResult`) already carry, and there
is no single place that reports the *complete* workflow queue (all six
`devbot:*` stable states) in one line. This Task consolidates that into
three clearly separated per-cycle reports - Queue Summary, Selected,
Cycle Result - without touching what the daemon actually decides.

## Dependencies

- Task 012 (role-based polling orchestration / `select_jobs`) - the
  scheduler this Task must not change.
- Task 013 (observability/debug logging) - `devbot/observability.py`'s
  `cycle_id`, DEBUG candidate diagnostics, and Job start/finish logging,
  which this Task builds alongside rather than replaces.
- Task 014 (workflow state machine hardening) - the `devbot:*` state
  labels (`ready`/`working`/`review`/`rework`/`manual-action`/`blocked`)
  the queue summary counts.
- Task 019 (daemon reliability baseline) - `FailureCategory`, which the
  normalized Cycle Result reuses for a failed cycle instead of inventing a
  parallel failure vocabulary.

## Scope

1. Replace duplicated queue messages with one structured queue summary per cycle.
2. Report counts for the stable workflow states used by the scheduler:
   - `ready`
   - `review`
   - `rework`
   - `blocked`
   - `manual-action`
   - `working`
3. Keep queue-state reporting separate from candidate selection and cycle result.
4. Report the selected Issue/PR/job type when a job is selected.
5. Report a normalized cycle result such as `NO_RUNNABLE_TASK`, `IMPLEMENT`, `REVIEW`, `REWORK`, or the structured failure category returned by the job.
6. Preserve detailed per-candidate and diagnostic messages at DEBUG level.
7. Preserve Task 013 structured logging fields such as `cycle_id`, repository, Issue, PR, job type, result, and elapsed time.
8. Avoid counting the same Issue in more than one stable-state bucket.
9. Keep existing scheduler priority and concurrency behavior unchanged.
10. Update operator documentation and produce a Result document.

## Expected Operator Output

```text
Queue Summary
  ready         : 0
  review        : 1
  rework        : 0
  blocked       : 1
  manual-action : 0
  working       : 0

Selected
  issue    : #38
  pr       : #39
  job_type : review

Cycle Result
  REVIEW
  elapsed: 402ms
```

When no work is runnable:

```text
Queue Summary
  ready         : 0
  review        : 0
  rework        : 0
  blocked       : 1
  manual-action : 0
  working       : 0

Cycle Result
  NO_RUNNABLE_TASK
```

## Out of Scope

- Scheduler priority changes
- State-machine changes
- Retry-policy changes
- Worktree isolation
- Timeline automatic event recording
- Dashboard or UI
- Notification integrations

## Checkpoints

### CP-020-1 — Single queue summary

Each polling cycle emits exactly one operator-level queue summary.

Required test:

`test_cycle_logs_queue_summary_once`

### CP-020-2 — Complete state counts

The queue summary includes ready, review, rework, blocked, manual-action, and working counts.

Required test:

`test_queue_summary_includes_all_workflow_states`

### CP-020-3 — No duplicate no-work message

A no-work cycle does not emit multiple operator-level messages that communicate the same fact.

Required test:

`test_no_runnable_task_is_reported_once`

### CP-020-4 — Selected job summary

A selected job reports Issue, optional PR, job type, and repository in a consistent structure.

Required test:

`test_selected_job_summary_contains_identity_fields`

### CP-020-5 — Normalized cycle result

Cycle completion reports a stable normalized result independently of queue counts.

Required test:

`test_cycle_result_is_reported_separately_from_queue_summary`

### CP-020-6 — DEBUG detail preservation

Detailed candidate-selection and skip-reason logs remain available at DEBUG level.

Required test:

`test_debug_logging_preserves_candidate_details`

### CP-020-7 — Structured context preservation

Existing cycle ID, repository, Issue, PR, job type, result, and elapsed context remains available.

Required test:

`test_queue_logging_preserves_structured_context`

### CP-020-8 — State count exclusivity

An Issue with malformed multiple state labels is not silently counted in multiple stable-state buckets; the anomaly is diagnosed consistently with existing state rules.

Required test:

`test_queue_summary_does_not_double_count_issue_state`

### CP-020-9 — Scheduler regression safety

REWORK > REVIEW > IMPLEMENT priority, per-repository exclusivity, and global concurrency remain unchanged.

Required tests:

- existing scheduler and polling test suite
- `test_queue_summary_does_not_change_job_selection`

### CP-020-10 — Documentation and Result

Update the relevant runbook/logging documentation and create:

`results/020-daemon-queue-summary.md`

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once`

PR Evidence must include:

- implementation summary
- checkpoint-to-test mapping
- exact validation results
- example no-work and selected-job log output
- remaining limitations

## Files Expected to Change

- `src/devbot/observability.py` - `QueueSummary`/`build_queue_summary`/
  `log_queue_summary`, `log_cycle_result`, `log_state_label_conflict`,
  and `log_job_selected`'s message format (identity fields + PR).
- `src/devbot/polling.py` - `PollingService.run_cycle()` (remove
  free-form narration, wire the three new reports),
  `_collect`/`_collect_job_candidates`/`_rework_state_candidate`/
  `_review_state_candidate` (thread PR numbers through for the Selected
  report; detect multi-label anomalies).
- `tests/test_observability.py`, `tests/test_polling.py`,
  `tests/test_scheduler.py` - CP-020 tests plus a scheduler regression
  test.
- `README.md`, `docs/08-beta-runbook.md`, `docs/00-roadmap.md`,
  `docs/07-decisions.md` - operator-facing documentation.
- `results/020-daemon-queue-summary.md` (new).
- No changes expected in `src/devbot/scheduler.py`, `src/devbot/issue_state.py`,
  `src/devbot/reliability.py`, `src/devbot/rework.py`, `src/devbot/review.py`,
  or `src/devbot/timeline.py` - this Task is a logging consolidation, not a
  behavior change to any of those.

## Risk

- Removing the free-form narration lines could hide information an
  operator relied on if the replacement structured logs miss a field -
  mitigated by CP-020-7's explicit requirement to preserve every Task 013
  correlation field.
- Normalizing a mixed cycle's result (more than one Job ran, with
  different outcomes) to a single value is inherently lossy; this Task
  documents the chosen tie-break (first failure, else first succeeded
  Job's type) rather than leaving it unspecified.
- Adding a `state_label_conflict` diagnostic touches `_collect()`'s
  per-Issue loop; a bug there could theoretically raise before a task is
  even built. Mitigated by keeping the same `_matched_task_states`
  first-match rule `issue_to_task` already used and by this being a
  read-only diagnostic (no control-flow change).

## Rollback Strategy

Revert the branch's commits. The change is additive/log-format-only - no
GitHub label, comment, or PR-body schema changes, no state-machine or
scheduler changes - so reverting restores the previous log format with no
data migration or cleanup required.

## Reviewer Focus

- Confirm `select_jobs`/`select_jobs_with_exclusions` and the state
  machine (`devbot/issue_state.py`) are byte-for-byte unchanged.
- Confirm the queue summary cannot double-count an Issue (CP-020-8) by
  reading `build_queue_summary` - it must sum over already-single-state
  `IssueTask`s, not re-derive state per bucket.
- Confirm DEBUG-level candidate diagnostics still appear unchanged
  (CP-020-6) and that removed INFO lines are genuinely redundant, not
  information operators depended on.
- Confirm PR Evidence's example log output was actually produced by this
  code (not hand-written prose).

## Definition of Done

- CP-020-1 through CP-020-10 all satisfied with the required test names
  passing.
- `uv sync` / `uv run ruff check .` / `uv run pytest` / `uv run devbot
  --once --dry-run` all succeed.
- `results/020-daemon-queue-summary.md` exists and matches the final diff.
- PR #39's body Evidence matches the final implementation exactly.
- Issue #38 is left in the repository-defined review state, not merged or
  closed.

## Result 문서 경로

`results/020-daemon-queue-summary.md`

## Branch and PR Policy

Continue all contract, implementation, tests, Result, and rework on:

- Branch: `task/020-daemon-queue-summary`
- Pull Request: the single Task 020 PR

Do not create a second implementation branch or PR.
