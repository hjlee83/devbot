import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from devbot.lock import ProcessLock
from devbot.main import _run_startup_self_update, main
from devbot.models import DevBotConfig
from devbot.startup import STARTUP_SELF_UPDATE_ENV, StartupSelfUpdateResult


def _config(tmp_path: Path) -> DevBotConfig:
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
        repositories=(),
    )


def test_cli_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("devbot.main.authoritative_version", return_value="9.8.7"):
        exit_code = main(["--version"])

    assert exit_code == 0
    assert capsys.readouterr().out == "devbot 9.8.7\n"


def test_cli_version_uses_authoritative_version_source() -> None:
    with patch("devbot.main.authoritative_version", return_value="1.2.3") as mock_version:
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_version.assert_called_once_with()


def test_cli_version_does_not_load_runtime_config(tmp_path: Path) -> None:
    missing_env = tmp_path / "missing.env"
    invalid_repositories = tmp_path / "repositories.yaml"
    invalid_repositories.write_text("repositories: [", encoding="utf-8")

    with patch("devbot.main.authoritative_version", return_value="1.2.3"):
        exit_code = main(
            ["--version"], env_path=missing_env, repositories_path=invalid_repositories
        )

    assert exit_code == 0


def test_cli_version_does_not_acquire_daemon_lock() -> None:
    with (
        patch("devbot.main.authoritative_version", return_value="1.2.3"),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_lock.assert_not_called()


def test_cli_version_does_not_contact_github() -> None:
    with (
        patch("devbot.main.authoritative_version", return_value="1.2.3"),
        patch("devbot.main.GitHubClient") as mock_read_client,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_read_client.assert_not_called()
    mock_write_client.assert_not_called()


def test_cli_version_does_not_start_polling_or_agents() -> None:
    with (
        patch("devbot.main.authoritative_version", return_value="1.2.3"),
        patch("devbot.main.PollingService") as mock_polling,
        patch("devbot.main.run_forever") as mock_run_forever,
        patch("devbot.main.build_agent_runner") as mock_build_agent_runner,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_polling.assert_not_called()
    mock_run_forever.assert_not_called()
    mock_build_agent_runner.assert_not_called()


def test_startup_update_restarts_process_when_head_changes(tmp_path: Path) -> None:
    result = StartupSelfUpdateResult(
        repository=str(tmp_path),
        current_sha="old",
        latest_sha="new",
        final_sha="new",
        result="updated",
    )
    captured: dict[str, object] = {}

    def _execvpe(executable: str, argv: list[str], env: dict[str, str]) -> None:
        captured["executable"] = executable
        captured["argv"] = argv
        captured["env"] = env
        raise SystemExit(0)

    with (
        patch("devbot.main.run_startup_self_update", return_value=(result,)),
        patch("devbot.main.os.execvpe", side_effect=_execvpe),
        pytest.raises(SystemExit),
    ):
        _run_startup_self_update(_config(tmp_path), logging.getLogger("test"))

    env = captured["env"]
    assert isinstance(env, dict)
    assert env[STARTUP_SELF_UPDATE_ENV] == "new"


def test_startup_update_does_not_restart_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(STARTUP_SELF_UPDATE_ENV, "new")
    result = StartupSelfUpdateResult(
        repository=str(tmp_path),
        current_sha="old",
        latest_sha="new",
        final_sha="new",
        result="updated",
    )

    with (
        patch("devbot.main.run_startup_self_update", return_value=(result,)),
        patch("devbot.main.os.execvpe") as mock_exec,
    ):
        assert _run_startup_self_update(_config(tmp_path), logging.getLogger("test")) is True

    mock_exec.assert_not_called()


def test_doctor_ci_skips_startup_self_update(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\n"
        "GITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with (
        patch("devbot.main._run_startup_self_update") as mock_update,
        patch("devbot.main.build_doctor_report") as mock_report,
        patch("devbot.main.render_doctor_report", return_value="doctor\n"),
    ):
        mock_report.return_value.safe_to_start = True
        exit_code = main(["doctor", "--ci"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    mock_update.assert_not_called()
    mock_report.assert_called_once()
    assert mock_report.call_args.kwargs["ci"] is True


def test_existing_cli_workflows_remain_compatible_with_version_command(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_file = tmp_path / "devbot.lock"

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={lock_file}\n",
        encoding="utf-8",
    )

    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.polling.PollingService.run_cycle") as mock_run_cycle,
    ):
        exit_code = main(
            ["--once", "--dry-run", "--verbose"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_run_cycle.assert_called_once()


def test_main_starts_and_exits_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CP-001-10: the CLI loads configuration and exits successfully.

    Task 005 replaced the CLI's eager "validate every enabled repository"
    startup step with a real polling iteration (see tests/test_polling.py
    and tests/test_main_loop.py for that behavior in detail). This test
    keeps validating the original checkpoint - config loads, the process
    runs to completion, and the lock is released - using `--once` with no
    enabled repositories so it stays fast and makes no network calls.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_file = tmp_path / "devbot.lock"

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={lock_file}\n",
        encoding="utf-8",
    )

    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with patch("devbot.main._run_startup_self_update", return_value=True):
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0

    # The lock must have been released on normal exit: re-acquiring it
    # immediately after main() returns must succeed.
    post_exit_lock = ProcessLock(lock_file)
    post_exit_lock.acquire()
    post_exit_lock.release()
