from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from devbot.github_client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
    GitHubUser,
    PullRequestDetail,
)
from devbot.github_review_submission import (
    GitHubReviewEvent,
    GitHubReviewPermissionError,
    GitHubReviewSubmissionAPIError,
    GitHubReviewSubmissionError,
    MissingReviewIdentityError,
    ReviewIdentityMismatchError,
    SelfApprovalError,
    StaleReviewHeadError,
    UnsupportedInlineLocationError,
    UnsupportedPullRequestStateError,
    build_github_review_submission_plan,
    render_github_review_submission_plan,
    submit_github_review,
)
from devbot.github_write_client import GitHubWriteClient, PullRequestReviewInfo
from devbot.models import RepositoryConfig
from devbot.review_decision import (
    ReviewFinding,
    ReviewLocation,
    ReviewSeverity,
    build_review_report,
)

_HEAD_SHA = "abc123"


def _repository(**overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone", repo="myrepo", enabled=True, local_path=None
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _pr_detail(**overrides: object) -> PullRequestDetail:
    defaults: dict[str, object] = dict(
        number=99,
        html_url="https://example.invalid/pull/99",
        body="",
        head_ref="feature",
        head_sha=_HEAD_SHA,
        base_ref="main",
        state="open",
        merged=False,
        merge_commit_sha=None,
        merged_at=None,
        author_login="alice",
    )
    defaults.update(overrides)
    return PullRequestDetail(**defaults)  # type: ignore[arg-type]


def _github_client(
    *, pr: PullRequestDetail | None = None, user: GitHubUser | None = None
) -> MagicMock:
    client = MagicMock(spec=GitHubClient)
    client.get_pull_request.return_value = pr or _pr_detail()
    client.get_authenticated_user.return_value = user or GitHubUser(login="devbot-bot", id=1)
    return client


def _report(findings=(), **metadata_overrides: str):
    metadata = {"reviewed_head_sha": _HEAD_SHA}
    metadata.update(metadata_overrides)
    return build_review_report(list(findings), metadata=metadata)


# --------------------------------------------------------------------------
# Report identity requirements.
# --------------------------------------------------------------------------


def test_missing_reviewed_head_sha_raises() -> None:
    report = build_review_report([])
    github_client = _github_client()

    with pytest.raises(MissingReviewIdentityError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_blank_reviewed_head_sha_raises() -> None:
    report = build_review_report([], metadata={"reviewed_head_sha": "   "})
    github_client = _github_client()

    with pytest.raises(MissingReviewIdentityError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_declared_repository_mismatch_raises() -> None:
    report = _report(repository="other/repo")
    github_client = _github_client()

    with pytest.raises(ReviewIdentityMismatchError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_declared_pr_number_mismatch_raises() -> None:
    report = _report(pr_number="5")
    github_client = _github_client()

    with pytest.raises(ReviewIdentityMismatchError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_declared_pr_number_non_integer_raises() -> None:
    report = _report(pr_number="not-a-number")
    github_client = _github_client()

    with pytest.raises(ReviewIdentityMismatchError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_matching_declared_identity_is_accepted() -> None:
    report = _report(repository="someone/myrepo", pr_number="99")
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.pr_number == 99


def test_no_declared_identity_metadata_is_fine() -> None:
    report = _report()
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.reviewed_head_sha == _HEAD_SHA


# --------------------------------------------------------------------------
# Stale head protection.
# --------------------------------------------------------------------------


def test_stale_head_raises() -> None:
    report = _report()
    github_client = _github_client(pr=_pr_detail(head_sha="different-sha"))

    with pytest.raises(StaleReviewHeadError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_matching_head_succeeds() -> None:
    report = _report()
    github_client = _github_client(pr=_pr_detail(head_sha=_HEAD_SHA))

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.reviewed_head_sha == _HEAD_SHA


# --------------------------------------------------------------------------
# PR state.
# --------------------------------------------------------------------------


def test_closed_unmerged_pr_rejected() -> None:
    report = _report()
    github_client = _github_client(pr=_pr_detail(state="closed"))

    with pytest.raises(UnsupportedPullRequestStateError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_merged_pr_rejected() -> None:
    report = _report()
    github_client = _github_client(
        pr=_pr_detail(state="closed", merged=True, merge_commit_sha="mergesha")
    )

    with pytest.raises(UnsupportedPullRequestStateError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_open_pr_accepted() -> None:
    report = _report()
    github_client = _github_client(pr=_pr_detail(state="open"))

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.APPROVE


# --------------------------------------------------------------------------
# Decision -> event mapping.
# --------------------------------------------------------------------------


def test_approved_maps_to_approve() -> None:
    report = _report()
    github_client = _github_client(pr=_pr_detail(author_login="someone-else"))

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.APPROVE


def test_changes_required_maps_to_request_changes() -> None:
    report = _report([ReviewFinding(severity=ReviewSeverity.BLOCKER, code="B1", message="bad")])
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.REQUEST_CHANGES


def test_comment_only_maps_to_comment() -> None:
    report = _report([ReviewFinding(severity=ReviewSeverity.WARNING, code="W1", message="fyi")])
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.COMMENT


# --------------------------------------------------------------------------
# Self-approval: proactive check, and never downgraded.
# --------------------------------------------------------------------------


def test_self_approval_raises_before_any_write(monkeypatch: pytest.MonkeyPatch) -> None:
    report = _report()
    github_client = _github_client(
        pr=_pr_detail(author_login="devbot-bot"), user=GitHubUser(login="devbot-bot", id=1)
    )
    write_client = MagicMock(spec=GitHubWriteClient)

    with pytest.raises(SelfApprovalError):
        submit_github_review(
            github_client, write_client, _repository(), 99, report, dry_run=False
        )

    write_client.submit_pull_request_review.assert_not_called()


def test_self_approval_check_skipped_for_non_approve_events() -> None:
    report = _report([ReviewFinding(severity=ReviewSeverity.BLOCKER, code="B1", message="bad")])
    github_client = _github_client(
        pr=_pr_detail(author_login="devbot-bot"), user=GitHubUser(login="devbot-bot", id=1)
    )

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.REQUEST_CHANGES
    github_client.get_authenticated_user.assert_not_called()


def test_different_author_does_not_trigger_self_approval() -> None:
    report = _report()
    github_client = _github_client(
        pr=_pr_detail(author_login="someone"), user=GitHubUser(login="devbot-bot", id=1)
    )

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.event is GitHubReviewEvent.APPROVE


def test_github_422_self_approval_message_is_recognized_and_not_downgraded() -> None:
    # Simulates the proactive check not catching it (e.g. a race), and
    # GitHub's own API rejecting the write - this must still surface as
    # SelfApprovalError, never as a silently-downgraded COMMENT.
    report = _report()
    github_client = _github_client(pr=_pr_detail(author_login="someone-else"))
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.submit_pull_request_review.side_effect = GitHubAPIError(
        "GitHub API error 422: Unable to create review because you "
        "can not approve your own pull request"
    )

    with pytest.raises(SelfApprovalError):
        submit_github_review(
            github_client, write_client, _repository(), 99, report, dry_run=False
        )


# --------------------------------------------------------------------------
# Deterministic body; every finding preserved.
# --------------------------------------------------------------------------


def test_body_contains_decision_counts_head_and_every_finding() -> None:
    findings = [
        ReviewFinding(severity=ReviewSeverity.BLOCKER, code="B1", message="bad thing"),
        ReviewFinding(
            severity=ReviewSeverity.WARNING,
            code="W1",
            message="minor issue",
            location=ReviewLocation(path="src/a.py", line=10),
        ),
    ]
    report = _report(findings)
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert "changes_required" in plan.body
    assert "blocker=1" in plan.body
    assert "warning=1" in plan.body
    assert _HEAD_SHA in plan.body
    assert "B1" in plan.body
    assert "bad thing" in plan.body
    assert "W1" in plan.body  # also present in body even though it's inline too


def test_body_is_deterministic_across_repeated_calls() -> None:
    report = _report(
        [ReviewFinding(severity=ReviewSeverity.WARNING, code="W1", message="fyi")]
    )
    github_client = _github_client()

    plan_a = build_github_review_submission_plan(github_client, _repository(), 99, report)
    plan_b = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan_a.body == plan_b.body


def test_empty_findings_body_reports_none() -> None:
    report = _report()
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert "(none)" in plan.body


# --------------------------------------------------------------------------
# Inline comment conversion.
# --------------------------------------------------------------------------


def test_finding_without_location_is_body_only() -> None:
    report = _report([ReviewFinding(severity=ReviewSeverity.WARNING, code="W1", message="fyi")])
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.inline_comments == ()
    assert plan.body_only_finding_count == 1


def test_finding_with_path_and_line_becomes_inline() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="src/a.py", line=10),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert len(plan.inline_comments) == 1
    comment = plan.inline_comments[0]
    assert comment.path == "src/a.py"
    assert comment.line == 10
    assert comment.side == "RIGHT"
    assert comment.start_line is None
    assert plan.body_only_finding_count == 0


def test_explicit_side_is_preserved() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py", line=5, side="LEFT"),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.inline_comments[0].side == "LEFT"


def test_multi_line_location_sets_start_line_and_start_side() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py", line=10, start_line=5),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    comment = plan.inline_comments[0]
    assert comment.start_line == 5
    assert comment.start_side == comment.side


def test_single_line_via_equal_start_line_omits_start_line() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py", line=10, start_line=10),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.inline_comments[0].start_line is None


def test_unsupported_side_fails_closed() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py", line=5, side="MIDDLE"),
            )
        ]
    )
    github_client = _github_client()

    with pytest.raises(UnsupportedInlineLocationError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


def test_path_without_line_is_body_only_not_error() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py"),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)

    assert plan.inline_comments == ()
    assert plan.body_only_finding_count == 1


# --------------------------------------------------------------------------
# Dry-run vs. real submission.
# --------------------------------------------------------------------------


def test_dry_run_never_requires_or_uses_write_client() -> None:
    report = _report()
    github_client = _github_client()

    result = submit_github_review(github_client, None, _repository(), 99, report, dry_run=True)

    assert result.dry_run is True
    assert result.submitted is False
    assert result.review_id is None
    assert result.review_url is None


def test_dry_run_reflects_planned_event_and_head() -> None:
    report = _report([ReviewFinding(severity=ReviewSeverity.BLOCKER, code="B1", message="bad")])
    github_client = _github_client()

    result = submit_github_review(github_client, None, _repository(), 99, report, dry_run=True)

    assert result.event is GitHubReviewEvent.REQUEST_CHANGES
    assert result.head_sha == _HEAD_SHA


def test_real_submission_without_write_client_raises() -> None:
    report = _report()
    github_client = _github_client()

    with pytest.raises(GitHubReviewSubmissionError):
        submit_github_review(github_client, None, _repository(), 99, report, dry_run=False)


def test_real_submission_calls_write_client_exactly_once() -> None:
    report = _report()
    github_client = _github_client()
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.submit_pull_request_review.return_value = PullRequestReviewInfo(
        id=42, html_url="https://example.invalid/review/42", state="APPROVED"
    )

    result = submit_github_review(
        github_client, write_client, _repository(), 99, report, dry_run=False
    )

    assert result.submitted is True
    assert result.review_id == 42
    write_client.submit_pull_request_review.assert_called_once()
    kwargs = write_client.submit_pull_request_review.call_args.kwargs
    assert kwargs["commit_id"] == _HEAD_SHA
    assert kwargs["event"] == "APPROVE"


def test_real_submission_makes_no_other_write_client_call() -> None:
    report = _report()
    github_client = _github_client()
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.submit_pull_request_review.return_value = PullRequestReviewInfo(
        id=1, html_url="https://example.invalid/review/1", state="APPROVED"
    )

    submit_github_review(github_client, write_client, _repository(), 99, report, dry_run=False)

    called_methods = {call[0] for call in write_client.method_calls}
    assert called_methods == {"submit_pull_request_review"}


# --------------------------------------------------------------------------
# GitHub API/permission failures.
# --------------------------------------------------------------------------


def test_authentication_error_wrapped_as_permission_error() -> None:
    report = _report()
    github_client = _github_client()
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.submit_pull_request_review.side_effect = GitHubAuthenticationError("nope")

    with pytest.raises(GitHubReviewPermissionError):
        submit_github_review(
            github_client, write_client, _repository(), 99, report, dry_run=False
        )


def test_generic_api_error_wrapped_as_submission_api_error() -> None:
    report = _report()
    github_client = _github_client()
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.submit_pull_request_review.side_effect = GitHubAPIError(
        "GitHub API error 500: internal error"
    )

    with pytest.raises(GitHubReviewSubmissionAPIError):
        submit_github_review(
            github_client, write_client, _repository(), 99, report, dry_run=False
        )


def test_pr_read_failure_wrapped() -> None:
    report = _report()
    github_client = MagicMock(spec=GitHubClient)
    github_client.get_pull_request.side_effect = GitHubClientError("boom")

    with pytest.raises(GitHubReviewSubmissionAPIError):
        build_github_review_submission_plan(github_client, _repository(), 99, report)


# --------------------------------------------------------------------------
# Error hierarchy and rendering.
# --------------------------------------------------------------------------


def test_all_submission_errors_share_base_class() -> None:
    error_types = (
        MissingReviewIdentityError,
        ReviewIdentityMismatchError,
        StaleReviewHeadError,
        UnsupportedPullRequestStateError,
        UnsupportedInlineLocationError,
        SelfApprovalError,
        GitHubReviewPermissionError,
        GitHubReviewSubmissionAPIError,
    )
    for error_type in error_types:
        assert issubclass(error_type, GitHubReviewSubmissionError)


def test_render_includes_all_key_fields() -> None:
    report = _report(
        [
            ReviewFinding(
                severity=ReviewSeverity.WARNING,
                code="W1",
                message="fyi",
                location=ReviewLocation(path="a.py", line=5),
            )
        ]
    )
    github_client = _github_client()

    plan = build_github_review_submission_plan(github_client, _repository(), 99, report)
    rendered = render_github_review_submission_plan(plan)

    assert "someone/myrepo" in rendered
    assert "pr_number: 99" in rendered
    assert _HEAD_SHA in rendered
    assert "event: COMMENT" in rendered
    assert "a.py:5" in rendered
