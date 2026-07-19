"""Task 049: Release Publish.

Promotes an already-prepared project version (Task 048:
`pyproject.toml`/`uv.lock`) on `main` into the matching Git tag and GitHub
Release. This module never calculates or mutates a version - it only reads
the version Task 048 already wrote and publishes exactly that.

**A second, deliberate publish path.** Task 037's `release_ops.py` only
ever dispatches `.github/workflows/release.yml`, which builds artifacts and
creates the tag/Release itself inside CI - by design, the only path that
existed until now (see `docs/07-decisions.md`, Task 037 ADR). This module
adds a second, direct path: DevBot's own process creates and pushes an
annotated Git tag, then calls the GitHub Release API directly. This is an
intentional architecture change made for Task 049 - see the updated
`docs/07-decisions.md` entry for the reasoning and how the two paths
coexist.

**Validate before writing.** `preview_release_publish` runs every
precondition (version-source consistency, clean worktree, non-stale main,
non-empty notes, no conflicting tag, no conflicting Release) and returns a
read-only `ReleasePublishPreview` - no external write happens here.
`publish_prepared_release` calls it first and only then decides whether the
call is a no-op (already published), a straight publish, or a safe
resume (tag exists, Release missing).

**Never force, never silently repair.** An existing tag or Release that
disagrees with the verified target commit fails closed
(`ConflictingTagError`/`ConflictingReleaseError`) - it is never moved,
overwritten, or force-pushed. If the tag push succeeds but Release creation
fails, `PartialPublicationError` is raised identifying the pushed tag; the
tag is never deleted automatically, and a later `publish_prepared_release`
call safely completes only the missing Release without recreating or
moving the tag.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from devbot.github_client import GitHubClient, GitHubClientError, GitHubNotFoundError
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.release_ops import local_checkout_is_dirty
from devbot.release_preparation import read_current_version
from devbot.startup import resolve_operator_checkout


class TagState(StrEnum):
    ABSENT = "absent"
    MATCHES_TARGET = "matches_target"
    CONFLICTING = "conflicting"


class ReleaseState(StrEnum):
    ABSENT = "absent"
    MATCHES_TARGET = "matches_target"
    CONFLICTING = "conflicting"


class PublishOutcome(StrEnum):
    PUBLISHED = "published"
    COMPLETED_MISSING_RELEASE = "completed_missing_release"
    ALREADY_PUBLISHED = "already_published"


@dataclass(frozen=True)
class ReleasePublishPreview:
    repository: str
    version: str
    tag: str
    title: str
    target_branch: str
    target_sha: str
    tag_state: TagState
    release_state: ReleaseState


@dataclass(frozen=True)
class ReleasePublishResult:
    version: str
    tag: str
    target_sha: str
    release_url: str | None
    outcome: PublishOutcome


class ReleasePublishError(RuntimeError):
    """Base for a publish that cannot safely proceed. Malformed or
    mismatched version sources instead raise
    `devbot.release_preparation.ReleasePreparationError` subclasses
    directly - reused as-is, not duplicated (Task 048's own version
    consistency check is the single source of truth for that)."""


class DirtyWorktreeError(ReleasePublishError):
    """The local checkout has uncommitted changes."""


class StaleMainError(ReleasePublishError):
    """The local checkout's `main` disagrees with the remote `main` tip."""


class MissingReleaseNotesError(ReleasePublishError):
    """Release notes were empty or not supplied."""


class ConflictingTagError(ReleasePublishError):
    """The canonical tag already exists (locally or remotely) pointing at a
    commit other than the verified target - never moved or overwritten."""


class ConflictingReleaseError(ReleasePublishError):
    """A GitHub Release already exists for the canonical tag but targets a
    commit other than the verified target."""


