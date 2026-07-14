from datetime import datetime

from devbot.models import IssueTask, Job, JobType, Priority, TaskState
from devbot.scheduler import select_jobs


def _task(
    *,
    repository: str = "someone/myrepo",
    number: int = 1,
    state: TaskState = TaskState.READY,
    priority: Priority = Priority.NONE,
    created_at: datetime = datetime(2026, 1, 1),
) -> IssueTask:
    return IssueTask(
        repository=repository,
        number=number,
        title="task",
        state=state,
        priority=priority,
        created_at=created_at,
    )


def test_rework_job_has_highest_priority() -> None:
    """CP-012-7: a REWORK candidate is selected over REVIEW/IMPLEMENT
    candidates even when it loses on every secondary ordering axis
    (priority label, age, Issue number) - job type dominates."""
    rework_task = _task(
        repository="someone/rework-repo",
        number=999,
        state=TaskState.REVIEW,
        priority=Priority.LOW,
        created_at=datetime(2026, 1, 10),
    )
    review_task = _task(
        repository="someone/review-repo",
        number=1,
        state=TaskState.REVIEW,
        priority=Priority.HIGH,
        created_at=datetime(2026, 1, 1),
    )
    implement_task = _task(
        repository="someone/implement-repo",
        number=1,
        state=TaskState.READY,
        priority=Priority.HIGH,
        created_at=datetime(2026, 1, 1),
    )
    candidates = [
        Job(job_type=JobType.IMPLEMENT, task=implement_task),
        Job(job_type=JobType.REVIEW, task=review_task),
        Job(job_type=JobType.REWORK, task=rework_task),
    ]

    selected = select_jobs(candidates, max_concurrent_jobs=1)

    assert len(selected) == 1
    assert selected[0].job_type is JobType.REWORK
    assert selected[0].task is rework_task


def test_parallel_jobs_run_for_different_repositories() -> None:
    """CP-012-11: with enough slots, candidates in different repositories
    are all selected."""
    task_a = _task(repository="someone/repo-a")
    task_b = _task(repository="someone/repo-b")
    candidates = [
        Job(job_type=JobType.IMPLEMENT, task=task_a),
        Job(job_type=JobType.IMPLEMENT, task=task_b),
    ]

    selected = select_jobs(candidates, max_concurrent_jobs=2)

    assert {job.task.repository for job in selected} == {"someone/repo-a", "someone/repo-b"}


def test_same_repository_jobs_are_not_run_in_parallel() -> None:
    """CP-012-12: even with slots to spare, at most one job per repository
    is selected in a single call."""
    rework_task = _task(number=7, state=TaskState.REVIEW)
    implement_task = _task(number=8, state=TaskState.READY)
    candidates = [
        Job(job_type=JobType.REWORK, task=rework_task),
        Job(job_type=JobType.IMPLEMENT, task=implement_task),
    ]

    selected = select_jobs(candidates, max_concurrent_jobs=2)

    assert len(selected) == 1
    assert selected[0].job_type is JobType.REWORK


def test_select_jobs_returns_empty_list_for_no_candidates() -> None:
    assert select_jobs([], max_concurrent_jobs=1) == []


def test_select_jobs_returns_empty_list_when_max_concurrent_jobs_is_zero() -> None:
    candidates = [Job(job_type=JobType.IMPLEMENT, task=_task())]

    assert select_jobs(candidates, max_concurrent_jobs=0) == []
