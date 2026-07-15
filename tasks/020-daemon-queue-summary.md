# Task 020: Daemon Queue Summary and Cycle Logging

## Goal

Make daemon runtime logs concise, structured, and immediately useful to an operator without changing queue selection or workflow behavior.

The daemon should report the complete workflow queue once per cycle and clearly separate queue state, selected work, and cycle result.

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

## Branch and PR Policy

Continue all contract, implementation, tests, Result, and rework on:

- Branch: `task/020-daemon-queue-summary`
- Pull Request: the single Task 020 PR

Do not create a second implementation branch or PR.
