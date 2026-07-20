# Task 118: Approved Goal Plan Execution Foundation

## Status

IMPLEMENTED

## Goal

Implement the deterministic core that accepts an already-approved
`GoalExecutionPlan` and advances it without re-planning or invoking a named AI
provider.

## Scope

- Provider-neutral `GoalExecutionPlan` contract.
- DAG-backed Task Graph validation and stable ready-node selection.
- Goal state machine and typed illegal-transition errors.
- Execution request/result and verification request/outcome records.
- PASS, RETRY, FAIL, and ESCALATE handling.
- Retry and architecture-review budget counters.
- Completion snapshot with task, evidence, budget, findings, final state, and
  reason.

## Out of Scope

- Runtime adapters for Codex, Claude Code, API, or local models.
- GitHub webhook orchestration.
- Automatic code modification.
- Final Goal audit implementation.

## Deliverables

- `src/devbot/goal_execution_foundation.py`
- `tests/test_goal_execution_foundation.py`
- `results/118-approved-goal-plan-execution-foundation.md`
- Roadmap update for the next adapter/integration Goal.

## Acceptance Criteria

1. A valid approved plan can run dependent tasks to `REVIEW_REQUESTED`.
2. Invalid task graphs with cycles, missing dependencies, or unstable dependency
   order are rejected before execution.
3. Ready-node ordering is deterministic.
4. PASS, RETRY, FAIL, and ESCALATE are typed and tested.
5. Retry and AI review budgets are enforced as data.
6. Core domain types contain no named AI provider dependency.
7. Existing validation remains green.

## Quality Gates

- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

## Git Rules

- Task Issue: #118
- Branch: `task/118-approved-goal-plan-execution-foundation`
- Pull Request: to be opened after validation
- Do not create another Issue, Branch, or Pull Request for this Task.
