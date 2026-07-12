from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.delivery import (
    CheckpointEvidence,
    DeliveryService,
    VerificationResult,
    build_commit_message,
    build_pr_body,
    commit_all_changes,
    push_task_branch,
)
from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient, PullRequestInfo
from devbot.models import RepositoryConfig


def _repo(local_path: Path, *, default_branch: str = "main") -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=local_path,
        default_branch=default_branch,
    )


def _issue(*, number: int = 42, title: str = "Add feature X") -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=number,
        title=title,
        body="issue body",
        state="open",
        labels=("devbot:working",),
        created_at=datetime(2026, 1, 1),
    )


def _passing_service(client: MagicMock, *, dry_run: bool) -> DeliveryService:
    return DeliveryService(
        client=client,
        dry_run=dry_run,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
    )


def test_failed_verification_prevents_commit() -> None:
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()
    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(
            passed=False, failed_command=("uv", "run", "pytest"), output="1 failed"
        ),
        commit=commit,
        push=push,
    )

    result = service.deliver(
        _repo(Path("/tmp/workspace/myrepo")), _issue(), "devbot/myrepo-42-add-feature-x", []
    )

    assert result.committed is False
    assert result.pushed is False
    assert result.pull_request is None
    commit.assert_not_called()
    push.assert_not_called()
    client.create_pull_request.assert_not_called()
    client.create_comment.assert_not_called()


def test_commit_message_references_issue() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"))
    issue = _issue(number=42, title="Add feature X")

    with patch("devbot.delivery.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        commit_all_changes(repository, build_commit_message(issue))

    assert mock_run.call_count == 2
    commit_args = mock_run.call_args_list[1][0][0]
    assert commit_args[:3] == ["git", "commit", "-m"]
    assert "#42" in commit_args[3]
    assert "Add feature X" in commit_args[3]


def test_push_targets_task_branch_only() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"))

    with patch("devbot.delivery.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        push_task_branch(repository, "devbot/myrepo-42-add-feature-x")

    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert args == [
        "git",
        "push",
        "origin",
        "devbot/myrepo-42-add-feature-x:devbot/myrepo-42-add-feature-x",
    ]


def test_pr_targets_default_branch() -> None:
    client = MagicMock(spec=GitHubWriteClient)
    client.create_pull_request.return_value = PullRequestInfo(
        number=99, html_url="https://github.com/someone/myrepo/pull/99"
    )
    service = _passing_service(client, dry_run=False)
    repository = _repo(Path("/tmp/workspace/myrepo"), default_branch="develop")

    service.deliver(repository, _issue(), "devbot/myrepo-42-add-feature-x", [])

    client.create_pull_request.assert_called_once()
    _, kwargs = client.create_pull_request.call_args
    assert kwargs["base"] == "develop"
    assert kwargs["head"] == "devbot/myrepo-42-add-feature-x"


def test_pr_body_contains_checkpoint_evidence() -> None:
    issue = _issue(number=42, title="Add feature X")
    evidence = [
        CheckpointEvidence("CP-007-1", ("test_failed_verification_prevents_commit",)),
        CheckpointEvidence("CP-007-2", ("test_commit_message_references_issue",)),
    ]

    body = build_pr_body(issue, evidence)

    assert "#42" in body
    for item in evidence:
        assert item.checkpoint_id in body
        for name in item.test_names:
            assert name in body


def test_issue_receives_pr_link_comment() -> None:
    client = MagicMock(spec=GitHubWriteClient)
    client.create_pull_request.return_value = PullRequestInfo(
        number=99, html_url="https://github.com/someone/myrepo/pull/99"
    )
    service = _passing_service(client, dry_run=False)
    repository = _repo(Path("/tmp/workspace/myrepo"))
    issue = _issue(number=42)

    result = service.deliver(repository, issue, "devbot/myrepo-42-add-feature-x", [])

    client.create_comment.assert_called_once_with(
        repository, 42, "Opened pull request: https://github.com/someone/myrepo/pull/99"
    )
    assert result.pull_request is not None
    assert result.pull_request.number == 99


def test_delivery_dry_run_has_no_side_effects() -> None:
    client = MagicMock(spec=GitHubWriteClient)
    service = _passing_service(client, dry_run=True)
    repository = _repo(Path("/tmp/workspace/myrepo"))

    result = service.deliver(repository, _issue(), "devbot/myrepo-42-add-feature-x", [])

    service.commit.assert_not_called()  # type: ignore[attr-defined]
    service.push.assert_not_called()  # type: ignore[attr-defined]
    client.create_pull_request.assert_not_called()
    client.create_comment.assert_not_called()
    assert result.committed is False
    assert result.pushed is False
    assert result.pull_request is None
    assert result.dry_run is True
