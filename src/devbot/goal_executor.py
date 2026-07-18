"""Task 040: Goal Executor.

`devbot goal execute "<goal>" --task <order> --confirm` materializes exactly
one approved Task from a Task 038 `devbot goal plan` result into the normal
DevBot workflow: one GitHub Issue, one Task branch from the latest
CI-validated `origin/<default_branch>`, and one draft Task contract file.

This module never re-plans. `fetch_goal_plan()` (`devbot.goal_planner`,
Task 038) is called exactly once and its result is used as-is - no
independent regeneration or reinterpretation of the Goal. This module also
never opens a Pull Request, never invokes an implementation Agent (it does
not import `devbot.agents` at all), never merges anything, never publishes a
Release, and never materializes more than one planned Task per invocation.
That is the hard safety boundary: this Task ends after Issue + branch +
contract creation.

Two layers, mirroring `devbot.release_ops`: `build_execution_plan()` is pure
(given already-fetched materialization state, no network calls, fully
unit-testable); `execute_goal()` is the GitHub/filesystem-reading-and-writing
orchestration `devbot goal execute` actually calls, and is idempotent - a
retry after a partial failure (e.g. Issue created but branch creation failed)
reuses whatever already exists and only creates what is missing, never
duplicating.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, replace
from pathlib import Path

from devbot.github_client import GitHubClient
from devbot.github_write_client import GitHubWriteClient
from devbot.goal_planner import GoalPlan, PlannedTask, fetch_goal_plan
from devbot.models import RepositoryConfig
from devbot.planner import (
    canonical_branch_name,
    canonical_contract_path,
    canonical_issue_title,
    canonical_result_path,
)
from devbot.release_ops import (
    CI_WORKFLOW_FILE,
    local_checkout_is_dirty,
    target_commit_is_ci_validated,
)
from devbot.startup import resolve_operator_checkout

TASKS_DIR = Path("tasks")
EXECUTABLE_DECISIONS = frozenset({"single_task", "multi_task"})

_SLUG_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TASK_FILENAME_RE = re.compile(r"^(\d{3})-")
_ISSUE_TITLE_RE = re.compile(r"^Task (\d{3}): (.+)$")


class GoalExecutorError(RuntimeError):
    """Raised when `devbot goal execute` cannot safely proceed."""


@dataclass(frozen=True)
class MaterializedTask:
    """An already-existing Task Issue matching a planned Task's exact
    title - evidence that Task was materialized by a prior `goal execute`
    run (or manually), keyed by `PlannedTask.title` by the caller."""

    task_number: int
    issue_number: int
    issue_url: str


@dataclass(frozen=True)
class ExecutionReadiness:
    ready: bool
    blockers: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExecutionPlan:
    """Everything `devbot goal execute --dry-run` reports, and everything
    `execute_goal()` needs to decide whether/what to materialize. Computed
    the same way regardless of `--confirm` - dry-run and confirmed execution
    see identical readiness/blocker logic."""

    goal: str
    plan: GoalPlan
    selected_task: PlannedTask | None
    already_materialized: bool
    existing_issue_number: int | None
    existing_issue_url: str | None
    task_number: int | None
    branch: str | None
    contract_path: str | None
    result_path: str | None
    issue_title: str | None
    issue_body: str | None
    contract_content: str | None
    readiness: ExecutionReadiness


@dataclass(frozen=True)
class MaterializeResult:
    issue_number: int
    issue_url: str
    issue_created: bool
    branch: str
    branch_created: bool
    contract_path: str
    contract_created: bool


@dataclass(frozen=True)
class ExecutionReport:
    goal: str
    execution_plan: ExecutionPlan
    executed: bool
    materialize_result: MaterializeResult | None
    next_operator_action: str


def _slugify(title: str) -> str:
    slug = "-".join(_SLUG_TOKEN_RE.findall(title.lower()))
    if not slug:
        raise GoalExecutorError(f"cannot derive a slug from planned Task title: {title!r}")
    return slug


def next_task_number(tasks_dir: Path = TASKS_DIR) -> int:
    """The next available 3-digit Task number, from the highest existing
    `tasks/NNN-*.md` filename in `tasks_dir` (local checkout - trustworthy
    only once the caller has confirmed local main matches `origin/main`)."""
    highest = 0
    if tasks_dir.is_dir():
        for entry in tasks_dir.iterdir():
            match = _TASK_FILENAME_RE.match(entry.name)
            if match:
                highest = max(highest, int(match.group(1)))
    return highest + 1


def find_materialized_task(
    github_client: GitHubClient, repository: RepositoryConfig, planned_task_title: str
) -> MaterializedTask | None:
    """An Issue titled exactly `Task NNN: <planned_task_title>` (open or
    closed) is evidence that planned Task was already materialized -
    regardless of which invocation created it or what Task number it was
    assigned."""
    for issue in github_client.list_issues(repository, state="all"):
        match = _ISSUE_TITLE_RE.match(issue.title)
        if match and match.group(2) == planned_task_title:
            issue_number = issue.number
            return MaterializedTask(
                task_number=int(match.group(1)),
                issue_number=issue_number,
                issue_url=(
                    f"https://github.com/{repository.owner}/{repository.repo}"
                    f"/issues/{issue_number}"
                ),
            )
    return None


def local_main_matches_origin(path: Path, *, default_branch: str) -> bool | None:
    """`True` only when the local checkout is currently on `default_branch`
    and its HEAD exactly matches `origin/<default_branch>` after a fetch.
    `None` (skip, not a blocker) when the checkout can't be resolved at all -
    mirrors `devbot.release_ops.local_checkout_is_dirty`'s best-effort
    policy."""
    try:
        fetched = subprocess.run(
            ["git", "fetch", "origin", default_branch, "--quiet"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        if fetched.returncode != 0:
            return None
        branch = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
        )
        if branch.returncode != 0:
            return None
        if branch.stdout.strip() != default_branch:
            return False
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(path), capture_output=True, text=True, check=False
        )
        origin = subprocess.run(
            ["git", "rev-parse", f"origin/{default_branch}"],
            cwd=str(path),
            capture_output=True,
            text=True,
            check=False,
        )
        if head.returncode != 0 or origin.returncode != 0:
            return None
        return head.stdout.strip() == origin.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def _render_dependencies(dependencies: tuple[str, ...]) -> str:
    return "\n".join(f"- {dependency}" for dependency in dependencies) or "- (none)"


def _render_bullets(items: tuple[str, ...]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_execution_issue_body(
    *, goal: str, planned_task: PlannedTask, branch: str, contract_path: str
) -> str:
    """The Task Issue body `devbot goal execute --confirm` creates. Content
    is drawn only from `planned_task`'s already-catalog-sourced fields
    (Task 038) and the Goal text itself - nothing invented here."""
    return (
        f'Generated by `devbot goal execute` (Task 040) from the Goal: "{goal}".\n\n'
        f"- Contract (draft): `{contract_path}`\n"
        f"- Branch: `{branch}`\n\n"
        "## Objective\n\n"
        f"{planned_task.objective}\n\n"
        "## Dependencies\n\n"
        f"{_render_dependencies(planned_task.dependencies)}\n\n"
        "## Expected Deliverables\n\n"
        f"{_render_bullets(planned_task.expected_deliverables)}\n\n"
        "## Acceptance Criteria\n\n"
        f"{_render_bullets(planned_task.acceptance_criteria)}\n\n"
        "The draft contract on this branch still needs Context, Quality Gates with "
        "required test names, and a Validation Gate (see "
        "`docs/09-task-contract-standard.md`) before it is ready for implementation. "
        "No Pull Request has been created yet - do not create one until the contract "
        "is finalized.\n"
    )


def render_draft_contract(
    *,
    task_number: int,
    goal: str,
    planned_task: PlannedTask,
    branch: str,
    issue_number: int | None,
) -> str:
    """The draft `tasks/NNN-slug.md` content `devbot goal execute --confirm`
    creates. Deliberately a starting point, not a completed contract - see
    the "Status" section it renders. Content is drawn only from
    `planned_task`'s already-catalog-sourced fields (Task 038)."""
    issue_reference = f"#{issue_number}" if issue_number is not None else "(assigned on creation)"
    return (
        f"# Task {task_number:03d}: {planned_task.title}\n\n"
        "## Status\n\n"
        "DRAFT - generated by `devbot goal execute` (Task 040) from a validated "
        f'`devbot goal plan` result for the Goal: "{goal}". This is a starting '
        "point, not a completed contract - a human Planner must still add Context, "
        "Quality Gates with required test names, and a Validation Gate, and verify "
        "this against the Task Contract Standard (`docs/09-task-contract-standard.md`) "
        "before attaching `devbot:ready`.\n\n"
        "## Goal\n\n"
        f"{planned_task.objective}\n\n"
        "## Dependencies\n\n"
        f"{_render_dependencies(planned_task.dependencies)}\n\n"
        "## Expected Deliverables\n\n"
        f"{_render_bullets(planned_task.expected_deliverables)}\n\n"
        "## Acceptance Criteria\n\n"
        f"{_render_bullets(planned_task.acceptance_criteria)}\n\n"
        "## Git Rules\n\n"
        f"- Task Issue: {issue_reference}\n"
        f"- Branch: `{branch}`\n"
        "- Pull Request: (not yet created - open one once this contract is finalized)\n"
        "- Do not create another Issue, Branch, or Pull Request for this Task.\n"
    )


