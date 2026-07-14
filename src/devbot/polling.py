"""Polling: one safe scheduling cycle, and an optional continuous loop.

Connects configuration, the GitHub read client, the job scheduler
(`devbot.scheduler`), workspace preparation, the implementer/reviewer
Agent runners, Issue state writes, and delivery into a single testable
cycle (`PollingService.run_cycle`), plus a continuous loop (`run_forever`)
that repeats it on a configured interval until a shutdown signal arrives.

Each enabled repository contributes at most one job candidate per cycle -
a pending `devbot:rework` fix, an unreviewed `devbot:review` PR head, or a
`devbot:ready` implementation, in that priority order (see
`devbot.scheduler.select_jobs`) - and a repository with any `devbot:working`
Issue contributes none at all (that Issue's own job, from a previous or
still-running cycle, has not resolved yet). `DevBotConfig.max_concurrent_jobs`
(default `1`, the same serial behavior as before Task 012) bounds how many
of those per-repository candidates actually run in one cycle; two different
repositories' jobs may run in parallel, but never two jobs for the same
repository or the same Issue.

Task 014 hardens the state machine itself (`devbot.issue_state`): a Job
claims its Issue to `working` before any preflight check, undoes that claim
back to its pre-claim stable state (`ready`/`review`/`rework`) if the
preflight fails, moves to `blocked` on any execution/delivery failure, and
never leaves an Issue stuck in `working` even on a completely unexpected
exception. `devbot:review` and `devbot:rework` are now distinct states -
an automatic review's `REQUEST CHANGES` moves an Issue to `devbot:rework`
(only `devbot:rework` Issues with unprocessed `@devbot` feedback become
REWORK candidates); `MERGE READY` leaves it `devbot:review`.

`run_once()` is a thin backward-compatible wrapper: with the default
`max_concurrent_jobs=1`, `run_cycle()` never selects more than one job, so
`run_once()` (`run_cycle()`'s first result, or a summary result when
nothing was selected) is exactly what it always was.

`state_writer` and `delivery` are optional. When both are supplied, a
successful implementer run is followed by the full `working -> verify ->
commit -> push -> PR -> review` flow (or `-> blocked` on failure); when
either is omitted, ready-Issue jobs fall back to their original Task 005
behavior (select and run the agent only, no GitHub writes) - this keeps
every existing caller that doesn't need the write path working unchanged.
`rework_service` and `review_service` independently gate whether rework and
automatic-review jobs are ever generated at all; a deployment that hasn't
wired one simply never sees that job type as a candidate.
"""

from __future__ import annotations

import logging
import re
import signal
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum

from devbot import observability
from devbot.agents.base import AgentRunner
from devbot.delivery import DeliveryService
from devbot.github_client import GitHubClient, GitHubIssue, PullRequest, PullRequestComment
from devbot.issue_state import ClaimConflictError, IssueStateWriter
from devbot.models import (
    CandidateExclusion,
    DevBotConfig,
    ExclusionReason,
    IssueComment,
    IssueTask,
    Job,
    JobType,
    Priority,
    RepositoryConfig,
    TaskState,
)
from devbot.review import ReviewService, has_review_marker_for_head
from devbot.rework import ReworkService, find_unprocessed_devbot_comments
from devbot.scheduler import select_jobs_with_exclusions
from devbot.workspace import (
    WorkspaceValidationError,
    build_agent_prompt,
    ensure_git_workspace_ready,
    generate_branch_name,
)

_STATE_LABEL_PREFIX = "devbot:"
_PRIORITY_LABEL_PREFIX = "priority:"

_LOGGER_NAME = "devbot"


class PollingStatus(Enum):
    """Outcome of a single job within a `PollingService.run_cycle()` cycle."""

    SKIPPED_ACTIVE_TASK = "skipped_active_task"
    NO_READY_TASK = "no_ready_task"
    NO_MANAGED_REPOSITORIES = "no_managed_repositories"
    WORKSPACE_INVALID = "workspace_invalid"
    AGENT_COMPLETED = "agent_completed"
    AGENT_FAILED = "agent_failed"
    DELIVERED = "delivered"
    REWORKED = "reworked"
    REVIEWED = "reviewed"
    BLOCKED = "blocked"
    ITERATION_ERROR = "iteration_error"


