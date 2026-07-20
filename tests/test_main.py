import logging
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from devbot.github_client import GitHubIssue, PullRequest, WorkflowRun
from devbot.goal_execution_foundation import (
    AgentSelection,
    AITokenBudget,
    ApiUsage,
    DefinitionOfDoneCriterion,
    ExecutionMode,
    ExecutionPolicy,
    GateKind,
    GoalExecutionPlan,
    ResourceStrategy,
    RoleExecutionPolicy,
    TaskGraph,
    TaskNode,
    VerificationGate,
    VerificationOutcome,
    VerificationPlan,
    VerificationRequest,
)
from devbot.goal_planner import GoalPlan
from devbot.goal_runtime_adapter import (
    APPROVED_GOAL_PLAN_SCHEMA_VERSION,
    ApprovedGoalPlanDocument,
    GoalTaskBinding,
    write_approved_goal_plan,
)
from devbot.lock import ProcessLock
from devbot.main import _build_goal_review_gate, _run_startup_self_update, main
from devbot.models import DevBotConfig, RepositoryConfig, TaskState
from devbot.release_ops import PublishOutcome, ReleasePreview, ReleaseReadiness, ReleaseStatus
from devbot.repository_registry import load_registry
from devbot.review import ReviewResult
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


def _approved_goal_plan_document() -> ApprovedGoalPlanDocument:
    budget = AITokenBudget(
        max_planner_calls=0,
        max_implementation_retries=1,
        max_architecture_review_calls_per_node=0,
        max_architecture_review_calls_per_goal=0,
        api_usage=ApiUsage.FORBIDDEN,
    )
    policy = ExecutionPolicy(
        roles={
            "implementer": RoleExecutionPolicy(
                primary=AgentSelection(
                    execution_mode=ExecutionMode.SUBSCRIPTION_RUNTIME,
                    resource="subscription",
                    runtime="cli",
                )
            )
        }
    )
    return ApprovedGoalPlanDocument(
        schema_version=APPROVED_GOAL_PLAN_SCHEMA_VERSION,
        plan=GoalExecutionPlan(
            goal_id="goal-141",
            objective="Run approved plan.",
            approved_scope=("adapter",),
            non_goals=("planning",),
            definition_of_done=(
                DefinitionOfDoneCriterion("complete", GateKind.GOAL),
            ),
            task_graph=TaskGraph((TaskNode("a", "Task A"),)),
            verification_plan=VerificationPlan((VerificationGate(GateKind.TECHNICAL),)),
            execution_policy=policy,
            resource_strategy=ResourceStrategy(
                input_channel="chatgpt",
                execution_policy=policy,
                budget=budget,
            ),
            budget=budget,
            exit_conditions=("review requested",),
            escalation_conditions=("manual action",),
        ),
        task_bindings=(
            GoalTaskBinding("a", "someone/myrepo", 141, "tasks/141-a.md", "task/141-a"),
        ),
    )


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


# --------------------------------------------------------------------------
# Task 051: `devbot release run` - composes recommendation, preparation,
# strategy resolution, and exactly one existing publish path.
# --------------------------------------------------------------------------


def _run_plan(**overrides: object):
    from devbot.release_classification import ReleaseRecommendation
    from devbot.release_orchestration import ReleaseRunPlan, ReleaseRunStage
    from devbot.release_publish_strategy import ReleasePublishStrategy

    defaults: dict[str, object] = dict(
        repository="someone/myrepo",
        recommendation=ReleaseRecommendation.PATCH,
        current_version="1.2.3",
        target_version="1.2.4",
        effective_strategy=ReleasePublishStrategy.WORKFLOW,
        preparation_required=True,
        publish_route=ReleaseRunStage.WORKFLOW_PUBLISH,
        direct_notes_available=False,
    )
    defaults.update(overrides)
    return ReleaseRunPlan(**defaults)  # type: ignore[arg-type]


