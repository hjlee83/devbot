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
    parse_branch_from_issue_body,
    parse_contract_path_from_issue_body,
    parse_pull_request_number_from_issue_body,
    parse_result_path_from_issue_body,
    render_prepared_workspace_context,
)


def _run_git(*args: str, cwd: Path) -> None:
    completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {completed.stderr or completed.stdout}"
        )


def _git_output(*args: str, cwd: Path) -> str:
    completed = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"git {' '.join(args)} failed in {cwd}: {completed.stderr or completed.stdout}"
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
    # `git init --bare`'s symbolic HEAD follows `init.defaultBranch`, which
    # is unset (typically resolving to `master`) on a CI runner but often
    # configured to `main` on a dev machine. Every `main` this file ever
    # pushes to `origin` is named explicitly, but a plain `git clone
    # <origin> <dest>` (`_clone()`, used by every throwaway-clone helper
    # below) resolves the branch to check out from `origin`'s HEAD - fixing
    # it to `main` up front keeps that resolution deterministic regardless
    # of the runner's default.
    _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=path)


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
    (scratch / filename).parent.mkdir(parents=True, exist_ok=True)
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


def _remote_head(origin_path: Path, ref: str) -> str:
    completed = subprocess.run(
        ["git", "ls-remote", str(origin_path), ref],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.split()[0]


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


def test_worktree_default_root_is_repository_local_dot_worktrees(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "external-workspace")

    assert manager.worktree_root(repository) == (repository.local_path / ".worktrees").resolve()


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


def test_prepared_workspace_preserves_host_checkout_path(tmp_path: Path) -> None:
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=59)

    prepared = manager.prepare(repository, issue, None)

    assert prepared.repository.local_path == prepared.worktree_path
    assert prepared.repository.host_checkout_path == repository.local_path


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


def test_existing_canonical_branch_worktree_is_reused(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=36)
    _push_branch(origin, tmp_path, "task/030-canonical")
    _run_git(
        "fetch",
        "origin",
        "task/030-canonical:task/030-canonical",
        cwd=repository.local_path,
    )
    canonical = tmp_path / "canonical-worktree"
    _run_git(
        "worktree",
        "add",
        str(canonical),
        "task/030-canonical",
        cwd=repository.local_path,
    )
    pull_request = _pull_request(head_ref="task/030-canonical", issue_number=36)

    prepared = manager.prepare(
        repository, issue, pull_request, synchronize_with_main=False
    )

    assert prepared.reused is True
    assert prepared.worktree_path == canonical.resolve()


def test_prepared_workspace_resolves_git_metadata_paths(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=30)
    _push_branch(origin, tmp_path, "task/030-metadata")
    pull_request = _pull_request(head_ref="task/030-metadata", issue_number=30)

    prepared = manager.prepare(repository, issue, pull_request)

    assert prepared.git_dir is not None
    assert prepared.git_common_dir is not None
    assert prepared.git_top_level == prepared.worktree_path
    assert prepared.git_dir.is_dir()
    assert prepared.git_common_dir.is_dir()
    assert prepared.git_dir != prepared.worktree_path


def test_implement_prepare_rebases_latest_main_and_force_pushes_with_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=31)
    _push_branch(origin, tmp_path, "task/030-sync", filename="task.txt")
    _push_to_main(origin, tmp_path, "main-after-task.txt")
    pull_request = _pull_request(head_ref="task/030-sync", issue_number=31)
    commands: list[tuple[str, ...]] = []
    real_run_git = subprocess.run

    def _spy(args, *a, **kw):
        if isinstance(args, list) and args[:1] == ["git"]:
            commands.append(tuple(args[1:]))
        return real_run_git(args, *a, **kw)

    monkeypatch.setattr("devbot.worktree.subprocess.run", _spy)

    prepared = manager.prepare(repository, issue, pull_request)

    assert (prepared.worktree_path / "main-after-task.txt").exists()
    assert any(cmd[:1] == ("rebase",) for cmd in commands)
    assert any("--force-with-lease" in cmd for cmd in commands)
    assert not any("--force" in cmd and "--force-with-lease" not in cmd for cmd in commands)


def test_review_prepare_does_not_change_pr_head(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=32)
    _push_branch(origin, tmp_path, "task/030-review", filename="task.txt")
    before = _remote_head(origin, "refs/heads/task/030-review")
    _push_to_main(origin, tmp_path, "main-after-review.txt")
    pull_request = _pull_request(head_ref="task/030-review", issue_number=32)

    prepared = manager.prepare(
        repository, issue, pull_request, synchronize_with_main=False
    )

    after = _remote_head(origin, "refs/heads/task/030-review")
    assert after == before
    assert not (prepared.worktree_path / "main-after-review.txt").exists()