def build_execution_plan(
    goal: str,
    plan: GoalPlan,
    *,
    task_order: int | None,
    materialized: dict[str, MaterializedTask],
    next_task_number_value: int,
    local_dirty: bool | None,
    local_main_synced: bool | None,
) -> ExecutionPlan:
    """Pure decision core: given an already-computed `plan` (never
    regenerated here), which planned Task (if any) is eligible for
    materialization, and why not otherwise. No network calls."""
    blockers: list[str] = []
    if local_dirty:
        blockers.append(
            "local checkout has uncommitted changes (git status --porcelain is non-empty)"
        )
    if local_main_synced is False:
        blockers.append("local main does not match origin/main (fetch/pull before executing)")

    def _empty(reason: str) -> ExecutionPlan:
        blockers.append(reason)
        return ExecutionPlan(
            goal=goal,
            plan=plan,
            selected_task=None,
            already_materialized=False,
            existing_issue_number=None,
            existing_issue_url=None,
            task_number=None,
            branch=None,
            contract_path=None,
            result_path=None,
            issue_title=None,
            issue_body=None,
            contract_content=None,
            readiness=ExecutionReadiness(ready=False, blockers=tuple(blockers)),
        )

    if plan.decision not in EXECUTABLE_DECISIONS:
        return _empty(f"goal decision is '{plan.decision}', not executable")

    if plan.decision == "single_task":
        if task_order not in (None, 1):
            return _empty(
                f"invalid task order {task_order!r}: single_task plans only have order 1"
            )
        selected = plan.planned_tasks[0]
    else:
        if task_order is None:
            return _empty("multi_task plans require an explicit --task <order>")
        matches = [task for task in plan.planned_tasks if task.order == task_order]
        if not matches:
            valid_orders = ", ".join(str(task.order) for task in plan.planned_tasks)
            return _empty(f"invalid task order {task_order}: valid orders are {valid_orders}")
        selected = matches[0]

    already_materialized = selected.title in materialized
    if not already_materialized:
        not_yet_materialized = [
            task for task in plan.planned_tasks if task.title not in materialized
        ]
        first_incomplete = not_yet_materialized[0]
        if selected.order != first_incomplete.order:
            return _empty(
                f"Task order {selected.order} ('{selected.title}') is blocked until "
                f"'{first_incomplete.title}' (order {first_incomplete.order}) "
                "is materialized first"
            )

    if already_materialized:
        existing = materialized[selected.title]
        task_number = existing.task_number
        existing_issue_number: int | None = existing.issue_number
        existing_issue_url: str | None = existing.issue_url
    else:
        task_number = next_task_number_value
        existing_issue_number = None
        existing_issue_url = None

    slug = _slugify(selected.title)
    branch = canonical_branch_name(task_number, slug)
    contract_path = canonical_contract_path(task_number, slug)
    result_path = canonical_result_path(task_number, slug)
    issue_title = canonical_issue_title(task_number, selected.title)
    issue_body = render_execution_issue_body(
        goal=goal, planned_task=selected, branch=branch, contract_path=contract_path
    )
    contract_content = render_draft_contract(
        task_number=task_number,
        goal=goal,
        planned_task=selected,
        branch=branch,
        issue_number=existing_issue_number,
    )

    return ExecutionPlan(
        goal=goal,
        plan=plan,
        selected_task=selected,
        already_materialized=already_materialized,
        existing_issue_number=existing_issue_number,
        existing_issue_url=existing_issue_url,
        task_number=task_number,
        branch=branch,
        contract_path=contract_path,
        result_path=result_path,
        issue_title=issue_title,
        issue_body=issue_body,
        contract_content=contract_content,
        readiness=ExecutionReadiness(ready=not blockers, blockers=tuple(blockers)),
    )


