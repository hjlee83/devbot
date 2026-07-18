"""Task 022: Planner Workflow Standard.

Machine-checkable helpers for the Planner-owned, contract-first workflow
documented in ``docs/12-planner-workflow.md``: one Task = one Issue, one
branch, one contract, one Pull Request, canonical naming, the Task Issue /
PR templates, the minimal review entry contract, and workspace validation
(naming/numbering, missing evidence, duplicate workspaces).

This module is intentionally not imported by ``devbot.polling`` /
``devbot.scheduler`` / ``devbot.main``. Planner workflow validation is
explicit (invoked by whoever is acting as Planner), never triggered by the
automatic daemon loop - see docs/12-planner-workflow.md section 8 and this
Task's "Out of scope" (automatic Planner model execution).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Role responsibilities (docs/12-planner-workflow.md section 1)
# ---------------------------------------------------------------------------

PLANNER_RESPONSIBILITIES: tuple[str, ...] = (
    "task_number_and_title",
    "scope_and_out_of_scope",
    "checkpoints_and_required_tests",
    "validation_gate",
    "branch_creation",
    "contract_file_creation",
    "pull_request_creation",
    "task_issue_creation",
    "cross_linking",
)

IMPLEMENTER_RESPONSIBILITIES: tuple[str, ...] = (
    "continue_on_existing_branch_and_pr",
    "implement_task_scope_only",
    "write_required_tests",
    "run_validation_gate",
    "write_result_document",
)

REVIEWER_RESPONSIBILITIES: tuple[str, ...] = (
    "resolve_review_context_from_minimal_request",
    "evaluate_against_task_contract_and_review_gate",
    "return_merge_ready_or_request_changes",
    "never_modify_code_or_merge",
)

OPERATOR_RESPONSIBILITIES: tuple[str, ...] = (
    "perform_policy_required_manual_merge",
    "resolve_manual_action_states",
)


# ---------------------------------------------------------------------------
# Canonical naming and numbering (docs/12-planner-workflow.md section 4)
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def canonical_branch_name(task_number: int, slug: str) -> str:
    return f"task/{task_number:03d}-{slug}"


def canonical_contract_path(task_number: int, slug: str) -> str:
    return f"tasks/{task_number:03d}-{slug}.md"


def canonical_result_path(task_number: int, slug: str) -> str:
    return f"results/{task_number:03d}-{slug}.md"


def canonical_pr_title(task_number: int, title: str) -> str:
    return f"Task {task_number:03d}: {title}"


def canonical_issue_title(task_number: int, title: str) -> str:
    return f"Task {task_number:03d}: {title}"


# ---------------------------------------------------------------------------
# Planner workspace model and validation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannerWorkspace:
    """One Task's Planner-owned workspace: contract, branch, PR, Issue."""

    task_number: int
    slug: str
    title: str
    branch: str
    contract_path: str
    result_path: str
    checkpoints: tuple[str, ...]
    validation_gate: tuple[str, ...]
    issue_number: int | None = None
    pr_number: int | None = None


@dataclass(frozen=True)
class KnownWorkspace:
    """A previously registered workspace for a Task number, as observed on
    GitHub (an existing branch and/or an existing open Pull Request)."""

    task_number: int
    branch: str
    pr_number: int | None = None


@dataclass(frozen=True)
class PlannerValidationResult:
    errors: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.errors


def validate_naming_and_numbering(workspace: PlannerWorkspace) -> list[str]:
    """Detect inconsistent Task number or slug (CP-022-3, CP-022-8)."""
    errors: list[str] = []

    if not _SLUG_RE.match(workspace.slug):
        errors.append(f"slug '{workspace.slug}' must be lowercase kebab-case")

    expected_branch = canonical_branch_name(workspace.task_number, workspace.slug)
    if workspace.branch != expected_branch:
        errors.append(
            f"branch '{workspace.branch}' does not match canonical name "
            f"'{expected_branch}' (inconsistent Task number or slug)"
        )

    expected_contract = canonical_contract_path(workspace.task_number, workspace.slug)
    if workspace.contract_path != expected_contract:
        errors.append(
            f"contract path '{workspace.contract_path}' does not match "
            f"canonical path '{expected_contract}'"
        )

    expected_result = canonical_result_path(workspace.task_number, workspace.slug)
    if workspace.result_path != expected_result:
        errors.append(
            f"result path '{workspace.result_path}' does not match "
            f"canonical path '{expected_result}'"
        )

    return errors


def contract_file_exists(contract_path: str) -> bool:
    """Default missing-contract-file check: is `contract_path` a real file
    in the current working tree (Task 022 Scope section 6, "missing
    contract file")."""
    return bool(contract_path) and Path(contract_path).is_file()


def validate_workspace_evidence(
    workspace: PlannerWorkspace,
    *,
    contract_file_check: Callable[[str], bool] = contract_file_exists,
) -> list[str]:
    """Detect a missing contract file, missing Checkpoints, missing
    Validation Gate, missing Result path, or missing cross-links (CP-022-8).

    `contract_file_check` defaults to a real filesystem check
    (`contract_file_exists`) and is injectable so callers/tests can supply a
    Task contract discovered from GitHub instead of the local working tree.
    """
    errors: list[str] = []

    if not contract_file_check(workspace.contract_path):
        errors.append(f"missing contract file: '{workspace.contract_path}'")
    if not workspace.checkpoints:
        errors.append("missing Checkpoints")
    if not workspace.validation_gate:
        errors.append("missing Validation Gate")
    if not workspace.result_path:
        errors.append("missing Result path")
    if workspace.issue_number is None:
        errors.append("missing Task Issue cross-link")
    if workspace.pr_number is None:
        errors.append("missing Pull Request cross-link")

    return errors


