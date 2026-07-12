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
    working_tasks = [
        _task(1, TaskState.WORKING),
        _task(2, TaskState.READY, priority=Priority.HIGH),
    ]
    review_tasks = [
        _task(3, TaskState.REVIEW),
        _task(4, TaskState.READY, priority=Priority.HIGH),
    ]

    assert select_ready_task(working_tasks) is None
    assert select_ready_task(review_tasks) is None


def test_ready_selection_orders_by_priority_then_age() -> None:
    base = datetime(2026, 1, 1)

    # High beats every other priority, regardless of age.
    high_vs_all = [
        _task(1, TaskState.READY, priority=Priority.MEDIUM, created_at=base - timedelta(days=10)),
        _task(2, TaskState.READY, priority=Priority.LOW, created_at=base - timedelta(days=10)),
        _task(3, TaskState.READY, priority=Priority.NONE, created_at=base - timedelta(days=10)),
        _task(4, TaskState.READY, priority=Priority.HIGH, created_at=base),
    ]
    assert select_ready_task(high_vs_all).number == 4

    # Medium beats low and none.
    medium_vs_low_none = [
        _task(5, TaskState.READY, priority=Priority.LOW, created_at=base - timedelta(days=10)),
        _task(6, TaskState.READY, priority=Priority.NONE, created_at=base - timedelta(days=10)),
        _task(7, TaskState.READY, priority=Priority.MEDIUM, created_at=base),
    ]
    assert select_ready_task(medium_vs_low_none).number == 7

    # Low beats none.
    low_vs_none = [
        _task(8, TaskState.READY, priority=Priority.NONE, created_at=base - timedelta(days=10)),
        _task(9, TaskState.READY, priority=Priority.LOW, created_at=base),
    ]
    assert select_ready_task(low_vs_none).number == 9

    # Within the same priority, the oldest Issue wins.
    same_priority_tie = [
        _task(10, TaskState.READY, priority=Priority.MEDIUM, created_at=base + timedelta(days=1)),
        _task(11, TaskState.READY, priority=Priority.MEDIUM, created_at=base),  # oldest
    ]
    assert select_ready_task(same_priority_tie).number == 11
