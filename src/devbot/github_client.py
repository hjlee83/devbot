"""GitHub REST API read client.

Implements authenticated read-only operations against the GitHub REST API:
the authenticated user, and Issue listing with state/label filters and
pagination. No write operations (labels, comments, PRs) are exposed here;
those land in a later Task.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import requests

from devbot.github_retry import GitHubRetryConfig, execute_with_github_retry
from devbot.models import RepositoryConfig
from devbot.release import PullRequestMetadata as ReleasePullRequestMetadata

GITHUB_API_BASE_URL = "https://api.github.com"
DEFAULT_PER_PAGE = 100


class GitHubClientError(RuntimeError):
    """Base class for errors raised by `GitHubClient`."""


class GitHubAuthenticationError(GitHubClientError):
    """Raised when GitHub rejects credentials or permissions (401/403)."""


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


@dataclass(frozen=True, slots=True)
class PullRequestComment:
    """A single conversation comment on an Issue or PR, per
    `GET /repos/{owner}/{repo}/issues/{number}/comments`.

    `reactions` is the raw per-content reaction count map (e.g.
    `{"eyes": 1}`); this module does not decide what any reaction means —
    callers (e.g. `devbot.rework`) interpret that convention."""

    id: int
    author: str
    body: str
    created_at: datetime
    reactions: dict[str, int]


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A GitHub Pull Request, per `GET /repos/{owner}/{repo}/pulls`."""

    number: int
    head_ref: str
    head_sha: str
    body: str
    html_url: str
    labels: tuple[str, ...] = ()
    merged: bool = False


@dataclass(frozen=True, slots=True)
class PullRequestDetail:
    """A single Pull Request's full metadata, per
    `GET /repos/{owner}/{repo}/pulls/{number}` - the only endpoint that
    returns `merge_commit_sha`, `merged_at`, and `body`/`html_url`
    together (Task 052; `list_pull_requests`'s `PullRequest` and
    `get_commit_pull_request_metadata`'s `PullRequestMetadata` each carry
    only a subset). `head_sha`, `state`, and `author_login` were added in
    Task 054 - the current head commit of an open PR (not
    `merge_commit_sha`, which is only set once merged), open/closed
    state, and the PR author, needed for stale-head and self-approval
    checks before submitting a review."""

    number: int
    html_url: str
    body: str
    head_ref: str
    head_sha: str
    base_ref: str
    state: str
    merged: bool
    merge_commit_sha: str | None
    merged_at: datetime | None
    author_login: str


@dataclass(frozen=True, slots=True)
class GitHubReleaseAsset:
    """A single Release asset, per `GET /repos/{owner}/{repo}/releases`."""

    id: int
    name: str
    size: int


@dataclass(frozen=True, slots=True)
class GitHubRelease:
    """A GitHub Release, per `GET /repos/{owner}/{repo}/releases`."""

    id: int
    tag_name: str
    target_commitish: str
    name: str
    body: str
    draft: bool
    prerelease: bool
    html_url: str
    assets: tuple[GitHubReleaseAsset, ...]
    published_at: datetime | None


@dataclass(frozen=True, slots=True)
class WorkflowRun:
    """A GitHub Actions workflow run, per
    `GET /repos/{owner}/{repo}/actions/runs`."""

    id: int
    name: str
    status: str
    conclusion: str | None
    html_url: str
    created_at: datetime
    head_sha: str
    event: str


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
    if status in (401, 403):
        raise GitHubAuthenticationError(f"GitHub authentication failed: {message}")
    if status == 404:
        raise GitHubNotFoundError(f"GitHub resource not found: {message}")
    raise GitHubAPIError(f"GitHub API error {status}: {message}")


def _parse_issue(repository: str, raw: dict[str, Any]) -> GitHubIssue:
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label) for label in raw.get("labels", [])
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


def _parse_comment(raw: dict[str, Any]) -> PullRequestComment:
    reactions_raw = raw.get("reactions") or {}
    reactions = {
        key: int(value) for key, value in reactions_raw.items() if key not in ("url", "total_count")
    }
    return PullRequestComment(
        id=raw["id"],
        author=raw["user"]["login"],
        body=raw.get("body") or "",
        created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
        reactions=reactions,
    )


