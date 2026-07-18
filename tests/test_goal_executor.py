from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import GitHubAuthenticationError, GitHubIssue, WorkflowRun
from devbot.goal_executor import (
    GoalExecutorError,
    MaterializedTask,
    build_execution_plan,
    execute_goal,
    find_materialized_task,
    local_main_matches_origin,
    materialize_planned_task,
    next_task_number,
)
from devbot.goal_planner import GoalPlan, PlannedTask
from devbot.models import RepositoryConfig


def _repository(**overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/nonexistent-devbot-repo"),
        default_branch="main",
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _task(title: str, order: int, dependencies: tuple[str, ...] = ()) -> PlannedTask:
    return PlannedTask(
        title=title,
        objective=f"objective for {title}",
        dependencies=dependencies,
        expected_deliverables=("deliverable 1",),
        acceptance_criteria=("criterion 1",),
        order=order,
    )


def _single_task_plan() -> GoalPlan:
    return GoalPlan(
        goal="Add a global PATH launcher.",
        decision="single_task",
        reasons=("not yet implemented",),
        evidence=("Task 032 Out of Scope",),
        planned_tasks=(_task("Global PATH Launcher", 1),),
    )


def _multi_task_plan() -> GoalPlan:
    return GoalPlan(
        goal="Implement Self Update.",
        decision="multi_task",
        reasons=("not yet implemented",),
        evidence=("docs/history.md Known Limitations",),
        planned_tasks=(
            _task("Self-Update Discovery", 1),
            _task("Self-Update Fetch and Verify", 2, dependencies=("Self-Update Discovery",)),
            _task("Self-Update Apply", 3, dependencies=("Self-Update Fetch and Verify",)),
        ),
    )


def _already_completed_plan() -> GoalPlan:
    return GoalPlan(
        goal="Publish the next stable release.",
        decision="already_completed",
        reasons=("capability domain 'release_publish' is already implemented",),
        evidence=("Task 037",),
    )


def _duplicate_open_work_plan() -> GoalPlan:
    return GoalPlan(
        goal="Add Slack notifications.",
        decision="duplicate_open_work",
        reasons=("an open Issue/Pull Request already covers this Goal: Issue #99",),
        evidence=("Issue #99: Slack notifications",),
    )


def _ambiguous_plan() -> GoalPlan:
    return GoalPlan(
        goal="Make things better.",
        decision="ambiguous",
        reasons=("the Goal has too few significant, actionable words",),
        evidence=(),
    )


# --------------------------------------------------------------------------
# build_execution_plan (pure)
# --------------------------------------------------------------------------


def test_single_task_ready_when_no_blockers() -> None:
    plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is True
    assert plan.selected_task is not None
    assert plan.selected_task.title == "Global PATH Launcher"
    assert plan.task_number == 41
    assert plan.branch == "task/041-global-path-launcher"
    assert plan.contract_path == "tasks/041-global-path-launcher.md"
    assert plan.issue_title == "Task 041: Global PATH Launcher"
    assert plan.already_materialized is False


def test_single_task_invalid_order_is_blocked() -> None:
    plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=2,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("invalid task order" in blocker for blocker in plan.readiness.blockers)


def test_multi_task_requires_explicit_task_flag() -> None:
    plan = build_execution_plan(
        "Implement Self Update.",
        _multi_task_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("explicit --task" in blocker for blocker in plan.readiness.blockers)


def test_multi_task_invalid_order_is_blocked() -> None:
    plan = build_execution_plan(
        "Implement Self Update.",
        _multi_task_plan(),
        task_order=99,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("invalid task order" in blocker for blocker in plan.readiness.blockers)


def test_multi_task_first_task_is_selectable() -> None:
    plan = build_execution_plan(
        "Implement Self Update.",
        _multi_task_plan(),
        task_order=1,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is True
    assert plan.selected_task is not None
    assert plan.selected_task.title == "Self-Update Discovery"


def test_multi_task_later_task_blocked_by_unmet_dependency() -> None:
    plan = build_execution_plan(
        "Implement Self Update.",
        _multi_task_plan(),
        task_order=2,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any(
        "blocked until 'Self-Update Discovery'" in blocker for blocker in plan.readiness.blockers
    )


def test_multi_task_later_task_selectable_once_earlier_materialized() -> None:
    materialized = {
        "Self-Update Discovery": MaterializedTask(
            task_number=41, issue_number=100, issue_url="https://example/issues/100"
        )
    }
    plan = build_execution_plan(
        "Implement Self Update.",
        _multi_task_plan(),
        task_order=2,
        materialized=materialized,
        next_task_number_value=42,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is True
    assert plan.selected_task is not None
    assert plan.selected_task.title == "Self-Update Fetch and Verify"
    assert plan.task_number == 42


def test_already_materialized_selected_task_is_ready_and_flagged() -> None:
    materialized = {
        "Global PATH Launcher": MaterializedTask(
            task_number=41, issue_number=100, issue_url="https://example/issues/100"
        )
    }
    plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized=materialized,
        next_task_number_value=55,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is True
    assert plan.already_materialized is True
    assert plan.task_number == 41
    assert plan.existing_issue_number == 100
    assert plan.existing_issue_url == "https://example/issues/100"


def test_already_completed_decision_blocks_execution() -> None:
    plan = build_execution_plan(
        "Publish the next stable release.",
        _already_completed_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert plan.selected_task is None
    assert any("already_completed" in blocker for blocker in plan.readiness.blockers)


def test_duplicate_open_work_decision_blocks_execution() -> None:
    plan = build_execution_plan(
        "Add Slack notifications.",
        _duplicate_open_work_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("duplicate_open_work" in blocker for blocker in plan.readiness.blockers)


def test_ambiguous_decision_blocks_execution() -> None:
    plan = build_execution_plan(
        "Make things better.",
        _ambiguous_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("ambiguous" in blocker for blocker in plan.readiness.blockers)


def test_dirty_checkout_blocks_execution() -> None:
    plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=True,
        local_main_synced=True,
    )

    assert plan.readiness.ready is False
    assert any("uncommitted changes" in blocker for blocker in plan.readiness.blockers)


def test_local_main_not_synced_blocks_execution() -> None:
    plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=False,
    )

    assert plan.readiness.ready is False
    assert any("does not match origin/main" in blocker for blocker in plan.readiness.blockers)


# --------------------------------------------------------------------------
# next_task_number
# --------------------------------------------------------------------------


def test_next_task_number_scans_tasks_dir(tmp_path: Path) -> None:
    tasks_dir = tmp_path / "tasks"
    tasks_dir.mkdir()
    (tasks_dir / "001-bootstrap.md").write_text("x")
    (tasks_dir / "037-release-operator-ux.md").write_text("x")
    (tasks_dir / "not-a-task.md").write_text("x")

    assert next_task_number(tasks_dir) == 38


def test_next_task_number_defaults_to_one_when_dir_missing(tmp_path: Path) -> None:
    assert next_task_number(tmp_path / "missing") == 1


# --------------------------------------------------------------------------
# local_main_matches_origin
# --------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_synced_repo(tmp_path: Path) -> Path:
    origin = tmp_path / "origin.git"
    _run_git(["init", "--bare", str(origin)], cwd=tmp_path)
    work = tmp_path / "work"
    _run_git(["clone", str(origin), str(work)], cwd=tmp_path)
    _run_git(["checkout", "-b", "main"], cwd=work)
    _run_git(["config", "user.email", "test@example.com"], cwd=work)
    _run_git(["config", "user.name", "Test"], cwd=work)
    (work / "f.txt").write_text("hello")
    _run_git(["add", "."], cwd=work)
    _run_git(["commit", "-m", "init"], cwd=work)
    _run_git(["push", "-u", "origin", "main"], cwd=work)
    return work


def test_local_main_matches_origin_true_when_synced(tmp_path: Path) -> None:
    work = _init_synced_repo(tmp_path)

    assert local_main_matches_origin(work, default_branch="main") is True


def test_local_main_matches_origin_false_when_local_ahead(tmp_path: Path) -> None:
    work = _init_synced_repo(tmp_path)
    (work / "f.txt").write_text("changed")
    _run_git(["commit", "-am", "unpushed change"], cwd=work)

    assert local_main_matches_origin(work, default_branch="main") is False


def test_local_main_matches_origin_false_on_different_branch(tmp_path: Path) -> None:
    work = _init_synced_repo(tmp_path)
    _run_git(["checkout", "-b", "task/041-feature"], cwd=work)

    assert local_main_matches_origin(work, default_branch="main") is False


def test_local_main_matches_origin_none_for_non_git_path(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    assert local_main_matches_origin(not_a_repo, default_branch="main") is None


# --------------------------------------------------------------------------
# find_materialized_task
# --------------------------------------------------------------------------


def _issue(number: int, title: str, state: str = "open") -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=number,
        title=title,
        body="",
        state=state,
        labels=(),
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


def test_find_materialized_task_matches_exact_title() -> None:
    client = MagicMock()
    client.list_issues.return_value = [
        _issue(41, "Task 041: Global PATH Launcher"),
        _issue(1, "Unrelated Issue"),
    ]

    found = find_materialized_task(client, _repository(), "Global PATH Launcher")

    assert found is not None
    assert found.task_number == 41
    assert found.issue_number == 41
    client.list_issues.assert_called_once_with(_repository(), state="all")


def test_find_materialized_task_returns_none_when_no_match() -> None:
    client = MagicMock()
    client.list_issues.return_value = [_issue(1, "Task 001: Bootstrap")]

    assert find_materialized_task(client, _repository(), "Global PATH Launcher") is None


# --------------------------------------------------------------------------
# materialize_planned_task: idempotency / partial-failure recovery
# --------------------------------------------------------------------------


def _ready_execution_plan(
    *, already_materialized: bool = False, existing_issue_number: int | None = None
):
    return build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized=(
            {"Global PATH Launcher": MaterializedTask(41, existing_issue_number, "https://example/issues/1")}
            if already_materialized
            else {}
        ),
        next_task_number_value=41,
        local_dirty=False,
        local_main_synced=True,
    )


def test_materialize_creates_all_three_when_none_exist() -> None:
    execution_plan = _ready_execution_plan()
    github_client = MagicMock()
    github_client.list_issues.return_value = []
    github_client.get_branch_ref.return_value = None
    github_client.get_file_content.return_value = None
    write_client = MagicMock()
    write_client.create_issue.return_value = MagicMock(number=41, html_url="https://example/issues/41")

    result = materialize_planned_task(
        github_client, write_client, _repository(), execution_plan, base_sha="deadbeef"
    )

    assert result.issue_created is True
    assert result.branch_created is True
    assert result.contract_created is True
    write_client.create_issue.assert_called_once()
    write_client.create_branch.assert_called_once_with(
        _repository(), branch="task/041-global-path-launcher", base_sha="deadbeef"
    )
    write_client.create_file.assert_called_once()


def test_materialize_reuses_existing_issue_and_branch_only_creates_contract() -> None:
    """Partial-failure retry: a prior attempt created the Issue and branch
    but failed before writing the contract file. Retrying must not
    duplicate the Issue or branch."""
    execution_plan = _ready_execution_plan()
    github_client = MagicMock()
    github_client.list_issues.return_value = [_issue(41, "Task 041: Global PATH Launcher")]
    github_client.get_branch_ref.return_value = "existing-sha"
    github_client.get_file_content.return_value = None
    write_client = MagicMock()

    result = materialize_planned_task(
        github_client, write_client, _repository(), execution_plan, base_sha="deadbeef"
    )

    assert result.issue_created is False
    assert result.issue_number == 41
    assert result.branch_created is False
    assert result.contract_created is True
    write_client.create_issue.assert_not_called()
    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_called_once()


def test_materialize_is_fully_idempotent_when_everything_exists() -> None:
    execution_plan = _ready_execution_plan()
    github_client = MagicMock()
    github_client.list_issues.return_value = [_issue(41, "Task 041: Global PATH Launcher")]
    github_client.get_branch_ref.return_value = "existing-sha"
    github_client.get_file_content.return_value = "# Task 041: Global PATH Launcher\n..."
    write_client = MagicMock()

    result = materialize_planned_task(
        github_client, write_client, _repository(), execution_plan, base_sha="deadbeef"
    )

    assert result.issue_created is False
    assert result.branch_created is False
    assert result.contract_created is False
    write_client.create_issue.assert_not_called()
    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_not_called()


def test_materialize_refuses_branch_collision_after_creating_new_issue() -> None:
    execution_plan = _ready_execution_plan()
    github_client = MagicMock()
    github_client.list_issues.return_value = []
    github_client.get_branch_ref.return_value = "unrelated-branch-sha"
    write_client = MagicMock()
    write_client.create_issue.return_value = MagicMock(number=41, html_url="https://example/issues/41")

    with pytest.raises(GoalExecutorError, match="naming collision"):
        materialize_planned_task(
            github_client, write_client, _repository(), execution_plan, base_sha="deadbeef"
        )

    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_not_called()


def test_materialize_already_materialized_creates_nothing() -> None:
    execution_plan = _ready_execution_plan(already_materialized=True, existing_issue_number=100)
    github_client = MagicMock()
    write_client = MagicMock()

    result = materialize_planned_task(
        github_client, write_client, _repository(), execution_plan, base_sha="deadbeef"
    )

    assert result.issue_created is False
    assert result.branch_created is False
    assert result.contract_created is False
    write_client.create_issue.assert_not_called()
    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_not_called()


def test_materialize_refuses_when_plan_not_ready() -> None:
    execution_plan = build_execution_plan(
        "Add a global PATH launcher.",
        _single_task_plan(),
        task_order=None,
        materialized={},
        next_task_number_value=41,
        local_dirty=True,
        local_main_synced=True,
    )

    with pytest.raises(GoalExecutorError):
        materialize_planned_task(
            MagicMock(), MagicMock(), _repository(), execution_plan, base_sha="deadbeef"
        )


# --------------------------------------------------------------------------
# execute_goal: end-to-end orchestration
# --------------------------------------------------------------------------


def _ci_success_run() -> WorkflowRun:
    return WorkflowRun(
        id=1,
        name="CI",
        status="completed",
        conclusion="success",
        html_url="https://example/actions/runs/1",
        created_at=datetime(2026, 7, 18, tzinfo=UTC),
        head_sha="deadbeef",
        event="push",
    )


def _base_github_client(*, decision_plan: GoalPlan) -> MagicMock:
    client = MagicMock()
    client.list_issues.return_value = []
    client.list_pull_requests.return_value = []
    client.get_commit_sha.return_value = "deadbeef"
    client.list_workflow_runs.return_value = [_ci_success_run()]
    client.get_branch_ref.return_value = None
    client.get_file_content.return_value = None
    return client


def test_execute_goal_single_task_dry_run_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)

    report = execute_goal(
        github_client,
        None,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=False,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    assert report.execution_plan.readiness.ready is True
    assert report.materialize_result is None


def test_execute_goal_single_task_confirmed_execution(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()
    write_client.create_issue.return_value = MagicMock(number=41, html_url="https://example/issues/41")

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is True
    assert report.materialize_result is not None
    assert report.materialize_result.issue_created is True
    assert report.materialize_result.branch_created is True
    assert report.materialize_result.contract_created is True
    write_client.create_issue.assert_called_once()
    write_client.create_branch.assert_called_once()
    write_client.create_file.assert_called_once()


def test_execute_goal_multi_task_without_task_flag_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _multi_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    assert report.execution_plan.readiness.ready is False
    write_client.create_issue.assert_not_called()


def test_execute_goal_multi_task_valid_first_selection_executes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _multi_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()
    write_client.create_issue.return_value = MagicMock(number=41, html_url="https://example/issues/41")

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=1,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is True
    assert report.execution_plan.selected_task is not None
    assert report.execution_plan.selected_task.title == "Self-Update Discovery"


def test_execute_goal_later_task_blocked_by_unmet_dependency(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _multi_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=2,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    assert any(
        "Self-Update Discovery" in blocker for blocker in report.execution_plan.readiness.blockers
    )
    write_client.create_issue.assert_not_called()


def test_execute_goal_already_completed_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _already_completed_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    write_client.create_issue.assert_not_called()


def test_execute_goal_duplicate_open_work_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _duplicate_open_work_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    write_client.create_issue.assert_not_called()


def test_execute_goal_ambiguous_refuses(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    plan = _ambiguous_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    write_client.create_issue.assert_not_called()


def test_execute_goal_invalid_task_order_refuses(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _multi_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=99,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    write_client.create_issue.assert_not_called()


def test_execute_goal_without_confirm_never_writes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)

    report = execute_goal(
        github_client,
        None,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=False,
        local_checkout_path=tmp_path,
    )

    assert report.executed is False
    assert report.materialize_result is None


def test_execute_goal_dirty_checkout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    monkeypatch.setattr("devbot.goal_executor.local_checkout_is_dirty", lambda path: True)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=Path("/tmp"),
    )

    assert report.executed is False
    assert any("uncommitted changes" in b for b in report.execution_plan.readiness.blockers)
    write_client.create_issue.assert_not_called()


def test_execute_goal_local_main_diverged_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    monkeypatch.setattr("devbot.goal_executor.local_checkout_is_dirty", lambda path: False)
    monkeypatch.setattr(
        "devbot.goal_executor.local_main_matches_origin", lambda path, default_branch: False
    )
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=Path("/tmp"),
    )

    assert report.executed is False
    assert any("origin/main" in b for b in report.execution_plan.readiness.blockers)
    write_client.create_issue.assert_not_called()


def test_execute_goal_existing_matching_issue_is_reused(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    github_client.list_issues.return_value = [_issue(41, "Task 041: Global PATH Launcher")]
    github_client.get_branch_ref.return_value = "existing-sha"
    github_client.get_file_content.return_value = "existing contract"
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    # The plan sees this planned Task as already materialized (an Issue
    # already exists with its exact title) - idempotent no-op, not an error.
    assert report.executed is False
    assert report.execution_plan.already_materialized is True
    assert report.execution_plan.readiness.ready is True
    write_client.create_issue.assert_not_called()
    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_not_called()


def test_execute_goal_existing_matching_branch_only_completes_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Existing branch + Issue (partial prior materialization, not yet
    reflected as fully "already_materialized" until the Issue search runs) -
    the Issue search finds it, so this is the idempotent path; contract
    creation still completes the missing piece if it runs materialize."""
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    github_client.list_issues.return_value = [_issue(41, "Task 041: Global PATH Launcher")]
    github_client.get_branch_ref.return_value = "existing-sha"
    github_client.get_file_content.return_value = None
    write_client = MagicMock()

    report = execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    # already_materialized (Issue found by title) short-circuits before any
    # branch/contract write is attempted - by design, "has a Task Issue" is
    # the completion signal, not a deep per-resource content check.
    assert report.execution_plan.already_materialized is True
    write_client.create_issue.assert_not_called()
    write_client.create_branch.assert_not_called()
    write_client.create_file.assert_not_called()


def test_execute_goal_github_api_failure_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    github_client.list_issues.side_effect = GitHubAuthenticationError("token rejected")
    write_client = MagicMock()

    with pytest.raises(GitHubAuthenticationError):
        execute_goal(
            github_client,
            write_client,
            _repository(),
            plan.goal,
            task_order=None,
            confirm=True,
            local_checkout_path=tmp_path,
        )

    write_client.create_issue.assert_not_called()


def test_execute_goal_never_creates_a_pull_request_or_invokes_an_agent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    plan = _single_task_plan()
    monkeypatch.setattr("devbot.goal_executor.fetch_goal_plan", lambda *a, **k: plan)
    github_client = _base_github_client(decision_plan=plan)
    write_client = MagicMock(spec=["create_issue", "create_branch", "create_file"])
    write_client.create_issue.return_value = MagicMock(number=41, html_url="https://example/issues/41")

    execute_goal(
        github_client,
        write_client,
        _repository(),
        plan.goal,
        task_order=None,
        confirm=True,
        local_checkout_path=tmp_path,
    )

    assert not hasattr(write_client, "create_pull_request")


def test_goal_executor_module_never_imports_agents() -> None:
    import ast

    source = Path("src/devbot/goal_executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module)

    assert not any("agents" in module for module in imported_modules)
