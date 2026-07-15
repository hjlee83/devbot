"""Host-managed workspace preparation (Task 023).

DevBot, not the Agent, owns discovering and preparing the Git workspace an
Implementer/Rework Job runs in: resolving the Planner-owned Task branch and
linked Pull Request, synchronizing the remote, and checking out an isolated
Git worktree dedicated to that Job. `WorktreeManager.prepare()` returns a
`PreparedWorkspace` whose `repository.local_path` points at that worktree -
`devbot.polling` runs the Agent and delivery against it instead of the
operator's own checkout, so a Job never needs its own `git fetch`, `gh`, or
`curl`, and never depends on the operator checkout's current branch or
uncommitted files.

Layout: `<workspace_root>/.devbot-worktrees/<repo>/issue-<issue_number>`
(Scope §3's recommended layout, keyed by the GitHub Issue number - the one
identifier every Job always has, unlike the Task number).

Lifecycle (Scope §8): a worktree is created before Agent execution, reused
only when repository/Issue/branch all match, preserved on Job failure for
diagnosis (this module never removes a worktree except via the explicit
`cleanup()` call), and rejected as a conflict when an unrelated, dirty
worktree already occupies the target path.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path

from devbot.github_client import GitHubIssue, PullRequest
from devbot.models import RepositoryConfig
from devbot.workspace import generate_branch_name

_WORKTREE_DIRNAME = ".devbot-worktrees"


class WorkspacePreparationFailure(StrEnum):
    """Explicit workspace-preparation failure categories (Scope §9's "at
    minimum distinguish" list) - finer-grained than the job-level
    `FailureCategory.WORKSPACE_PREPARATION_FAILED` (`devbot.models`), which
    every one of these maps to for daemon-wide retry/recovery purposes."""

    REMOTE_SYNC_FAILED = "remote_sync_failed"
    LINKED_BRANCH_MISSING = "linked_branch_missing"
    BRANCH_PR_MISMATCH = "branch_pr_mismatch"
    WORKTREE_CREATION_FAILED = "worktree_creation_failed"
    WORKTREE_CONFLICT = "worktree_conflict"
    WORKSPACE_DIRTY = "workspace_dirty"


class WorkspacePreparationError(RuntimeError):
    """Raised by `WorktreeManager.prepare()`/`cleanup()` for any failure
    that must be resolved before an Agent runs (Scope §9). Always carries an
    explicit `category`."""

    def __init__(self, category: WorkspacePreparationFailure, message: str) -> None:
        super().__init__(message)
        self.category = category


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    """The outcome of one `WorktreeManager.prepare()` call: everything a Job
    needs to run without ever discovering it itself (Scope §5)."""

    repository: RepositoryConfig
    """`RepositoryConfig` whose `local_path` is the isolated Job worktree -
    not the operator checkout."""
    branch: str
    base_branch: str
    issue_number: int
    pull_request: PullRequest | None
    worktree_path: Path
    reused: bool
    contract_path: str | None = None
    result_path: str | None = None


@dataclass(frozen=True, slots=True)
class _WorktreeEntry:
    """One entry parsed from `git worktree list --porcelain`."""

    path: Path
    branch: str | None
    locked: bool
    prunable: bool


@dataclass(frozen=True, slots=True)
class WorktreeHealthReport:
    """`devbot doctor` / diagnostics view of a repository's Job worktrees
    (Scope §10)."""

    operator_checkout_path: Path
    operator_branch: str | None
    worktree_root: Path
    active: tuple[Path, ...]
    stale: tuple[Path, ...]
    conflicting: tuple[Path, ...]

    @property
    def safe_to_start(self) -> bool:
        """False when an on-disk directory under `worktree_root` is not a
        registered Git worktree - a future `prepare()` for that path would
        fail (Scope §9's `worktree_conflict`)."""
        return not self.conflicting


_CONTRACT_PATH_RE = re.compile(r"Contract:\s*`([^`]+)`")
_RESULT_PATH_RE = re.compile(r"Produce\s*`([^`]+)`")


def parse_contract_path_from_issue_body(body: str) -> str | None:
    """Best-effort extraction of the Task contract path a Planner-rendered
    execution Issue body embeds (`devbot.planner.render_execution_issue_body`
    writes `` - Contract: `tasks/...md` ``). Returns `None` when the body
    does not follow that convention (e.g. a manually authored Issue)."""
    match = _CONTRACT_PATH_RE.search(body)
    return match.group(1) if match else None


def parse_result_path_from_issue_body(body: str) -> str | None:
    """Best-effort extraction of the Result document path a Planner-rendered
    execution Issue body embeds (`` Produce `results/...md`. ``)."""
    match = _RESULT_PATH_RE.search(body)
    return match.group(1) if match else None


def render_prepared_workspace_context(prepared: PreparedWorkspace) -> str:
    """Render the "prepared Agent context" block (Scope §5): authoritative
    branch/PR/Issue/contract/Result identifiers, plus the two explicit
    policy statements the Task 022 daemon attempt was missing - that remote
    discovery is already complete, and that the Agent must not create
    another branch or PR."""
    lines = [
        "## Prepared Workspace (host-managed, Task 023)",
        "",
        f"- Repository path (current working directory): `{prepared.repository.local_path}`",
        f"- Branch (already checked out): `{prepared.branch}`",
        f"- Base branch: `{prepared.base_branch}`",
        f"- Issue: #{prepared.issue_number}",
    ]
    if prepared.pull_request is not None:
        lines.append(f"- Pull Request: #{prepared.pull_request.number}")
    if prepared.contract_path:
        lines.append(f"- Task contract: `{prepared.contract_path}`")
    if prepared.result_path:
        lines.append(f"- Result document: `{prepared.result_path}`")
    lines.extend(
        [
            "",
            "Remote discovery is already complete - do not run `git fetch`, `gh`, "
            "`curl`, or any other network/discovery command. Do not create another "
            "branch or Pull Request; continue on the branch and Pull Request above.",
        ]
    )
    return "\n".join(lines)


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )


def _is_dirty(path: Path) -> bool:
    completed = _run_git(path, "status", "--porcelain")
    return bool(completed.stdout.strip())


def _current_branch(path: Path) -> str | None:
    completed = _run_git(path, "rev-parse", "--abbrev-ref", "HEAD")
    if completed.returncode != 0:
        return None
    branch = completed.stdout.strip()
    return None if branch == "HEAD" else branch


_PORCELAIN_ENTRY_RE = re.compile(r"^worktree (?P<path>.+)$", re.MULTILINE)


def _parse_worktree_list(raw: str) -> list[_WorktreeEntry]:
    entries: list[_WorktreeEntry] = []
    path: Path | None = None
    branch: str | None = None
    locked = False
    prunable = False

    def _flush() -> None:
        if path is not None:
            entries.append(
                _WorktreeEntry(path=path, branch=branch, locked=locked, prunable=prunable)
            )

    for block in raw.split("\n\n"):
        block = block.strip("\n")
        if not block:
            continue
        path = None
        branch = None
        locked = False
        prunable = False
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree ") :]).resolve()
            elif line.startswith("branch "):
                ref = line[len("branch ") :]
                branch = ref.removeprefix("refs/heads/")
            elif line.startswith("locked"):
                locked = True
            elif line.startswith("prunable"):
                prunable = True
        _flush()

    return entries


@dataclass
class WorktreeManager:
    """Prepares, reuses, reports on, and cleans up isolated Job worktrees
    for one `workspace_root` (Scope §3/§8)."""

    workspace_root: Path

    def worktree_path(self, repository: RepositoryConfig, issue_number: int) -> Path:
        return self.worktree_root(repository) / f"issue-{issue_number}"

    def worktree_root(self, repository: RepositoryConfig) -> Path:
        # `.resolve()` (not just `/`-joining): `git worktree list --porcelain`
        # always reports the real, symlink-resolved path (e.g. macOS's
        # `/var` -> `/private/var`), so every path this class computes must
        # be resolved the same way - otherwise a `workspace_root` that
        # happens to sit behind a symlink makes every registered worktree
        # look unregistered (spurious `WORKTREE_CONFLICT`).
        return (self.workspace_root / _WORKTREE_DIRNAME / repository.repo).resolve()

    def _list_worktrees(self, repository: RepositoryConfig) -> list[_WorktreeEntry]:
        completed = _run_git(repository.local_path, "worktree", "list", "--porcelain")
        if completed.returncode != 0:
            raise WorkspacePreparationError(
                WorkspacePreparationFailure.WORKTREE_CREATION_FAILED,
                f"git worktree list failed: {completed.stderr or completed.stdout}",
            )
        return _parse_worktree_list(completed.stdout)

    def _sync_remote(
        self, repository: RepositoryConfig, *, branch: str | None, base_branch: str
    ) -> None:
        """Host-managed remote synchronization (Scope §2): fetch exactly the
        refs a Job's worktree needs - the linked branch (when one already
        exists) and the base branch - before any worktree is created from
        them. Never fetches `branch` for a brand-new Task (Scope §4's
        fallback must not run when the branch does not exist yet)."""
        refs = [base_branch] if branch is None or branch == base_branch else [branch, base_branch]
        completed = _run_git(repository.local_path, "fetch", "origin", *refs)
        if completed.returncode == 0:
            return

        output = completed.stderr or completed.stdout
        if branch is not None and "couldn't find remote ref" in output.casefold():
            raise WorkspacePreparationError(
                WorkspacePreparationFailure.LINKED_BRANCH_MISSING,
                f"linked branch {branch!r} not found on origin: {output}",
            )
        raise WorkspacePreparationError(
            WorkspacePreparationFailure.REMOTE_SYNC_FAILED,
            f"git fetch origin {' '.join(refs)} failed: {output}",
        )

    def _create_or_reuse(
        self,
        repository: RepositoryConfig,
        target: Path,
        branch: str,
        base_branch: str,
        *,
        create_branch: bool,
    ) -> bool:
        entries = self._list_worktrees(repository)
        matching = next((entry for entry in entries if entry.path == target), None)

        if matching is not None and not target.is_dir():
            # Registered with Git but missing on disk - prune the stale
            # registration and fall through to a fresh `worktree add`.
            _run_git(repository.local_path, "worktree", "prune")
            matching = None

        if matching is not None:
            if matching.branch == branch:
                return True  # Scope §8: reuse - same repository/Issue/branch.
            if _is_dirty(target):
                raise WorkspacePreparationError(
                    WorkspacePreparationFailure.WORKTREE_CONFLICT,
                    f"worktree at {target} is on branch {matching.branch!r} (expected "
                    f"{branch!r}) and has uncommitted changes - refusing unsafe reuse",
                )
            # Clean but stale (e.g. the linked PR's branch changed): drop the
            # old registration and recreate on the correct branch.
            _run_git(repository.local_path, "worktree", "remove", "--force", str(target))
        elif target.exists():
            raise WorkspacePreparationError(
                WorkspacePreparationFailure.WORKTREE_CONFLICT,
                f"path exists but is not a registered Git worktree: {target}",
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        if create_branch:
            args = ("worktree", "add", "-b", branch, str(target), f"origin/{base_branch}")
        else:
            args = ("worktree", "add", str(target), branch)
        completed = _run_git(repository.local_path, *args)
        if completed.returncode != 0:
            output = completed.stderr or completed.stdout
            if "already checked out" in output.casefold():
                raise WorkspacePreparationError(
                    WorkspacePreparationFailure.WORKTREE_CONFLICT,
                    f"branch {branch!r} is already checked out elsewhere: {output}",
                )
            raise WorkspacePreparationError(
                WorkspacePreparationFailure.WORKTREE_CREATION_FAILED,
                f"git worktree add failed: {output}",
            )
        return False

    def prepare(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        """Resolve the Job's branch/PR (Scope §1 - `linked_pull_request` is
        whatever the caller already fetched from GitHub, so resolution
        itself always happens before this call, let alone before the
        Agent), synchronize the remote (Scope §2), and create-or-reuse the
        Issue's isolated worktree (Scope §3/§4)."""
        base_branch = repository.default_branch
        if linked_pull_request is not None:
            branch = linked_pull_request.head_ref
            create_branch = False
        else:
            branch = generate_branch_name(repository, issue.number, issue.title)
            create_branch = True

        self._sync_remote(
            repository, branch=None if create_branch else branch, base_branch=base_branch
        )

        target = self.worktree_path(repository, issue.number)
        reused = self._create_or_reuse(
            repository, target, branch, base_branch, create_branch=create_branch
        )

        return PreparedWorkspace(
            repository=replace(repository, local_path=target),
            branch=branch,
            base_branch=base_branch,
            issue_number=issue.number,
            pull_request=linked_pull_request,
            worktree_path=target,
            reused=reused,
            contract_path=parse_contract_path_from_issue_body(issue.body),
            result_path=parse_result_path_from_issue_body(issue.body),
        )

    def cleanup(
        self, repository: RepositoryConfig, issue_number: int, *, force: bool = True
    ) -> None:
        """Explicit cleanup (Scope §8): remove the Issue's worktree. Never
        called automatically by a Job - only after a successful merge or an
        explicit operator/CLI request (`devbot worktree cleanup`)."""
        target = self.worktree_path(repository, issue_number)
        args = ["worktree", "remove", str(target)]
        if force:
            args.append("--force")
        completed = _run_git(repository.local_path, *args)
        if completed.returncode != 0:
            raise WorkspacePreparationError(
                WorkspacePreparationFailure.WORKTREE_CREATION_FAILED,
                f"git worktree remove failed: {completed.stderr or completed.stdout}",
            )
        _run_git(repository.local_path, "worktree", "prune")

    def health(self, repository: RepositoryConfig) -> WorktreeHealthReport:
        """Read-only diagnostic view for `devbot doctor` (Scope §10). Never
        raises, even when the operator checkout itself does not exist yet -
        `devbot doctor` must always finish and print a report."""
        root = self.worktree_root(repository)

        if not repository.local_path.is_dir() or not (repository.local_path / ".git").exists():
            return WorktreeHealthReport(
                operator_checkout_path=repository.local_path,
                operator_branch=None,
                worktree_root=root,
                active=(),
                stale=(),
                conflicting=(),
            )

        operator_branch = _current_branch(repository.local_path)

        try:
            entries = self._list_worktrees(repository)
        except WorkspacePreparationError:
            entries = []
        under_root = [entry for entry in entries if root in entry.path.parents]

        active = tuple(entry.path for entry in under_root if not entry.prunable)
        stale = tuple(entry.path for entry in under_root if entry.prunable)

        registered_paths = {entry.path for entry in under_root}
        on_disk: set[Path] = set()
        if root.is_dir():
            on_disk = {path for path in root.glob("issue-*") if path.is_dir()}
        conflicting = tuple(sorted(on_disk - registered_paths))

        return WorktreeHealthReport(
            operator_checkout_path=repository.local_path,
            operator_branch=operator_branch,
            worktree_root=root,
            active=active,
            stale=stale,
            conflicting=conflicting,
        )
