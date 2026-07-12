from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import InvalidStateTransitionError, IssueStateWriter
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _issue(number: int, *, labels: list[str]) -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=number,
        title="issue title",
        body="issue body",
        state="open",
        labels=tuple(labels),
        created_at=datetime(2026, 1, 1),
    )


def _writer(*, dry_run: bool = False) -> tuple[IssueStateWriter, MagicMock]:
    client = MagicMock(spec=GitHubWriteClient)
    return IssueStateWriter(client=client, dry_run=dry_run), client


def test_claim_replaces_ready_with_working() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(1, labels=["devbot:ready", "priority:high"])

    writer.claim(repository, issue)

    client.set_labels.assert_called_once_with(repository, 1, ["priority:high", "devbot:working"])


def test_claim_dry_run_performs_no_write() -> None:
    writer, client = _writer(dry_run=True)
    repository = _repository()
    issue = _issue(1, labels=["devbot:ready"])

    writer.claim(repository, issue)

    client.set_labels.assert_not_called()
    client.create_comment.assert_not_called()


def test_blocked_transition_adds_reason_comment() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(2, labels=["devbot:working"])

    writer.block(repository, issue, "CI keeps failing on the same test")

    client.set_labels.assert_called_once_with(repository, 2, ["devbot:blocked"])
    client.create_comment.assert_called_once_with(
        repository, 2, "CI keeps failing on the same test"
    )


def test_blocked_transition_dry_run_skips_comment() -> None:
    writer, client = _writer(dry_run=True)
    repository = _repository()
    issue = _issue(2, labels=["devbot:working"])

    writer.block(repository, issue, "CI keeps failing on the same test")

    client.set_labels.assert_not_called()
    client.create_comment.assert_not_called()


def test_success_transition_moves_working_to_review() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(3, labels=["devbot:working", "priority:low"])

    writer.mark_for_review(repository, issue)

    client.set_labels.assert_called_once_with(repository, 3, ["priority:low", "devbot:review"])


def test_review_feedback_returns_issue_to_working() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(4, labels=["devbot:review"])

    writer.request_changes(repository, issue)

    client.set_labels.assert_called_once_with(repository, 4, ["devbot:working"])


def test_invalid_state_transition_is_rejected() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(5, labels=["devbot:working"])

    with pytest.raises(InvalidStateTransitionError):
        writer.claim(repository, issue)

    client.set_labels.assert_not_called()


def test_invalid_state_transition_is_rejected_regardless_of_dry_run() -> None:
    writer, client = _writer(dry_run=True)
    repository = _repository()
    issue = _issue(6, labels=["devbot:done"])

    with pytest.raises(InvalidStateTransitionError):
        writer.request_changes(repository, issue)


def test_invalid_state_transition_with_no_state_label_is_rejected() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(7, labels=["priority:high"])

    with pytest.raises(InvalidStateTransitionError):
        writer.claim(repository, issue)


def test_transition_with_multiple_state_labels_is_rejected() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(8, labels=["devbot:ready", "devbot:review"])

    with pytest.raises(InvalidStateTransitionError):
        writer.claim(repository, issue)


def test_dry_run_transitions_can_be_chained_using_returned_issue() -> None:
    writer, client = _writer(dry_run=True)
    repository = _repository()
    issue = _issue(9, labels=["devbot:review"])

    working_issue = writer.request_changes(repository, issue)
    assert working_issue.labels == ("devbot:working",)

    reviewed_issue = writer.mark_for_review(repository, working_issue)
    assert reviewed_issue.labels == ("devbot:review",)

    client.set_labels.assert_not_called()

    client.set_labels.assert_not_called()
