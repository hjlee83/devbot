import logging
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import ClaimConflictError, InvalidStateTransitionError, IssueStateWriter
from devbot.models import JobType, RepositoryConfig, TaskState


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


def test_claim_from_review_succeeds() -> None:
    """A `devbot:review` Issue may be claimed for an automatic review."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(4, labels=["devbot:review"])

    writer.claim(repository, issue, job_type=JobType.REVIEW)

    client.set_labels.assert_called_once_with(repository, 4, ["devbot:working"])


def test_claim_from_rework_succeeds() -> None:
    """A `devbot:rework` Issue may be claimed to apply PR feedback
    (CP-014-3/CP-014-4)."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(10, labels=["devbot:rework"])

    writer.claim(repository, issue, job_type=JobType.REWORK)

    client.set_labels.assert_called_once_with(repository, 10, ["devbot:working"])


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


def test_send_to_rework_moves_working_to_rework() -> None:
    """CP-014-2: an automatic review's REQUEST CHANGES moves the Issue to
    `devbot:rework`, not back to `devbot:review`."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(11, labels=["devbot:working"])

    writer.send_to_rework(repository, issue, job_type=JobType.REVIEW)

    client.set_labels.assert_called_once_with(repository, 11, ["devbot:rework"])


def test_manual_action_transition_keeps_single_state_label() -> None:
    writer, client = _writer(dry_run=False)
    repository = _repository()
    issue = _issue(12, labels=("devbot:working", "devbot:rework", "priority:high"))

    updated = writer.require_manual_action(
        repository, issue, "metadata action required", job_type=JobType.REWORK
    )

    assert updated.labels == ("priority:high", "devbot:manual-action")
    client.set_labels.assert_called_once_with(
        repository, 12, ["priority:high", "devbot:manual-action"]
    )
    client.create_comment.assert_called_once_with(repository, 12, "metadata action required")


def test_restore_moves_working_back_to_ready() -> None:
    """CP-014-5: undoing a claim after a preflight failure, before any
    Agent ran."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(12, labels=["devbot:working"])

    writer.restore(repository, issue, TaskState.READY, job_type=JobType.IMPLEMENT)

    client.set_labels.assert_called_once_with(repository, 12, ["devbot:ready"])


def test_restore_moves_working_back_to_rework() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(13, labels=["devbot:working"])

    writer.restore(repository, issue, TaskState.REWORK, job_type=JobType.REWORK)

    client.set_labels.assert_called_once_with(repository, 13, ["devbot:rework"])


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
        writer.claim(repository, issue)


def test_invalid_state_transition_with_no_state_label_is_rejected() -> None:
    writer, client = _writer()
    repository = _repository()
    issue = _issue(7, labels=["priority:high"])

    with pytest.raises(InvalidStateTransitionError):
        writer.claim(repository, issue)


def test_state_transition_keeps_exactly_one_devbot_state_label() -> None:
    """CP-014-1: an Issue that (from a stale manual edit, or a previous
    write that partially applied) carries more than one `devbot:*` label
    still normalizes to exactly one after a successful transition, instead
    of failing closed. `devbot:rework` outranks `devbot:review` in the
    precedence order, so this resolves as a `rework -> working` claim -
    the only transition consistent with either label being the "real" one
    that also allows the requested target."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(20, labels=["devbot:review", "devbot:rework", "priority:high"])

    updated = writer.claim(repository, issue, job_type=JobType.REWORK)

    assert updated.labels == ("priority:high", "devbot:working")
    state_labels = [label for label in updated.labels if label.startswith("devbot:")]
    assert len(state_labels) == 1
    client.set_labels.assert_called_once_with(repository, 20, ["priority:high", "devbot:working"])


def test_dry_run_transitions_can_be_chained_using_returned_issue() -> None:
    writer, client = _writer(dry_run=True)
    repository = _repository()
    issue = _issue(9, labels=["devbot:review"])

    working_issue = writer.claim(repository, issue)
    assert working_issue.labels == ("devbot:working",)

    reviewed_issue = writer.mark_for_review(repository, working_issue)
    assert reviewed_issue.labels == ("devbot:review",)

    client.set_labels.assert_not_called()


def test_competing_claim_runs_only_one_job() -> None:
    """CP-014-8: two threads racing to claim the same Issue at the same
    time - only one may reserve the claim and proceed to write; the other
    must raise `ClaimConflictError` immediately, before it would ever run
    an Agent, and without writing any label itself."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(30, labels=["devbot:ready"])

    barrier = threading.Barrier(2)
    claimed_by: list[str] = []
    conflicted_by: list[str] = []

    def _attempt(label: str) -> None:
        barrier.wait()
        try:
            writer.claim(repository, issue, job_type=JobType.IMPLEMENT)
        except ClaimConflictError:
            conflicted_by.append(label)
            return
        claimed_by.append(label)

    threads = [threading.Thread(target=_attempt, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(claimed_by) == 1
    assert len(conflicted_by) == 1
    assert client.set_labels.call_count == 1


def test_claim_conflict_is_released_after_the_issue_leaves_working() -> None:
    """A resolved claim (moved out of `working`) frees the key: a later
    claim for the same Issue is not spuriously rejected as a conflict."""
    writer, client = _writer()
    repository = _repository()
    issue = _issue(31, labels=["devbot:ready"])

    working_issue = writer.claim(repository, issue, job_type=JobType.IMPLEMENT)
    restored_issue = writer.restore(repository, working_issue, TaskState.READY)

    # Claiming again after the restore must succeed - the earlier claim's
    # reservation was released, not left dangling.
    writer.claim(repository, restored_issue, job_type=JobType.IMPLEMENT)

    assert client.set_labels.call_count == 3


def test_claim_conflict_is_released_when_the_github_write_fails() -> None:
    """If the GitHub write behind `claim()` itself fails, the in-process
    reservation must not become a permanent phantom lock for this Issue."""
    client = MagicMock(spec=GitHubWriteClient)
    client.set_labels.side_effect = RuntimeError("GitHub API 오류")
    writer = IssueStateWriter(client=client, dry_run=False)
    repository = _repository()
    issue = _issue(32, labels=["devbot:ready"])

    with pytest.raises(RuntimeError):
        writer.claim(repository, issue, job_type=JobType.IMPLEMENT)

    client.set_labels.side_effect = None
    # A retry (e.g. the next polling cycle) must not be rejected as a
    # conflict with the failed attempt above.
    writer.claim(repository, issue, job_type=JobType.IMPLEMENT)
    assert client.set_labels.call_count == 2


def test_state_transition_log_contains_correlation_and_reason(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-014-11: every transition logs repository, Issue number, from/to
    state, job type, and reason."""
    writer, _client = _writer()
    repository = _repository()
    issue = _issue(40, labels=["devbot:working"])

    with caplog.at_level(logging.INFO, logger="devbot"):
        writer.block(repository, issue, "검증 실패", job_type=JobType.IMPLEMENT)

    records = [
        record for record in caplog.records if getattr(record, "event", None) == "state_transition"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.repository == repository.full_name
    assert record.issue_number == 40
    assert record.from_state == "working"
    assert record.to_state == "blocked"
    assert record.job_type == "implement"
    assert record.reason == "검증 실패"