def _find_issue_by_exact_title(
    github_client: GitHubClient, repository: RepositoryConfig, title: str
) -> tuple[int, str] | None:
    for issue in github_client.list_issues(repository, state="all"):
        if issue.title == title:
            return issue.number, (
                f"https://github.com/{repository.owner}/{repository.repo}/issues/{issue.number}"
            )
    return None


def materialize_planned_task(
    github_client: GitHubClient,
    write_client: GitHubWriteClient,
    repository: RepositoryConfig,
    execution_plan: ExecutionPlan,
    *,
    base_sha: str,
) -> MaterializeResult:
    """Idempotently create whatever is missing of {Issue, branch, draft
    contract} for `execution_plan.selected_task`. Never opens a Pull
    Request. Safe to call again after a partial failure - each of the three
    resources is checked for existence before being created, so a retry
    only creates what's still missing."""
    if not execution_plan.readiness.ready:
        raise GoalExecutorError(
            "refusing to execute: " + "; ".join(execution_plan.readiness.blockers)
        )
    if (
        execution_plan.selected_task is None
        or execution_plan.task_number is None
        or execution_plan.branch is None
        or execution_plan.contract_path is None
        or execution_plan.issue_title is None
    ):
        raise GoalExecutorError("no selected Task to materialize")

    if execution_plan.already_materialized:
        assert execution_plan.existing_issue_number is not None
        assert execution_plan.existing_issue_url is not None
        return MaterializeResult(
            issue_number=execution_plan.existing_issue_number,
            issue_url=execution_plan.existing_issue_url,
            issue_created=False,
            branch=execution_plan.branch,
            branch_created=False,
            contract_path=execution_plan.contract_path,
            contract_created=False,
        )

    existing_issue = _find_issue_by_exact_title(
        github_client, repository, execution_plan.issue_title
    )
    if existing_issue is not None:
        issue_number, issue_url = existing_issue
        issue_created = False
    else:
        assert execution_plan.issue_body is not None
        info = write_client.create_issue(
            repository, title=execution_plan.issue_title, body=execution_plan.issue_body
        )
        issue_number, issue_url = info.number, info.html_url
        issue_created = True

    existing_branch_sha = github_client.get_branch_ref(repository, execution_plan.branch)
    if existing_branch_sha is not None:
        if issue_created:
            raise GoalExecutorError(
                f"just created Issue #{issue_number} but branch "
                f"'{execution_plan.branch}' already exists without a matching Task "
                "Issue - refusing to reuse it (possible naming collision); resolve "
                "manually before retrying"
            )
        branch_created = False
    else:
        write_client.create_branch(repository, branch=execution_plan.branch, base_sha=base_sha)
        branch_created = True

    existing_content = github_client.get_file_content(
        repository, execution_plan.contract_path, ref=execution_plan.branch
    )
    if existing_content is not None:
        contract_created = False
    else:
        assert execution_plan.selected_task is not None
        contract_content = render_draft_contract(
            task_number=execution_plan.task_number,
            goal=execution_plan.goal,
            planned_task=execution_plan.selected_task,
            branch=execution_plan.branch,
            issue_number=issue_number,
        )
        write_client.create_file(
            repository,
            branch=execution_plan.branch,
            path=execution_plan.contract_path,
            content=contract_content,
            message=f"Task {execution_plan.task_number:03d}: draft contract (devbot goal execute)",
        )
        contract_created = True

    return MaterializeResult(
        issue_number=issue_number,
        issue_url=issue_url,
        issue_created=issue_created,
        branch=execution_plan.branch,
        branch_created=branch_created,
        contract_path=execution_plan.contract_path,
        contract_created=contract_created,
    )