def _parse_release(raw: dict[str, Any]) -> GitHubRelease:
    published_at = raw.get("published_at")
    assets = tuple(
        GitHubReleaseAsset(id=asset["id"], name=asset["name"], size=asset["size"])
        for asset in raw.get("assets", [])
    )
    return GitHubRelease(
        id=raw["id"],
        tag_name=raw["tag_name"],
        target_commitish=raw["target_commitish"],
        name=raw.get("name") or "",
        body=raw.get("body") or "",
        draft=bool(raw.get("draft")),
        prerelease=bool(raw.get("prerelease")),
        html_url=raw["html_url"],
        assets=assets,
        published_at=(
            datetime.fromisoformat(published_at.replace("Z", "+00:00")) if published_at else None
        ),
    )


def _parse_workflow_run(raw: dict[str, Any]) -> WorkflowRun:
    return WorkflowRun(
        id=raw["id"],
        name=raw.get("name") or "",
        status=raw["status"],
        conclusion=raw.get("conclusion"),
        html_url=raw["html_url"],
        created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
        head_sha=raw["head_sha"],
        event=raw.get("event") or "",
    )


def _parse_release_pull_request_metadata(raw: dict[str, Any]) -> ReleasePullRequestMetadata | None:
    """Parse one item of `GET /repos/{owner}/{repo}/commits/{sha}/pulls`
    into `devbot.release.PullRequestMetadata`. Returns `None` for a payload
    missing `base`/`merge_commit_sha` - a PR that has not merged has no
    `merge_commit_sha`, so it cannot be a release source (Task 037)."""
    base = raw.get("base")
    merge_commit_sha = raw.get("merge_commit_sha")
    if not isinstance(base, dict) or not merge_commit_sha:
        return None
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label) for label in raw.get("labels", [])
    )
    return ReleasePullRequestMetadata(
        number=raw["number"],
        title=str(raw.get("title") or ""),
        labels=labels,
        merged=bool(raw.get("merged") or raw.get("merged_at")),
        base_ref=str(base["ref"]),
        merge_commit_sha=str(merge_commit_sha),
    )


def _parse_pull_request(raw: dict[str, Any]) -> PullRequest:
    labels = tuple(
        label["name"] if isinstance(label, dict) else str(label) for label in raw.get("labels", [])
    )
    return PullRequest(
        number=raw["number"],
        head_ref=raw["head"]["ref"],
        head_sha=raw["head"]["sha"],
        body=raw.get("body") or "",
        html_url=raw["html_url"],
        labels=labels,
        merged=bool(raw.get("merged") or raw.get("merged_at")),
    )


