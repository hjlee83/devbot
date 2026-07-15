"""Local workspace preparation.

Confirms that a repository is safely usable before an agent runs against
it (exists, is a Git checkout, has no uncommitted changes), and builds the
deterministic branch name and agent prompt for a given Issue. No network
access; cloning repositories is out of scope for this Task.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from devbot.github_client import GitHubIssue
from devbot.models import IssueComment, RepositoryConfig

DEFAULT_PROMPT_TEMPLATE_PATH = Path("prompts/issue-task.md")

_SLUG_INVALID_CHARS = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LENGTH = 40


class WorkspaceValidationError(RuntimeError):
    """Raised when a repository's local path is not usable."""


class NotAGitRepositoryError(WorkspaceValidationError):
    """Raised when the repository's local path is not a Git checkout."""


class DirtyWorkspaceError(WorkspaceValidationError):
    """Raised when the repository's working tree has uncommitted changes."""


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
                f"Repository path does not exist: {repository.local_path} ({repository.full_name})"
            )
        validated.append(repository)

    return tuple(validated)


def _has_uncommitted_changes(path: Path) -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(path),
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(result.stdout.strip())


@dataclass(frozen=True, slots=True)
class WorkspaceStatus:
    """Non-raising counterpart to `ensure_git_workspace_ready()`'s checks
    (Task 019 CP-019-4/CP-019-6): reports what it found instead of raising
    on the first violation, so startup validation and failure diagnostics
    can describe a bad workspace without ever aborting on one."""

    exists: bool
    is_git_repository: bool
    has_uncommitted_changes: bool | None
    changed_files: tuple[str, ...] = ()


def inspect_workspace(repository: RepositoryConfig) -> WorkspaceStatus:
    """Best-effort workspace status for `repository.local_path`.
    `has_uncommitted_changes`/`changed_files` are `None`/`()` when they
    can't be determined (missing path, not a Git checkout, or the `git`
    invocation itself failed) - never raises."""
    if not repository.local_path.is_dir():
        return WorkspaceStatus(exists=False, is_git_repository=False, has_uncommitted_changes=None)
    if not (repository.local_path / ".git").exists():
        return WorkspaceStatus(exists=True, is_git_repository=False, has_uncommitted_changes=None)

    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=str(repository.local_path),
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, OSError):
        return WorkspaceStatus(exists=True, is_git_repository=True, has_uncommitted_changes=None)

    changed_files = tuple(
        line[3:] if len(line) > 3 else line for line in result.stdout.splitlines() if line.strip()
    )
    return WorkspaceStatus(
        exists=True,
        is_git_repository=True,
        has_uncommitted_changes=bool(changed_files),
        changed_files=changed_files,
    )


def ensure_git_workspace_ready(repository: RepositoryConfig) -> None:
    """Validate that `repository.local_path` is safe for an agent to run in.

    Checks, in order: the path exists, it is a Git checkout (`.git` is
    present), and its working tree has no uncommitted changes. Raises on
    the first violation found.
    """
    if not repository.local_path.is_dir():
        raise WorkspaceValidationError(
            f"Repository path does not exist: {repository.local_path} ({repository.full_name})"
        )

    if not (repository.local_path / ".git").exists():
        raise NotAGitRepositoryError(
            f"Not a Git repository: {repository.local_path} ({repository.full_name})"
        )

    if _has_uncommitted_changes(repository.local_path):
        raise DirtyWorkspaceError(
            f"Workspace has uncommitted changes: {repository.local_path} ({repository.full_name})"
        )


def generate_branch_name(repository: RepositoryConfig, issue_number: int, title: str) -> str:
    """Build a deterministic, Git-ref-safe branch name for an Issue.

    The same repository/issue_number/title always produce the same name;
    the title is slugified (lowercased, non-alphanumeric runs collapsed to
    a single `-`, truncated) so the result is a valid branch name
    regardless of what characters the Issue title contains.
    """
    slug = _SLUG_INVALID_CHARS.sub("-", title.lower()).strip("-")
    slug = slug[:_MAX_SLUG_LENGTH].strip("-") or "issue"
    return f"devbot/{repository.repo}-{issue_number}-{slug}"


def build_agent_prompt(
    repository: RepositoryConfig,
    issue: GitHubIssue,
    unprocessed_comments: Sequence[IssueComment] = (),
    *,
    template_path: Path | str = DEFAULT_PROMPT_TEMPLATE_PATH,
) -> str:
    """Render `prompts/issue-task.md` for `issue`, including any unprocessed
    comments. Callers are responsible for deciding which comments count as
    "unprocessed"; this function only renders what it is given."""
    template = Path(template_path).read_text(encoding="utf-8")

    if unprocessed_comments:
        comments_section = "\n\n".join(
            f"- {comment.author}: {comment.body}" for comment in unprocessed_comments
        )
    else:
        comments_section = "(none)"

    return (
        template.replace("{{owner}}", repository.owner)
        .replace("{{repo}}", repository.repo)
        .replace("{{issue_number}}", str(issue.number))
        .replace("{{issue_title}}", issue.title)
        .replace("{{issue_body}}", issue.body)
        .replace("{{unprocessed_comments}}", comments_section)
    )
