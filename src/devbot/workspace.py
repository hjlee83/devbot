"""Local workspace validation.

Confirms that enabled repositories are actually checked out at their
derived local path. No network access; cloning repositories is out of scope
for this Task.
"""

from __future__ import annotations

from collections.abc import Iterable

from devbot.models import RepositoryConfig


class WorkspaceValidationError(RuntimeError):
    """Raised when an enabled repository's local path is not usable."""


def validate_repository_paths(
    repositories: Iterable[RepositoryConfig],
) -> tuple[RepositoryConfig, ...]:
    """Return enabled repositories whose local path exists as a directory.

    Disabled repositories are skipped without being checked. Raises
    `WorkspaceValidationError` for an enabled repository whose local path is
    missing or not a directory.
    """
    validated: list[RepositoryConfig] = []
    for repository in repositories:
        if not repository.enabled:
            continue
        if not repository.local_path.is_dir():
            raise WorkspaceValidationError(
                f"Repository path does not exist: {repository.local_path} "
                f"({repository.full_name})"
            )
        validated.append(repository)

    return tuple(validated)
