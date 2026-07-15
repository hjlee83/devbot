"""Automatic PR review (Task 012).

Task 011 added a `reviewer_runner` slot to `PollingService` but never
called it. This module is what actually runs it: given a `devbot:review`
Issue and its linked PR, `ReviewService.process()` runs the reviewer once
per PR head commit, posts its output back to the PR with an idempotency
marker, and folds the result into DevBot's existing state machine and
Task 010 rework path - without ever mentioning a specific Agent vendor in
any label, state, or control-flow decision.

Idempotency: the marker `<!-- devbot:auto-review head=<sha> -->` is
appended to the posted comment. `has_review_marker_for_head()` is the
single source of truth for "has this exact PR head commit already been
reviewed" - callers (`devbot.polling`) check it *before* calling
`process()` to decide whether a review job is even a candidate this cycle
(a read-only check, so it's free to run even in dry-run planning); nothing
inside `process()` re-checks it, since by the time `process()` runs, the
caller has already committed to actually reviewing this head.

Rework linkage: when the review result is `REQUEST CHANGES`, the posted
comment also contains the literal string `@devbot`, so the *existing*
Task 010 mechanism (`devbot.rework.find_unprocessed_devbot_comments`)
detects it on the next cycle without this module needing to know anything
about rework itself. `MERGE READY` never contains that mention, so it
never triggers rework.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from devbot.agents.base import AgentRunner, is_session_limit_output
from devbot.github_client import GitHubIssue, PullRequest, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import JobType, RepositoryConfig, TaskState
from devbot.reliability import session_limit_block_reason
from devbot.rework import ReworkActionScope, classify_rework_action_scope
from devbot.timeline import TimelineService, safe_end, safe_start

_MENTION = "@devbot"
_MERGE_READY = "MERGE READY"
_REQUEST_CHANGES = "REQUEST CHANGES"

_MARKER_RE = re.compile(r"<!--\s*devbot:auto-review\s+head=(\S+?)\s*-->")


def build_review_marker(head_sha: str) -> str:
    """The idempotency marker embedded in every posted review comment."""
    return f"<!-- devbot:auto-review head={head_sha} -->"


def has_review_marker_for_head(comments: Sequence[PullRequestComment], head_sha: str) -> bool:
    """True if any comment already carries the auto-review marker for
    exactly this PR head SHA - the reviewer must not run again for it."""
    return any(
        match.group(1) == head_sha
        for comment in comments
        for match in _MARKER_RE.finditer(comment.body)
    )


def build_review_prompt(
    repository: RepositoryConfig,
    issue: GitHubIssue,
    pull_request: PullRequest,
) -> str:
    """The reviewer prompt: everything `AGENTS.md`'s review SOP needs to
    review the PR, plus the output-format and no-merge constraints Task 012
    requires.

    `issue.body` is included verbatim rather than DevBot resolving a Task
    contract/Result document path itself - by convention (see
    `docs/09-task-contract-standard.md`) every execution Issue's body
    already names its own contract and Result document paths, and DevBot
    has no other generic Issue-number-to-Task-number mapping to resolve
    them from.
    """
    return (
        "AGENTS.md의 리뷰 표준 절차(SOP)를 따라 아래 Pull Request를 리뷰하세요. "
        "Task 계약서와 Result 문서 경로는 Issue 본문에 적혀 있습니다.\n\n"
        f"- Issue: {repository.full_name}#{issue.number}\n"
        f"- Issue 제목: {issue.title}\n"
        f"- Issue 본문:\n{issue.body}\n\n"
        f"- Pull Request: {repository.full_name}#{pull_request.number}\n"
        f"- 리뷰 대상 head commit: {pull_request.head_sha}\n\n"
        "리뷰 게이트:\n"
        "- Task 계약, Result 문서, PR Evidence, CI 결과, AGENTS.md 운영 정책이 "
        "서로 일치하는지 확인하세요.\n"
        "- 코드와 테스트가 통과했더라도 위 항목 중 하나라도 누락, 불일치, 최신성 문제, "
        f"근거 부족이 있으면 반드시 `{_REQUEST_CHANGES}`로 판단하세요.\n"
        "- PR Evidence에는 연결 Issue/Task 계약서, 작업 Branch/PR, Result 문서, "
        "Checkpoint별 테스트, 실행한 검증 명령과 결과, CI 상태가 포함되어야 합니다.\n"
        "- Task 하나는 하나의 Branch와 하나의 Pull Request로 추적되어야 하며, "
        "중복 Branch/PR 또는 운영 정책 위반은 리뷰 Blocker입니다.\n"
        "- 위 리뷰 게이트는 구현 관여 여부와 관계없이 모든 리뷰어에게 예외 없이 "
        "적용됩니다. 구현 관여 여부나 특정 Agent 종류에 따라 기준을 완화하거나 "
        "강화하지 말고, 모든 Reviewer가 PR Evidence와 Result가 실제 diff, 테스트, "
        "검증 결과를 빠짐없이 증명하는지 같은 strict gate로 확인하세요.\n\n"
        "Rework 트리거 정책:\n"
        "- repository file change(code/test/docs/result file 등 commit이 필요한 변경)가 "
        "필요한 경우에만 `@devbot rework`로 처리될 수 있게 작성하세요.\n"
        "- PR title/body/evidence, label, Issue/PR comment, reaction 같은 metadata-only "
        "조치에는 `@devbot rework`를 요구하지 말고 `Metadata action required` 섹션에 "
        "사람이 해야 할 일을 적으세요.\n"
        "- CI 확인, 네트워크가 열린 환경의 dry-run 확인, 사람 승인 같은 external "
        "verification 조치에는 `@devbot rework`를 요구하지 말고 "
        "`Manual action required` 섹션에 적으세요.\n\n"
        "최종 출력은 반드시 AGENTS.md에 정의된 `# Review Summary` 형식을 따르고, "
        f"`## 상태` 아래에 `{_MERGE_READY}` 또는 `{_REQUEST_CHANGES}` 중 정확히 하나만 "
        "포함해야 합니다. Pull Request를 Merge하지 마세요."
    )


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """Structured outcome of one `ReviewService.process()` call.

    - `triggered`: a reviewer run was actually attempted (always True for
      `process()` - the caller decides *not* to call it at all when the
      marker check says this head is already reviewed).
    - `status`: `"MERGE READY"` / `"REQUEST CHANGES"` on a valid review,
      else `None` (execution failure or an invalid Review Summary).
    - `issue_state`: the Issue's resulting `devbot:*` state (Task 014
      CP-014-2) - `review` on `MERGE READY`, `rework` on `REQUEST CHANGES`,
      `blocked` on any failure.
    """

    triggered: bool
    status: str | None
    issue_state: TaskState
    message: str = ""


def _parse_review_status(review_text: str) -> str | None:
    """Exactly one of the two literal status markers must appear - both or
    neither is an invalid Review Summary (Task 012 동작 규칙)."""
    has_merge_ready = _MERGE_READY in review_text
    has_request_changes = _REQUEST_CHANGES in review_text
    if has_merge_ready == has_request_changes:
        return None
    return _MERGE_READY if has_merge_ready else _REQUEST_CHANGES


BuildReviewPromptFn = Callable[[RepositoryConfig, GitHubIssue, PullRequest], str]


@dataclass
class ReviewService:
    """Runs one automatic-review cycle for a single Issue/PR head commit."""

    state_writer: IssueStateWriter
    write_client: GitHubWriteClient
    reviewer_runner: AgentRunner
    dry_run: bool = False
    build_prompt: BuildReviewPromptFn = field(default=build_review_prompt)
    # Task 024: automatic `review:start`/`review:end` recording. `None`
    # (every existing caller/test that doesn't set this) is a silent no-op -
    # see `devbot.timeline.safe_start`/`safe_end`.
    timeline: TimelineService | None = None
    actor: str | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("devbot"))

    def process(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        pull_request: PullRequest,
    ) -> ReviewResult:
        if self.dry_run:
            return ReviewResult(
                triggered=True,
                status=None,
                issue_state=TaskState.REVIEW,
                message="[dry-run] would run reviewer; no Agent execution, no GitHub write",
            )

        working_issue = self.state_writer.claim(repository, issue, job_type=JobType.REVIEW)
        actor = self.actor or "unknown"
        safe_start(
            self.timeline,
            repository,
            issue.number,
            phase="review",
            actor=actor,
            pr=pull_request.number,
            logger=self.logger,
        )

        def _end(result: str) -> None:
            safe_end(
                self.timeline,
                repository,
                issue.number,
                phase="review",
                actor=actor,
                result=result,
                pr=pull_request.number,
                logger=self.logger,
            )

        prompt = self.build_prompt(repository, issue, pull_request)

        try:
            result = self.reviewer_runner.run(repository, prompt)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - record, then block
            reason = f"리뷰 Agent 실행 중 오류로 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REVIEW)
            _end("blocked")
            return ReviewResult(
                triggered=True,
                status=None,
                issue_state=TaskState.BLOCKED,
                message="blocked: reviewer execution failed",
            )

        if result.failed:
            reason = f"리뷰 Agent 실행 실패: {result.message}"
            # Task 019 CP-019-9: classify and add a clear recovery hint for
            # a session/usage-limit failure; the devbot:blocked transition
            # itself is unchanged either way.
            if is_session_limit_output(result.message):
                reason = session_limit_block_reason(reason)
                message = "blocked: agent session limit"
            else:
                message = "blocked: reviewer execution failed"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REVIEW)
            _end("blocked")
            return ReviewResult(
                triggered=True,
                status=None,
                issue_state=TaskState.BLOCKED,
                message=message,
            )

        review_text = result.message
        status = _parse_review_status(review_text)
        if status is None:
            reason = (
                f"리뷰 결과 형식이 올바르지 않습니다 (`{_MERGE_READY}` 또는 "
                f"`{_REQUEST_CHANGES}` 중 정확히 하나 필요):\n\n{review_text}"
            )
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REVIEW)
            _end("blocked")
            return ReviewResult(
                triggered=True,
                status=None,
                issue_state=TaskState.BLOCKED,
                message="blocked: invalid review summary",
            )

        comment_body = review_text.rstrip("\n")
        if status == _REQUEST_CHANGES:
            action_scope = classify_rework_action_scope(review_text)
            if action_scope is ReworkActionScope.REPOSITORY_CHANGE:
                comment_body += f"\n\n{_MENTION} 위 REQUEST CHANGES 내용을 반영해 rework 해주세요."
            else:
                comment_body += (
                    f"\n\nMetadata/Manual action scope: `{action_scope.value}`. "
                    "자동 rework 트리거를 만들지 않습니다."
                )
        comment_body += f"\n\n{build_review_marker(pull_request.head_sha)}"

        try:
            self.write_client.create_comment(repository, pull_request.number, comment_body)
        except Exception as exc:  # noqa: BLE001 - record, then block
            reason = f"리뷰 결과 게시 실패: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REVIEW)
            _end("blocked")
            return ReviewResult(
                triggered=True,
                status=status,
                issue_state=TaskState.BLOCKED,
                message="blocked: failed to post review comment",
            )

        # CP-014-2: REQUEST CHANGES separates into its own `devbot:rework`
        # state instead of staying `devbot:review` - Task 010's rework
        # detection still keys off the `@devbot` mention in the posted
        # comment above, unchanged.
        try:
            if status == _REQUEST_CHANGES:
                action_scope = classify_rework_action_scope(review_text)
                if action_scope is ReworkActionScope.REPOSITORY_CHANGE:
                    self.state_writer.send_to_rework(
                        repository,
                        working_issue,
                        job_type=JobType.REVIEW,
                        reason="REQUEST CHANGES 게시 완료",
                    )
                    issue_state = TaskState.REWORK
                    timeline_result = "request-changes"
                else:
                    self.state_writer.require_manual_action(
                        repository,
                        working_issue,
                        (
                            "리뷰 결과가 repository 변경이 아닌 "
                            f"{action_scope.value} 조치를 요구합니다."
                        ),
                        job_type=JobType.REVIEW,
                    )
                    issue_state = TaskState.MANUAL_ACTION
                    timeline_result = "manual-action"
            else:
                self.state_writer.mark_for_review(
                    repository,
                    working_issue,
                    job_type=JobType.REVIEW,
                    reason="MERGE READY 게시 완료",
                )
                issue_state = TaskState.REVIEW
                timeline_result = "merge-ready"
        except Exception as exc:  # noqa: BLE001 - visible comment already posted; block the claim
            reason = f"리뷰 결과 게시 후 상태 전이 실패: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REVIEW)
            _end("blocked")
            return ReviewResult(
                triggered=True,
                status=status,
                issue_state=TaskState.BLOCKED,
                message="blocked: review state transition failed",
            )

        _end(timeline_result)
        return ReviewResult(
            triggered=True,
            status=status,
            issue_state=issue_state,
            message=f"reviewed: {status}",
        )
