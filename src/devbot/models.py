"""Shared data structures for configuration, repositories, and the task queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class TaskState(StrEnum):
    """Lifecycle state of a queued Issue, mirroring the `devbot:*` labels."""

    READY = "ready"
    WORKING = "working"
    REVIEW = "review"
    REWORK = "rework"
    MANUAL_ACTION = "manual-action"
    BLOCKED = "blocked"
    DONE = "done"


class Priority(StrEnum):
    """Selection priority, mirroring the `priority:*` labels."""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    NONE = "none"


class JobType(StrEnum):
    """The kind of work a scheduled `Job` represents. Role-neutral by
    design (Task 012) - no vendor/product name appears here."""

    REWORK = "rework"
    REVIEW = "review"
    IMPLEMENT = "implement"


class FailureCategory(StrEnum):
    """Deterministic failure classification (Task 019 CP-019-1) used for
    retry decisions, recovery-policy lookups, and operator diagnostics -
    never for GitHub state-machine control flow itself, which remains
    `devbot.issue_state`'s job."""

    WORKSPACE_INVALID = "workspace_invalid"
    WORKSPACE_PREPARATION_FAILED = "workspace_preparation_failed"
    STARTUP_VALIDATION_FAILED = "startup_validation_failed"
    AGENT_SESSION_LIMIT = "agent_session_limit"
    AGENT_EXECUTION_FAILED = "agent_execution_failed"
    DELIVERY_FAILED = "delivery_failed"
    REVIEW_FAILED = "review_failed"
    GITHUB_API_ERROR = "github_api_error"
    CONFIGURATION_ERROR = "configuration_error"
    UNKNOWN_ERROR = "unknown_error"


class RecoveryOutcome(StrEnum):
    """The explicit outcome a claimed (`devbot:working`) workflow must reach
    (Task 019 Recovery Policy) - the same four outcomes
    `docs/07-decisions.md`'s "Working must be transient" decision and Task
    014 (CP-014-5/6/7) already enforce structurally."""

    RESTORE = "restore"
    BLOCKED = "blocked"
    MANUAL_ACTION = "manual-action"
    REVIEW = "review"


class AgentOutcome(StrEnum):
    """Normalized classification of a single Agent invocation's result
    (Task 021 CP-021-1) - a narrower, earlier question than
    `FailureCategory`: did *this run* actually complete the requested
    implementation, and if not, why not. `devbot.agent_outcome` classifies
    an `AgentRunResult` (`devbot.agents.base`) into exactly one of these
    and `devbot.polling` gates delivery/review on the result, instead of
    inferring completion from the absence of a problem (process exit
    success, no raised exception, an existing PR, or a clean workspace -
    Task 021 CP-021-11)."""

    IMPLEMENTATION_COMPLETED = "implementation_completed"
    IMPLEMENTATION_SKIPPED = "implementation_skipped"
    NO_REPOSITORY_CHANGES = "no_repository_changes"
    APPROVAL_REQUIRED = "approval_required"
    NETWORK_BLOCKED = "network_blocked"
    SESSION_LIMIT = "session_limit"
    REPOSITORY_LOCKED = "repository_locked"
    AGENT_FAILED = "agent_failed"
    UNKNOWN = "unknown"


class ExclusionReason(StrEnum):
    """Structured reason a candidate `Job` did not run this cycle (Task
    013's "표준 제외 사유"). Diagnostic logging uses these codes instead of
    free-form strings so operators and tests can match on a fixed
    vocabulary rather than parsing prose."""

    REPOSITORY_BUSY = "repository_busy"
    ISSUE_BUSY = "issue_busy"
    CONCURRENCY_LIMIT = "concurrency_limit"
    MISSING_LINKED_PR = "missing_linked_pr"
    MISSING_PR_HEAD = "missing_pr_head"
    ALREADY_REVIEWED_HEAD = "already_reviewed_head"
    NO_UNPROCESSED_FEEDBACK = "no_unprocessed_feedback"
    NOT_READY = "not_ready"
    LOWER_PRIORITY = "lower_priority"
    DRY_RUN = "dry_run"


@dataclass(frozen=True, slots=True)
class RepositoryConfig:
    """A single managed repository."""

    owner: str
    repo: str
    enabled: bool
    local_path: Path
    default_branch: str = "main"

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"


@dataclass(frozen=True, slots=True)
class DevBotConfig:
    """Fully resolved DevBot runtime configuration."""

    workspace_root: Path
    poll_interval_seconds: int
    lock_file: Path
    default_agent: str
    implementer_agent: str
    reviewer_agent: str
    max_concurrent_jobs: int
    dry_run: bool
    github_token: str
    repositories: tuple[RepositoryConfig, ...]
    log_level: str = "INFO"

    @property
    def enabled_repositories(self) -> tuple[RepositoryConfig, ...]:
        return tuple(repo for repo in self.repositories if repo.enabled)


@dataclass(frozen=True, slots=True)
class IssueTask:
    """A single unit of work sourced from a GitHub Issue."""

    repository: str
    number: int
    title: str
    state: TaskState
    priority: Priority
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssueComment:
    """A single comment on a GitHub Issue, already filtered to "unprocessed"
    by the caller (this module does not decide what counts as processed)."""

    author: str
    body: str


@dataclass(frozen=True, slots=True)
class Job:
    """One scheduling candidate: `job_type` work for `task`'s Issue.

    Carries only what `devbot.scheduler.select_jobs` needs to order and
    dedupe candidates (repository, priority, age, Issue number, via
    `task`) - not the full GitHub state (linked PR, comments, ...) a job's
    execution needs, which the caller re-fetches once a job is actually
    selected to run.
    """

    job_type: JobType
    task: IssueTask


@dataclass(frozen=True, slots=True)
class CandidateExclusion:
    """One candidate that did not become a selected `Job` this cycle,
    with a structured `ExclusionReason` (Task 013). `job_type` is `None`
    when a task was excluded before any job type could even be
    determined for it (e.g. its `devbot:*` state isn't schedulable)."""

    repository: str
    issue_number: int
    reason: ExclusionReason
    job_type: JobType | None = None
    detail: str = ""
