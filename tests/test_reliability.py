from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.agents.base import (
    AgentRunResult,
    AgentSessionLimitError,
    is_session_limit_output,
)
from devbot.config import ConfigError
from devbot.delivery import DeliveryError
from devbot.github_client import GitHubAPIError, PullRequestComment
from devbot.github_client import GitHubIssue as _GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import FailureCategory, JobType, RecoveryOutcome, RepositoryConfig, TaskState
from devbot.polling import PollingStatus, classify_job_failure
from devbot.reliability import (
    RETRY_POLICY,
    build_diagnostic_report,
    classify_exception,
    decide_retry,
    recovery_outcome_for,
    render_diagnostic_report,
    session_limit_block_reason,
)
from devbot.review import ReviewService
from devbot.rework import ReworkService
from devbot.workspace import WorkspaceValidationError

# ---- CP-019-1: failure classification ----


def test_failure_classification() -> None:
    """CP-019-1: every recognized exception type and Job outcome maps to
    exactly one of the 9 contract-required `FailureCategory` values."""
    assert classify_exception(WorkspaceValidationError("x")) is FailureCategory.WORKSPACE_INVALID
    assert classify_exception(ConfigError("x")) is FailureCategory.CONFIGURATION_ERROR
    assert classify_exception(GitHubAPIError("x")) is FailureCategory.GITHUB_API_ERROR
    assert classify_exception(DeliveryError("x")) is FailureCategory.DELIVERY_FAILED
    assert classify_exception(RuntimeError("something else")) is FailureCategory.UNKNOWN_ERROR

    assert (
        classify_job_failure(JobType.IMPLEMENT, PollingStatus.WORKSPACE_INVALID, "bad workspace")
        is FailureCategory.WORKSPACE_INVALID
    )
    assert (
        classify_job_failure(JobType.IMPLEMENT, PollingStatus.AGENT_FAILED, "agent crashed")
        is FailureCategory.AGENT_EXECUTION_FAILED
    )
    assert (
        classify_job_failure(
            JobType.IMPLEMENT, PollingStatus.AGENT_FAILED, "Usage limit reached, resets at 5pm"
        )
        is FailureCategory.AGENT_SESSION_LIMIT
    )
    assert (
        classify_job_failure(JobType.IMPLEMENT, PollingStatus.ITERATION_ERROR, "GitHub 500")
        is FailureCategory.GITHUB_API_ERROR
    )
    assert (
        classify_job_failure(JobType.IMPLEMENT, PollingStatus.BLOCKED, "Verification failed: ruff")
        is FailureCategory.DELIVERY_FAILED
    )
    assert (
        classify_job_failure(JobType.REWORK, PollingStatus.BLOCKED, "blocked: verification failed")
        is FailureCategory.DELIVERY_FAILED
    )
    assert (
        classify_job_failure(JobType.REVIEW, PollingStatus.BLOCKED, "blocked: invalid review")
        is FailureCategory.REVIEW_FAILED
    )
    assert (
        classify_job_failure(JobType.REVIEW, PollingStatus.BLOCKED, "rate limit exceeded")
        is FailureCategory.AGENT_SESSION_LIMIT
    )
    assert (
        classify_job_failure(JobType.IMPLEMENT, PollingStatus.AGENT_COMPLETED, "ok")
        is FailureCategory.UNKNOWN_ERROR
    )


def test_session_limit_output_detection() -> None:
    assert is_session_limit_output("You have hit your usage limit for this session.")
    assert is_session_limit_output("Rate limit exceeded, try again later.")
    assert not is_session_limit_output("agent crashed with a stack trace")
    assert not is_session_limit_output("")


# ---- CP-019-2: retry policy ----


def test_retry_policy() -> None:
    """CP-019-2: the 5 contract-minimum rules, plus completeness (every
    `FailureCategory` has a defined policy)."""
    assert set(RETRY_POLICY) == set(FailureCategory)

    assert decide_retry(FailureCategory.WORKSPACE_INVALID).should_retry is False
    assert decide_retry(FailureCategory.AGENT_SESSION_LIMIT).should_retry is False
    assert decide_retry(FailureCategory.CONFIGURATION_ERROR).should_retry is False

    first_delivery = decide_retry(FailureCategory.DELIVERY_FAILED, attempt=1)
    assert first_delivery.should_retry is True
    assert first_delivery.backoff_seconds is not None
    exhausted_delivery = decide_retry(FailureCategory.DELIVERY_FAILED, attempt=10)
    assert exhausted_delivery.should_retry is False

    first_github = decide_retry(FailureCategory.GITHUB_API_ERROR, attempt=1)
    second_github = decide_retry(FailureCategory.GITHUB_API_ERROR, attempt=2)
    assert first_github.should_retry is True
    assert second_github.backoff_seconds is not None
    assert first_github.backoff_seconds is not None
    assert second_github.backoff_seconds > first_github.backoff_seconds
    exhausted_github = decide_retry(FailureCategory.GITHUB_API_ERROR, attempt=100)
    assert exhausted_github.should_retry is False


# ---- CP-019-3: recovery policy ----


