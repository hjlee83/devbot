from __future__ import annotations

import dataclasses
import unicodedata
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from devbot.github_client import GitHubIssue, PullRequest
from devbot.goal_planner import (
    CAPABILITY_CATALOG,
    RoadmapEntry,
    _templates_to_planned_tasks,
    dependency_order_is_valid,
    fetch_goal_plan,
    parse_roadmap,
    plan_goal,
)
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/nonexistent-devbot-repo"),
        default_branch="main",
    )


# --------------------------------------------------------------------------
# 1. An already completed Goal
# --------------------------------------------------------------------------


def test_goal_matching_implemented_catalog_domain_is_already_completed() -> None:
    plan = plan_goal("Publish the next stable release.")

    assert plan.decision == "already_completed"
    assert plan.planned_tasks == ()
    assert any("Task 037" in item for item in plan.evidence)


def test_goal_matching_completed_roadmap_entry_is_already_completed() -> None:
    roadmap_entries = (
        RoadmapEntry(
            number=20,
            title="daemon queue summary and cycle logging",
            completed=True,
            text="daemon queue summary and cycle logging cycle marker workflow log",
        ),
    )

    plan = plan_goal(
        "Add daemon queue summary and cycle logging",
        roadmap_entries=roadmap_entries,
    )

    assert plan.decision == "already_completed"
    assert plan.planned_tasks == ()
    assert any("Task 020" in item for item in plan.evidence)


def test_incomplete_roadmap_entry_is_not_treated_as_completed_evidence() -> None:
    roadmap_entries = (
        RoadmapEntry(
            number=99,
            title="macOS launchd",
            completed=False,
            text="macOS launchd service installer",
        ),
    )

    plan = plan_goal("Build a macOS launchd service installer", roadmap_entries=roadmap_entries)

    # No catalog match, and the only roadmap hit is incomplete, so this
    # cannot be reported "already completed" - it falls through to ambiguous
    # (the planner has no grounded evidence to build a plan from either).
    assert plan.decision == "ambiguous"


# --------------------------------------------------------------------------
# 2. A one-Task Goal
# --------------------------------------------------------------------------


def test_single_task_goal_produces_exactly_one_planned_task() -> None:
    plan = plan_goal("Add a global PATH launcher.")

    assert plan.decision == "single_task"
    assert len(plan.planned_tasks) == 1
    task = plan.planned_tasks[0]
    assert task.title == "Global PATH Launcher"
    assert task.order == 1
    assert task.dependencies == ()
    assert task.expected_deliverables
    assert task.acceptance_criteria


# --------------------------------------------------------------------------
# 3. A multi-Task Goal
# --------------------------------------------------------------------------


def test_multi_task_goal_produces_multiple_ordered_planned_tasks() -> None:
    plan = plan_goal("Implement Self Update.")

    assert plan.decision == "multi_task"
    assert len(plan.planned_tasks) == 3
    assert [task.order for task in plan.planned_tasks] == [1, 2, 3]
    assert [task.title for task in plan.planned_tasks] == [
        "Self-Update Discovery",
        "Self-Update Fetch and Verify",
        "Self-Update Apply",
    ]


# --------------------------------------------------------------------------
# 4. An ambiguous Goal
# --------------------------------------------------------------------------


def test_short_goal_is_ambiguous() -> None:
    plan = plan_goal("xyz")

    assert plan.decision == "ambiguous"
    assert plan.planned_tasks == ()
    assert plan.evidence == ()


def test_goal_without_an_actionable_verb_is_ambiguous() -> None:
    plan = plan_goal("Release quality documentation notes")

    assert plan.decision == "ambiguous"


def test_actionable_but_unrecognized_goal_is_ambiguous_not_invented() -> None:
    plan = plan_goal("Implement a quantum teleportation dashboard widget")

    assert plan.decision == "ambiguous"
    assert plan.planned_tasks == ()
    assert "inventing scope" in plan.reasons[0]


# --------------------------------------------------------------------------
# 5. A duplicate or overlapping Goal
# --------------------------------------------------------------------------


