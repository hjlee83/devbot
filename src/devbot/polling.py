"""Polling: one safe iteration, and an optional continuous loop.

Connects configuration, the GitHub read client, the global queue, workspace
preparation, and the agent runner into a single testable iteration
(`PollingService.run_once`), plus a continuous loop (`run_forever`) that
repeats it on a configured interval until a shutdown signal arrives.

No GitHub label writes, state transitions, commits, or PRs happen here —
that is Task 006+ scope. This module only decides what *would* run next
and hands it to the (possibly dry-run) `AgentRunner`.
"""

from __future__ import annotations

import logging
import signal
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from enum import Enum

from devbot.agents.base import AgentRunner
from devbot.github_client import GitHubClient, GitHubIssue
from devbot.models import (
    DevBotConfig,
    IssueComment,
    IssueTask,
    Priority,
    RepositoryConfig,
    TaskState,
)
from devbot.queue import has_active_task, select_global_ready_task
from devbot.workspace import (
    WorkspaceValidationError,
    build_agent_prompt,
    ensure_git_workspace_ready,
)

_STATE_LABEL_PREFIX = "devbot:"
_PRIORITY_LABEL_PREFIX = "priority:"

_LOGGER_NAME = "devbot"


class PollingStatus(Enum):
    """Outcome of a single `PollingService.run_once()` iteration."""

    SKIPPED_ACTIVE_TASK = "skipped_active_task"
    NO_READY_TASK = "no_ready_task"
    WORKSPACE_INVALID = "workspace_invalid"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    ITERATION_ERROR = "iteration_error"


@dataclass(frozen=True, slots=True)
class PollingResult:
    """Structured outcome of one iteration. Never raises for expected failures."""

    status: PollingStatus
    task: IssueTask | None = None
    message: str = ""


def _task_state_from_labels(labels: Iterable[str]) -> TaskState | None:
    label_set = set(labels)
    for state in TaskState:
        if f"{_STATE_LABEL_PREFIX}{state.value}" in label_set:
            return state
    return None


def _priority_from_labels(labels: Iterable[str]) -> Priority:
    label_set = set(labels)
    for priority in (Priority.HIGH, Priority.MEDIUM, Priority.LOW):
        if f"{_PRIORITY_LABEL_PREFIX}{priority.value}" in label_set:
            return priority
    return Priority.NONE


def issue_to_task(issue: GitHubIssue) -> IssueTask | None:
    """Convert a `GitHubIssue` into an `IssueTask`, or None if it isn't a
    DevBot-managed Issue (no `devbot:*` label at all)."""
    state = _task_state_from_labels(issue.labels)
    if state is None:
        return None
    return IssueTask(
        repository=issue.repository,
        number=issue.number,
        title=issue.title,
        state=state,
        priority=_priority_from_labels(issue.labels),
        created_at=issue.created_at,
    )


SelectTaskFn = Callable[[Iterable[IssueTask], Iterable[RepositoryConfig]], IssueTask | None]
EnsureWorkspaceFn = Callable[[RepositoryConfig], None]
BuildPromptFn = Callable[[RepositoryConfig, GitHubIssue, Sequence[IssueComment]], str]


@dataclass
class PollingService:
    """Runs one polling iteration against every enabled repository.

    Every external dependency is injected (with real defaults) so tests can
    run this without a real GitHub account, a real Git checkout, or a real
    agent process.
    """

    config: DevBotConfig
    github_client: GitHubClient
    agent_runner: AgentRunner
    select_task: SelectTaskFn = field(default=select_global_ready_task)
    ensure_workspace_ready: EnsureWorkspaceFn = field(default=ensure_git_workspace_ready)
    build_prompt: BuildPromptFn = field(default=build_agent_prompt)
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(_LOGGER_NAME))

    def _collect(
        self, repositories: Sequence[RepositoryConfig]
    ) -> tuple[list[IssueTask], dict[tuple[str, int], GitHubIssue]]:
        tasks: list[IssueTask] = []
        issues_by_key: dict[tuple[str, int], GitHubIssue] = {}
        for repository in repositories:
            for issue in self.github_client.list_issues(repository, state="open"):
                task = issue_to_task(issue)
                if task is None:
                    continue
                tasks.append(task)
                issues_by_key[(issue.repository, issue.number)] = issue
        return tasks, issues_by_key

    def run_once(self) -> PollingResult:
        self.logger.info("폴링을 시작합니다.")
        repositories = self.config.enabled_repositories
        self.logger.info("관리 저장소 수: %d", len(repositories))

        try:
            tasks, issues_by_key = self._collect(repositories)
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error("GitHub 조회 중 오류가 발생했습니다: %s", exc)
            return PollingResult(status=PollingStatus.ITERATION_ERROR, message=str(exc))

        if has_active_task(tasks):
            self.logger.info(
                "이미 진행 중인(working/review) Issue가 있어 새 작업을 선택하지 않습니다."
            )
            return PollingResult(status=PollingStatus.SKIPPED_ACTIVE_TASK)

        ready_count = sum(1 for task in tasks if task.state == TaskState.READY)
        self.logger.info("ready 상태 Issue 수: %d", ready_count)

        selected = self.select_task(tasks, repositories)
        if selected is None:
            self.logger.info("선택 가능한 ready Issue가 없습니다.")
            return PollingResult(status=PollingStatus.NO_READY_TASK)

        self.logger.info(
            "Issue 선택: %s #%d (%s)", selected.repository, selected.number, selected.title
        )

        repository = next(
            repo for repo in repositories if repo.full_name == selected.repository
        )

        try:
            self.ensure_workspace_ready(repository)
        except WorkspaceValidationError as exc:
            self.logger.error(
                "워크스페이스 검증 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.WORKSPACE_INVALID, task=selected, message=str(exc)
            )

        issue = issues_by_key[(selected.repository, selected.number)]
        prompt = self.build_prompt(repository, issue, [])

        self.logger.info(
            "AgentRunner 실행: agent=%s dry_run=%s", self.config.default_agent, self.config.dry_run
        )

        try:
            agent_result = self.agent_runner.run(repository, prompt)
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=str(exc))

        self.logger.info("실행 결과: %s", agent_result.message)
        return PollingResult(
            status=PollingStatus.AGENT_COMPLETED, task=selected, message=agent_result.message
        )


def run_forever(
    polling_service: PollingService,
    poll_interval_seconds: int,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> None:
    """Run `polling_service.run_once()` repeatedly until SIGINT/SIGTERM."""
    log = logger or logging.getLogger(_LOGGER_NAME)
    shutdown_requested = False

    def _handle_shutdown_signal(signum: int, _frame: object) -> None:
        nonlocal shutdown_requested
        log.info("종료 신호(%s)를 받았습니다. 안전하게 종료합니다.", signal.Signals(signum).name)
        shutdown_requested = True

    previous_sigint = signal.signal(signal.SIGINT, _handle_shutdown_signal)
    previous_sigterm = signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    try:
        while not shutdown_requested:
            polling_service.run_once()
            if shutdown_requested:
                break
            log.info("다음 폴링까지 %d초 대기합니다.", poll_interval_seconds)
            sleep_fn(poll_interval_seconds)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
