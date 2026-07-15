"""Failure classification, retry policy, recovery policy, and operator
diagnostics (Task 019: Daemon Reliability Baseline).

This module is declarative and I/O-free: it says what *should* happen for
a given `FailureCategory` (retry or not, with what backoff; which of the
four explicit `RecoveryOutcome`s a claimed workflow should reach) and how
to render an operator-friendly diagnostic report. It does not itself
perform any `devbot:*` state transition - that remains `devbot.issue_state`
(already hardened by Task 014) and its callers' job. Callers
(`devbot.polling`, `devbot.review`, `devbot.rework`, `devbot.main`) decide
*when* to classify a failure and log/report it; this module only decides
what the classification implies.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devbot.config import ConfigError
from devbot.delivery import DeliveryError
from devbot.github_client import GitHubClientError
from devbot.models import FailureCategory, RecoveryOutcome
from devbot.workspace import WorkspaceValidationError
from devbot.worktree import WorkspacePreparationError

# ---- Recovery policy (Task 019 CP-019-3) ----
#
# Declares which of the four explicit outcomes
# (`docs/07-decisions.md`'s "Working must be transient") a *claimed*
# (`devbot:working`) workflow's recovery reaches for each failure category.
# `RESTORE`/`MANUAL_ACTION`/`REVIEW` never apply to a genuine failure
# category by definition here - a preflight failure right after claim
# (Task 014 CP-014-5) is the one case this table calls out as `RESTORE`;
# every other category's claimed-workflow recovery is `BLOCKED`, matching
# `devbot.issue_state`'s existing `block()` transition.

RECOVERY_POLICY: dict[FailureCategory, RecoveryOutcome] = {
    FailureCategory.WORKSPACE_INVALID: RecoveryOutcome.RESTORE,
    # Task 023 CP-023-9: a workspace-preparation failure always happens
    # before Agent invocation (before any `claim()`, or right after one
    # with nothing else done yet) - the same "undo the claim" recovery as
    # a preflight `WORKSPACE_INVALID` failure, never `BLOCKED`.
    FailureCategory.WORKSPACE_PREPARATION_FAILED: RecoveryOutcome.RESTORE,
    FailureCategory.STARTUP_VALIDATION_FAILED: RecoveryOutcome.BLOCKED,
    FailureCategory.AGENT_SESSION_LIMIT: RecoveryOutcome.BLOCKED,
    FailureCategory.AGENT_EXECUTION_FAILED: RecoveryOutcome.BLOCKED,
    FailureCategory.DELIVERY_FAILED: RecoveryOutcome.BLOCKED,
    FailureCategory.REVIEW_FAILED: RecoveryOutcome.BLOCKED,
    FailureCategory.GITHUB_API_ERROR: RecoveryOutcome.BLOCKED,
    FailureCategory.CONFIGURATION_ERROR: RecoveryOutcome.BLOCKED,
    FailureCategory.UNKNOWN_ERROR: RecoveryOutcome.BLOCKED,
}


def recovery_outcome_for(category: FailureCategory) -> RecoveryOutcome:
    """The `RecoveryOutcome` a claimed workflow's failure of `category`
    should reach. Every `FailureCategory` is covered (`KeyError` otherwise
    signals a category was added without updating this policy)."""
    return RECOVERY_POLICY[category]


# ---- Retry policy (Task 019 CP-019-2) ----


@dataclass(frozen=True, slots=True)
class RetryRule:
    """One `FailureCategory`'s retry policy."""

    retryable: bool
    max_attempts: int | None
    backoff: str = "none"  # "none" | "fixed" | "exponential"
    initial_backoff_seconds: float = 0.0
    max_backoff_seconds: float = 0.0


