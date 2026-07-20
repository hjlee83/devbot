# Task 141: Approved GoalExecutionPlan Integration Adapter

## Status

IMPLEMENTED

## Goal

Connect the provider-neutral Goal execution foundation from Task 118 to a thin
runtime adapter layer that can load, validate, start, persist, resume, and
advance an approved `GoalExecutionPlan` without re-planning.

## Scope

- Versioned approved Goal plan JSON loading and validation.
- Versioned runtime state persistence and resume.
- Atomic state writes.
- CLI commands for validate/start/status/resume.
- RuntimeScheduler-backed execution request submission.
- Execution and verification adapter protocols.
- Completion snapshot persistence.

## Out of Scope

- Final Goal audit / `GOAL_ACCEPTED`.
- Provider-specific Codex, Claude, OpenAI, Anthropic, or local-model logic in
  core domain types.
- GitHub webhook orchestration.
- Database-backed distributed execution.
- Automatic scope changes or LLM-based planning.

## Deliverables

- `src/devbot/goal_runtime_adapter.py`
- Goal approved-plan CLI wiring in `src/devbot/main.py`
- `tests/test_goal_runtime_adapter.py`
- CLI wiring coverage in `tests/test_main.py`
- `results/141-approved-goal-plan-integration-adapter.md`
- Roadmap update for the end-to-end Goal release validation step.

## Acceptance Criteria

1. A valid versioned approved `GoalExecutionPlan` can be loaded from disk.
2. Unsupported schema versions and corrupt state fail closed with typed errors.
3. CLI can validate, start, status, and resume an approved plan without
   re-planning.
4. Pending execution requests are submitted through `RuntimeScheduler`.
5. Same-repository Goal work remains sequential; different repositories can run
   concurrently within scheduler limits.
6. Execution and verification requests are mapped through adapter protocols.
7. Goal state, evidence, pending requests, and budget counters persist.
8. Successful multi-Task execution reaches `REVIEW_REQUESTED` and writes a
   completion snapshot.
9. Existing test suite passes.

## Quality Gates

- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

## Git Rules

- Task Issue: #141
- Branch: `task/141-approved-goal-plan-integration-adapter`
- Pull Request: to be opened after validation
- Do not create another Issue, Branch, or Pull Request for this Task.