@dataclass(frozen=True, slots=True)
class PollingResult:
    """Structured outcome of one job. Never raises for expected failures."""

    status: PollingStatus
    task: IssueTask | None = None
    message: str = ""


# Statuses a Job can end in that represent a genuine failure worth a
# structured "Job 실패 요약" log line (Task 013 #5) - the same set
# `devbot.main` treats as a nonzero exit code for `--once`.
_JOB_FAILURE_STATUSES = {
    PollingStatus.WORKSPACE_INVALID,
    PollingStatus.AGENT_FAILED,
    PollingStatus.BLOCKED,
    PollingStatus.ITERATION_ERROR,
}


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


_CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s*#(\d+)\b", re.IGNORECASE
)


def find_linked_pull_request(
    issue: GitHubIssue, pull_requests: Iterable[PullRequest]
) -> PullRequest | None:
    """Return the open PR that closes `issue`, identified the same way
    GitHub's own "Development" linking does: a closing keyword (`Closes
    #N`, `Fixes #N`, `Resolves #N`, ...) referencing the Issue's number in
    the PR body. `delivery.build_pr_body()` always writes `Closes #N`, so
    every PR DevBot itself opens is found this way; this also finds a
    manually-opened PR that uses the same convention."""
    for pull_request in pull_requests:
        referenced_numbers = {
            int(match) for match in _CLOSING_KEYWORD_RE.findall(pull_request.body)
        }
        if issue.number in referenced_numbers:
            return pull_request
    return None


EnsureWorkspaceFn = Callable[[RepositoryConfig], None]
BuildPromptFn = Callable[[RepositoryConfig, GitHubIssue, Sequence[IssueComment]], str]

IssuesByKey = dict[tuple[str, int], GitHubIssue]


