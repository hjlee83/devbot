"""GitHub REST API read client.

Implements authenticated read-only operations against the GitHub REST API:
the authenticated user, and Issue listing with state/label filters and
pagination. No write operations (labels, comments, PRs) are exposed here;
those land in a later Task.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from devbot.models import RepositoryConfig

GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_PER_PAGE = 100


class GitHubClientError(RuntimeError):
    """Base class for errors raised by `GitHubClient`."""


class GitHubAuthenticationError(GitHubClientError):
    """Raised when GitHub rejects the credentials (401)."""


class GitHubNotFoundError(GitHubClientError):
    """Raised when the requested resource does not exist (404)."""


class GitHubAPIError(GitHubClientError):
    """Raised for any other non-2xx GitHub API response."""


@dataclass(frozen=True, slots=True)
class GitHubUser:
    """The authenticated GitHub user, per `GET /user`."""

    login: str
    id: int


@dataclass(frozen=True, slots=True)
class GitHubIssue:
    """A GitHub Issue, per `GET /repos/{owner}/{repo}/issues`."""

    repository: str
    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]
    created_at: datetime


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


def _parse_issue(repository: str, raw: dict[str, Any]) -> GitHubIssue:
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label)
        for label in raw.get("labels", [])
    )
    return GitHubIssue(
        repository=repository,
        number=raw["number"],
        title=raw["title"],
        body=raw.get("body") or "",
        state=raw["state"],
        labels=labels,
        created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
    )


class GitHubClient:
    """Read-only GitHub REST API client. No write operations are exposed."""

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

    def _get(self, path: str, params: dict[str, Any] | None = None) -> requests.Response:
        response = self._session.get(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
            },
            params=params,
            timeout=30,
        )
        _raise_for_status(response)
        return response

    def get_authenticated_user(self) -> GitHubUser:
        """Return the user identified by this client's token (`GET /user`)."""
        payload = self._get("/user").json()
        return GitHubUser(login=payload["login"], id=payload["id"])

    def list_issues(
        self,
        repository: RepositoryConfig,
        *,
        state: str = "open",
        labels: Sequence[str] | None = None,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[GitHubIssue]:
        """List Issues for `repository`, following pagination to completion.

        GitHub's Issues API also returns pull requests; those are filtered
        out so callers only see actual Issues.
        """
        issues: list[GitHubIssue] = []
        page = 1
        while True:
            params: dict[str, Any] = {"state": state, "page": page, "per_page": per_page}
            if labels:
                params["labels"] = ",".join(labels)

            raw_page = self._get(
                f"/repos/{repository.owner}/{repository.repo}/issues",
                params=params,
            ).json()

            issues.extend(
                _parse_issue(repository.full_name, raw_issue)
                for raw_issue in raw_page
                if "pull_request" not in raw_issue
            )

            if len(raw_page) < per_page:
                break
            page += 1

        return issues
