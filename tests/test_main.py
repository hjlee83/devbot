import logging
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbot.github_client import WorkflowRun
from devbot.goal_planner import GoalPlan
from devbot.lock import ProcessLock
from devbot.main import _run_startup_self_update, main
from devbot.models import DevBotConfig
from devbot.release_ops import PublishOutcome, ReleasePreview, ReleaseReadiness, ReleaseStatus
from devbot.startup import (
    STARTUP_SELF_UPDATE_ENV,
    StartupSelfUpdateError,
    StartupSelfUpdateResult,
)


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


def _release_env(tmp_path: Path) -> tuple[Path, Path]:
    """`env_path`/`repositories_path` for a full `main()` call with exactly
    one enabled repository (`_resolve_repository`'s implicit-target case),
    matching the `--once`/`doctor` test fixtures above."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true\n",
        encoding="utf-8",
    )
    return env_path, repositories_path


def _ready_preview(**overrides: object) -> ReleasePreview:
    defaults: dict[object, object] = dict(
        previous_version="0.1.0",
        next_version="0.2.0",
        increment="minor",
        target_commit="deadbeef",
        target_commit_validated=True,
        previous_release_commit="cafef00d",
        readiness=ReleaseReadiness(ready=True, blockers=()),
        expected_assets=("devbot-0.2.0-linux-x86_64.tar.gz",),
        changes=(),
        notes="## devbot 0.2.0\n",
    )
    defaults.update(overrides)
    return ReleasePreview(**defaults)  # type: ignore[arg-type]


def test_release_preview_command_is_wired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview()

    with (
        patch("devbot.main.fetch_release_preview", return_value=preview) as mock_preview,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["release", "preview"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_preview.assert_called_once()
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "next_version: 0.2.0" in out
    assert "ready: yes" in out


def test_release_preview_reports_not_ready_as_failure(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview(
        readiness=ReleaseReadiness(ready=False, blockers=("main is dirty",))
    )

    with patch("devbot.main.fetch_release_preview", return_value=preview):
        exit_code = main(
            ["release", "preview"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 1


def test_release_status_is_read_only(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    status = ReleaseStatus(
        latest_stable_version="0.1.0",
        latest_release_url="https://github.com/someone/myrepo/releases/tag/v0.1.0",
        latest_release_published_at=datetime(2026, 7, 18, 6, 26, 19, tzinfo=UTC),
        last_published_commit="deadbeef",
        latest_workflow_run=WorkflowRun(
            id=1,
            name="Release",
            status="completed",
            conclusion="success",
            html_url="https://github.com/someone/myrepo/actions/runs/1",
            created_at=datetime(2026, 7, 18, 6, 0, 0, tzinfo=UTC),
            head_sha="deadbeef",
            event="workflow_dispatch",
        ),
        publication_state="up-to-date",
    )

    with (
        patch("devbot.main.build_release_status", return_value=status) as mock_status,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["release", "status"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_status.assert_called_once()
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "publication_state: up-to-date" in out
    assert "latest_stable_version: 0.1.0" in out


def test_release_publish_dry_run_never_dispatches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview()

    with (
        patch("devbot.main.fetch_release_preview", return_value=preview),
        patch("devbot.main.publish_release") as mock_publish,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["release", "publish", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_publish.assert_not_called()
    mock_write_client.assert_not_called()
    assert "dry-run" in capsys.readouterr().out


def test_release_publish_refuses_when_not_ready(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview(
        readiness=ReleaseReadiness(ready=False, blockers=("CI not validated",))
    )

    with (
        patch("devbot.main.fetch_release_preview", return_value=preview),
        patch("devbot.main.publish_release") as mock_publish,
    ):
        exit_code = main(
            ["release", "publish"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 1
    mock_publish.assert_not_called()


def test_release_publish_dispatches_when_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview()
    outcome = PublishOutcome(
        preview=preview,
        workflow_run=WorkflowRun(
            id=2,
            name="Release",
            status="completed",
            conclusion="success",
            html_url="https://github.com/someone/myrepo/actions/runs/2",
            created_at=datetime(2026, 7, 18, 7, 0, 0, tzinfo=UTC),
            head_sha="deadbeef",
            event="workflow_dispatch",
        ),
        release_url="https://github.com/someone/myrepo/releases/tag/v0.2.0",
        tag="v0.2.0",
        validated_assets=("SHA256SUMS", "devbot-0.2.0-linux-x86_64.tar.gz"),
    )

    with (
        patch("devbot.main.fetch_release_preview", return_value=preview),
        patch("devbot.main.publish_release", return_value=outcome) as mock_publish,
    ):
        exit_code = main(
            ["release", "publish"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_publish.assert_called_once()
    assert mock_publish.call_args.kwargs["preview"] is preview
    out = capsys.readouterr().out
    assert "tag: v0.2.0" in out
    assert "release_url: https://github.com/someone/myrepo/releases/tag/v0.2.0" in out


def test_release_command_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    preview = _ready_preview()

    with (
        patch("devbot.main.fetch_release_preview", return_value=preview),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["release", "preview"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


def test_goal_plan_command_is_wired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = GoalPlan(
        goal="Publish the next stable release.",
        decision="already_completed",
        reasons=("capability domain 'release_publish' is already implemented",),
        evidence=("Task 037",),
    )

    with (
        patch("devbot.main.fetch_goal_plan", return_value=plan) as mock_plan,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["goal", "plan", "Publish the next stable release."],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_plan.assert_called_once()
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "decision: already_completed" in out
    assert "Task 037" in out


def test_goal_plan_ambiguous_goal_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = GoalPlan(
        goal="xyz",
        decision="ambiguous",
        reasons=("the Goal has too few significant, actionable words to plan safely",),
        evidence=(),
    )

    with patch("devbot.main.fetch_goal_plan", return_value=plan):
        exit_code = main(
            ["goal", "plan", "xyz"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 1


def test_goal_plan_command_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = GoalPlan(goal="g", decision="already_completed", reasons=(), evidence=())

    with (
        patch("devbot.main.fetch_goal_plan", return_value=plan),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["goal", "plan", "Publish the next stable release."],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


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


def test_dirty_checkout_is_bypassed_when_allow_dirty_skip_and_reason_is_dirty(
    tmp_path: Path,
) -> None:
    """CP-B0-1: a dirty *operator checkout* is the one reason_code safe to
    treat as non-fatal, and only when the caller explicitly opts in."""
    result = StartupSelfUpdateResult(
        repository=str(tmp_path),
        current_sha="abc",
        latest_sha="",
        final_sha="abc",
        result="failed",
        skip_reason="operator checkout dirty",
        reason_code="dirty_checkout",
    )

    with patch("devbot.main.run_startup_self_update", side_effect=StartupSelfUpdateError(result)):
        outcome = _run_startup_self_update(
            _config(tmp_path), logging.getLogger("test"), allow_dirty_skip=True
        )

    assert outcome is True


def test_other_reason_codes_stay_fatal_even_with_allow_dirty_skip(tmp_path: Path) -> None:
    """Only `dirty_checkout` is bypassable - a wrong-branch/fetch/pull
    failure indicates a more serious problem and must still stop the run."""
    result = StartupSelfUpdateResult(
        repository=str(tmp_path),
        current_sha="abc",
        latest_sha="",
        final_sha="abc",
        result="failed",
        skip_reason="current branch is not main: task/not-main",
        reason_code="wrong_branch",
    )

    with patch("devbot.main.run_startup_self_update", side_effect=StartupSelfUpdateError(result)):
        outcome = _run_startup_self_update(
            _config(tmp_path), logging.getLogger("test"), allow_dirty_skip=True
        )

    assert outcome is False


def test_dirty_checkout_stays_fatal_when_allow_dirty_skip_is_not_set(tmp_path: Path) -> None:
    """Backward compatibility: omitting `allow_dirty_skip` (its default is
    False) preserves the pre-CP-B0-1 strict behavior."""
    result = StartupSelfUpdateResult(
        repository=str(tmp_path),
        current_sha="abc",
        latest_sha="",
        final_sha="abc",
        result="failed",
        skip_reason="operator checkout dirty",
        reason_code="dirty_checkout",
    )

    with patch("devbot.main.run_startup_self_update", side_effect=StartupSelfUpdateError(result)):
        outcome = _run_startup_self_update(_config(tmp_path), logging.getLogger("test"))

    assert outcome is False


def test_once_daemon_path_passes_dry_run_as_allow_dirty_skip(tmp_path: Path) -> None:
    """CP-B0-1 wiring: the daemon/--once call site opts into the dirty-
    checkout bypass exactly when the run itself is a dry-run (DRY_RUN
    defaults to "true" - see devbot.config - so only deployments that
    explicitly set DRY_RUN=false keep the strict gate unconditionally)."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true\n",
        encoding="utf-8",
    )

    with (
        patch("devbot.main._run_startup_self_update", return_value=True) as mock_update,
        patch("devbot.polling.PollingService.run_cycle", return_value=()),
    ):
        exit_code = main(
            ["--once", "--dry-run"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    assert mock_update.call_args.kwargs["allow_dirty_skip"] is True


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
