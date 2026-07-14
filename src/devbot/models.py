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
