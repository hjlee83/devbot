from datetime import datetime, timedelta

from devbot.models import IssueTask, Priority, TaskState
from devbot.queue import select_ready_task


def _task(
    number: int,
    state: TaskState,
    priority: Priority = Priority.NONE,
    created_at: datetime | None = None,
) -> IssueTask:
    return IssueTask(
        repository="owner/repo",
        number=number,
        title=f"issue {number}",
        state=state,
        priority=priority,
        created_at=created_at or datetime(2026, 1, 1),
    )


def test_active_task_blocks_ready_selection() -> None:
    tasks = [
        _task(1, TaskState.WORKING),
        _task(2, TaskState.READY, priority=Priority.HIGH),
    ]

    assert select_ready_task(tasks) is None


def test_ready_selection_orders_by_priority_then_age() -> None:
    base = datetime(2026, 1, 1)
    tasks = [
        _task(1, TaskState.READY, priority=Priority.LOW, created_at=base),
        _task(2, TaskState.READY, priority=Priority.MEDIUM, created_at=base + timedelta(days=1)),
        _task(3, TaskState.READY, priority=Priority.MEDIUM, created_at=base),  # oldest medium
        _task(4, TaskState.READY, priority=Priority.NONE, created_at=base - timedelta(days=5)),
    ]

    selected = select_ready_task(tasks)

    assert selected is not None
    assert selected.number == 3
