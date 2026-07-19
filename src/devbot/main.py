"""DevBot CLI entry point.

`uv run devbot --once` loads configuration, acquires the single-process
lock, runs exactly one polling iteration, prints a summary, and exits with
a status-appropriate code.

`uv run devbot` (no flags) runs the same iteration continuously on
`POLL_INTERVAL_SECONDS`, until SIGINT/SIGTERM requests a safe shutdown.

`--dry-run` forces dry-run regardless of the `DRY_RUN` environment
variable, so a smoke test never depends on how the deployment's `.env` is
configured.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from devbot.agent_execution import AgentExecutionContext
from devbot.agent_outcome import AgentOutcomeError, classify_agent_outcome
from devbot.agent_registry import (
    AgentRegistryError,
    RoutingError,
    list_roles,
    load_agent_registry,
    resolve_agent,
)
from devbot.agents import build_agent_runner
from devbot.agents.base import AgentRunner, AgentSessionLimitError
from devbot.automerge import AutomergeService
from devbot.config import ConfigError, load_config
from devbot.delivery import DeliveryService
from devbot.doctor import build_doctor_report, render_doctor_report
from devbot.github_client import GitHubClient, GitHubClientError, GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.goal_executor import (
    ExecutionPlan,
    ExecutionReport,
    GoalExecutorError,
    execute_goal,
)
from devbot.goal_planner import GoalPlan, fetch_goal_plan
from devbot.issue_state import IssueStateWriter
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.models import AgentOutcome, DevBotConfig, IssueComment, RepositoryConfig
from devbot.observability import (
    LOG_LEVELS,
    install_secret_filter,
    log_startup,
    log_startup_validation,
)
from devbot.polling import PollingService, PollingStatus, run_forever
from devbot.release import authoritative_version
from devbot.release_classification import ReleaseRecommendation
from devbot.release_ops import (
    ReleaseOpsError,
    ReleasePreview,
    ReleaseStatus,
    build_release_status,
    fetch_release_preview,
    publish_release,
)
from devbot.release_preparation import (
    ReleasePreparationError,
    plan_release_preparation,
    prepare_release,
)
from devbot.release_publish import (
    ReleasePublishError,
    preview_release_publish,
    publish_prepared_release,
)
from devbot.review import ReviewService
from devbot.rework import ReworkService
from devbot.specification import (
    SpecificationError,
    generate_specification,
    write_specification,
)
from devbot.specification_templates import (
    SpecificationTemplateError,
    get_specification_template,
    list_specification_templates,
    render_template_policy,
)
from devbot.specification_validation import (
    SpecificationValidationError,
    render_validation_report,
    validate_specification_file,
)
from devbot.startup import (
    STARTUP_SELF_UPDATE_ENV,
    StartupSelfUpdateError,
    run_startup_checks,
    run_startup_self_update,
)
from devbot.timeline import TimelineError, TimelineService
from devbot.workspace import build_agent_prompt
from devbot.worktree import WorkspacePreparationError, WorktreeManager

_LOGGER_NAME = "devbot"

_FAILURE_STATUSES = {
    PollingStatus.WORKSPACE_INVALID,
    PollingStatus.WORKSPACE_PREPARATION_FAILED,
    PollingStatus.AGENT_FAILED,
    PollingStatus.BLOCKED,
    PollingStatus.ITERATION_ERROR,
}


def _apply_rework_changes(
    implementer_runner: AgentRunner,
    repository: RepositoryConfig,
    issue: GitHubIssue,
    comment: PullRequestComment,
    execution_context: AgentExecutionContext | None = None,
) -> None:
    prompt = build_agent_prompt(
        repository,
        issue,
        [IssueComment(author=comment.author, body=comment.body)],
    )
    if execution_context is not None:
        result = implementer_runner.run_context(execution_context, prompt)
    else:
        result = implementer_runner.run(repository, prompt)

    # CP-B0: classify via the same `classify_agent_outcome()` the initial
    # IMPLEMENT job uses (`devbot.polling`), instead of only checking
    # `result.failed`. This closes an Issue #41-class false-success gap on
    # the rework path specifically: an Agent that exits 0 but whose own
    # output says it needs approval (or is network-blocked, etc.) was
    # previously treated as a successful rework with nothing having changed.
    classification = classify_agent_outcome(result)
    if classification.outcome is AgentOutcome.IMPLEMENTATION_COMPLETED:
        return
    message = result.message or f"AgentRunner exited with code {result.returncode}"
    if classification.outcome is AgentOutcome.SESSION_LIMIT:
        # Task 019 CP-019-9: a dedicated exception type lets
        # `ReworkService.process()` (`devbot.rework`) classify this
        # distinctly (a clear recovery hint) while still being caught by
        # its existing generic `except (Exception, KeyboardInterrupt)`.
        raise AgentSessionLimitError(message)
    raise AgentOutcomeError(classification, message)


def _add_timeline_common_args(sub_parser: argparse.ArgumentParser) -> None:
    sub_parser.add_argument("--issue", type=int, required=True, help="대상 GitHub Issue 번호.")
    sub_parser.add_argument(
        "--repo",
        default=None,
        help="owner/repo 형식. 생략하면 config/repositories.yaml의 단일 enabled 저장소를 씁니다.",
    )


def _add_timeline_write_args(sub_parser: argparse.ArgumentParser) -> None:
    """`start`/`end`에만 붙는 자체 `--dry-run`. `devbot`(daemon) 최상위의
    `--dry-run`/`DRY_RUN` 환경 변수와는 별개다 - `timeline start/end`는
    사람이 그 순간 명시적으로 실행한 1회성 기록 커맨드이므로 daemon의
    "기본은 안전하게 dry-run" 정책을 그대로 물려받으면 계약(Task 018
    Goal/Scope 2, CP-018-2/3/4)이 요구하는 "실행하면 실제로 GitHub에
    기록한다"를 기본 배포 설정에서 지키지 못한다. 미리보기가 필요하면
    이 플래그로 명시적으로 opt-in한다."""
    sub_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제로 GitHub에 쓰지 않고 계산된 Status Card만 출력합니다.",
    )


def _build_timeline_parser(subparsers: argparse._SubParsersAction) -> None:
    timeline_parser = subparsers.add_parser(
        "timeline", help="GitHub Status Timeline(Task 017 프로토콜)을 수동으로 기록/조회합니다."
    )
    timeline_subparsers = timeline_parser.add_subparsers(dest="timeline_command", required=True)

    start_parser = timeline_subparsers.add_parser("start", help="phase 시작 이벤트를 기록합니다.")
    _add_timeline_common_args(start_parser)
    start_parser.add_argument("--phase", choices=["dev", "review"], required=True)
    start_parser.add_argument("--actor", required=True, help="이 phase를 시작한 Agent/사람 식별자.")
    start_parser.add_argument("--pr", type=int, default=None)
    _add_timeline_write_args(start_parser)

    end_parser = timeline_subparsers.add_parser("end", help="phase 종료 이벤트를 기록합니다.")
    _add_timeline_common_args(end_parser)
    end_parser.add_argument("--phase", choices=["dev", "review"], required=True)
    end_parser.add_argument("--actor", required=True, help="이 phase를 종료한 Agent/사람 식별자.")
    end_parser.add_argument(
        "--result",
        required=True,
        help="예: pushed, manual-action, blocked, merge-ready, request-changes, done",
    )
    end_parser.add_argument("--pr", type=int, default=None)
    _add_timeline_write_args(end_parser)

    status_parser = timeline_subparsers.add_parser("status", help="Status Card를 조회합니다.")
    _add_timeline_common_args(status_parser)


def _build_worktree_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 023 Scope §8: an explicit operator command for Job worktree
    lifecycle management - `devbot worktree` never runs automatically as
    part of a polling cycle."""
    worktree_parser = subparsers.add_parser(
        "worktree", help="DevBot가 준비한 격리 Job worktree를 조회/정리합니다."
    )
    worktree_subparsers = worktree_parser.add_subparsers(dest="worktree_command", required=True)

    status_parser = worktree_subparsers.add_parser(
        "status", help="worktree 상태(active/stale/conflicting)를 조회합니다 (읽기 전용)."
    )
    status_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )

    cleanup_parser = worktree_subparsers.add_parser(
        "cleanup", help="지정한 Issue의 worktree를 명시적으로 제거합니다."
    )
    cleanup_parser.add_argument("--issue", type=int, required=True, help="대상 GitHub Issue 번호.")
    cleanup_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    cleanup_parser.add_argument(
        "--force",
        action="store_true",
        default=True,
        help="미커밋 변경이 있어도 강제로 제거합니다 (기본값).",
    )


