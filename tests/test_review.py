from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from devbot.agents.base import AgentRunResult
from devbot.github_client import GitHubIssue, PullRequest, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import RepositoryConfig, TaskState
from devbot.review import ReviewService, build_review_marker, has_review_marker_for_head

HEAD_SHA = "a1b2c3d4e5f6"


def _repo() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _issue(*, labels: tuple[str, ...] = ("devbot:review",)) -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=17,
        title="Add feature X",
        body="issue body",
        state="open",
        labels=labels,
        created_at=datetime(2026, 1, 1),
    )


def _pull_request(*, head_sha: str = HEAD_SHA) -> PullRequest:
    return PullRequest(
        number=16,
        head_ref="feature/add-x",
        head_sha=head_sha,
        body="Closes #17",
        html_url="https://github.com/someone/myrepo/pull/16",
    )


def _comment(*, comment_id: int = 1, body: str = "looks good") -> PullRequestComment:
    return PullRequestComment(
        id=comment_id, author="reviewer", body=body, created_at=datetime(2026, 1, 2), reactions={}
    )


def _service(
    *,
    reviewer_runner: MagicMock | None = None,
    state_writer: MagicMock | None = None,
    write_client: MagicMock | None = None,
    dry_run: bool = False,
) -> tuple[ReviewService, MagicMock, MagicMock, MagicMock]:
    reviewer_runner = reviewer_runner or MagicMock()
    state_writer = state_writer or MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    write_client = write_client or MagicMock(spec=GitHubWriteClient)
    service = ReviewService(
        state_writer=state_writer,
        write_client=write_client,
        reviewer_runner=reviewer_runner,
        dry_run=dry_run,
    )
    return service, reviewer_runner, state_writer, write_client


def _process(service: ReviewService, pull_request: PullRequest | None = None):
    return service.process(_repo(), _issue(), pull_request or _pull_request())


# --- CP-012-3: marker prevents duplicate review for the same head -------


def test_review_marker_prevents_duplicate_review_for_same_head() -> None:
    marker_comment = _comment(body=f"# Review Summary\n\n{build_review_marker(HEAD_SHA)}")

    assert has_review_marker_for_head([marker_comment], HEAD_SHA) is True


def test_review_marker_for_different_head_does_not_match() -> None:
    marker_comment = _comment(body=f"# Review Summary\n\n{build_review_marker('other-sha')}")

    assert has_review_marker_for_head([marker_comment], HEAD_SHA) is False


def test_no_marker_comments_means_not_yet_reviewed() -> None:
    assert has_review_marker_for_head([_comment(body="unrelated comment")], HEAD_SHA) is False


# --- CP-012-5 / CP-012-6: REQUEST CHANGES vs MERGE READY -----------------


def test_request_changes_moves_issue_from_review_to_rework() -> None:
    """CP-014-2: REQUEST CHANGES moves the Issue from review claim to the
    explicit `devbot:rework` state."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True,
        dry_run=False,
        message="# Review Summary\n\n## 상태\n\n- REQUEST CHANGES\n\n블로커가 있습니다.",
    )
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.status == "REQUEST CHANGES"
    assert result.issue_state is TaskState.REWORK
    state_writer.send_to_rework.assert_called_once()
    state_writer.mark_for_review.assert_not_called()
    write_client.create_comment.assert_called_once()
    _, _, posted_body = write_client.create_comment.call_args.args
    assert "@devbot" in posted_body
    assert build_review_marker(HEAD_SHA) in posted_body


def test_merge_ready_keeps_issue_in_review_state() -> None:
    """CP-014-2: MERGE READY keeps the Issue in `devbot:review` and does
    not create a rework trigger."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True,
        dry_run=False,
        message="# Review Summary\n\n## 상태\n\n- MERGE READY\n\n문제 없습니다.",
    )
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.status == "MERGE READY"
    assert result.issue_state is TaskState.REVIEW
    state_writer.mark_for_review.assert_called_once()
    state_writer.send_to_rework.assert_not_called()
    _, _, posted_body = write_client.create_comment.call_args.args
    assert "@devbot" not in posted_body
    assert build_review_marker(HEAD_SHA) in posted_body


