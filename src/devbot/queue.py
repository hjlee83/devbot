"""Global queue selection.

Models one global queue across every managed repository. No network access:
callers are responsible for supplying the current `IssueTask` snapshot (from
GitHub, in a later Task). This module implements the selection rules:

- Disabled repositories are ignored entirely, including for the purpose of
  the active-task block below.
- Refuse new work while any `working` or `review` task exists in an enabled
  repository.
- Otherwise select the highest-priority, then oldest, `ready` task.
"""

from __future__ import annotations

from collections.abc import Iterable

from devbot.models import IssueTask, Priority, RepositoryConfig, TaskState

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


def select_global_ready_task(
    tasks: Iterable[IssueTask],
    repositories: Iterable[RepositoryConfig],
) -> IssueTask | None:
    """Select at most one eligible `ready` Issue across every enabled repository.

    Tasks belonging to a disabled repository are ignored entirely, as if
    they did not exist, before applying `select_ready_task`'s blocking and
    priority/age ordering rules to what remains.
    """
    enabled_repository_names = {repo.full_name for repo in repositories if repo.enabled}
    eligible_tasks = [task for task in tasks if task.repository in enabled_repository_names]
    return select_ready_task(eligible_tasks)