def _build_goal_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 038: read-only Goal-based planning. The operator states a
    high-level Goal instead of manually deciding which Task(s) to write;
    DevBot compares it against the roadmap, completed Tasks, and open
    GitHub Issues/Pull Requests and reports a plan. Never creates an Issue,
    branch, contract, or PR, and never runs as part of a polling cycle."""
    goal_parser = subparsers.add_parser(
        "goal", help="Goal 기반 계획 수립 (읽기 전용, 아무것도 쓰지 않음)."
    )
    goal_subparsers = goal_parser.add_subparsers(dest="goal_command", required=True)

    plan_parser = goal_subparsers.add_parser(
        "plan",
        help=(
            "Goal을 로드맵/완료된 Task/열린 Issue-PR과 비교해 필요한 Task 수와 "
            "각 Task 계획을 계산합니다 (읽기 전용)."
        ),
    )
    plan_parser.add_argument(
        "goal", help='분석할 Goal 문장. 예: "Publish the next stable release."'
    )
    plan_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )

    _build_goal_execute_style_parser(
        goal_subparsers,
        "execute",
        help=(
            "Task 038 계획에서 승인된 Task 하나만 Issue/Branch/초안 Contract로 "
            "구체화합니다. PR 생성이나 구현 Agent 호출은 하지 않습니다."
        ),
    )
    # Task 041: identical behavior to `execute` (same underlying
    # `execute_goal()` call, same Issue/Branch/Contract-only boundary) -
    # "dispatch" additionally reports which Agent the Router would resolve
    # for the "implementer" Role, so the Role-based routing this Task adds
    # is visible end-to-end through the Goal Executor CLI without ever
    # invoking that Agent.
    _build_goal_execute_style_parser(
        goal_subparsers,
        "dispatch",
        help=(
            "goal execute와 동일하지만, 'implementer' Role이 어떤 Agent로 라우팅될지도 "
            "함께 보여줍니다 (Agent를 실제로 호출하지는 않습니다)."
        ),
    )


def _build_goal_execute_style_parser(
    goal_subparsers: argparse._SubParsersAction, name: str, *, help: str
) -> None:
    parser = goal_subparsers.add_parser(name, help=help)
    parser.add_argument(
        "goal", help='실행할 Goal 문장. devbot goal plan과 동일한 계획을 다시 계산합니다.'
    )
    parser.add_argument(
        "--task",
        type=int,
        default=None,
        help=(
            "multi_task 계획에서 구체화할 Task의 order (필수). single_task 계획에서는 "
            "생략하면 Task 1이 자동 선택됩니다."
        ),
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="실제로 Issue/Branch/초안 Contract를 생성합니다. 생략하면 계획만 보여줍니다.",
    )
    # Deliberately the same `dry_run` dest as the top-level `--dry-run` and
    # `release publish --dry-run` (not an independent one): whichever
    # spelling is present forces read-only regardless of `--confirm` - if
    # both are given by mistake, the safe side (no write) wins.
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산된 계획만 출력하고 --confirm과 무관하게 아무것도 쓰지 않습니다.",
    )
    parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )


def _build_role_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 041: read-only Role -> Agent routing. DevBot dispatches only
    Roles ("implementer", "reviewer", ...); these commands let an operator
    inspect what the Router would resolve, without dispatching anything."""
    role_parser = subparsers.add_parser(
        "role", help="Role -> Agent 라우팅을 조회합니다 (읽기 전용)."
    )
    role_subparsers = role_parser.add_subparsers(dest="role_command", required=True)
    role_subparsers.add_parser("list", help="설정된 모든 Role을 나열합니다.")
    resolve_parser = role_subparsers.add_parser(
        "resolve", help="Role이 현재 어떤 Agent로 라우팅되는지 확인합니다."
    )
    resolve_parser.add_argument("role", help='조회할 Role 이름. 예: "implementer"')


