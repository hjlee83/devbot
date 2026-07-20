"""Runtime adapter layer for approved GoalExecutionPlan documents.

This module keeps runtime concerns outside ``goal_execution_foundation``. It
loads versioned approved-plan documents, persists ``GoalRun`` state atomically,
and maps provider-neutral execution/verification requests to adapter protocols
that existing DevBot services can implement.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from devbot.agent_execution import AgentExecutionContext, AgentRole
from devbot.agents.base import AgentRunner
from devbot.github_client import GitHubClient, PullRequest
from devbot.goal_execution_foundation import (
    AgentSelection,
    AITokenBudget,
    ApiUsage,
    BudgetConsumption,
    CompletionSnapshot,
    DefinitionOfDoneCriterion,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    ExhaustionBehavior,
    GateKind,
    GoalExecutionPlan,
    GoalRun,
    GoalState,
    ResourceStrategy,
    RoleExecutionPolicy,
    TaskGraph,
    TaskNode,
    TaskNodeState,
    VerificationEvidence,
    VerificationGate,
    VerificationOutcome,
    VerificationPlan,
    VerificationRequest,
)
from devbot.models import IssueTask, Job, JobType, Priority, RepositoryConfig, TaskState
from devbot.runtime_scheduler import RuntimeScheduler
from devbot.validation import (
    CommandExecution,
    run_validation_command,
    validation_commands_with_environment,
)
from devbot.workspace import build_agent_prompt
from devbot.worktree import WorktreeManager

APPROVED_GOAL_PLAN_SCHEMA_VERSION = 1
GOAL_RUNTIME_STATE_SCHEMA_VERSION = 1


class GoalRuntimeAdapterError(ValueError):
    """Raised when approved Goal runtime data cannot be loaded or advanced."""


class UnsupportedGoalSchemaError(GoalRuntimeAdapterError):
    """Raised when a plan/state document uses an unsupported schema version."""


class CorruptGoalStateError(GoalRuntimeAdapterError):
    """Raised when persisted state is unreadable or structurally invalid."""


@dataclass(frozen=True)
class GoalTaskBinding:
    node_id: str
    repository: str
    issue_number: int
    contract_path: str
    branch: str


@dataclass(frozen=True)
class RuntimeSideEffectMarker:
    marker_id: str
    kind: str
    node_id: str
    request_role: str
    started_at: str
    recovery: str = "fail_closed"


@dataclass(frozen=True)
class ApprovedGoalPlanDocument:
    schema_version: int
    plan: GoalExecutionPlan
    task_bindings: tuple[GoalTaskBinding, ...]

    def binding_for(self, node_id: str) -> GoalTaskBinding:
        for binding in self.task_bindings:
            if binding.node_id == node_id:
                return binding
        raise GoalRuntimeAdapterError(f"missing task binding for node {node_id!r}")

    def validate(self) -> None:
        if self.schema_version != APPROVED_GOAL_PLAN_SCHEMA_VERSION:
            raise UnsupportedGoalSchemaError(
                f"unsupported approved Goal plan schema version: {self.schema_version}"
            )
        node_ids = self.plan.task_graph.node_ids
        binding_id_list = [binding.node_id for binding in self.task_bindings]
        duplicate_ids = sorted(
            node_id for node_id in set(binding_id_list) if binding_id_list.count(node_id) > 1
        )
        if duplicate_ids:
            raise GoalRuntimeAdapterError(f"duplicate task bindings: {', '.join(duplicate_ids)}")
        binding_ids = set(binding_id_list)
        missing = sorted(node_ids - binding_ids)
        extra = sorted(binding_ids - node_ids)
        if missing:
            raise GoalRuntimeAdapterError(f"missing task bindings: {', '.join(missing)}")
        if extra:
            raise GoalRuntimeAdapterError(f"unknown task bindings: {', '.join(extra)}")


@dataclass(frozen=True)
class GoalRuntimeStateDocument:
    schema_version: int
    run: GoalRun
    completion_snapshot: CompletionSnapshot | None = None
    in_flight_side_effect: RuntimeSideEffectMarker | None = None

    def validate(self) -> None:
        if self.schema_version != GOAL_RUNTIME_STATE_SCHEMA_VERSION:
            raise UnsupportedGoalSchemaError(
                f"unsupported Goal runtime state schema version: {self.schema_version}"
            )


class GoalExecutionAdapter(Protocol):
    def execute(self, request: ExecutionRequest, binding: GoalTaskBinding) -> ExecutionResult:
        """Run an execution request through a concrete runtime seam."""


class GoalVerificationAdapter(Protocol):
    def verify(
        self, request: VerificationRequest, binding: GoalTaskBinding
    ) -> VerificationEvidence:
        """Run a verification request through a concrete validation/review seam."""


@dataclass(frozen=True)
class ExistingDevBotExecutionAdapter:
    """Concrete adapter that composes existing workspace and agent seams.

    The callables are the same seam shape production code already owns:
    prepare a workspace/context for the bound Task, then dispatch the selected
    role through the existing Agent execution path.
    """

    prepare_execution_context: Callable[[ExecutionRequest, GoalTaskBinding], object]
    dispatch_agent: Callable[[object, ExecutionRequest, GoalTaskBinding], ExecutionResult | str]

    def execute(self, request: ExecutionRequest, binding: GoalTaskBinding) -> ExecutionResult:
        context = self.prepare_execution_context(request, binding)
        result = self.dispatch_agent(context, request, binding)
        if isinstance(result, ExecutionResult):
            return result
        return ExecutionResult(request.node_id, True, str(result))


@dataclass(frozen=True)
class ExistingDevBotVerificationAdapter:
    """Concrete adapter for deterministic validation and review seams."""

    run_validation_gate: Callable[
        [VerificationRequest, GoalTaskBinding], VerificationEvidence | str
    ]
    run_review_gate: (
        Callable[[VerificationRequest, GoalTaskBinding], VerificationEvidence | str] | None
    ) = None

    def verify(
        self, request: VerificationRequest, binding: GoalTaskBinding
    ) -> VerificationEvidence:
        if request.gate is GateKind.ARCHITECTURE and self.run_review_gate is not None:
            result = self.run_review_gate(request, binding)
        else:
            result = self.run_validation_gate(request, binding)
        if isinstance(result, VerificationEvidence):
            return result
        return VerificationEvidence(
            node_id=request.node_id,
            gate=request.gate,
            outcome=VerificationOutcome.PASS,
            evidence=str(result),
        )


@dataclass(frozen=True)
class DevBotGoalExecutionAdapter:
    """Production composition for Goal execution requests.

    This reaches the same concrete seams as the daemon's implement path:
    GitHub issue/PR lookup, host-managed workspace preparation, prompt
    construction, and role-based AgentRunner execution.
    """

    repositories: Sequence[RepositoryConfig]
    github_client: GitHubClient
    worktree_manager: WorktreeManager
    agent_runner: AgentRunner

    def execute(self, request: ExecutionRequest, binding: GoalTaskBinding) -> ExecutionResult:
        repository = _repository_for_binding(self.repositories, binding)
        issue = self.github_client.get_issue(repository, binding.issue_number)
        linked_pull_request = _find_pull_request_for_binding(
            self.github_client.list_pull_requests(repository, state="open"),
            binding,
        )
        prepared = self.worktree_manager.prepare(repository, issue, linked_pull_request)
        context = AgentExecutionContext(
            repository=repository,
            prepared_workspace=prepared,
            canonical_branch=prepared.branch,
            issue=issue,
            pull_request=prepared.pull_request,
            execution_id=f"{request.goal_id}:{request.node_id}:{request.role}",
            role=AgentRole.IMPLEMENT if request.role != "reviewer" else AgentRole.REVIEW,
        )
        prompt = build_agent_prompt(repository, issue, [])
        result = self.agent_runner.run_context(context, prompt)
        return ExecutionResult(
            node_id=request.node_id,
            success=not result.failed,
            evidence=result.message or f"agent returncode={result.returncode}",
        )


@dataclass(frozen=True)
class DevBotGoalVerificationAdapter:
    """Production composition for deterministic validation and review gates."""

    repositories: Sequence[RepositoryConfig]
    run_command: Callable[
        [RepositoryConfig, Sequence[str]], CommandExecution
    ] = run_validation_command
    review_gate: (
        Callable[[VerificationRequest, GoalTaskBinding], VerificationEvidence] | None
    ) = None

    def verify(
        self, request: VerificationRequest, binding: GoalTaskBinding
    ) -> VerificationEvidence:
        if request.gate is GateKind.ARCHITECTURE and self.review_gate is not None:
            return self.review_gate(request, binding)
        repository = _repository_for_binding(self.repositories, binding)
        executions = [
            self.run_command(repository, command)
            for command in validation_commands_with_environment()
        ]
        failed = next((execution for execution in executions if execution.returncode != 0), None)
        if failed is not None:
            return VerificationEvidence(
                node_id=request.node_id,
                gate=request.gate,
                outcome=VerificationOutcome.RETRY,
                evidence=_summarize_command_execution(failed),
                unresolved_findings=(_summarize_command_execution(failed),),
            )
        return VerificationEvidence(
            node_id=request.node_id,
            gate=request.gate,
            outcome=VerificationOutcome.PASS,
            evidence="; ".join(_summarize_command_execution(item) for item in executions),
        )


def load_approved_goal_plan(path: Path) -> ApprovedGoalPlanDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorruptGoalStateError(f"cannot read approved Goal plan {path}: {exc}") from exc
    document = _approved_plan_document_from_dict(raw)
    document.validate()
    return document


def write_approved_goal_plan(document: ApprovedGoalPlanDocument, path: Path) -> None:
    document.validate()
    _atomic_write_json(path, _approved_plan_document_to_dict(document))


def load_goal_runtime_state(path: Path) -> GoalRuntimeStateDocument:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorruptGoalStateError(f"cannot read Goal runtime state {path}: {exc}") from exc
    document = _runtime_state_document_from_dict(raw)
    document.validate()
    return document


def write_goal_runtime_state(document: GoalRuntimeStateDocument, path: Path) -> None:
    document.validate()
    _atomic_write_json(path, _runtime_state_document_to_dict(document))


def start_goal_runtime(plan_document: ApprovedGoalPlanDocument, state_path: Path) -> GoalRun:
    plan_document.validate()
    run = GoalRun(plan=plan_document.plan).start().request_next_execution()
    write_goal_runtime_state(
        GoalRuntimeStateDocument(schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION, run=run),
        state_path,
    )
    return run


def resume_goal_runtime(state_path: Path) -> GoalRun:
    state = load_goal_runtime_state(state_path)
    run = state.run
    if state.in_flight_side_effect is not None:
        marker = state.in_flight_side_effect
        run = replace(
            run,
            state=GoalState.ESCALATED,
            reason=(
                "ambiguous in-flight side effect detected during resume: "
                f"{marker.kind} node={marker.node_id} marker={marker.marker_id}"
            ),
        )
        completion = run.completion_snapshot()
        write_goal_runtime_state(
            GoalRuntimeStateDocument(
                schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
                run=run,
                completion_snapshot=completion,
                in_flight_side_effect=marker,
            ),
            state_path,
        )
        return run
    if run.state in {GoalState.REVIEW_REQUESTED, GoalState.ESCALATED, GoalState.FAILED}:
        return run
    if run.pending_execution_request is None and not run.pending_verification_requests:
        run = run.request_next_execution()
        write_goal_runtime_state(
            GoalRuntimeStateDocument(
                schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
                run=run,
                completion_snapshot=state.completion_snapshot,
            ),
            state_path,
        )
    return run


def submit_pending_execution(
    *,
    plan_document: ApprovedGoalPlanDocument,
    state_path: Path,
    scheduler: RuntimeScheduler,
    adapter: GoalExecutionAdapter,
) -> GoalRun:
    state = load_goal_runtime_state(state_path)
    run = state.run
    request = run.pending_execution_request
    if request is None:
        raise GoalRuntimeAdapterError("no pending execution request")
    binding = plan_document.binding_for(request.node_id)
    job = _job_for_binding(binding)
    marker = _execution_marker(request)
    write_goal_runtime_state(
        GoalRuntimeStateDocument(
            schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
            run=run,
            completion_snapshot=state.completion_snapshot,
            in_flight_side_effect=marker,
        ),
        state_path,
    )

    def run_job(_: Job) -> ExecutionResult:
        return adapter.execute(request, binding)

    results = scheduler.execute(
        [job],
        run_job,
        lambda _job, exc: ExecutionResult(request.node_id, False, str(exc)),
    )
    advanced = run.record_execution_result(results[0])
    completion = (
        advanced.completion_snapshot()
        if advanced.state in {GoalState.REVIEW_REQUESTED, GoalState.ESCALATED, GoalState.FAILED}
        else state.completion_snapshot
    )
    write_goal_runtime_state(
        GoalRuntimeStateDocument(
            schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
            run=advanced,
            completion_snapshot=completion,
            in_flight_side_effect=None,
        ),
        state_path,
    )
    return advanced


def submit_pending_executions(
    *,
    plan_documents: Sequence[ApprovedGoalPlanDocument],
    state_paths: Sequence[Path],
    scheduler: RuntimeScheduler,
    adapter: GoalExecutionAdapter,
) -> tuple[GoalRun, ...]:
    if len(plan_documents) != len(state_paths):
        raise GoalRuntimeAdapterError("plan_documents and state_paths must have equal length")
    loaded = tuple(load_goal_runtime_state(path) for path in state_paths)
    requests: list[ExecutionRequest] = []
    bindings: list[GoalTaskBinding] = []
    jobs: list[Job] = []
    for document, state in zip(plan_documents, loaded, strict=True):
        request = state.run.pending_execution_request
        if request is None:
            raise GoalRuntimeAdapterError("all scheduled Goal states need pending execution")
        binding = document.binding_for(request.node_id)
        requests.append(request)
        bindings.append(binding)
        jobs.append(_job_for_binding(binding))

    for state_path, state, request in zip(state_paths, loaded, requests, strict=True):
        write_goal_runtime_state(
            GoalRuntimeStateDocument(
                schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
                run=state.run,
                completion_snapshot=state.completion_snapshot,
                in_flight_side_effect=_execution_marker(request),
            ),
            state_path,
        )

    index_by_job_id = {id(job): index for index, job in enumerate(jobs)}

    def run_job(job: Job) -> ExecutionResult:
        index = index_by_job_id[id(job)]
        return adapter.execute(requests[index], bindings[index])

    results = scheduler.execute(
        jobs,
        run_job,
        lambda job, exc: ExecutionResult(
            requests[index_by_job_id[id(job)]].node_id,
            False,
            str(exc),
        ),
    )
    advanced_runs: list[GoalRun] = []
    for state_path, state, result in zip(state_paths, loaded, results, strict=True):
        advanced = state.run.record_execution_result(result)
        completion = (
            advanced.completion_snapshot()
            if advanced.state in {GoalState.REVIEW_REQUESTED, GoalState.ESCALATED, GoalState.FAILED}
            else state.completion_snapshot
        )
        write_goal_runtime_state(
            GoalRuntimeStateDocument(
                schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
                run=advanced,
                completion_snapshot=completion,
                in_flight_side_effect=None,
            ),
            state_path,
        )
        advanced_runs.append(advanced)
    return tuple(advanced_runs)


def process_pending_verification(
    *,
    plan_document: ApprovedGoalPlanDocument,
    state_path: Path,
    adapter: GoalVerificationAdapter,
) -> GoalRun:
    state = load_goal_runtime_state(state_path)
    run = state.run
    if not run.pending_verification_requests:
        raise GoalRuntimeAdapterError("no pending verification request")
    request = run.pending_verification_requests[0]
    binding = plan_document.binding_for(request.node_id)
    marker = _verification_marker(request)
    write_goal_runtime_state(
        GoalRuntimeStateDocument(
            schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
            run=run,
            completion_snapshot=state.completion_snapshot,
            in_flight_side_effect=marker,
        ),
        state_path,
    )
    evidence = adapter.verify(request, binding)
    advanced = run.record_verification_outcome(evidence)
    if (
        advanced.state is GoalState.EXECUTING
        and advanced.pending_execution_request is None
        and not advanced.pending_verification_requests
    ):
        advanced = advanced.request_next_execution()
    completion = (
        advanced.completion_snapshot()
        if advanced.state in {GoalState.REVIEW_REQUESTED, GoalState.ESCALATED, GoalState.FAILED}
        else state.completion_snapshot
    )
    write_goal_runtime_state(
        GoalRuntimeStateDocument(
            schema_version=GOAL_RUNTIME_STATE_SCHEMA_VERSION,
            run=advanced,
            completion_snapshot=completion,
            in_flight_side_effect=None,
        ),
        state_path,
    )
    return advanced


def render_goal_runtime_status(state: GoalRuntimeStateDocument) -> str:
    run = state.run
    graph = run.graph or run.plan.task_graph
    lines = [
        f"goal_id: {run.plan.goal_id}",
        f"state: {run.state.value}",
        f"reason: {run.reason or 'none'}",
        "tasks:",
    ]
    for node in graph.nodes:
        lines.append(f"  - {node.node_id}: {node.state.value}")
    if run.pending_execution_request is not None:
        request = run.pending_execution_request
        lines.append(
            f"pending_execution: node={request.node_id} role={request.role}"
        )
    else:
        lines.append("pending_execution: none")
    if run.pending_verification_requests:
        lines.append("pending_verification:")
        for request in run.pending_verification_requests:
            budget = "yes" if request.consumes_ai_budget else "no"
            lines.append(
                f"  - node={request.node_id} gate={request.gate.value} consumes_ai_budget={budget}"
            )
    else:
        lines.append("pending_verification: none")
    budget = run.budget_consumption
    lines.append(f"implementation_retries: {dict(budget.implementation_retries_by_node)}")
    lines.append(f"architecture_review_calls: {dict(budget.architecture_review_calls_by_node)}")
    lines.append(f"architecture_review_calls_total: {budget.architecture_review_calls_total}")
    if state.completion_snapshot is not None:
        lines.append(f"completion_final_state: {state.completion_snapshot.final_state.value}")
    return "\n".join(lines)


def _job_for_binding(binding: GoalTaskBinding) -> Job:
    return Job(
        job_type=JobType.IMPLEMENT,
        task=IssueTask(
            repository=binding.repository,
            number=binding.issue_number,
            title=binding.node_id,
            state=TaskState.READY,
            priority=Priority.NONE,
            created_at=datetime.fromtimestamp(0, UTC),
        ),
    )


def _repository_for_binding(
    repositories: Sequence[RepositoryConfig], binding: GoalTaskBinding
) -> RepositoryConfig:
    for repository in repositories:
        if repository.full_name == binding.repository:
            return repository
    raise GoalRuntimeAdapterError(f"repository is not configured: {binding.repository}")


def _find_pull_request_for_binding(
    pull_requests: Sequence[PullRequest], binding: GoalTaskBinding
) -> PullRequest | None:
    for pull_request in pull_requests:
        if pull_request.head_ref == binding.branch:
            return pull_request
    return None


def _summarize_command_execution(execution: CommandExecution) -> str:
    output = execution.output.strip().replace("\n", " ")
    if len(output) > 240:
        output = output[:240]
    command = " ".join(execution.command)
    return f"{command} exit={execution.returncode}" + (f" output={output}" if output else "")


def _atomic_write_json(path: Path, data: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def _execution_marker(request: ExecutionRequest) -> RuntimeSideEffectMarker:
    return RuntimeSideEffectMarker(
        marker_id=f"{request.goal_id}:{request.node_id}:{request.role}",
        kind="execution",
        node_id=request.node_id,
        request_role=request.role,
        started_at=datetime.now(UTC).isoformat(),
    )


def _verification_marker(request: VerificationRequest) -> RuntimeSideEffectMarker:
    return RuntimeSideEffectMarker(
        marker_id=f"{request.goal_id}:{request.node_id}:{request.gate.value}",
        kind="verification",
        node_id=request.node_id,
        request_role=request.gate.value,
        started_at=datetime.now(UTC).isoformat(),
    )


def _approved_plan_document_to_dict(document: ApprovedGoalPlanDocument) -> dict[str, object]:
    return {
        "schema_version": document.schema_version,
        "plan": _plan_to_dict(document.plan),
        "task_bindings": [_binding_to_dict(binding) for binding in document.task_bindings],
    }


def _approved_plan_document_from_dict(raw: Mapping[str, object]) -> ApprovedGoalPlanDocument:
    return ApprovedGoalPlanDocument(
        schema_version=_int(raw, "schema_version"),
        plan=_plan_from_dict(_mapping(raw, "plan")),
        task_bindings=tuple(_binding_from_dict(item) for item in _sequence(raw, "task_bindings")),
    )


def _runtime_state_document_to_dict(document: GoalRuntimeStateDocument) -> dict[str, object]:
    return {
        "schema_version": document.schema_version,
        "run": _run_to_dict(document.run),
        "completion_snapshot": (
            _completion_snapshot_to_dict(document.completion_snapshot)
            if document.completion_snapshot is not None
            else None
        ),
        "in_flight_side_effect": (
            _side_effect_marker_to_dict(document.in_flight_side_effect)
            if document.in_flight_side_effect is not None
            else None
        ),
    }


def _runtime_state_document_from_dict(raw: Mapping[str, object]) -> GoalRuntimeStateDocument:
    completion_raw = raw.get("completion_snapshot")
    marker_raw = raw.get("in_flight_side_effect")
    return GoalRuntimeStateDocument(
        schema_version=_int(raw, "schema_version"),
        run=_run_from_dict(_mapping(raw, "run")),
        completion_snapshot=(
            _completion_snapshot_from_dict(completion_raw)
            if isinstance(completion_raw, Mapping)
            else None
        ),
        in_flight_side_effect=(
            _side_effect_marker_from_dict(marker_raw)
            if isinstance(marker_raw, Mapping)
            else None
        ),
    )


def _plan_to_dict(plan: GoalExecutionPlan) -> dict[str, object]:
    return {
        "goal_id": plan.goal_id,
        "objective": plan.objective,
        "approved_scope": list(plan.approved_scope),
        "non_goals": list(plan.non_goals),
        "definition_of_done": [
            {
                "description": item.description,
                "verifiable_by": item.verifiable_by.value,
                "required": item.required,
            }
            for item in plan.definition_of_done
        ],
        "task_graph": _task_graph_to_dict(plan.task_graph),
        "verification_plan": {
            "gates": [
                {
                    "kind": gate.kind.value,
                    "required": gate.required,
                    "node_ids": list(gate.node_ids),
                    "ai_review_required": gate.ai_review_required,
                }
                for gate in plan.verification_plan.gates
            ]
        },
        "execution_policy": _execution_policy_to_dict(plan.execution_policy),
        "resource_strategy": _resource_strategy_to_dict(plan.resource_strategy),
        "budget": _budget_to_dict(plan.budget),
        "exit_conditions": list(plan.exit_conditions),
        "escalation_conditions": list(plan.escalation_conditions),
    }


def _plan_from_dict(raw: Mapping[str, object]) -> GoalExecutionPlan:
    budget = _budget_from_dict(_mapping(raw, "budget"))
    execution_policy = _execution_policy_from_dict(_mapping(raw, "execution_policy"))
    resource_raw = _mapping(raw, "resource_strategy")
    resource_strategy = ResourceStrategy(
        input_channel=_str(resource_raw, "input_channel"),
        execution_policy=execution_policy,
        budget=budget,
    )
    return GoalExecutionPlan(
        goal_id=_str(raw, "goal_id"),
        objective=_str(raw, "objective"),
        approved_scope=_str_tuple(raw, "approved_scope"),
        non_goals=_str_tuple(raw, "non_goals"),
        definition_of_done=tuple(
            DefinitionOfDoneCriterion(
                description=_str(item, "description"),
                verifiable_by=GateKind(_str(item, "verifiable_by")),
                required=_bool(item, "required", default=True),
            )
            for item in _sequence(raw, "definition_of_done")
        ),
        task_graph=_task_graph_from_dict(_mapping(raw, "task_graph")),
        verification_plan=VerificationPlan(
            tuple(
                VerificationGate(
                    kind=GateKind(_str(item, "kind")),
                    required=_bool(item, "required", default=True),
                    node_ids=_str_tuple(item, "node_ids", default=()),
                    ai_review_required=_bool(item, "ai_review_required", default=False),
                )
                for item in _sequence(_mapping(raw, "verification_plan"), "gates")
            )
        ),
        execution_policy=execution_policy,
        resource_strategy=resource_strategy,
        budget=budget,
        exit_conditions=_str_tuple(raw, "exit_conditions"),
        escalation_conditions=_str_tuple(raw, "escalation_conditions"),
    )


def _task_graph_to_dict(graph: TaskGraph) -> dict[str, object]:
    return {
        "nodes": [
            {
                "node_id": node.node_id,
                "title": node.title,
                "dependencies": list(node.dependencies),
                "required": node.required,
                "state": node.state.value,
            }
            for node in graph.nodes
        ]
    }


def _task_graph_from_dict(raw: Mapping[str, object]) -> TaskGraph:
    return TaskGraph(
        tuple(
            TaskNode(
                node_id=_str(item, "node_id"),
                title=_str(item, "title"),
                dependencies=_str_tuple(item, "dependencies", default=()),
                required=_bool(item, "required", default=True),
                state=TaskNodeState(_str(item, "state", default=TaskNodeState.READY.value)),
            )
            for item in _sequence(raw, "nodes")
        )
    )


def _execution_policy_to_dict(policy: ExecutionPolicy) -> dict[str, object]:
    return {
        "routing_strategy": policy.routing_strategy,
        "roles": {
            role: {
                "primary": _agent_selection_to_dict(item.primary),
                "fallback": (
                    _agent_selection_to_dict(item.fallback) if item.fallback is not None else None
                ),
            }
            for role, item in policy.roles.items()
        },
    }


def _execution_policy_from_dict(raw: Mapping[str, object]) -> ExecutionPolicy:
    roles = {
        str(role): RoleExecutionPolicy(
            primary=_agent_selection_from_dict(_mapping(item, "primary")),
            fallback=(
                _agent_selection_from_dict(fallback)
                if isinstance((fallback := item.get("fallback")), Mapping)
                else None
            ),
        )
        for role, item in _mapping(raw, "roles").items()
        if isinstance(item, Mapping)
    }
    return ExecutionPolicy(roles=roles, routing_strategy=_str(raw, "routing_strategy"))


def _agent_selection_to_dict(selection: AgentSelection) -> dict[str, object]:
    return {
        "execution_mode": selection.execution_mode.value,
        "resource": selection.resource,
        "runtime": selection.runtime,
        "model": selection.model,
        "agent": selection.agent,
    }


def _agent_selection_from_dict(raw: Mapping[str, object]) -> AgentSelection:
    return AgentSelection(
        execution_mode=ExecutionMode(_str(raw, "execution_mode")),
        resource=_str(raw, "resource"),
        runtime=_str(raw, "runtime"),
        model=_optional_str(raw, "model"),
        agent=_optional_str(raw, "agent"),
    )


def _resource_strategy_to_dict(strategy: ResourceStrategy) -> dict[str, object]:
    return {
        "input_channel": strategy.input_channel,
        "execution_policy": _execution_policy_to_dict(strategy.execution_policy),
        "budget": _budget_to_dict(strategy.budget),
    }


def _budget_to_dict(budget: AITokenBudget) -> dict[str, object]:
    return {
        "max_planner_calls": budget.max_planner_calls,
        "max_implementation_retries": budget.max_implementation_retries,
        "max_architecture_review_calls_per_node": (
            budget.max_architecture_review_calls_per_node
        ),
        "max_architecture_review_calls_per_goal": (
            budget.max_architecture_review_calls_per_goal
        ),
        "api_usage": budget.api_usage.value,
        "exhaustion_behavior": budget.exhaustion_behavior.value,
    }


def _budget_from_dict(raw: Mapping[str, object]) -> AITokenBudget:
    return AITokenBudget(
        max_planner_calls=_int(raw, "max_planner_calls"),
        max_implementation_retries=_int(raw, "max_implementation_retries"),
        max_architecture_review_calls_per_node=_int(
            raw, "max_architecture_review_calls_per_node"
        ),
        max_architecture_review_calls_per_goal=_int(
            raw, "max_architecture_review_calls_per_goal"
        ),
        api_usage=ApiUsage(_str(raw, "api_usage")),
        exhaustion_behavior=ExhaustionBehavior(_str(raw, "exhaustion_behavior")),
    )


def _run_to_dict(run: GoalRun) -> dict[str, object]:
    return {
        "plan": _plan_to_dict(run.plan),
        "state": run.state.value,
        "graph": _task_graph_to_dict(run.graph) if run.graph is not None else None,
        "pending_execution_request": (
            _execution_request_to_dict(run.pending_execution_request)
            if run.pending_execution_request is not None
            else None
        ),
        "pending_verification_requests": [
            _verification_request_to_dict(item) for item in run.pending_verification_requests
        ],
        "evidence": [_verification_evidence_to_dict(item) for item in run.evidence],
        "budget_consumption": _budget_consumption_to_dict(run.budget_consumption),
        "reason": run.reason,
    }


def _run_from_dict(raw: Mapping[str, object]) -> GoalRun:
    graph_raw = raw.get("graph")
    execution_raw = raw.get("pending_execution_request")
    return GoalRun(
        plan=_plan_from_dict(_mapping(raw, "plan")),
        state=GoalState(_str(raw, "state")),
        graph=_task_graph_from_dict(graph_raw) if isinstance(graph_raw, Mapping) else None,
        pending_execution_request=(
            _execution_request_from_dict(execution_raw)
            if isinstance(execution_raw, Mapping)
            else None
        ),
        pending_verification_requests=tuple(
            _verification_request_from_dict(item)
            for item in _sequence(raw, "pending_verification_requests", default=())
        ),
        evidence=tuple(
            _verification_evidence_from_dict(item)
            for item in _sequence(raw, "evidence", default=())
        ),
        budget_consumption=_budget_consumption_from_dict(
            _mapping(raw, "budget_consumption")
        ),
        reason=_str(raw, "reason", default=""),
    )


def _binding_to_dict(binding: GoalTaskBinding) -> dict[str, object]:
    return {
        "node_id": binding.node_id,
        "repository": binding.repository,
        "issue_number": binding.issue_number,
        "contract_path": binding.contract_path,
        "branch": binding.branch,
    }


def _binding_from_dict(raw: Mapping[str, object]) -> GoalTaskBinding:
    return GoalTaskBinding(
        node_id=_str(raw, "node_id"),
        repository=_str(raw, "repository"),
        issue_number=_int(raw, "issue_number"),
        contract_path=_str(raw, "contract_path"),
        branch=_str(raw, "branch"),
    )


def _side_effect_marker_to_dict(marker: RuntimeSideEffectMarker) -> dict[str, object]:
    return {
        "marker_id": marker.marker_id,
        "kind": marker.kind,
        "node_id": marker.node_id,
        "request_role": marker.request_role,
        "started_at": marker.started_at,
        "recovery": marker.recovery,
    }


def _side_effect_marker_from_dict(raw: Mapping[str, object]) -> RuntimeSideEffectMarker:
    return RuntimeSideEffectMarker(
        marker_id=_str(raw, "marker_id"),
        kind=_str(raw, "kind"),
        node_id=_str(raw, "node_id"),
        request_role=_str(raw, "request_role"),
        started_at=_str(raw, "started_at"),
        recovery=_str(raw, "recovery", default="fail_closed"),
    )


def _execution_request_to_dict(request: ExecutionRequest) -> dict[str, object]:
    return {"goal_id": request.goal_id, "node_id": request.node_id, "role": request.role}


def _execution_request_from_dict(raw: Mapping[str, object]) -> ExecutionRequest:
    return ExecutionRequest(
        goal_id=_str(raw, "goal_id"),
        node_id=_str(raw, "node_id"),
        role=_str(raw, "role", default="implementer"),
    )


def _verification_request_to_dict(request: VerificationRequest) -> dict[str, object]:
    return {
        "goal_id": request.goal_id,
        "node_id": request.node_id,
        "gate": request.gate.value,
        "consumes_ai_budget": request.consumes_ai_budget,
    }


def _verification_request_from_dict(raw: Mapping[str, object]) -> VerificationRequest:
    return VerificationRequest(
        goal_id=_str(raw, "goal_id"),
        node_id=_str(raw, "node_id"),
        gate=GateKind(_str(raw, "gate")),
        consumes_ai_budget=_bool(raw, "consumes_ai_budget", default=False),
    )


def _verification_evidence_to_dict(evidence: VerificationEvidence) -> dict[str, object]:
    return {
        "node_id": evidence.node_id,
        "gate": evidence.gate.value,
        "outcome": evidence.outcome.value,
        "evidence": evidence.evidence,
        "unresolved_findings": list(evidence.unresolved_findings),
    }


def _verification_evidence_from_dict(raw: Mapping[str, object]) -> VerificationEvidence:
    return VerificationEvidence(
        node_id=_str(raw, "node_id"),
        gate=GateKind(_str(raw, "gate")),
        outcome=VerificationOutcome(_str(raw, "outcome")),
        evidence=_str(raw, "evidence"),
        unresolved_findings=_str_tuple(raw, "unresolved_findings", default=()),
    )


def _budget_consumption_to_dict(consumption: BudgetConsumption) -> dict[str, object]:
    return {
        "implementation_retries_by_node": dict(consumption.implementation_retries_by_node),
        "architecture_review_calls_by_node": dict(consumption.architecture_review_calls_by_node),
        "planner_calls": consumption.planner_calls,
    }


def _budget_consumption_from_dict(raw: Mapping[str, object]) -> BudgetConsumption:
    return BudgetConsumption(
        implementation_retries_by_node={
            str(key): int(value)
            for key, value in _mapping(raw, "implementation_retries_by_node").items()
        },
        architecture_review_calls_by_node={
            str(key): int(value)
            for key, value in _mapping(raw, "architecture_review_calls_by_node").items()
        },
        planner_calls=_int(raw, "planner_calls"),
    )


def _completion_snapshot_to_dict(snapshot: CompletionSnapshot) -> dict[str, object]:
    return {
        "goal_id": snapshot.goal_id,
        "completed_tasks": list(snapshot.completed_tasks),
        "verification_evidence": [
            _verification_evidence_to_dict(item) for item in snapshot.verification_evidence
        ],
        "budget_consumption": _budget_consumption_to_dict(snapshot.budget_consumption),
        "unresolved_findings": list(snapshot.unresolved_findings),
        "final_state": snapshot.final_state.value,
        "reason": snapshot.reason,
    }


def _completion_snapshot_from_dict(raw: Mapping[str, object]) -> CompletionSnapshot:
    return CompletionSnapshot(
        goal_id=_str(raw, "goal_id"),
        completed_tasks=_str_tuple(raw, "completed_tasks"),
        verification_evidence=tuple(
            _verification_evidence_from_dict(item)
            for item in _sequence(raw, "verification_evidence")
        ),
        budget_consumption=_budget_consumption_from_dict(
            _mapping(raw, "budget_consumption")
        ),
        unresolved_findings=_str_tuple(raw, "unresolved_findings"),
        final_state=GoalState(_str(raw, "final_state")),
        reason=_str(raw, "reason"),
    )


def _mapping(raw: Mapping[str, object] | object, key: str) -> Mapping[str, object]:
    if isinstance(raw, Mapping):
        value = raw.get(key)
        if isinstance(value, Mapping):
            return value
    raise CorruptGoalStateError(f"expected object field: {key}")


def _sequence(
    raw: Mapping[str, object] | object, key: str, *, default: Sequence[object] | None = None
) -> Sequence[Mapping[str, object]]:
    if isinstance(raw, Mapping):
        value = raw.get(key, default)
        if isinstance(value, list | tuple):
            if all(isinstance(item, Mapping) for item in value):
                return value
    raise CorruptGoalStateError(f"expected list field: {key}")


def _str(raw: Mapping[str, object], key: str, *, default: str | None = None) -> str:
    value = raw.get(key, default)
    if isinstance(value, str):
        return value
    raise CorruptGoalStateError(f"expected string field: {key}")


def _optional_str(raw: Mapping[str, object], key: str) -> str | None:
    value = raw.get(key)
    if value is None or isinstance(value, str):
        return value
    raise CorruptGoalStateError(f"expected optional string field: {key}")


def _str_tuple(
    raw: Mapping[str, object], key: str, *, default: Sequence[object] | None = None
) -> tuple[str, ...]:
    value = raw.get(key, default)
    if isinstance(value, list | tuple) and all(isinstance(item, str) for item in value):
        return tuple(value)
    raise CorruptGoalStateError(f"expected string list field: {key}")


def _int(raw: Mapping[str, object], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, int):
        return value
    raise CorruptGoalStateError(f"expected integer field: {key}")


def _bool(raw: Mapping[str, object], key: str, *, default: bool) -> bool:
    value = raw.get(key, default)
    if isinstance(value, bool):
        return value
    raise CorruptGoalStateError(f"expected boolean field: {key}")
