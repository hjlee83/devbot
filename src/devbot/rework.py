"""PR feedback rework loop.

While an Issue is `review`, a human (or the coordinating GPT) can leave a
`@devbot` PR comment asking for changes. `ReworkService.process()` detects
the first unprocessed such comment, returns the Issue to `working`, lets
the caller apply the requested change (`apply_changes`), reruns
verification, and either:

- pushes the update to the *existing* branch (no new branch, no new PR)
  and marks the comment processed and the Issue `review` again, or
- leaves the branch alone and moves the Issue to `blocked` with the
  verification failure (or another failure reason) as evidence.

Before touching anything, `process()` also confirms the workspace is
actually checked out on the branch it is about to reuse, and it never
lets an Agent exception or `KeyboardInterrupt` escape uncaught - both are
recorded as a `blocked` reason instead, matching the same "never crash the
loop, never silently push the wrong branch" contract as the rest of
`devbot.polling`.

"Processed" is tracked with a GitHub-native `eyes` reaction on the
comment, keeping every failure/rework marker in GitHub itself rather than
local state (see `docs/07-decisions.md`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from devbot.delivery import (
    CommitFn,
    CurrentBranchFn,
    PushFn,
    RunVerificationFn,
    VerificationResult,
    build_commit_message,
    commit_all_changes,
    current_git_branch,
    push_task_branch,
    run_verification_commands,
)
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import RepositoryConfig, TaskState

_MENTION = "@devbot"
_PROCESSED_REACTION = "eyes"


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


ApplyChangesFn = Callable[[RepositoryConfig, GitHubIssue, PullRequestComment], None]


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

    def process(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        branch: str,
        comments: Sequence[PullRequestComment],
    ) -> ReworkResult:
        unprocessed = find_unprocessed_devbot_comments(comments)
        if not unprocessed:
            return ReworkResult(
                triggered=False,
                comment=None,
                verification=None,
                # process() is only ever called for a `devbot:review` Issue
                # (see `PollingService._process_review_task`); nothing
                # changed, so the state stays `review`.
                issue_state=TaskState.REVIEW,
                message="no unprocessed @devbot comments",
            )

        comment = unprocessed[0]

        working_issue = self.state_writer.request_changes(repository, issue)

        actual_branch = self.current_branch(repository)
        if actual_branch != branch:
            reason = (
                "기존 PR head 브랜치와 로컬 브랜치 불일치로 rework 중단: "
                f"expected={branch!r} actual={actual_branch!r}"
            )
            self.state_writer.block(repository, working_issue, reason)
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                message="blocked: branch mismatch",
            )

        try:
            self.apply_changes(repository, working_issue, comment)
        except (Exception, KeyboardInterrupt) as exc:  # noqa: BLE001 - record, then block
            reason = f"Agent 실행 중 오류로 rework 중단: {exc!r}"
            self.state_writer.block(repository, working_issue, reason)
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=None,
                issue_state=TaskState.BLOCKED,
                pr_reused=True,
                message="blocked: agent execution failed",
            )

        verification = self.run_verification(repository)
        if not verification.passed:
            self.state_writer.block(
                repository,
                working_issue,
                "PR 피드백 반영 후 검증 실패: "
                f"{' '.join(verification.failed_command or ())}\n\n{verification.output}",
            )
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=TaskState.BLOCKED,
                code_changed=True,
                pr_reused=True,
                message="blocked",
            )

        if self.dry_run:
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
                issue_state=TaskState.WORKING,
                code_changed=True,
                verification_passed=True,
                pr_reused=True,
                message=(
                    "[dry-run] rework verification passed; "
                    "no commit, push, reaction, or review transition"
                ),
            )

        self.commit(repository, build_commit_message(working_issue))
        self.push(repository, branch)
        self.write_client.add_reaction_to_comment(
            repository, comment.id, content=_PROCESSED_REACTION
        )
        self.state_writer.mark_for_review(repository, working_issue)

        return ReworkResult(
            triggered=True,
            comment=comment,
            verification=verification,
            issue_state=TaskState.REVIEW,
            code_changed=True,
            verification_passed=True,
            committed=True,
            pushed=True,
            pr_reused=True,
            message="reworked",
        )
