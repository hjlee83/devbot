"""Task 022: Planner Workflow Standard tests.

Covers `devbot.planner` (naming/numbering, templates, review entry
resolution, workspace validation) and the doc/module cross-checks that keep
`docs/12-planner-workflow.md` and `AGENTS.md` in sync with the code.
"""

from dataclasses import replace
from pathlib import Path

import pytest

from devbot.planner import (
    IMPLEMENTER_RESPONSIBILITIES,
    OPERATOR_RESPONSIBILITIES,
    PLANNER_RESPONSIBILITIES,
    REQUIRED_REVIEW_SOURCES,
    REVIEWER_RESPONSIBILITIES,
    KnownWorkspace,
    PlannerWorkspace,
    canonical_branch_name,
    canonical_contract_path,
    canonical_issue_title,
    canonical_pr_title,
    canonical_result_path,
    contract_file_exists,
    find_duplicate_workspaces,
    render_execution_issue_body,
    render_pr_body,
    resolve_review_entry,
    validate_naming_and_numbering,
    validate_planner_workspace,
    validate_workspace_evidence,
)

PLANNER_DOC = Path("docs/12-planner-workflow.md")
AGENTS_DOC = Path("AGENTS.md")
TASK_CONTRACT_STANDARD_DOC = Path("docs/09-task-contract-standard.md")

SAMPLE_WORKSPACE = PlannerWorkspace(
    task_number=22,
    slug="planner-workflow-standard",
    title="Planner Workflow Standard",
    branch="task/022-planner-workflow-standard",
    contract_path="tasks/022-planner-workflow-standard.md",
    result_path="results/022-planner-workflow-standard.md",
    checkpoints=tuple(f"CP-022-{n}" for n in range(1, 11)),
    validation_gate=("uv sync", "uv run ruff check .", "uv run pytest"),
    issue_number=43,
    pr_number=42,
)


def test_planner_role_contract() -> None:
    # Machine-checkable side: every role has non-empty, distinct responsibilities.
    for responsibilities in (
        PLANNER_RESPONSIBILITIES,
        IMPLEMENTER_RESPONSIBILITIES,
        REVIEWER_RESPONSIBILITIES,
        OPERATOR_RESPONSIBILITIES,
    ):
        assert responsibilities
        assert len(responsibilities) == len(set(responsibilities))

    assert "branch_creation" in PLANNER_RESPONSIBILITIES
    assert "pull_request_creation" in PLANNER_RESPONSIBILITIES
    assert "execution_issue_creation" in PLANNER_RESPONSIBILITIES
    assert "cross_linking" in PLANNER_RESPONSIBILITIES
    assert "continue_on_existing_branch_and_pr" in IMPLEMENTER_RESPONSIBILITIES
    assert "return_merge_ready_or_request_changes" in REVIEWER_RESPONSIBILITIES
    assert "perform_final_merge" in OPERATOR_RESPONSIBILITIES

    # Documentation side: docs/12 documents all four roles' responsibilities.
    text = PLANNER_DOC.read_text(encoding="utf-8")
    assert "Planner" in text and "Implementer" in text
    assert "Reviewer" in text and "Operator" in text
    for phrase in [
        "Task 번호와 제목",
        "Branch 생성",
        "계약서 파일 생성",
        "Pull Request 생성",
        "실행용 Issue 생성",
        "코드를 수정하거나 Merge하지 않는다",
        "최종 Merge를 수행한다",
    ]:
        assert phrase in text, f"'{phrase}' 가 {PLANNER_DOC} 에 정의되어야 한다"

    # AGENTS.md references the Planner workflow standard doc.
    agents_text = AGENTS_DOC.read_text(encoding="utf-8")
    assert "docs/12-planner-workflow.md" in agents_text
    assert "Planner" in agents_text and "Implementer" in agents_text
    assert "Reviewer" in agents_text and "Operator" in agents_text


def test_single_task_workspace_policy() -> None:
    text = PLANNER_DOC.read_text(encoding="utf-8")
    assert "1 Task = 1 Branch = 1 Pull Request" in text
    assert "단일 Task 작업공간 정책" in text or "단일 Task 추적 정책" in text

    # Same branch/PR reused across cycles is not flagged - only a genuine
    # second workspace is.
    known = [KnownWorkspace(task_number=22, branch=SAMPLE_WORKSPACE.branch, pr_number=42)]
    result = validate_planner_workspace(SAMPLE_WORKSPACE, known_workspaces=known)
    assert result.is_valid, result.errors

    # A second branch for the same Task number violates the policy.
    second_branch = replace(
        SAMPLE_WORKSPACE,
        branch="task/022-planner-workflow-standard-v2",
        contract_path="tasks/022-planner-workflow-standard-v2.md",
        result_path="results/022-planner-workflow-standard-v2.md",
        slug="planner-workflow-standard-v2",
    )
    violation = validate_planner_workspace(second_branch, known_workspaces=known)
    assert not violation.is_valid
    assert any("duplicate" in error.lower() for error in violation.errors)


