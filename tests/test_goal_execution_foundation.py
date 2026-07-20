from __future__ import annotations

import pytest

from devbot.goal_execution_foundation import (
    AgentSelection,
    AITokenBudget,
    ApiUsage,
    DefinitionOfDoneCriterion,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionResult,
    ExhaustionBehavior,
    GateKind,
    GoalExecutionPlan,
    GoalRun,
    GoalState,
    IllegalGoalTransitionError,
    PlanValidationError,
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
    run_approved_goal_plan,
)


def _budget(**overrides: object) -> AITokenBudget:
    defaults: dict[str, object] = dict(
        max_planner_calls=0,
        max_implementation_retries=1,
        max_architecture_review_calls_per_node=1,
        max_architecture_review_calls_per_goal=2,
        api_usage=ApiUsage.FORBIDDEN,
    )
    defaults.update(overrides)
    return AITokenBudget(**defaults)  # type: ignore[arg-type]


def _execution_policy() -> ExecutionPolicy:
    return ExecutionPolicy(
        roles={
            "implementer": RoleExecutionPolicy(
                primary=AgentSelection(
                    execution_mode=ExecutionMode.SUBSCRIPTION_RUNTIME,
                    resource="codex-subscription",
                    runtime="codex-cli",
                    agent="codex",
                )
            ),
            "reviewer": RoleExecutionPolicy(
                primary=AgentSelection(
                    execution_mode=ExecutionMode.DETERMINISTIC,
                    resource="local",
                    runtime="pytest",
                    agent="validator",
                )
            ),
        }
    )


def _plan(
    *,
    graph: TaskGraph | None = None,
    verification_plan: VerificationPlan | None = None,
    budget: AITokenBudget | None = None,
) -> GoalExecutionPlan:
    resolved_budget = budget or _budget()
    policy = _execution_policy()
    resource_strategy = ResourceStrategy(
        input_channel="chatgpt",
        execution_policy=policy,
        budget=resolved_budget,
    )
    return GoalExecutionPlan(
        goal_id="goal-118",
        objective="Execute an approved goal plan.",
        approved_scope=("domain execution foundation",),
        non_goals=("runtime adapters",),
        definition_of_done=(
            DefinitionOfDoneCriterion("all required nodes pass", GateKind.GOAL),
        ),
        task_graph=graph
        or TaskGraph(
            (
                TaskNode("a", "First task"),
                TaskNode("b", "Second task", dependencies=("a",)),
            )
        ),
        verification_plan=verification_plan
        or VerificationPlan((VerificationGate(GateKind.TECHNICAL),)),
        execution_policy=policy,
        resource_strategy=resource_strategy,
        budget=resolved_budget,
        exit_conditions=("review requested",),
        escalation_conditions=("budget exhausted",),
    )


def _pass(node_id: str, gate: GateKind = GateKind.TECHNICAL) -> VerificationEvidence:
    return VerificationEvidence(
        node_id=node_id,
        gate=gate,
        outcome=VerificationOutcome.PASS,
        evidence=f"{gate.value} passed",
    )


def test_approved_plan_runs_dependent_tasks_to_review_requested() -> None:
    run = run_approved_goal_plan(_plan())

    assert run.state is GoalState.EXECUTING
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.node_id == "a"

    run = run.record_execution_result(ExecutionResult("a", True, "implemented a"))
    assert run.state is GoalState.VERIFYING
    run = run.record_verification_outcome(_pass("a"))

    run = run.request_next_execution()
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.node_id == "b"

    run = run.record_execution_result(ExecutionResult("b", True, "implemented b"))
    run = run.record_verification_outcome(_pass("b"))
    run = run.request_next_execution()

    snapshot = run.completion_snapshot()
    assert run.state is GoalState.REVIEW_REQUESTED
    assert snapshot.completed_tasks == ("a", "b")
    assert snapshot.final_state is GoalState.REVIEW_REQUESTED
    assert snapshot.reason == "all required task nodes passed verification"


