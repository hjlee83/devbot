"""GitHub REST API write client.

Implements the literal writes DevBot needs to advance an Issue and deliver
an implementation: replacing an Issue's full label set, posting a comment,
and opening a pull request. This client performs no validation of *which*
writes make sense; callers (`devbot.issue_state`, `devbot.delivery`)
decide that and call this client only once a transition or delivery step
has been validated.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests

from devbot.github_client import (
    GITHUB_API_BASE_URL,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubNotFoundError,
)
from devbot.models import RepositoryConfig


def _error_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text
    if isinstance(payload, dict) and "message" in payload:
        return str(payload["message"])
    return response.text


def _raise_for_status(response: requests.Response) -> None:
    if response.ok:
        return

    message = _error_message(response)
    status = response.status_code
    if status == 401:
        raise GitHubAuthenticationError(f"GitHub authentication failed: {message}")
    if status == 404:
        raise GitHubNotFoundError(f"GitHub resource not found: {message}")
    raise GitHubAPIError(f"GitHub API error {status}: {message}")


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """A created pull request, per `POST /repos/{owner}/{repo}/pulls`."""

    number: int
    html_url: str


class GitHubWriteClient:
    """Minimal authenticated GitHub REST API write client."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = GITHUB_API_BASE_URL,
        session: requests.Session | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _post(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = self._session.post(
            f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
        )
        _raise_for_status(response)
        return response

    def _put(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = self._session.put(
            f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
        )
        _raise_for_status(response)
        return response

    def _patch(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = self._session.patch(
            f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
        )
        _raise_for_status(response)
        return response

    def set_labels(
        self, repository: RepositoryConfig, issue_number: int, labels: Sequence[str]
    ) -> None:
        """Replace the full label set on an Issue."""
        self._put(
            f"/repos/{repository.owner}/{repository.repo}/issues/{issue_number}/labels",
            json={"labels": list(labels)},
        )

    def set_pull_request_labels(
        self, repository: RepositoryConfig, pull_request_number: int, labels: Sequence[str]
    ) -> None:
        """Replace the full label set on a Pull Request.

        GitHub exposes PR labels through the Issues API because every PR
        is also an issue conversation. Keeping this as a named method lets
        callers make PR-state writes without pretending they are changing
        the linked Task Issue.
        """
        self.set_labels(repository, pull_request_number, labels)

    def create_comment(self, repository: RepositoryConfig, issue_number: int, body: str) -> None:
        """Post a new comment on an Issue."""
        self._post(
            f"/repos/{repository.owner}/{repository.repo}/issues/{issue_number}/comments",
            json={"body": body},
        )

    def update_comment(self, repository: RepositoryConfig, comment_id: int, body: str) -> None:
        """Replace an existing Issue/PR comment's full body (used by
        `devbot.timeline` to keep a single Timeline comment per Issue up to
        date instead of posting a new comment per event)."""
        self._patch(
            f"/repos/{repository.owner}/{repository.repo}/issues/comments/{comment_id}",
            json={"body": body},
        )

    def add_reaction_to_comment(
        self, repository: RepositoryConfig, comment_id: int, *, content: str
    ) -> None:
        """React to an Issue/PR conversation comment (used by
        `devbot.rework` to mark a `@devbot` comment processed)."""
        self._post(
            f"/repos/{repository.owner}/{repository.repo}/issues/comments/{comment_id}/reactions",
            json={"content": content},
        )

    def create_pull_request(
        self, repository: RepositoryConfig, *, title: str, body: str, head: str, base: str
    ) -> PullRequestInfo:
        """Open a pull request from `head` into `base`."""
        payload = self._post(
            f"/repos/{repository.owner}/{repository.repo}/pulls",
            json={"title": title, "body": body, "head": head, "base": base},
        ).json()
        return PullRequestInfo(number=payload["number"], html_url=payload["html_url"])


__all__ = ["GitHubClientError", "GitHubWriteClient", "PullRequestInfo"]