class GitHubClient:
    """Read-only GitHub REST API client. No write operations are exposed."""

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

    def _get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        accept: str = "application/vnd.github+json",
    ) -> requests.Response:
        response = execute_with_github_retry(
            lambda: self._session.get(
                f"{self.base_url}{path}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Accept": accept,
                },
                params=params,
                timeout=30,
            ),
            config=self.retry_config,
            endpoint_category="read",
            logger=self.logger,
        )
        _raise_for_status(response)
        return response

    def get_authenticated_user(self) -> GitHubUser:
        """Return the user identified by this client's token (`GET /user`)."""
        payload = self._get("/user").json()
        return GitHubUser(login=payload["login"], id=payload["id"])

    def get_issue(self, repository: RepositoryConfig, issue_number: int) -> GitHubIssue:
        """Fetch a single Issue by number (`GET /repos/{owner}/{repo}/issues/{number}`)."""
        payload = self._get(
            f"/repos/{repository.owner}/{repository.repo}/issues/{issue_number}"
        ).json()
        return _parse_issue(repository.full_name, payload)

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

    def list_issue_comments(
        self,
        repository: RepositoryConfig,
        issue_number: int,
        *,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[PullRequestComment]:
        """List every conversation comment on an Issue or PR, following
        pagination to completion."""
        comments: list[PullRequestComment] = []
        page = 1
        while True:
            raw_page = self._get(
                f"/repos/{repository.owner}/{repository.repo}/issues/{issue_number}/comments",
                params={"page": page, "per_page": per_page},
            ).json()

            comments.extend(_parse_comment(raw_comment) for raw_comment in raw_page)

            if len(raw_page) < per_page:
                break
            page += 1

        return comments

    def list_pull_requests(
        self,
        repository: RepositoryConfig,
        *,
        state: str = "open",
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[PullRequest]:
        """List Pull Requests for `repository`, following pagination to
        completion."""
        pull_requests: list[PullRequest] = []
        page = 1
        while True:
            raw_page = self._get(
                f"/repos/{repository.owner}/{repository.repo}/pulls",
                params={"state": state, "page": page, "per_page": per_page},
            ).json()

            pull_requests.extend(_parse_pull_request(raw_pr) for raw_pr in raw_page)

            if len(raw_page) < per_page:
                break
            page += 1

        return pull_requests

    def get_pull_request(self, repository: RepositoryConfig, number: int) -> PullRequestDetail:
        """Fetch one Pull Request's full metadata
        (`GET /repos/{owner}/{repo}/pulls/{number}`, Task 052)."""
        payload = self._get(f"/repos/{repository.owner}/{repository.repo}/pulls/{number}").json()
        merged_at_raw = payload.get("merged_at")
        return PullRequestDetail(
            number=payload["number"],
            html_url=payload["html_url"],
            body=payload.get("body") or "",
            head_ref=payload["head"]["ref"],
            head_sha=payload["head"]["sha"],
            base_ref=payload["base"]["ref"],
            state=payload["state"],
            merged=bool(merged_at_raw),
            merge_commit_sha=payload.get("merge_commit_sha"),
            author_login=payload["user"]["login"],
            merged_at=(
                datetime.fromisoformat(merged_at_raw.replace("Z", "+00:00"))
                if merged_at_raw
                else None
            ),
        )

    def list_check_runs_for_ref(
        self,
        repository: RepositoryConfig,
        ref: str,
        *,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[dict[str, Any]]:
        """List Check Runs for a commit ref, following pagination."""
        check_runs: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = self._get(
                f"/repos/{repository.owner}/{repository.repo}/commits/{ref}/check-runs",
                params={"page": page, "per_page": per_page},
            ).json()
            raw_page = payload.get("check_runs", [])
            check_runs.extend(raw_page)

            if len(raw_page) < per_page:
                break
            page += 1

        return check_runs

    def list_releases(
        self, repository: RepositoryConfig, *, per_page: int = DEFAULT_PER_PAGE
    ) -> list[GitHubRelease]:
        """List every Release (draft, prerelease, and stable), following
        pagination to completion (Task 037)."""
        releases: list[GitHubRelease] = []
        page = 1
        while True:
            raw_page = self._get(
                f"/repos/{repository.owner}/{repository.repo}/releases",
                params={"page": page, "per_page": per_page},
            ).json()

            releases.extend(_parse_release(raw_release) for raw_release in raw_page)

            if len(raw_page) < per_page:
                break
            page += 1

        return releases

    def get_release_by_tag(
        self, repository: RepositoryConfig, tag: str
    ) -> GitHubRelease | None:
        """Fetch a Release by tag name, or `None` if no Release exists for
        that tag (`GET /repos/{owner}/{repo}/releases/tags/{tag}`)."""
        try:
            payload = self._get(
                f"/repos/{repository.owner}/{repository.repo}/releases/tags/{tag}"
            ).json()
        except GitHubNotFoundError:
            return None
        return _parse_release(payload)

    def get_commit_sha(self, repository: RepositoryConfig, ref: str) -> str:
        """Resolve a branch, tag, or SHA to a full commit SHA
        (`GET /repos/{owner}/{repo}/commits/{ref}`)."""
        payload = self._get(f"/repos/{repository.owner}/{repository.repo}/commits/{ref}").json()
        return str(payload["sha"])

    def list_commits(
        self,
        repository: RepositoryConfig,
        *,
        sha: str,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = 100,
    ) -> list[str]:
        """List commit SHAs reachable from `sha`, newest first, following
        pagination up to `max_pages` (`GET /repos/{owner}/{repo}/commits`)."""
        shas: list[str] = []
        page = 1
        while page <= max_pages:
            raw_page = self._get(
                f"/repos/{repository.owner}/{repository.repo}/commits",
                params={"sha": sha, "page": page, "per_page": per_page},
            ).json()

            shas.extend(str(commit["sha"]) for commit in raw_page)

            if len(raw_page) < per_page:
                break
            page += 1

        return shas

    def compare_commits(
        self, repository: RepositoryConfig, base: str, head: str
    ) -> list[str]:
        """List commit SHAs strictly after `base` up to and including `head`,
        oldest first (`GET /repos/{owner}/{repo}/compare/{base}...{head}`)."""
        payload = self._get(
            f"/repos/{repository.owner}/{repository.repo}/compare/{base}...{head}"
        ).json()
        return [str(commit["sha"]) for commit in payload.get("commits", [])]

    def get_commit_pull_request_metadata(
        self, repository: RepositoryConfig, sha: str
    ) -> ReleasePullRequestMetadata | None:
        """Return the merged Pull Request associated with a commit as
        `devbot.release.PullRequestMetadata` - the shape release policy
        (`release_increment_for_pr`, release note generation) consumes -
        or `None` if the commit has no associated Pull Request
        (`GET /repos/{owner}/{repo}/commits/{sha}/pulls`, Task 037)."""
        raw_page = self._get(
            f"/repos/{repository.owner}/{repository.repo}/commits/{sha}/pulls"
        ).json()
        for raw_pr in raw_page:
            metadata = _parse_release_pull_request_metadata(raw_pr)
            if metadata is not None:
                return metadata
        return None

    def list_workflow_runs(
        self,
        repository: RepositoryConfig,
        workflow_file: str,
        *,
        event: str | None = None,
        created: str | None = None,
        head_sha: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
    ) -> list[WorkflowRun]:
        """List runs of `workflow_file` (e.g. `release.yml`), most recent
        first (`GET /repos/{owner}/{repo}/actions/workflows/{workflow_file}/runs`).

        `head_sha` narrows server-side to runs whose head commit exactly
        matches - the precise, workflow-scoped alternative to
        `list_check_runs_for_ref` (Task 039), which returns check runs from
        *every* workflow triggered for a commit, not just `workflow_file`."""
        params: dict[str, Any] = {"per_page": per_page}
        if event is not None:
            params["event"] = event
        if created is not None:
            params["created"] = created
        if head_sha is not None:
            params["head_sha"] = head_sha
        payload = self._get(
            f"/repos/{repository.owner}/{repository.repo}/actions/workflows/"
            f"{workflow_file}/runs",
            params=params,
        ).json()
        return [_parse_workflow_run(raw_run) for raw_run in payload.get("workflow_runs", [])]

    def get_workflow_run(self, repository: RepositoryConfig, run_id: int) -> WorkflowRun:
        """Fetch a single workflow run by id
        (`GET /repos/{owner}/{repo}/actions/runs/{run_id}`)."""
        payload = self._get(
            f"/repos/{repository.owner}/{repository.repo}/actions/runs/{run_id}"
        ).json()
        return _parse_workflow_run(payload)

    def download_release_asset(self, repository: RepositoryConfig, asset_id: int) -> bytes:
        """Download a Release asset's raw bytes
        (`GET /repos/{owner}/{repo}/releases/assets/{asset_id}`)."""
        response = self._get(
            f"/repos/{repository.owner}/{repository.repo}/releases/assets/{asset_id}",
            accept="application/octet-stream",
        )
        return response.content

    def get_branch_ref(self, repository: RepositoryConfig, branch: str) -> str | None:
        """Resolve a branch to its current head commit SHA, or `None` if the
        branch does not exist
        (`GET /repos/{owner}/{repo}/git/ref/heads/{branch}`, Task 040)."""
        try:
            payload = self._get(
                f"/repos/{repository.owner}/{repository.repo}/git/ref/heads/{branch}"
            ).json()
        except GitHubNotFoundError:
            return None
        return str(payload["object"]["sha"])

    def get_file_content(
        self, repository: RepositoryConfig, path: str, *, ref: str
    ) -> str | None:
        """Fetch a text file's decoded content at `ref`, or `None` if it does
        not exist there (`GET /repos/{owner}/{repo}/contents/{path}?ref=...`,
        Task 040 - used to idempotently detect an already-created draft
        contract before writing one)."""
        try:
            payload = self._get(
                f"/repos/{repository.owner}/{repository.repo}/contents/{path}",
                params={"ref": ref},
            ).json()
        except GitHubNotFoundError:
            return None
        if payload.get("encoding") != "base64":
            raise GitHubAPIError(
                f"unexpected content encoding for {path!r}: {payload.get('encoding')!r}"
            )
        return base64.b64decode(payload["content"]).decode("utf-8")
