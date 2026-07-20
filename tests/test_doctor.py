import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.doctor import (
    build_doctor_report,
    check_agent_execution_readiness,
    check_agent_roles,
    check_repository_registrations,
    render_doctor_report,
)
from devbot.github_client import GitHubAuthenticationError, GitHubUser
from devbot.lock import ProcessLock
from devbot.models import DevBotConfig, RepositoryConfig


def _repo(local_path: Path) -> RepositoryConfig:
    return RepositoryConfig(owner="someone", repo="myrepo", enabled=True, local_path=local_path)


def _config(repositories: list[RepositoryConfig], **overrides: object) -> DevBotConfig:
    values: dict[str, object] = {
        "workspace_root": Path("/tmp/workspace"),
        "poll_interval_seconds": 60,
        "lock_file": Path("/tmp/devbot-doctor-test.lock"),
        "default_agent": "codex",
        "implementer_agent": "claude",
        "reviewer_agent": "codex",
        "max_concurrent_jobs": 1,
        "dry_run": True,
        "github_token": "test-token",
        "repositories": tuple(repositories),
    }
    values.update(overrides)
    return DevBotConfig(**values)  # type: ignore[arg-type]


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git_repo(path: Path) -> None:
    """`git init`'s default initial branch name depends on the runner's
    `init.defaultBranch` config (`main` locally vs `master` on CI) -
    renaming to `main` right after the first commit keeps this
    deterministic (see `tests/test_startup.py::_init_git_repo`)."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)
    _run_git("branch", "-m", "main", cwd=path)


# ---- CP-019-5: doctor command ----


def test_doctor_command(tmp_path: Path) -> None:
    """CP-019-5: `devbot doctor` reports configured repositories, workspace
    status, current branch, GitHub connectivity/authentication, daemon lock
    status, configured implementer/reviewer, and whether it is safe to
    start - and never raises even without real network access."""
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    lock_file = tmp_path / "devbot.lock"
    config = _config([_repo(repo_path)], lock_file=lock_file)

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        side_effect=ConnectionError("no network in this sandbox"),
    ):
        report = build_doctor_report(config)

    names = {check.name for check in report.checks}
    assert "repository_configuration" in names
    assert f"workspace_cleanliness[{config.repositories[0].full_name}]" in names
    assert f"current_branch_compatibility[{config.repositories[0].full_name}]" in names
    assert "github_connectivity" in names
    assert "daemon_lock" in names
    assert "agent_roles" in names

    # No live network access is a reported failure, but must not crash the
    # command, and must not by itself make the daemon "unsafe to start"
    # (see `DoctorReport.safe_to_start`'s docstring).
    connectivity = next(c for c in report.checks if c.name == "github_connectivity")
    assert connectivity.ok is False
    assert report.safe_to_start is True

    rendered = render_doctor_report(report)
    assert "safe_to_start: yes" in rendered
    assert "[FAIL] github_connectivity" in rendered


def test_doctor_command_reports_unsafe_when_lock_is_held(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    lock_file = tmp_path / "devbot.lock"
    config = _config([_repo(repo_path)], lock_file=lock_file)

    holder = ProcessLock(lock_file)
    holder.acquire()
    try:
        with patch(
            "devbot.github_client.GitHubClient.get_authenticated_user",
            side_effect=ConnectionError("no network"),
        ):
            report = build_doctor_report(config)
    finally:
        holder.release()

    assert report.safe_to_start is False
    rendered = render_doctor_report(report)
    assert "safe_to_start: no" in rendered


def test_doctor_command_reports_successful_github_connectivity(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config([_repo(repo_path)], lock_file=tmp_path / "devbot.lock")

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        return_value=GitHubUser(login="devbot-user", id=1),
    ):
        report = build_doctor_report(config)

    connectivity = next(c for c in report.checks if c.name == "github_connectivity")
    assert connectivity.ok is True
    assert "devbot-user" in connectivity.detail


def test_doctor_command_reports_authentication_failure() -> None:
    config = _config([])

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        side_effect=GitHubAuthenticationError("bad token"),
    ):
        report = build_doctor_report(config)

    connectivity = next(c for c in report.checks if c.name == "github_connectivity")
    assert connectivity.ok is False


def test_doctor_reports_configured_implementer_and_reviewer() -> None:
    config = _config([], implementer_agent="claude", reviewer_agent="codex")

    check = check_agent_roles(config)

    assert check.ok is True
    assert "implementer=claude" in check.detail
    assert "reviewer=codex" in check.detail


def test_doctor_reports_agent_execution_readiness() -> None:
    capabilities = {
        "approval": True,
        "sandbox": True,
        "cd": True,
        "add_dir": True,
        "config": True,
    }

    with (
        patch("devbot.doctor.shutil.which", return_value="/usr/local/bin/codex"),
        patch("devbot.doctor.subprocess.run") as mock_run,
        patch("devbot.doctor.CodexRunner._detect_capabilities", return_value=capabilities),
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="codex 1.0\n", stderr="")
        check = check_agent_execution_readiness("codex", "reviewer")

    assert check.ok is True
    assert check.name == "agent_execution_readiness[reviewer:codex]"
    assert "version=codex 1.0" in check.detail
    assert "unattended_ready=True" in check.detail


def test_doctor_checks_claude_auth_with_launcher_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config([_repo(repo_path)], reviewer_agent="claude")
    monkeypatch.setenv("HOME", "/Users/tester")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def _run(args: list[str], **kwargs: object) -> MagicMock:
        calls.append((args, kwargs))
        return MagicMock(returncode=0, stdout="claude 1.0\n", stderr="")

    with (
        patch("devbot.doctor.shutil.which", return_value="/usr/local/bin/claude"),
        patch("devbot.doctor.subprocess.run", side_effect=_run),
    ):
        check = check_agent_execution_readiness("claude", "reviewer", config)

    assert check.ok is True
    assert calls[0][0] == ["claude", "--version"]
    assert calls[1][0] == ["claude", "auth", "status"]
    for _args, kwargs in calls:
        assert kwargs["cwd"] == str(repo_path)
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert env["HOME"] == "/Users/tester"
        assert env["DEVBOT_ROLE"] == "review"
        assert "GITHUB_TOKEN" not in env



def test_doctor_ci_profile_skips_agent_auth_readiness(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config([_repo(repo_path)], reviewer_agent="claude")

    with (
        patch(
            "devbot.github_client.GitHubClient.get_authenticated_user",
            return_value=GitHubUser(login="devbot-user", id=1),
        ),
        patch("devbot.doctor.shutil.which", return_value=None),
    ):
        report = build_doctor_report(config, ci=True)

    names = {check.name for check in report.checks}
    assert "agent_execution_readiness[ci]" in names
    assert "agent_execution_readiness[reviewer:claude]" not in names
    ci_check = next(
        check for check in report.checks if check.name == "agent_execution_readiness[ci]"
    )
    assert ci_check.ok is True
    assert "skipped Agent executable/auth checks" in ci_check.detail
    assert report.safe_to_start is True


def test_doctor_default_profile_keeps_agent_auth_readiness(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config([_repo(repo_path)], reviewer_agent="claude")

    with patch("devbot.doctor.shutil.which", return_value=None):
        report = build_doctor_report(config)

    names = {check.name for check in report.checks}
    assert "agent_execution_readiness[reviewer:claude]" in names
    assert "agent_execution_readiness[ci]" not in names

# ---- CP-023-10: doctor reports Job worktree health ----


def test_doctor_reports_worktree_health(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    workspace_root = tmp_path / "workspace"
    config = _config(
        [_repo(repo_path)], workspace_root=workspace_root, lock_file=tmp_path / "devbot.lock"
    )
    check_name = f"worktree_health[{config.repositories[0].full_name}]"

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        side_effect=ConnectionError("no network in this sandbox"),
    ):
        report = build_doctor_report(config)

    check = next(c for c in report.checks if c.name == check_name)
    assert check.ok is True
    assert "active=0" in check.detail
    assert "stale=0" in check.detail
    assert "conflicting=0" in check.detail
    assert report.safe_to_start is True

    # An on-disk directory under the worktree root that Git itself does not
    # know about is a real conflict (a future `prepare()` for that path
    # would fail) - reported, but never fatal to the whole daemon.
    orphaned = repo_path / ".worktrees" / "issue-99"
    orphaned.mkdir(parents=True)

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        side_effect=ConnectionError("no network in this sandbox"),
    ):
        report_with_conflict = build_doctor_report(config)

    conflicting_check = next(c for c in report_with_conflict.checks if c.name == check_name)
    assert conflicting_check.ok is False
    assert "conflicting=1" in conflicting_check.detail
    assert str(orphaned) in conflicting_check.detail
    assert report_with_conflict.safe_to_start is True


# ---- Issue #122: doctor reports devbot init registration problems ----


def test_check_repository_registrations_ok_when_no_diagnostics() -> None:
    config = _config([], registry_diagnostics=())

    check = check_repository_registrations(config)

    assert check.ok is True
    assert check.fatal is False


def test_check_repository_registrations_reports_each_diagnostic() -> None:
    config = _config(
        [],
        registry_diagnostics=(
            "missing_path: registered repository path does not exist: /tmp/gone",
            "duplicate_repository: someone/myrepo is registered at both /a and /b",
        ),
    )

    check = check_repository_registrations(config)

    assert check.ok is False
    assert check.fatal is False
    assert "missing_path" in check.detail
    assert "/tmp/gone" in check.detail
    assert "duplicate_repository" in check.detail


def test_build_doctor_report_includes_repository_registrations_check(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config(
        [_repo(repo_path)],
        lock_file=tmp_path / "devbot.lock",
        registry_diagnostics=("missing_path: /tmp/gone is missing",),
    )

    with patch(
        "devbot.github_client.GitHubClient.get_authenticated_user",
        side_effect=ConnectionError("no network in this sandbox"),
    ):
        report = build_doctor_report(config)

    check = next(c for c in report.checks if c.name == "repository_registrations")
    assert check.ok is False
    # Informational, like every other non-lock check in this module - one
    # broken registration must not stop the daemon from starting.
    assert report.safe_to_start is True
