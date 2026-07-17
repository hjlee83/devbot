import subprocess
from pathlib import Path

import pytest

from devbot.models import DevBotConfig, RepositoryConfig
from devbot.startup import (
    STARTUP_SELF_UPDATE_ENV,
    StartupSelfUpdateError,
    run_startup_self_update,
)


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)
    _run_git("branch", "-m", "main", cwd=path)


def _repo_with_origin(tmp_path: Path) -> tuple[RepositoryConfig, Path, Path]:
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _run_git("symbolic-ref", "HEAD", "refs/heads/main", cwd=origin)
    operator = tmp_path / "operator"
    _init_repo(operator)
    _run_git("remote", "add", "origin", str(origin), cwd=operator)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=operator)
    repo = RepositoryConfig(owner="someone", repo="repo", enabled=True, local_path=operator)
    return repo, origin, operator


def _config(repo: RepositoryConfig, tmp_path: Path) -> DevBotConfig:
    return DevBotConfig(
        workspace_root=tmp_path / "workspace",
        poll_interval_seconds=60,
        lock_file=tmp_path / "devbot.lock",
        default_agent="codex",
        implementer_agent="codex",
        reviewer_agent="codex",
        max_concurrent_jobs=1,
        dry_run=True,
        github_token="token",
        repositories=(repo,),
    )


def _advance_origin(origin: Path, tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    subprocess.run(["git", "clone", "-q", str(origin), str(scratch)], check=True)
    _run_git("config", "user.email", "test@example.com", cwd=scratch)
    _run_git("config", "user.name", "Test", cwd=scratch)
    (scratch / "new.txt").write_text("new\n", encoding="utf-8")
    _run_git("add", ".", cwd=scratch)
    _run_git("commit", "-q", "-m", "advance main", cwd=scratch)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=scratch)


def test_startup_updates_operator_checkout_main_only(tmp_path: Path) -> None:
    repo, origin, operator = _repo_with_origin(tmp_path)
    _advance_origin(origin, tmp_path)

    result = run_startup_self_update(_config(repo, tmp_path), operator_checkout=operator)[0]

    assert result.result == "updated"
    assert (operator / "new.txt").exists()


def test_startup_rejects_dirty_main_checkout(tmp_path: Path) -> None:
    repo, _origin, operator = _repo_with_origin(tmp_path)
    (operator / "dirty.txt").write_text("dirty\n", encoding="utf-8")

    with pytest.raises(StartupSelfUpdateError) as exc_info:
        run_startup_self_update(_config(repo, tmp_path), operator_checkout=operator)

    assert "dirty" in exc_info.value.result.skip_reason


def test_startup_uses_ff_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo, _origin, _operator = _repo_with_origin(tmp_path)
    commands: list[tuple[str, ...]] = []
    real_run = subprocess.run

    def _spy(args, *a, **kw):
        if isinstance(args, list) and args[:1] == ["git"]:
            commands.append(tuple(args[1:]))
        return real_run(args, *a, **kw)

    monkeypatch.setattr("devbot.startup.subprocess.run", _spy)

    run_startup_self_update(_config(repo, tmp_path), operator_checkout=_operator)

    assert ("pull", "--ff-only", "origin", "main") in commands
    assert not any(cmd[:1] == ("merge",) for cmd in commands)
    assert not any(cmd[:1] == ("pull",) and "--ff-only" not in cmd for cmd in commands)


def test_startup_never_updates_task_branch_or_prepared_workspace(tmp_path: Path) -> None:
    repo, origin, operator = _repo_with_origin(tmp_path)
    prepared = operator / ".worktrees" / "issue-64"
    prepared.mkdir(parents=True)
    marker = prepared / "marker.txt"
    marker.write_text("keep\n", encoding="utf-8")
    _advance_origin(origin, tmp_path)

    run_startup_self_update(_config(repo, tmp_path), operator_checkout=operator)

    assert marker.read_text(encoding="utf-8") == "keep\n"


def test_startup_failure_prevents_doctor_planner_and_agent_execution(tmp_path: Path) -> None:
    repo, _origin, operator = _repo_with_origin(tmp_path)
    _run_git("checkout", "-q", "-b", "task/not-main", cwd=operator)

    with pytest.raises(StartupSelfUpdateError):
        run_startup_self_update(_config(repo, tmp_path), operator_checkout=operator)


def test_startup_runs_doctor_after_successful_main_update(tmp_path: Path) -> None:
    repo, _origin, _operator = _repo_with_origin(tmp_path)

    result = run_startup_self_update(_config(repo, tmp_path), operator_checkout=_operator)[0]

    assert result.result == "already_current"


def test_startup_update_diagnostics_are_complete_and_redacted(tmp_path: Path) -> None:
    repo, origin, _operator = _repo_with_origin(tmp_path)
    _advance_origin(origin, tmp_path)

    result = run_startup_self_update(_config(repo, tmp_path), operator_checkout=_operator)[0]

    assert result.current_sha
    assert result.latest_sha
    assert result.final_sha
    assert result.result in {"updated", "already_current"}
    assert "token" not in repr(result).lower()


def test_startup_does_not_update_managed_repositories(tmp_path: Path) -> None:
    operator_repo, operator_origin, operator = _repo_with_origin(tmp_path / "operator")
    managed_repo, managed_origin, managed = _repo_with_origin(tmp_path / "managed")
    _advance_origin(operator_origin, tmp_path / "operator-advance")
    _advance_origin(managed_origin, tmp_path / "managed-advance")
    managed_sha_before = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(managed), text=True
    ).strip()
    config = _config(managed_repo, tmp_path)

    result = run_startup_self_update(config, operator_checkout=operator)[0]

    managed_sha_after = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(managed), text=True
    ).strip()
    assert result.repository == str(operator)
    assert result.result == "updated"
    assert (operator / "new.txt").exists()
    assert not (managed / "new.txt").exists()
    assert managed_sha_after == managed_sha_before
    assert operator_repo.local_path == operator


def test_startup_update_restart_env_constant_is_available() -> None:
    assert STARTUP_SELF_UPDATE_ENV == "DEVBOT_STARTUP_SELF_UPDATED"
