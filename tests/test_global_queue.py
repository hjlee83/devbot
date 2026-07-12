from datetime import datetime, timedelta
from pathlib import Path

from devbot.models import IssueTask, Priority, RepositoryConfig, TaskState
from devbot.queue import select_global_ready_task


def _repo(name: str, *, enabled: bool = True) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo=name,
        enabled=enabled,
        local_path=Path(f"/tmp/workspace/{name}"),
    )


def _task(
    repository: str,
    number: int,
    state: TaskState,
    priority: Priority = Priority.NONE,
    created_at: datetime | None = None,
) -> IssueTask:
    return IssueTask(
        repository=repository,
        number=number,
        title=f"{repository}#{number}",
        state=state,
        priority=priority,
        created_at=created_at or datetime(2026, 1, 1),
    )


def test_working_issue_in_any_repo_blocks_selection() -> None:
    repositories = [_repo("repo-a"), _repo("repo-b")]
    tasks = [
        _task("someone/repo-a", 1, TaskState.WORKING),
        _task("someone/repo-b", 2, TaskState.READY, priority=Priority.HIGH),
    ]

    assert select_global_ready_task(tasks, repositories) is None


def test_review_issue_in_any_repo_blocks_selection() -> None:
    repositories = [_repo("repo-a"), _repo("repo-b")]
    tasks = [
        _task("someone/repo-a", 1, TaskState.REVIEW),
        _task("someone/repo-b", 2, TaskState.READY, priority=Priority.HIGH),
    ]

    assert select_global_ready_task(tasks, repositories) is None


def test_global_queue_prefers_highest_priority() -> None:
    base = datetime(2026, 1, 1)
    repositories = [_repo("repo-a"), _repo("repo-b"), _repo("repo-c")]
    tasks = [
        _task("someone/repo-a", 1, TaskState.READY, priority=Priority.LOW, created_at=base),
        _task("someone/repo-b", 2, TaskState.READY, priority=Priority.MEDIUM, created_at=base),
        _task("someone/repo-c", 3, TaskState.READY, priority=Priority.HIGH, created_at=base),
    ]

    selected = select_global_ready_task(tasks, repositories)

    assert selected is not None
    assert (selected.repository, selected.number) == ("someone/repo-c", 3)


def test_global_queue_prefers_oldest_with_same_priority() -> None:
    base = datetime(2026, 1, 1)
    repositories = [_repo("repo-a"), _repo("repo-b")]
    tasks = [
        _task(
            "someone/repo-a",
            1,
            TaskState.READY,
            priority=Priority.MEDIUM,
            created_at=base + timedelta(days=2),
        ),
        _task(
            "someone/repo-b",
            2,
            TaskState.READY,
            priority=Priority.MEDIUM,
            created_at=base,
        ),  # oldest, different repo
    ]

    selected = select_global_ready_task(tasks, repositories)

    assert selected is not None
    assert (selected.repository, selected.number) == ("someone/repo-b", 2)


def test_disabled_repository_is_ignored() -> None:
    repositories = [_repo("repo-a", enabled=False), _repo("repo-b", enabled=True)]
    tasks = [
        # Highest priority, but lives in a disabled repository.
        _task("someone/repo-a", 1, TaskState.READY, priority=Priority.HIGH),
        _task("someone/repo-b", 2, TaskState.READY, priority=Priority.LOW),
    ]

    selected = select_global_ready_task(tasks, repositories)

    assert selected is not None
    assert (selected.repository, selected.number) == ("someone/repo-b", 2)


def test_disabled_repository_active_task_does_not_block_selection() -> None:
    """A working/review Issue in a disabled repository must not block
    selection elsewhere: disabled repositories are ignored entirely, not
    just excluded from ready-task candidates."""
    repositories = [_repo("repo-a", enabled=False), _repo("repo-b", enabled=True)]
    tasks = [
        _task("someone/repo-a", 1, TaskState.WORKING),
        _task("someone/repo-b", 2, TaskState.READY, priority=Priority.LOW),
    ]

    selected = select_global_ready_task(tasks, repositories)

    assert selected is not None
    assert (selected.repository, selected.number) == ("someone/repo-b", 2)


def test_no_ready_issue_returns_none() -> None:
    repositories = [_repo("repo-a"), _repo("repo-b")]

    assert select_global_ready_task([], repositories) is None

    done_only = [_task("someone/repo-a", 1, TaskState.DONE)]
    assert select_global_ready_task(done_only, repositories) is None
