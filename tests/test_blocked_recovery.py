from __future__ import annotations

from datetime import datetime
from pathlib import Path

from devbot.blocked_recovery import (
    BlockedDiagnosticContext,
    render_blocked_diagnostic_comment,
    validate_blocked_resume,
)
from devbot.github_client import GitHubIssue
from devbot.models import JobType, RepositoryConfig


def _repo(path: Path) -> RepositoryConfig:
    return RepositoryConfig(owner="someone", repo="myrepo", enabled=True, local_path=path)


def _issue(
    *,
    labels: tuple[str, ...] = ("devbot:blocked",),
    body: str | None = None,
) -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=155,
        title="Recover blocked job",
        body=body
        or (
            "- Branch: `task/155-recovery`\n"
            "- Contract: `tasks/155-recovery.md`\n"
        ),
        state="open",
        labels=labels,
        created_at=datetime(2026, 1, 1),
    )


def test_blocked_diagnostic_comment_extracts_missing_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    issue = _issue()
    exc = FileNotFoundError(2, "No such file or directory", "uv")

    body = render_blocked_diagnostic_comment(
        BlockedDiagnosticContext(
            repository=repo,
            issue=issue,
            job_type=JobType.IMPLEMENT,
            stage="delivery",
            branch="task/155-recovery",
            worktree_path=tmp_path / ".worktrees" / "issue-155",
            command="uv run pytest",
            changed_files=("src/app.py",),
        ),
        exc,
    )

    assert "devbot-blocked-diagnostic:v1" in body
    assert "exception: FileNotFoundError" in body
    assert "missing executable or path: uv" in body
    assert "stage: delivery" in body
    assert "worktree:" in body
    assert "command: uv run pytest" in body
    assert "src/app.py" in body
    assert "Traceback excerpt" in body


def test_validate_blocked_resume_accepts_preserved_contract(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees" / "issue-155"
    (worktree / ".git").mkdir(parents=True)
    (worktree / "tasks").mkdir()
    (worktree / "tasks" / "155-recovery.md").write_text("contract\n", encoding="utf-8")

    result = validate_blocked_resume(
        repository=_repo(tmp_path),
        issue=_issue(),
        worktree_path=worktree,
    )

    assert result.ok is True
    assert result.branch == "task/155-recovery"
    assert result.worktree_path == worktree


def test_validate_blocked_resume_rejects_missing_worktree(tmp_path: Path) -> None:
    result = validate_blocked_resume(
        repository=_repo(tmp_path),
        issue=_issue(),
        worktree_path=tmp_path / "missing",
    )

    assert result.ok is False
    assert "보존된 worktree가 없습니다" in result.message


def test_validate_blocked_resume_rejects_missing_contract_metadata(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees" / "issue-155"
    (worktree / ".git").mkdir(parents=True)

    result = validate_blocked_resume(
        repository=_repo(tmp_path),
        issue=_issue(body="- Branch: `task/155-recovery`\n"),
        worktree_path=worktree,
    )

    assert result.ok is False
    assert "Task Contract 경로" in result.message
    assert "선언되어 있지 않아" in result.message


def test_validate_blocked_resume_rejects_missing_declared_contract(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees" / "issue-155"
    (worktree / ".git").mkdir(parents=True)

    result = validate_blocked_resume(
        repository=_repo(tmp_path),
        issue=_issue(),
        worktree_path=worktree,
    )

    assert result.ok is False
    assert "Task Contract가 worktree에 없습니다" in result.message


def test_validate_blocked_resume_rejects_contract_path_traversal(tmp_path: Path) -> None:
    worktree = tmp_path / ".worktrees" / "issue-155"
    (worktree / ".git").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("not this contract\n", encoding="utf-8")

    result = validate_blocked_resume(
        repository=_repo(tmp_path),
        issue=_issue(
            body=(
                "- Branch: `task/155-recovery`\n"
                "- Contract: `../../outside.md`\n"
            )
        ),
        worktree_path=worktree,
    )

    assert result.ok is False
    assert "worktree 밖" in result.message
