"""Policy-gated automatic pull request merge support."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from devbot.ci_status import (
    CISource,
    CISourceReading,
    CISourceUnavailable,
    CIStatusResult,
    CIVerdict,
    classify_check_runs,
    classify_combined_status,
    classify_workflow_runs,
    evaluate_ci_status,
    summarize_check_runs,
)
from devbot.github_client import (
    CombinedCommitStatus,
    GitHubClientError,
    GitHubIssue,
    PullRequest,
    WorkflowRun,
)
from devbot.github_write_client import GitHubWriteClient, MergePullRequestResult
from devbot.issue_state import IssueStateWriter
from devbot.models import DevBotConfig, RepositoryConfig

READY_TO_MERGE_LABEL = "devbot:ready-to-merge"


class AutomergeDecision(StrEnum):
    MERGED = "merged"
    BLOCKED = "blocked"
    NOT_CANDIDATE = "not_candidate"


@dataclass(frozen=True, slots=True)
class AutomergeResult:
    decision: AutomergeDecision
    message: str
    merge_result: MergePullRequestResult | None = None


class ListWorkflowRunsForRefFn(Protocol):
    def __call__(self, repository: RepositoryConfig, head_sha: str) -> Sequence[WorkflowRun]: ...


class GetCombinedStatusForRefFn(Protocol):
    def __call__(self, repository: RepositoryConfig, ref: str) -> CombinedCommitStatus: ...


class ListCheckRunsForRefFn(Protocol):
    def __call__(self, repository: RepositoryConfig, ref: str) -> Sequence[dict[str, object]]: ...


CreateCommentFn = Callable[[RepositoryConfig, int, str], None]


class AutomergeService:
    """Evaluate B2 gates and merge a ready PR only when every gate passes."""

    def __init__(
        self,
        *,
        config: DevBotConfig,
        write_client: GitHubWriteClient,
        state_writer: IssueStateWriter,
        list_workflow_runs_for_ref: ListWorkflowRunsForRefFn,
        get_combined_status_for_ref: GetCombinedStatusForRefFn,
        list_check_runs_for_ref: ListCheckRunsForRefFn,
        create_comment: CreateCommentFn | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.write_client = write_client
        self.state_writer = state_writer
        self.list_workflow_runs_for_ref = list_workflow_runs_for_ref
        self.get_combined_status_for_ref = get_combined_status_for_ref
        self.list_check_runs_for_ref = list_check_runs_for_ref
        self.create_comment = create_comment or write_client.create_comment
        self.logger = logger or logging.getLogger("devbot")
        self._reported_blocks: set[tuple[str, int, int, str, str]] = set()

    def process(
        self, repository: RepositoryConfig, issue: GitHubIssue, pull_request: PullRequest
    ) -> AutomergeResult:
        if READY_TO_MERGE_LABEL not in pull_request.labels:
            return AutomergeResult(
                AutomergeDecision.NOT_CANDIDATE,
                f"PR #{pull_request.number}에는 {READY_TO_MERGE_LABEL} 라벨이 없습니다",
            )

        blocked_reason = self._first_blocking_reason(repository, pull_request)
        if blocked_reason is not None:
            self._record_blocked(repository, issue, pull_request, blocked_reason)
            return AutomergeResult(AutomergeDecision.BLOCKED, blocked_reason)

        merge_result = self.write_client.merge_pull_request(
            repository,
            pull_request.number,
            expected_head_sha=pull_request.head_sha,
            commit_title=f"Merge PR #{pull_request.number}",
            commit_message="Merged automatically by DevBot after MERGE READY and green CI.",
            merge_method="merge",
        )
        self.state_writer.mark_done(
            repository,
            issue,
            reason=f"PR #{pull_request.number} 자동 머지 완료: {merge_result.sha}",
        )
        message = f"PR #{pull_request.number} 자동 머지 완료: {merge_result.sha}"
        self.logger.info(message)
        return AutomergeResult(AutomergeDecision.MERGED, message, merge_result=merge_result)

    def _first_blocking_reason(
        self, repository: RepositoryConfig, pull_request: PullRequest
    ) -> str | None:
        if not self.config.automerge_enabled:
            return "자동 머지 킬스위치가 꺼져 있습니다(AUTOMERGE_ENABLED=false)"
        if not repository.automerge_allowed:
            return f"{repository.full_name} 저장소가 자동 머지 allowlist에 없습니다"
        if repository.is_self_repo:
            return f"{repository.full_name}는 DevBot 자기수정 저장소라 자동 머지하지 않습니다"

        ci_result = self._evaluate_ci_status(repository, pull_request.head_sha)
        if ci_result.verdict is CIVerdict.UNKNOWN:
            # Issue #124: when no source can confirm CI status at all
            # (permission gaps, 404s, transient API errors, ...), that must
            # never be treated as "nothing configured, proceed" - it means
            # we genuinely don't know CI status, so this fails exactly like
            # an unmet gate (`_record_blocked`, `devbot:ready-to-merge`
            # kept, human can still merge manually) instead of letting the
            # exception escape uncaught into `run_cycle()` and crash
            # `--once`.
            return f"CI gate 확인 불가: {ci_result.reason}"
        if ci_result.verdict is not CIVerdict.GREEN:
            return f"CI gate 실패: {ci_result.reason}"
        return None

    def _evaluate_ci_status(
        self, repository: RepositoryConfig, head_sha: str
    ) -> CIStatusResult:
        # Issue #127: a fine-grained PAT frequently cannot be granted the
        # "Checks" repository permission, so `list_check_runs_for_ref`
        # alone 403s and cannot be the single source of truth. Each source
        # below is consulted independently and a failure/permission gap in
        # one does not block the others from still confirming CI status.
        readings: list[CISourceReading] = []
        unavailable: list[CISourceUnavailable] = []

        try:
            workflow_runs = self.list_workflow_runs_for_ref(repository, head_sha)
        except GitHubClientError as exc:
            unavailable.append(CISourceUnavailable(CISource.WORKFLOW_RUNS, str(exc)))
        else:
            reading = classify_workflow_runs(workflow_runs)
            (readings if isinstance(reading, CISourceReading) else unavailable).append(reading)

        try:
            combined_status = self.get_combined_status_for_ref(repository, head_sha)
        except GitHubClientError as exc:
            unavailable.append(CISourceUnavailable(CISource.COMMIT_STATUS, str(exc)))
        else:
            reading = classify_combined_status(combined_status)
            (readings if isinstance(reading, CISourceReading) else unavailable).append(reading)

        try:
            raw_check_runs = self.list_check_runs_for_ref(repository, head_sha)
        except GitHubClientError as exc:
            unavailable.append(CISourceUnavailable(CISource.CHECK_RUNS, str(exc)))
        else:
            reading = classify_check_runs(summarize_check_runs(raw_check_runs))
            (readings if isinstance(reading, CISourceReading) else unavailable).append(reading)

        return evaluate_ci_status(readings, unavailable)

    def _record_blocked(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        pull_request: PullRequest,
        reason: str,
    ) -> None:
        message = (
            "자동 머지 게이트가 PR "
            f"#{pull_request.number}을 보류했습니다.\n\n"
            f"- 이유: {reason}\n"
            "- 조치: `devbot:ready-to-merge` 라벨은 유지합니다. "
            "사람이 GitHub에서 수동 머지할 수 있습니다."
        )
        report_key = (
            repository.full_name,
            issue.number,
            pull_request.number,
            pull_request.head_sha,
            reason,
        )
        self.logger.info(
            "자동 머지 보류: repository=%s issue=%d pr=%d reason=%s",
            repository.full_name,
            issue.number,
            pull_request.number,
            reason,
        )
        if report_key in self._reported_blocks:
            return
        self._reported_blocks.add(report_key)
        if not self.config.dry_run:
            self.create_comment(repository, issue.number, message)
