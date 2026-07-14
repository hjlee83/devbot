"""Job scheduling: turns a set of candidate `Job`s into the (at most
`max_concurrent_jobs`) jobs that actually run this cycle.

Pure and I/O-free - callers (`devbot.polling`) gather candidates from
GitHub state and hand them here; this module only orders and dedupes them.
No vendor/product name appears here (Task 012): jobs are typed only as
`JobType.REWORK` / `REVIEW` / `IMPLEMENT`.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from devbot.models import CandidateExclusion, ExclusionReason, Job, JobType, Priority

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


@dataclass(frozen=True, slots=True)
class SelectionResult:
    """`select_jobs_with_exclusions()`'s full output: the jobs selected to
    run this cycle, plus a structured, testable reason for every candidate
    that was *not* selected (Task 013 CP-013-7)."""

    selected: list[Job] = field(default_factory=list)
    exclusions: list[CandidateExclusion] = field(default_factory=list)


def select_jobs_with_exclusions(
    candidates: Sequence[Job], max_concurrent_jobs: int
) -> SelectionResult:
    """Same selection as `select_jobs()`, plus a structured exclusion
    reason for every candidate that didn't make it in.

    A candidate is excluded for exactly one of two reasons at this stage:

    - `LOWER_PRIORITY`: another candidate for the *same* repository already
      outranked it (job type, then priority, then age, then Issue number) -
      at most one job per repository is ever selected, independent of
      `max_concurrent_jobs`.
    - `CONCURRENCY_LIMIT`: its repository was still free, but every
      available `max_concurrent_jobs` slot was already filled by
      higher-ranked candidates.
    """
    if max_concurrent_jobs < 1:
        return SelectionResult(
            selected=[],
            exclusions=[
                CandidateExclusion(
                    repository=job.task.repository,
                    issue_number=job.task.number,
                    reason=ExclusionReason.CONCURRENCY_LIMIT,
                    job_type=job.job_type,
                    detail="max_concurrent_jobs < 1",
                )
                for job in candidates
            ],
        )

    selected: list[Job] = []
    exclusions: list[CandidateExclusion] = []
    used_repositories: set[str] = set()
    for job in sorted(candidates, key=_sort_key):
        if job.task.repository in used_repositories:
            exclusions.append(
                CandidateExclusion(
                    repository=job.task.repository,
                    issue_number=job.task.number,
                    reason=ExclusionReason.LOWER_PRIORITY,
                    job_type=job.job_type,
                    detail="same repository already selected a higher-ranked job this cycle",
                )
            )
            continue
        if len(selected) >= max_concurrent_jobs:
            exclusions.append(
                CandidateExclusion(
                    repository=job.task.repository,
                    issue_number=job.task.number,
                    reason=ExclusionReason.CONCURRENCY_LIMIT,
                    job_type=job.job_type,
                    detail=f"max_concurrent_jobs={max_concurrent_jobs} slots already filled",
                )
            )
            continue
        selected.append(job)
        used_repositories.add(job.task.repository)
    return SelectionResult(selected=selected, exclusions=exclusions)


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

    A thin wrapper around `select_jobs_with_exclusions()` kept for callers
    that only need the selected list (unchanged signature/behavior from
    Task 012).
    """
    return select_jobs_with_exclusions(candidates, max_concurrent_jobs).selected