@pytest.mark.parametrize(
    "verification_plan",
    [
        VerificationPlan(()),
        VerificationPlan((VerificationGate(GateKind.TECHNICAL, required=False),)),
        VerificationPlan((VerificationGate(GateKind.TECHNICAL, node_ids=("b",)),)),
    ],
)
def test_task_with_no_required_verification_requests_completes_without_stalling(
    verification_plan: VerificationPlan,
) -> None:
    run = run_approved_goal_plan(
        _plan(
            graph=TaskGraph(
                (
                    TaskNode("a", "First task"),
                    TaskNode("b", "Second task", dependencies=("a",)),
                )
            ),
            verification_plan=verification_plan,
        )
    )

    run = run.record_execution_result(ExecutionResult("a", True, "implemented a"))

    assert run.state is GoalState.EXECUTING
    assert run.pending_execution_request is None
    assert run.pending_verification_requests == ()
    assert run.graph is not None
    assert run.graph.nodes[0].state is TaskNodeState.COMPLETED

    run = run.request_next_execution()
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.node_id == "b"


def test_task_graph_ready_nodes_are_stable_and_dependency_aware() -> None:
    graph = TaskGraph(
        (
            TaskNode("setup", "Setup"),
            TaskNode("docs", "Docs"),
            TaskNode("integration", "Integration", dependencies=("setup", "docs")),
        )
    )

    assert tuple(node.node_id for node in graph.ready_nodes()) == ("setup", "docs")

    graph = graph.replace_node("docs", state=TaskNodeState.COMPLETED)
    assert tuple(node.node_id for node in graph.ready_nodes()) == ("setup",)

    graph = graph.replace_node("setup", state=TaskNodeState.COMPLETED)
    assert tuple(node.node_id for node in graph.ready_nodes()) == ("integration",)


def test_task_graph_rejects_missing_dependency() -> None:
    with pytest.raises(PlanValidationError, match="missing dependencies"):
        TaskGraph((TaskNode("b", "Broken", dependencies=("missing",)),))


def test_task_graph_accepts_valid_dag_independent_of_input_order() -> None:
    graph = TaskGraph(
        (
            TaskNode("b", "Second", dependencies=("a",)),
            TaskNode("a", "First"),
        )
    )

    assert tuple(node.node_id for node in graph.ready_nodes()) == ("a",)
    graph = graph.replace_node("a", state=TaskNodeState.COMPLETED)
    assert tuple(node.node_id for node in graph.ready_nodes()) == ("b",)


def test_illegal_transition_is_typed() -> None:
    with pytest.raises(IllegalGoalTransitionError, match="has not started"):
        GoalRun(plan=_plan()).completion_snapshot()


def test_retry_returns_node_to_retryable_and_consumes_retry_budget() -> None:
    run = run_approved_goal_plan(_plan())
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(
        VerificationEvidence(
            node_id="a",
            gate=GateKind.TECHNICAL,
            outcome=VerificationOutcome.RETRY,
            evidence="transient validation error",
        )
    )

    assert run.state is GoalState.REVISING
    assert run.budget_consumption.implementation_retries_by_node["a"] == 1
    assert run.graph is not None
    assert run.graph.nodes[0].state is TaskNodeState.RETRYABLE


@pytest.mark.parametrize(
    ("behavior", "expected_state", "expected_node_state"),
    [
        (ExhaustionBehavior.STOP, GoalState.FAILED, TaskNodeState.BLOCKED),
        (ExhaustionBehavior.ESCALATE, GoalState.ESCALATED, TaskNodeState.ESCALATED),
        (ExhaustionBehavior.FALLBACK, GoalState.EXECUTING, TaskNodeState.RUNNING),
    ],
)
def test_retry_budget_exhaustion_follows_configured_behavior(
    behavior: ExhaustionBehavior,
    expected_state: GoalState,
    expected_node_state: TaskNodeState,
) -> None:
    run = run_approved_goal_plan(
        _plan(
            budget=_budget(
                max_implementation_retries=0,
                exhaustion_behavior=behavior,
            )
        )
    )
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))

    run = run.record_verification_outcome(
        VerificationEvidence(
            node_id="a",
            gate=GateKind.TECHNICAL,
            outcome=VerificationOutcome.RETRY,
            evidence="still failing",
        )
    )

    assert run.state is expected_state
    assert "implementation retry budget exhausted for a" in run.reason
    assert run.graph is not None
    assert run.graph.nodes[0].state is expected_node_state
    if behavior is ExhaustionBehavior.FALLBACK:
        assert run.pending_execution_request is not None
        assert run.pending_execution_request.role == "fallback"


