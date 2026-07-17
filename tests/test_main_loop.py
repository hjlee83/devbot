import logging
import os
import signal
import subprocess
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.agents.base import AgentRunResult
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.lock import ProcessLock
from devbot.main import _apply_rework_changes, main
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

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.polling.PollingService.run_cycle") as mock_run_cycle,
    ):
        mock_run_cycle.return_value = [PollingResult(status=PollingStatus.NO_READY_TASK)]
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    mock_run_cycle.assert_called_once()


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

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.main.PollingService") as mock_service_cls,
    ):
        mock_service_cls.return_value.run_cycle.return_value = [
            PollingResult(status=PollingStatus.NO_READY_TASK)
        ]
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
    assert kwargs["review_service"].dry_run is True


def test_cli_constructs_rework_service(tmp_path: Path) -> None:
    env_path, repositories_path = _write_fixture(tmp_path)

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.main.PollingService") as mock_service_cls,
    ):
        mock_service_cls.return_value.run_cycle.return_value = [
            PollingResult(status=PollingStatus.NO_READY_TASK)
        ]
        exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    _, kwargs = mock_service_cls.call_args
    assert kwargs["rework_service"] is not None
    assert kwargs["review_service"] is not None


def test_verbose_flag_enables_debug_logging(tmp_path: Path) -> None:
    """CP-013-3: `--verbose` switches this process's logging to DEBUG
    regardless of the configured `LOG_LEVEL` (default INFO here), and does
    so only for this process - it never touches `.env` or the real
    environment (Task 013 동작 규칙 #7)."""
    env_path, repositories_path = _write_fixture(tmp_path)
    logger = logging.getLogger("devbot")
    logger.setLevel(logging.INFO)

    with patch("devbot.polling.PollingService.run_cycle") as mock_run_cycle:
        mock_run_cycle.return_value = [PollingResult(status=PollingStatus.NO_READY_TASK)]
        main(["--once", "--verbose"], env_path=env_path, repositories_path=repositories_path)

    assert logger.level == logging.DEBUG


