"""Global queue selection skeleton.

Models one global queue across every managed repository. No network access:
callers are responsible for supplying the current `IssueTask` snapshot (from
GitHub, in a later Task). This module only implements the selection rules:

- Refuse new work while any `working` or `review` task exists anywhere.
- Otherwise select the highest-priority, then oldest, `ready` task.
"""

from __future__ import annotations

from collections.abc import Iterable

from devbot.models import IssueTask, Priority, TaskState

_ACTIVE_STATES = (TaskState.WORKING, TaskState.REVIEW)

_PRIORITY_ORDER: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
    Priority.NONE: 3,
}


def has_active_task(tasks: Iterable[IssueTask]) -> bool:
    """Return True if any task is currently `working` or `review`."""
    return any(task.state in _ACTIVE_STATES for task in tasks)


def select_ready_task(tasks: Iterable[IssueTask]) -> IssueTask | None:
    """Select the next task to run, or None if selection is blocked or empty.

    Selection is blocked while any task is `working` or `review`. Otherwise
    the eligible `ready` task with the highest priority is chosen; ties are
    broken by the oldest `created_at`.
    """
    materialized = list(tasks)
    if has_active_task(materialized):
        return None

    ready_tasks = [task for task in materialized if task.state == TaskState.READY]
    if not ready_tasks:
        return None

    return min(ready_tasks, key=lambda task: (_PRIORITY_ORDER[task.priority], task.created_at))