def _build_agent_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 041: read-only Agent Registry inspection."""
    agent_parser = subparsers.add_parser(
        "agent", help="Agent Registry를 조회합니다 (읽기 전용)."
    )
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_subparsers.add_parser("list", help="등록된 모든 Agent를 나열합니다.")


def _build_specification_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 042: deterministic, evidence-grounded Specification generation
    (Goal -> Planner -> Issue -> Contract -> **Specification** -> Dispatch).
    Never invokes an Agent and never changes how any existing Agent is
    dispatched - only prepares the artifact a future Dispatch would read."""
    specification_parser = subparsers.add_parser(
        "specification", help="Task Specification을 생성/조회합니다."
    )
    specification_subparsers = specification_parser.add_subparsers(
        dest="specification_command", required=True
    )

    generate_parser = specification_subparsers.add_parser(
        "generate",
        help=(
            "Task Contract/Issue/로드맵 근거만으로 specifications/NNN-slug.md를 "
            "결정론적으로 생성합니다."
        ),
    )
    generate_parser.add_argument("--task", type=int, required=True, help="Task 번호.")
    generate_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    generate_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="파일에 쓰지 않고 계산된 Specification만 출력합니다 (show와 동일).",
    )
    generate_parser.add_argument(
        "--template", default=None, help="등록된 Specification template ID로 선택을 override합니다."
    )

    show_parser = specification_subparsers.add_parser(
        "show", help="Specification을 계산만 하고 출력합니다 (읽기 전용, 아무것도 쓰지 않음)."
    )
    show_parser.add_argument("--task", type=int, required=True, help="Task 번호.")
    show_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    show_parser.add_argument(
        "--template", default=None, help="등록된 Specification template ID로 선택을 override합니다."
    )

    specification_subparsers.add_parser(
        "templates", help="등록된 Specification template 목록을 결정론적으로 출력합니다."
    )
    template_parser = specification_subparsers.add_parser(
        "template", help="Specification template 세부 정보를 조회합니다."
    )
    template_subparsers = template_parser.add_subparsers(
        dest="template_command", required=True
    )
    template_show_parser = template_subparsers.add_parser(
        "show", help="등록된 Specification template 정책을 출력합니다."
    )
    template_show_parser.add_argument("--template", required=True, help="template ID.")

    validate_parser = specification_subparsers.add_parser(
        "validate",
        help=(
            "specifications/NNN-slug.md가 Task 042 스키마를 만족하는지 검증합니다 "
            "(읽기 전용, GitHub 호출 없음)."
        ),
    )
    validate_parser.add_argument("--task", type=int, required=True, help="Task 번호.")
    validate_parser.add_argument(
        "--format", choices=("text", "json"), default="text", help="출력 형식 (기본값: text)."
    )


def _run_specification_template_command(args: argparse.Namespace) -> int:
    try:
        if args.specification_command == "templates":
            for template in list_specification_templates():
                print(f"{template.id}: {template.description}")
            return 0
        if args.specification_command == "template" and args.template_command == "show":
            print(render_template_policy(get_specification_template(args.template)), end="")
            return 0
    except SpecificationTemplateError as exc:
        print(f"specification template 오류: {exc}", file=sys.stderr)
        return 1
    return 1


def _run_specification_validate_command(args: argparse.Namespace) -> int:
    """Task 043: read-only, no GitHub client - resolves and validates the
    on-disk `specifications/NNN-*.md` file directly."""
    try:
        result = validate_specification_file(Path("."), args.task)
    except SpecificationValidationError as exc:
        print(f"specification validate 오류: {exc}", file=sys.stderr)
        return 2
    print(render_validation_report(result, output_format=args.format), end="")
    return 0 if result.passed else 1


def _run_specification_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    if args.specification_command == "validate":
        return _run_specification_validate_command(args)
    if args.specification_command in {"templates", "template"}:
        return _run_specification_template_command(args)

    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    github_client = GitHubClient(config.github_token)
    try:
        specification = generate_specification(
            github_client, repository, args.task, template_id=getattr(args, "template", None)
        )
    except (SpecificationError, SpecificationTemplateError, GitHubClientError) as exc:
        print(f"specification 오류: {exc}", file=sys.stderr)
        return 1

    write = args.specification_command == "generate" and not getattr(args, "dry_run", False)
    if write:
        written_path = write_specification(specification)
        print(f"task: {specification.task_number:03d}")
        print(f"path: {written_path}")
        print(f"bytes: {len(specification.content.encode('utf-8'))}")
    else:
        print(specification.content)

    return 0


def _render_goal_plan(plan: GoalPlan) -> str:
    lines = [f"goal: {plan.goal}", f"decision: {plan.decision}", "reasons:"]
    lines.extend(f"  - {reason}" for reason in plan.reasons)
    lines.append("evidence:")
    lines.extend(f"  - {item}" for item in plan.evidence)
    lines.append(f"planned_tasks ({len(plan.planned_tasks)}):")
    for task in plan.planned_tasks:
        lines.append(f"  [{task.order}] {task.title}")
        lines.append(f"      objective: {task.objective}")
        lines.append(f"      dependencies: {', '.join(task.dependencies) or 'none'}")
        lines.append("      expected_deliverables:")
        lines.extend(f"        - {item}" for item in task.expected_deliverables)
        lines.append("      acceptance_criteria:")
        lines.extend(f"        - {item}" for item in task.acceptance_criteria)
    return "\n".join(lines)


def _run_goal_plan_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    github_client = GitHubClient(config.github_token)
    try:
        plan = fetch_goal_plan(github_client, repository, args.goal)
    except GitHubClientError as exc:
        print(f"goal plan 오류: GitHub 요청 실패: {exc}", file=sys.stderr)
        return 1

    print(_render_goal_plan(plan))
    return 1 if plan.decision == "ambiguous" else 0


