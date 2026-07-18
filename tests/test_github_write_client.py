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
        commit_title="Merge PR #99: Add feature X",
        commit_message="Merged automatically by DevBot.",
    )

    assert result.sha == "merge-sha"
    assert result.merged is True
    assert result.message == "Pull Request successfully merged"
    args, kwargs = session.put.call_args
    assert args[0].endswith("/repos/someone/myrepo/pulls/99/merge")
    assert kwargs["json"] == {
        "commit_title": "Merge PR #99: Add feature X",
        "commit_message": "Merged automatically by DevBot.",
        "merge_method": "merge",
    }


def test_write_client_exposes_write_operations_only() -> None:
    allowed_public_methods = {
        "add_reaction_to_comment",
        "create_comment",
        "create_pull_request",
        "merge_pull_request",
        "set_labels",
        "set_pull_request_labels",
        "update_comment",
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
