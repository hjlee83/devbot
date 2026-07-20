"""Runtime worker scheduler for executing selected DevBot jobs.

The pure selector in ``devbot.scheduler`` decides which Jobs are runnable for a
cycle. This module owns the runtime execution layer above that selection: a
configurable worker pool, one lock per repository, an independently sized AI
semaphore, and a status snapshot suitable for a future ``devbot status`` view.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from devbot.models import Job


class WorkerState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    FAILED = "failed"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class WorkerSnapshot:
    worker_id: int
    state: WorkerState
    repository: str | None = None
    issue_number: int | None = None
    job_type: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    message: str = ""


@dataclass(frozen=True, slots=True)
class RuntimeSchedulerSnapshot:
    worker_count: int
    ai_concurrency: int
    workers: tuple[WorkerSnapshot, ...]


@dataclass
class RuntimeScheduler:
    """Execute selected Jobs through a worker pool with per-repository locks."""

    worker_count: int
    ai_concurrency: int
    _repository_locks: dict[str, threading.Lock] = field(default_factory=dict, init=False)
    _state_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _workers: dict[int, WorkerSnapshot] = field(default_factory=dict, init=False)

    def __post_init__(self) -> None:
        if self.worker_count < 1:
            raise ValueError("worker_count must be >= 1")
        if self.ai_concurrency < 1:
            raise ValueError("ai_concurrency must be >= 1")
        self._ai_semaphore = threading.Semaphore(self.ai_concurrency)
        for index in range(self.worker_count):
            self._workers[index] = WorkerSnapshot(worker_id=index, state=WorkerState.IDLE)

    def snapshot(self) -> RuntimeSchedulerSnapshot:
        with self._state_lock:
            workers = tuple(self._workers[index] for index in sorted(self._workers))
        return RuntimeSchedulerSnapshot(
            worker_count=self.worker_count, ai_concurrency=self.ai_concurrency, workers=workers
        )

    def _repository_lock(self, repository: str) -> threading.Lock:
        with self._state_lock:
            lock = self._repository_locks.get(repository)
            if lock is None:
                lock = threading.Lock()
                self._repository_locks[repository] = lock
            return lock

    def _set_worker(self, worker_id: int, snapshot: WorkerSnapshot) -> None:
        with self._state_lock:
            self._workers[worker_id] = snapshot

    def build_repository_queues(self, jobs: Sequence[Job]) -> Mapping[str, tuple[Job, ...]]:
        """Group selected jobs into repository-local FIFO queues."""

        queues: dict[str, list[Job]] = {}
        for job in jobs:
            queues.setdefault(job.task.repository, []).append(job)
        return {
            repository: tuple(repository_jobs)
            for repository, repository_jobs in queues.items()
        }

    def execute[Result](
        self,
        jobs: Sequence[Job],
        run_job: Callable[[Job], Result],
        make_error_result: Callable[[Job, Exception], Result],
    ) -> list[Result]:
        """Run ``jobs`` and preserve input order in the returned results."""

        if not jobs:
            return []

        repository_queues = self.build_repository_queues(jobs)
        for repository in repository_queues:
            self._repository_lock(repository)

        results: list[Result | None] = [None for _ in jobs]
        worker_slots = list(range(self.worker_count))
        slot_lock = threading.Lock()

        def acquire_worker_id() -> int:
            with slot_lock:
                return worker_slots.pop()

        def release_worker_id(worker_id: int) -> None:
            with slot_lock:
                worker_slots.append(worker_id)

        def run_one(index: int, job: Job) -> tuple[int, Result]:
            worker_id = acquire_worker_id()
            repository = job.task.repository
            issue_number = job.task.number
            job_type = job.job_type.value
            started_at = datetime.now(UTC)
            self._set_worker(
                worker_id,
                WorkerSnapshot(
                    worker_id=worker_id,
                    state=WorkerState.RUNNING,
                    repository=repository,
                    issue_number=issue_number,
                    job_type=job_type,
                    started_at=started_at,
                ),
            )
            try:
                with self._repository_lock(repository):
                    with self._ai_semaphore:
                        result = run_job(job)
                self._set_worker(
                    worker_id,
                    WorkerSnapshot(
                        worker_id=worker_id,
                        state=WorkerState.COMPLETED,
                        repository=repository,
                        issue_number=issue_number,
                        job_type=job_type,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                    ),
                )
                return index, result
            except Exception as exc:
                result = make_error_result(job, exc)
                self._set_worker(
                    worker_id,
                    WorkerSnapshot(
                        worker_id=worker_id,
                        state=WorkerState.FAILED,
                        repository=repository,
                        issue_number=issue_number,
                        job_type=job_type,
                        started_at=started_at,
                        finished_at=datetime.now(UTC),
                        message=str(exc),
                    ),
                )
                return index, result
            finally:
                release_worker_id(worker_id)

        with ThreadPoolExecutor(max_workers=min(self.worker_count, len(jobs))) as executor:
            future_to_index = {
                executor.submit(run_one, index, job): index
                for index, job in enumerate(jobs)
            }
            for future in as_completed(future_to_index):
                index, result = future.result()
                results[index] = result

        return [result for result in results if result is not None]