def _render_execution_plan(execution_plan: ExecutionPlan) -> str:
    lines = [f"goal: {execution_plan.goal}", f"decision: {execution_plan.plan.decision}"]
    task = execution_plan.selected_task
    if task is not None:
        lines.append(f"selected_task: [{task.order}] {task.title}")
        lines.append(f"dependencies: {', '.join(task.dependencies) or 'none'}")
        already = "yes" if execution_plan.already_materialized else "no"
        lines.append(f"already_materialized: {already}")
        if execution_plan.already_materialized:
            lines.append(f"existing_issue: {execution_plan.existing_issue_url}")
        lines.append(f"next_task_number: {execution_plan.task_number}")
        lines.append(f"proposed_branch: {execution_plan.branch}")
        lines.append(f"proposed_contract_path: {execution_plan.contract_path}")
        lines.append(f"proposed_result_path: {execution_plan.result_path}")
        lines.append(f"proposed_issue_title: {execution_plan.issue_title}")
        lines.append("proposed_issue_body:")
        lines.extend(f"  {line}" for line in (execution_plan.issue_body or "").splitlines())
        content_lines = (execution_plan.contract_content or "").splitlines()
        summary_limit = 14
        lines.append("proposed_contract_summary:")
        lines.extend(f"  {line}" for line in content_lines[:summary_limit])
        if len(content_lines) > summary_limit:
            lines.append(f"  ... ({len(content_lines) - summary_limit} more lines)")
    lines.append(f"ready: {'yes' if execution_plan.readiness.ready else 'no'}")
    lines.extend(f"  blocker: {blocker}" for blocker in execution_plan.readiness.blockers)
    return "\n".join(lines)


def _render_execution_report(report: ExecutionReport) -> str:
    executed = "yes" if report.executed else "no"
    lines = [_render_execution_plan(report.execution_plan), f"executed: {executed}"]
    result = report.materialize_result
    if result is not None:
        lines.append(f"issue_url: {result.issue_url}")
        lines.append(f"issue_status: {'created' if result.issue_created else 'reused'}")
        lines.append(f"branch: {result.branch}")
        lines.append(f"branch_status: {'created' if result.branch_created else 'reused'}")
        lines.append(f"contract_path: {result.contract_path}")
        lines.append(f"contract_status: {'created' if result.contract_created else 'reused'}")
    elif report.execution_plan.already_materialized and report.execution_plan.existing_issue_url:
        lines.append(f"issue_url: {report.execution_plan.existing_issue_url}")
        lines.append("issue_status: reused")
    lines.append(f"next_operator_action: {report.next_operator_action}")
    return "\n".join(lines)


def _run_goal_execute_command(
    args: argparse.Namespace, config: DevBotConfig, *, show_role_resolution: bool = False
) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    confirm = args.confirm and not args.dry_run
    github_client = GitHubClient(config.github_token)
    write_client = GitHubWriteClient(config.github_token) if confirm else None

    try:
        report = execute_goal(
            github_client,
            write_client,
            repository,
            args.goal,
            task_order=args.task,
            confirm=confirm,
        )
    except (GoalExecutorError, GitHubClientError) as exc:
        print(f"goal execute 오류: {exc}", file=sys.stderr)
        return 1

    print(_render_execution_report(report))
    if show_role_resolution and report.execution_plan.selected_task is not None:
        # Task 041: read-only Role -> Agent routing info only - never
        # invokes the resolved Agent. Reported best-effort - a misconfigured
        # or missing Agent Registry must not hide the execution report above
        # (which may already have written the Issue/Branch/Contract), so any
        # RoutingError/AgentRegistryError here is shown, not raised.
        try:
            agent_registry = load_agent_registry(config)
            agent = resolve_agent(agent_registry, "implementer")
            print(
                f"resolved_role: implementer -> agent={agent.id} (backend={agent.backend}) "
                "[not invoked]"
            )
        except (AgentRegistryError, RoutingError) as exc:
            print(f"resolved_role: implementer -> 오류: {exc}")
    if not confirm and report.execution_plan.readiness.ready:
        print(
            "\n(--confirm 없이 실행되어 아무것도 생성되지 않았습니다. "
            "위 계획을 확인한 뒤 --confirm으로 다시 실행하세요.)"
        )
    return 0 if report.execution_plan.readiness.ready else 1


def _run_role_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        agent_registry = load_agent_registry(config)
    except AgentRegistryError as exc:
        print(f"role 오류: {exc}", file=sys.stderr)
        return 1

    if args.role_command == "list":
        for role in list_roles(agent_registry):
            print(role)
        return 0

    try:
        agent = resolve_agent(agent_registry, args.role)
    except RoutingError as exc:
        print(f"role resolve 오류: {exc}", file=sys.stderr)
        return 1

    print(f"role: {args.role}")
    print(f"resolved_agent_id: {agent.id}")
    print(f"backend: {agent.backend}")
    print(f"priority: {agent.priority}")
    print(f"capabilities: {', '.join(agent.capabilities) or 'none'}")
    return 0


def _run_agent_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        agent_registry = load_agent_registry(config)
    except AgentRegistryError as exc:
        print(f"agent 오류: {exc}", file=sys.stderr)
        return 1

    for agent in agent_registry.agents:
        status = "enabled" if agent.enabled else "disabled"
        roles = ", ".join(agent.supported_roles) or "none"
        capabilities = ", ".join(agent.capabilities) or "none"
        print(
            f"{agent.id} [{status}] backend={agent.backend} priority={agent.priority} "
            f"roles=[{roles}] capabilities=[{capabilities}]"
        )
    return 0


def _run_goal_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    if args.goal_command == "execute":
        return _run_goal_execute_command(args, config)
    if args.goal_command == "dispatch":
        return _run_goal_execute_command(args, config, show_role_resolution=True)
    return _run_goal_plan_command(args, config)