@dataclass
class PollingService:
    """Runs one scheduling cycle against every enabled repository.

    Every external dependency is injected (with real defaults) so tests can
    run this without a real GitHub account, a real Git checkout, or a real
    agent process. `state_writer` and `delivery` are the exception: they
    have no side-effect-free default (both need a real `GitHubWriteClient`),
    so they default to `None`, which disables the write path entirely (see
    module docstring). `devbot.main` always supplies both in production.
    """

    config: DevBotConfig
    github_client: GitHubClient
    implementer_runner: AgentRunner
    reviewer_runner: AgentRunner | None = None
    ensure_workspace_ready: EnsureWorkspaceFn = field(default=ensure_git_workspace_ready)
    build_prompt: BuildPromptFn = field(default=build_agent_prompt)
    state_writer: IssueStateWriter | None = None
    delivery: DeliveryService | None = None
    rework_service: ReworkService | None = None
    review_service: ReviewService | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(_LOGGER_NAME))

    def __post_init__(self) -> None:
        # CP-013-12: every `self.logger.*` call in this class - not only
        # the `observability.log_*` helpers - must survive a broken
        # handler/formatter without changing a Job's outcome.
        self.logger = observability.ensure_safe_logger(self.logger)

    def _block(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        reason: str,
        selected: IssueTask,
        *,
        job_type: JobType = JobType.IMPLEMENT,
    ) -> PollingResult | None:
        """Attempt to move `issue` to `blocked`. Returns a `PollingResult`
        if the state write itself fails (so the caller can return that
        instead of letting the exception propagate); returns None on
        success so the caller keeps returning its own failure status."""
        try:
            self.state_writer.block(repository, issue, reason, job_type=job_type)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Blocked 처리 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )
        return None

    def _restore(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        to_state: TaskState,
        reason: str,
        selected: IssueTask,
        *,
        job_type: JobType = JobType.IMPLEMENT,
    ) -> PollingResult | None:
        """Attempt to move `issue` back to `to_state` (CP-014-5: undo a
        claim after a preflight failure, before any Agent ran). Returns a
        `PollingResult` if the state write itself fails; returns None on
        success so the caller keeps returning its own failure status."""
        try:
            self.state_writer.restore(  # type: ignore[union-attr]
                repository, issue, to_state, job_type=job_type, reason=reason
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "이전 상태 복구 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )
        return None

    def _collect(
        self, repositories: Sequence[RepositoryConfig], cycle_id: str
    ) -> tuple[list[IssueTask], IssuesByKey]:
        tasks: list[IssueTask] = []
        issues_by_key: IssuesByKey = {}
        for repository in repositories:
            issues = self.github_client.list_issues(repository, state="open")
            observability.log_repository_search(
                self.logger,
                cycle_id,
                repository.full_name,
                state="open",
                label_filter="devbot:*(client-side)",
                result_count=len(issues),
            )
            for issue in issues:
                task = issue_to_task(issue)
                if task is None:
                    continue
                tasks.append(task)
                issues_by_key[(issue.repository, issue.number)] = issue
        return tasks, issues_by_key

    def _fetch_linked_pull_request_and_comments(
        self, repository: RepositoryConfig, selected: IssueTask, issue: GitHubIssue, cycle_id: str
    ) -> tuple[PullRequest | None, list[PullRequestComment], PollingResult | None]:
        """Resolve `issue`'s linked PR and that PR's own conversation
        comments (not `issue`'s comments - see the module docstring: both
        rework detection and review-marker detection now key off the PR,
        which is where review feedback actually gets posted)."""
        try:
            pull_requests = self.github_client.list_pull_requests(repository)
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "PR 조회 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return (
                None,
                [],
                PollingResult(
                    status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                ),
            )

        linked_pull_request = find_linked_pull_request(issue, pull_requests)
        if linked_pull_request is None:
            self.logger.error(
                "review Issue에 연결된 PR을 찾지 못했습니다 (%s #%d)",
                selected.repository,
                selected.number,
            )
            observability.log_candidate_excluded(
                self.logger,
                cycle_id,
                CandidateExclusion(
                    repository=selected.repository,
                    issue_number=selected.number,
                    reason=ExclusionReason.MISSING_LINKED_PR,
                    detail=f"Issue #{issue.number}에 연결된 열린 PR을 찾지 못함",
                ),
            )
            return (
                None,
                [],
                PollingResult(
                    status=PollingStatus.ITERATION_ERROR,
                    task=selected,
                    message=f"No linked pull request found for Issue #{issue.number}",
                ),
            )

        try:
            pr_comments = self.github_client.list_issue_comments(
                repository, linked_pull_request.number
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "PR 댓글 조회 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return (
                None,
                [],
                PollingResult(
                    status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                ),
            )

        return linked_pull_request, pr_comments, None

    def _rework_state_candidate(
        self,
        repository: RepositoryConfig,
        rework_task: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> tuple[Job | None, PollingResult | None]:
        """A `devbot:rework` Issue is a REWORK candidate only when its
        linked PR has an unprocessed `@devbot` comment (CP-014-3); other-
        wise it is excluded (DEBUG) and stays `devbot:rework`, waiting for
        feedback."""
        if self.rework_service is None:
            return None, None

        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, rework_task, issue, cycle_id
        )
        if error is not None:
            return None, error
        assert linked_pull_request is not None  # `error` is None only when this is set

        if find_unprocessed_devbot_comments(pr_comments):
            job = Job(job_type=JobType.REWORK, task=rework_task)
            observability.log_candidate_found(
                self.logger, cycle_id, job, pr_number=linked_pull_request.number
            )
            return job, None

        observability.log_candidate_excluded(
            self.logger,
            cycle_id,
            CandidateExclusion(
                repository=rework_task.repository,
                issue_number=rework_task.number,
                reason=ExclusionReason.NO_UNPROCESSED_FEEDBACK,
                job_type=JobType.REWORK,
                detail=f"PR #{linked_pull_request.number}에 처리되지 않은 @devbot 댓글 없음",
            ),
        )
        return None, None

    def _review_state_candidate(
        self,
        repository: RepositoryConfig,
        review_task: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> tuple[Job | None, PollingResult | None]:
        """A `devbot:review` Issue is a REVIEW candidate whenever its
        linked PR's current head commit has no auto-review marker yet
        (Task 014: rework detection no longer runs against `devbot:review`
        Issues - `@devbot` feedback only exists on `devbot:rework` Issues
        now, CP-014-2/CP-014-3)."""
        if self.review_service is None:
            return None, None

        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, review_task, issue, cycle_id
        )
        if error is not None:
            return None, error
        assert linked_pull_request is not None  # `error` is None only when this is set

        if not has_review_marker_for_head(pr_comments, linked_pull_request.head_sha):
            job = Job(job_type=JobType.REVIEW, task=review_task)
            observability.log_candidate_found(
                self.logger, cycle_id, job, pr_number=linked_pull_request.number
            )
            return job, None

        observability.log_candidate_excluded(
            self.logger,
            cycle_id,
            CandidateExclusion(
                repository=review_task.repository,
                issue_number=review_task.number,
                reason=ExclusionReason.ALREADY_REVIEWED_HEAD,
                job_type=JobType.REVIEW,
                detail=(
                    f"PR #{linked_pull_request.number} head="
                    f"{linked_pull_request.head_sha}는 이미 자동 리뷰됨"
                ),
            ),
        )
        return None, None

    def _collect_job_candidates(
        self,
        repositories: Sequence[RepositoryConfig],
        tasks: Sequence[IssueTask],
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> tuple[list[Job], list[PollingResult]]:
        tasks_by_repo: dict[str, list[IssueTask]] = {}
        for task in tasks:
            tasks_by_repo.setdefault(task.repository, []).append(task)

        candidates: list[Job] = []
        hard_errors: list[PollingResult] = []

        for repository in repositories:
            repo_tasks = tasks_by_repo.get(repository.full_name, [])
            if any(task.state == TaskState.WORKING for task in repo_tasks):
                # Active (or crashed-mid-run) work already claims this
                # repository's single local workspace this cycle.
                for task in repo_tasks:
                    if task.state is TaskState.WORKING:
                        continue
                    observability.log_candidate_excluded(
                        self.logger,
                        cycle_id,
                        CandidateExclusion(
                            repository=task.repository,
                            issue_number=task.number,
                            reason=ExclusionReason.REPOSITORY_BUSY,
                            detail="저장소에 이미 devbot:working Issue가 있어 이번 cycle은 건너뜀",
                        ),
                    )
                continue

            for task in repo_tasks:
                if task.state not in (TaskState.REVIEW, TaskState.REWORK, TaskState.READY):
                    observability.log_candidate_excluded(
                        self.logger,
                        cycle_id,
                        CandidateExclusion(
                            repository=task.repository,
                            issue_number=task.number,
                            reason=ExclusionReason.NOT_READY,
                            detail=f"Issue 상태가 devbot:{task.state.value}",
                        ),
                    )

            rework_tasks = [task for task in repo_tasks if task.state == TaskState.REWORK]
            for rework_task in rework_tasks:
                issue = issues_by_key[(rework_task.repository, rework_task.number)]
                job, error = self._rework_state_candidate(repository, rework_task, issue, cycle_id)
                if error is not None:
                    hard_errors.append(error)
                elif job is not None:
                    candidates.append(job)

            review_tasks = [task for task in repo_tasks if task.state == TaskState.REVIEW]
            for review_task in review_tasks:
                issue = issues_by_key[(review_task.repository, review_task.number)]
                job, error = self._review_state_candidate(repository, review_task, issue, cycle_id)
                if error is not None:
                    hard_errors.append(error)
                elif job is not None:
                    candidates.append(job)

            if rework_tasks or review_tasks:
                # An unmerged PR already occupies this repository's
                # workspace; a fresh `ready` Issue waits for it.
                for ready_task in (task for task in repo_tasks if task.state == TaskState.READY):
                    observability.log_candidate_excluded(
                        self.logger,
                        cycle_id,
                        CandidateExclusion(
                            repository=ready_task.repository,
                            issue_number=ready_task.number,
                            reason=ExclusionReason.ISSUE_BUSY,
                            job_type=JobType.IMPLEMENT,
                            detail="저장소에 devbot:rework 또는 devbot:review Issue가 워크스페이스를 점유 중",
                        ),
                    )
                continue

            for ready_task in (task for task in repo_tasks if task.state == TaskState.READY):
                job = Job(job_type=JobType.IMPLEMENT, task=ready_task)
                candidates.append(job)
                observability.log_candidate_found(self.logger, cycle_id, job)

        return candidates, hard_errors

    _ROLE_BY_JOB_TYPE = {
        JobType.IMPLEMENT: "implementer",
        JobType.REWORK: "implementer",
        JobType.REVIEW: "reviewer",
    }

    def _execute_job(
        self,
        job: Job,
        repositories: Sequence[RepositoryConfig],
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> PollingResult:
        repository = next(repo for repo in repositories if repo.full_name == job.task.repository)
        role = self._ROLE_BY_JOB_TYPE[job.job_type]
        observability.log_job_started(self.logger, cycle_id, job, role=role)
        start = time.monotonic()

        if job.job_type is JobType.IMPLEMENT:
            result = self._run_implement_job(repository, job.task, issues_by_key, cycle_id)
        elif job.job_type is JobType.REWORK:
            result = self._run_rework_job(repository, job.task, issues_by_key, cycle_id)
        else:
            result = self._run_review_job(repository, job.task, issues_by_key, cycle_id)

        failure_summary = result.message if result.status in _JOB_FAILURE_STATUSES else ""
        observability.log_job_finished(
            self.logger,
            cycle_id,
            job,
            status=result.status.value,
            start=start,
            failure_summary=failure_summary,
        )
        return result

    def _execute_jobs(
        self,
        jobs: Sequence[Job],
        repositories: Sequence[RepositoryConfig],
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> list[PollingResult]:
        if not jobs:
            return []

        if len(jobs) == 1:
            return [self._execute_job(jobs[0], repositories, issues_by_key, cycle_id)]

        results: list[PollingResult] = [
            PollingResult(status=PollingStatus.ITERATION_ERROR, task=job.task) for job in jobs
        ]
        with ThreadPoolExecutor(max_workers=len(jobs)) as executor:
            future_to_index = {
                executor.submit(
                    self._execute_job, job, repositories, issues_by_key, cycle_id
                ): index
                for index, job in enumerate(jobs)
            }
            for future in as_completed(future_to_index):
                index = future_to_index[future]
                try:
                    results[index] = future.result()
                except Exception as exc:  # noqa: BLE001 - must not crash other jobs/the next cycle
                    self.logger.error(
                        "Job 실행 중 예외 (%s #%d): %s",
                        jobs[index].task.repository,
                        jobs[index].task.number,
                        exc,
                    )
                    results[index] = PollingResult(
                        status=PollingStatus.ITERATION_ERROR,
                        task=jobs[index].task,
                        message=str(exc),
                    )
        return results

    def run_cycle(self) -> list[PollingResult]:
        """Run one scheduling cycle: collect every repository's job
        candidate, select up to `config.max_concurrent_jobs` of them
        (never two for the same repository), and run the selected ones -
        serially if only one was selected, otherwise in parallel. Always
        returns at least one `PollingResult`.

        Every cycle gets a fresh `cycle_id` that correlates every log line
        it produces (Task 013 동작 규칙 #5). Zero enabled/managed
        repositories is diagnosed as `NO_MANAGED_REPOSITORIES` - distinct
        from `NO_READY_TASK` - and the cycle is skipped before any GitHub
        call is made (Task 013 동작 규칙 #4)."""
        repositories = self.config.enabled_repositories
        if not repositories:
            observability.log_no_managed_repositories(self.logger)
            return [PollingResult(status=PollingStatus.NO_MANAGED_REPOSITORIES)]

        cycle_id = observability.new_cycle_id()
        cycle_start = time.monotonic()
        observability.log_cycle_start(self.logger, cycle_id, len(repositories))
        self.logger.info("폴링을 시작합니다.")
        self.logger.info("관리 저장소 수: %d", len(repositories))
        self.logger.info(
            "Agent 구성: implementer=%s reviewer=%s dry_run=%s",
            self.config.implementer_agent,
            self.config.reviewer_agent,
            self.config.dry_run,
        )

        try:
            tasks, issues_by_key = self._collect(repositories, cycle_id)
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error("GitHub 조회 중 오류가 발생했습니다: %s", exc)
            results = [PollingResult(status=PollingStatus.ITERATION_ERROR, message=str(exc))]
            observability.log_cycle_end(
                self.logger,
                observability.build_cycle_summary(
                    cycle_id=cycle_id,
                    start=cycle_start,
                    candidates=[],
                    selected=[],
                    available_slots=self.config.max_concurrent_jobs,
                    results=results,
                ),
            )
            return results

        ready_count = sum(1 for task in tasks if task.state == TaskState.READY)
        self.logger.info("ready 상태 Issue 수: %d", ready_count)

        candidates, hard_errors = self._collect_job_candidates(
            repositories, tasks, issues_by_key, cycle_id
        )
        selection = select_jobs_with_exclusions(candidates, self.config.max_concurrent_jobs)
        observability.log_candidates_excluded(self.logger, cycle_id, selection.exclusions)
        observability.log_jobs_selected(
            self.logger,
            cycle_id,
            selection.selected,
            available_slots=self.config.max_concurrent_jobs,
        )
        job_results = self._execute_jobs(selection.selected, repositories, issues_by_key, cycle_id)

        results = [*hard_errors, *job_results]
        if not results:
            if not tasks:
                self.logger.info("선택 가능한 ready Issue가 없습니다.")
                results = [PollingResult(status=PollingStatus.NO_READY_TASK)]
            elif any(
                task.state in (TaskState.WORKING, TaskState.REVIEW, TaskState.REWORK)
                for task in tasks
            ):
                self.logger.info(
                    "이미 진행 중인(working/review/rework) Issue가 있어 새 작업을 선택하지 않습니다."
                )
                results = [PollingResult(status=PollingStatus.SKIPPED_ACTIVE_TASK)]
            else:
                self.logger.info("선택 가능한 ready Issue가 없습니다.")
                results = [PollingResult(status=PollingStatus.NO_READY_TASK)]

        observability.log_cycle_end(
            self.logger,
            observability.build_cycle_summary(
                cycle_id=cycle_id,
                start=cycle_start,
                candidates=candidates,
                selected=selection.selected,
                available_slots=self.config.max_concurrent_jobs,
                results=results,
            ),
        )
        return results

    def run_once(self) -> PollingResult:
        """Backward-compatible single-result wrapper around `run_cycle()`.

        With the default `max_concurrent_jobs=1`, `run_cycle()` never
        selects more than one job, so this is exactly the same outcome
        `run_once()` always returned."""
        return self.run_cycle()[0]

    def _run_implement_job(
        self,
        repository: RepositoryConfig,
        selected: IssueTask,
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> PollingResult:
        self.logger.info(
            "Issue 선택: %s #%d (%s)", selected.repository, selected.number, selected.title
        )

        issue = issues_by_key[(selected.repository, selected.number)]
        full_flow = self.state_writer is not None and self.delivery is not None

        if not full_flow:
            return self._run_unclaimed_implement_job(repository, selected, issue, cycle_id)

        # CP-014-5/CP-014-8: claim *before* the preflight workspace check,
        # so ownership is visible (and races are rejected) as early as
        # possible - a preflight failure right after this undoes the claim
        # (`_restore`) instead of ever running the Agent.
        try:
            issue = self.state_writer.claim(repository, issue, job_type=JobType.IMPLEMENT)
        except ClaimConflictError as exc:
            self.logger.warning(
                "경쟁 claim으로 건너뜀 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.SKIPPED_ACTIVE_TASK, task=selected, message=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Issue claim 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )

        try:
            return self._run_claimed_implement_job(repository, selected, issue, cycle_id)
        except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working` behind
            self.logger.error(
                "예상하지 못한 예외로 Job 중단 (%s #%d): %s", selected.repository, selected.number, exc
            )
            block_failure = self._block(
                repository,
                issue,
                f"예상하지 못한 예외로 Job 중단: {exc!r}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )

    def _run_unclaimed_implement_job(
        self,
        repository: RepositoryConfig,
        selected: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> PollingResult:
        """Task 005 fallback behavior: no `state_writer`/`delivery`
        configured, so this only validates the workspace and runs the
        Agent - no claim, no GitHub write, nothing that could ever leave an
        Issue stuck in `working`."""
        workspace_start = time.monotonic()
        try:
            self.ensure_workspace_ready(repository)
        except WorkspaceValidationError as exc:
            self.logger.error(
                "워크스페이스 검증 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.WORKSPACE_INVALID, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="workspace_validate",
                start=workspace_start,
            )

        prompt = self.build_prompt(repository, issue, [])
        self.logger.info(
            "AgentRunner 실행: implementer=%s dry_run=%s",
            self.config.implementer_agent,
            self.config.dry_run,
        )

        agent_start = time.monotonic()
        try:
            agent_result = self.implementer_runner.run(repository, prompt)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=str(exc))
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="agent_execution",
                start=agent_start,
            )

        if agent_result.failed:
            message = (
                agent_result.message or f"AgentRunner exited with code {agent_result.returncode}"
            )
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): 종료 코드 %s",
                selected.repository,
                selected.number,
                agent_result.returncode,
            )
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=message)

        self.logger.info("실행 결과: %s", agent_result.message)
        return PollingResult(
            status=PollingStatus.AGENT_COMPLETED, task=selected, message=agent_result.message
        )

    def _run_claimed_implement_job(
        self,
        repository: RepositoryConfig,
        selected: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> PollingResult:
        """`issue` is already `working` (claimed by `_run_implement_job`).
        Any failure from here on either restores the pre-claim state
        (preflight, CP-014-5) or blocks with a reason (execution/delivery,
        CP-014-6); the caller's own `except Exception` is the last-resort
        safety net for anything unexpected (CP-014-7)."""
        workspace_start = time.monotonic()
        try:
            self.ensure_workspace_ready(repository)
        except WorkspaceValidationError as exc:
            self.logger.error(
                "워크스페이스 검증 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            restore_failure = self._restore(
                repository,
                issue,
                selected.state,
                f"claim 이후 워크스페이스 검증 실패: {exc}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            if restore_failure is not None:
                return restore_failure
            return PollingResult(
                status=PollingStatus.WORKSPACE_INVALID, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="workspace_validate",
                start=workspace_start,
            )

        prompt = self.build_prompt(repository, issue, [])

        self.logger.info(
            "AgentRunner 실행: implementer=%s dry_run=%s",
            self.config.implementer_agent,
            self.config.dry_run,
        )

        agent_start = time.monotonic()
        try:
            agent_result = self.implementer_runner.run(repository, prompt)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            block_failure = self._block(
                repository,
                issue,
                f"AgentRunner 실행 실패: {exc}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=str(exc))
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="agent_execution",
                start=agent_start,
            )

        if agent_result.failed:
            message = (
                agent_result.message or f"AgentRunner exited with code {agent_result.returncode}"
            )
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): 종료 코드 %s",
                selected.repository,
                selected.number,
                agent_result.returncode,
            )
            block_failure = self._block(
                repository,
                issue,
                f"AgentRunner 실행 실패: {message}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=message)

        self.logger.info("실행 결과: %s", agent_result.message)

        branch = generate_branch_name(repository, issue.number, issue.title)
        self.logger.info("Delivery 시작: branch=%s", branch)

        delivery_start = time.monotonic()
        try:
            delivery_result = self.delivery.deliver(repository, issue, branch, [])
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Delivery 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="delivery",
                start=delivery_start,
            )

        if not delivery_result.verification.passed:
            self.logger.error(
                "검증 실패로 blocked 처리 (%s #%d): %s",
                selected.repository,
                selected.number,
                delivery_result.message,
            )
            block_failure = self._block(
                repository,
                issue,
                f"검증 실패: {delivery_result.message}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=delivery_result.message
            )

        if delivery_result.dry_run:
            self.logger.info("Delivery 결과: %s", delivery_result.message)
            return PollingResult(
                status=PollingStatus.AGENT_COMPLETED, task=selected, message=delivery_result.message
            )

        try:
            self.state_writer.mark_for_review(
                repository, issue, job_type=JobType.IMPLEMENT, reason="구현 성공"
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Review 전환 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )

        self.logger.info("Delivery 완료, review로 전환: %s", delivery_result.message)
        return PollingResult(
            status=PollingStatus.DELIVERED, task=selected, message=delivery_result.message
        )

    def _run_rework_job(
        self,
        repository: RepositoryConfig,
        selected: IssueTask,
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> PollingResult:
        self.logger.info(
            "Rework 대상 확인: %s #%d (%s)", selected.repository, selected.number, selected.title
        )

        workspace_start = time.monotonic()
        try:
            self.ensure_workspace_ready(repository)
        except WorkspaceValidationError as exc:
            self.logger.error(
                "워크스페이스 검증 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.WORKSPACE_INVALID, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="workspace_validate",
                start=workspace_start,
            )

        issue = issues_by_key[(selected.repository, selected.number)]
        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, selected, issue, cycle_id
        )
        if error is not None:
            return error

        rework_start = time.monotonic()
        try:
            rework_result = self.rework_service.process(  # type: ignore[union-attr]
                repository, issue, linked_pull_request.head_ref, pr_comments
            )
        except ClaimConflictError as exc:  # CP-014-8: another Job already owns this Issue
            self.logger.warning(
                "경쟁 claim으로 건너뜀 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.SKIPPED_ACTIVE_TASK, task=selected, message=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Rework 처리 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="rework_process",
                start=rework_start,
            )

        if not rework_result.triggered:
            self.logger.info("처리할 @devbot review 댓글이 없습니다.")
            return PollingResult(
                status=PollingStatus.SKIPPED_ACTIVE_TASK,
                task=selected,
                message=rework_result.message,
            )

        if rework_result.issue_state is TaskState.BLOCKED:
            self.logger.error("Rework 실패로 blocked 처리: %s", rework_result.message)
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=rework_result.message
            )

        self.logger.info("Rework 완료: %s", rework_result.message)
        return PollingResult(
            status=PollingStatus.REWORKED, task=selected, message=rework_result.message
        )

    def _run_review_job(
        self,
        repository: RepositoryConfig,
        selected: IssueTask,
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> PollingResult:
        self.logger.info(
            "자동 리뷰 대상 확인: %s #%d (%s)", selected.repository, selected.number, selected.title
        )

        workspace_start = time.monotonic()
        try:
            self.ensure_workspace_ready(repository)
        except WorkspaceValidationError as exc:
            self.logger.error(
                "워크스페이스 검증 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.WORKSPACE_INVALID, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="workspace_validate",
                start=workspace_start,
            )

        issue = issues_by_key[(selected.repository, selected.number)]
        linked_pull_request, _pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, selected, issue, cycle_id
        )
        if error is not None:
            return error

        review_start = time.monotonic()
        try:
            review_result = self.review_service.process(  # type: ignore[union-attr]
                repository, issue, linked_pull_request
            )
        except ClaimConflictError as exc:  # CP-014-8: another Job already owns this Issue
            self.logger.warning(
                "경쟁 claim으로 건너뜀 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.SKIPPED_ACTIVE_TASK, task=selected, message=str(exc)
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "리뷰 처리 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            return PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
            )
        finally:
            observability.log_stage(
                self.logger,
                cycle_id,
                repository=selected.repository,
                issue_number=selected.number,
                stage="review_process",
                start=review_start,
            )

        if review_result.issue_state is TaskState.BLOCKED:
            self.logger.error("리뷰 실패로 blocked 처리: %s", review_result.message)
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=review_result.message
            )

        self.logger.info("리뷰 완료: %s", review_result.message)
        return PollingResult(
            status=PollingStatus.REVIEWED, task=selected, message=review_result.message
        )


def run_forever(
    polling_service: PollingService,
    poll_interval_seconds: int,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    logger: logging.Logger | None = None,
) -> None:
    """Run `polling_service.run_once()` repeatedly until SIGINT/SIGTERM.

    `run_once()` still runs every job `run_cycle()` selects this cycle
    (`max_concurrent_jobs` may be > 1) - it only ever *returns* the first
    one, which is all a single-result caller like this loop needs."""
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