# Minimum rules from the Task 019 contract:
# - workspace_invalid: no automatic retry.
# - agent_session_limit: no repeated polling retry; operator action or a
#   known retry-after boundary is required instead (see
#   `_RECOVERY_HINTS` below).
# - delivery_failed: bounded retry only.
# - github_api_error: bounded exponential backoff.
# - configuration_error: fatal startup failure (never retried automatically
#   - `devbot.main` stops before polling; see `devbot.startup`).
RETRY_POLICY: dict[FailureCategory, RetryRule] = {
    FailureCategory.WORKSPACE_INVALID: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.WORKSPACE_PREPARATION_FAILED: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.STARTUP_VALIDATION_FAILED: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.AGENT_SESSION_LIMIT: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.AGENT_EXECUTION_FAILED: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.DELIVERY_FAILED: RetryRule(
        retryable=True,
        max_attempts=3,
        backoff="fixed",
        initial_backoff_seconds=60.0,
        max_backoff_seconds=60.0,
    ),
    FailureCategory.REVIEW_FAILED: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.GITHUB_API_ERROR: RetryRule(
        retryable=True,
        max_attempts=5,
        backoff="exponential",
        initial_backoff_seconds=30.0,
        max_backoff_seconds=900.0,
    ),
    FailureCategory.CONFIGURATION_ERROR: RetryRule(retryable=False, max_attempts=0),
    FailureCategory.UNKNOWN_ERROR: RetryRule(retryable=False, max_attempts=0),
}


@dataclass(frozen=True, slots=True)
class RetryDecision:
    """The retry outcome for one failure occurrence."""

    should_retry: bool
    backoff_seconds: float | None
    reason: str


def decide_retry(category: FailureCategory, attempt: int = 1) -> RetryDecision:
    """Deterministic retry decision for the `attempt`-th occurrence
    (1-based) of a `category` failure. Never mutates any counter itself -
    callers own tracking `attempt` across cycles, if they track it at all."""
    rule = RETRY_POLICY[category]

    if not rule.retryable:
        return RetryDecision(
            should_retry=False,
            backoff_seconds=None,
            reason=f"{category.value}는 자동 재시도를 지원하지 않습니다.",
        )

    if rule.max_attempts is not None and attempt > rule.max_attempts:
        return RetryDecision(
            should_retry=False,
            backoff_seconds=None,
            reason=f"{category.value}의 최대 재시도 횟수({rule.max_attempts})를 초과했습니다.",
        )

    if rule.backoff == "exponential":
        backoff = min(rule.initial_backoff_seconds * (2 ** (attempt - 1)), rule.max_backoff_seconds)
    elif rule.backoff == "fixed":
        backoff = rule.initial_backoff_seconds
    else:
        backoff = 0.0

    return RetryDecision(
        should_retry=True,
        backoff_seconds=backoff,
        reason=f"{category.value} bounded retry (attempt {attempt}/{rule.max_attempts})",
    )


# ---- Exception classification (Task 019 CP-019-1) ----


def classify_exception(exc: BaseException) -> FailureCategory:
    """Map a raised exception to a `FailureCategory` by type. Falls back to
    `UNKNOWN_ERROR` for anything not recognized - never raises itself."""
    if isinstance(exc, WorkspaceValidationError):
        return FailureCategory.WORKSPACE_INVALID
    if isinstance(exc, WorkspacePreparationError):
        return FailureCategory.WORKSPACE_PREPARATION_FAILED
    if isinstance(exc, ConfigError):
        return FailureCategory.CONFIGURATION_ERROR
    if isinstance(exc, GitHubClientError):
        return FailureCategory.GITHUB_API_ERROR
    if isinstance(exc, DeliveryError):
        return FailureCategory.DELIVERY_FAILED
    return FailureCategory.UNKNOWN_ERROR


# ---- Diagnostics (Task 019 CP-019-6) ----