def _build_release_parser(subparsers: argparse._SubParsersAction) -> None:
    """Task 037: DevBot's own stable Release lifecycle. The operator's
    entire interaction is meant to be "publish the next stable release" -
    DevBot determines the version, commit, and Release notes; it never runs
    automatically as part of a polling cycle."""
    release_parser = subparsers.add_parser(
        "release", help="DevBot 자체 stable Release를 조회/게시합니다."
    )
    release_subparsers = release_parser.add_subparsers(dest="release_command", required=True)

    preview_parser = release_subparsers.add_parser(
        "preview",
        help="다음 stable Release 계획을 계산만 하고 GitHub에 쓰지 않습니다 (읽기 전용).",
    )
    preview_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )

    publish_parser = release_subparsers.add_parser(
        "publish",
        help=(
            "다음 stable Release를 게시합니다: 버전/커밋/Release Notes를 자동 결정하고 "
            "기존 Release 워크플로(.github/workflows/release.yml)를 dispatch합니다."
        ),
    )
    publish_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    # Deliberately the same `dry_run` dest as the top-level `--dry-run`
    # (unlike `_add_timeline_write_args`'s independent one): both spellings
    # - `devbot --dry-run release publish` and `devbot release publish
    # --dry-run` - mean the same thing here ("compute the plan, dispatch
    # nothing"), so one flag is enough.
    publish_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산된 계획만 출력하고 워크플로를 dispatch하지 않습니다.",
    )
    publish_parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=15.0,
        help="워크플로 실행 상태를 폴링하는 간격(초). 기본값 15초.",
    )
    publish_parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=1800.0,
        help="워크플로 완료를 기다리는 최대 시간(초). 기본값 1800초.",
    )

    status_parser = release_subparsers.add_parser(
        "status", help="최근 stable Release와 Release 워크플로 상태를 조회합니다 (읽기 전용)."
    )
    status_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )

    prepare_parser = release_subparsers.add_parser(
        "prepare",
        help=(
            "Task 048: 로컬 pyproject.toml/uv.lock 버전을 다음 버전으로 준비합니다 "
            "(Git 태그/GitHub Release/push/PR 없음, GitHub 호출 없음)."
        ),
    )
    prepare_parser.add_argument(
        "--level",
        choices=("major", "minor", "patch"),
        required=True,
        help="릴리스 수준. none은 준비할 것이 없으므로 지원하지 않습니다.",
    )
    prepare_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="계산된 다음 버전만 출력하고 파일을 쓰지 않습니다.",
    )

    publish_prepared_parser = release_subparsers.add_parser(
        "publish-prepared",
        help=(
            "Task 049: 이미 준비된(Task 048) 버전을 Git 태그 + GitHub Release로 직접 "
            "게시합니다 - 기존 'release publish'(워크플로 dispatch, Task 037)와는 "
            "별개의 명시적 두 번째 경로입니다. 이름을 다르게 둔 이유는 두 경로가 "
            "서로 다른 플래그/동작을 가지므로 혼동을 피하기 위함입니다."
        ),
    )
    publish_prepared_parser.add_argument(
        "--repo", default=None, help="owner/repo 형식. 생략하면 단일 enabled 저장소를 씁니다."
    )
    publish_prepared_parser.add_argument(
        "--notes-file",
        required=True,
        help="Release Notes 본문이 담긴 파일 경로. 비어 있으면 거부됩니다.",
    )
    publish_prepared_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="검증된 미리보기(버전/태그/대상 커밋)만 출력하고 태그/Release를 만들지 않습니다.",
    )


def _run_release_prepare_command(args: argparse.Namespace) -> int:
    """Task 048: purely local - no GitHub client, no `_resolve_repository`
    call. Operates on the current working directory's `pyproject.toml`/
    `uv.lock` only."""
    recommendation = ReleaseRecommendation(args.level)
    project_root = Path(".")

    try:
        if args.dry_run:
            plan = plan_release_preparation(project_root, recommendation)
            print(f"recommendation: {plan.recommendation}")
            print(f"old_version: {plan.old_version}")
            print(f"new_version: {plan.new_version}")
            print("dry-run: 파일을 쓰지 않았습니다.")
            return 0

        result = prepare_release(project_root, recommendation)
    except ReleasePreparationError as exc:
        print(f"release prepare 오류: {exc}", file=sys.stderr)
        return 1

    print(f"recommendation: {result.recommendation}")
    print(f"old_version: {result.old_version}")
    print(f"new_version: {result.new_version}")
    print("changed_paths:")
    for path in result.changed_paths:
        print(f"  - {path}")
    return 0


def _run_release_publish_prepared_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    """Task 049: the direct tag+Release publish path - a second,
    intentional path alongside Task 037's workflow-dispatch `release
    publish` (see `docs/07-decisions.md`)."""
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    try:
        notes = Path(args.notes_file).read_text(encoding="utf-8")
    except OSError as exc:
        print(
            f"release publish-prepared 오류: notes 파일을 읽을 수 없습니다: {exc}",
            file=sys.stderr,
        )
        return 1

    github_client = GitHubClient(config.github_token)

    try:
        if args.dry_run:
            preview = preview_release_publish(github_client, repository, notes)
            print(f"repository: {preview.repository}")
            print(f"version: {preview.version}")
            print(f"tag: {preview.tag}")
            print(f"title: {preview.title}")
            print(f"target_branch: {preview.target_branch}")
            print(f"target_sha: {preview.target_sha}")
            print(f"tag_state: {preview.tag_state}")
            print(f"release_state: {preview.release_state}")
            print("dry-run: 태그/Release를 만들지 않았습니다.")
            return 0

        write_client = GitHubWriteClient(config.github_token)
        result = publish_prepared_release(github_client, write_client, repository, notes)
    except (ReleasePublishError, ReleasePreparationError, GitHubClientError) as exc:
        print(f"release publish-prepared 오류: {exc}", file=sys.stderr)
        return 1

    print(f"version: {result.version}")
    print(f"tag: {result.tag}")
    print(f"target_sha: {result.target_sha}")
    print(f"release_url: {result.release_url or 'none'}")
    print(f"outcome: {result.outcome}")
    return 0


