# Task 128: DevBot State Label Precedence

## Issue

GitHub Issue: #128 - `devbot:*` multi-label Issues resolve to different states in `polling.py` vs `issue_state.py`.

## Problem

Manual label edits or partially failed label writes can leave an Issue with more than one `devbot:*` state label. Before this Task, polling candidate collection resolved ambiguity by `TaskState` declaration order, while Issue transition validation resolved it by later-workflow/terminal precedence. The same Issue could therefore be scheduled as `ready` while transition validation treated it as `done` or `blocked`.

## Scope

- Define one shared state-label precedence helper.
- Make `devbot.polling` and `devbot.issue_state` use the same resolver.
- Preserve existing successful transition behavior that strips all old `devbot:*` labels before writing the new one.
- Keep conflict visibility through `state_label_conflict` logs.
- Add regression tests proving polling and issue-state resolution agree for multi-label inputs.

## Out of Scope

- Automatic correction of conflicted GitHub labels without a state transition.
- Changing normal single-label state transitions.
- Changing scheduler job priority or repository concurrency rules.

## Acceptance Criteria

1. `devbot:ready` + `devbot:done` resolves to `done` in both polling and issue-state validation.
2. `devbot:review` + `devbot:blocked` resolves to `blocked` in queue summary and conflict logs.
3. The shared precedence is defined in one source of truth.
4. Existing single-label behavior remains unchanged.
5. `uv run ruff check .` passes.
6. `uv run pytest` passes.
