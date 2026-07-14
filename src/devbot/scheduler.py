"""Job scheduling: turns a set of candidate `Job`s into the (at most
`max_concurrent_jobs`) jobs that actually run this cycle.

Pure and I/O-free - callers (`devbot.polling`) gather candidates from
GitHub state and hand them here; this module only orders and dedupes them.
No vendor/product name appears here (Task 012): jobs are typed only as
`JobType.REWORK` / `REVIEW` / `IMPLEMENT`.
"""

from __future__ import annotations

from collections.abc import Sequence

from devbot.models import Job, JobType, Priority

_JOB_TYPE_ORDER: dict[JobType, int] = {
    JobType.REWORK: 0,
    JobType.REVIEW: 1,
    JobType.IMPLEMENT: 2,
}

_PRIORITY_ORDER: dict[Priority, int] = {
    Priority.HIGH: 0,
    Priority.MEDIUM: 1,
    Priority.LOW: 2,
    Priority.NONE: 3,
}


def _sort_key(job: Job) -> tuple[int, int, object, int]:
    return (
        _JOB_TYPE_ORDER[job.job_type],
        _PRIORITY_ORDER[job.task.priority],
        job.task.created_at,
        job.task.number,
    )


def select_jobs(candidates: Sequence[Job], max_concurrent_jobs: int) -> list[Job]:
    """Select up to `max_concurrent_jobs` jobs from `candidates`.

    Ordering: every `REWORK` candidate sorts before every `REVIEW`
    candidate, which sorts before every `IMPLEMENT` candidate - a pending
    PR-feedback rework must never wait behind a review or a fresh
    implementation. Within the same job type, ties break the same way the
    original single-task Queue (Task 003) always has: priority label, then
    age (`created_at`), then Issue number.

    At most one job per repository is ever selected in a single call - if
    a repository has multiple candidates (e.g. a rework and, in a
    different repository issue, a ready implementation), only the
    highest-ranked one is selected; the rest defer to the next cycle. This
    is what keeps same-repository and same-Issue jobs from ever running
    concurrently, independent of `max_concurrent_jobs`.
    """
    if max_concurrent_jobs < 1:
        return []

    selected: list[Job] = []
    used_repositories: set[str] = set()
    for job in sorted(candidates, key=_sort_key):
        if len(selected) >= max_concurrent_jobs:
            break
        if job.task.repository in used_repositories:
            continue
        selected.append(job)
        used_repositories.add(job.task.repository)
    return selected
