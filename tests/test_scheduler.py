from datetime import datetime

from devbot.models import ExclusionReason, IssueTask, Job, JobType, Priority, TaskState
from devbot.observability import build_queue_summary
from devbot.scheduler import select_jobs, select_jobs_with_exclusions


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


def test_state_machine_preserves_job_priority_and_concurrency() -> None:
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


def test_scheduler_logs_structured_candidate_exclusion_reason() -> None:
    """CP-013-7: every candidate that does not become a selected Job is
    reported with a structured `ExclusionReason` (not a free-form string),
    and the reason matches the scheduler's actual ranking decision."""
    rework_task = _task(repository="someone/repo-a", number=1, state=TaskState.REVIEW)
    same_repo_loser = _task(repository="someone/repo-a", number=2, state=TaskState.READY)
    over_capacity_task = _task(repository="someone/repo-b", number=1, state=TaskState.READY)
    candidates = [
        Job(job_type=JobType.REWORK, task=rework_task),
        Job(job_type=JobType.IMPLEMENT, task=same_repo_loser),
        Job(job_type=JobType.IMPLEMENT, task=over_capacity_task),
    ]

    result = select_jobs_with_exclusions(candidates, max_concurrent_jobs=1)

    assert [job.task.number for job in result.selected] == [1]
    exclusions_by_issue = {exclusion.issue_number: exclusion for exclusion in result.exclusions}
    assert exclusions_by_issue[2].reason is ExclusionReason.LOWER_PRIORITY
    assert exclusions_by_issue[2].repository == "someone/repo-a"
    assert exclusions_by_issue[1].reason is ExclusionReason.CONCURRENCY_LIMIT
    # (Boundary) `select_jobs()` itself is unaffected by the new
    # diagnostics - same selection as always.
    assert select_jobs(candidates, max_concurrent_jobs=1) == result.selected


def test_select_jobs_with_exclusions_reports_concurrency_limit_when_zero_slots() -> None:
    """Boundary: `max_concurrent_jobs=0` excludes every candidate with
    `CONCURRENCY_LIMIT`, never silently dropping the diagnostic."""
    candidates = [Job(job_type=JobType.IMPLEMENT, task=_task())]

    result = select_jobs_with_exclusions(candidates, max_concurrent_jobs=0)

    assert result.selected == []
    assert len(result.exclusions) == 1
    assert result.exclusions[0].reason is ExclusionReason.CONCURRENCY_LIMIT


def test_queue_summary_does_not_change_job_selection() -> None:
    """CP-020-9: building a Task 020 queue summary alongside scheduling is
    read-only observation - it must not change
    `select_jobs_with_exclusions()`'s REWORK > REVIEW > IMPLEMENT priority
    or per-repository concurrency decision."""
    rework_task = _task(
        repository="someone/rework-repo", number=1, state=TaskState.REVIEW, priority=Priority.LOW
    )
    review_task = _task(
        repository="someone/review-repo", number=2, state=TaskState.REVIEW, priority=Priority.HIGH
    )
    implement_task = _task(
        repository="someone/implement-repo", number=3, state=TaskState.READY
    )
    tasks = [rework_task, review_task, implement_task]
    candidates = [
        Job(job_type=JobType.REWORK, task=rework_task),
        Job(job_type=JobType.REVIEW, task=review_task),
        Job(job_type=JobType.IMPLEMENT, task=implement_task),
    ]

    before = select_jobs_with_exclusions(candidates, max_concurrent_jobs=1)
    summary = build_queue_summary("cycle-1", tasks)
    after = select_jobs_with_exclusions(candidates, max_concurrent_jobs=1)

    assert before.selected == after.selected
    assert [job.job_type for job in after.selected] == [JobType.REWORK]
    # The queue summary itself is unaffected by - and does not affect -
    # which Job the scheduler picked.
    assert summary.review == 2
    assert summary.ready == 1
