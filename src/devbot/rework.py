"""PR feedback rework loop.

While an Issue is `review`, a human (or the coordinating GPT) can leave a
`@devbot` PR comment asking for changes. `ReworkService.process()` detects
the first unprocessed such comment, returns the Issue to `working`, lets
the caller apply the requested change (`apply_changes`), reruns
verification, and either:

- pushes the update to the *existing* branch (no new branch, no new PR)
  and marks the comment processed and the Issue `review` again, or
- leaves the branch alone and moves the Issue to `blocked` with the
  verification failure as evidence.

"Processed" is tracked with a GitHub-native `eyes` reaction on the
comment, keeping every failure/rework marker in GitHub itself rather than
local state (see `docs/07-decisions.md`).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from devbot.delivery import (
    CommitFn,
    PushFn,
    RunVerificationFn,
    VerificationResult,
    build_commit_message,
    commit_all_changes,
    push_task_branch,
    run_verification_commands,
)
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import RepositoryConfig

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
    """Structured outcome of one `ReworkService.process()` call."""

    triggered: bool
    comment: PullRequestComment | None
    verification: VerificationResult | None
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
                message="no unprocessed @devbot comments",
            )

        comment = unprocessed[0]

        working_issue = self.state_writer.request_changes(repository, issue)
        self.apply_changes(repository, working_issue, comment)

        verification = self.run_verification(repository)
        if not verification.passed:
            self.state_writer.block(
                repository,
                working_issue,
                "PR 피드백 반영 후 검증 실패: "
                f"{' '.join(verification.failed_command or ())}\n\n{verification.output}",
            )
            return ReworkResult(
                triggered=True, comment=comment, verification=verification, message="blocked"
            )

        if self.dry_run:
            return ReworkResult(
                triggered=True,
                comment=comment,
                verification=verification,
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
            triggered=True, comment=comment, verification=verification, message="reworked"
        )