def _describe_next_action(execution_plan: ExecutionPlan, *, confirmed: bool) -> str:
    if not execution_plan.readiness.ready:
        return "resolve the blockers above, then re-run with --confirm"
    if not confirmed:
        return "re-run with --confirm to create the Issue, branch, and draft contract"
    if execution_plan.already_materialized:
        return (
            "this Task was already materialized - review the existing Issue/branch/"
            "contract, finish the draft contract, and open a Pull Request when ready"
        )
    return (
        "finish the draft contract (Context, Quality Gates, Validation Gate), "
        "then open a Pull Request - devbot goal execute never does either"
    )


def execute_goal(
    github_client: GitHubClient,
    write_client: GitHubWriteClient | None,
    repository: RepositoryConfig,
    goal: str,
    *,
    task_order: int | None,
    confirm: bool,
    local_checkout_path: Path | None = None,
    tasks_dir: Path = TASKS_DIR,
    ci_workflow_file: str = CI_WORKFLOW_FILE,
) -> ExecutionReport:
    """End to end `devbot goal execute`: fetch the Task 038 plan exactly
    once, decide eligibility, and - only when `confirm` is true and the plan
    is ready - materialize the selected Task's Issue/branch/draft contract.
    Read-only whenever `confirm` is false, regardless of `--dry-run`."""
    plan = fetch_goal_plan(github_client, repository, goal)

    materialized: dict[str, MaterializedTask] = {}
    if plan.decision in EXECUTABLE_DECISIONS:
        for candidate in plan.planned_tasks:
            found = find_materialized_task(github_client, repository, candidate.title)
            if found is not None:
                materialized[candidate.title] = found

    path = local_checkout_path
    if path is None:
        try:
            path = resolve_operator_checkout()
        except Exception:  # noqa: BLE001 - best-effort; unresolved checkout is not a blocker
            path = None
    local_dirty = local_checkout_is_dirty(path) if path is not None else None
    local_main_synced = (
        local_main_matches_origin(path, default_branch=repository.default_branch)
        if path is not None
        else None
    )

    execution_plan = build_execution_plan(
        goal,
        plan,
        task_order=task_order,
        materialized=materialized,
        next_task_number_value=next_task_number(tasks_dir),
        local_dirty=local_dirty,
        local_main_synced=local_main_synced,
    )

    if not confirm:
        return ExecutionReport(
            goal=goal,
            execution_plan=execution_plan,
            executed=False,
            materialize_result=None,
            next_operator_action=_describe_next_action(execution_plan, confirmed=False),
        )

    if not execution_plan.readiness.ready:
        return ExecutionReport(
            goal=goal,
            execution_plan=execution_plan,
            executed=False,
            materialize_result=None,
            next_operator_action=_describe_next_action(execution_plan, confirmed=False),
        )

    if execution_plan.already_materialized:
        return ExecutionReport(
            goal=goal,
            execution_plan=execution_plan,
            executed=False,
            materialize_result=None,
            next_operator_action=_describe_next_action(execution_plan, confirmed=True),
        )

    if write_client is None:
        raise GoalExecutorError("a write client is required to execute with --confirm")

    base_sha = github_client.get_commit_sha(repository, repository.default_branch)
    if not target_commit_is_ci_validated(
        github_client, repository, base_sha, workflow_file=ci_workflow_file
    ):
        blocked_plan = replace(
            execution_plan,
            readiness=ExecutionReadiness(
                ready=False,
                blockers=(
                    *execution_plan.readiness.blockers,
                    f"latest origin/{repository.default_branch} commit {base_sha} is not "
                    "CI-validated",
                ),
            ),
        )
        return ExecutionReport(
            goal=goal,
            execution_plan=blocked_plan,
            executed=False,
            materialize_result=None,
            next_operator_action=_describe_next_action(blocked_plan, confirmed=False),
        )

    result = materialize_planned_task(
        github_client, write_client, repository, execution_plan, base_sha=base_sha
    )
    return ExecutionReport(
        goal=goal,
        execution_plan=execution_plan,
        executed=True,
        materialize_result=result,
        next_operator_action=_describe_next_action(execution_plan, confirmed=True),
    )


__all__: Sequence[str] = (
    "EXECUTABLE_DECISIONS",
    "ExecutionPlan",
    "ExecutionReadiness",
    "ExecutionReport",
    "GoalExecutorError",
    "MaterializeResult",
    "MaterializedTask",
    "build_execution_plan",
    "execute_goal",
    "find_materialized_task",
    "local_main_matches_origin",
    "materialize_planned_task",
    "next_task_number",
    "render_draft_contract",
    "render_execution_issue_body",
)
