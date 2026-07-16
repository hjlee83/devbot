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
import subprocess
import time
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum

from devbot import observability
from devbot.agent_outcome import classify_agent_outcome
from devbot.agents.base import AgentRunner, is_session_limit_output
from devbot.delivery import DeliveryService, branch_has_implementation_evidence
from devbot.github_client import GitHubClient, GitHubIssue, PullRequest, PullRequestComment
from devbot.issue_state import ClaimConflictError, IssueStateWriter
from devbot.models import (
    AgentOutcome,
    CandidateExclusion,
    DevBotConfig,
    ExclusionReason,
    FailureCategory,
    IssueComment,
    IssueTask,
    Job,
    JobType,
    Priority,
    RepositoryConfig,
    TaskState,
)
from devbot.reliability import (
    build_diagnostic_report,
    render_diagnostic_report,
    session_limit_block_reason,
)
from devbot.review import ReviewService, has_review_marker_for_head
from devbot.rework import ReworkService, find_unprocessed_devbot_comments
from devbot.scheduler import select_jobs_with_exclusions
from devbot.timeline import TimelineService, safe_end, safe_ready, safe_start
from devbot.workspace import (
    WorkspaceValidationError,
    build_agent_prompt,
    ensure_git_workspace_ready,
    ensure_repository_present,
    generate_branch_name,
)
from devbot.worktree import (
    PreparedWorkspace,
    WorkspacePreparationError,
    WorkspacePreparationFailure,
    parse_branch_from_issue_body,
    parse_pull_request_number_from_issue_body,
    render_prepared_workspace_context,
    render_resume_workspace_context,
)

_STATE_LABEL_PREFIX = "devbot:"
_PRIORITY_LABEL_PREFIX = "priority:"

_LOGGER_NAME = "devbot"

# Task 021 Scope §6: outcomes that require operator action rather than an
# automatic retry, and must never proceed to delivery. `AGENT_FAILED` and
# `SESSION_LIMIT` are handled by their own branches (matching their
# existing, distinct `devbot:blocked` message formats) - every other
# non-completed outcome shares this one manual-action branch.
_MANUAL_ACTION_OUTCOMES = frozenset(
    {
        AgentOutcome.APPROVAL_REQUIRED,
        AgentOutcome.NETWORK_BLOCKED,
        AgentOutcome.REPOSITORY_LOCKED,
        AgentOutcome.IMPLEMENTATION_SKIPPED,
    }
)

RESUME_ATTEMPT_LIMIT = 3
_RESUME_MARKER_RE = re.compile(
    r"<!-- devbot-resume:v1 issue=(?P<issue>\d+) pr=(?P<pr>\d+|-) "
    r"attempt=(?P<attempt>\d+) branch=(?P<branch>[^ ]+) reason=(?P<reason>[^ ]+) -->"
)


def _resume_attempt_from_comments(
    comments: Sequence[IssueComment | PullRequestComment],
    *,
    issue_number: int,
    branch: str,
    pr_number: int | None,
) -> int:
    expected_pr = str(pr_number) if pr_number is not None else "-"
    attempts: list[int] = []
    for comment in comments:
        for match in _RESUME_MARKER_RE.finditer(comment.body):
            if (
                int(match.group("issue")) == issue_number
                and match.group("branch") == branch
                and match.group("pr") == expected_pr
            ):
                attempts.append(int(match.group("attempt")))
    return max(attempts, default=0)


def _changed_files(repository: RepositoryConfig) -> tuple[str, ...]:
    if not repository.local_path.exists():
        return ()
    completed = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(repository.local_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return ()
    files: list[str] = []
    for line in completed.stdout.splitlines():
        if not line:
            continue
        path = line[3:] if len(line) > 3 else line
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[1]
        files.append(path)
    return tuple(files)


def _render_resume_marker(
    *,
    repository: RepositoryConfig,
    issue: GitHubIssue,
    prepared: PreparedWorkspace,
    attempt: int,
    reason: str,
) -> str:
    pr = prepared.pull_request.number if prepared.pull_request is not None else None
    report = build_diagnostic_report(
        repository=repository.full_name,
        category=FailureCategory.AGENT_EXECUTION_FAILED,
        issue_number=issue.number,
        pull_request_number=pr,
        current_branch=prepared.branch,
        workspace_status="dirty" if prepared.dirty else "clean",
        changed_files=_changed_files(prepared.repository),
        attempt=attempt,
    )
    marker = (
        f"<!-- devbot-resume:v1 issue={issue.number} pr={pr if pr is not None else '-'} "
        f"attempt={attempt} branch={prepared.branch} reason={reason} -->"
    )
    return "\n\n".join(
        [
            marker,
            "[DevBot Resume Recovery]",
            f"reason: {reason}",
            f"resume_attempt: {attempt}/{RESUME_ATTEMPT_LIMIT}",
            "worktree: preserved",
            render_diagnostic_report(report),
        ]
    )


class PollingStatus(Enum):
    """Outcome of a single job within a `PollingService.run_cycle()` cycle."""

    SKIPPED_ACTIVE_TASK = "skipped_active_task"
    NO_READY_TASK = "no_ready_task"
    NO_MANAGED_REPOSITORIES = "no_managed_repositories"
    WORKSPACE_INVALID = "workspace_invalid"
    WORKSPACE_PREPARATION_FAILED = "workspace_preparation_failed"
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
    PollingStatus.WORKSPACE_PREPARATION_FAILED,
    PollingStatus.AGENT_FAILED,
    PollingStatus.BLOCKED,
    PollingStatus.ITERATION_ERROR,
}