def test_fallback_request_result_can_reenter_verification() -> None:
    run = run_approved_goal_plan(
        _plan(
            budget=_budget(
                max_implementation_retries=0,
                exhaustion_behavior=ExhaustionBehavior.FALLBACK,
            )
        )
    )
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(
        VerificationEvidence(
            node_id="a",
            gate=GateKind.TECHNICAL,
            outcome=VerificationOutcome.RETRY,
            evidence="retry exhausted",
        )
    )

    assert run.state is GoalState.EXECUTING
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.role == "fallback"

    run = run.record_execution_result(ExecutionResult("a", True, "fallback fixed it"))

    assert run.state is GoalState.VERIFYING
    assert run.pending_execution_request is None
    assert run.pending_verification_requests == (
        VerificationRequest("goal-118", "a", GateKind.TECHNICAL),
    )


def test_fail_stops_goal_as_failed() -> None:
    run = run_approved_goal_plan(_plan())
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(
        VerificationEvidence(
            node_id="a",
            gate=GateKind.TECHNICAL,
            outcome=VerificationOutcome.FAIL,
            evidence="tests failed",
            unresolved_findings=("pytest failure",),
        )
    )

    snapshot = run.completion_snapshot()
    assert run.state is GoalState.FAILED
    assert snapshot.unresolved_findings == ("pytest failure",)
    assert snapshot.reason == "tests failed"


def test_escalate_stops_autonomous_progress() -> None:
    run = run_approved_goal_plan(_plan())
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(
        VerificationEvidence(
            node_id="a",
            gate=GateKind.TECHNICAL,
            outcome=VerificationOutcome.ESCALATE,
            evidence="approved scope is insufficient",
        )
    )

    assert run.state is GoalState.ESCALATED
    assert run.reason == "approved scope is insufficient"


def test_architecture_review_budget_is_consumed_and_limited() -> None:
    plan = _plan(
        graph=TaskGraph((TaskNode("a", "Review me"),)),
        verification_plan=VerificationPlan(
            (VerificationGate(GateKind.ARCHITECTURE, ai_review_required=True),)
        ),
        budget=_budget(
            max_architecture_review_calls_per_node=1,
            max_architecture_review_calls_per_goal=1,
        ),
    )
    run = run_approved_goal_plan(plan)
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(_pass("a", GateKind.ARCHITECTURE))

    assert run.budget_consumption.architecture_review_calls_by_node["a"] == 1
    assert run.budget_consumption.architecture_review_calls_total == 1


def test_architecture_review_budget_exhaustion_follows_configured_behavior() -> None:
    plan = _plan(
        graph=TaskGraph((TaskNode("a", "Review me"),)),
        verification_plan=VerificationPlan(
            (
                VerificationGate(GateKind.ARCHITECTURE, ai_review_required=True),
                VerificationGate(GateKind.ARCHITECTURE, ai_review_required=True),
            )
        ),
        budget=_budget(
            max_architecture_review_calls_per_node=1,
            max_architecture_review_calls_per_goal=2,
            exhaustion_behavior=ExhaustionBehavior.ESCALATE,
        ),
    )
    run = run_approved_goal_plan(plan)
    run = run.record_execution_result(ExecutionResult("a", True, "implemented"))
    run = run.record_verification_outcome(_pass("a", GateKind.ARCHITECTURE))

    run = run.record_verification_outcome(_pass("a", GateKind.ARCHITECTURE))

    assert run.state is GoalState.ESCALATED
    assert "architecture review budget exhausted for a" in run.reason
    assert run.graph is not None
    assert run.graph.nodes[0].state is TaskNodeState.ESCALATED


def test_architecture_review_plan_must_fit_budget() -> None:
    with pytest.raises(PlanValidationError, match="per-goal budget"):
        _plan(
            graph=TaskGraph(
                (
                    TaskNode("a", "A"),
                    TaskNode("b", "B"),
                )
            ),
            verification_plan=VerificationPlan(
                (VerificationGate(GateKind.ARCHITECTURE, ai_review_required=True),)
            ),
            budget=_budget(
                max_architecture_review_calls_per_node=1,
                max_architecture_review_calls_per_goal=1,
            ),
        )


def test_plan_rejects_named_unknown_verification_node() -> None:
    with pytest.raises(PlanValidationError, match="unknown nodes"):
        _plan(
            verification_plan=VerificationPlan(
                (VerificationGate(GateKind.TECHNICAL, node_ids=("missing",)),)
            )
        )
