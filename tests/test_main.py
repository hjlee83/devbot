from pathlib import Path
from unittest.mock import patch

import pytest

from devbot.lock import ProcessLock
from devbot.main import main


def test_cli_version_prints_package_version(capsys: pytest.CaptureFixture[str]) -> None:
    with patch("devbot.main.package_version", return_value="9.8.7"):
        exit_code = main(["--version"])

    assert exit_code == 0
    assert capsys.readouterr().out == "devbot 9.8.7\n"


def test_cli_version_uses_authoritative_version_source() -> None:
    with patch("devbot.main.package_version", return_value="1.2.3") as mock_version:
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_version.assert_called_once_with("devbot")


def test_cli_version_does_not_load_runtime_config(tmp_path: Path) -> None:
    missing_env = tmp_path / "missing.env"
    invalid_repositories = tmp_path / "repositories.yaml"
    invalid_repositories.write_text("repositories: [", encoding="utf-8")

    with patch("devbot.main.package_version", return_value="1.2.3"):
        exit_code = main(
            ["--version"], env_path=missing_env, repositories_path=invalid_repositories
        )

    assert exit_code == 0


def test_cli_version_does_not_acquire_daemon_lock() -> None:
    with (
        patch("devbot.main.package_version", return_value="1.2.3"),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_lock.assert_not_called()


def test_cli_version_does_not_contact_github() -> None:
    with (
        patch("devbot.main.package_version", return_value="1.2.3"),
        patch("devbot.main.GitHubClient") as mock_read_client,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_read_client.assert_not_called()
    mock_write_client.assert_not_called()


def test_cli_version_does_not_start_polling_or_agents() -> None:
    with (
        patch("devbot.main.package_version", return_value="1.2.3"),
        patch("devbot.main.PollingService") as mock_polling,
        patch("devbot.main.run_forever") as mock_run_forever,
        patch("devbot.main.build_agent_runner") as mock_build_agent_runner,
    ):
        exit_code = main(["--version"])

    assert exit_code == 0
    mock_polling.assert_not_called()
    mock_run_forever.assert_not_called()
    mock_build_agent_runner.assert_not_called()


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

    with patch("devbot.polling.PollingService.run_cycle") as mock_run_cycle:
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

    exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0

    # The lock must have been released on normal exit: re-acquiring it
    # immediately after main() returns must succeed.
    post_exit_lock = ProcessLock(lock_file)
    post_exit_lock.acquire()
    post_exit_lock.release()
