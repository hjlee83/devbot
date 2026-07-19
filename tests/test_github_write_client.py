from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubAuthenticationError, GitHubNotFoundError
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _mock_response(
    *, status_code: int = 200, json_data: object = None, text: str = ""
) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.ok = 200 <= status_code < 300
    response.json.return_value = json_data
    response.text = text
    return response


def test_set_labels_sends_put_with_full_label_set() -> None:
    session = MagicMock()
    session.put.return_value = _mock_response(json_data=[])
    client = GitHubWriteClient("token123", session=session)

    client.set_labels(_repository(), 1, ["devbot:working", "priority:high"])

    session.put.assert_called_once()
    args, kwargs = session.put.call_args
    assert args[0].endswith("/repos/someone/myrepo/issues/1/labels")
    assert kwargs["json"] == {"labels": ["devbot:working", "priority:high"]}
    assert kwargs["headers"]["Authorization"] == "Bearer token123"


def test_create_comment_sends_post_with_body() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(json_data={})
    client = GitHubWriteClient("token123", session=session)

    client.create_comment(_repository(), 1, "blocked: needs clarification")

    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0].endswith("/repos/someone/myrepo/issues/1/comments")
    assert kwargs["json"] == {"body": "blocked: needs clarification"}


def test_update_comment_sends_patch_with_body() -> None:
    session = MagicMock()
    session.patch.return_value = _mock_response(json_data={})
    client = GitHubWriteClient("token123", session=session)

    client.update_comment(_repository(), 555, "updated body")

    session.patch.assert_called_once()
    args, kwargs = session.patch.call_args
    assert args[0].endswith("/repos/someone/myrepo/issues/comments/555")
    assert kwargs["json"] == {"body": "updated body"}


def test_add_reaction_to_comment_sends_post_with_content() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(json_data={})
    client = GitHubWriteClient("token123", session=session)

    client.add_reaction_to_comment(_repository(), 7, content="eyes")

    session.post.assert_called_once()
    args, kwargs = session.post.call_args
    assert args[0].endswith("/repos/someone/myrepo/issues/comments/7/reactions")
    assert kwargs["json"] == {"content": "eyes"}


def test_create_pull_request_sends_post_and_parses_response() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"number": 99, "html_url": "https://github.com/someone/myrepo/pull/99"}
    )
    client = GitHubWriteClient("token123", session=session)

    pull_request = client.create_pull_request(
        _repository(),
        title="Add feature X (#42)",
        body="Closes #42",
        head="devbot/myrepo-42-add-feature-x",
        base="main",
    )

    assert pull_request.number == 99
    assert pull_request.html_url == "https://github.com/someone/myrepo/pull/99"
    args, kwargs = session.post.call_args
    assert args[0].endswith("/repos/someone/myrepo/pulls")
    assert kwargs["json"] == {
        "title": "Add feature X (#42)",
        "body": "Closes #42",
        "head": "devbot/myrepo-42-add-feature-x",
        "base": "main",
    }


def test_merge_pull_request_sends_put_and_parses_response() -> None:
    session = MagicMock()
    session.put.return_value = _mock_response(
        json_data={
            "sha": "merge-sha",
            "merged": True,
            "message": "Pull Request successfully merged",
        }
    )
    client = GitHubWriteClient("token123", session=session)

    result = client.merge_pull_request(
        _repository(),
        99,
        expected_head_sha="head-sha",
        commit_title="Merge PR #99: Add feature X",
        commit_message="Merged automatically by DevBot.",
    )

    assert result.sha == "merge-sha"
    assert result.merged is True
    assert result.message == "Pull Request successfully merged"
    args, kwargs = session.put.call_args
    assert args[0].endswith("/repos/someone/myrepo/pulls/99/merge")
    assert kwargs["json"] == {
        "sha": "head-sha",
        "commit_title": "Merge PR #99: Add feature X",
        "commit_message": "Merged automatically by DevBot.",
        "merge_method": "merge",
    }


def test_submit_pull_request_review_sends_post_and_parses_response() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={
            "id": 777,
            "html_url": "https://github.com/someone/myrepo/pull/99#pullrequestreview-777",
            "state": "APPROVED",
        }
    )
    client = GitHubWriteClient("token123", session=session)

    result = client.submit_pull_request_review(
        _repository(),
        99,
        commit_id="head-sha",
        event="APPROVE",
        body="Looks good.",
        comments=[{"path": "a.py", "line": 5, "side": "RIGHT", "body": "nit"}],
    )

    assert result.id == 777
    assert result.html_url == "https://github.com/someone/myrepo/pull/99#pullrequestreview-777"
    assert result.state == "APPROVED"
    args, kwargs = session.post.call_args
    assert args[0].endswith("/repos/someone/myrepo/pulls/99/reviews")
    assert kwargs["json"] == {
        "commit_id": "head-sha",
        "event": "APPROVE",
        "body": "Looks good.",
        "comments": [{"path": "a.py", "line": 5, "side": "RIGHT", "body": "nit"}],
    }


def test_submit_pull_request_review_omits_comments_key_when_empty() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        json_data={"id": 778, "html_url": "https://example.invalid/778", "state": "COMMENTED"}
    )
    client = GitHubWriteClient("token123", session=session)

    client.submit_pull_request_review(
        _repository(), 99, commit_id="head-sha", event="COMMENT", body="fyi"
    )

    _, kwargs = session.post.call_args
    assert "comments" not in kwargs["json"]


def test_write_client_exposes_write_operations_only() -> None:
    allowed_public_methods = {
        "add_reaction_to_comment",
        "create_comment",
        "create_pull_request",
        "merge_pull_request",
        "set_labels",
        "set_pull_request_labels",
        "update_comment",
        # Task 037: dispatches the existing Release workflow; never creates
        # a tag or Release directly.
        "dispatch_workflow",
        # Task 040: goal executor's only writes - one Issue, one branch ref,
        # one file commit. Never opens a Pull Request or merges anything.
        "create_issue",
        "create_branch",
        "create_file",
        # Task 049: the second, deliberate direct publish path - creates a
        # published GitHub Release for a tag `release_publish` already
        # created and pushed itself via local `git`. See
        # docs/07-decisions.md for why this coexists with `dispatch_workflow`.
        "create_release",
        # Task 054: submits exactly one official PR review. Owns no event
        # mapping or validation policy of its own - devbot.github_review
        # _submission does that and always binds commit_id to a
        # pre-validated head SHA.
        "submit_pull_request_review",
    }

    public_attrs = {
        name
        for name in dir(GitHubWriteClient)
        if not name.startswith("_") and callable(getattr(GitHubWriteClient, name))
    }

    assert public_attrs == allowed_public_methods


def test_write_not_found_error_is_translated() -> None:
    session = MagicMock()
    session.put.return_value = _mock_response(status_code=404, json_data={"message": "Not Found"})
    client = GitHubWriteClient("token123", session=session)

    with pytest.raises(GitHubNotFoundError):
        client.set_labels(_repository(), 1, ["devbot:working"])


def test_write_authentication_error_is_translated() -> None:
    session = MagicMock()
    session.post.return_value = _mock_response(
        status_code=401, json_data={"message": "Bad credentials"}
    )
    client = GitHubWriteClient("token123", session=session)

    with pytest.raises(GitHubAuthenticationError):
        client.create_comment(_repository(), 1, "reason")
