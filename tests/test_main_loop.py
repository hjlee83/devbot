import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.github_client import GitHubIssue
from devbot.lock import ProcessLock
from devbot.main import main
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.polling import PollingResult, PollingService, PollingStatus, run_forever


def _repo(name: str, *, enabled: bool = True) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo=name, enabled=enabled, local_path=Path(f"/tmp/workspace/{name}")
    )


def _config(repositories: list[RepositoryConfig]) -> DevBotConfig:
    return DevBotConfig(
        workspace_root=Path("/tmp/workspace"),
        poll_interval_seconds=60,
        lock_file=Path("/tmp/devbot.lock"),
        default_agent="codex",
        implementer_agent="codex",
        reviewer_agent="codex",
        max_concurrent_jobs=1,
        dry_run=True,
        github_token="test-token",
        repositories=tuple(repositories),
    )


class _EmptyGitHubClient:
    def list_issues(self, repository: RepositoryConfig, **_kwargs: object) -> list:
        return []


def _write_fixture(tmp_path: Path, *, lock_file: Path | None = None) -> tuple[Path, Path]:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    resolved_lock_file = lock_file or (tmp_path / "devbot.lock")

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\n"
        f"GITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={resolved_lock_file}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )
    return env_path, repositories_path


def test_run_once_exits_after_single_iteration(tmp_path: Path) -> None:
    env_path, repositories_path = _write_fixture(tmp_path)

    with patch("devbot.polling.PollingService.run_once") as mock_run_once:
        mock_run_once.return_value = PollingResult(status=PollingStatus.NO_READY_TASK)
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    mock_run_once.assert_called_once()


def test_cli_dry_run_flag_forces_dry_run_regardless_of_env(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\n"
        f"GITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n"
        f"DRY_RUN=false\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with patch("devbot.main.PollingService") as mock_service_cls:
        mock_service_cls.return_value.run_once.return_value = PollingResult(
            status=PollingStatus.NO_READY_TASK
        )
        exit_code = main(
            ["--once", "--dry-run"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    _, kwargs = mock_service_cls.call_args
    assert kwargs["config"].dry_run is True
    assert kwargs["implementer_runner"].dry_run is True
    assert kwargs["state_writer"].dry_run is True
    assert kwargs["delivery"].dry_run is True
    assert kwargs["rework_service"].dry_run is True


def test_cli_constructs_rework_service(tmp_path: Path) -> None:
    env_path, repositories_path = _write_fixture(tmp_path)

    with patch("devbot.main.PollingService") as mock_service_cls:
        mock_service_cls.return_value.run_once.return_value = PollingResult(
            status=PollingStatus.NO_READY_TASK
        )
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    _, kwargs = mock_service_cls.call_args
    assert kwargs["rework_service"] is not None


def test_continuous_loop_uses_configured_poll_interval() -> None:
    config = _config([_repo("myrepo")])
    service = PollingService(
        config=config, github_client=_EmptyGitHubClient(), implementer_runner=MagicMock()
    )

    class _StopLoop(Exception):
        pass

    intervals: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        intervals.append(seconds)
        raise _StopLoop()

    with pytest.raises(_StopLoop):
        run_forever(service, poll_interval_seconds=42, sleep_fn=_fake_sleep)

    assert intervals == [42]


def test_shutdown_signal_stops_loop_gracefully() -> None:
    config = _config([_repo("myrepo")])
    service = PollingService(
        config=config, github_client=_EmptyGitHubClient(), implementer_runner=MagicMock()
    )
    run_once_spy = MagicMock(side_effect=service.run_once)
    service.run_once = run_once_spy  # type: ignore[method-assign]

    sleep_calls = 0

    def _fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        # Deliver a real SIGINT to this process, simulating Ctrl-C arriving
        # while the loop is asleep between iterations.
        os.kill(os.getpid(), signal.SIGINT)

    run_forever(service, poll_interval_seconds=1, sleep_fn=_fake_sleep)

    assert run_once_spy.call_count == 1
    assert sleep_calls == 1


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


def test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero(
    tmp_path: Path,
) -> None:
    """A real (non-dry-run) AgentRunner that exits non-zero must make
    `main --once` exit non-zero too, not report success.

    DRY_RUN=false also activates the real (non-dry-run) `IssueStateWriter`
    wired in by `main.py` (Task 009), so `GitHubWriteClient`'s HTTP calls
    (`claim()` before the agent runs, `block()` after it fails) must be
    mocked here too - otherwise this test would silently make real network
    requests to api.github.com instead of exercising the AGENT_FAILED path.
    """
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "myrepo"
    _init_git_repo(repo_path)

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\n"
        f"GITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n"
        f"DRY_RUN=false\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true\n",
        encoding="utf-8",
    )

    ready_issue = GitHubIssue(
        repository="someone/myrepo",
        number=1,
        title="Broken issue",
        body="",
        state="open",
        labels=("devbot:ready",),
        created_at=datetime(2026, 1, 1),
    )

    with (
        patch("devbot.github_client.GitHubClient.list_issues", return_value=[ready_issue]),
        patch("devbot.github_write_client.GitHubWriteClient.set_labels") as mock_set_labels,
        patch("devbot.github_write_client.GitHubWriteClient.create_comment") as mock_create_comment,
        patch("devbot.agents.codex.subprocess.run") as mock_subprocess_run,
    ):
        mock_subprocess_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 1
    # Claimed (ready -> working) before the agent ran, then blocked
    # (working -> blocked) after it failed - not a network-error short
    # circuit into ITERATION_ERROR, which would also exit 1 but for the
    # wrong reason.
    assert mock_set_labels.call_count == 2
    mock_create_comment.assert_called_once()


def test_main_loop_respects_process_lock(tmp_path: Path) -> None:
    lock_file = tmp_path / "devbot.lock"
    env_path, repositories_path = _write_fixture(tmp_path, lock_file=lock_file)

    held_lock = ProcessLock(lock_file)
    held_lock.acquire()
    try:
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)
    finally:
        held_lock.release()

    assert exit_code == 1