def test_goal_overlapping_open_issue_is_duplicate_open_work() -> None:
    open_work = [
        (
            "Add Slack notification integration for release publish events",
            "Issue #99: Slack notifications",
        )
    ]

    plan = plan_goal(
        "Add Slack notification integration for release publish events",
        open_work=open_work,
    )

    assert plan.decision == "duplicate_open_work"
    assert plan.evidence == ("Issue #99: Slack notifications",)
    assert plan.planned_tasks == ()


def test_open_work_overlap_takes_priority_over_catalog_match() -> None:
    open_work = [
        (
            "Publish the next stable release automatically end to end",
            "Pull Request #81",
        )
    ]

    plan = plan_goal("Publish the next stable release.", open_work=open_work)

    assert plan.decision == "duplicate_open_work"
    assert plan.evidence == ("Pull Request #81",)


def test_low_overlap_open_work_does_not_count_as_duplicate() -> None:
    open_work = [("Fix a typo in the README", "Issue #5: README typo")]

    plan = plan_goal("Implement Self Update.", open_work=open_work)

    assert plan.decision == "multi_task"


def test_repo_name_and_task_template_boilerplate_do_not_inflate_overlap() -> None:
    """Regression: found live against the real `hjlee83/devbot` repo -
    "Build a self-update mechanism for devbot." scored 0.6 overlap (at the
    duplicate threshold) against an unrelated Issue purely because both
    texts contained "devbot" and Task/Issue/PR template boilerplate words,
    not because they were actually about the same thing."""
    unrelated_task_issue = (
        "Task 038: Goal-based Planning (Operator Planner) Implement Task 038 "
        "according to the approved contract. Contract: "
        "tasks/038-goal-based-planning.md Branch: task/038-goal-based-planning "
        "Pull Request: none yet",
        "Issue #81: Task 038: Goal-based Planning (Operator Planner)",
    )

    plan = plan_goal(
        "Build a self-update mechanism for devbot.",
        open_work=[unrelated_task_issue],
    )

    assert plan.decision == "multi_task"


# --------------------------------------------------------------------------
# 6. Dependency ordering
# --------------------------------------------------------------------------


def test_multi_task_plan_dependencies_reference_strictly_earlier_tasks() -> None:
    plan = plan_goal("Implement Self Update.")

    assert dependency_order_is_valid(plan.planned_tasks)
    order_by_title = {task.title: task.order for task in plan.planned_tasks}
    for task in plan.planned_tasks:
        for dependency in task.dependencies:
            assert order_by_title[dependency] < task.order


def test_dependency_order_is_valid_detects_a_forward_reference() -> None:
    from devbot.goal_planner import PlannedTask

    broken = (
        PlannedTask(
            title="A",
            objective="",
            dependencies=("B",),
            expected_deliverables=(),
            acceptance_criteria=(),
            order=1,
        ),
        PlannedTask(
            title="B",
            objective="",
            dependencies=(),
            expected_deliverables=(),
            acceptance_criteria=(),
            order=2,
        ),
    )

    assert dependency_order_is_valid(broken) is False


def test_every_catalog_domain_has_valid_dependency_ordering() -> None:
    for domain in CAPABILITY_CATALOG:
        planned = _templates_to_planned_tasks(domain.tasks)
        assert dependency_order_is_valid(planned), domain.domain_id


# --------------------------------------------------------------------------
# 7. Read-only behavior
# --------------------------------------------------------------------------


def test_fetch_goal_plan_never_calls_a_write_method() -> None:
    github_client = MagicMock(spec=["list_issues", "list_pull_requests"])
    github_client.list_issues.return_value = []
    github_client.list_pull_requests.return_value = []

    fetch_goal_plan(
        github_client,
        _repository(),
        "Publish the next stable release.",
        roadmap_path=Path("docs/00-roadmap.md"),
    )

    github_client.list_issues.assert_called_once_with(_repository(), state="open")
    github_client.list_pull_requests.assert_called_once_with(_repository(), state="open")


def test_fetch_goal_plan_reads_open_issues_and_pull_requests(tmp_path: Path) -> None:
    roadmap_path = tmp_path / "roadmap.md"
    roadmap_path.write_text("# Roadmap\n", encoding="utf-8")

    github_client = MagicMock()
    github_client.list_issues.return_value = [
        GitHubIssue(
            repository="someone/myrepo",
            number=12,
            title="Add Slack notification integration for release publish events",
            body="",
            state="open",
            labels=(),
            created_at=datetime(2026, 7, 18),
        )
    ]
    github_client.list_pull_requests.return_value = []

    plan = fetch_goal_plan(
        github_client,
        _repository(),
        "Add Slack notification integration for release publish events",
        roadmap_path=roadmap_path,
    )

    assert plan.decision == "duplicate_open_work"
    assert plan.evidence == (
        "Issue #12: Add Slack notification integration for release publish events",
    )


