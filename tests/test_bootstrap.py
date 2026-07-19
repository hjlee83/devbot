from dataclasses import replace
from datetime import datetime

import pytest

from devbot.bootstrap import (
    BootstrapValidationError,
    BranchNamingPolicy,
    build_bootstrap_plan,
    render_task_contract_from_issue,
    validate_bootstrap_issue_metadata,
)
from devbot.github_client import GitHubIssue


def _issue(*, title: str = "Reduce planner bootstrap responsibilities") -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=119,
        title=title,
        body=(
            "## Objective\n\nDo the thing.\n\n"
            "## Scope\n\nOnly this change.\n\n"
            "## Out of scope\n\nDo not change merge behavior.\n\n"
            "## Acceptance criteria\n\n- It works.\n\n"
            "## Verification commands\n\n- `uv run pytest`\n\n"
            "## Implementation context\n\nUse the existing polling path.\n"
        ),
        state="open",
        labels=("devbot:ready",),
        created_at=datetime(2026, 1, 1),
    )


def test_branch_naming_policy_preserves_task_pattern_and_normalizes_title() -> None:
    policy = BranchNamingPolicy(max_slug_length=30)

    branch = policy.generate(task_number=119, title="Fix A/B: Planner   bootstrap!!!")

    assert branch == "task/119-fix-a-b-planner-bootstrap"


def test_branch_naming_policy_handles_four_digit_issue_numbers() -> None:
    """`canonical_branch_name()` zero-pads to a *minimum* of 3 digits
    (`:03d`), so a 4+-digit Issue number produces a 4+-digit branch
    segment - `parse_slug()` must still recognize it, not just Issue
    numbers up to 999."""
    policy = BranchNamingPolicy()

    branch_999 = policy.generate(task_number=999, title="nine nine nine")
    branch_1000 = policy.generate(task_number=1000, title="one thousand")

    assert branch_999 == "task/999-nine-nine-nine"
    assert branch_1000 == "task/1000-one-thousand"
    assert policy.parse_slug(branch_999) == "nine-nine-nine"
    assert policy.parse_slug(branch_1000) == "one-thousand"


def test_bootstrap_plan_succeeds_for_four_digit_issue_number() -> None:
    """`build_bootstrap_plan()` must not raise `BootstrapValidationError`
    for Issue numbers >= 1000 - GitHub Issue numbers are not bounded to
    three digits."""
    issue = _issue()
    issue = replace(issue, number=1000)

    plan = build_bootstrap_plan(issue)

    assert plan.task_number == 1000
    assert plan.branch == "task/1000-reduce-planner-bootstrap-responsibilities"
    assert plan.contract_path == "tasks/1000-reduce-planner-bootstrap-responsibilities.md"


def test_branch_naming_policy_resolves_collisions_deterministically() -> None:
    policy = BranchNamingPolicy(max_slug_length=30)

    branch = policy.generate(
        task_number=119,
        title="Fix Planner Bootstrap",
        existing_branches=("task/119-fix-planner-bootstrap",),
    )

    assert branch == "task/119-fix-planner-bootstrap-2"


def test_validate_bootstrap_issue_metadata_requires_all_contract_inputs() -> None:
    issue = _issue()

    metadata = validate_bootstrap_issue_metadata(issue.body)

    assert metadata.objective == "Do the thing."
    assert "Only this change" in metadata.scope


def test_validate_bootstrap_issue_metadata_rejects_missing_acceptance() -> None:
    issue = _issue()
    body = issue.body.replace("## Acceptance criteria\n\n- It works.\n\n", "")

    with pytest.raises(BootstrapValidationError, match="acceptance criteria"):
        validate_bootstrap_issue_metadata(body)


def test_bootstrap_plan_uses_issue_number_and_contract_paths() -> None:
    plan = build_bootstrap_plan(_issue())

    assert plan.branch == "task/119-reduce-planner-bootstrap-responsibilities"
    assert plan.contract_path == "tasks/119-reduce-planner-bootstrap-responsibilities.md"
    assert plan.result_path == "results/119-reduce-planner-bootstrap-responsibilities.md"


def test_render_task_contract_preserves_issue_sections() -> None:
    issue = _issue()
    plan = build_bootstrap_plan(issue)

    contract = render_task_contract_from_issue(issue, plan)

    assert "# Task 119: Reduce planner bootstrap responsibilities" in contract
    assert "- Issue: #119" in contract
    assert "Do not change merge behavior." in contract
    assert "- `uv run pytest`" in contract
