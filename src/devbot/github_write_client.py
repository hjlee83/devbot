"""GitHub REST API write client.

Implements the literal writes DevBot needs to advance an Issue and deliver
an implementation: replacing an Issue's full label set, posting a comment,
and opening a pull request. This client performs no validation of *which*
writes make sense; callers (`devbot.issue_state`, `devbot.delivery`)
decide that and call this client only once a transition or delivery step
has been validated.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import requests

from devbot.github_client import (
    GITHUB_API_BASE_URL,
    GitHubClientError,
    _raise_for_status,
)
from devbot.github_retry import GitHubRetryConfig, execute_with_github_retry
from devbot.models import RepositoryConfig


@dataclass(frozen=True, slots=True)
class PullRequestInfo:
    """A created pull request, per `POST /repos/{owner}/{repo}/pulls`."""

    number: int
    html_url: str


@dataclass(frozen=True, slots=True)
class MergePullRequestResult:
    """A successful pull request merge result."""

    sha: str
    merged: bool
    message: str


@dataclass(frozen=True, slots=True)
class IssueInfo:
    """A created Issue, per `POST /repos/{owner}/{repo}/issues` (Task 040)."""

    number: int
    html_url: str


@dataclass(frozen=True, slots=True)
class ReleaseInfo:
    """A created GitHub Release, per `POST /repos/{owner}/{repo}/releases`
    (Task 049)."""

    id: int
    tag_name: str
    html_url: str


class GitHubWriteClient:
    """Minimal authenticated GitHub REST API write client."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = GITHUB_API_BASE_URL,
        session: requests.Session | None = None,
        retry_config: GitHubRetryConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self._session = session or requests.Session()
        self.retry_config = retry_config or GitHubRetryConfig()
        self.logger = logger

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
        }

    def _post(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = execute_with_github_retry(
            lambda: self._session.post(
                f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
            ),
            config=self.retry_config,
            endpoint_category="write",
            logger=self.logger,
        )
        _raise_for_status(response)
        return response

    def _put(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = execute_with_github_retry(
            lambda: self._session.put(
                f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
            ),
            config=self.retry_config,
            endpoint_category="write",
            logger=self.logger,
        )
        _raise_for_status(response)
        return response

    def _patch(self, path: str, json: dict[str, Any]) -> requests.Response:
        response = execute_with_github_retry(
            lambda: self._session.patch(
                f"{self.base_url}{path}", headers=self._headers(), json=json, timeout=30
            ),
            config=self.retry_config,
            endpoint_category="write",
            logger=self.logger,
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

    def create_issue(self, repository: RepositoryConfig, *, title: str, body: str) -> IssueInfo:
        """Create a new Issue (Task 040 - the Goal Executor's Task Issue)."""
        payload = self._post(
            f"/repos/{repository.owner}/{repository.repo}/issues",
            json={"title": title, "body": body},
        ).json()
        return IssueInfo(number=payload["number"], html_url=payload["html_url"])

    def create_branch(self, repository: RepositoryConfig, *, branch: str, base_sha: str) -> None:
        """Create a new branch ref pointing at `base_sha`
        (`POST /repos/{owner}/{repo}/git/refs`, Task 040)."""
        self._post(
            f"/repos/{repository.owner}/{repository.repo}/git/refs",
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )

    def create_file(
        self,
        repository: RepositoryConfig,
        *,
        branch: str,
        path: str,
        content: str,
        message: str,
    ) -> None:
        """Create a new file at `path` on `branch` as a single commit
        (`PUT /repos/{owner}/{repo}/contents/{path}`, Task 040 - used for the
        Goal Executor's draft Task contract, so no local git checkout
        manipulation is needed to write it)."""
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        self._put(
            f"/repos/{repository.owner}/{repository.repo}/contents/{path}",
            json={"message": message, "content": encoded, "branch": branch},
        )

    def dispatch_workflow(
        self,
        repository: RepositoryConfig,
        workflow_file: str,
        *,
        ref: str,
        inputs: dict[str, str],
    ) -> None:
        """Trigger a `workflow_dispatch` event for `workflow_file` (e.g.
        `release.yml`) on `ref`, with `inputs` as the workflow's declared
        `workflow_dispatch.inputs` (Task 037) - the CI-driven release path,
        which builds artifacts and runs its own tag/Release creation inside
        the workflow run. `create_release` (Task 049) is the second,
        deliberate direct path added on top of an already externally
        prepared and tagged version - see `docs/07-decisions.md`."""
        self._post(
            f"/repos/{repository.owner}/{repository.repo}/actions/workflows/"
            f"{workflow_file}/dispatches",
            json={"ref": ref, "inputs": inputs},
        )

    def create_release(
        self,
        repository: RepositoryConfig,
        *,
        tag_name: str,
        target_commitish: str,
        name: str,
        body: str,
    ) -> ReleaseInfo:
        """Create a published (non-draft, non-prerelease) GitHub Release for
        `tag_name` (`POST /repos/{owner}/{repo}/releases`, Task 049).

        If `tag_name` does not already exist as a Git ref, GitHub creates a
        *lightweight* tag pointing at `target_commitish` as a side effect of
        this call. `devbot.release_publish` never relies on that - it always
        creates and pushes an *annotated* tag itself first (matching
        `.github/workflows/release.yml`'s own convention), so this call only
        ever targets a tag that already exists."""
        payload = self._post(
            f"/repos/{repository.owner}/{repository.repo}/releases",
            json={
                "tag_name": tag_name,
                "target_commitish": target_commitish,
                "name": name,
                "body": body,
                "draft": False,
                "prerelease": False,
            },
        ).json()
        return ReleaseInfo(
            id=payload["id"], tag_name=payload["tag_name"], html_url=payload["html_url"]
        )

    def merge_pull_request(
        self,
        repository: RepositoryConfig,
        pull_request_number: int,
        *,
        expected_head_sha: str,
        commit_title: str,
        commit_message: str = "",
        merge_method: str = "merge",
    ) -> MergePullRequestResult:
        """Merge an eligible pull request."""
        payload = self._put(
            f"/repos/{repository.owner}/{repository.repo}/pulls/{pull_request_number}/merge",
            json={
                "sha": expected_head_sha,
                "commit_title": commit_title,
                "commit_message": commit_message,
                "merge_method": merge_method,
            },
        ).json()
        return MergePullRequestResult(
            sha=payload["sha"], merged=payload["merged"], message=payload["message"]
        )


__all__ = [
    "GitHubClientError",
    "GitHubWriteClient",
    "IssueInfo",
    "MergePullRequestResult",
    "PullRequestInfo",
]
