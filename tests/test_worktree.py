import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from devbot.github_client import GitHubIssue, PullRequest
from devbot.models import RepositoryConfig
from devbot.worktree import (
    WorkspacePreparationError,
    WorkspacePreparationFailure,
    WorktreeManager,
    parse_contract_path_from_issue_body,
    parse_result_path_from_issue_body,
    render_prepared_workspace_context,
)


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _git_output(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return completed.stdout


def _init_git_repo(path: Path) -> None:
    """`git init`'s default initial branch name depends on the runner's
    `init.defaultBranch` config - renaming to `main` right after the first
    commit keeps this deterministic (see `tests/test_doctor.py`)."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)
    _run_git("branch", "-m", "main", cwd=path)


def _init_bare_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "--bare", str(path)], check=True, capture_output=True)


def _make_operator_repo(tmp_path: Path, *, name: str = "myrepo") -> tuple[RepositoryConfig, Path]:
    """A real operator checkout with a real `origin` remote (a bare repo)
    already carrying one commit on `main` - the same shape a real DevBot
    deployment's configured `local_path` has."""
    origin = tmp_path / "origin.git"
    _init_bare_repo(origin)

    operator = tmp_path / "operator"
    _init_git_repo(operator)
    _run_git("remote", "add", "origin", str(origin), cwd=operator)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=operator)

    repository = RepositoryConfig(
        owner="someone", repo=name, enabled=True, local_path=operator, default_branch="main"
    )
    return repository, origin


def _clone(origin_path: Path, dest: Path) -> None:
    subprocess.run(
        ["git", "clone", "-q", str(origin_path), str(dest)], check=True, capture_output=True
    )
    # A CI runner has no global git identity configured (unlike a typical
    # dev machine) - every throwaway clone that commits needs its own.
    _run_git("config", "user.email", "test@example.com", cwd=dest)
    _run_git("config", "user.name", "Test", cwd=dest)


def _push_branch(
    origin_path: Path, tmp_path: Path, branch: str, *, filename: str = "task.txt"
) -> None:
    """Push `branch` straight to `origin` from a throwaway clone, so the
    operator checkout never locally checks it out itself - exactly like a
    Planner-created Task branch the operator has never touched."""
    scratch = tmp_path / f"scratch-{branch.replace('/', '-')}"
    _clone(origin_path, scratch)
    _run_git("checkout", "-q", "-b", branch, cwd=scratch)
    (scratch / filename).write_text("task work\n", encoding="utf-8")
    _run_git("add", ".", cwd=scratch)
    _run_git("commit", "-q", "-m", "task work", cwd=scratch)
    _run_git("push", "-q", "origin", f"{branch}:{branch}", cwd=scratch)


def _push_to_main(origin_path: Path, tmp_path: Path, filename: str) -> None:
    """Push a new commit to `origin`'s `main` from a throwaway clone -
    something the operator checkout's own stale `origin/main` ref does not
    know about until it is fetched again."""
    scratch = tmp_path / "scratch-main-update"
    _clone(origin_path, scratch)
    (scratch / filename).write_text("external update\n", encoding="utf-8")
    _run_git("add", ".", cwd=scratch)
    _run_git("commit", "-q", "-m", "external update", cwd=scratch)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=scratch)


def _issue(*, number: int, title: str = "Some task", body: str = "") -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=number,
        title=title,
        body=body,
        state="open",
        labels=("devbot:ready",),
        created_at=datetime(2026, 1, 1),
    )


def _pull_request(*, head_ref: str, number: int = 1, issue_number: int = 1) -> PullRequest:
    return PullRequest(
        number=number,
        head_ref=head_ref,
        head_sha="deadbeef",
        body=f"Closes #{issue_number}",
        html_url=f"https://github.com/someone/myrepo/pull/{number}",
    )


# ---- CP-023-2: host-managed remote synchronization ----


def test_host_prepares_remote_branch_before_agent(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=12)

    # Someone else pushes a new commit to `origin`'s `main` *after* the
    # operator checkout was created - the operator's own local
    # `refs/remotes/origin/main` does not know about it yet.
    _push_to_main(origin, tmp_path, "from-origin.txt")

    prepared = manager.prepare(repository, issue, None)

    # `prepare()` must have fetched `origin` itself before branching the new
    # Task branch off `origin/main` - a stale local `origin/main` would
    # produce a worktree missing this file entirely.
    assert (prepared.worktree_path / "from-origin.txt").exists()


# ---- CP-023-3: isolated Job worktree ----


def test_job_uses_isolated_worktree(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    original_branch = _git_output(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=repository.local_path
    ).strip()
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=10)

    prepared = manager.prepare(repository, issue, None)

    assert prepared.repository.local_path != repository.local_path
    assert prepared.worktree_path.is_dir()
    assert (prepared.worktree_path / "README.md").exists()

    # The operator checkout's own branch and working tree are untouched.
    assert (
        _git_output("rev-parse", "--abbrev-ref", "HEAD", cwd=repository.local_path).strip()
        == original_branch
    )
    assert _git_output("status", "--porcelain", cwd=repository.local_path).strip() == ""


