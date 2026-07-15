import subprocess
from pathlib import Path
from unittest.mock import patch

from devbot.doctor import build_doctor_report, check_agent_roles, render_doctor_report
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
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)


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