def _render_release_preview(preview: ReleasePreview) -> str:
    lines = [
        f"previous_version: {preview.previous_version}",
        f"next_version: {preview.next_version}",
        f"increment: {preview.increment or 'none'}",
        f"target_commit: {preview.target_commit}",
        f"target_commit_validated: {'yes' if preview.target_commit_validated else 'no'}",
        f"previous_release_commit: {preview.previous_release_commit or 'none'}",
        f"ready: {'yes' if preview.readiness.ready else 'no'}",
    ]
    for blocker in preview.readiness.blockers:
        lines.append(f"  blocker: {blocker}")
    lines.append(f"expected_assets ({len(preview.expected_assets)}):")
    lines.extend(f"  - {asset}" for asset in preview.expected_assets)
    lines.append(f"merged_prs ({len(preview.changes)}):")
    lines.extend(
        f"  - #{change.pr.number} [{change.increment}] {change.pr.title}"
        for change in preview.changes
    )
    lines.append("")
    lines.append("release_notes:")
    lines.append(preview.notes if preview.notes else "(none)")
    return "\n".join(lines)


def _render_release_status(status: ReleaseStatus) -> str:
    run = status.latest_workflow_run
    published_at = (
        status.latest_release_published_at.isoformat()
        if status.latest_release_published_at
        else "none"
    )
    lines = [
        f"latest_stable_version: {status.latest_stable_version or 'none'}",
        f"latest_release_url: {status.latest_release_url or 'none'}",
        f"latest_release_published_at: {published_at}",
        f"last_published_commit: {status.last_published_commit or 'none'}",
        (
            f"latest_workflow_run: {run.html_url} status={run.status} "
            f"conclusion={run.conclusion or 'none'}"
            if run is not None
            else "latest_workflow_run: none"
        ),
        f"publication_state: {status.publication_state}",
    ]
    return "\n".join(lines)


def _run_release_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    if args.release_command == "prepare":
        return _run_release_prepare_command(args)
    if args.release_command == "publish-prepared":
        return _run_release_publish_prepared_command(args, config)

    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    github_client = GitHubClient(config.github_token)

    try:
        if args.release_command == "status":
            print(_render_release_status(build_release_status(github_client, repository)))
            return 0

        preview = fetch_release_preview(github_client, repository)
    except GitHubClientError as exc:
        print(f"release {args.release_command} 오류: GitHub 요청 실패: {exc}", file=sys.stderr)
        return 1

    print(_render_release_preview(preview))

    if args.release_command == "preview":
        return 0 if preview.readiness.ready else 1

    # publish
    if not preview.readiness.ready:
        print("release publish 오류: 릴리스 준비가 되지 않았습니다.", file=sys.stderr)
        return 1
    if args.dry_run:
        print("dry-run: 워크플로를 dispatch하지 않았습니다.")
        return 0

    write_client = GitHubWriteClient(config.github_token)
    try:
        outcome = publish_release(
            github_client,
            write_client,
            repository,
            preview=preview,
            poll_interval_seconds=args.poll_interval_seconds,
            timeout_seconds=args.timeout_seconds,
        )
    except (ReleaseOpsError, GitHubClientError) as exc:
        print(f"release publish 오류: {exc}", file=sys.stderr)
        return 1

    print(f"tag: {outcome.tag}")
    print(f"release_url: {outcome.release_url}")
    print(f"workflow_run: {outcome.workflow_run.html_url}")
    print(f"validated_assets: {', '.join(outcome.validated_assets)}")
    return 0


def _run_worktree_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    manager = WorktreeManager(workspace_root=config.workspace_root)

    if args.worktree_command == "status":
        report = manager.health(repository)
        print(f"operator_checkout: {report.operator_checkout_path}")
        print(f"operator_branch: {report.operator_branch or 'unknown'}")
        print(f"worktree_root: {report.worktree_root}")
        print(f"active ({len(report.active)}):")
        for path in report.active:
            print(f"  - {path}")
        print(f"stale ({len(report.stale)}):")
        for path in report.stale:
            print(f"  - {path}")
        print(f"conflicting ({len(report.conflicting)}):")
        for path in report.conflicting:
            print(f"  - {path}")
        print(f"safe_to_start: {'yes' if report.safe_to_start else 'no'}")
        return 0

    try:
        manager.cleanup(repository, args.issue, force=args.force)
    except WorkspacePreparationError as exc:
        print(f"worktree cleanup 오류: [{exc.category.value}] {exc}", file=sys.stderr)
        return 1
    print(f"worktree 제거 완료: issue #{args.issue}")
    return 0


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="devbot")
    parser.add_argument(
        "--version",
        action="store_true",
        help="DevBot 패키지 버전을 출력하고 종료합니다.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="한 번만 폴링하고 종료합니다.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="DRY_RUN 환경 변수 값과 무관하게 강제로 dry-run으로 실행합니다.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="LOG_LEVEL 설정과 무관하게 이번 실행에서만 DEBUG 수준 로그를 켭니다.",
    )
    subparsers = parser.add_subparsers(dest="command")
    _build_timeline_parser(subparsers)
    _build_worktree_parser(subparsers)
    _build_release_parser(subparsers)
    _build_goal_parser(subparsers)
    _build_role_parser(subparsers)
    _build_agent_parser(subparsers)
    _build_specification_parser(subparsers)
    doctor_parser = subparsers.add_parser(
        "doctor",
        help=(
            "설정/워크스페이스/GitHub 연결/Lock 상태를 점검하고 데몬 시작 가능 여부를 "
            "보고합니다 (읽기 전용, GitHub에 쓰지 않음)."
        ),
    )
    doctor_parser.add_argument(
        "--ci",
        action="store_true",
        help="CI 검증용으로 Agent 실행 파일/사용자 로그인 검사를 생략합니다.",
    )
    return parser.parse_args(argv)


def _configure_logging() -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger


def _apply_log_level(logger: logging.Logger, config_log_level: str, *, verbose: bool) -> None:
    """`--verbose` overrides `LOG_LEVEL` to DEBUG for this process only - it
    never writes back to `.env` or the process environment (Task 013 동작
    규칙 #7)."""
    level = logging.DEBUG if verbose else LOG_LEVELS[config_log_level]
    logger.setLevel(level)


