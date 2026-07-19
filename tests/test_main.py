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


def _release_env(
    tmp_path: Path, *, publish_strategy: str | None = None
) -> tuple[Path, Path]:
    """`env_path`/`repositories_path` for a full `main()` call with exactly
    one enabled repository (`_resolve_repository`'s implicit-target case),
    matching the `--once`/`doctor` test fixtures above.

    Task 050: `publish_strategy` is omitted from `repositories.yaml` by
    default, matching every existing config in this file - this is what
    proves backward compatibility for configs written before this task."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    strategy_line = (
        f"\n    publish_strategy: {publish_strategy}" if publish_strategy is not None else ""
    )
    repositories_path.write_text(
        f"repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: true{strategy_line}\n",
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


_RELEASE_PREP_PYPROJECT = """[project]
name = "devbot"
version = "{version}"
requires-python = ">=3.13,<3.14"
"""

_RELEASE_PREP_UV_LOCK = """version = 1
requires-python = "==3.13.*"

[[package]]
name = "devbot"
version = "{version}"
source = {{ editable = "." }}
"""


def _write_release_prep_project(directory: Path, version: str = "0.1.2") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(
        _RELEASE_PREP_PYPROJECT.format(version=version), encoding="utf-8"
    )
    (directory / "uv.lock").write_text(
        _RELEASE_PREP_UV_LOCK.format(version=version), encoding="utf-8"
    )


def test_release_prepare_dry_run_does_not_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    project_dir = tmp_path / "project"
    _write_release_prep_project(project_dir)
    monkeypatch.chdir(project_dir)

    exit_code = main(
        ["release", "prepare", "--level", "patch", "--dry-run"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "old_version: 0.1.2" in out
    assert "new_version: 0.1.3" in out
    assert 'version = "0.1.2"' in (project_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.1.2"' in (project_dir / "uv.lock").read_text(encoding="utf-8")


def test_release_prepare_writes_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    project_dir = tmp_path / "project"
    _write_release_prep_project(project_dir)
    monkeypatch.chdir(project_dir)

    exit_code = main(
        ["release", "prepare", "--level", "minor"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "new_version: 0.2.0" in out
    assert 'version = "0.2.0"' in (project_dir / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "0.2.0"' in (project_dir / "uv.lock").read_text(encoding="utf-8")


def test_release_prepare_rejects_none_level(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    with pytest.raises(SystemExit) as excinfo:
        main(
            ["release", "prepare", "--level", "none"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert excinfo.value.code == 2


def test_release_prepare_error_returns_failure_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    (project_dir / "pyproject.toml").write_text(
        _RELEASE_PREP_PYPROJECT.format(version="0.1.2"), encoding="utf-8"
    )
    (project_dir / "uv.lock").write_text(
        _RELEASE_PREP_UV_LOCK.format(version="0.1.1"), encoding="utf-8"
    )
    monkeypatch.chdir(project_dir)

    exit_code = main(
        ["release", "prepare", "--level", "patch"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_release_prepare_does_not_acquire_daemon_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    project_dir = tmp_path / "project"
    _write_release_prep_project(project_dir)
    monkeypatch.chdir(project_dir)

    with patch("devbot.main.ProcessLock") as mock_lock:
        exit_code = main(
            ["release", "prepare", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


def test_release_prepare_does_not_call_github(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    project_dir = tmp_path / "project"
    _write_release_prep_project(project_dir)
    monkeypatch.chdir(project_dir)

    with patch("devbot.main.GitHubClient") as mock_github_client:
        exit_code = main(
            ["release", "prepare", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_github_client.assert_not_called()


def _publish_preview(**overrides: object):
    from devbot.release_publish import ReleasePublishPreview, ReleaseState, TagState

    defaults: dict[str, object] = dict(
        repository="someone/myrepo",
        version="1.2.3",
        tag="v1.2.3",
        title="v1.2.3",
        target_branch="main",
        target_sha="abc123",
        tag_state=TagState.ABSENT,
        release_state=ReleaseState.ABSENT,
    )
    defaults.update(overrides)
    return ReleasePublishPreview(**defaults)  # type: ignore[arg-type]


def _publish_result(**overrides: object):
    from devbot.release_publish import PublishOutcome, ReleasePublishResult

    defaults: dict[str, object] = dict(
        version="1.2.3",
        tag="v1.2.3",
        target_sha="abc123",
        release_url="https://example.invalid/r",
        outcome=PublishOutcome.PUBLISHED,
    )
    defaults.update(overrides)
    return ReleasePublishResult(**defaults)  # type: ignore[arg-type]


def test_release_publish_prepared_dry_run_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    with (
        patch(
            "devbot.main.preview_release_publish", return_value=_publish_preview()
        ) as mock_preview,
        patch("devbot.main.publish_prepared_release") as mock_publish,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["release", "publish-prepared", "--notes-file", str(notes_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_preview.assert_called_once()
    mock_publish.assert_not_called()
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "tag: v1.2.3" in out
    assert "dry-run" in out


def test_release_publish_prepared_writes_on_real_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    with patch(
        "devbot.main.publish_prepared_release", return_value=_publish_result()
    ) as mock_publish:
        exit_code = main(
            ["release", "publish-prepared", "--notes-file", str(notes_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_publish.assert_called_once()
    out = capsys.readouterr().out
    assert "outcome: published" in out
    assert "release_url: https://example.invalid/r" in out


def test_release_publish_prepared_error_returns_failure_exit_code(tmp_path: Path) -> None:
    from devbot.release_publish import DirtyWorktreeError

    env_path, repositories_path = _release_env(tmp_path)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    with patch(
        "devbot.main.publish_prepared_release",
        side_effect=DirtyWorktreeError("checkout has uncommitted changes"),
    ):
        exit_code = main(
            ["release", "publish-prepared", "--notes-file", str(notes_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1


def test_release_publish_prepared_missing_notes_file_returns_failure_exit_code(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["release", "publish-prepared", "--notes-file", str(tmp_path / "does-not-exist.md")],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_release_publish_prepared_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    with (
        patch("devbot.main.preview_release_publish", return_value=_publish_preview()),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["release", "publish-prepared", "--notes-file", str(notes_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


# --------------------------------------------------------------------------
# Task 050: `devbot release strategy` inspection, and both `release publish`
# and `release publish-prepared` refusing a repository whose effective
# publish strategy doesn't match what the command requires.
# --------------------------------------------------------------------------


def test_release_strategy_command_reports_omitted_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["release", "strategy"], env_path=env_path, repositories_path=repositories_path
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "repository: someone/myrepo" in out
    assert "configured: omitted" in out
    assert "effective: workflow" in out
    assert "defaulted: yes" in out


def test_release_strategy_command_reports_explicit_workflow(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="workflow")

    exit_code = main(
        ["release", "strategy"], env_path=env_path, repositories_path=repositories_path
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "configured: workflow" in out
    assert "effective: workflow" in out
    assert "defaulted: no" in out


def test_release_strategy_command_reports_explicit_direct(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")

    exit_code = main(
        ["release", "strategy"], env_path=env_path, repositories_path=repositories_path
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "configured: direct" in out
    assert "effective: direct" in out
    assert "defaulted: no" in out


def test_release_strategy_command_rejects_invalid_configuration(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="bogus")

    exit_code = main(
        ["release", "strategy"], env_path=env_path, repositories_path=repositories_path
    )

    assert exit_code == 1
    assert "release strategy 오류" in capsys.readouterr().err


def test_release_strategy_command_does_not_construct_github_client(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    with (
        patch("devbot.main.GitHubClient") as mock_client,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["release", "strategy"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_client.assert_not_called()
    mock_write_client.assert_not_called()
    mock_lock.assert_not_called()


def test_release_publish_refuses_direct_configured_repository_before_github_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")

    with (
        patch("devbot.main.GitHubClient") as mock_client,
        patch("devbot.main.fetch_release_preview") as mock_preview,
        patch("devbot.main.publish_release") as mock_publish,
    ):
        exit_code = main(
            ["release", "publish"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 1
    mock_client.assert_not_called()
    mock_preview.assert_not_called()
    mock_publish.assert_not_called()
    assert "release publish 오류" in capsys.readouterr().err


def test_release_publish_dry_run_still_enforces_strategy_mismatch(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")

    with patch("devbot.main.fetch_release_preview") as mock_preview:
        exit_code = main(
            ["release", "publish", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1
    mock_preview.assert_not_called()


def test_release_publish_prepared_refuses_default_strategy_without_writes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # No mocks on preview_release_publish/publish_prepared_release/GitHubClient
    # on purpose: Task 050's guard is the very first line of
    # `preview_release_publish`, which `publish_prepared_release` always
    # calls first, so this refuses before any filesystem or Git access -
    # this test exercises that real call chain end to end.
    env_path, repositories_path = _release_env(tmp_path)
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    exit_code = main(
        ["release", "publish-prepared", "--notes-file", str(notes_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    assert "release publish-prepared 오류" in capsys.readouterr().err


def test_release_publish_prepared_dry_run_still_enforces_strategy_mismatch(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path, publish_strategy="workflow")
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")

    exit_code = main(
        ["release", "publish-prepared", "--notes-file", str(notes_file), "--dry-run"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    assert "release publish-prepared 오류" in capsys.readouterr().err


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


def _execution_report(*, ready: bool = True, executed: bool = False, blockers: tuple = ()):
    from devbot.goal_executor import ExecutionPlan, ExecutionReadiness, ExecutionReport
    from devbot.goal_planner import PlannedTask

    plan = GoalPlan(
        goal="Add a global PATH launcher.",
        decision="single_task",
        reasons=(),
        evidence=("Task 032 Out of Scope",),
        planned_tasks=(
            PlannedTask(
                title="Global PATH Launcher",
                objective="obj",
                dependencies=(),
                expected_deliverables=("d",),
                acceptance_criteria=("a",),
                order=1,
            ),
        ),
    )
    execution_plan = ExecutionPlan(
        goal=plan.goal,
        plan=plan,
        selected_task=plan.planned_tasks[0],
        already_materialized=False,
        existing_issue_number=None,
        existing_issue_url=None,
        task_number=41,
        branch="task/041-global-path-launcher",
        contract_path="tasks/041-global-path-launcher.md",
        result_path="results/041-global-path-launcher.md",
        issue_title="Task 041: Global PATH Launcher",
        issue_body="body",
        contract_content="content",
        readiness=ExecutionReadiness(ready=ready, blockers=blockers),
    )
    return ExecutionReport(
        goal=plan.goal,
        execution_plan=execution_plan,
        executed=executed,
        materialize_result=None,
        next_operator_action="re-run with --confirm",
    )


def test_goal_execute_dry_run_never_constructs_write_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=True, executed=False)

    with (
        patch("devbot.main.execute_goal", return_value=report) as mock_execute,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["goal", "execute", "Add a global PATH launcher.", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write_client.assert_not_called()
    assert mock_execute.call_args.kwargs["confirm"] is False
    out = capsys.readouterr().out
    assert "executed: no" in out
    assert "--confirm" in out


def test_goal_execute_confirm_constructs_write_client_and_calls_execute(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=True, executed=True)

    with (
        patch("devbot.main.execute_goal", return_value=report) as mock_execute,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["goal", "execute", "Add a global PATH launcher.", "--task", "1", "--confirm"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write_client.assert_called_once()
    assert mock_execute.call_args.kwargs["confirm"] is True
    assert mock_execute.call_args.kwargs["task_order"] == 1


def test_goal_execute_dry_run_flag_overrides_confirm(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=True, executed=False)

    with (
        patch("devbot.main.execute_goal", return_value=report) as mock_execute,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["goal", "execute", "Add a global PATH launcher.", "--confirm", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write_client.assert_not_called()
    assert mock_execute.call_args.kwargs["confirm"] is False


def test_goal_execute_blocked_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=False, executed=False, blockers=("dirty checkout",))

    with patch("devbot.main.execute_goal", return_value=report):
        exit_code = main(
            ["goal", "execute", "Add a global PATH launcher.", "--confirm"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1


def test_goal_execute_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=True, executed=False)

    with (
        patch("devbot.main.execute_goal", return_value=report),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["goal", "execute", "Add a global PATH launcher.", "--dry-run"],
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


def test_daemon_dispatch_backend_unchanged_without_agents_registry_file(
    tmp_path: Path,
) -> None:
    """Task 041 CP: with no `config/agents.yaml`, the Router synthesizes a
    registry from `IMPLEMENTER_AGENT`/`REVIEWER_AGENT`, so the resolved
    backend passed to `build_agent_runner` (the unchanged execution
    backend) is byte-for-byte what pre-Task-041 code passed directly."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n"
        "IMPLEMENTER_AGENT=claude\nREVIEWER_AGENT=codex\n"
        f"DEVBOT_AGENTS_PATH={tmp_path / 'no-such-agents.yaml'}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.main.build_agent_runner") as mock_build_runner,
    ):
        exit_code = main(
            ["--once", "--dry-run"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    backends_used = [call.args[0] for call in mock_build_runner.call_args_list]
    assert backends_used == ["claude", "codex"]


def test_daemon_dispatch_uses_router_resolved_role(tmp_path: Path) -> None:
    """Task 041 CP: the daemon asks the Router for the "implementer"/
    "reviewer" Role's Agent - a `config/agents.yaml` that resolves to a
    *different* backend than `IMPLEMENTER_AGENT`/`REVIEWER_AGENT` proves the
    call site no longer reads those fields directly."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    agents_path = tmp_path / "agents.yaml"
    agents_path.write_text(
        "agents:\n"
        "  - id: routed-implementer\n"
        "    backend: claude\n"
        "    priority: 100\n"
        "    supported_roles: [implementer]\n"
        "  - id: routed-reviewer\n"
        "    backend: codex\n"
        "    priority: 100\n"
        "    supported_roles: [reviewer]\n",
        encoding="utf-8",
    )
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n"
        f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n"
        # Deliberately the *opposite* backends of agents.yaml above - if the
        # daemon still resolved these directly, backends_used would be
        # ["codex", "claude"] instead of ["claude", "codex"].
        "IMPLEMENTER_AGENT=codex\nREVIEWER_AGENT=claude\n"
        f"DEVBOT_AGENTS_PATH={agents_path}\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.main.build_agent_runner") as mock_build_runner,
    ):
        exit_code = main(
            ["--once", "--dry-run"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    backends_used = [call.args[0] for call in mock_build_runner.call_args_list]
    assert backends_used == ["claude", "codex"]


def test_role_list_command_is_wired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    with patch("devbot.main.ProcessLock") as mock_lock:
        exit_code = main(["role", "list"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    mock_lock.assert_not_called()
    out = capsys.readouterr().out
    assert "implementer" in out
    assert "reviewer" in out


def test_role_resolve_command_is_wired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["role", "resolve", "implementer"], env_path=env_path, repositories_path=repositories_path
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "role: implementer" in out
    assert "resolved_agent_id:" in out


def test_role_resolve_unconfigured_role_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["role", "resolve", "nonexistent-role"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_agent_list_command_is_wired(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(["agent", "list"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "backend=" in out
    assert "priority=" in out


def _specification(**overrides: object):
    from devbot.specification import Specification

    defaults: dict[str, object] = dict(
        task_number=99,
        slug="sample",
        path=Path("specifications/099-sample.md"),
        content="# Specification: Task 099 — Sample\n\n# Overview\n",
    )
    defaults.update(overrides)
    return Specification(**defaults)  # type: ignore[arg-type]


def test_specification_show_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()

    with (
        patch("devbot.main.generate_specification", return_value=specification) as mock_generate,
        patch("devbot.main.write_specification") as mock_write,
    ):
        exit_code = main(
            ["specification", "show", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_generate.assert_called_once()
    mock_write.assert_not_called()
    assert "# Specification: Task 099" in capsys.readouterr().out


def test_specification_show_passes_template_override(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()

    with patch("devbot.main.generate_specification", return_value=specification) as mock_generate:
        exit_code = main(
            ["specification", "show", "--task", "99", "--template", "docs"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    assert mock_generate.call_args.kwargs["template_id"] == "docs"


def test_specification_generate_writes_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()
    written_path = Path("specifications/099-sample.md")

    with (
        patch("devbot.main.generate_specification", return_value=specification),
        patch("devbot.main.write_specification", return_value=written_path) as mock_write,
    ):
        exit_code = main(
            ["specification", "generate", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write.assert_called_once_with(specification)
    out = capsys.readouterr().out
    assert "path: specifications/099-sample.md" in out


def test_specification_generate_dry_run_does_not_write(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()

    with (
        patch("devbot.main.generate_specification", return_value=specification),
        patch("devbot.main.write_specification") as mock_write,
    ):
        exit_code = main(
            ["specification", "generate", "--task", "99", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write.assert_not_called()


def test_specification_generate_passes_template_override(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()

    with patch("devbot.main.generate_specification", return_value=specification) as mock_generate:
        exit_code = main(
            ["specification", "generate", "--task", "99", "--template", "bugfix", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    assert mock_generate.call_args.kwargs["template_id"] == "bugfix"


def test_specification_error_returns_failure_exit_code(tmp_path: Path) -> None:
    from devbot.specification import ContractMissingError

    env_path, repositories_path = _release_env(tmp_path)

    with patch(
        "devbot.main.generate_specification",
        side_effect=ContractMissingError("no Task Contract found for Task 999"),
    ):
        exit_code = main(
            ["specification", "show", "--task", "999"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1


def test_specification_command_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    specification = _specification()

    with (
        patch("devbot.main.generate_specification", return_value=specification),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["specification", "show", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


def _validation_result(**overrides: object):
    from devbot.specification_validation import SpecificationValidationResult

    defaults: dict[str, object] = dict(
        task_number=99,
        specification_path=Path("specifications/099-sample.md"),
        passed=True,
        errors=(),
        warnings=(),
    )
    defaults.update(overrides)
    return SpecificationValidationResult(**defaults)  # type: ignore[arg-type]


def _sample_error_issue():
    from devbot.specification_validation import ValidationIssue, ValidationSeverity

    return ValidationIssue(
        code="SPV-005",
        severity=ValidationSeverity.ERROR,
        message="empty required body for subsection 'Background'",
        section="Background",
        line=10,
    )


def test_specification_validate_text_output_passes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    result = _validation_result()

    with patch("devbot.main.validate_specification_file", return_value=result) as mock_validate:
        exit_code = main(
            ["specification", "validate", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_validate.assert_called_once()
    out = capsys.readouterr().out
    assert "result: PASS" in out


def test_specification_validate_json_output_is_valid(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    env_path, repositories_path = _release_env(tmp_path)
    result = _validation_result()

    with patch("devbot.main.validate_specification_file", return_value=result):
        exit_code = main(
            ["specification", "validate", "--task", "99", "--format", "json"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task_number"] == 99
    assert payload["passed"] is True


def test_specification_validate_failure_exits_one(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    result = _validation_result(errors=(_sample_error_issue(),), passed=False)

    with patch("devbot.main.validate_specification_file", return_value=result):
        exit_code = main(
            ["specification", "validate", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1


def test_specification_validate_operational_error_exits_two(tmp_path: Path) -> None:
    from devbot.specification_validation import SpecificationNotFoundError

    env_path, repositories_path = _release_env(tmp_path)

    with patch(
        "devbot.main.validate_specification_file",
        side_effect=SpecificationNotFoundError("no Specification found for Task 999"),
    ):
        exit_code = main(
            ["specification", "validate", "--task", "999"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 2


def test_specification_validate_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    result = _validation_result()

    with (
        patch("devbot.main.validate_specification_file", return_value=result),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["specification", "validate", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


def test_specification_validate_does_not_call_github(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    result = _validation_result()

    with (
        patch("devbot.main.validate_specification_file", return_value=result),
        patch("devbot.main.GitHubClient") as mock_github_client,
    ):
        exit_code = main(
            ["specification", "validate", "--task", "99"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_github_client.assert_not_called()


def test_specification_templates_lists_registered_templates(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    with patch("devbot.main.ProcessLock") as mock_lock:
        exit_code = main(
            ["specification", "templates"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()
    out = capsys.readouterr().out
    assert "bugfix:" in out
    assert out.index("bugfix:") < out.index("docs:") < out.index("feature:")


def test_specification_template_show_is_read_only(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    with (
        patch("devbot.main.ProcessLock") as mock_lock,
        patch("devbot.main.GitHubClient") as mock_github_client,
    ):
        exit_code = main(
            ["specification", "template", "show", "--template", "internal"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()
    mock_github_client.assert_not_called()
    assert "template: internal" in capsys.readouterr().out


def test_specification_template_show_unknown_returns_nonzero(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["specification", "template", "show", "--template", "release"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_goal_dispatch_shows_role_resolution_without_invoking_agent(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report = _execution_report(ready=True, executed=False)

    with (
        patch("devbot.main.execute_goal", return_value=report),
        patch("devbot.main.build_agent_runner") as mock_build_runner,
    ):
        exit_code = main(
            ["goal", "dispatch", "Add a global PATH launcher.", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_build_runner.assert_not_called()
    out = capsys.readouterr().out
    assert "resolved_role: implementer ->" in out
    assert "[not invoked]" in out


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
