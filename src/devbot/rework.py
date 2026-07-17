"""PR feedback rework loop.

An Issue moves to `rework` (Task 014 CP-014-2) when an automatic review
posts `REQUEST CHANGES`, carrying an unprocessed `@devbot` PR comment.
`ReworkService.process()` detects the first unprocessed such comment,
claims the Issue to `working`, lets the caller apply the requested change
(`apply_changes`), reruns verification, and either:

- pushes the update to the *existing* branch (no new branch, no new PR)
  and marks the comment processed and the Issue `review` again, or
- leaves the branch alone and moves the Issue to `blocked` with the
  verification failure (or another failure reason) as evidence.

Before touching anything, `process()` also confirms the workspace is
actually checked out on the branch it is about to reuse, and no exception -
Agent, branch lookup, verification, commit/push/reaction, or
`KeyboardInterrupt` - escapes uncaught: every one is recorded as a
`blocked` reason instead (CP-014-7), matching the same "never crash the
loop, never leave the Issue stuck in `working`" contract as the rest of
`devbot.polling`.

"Processed" is tracked with a GitHub-native `eyes` reaction on the
comment, keeping every failure/rework marker in GitHub itself rather than
local state (see `docs/07-decisions.md`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from devbot.agent_execution import AgentExecutionContext
from devbot.agent_outcome import AgentOutcomeError, transition_for
from devbot.agents.base import AgentSessionLimitError
from devbot.delivery import (
    CommitFn,
    CurrentBranchFn,
    HasChangesFn,
    PushFn,
    RunVerificationFn,
    VerificationResult,
    build_commit_message,
    commit_all_changes,
    current_git_branch,
    push_task_branch,
    repository_has_changes,
    run_verification_commands,
)
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import JobType, RepositoryConfig, TaskState
from devbot.reliability import session_limit_block_reason
from devbot.timeline import TimelineService, safe_end, safe_start
from devbot.validation import ValidationFailureCategory

_MENTION = "@devbot"
_PROCESSED_REACTION = "eyes"


class ReworkActionScope(StrEnum):
    """The kind of action a rework comment asks DevBot to perform."""

    REPOSITORY_CHANGE = "repository-change"
    METADATA_ONLY = "metadata-only"
    EXTERNAL_VERIFICATION = "external-verification"


_METADATA_PATTERNS = (
    "pr evidence",
    "pr body",
    "pull request body",
    "pr title",
    "pull request title",
    "issue comment",
    "pr comment",
    "label",
    "metadata",
)

_EXTERNAL_VERIFICATION_PATTERNS = (
    # CP-B0: "ci", "network", "dry-run" 등 기술적 주장은 여기서 사전 판단하지
    # 않는다 - AGENTS.md 12번 섹션이 모든 리뷰에 "CI" 항목을 의무 포함시키므로
    # 정상적인 리뷰조차 구조적으로 오검출됐다 (devbot/devbot#69,#70). 이런
    # 기술적 주장은 실제로 구현 AI가 시도한 뒤 그 실행 결과로 판단하는 편이
    # 텍스트 사전 판단보다 정확하다 (devbot.agent_outcome.classify_agent_outcome
    # 참고). 여기 남긴 두 패턴은 "사람의 권한/승인"이라는, 시도해봐도 대체할
    # 실행결과 신호가 없는 조직적 요구만이다.
    "사람",
    "승인",
)

_REPOSITORY_CHANGE_PATTERNS = (
    "code",
    "test",
    "docs",
    "document",
    "result",
    "file",
    "source",
    "코드",
    "테스트",
    "문서",
    "파일",
)

_VALIDATION_MANUAL_ACTION_CATEGORIES = frozenset(
    {
        ValidationFailureCategory.ENVIRONMENT_PREPARATION_FAILED,
        ValidationFailureCategory.DEPENDENCY_NETWORK_UNAVAILABLE,
        ValidationFailureCategory.FORBIDDEN_HOST_FALLBACK,
    }
)

def classify_rework_action_scope(comment_body: str) -> ReworkActionScope:
    """Classify an unprocessed rework request by the action DevBot can take.

    The classifier is deliberately conservative: explicit metadata or
    external-verification wording wins, otherwise an ambiguous `@devbot`
    request remains a repository-change so existing rework behavior is
    preserved.

    Matching is plain casefolded substring containment, not word-boundary
    regex: this codebase's review/rework text freely mixes Korean and
    English (e.g. "PR body를", "label을"), and Korean case/topic particles
    (이/가/을/를/은/는/와/과/도/의 ...) fuse directly onto the *preceding*
    token with no whitespace - including an English one. A `\\b...\\b`
    boundary check fails at that fusion point regardless of which script
    the pattern itself is in (confirmed empirically: `\\bpr evidence\\b`
    does not match "pr evidence와", `\\blabel\\b` does not match "label을"),
    so word-boundary matching is actively unsafe here, not protective. The
    one real structural collision this classifier had (the bare `"ci"`
    token matching inside ordinary words and the `AGENTS.md`-mandated "CI"
    review item, CP-B0) is fixed by removing that token from
    `_EXTERNAL_VERIFICATION_PATTERNS` entirely, not by adding boundaries.
    """
    body = comment_body.casefold()
    if any(pattern in body for pattern in _EXTERNAL_VERIFICATION_PATTERNS):
        return ReworkActionScope.EXTERNAL_VERIFICATION
    if any(pattern in body for pattern in _METADATA_PATTERNS):
        return ReworkActionScope.METADATA_ONLY
    if any(pattern in body for pattern in _REPOSITORY_CHANGE_PATTERNS):
        return ReworkActionScope.REPOSITORY_CHANGE
    return ReworkActionScope.REPOSITORY_CHANGE


def find_unprocessed_devbot_comments(
    comments: Sequence[PullRequestComment],
) -> list[PullRequestComment]:
    """Return every comment that mentions `@devbot` and has no prior
    `eyes` reaction (DevBot's own "already handled this" marker)."""
    return [
        comment
        for comment in comments
        if _MENTION in comment.body.lower() and comment.reactions.get(_PROCESSED_REACTION, 0) == 0
    ]


ApplyChangesFn = Callable[..., None]


@dataclass(frozen=True, slots=True)
class ReworkResult:
    """Structured outcome of one `ReworkService.process()` call.

    The boolean fields let callers (and tests) tell exactly how far this
    call got without parsing `message`:

    - `code_changed`: `apply_changes` returned without raising.
    - `verification_passed`: verification ran and passed.
    - `committed` / `pushed`: the corresponding Git operation actually ran
      (always `False` under `dry_run`, and whenever the call was blocked
      before reaching that step).
    - `pr_reused`: the pre-existing branch/PR was confirmed safe to reuse
      before any further action was taken (this service never creates a
      new branch or PR - `False` here means the call stopped *before*
      that confirmation, i.e. no unprocessed comment, or a branch
      mismatch).
    - `issue_state`: the Issue's resulting `devbot:*` state.
    """

    triggered: bool
    comment: PullRequestComment | None
    verification: VerificationResult | None
    issue_state: TaskState
    action_scope: ReworkActionScope | None = None
    code_changed: bool = False
    verification_passed: bool = False
    committed: bool = False
    pushed: bool = False
    pr_reused: bool = False
    message: str = ""


@dataclass
class ReworkService:
    """Runs one PR-feedback rework cycle for a single Issue/PR."""

    state_writer: IssueStateWriter
    write_client: GitHubWriteClient
    apply_changes: ApplyChangesFn
    dry_run: bool = False
    run_verification: RunVerificationFn = field(default=run_verification_commands)
    commit: CommitFn = field(default=commit_all_changes)
    push: PushFn = field(default=push_task_branch)
    current_branch: CurrentBranchFn = field(default=current_git_branch)
    has_changes: HasChangesFn = field(default=repository_has_changes)
    # Task 024: automatic `dev:start`/`dev:end` recording for this rework
    # cycle. `None` (every existing caller/test that doesn't set this) is a
    # silent no-op - see `devbot.timeline.safe_start`/`safe_end`.
    timeline: TimelineService | None = None
    actor: str | None = None
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger("devbot"))

    def process(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        branch: str,
        comments: Sequence[PullRequestComment],
        execution_context: AgentExecutionContext | None = None,
    ) -> ReworkResult:
        unprocessed = find_unprocessed_devbot_comments(comments)
        if not unprocessed:
            return ReworkResult(
                triggered=False,
                comment=None,
                verification=None,
                # process() is only ever called for a `devbot:rework` Issue
                # (Task 014 CP-014-3: `PollingService` only selects a REWORK
                # Job when the Issue is already `devbot:rework`); nothing
                # changed, so the state stays `rework`.
                issue_state=TaskState.REWORK,
                message="no unprocessed @devbot comments",
            )

        comment = unprocessed[0]
        action_scope = classify_rework_action_scope(comment.body)

        working_issue = self.state_writer.claim(repository, issue, job_type=JobType.REWORK)
        actor = self.actor or "unknown"
        safe_start(
            self.timeline, repository, issue.number, phase="dev", actor=actor, logger=self.logger
        )

        def _end(result: str) -> None:
            safe_end(
                self.timeline, repository, issue.number, phase="dev", actor=actor, result=result,
                logger=self.logger,
            )

        if action_scope is not ReworkActionScope.REPOSITORY_CHANGE:
            reason = (
                f"Rework 요청이 repository commit으로 해결할 수 없는 action scope입니다: "
                f"{action_scope.value}. 사람 또는 GitHub metadata 조치가 필요합니다."
            )
            self.state_writer.require_manual_action(
                repository, working_issue, reason, job_type=JobType.REWORK
            )
            _end("manual-action")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.MANUAL_ACTION,
                action_scope=action_scope,
                message=f"manual action required: {action_scope.value}",
            )

        try:
            actual_branch = self.current_branch(repository)
        except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working`
            reason = f"현재 브랜치 확인 중 오류로 rework 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                message="blocked: branch lookup failed",
            )

        if actual_branch != branch:
            reason = (
                "기존 PR head 브랜치와 로컬 브랜치 불일치로 rework 중단: "
                f"expected={branch!r} actual={actual_branch!r}"
            )
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                message="blocked: branch mismatch",
            )

        try:
            try:
                self.apply_changes(repository, working_issue, comment, execution_context)
            except TypeError:
                self.apply_changes(repository, working_issue, comment)
        except AgentSessionLimitError as exc:
            # Task 019 CP-019-9: classify and add a clear recovery hint for
            # a session/usage-limit failure; still ends in devbot:blocked,
            # same as the generic Agent-failure path below.
            reason = session_limit_block_reason(f"Agent 실행 중 오류로 rework 중단: {exc}")
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                pr_reused=True,
                message="blocked: agent session limit",
            )
        except AgentOutcomeError as exc:
            # CP-B0: `_apply_rework_changes` (`devbot.main`) now classifies
            # the Agent result via `classify_agent_outcome()` before
            # raising, so a genuine block (approval-required,
            # network-blocked, repository-locked, implementation-skipped,
            # config-invalid, ...) - even one that exited 0 - gets a precise
            # target state and recovery hint here, instead of collapsing
            # into the generic "blocked: agent execution failed" below.
            classification = exc.classification
            transition = transition_for(classification.outcome)
            # ReworkService has no bounded-resume machinery (unlike
            # PollingService's RESUME_ATTEMPT_LIMIT path for the initial
            # IMPLEMENT job) - a RESUMABLE_INTERRUPTION (target_state=None)
            # falls back to blocked here, not an automatic retry.
            target_state = transition.target_state or TaskState.BLOCKED
            reason = (
                f"Agent 실행 결과가 {classification.outcome.value}로 분류되어 "
                f"rework 중단: {exc}\n{transition.recovery_hint}"
            )
            if target_state is TaskState.MANUAL_ACTION:
                self.state_writer.require_manual_action(
                    repository, working_issue, reason, job_type=JobType.REWORK
                )
                _end("manual-action")
            else:
                self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
                _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=target_state,
                action_scope=action_scope,
                pr_reused=True,
                message=f"{target_state.value}: {classification.outcome.value}",
            )
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - record, then block
            reason = f"Agent 실행 중 오류로 rework 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                pr_reused=True,
                message="blocked: agent execution failed",
            )

        try:
            verification = self.run_verification(repository)
        except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working`
            reason = f"검증 실행 중 오류로 rework 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                code_changed=True,
                pr_reused=True,
                message="blocked: verification execution failed",
            )

        if not verification.passed:
            category = (
                verification.failure_category.value
                if verification.failure_category
                else "unknown"
            )
            reason = (
                "PR 피드백 반영 후 검증 실패: "
                f"{' '.join(verification.failed_command or ())}\n"
                f"failure_category={category}"
                f"\n\n{verification.output}"
            )
            if verification.failure_category is ValidationFailureCategory.VALIDATION_COMMAND_FAILED:
                self.state_writer.send_to_rework(
                    repository,
                    working_issue,
                    job_type=JobType.REWORK,
                    reason=reason,
                )
                issue_state = TaskState.REWORK
                message = "rework"
                _end("request-changes")
            elif verification.failure_category in _VALIDATION_MANUAL_ACTION_CATEGORIES:
                self.state_writer.require_manual_action(
                    repository,
                    working_issue,
                    reason,
                    job_type=JobType.REWORK,
                )
                issue_state = TaskState.MANUAL_ACTION
                message = "manual-action"
                _end("manual-action")
            else:
                self.state_writer.block(
                    repository,
                    working_issue,
                    reason,
                    job_type=JobType.REWORK,
                )
                issue_state = TaskState.BLOCKED
                message = "blocked"
                _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=issue_state,
                action_scope=action_scope,
                code_changed=True,
                pr_reused=True,
                message=message,
            )

        if self.dry_run:
            _end("pushed")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=TaskState.WORKING,
                action_scope=action_scope,
                code_changed=True,
                verification_passed=True,
                pr_reused=True,
                message=(
                    "[dry-run] rework verification passed; "
                    "no commit, push, reaction, or review transition"
                ),
            )

        try:
            if not self.has_changes(repository):
                self.write_client.add_reaction_to_comment(
                    repository, comment.id, content=_PROCESSED_REACTION
                )
                self.state_writer.mark_for_review(
                    repository,
                    working_issue,
                    job_type=JobType.REWORK,
                    reason=(
                        "rework 검증은 통과했지만 repository 변경이 없어 "
                        "commit/push 없이 review로 복귀"
                    ),
                )
                _end("pushed")
                return ReworkResult(
                    triggered=True,
                    comment=comment,
                    verification=verification,
                    issue_state=TaskState.REVIEW,
                    action_scope=action_scope,
                    code_changed=False,
                    verification_passed=True,
                    pr_reused=True,
                    message="no_repository_changes",
                )
            self.commit(repository, build_commit_message(working_issue))
            self.push(repository, branch)
            self.write_client.add_reaction_to_comment(
                repository, comment.id, content=_PROCESSED_REACTION
            )
        except Exception as exc:  # noqa: BLE001 - CP-014-7: never leave `working`
            reason = f"커밋/푸시/댓글 반응 처리 중 오류로 rework 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                code_changed=True,
                verification_passed=True,
                pr_reused=True,
                message="blocked: commit/push/reaction failed",
            )

        try:
            self.state_writer.mark_for_review(
                repository, working_issue, job_type=JobType.REWORK, reason="rework 성공"
            )
        except Exception as exc:  # noqa: BLE001 - commit/push already happened; block the claim
            reason = f"rework 성공 후 review 상태 전이 실패: {exc!r}"
            self.state_writer.block(repository, working_issue, reason, job_type=JobType.REWORK)
            _end("blocked")
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=TaskState.BLOCKED,
                action_scope=action_scope,
                code_changed=True,
                verification_passed=True,
                committed=True,
                pushed=True,
                pr_reused=True,
                message="blocked: review transition failed",
            )

        _end("pushed")
        return ReworkResult(
            triggered=True,
            comment=comment,
            verification=verification,
            issue_state=TaskState.REVIEW,
            action_scope=action_scope,
            code_changed=True,
            verification_passed=True,
            committed=True,
            pushed=True,
            pr_reused=True,
            message="reworked",
        )