class PartialPublicationError(ReleasePublishError):
    """The tag was created and pushed, but GitHub Release creation failed.
    The tag is intentionally left in place - re-invoking
    `publish_prepared_release` will detect it and safely complete only the
    missing Release, never recreating or moving the tag."""

    def __init__(self, message: str, *, tag: str, target_sha: str) -> None:
        super().__init__(message)
        self.tag = tag
        self.target_sha = target_sha


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _local_ref_commit(local_path: Path, ref: str) -> str | None:
    """The commit SHA `ref` (a branch or tag name) resolves to in the local
    checkout, dereferencing an annotated tag to its underlying commit, or
    `None` if `ref` does not exist there. Never raises - an unresolvable
    local ref is treated as "unknown", matching `release_ops.
    local_checkout_is_dirty`'s existing precedent that an unusable local
    checkout is not itself a blocker."""
    completed = _run_git(local_path, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def _remote_tag_commit(
    github_client: GitHubClient, repository: RepositoryConfig, tag: str
) -> str | None:
    """The commit SHA `tag` resolves to on GitHub (dereferencing an
    annotated tag automatically, same as any other ref), or `None` if no
    such tag exists remotely."""
    try:
        return github_client.get_commit_sha(repository, tag)
    except GitHubNotFoundError:
        return None


def _resolve_tag_state(
    local_path: Path,
    github_client: GitHubClient,
    repository: RepositoryConfig,
    tag: str,
    target_sha: str,
) -> TagState:
    local_sha = _local_ref_commit(local_path, f"refs/tags/{tag}")
    remote_sha = _remote_tag_commit(github_client, repository, tag)
    observed = {sha for sha in (local_sha, remote_sha) if sha is not None}
    if not observed:
        return TagState.ABSENT
    if observed == {target_sha}:
        return TagState.MATCHES_TARGET
    return TagState.CONFLICTING


def _resolve_release_state(
    github_client: GitHubClient, repository: RepositoryConfig, tag: str, target_sha: str
) -> ReleaseState:
    release = github_client.get_release_by_tag(repository, tag)
    if release is None:
        return ReleaseState.ABSENT
    if release.target_commitish == target_sha:
        return ReleaseState.MATCHES_TARGET
    return ReleaseState.CONFLICTING


def preview_release_publish(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    notes: str,
    *,
    local_checkout_path: Path | None = None,
) -> ReleasePublishPreview:
    """Read-only: validates every publication precondition and returns the
    computed preview. Raises on the first failing precondition - no
    external write ever happens in this function."""
    project_root = local_checkout_path or resolve_operator_checkout()

    version = read_current_version(project_root)
    tag = f"v{version}"

    if local_checkout_is_dirty(project_root):
        raise DirtyWorktreeError(f"{project_root} has uncommitted changes")

    remote_main_sha = github_client.get_commit_sha(repository, repository.default_branch)
    local_main_sha = _local_ref_commit(project_root, repository.default_branch)
    if local_main_sha is not None and local_main_sha != remote_main_sha:
        raise StaleMainError(
            f"local {repository.default_branch} ({local_main_sha}) does not match "
            f"remote {repository.default_branch} ({remote_main_sha})"
        )
    target_sha = remote_main_sha

    if not notes or not notes.strip():
        raise MissingReleaseNotesError("release notes must not be empty")

    tag_state = _resolve_tag_state(project_root, github_client, repository, tag, target_sha)
    if tag_state is TagState.CONFLICTING:
        raise ConflictingTagError(
            f"tag {tag!r} already exists but does not point at the verified target "
            f"{target_sha!r}"
        )

    release_state = _resolve_release_state(github_client, repository, tag, target_sha)
    if release_state is ReleaseState.CONFLICTING:
        raise ConflictingReleaseError(
            f"a GitHub Release for tag {tag!r} already exists but does not target the "
            f"verified target {target_sha!r}"
        )

    return ReleasePublishPreview(
        repository=repository.full_name,
        version=version,
        tag=tag,
        title=tag,
        target_branch=repository.default_branch,
        target_sha=target_sha,
        tag_state=tag_state,
        release_state=release_state,
    )


def _ensure_tag_created_and_pushed(
    local_path: Path,
    github_client: GitHubClient,
    repository: RepositoryConfig,
    tag: str,
    target_sha: str,
) -> None:
    """Idempotent and safe to call unconditionally: creates the local tag
    only if it does not already exist there (at the correct target), and
    pushes only if the *remote* does not already have it (at the correct
    target) - checked independently, not merged into one combined signal.

    This matters because a prior call can fail between the two steps (`git
    tag` succeeds, `git push` fails) - a combined local-or-remote check
    would then see the *local* tag and wrongly conclude the tag was fully
    published, skipping the push and letting `create_release` proceed
    against a tag that does not exist on GitHub (PR #104 review). Checking
    each side on its own guarantees the remote tag genuinely exists at
    `target_sha` before this function returns.

    Never force-creates or force-moves - an unexpected disagreement with
    `target_sha` on either side raises `ConflictingTagError` defensively,
    even though `preview_release_publish` should already have caught it."""
    local_sha = _local_ref_commit(local_path, f"refs/tags/{tag}")
    if local_sha is None:
        tag_result = _run_git(local_path, "tag", "-a", tag, "-m", f"Release {tag}", target_sha)
        if tag_result.returncode != 0:
            raise ReleasePublishError(
                f"git tag failed: {(tag_result.stderr or tag_result.stdout).strip()}"
            )
    elif local_sha != target_sha:
        raise ConflictingTagError(
            f"local tag {tag!r} points at {local_sha!r}, expected {target_sha!r}"
        )

    remote_sha = _remote_tag_commit(github_client, repository, tag)
    if remote_sha is None:
        push_result = _run_git(local_path, "push", "origin", tag)
        if push_result.returncode != 0:
            raise ReleasePublishError(
                f"git push of tag {tag!r} failed: "
                f"{(push_result.stderr or push_result.stdout).strip()}"
            )
    elif remote_sha != target_sha:
        raise ConflictingTagError(
            f"remote tag {tag!r} points at {remote_sha!r}, expected {target_sha!r}"
        )


def publish_prepared_release(
    github_client: GitHubClient,
    write_client: GitHubWriteClient,
    repository: RepositoryConfig,
    notes: str,
    *,
    local_checkout_path: Path | None = None,
) -> ReleasePublishResult:
    """Validates via `preview_release_publish`, then publishes: ensures the
    annotated tag is both created locally and pushed remotely (each
    checked and applied independently - see `_ensure_tag_created_and_pushed`
    for why a combined check is unsafe), then creates the GitHub Release.
    Never force-moves a tag. If tag push succeeds but Release creation
    fails, raises `PartialPublicationError` identifying the pushed tag -
    the tag is never deleted, and a later call safely completes only the
    missing Release."""
    project_root = local_checkout_path or resolve_operator_checkout()
    preview = preview_release_publish(
        github_client, repository, notes, local_checkout_path=project_root
    )

    already_published = (
        preview.tag_state is TagState.MATCHES_TARGET
        and preview.release_state is ReleaseState.MATCHES_TARGET
    )
    if already_published:
        existing_release = github_client.get_release_by_tag(repository, preview.tag)
        release_url = existing_release.html_url if existing_release is not None else None
        return ReleasePublishResult(
            version=preview.version,
            tag=preview.tag,
            target_sha=preview.target_sha,
            release_url=release_url,
            outcome=PublishOutcome.ALREADY_PUBLISHED,
        )

    _ensure_tag_created_and_pushed(
        project_root, github_client, repository, preview.tag, preview.target_sha
    )

    try:
        release_info = write_client.create_release(
            repository,
            tag_name=preview.tag,
            target_commitish=preview.target_sha,
            name=preview.title,
            body=notes,
        )
    except GitHubClientError as exc:
        raise PartialPublicationError(
            f"tag {preview.tag!r} was pushed but Release creation failed: {exc}",
            tag=preview.tag,
            target_sha=preview.target_sha,
        ) from exc

    outcome = (
        PublishOutcome.PUBLISHED
        if preview.tag_state is TagState.ABSENT
        else PublishOutcome.COMPLETED_MISSING_RELEASE
    )
    return ReleasePublishResult(
        version=preview.version,
        tag=preview.tag,
        target_sha=preview.target_sha,
        release_url=release_info.html_url,
        outcome=outcome,
    )