# --- CP-012-8: reviewer execution failure -> blocked ----------------------


def test_reviewer_failure_moves_issue_to_blocked() -> None:
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=False, dry_run=False, message="Claude CLI가 설치되어 있지 않습니다."
    )
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()
    write_client.create_comment.assert_not_called()


def test_reviewer_exception_moves_issue_to_blocked() -> None:
    reviewer_runner = MagicMock()
    reviewer_runner.run.side_effect = RuntimeError("reviewer crashed")
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()
    write_client.create_comment.assert_not_called()


# --- CP-012-9: invalid Review Summary -> blocked ---------------------------


def test_invalid_review_summary_moves_issue_to_blocked() -> None:
    """Neither `MERGE READY` nor `REQUEST CHANGES` present."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="# Review Summary\n\n리뷰 형식을 안 지켰습니다."
    )
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.status is None
    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()
    write_client.create_comment.assert_not_called()


def test_review_summary_with_both_statuses_moves_issue_to_blocked() -> None:
    """Both `MERGE READY` and `REQUEST CHANGES` present is just as invalid
    as neither - exactly one is required."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True,
        dry_run=False,
        message="# Review Summary\n\nMERGE READY 이면서\nREQUEST CHANGES 이기도 합니다.",
    )
    service, _, state_writer, _ = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.status is None
    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()


# --- CP-012-15: dry-run has no side effects --------------------------------


def test_review_polling_dry_run_has_no_side_effects() -> None:
    reviewer_runner = MagicMock()
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner, dry_run=True)

    result = _process(service)

    assert result.issue_state is TaskState.REVIEW
    reviewer_runner.run.assert_not_called()
    state_writer.claim.assert_not_called()
    state_writer.block.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    write_client.create_comment.assert_not_called()


# --- Extra: happy path claims/releases state -------------------------------


def test_successful_review_claims_and_returns_issue_to_review() -> None:
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="# Review Summary\n\n## 상태\n\n- MERGE READY"
    )
    service, _, state_writer, _ = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    state_writer.claim.assert_called_once()
    state_writer.mark_for_review.assert_called_once()
    assert result.issue_state is TaskState.REVIEW


def test_empty_reviewer_stdout_is_an_invalid_review() -> None:
    """Regression: an empty reviewer stdout (e.g. the CLI produced no
    output) contains neither status marker, so it's an invalid review -
    not a crash, not a silent no-op."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="")
    service, _, state_writer, write_client = _service(reviewer_runner=reviewer_runner)

    result = _process(service)

    assert result.status is None
    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()
    write_client.create_comment.assert_not_called()


def test_state_transition_failure_after_posting_comment_is_not_silently_lost() -> None:
    """Regression: if `mark_for_review` fails *after* the review comment
    was already posted, that's a partial success - the comment (a real,
    visible GitHub side effect) must not be silently discarded. Matching
    `ReworkService`'s own precedent (its `mark_for_review` call isn't
    wrapped either), `ReviewService.process()` lets this propagate rather
    than pretend nothing happened; `devbot.polling` catches it and reports
    `ITERATION_ERROR` for a human/next cycle to notice, while the posted
    comment remains as history."""
    reviewer_runner = MagicMock()
    reviewer_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="# Review Summary\n\n## 상태\n\n- MERGE READY"
    )
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    state_writer.mark_for_review.side_effect = RuntimeError("GitHub API 오류")
    service, _, _, write_client = _service(
        reviewer_runner=reviewer_runner, state_writer=state_writer
    )

    result = _process(service)

    assert result.issue_state is TaskState.BLOCKED
    state_writer.block.assert_called_once()
    write_client.create_comment.assert_called_once()