# ---- CP-023-4: existing branch reuse ----


def test_existing_task_branch_is_reused(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=11)
    _push_branch(origin, tmp_path, "task/023-existing")
    pull_request = _pull_request(head_ref="task/023-existing", issue_number=11)

    prepared = manager.prepare(repository, issue, pull_request)
    assert prepared.branch == "task/023-existing"
    assert prepared.reused is False

    prepared_again = manager.prepare(repository, issue, pull_request)
    assert prepared_again.reused is True
    assert prepared_again.worktree_path == prepared.worktree_path

    # No `devbot/...` fallback branch was ever generated for this Task.
    branches = _git_output("branch", "--list", cwd=repository.local_path)
    assert "devbot/" not in branches


# ---- CP-023-8: worktree lifecycle and cleanup ----


def test_failed_job_preserves_worktree_for_recovery(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=8)

    prepared = manager.prepare(repository, issue, None)
    (prepared.worktree_path / "leftover.txt").write_text(
        "uncommitted agent output from a failed run\n", encoding="utf-8"
    )

    # A later Job attempt (e.g. the daemon's next cycle) must reuse the same
    # worktree with that evidence intact - a Job failure must never
    # silently wipe it; only explicit `cleanup()` does.
    prepared_again = manager.prepare(repository, issue, None)

    assert prepared_again.worktree_path == prepared.worktree_path
    assert prepared_again.reused is True
    assert (prepared_again.worktree_path / "leftover.txt").exists()


def test_successful_cleanup_removes_worktree(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=9)
    prepared = manager.prepare(repository, issue, None)
    assert prepared.worktree_path.is_dir()

    manager.cleanup(repository, issue.number)

    assert not prepared.worktree_path.exists()
    listed = _git_output("worktree", "list", cwd=repository.local_path)
    assert str(prepared.worktree_path) not in listed


def test_conflicting_dirty_worktree_is_rejected(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=7)
    _push_branch(origin, tmp_path, "task/023-foo")
    pull_request = _pull_request(head_ref="task/023-foo", issue_number=7)

    prepared = manager.prepare(repository, issue, pull_request)

    # Simulate an anomalous state: something switched this Issue's worktree
    # to an unrelated branch and left it dirty - a later `prepare()` call
    # for the original branch must refuse to reuse it silently.
    _run_git("checkout", "-q", "-b", "unexpected-other-branch", cwd=prepared.worktree_path)
    (prepared.worktree_path / "scratch.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, pull_request)
    assert exc_info.value.category is WorkspacePreparationFailure.WORKTREE_CONFLICT


# ---- Supplementary: remote sync/branch-missing failure categories ----


def test_remote_sync_failure_is_classified(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=13)

    # Origin no longer exists - `git fetch` fails outright.
    import shutil

    shutil.rmtree(origin)

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, None)
    assert exc_info.value.category is WorkspacePreparationFailure.REMOTE_SYNC_FAILED


def test_linked_branch_missing_is_classified(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=14)
    pull_request = _pull_request(head_ref="task/023-never-pushed", issue_number=14)

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, pull_request)
    assert exc_info.value.category is WorkspacePreparationFailure.LINKED_BRANCH_MISSING


# ---- Prepared Agent context rendering (CP-023-5/CP-023-6 helpers) ----


def test_render_prepared_workspace_context_states_no_network_needed(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(
        number=15,
        body="- Contract: `tasks/023-host-managed-workspace-preparation.md`\n"
        "- Produce `results/023-host-managed-workspace-preparation.md`.\n",
    )
    _push_branch(origin, tmp_path, "task/023-context")
    pull_request = _pull_request(head_ref="task/023-context", number=44, issue_number=15)

    prepared = manager.prepare(repository, issue, pull_request)
    context = render_prepared_workspace_context(prepared)

    assert "task/023-context" in context
    assert "#44" in context
    assert "#15" in context
    assert "tasks/023-host-managed-workspace-preparation.md" in context
    assert "results/023-host-managed-workspace-preparation.md" in context
    assert "git fetch" in context
    assert "do not create another branch" in context.lower()


def test_parse_contract_and_result_path_from_issue_body() -> None:
    body = (
        "Implement Task 023 according to the approved contract.\n\n"
        "- Contract: `tasks/023-host-managed-workspace-preparation.md`\n"
        "- Branch: `task/023-host-managed-workspace-preparation`\n"
        "- Pull Request: #44\n\n"
        "- Produce `results/023-host-managed-workspace-preparation.md`.\n"
    )

    assert (
        parse_contract_path_from_issue_body(body)
        == "tasks/023-host-managed-workspace-preparation.md"
    )
    assert (
        parse_result_path_from_issue_body(body)
        == "results/023-host-managed-workspace-preparation.md"
    )


def test_parse_contract_path_returns_none_without_convention() -> None:
    assert parse_contract_path_from_issue_body("just a plain manual issue body") is None
    assert parse_result_path_from_issue_body("just a plain manual issue body") is None
