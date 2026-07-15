import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from devbot.models import (
    CandidateExclusion,
    DevBotConfig,
    ExclusionReason,
    IssueTask,
    Job,
    JobType,
    Priority,
    RepositoryConfig,
    TaskState,
)
from devbot.observability import (
    SafeLogger,
    SecretRedactingFilter,
    build_cycle_summary,
    build_queue_summary,
    elapsed_ms,
    ensure_safe_logger,
    install_secret_filter,
    log_candidate_excluded,
    log_job_finished,
    log_startup,
    new_cycle_id,
    redact_secrets,
)
from devbot.polling import PollingResult, PollingStatus


def _task(
    *,
    repository: str = "someone/myrepo",
    number: int = 1,
    state: TaskState = TaskState.READY,
) -> IssueTask:
    return IssueTask(
        repository=repository,
        number=number,
        title="task",
        state=state,
        priority=Priority.NONE,
        created_at=datetime(2026, 1, 1),
    )


def _repo(name: str = "myrepo") -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo=name, enabled=True, local_path=Path(f"/tmp/workspace/{name}")
    )


def _config(repositories: list[RepositoryConfig]) -> DevBotConfig:
    return DevBotConfig(
        workspace_root=Path("/tmp/workspace"),
        poll_interval_seconds=60,
        lock_file=Path("/tmp/devbot.lock"),
        default_agent="codex",
        implementer_agent="claude",
        reviewer_agent="codex",
        max_concurrent_jobs=1,
        dry_run=True,
        github_token="super-secret-token",
        repositories=tuple(repositories),
    )


# --- redact_secrets / SecretRedactingFilter (CP-013-10) ---------------------


def test_redact_secrets_replaces_known_secret_value() -> None:
    text = "GitHub 요청 실패: token=super-secret-token"

    redacted = redact_secrets(text, "super-secret-token")

    assert "super-secret-token" not in redacted
    assert "***" in redacted


def test_redact_secrets_redacts_authorization_header_value_even_when_unknown() -> None:
    """The Authorization/Bearer pattern is redacted even when the actual
    secret value was never passed in - e.g. surfaced verbatim inside a raw
    HTTP error body this module didn't generate itself."""
    text = "curl failed: Authorization: Bearer abc123XYZ-not-a-known-secret"

    redacted = redact_secrets(text)

    assert "abc123XYZ-not-a-known-secret" not in redacted
    assert "Authorization: ***" in redacted


def test_redact_secrets_redacts_bare_bearer_token_without_authorization_prefix() -> None:
    text = "using bearer sekrit-value-here for this request"

    redacted = redact_secrets(text)

    assert "sekrit-value-here" not in redacted


def test_redact_secrets_is_a_no_op_for_plain_text() -> None:
    """Boundary: a message with nothing secret-shaped in it passes through
    unchanged."""
    text = "cycle 시작: cycle_id=abc123 관리 저장소 수=1"

    assert redact_secrets(text) == text