def test_dry_run_prepare_does_not_rebase_or_force_push(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-B0-1 regression: dry_run must stop `_sync_task_branch_with_main`
    before its first local mutation (rebase), let alone the remote one
    (force-push) - mirrors `test_implement_prepare_rebases_latest_main_and_
    force_pushes_with_lease` with the assertions inverted."""
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace", dry_run=True)
    issue = _issue(number=33)
    _push_branch(origin, tmp_path, "task/030-dry-run", filename="task.txt")
    before = _remote_head(origin, "refs/heads/task/030-dry-run")
    _push_to_main(origin, tmp_path, "main-after-dry-run.txt")
    pull_request = _pull_request(head_ref="task/030-dry-run", issue_number=33)
    commands: list[tuple[str, ...]] = []
    real_run_git = subprocess.run

    def _spy(args, *a, **kw):
        if isinstance(args, list) and args[:1] == ["git"]:
            commands.append(tuple(args[1:]))
        return real_run_git(args, *a, **kw)

    monkeypatch.setattr("devbot.worktree.subprocess.run", _spy)

    prepared = manager.prepare(repository, issue, pull_request)

    after = _remote_head(origin, "refs/heads/task/030-dry-run")
    assert after == before
    assert not any(cmd[:1] == ("rebase",) for cmd in commands)
    assert not any(cmd[:1] == ("push",) for cmd in commands)
    assert not (prepared.worktree_path / "main-after-dry-run.txt").exists()


def test_worktree_manager_dry_run_defaults_to_false(tmp_path: Path) -> None:
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")

    assert manager.dry_run is False


def test_review_integration_validation_uses_non_mutating_merge_tree(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=37)
    _push_branch(origin, tmp_path, "task/030-merge-tree", filename="task.txt")
    _push_to_main(origin, tmp_path, "main-only.txt")
    pull_request = _pull_request(head_ref="task/030-merge-tree", issue_number=37)
    prepared = manager.prepare(
        repository, issue, pull_request, synchronize_with_main=False
    )
    before = _git_output("rev-parse", "HEAD", cwd=prepared.worktree_path).strip()

    validation = manager.validate_review_integration(prepared)

    after = _git_output("rev-parse", "HEAD", cwd=prepared.worktree_path).strip()
    assert validation.mergeable is True
    assert validation.method == "git merge-tree --write-tree origin/main HEAD"
    assert after == before
    assert _git_output("status", "--porcelain", cwd=prepared.worktree_path).strip() == ""


def test_dirty_worktree_is_not_rebased_or_overwritten(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=33)
    _push_branch(origin, tmp_path, "task/030-dirty")
    pull_request = _pull_request(head_ref="task/030-dirty", issue_number=33)
    prepared = manager.prepare(repository, issue, pull_request)
    (prepared.worktree_path / "dirty.txt").write_text("keep me\n", encoding="utf-8")

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, pull_request)

    assert exc_info.value.category is WorkspacePreparationFailure.WORKSPACE_DIRTY
    assert (prepared.worktree_path / "dirty.txt").read_text(encoding="utf-8") == "keep me\n"


def test_stale_pr_head_metadata_stops_execution(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=34)
    _push_branch(origin, tmp_path, "task/030-stale")
    stale_pr = _pull_request(head_ref="task/030-stale", issue_number=34)
    stale_pr = PullRequest(
        number=stale_pr.number,
        head_ref=stale_pr.head_ref,
        head_sha="0" * 40,
        body=stale_pr.body,
        html_url=stale_pr.html_url,
    )

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, stale_pr)

    assert exc_info.value.category is WorkspacePreparationFailure.STALE_PR_HEAD


def test_rebase_conflict_preserves_original_branch(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=35)
    _push_branch(origin, tmp_path, "task/030-conflict", filename="README.md")
    scratch = tmp_path / "scratch-conflict-main"
    _clone(origin, scratch)
    (scratch / "README.md").write_text("conflicting main\n", encoding="utf-8")
    _run_git("add", ".", cwd=scratch)
    _run_git("commit", "-q", "-m", "conflicting main", cwd=scratch)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=scratch)
    pull_request = _pull_request(head_ref="task/030-conflict", issue_number=35)

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, pull_request)

    assert exc_info.value.category is WorkspacePreparationFailure.TASK_BRANCH_CONFLICT
    prepared_path = manager.worktree_path(repository, issue.number)
    assert _git_output("status", "--porcelain", cwd=prepared_path).strip() == ""
    assert "task work" in (prepared_path / "README.md").read_text(encoding="utf-8")


def test_prepared_workspace_contains_planner_contract(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    contract_path = "tasks/025-planner-linked-pr-resolution.md"
    _push_branch(
        origin,
        tmp_path,
        "task/025-planner-linked-pr-resolution",
        filename=contract_path,
    )
    issue = _issue(
        number=49,
        body=(
            f"- Contract: `{contract_path}`\n"
            "- Branch: `task/025-planner-linked-pr-resolution`\n"
            "- Pull Request: #48\n"
        ),
    )
    pull_request = _pull_request(
        head_ref="task/025-planner-linked-pr-resolution", number=48, issue_number=49
    )

    prepared = manager.prepare(repository, issue, pull_request)

    assert prepared.branch == "task/025-planner-linked-pr-resolution"
    assert (prepared.worktree_path / contract_path).is_file()
    assert prepared.contract_path == contract_path


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


def test_branch_pr_mismatch_is_classified(tmp_path: Path) -> None:
    """PR #44 REQUEST CHANGES (CP-023-9): a worktree already prepared for
    one branch, still clean, whose Issue is now linked to a *different*
    branch/PR (e.g. the Task branch was recreated) must be reported as an
    explicit `branch_pr_mismatch` - never silently deleted and recreated
    on the new branch without a trace."""
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    issue = _issue(number=16)
    _push_branch(origin, tmp_path, "task/023-old-branch")
    _push_branch(origin, tmp_path, "task/023-new-branch")
    old_pr = _pull_request(head_ref="task/023-old-branch", issue_number=16)
    new_pr = _pull_request(head_ref="task/023-new-branch", number=2, issue_number=16)

    prepared = manager.prepare(repository, issue, old_pr)
    assert prepared.branch == "task/023-old-branch"

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, new_pr)
    assert exc_info.value.category is WorkspacePreparationFailure.BRANCH_PR_MISMATCH
    # The stale worktree is preserved, not silently deleted, for diagnosis.
    assert prepared.worktree_path.is_dir()


def test_issue_branch_and_pr_head_mismatch_rejected(tmp_path: Path) -> None:
    repository, origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace")
    _push_branch(origin, tmp_path, "task/025-expected")
    _push_branch(origin, tmp_path, "task/025-actual")
    issue = _issue(
        number=25,
        body="- Branch: `task/025-expected`\n- Pull Request: #48\n",
    )
    pull_request = _pull_request(head_ref="task/025-actual", number=48, issue_number=25)

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, pull_request)

    assert exc_info.value.category is WorkspacePreparationFailure.BRANCH_PR_MISMATCH
    assert "expected_branch='task/025-expected'" in str(exc_info.value)
    assert "resolved_pr=#48" in str(exc_info.value)


def test_workspace_dirty_is_classified(tmp_path: Path) -> None:
    """PR #44 REQUEST CHANGES (CP-023-9): a freshly created worktree that
    is unexpectedly dirty immediately after checkout (e.g. a smudge/clean
    filter or filesystem anomaly) must be reported as `workspace_dirty`
    rather than silently handed to the Agent."""
    repository, _origin = _make_operator_repo(tmp_path)
    manager = WorktreeManager(workspace_root=tmp_path / "workspace", is_dirty=lambda path: True)
    issue = _issue(number=17)

    with pytest.raises(WorkspacePreparationError) as exc_info:
        manager.prepare(repository, issue, None)
    assert exc_info.value.category is WorkspacePreparationFailure.WORKSPACE_DIRTY


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


def test_task_issue_pr_number_is_parsed() -> None:
    body = (
        "Implement Task 025 according to the approved contract.\n\n"
        "- Contract: `tasks/025-planner-linked-pr-resolution.md`\n"
        "- Branch: `task/025-planner-linked-pr-resolution`\n"
        "- Pull Request: #48\n\n"
        "- Produce `results/025-planner-linked-pr-resolution.md`.\n"
    )

    assert parse_pull_request_number_from_issue_body(body) == 48
    assert parse_branch_from_issue_body(body) == "task/025-planner-linked-pr-resolution"
    assert parse_pull_request_number_from_issue_body("manual issue") is None


def test_parse_contract_path_returns_none_without_convention() -> None:
    assert parse_contract_path_from_issue_body("just a plain manual issue body") is None
    assert parse_result_path_from_issue_body("just a plain manual issue body") is None