def _resolve_repository(config: DevBotConfig, repo_arg: str | None) -> RepositoryConfig:
    """Pick the `RepositoryConfig` a `timeline` command targets: an
    explicit `--repo owner/repo`, or the sole enabled repository when there
    is exactly one (every current deployment - `config/repositories.yaml`
    - manages a single repository)."""
    if repo_arg is not None:
        owner, separator, repo = repo_arg.partition("/")
        if not separator:
            raise ConfigError(f"--repo는 owner/repo 형식이어야 합니다: {repo_arg!r}")
        for candidate in config.repositories:
            if candidate.owner == owner and candidate.repo == repo:
                return candidate
        raise ConfigError(f"config/repositories.yaml에 없는 저장소입니다: {repo_arg!r}")

    enabled = config.enabled_repositories
    if len(enabled) != 1:
        raise ConfigError(
            "timeline 명령은 대상 저장소를 하나로 특정할 수 없습니다 "
            f"(enabled repositories: {len(enabled)}개). --repo owner/repo를 지정하세요."
        )
    return enabled[0]


def _run_timeline_command(args: argparse.Namespace, config: DevBotConfig) -> int:
    try:
        repository = _resolve_repository(config, args.repo)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    # `getattr(..., False)`: only `start`/`end` define their own `--dry-run`
    # (`status` never writes, so it has none). Deliberately *not*
    # `config.dry_run` - see `_add_timeline_write_args`'s docstring for why
    # this command must default to a real write regardless of the
    # deployment's global `DRY_RUN` value.
    service = TimelineService(
        read_client=GitHubClient(config.github_token),
        write_client=GitHubWriteClient(config.github_token),
        dry_run=getattr(args, "dry_run", False),
    )

    try:
        if args.timeline_command == "start":
            outcome = service.start(
                repository, args.issue, phase=args.phase, actor=args.actor, pr=args.pr
            )
        elif args.timeline_command == "end":
            outcome = service.end(
                repository,
                args.issue,
                phase=args.phase,
                actor=args.actor,
                result=args.result,
                pr=args.pr,
            )
        else:
            outcome = service.status(repository, args.issue)
    except TimelineError as exc:
        print(f"timeline 오류: {exc}", file=sys.stderr)
        return 1

    print(outcome.status_card)
    return 0


def _run_doctor_command(config: DevBotConfig, *, ci: bool = False) -> int:
    """`devbot doctor` (Task 019 CP-019-5): read-only, never acquires the
    daemon lock (`devbot.doctor.check_daemon_lock` only probes it), so it
    is safe to run alongside an already-running DevBot process."""
    report = build_doctor_report(config, ci=ci)
    print(render_doctor_report(report))
    return 0 if report.safe_to_start else 1


def _sweep_stuck_working_issues(
    config: DevBotConfig,
    github_client: GitHubClient,
    state_writer: IssueStateWriter,
    logger: logging.Logger,
) -> None:
    """CP-B1: successfully acquiring `ProcessLock` (the caller's `with`
    block) guarantees no other process sharing this deployment's lock file
    can be mid-job right now - so any Issue still labeled `devbot:working`
    at this exact moment must be a crash remnant (the kernel releases
    `flock` on an unclean exit, but nothing else ever re-evaluates the
    label). Left alone, `polling.PollingService._collect_job_candidates`
    treats ANY `devbot:working` Issue as reason to exclude every other
    Issue in that repository too (`REPOSITORY_BUSY`), stalling the whole
    repository's queue indefinitely. Must live here, inside the daemon's
    `ProcessLock` block - not in `devbot.startup`, which `devbot doctor`
    also calls without ever holding the lock.
    """
    for repository in config.enabled_repositories:
        try:
            stuck = github_client.list_issues(
                repository, state="open", labels=["devbot:working"]
            )
        except Exception as exc:  # noqa: BLE001 - must never abort startup
            logger.error(
                "stuck-working sweep: 이슈 조회 실패 (%s): %s", repository.full_name, exc
            )
            continue
        for issue in stuck:
            try:
                state_writer.block(
                    repository,
                    issue,
                    "daemon 재시작 사이 devbot:working 상태로 남아있었습니다 "
                    "(이전 프로세스 크래시로 추정). 운영자 검토 후 devbot:ready로 "
                    "되돌리세요.",
                    job_type=None,
                )
            except Exception as exc:  # noqa: BLE001 - one failure must not abort the sweep
                logger.error(
                    "stuck-working sweep: %s #%d block 실패: %s",
                    repository.full_name,
                    issue.number,
                    exc,
                )


def _restart_after_startup_update(final_sha: str) -> None:
    if os.environ.get(STARTUP_SELF_UPDATE_ENV) == final_sha:
        return
    env = os.environ.copy()
    env[STARTUP_SELF_UPDATE_ENV] = final_sha
    os.execvpe(sys.executable, [sys.executable, *sys.argv], env)


def _run_startup_self_update(
    config: DevBotConfig, logger: logging.Logger, *, allow_dirty_skip: bool = False
) -> bool:
    try:
        results = run_startup_self_update(config)
    except StartupSelfUpdateError as exc:
        result = exc.result
        # CP-B0-1: only a dirty *operator checkout* is safe to treat as
        # non-fatal, and only when the caller opted in (daemon/--once under
        # --dry-run) - a developer's uncommitted local edits shouldn't stop
        # a supervised, no-real-effects run. Every other reason_code (wrong
        # branch, fetch/switch/pull failure, ...) stays fatal regardless,
        # since those indicate a more serious problem an operator should see
        # immediately rather than silently run past.
        if allow_dirty_skip and result.reason_code == "dirty_checkout":
            logger.warning(
                "startup self-update skipped: dirty operator checkout allowed under "
                "dry-run (repository=%s current_sha=%s skip_reason=%s)",
                result.repository,
                result.current_sha,
                result.skip_reason,
            )
            return True
        logger.error(
            "startup self-update failed: repository=%s current_sha=%s latest_sha=%s "
            "final_sha=%s result=%s skip_reason=%s",
            result.repository,
            result.current_sha,
            result.latest_sha,
            result.final_sha,
            result.result,
            result.skip_reason,
        )
        return False
    for result in results:
        logger.info(
            "startup self-update: repository=%s current_sha=%s latest_sha=%s "
            "final_sha=%s result=%s skip_reason=%s",
            result.repository,
            result.current_sha,
            result.latest_sha,
            result.final_sha,
            result.result,
            result.skip_reason,
        )
        if result.current_sha != result.final_sha:
            _restart_after_startup_update(result.final_sha)
    return True