def classify_job_failure(job_type: JobType, status: PollingStatus, message: str) -> FailureCategory:
    """Best-effort `FailureCategory` for one failed Job result (Task 019
    CP-019-1) - diagnostics/logging only (`devbot.reliability`), never used
    for control flow. The `devbot:*` transition this failure led to has
    already happened by the time a caller calls this (`devbot.issue_state`,
    hardened by Task 014, decides that independently)."""
    if status is PollingStatus.WORKSPACE_INVALID:
        return FailureCategory.WORKSPACE_INVALID
    if status is PollingStatus.WORKSPACE_PREPARATION_FAILED:
        return FailureCategory.WORKSPACE_PREPARATION_FAILED
    if status is PollingStatus.AGENT_FAILED:
        return (
            FailureCategory.AGENT_SESSION_LIMIT
            if is_session_limit_output(message)
            else FailureCategory.AGENT_EXECUTION_FAILED
        )
    if status is PollingStatus.ITERATION_ERROR:
        return FailureCategory.GITHUB_API_ERROR
    if status is PollingStatus.BLOCKED:
        if is_session_limit_output(message):
            return FailureCategory.AGENT_SESSION_LIMIT
        if job_type is JobType.REVIEW:
            return FailureCategory.REVIEW_FAILED
        return FailureCategory.DELIVERY_FAILED
    return FailureCategory.UNKNOWN_ERROR


_NO_WORK_STATUSES = {
    PollingStatus.NO_READY_TASK,
    PollingStatus.SKIPPED_ACTIVE_TASK,
    PollingStatus.NO_MANAGED_REPOSITORIES,
}


def _normalized_cycle_result(selected_jobs: Sequence[Job], results: Sequence[PollingResult]) -> str:
    """Task 020 CP-020-5: one stable, uppercase label summarizing the whole
    cycle - `NO_RUNNABLE_TASK` when no Job ran, the `FailureCategory` of the
    first genuine Job failure this cycle (in `results` order: candidate-
    collection hard errors first, then executed Job results in selection
    order), or the `JobType` of the Job that ran. Independent of
    `observability.QueueSummary`'s state counts - this only summarizes
    *what the cycle did*, not the queue it saw."""
    if not results or all(result.status in _NO_WORK_STATUSES for result in results):
        return "NO_RUNNABLE_TASK"

    job_by_key = {(job.task.repository, job.task.number): job for job in selected_jobs}
    for result in results:
        if result.status in _JOB_FAILURE_STATUSES:
            task_key = (result.task.repository, result.task.number) if result.task else None
            job = job_by_key.get(task_key) if task_key is not None else None
            job_type = job.job_type if job is not None else JobType.IMPLEMENT
            return classify_job_failure(job_type, result.status, result.message).value.upper()

    for result in results:
        task_key = (result.task.repository, result.task.number) if result.task else None
        job = job_by_key.get(task_key) if task_key is not None else None
        if job is not None:
            return job.job_type.value.upper()

    return results[0].status.value.upper()


def _matched_task_states(labels: Iterable[str]) -> list[TaskState]:
    """Every `devbot:*` state label present on `labels`, in `TaskState`
    declaration order (READY, WORKING, REVIEW, REWORK, MANUAL_ACTION,
    BLOCKED, DONE). `_task_state_from_labels` picks the first of these when
    more than one is present - an existing, unchanged rule (Task 020 only
    makes the ambiguity visible via `observability.log_state_label_conflict`,
    CP-020-8)."""
    label_set = set(labels)
    return [state for state in TaskState if f"{_STATE_LABEL_PREFIX}{state.value}" in label_set]


def _task_state_from_labels(labels: Iterable[str]) -> TaskState | None:
    matched = _matched_task_states(labels)
    return matched[0] if matched else None


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
    """Return the open PR linked to `issue`.

    Planner execution Issues declare authoritative metadata in their body
    (`Pull Request: #N`). That explicit PR wins even if the PR body does
    not yet contain a closing keyword. Legacy Issues without Planner PR
    metadata keep the existing closing-keyword behavior."""
    explicit_pr_number = parse_pull_request_number_from_issue_body(issue.body)
    if explicit_pr_number is not None:
        for pull_request in pull_requests:
            if pull_request.number == explicit_pr_number:
                return pull_request
        return None

    for pull_request in pull_requests:
        referenced_numbers = {
            int(match) for match in _CLOSING_KEYWORD_RE.findall(pull_request.body)
        }
        if issue.number in referenced_numbers:
            return pull_request
    return None


def _planner_pr_resolution_failure(issue: GitHubIssue) -> WorkspacePreparationError | None:
    expected_pr = parse_pull_request_number_from_issue_body(issue.body)
    if expected_pr is None:
        return None
    expected_branch = parse_branch_from_issue_body(issue.body)
    return WorkspacePreparationError(
        WorkspacePreparationFailure.LINKED_BRANCH_MISSING,
        "Planner Issue declared a Pull Request that could not be resolved; "
        "fallback branch creation is disabled for Planner Issues: "
        f"expected_pr=#{expected_pr}, expected_branch={expected_branch!r}",
    )


