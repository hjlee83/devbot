import re
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from devbot.github_client import GitHubIssue
from devbot.models import IssueComment, RepositoryConfig
from devbot.workspace import (
    DirtyWorkspaceError,
    NotAGitRepositoryError,
    WorkspaceValidationError,
    build_agent_prompt,
    ensure_git_workspace_ready,
    generate_branch_name,
    inspect_workspace,
)


def _repo(local_path: Path, *, repo: str = "myrepo") -> RepositoryConfig:
    return RepositoryConfig(owner="someone", repo=repo, enabled=True, local_path=local_path)


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)


def test_missing_repository_path_raises(tmp_path: Path) -> None:
    repository = _repo(tmp_path / "does-not-exist")

    with pytest.raises(WorkspaceValidationError):
        ensure_git_workspace_ready(repository)


def test_non_git_directory_is_rejected(tmp_path: Path) -> None:
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    repository = _repo(plain_dir)

    with pytest.raises(NotAGitRepositoryError):
        ensure_git_workspace_ready(repository)


def test_dirty_workspace_is_detected(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    (repo_path / "untracked.txt").write_text("uncommitted\n", encoding="utf-8")
    repository = _repo(repo_path)

    with pytest.raises(DirtyWorkspaceError):
        ensure_git_workspace_ready(repository)


def test_devbot_metadata_changes_do_not_dirty_workspace(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    (repo_path / ".devbot").mkdir()
    (repo_path / ".devbot" / "config.yaml").write_text("owner: someone\n", encoding="utf-8")
    repository = _repo(repo_path)

    with caplog.at_level("DEBUG", logger="devbot"):
        status = inspect_workspace(repository)
        ensure_git_workspace_ready(repository)

    assert status.has_uncommitted_changes is False
    assert status.changed_files == ()
    assert "ignored DevBot metadata workspace change: .devbot/" in caplog.text


def test_devbot_metadata_filter_preserves_real_source_changes(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    (repo_path / ".devbot").mkdir()
    (repo_path / ".devbot" / "config.yaml").write_text("owner: someone\n", encoding="utf-8")
    (repo_path / "src.py").write_text("print('dirty')\n", encoding="utf-8")
    repository = _repo(repo_path)

    status = inspect_workspace(repository)

    assert status.has_uncommitted_changes is True
    assert status.changed_files == ("src.py",)
    with pytest.raises(DirtyWorkspaceError):
        ensure_git_workspace_ready(repository)


def test_workspace_cleanliness_respects_gitignore(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    (repo_path / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    _run_git("add", ".gitignore", cwd=repo_path)
    _run_git("commit", "-q", "-m", "ignore logs", cwd=repo_path)
    (repo_path / "ignored.log").write_text("ignored\n", encoding="utf-8")

    status = inspect_workspace(_repo(repo_path))

    assert status.has_uncommitted_changes is False
    assert status.changed_files == ()


def test_clean_workspace_is_accepted(tmp_path: Path) -> None:
    repo_path = tmp_path / "repo"
    _init_git_repo(repo_path)
    repository = _repo(repo_path)

    ensure_git_workspace_ready(repository)  # must not raise


def test_branch_name_is_safe_and_deterministic() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"), repo="myrepo")
    title = "Fix login bug! (urgent) — now??"

    first = generate_branch_name(repository, 42, title)
    second = generate_branch_name(repository, 42, title)

    assert first == second
    assert first.startswith("task/042-")
    assert " " not in first
    assert re.search(r"[~^:?*\[\\]", first) is None
    assert ".." not in first
    assert not first.endswith(".")
    assert not first.endswith("/")


def test_branch_name_differs_for_different_issues() -> None:
    repository = _repo(Path("/tmp/workspace/myrepo"), repo="myrepo")

    first = generate_branch_name(repository, 1, "Same title")
    second = generate_branch_name(repository, 2, "Same title")

    assert first != second


def test_prompt_contains_issue_and_unprocessed_comments(tmp_path: Path) -> None:
    template_path = tmp_path / "issue-task.md"
    template_path.write_text(
        "Repo: {{owner}}/{{repo}}\n"
        "Issue #{{issue_number}}: {{issue_title}}\n"
        "{{issue_body}}\n"
        "Comments:\n{{unprocessed_comments}}\n",
        encoding="utf-8",
    )
    repository = _repo(tmp_path / "myrepo", repo="myrepo")
    issue = GitHubIssue(
        repository=repository.full_name,
        number=7,
        title="Something broke",
        body="Steps to reproduce the bug.",
        state="open",
        labels=("devbot:ready",),
        created_at=datetime(2026, 1, 1),
    )
    comments = [IssueComment(author="alice", body="Can you also check the timeout?")]

    prompt = build_agent_prompt(repository, issue, comments, template_path=template_path)

    assert "Something broke" in prompt
    assert "Steps to reproduce the bug." in prompt
    assert "#7" in prompt
    assert "alice" in prompt
    assert "Can you also check the timeout?" in prompt


def test_prompt_without_comments_shows_placeholder(tmp_path: Path) -> None:
    template_path = tmp_path / "issue-task.md"
    template_path.write_text("{{unprocessed_comments}}", encoding="utf-8")
    repository = _repo(tmp_path / "myrepo", repo="myrepo")
    issue = GitHubIssue(
        repository=repository.full_name,
        number=1,
        title="No comments here",
        body="",
        state="open",
        labels=(),
        created_at=datetime(2026, 1, 1),
    )

    prompt = build_agent_prompt(repository, issue, [], template_path=template_path)

    assert prompt.strip() == "(none)"
