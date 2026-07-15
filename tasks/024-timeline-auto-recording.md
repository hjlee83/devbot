# Task 024: Timeline Auto Recording

## Goal

Automatically record Task 017/018 timeline events from the DevBot daemon so operators no longer need to call `devbot timeline start/end` manually for normal implement, review, and rework Jobs.

## Background

Task 017 defined the GitHub status timeline protocol. Task 018 added the manual Timeline CLI. Task 023 now prepares isolated host-managed workspaces and authoritative Job context before Agent execution. The next step is to connect daemon lifecycle events to the existing TimelineService without changing scheduler or workflow semantics.

## Scope

1. Record `dev:start` immediately before Implementer execution.
2. Record `dev:end` after implement/rework completion with normalized actor, cycle, and result.
3. Record `review:start` immediately before Reviewer execution.
4. Record `review:end` after review completion with `merge-ready`, `request-changes`, or normalized failure result.
5. Record the initial `ready` event when a ready Issue is first claimed and no ready marker exists.
6. Reuse the single Timeline comment and `devbot-timeline:v1` marker format from Tasks 017/018.
7. Use configured Agent role names as actors (`claude`, `codex`, or other supported configured names), not hardcoded product assumptions.
8. Make event writes idempotent for retries and duplicate cycle execution.
9. Timeline write failure must not corrupt the workflow state or falsely mark a Job successful.
10. Expose timeline write failures through existing reliability diagnostics while preserving the primary Job outcome.
11. Preserve manual Timeline CLI compatibility.
12. Update documentation, roadmap, Result, PR Evidence, and tests.

## Out of Scope

- Dashboard/UI
- notifications
- automatic merge
- multi-agent failover
- historical backfill for Tasks completed before Task 024
- distributed locking beyond the current GitHub comment update model
- changing scheduler priority, state-machine transitions, or Review Gate policy

## Checkpoints

### CP-024-1 — Automatic ready marker

The first daemon claim for a ready Issue records exactly one `ready` event.

Required test: `test_daemon_records_ready_event_once`

### CP-024-2 — Implement start marker

An IMPLEMENT Job records `dev:start` before the Implementer runs.

Required test: `test_implement_job_records_dev_start`

### CP-024-3 — Implement end marker

A completed IMPLEMENT Job records `dev:end` with actor, cycle, timestamp, and normalized result.

Required test: `test_implement_job_records_dev_end`

### CP-024-4 — Rework timeline

A REWORK Job records a new development cycle using `dev:start` and `dev:end` on the existing Timeline comment.

Required test: `test_rework_job_records_next_dev_cycle`

### CP-024-5 — Review start marker

A REVIEW Job records `review:start` before the Reviewer runs.

Required test: `test_review_job_records_review_start`

### CP-024-6 — Review end result

A REVIEW Job records `review:end` with `merge-ready` or `request-changes` according to the parsed review result.

Required test: `test_review_job_records_review_end_result`

### CP-024-7 — Configured actor identity

Timeline events use the configured Implementer/Reviewer Agent names as actors.

Required test: `test_timeline_uses_configured_agent_actor`

### CP-024-8 — Idempotent retry behavior

Retrying the same lifecycle boundary does not append duplicate markers.

Required test: `test_automatic_timeline_recording_is_idempotent`

### CP-024-9 — Failure event handling

Agent, verification, delivery, review, or workspace-preparation failures close any open phase with an appropriate normalized result when possible.

Required test: `test_failed_job_closes_open_timeline_phase`

### CP-024-10 — Timeline failure isolation

A GitHub Timeline write failure is diagnosed without replacing or hiding the primary Job outcome and without leaving the Issue permanently in `devbot:working`.

Required test: `test_timeline_write_failure_preserves_primary_job_outcome`

### CP-024-11 — Manual CLI regression safety

Task 018 `timeline start`, `timeline end`, and `timeline status` behavior remains compatible.

Required test: `test_manual_timeline_cli_remains_compatible`

### CP-024-12 — Existing workflow compatibility

Queue selection, host-managed worktree preparation, delivery, review, rework, retry, and state-machine behavior remain unchanged except for timeline side effects.

Required test: `test_existing_workflows_compatible_with_timeline_auto_recording`

### CP-024-13 — Result and documentation

Create `results/024-timeline-auto-recording.md` and update referenced documentation and roadmap.

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

Manual verification:

- run one IMPLEMENT cycle and inspect the single Timeline comment
- run one REVIEW cycle and verify actor/result markers
- verify a retry does not duplicate markers
- verify Timeline status renders active, waiting, and elapsed durations from automatically recorded events

## Branch and PR Policy

- Branch: `task/024-timeline-auto-recording`
- Pull Request: one Planner-created PR for contract, implementation, tests, Result, and rework
- Execution Issue: one linked Issue
- Do not create another branch or PR

## Result

- `results/024-timeline-auto-recording.md`

## Definition of Done

The daemon automatically maintains the Task Timeline for normal implement, rework, and review workflows, while existing workflow outcomes remain authoritative and manual Timeline CLI behavior remains compatible.