def main(
    argv: Sequence[str] | None = None,
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> int:
    args = _parse_args(argv)
    if args.version:
        print(f"devbot {authoritative_version()}")
        return 0

    logger = _configure_logging()

    try:
        config = load_config(env_path=env_path, repositories_path=repositories_path)
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 1

    if args.dry_run and not config.dry_run:
        config = replace(config, dry_run=True)

    _apply_log_level(logger, config.log_level, verbose=args.verbose)
    install_secret_filter(logger, [config.github_token])

    if args.command == "timeline":
        return _run_timeline_command(args, config)

    if args.command == "worktree":
        return _run_worktree_command(args, config)

    if args.command == "release":
        return _run_release_command(args, config)

    if args.command == "goal":
        return _run_goal_command(args, config)

    if args.command == "role":
        return _run_role_command(args, config)

    if args.command == "agent":
        return _run_agent_command(args, config)

    if args.command == "specification":
        return _run_specification_command(args, config)

    if args.command == "doctor":
        if not args.ci and not _run_startup_self_update(config, logger):
            return 1
        return _run_doctor_command(config, ci=args.ci)

    try:
        with ProcessLock(config.lock_file):
            log_startup(logger, config)
            if config.enabled_repositories and not _run_startup_self_update(
                config, logger, allow_dirty_skip=config.dry_run
            ):
                return 1
            # Task 019 CP-019-4: informational only (see
            # `devbot.startup`'s module docstring) - the two genuinely
            # fatal startup conditions (bad config, a duplicate daemon
            # instance) already stopped `main()` above this point via
            # `ConfigError`/`LockAcquisitionError`, before any GitHub
            # write client exists (CP-019-8).
            log_startup_validation(logger, run_startup_checks(config))
            write_client = GitHubWriteClient(config.github_token)
            github_client = GitHubClient(config.github_token)
            # Task 041: dispatch(role) instead of dispatch(agent) - main.py
            # asks the Router which Agent backs the "implementer"/"reviewer"
            # Role, rather than reading `config.implementer_agent`/
            # `.reviewer_agent` directly. `build_agent_runner` (the
            # execution backend) is unchanged; when no `config/agents.yaml`
            # exists, `agent_registry` is synthesized from those same two
            # config fields, so the resolved backend - and therefore
            # dispatch behavior - is identical to before this Task.
            try:
                agent_registry = load_agent_registry(config)
                implementer_backend = resolve_agent(agent_registry, "implementer").backend
                reviewer_backend = resolve_agent(agent_registry, "reviewer").backend
            except (AgentRegistryError, RoutingError) as exc:
                print(f"agent registry 오류: {exc}", file=sys.stderr)
                return 1
            implementer_runner = build_agent_runner(implementer_backend, dry_run=config.dry_run)
            reviewer_runner = build_agent_runner(reviewer_backend, dry_run=config.dry_run)
            state_writer = IssueStateWriter(
                client=write_client, dry_run=config.dry_run, logger=logger
            )
            _sweep_stuck_working_issues(config, github_client, state_writer, logger)
            # Task 023: host-managed workspace preparation - every IMPLEMENT/
            # REWORK Job runs in its own isolated Git worktree under
            # `config.workspace_root`, resolved and synchronized by DevBot
            # itself before the Agent ever runs (see `devbot.worktree`).
            worktree_manager = WorktreeManager(
                workspace_root=config.workspace_root, dry_run=config.dry_run
            )
            # Task 024: automatic Timeline recording. `dry_run=config.dry_run`
            # (not the manual `timeline` CLI's own `False` default - see
            # `_run_timeline_command` above) so automatic writes obey the
            # same global safety switch as every other daemon write path.
            timeline_service = TimelineService(
                read_client=github_client,
                write_client=write_client,
                dry_run=config.dry_run,
            )
            polling_service = PollingService(
                config=config,
                github_client=github_client,
                implementer_runner=implementer_runner,
                reviewer_runner=reviewer_runner,
                prepare_workspace=worktree_manager.prepare,
                validate_review_integration=worktree_manager.validate_review_integration,
                state_writer=state_writer,
                delivery=DeliveryService(client=write_client, dry_run=config.dry_run),
                rework_service=ReworkService(
                    state_writer=state_writer,
                    write_client=write_client,
                    apply_changes=lambda repository, issue, comment: _apply_rework_changes(
                        implementer_runner, repository, issue, comment
                    ),
                    dry_run=config.dry_run,
                    timeline=timeline_service,
                    actor=config.implementer_agent,
                    logger=logger,
                ),
                review_service=ReviewService(
                    state_writer=state_writer,
                    write_client=write_client,
                    reviewer_runner=reviewer_runner,
                    dry_run=config.dry_run,
                    timeline=timeline_service,
                    actor=config.reviewer_agent,
                    logger=logger,
                    review_loop_limit=config.review_loop_limit,
                    current_head_sha=lambda repository, pull_request: next(
                        candidate.head_sha
                        for candidate in github_client.list_pull_requests(repository)
                        if candidate.number == pull_request.number
                    ),
                ),
                automerge_service=AutomergeService(
                    config=config,
                    write_client=write_client,
                    state_writer=state_writer,
                    list_check_runs_for_ref=github_client.list_check_runs_for_ref,
                    logger=logger,
                ),
                timeline=timeline_service,
                logger=logger,
            )

            if args.once:
                results = polling_service.run_cycle()
                for result in results:
                    logger.info("1회 실행 완료: %s", result.status.value)
                return 1 if any(result.status in _FAILURE_STATUSES for result in results) else 0

            run_forever(polling_service, config.poll_interval_seconds, logger=logger)
            return 0
    except LockAcquisitionError as exc:
        print(f"락 오류: {exc}", file=sys.stderr)
        return 1


def run() -> None:
    sys.exit(main())


if __name__ == "__main__":
    run()
