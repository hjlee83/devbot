from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.delivery import (
    CheckpointEvidence,
    DeliveryError,
    DeliveryService,
    VerificationResult,
    build_commit_message,
    build_pr_body,
    commit_all_changes,
    local_branch_exists,
    push_task_branch,
    repository_has_changes,
)
from devbot.github_client import GitHubIssue, PullRequest
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


def _linked_pull_request(
    *, number: int = 30, head_ref: str = "task/016-existing-branch", issue_number: int = 31
) -> PullRequest:
    return PullRequest(
        number=number,
        head_ref=head_ref,
        head_sha="deadbeef",
        body=f"Closes #{issue_number}",
        html_url=f"https://github.com/someone/myrepo/pull/{number}",
    )


def _passing_service(
    client: MagicMock, *, dry_run: bool, current_branch: str = "devbot/myrepo-42-add-feature-x"
) -> DeliveryService:
    return DeliveryService(
        client=client,
        dry_run=dry_run,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
        has_changes=lambda repository: True,
        branch_exists=lambda repository, branch: True,
        current_branch=lambda repository: current_branch,
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


def test_repository_has_changes_uses_porcelain_status() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"))

    with patch("devbot.delivery.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=" M src/app.py\n", stderr="")

        assert repository_has_changes(repository) is True

    args = mock_run.call_args.args[0]
    assert args == ["git", "status", "--porcelain"]


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


def test_local_branch_exists_uses_show_ref() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"))

    with patch("devbot.delivery.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

        assert local_branch_exists(repository, "devbot/myrepo-42-add-feature-x") is True

    args = mock_run.call_args.args[0]
    assert args == [
        "git",
        "show-ref",
        "--verify",
        "--quiet",
        "refs/heads/devbot/myrepo-42-add-feature-x",
    ]


def test_delivery_uses_linked_pr_head_branch() -> None:
    """CP-016-10: when the Issue already has a linked open PR, delivery
    must push that PR's own head branch - never a freshly generated
    `devbot/devbot-*` name - and must not open a second PR for it."""
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()
    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        has_changes=lambda repository: True,
        branch_exists=lambda repository, branch: True,
        current_branch=lambda repository: "task/016-existing-branch",
    )
    linked_pull_request = _linked_pull_request()
    repository = _repo(Path("/tmp/workspace/myrepo"))

    result = service.deliver(
        repository,
        _issue(number=31),
        "devbot/myrepo-31-generated-name",
        [],
        linked_pull_request=linked_pull_request,
    )

    push.assert_called_once_with(repository, "task/016-existing-branch")
    client.create_pull_request.assert_not_called()
    client.create_comment.assert_called_once()
    assert result.pushed is True
    assert result.pull_request is not None
    assert result.pull_request.number == 30


def test_delivery_rejects_branch_mismatch_before_commit() -> None:
    """Task 023 Scope §7 (PR #44 REQUEST CHANGES, CP-023-7): if the
    worktree is actually checked out on a different branch than the
    resolved target (e.g. a linked PR's head branch), delivery must reject
    before `commit`/`push` ever run - never commit real work onto the
    wrong local branch and then push the unrelated `target_branch` ref."""
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()
    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        has_changes=lambda repository: True,
        branch_exists=lambda repository, branch: True,
        current_branch=lambda repository: "some-other-branch",
    )
    linked_pull_request = _linked_pull_request()

    result = service.deliver(
        _repo(Path("/tmp/workspace/myrepo")),
        _issue(number=31),
        "devbot/myrepo-31-generated-name",
        [],
        linked_pull_request=linked_pull_request,
    )

    commit.assert_not_called()
    push.assert_not_called()
    client.create_pull_request.assert_not_called()
    client.create_comment.assert_not_called()
    assert result.committed is False
    assert result.pushed is False
    assert result.pull_request is None
    assert "delivery_branch_mismatch" in result.message
    assert "task/016-existing-branch" in result.message
    assert "some-other-branch" in result.message


def test_delivery_rejects_when_current_branch_lookup_fails() -> None:
    """Boundary: an error determining the current branch (e.g. a detached
    or corrupted checkout) must reject cleanly, the same as a genuine
    mismatch - never raise out of `deliver()`."""
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()

    def _raise(repository: RepositoryConfig) -> str:
        raise DeliveryError("git rev-parse --abbrev-ref HEAD failed")

    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        has_changes=lambda repository: True,
        branch_exists=lambda repository, branch: True,
        current_branch=_raise,
    )

    result = service.deliver(
        _repo(Path("/tmp/workspace/myrepo")), _issue(), "devbot/myrepo-42-add-feature-x", []
    )

    commit.assert_not_called()
    push.assert_not_called()
    assert result.committed is False
    assert result.pushed is False
    assert "delivery_branch_mismatch" in result.message


def test_delivery_rejects_missing_local_branch_before_push() -> None:
    """CP-016-11: a push-target branch that doesn't exist locally must be
    rejected as `delivery_branch_invalid` before `push` is ever called -
    never surfaced as a post-hoc `src refspec ... does not match any`."""
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()
    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        has_changes=lambda repository: True,
        branch_exists=lambda repository, branch: False,
        current_branch=lambda repository: "devbot/myrepo-42-add-feature-x",
    )

    result = service.deliver(
        _repo(Path("/tmp/workspace/myrepo")), _issue(), "devbot/myrepo-42-add-feature-x", []
    )

    commit.assert_called_once()
    push.assert_not_called()
    client.create_pull_request.assert_not_called()
    client.create_comment.assert_not_called()
    assert result.committed is True
    assert result.pushed is False
    assert result.pull_request is None
    assert "delivery_branch_invalid" in result.message


def test_delivery_does_not_push_when_commit_created_no_changes() -> None:
    """CP-016-12: a clean workspace (no-op/already-implemented Agent run)
    must not attempt `commit` or `push` - reported as
    `no_repository_changes`, not a `DeliveryError`."""
    client = MagicMock(spec=GitHubWriteClient)
    commit = MagicMock()
    push = MagicMock()
    service = DeliveryService(
        client=client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        has_changes=lambda repository: False,
        current_branch=lambda repository: "devbot/myrepo-42-add-feature-x",
    )

    result = service.deliver(
        _repo(Path("/tmp/workspace/myrepo")), _issue(), "devbot/myrepo-42-add-feature-x", []
    )

    commit.assert_not_called()
    push.assert_not_called()
    client.create_pull_request.assert_not_called()
    assert result.committed is False
    assert result.pushed is False
    assert result.message == "no_repository_changes"