def test_planner_naming_and_numbering_policy() -> None:
    assert canonical_branch_name(22, "planner-workflow-standard") == (
        "task/022-planner-workflow-standard"
    )
    assert canonical_contract_path(22, "planner-workflow-standard") == (
        "tasks/022-planner-workflow-standard.md"
    )
    assert canonical_result_path(22, "planner-workflow-standard") == (
        "results/022-planner-workflow-standard.md"
    )
    assert canonical_pr_title(22, "Planner Workflow Standard") == (
        "Task 022: Planner Workflow Standard"
    )
    assert canonical_issue_title(22, "Planner Workflow Standard") == (
        "Execute Task 022: Planner Workflow Standard"
    )

    # Happy path: a workspace that matches canonical naming validates clean,
    # even though its Issue (#43) and PR (#42) numbers differ from the Task
    # number (022) - those identifiers are independent.
    assert validate_naming_and_numbering(SAMPLE_WORKSPACE) == []
    assert SAMPLE_WORKSPACE.issue_number != SAMPLE_WORKSPACE.task_number
    assert SAMPLE_WORKSPACE.pr_number != SAMPLE_WORKSPACE.task_number

    # Failure path: inconsistent Task number/slug in the branch is rejected.
    inconsistent = replace(SAMPLE_WORKSPACE, branch="task/022-something-else")
    errors = validate_naming_and_numbering(inconsistent)
    assert errors and "canonical name" in errors[0]

    # Boundary: an upper-case or underscore slug is rejected by the pattern.
    bad_slug = replace(
        SAMPLE_WORKSPACE,
        slug="Planner_Workflow",
        branch="task/022-Planner_Workflow",
    )
    errors = validate_naming_and_numbering(bad_slug)
    assert any("kebab-case" in error for error in errors)

    text = PLANNER_DOC.read_text(encoding="utf-8")
    assert "task/<task-number>-<slug>" in text
    assert "독립적인 식별자" in text


def test_execution_issue_contract_template() -> None:
    body = render_execution_issue_body(SAMPLE_WORKSPACE)

    assert "tasks/022-planner-workflow-standard.md" in body
    assert "task/022-planner-workflow-standard" in body
    assert "#42" in body
    assert "CP-022-1 through CP-022-10" in body
    assert "Validation Gate" in body
    assert "uv run pytest" in body
    assert "results/022-planner-workflow-standard.md" in body
    assert "Do not create another branch or Pull Request" in body

    # Boundary: a single Checkpoint renders without "through".
    single_checkpoint = replace(SAMPLE_WORKSPACE, checkpoints=("CP-022-1",))
    single_body = render_execution_issue_body(single_checkpoint)
    assert "Satisfy CP-022-1." in single_body
    assert "through" not in single_body


def test_planner_pr_contract_template() -> None:
    body = render_pr_body(SAMPLE_WORKSPACE, scope="Planner-owned contract-first workflow.")

    assert "tasks/022-planner-workflow-standard.md" in body
    assert "Continue contract, implementation, tests, Result, and rework" in body
    assert "Do not create another implementation branch or PR" in body
    assert "Planner-owned contract-first workflow." in body
    assert "#43" in body
    assert "Closes #43" in body


def test_minimal_review_entry_contract() -> None:
    # Happy path.
    context = resolve_review_entry("Review PR #42.")
    assert context.pr_number == 42
    assert context.required_sources == REQUIRED_REVIEW_SOURCES
    for source in [
        "AGENTS.md",
        "docs/09-task-contract-standard.md",
        "linked Task contract",
        "linked Result document",
        "PR Evidence",
        "CI/check status",
    ]:
        assert source in context.required_sources

    # Boundary: extra wording around the same pattern still resolves.
    assert resolve_review_entry("리뷰해줘: Review PR #42 please").pr_number == 42

    # Failure path: no PR number in the request.
    with pytest.raises(ValueError):
        resolve_review_entry("Please take a look at this")

    text = PLANNER_DOC.read_text(encoding="utf-8")
    assert "Review PR #<number>." in text
    assert "최소 리뷰 진입 계약" in text

    agents_text = AGENTS_DOC.read_text(encoding="utf-8")
    assert "Review PR #<number>." in agents_text

    standard_text = TASK_CONTRACT_STANDARD_DOC.read_text(encoding="utf-8")
    assert "Review PR #<number>." in standard_text