def _run_result(**overrides: object):
    from devbot.release_orchestration import ReleaseRunOutcome, ReleaseRunResult

    defaults: dict[str, object] = dict(
        plan=_run_plan(),
        outcome=ReleaseRunOutcome.WORKFLOW_PUBLISHED,
        preparation=None,
        workflow_outcome=None,
        direct_result=None,
    )
    defaults.update(overrides)
    return ReleaseRunResult(**defaults)  # type: ignore[arg-type]


def test_release_run_dry_run_workflow_strategy(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = _run_plan()

    with (
        patch("devbot.main.build_release_run_plan", return_value=plan) as mock_plan,
        patch("devbot.main.GitHubClient") as mock_client,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        exit_code = main(
            ["release", "run", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_plan.assert_called_once()
    # Task 051 review fix: planning now performs read-only GitHub calls so
    # the plan matches what real execution would actually publish - a
    # GitHubClient (read-only) IS constructed even for dry-run, but never a
    # GitHubWriteClient.
    mock_client.assert_called_once()
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "target_version: 1.2.4" in out
    assert "publish_route: workflow_publish" in out
    assert "dry-run" in out


def test_release_run_dry_run_workflow_reports_recommendation_conflict_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = _run_plan(
        recommendation_conflict=(
            "the release increment computed from merged PR history is 'minor', "
            "not 'patch' as requested via --level"
        )
    )

    with patch("devbot.main.build_release_run_plan", return_value=plan):
        exit_code = main(
            ["release", "run", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "blocker" in out
    assert "minor" in out


def test_release_run_dry_run_direct_strategy_reports_notes_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.release_orchestration import ReleaseRunStage
    from devbot.release_publish_strategy import ReleasePublishStrategy

    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")
    plan = _run_plan(
        effective_strategy=ReleasePublishStrategy.DIRECT,
        publish_route=ReleaseRunStage.DIRECT_PUBLISH,
        direct_notes_available=False,
    )

    with patch("devbot.main.build_release_run_plan", return_value=plan):
        exit_code = main(
            ["release", "run", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "blocker" in out
    assert "--notes-file" in out


def test_release_run_none_recommendation_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["release", "run", "--level", "none"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "release run 오류" in err
    assert "recommendation" in err


def test_release_run_missing_notes_file_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        [
            "release",
            "run",
            "--level",
            "patch",
            "--notes-file",
            str(tmp_path / "does-not-exist.md"),
        ],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_release_run_workflow_success(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.release_ops import PublishOutcome as WorkflowPublishOutcome

    env_path, repositories_path = _release_env(tmp_path)
    outcome = WorkflowPublishOutcome(
        preview=object(),
        workflow_run=object(),
        release_url="https://example.invalid/releases/tag/v1.2.4",
        tag="v1.2.4",
        validated_assets=("SHA256SUMS",),
    )
    result = _run_result(workflow_outcome=outcome)

    with patch("devbot.main.run_release", return_value=result) as mock_run:
        exit_code = main(
            ["release", "run", "--level", "patch"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_run.assert_called_once()
    out = capsys.readouterr().out
    assert "outcome: workflow_published" in out
    assert "tag: v1.2.4" in out
    assert "release_url: https://example.invalid/releases/tag/v1.2.4" in out


def test_release_run_direct_success(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from devbot.release_orchestration import ReleaseRunOutcome
    from devbot.release_publish import PublishOutcome as DirectPublishOutcome
    from devbot.release_publish import ReleasePublishResult

    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")
    direct_result = ReleasePublishResult(
        version="1.2.4",
        tag="v1.2.4",
        target_sha="abc123",
        release_url="https://example.invalid/r",
        outcome=DirectPublishOutcome.PUBLISHED,
    )
    result = _run_result(outcome=ReleaseRunOutcome.DIRECT_PUBLISHED, direct_result=direct_result)

    with patch("devbot.main.run_release", return_value=result):
        exit_code = main(
            ["release", "run", "--level", "patch", "--notes-file", str(notes_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "outcome: direct_published" in out
    assert "tag: v1.2.4" in out


def test_release_run_prepared_pending_commit_prints_next_step(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.release_classification import ReleaseRecommendation as _ReleaseRecommendation
    from devbot.release_orchestration import ReleaseRunOutcome
    from devbot.release_preparation import ReleasePreparationResult

    env_path, repositories_path = _release_env(tmp_path, publish_strategy="direct")
    notes_file = tmp_path / "notes.md"
    notes_file.write_text("some notes", encoding="utf-8")
    preparation = ReleasePreparationResult(
        recommendation=_ReleaseRecommendation.PATCH,
        old_version="1.2.3",
        new_version="1.2.4",
        changed_paths=("pyproject.toml", "uv.lock"),
    )
    result = _run_result(
        outcome=ReleaseRunOutcome.PREPARED_PENDING_COMMIT, preparation=preparation
    )

    with patch("devbot.main.run_release", return_value=result):
        exit_code = main(
            ["release", "run", "--level", "patch", "--notes-file", str(notes_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "outcome: prepared_pending_commit" in out
    assert "publish-prepared" in out


def test_release_run_stage_error_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.release_orchestration import ReleaseRunStage, ReleaseRunStageError

    env_path, repositories_path = _release_env(tmp_path)

    with patch(
        "devbot.main.run_release",
        side_effect=ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, "boom"),
    ):
        exit_code = main(
            ["release", "run", "--level", "patch"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "release run 오류" in err
    assert "workflow_publish" in err


def test_release_run_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan = _run_plan()

    with (
        patch("devbot.main.build_release_run_plan", return_value=plan),
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["release", "run", "--level", "patch", "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


# --------------------------------------------------------------------------
# Task 052: `devbot release recommend` - aggregates every merged Task PR's
# Contract since the latest stable Release into one recommendation.
# --------------------------------------------------------------------------


def _recommend_aggregation(**overrides: object):
    from devbot.release_classification import ReleaseRecommendation
    from devbot.release_recommendation_aggregation import ReleaseRecommendationAggregation

    defaults: dict[str, object] = dict(
        repository="someone/myrepo",
        boundary_version="1.2.3",
        boundary_tag="v1.2.3",
        boundary_commit="boundarysha",
        inspected_pr_count=0,
        evidence=(),
        excluded=(),
        recommendation=ReleaseRecommendation.NONE,
    )
    defaults.update(overrides)
    return ReleaseRecommendationAggregation(**defaults)  # type: ignore[arg-type]


def test_release_recommend_command_is_wired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    aggregation = _recommend_aggregation()

    with patch(
        "devbot.main.build_release_recommendation_aggregation", return_value=aggregation
    ) as mock_build:
        exit_code = main(
            ["release", "recommend"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_build.assert_called_once()
    out = capsys.readouterr().out
    assert "boundary_version: 1.2.3" in out
    assert "recommendation: none" in out


def test_release_recommend_never_constructs_write_client(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    aggregation = _recommend_aggregation()

    with (
        patch(
            "devbot.main.build_release_recommendation_aggregation", return_value=aggregation
        ),
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["release", "recommend"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    mock_write_client.assert_not_called()
    mock_lock.assert_not_called()


def test_release_recommend_error_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.release_recommendation_aggregation import MissingContractError

    env_path, repositories_path = _release_env(tmp_path)

    with patch(
        "devbot.main.build_release_recommendation_aggregation",
        side_effect=MissingContractError("PR #1: expected Contract 'tasks/001-x.md' missing"),
    ):
        exit_code = main(
            ["release", "recommend"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "release recommend 오류" in err
    assert "tasks/001-x.md" in err


def test_release_recommend_renders_evidence_and_final_recommendation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import UTC, datetime

    from devbot.release_classification import ReleaseRecommendation
    from devbot.release_recommendation_aggregation import ReleaseRecommendationEvidence

    env_path, repositories_path = _release_env(tmp_path)
    evidence = ReleaseRecommendationEvidence(
        pr_number=1,
        pr_url="https://example.invalid/pull/1",
        merge_commit_sha="sha1",
        merged_at=datetime(2026, 7, 19, tzinfo=UTC),
        task_number=1,
        contract_path="tasks/001-test-task.md",
        recommendation=ReleaseRecommendation.MINOR,
        classification_reasons=("release_impact=feature",),
    )
    aggregation = _recommend_aggregation(
        evidence=(evidence,), recommendation=ReleaseRecommendation.MINOR
    )

    with patch(
        "devbot.main.build_release_recommendation_aggregation", return_value=aggregation
    ):
        exit_code = main(
            ["release", "recommend"], env_path=env_path, repositories_path=repositories_path
        )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "PR #1" in out
    assert "release_impact=feature" in out
    assert "recommendation: minor" in out


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


# --------------------------------------------------------------------------
# Task 053: `devbot review report` - read-only validate/render boundary for
# an existing review report JSON payload.
# --------------------------------------------------------------------------


def _write_report_file(tmp_path: Path, payload: dict) -> Path:
    import json

    path = tmp_path / "report.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_review_report_text_output_for_blocker(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path,
        {
            "findings": [
                {"severity": "blocker", "code": "SEC-001", "message": "SQL injection risk"}
            ]
        },
    )

    exit_code = main(
        ["review", "report", "--input", str(report_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "decision: changes_required" in out
    assert "SEC-001" in out


def test_review_report_json_output_round_trips_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    import json

    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path, {"findings": [{"severity": "comment", "code": "C1", "message": "fyi"}]}
    )

    exit_code = main(
        ["review", "report", "--input", str(report_file), "--format", "json"],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["decision"] == "comment_only"
    assert payload["findings"][0]["code"] == "C1"


def test_review_report_empty_findings_approved_exit_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(tmp_path, {"findings": []})

    exit_code = main(
        ["review", "report", "--input", str(report_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 0
    assert "decision: approved" in capsys.readouterr().out


def test_review_report_malformed_json_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = tmp_path / "bad.json"
    report_file.write_text("not json{{{", encoding="utf-8")

    exit_code = main(
        ["review", "report", "--input", str(report_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "review report 오류" in err


def test_review_report_invalid_payload_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(tmp_path, {"findings": "not-a-list"})

    exit_code = main(
        ["review", "report", "--input", str(report_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    assert "review report 오류" in capsys.readouterr().err


def test_review_report_contradictory_declared_decision_returns_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path,
        {
            "findings": [{"severity": "blocker", "code": "B1", "message": "m"}],
            "decision": "approved",
        },
    )

    exit_code = main(
        ["review", "report", "--input", str(report_file)],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1
    assert "review report 오류" in capsys.readouterr().err


def test_review_report_missing_input_file_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        ["review", "report", "--input", str(tmp_path / "does-not-exist.json")],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_review_report_never_constructs_github_client_or_write_client(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(tmp_path, {"findings": []})

    with (
        patch("devbot.main.GitHubClient") as mock_client,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        exit_code = main(
            ["review", "report", "--input", str(report_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_client.assert_not_called()
    mock_write_client.assert_not_called()
    mock_lock.assert_not_called()


# --------------------------------------------------------------------------
# Task 054: `devbot review submit` - submits exactly one official GitHub PR
# review from a validated Task 053 report. `--dry-run` must never
# construct a write client.
# --------------------------------------------------------------------------


def _submit_pr_detail(**overrides: object):
    from devbot.github_client import PullRequestDetail

    defaults: dict[str, object] = dict(
        number=99,
        html_url="https://example.invalid/pull/99",
        body="",
        head_ref="feature",
        head_sha="abc123",
        base_ref="main",
        state="open",
        merged=False,
        merge_commit_sha=None,
        merged_at=None,
        author_login="alice",
    )
    defaults.update(overrides)
    return PullRequestDetail(**defaults)  # type: ignore[arg-type]


def test_review_submit_dry_run_never_constructs_write_client(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path, {"findings": [], "metadata": {"reviewed_head_sha": "abc123"}}
    )

    with (
        patch("devbot.main.GitHubClient") as mock_client_cls,
        patch("devbot.main.GitHubWriteClient") as mock_write_client,
    ):
        mock_client_cls.return_value.get_pull_request.return_value = _submit_pr_detail()
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write_client.assert_not_called()
    out = capsys.readouterr().out
    assert "event: APPROVE" in out
    assert "dry-run" in out


def test_review_submit_real_execution_submits_exactly_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from devbot.github_write_client import PullRequestReviewInfo

    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path, {"findings": [], "metadata": {"reviewed_head_sha": "abc123"}}
    )

    with (
        patch("devbot.main.GitHubClient") as mock_client_cls,
        patch("devbot.main.GitHubWriteClient") as mock_write_cls,
    ):
        mock_client_cls.return_value.get_pull_request.return_value = _submit_pr_detail()
        mock_write_cls.return_value.submit_pull_request_review.return_value = (
            PullRequestReviewInfo(
                id=42, html_url="https://example.invalid/review/42", state="APPROVED"
            )
        )
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_write_cls.return_value.submit_pull_request_review.assert_called_once()
    out = capsys.readouterr().out
    assert "submitted: yes" in out
    assert "review_id: 42" in out


def test_review_submit_stale_head_returns_failure_exit_code(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path, {"findings": [], "metadata": {"reviewed_head_sha": "abc123"}}
    )

    with patch("devbot.main.GitHubClient") as mock_client_cls:
        mock_client_cls.return_value.get_pull_request.return_value = _submit_pr_detail(
            head_sha="different-sha"
        )
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1
    assert "review submit 오류" in capsys.readouterr().err


def test_review_submit_missing_metadata_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(tmp_path, {"findings": []})

    with patch("devbot.main.GitHubClient"):
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1


def test_review_submit_malformed_report_returns_failure_before_github_call(
    tmp_path: Path,
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(tmp_path, {"findings": "not-a-list"})

    with patch("devbot.main.GitHubClient") as mock_client_cls:
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file)],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 1
    mock_client_cls.assert_not_called()


def test_review_submit_missing_report_file_returns_failure_exit_code(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)

    exit_code = main(
        [
            "review",
            "submit",
            "--pr",
            "99",
            "--report",
            str(tmp_path / "does-not-exist.json"),
        ],
        env_path=env_path,
        repositories_path=repositories_path,
    )

    assert exit_code == 1


def test_review_submit_does_not_acquire_daemon_lock(tmp_path: Path) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    report_file = _write_report_file(
        tmp_path, {"findings": [], "metadata": {"reviewed_head_sha": "abc123"}}
    )

    with (
        patch("devbot.main.GitHubClient") as mock_client_cls,
        patch("devbot.main.ProcessLock") as mock_lock,
    ):
        mock_client_cls.return_value.get_pull_request.return_value = _submit_pr_detail()
        exit_code = main(
            ["review", "submit", "--pr", "99", "--report", str(report_file), "--dry-run"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    mock_lock.assert_not_called()


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


# --------------------------------------------------------------------------
# Issue #122: `devbot init`. Runs before `load_config()` - none of these
# tests set up `WORKSPACE_ROOT`/`GITHUB_TOKEN`/`.env` at all, matching the
# actual first-run scenario this command exists for.
# --------------------------------------------------------------------------


def _init_git_repo(path: Path, *, remote_url: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=str(path), check=True)
    return path


def test_init_command_never_calls_load_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The whole point of `devbot init` is to work before WORKSPACE_ROOT/
    GITHUB_TOKEN/config/repositories.yaml exist - no `.env` fixture is
    passed here at all, unlike every other `main()` test in this file."""
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    with patch("devbot.main.load_config") as mock_load_config:
        exit_code = main(["init"])

    assert exit_code == 0
    mock_load_config.assert_not_called()
    out = capsys.readouterr().out
    assert "someone/myrepo" in out


def test_init_command_registers_repository_and_writes_local_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(registry_path))

    exit_code = main(["init"])

    assert exit_code == 0
    assert (repo_root / ".devbot" / "config.yaml").is_file()
    assert registry_path.is_file()
    out = capsys.readouterr().out
    assert "신규 작성" in out
    assert "새로 등록" in out


def test_init_command_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    main(["init"])
    capsys.readouterr()
    exit_code = main(["init"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "변경 없음" in out
    assert "이미 등록됨" in out


def test_init_command_owner_repo_flags_override_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    exit_code = main(["init", "--owner", "other", "--repo", "renamed"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "other/renamed" in out


def test_init_command_fails_actionably_outside_a_git_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    plain_dir = tmp_path / "not-a-repo"
    plain_dir.mkdir()
    monkeypatch.chdir(plain_dir)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    exit_code = main(["init"])

    assert exit_code == 1
    assert "not inside a Git repository" in capsys.readouterr().err


def test_init_command_unregister_removes_registration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(registry_path))
    main(["init"])
    capsys.readouterr()

    exit_code = main(["init", "--unregister"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "등록을 해제했습니다" in out
    assert load_registry(registry_path) == ()
    # The repository-local config itself is left in place.
    assert (repo_root / ".devbot" / "config.yaml").is_file()


def test_init_command_unregister_reports_when_not_registered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo_root = _init_git_repo(tmp_path / "repo")
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(tmp_path / "registry.yaml"))

    exit_code = main(["init", "--unregister"])

    assert exit_code == 0
    assert "등록되어 있지 않았습니다" in capsys.readouterr().out


def test_daemon_run_sees_a_devbot_init_registered_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """End-to-end: a repository registered purely via `devbot init` (no
    WORKSPACE_ROOT, no config/repositories.yaml at all) is picked up by the
    normal `--once` daemon path through `load_config()` and actually
    queried for candidate Issues - `GitHubClient` is mocked (never a real
    network call), matching every other `--once` test in this file.
    Startup logs the managed-repository list via `devbot.observability`
    (not `print`), so this asserts on `caplog`, matching
    `tests/test_main_loop.py`'s established pattern for the same kind of
    check - not `capsys`, which only sees `print()` output."""
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    registry_path = tmp_path / "registry.yaml"
    monkeypatch.chdir(repo_root)
    monkeypatch.setenv("DEVBOT_REGISTRY_PATH", str(registry_path))
    main(["init"])

    lock_file = tmp_path / "devbot.lock"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"GITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={lock_file}\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    with (
        patch("devbot.main._run_startup_self_update", return_value=True),
        patch("devbot.main.GitHubClient") as mock_client_cls,
        caplog.at_level(logging.INFO, logger="devbot"),
    ):
        mock_client_cls.return_value.list_issues.return_value = []
        exit_code = main(
            ["--once", "--dry-run"], env_path=env_path, registry_path=registry_path
        )

    assert exit_code == 0
    repo_records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "managed_repository"
    ]
    assert any("someone/myrepo" in record.getMessage() for record in repo_records)


def test_worktree_cleanup_stale_command_is_wired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    removed_path = tmp_path / "workspace" / "myrepo" / ".worktrees" / "issue-30"

    with patch("devbot.main.WorktreeManager") as manager_class:
        manager = manager_class.return_value
        manager.cleanup_stale.return_value = (removed_path,)

        exit_code = main(
            ["worktree", "cleanup", "--stale"],
            env_path=env_path,
            repositories_path=repositories_path,
        )

    assert exit_code == 0
    manager.cleanup_stale.assert_called_once()
    manager.cleanup.assert_not_called()
    out = capsys.readouterr().out
    assert "stale worktree 정리 완료: 1개" in out
    assert str(removed_path) in out


def test_status_command_reports_runtime_scheduler_state(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)
    monkeypatch.delenv("AI_CONCURRENCY", raising=False)
    env_path, repositories_path = _release_env(tmp_path)
    env_text = env_path.read_text(encoding="utf-8")
    env_path.write_text(env_text + "MAX_CONCURRENT_JOBS=2\nAI_CONCURRENCY=1\n", encoding="utf-8")

    exit_code = main(["status"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "runtime_scheduler:" in out
    assert "worker_count: 2" in out
    assert "ai_concurrency: 1" in out
    assert "worker 0: state=idle" in out
    assert "worker 1: state=idle" in out


def test_goal_approved_validate_start_status_resume_commands_are_wired(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env_path, repositories_path = _release_env(tmp_path)
    plan_path = tmp_path / "goal-plan.json"
    state_path = tmp_path / "goal-state.json"
    write_approved_goal_plan(_approved_goal_plan_document(), plan_path)

    assert (
        main(
            ["goal", "validate-approved", "--plan", str(plan_path)],
            env_path=env_path,
            repositories_path=repositories_path,
        )
        == 0
    )
    assert "valid: yes" in capsys.readouterr().out

    assert (
        main(
            [
                "goal",
                "start-approved",
                "--plan",
                str(plan_path),
                "--state",
                str(state_path),
            ],
            env_path=env_path,
            repositories_path=repositories_path,
        )
        == 0
    )
    assert "pending_execution: node=a role=implementer" in capsys.readouterr().out

    assert (
        main(
            ["goal", "status-approved", "--state", str(state_path)],
            env_path=env_path,
            repositories_path=repositories_path,
        )
        == 0
    )
    assert "state: EXECUTING" in capsys.readouterr().out

    assert (
        main(
            ["goal", "resume-approved", "--state", str(state_path)],
            env_path=env_path,
            repositories_path=repositories_path,
        )
        == 0
    )
    assert "pending_execution: node=a role=implementer" in capsys.readouterr().out


def test_goal_review_gate_reaches_review_service() -> None:
    repository = RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/myrepo"),
    )
    issue = GitHubIssue(
        repository=repository.full_name,
        number=141,
        title="Task 141",
        body="body",
        state="open",
        labels=("devbot:review",),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    pull_request = PullRequest(
        number=142,
        head_ref="task/141-a",
        head_sha="head-sha",
        body="body",
        html_url="https://github.example/pr/142",
    )
    calls: list[str] = []

    class FakeGitHubClient:
        def get_issue(
            self, received_repository: RepositoryConfig, issue_number: int
        ) -> GitHubIssue:
            assert received_repository == repository
            assert issue_number == 141
            calls.append("issue")
            return issue

        def list_pull_requests(
            self, received_repository: RepositoryConfig, *, state: str = "open"
        ) -> list[PullRequest]:
            assert received_repository == repository
            assert state == "open"
            calls.append("prs")
            return [pull_request]

    class FakeReviewService:
        def process(
            self,
            received_repository: RepositoryConfig,
            received_issue: GitHubIssue,
            received_pull_request: PullRequest,
        ) -> ReviewResult:
            assert received_repository == repository
            assert received_issue == issue
            assert received_pull_request == pull_request
            calls.append("review")
            return ReviewResult(
                triggered=True,
                status="MERGE READY",
                issue_state=TaskState.REVIEW,
                message="review passed",
            )

    gate = _build_goal_review_gate(
        repositories=(repository,),
        github_client=FakeGitHubClient(),  # type: ignore[arg-type]
        review_service=FakeReviewService(),  # type: ignore[arg-type]
    )

    evidence = gate(
        VerificationRequest("goal-141", "a", GateKind.ARCHITECTURE),
        GoalTaskBinding("a", repository.full_name, 141, "tasks/141-a.md", "task/141-a"),
    )

    assert evidence.outcome is VerificationOutcome.PASS
    assert evidence.evidence == "review passed"
    assert calls == ["issue", "prs", "review"]
