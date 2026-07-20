import threading
import time
from datetime import datetime

from devbot.models import IssueTask, Job, JobType, Priority, TaskState
from devbot.runtime_scheduler import RuntimeScheduler, WorkerState


def _job(repository: str, number: int) -> Job:
    return Job(
        job_type=JobType.IMPLEMENT,
        task=IssueTask(
            repository=repository,
            number=number,
            title=f"task {number}",
            state=TaskState.READY,
            priority=Priority.NONE,
            created_at=datetime(2026, 1, 1),
        ),
    )


def test_runtime_scheduler_runs_different_repositories_concurrently() -> None:
    scheduler = RuntimeScheduler(worker_count=2, ai_concurrency=2)
    running = 0
    max_running = 0
    lock = threading.Lock()

    def run(job: Job) -> str:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return job.task.repository

    results = scheduler.execute(
        [_job("someone/a", 1), _job("someone/b", 2)],
        run,
        lambda job, exc: f"error:{exc}",
    )

    assert results == ["someone/a", "someone/b"]
    assert max_running == 2


def test_runtime_scheduler_serializes_same_repository_jobs() -> None:
    scheduler = RuntimeScheduler(worker_count=2, ai_concurrency=2)
    running_by_repo = 0
    max_same_repo = 0
    lock = threading.Lock()

    def run(job: Job) -> str:
        nonlocal running_by_repo, max_same_repo
        with lock:
            running_by_repo += 1
            max_same_repo = max(max_same_repo, running_by_repo)
        time.sleep(0.05)
        with lock:
            running_by_repo -= 1
        return str(job.task.number)

    results = scheduler.execute(
        [_job("someone/a", 1), _job("someone/a", 2)],
        run,
        lambda job, exc: f"error:{exc}",
    )

    assert results == ["1", "2"]
    assert max_same_repo == 1


def test_runtime_scheduler_ai_semaphore_limits_ai_concurrency() -> None:
    scheduler = RuntimeScheduler(worker_count=2, ai_concurrency=1)
    running = 0
    max_running = 0
    lock = threading.Lock()

    def run(job: Job) -> str:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.05)
        with lock:
            running -= 1
        return job.task.repository

    scheduler.execute(
        [_job("someone/a", 1), _job("someone/b", 2)],
        run,
        lambda job, exc: f"error:{exc}",
    )

    assert max_running == 1


def test_runtime_scheduler_builds_one_fifo_queue_per_repository() -> None:
    scheduler = RuntimeScheduler(worker_count=3, ai_concurrency=2)
    first_a = _job("someone/a", 1)
    first_b = _job("someone/b", 2)
    second_a = _job("someone/a", 3)

    queues = scheduler.build_repository_queues([first_a, first_b, second_a])

    assert list(queues) == ["someone/a", "someone/b"]
    assert queues["someone/a"] == (first_a, second_a)
    assert queues["someone/b"] == (first_b,)


def test_runtime_scheduler_snapshot_exposes_worker_state() -> None:
    scheduler = RuntimeScheduler(worker_count=2, ai_concurrency=1)

    snapshot = scheduler.snapshot()

    assert snapshot.worker_count == 2
    assert snapshot.ai_concurrency == 1
    assert [worker.state for worker in snapshot.workers] == [WorkerState.IDLE, WorkerState.IDLE]
