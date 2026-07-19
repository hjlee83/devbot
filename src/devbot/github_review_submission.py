"""Task 054: GitHub Review Submission.

Converts one validated Task 053 `ReviewReport` into exactly one official
GitHub Pull Request review (`APPROVE`/`REQUEST_CHANGES`/`COMMENT`), never
allowing stale evidence, a silently dropped finding, or an unrelated
repository write. Task 053 stays entirely provider-neutral - the
`ReviewDecision` -> GitHub event mapping lives only here.

Architecture note - stale-head protection. A report only carries evidence
about one exact commit. `build_github_review_submission_plan` always reads
the PR's *current* head SHA (`GitHubClient.get_pull_request`, read-only)
and compares it against the report's declared `reviewed_head_sha` before
anything else happens - if the PR has moved on since the report was
produced, submitting would misrepresent stale findings as covering the
current code. This is checked before any `GitHubWriteClient` is even
constructed, for both dry-run and real submission, since both share this
same plan-building step.

Architecture note - self-approval. GitHub's real API rejects `APPROVE`
when the authenticated identity authored the PR (HTTP 422). Relying only
on that after the fact would mean an operator only learns this from an
opaque GitHub error message, and - per this Task's Specification -
"do not silently downgrade APPROVE to COMMENT" forbids working around it
quietly. `build_github_review_submission_plan` proactively compares the
authenticated user (`GitHubClient.get_authenticated_user`, read-only)
against the PR author before returning a plan whose event is `APPROVE`,
raising `SelfApprovalError` early with a clear, actionable message. As a
second layer, `submit_github_review` also recognizes GitHub's own 422
self-approval message if the proactive check somehow did not catch it
(e.g. the authenticated identity changed between planning and submission)
and raises the same typed error rather than letting a generic API error
mask what happened.

Architecture note - inline comments never silently drop or misplace a
finding. A finding converts to an inline comment only when it has both a
`path` and a `line` (an anchor GitHub can place a comment at) and, if
`side` is set, that side is one of GitHub's two accepted values (`LEFT`/
`RIGHT`). A finding with no location at all is not an error - it simply
stays body-only. A finding whose location data would only be *partially*
usable in a way that could misplace it (recognized but invalid `side`) is
an error (`UnsupportedInlineLocationError`) - refusing the whole
submission is preferable to silently placing a review comment on the
wrong side of a diff, or silently dropping it into the body only. Every
finding, whether converted inline or not, is also always duplicated
into the top-level body, so nothing can disappear even if GitHub itself
fails to render an inline comment for any reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from devbot.github_client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
)
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.review_decision import ReviewDecision, ReviewLocation, ReviewReport, ReviewSeverity

_HEAD_SHA_METADATA_KEY = "reviewed_head_sha"
_REPOSITORY_METADATA_KEY = "repository"
_PR_NUMBER_METADATA_KEY = "pr_number"

_SUPPORTED_SIDES = ("LEFT", "RIGHT")
_DEFAULT_SIDE = "RIGHT"


class GitHubReviewEvent(StrEnum):
    APPROVE = "APPROVE"
    REQUEST_CHANGES = "REQUEST_CHANGES"
    COMMENT = "COMMENT"


_DECISION_TO_EVENT: dict[ReviewDecision, GitHubReviewEvent] = {
    ReviewDecision.APPROVED: GitHubReviewEvent.APPROVE,
    ReviewDecision.CHANGES_REQUIRED: GitHubReviewEvent.REQUEST_CHANGES,
    ReviewDecision.COMMENT_ONLY: GitHubReviewEvent.COMMENT,
}


class GitHubReviewSubmissionError(RuntimeError):
    """Base for a GitHub review that cannot be safely submitted."""


class MissingReviewIdentityError(GitHubReviewSubmissionError):
    """The report's metadata does not identify the exact commit it
    reviewed."""


class ReviewIdentityMismatchError(GitHubReviewSubmissionError):
    """The report's own declared repository/PR metadata disagrees with the
    repository/PR this submission targets."""


class StaleReviewHeadError(GitHubReviewSubmissionError):
    """The report's reviewed head SHA differs from the PR's current head
    SHA - the evidence examined is no longer current."""


class UnsupportedPullRequestStateError(GitHubReviewSubmissionError):
    """The PR is closed, merged, or otherwise not open for review."""


class UnsupportedInlineLocationError(GitHubReviewSubmissionError):
    """A finding's location data is recognized but invalid in a way that
    would misplace or misrepresent an inline comment if silently
    converted."""


class SelfApprovalError(GitHubReviewSubmissionError):
    """GitHub refuses because the authenticated identity authored the PR -
    never silently downgraded to a different event."""


class GitHubReviewPermissionError(GitHubReviewSubmissionError):
    """GitHub rejected the request due to insufficient permissions."""


class GitHubReviewSubmissionAPIError(GitHubReviewSubmissionError):
    """Any other GitHub API failure while reading PR state or submitting
    the review."""


@dataclass(frozen=True)
class InlineReviewComment:
    path: str
    line: int
    side: str
    body: str
    start_line: int | None = None
    start_side: str | None = None


@dataclass(frozen=True)
class GitHubReviewSubmissionPlan:
    repository: str
    pr_number: int
    reviewed_head_sha: str
    event: GitHubReviewEvent
    body: str
    inline_comments: tuple[InlineReviewComment, ...]
    body_only_finding_count: int


@dataclass(frozen=True)
class GitHubReviewSubmissionResult:
    repository: str
    pr_number: int
    head_sha: str
    event: GitHubReviewEvent
    dry_run: bool
    submitted: bool
    review_id: int | None
    review_url: str | None
    inline_comment_count: int


def _require_reviewed_head_sha(report: ReviewReport) -> str:
    metadata = report.metadata or {}
    value = metadata.get(_HEAD_SHA_METADATA_KEY)
    if value is None or not value.strip():
        raise MissingReviewIdentityError(
            f"report metadata must include a non-empty {_HEAD_SHA_METADATA_KEY!r} field "
            "identifying the exact commit this report reviewed"
        )
    return value.strip()


def _check_identity_cross_reference(
    report: ReviewReport, repository: RepositoryConfig, pr_number: int
) -> None:
    metadata = report.metadata or {}

    declared_repository = metadata.get(_REPOSITORY_METADATA_KEY)
    if declared_repository is not None and declared_repository != repository.full_name:
        raise ReviewIdentityMismatchError(
            f"report metadata declares repository {declared_repository!r}, but this "
            f"submission targets {repository.full_name!r}"
        )

    declared_pr_raw = metadata.get(_PR_NUMBER_METADATA_KEY)
    if declared_pr_raw is not None:
        try:
            declared_pr = int(declared_pr_raw)
        except ValueError:
            raise ReviewIdentityMismatchError(
                f"report metadata's {_PR_NUMBER_METADATA_KEY!r} is not an integer: "
                f"{declared_pr_raw!r}"
            ) from None
        if declared_pr != pr_number:
            raise ReviewIdentityMismatchError(
                f"report metadata declares PR #{declared_pr}, but this submission targets "
                f"PR #{pr_number}"
            )


def _render_location_text(location: ReviewLocation | None) -> str:
    if location is None or location.path is None:
        return ""
    parts = [location.path]
    if location.line is not None:
        if location.start_line is not None and location.start_line != location.line:
            parts.append(f"{location.start_line}-{location.line}")
        else:
            parts.append(str(location.line))
    return ":".join(parts)


def _render_review_body(report: ReviewReport, reviewed_head_sha: str) -> str:
    lines = [
        f"**Decision:** {report.decision.value}",
        "",
        f"**Counts:** blocker={report.counts[ReviewSeverity.BLOCKER]} "
        f"warning={report.counts[ReviewSeverity.WARNING]} "
        f"comment={report.counts[ReviewSeverity.COMMENT]}",
        "",
        f"**Reviewed commit:** `{reviewed_head_sha}`",
    ]
    if report.summary:
        lines += ["", report.summary]
    lines += ["", "**Findings:**", ""]
    if not report.findings:
        lines.append("(none)")
    for finding in report.findings:
        location_text = _render_location_text(finding.location)
        suffix = f" ({location_text})" if location_text else ""
        lines.append(
            f"- **[{finding.severity.value}]** `{finding.code}`: {finding.message}{suffix}"
        )
    return "\n".join(lines)


def _build_inline_comments(
    report: ReviewReport,
) -> tuple[tuple[InlineReviewComment, ...], int]:
    comments: list[InlineReviewComment] = []
    body_only_count = 0
    for finding in report.findings:
        location = finding.location
        if location is None or location.path is None or location.line is None:
            body_only_count += 1
            continue

        side = location.side if location.side is not None else _DEFAULT_SIDE
        if side not in _SUPPORTED_SIDES:
            raise UnsupportedInlineLocationError(
                f"finding {finding.code!r} has an unsupported location side {location.side!r} "
                f"(must be one of {_SUPPORTED_SIDES}) - refusing rather than silently "
                "misplacing or dropping this finding"
            )

        has_multi_line = (
            location.start_line is not None and location.start_line != location.line
        )
        comments.append(
            InlineReviewComment(
                path=location.path,
                line=location.line,
                side=side,
                start_line=location.start_line if has_multi_line else None,
                start_side=side if has_multi_line else None,
                body=f"**[{finding.severity.value}]** `{finding.code}`: {finding.message}",
            )
        )
    return tuple(comments), body_only_count


def build_github_review_submission_plan(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    pr_number: int,
    report: ReviewReport,
) -> GitHubReviewSubmissionPlan:
    """Read-only: no write client, no GitHub write. Reused by both
    dry-run and as the first step of real submission, so stale-head and
    self-approval protection can never differ between the two."""
    reviewed_head_sha = _require_reviewed_head_sha(report)
    _check_identity_cross_reference(report, repository, pr_number)

    try:
        pull_request = github_client.get_pull_request(repository, pr_number)
    except GitHubClientError as exc:
        raise GitHubReviewSubmissionAPIError(
            f"could not read PR #{pr_number}: {exc}"
        ) from exc

    if pull_request.state != "open" or pull_request.merged:
        raise UnsupportedPullRequestStateError(
            f"PR #{pr_number} is not open for review (state={pull_request.state!r}, "
            f"merged={pull_request.merged})"
        )

    if pull_request.head_sha != reviewed_head_sha:
        raise StaleReviewHeadError(
            f"report reviewed commit {reviewed_head_sha!r}, but PR #{pr_number}'s current "
            f"head is {pull_request.head_sha!r} - refusing to submit a review against "
            "evidence that is no longer current"
        )

    event = _DECISION_TO_EVENT[report.decision]

    if event is GitHubReviewEvent.APPROVE:
        try:
            authenticated = github_client.get_authenticated_user()
        except GitHubClientError as exc:
            raise GitHubReviewSubmissionAPIError(
                f"could not verify the authenticated identity: {exc}"
            ) from exc
        if authenticated.login == pull_request.author_login:
            raise SelfApprovalError(
                f"the authenticated identity {authenticated.login!r} authored PR "
                f"#{pr_number} - GitHub does not permit self-approval; refusing before "
                "any write"
            )

    body = _render_review_body(report, reviewed_head_sha)
    inline_comments, body_only_count = _build_inline_comments(report)

    return GitHubReviewSubmissionPlan(
        repository=repository.full_name,
        pr_number=pr_number,
        reviewed_head_sha=reviewed_head_sha,
        event=event,
        body=body,
        inline_comments=inline_comments,
        body_only_finding_count=body_only_count,
    )


def _comment_to_payload(comment: InlineReviewComment) -> dict:
    payload: dict[str, object] = {
        "path": comment.path,
        "line": comment.line,
        "side": comment.side,
        "body": comment.body,
    }
    if comment.start_line is not None:
        payload["start_line"] = comment.start_line
        payload["start_side"] = comment.start_side
    return payload


def _looks_like_self_approval_message(message: str) -> bool:
    lowered = message.lower()
    return (
        "own pull request" in lowered
        or "can not approve" in lowered
        or "cannot approve" in lowered
    )


def submit_github_review(
    github_client: GitHubClient,
    write_client: GitHubWriteClient | None,
    repository: RepositoryConfig,
    pr_number: int,
    report: ReviewReport,
    *,
    dry_run: bool = False,
) -> GitHubReviewSubmissionResult:
    """`dry_run=True` never touches `write_client` (it may be `None`) and
    performs no GitHub write - only `build_github_review_submission_plan`'s
    read-only checks run. `dry_run=False` requires `write_client` and
    submits exactly one review request; no other PR mutation ever
    happens."""
    plan = build_github_review_submission_plan(github_client, repository, pr_number, report)

    if dry_run:
        return GitHubReviewSubmissionResult(
            repository=plan.repository,
            pr_number=plan.pr_number,
            head_sha=plan.reviewed_head_sha,
            event=plan.event,
            dry_run=True,
            submitted=False,
            review_id=None,
            review_url=None,
            inline_comment_count=len(plan.inline_comments),
        )

    if write_client is None:
        raise GitHubReviewSubmissionError("write_client is required when dry_run is False")

    try:
        info = write_client.submit_pull_request_review(
            repository,
            pr_number,
            commit_id=plan.reviewed_head_sha,
            event=plan.event.value,
            body=plan.body,
            comments=[_comment_to_payload(comment) for comment in plan.inline_comments],
        )
    except GitHubAuthenticationError as exc:
        raise GitHubReviewPermissionError(
            f"GitHub rejected the review submission due to insufficient permissions: {exc}"
        ) from exc
    except GitHubAPIError as exc:
        if _looks_like_self_approval_message(str(exc)):
            raise SelfApprovalError(f"GitHub refused the review submission: {exc}") from exc
        raise GitHubReviewSubmissionAPIError(
            f"GitHub rejected the review submission: {exc}"
        ) from exc

    return GitHubReviewSubmissionResult(
        repository=plan.repository,
        pr_number=plan.pr_number,
        head_sha=plan.reviewed_head_sha,
        event=plan.event,
        dry_run=False,
        submitted=True,
        review_id=info.id,
        review_url=info.html_url,
        inline_comment_count=len(plan.inline_comments),
    )


def render_github_review_submission_plan(plan: GitHubReviewSubmissionPlan) -> str:
    lines = [
        f"repository: {plan.repository}",
        f"pr_number: {plan.pr_number}",
        f"reviewed_head_sha: {plan.reviewed_head_sha}",
        f"event: {plan.event.value}",
        "",
        "body:",
        plan.body,
        "",
        f"inline_comments ({len(plan.inline_comments)}):",
    ]
    for comment in plan.inline_comments:
        location = (
            f"{comment.start_line}-{comment.line}" if comment.start_line else str(comment.line)
        )
        lines.append(f"  - {comment.path}:{location} [{comment.side}] {comment.body}")
    lines.append(f"body_only_finding_count: {plan.body_only_finding_count}")
    return "\n".join(lines)
