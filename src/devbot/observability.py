"""Structured operational logging for DevBot (Task 013).

Everything here answers one question: *why* did the daemon do (or not do)
what it did this cycle? Startup logs the resolved runtime configuration
once; per-cycle logs correlate every search, candidate, exclusion, and Job
execution by a shared `cycle_id`; DEBUG adds the detailed search/selection
evidence INFO omits.

Two safety properties hold across every function in this module:

- Logging never raises. Every public `log_*` function swallows any
  exception it encounters (a bad formatter, a full disk, a handler that
  itself raises) and returns without propagating - a logging failure must
  never abort or change the outcome of a Job (Task 013 동작 규칙 #6 /
  CP-013-12). Callers (`devbot.polling`, `devbot.scheduler`, `devbot.main`)
  call these functions inline, unguarded, exactly because this module
  already guarantees they cannot raise.
- Logging never leaks secrets. `redact_secrets()` and the
  `SecretRedactingFilter` installed on the `devbot` logger strip known
  secret values and any `Authorization:`/`Bearer ...`-shaped substring
  from every message this module (or anything logging through the
  `devbot` logger) emits, regardless of which code path produced the
  message (CP-013-10).

Log messages and structured fields are assembled by small pure functions
(`build_*`) that return plain dataclasses / strings; the `log_*` wrappers
are the only place that actually calls into `logging`. Tests can exercise
the pure builders directly (checking fields and event codes, not prose) or
assert on `caplog` records' `extra` attributes.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import wraps
from importlib import metadata
from typing import ParamSpec, TypeVar

from devbot.models import CandidateExclusion, DevBotConfig, Job, JobType

LOG_LEVELS: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_LOGGER_NAME = "devbot"

# Both patterns consume the *rest of the line* after the trigger keyword,
# not just the next token - an `Authorization:` value is often a full
# `Bearer <token>` scheme+value pair, and stopping at the first
# whitespace-delimited token would strip the scheme but leave the token
# itself (the actual secret) exposed right after it.
_AUTH_HEADER_RE = re.compile(r"(?i)(authorization\s*[:=]\s*)\S.*")
_BEARER_RE = re.compile(r"(?i)(bearer\s+)\S.*")

_P = ParamSpec("_P")
_T = TypeVar("_T")


def redact_secrets(text: str, *secrets: str) -> str:
    """Replace every occurrence of a known secret value, plus anything
    shaped like an `Authorization:`/`Bearer ...` header, with `***`.

    Applied unconditionally (not only when a secret happens to be known)
    so an incidental `Authorization: Bearer <token>` string surfaced from,
    say, a raw HTTP error body is still redacted even if the token value
    itself was never passed in."""
    redacted = text
    for secret in secrets:
        if secret:
            redacted = redacted.replace(secret, "***")
    redacted = _AUTH_HEADER_RE.sub(r"\1***", redacted)
    redacted = _BEARER_RE.sub(r"\1***", redacted)
    return redacted


class SecretRedactingFilter(logging.Filter):
    """A `logging.Filter` that redacts `secrets` (and any
    Authorization/Bearer-shaped substring) from every record's rendered
    message before it reaches any handler."""

    def __init__(self, secrets: Sequence[str]) -> None:
        super().__init__()
        self._secrets = tuple(secret for secret in secrets if secret)

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            original = record.getMessage()
        except Exception:  # noqa: BLE001 - never break logging
            return True
        redacted = redact_secrets(original, *self._secrets)
        record.msg = redacted
        record.args = ()
        return True


def install_secret_filter(logger: logging.Logger, secrets: Sequence[str]) -> None:
    """Attach a `SecretRedactingFilter` for `secrets` to `logger`,
    replacing any previously installed one (safe to call more than once,
    e.g. across repeated `main()` calls in tests)."""
    for existing in list(logger.filters):
        if isinstance(existing, SecretRedactingFilter):
            logger.removeFilter(existing)
    logger.addFilter(SecretRedactingFilter(secrets))


_GUARDED_METHODS = ("debug", "info", "warning", "error", "exception", "critical", "log")


class SafeLogger:
    """Wraps a `logging.Logger` so every logging call site using it is
    exception-safe by construction, not only the `log_*` helpers below
    (CP-013-12: a logging failure - a broken handler, a formatter bug -
    must never abort or change a Job's outcome). Everything else
    (`.level`, `.isEnabledFor`, `.setLevel`, `.handlers`, ...) delegates
    straight to the wrapped logger, so this is a drop-in wherever a
    `logging.Logger` is expected."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    def __getattr__(self, name: str) -> object:
        attr = getattr(self._logger, name)
        if name not in _GUARDED_METHODS:
            return attr

        def _guarded(*args: object, **kwargs: object) -> None:
            try:
                attr(*args, **kwargs)
            except Exception:  # noqa: BLE001 - logging must never crash the caller
                pass

        return _guarded


def ensure_safe_logger(logger: logging.Logger) -> logging.Logger:
    """Return `logger` wrapped in a `SafeLogger`, unless it already is
    one."""
    if isinstance(logger, SafeLogger):
        return logger
    return SafeLogger(logger)  # type: ignore[return-value]


def _safe_log[**P, T](fn: Callable[_P, _T]) -> Callable[_P, _T | None]:
    """Wrap a `log_*` function so any exception it raises is swallowed
    instead of propagating (CP-013-12: a logging failure must never abort
    Job execution)."""

    @wraps(fn)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _T | None:
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - logging must never crash the caller
            return None

    return wrapper


def new_cycle_id() -> str:
    """A short opaque identifier correlating every log line for one
    `PollingService.run_cycle()` call."""
    return uuid.uuid4().hex[:12]


def elapsed_ms(start: float) -> float:
    """Milliseconds since `start` (a `time.monotonic()` timestamp)."""
    return (time.monotonic() - start) * 1000


def _resolve_version() -> str:
    """Best-effort build identifier: the installed package version if
    available, else the current Git commit (short SHA), else `"unknown"`.
    Never raises - a fresh checkout with no Git history or an editable
    install without package metadata must not block startup."""
    try:
        return metadata.version("devbot")
    except metadata.PackageNotFoundError:
        pass
    except Exception:  # noqa: BLE001 - version lookup must never block startup
        pass

    try:
        completed = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if completed.returncode == 0:
            commit = completed.stdout.strip()
            if commit:
                return commit
    except Exception:  # noqa: BLE001 - version lookup must never block startup
        pass

    return "unknown"


@_safe_log
def log_startup(logger: logging.Logger, config: DevBotConfig) -> None:
    """Log the resolved runtime configuration once at daemon startup
    (Task 013 "시작 로그"): version, role assignments, dry-run, poll
    interval, concurrency, and every managed repository's identity, local
    path, and default branch. Never includes `config.github_token`."""
    repositories = config.enabled_repositories
    logger.info(
        "DevBot 시작: version=%s implementer=%s reviewer=%s dry_run=%s "
        "poll_interval_seconds=%d max_concurrent_jobs=%d log_level=%s "
        "관리 저장소 수=%d",
        _resolve_version(),
        config.implementer_agent,
        config.reviewer_agent,
        config.dry_run,
        config.poll_interval_seconds,
        config.max_concurrent_jobs,
        config.log_level,
        len(repositories),
        extra={
            "event": "startup",
            "implementer_agent": config.implementer_agent,
            "reviewer_agent": config.reviewer_agent,
            "dry_run": config.dry_run,
            "poll_interval_seconds": config.poll_interval_seconds,
            "max_concurrent_jobs": config.max_concurrent_jobs,
            "managed_repository_count": len(repositories),
        },
    )
    for repository in repositories:
        logger.info(
            "관리 저장소: %s local_path=%s default_branch=%s",
            repository.full_name,
            repository.local_path,
            repository.default_branch,
            extra={
                "event": "managed_repository",
                "repository": repository.full_name,
                "local_path": str(repository.local_path),
                "default_branch": repository.default_branch,
            },
        )


@_safe_log
def log_no_managed_repositories(logger: logging.Logger) -> None:
    """CP-013-5: a clear, distinct diagnostic (never conflated with
    `no_ready_task`) plus recovery guidance when there is nothing to poll
    because no repository is configured/enabled at all."""
    logger.warning(
        "no_managed_repositories: 관리 저장소가 0개라 이번 cycle을 건너뜁니다. "
        "config/repositories.yaml과 각 repository의 enabled 설정을 확인하세요.",
        extra={"event": "no_managed_repositories"},
    )


@_safe_log
def log_cycle_start(logger: logging.Logger, cycle_id: str, repository_count: int) -> None:
    logger.info(
        "cycle 시작: cycle_id=%s 관리 저장소 수=%d",
        cycle_id,
        repository_count,
        extra={
            "event": "cycle_start",
            "cycle_id": cycle_id,
            "managed_repository_count": repository_count,
        },
    )


@dataclass(frozen=True, slots=True)
class CycleSummary:
    """Pure summary of one cycle's outcome, built before logging so the
    aggregation logic itself is testable without a logger."""

    cycle_id: str
    elapsed_ms: float
    candidate_counts: dict[JobType, int]
    selected_count: int
    available_slots: int
    result_statuses: tuple[str, ...]


def build_cycle_summary(
    *,
    cycle_id: str,
    start: float,
    candidates: Sequence[Job],
    selected: Sequence[Job],
    available_slots: int,
    results: Sequence[object],
) -> CycleSummary:
    """Build a `CycleSummary` from raw cycle state. `results` items only
    need a `.status` attribute (typically `PollingResult`); kept loosely
    typed here so this module has no dependency on `devbot.polling`."""
    counts = {job_type: 0 for job_type in JobType}
    for job in candidates:
        counts[job.job_type] += 1
    statuses = tuple(
        result.status.value  # type: ignore[attr-defined]
        for result in results
        if result is not None and hasattr(result, "status")
    )
    return CycleSummary(
        cycle_id=cycle_id,
        elapsed_ms=elapsed_ms(start),
        candidate_counts=counts,
        selected_count=len(selected),
        available_slots=available_slots,
        result_statuses=statuses,
    )


@_safe_log
def log_cycle_end(logger: logging.Logger, summary: CycleSummary) -> None:
    logger.info(
        "cycle 종료: cycle_id=%s 소요=%dms 후보(rework=%d review=%d implement=%d) "
        "선택=%d/%d 결과=%s",
        summary.cycle_id,
        summary.elapsed_ms,
        summary.candidate_counts.get(JobType.REWORK, 0),
        summary.candidate_counts.get(JobType.REVIEW, 0),
        summary.candidate_counts.get(JobType.IMPLEMENT, 0),
        summary.selected_count,
        summary.available_slots,
        ", ".join(summary.result_statuses) or "-",
        extra={
            "event": "cycle_end",
            "cycle_id": summary.cycle_id,
            "elapsed_ms": summary.elapsed_ms,
            "rework_candidates": summary.candidate_counts.get(JobType.REWORK, 0),
            "review_candidates": summary.candidate_counts.get(JobType.REVIEW, 0),
            "implement_candidates": summary.candidate_counts.get(JobType.IMPLEMENT, 0),
            "selected_count": summary.selected_count,
            "available_slots": summary.available_slots,
        },
    )


@_safe_log
def log_repository_search(
    logger: logging.Logger,
    cycle_id: str,
    repository: str,
    *,
    state: str,
    label_filter: str,
    result_count: int,
) -> None:
    """DEBUG only (Task 013 #3): the logical GitHub search DevBot issued
    for `repository` and how many Issues it returned, before any
    devbot:*-label/candidate filtering. CP-013-6 / CP-013-11."""
    logger.debug(
        "저장소 검색: cycle_id=%s repo=%s state=%s label_filter=%s 결과 수=%d",
        cycle_id,
        repository,
        state,
        label_filter,
        result_count,
        extra={
            "event": "repository_search",
            "cycle_id": cycle_id,
            "repository": repository,
            "state": state,
            "label_filter": label_filter,
            "result_count": result_count,
        },
    )


@_safe_log
def log_candidate_found(
    logger: logging.Logger,
    cycle_id: str,
    job: Job,
    *,
    pr_number: int | None = None,
) -> None:
    """DEBUG: one candidate Job produced during collection, before
    scheduling (CP-013-6)."""
    logger.debug(
        "후보 발견: cycle_id=%s repo=%s issue=#%d pr=%s job_type=%s",
        cycle_id,
        job.task.repository,
        job.task.number,
        pr_number if pr_number is not None else "-",
        job.job_type.value,
        extra={
            "event": "candidate_found",
            "cycle_id": cycle_id,
            "repository": job.task.repository,
            "issue_number": job.task.number,
            "pr_number": pr_number,
            "job_type": job.job_type.value,
        },
    )


@_safe_log
def log_candidate_excluded(
    logger: logging.Logger, cycle_id: str, exclusion: CandidateExclusion
) -> None:
    """DEBUG: one candidate that did not become a selected Job this cycle,
    with a structured `ExclusionReason` (CP-013-7)."""
    logger.debug(
        "후보 제외: cycle_id=%s repo=%s issue=#%d job_type=%s reason=%s detail=%s",
        cycle_id,
        exclusion.repository,
        exclusion.issue_number,
        exclusion.job_type.value if exclusion.job_type is not None else "-",
        exclusion.reason.value,
        exclusion.detail,
        extra={
            "event": "candidate_excluded",
            "cycle_id": cycle_id,
            "repository": exclusion.repository,
            "issue_number": exclusion.issue_number,
            "job_type": exclusion.job_type.value if exclusion.job_type is not None else None,
            "reason": exclusion.reason.value,
        },
    )


@_safe_log
def log_candidates_excluded(
    logger: logging.Logger, cycle_id: str, exclusions: Sequence[CandidateExclusion]
) -> None:
    for exclusion in exclusions:
        log_candidate_excluded(logger, cycle_id, exclusion)


@_safe_log
def log_job_selected(logger: logging.Logger, cycle_id: str, job: Job, *, rank: int) -> None:
    """INFO: one Job actually selected to run this cycle, with the
    correlation fields (cycle/repository/issue/job type) CP-013-8
    requires - `rank` is this job's 1-based position in the selection
    order (rework outranks review outranks implement, then priority/age)."""
    logger.info(
        "Job 선택: cycle_id=%s repo=%s issue=#%d job_type=%s 순위=%d",
        cycle_id,
        job.task.repository,
        job.task.number,
        job.job_type.value,
        rank,
        extra={
            "event": "job_selected",
            "cycle_id": cycle_id,
            "repository": job.task.repository,
            "issue_number": job.task.number,
            "job_type": job.job_type.value,
            "rank": rank,
        },
    )


@_safe_log
def log_jobs_selected(
    logger: logging.Logger, cycle_id: str, jobs: Sequence[Job], *, available_slots: int
) -> None:
    logger.info(
        "Job 선택 완료: cycle_id=%s 선택=%d 가용 slot=%d",
        cycle_id,
        len(jobs),
        available_slots,
        extra={
            "event": "jobs_selected",
            "cycle_id": cycle_id,
            "selected_count": len(jobs),
            "available_slots": available_slots,
        },
    )
    for rank, job in enumerate(jobs, start=1):
        log_job_selected(logger, cycle_id, job, rank=rank)


@_safe_log
def log_job_started(
    logger: logging.Logger,
    cycle_id: str,
    job: Job,
    *,
    role: str,
    pr_number: int | None = None,
) -> None:
    logger.info(
        "Job 시작: cycle_id=%s repo=%s issue=#%d pr=%s job_type=%s role=%s",
        cycle_id,
        job.task.repository,
        job.task.number,
        pr_number if pr_number is not None else "-",
        job.job_type.value,
        role,
        extra={
            "event": "job_started",
            "cycle_id": cycle_id,
            "repository": job.task.repository,
            "issue_number": job.task.number,
            "pr_number": pr_number,
            "job_type": job.job_type.value,
            "role": role,
        },
    )


@_safe_log
def log_job_finished(
    logger: logging.Logger,
    cycle_id: str,
    job: Job,
    *,
    status: str,
    start: float,
    failure_summary: str = "",
) -> None:
    """INFO: Job end, with total elapsed time (CP-013-9). `failure_summary`
    is a short, already-redacted description of the last error - never the
    raw Agent stdout/stderr (Task 013 #5)."""
    duration_ms = elapsed_ms(start)
    logger.info(
        "Job 종료: cycle_id=%s repo=%s issue=#%d job_type=%s status=%s 소요=%dms",
        cycle_id,
        job.task.repository,
        job.task.number,
        job.job_type.value,
        status,
        duration_ms,
        extra={
            "event": "job_finished",
            "cycle_id": cycle_id,
            "repository": job.task.repository,
            "issue_number": job.task.number,
            "job_type": job.job_type.value,
            "status": status,
            "elapsed_ms": duration_ms,
        },
    )
    if failure_summary:
        logger.error(
            "Job 실패 요약: cycle_id=%s repo=%s issue=#%d %s",
            cycle_id,
            job.task.repository,
            job.task.number,
            redact_secrets(failure_summary),
            extra={
                "event": "job_failure_summary",
                "cycle_id": cycle_id,
                "repository": job.task.repository,
                "issue_number": job.task.number,
            },
        )


@_safe_log
def log_stage(
    logger: logging.Logger,
    cycle_id: str,
    *,
    repository: str,
    issue_number: int,
    stage: str,
    start: float,
) -> None:
    """DEBUG: one named sub-step's elapsed time within a Job (agent
    execution, verification/commit/push/PR delivery, GitHub state write -
    Task 013 #5's "주요 단계별 소요 시간")."""
    duration_ms = elapsed_ms(start)
    logger.debug(
        "단계 완료: cycle_id=%s repo=%s issue=#%d stage=%s 소요=%dms",
        cycle_id,
        repository,
        issue_number,
        stage,
        duration_ms,
        extra={
            "event": "stage_finished",
            "cycle_id": cycle_id,
            "repository": repository,
            "issue_number": issue_number,
            "stage": stage,
            "elapsed_ms": duration_ms,
        },
    )