_RECOVERY_HINTS: dict[FailureCategory, str] = {
    FailureCategory.WORKSPACE_INVALID: (
        "워크스페이스 상태(존재 여부/Git 여부/미커밋 변경)를 확인하고 정리하세요. "
        "자동 재시도하지 않습니다."
    ),
    FailureCategory.WORKSPACE_PREPARATION_FAILED: (
        "DevBot가 Agent 실행 전에 준비하는 격리 worktree(원격 동기화/branch 재사용/"
        "worktree 생성) 단계가 실패했습니다. `devbot doctor`의 worktree_health로 "
        "충돌/오래된 worktree를 확인하고 필요하면 `devbot worktree cleanup`으로 "
        "정리한 뒤 재시도하세요. 자동 재시도하지 않습니다."
    ),
    FailureCategory.STARTUP_VALIDATION_FAILED: (
        "시작 검증 실패 항목을 해결한 뒤 데몬을 다시 시작하세요. 폴링은 시작되지 않았습니다."
    ),
    FailureCategory.AGENT_SESSION_LIMIT: (
        "Agent 세션/사용량 제한입니다. 자동 재시도를 하지 않았습니다. "
        "제한이 해제된 뒤 Issue를 devbot:ready(또는 이전 상태)로 되돌리세요."
    ),
    FailureCategory.AGENT_EXECUTION_FAILED: (
        "Agent 실행 실패 요약을 확인하고 원인을 해결한 뒤 Issue를 이전 상태로 되돌리세요."
    ),
    FailureCategory.DELIVERY_FAILED: (
        "검증/커밋/푸시/PR 단계 중 실패 지점을 확인하세요. 제한된 횟수 내에서만 자동 재시도합니다."
    ),
    FailureCategory.REVIEW_FAILED: (
        "리뷰 Agent 실행 또는 Review Summary 형식을 확인한 뒤 Issue를 devbot:review로 되돌리세요."
    ),
    FailureCategory.GITHUB_API_ERROR: (
        "GitHub API 상태/권한/네트워크를 확인하세요. 지수 백오프로 제한된 횟수만 자동 재시도합니다."
    ),
    FailureCategory.CONFIGURATION_ERROR: (
        "환경 변수/설정 파일을 수정한 뒤 데몬을 다시 시작하세요. 폴링은 시작되지 않았습니다."
    ),
    FailureCategory.UNKNOWN_ERROR: (
        "예상하지 못한 오류입니다. 로그 전체를 확인하고 필요하면 사람이 개입하세요."
    ),
}


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    """Operator-friendly diagnostic report for one operational failure
    (Task 019 CP-019-6)."""

    repository: str
    category: FailureCategory
    retry: RetryDecision
    recovery_recommendation: str
    issue_number: int | None = None
    pull_request_number: int | None = None
    current_branch: str | None = None
    workspace_status: str = "unknown"
    changed_files: tuple[str, ...] = field(default_factory=tuple)


def build_diagnostic_report(
    *,
    repository: str,
    category: FailureCategory,
    issue_number: int | None = None,
    pull_request_number: int | None = None,
    current_branch: str | None = None,
    workspace_status: str = "unknown",
    changed_files: tuple[str, ...] = (),
    attempt: int = 1,
) -> DiagnosticReport:
    """Build a `DiagnosticReport` for one failure, deriving its retry
    decision and recovery recommendation from `category`."""
    return DiagnosticReport(
        repository=repository,
        category=category,
        retry=decide_retry(category, attempt),
        recovery_recommendation=_RECOVERY_HINTS[category],
        issue_number=issue_number,
        pull_request_number=pull_request_number,
        current_branch=current_branch,
        workspace_status=workspace_status,
        changed_files=changed_files,
    )


def render_diagnostic_report(report: DiagnosticReport) -> str:
    """Render `report` as operator-facing text (used by log lines and the
    `devbot doctor` report alike)."""
    return "\n".join(
        [
            "[DevBot Diagnostic Report]",
            f"repository: {report.repository}",
            f"issue: #{report.issue_number if report.issue_number is not None else '-'}",
            "pull_request: #"
            f"{report.pull_request_number if report.pull_request_number is not None else '-'}",
            f"current_branch: {report.current_branch or '-'}",
            f"workspace_status: {report.workspace_status}",
            f"changed_files: {', '.join(report.changed_files) or '-'}",
            f"failure_category: {report.category.value}",
            f"retry_decision: should_retry={report.retry.should_retry} "
            f"backoff_seconds={report.retry.backoff_seconds} reason={report.retry.reason}",
            f"recovery_recommendation: {report.recovery_recommendation}",
        ]
    )


def session_limit_block_reason(base_reason: str) -> str:
    """Append the `AGENT_SESSION_LIMIT` recovery hint (CP-019-9's "clear
    recovery hint") to a GitHub-facing `block()` reason string."""
    hint = _RECOVERY_HINTS[FailureCategory.AGENT_SESSION_LIMIT]
    return f"{base_reason}\n\n[failure_category={FailureCategory.AGENT_SESSION_LIMIT.value}] {hint}"
