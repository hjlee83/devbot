"""Provider-neutral execution foundation for approved Goal plans.

This module is intentionally pure domain logic. It accepts an already-approved
``GoalExecutionPlan`` and emits/records typed requests and outcomes without
calling a concrete AI provider or mutating GitHub directly.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum


class GoalExecutionError(ValueError):
    """Base class for invalid Goal execution contracts or transitions."""


class PlanValidationError(GoalExecutionError):
    """Raised when a GoalExecutionPlan is structurally inconsistent."""


class IllegalGoalTransitionError(GoalExecutionError):
    """Raised when a Goal state transition violates the state machine."""


class BudgetExhaustedError(GoalExecutionError):
    """Raised when an execution or verification budget cannot be consumed."""


class GoalState(StrEnum):
    GOAL_PROPOSED = "GOAL_PROPOSED"
    GOAL_APPROVED = "GOAL_APPROVED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    REVISING = "REVISING"
    REVIEW_REQUESTED = "REVIEW_REQUESTED"
    AUDITING = "AUDITING"
    GOAL_ACCEPTED = "GOAL_ACCEPTED"
    RELEASE_REPORTED = "RELEASE_REPORTED"
    ESCALATED = "ESCALATED"
    FAILED = "FAILED"


class TaskNodeState(StrEnum):
    READY = "ready"
    RUNNING = "running"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    RETRYABLE = "retryable"
    BLOCKED = "blocked"
    ESCALATED = "escalated"


class VerificationOutcome(StrEnum):
    PASS = "PASS"
    RETRY = "RETRY"
    FAIL = "FAIL"
    ESCALATE = "ESCALATE"


class ExhaustionBehavior(StrEnum):
    STOP = "stop"
    FALLBACK = "fallback"
    ESCALATE = "escalate"


class ApiUsage(StrEnum):
    ALLOWED = "allowed"
    FORBIDDEN = "forbidden"


class ExecutionMode(StrEnum):
    SUBSCRIPTION_ASSISTED = "subscription_assisted"
    SUBSCRIPTION_RUNTIME = "subscription_runtime"
    LOCAL_RUNTIME = "local_runtime"
    API = "api"
    DETERMINISTIC = "deterministic"


class GateKind(StrEnum):
    TECHNICAL = "technical"
    CONTRACT = "contract"
    ARCHITECTURE = "architecture"
    GOAL = "goal"


@dataclass(frozen=True)
class DefinitionOfDoneCriterion:
    description: str
    verifiable_by: GateKind
    required: bool = True


@dataclass(frozen=True)
class TaskNode:
    node_id: str
    title: str
    dependencies: tuple[str, ...] = ()
    required: bool = True
    state: TaskNodeState = TaskNodeState.READY


@dataclass(frozen=True)
class TaskGraph:
    nodes: tuple[TaskNode, ...]

    def __post_init__(self) -> None:
        self.validate()

    @property
    def node_ids(self) -> frozenset[str]:
        return frozenset(node.node_id for node in self.nodes)

    def validate(self) -> None:
        if not self.nodes:
            raise PlanValidationError("task graph must contain at least one node")
        ids = [node.node_id for node in self.nodes]
        if any(not node_id for node_id in ids):
            raise PlanValidationError("task node IDs must be non-empty")
        if len(ids) != len(set(ids)):
            raise PlanValidationError("task node IDs must be unique")

        known = set(ids)
        missing = sorted(
            dependency
            for node in self.nodes
            for dependency in node.dependencies
            if dependency not in known
        )
        if missing:
            raise PlanValidationError(f"task graph has missing dependencies: {', '.join(missing)}")

        ordered_index = {node.node_id: index for index, node in enumerate(self.nodes)}
        for node in self.nodes:
            for dependency in node.dependencies:
                if ordered_index[dependency] >= ordered_index[node.node_id]:
                    raise PlanValidationError(
                        f"dependency {dependency!r} must appear before {node.node_id!r}"
                    )

        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {node.node_id: node.dependencies for node in self.nodes}

        def visit(node_id: str) -> None:
            if node_id in visiting:
                raise PlanValidationError("task graph contains a cycle")
            if node_id in visited:
                return
            visiting.add(node_id)
            for dependency in dependencies[node_id]:
                visit(dependency)
            visiting.remove(node_id)
            visited.add(node_id)

        for node_id in ids:
            visit(node_id)

    def ready_nodes(self) -> tuple[TaskNode, ...]:
        complete = {
            node.node_id for node in self.nodes if node.state is TaskNodeState.COMPLETED
        }
        return tuple(
            node
            for node in self.nodes
            if node.state in {TaskNodeState.READY, TaskNodeState.RETRYABLE}
            and all(dependency in complete for dependency in node.dependencies)
        )

    def replace_node(self, node_id: str, **changes: object) -> TaskGraph:
        if node_id not in self.node_ids:
            raise PlanValidationError(f"unknown task node: {node_id}")
        return TaskGraph(
            tuple(
                replace(node, **changes) if node.node_id == node_id else node
                for node in self.nodes
            )
        )


@dataclass(frozen=True)
class VerificationGate:
    kind: GateKind
    required: bool = True
    node_ids: tuple[str, ...] = ()
    ai_review_required: bool = False

    def applies_to(self, node: TaskNode) -> bool:
        return not self.node_ids or node.node_id in self.node_ids


@dataclass(frozen=True)
class VerificationPlan:
    gates: tuple[VerificationGate, ...]


@dataclass(frozen=True)
class AgentSelection:
    execution_mode: ExecutionMode
    resource: str
    runtime: str
    model: str | None = None
    agent: str | None = None


@dataclass(frozen=True)
class RoleExecutionPolicy:
    primary: AgentSelection
    fallback: AgentSelection | None = None


@dataclass(frozen=True)
class ExecutionPolicy:
    roles: Mapping[str, RoleExecutionPolicy]
    routing_strategy: str = "priority"


@dataclass(frozen=True)
class AITokenBudget:
    max_planner_calls: int
    max_implementation_retries: int
    max_architecture_review_calls_per_node: int
    max_architecture_review_calls_per_goal: int
    api_usage: ApiUsage = ApiUsage.FORBIDDEN
    exhaustion_behavior: ExhaustionBehavior = ExhaustionBehavior.ESCALATE

    def __post_init__(self) -> None:
        for name in (
            "max_planner_calls",
            "max_implementation_retries",
            "max_architecture_review_calls_per_node",
            "max_architecture_review_calls_per_goal",
        ):
            if getattr(self, name) < 0:
                raise PlanValidationError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ResourceStrategy:
    input_channel: str
    execution_policy: ExecutionPolicy
    budget: AITokenBudget


@dataclass(frozen=True)
class GoalExecutionPlan:
    goal_id: str
    objective: str
    approved_scope: tuple[str, ...]
    non_goals: tuple[str, ...]
    definition_of_done: tuple[DefinitionOfDoneCriterion, ...]
    task_graph: TaskGraph
    verification_plan: VerificationPlan
    execution_policy: ExecutionPolicy
    resource_strategy: ResourceStrategy
    budget: AITokenBudget
    exit_conditions: tuple[str, ...]
    escalation_conditions: tuple[str, ...]

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not self.goal_id:
            raise PlanValidationError("goal_id is required")
        if not self.objective:
            raise PlanValidationError("objective is required")
        if not self.approved_scope:
            raise PlanValidationError("approved_scope is required")
        if not self.non_goals:
            raise PlanValidationError("non_goals is required")
        if not self.definition_of_done:
            raise PlanValidationError("definition_of_done is required")
        if self.resource_strategy.budget != self.budget:
            raise PlanValidationError("resource_strategy budget must match plan budget")

        unknown_gate_nodes = sorted(
            node_id
            for gate in self.verification_plan.gates
            for node_id in gate.node_ids
            if node_id not in self.task_graph.node_ids
        )
        if unknown_gate_nodes:
            raise PlanValidationError(
                f"verification plan references unknown nodes: {', '.join(unknown_gate_nodes)}"
            )

        flagged_by_node: dict[str, int] = defaultdict(int)
        for gate in self.verification_plan.gates:
            if gate.kind is GateKind.ARCHITECTURE and gate.ai_review_required:
                target_ids = gate.node_ids or tuple(node.node_id for node in self.task_graph.nodes)
                for node_id in target_ids:
                    flagged_by_node[node_id] += 1
        if flagged_by_node:
            if self.budget.max_architecture_review_calls_per_node < 1:
                raise PlanValidationError(
                    "architecture review is required but per-node budget is 0"
                )
            if self.budget.max_architecture_review_calls_per_goal < len(flagged_by_node):
                raise PlanValidationError("architecture review plan exceeds per-goal budget")


@dataclass(frozen=True)
class ExecutionRequest:
    goal_id: str
    node_id: str
    role: str = "implementer"


@dataclass(frozen=True)
class ExecutionResult:
    node_id: str
    success: bool
    evidence: str


@dataclass(frozen=True)
class VerificationRequest:
    goal_id: str
    node_id: str
    gate: GateKind


@dataclass(frozen=True)
class VerificationEvidence:
    node_id: str
    gate: GateKind
    outcome: VerificationOutcome
    evidence: str
    unresolved_findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class BudgetConsumption:
    implementation_retries_by_node: Mapping[str, int] = field(default_factory=dict)
    architecture_review_calls_by_node: Mapping[str, int] = field(default_factory=dict)
    planner_calls: int = 0

    @property
    def architecture_review_calls_total(self) -> int:
        return sum(self.architecture_review_calls_by_node.values())

    def consume_implementation_retry(
        self, node_id: str, budget: AITokenBudget
    ) -> BudgetConsumption:
        next_count = self.implementation_retries_by_node.get(node_id, 0) + 1
        if next_count > budget.max_implementation_retries:
            raise BudgetExhaustedError(f"implementation retry budget exhausted for {node_id}")
        updated = dict(self.implementation_retries_by_node)
        updated[node_id] = next_count
        return replace(self, implementation_retries_by_node=updated)

    def consume_architecture_review_call(
        self, node_id: str, budget: AITokenBudget
    ) -> BudgetConsumption:
        next_node_count = self.architecture_review_calls_by_node.get(node_id, 0) + 1
        if next_node_count > budget.max_architecture_review_calls_per_node:
            raise BudgetExhaustedError(f"architecture review budget exhausted for {node_id}")
        if self.architecture_review_calls_total + 1 > budget.max_architecture_review_calls_per_goal:
            raise BudgetExhaustedError("architecture review budget exhausted for goal")
        updated = dict(self.architecture_review_calls_by_node)
        updated[node_id] = next_node_count
        return replace(self, architecture_review_calls_by_node=updated)


@dataclass(frozen=True)
class CompletionSnapshot:
    goal_id: str
    completed_tasks: tuple[str, ...]
    verification_evidence: tuple[VerificationEvidence, ...]
    budget_consumption: BudgetConsumption
    unresolved_findings: tuple[str, ...]
    final_state: GoalState
    reason: str


@dataclass(frozen=True)
class GoalRun:
    plan: GoalExecutionPlan
    state: GoalState = GoalState.GOAL_APPROVED
    graph: TaskGraph | None = None
    pending_execution_request: ExecutionRequest | None = None
    pending_verification_requests: tuple[VerificationRequest, ...] = ()
    evidence: tuple[VerificationEvidence, ...] = ()
    budget_consumption: BudgetConsumption = field(default_factory=BudgetConsumption)
    reason: str = ""

    def start(self) -> GoalRun:
        if self.state is not GoalState.GOAL_APPROVED:
            raise IllegalGoalTransitionError("approved Goal plan can only start from GOAL_APPROVED")
        return replace(self, state=GoalState.EXECUTING, graph=self.plan.task_graph)

    def request_next_execution(self) -> GoalRun:
        if self.state not in {GoalState.EXECUTING, GoalState.REVISING}:
            raise IllegalGoalTransitionError("execution requests require EXECUTING or REVISING")
        graph = self._graph()
        ready = graph.ready_nodes()
        if not ready:
            if all(
                (not node.required) or node.state is TaskNodeState.COMPLETED
                for node in graph.nodes
            ):
                return replace(
                    self,
                    state=GoalState.REVIEW_REQUESTED,
                    pending_execution_request=None,
                    reason="all required task nodes passed verification",
                )
            return replace(
                self, state=GoalState.ESCALATED, reason="no ready task node is available"
            )
        node = ready[0]
        return replace(
            self,
            state=GoalState.EXECUTING,
            graph=graph.replace_node(node.node_id, state=TaskNodeState.RUNNING),
            pending_execution_request=ExecutionRequest(self.plan.goal_id, node.node_id),
        )

    def record_execution_result(self, result: ExecutionResult) -> GoalRun:
        graph = self._graph()
        if self.state is not GoalState.EXECUTING:
            raise IllegalGoalTransitionError("execution results require EXECUTING")
        if self.pending_execution_request is None:
            raise IllegalGoalTransitionError("no execution request is pending")
        if result.node_id != self.pending_execution_request.node_id:
            raise IllegalGoalTransitionError("execution result node does not match pending request")
        if not result.success:
            return replace(
                self,
                state=GoalState.FAILED,
                graph=graph.replace_node(result.node_id, state=TaskNodeState.FAILED),
                pending_execution_request=None,
                reason=result.evidence,
            )

        requests = tuple(
            VerificationRequest(self.plan.goal_id, result.node_id, gate.kind)
            for gate in self.plan.verification_plan.gates
            if gate.applies_to(self._node(result.node_id, graph)) and gate.required
        )
        if not requests:
            return replace(
                self,
                state=GoalState.EXECUTING,
                graph=graph.replace_node(result.node_id, state=TaskNodeState.COMPLETED),
                pending_execution_request=None,
                pending_verification_requests=(),
            )
        return replace(
            self,
            state=GoalState.VERIFYING,
            graph=graph.replace_node(result.node_id, state=TaskNodeState.VERIFYING),
            pending_execution_request=None,
            pending_verification_requests=requests,
        )

    def record_verification_outcome(self, evidence: VerificationEvidence) -> GoalRun:
        if self.state is not GoalState.VERIFYING:
            raise IllegalGoalTransitionError("verification outcomes require VERIFYING")
        pending = list(self.pending_verification_requests)
        request = VerificationRequest(self.plan.goal_id, evidence.node_id, evidence.gate)
        if request not in pending:
            raise IllegalGoalTransitionError(
                "verification outcome does not match a pending request"
            )

        consumption = self.budget_consumption
        if evidence.gate is GateKind.ARCHITECTURE:
            consumption = consumption.consume_architecture_review_call(
                evidence.node_id, self.plan.budget
            )

        graph = self._graph()
        all_evidence = self.evidence + (evidence,)
        pending.remove(request)

        if evidence.outcome is VerificationOutcome.PASS:
            if pending:
                return replace(
                    self,
                    pending_verification_requests=tuple(pending),
                    evidence=all_evidence,
                    budget_consumption=consumption,
                )
            return replace(
                self,
                state=GoalState.EXECUTING,
                graph=graph.replace_node(evidence.node_id, state=TaskNodeState.COMPLETED),
                pending_verification_requests=(),
                evidence=all_evidence,
                budget_consumption=consumption,
            )

        if evidence.outcome is VerificationOutcome.RETRY:
            consumption = consumption.consume_implementation_retry(
                evidence.node_id, self.plan.budget
            )
            return replace(
                self,
                state=GoalState.REVISING,
                graph=graph.replace_node(evidence.node_id, state=TaskNodeState.RETRYABLE),
                pending_verification_requests=(),
                evidence=all_evidence,
                budget_consumption=consumption,
                reason=evidence.evidence,
            )

        if evidence.outcome is VerificationOutcome.FAIL:
            return replace(
                self,
                state=GoalState.FAILED,
                graph=graph.replace_node(evidence.node_id, state=TaskNodeState.FAILED),
                pending_verification_requests=(),
                evidence=all_evidence,
                budget_consumption=consumption,
                reason=evidence.evidence,
            )

        return replace(
            self,
            state=GoalState.ESCALATED,
            graph=graph.replace_node(evidence.node_id, state=TaskNodeState.ESCALATED),
            pending_verification_requests=(),
            evidence=all_evidence,
            budget_consumption=consumption,
            reason=evidence.evidence,
        )

    def completion_snapshot(self) -> CompletionSnapshot:
        graph = self._graph()
        unresolved = tuple(
            finding for item in self.evidence for finding in item.unresolved_findings
        )
        if self.state is GoalState.REVIEW_REQUESTED:
            reason = self.reason or "Goal is ready for final audit"
        elif self.state in {GoalState.ESCALATED, GoalState.FAILED}:
            reason = self.reason
        else:
            reason = f"Goal is not complete: {self.state.value}"
        return CompletionSnapshot(
            goal_id=self.plan.goal_id,
            completed_tasks=tuple(
                node.node_id for node in graph.nodes if node.state is TaskNodeState.COMPLETED
            ),
            verification_evidence=self.evidence,
            budget_consumption=self.budget_consumption,
            unresolved_findings=unresolved,
            final_state=self.state,
            reason=reason,
        )

    def _graph(self) -> TaskGraph:
        if self.graph is None:
            raise IllegalGoalTransitionError("Goal execution has not started")
        return self.graph

    @staticmethod
    def _node(node_id: str, graph: TaskGraph) -> TaskNode:
        for node in graph.nodes:
            if node.node_id == node_id:
                return node
        raise PlanValidationError(f"unknown task node: {node_id}")


def run_approved_goal_plan(plan: GoalExecutionPlan) -> GoalRun:
    """Create a run for an approved plan and emit its first execution request."""
    return GoalRun(plan=plan).start().request_next_execution()
