from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import (
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClient,
    GitHubClientError,
    GitHubNotFoundError,
)
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/workspace/myrepo"),
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


def _issue(number: int, *, labels: list[str] | None = None, state: str = "open") -> dict:
    return {
        "number": number,
        "title": f"issue {number}",
        "state": state,
        "labels": [{"name": name} for name in (labels or [])],
        "created_at": "2026-01-01T00:00:00Z",
    }


def _comment(
    comment_id: int, *, body: str = "a comment", reactions: dict[str, int] | None = None
) -> dict:
    return {
        "id": comment_id,
        "user": {"login": "someone"},
        "body": body,
        "created_at": "2026-01-01T00:00:00Z",
        "reactions": {"url": "https://...", "total_count": 0, **(reactions or {})},
    }


def test_get_authenticated_user() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(json_data={"login": "octocat", "id": 42})
    client = GitHubClient("token123", session=session)

    user = client.get_authenticated_user()

    assert user.login == "octocat"
    assert user.id == 42
    session.get.assert_called_once()
    _, kwargs = session.get.call_args
    assert kwargs["headers"]["Authorization"] == "Bearer token123"


def test_get_issue_parses_single_issue() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(
        json_data=_issue(34, labels=["devbot:working"])
    )
    client = GitHubClient("token123", session=session)

    issue = client.get_issue(_repository(), 34)

    assert issue.number == 34
    assert issue.labels == ("devbot:working",)
    args, _kwargs = session.get.call_args
    assert args[0].endswith("/repos/someone/myrepo/issues/34")


def test_list_issues_follows_pagination() -> None:
    session = MagicMock()
    page_1 = [_issue(1), _issue(2)]
    page_2 = [_issue(3)]
    session.get.side_effect = [
        _mock_response(json_data=page_1),
        _mock_response(json_data=page_2),
    ]
    client = GitHubClient("token123", session=session)

    issues = client.list_issues(_repository(), per_page=2)

    assert [issue.number for issue in issues] == [1, 2, 3]
    assert session.get.call_count == 2
    first_call, second_call = session.get.call_args_list
    assert first_call.kwargs["params"]["page"] == 1
    assert second_call.kwargs["params"]["page"] == 2


def test_list_issues_applies_state_and_label_filters() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(json_data=[])
    client = GitHubClient("token123", session=session)

    client.list_issues(_repository(), state="closed", labels=["priority:high", "devbot:ready"])

    _, kwargs = session.get.call_args
    assert kwargs["params"]["state"] == "closed"
    assert kwargs["params"]["labels"] == "priority:high,devbot:ready"


def test_list_issue_comments_follows_pagination_and_parses_reactions() -> None:
    session = MagicMock()
    page_1 = [
        _comment(1, body="looks good"),
        _comment(2, body="@devbot fix", reactions={"eyes": 1}),
    ]
    page_2 = [_comment(3, body="@devbot also this")]
    session.get.side_effect = [
        _mock_response(json_data=page_1),
        _mock_response(json_data=page_2),
    ]
    client = GitHubClient("token123", session=session)

    comments = client.list_issue_comments(_repository(), 42, per_page=2)

    assert [comment.id for comment in comments] == [1, 2, 3]
    assert comments[1].reactions == {"eyes": 1}
    assert comments[2].reactions == {}
    assert session.get.call_count == 2
    first_call = session.get.call_args_list[0]
    assert first_call.args[0].endswith("/repos/someone/myrepo/issues/42/comments")


def _pull_request(
    number: int, *, head_ref: str, head_sha: str = "deadbeef", body: str = ""
) -> dict:
    return {
        "number": number,
        "head": {"ref": head_ref, "sha": head_sha},
        "body": body,
        "html_url": f"https://github.com/someone/myrepo/pull/{number}",
    }


def test_list_pull_requests_follows_pagination_and_parses_head_ref() -> None:
    session = MagicMock()
    page_1 = [
        _pull_request(1, head_ref="devbot/myrepo-1-fix", body="Closes #1"),
        _pull_request(2, head_ref="devbot/myrepo-2-fix", body="Closes #2"),
    ]
    page_2 = [_pull_request(3, head_ref="devbot/myrepo-3-fix", body="Closes #3")]
    session.get.side_effect = [
        _mock_response(json_data=page_1),
        _mock_response(json_data=page_2),
    ]
    client = GitHubClient("token123", session=session)

    pull_requests = client.list_pull_requests(_repository(), per_page=2)

    assert [pr.number for pr in pull_requests] == [1, 2, 3]
    assert pull_requests[0].head_ref == "devbot/myrepo-1-fix"
    assert pull_requests[0].head_sha == "deadbeef"
    assert pull_requests[0].body == "Closes #1"
    assert session.get.call_count == 2
    first_call = session.get.call_args_list[0]
    assert first_call.args[0].endswith("/repos/someone/myrepo/pulls")
    assert first_call.kwargs["params"]["state"] == "open"


def test_github_error_is_translated() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=404, json_data={"message": "Not Found"})
    client = GitHubClient("token123", session=session)

    with pytest.raises(GitHubNotFoundError):
        client.get_authenticated_user()


def test_github_authentication_error_is_translated() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(
        status_code=401, json_data={"message": "Bad credentials"}
    )
    client = GitHubClient("token123", session=session)

    with pytest.raises(GitHubAuthenticationError):
        client.get_authenticated_user()


def test_github_generic_error_is_translated() -> None:
    session = MagicMock()
    session.get.return_value = _mock_response(status_code=500, json_data={"message": "boom"})
    client = GitHubClient("token123", session=session)

    with pytest.raises(GitHubAPIError):
        client.get_authenticated_user()


def test_client_exposes_read_operations_only() -> None:
    allowed_public_methods = {
        "get_authenticated_user",
        "get_issue",
        "list_issues",
        "list_issue_comments",
        "list_pull_requests",
    }
    forbidden_names = {
        "create_issue",
        "update_issue",
        "close_issue",
        "add_label",
        "remove_label",
        "create_comment",
        "create_pull_request",
        "merge_pull_request",
        "create_branch",
        "push",
    }

    public_attrs = {
        name
        for name in dir(GitHubClient)
        if not name.startswith("_") and callable(getattr(GitHubClient, name))
    }

    assert public_attrs == allowed_public_methods
    assert public_attrs.isdisjoint(forbidden_names)
    for name in forbidden_names:
        assert not hasattr(GitHubClient, name)


def test_github_client_error_hierarchy() -> None:
    assert issubclass(GitHubNotFoundError, GitHubClientError)
    assert issubclass(GitHubAuthenticationError, GitHubClientError)
    assert issubclass(GitHubAPIError, GitHubClientError)