def test_logs_do_not_expose_secrets_or_authorization_headers(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-013-10: once `install_secret_filter` is attached to a logger,
    nothing logged through it - regardless of which code path produced the
    message - can leak the known secret value or an Authorization header,
    across every log level."""
    logger = logging.getLogger("devbot.test-cp-013-10")
    logger.handlers.clear()
    logger.propagate = True
    install_secret_filter(logger, ["super-secret-token"])

    with caplog.at_level(logging.DEBUG, logger="devbot.test-cp-013-10"):
        logger.error(
            "GitHub API 오류: %s",
            "Authorization: Bearer super-secret-token rejected (401)",
        )
        logger.debug("설정 로드: github_token=%s", "super-secret-token")
        logger.info("일반 메시지는 그대로 통과: %s", "hello")

    assert "super-secret-token" not in caplog.text
    assert "hello" in caplog.text  # the filter doesn't redact unrelated text


def test_install_secret_filter_is_idempotent_across_repeated_calls() -> None:
    """Calling `install_secret_filter` again (e.g. across repeated
    `main()` invocations in tests) replaces, rather than stacks, the
    filter."""
    logger = logging.getLogger("devbot.test-idempotent-filter")
    install_secret_filter(logger, ["first-token"])
    install_secret_filter(logger, ["second-token"])

    assert sum(isinstance(f, SecretRedactingFilter) for f in logger.filters) == 1


# --- SafeLogger (CP-013-12) --------------------------------------------------


class _RaisingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        raise RuntimeError("logging backend unavailable")


def test_safe_logger_swallows_handler_exceptions() -> None:
    """CP-013-12 (mechanism-level): every guarded method on `SafeLogger`
    survives a handler that raises on every `emit()`."""
    broken = logging.getLogger("devbot.test-safe-logger")
    broken.handlers.clear()
    broken.addHandler(_RaisingHandler())
    broken.propagate = False
    safe = SafeLogger(broken)

    safe.info("메시지 %s", "one")
    safe.debug("메시지 %s", "two")
    safe.warning("메시지 %s", "three")
    safe.error("메시지 %s", "four")
    # No exception raised above is the assertion itself.


def test_safe_logger_still_delegates_non_guarded_attributes() -> None:
    """`SafeLogger` is a drop-in for a plain `logging.Logger` for anything
    that isn't itself a logging call (`.level`, `.setLevel`, ...)."""
    real_logger = logging.getLogger("devbot.test-safe-logger-delegation")
    safe = SafeLogger(real_logger)

    safe.setLevel(logging.DEBUG)

    assert real_logger.level == logging.DEBUG
    assert safe.name == "devbot.test-safe-logger-delegation"


def test_ensure_safe_logger_does_not_double_wrap() -> None:
    real_logger = logging.getLogger("devbot.test-ensure-safe-logger")
    once = ensure_safe_logger(real_logger)
    twice = ensure_safe_logger(once)

    assert once is twice


# --- pure helpers: cycle_id / elapsed_ms / build_cycle_summary --------------


def test_new_cycle_id_is_short_and_unique() -> None:
    first = new_cycle_id()
    second = new_cycle_id()

    assert first != second
    assert 0 < len(first) <= 16


def test_elapsed_ms_is_non_negative_and_monotonic() -> None:
    start = time.monotonic()
    time.sleep(0.001)

    assert elapsed_ms(start) > 0


def test_build_cycle_summary_counts_candidates_by_job_type() -> None:
    candidates = [
        Job(job_type=JobType.REWORK, task=_task(repository="someone/a")),
        Job(job_type=JobType.REVIEW, task=_task(repository="someone/b")),
        Job(job_type=JobType.IMPLEMENT, task=_task(repository="someone/c")),
        Job(job_type=JobType.IMPLEMENT, task=_task(repository="someone/d")),
    ]
    selected = candidates[:1]
    results = [PollingResult(status=PollingStatus.REWORKED)]

    summary = build_cycle_summary(
        cycle_id="cyc-1",
        start=time.monotonic(),
        candidates=candidates,
        selected=selected,
        available_slots=1,
        results=results,
    )

    assert summary.candidate_counts[JobType.REWORK] == 1
    assert summary.candidate_counts[JobType.REVIEW] == 1
    assert summary.candidate_counts[JobType.IMPLEMENT] == 2
    assert summary.selected_count == 1
    assert summary.available_slots == 1
    assert summary.result_statuses == ("reworked",)
    assert summary.elapsed_ms >= 0


def test_build_cycle_summary_handles_no_candidates_or_results() -> None:
    """Boundary: an empty cycle (no candidates, no results) still builds a
    valid summary instead of raising."""
    summary = build_cycle_summary(
        cycle_id="cyc-empty",
        start=time.monotonic(),
        candidates=[],
        selected=[],
        available_slots=1,
        results=[],
    )

    assert summary.candidate_counts[JobType.REWORK] == 0
    assert summary.result_statuses == ()


# --- Task 020: build_queue_summary ------------------------------------------


def test_build_queue_summary_counts_each_stable_state() -> None:
    """CP-020-2: every stable workflow state gets its own count, and a
    state with multiple tasks accumulates correctly."""
    tasks = [
        _task(number=1, state=TaskState.READY),
        _task(number=2, state=TaskState.REVIEW),
        _task(number=3, state=TaskState.REVIEW),
        _task(number=4, state=TaskState.REWORK),
        _task(number=5, state=TaskState.BLOCKED),
        _task(number=6, state=TaskState.MANUAL_ACTION),
        _task(number=7, state=TaskState.WORKING),
    ]

    summary = build_queue_summary("cyc-1", tasks)

    assert summary.cycle_id == "cyc-1"
    assert summary.ready == 1
    assert summary.review == 2
    assert summary.rework == 1
    assert summary.blocked == 1
    assert summary.manual_action == 1
    assert summary.working == 1


def test_build_queue_summary_returns_all_zero_counts_for_no_tasks() -> None:
    """Boundary: an empty queue still builds a valid summary instead of
    raising, with every bucket at zero."""
    summary = build_queue_summary("cyc-empty", [])

    assert (
        summary.ready,
        summary.review,
        summary.rework,
        summary.blocked,
        summary.manual_action,
        summary.working,
    ) == (0, 0, 0, 0, 0, 0)


def test_build_queue_summary_ignores_terminal_done_state() -> None:
    """Boundary: a `DONE` task (outside the six scheduler-facing stable
    states) contributes to no bucket rather than raising or being silently
    added somewhere unexpected."""
    summary = build_queue_summary("cyc-done", [_task(state=TaskState.DONE)])

    assert (
        summary.ready,
        summary.review,
        summary.rework,
        summary.blocked,
        summary.manual_action,
        summary.working,
    ) == (0, 0, 0, 0, 0, 0)


# --- log_startup / log_job_finished / log_candidate_excluded ----------------


def test_log_startup_never_includes_github_token(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("devbot.test-log-startup")
    logger.propagate = True
    config = _config([_repo("myrepo")])

    with caplog.at_level(logging.INFO, logger="devbot.test-log-startup"):
        log_startup(logger, config)

    assert "super-secret-token" not in caplog.text
    startup_records = [r for r in caplog.records if getattr(r, "event", None) == "startup"]
    assert len(startup_records) == 1
    assert startup_records[0].implementer_agent == "claude"
    assert startup_records[0].reviewer_agent == "codex"
    repo_records = [r for r in caplog.records if getattr(r, "event", None) == "managed_repository"]
    assert repo_records[0].repository == "someone/myrepo"


def test_log_job_finished_records_status_and_elapsed_ms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("devbot.test-log-job-finished")
    logger.propagate = True
    job = Job(job_type=JobType.IMPLEMENT, task=_task(number=42))

    with caplog.at_level(logging.INFO, logger="devbot.test-log-job-finished"):
        log_job_finished(logger, "cyc-1", job, status="agent_completed", start=time.monotonic())

    records = [r for r in caplog.records if getattr(r, "event", None) == "job_finished"]
    assert len(records) == 1
    assert records[0].issue_number == 42
    assert records[0].status == "agent_completed"
    assert records[0].elapsed_ms >= 0


def test_log_job_finished_logs_redacted_failure_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("devbot.test-log-job-finished-failure")
    logger.propagate = True
    install_secret_filter(logger, ["super-secret-token"])
    job = Job(job_type=JobType.IMPLEMENT, task=_task())

    with caplog.at_level(logging.ERROR, logger="devbot.test-log-job-finished-failure"):
        log_job_finished(
            logger,
            "cyc-1",
            job,
            status="agent_failed",
            start=time.monotonic(),
            failure_summary="token=super-secret-token invalid",
        )

    assert "super-secret-token" not in caplog.text


def test_log_candidate_excluded_carries_structured_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    logger = logging.getLogger("devbot.test-log-candidate-excluded")
    logger.propagate = True
    exclusion = CandidateExclusion(
        repository="someone/myrepo",
        issue_number=3,
        reason=ExclusionReason.NO_UNPROCESSED_FEEDBACK,
        job_type=JobType.REWORK,
        detail="no unprocessed @devbot comment",
    )

    with caplog.at_level(logging.DEBUG, logger="devbot.test-log-candidate-excluded"):
        log_candidate_excluded(logger, "cyc-1", exclusion)

    records = [r for r in caplog.records if getattr(r, "event", None) == "candidate_excluded"]
    assert len(records) == 1
    assert records[0].reason == "no_unprocessed_feedback"
    assert records[0].issue_number == 3
    assert records[0].job_type == "rework"


def test_log_candidate_excluded_does_not_raise_when_handler_is_broken() -> None:
    """CP-013-12 (mechanism-level, via the `log_*` `_safe_log` wrapper
    rather than `SafeLogger`): the public logging helpers this module
    exports are themselves exception-safe too."""
    broken = logging.getLogger("devbot.test-log-candidate-excluded-broken")
    broken.handlers.clear()
    broken.addHandler(_RaisingHandler())
    broken.propagate = False
    exclusion = CandidateExclusion(
        repository="someone/myrepo", issue_number=1, reason=ExclusionReason.NOT_READY
    )

    log_candidate_excluded(broken, "cyc-1", exclusion)
    # No exception raised above is the assertion itself.