def test_recovery_policy() -> None:
    """CP-019-3: every claimed workflow's recovery for each failure
    category is one of the 4 explicit `RecoveryOutcome`s, and every
    `FailureCategory` is covered (completeness)."""
    for category in FailureCategory:
        outcome = recovery_outcome_for(category)
        assert outcome in RecoveryOutcome

    assert recovery_outcome_for(FailureCategory.WORKSPACE_INVALID) is RecoveryOutcome.RESTORE
    assert recovery_outcome_for(FailureCategory.AGENT_SESSION_LIMIT) is RecoveryOutcome.BLOCKED
    assert recovery_outcome_for(FailureCategory.DELIVERY_FAILED) is RecoveryOutcome.BLOCKED
    assert recovery_outcome_for(FailureCategory.REVIEW_FAILED) is RecoveryOutcome.BLOCKED


# ---- CP-019-6: diagnostics ----


def test_diagnostics_output() -> None:
    """CP-019-6: a diagnostic report contains repository, Issue/PR,
    current branch, workspace status/changed files, failure category,
    retry decision, and recovery recommendation."""
    report = build_diagnostic_report(
        repository="someone/myrepo",
        category=FailureCategory.DELIVERY_FAILED,
        issue_number=42,
        pull_request_number=7,
        current_branch="devbot/myrepo-42-fix",
        workspace_status="dirty",
        changed_files=("src/foo.py",),
        attempt=2,
    )

    assert report.repository == "someone/myrepo"
    assert report.issue_number == 42
    assert report.pull_request_number == 7
    assert report.current_branch == "devbot/myrepo-42-fix"
    assert report.workspace_status == "dirty"
    assert report.changed_files == ("src/foo.py",)
    assert report.category is FailureCategory.DELIVERY_FAILED
    assert report.retry.should_retry is True
    assert report.recovery_recommendation

    rendered = render_diagnostic_report(report)
    assert "someone/myrepo" in rendered
    assert "#42" in rendered
    assert "#7" in rendered
    assert "devbot/myrepo-42-fix" in rendered
    assert "dirty" in rendered
    assert "src/foo.py" in rendered
    assert "delivery_failed" in rendered
    assert "retry_decision" in rendered
    assert "recovery_recommendation" in rendered


def test_diagnostics_output_without_issue_or_pr_uses_placeholders() -> None:
    report = build_diagnostic_report(
        repository="someone/myrepo", category=FailureCategory.GITHUB_API_ERROR
    )

    rendered = render_diagnostic_report(report)

    assert "issue: #-" in rendered
    assert "pull_request: #-" in rendered


# ---- CP-019-9: agent session-limit handling ----


def _repo() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _issue(*, labels: tuple[str, ...]) -> _GitHubIssue:
    return _GitHubIssue(
        repository="someone/myrepo",
        number=42,
        title="Add feature X",
        body="issue body",
        state="open",
        labels=labels,
        created_at=datetime(2026, 1, 1),
    )


def _comment(*, body: str) -> PullRequestComment:
    return PullRequestComment(
        id=1, author="reviewer", body=body, created_at=datetime(2026, 1, 2), reactions={}
    )


def test_session_limit_block_reason_includes_category_and_hint() -> None:
    reason = session_limit_block_reason("AgentRunner 실행 실패: usage limit reached")

    assert "usage limit reached" in reason
    assert "failure_category=agent_session_limit" in reason
    assert "자동 재시도" in reason


def test_agent_session_limit_handling() -> None:
    """CP-019-9: a session-limit reviewer failure is classified, never
    retried, never left in devbot:working (goes straight to blocked), and
    carries a clear recovery hint on the posted block reason."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True,
        dry_run=False,
        returncode=1,
        message="Usage limit reached, resets at 09:00 UTC",
    )
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    write_client = MagicMock(spec=GitHubWriteClient)
    service = ReviewService(
        state_writer=state_writer,
        write_client=write_client,
        reviewer_runner=reviewer_runner,
        dry_run=False,
    )

    result = service.process(
        _repo(),
        _issue(labels=("devbot:review",)),
        MagicMock(number=1, head_sha="deadbeef", head_ref="feature/x"),
    )

    assert result.issue_state is TaskState.BLOCKED
    assert result.message == "blocked: agent session limit"
    state_writer.block.assert_called_once()
    _, _, reason = state_writer.block.call_args.args
    assert "failure_category=agent_session_limit" in reason
    assert decide_retry(FailureCategory.AGENT_SESSION_LIMIT).should_retry is False


def test_agent_session_limit_handling_rework_path() -> None:
    """CP-019-9, rework path: `apply_changes` raising `AgentSessionLimitError`
    is classified distinctly and still ends in devbot:blocked."""

    def _apply_changes(repository: object, issue: object, comment: object) -> None:
        raise AgentSessionLimitError("session limit reached")

    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    write_client = MagicMock(spec=GitHubWriteClient)
    branch = "devbot/myrepo-42-x"
    service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=_apply_changes,
        current_branch=lambda repository: branch,
    )
    comments = [_comment(body="@devbot please fix the typo")]

    result = service.process(_repo(), _issue(labels=("devbot:rework",)), branch, comments)

    assert result.issue_state is TaskState.BLOCKED
    assert result.message == "blocked: agent session limit"
    state_writer.block.assert_called_once()
    _, _, reason = state_writer.block.call_args.args
    assert "failure_category=agent_session_limit" in reason


@pytest.mark.parametrize(
    "category",
    [
        FailureCategory.AGENT_SESSION_LIMIT,
        FailureCategory.WORKSPACE_INVALID,
        FailureCategory.CONFIGURATION_ERROR,
    ],
)
def test_non_retryable_categories_never_suggest_retry_regardless_of_attempt(
    category: FailureCategory,
) -> None:
    for attempt in (1, 2, 100):
        assert decide_retry(category, attempt=attempt).should_retry is False
