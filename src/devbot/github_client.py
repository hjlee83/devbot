"""GitHub client skeleton.

No real GitHub API calls are made in this Task. This interface exists so
later Tasks (reading Issues/PRs, writing labels, pushing branches, opening
PRs) have a stable seam to implement against.
"""

from __future__ import annotations

from devbot.models import RepositoryConfig


class GitHubClient:
    """Skeleton GitHub API client. Real integration lands in a later Task."""

    def __init__(self, token: str | None = None) -> None:
        self.token = token

    def list_ready_issues(self, repository: RepositoryConfig) -> list[dict[str, object]]:
        raise NotImplementedError("GitHub API integration is out of scope for Task 001")