def test_startup_log_contains_runtime_configuration(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """CP-013-4: the startup log records the resolved implementer/reviewer
    role assignments, dry-run flag, concurrency, and every managed
    repository - what an operator needs to confirm the daemon started with
    the configuration they actually expect."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\n"
        f"GITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n"
        "IMPLEMENTER_AGENT=claude\n"
        "REVIEWER_AGENT=codex\n"
        "MAX_CONCURRENT_JOBS=1\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true\n",
        encoding="utf-8",
    )

    with (
        caplog.at_level(logging.INFO, logger="devbot"),
        patch("devbot.polling.PollingService.run_cycle") as mock_run_cycle,
        patch.dict(os.environ, {"IMPLEMENTER_AGENT": "", "REVIEWER_AGENT": ""}, clear=False),
    ):
        mock_run_cycle.return_value = [PollingResult(status=PollingStatus.NO_READY_TASK)]
        main(["--once", "--dry-run"], env_path=env_path, repositories_path=repositories_path)

    startup_records = [r for r in caplog.records if getattr(r, "event", None) == "startup"]
    assert len(startup_records) == 1
    startup = startup_records[0]
    assert startup.implementer_agent == "claude"
    assert startup.reviewer_agent == "codex"
    assert startup.dry_run is True
    assert startup.max_concurrent_jobs == 1
    assert startup.managed_repository_count == 1

    repo_records = [r for r in caplog.records if getattr(r, "event", None) == "managed_repository"]
    assert len(repo_records) == 1
    assert repo_records[0].repository == "someone/myrepo"
    assert repo_records[0].default_branch == "main"

    # Never leaks the token, in this most basic case either.
    assert "test-token" not in caplog.text


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


def test_daemon_survives_non_fatal_failure() -> None:
    """CP-019-7: a non-fatal Job failure must not terminate the daemon
    process, and the next cycle must still run.

    An exception type `_run_unclaimed_implement_job`'s own
    `except WorkspaceValidationError` does not catch (e.g. a bare `OSError`
    from a misbehaving `ensure_workspace_ready`) previously escaped
    uncaught all the way through `_execute_job` for the common single-job
    cycle (`max_concurrent_jobs=1`, the default) - only the concurrent
    (`ThreadPoolExecutor`) path had a safety net. `PollingService.run_once()`
    must now convert it to `ITERATION_ERROR` instead of raising."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = GitHubIssue(
        repository=repo.full_name,
        number=9,
        title="Do work",
        body="",
        state="open",
        labels=("devbot:ready",),
        created_at=datetime(2026, 1, 1),
    )

    class _SingleIssueGitHubClient:
        def list_issues(self, repository: RepositoryConfig, **_kwargs: object) -> list:
            return [issue]

    def _raise_unexpected(_repository: RepositoryConfig) -> None:
        raise OSError("disk unavailable")

    service = PollingService(
        config=config,
        github_client=_SingleIssueGitHubClient(),
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_raise_unexpected,
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR


def test_run_forever_continues_after_run_once_raises() -> None:
    """CP-019-7, `run_forever()` level: even if a cycle somehow still
    raises, the loop must log it and continue into its next cycle rather
    than crashing the daemon process."""
    config = _config([_repo("myrepo")])
    service = PollingService(
        config=config, github_client=_EmptyGitHubClient(), implementer_runner=MagicMock()
    )

    call_count = 0

    def _flaky_run_once() -> PollingResult:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated unexpected crash")
        return PollingResult(status=PollingStatus.NO_READY_TASK)

    service.run_once = _flaky_run_once  # type: ignore[method-assign]

    class _StopLoop(Exception):
        pass

    sleep_calls: list[float] = []

    def _fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        if len(sleep_calls) >= 2:
            raise _StopLoop()

    with pytest.raises(_StopLoop):
        run_forever(service, poll_interval_seconds=5, sleep_fn=_fake_sleep)

    assert call_count == 2
    assert len(sleep_calls) == 2


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

    Task 023: `main.py` now always wires a real `WorktreeManager.prepare()`
    ahead of the Agent, which shells out to `git` itself (`devbot.worktree`
    imports the same process-wide `subprocess` module `devbot.agents.codex`
    does, so a blanket `patch("devbot.agents.codex.subprocess.run")` would
    intercept both). `origin_path` gives it a real remote to fetch from, and
    the `side_effect` below only fakes the `codex` invocation itself,
    letting every `git` subprocess call run for real.
    """
    workspace_root = tmp_path / "workspace"
    repo_path = workspace_root / "myrepo"
    _init_git_repo(repo_path)

    origin_path = tmp_path / "origin.git"
    origin_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "--bare", str(origin_path)], check=True, capture_output=True
    )
    _run_git("remote", "add", "origin", str(origin_path), cwd=repo_path)
    _run_git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=repo_path)

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

    real_subprocess_run = subprocess.run

    def _fake_subprocess_run(args: object, *a: object, **kw: object) -> MagicMock:
        # Only fake the `codex` invocation itself - `devbot.worktree`'s
        # `git fetch`/`git worktree add` (Task 023) share the same
        # process-wide `subprocess` module and must run for real against
        # `origin_path` above.
        if isinstance(args, (list, tuple)) and args and args[0] == "codex":
            return MagicMock(returncode=1, stdout="", stderr="boom")
        return real_subprocess_run(args, *a, **kw)  # type: ignore[arg-type]

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.github_client.GitHubClient.list_issues", return_value=[ready_issue]),
        patch("devbot.github_client.GitHubClient.list_pull_requests", return_value=[]),
        patch("devbot.github_write_client.GitHubWriteClient.set_labels") as mock_set_labels,
        patch("devbot.github_write_client.GitHubWriteClient.create_comment") as mock_create_comment,
        patch("devbot.agents.codex.subprocess.run", side_effect=_fake_subprocess_run),
    ):
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


def test_daemon_stops_on_fatal_failure(tmp_path: Path) -> None:
    """CP-019-8: a fatal configuration or startup failure (bad config, a
    duplicate daemon instance) terminates safely before polling begins and
    never mutates GitHub state - `GitHubWriteClient` is never even
    constructed, so no write call is even possible."""
    # 1. Fatal configuration failure: no WORKSPACE_ROOT/GITHUB_TOKEN at all.
    empty_env_path = tmp_path / "empty.env"
    empty_env_path.write_text("", encoding="utf-8")
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with patch("devbot.main.GitHubWriteClient") as mock_write_client:
        exit_code = main([], env_path=empty_env_path, repositories_path=repositories_path)

    assert exit_code == 1
    mock_write_client.assert_not_called()

    # 2. Fatal startup failure: a duplicate daemon instance (lock already held).
    lock_file = tmp_path / "devbot.lock"
    env_path, repositories_path = _write_fixture(tmp_path, lock_file=lock_file)
    held_lock = ProcessLock(lock_file)
    held_lock.acquire()
    try:
        with patch("devbot.main.GitHubWriteClient") as mock_write_client:
            exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)
    finally:
        held_lock.release()

    assert exit_code == 1
    mock_write_client.assert_not_called()


def test_apply_rework_changes_raises_when_agent_result_is_unexecuted_and_not_dry_run() -> None:
    """Same regression as the ready-task path (see
    `tests/test_beta_smoke.py::test_unexecuted_non_dry_run_agent_result_blocks_before_delivery`),
    for Task 010's rework path: `ClaudeRunner` reporting a missing CLI or a
    timeout as `executed=False, dry_run=False, returncode=None` must raise
    - so `ReworkService.process()` blocks the Issue - not be treated as a
    successful agent apply that proceeds to verification/commit/push."""
    repo = _repo("myrepo")
    issue = GitHubIssue(
        repository=repo.full_name,
        number=7,
        title="Fix bug",
        body="",
        state="open",
        labels=("devbot:review",),
        created_at=datetime(2026, 1, 1),
    )
    comment = PullRequestComment(
        id=1,
        author="reviewer",
        body="@devbot please fix",
        created_at=datetime(2026, 1, 2),
        reactions={},
    )
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(
        executed=False, dry_run=False, message="Claude CLI가 설치되어 있지 않습니다."
    )

    with pytest.raises(RuntimeError, match="Claude CLI"):
        _apply_rework_changes(implementer_runner, repo, issue, comment)