def test_fetch_goal_plan_tolerates_a_missing_roadmap_file(tmp_path: Path) -> None:
    github_client = MagicMock()
    github_client.list_issues.return_value = []
    github_client.list_pull_requests.return_value = []

    plan = fetch_goal_plan(
        github_client,
        _repository(),
        "Publish the next stable release.",
        roadmap_path=tmp_path / "missing-roadmap.md",
    )

    assert plan.decision == "already_completed"


# --------------------------------------------------------------------------
# parse_roadmap
# --------------------------------------------------------------------------


def test_parse_roadmap_extracts_completed_and_incomplete_entries() -> None:
    text = (
        "# Roadmap\n\n"
        "## Phase 1\n"
        "- [x] Task 001: Bootstrap. Short description that\n"
        "      continues on an indented line.\n"
        "- [ ] Task 002: Not yet done.\n"
    )

    entries = parse_roadmap(text)

    assert len(entries) == 2
    assert entries[0].number == 1
    assert entries[0].title == "Bootstrap"
    assert entries[0].completed is True
    assert "continues on an indented line" in entries[0].text
    assert entries[1].number == 2
    assert entries[1].title == "Not yet done."
    assert entries[1].completed is False


def test_pull_request_dataclass_still_has_no_title_field() -> None:
    # Documents why `fetch_goal_plan` uses PR body (which restates the Task
    # title per `devbot.planner.render_pr_body`'s "## Summary" line) rather
    # than a PR title field - `PullRequest` genuinely has none.
    field_names = {field.name for field in dataclasses.fields(PullRequest)}
    assert "title" not in field_names
    assert "body" in field_names


# --------------------------------------------------------------------------
# Korean-language Goal support (PR #82 review)
# --------------------------------------------------------------------------


def test_korean_goal_matching_implemented_catalog_domain_is_already_completed() -> None:
    plan = plan_goal("다음 안정 릴리스를 발행해")

    assert plan.decision == "already_completed"
    assert plan.planned_tasks == ()
    assert any("Task 037" in item for item in plan.evidence)


def test_korean_goal_matching_not_implemented_domain_is_multi_task() -> None:
    plan = plan_goal("셀프 업데이트 기능을 구현해")

    assert plan.decision == "multi_task"
    assert len(plan.planned_tasks) == 3
    assert [task.order for task in plan.planned_tasks] == [1, 2, 3]
    assert dependency_order_is_valid(plan.planned_tasks)


def test_korean_goal_with_no_actionable_verb_is_ambiguous() -> None:
    plan = plan_goal("그거 좀 해줘")

    assert plan.decision == "ambiguous"
    assert plan.planned_tasks == ()


def test_korean_goal_without_catalog_or_roadmap_evidence_is_ambiguous_not_invented() -> None:
    plan = plan_goal("양자 순간이동 대시보드를 구현해줘")

    assert plan.decision == "ambiguous"
    assert plan.planned_tasks == ()
    assert "inventing scope" in plan.reasons[0]


def test_nfd_and_nfc_korean_input_match_identically() -> None:
    nfc_goal = "다음 안정 릴리스를 발행해"
    nfd_goal = unicodedata.normalize("NFD", nfc_goal)
    assert nfc_goal != nfd_goal  # sanity: the two encodings really do differ

    assert plan_goal(nfc_goal).decision == plan_goal(nfd_goal).decision == "already_completed"


def test_korean_goal_overlapping_open_issue_is_duplicate_open_work() -> None:
    open_work = [
        (
            "릴리스 발행 시 Slack 알림 통합 기능 추가",
            "Issue #99: Slack 알림 통합",
        )
    ]

    plan = plan_goal("릴리스 발행 시 Slack 알림 통합 기능 추가", open_work=open_work)

    assert plan.decision == "duplicate_open_work"
    assert plan.evidence == ("Issue #99: Slack 알림 통합",)
