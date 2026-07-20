# Task 130: Execution Lifecycle Logging

## Issue

GitHub Issue: #130 - Improve execution lifecycle logging and job status visibility.

## Problem

Operators need a consistent terminal-readable view of what a daemon job is doing, which stage is taking time, where a failure occurred, and how long each stage took. Existing DEBUG stage timing exists for some paths, but there is no standardized INFO start/end lifecycle format.

## Scope

- Add standardized lifecycle start/end logs for major execution stages.
- Include elapsed time for completed stages.
- Keep logs compact and readable in terminal/tmux/mobile output.
- Preserve existing diagnostic DEBUG logs and failure summaries.
- Align stage names to shared lifecycle terminology where possible: bootstrap, implement, verify, delivery, review.

## Out of Scope

- New scheduling behavior.
- Multi-repository supervision.
- Metrics backend, dashboards, or persistent performance storage.
- Replacing Timeline comments or GitHub state transitions.

## Acceptance Criteria

1. Major lifecycle stages emit start logs.
2. Major lifecycle stages emit end logs with elapsed milliseconds.
3. Implement jobs expose bootstrap, implement, verify, and delivery stage terminology.
4. Review jobs expose review stage terminology through the same helper.
5. Existing diagnostic information remains available.
6. `uv run ruff check .` passes.
7. `uv run pytest` passes.