def find_duplicate_workspaces(
    workspace: PlannerWorkspace,
    known_workspaces: Iterable[KnownWorkspace],
) -> list[str]:
    """Detect an additional branch or PR for a Task that already has a
    Planner-owned workspace (CP-022-7).

    A `known_workspaces` entry that matches `workspace` exactly on branch
    (and, when both are set, PR number) is an update to the existing
    workspace, not a duplicate.
    """
    errors: list[str] = []

    for known in known_workspaces:
        if known.task_number != workspace.task_number:
            continue

        if known.branch != workspace.branch:
            errors.append(
                f"Task {workspace.task_number:03d} already has branch "
                f"'{known.branch}'; '{workspace.branch}' is a duplicate "
                "workspace (single Task workspace policy)"
            )

        if (
            known.pr_number is not None
            and workspace.pr_number is not None
            and known.pr_number != workspace.pr_number
        ):
            errors.append(
                f"Task {workspace.task_number:03d} already has Pull Request "
                f"#{known.pr_number}; #{workspace.pr_number} is a duplicate "
                "Pull Request (single Task workspace policy)"
            )

    return errors


def validate_planner_workspace(
    workspace: PlannerWorkspace,
    *,
    known_workspaces: Iterable[KnownWorkspace] = (),
    contract_file_check: Callable[[str], bool] = contract_file_exists,
) -> PlannerValidationResult:
    """Run every Planner checklist validation against one workspace
    (docs/12-planner-workflow.md section 7)."""
    errors: list[str] = [
        *validate_naming_and_numbering(workspace),
        *validate_workspace_evidence(workspace, contract_file_check=contract_file_check),
        *find_duplicate_workspaces(workspace, known_workspaces),
    ]
    return PlannerValidationResult(errors=tuple(errors))


# ---------------------------------------------------------------------------
# Task Issue template (CP-022-4, docs/12-planner-workflow.md section 5)
# ---------------------------------------------------------------------------


def _checkpoint_range_label(checkpoints: tuple[str, ...]) -> str:
    if not checkpoints:
        return ""
    if len(checkpoints) == 1:
        return checkpoints[0]
    return f"{checkpoints[0]} through {checkpoints[-1]}"


def render_task_issue_body(workspace: PlannerWorkspace) -> str:
    """Render the canonical Task Issue body for a Planner-owned
    workspace (CP-022-4). Contains contract path, branch name, PR number,
    Checkpoint range, Validation Gate requirement, Result path, and an
    explicit prohibition on creating another branch or PR."""
    checkpoint_range = _checkpoint_range_label(workspace.checkpoints)
    validation_gate = "\n".join(f"- `{command}`" for command in workspace.validation_gate)

    return (
        f"Implement Task {workspace.task_number:03d} according to the approved contract.\n\n"
        f"- Contract: `{workspace.contract_path}`\n"
        f"- Branch: `{workspace.branch}`\n"
        f"- Pull Request: #{workspace.pr_number}\n\n"
        "Requirements:\n\n"
        f"- Continue implementation on the existing branch and Pull Request. "
        "Do not create another branch or Pull Request.\n"
        f"- Satisfy {checkpoint_range}.\n"
        "- Pass the Validation Gate:\n"
        f"{validation_gate}\n"
        f"- Produce `{workspace.result_path}`.\n"
    )


# ---------------------------------------------------------------------------
# PR contract template (CP-022-5, docs/12-planner-workflow.md section 6)
# ---------------------------------------------------------------------------


def render_pr_body(workspace: PlannerWorkspace, *, scope: str) -> str:
    """Render the canonical Planner-created PR body (CP-022-5). Contains the
    contract path, branch/PR continuation policy, scope, and the Task Issue
    link."""
    return (
        "## Summary\n\n"
        f"Define and implement Task {workspace.task_number:03d}: {workspace.title}.\n\n"
        "## Scope\n\n"
        f"{scope}\n\n"
        "## Contract\n\n"
        f"- `{workspace.contract_path}`\n\n"
        "## Branch / PR Policy\n\n"
        "Continue contract, implementation, tests, Result, and rework on "
        "this same branch and PR.\n\n"
        f"- Branch: `{workspace.branch}`\n"
        f"- Pull Request: #{workspace.pr_number}\n"
        f"- Task Issue: #{workspace.issue_number}\n\n"
        "Do not create another implementation branch or PR.\n\n"
        f"Closes #{workspace.issue_number}\n"
    )


# ---------------------------------------------------------------------------
# Minimal review entry contract (CP-022-6, docs/12-planner-workflow.md
# section 2)
# ---------------------------------------------------------------------------

REQUIRED_REVIEW_SOURCES: tuple[str, ...] = (
    "AGENTS.md",
    "docs/09-task-contract-standard.md",
    "linked Task contract",
    "linked Result document",
    "PR Evidence",
    "CI/check status",
)

_REVIEW_REQUEST_RE = re.compile(r"PR\s*#(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class ReviewEntryContext:
    pr_number: int
    required_sources: tuple[str, ...] = REQUIRED_REVIEW_SOURCES


def resolve_review_entry(request: str) -> ReviewEntryContext:
    """Resolve a minimal review request (``"Review PR #<number>."``) into
    the Review Gate sources a Reviewer must discover and read (CP-022-6).

    Raises `ValueError` when no PR number can be found in `request`.
    """
    match = _REVIEW_REQUEST_RE.search(request)
    if match is None:
        raise ValueError(f"cannot resolve a PR number from review request: {request!r}")
    return ReviewEntryContext(pr_number=int(match.group(1)))