EnsureWorkspaceFn = Callable[[RepositoryConfig], None]
BuildPromptFn = Callable[[RepositoryConfig, GitHubIssue, Sequence[IssueComment]], str]
HasImplementationEvidenceFn = Callable[[RepositoryConfig, str, str], bool]
PrepareWorkspaceFn = Callable[
    [RepositoryConfig, GitHubIssue, PullRequest | None], PreparedWorkspace
]

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
    has_implementation_evidence: HasImplementationEvidenceFn = field(
        default=branch_has_implementation_evidence
    )
    # Task 023: host-managed workspace preparation for IMPLEMENT/REWORK
    # Jobs (`devbot.worktree.WorktreeManager.prepare`, wired by
    # `devbot.main`). `None` (every existing test/caller that doesn't set
    # this) preserves the exact pre-Task-023 behavior - the Agent and
    # delivery run directly against the operator checkout, as always.
    prepare_workspace: PrepareWorkspaceFn | None = None
    state_writer: IssueStateWriter | None = None
    delivery: DeliveryService | None = None
    rework_service: ReworkService | None = None
    review_service: ReviewService | None = None
    # Task 024: automatic `ready`/`dev:start`/`dev:end` recording for
    # IMPLEMENT Jobs (REWORK/REVIEW record their own via `rework_service`/
    # `review_service`). `None` (every existing caller/test that doesn't set
    # this) is a silent no-op - see `devbot.timeline.safe_start`/`safe_end`.
    timeline: TimelineService | None = None
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
                matched_states = _matched_task_states(issue.labels)
                if len(matched_states) > 1:
                    observability.log_state_label_conflict(
                        self.logger,
                        cycle_id,
                        repository=issue.repository,
                        issue_number=issue.number,
                        matched_states=matched_states,
                        resolved_state=matched_states[0],
                    )
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
            resolution_failure = _planner_pr_resolution_failure(issue)
            if resolution_failure is not None:
                self.logger.error(
                    "review Issue의 Planner PR 해석 실패 (%s #%d): %s",
                    selected.repository,
                    selected.number,
                    resolution_failure,
                )
                return (
                    None,
                    [],
                    PollingResult(
                        status=PollingStatus.WORKSPACE_PREPARATION_FAILED,
                        task=selected,
                        message=f"{resolution_failure.category.value}: {resolution_failure}",
                    ),
                )
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

    def _find_linked_pull_request_best_effort(
        self, repository: RepositoryConfig, selected: IssueTask, issue: GitHubIssue
    ) -> PullRequest | None:
        """Best-effort linked-PR lookup for IMPLEMENT delivery (CP-016-10).

        Unlike `_fetch_linked_pull_request_and_comments` (REWORK/REVIEW,
        where a missing linked PR is itself a hard error - those states
        imply a PR must already exist), a `devbot:ready` Issue normally
        has no PR yet. So this never fails the Job: any lookup error just
        falls back to `None` (a freshly generated branch name), the same
        as if no PR were linked."""
        try:
            pull_requests = self.github_client.list_pull_requests(repository)
        except Exception as exc:  # noqa: BLE001 - optional lookup, must not crash the loop
            self.logger.warning(
                "Linked PR 조회 실패, 신규 branch로 진행 (%s #%d): %s",
                selected.repository,
                selected.number,
                exc,
            )
            return None
        return find_linked_pull_request(issue, pull_requests)

    def _rework_state_candidate(
        self,
        repository: RepositoryConfig,
        rework_task: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> tuple[Job | None, int | None, PollingResult | None]:
        """A `devbot:rework` Issue is a REWORK candidate only when its
        linked PR has an unprocessed `@devbot` comment (CP-014-3); other-
        wise it is excluded (DEBUG) and stays `devbot:rework`, waiting for
        feedback. Returns the candidate's linked PR number alongside the
        `Job` (Task 020 CP-020-4) so a caller that later selects this Job
        can report it without a second GitHub lookup."""
        if self.rework_service is None:
            return None, None, None

        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, rework_task, issue, cycle_id
        )
        if error is not None:
            return None, None, error
        assert linked_pull_request is not None  # `error` is None only when this is set

        if find_unprocessed_devbot_comments(pr_comments):
            job = Job(job_type=JobType.REWORK, task=rework_task)
            observability.log_candidate_found(
                self.logger, cycle_id, job, pr_number=linked_pull_request.number
            )
            return job, linked_pull_request.number, None

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
        return None, None, None

    def _review_state_candidate(
        self,
        repository: RepositoryConfig,
        review_task: IssueTask,
        issue: GitHubIssue,
        cycle_id: str,
    ) -> tuple[Job | None, int | None, PollingResult | None]:
        """A `devbot:review` Issue is a REVIEW candidate whenever its
        linked PR's current head commit has no auto-review marker yet
        (Task 014: rework detection no longer runs against `devbot:review`
        Issues - `@devbot` feedback only exists on `devbot:rework` Issues
        now, CP-014-2/CP-014-3). Returns the candidate's linked PR number
        alongside the `Job` (Task 020 CP-020-4), same as
        `_rework_state_candidate`."""
        if self.review_service is None:
            return None, None, None

        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, review_task, issue, cycle_id
        )
        if error is not None:
            return None, None, error
        assert linked_pull_request is not None  # `error` is None only when this is set

        if not has_review_marker_for_head(pr_comments, linked_pull_request.head_sha):
            job = Job(job_type=JobType.REVIEW, task=review_task)
            observability.log_candidate_found(
                self.logger, cycle_id, job, pr_number=linked_pull_request.number
            )
            return job, linked_pull_request.number, None

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
        return None, None, None

    def _collect_job_candidates(
        self,
        repositories: Sequence[RepositoryConfig],
        tasks: Sequence[IssueTask],
        issues_by_key: IssuesByKey,
        cycle_id: str,
    ) -> tuple[list[Job], list[PollingResult], dict[tuple[str, int], int]]:
        tasks_by_repo: dict[str, list[IssueTask]] = {}
        for task in tasks:
            tasks_by_repo.setdefault(task.repository, []).append(task)

        candidates: list[Job] = []
        hard_errors: list[PollingResult] = []
        candidate_pr_numbers: dict[tuple[str, int], int] = {}

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
                job, pr_number, error = self._rework_state_candidate(
                    repository, rework_task, issue, cycle_id
                )
                if error is not None:
                    hard_errors.append(error)
                elif job is not None:
                    candidates.append(job)
                    if pr_number is not None:
                        candidate_pr_numbers[(job.task.repository, job.task.number)] = pr_number

            review_tasks = [task for task in repo_tasks if task.state == TaskState.REVIEW]
            for review_task in review_tasks:
                issue = issues_by_key[(review_task.repository, review_task.number)]
                job, pr_number, error = self._review_state_candidate(
                    repository, review_task, issue, cycle_id
                )
                if error is not None:
                    hard_errors.append(error)
                elif job is not None:
                    candidates.append(job)
                    if pr_number is not None:
                        candidate_pr_numbers[(job.task.repository, job.task.number)] = pr_number

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
                            detail=(
                                "저장소에 devbot:rework 또는 devbot:review Issue가 "
                                "워크스페이스를 점유 중"
                            ),
                        ),
                    )
                continue

            for ready_task in (task for task in repo_tasks if task.state == TaskState.READY):
                job = Job(job_type=JobType.IMPLEMENT, task=ready_task)
                candidates.append(job)
                observability.log_candidate_found(self.logger, cycle_id, job)

        return candidates, hard_errors, candidate_pr_numbers

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

        try:
            if job.job_type is JobType.IMPLEMENT:
                result = self._run_implement_job(repository, job.task, issues_by_key, cycle_id)
            elif job.job_type is JobType.REWORK:
                result = self._run_rework_job(repository, job.task, issues_by_key, cycle_id)
            else:
                result = self._run_review_job(repository, job.task, issues_by_key, cycle_id)
        except Exception as exc:  # noqa: BLE001 - CP-019-7: no Job may ever crash the daemon loop
            self.logger.error(
                "Job 실행 중 처리되지 않은 예외 (%s #%d): %s",
                job.task.repository,
                job.task.number,
                exc,
            )
            result = PollingResult(
                status=PollingStatus.ITERATION_ERROR, task=job.task, message=str(exc)
            )

        failure_summary = result.message if result.status in _JOB_FAILURE_STATUSES else ""
        observability.log_job_finished(
            self.logger,
            cycle_id,
            job,
            status=result.status.value,
            start=start,
            failure_summary=failure_summary,
        )

        if result.status in _JOB_FAILURE_STATUSES:
            category = classify_job_failure(job.job_type, result.status, result.message)
            report = build_diagnostic_report(
                repository=job.task.repository,
                category=category,
                issue_number=job.task.number,
            )
            observability.log_diagnostic_report(self.logger, report)

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
            observability.log_cycle_result(
                self.logger,
                cycle_id,
                _normalized_cycle_result([], results),
                observability.elapsed_ms(cycle_start),
            )
            return results

        # CP-020-1/CP-020-2: one structured queue summary per cycle,
        # covering every stable workflow state - replaces the free-form
        # "ready 상태 Issue 수: ..." line this used to log inline.
        observability.log_queue_summary(
            self.logger, observability.build_queue_summary(cycle_id, tasks)
        )

        candidates, hard_errors, candidate_pr_numbers = self._collect_job_candidates(
            repositories, tasks, issues_by_key, cycle_id
        )
        selection = select_jobs_with_exclusions(candidates, self.config.max_concurrent_jobs)
        observability.log_candidates_excluded(self.logger, cycle_id, selection.exclusions)
        # CP-020-4: one "Selected" report per Job actually chosen to run
        # this cycle (none at all when nothing was selected).
        for job in selection.selected:
            observability.log_job_selected(
                self.logger,
                cycle_id,
                job,
                pr_number=candidate_pr_numbers.get((job.task.repository, job.task.number)),
            )
        job_results = self._execute_jobs(selection.selected, repositories, issues_by_key, cycle_id)

        results = [*hard_errors, *job_results]
        if not results:
            # CP-020-3: no separate free-form "no work" narration - the
            # normalized `NO_RUNNABLE_TASK` cycle result below covers both
            # of these cases; the distinction between them still lives in
            # the returned `PollingStatus` itself.
            if not tasks:
                results = [PollingResult(status=PollingStatus.NO_READY_TASK)]
            elif any(
                task.state in (TaskState.WORKING, TaskState.REVIEW, TaskState.REWORK)
                for task in tasks
            ):
                results = [PollingResult(status=PollingStatus.SKIPPED_ACTIVE_TASK)]
            else:
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
        # CP-020-5: one normalized cycle result, independent of the queue
        # summary's state counts.
        observability.log_cycle_result(
            self.logger,
            cycle_id,
            _normalized_cycle_result(selection.selected, results),
            observability.elapsed_ms(cycle_start),
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

        # Task 024 CP-024-1/CP-024-2: `ready` (Queue start, idempotent - only
        # the first-ever claim actually writes a marker) and `dev:start`
        # (this cycle's Dev phase start) both happen right after claim,
        # matching `docs/10-github-status-timeline.md` section 5's own
        # definition of `dev:start` ("Implementer가 Issue를 claim하고
        # 개발/수정을 시작"). Every downstream failure below - including a
        # preflight `WORKSPACE_INVALID`/`WORKSPACE_PREPARATION_FAILED` that
        # `_restore()`s the claim - must close this phase (CP-024-9), which
        # is why this must happen before, not after, workspace validation.
        safe_ready(self.timeline, repository, issue.number, logger=self.logger)
        safe_start(
            self.timeline,
            repository,
            issue.number,
            phase="dev",
            actor=self.config.implementer_agent,
            logger=self.logger,
        )

        try:
            return self._run_claimed_implement_job(repository, selected, issue, cycle_id)
        except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working` behind
            self.logger.error(
                "예상하지 못한 예외로 Job 중단 (%s #%d): %s",
                selected.repository,
                selected.number,
                exc,
            )
            block_failure = self._block(
                repository,
                issue,
                f"예상하지 못한 예외로 Job 중단: {exc!r}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                logger=self.logger,
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
            if self.prepare_workspace is not None:
                # Task 023 Scope §11: a Job that prepares its own isolated
                # worktree only needs the operator checkout to exist and be
                # a Git repository - not to be clean or on any particular
                # branch (that requirement moves to the worktree itself,
                # trivially satisfied right after it is created).
                ensure_repository_present(repository)
            else:
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
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="restored",
                logger=self.logger,
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

        # Task 023 CP-023-1/CP-023-2/CP-023-3/CP-023-4: resolve the linked
        # PR and prepare an isolated Job worktree *before* the Agent runs
        # (only when `prepare_workspace` is configured - production always
        # wires it via `devbot.main`; every existing caller/test that
        # doesn't leaves this Job's behavior exactly as before Task 023).
        work_repository = repository
        prepared: PreparedWorkspace | None = None
        linked_pull_request: PullRequest | None = None
        if self.prepare_workspace is not None:
            linked_pull_request = self._find_linked_pull_request_best_effort(
                repository, selected, issue
            )
            if linked_pull_request is None:
                resolution_failure = _planner_pr_resolution_failure(issue)
                if resolution_failure is not None:
                    self.logger.error(
                        "Planner PR 해석 실패 (%s #%d): %s",
                        selected.repository,
                        selected.number,
                        resolution_failure,
                    )
                    restore_failure = self._restore(
                        repository,
                        issue,
                        selected.state,
                        f"워크스페이스 준비 실패({resolution_failure.category.value}): "
                        f"{resolution_failure}",
                        selected,
                        job_type=JobType.IMPLEMENT,
                    )
                    if restore_failure is not None:
                        return restore_failure
                    return PollingResult(
                        status=PollingStatus.WORKSPACE_PREPARATION_FAILED,
                        task=selected,
                        message=f"{resolution_failure.category.value}: {resolution_failure}",
                    )
            prep_start = time.monotonic()
            try:
                prepared = self.prepare_workspace(repository, issue, linked_pull_request)
            except WorkspacePreparationError as exc:
                self.logger.error(
                    "워크스페이스 준비 실패 (%s #%d): [%s] %s",
                    selected.repository,
                    selected.number,
                    exc.category.value,
                    exc,
                )
                restore_failure = self._restore(
                    repository,
                    issue,
                    selected.state,
                    f"워크스페이스 준비 실패({exc.category.value}): {exc}",
                    selected,
                    job_type=JobType.IMPLEMENT,
                )
                safe_end(
                    self.timeline,
                    repository,
                    issue.number,
                    phase="dev",
                    actor=self.config.implementer_agent,
                    result="restored",
                    pr=linked_pull_request.number if linked_pull_request is not None else None,
                    logger=self.logger,
                )
                if restore_failure is not None:
                    return restore_failure
                return PollingResult(
                    status=PollingStatus.WORKSPACE_PREPARATION_FAILED,
                    task=selected,
                    message=f"{exc.category.value}: {exc}",
                )
            finally:
                observability.log_stage(
                    self.logger,
                    cycle_id,
                    repository=selected.repository,
                    issue_number=selected.number,
                    stage="workspace_preparation",
                    start=prep_start,
                )
            work_repository = prepared.repository

        # Task 024: best-effort PR number for `dev:end` markers recorded
        # between here and delivery's own branch resolution below - `pr` is
        # an optional Timeline field (`docs/10-github-status-timeline.md`
        # section 4.2), so `None` here just means the Status Card fills it
        # in from a later event (e.g. `review:start`) instead.
        dev_pr_number = (
            prepared.pull_request.number
            if prepared is not None and prepared.pull_request is not None
            else (linked_pull_request.number if linked_pull_request is not None else None)
        )

        issue_comments: Sequence[IssueComment | PullRequestComment] = ()
        resume_attempt = 0
        if prepared is not None:
            try:
                issue_comments = self.github_client.list_issue_comments(repository, issue.number)
            except Exception as exc:  # noqa: BLE001 - comments are diagnostic only
                self.logger.warning(
                    "Resume marker 댓글 조회 실패 (%s #%d): %s",
                    selected.repository,
                    selected.number,
                    exc,
                )
            resume_attempt = _resume_attempt_from_comments(
                issue_comments,
                issue_number=issue.number,
                branch=prepared.branch,
                pr_number=(
                    prepared.pull_request.number if prepared.pull_request is not None else None
                ),
            )
            if prepared.reused and prepared.dirty:
                if not prepared.contract_path:
                    reason = (
                        "dirty worktree가 있지만 Task contract metadata가 없어 안전하게 "
                        "resume할 수 없습니다. 기존 작업은 삭제하지 않았습니다."
                    )
                    try:
                        self.state_writer.require_manual_action(  # type: ignore[union-attr]
                            repository, issue, reason, job_type=JobType.IMPLEMENT
                        )
                    except Exception as exc:  # noqa: BLE001
                        return PollingResult(
                            status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                        )
                    safe_end(
                        self.timeline,
                        repository,
                        issue.number,
                        phase="dev",
                        actor=self.config.implementer_agent,
                        result="manual-action",
                        pr=dev_pr_number,
                        logger=self.logger,
                    )
                    return PollingResult(
                        status=PollingStatus.BLOCKED, task=selected, message=reason
                    )
                if resume_attempt >= RESUME_ATTEMPT_LIMIT:
                    reason = (
                        f"resume attempt limit exceeded ({resume_attempt}/{RESUME_ATTEMPT_LIMIT}). "
                        "보존된 worktree를 삭제하지 않고 manual-action으로 전환합니다."
                    )
                    try:
                        self.state_writer.require_manual_action(  # type: ignore[union-attr]
                            repository, issue, reason, job_type=JobType.IMPLEMENT
                        )
                    except Exception as exc:  # noqa: BLE001
                        return PollingResult(
                            status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                        )
                    safe_end(
                        self.timeline,
                        repository,
                        issue.number,
                        phase="dev",
                        actor=self.config.implementer_agent,
                        result="manual-action",
                        pr=dev_pr_number,
                        logger=self.logger,
                    )
                    return PollingResult(
                        status=PollingStatus.BLOCKED, task=selected, message=reason
                    )

        prompt = self.build_prompt(work_repository, issue, [])
        if prepared is not None:
            prompt = f"{render_prepared_workspace_context(prepared)}\n\n{prompt}"
            if prepared.reused and prepared.dirty:
                prompt = (
                    f"{render_resume_workspace_context(prepared, attempt=resume_attempt + 1)}"
                    f"\n\n{prompt}"
                )

        self.logger.info(
            "AgentRunner 실행: implementer=%s dry_run=%s",
            self.config.implementer_agent,
            self.config.dry_run,
        )

        agent_start = time.monotonic()
        try:
            agent_result = self.implementer_runner.run(work_repository, prompt)
        except KeyboardInterrupt as exc:
            self.logger.error(
                "AgentRunner 실행 중단 (%s #%d): %s", selected.repository, selected.number, exc
            )
            if prepared is not None:
                comment = _render_resume_marker(
                    repository=repository,
                    issue=issue,
                    prepared=prepared,
                    attempt=resume_attempt + 1,
                    reason="interrupted",
                )
                try:
                    self.state_writer.comment(repository, issue, comment)  # type: ignore[union-attr]
                except Exception as comment_exc:  # noqa: BLE001
                    self.logger.warning("Resume 진단 댓글 기록 실패: %s", comment_exc)
                restore_failure = self._restore(
                    repository,
                    issue,
                    selected.state,
                    f"Agent 실행이 중단되어 worktree를 보존하고 resume 대기: {exc}",
                    selected,
                    job_type=JobType.IMPLEMENT,
                )
                safe_end(
                    self.timeline,
                    repository,
                    issue.number,
                    phase="dev",
                    actor=self.config.implementer_agent,
                    result="resumable",
                    pr=dev_pr_number,
                    logger=self.logger,
                )
                if restore_failure is not None:
                    return restore_failure
                return PollingResult(
                    status=PollingStatus.AGENT_FAILED,
                    task=selected,
                    message=f"resumable_interruption: {exc}",
                )
            block_failure = self._block(
                repository,
                issue,
                f"AgentRunner 실행 중단: {exc}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=dev_pr_number,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=str(exc))
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
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
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=dev_pr_number,
                logger=self.logger,
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

        # Task 021 CP-021-1: classify the run into one explicit
        # `AgentOutcome` before deciding anything else. This replaces the
        # old two-step "check `.failed`, then separately check
        # `is_approval_required_output`" chain - blocking text patterns
        # (approval/session-limit/network/lock/skip) are checked *before*
        # `.failed` deliberately, since a genuine block can arrive from a
        # process that still exited 0 (CP-021-3/4/5's "even though the
        # process itself may have exited 0").
        classification = classify_agent_outcome(agent_result)
        self.logger.info(
            "실행 결과: %s (agent_outcome=%s)", agent_result.message, classification.outcome.value
        )

        if classification.outcome is AgentOutcome.RESUMABLE_INTERRUPTION:
            if prepared is None:
                block_failure = self._block(
                    repository,
                    issue,
                    "Agent 실행이 resumable interruption으로 분류되었지만 prepared "
                    f"worktree가 없음: {classification.matched_reason}",
                    selected,
                    job_type=JobType.IMPLEMENT,
                )
                safe_end(
                    self.timeline,
                    repository,
                    issue.number,
                    phase="dev",
                    actor=self.config.implementer_agent,
                    result="blocked",
                    pr=dev_pr_number,
                    logger=self.logger,
                )
                if block_failure is not None:
                    return block_failure
                return PollingResult(
                    status=PollingStatus.AGENT_FAILED,
                    task=selected,
                    message=f"resumable_interruption_without_worktree: {agent_result.message}",
                )
            attempt = resume_attempt + 1
            comment = _render_resume_marker(
                repository=repository,
                issue=issue,
                prepared=prepared,
                attempt=attempt,
                reason="timeout",
            )
            try:
                self.state_writer.comment(repository, issue, comment)  # type: ignore[union-attr]
            except Exception as exc:  # noqa: BLE001 - diagnostics are best-effort
                self.logger.warning("Resume 진단 댓글 기록 실패: %s", exc)
            restore_failure = self._restore(
                repository,
                issue,
                selected.state,
                f"Agent timeout/interruption: worktree 보존 후 resume 대기 "
                f"(attempt {attempt}/{RESUME_ATTEMPT_LIMIT})",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="resumable",
                pr=dev_pr_number,
                logger=self.logger,
            )
            if restore_failure is not None:
                return restore_failure
            return PollingResult(
                status=PollingStatus.AGENT_FAILED,
                task=selected,
                message=f"resumable_interruption: {agent_result.message}",
            )

        if classification.outcome is AgentOutcome.AGENT_FAILED:
            message = (
                agent_result.message or f"AgentRunner exited with code {agent_result.returncode}"
            )
            self.logger.error(
                "AgentRunner 실행 실패 (%s #%d): 종료 코드 %s",
                selected.repository,
                selected.number,
                agent_result.returncode,
            )
            # Task 019 CP-019-9: a session/usage-limit failure gets a
            # distinct, actionable recovery hint on the blocking comment
            # itself - the underlying devbot:blocked transition (and "no
            # automatic retry" behavior) is unchanged either way.
            block_reason = f"AgentRunner 실행 실패: {message}"
            if is_session_limit_output(message):
                block_reason = session_limit_block_reason(block_reason)
            block_failure = self._block(
                repository,
                issue,
                block_reason,
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=dev_pr_number,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(status=PollingStatus.AGENT_FAILED, task=selected, message=message)

        if classification.outcome is AgentOutcome.SESSION_LIMIT:
            # Task 021 CP-021-3: classified independently of
            # `AgentRunResult.failed` - a session/usage-limit message can
            # arrive even from a process that exited 0, which the old
            # `.failed`-gated check never saw. Still resolves to
            # `devbot:blocked` (docs/07-decisions.md, 2026-07-15 "Agent
            # session-limit failures get a distinct block reason, not a new
            # state") with the same recovery hint, never retried.
            block_reason = session_limit_block_reason(
                "Agent 실행 결과가 세션/사용량 제한으로 분류되어 delivery를 실행하지 않음: "
                f"{agent_result.message}"
            )
            block_failure = self._block(
                repository, issue, block_reason, selected, job_type=JobType.IMPLEMENT
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=dev_pr_number,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED,
                task=selected,
                message=f"session_limit: {agent_result.message}",
            )

        if classification.outcome in _MANUAL_ACTION_OUTCOMES:
            # approval_required (CP-016-9) / network_blocked /
            # repository_locked / implementation_skipped (Task 021 Scope
            # §6): the Agent process may have exited 0 having done nothing
            # committable - delivery must not run. This needs operator
            # action, not a `blocked` retry loop.
            reason = (
                f"Agent 실행 결과가 {classification.outcome.value}로 분류되어 delivery를 실행하지 "
                f"않음: {classification.matched_reason}"
            )
            self.logger.error(
                "Agent 실행 결과가 %s입니다 (%s #%d): %s",
                classification.outcome.value,
                selected.repository,
                selected.number,
                agent_result.message,
            )
            try:
                self.state_writer.require_manual_action(  # type: ignore[union-attr]
                    repository, issue, reason, job_type=JobType.IMPLEMENT
                )
            except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working`
                self.logger.error(
                    "Manual action 처리 실패 (%s #%d): %s",
                    selected.repository,
                    selected.number,
                    exc,
                )
                return PollingResult(
                    status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="manual-action",
                pr=dev_pr_number,
                logger=self.logger,
            )
            return PollingResult(
                status=PollingStatus.BLOCKED,
                task=selected,
                message=f"{classification.outcome.value}: {agent_result.message}",
            )

        if classification.outcome is AgentOutcome.UNKNOWN:
            # Task 021 CP-021-11: a run that neither failed nor matched any
            # recognized signal is not implicit success - "safe failure
            # state, never implicit success" (Scope §6).
            reason = (
                "Agent 실행 결과를 분류할 수 없어(unknown) delivery를 실행하지 않고 blocked "
                f"처리함: {classification.matched_reason}"
            )
            block_failure = self._block(
                repository, issue, reason, selected, job_type=JobType.IMPLEMENT
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=dev_pr_number,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED,
                task=selected,
                message=f"unknown: {agent_result.message}",
            )

        # classification.outcome is IMPLEMENTATION_COMPLETED (an executed,
        # non-failed run matching no blocking pattern, or DevBot's own
        # dry-run pipeline preview) - proceed to delivery. This alone is
        # still not proof of a completed implementation (CP-021-11):
        # delivery's own commit/push evidence - or, for a reused PR with
        # nothing new to commit, evidence beyond its pre-existing
        # contract-only commit (see the `no_repository_changes` branch
        # below) - is what actually gates `devbot:review`.
        if prepared is not None:
            # Task 023 CP-023-7: delivery uses the same prepared worktree
            # branch the Agent just ran in - never re-resolved, so it can
            # never mismatch what was actually implemented against.
            branch = prepared.branch
            linked_pull_request = prepared.pull_request
            self.logger.info(
                "준비된 worktree branch로 delivery: branch=%s worktree=%s",
                branch,
                prepared.worktree_path,
            )
        else:
            linked_pull_request = self._find_linked_pull_request_best_effort(
                repository, selected, issue
            )
            if linked_pull_request is not None:
                branch = linked_pull_request.head_ref
                self.logger.info(
                    "기존 linked PR 발견, 해당 branch 재사용: PR #%d branch=%s",
                    linked_pull_request.number,
                    branch,
                )
            else:
                resolution_failure = _planner_pr_resolution_failure(issue)
                if resolution_failure is not None:
                    block_failure = self._block(
                        repository,
                        issue,
                        f"Planner PR 해석 실패: {resolution_failure}",
                        selected,
                        job_type=JobType.IMPLEMENT,
                    )
                    if block_failure is not None:
                        return block_failure
                    return PollingResult(
                        status=PollingStatus.WORKSPACE_PREPARATION_FAILED,
                        task=selected,
                        message=f"{resolution_failure.category.value}: {resolution_failure}",
                    )
                branch = generate_branch_name(repository, issue.number, issue.title)
        self.logger.info("Delivery 시작: branch=%s", branch)

        delivery_start = time.monotonic()
        try:
            delivery_result = self.delivery.deliver(
                work_repository, issue, branch, [], linked_pull_request=linked_pull_request
            )
        except Exception as exc:  # noqa: BLE001 - must not crash the loop
            self.logger.error(
                "Delivery 실패 (%s #%d): %s", selected.repository, selected.number, exc
            )
            block_failure = self._block(
                repository,
                issue,
                f"Delivery 실패: {exc!r}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=linked_pull_request.number if linked_pull_request is not None else None,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=str(exc)
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
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=linked_pull_request.number if linked_pull_request is not None else None,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=delivery_result.message
            )

        if delivery_result.dry_run:
            self.logger.info("Delivery 결과: %s", delivery_result.message)
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="pushed",
                pr=linked_pull_request.number if linked_pull_request is not None else None,
                logger=self.logger,
            )
            return PollingResult(
                status=PollingStatus.AGENT_COMPLETED, task=selected, message=delivery_result.message
            )

        if delivery_result.message == "no_repository_changes":
            # Task 021 Scope §7/§8: a linked PR's mere existence is not
            # proof implementation completed - a contract-only PR (just the
            # Task-contract-authoring commit, e.g. Issue #41's motivating
            # incident) must not satisfy it either. Only resume review when
            # the PR's own branch carries git history evidence beyond that
            # pre-existing contract-only commit.
            has_evidence = linked_pull_request is not None and self.has_implementation_evidence(
                work_repository, linked_pull_request.head_ref, work_repository.default_branch
            )
            if has_evidence:
                assert linked_pull_request is not None  # narrows for type-checking
                # Nothing new to commit, but the reused PR's branch already
                # carries commits beyond its pre-existing contract-only
                # commit - review can safely resume on that PR's existing
                # (unchanged) head.
                try:
                    self.state_writer.mark_for_review(
                        repository,
                        issue,
                        job_type=JobType.IMPLEMENT,
                        reason=(
                            "구현 검증은 통과했지만 신규 repository 변경이 없어 "
                            f"기존 연결 PR #{linked_pull_request.number}을 유지한 채 review로 복귀"
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - must not crash the loop
                    self.logger.error(
                        "Review 전환 실패 (%s #%d): %s", selected.repository, selected.number, exc
                    )
                    return PollingResult(
                        status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                    )
                self.logger.info("Delivery 완료(변경 없음), 기존 PR 유지하며 review로 전환")
                safe_end(
                    self.timeline,
                    repository,
                    issue.number,
                    phase="dev",
                    actor=self.config.implementer_agent,
                    result="pushed",
                    pr=linked_pull_request.number,
                    logger=self.logger,
                )
                return PollingResult(
                    status=PollingStatus.DELIVERED, task=selected, message=delivery_result.message
                )

            # No linked PR, or a linked PR with no implementation evidence
            # beyond its pre-existing contract-only commit: moving to
            # `review` would leave the Issue there with nothing actually
            # implemented behind it - the exact "automation silently
            # advances without a real delivery" failure Task 016 first
            # closed for the no-linked-PR case, and Task 021 now closes for
            # the linked-but-contract-only-PR case too.
            if linked_pull_request is not None:
                reason = (
                    "구현 검증은 통과했지만 신규 repository 변경이 없고 "
                    f"연결 PR #{linked_pull_request.number}에도 계약(contract) 커밋 이외의 "
                    f"구현 증거가 없어 review로 전환하지 않고 manual-action으로 표시함: "
                    f"{delivery_result.message}"
                )
            else:
                reason = (
                    "구현 검증은 통과했지만 신규 repository 변경도 연결된 PR도 없어 "
                    f"review로 전환하지 않고 manual-action으로 표시함: {delivery_result.message}"
                )
            self.logger.error(
                "변경/구현 증거 없이 review 전환을 막고 manual-action 처리 (%s #%d): %s",
                selected.repository,
                selected.number,
                delivery_result.message,
            )
            try:
                self.state_writer.require_manual_action(  # type: ignore[union-attr]
                    repository, issue, reason, job_type=JobType.IMPLEMENT
                )
            except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working`
                self.logger.error(
                    "Manual action 처리 실패 (%s #%d): %s",
                    selected.repository,
                    selected.number,
                    exc,
                )
                return PollingResult(
                    status=PollingStatus.ITERATION_ERROR, task=selected, message=str(exc)
                )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="manual-action",
                pr=linked_pull_request.number if linked_pull_request is not None else None,
                logger=self.logger,
            )
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=delivery_result.message
            )

        if not delivery_result.pushed or delivery_result.pull_request is None:
            # e.g. `delivery_branch_invalid`: verification passed, but the
            # actual push/PR never happened. Never let this silently
            # advance to `review` - block it like any other delivery
            # failure instead.
            self.logger.error(
                "Push/PR 없이 delivery 종료, blocked 처리 (%s #%d): %s",
                selected.repository,
                selected.number,
                delivery_result.message,
            )
            block_failure = self._block(
                repository,
                issue,
                f"Delivery 실패: {delivery_result.message}",
                selected,
                job_type=JobType.IMPLEMENT,
            )
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="dev",
                actor=self.config.implementer_agent,
                result="blocked",
                pr=linked_pull_request.number if linked_pull_request is not None else None,
                logger=self.logger,
            )
            if block_failure is not None:
                return block_failure
            return PollingResult(
                status=PollingStatus.BLOCKED, task=selected, message=delivery_result.message
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
        safe_end(
            self.timeline,
            repository,
            issue.number,
            phase="dev",
            actor=self.config.implementer_agent,
            result="pushed",
            pr=delivery_result.pull_request.number,
            logger=self.logger,
        )
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
            if self.prepare_workspace is not None:
                ensure_repository_present(repository)
            else:
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

        # Task 023 CP-023-4/CP-023-7: a REWORK Job always has an existing
        # linked branch/PR (Task 014 CP-014-3 already requires one) - reuse
        # it in an isolated worktree instead of the operator checkout, the
        # same as an IMPLEMENT Job. The Issue is still `devbot:rework`
        # (not yet claimed - `ReworkService.process()` claims it only after
        # confirming an unprocessed comment exists), so a preparation
        # failure here needs no `_restore()`: nothing was claimed yet.
        work_repository = repository
        if self.prepare_workspace is not None:
            prep_start = time.monotonic()
            try:
                prepared = self.prepare_workspace(repository, issue, linked_pull_request)
            except WorkspacePreparationError as exc:
                self.logger.error(
                    "워크스페이스 준비 실패 (%s #%d): [%s] %s",
                    selected.repository,
                    selected.number,
                    exc.category.value,
                    exc,
                )
                return PollingResult(
                    status=PollingStatus.WORKSPACE_PREPARATION_FAILED,
                    task=selected,
                    message=f"{exc.category.value}: {exc}",
                )
            finally:
                observability.log_stage(
                    self.logger,
                    cycle_id,
                    repository=selected.repository,
                    issue_number=selected.number,
                    stage="workspace_preparation",
                    start=prep_start,
                )
            work_repository = prepared.repository

        rework_start = time.monotonic()
        try:
            rework_result = self.rework_service.process(  # type: ignore[union-attr]
                work_repository, issue, linked_pull_request.head_ref, pr_comments
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

        self.logger.info(
            "Rework 완료: %s action_scope=%s",
            rework_result.message,
            (
                rework_result.action_scope.value
                if rework_result.action_scope is not None
                else "unknown"
            ),
        )
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
        linked_pull_request, pr_comments, error = self._fetch_linked_pull_request_and_comments(
            repository, selected, issue, cycle_id
        )
        if error is not None:
            return error

        review_start = time.monotonic()
        try:
            review_result = self.review_service.process(  # type: ignore[union-attr]
                repository, issue, linked_pull_request, comments=pr_comments
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
            try:
                polling_service.run_once()
            except Exception as exc:  # noqa: BLE001 - CP-019-7: one cycle's failure must not stop the daemon
                log.error(
                    "cycle 실행 중 처리되지 않은 예외, 다음 cycle을 계속 진행합니다: %s", exc
                )
            if shutdown_requested:
                break
            log.info("다음 폴링까지 %d초 대기합니다.", poll_interval_seconds)
            sleep_fn(poll_interval_seconds)
    finally:
        signal.signal(signal.SIGINT, previous_sigint)
        signal.signal(signal.SIGTERM, previous_sigterm)