def test_duplicate_task_workspace_rejected() -> None:
    known = [KnownWorkspace(task_number=22, branch=SAMPLE_WORKSPACE.branch, pr_number=42)]

    # Happy path: identical branch/PR for the same Task is not a duplicate.
    assert find_duplicate_workspaces(SAMPLE_WORKSPACE, known) == []

    # Failure path: a second branch and a second PR for Task 022 are both
    # flagged as duplicate workspaces.
    duplicate = replace(
        SAMPLE_WORKSPACE,
        slug="planner-workflow-standard-v2",
        branch="task/022-planner-workflow-standard-v2",
        contract_path="tasks/022-planner-workflow-standard-v2.md",
        result_path="results/022-planner-workflow-standard-v2.md",
        issue_number=99,
        pr_number=100,
    )
    errors = find_duplicate_workspaces(duplicate, known)
    assert len(errors) == 2
    assert any("branch" in error for error in errors)
    assert any("Pull Request" in error for error in errors)

    result = validate_planner_workspace(duplicate, known_workspaces=known)
    assert not result.is_valid

    # Boundary: a workspace for a different Task number is never a duplicate.
    unrelated = replace(
        SAMPLE_WORKSPACE,
        task_number=23,
        slug="unrelated-task",
        branch="task/023-unrelated-task",
        contract_path="tasks/023-unrelated-task.md",
        result_path="results/023-unrelated-task.md",
    )
    assert find_duplicate_workspaces(unrelated, known) == []


def test_planner_contract_missing_evidence() -> None:
    incomplete = replace(
        SAMPLE_WORKSPACE,
        checkpoints=(),
        validation_gate=(),
        result_path="",
        issue_number=None,
        pr_number=None,
    )

    errors = validate_workspace_evidence(incomplete)
    assert "missing Checkpoints" in errors
    assert "missing Validation Gate" in errors
    assert "missing Result path" in errors
    assert "missing execution Issue cross-link" in errors
    assert "missing Pull Request cross-link" in errors

    result = validate_planner_workspace(incomplete)
    assert not result.is_valid
    assert len(result.errors) >= 5

    # Happy path: a fully evidenced workspace has no missing-evidence errors.
    assert validate_workspace_evidence(SAMPLE_WORKSPACE) == []

    # Boundary: only the Issue cross-link is missing.
    only_missing_issue = replace(SAMPLE_WORKSPACE, issue_number=None)
    errors = validate_workspace_evidence(only_missing_issue)
    assert errors == ["missing execution Issue cross-link"]

    # Failure path (Task 022 Scope §6: "missing contract file" is its own,
    # separate detection item from cross-links): the contract file itself
    # does not exist in the working tree.
    assert contract_file_exists("tasks/022-planner-workflow-standard.md")
    assert not contract_file_exists("tasks/022-does-not-exist.md")
    assert not contract_file_exists("")

    missing_contract = replace(SAMPLE_WORKSPACE, contract_path="tasks/022-does-not-exist.md")
    errors = validate_workspace_evidence(missing_contract)
    assert any("missing contract file" in error for error in errors)
    assert "tasks/022-does-not-exist.md" in errors[0]

    result = validate_planner_workspace(missing_contract)
    assert not result.is_valid
    assert any("missing contract file" in error for error in result.errors)

    # Boundary: an injected contract_file_check overrides the default
    # filesystem lookup - useful when the contract was read from GitHub
    # rather than a local checkout.
    assert validate_workspace_evidence(
        missing_contract, contract_file_check=lambda _path: True
    ) == []
    forced_missing = validate_workspace_evidence(
        SAMPLE_WORKSPACE, contract_file_check=lambda _path: False
    )
    assert any("missing contract file" in error for error in forced_missing)


def test_existing_workflows_compatible_with_planner_standard() -> None:
    import devbot.delivery as delivery
    import devbot.issue_state as issue_state
    import devbot.models as models
    import devbot.polling as polling
    import devbot.review as review
    import devbot.rework as rework
    import devbot.scheduler as scheduler
    import devbot.timeline as timeline

    # devbot.planner is a standalone, explicitly-invoked module: none of the
    # daemon/review/rework/delivery/timeline/state-machine modules import it,
    # so Planner workflow validation never runs as part of the automatic
    # daemon loop (docs/12-planner-workflow.md section 8).
    coupled_modules = (
        polling,
        scheduler,
        review,
        rework,
        delivery,
        timeline,
        issue_state,
    )
    for module in coupled_modules:
        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "devbot.planner" not in source
        assert "from devbot import planner" not in source

    # The state machine, job types, and core services this Task must leave
    # untouched are still exactly as documented in docs/04-agent-system.md.
    assert {state.value for state in models.TaskState} == {
        "ready",
        "working",
        "review",
        "rework",
        "manual-action",
        "blocked",
        "done",
    }
    assert {job_type.value for job_type in models.JobType} == {
        "rework",
        "review",
        "implement",
    }
    assert hasattr(polling, "PollingService")
    assert hasattr(polling.PollingService, "run_cycle")
    assert hasattr(review, "ReviewService")
    assert hasattr(review, "has_review_marker_for_head")
    assert hasattr(rework, "ReworkService")
    assert hasattr(rework.ReworkService, "process")
    assert hasattr(delivery, "branch_has_implementation_evidence")
    assert hasattr(issue_state, "IssueStateWriter")
