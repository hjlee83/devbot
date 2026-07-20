# Task 138: Runtime Scheduler Architecture

## Issue

GitHub Issue: #138 - Runtime scheduler architecture for multi-repository worker execution.

## Problem

DevBot can discover work across multiple registered repositories, but execution needs a dedicated runtime scheduling layer between polling and job execution. The daemon must allow work from different repositories to run concurrently while preserving repository-local serialization and keeping AI execution concurrency independently configurable.

## Scope

- Add a runtime scheduler layer between polling and execution.
- Keep a single daemon process model.
- Maintain one FIFO queue per repository for selected jobs.
- Enforce one running worker per repository with repository locks.
- Make the global worker pool configurable with `MAX_CONCURRENT_JOBS`.
- Add independent AI concurrency control with `AI_CONCURRENCY`.
- Expose runtime worker state suitable for `devbot status`.

## Out of Scope

- GoalExecutionPlan or DAG scheduling.
- Retry priority scheduling.
- Multi-agent orchestration.
- Per-repository polling cadence.
- Parallel execution inside a single repository.

## Acceptance Criteria

1. The daemon dispatches selected jobs through the runtime scheduler.
2. Jobs from different repositories can run concurrently when worker capacity and AI concurrency allow it.
3. Jobs from the same repository execute sequentially.
4. Worker count is configurable through existing configuration.
5. AI concurrency is configured independently from worker count.
6. Runtime state exposes worker slots, state, repository, issue, and job type for status reporting.
7. `devbot status` reports scheduler configuration and current worker state.
8. `uv run ruff check .` passes.
9. `uv run pytest` passes.
