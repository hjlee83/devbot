import os
import signal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

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


def test_continuous_loop_uses_configured_poll_interval() -> None:
    config = _config([_repo("myrepo")])
    service = PollingService(
        config=config, github_client=_EmptyGitHubClient(), agent_runner=MagicMock()
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
        config=config, github_client=_EmptyGitHubClient(), agent_runner=MagicMock()
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
