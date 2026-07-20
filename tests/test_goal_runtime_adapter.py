from __future__ import annotations

import json
import time
from collections.abc import Sequence
from pathlib import Path

import pytest

from devbot.agents.base import AgentRunResult
from devbot.github_client import GitHubIssue
from devbot.goal_execution_foundation import (
    AgentSelection,
    AITokenBudget,
    ApiUsage,
    DefinitionOfDoneCriterion,
    ExecutionMode,
    ExecutionPolicy,
    ExecutionRequest,
    ExecutionResult,
    GateKind,
    GoalExecutionPlan,
    ResourceStrategy,
    RoleExecutionPolicy,
    TaskGraph,
    TaskNode,
    VerificationEvidence,
    VerificationGate,
    VerificationOutcome,
    VerificationPlan,
    VerificationRequest,
)
from devbot.goal_runtime_adapter import (
    APPROVED_GOAL_PLAN_SCHEMA_VERSION,
    ApprovedGoalPlanDocument,
    CorruptGoalStateError,
    DevBotGoalExecutionAdapter,
    DevBotGoalVerificationAdapter,
    ExistingDevBotExecutionAdapter,
    ExistingDevBotVerificationAdapter,
    GoalExecutionAdapter,
    GoalRuntimeAdapterError,
    GoalTaskBinding,
    GoalVerificationAdapter,
    UnsupportedGoalSchemaError,
    load_approved_goal_plan,
    load_goal_runtime_state,
    process_pending_verification,
    render_goal_runtime_status,
    resume_goal_runtime,
    start_goal_runtime,
    submit_pending_execution,
    submit_pending_executions,
    write_approved_goal_plan,
)
from devbot.models import RepositoryConfig
from devbot.runtime_scheduler import RuntimeScheduler
from devbot.validation import CommandExecution
from devbot.worktree import PreparedWorkspace


def _plan(*, gates: tuple[VerificationGate, ...] | None = None) -> GoalExecutionPlan:
    budget = AITokenBudget(
        max_planner_calls=0,
        max_implementation_retries=1,
        max_architecture_review_calls_per_node=1,
        max_architecture_review_calls_per_goal=1,
        api_usage=ApiUsage.FORBIDDEN,
    )
    policy = ExecutionPolicy(
        roles={
            "implementer": RoleExecutionPolicy(
                primary=AgentSelection(
                    execution_mode=ExecutionMode.SUBSCRIPTION_RUNTIME,
                    resource="subscription",
                    runtime="cli",
                )
            )
        }
    )
    return GoalExecutionPlan(
        goal_id="goal-141",
        objective="Run an approved plan.",
        approved_scope=("runtime adapter",),
        non_goals=("planning",),
        definition_of_done=(
            DefinitionOfDoneCriterion("required nodes complete", GateKind.GOAL),
        ),
        task_graph=TaskGraph(
            (
                TaskNode("a", "First"),
                TaskNode("b", "Second", dependencies=("a",)),
            )
        ),
        verification_plan=VerificationPlan(
            gates if gates is not None else (VerificationGate(GateKind.TECHNICAL),)
        ),
        execution_policy=policy,
        resource_strategy=ResourceStrategy(
            input_channel="chatgpt",
            execution_policy=policy,
            budget=budget,
        ),
        budget=budget,
        exit_conditions=("review requested",),
        escalation_conditions=("manual action",),
    )


def _document(repository: str = "someone/repo") -> ApprovedGoalPlanDocument:
    return ApprovedGoalPlanDocument(
        schema_version=APPROVED_GOAL_PLAN_SCHEMA_VERSION,
        plan=_plan(),
        task_bindings=(
            GoalTaskBinding("a", repository, 141, "tasks/141-a.md", "task/141-a"),
            GoalTaskBinding("b", repository, 142, "tasks/142-b.md", "task/142-b"),
        ),
    )


class _ExecutionAdapter(GoalExecutionAdapter):
    def __init__(self) -> None:
        self.requests: list[ExecutionRequest] = []

    def execute(self, request: ExecutionRequest, binding: GoalTaskBinding) -> ExecutionResult:
        self.requests.append(request)
        assert binding.repository == "someone/repo"
        return ExecutionResult(request.node_id, True, "implemented")


class _VerificationAdapter(GoalVerificationAdapter):
    def __init__(self) -> None:
        self.requests: list[VerificationRequest] = []

    def verify(
        self, request: VerificationRequest, binding: GoalTaskBinding
    ) -> VerificationEvidence:
        self.requests.append(request)
        assert binding.issue_number in {141, 142}
        return VerificationEvidence(
            node_id=request.node_id,
            gate=request.gate,
            outcome=VerificationOutcome.PASS,
            evidence="validated",
        )


def test_approved_plan_document_round_trips_and_validates(tmp_path: Path) -> None:
    path = tmp_path / "goal-plan.json"

    write_approved_goal_plan(_document(), path)
    loaded = load_approved_goal_plan(path)

    assert loaded.schema_version == APPROVED_GOAL_PLAN_SCHEMA_VERSION
    assert loaded.plan.goal_id == "goal-141"
    assert loaded.binding_for("a").issue_number == 141


def test_duplicate_task_bindings_are_rejected() -> None:
    document = ApprovedGoalPlanDocument(
        schema_version=APPROVED_GOAL_PLAN_SCHEMA_VERSION,
        plan=_plan(),
        task_bindings=(
            GoalTaskBinding("a", "someone/repo", 141, "tasks/141-a.md", "task/141-a"),
            GoalTaskBinding("a", "someone/repo", 999, "tasks/999-a.md", "task/999-a"),
            GoalTaskBinding("b", "someone/repo", 142, "tasks/142-b.md", "task/142-b"),
        ),
    )

    with pytest.raises(GoalRuntimeAdapterError, match="duplicate task bindings"):
        document.validate()


def test_unsupported_plan_schema_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "goal-plan.json"
    write_approved_goal_plan(_document(), path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["schema_version"] = 999
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(UnsupportedGoalSchemaError):
        load_approved_goal_plan(path)


def test_corrupt_runtime_state_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(CorruptGoalStateError):
        load_goal_runtime_state(path)


def test_start_status_resume_and_successful_completion(tmp_path: Path) -> None:
    document = _document()
    state_path = tmp_path / "state.json"
    execution = _ExecutionAdapter()
    verification = _VerificationAdapter()

    run = start_goal_runtime(document, state_path)
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.node_id == "a"

    resumed = resume_goal_runtime(state_path)
    assert resumed.pending_execution_request == run.pending_execution_request

    scheduler = RuntimeScheduler(worker_count=1, ai_concurrency=1)
    run = submit_pending_execution(
        plan_document=document,
        state_path=state_path,
        scheduler=scheduler,
        adapter=execution,
    )
    assert run.pending_verification_requests == (
        VerificationRequest("goal-141", "a", GateKind.TECHNICAL),
    )

    run = process_pending_verification(
        plan_document=document,
        state_path=state_path,
        adapter=verification,
    )
    assert run.pending_execution_request is not None
    assert run.pending_execution_request.node_id == "b"

    submit_pending_execution(
        plan_document=document,
        state_path=state_path,
        scheduler=scheduler,
        adapter=execution,
    )
    run = process_pending_verification(
        plan_document=document,
        state_path=state_path,
        adapter=verification,
    )

    state = load_goal_runtime_state(state_path)
    assert run.state.value == "REVIEW_REQUESTED"
    assert state.completion_snapshot is not None
    assert state.completion_snapshot.completed_tasks == ("a", "b")
    assert "state: REVIEW_REQUESTED" in render_goal_runtime_status(state)


def test_in_flight_execution_marker_fails_closed_on_resume(tmp_path: Path) -> None:
    document = _document()
    state_path = tmp_path / "state.json"
    start_goal_runtime(document, state_path)

    class CrashingAdapter(GoalExecutionAdapter):
        def execute(
            self, request: ExecutionRequest, binding: GoalTaskBinding
        ) -> ExecutionResult:
            state = load_goal_runtime_state(state_path)
            assert state.in_flight_side_effect is not None
            raise SystemExit("crash after side effect marker")

    with pytest.raises(SystemExit):
        submit_pending_execution(
            plan_document=document,
            state_path=state_path,
            scheduler=RuntimeScheduler(worker_count=1, ai_concurrency=1),
            adapter=CrashingAdapter(),
        )

    resumed = resume_goal_runtime(state_path)

    assert resumed.state.value == "ESCALATED"
    assert "ambiguous in-flight side effect" in resumed.reason
    state = load_goal_runtime_state(state_path)
    assert state.in_flight_side_effect is not None
    assert state.completion_snapshot is not None


def test_in_flight_verification_marker_fails_closed_on_resume(tmp_path: Path) -> None:
    document = _document()
    state_path = tmp_path / "state.json"
    start_goal_runtime(document, state_path)
    submit_pending_execution(
        plan_document=document,
        state_path=state_path,
        scheduler=RuntimeScheduler(worker_count=1, ai_concurrency=1),
        adapter=_ExecutionAdapter(),
    )

    class CrashingVerificationAdapter(GoalVerificationAdapter):
        def verify(
            self, request: VerificationRequest, binding: GoalTaskBinding
        ) -> VerificationEvidence:
            state = load_goal_runtime_state(state_path)
            assert state.in_flight_side_effect is not None
            assert state.in_flight_side_effect.kind == "verification"
            raise SystemExit("crash after verification side effect marker")

    with pytest.raises(SystemExit):
        process_pending_verification(
            plan_document=document,
            state_path=state_path,
            adapter=CrashingVerificationAdapter(),
        )

    resumed = resume_goal_runtime(state_path)

    assert resumed.state.value == "ESCALATED"
    assert "ambiguous in-flight side effect" in resumed.reason
    state = load_goal_runtime_state(state_path)
    assert state.in_flight_side_effect is not None
    assert state.completion_snapshot is not None


def test_devbot_goal_execution_adapter_reaches_worktree_and_agent_seams(tmp_path: Path) -> None:
    repository = RepositoryConfig(
        owner="someone",
        repo="repo",
        enabled=True,
        local_path=tmp_path / "repo",
    )
    issue = GitHubIssue(
        repository=repository.full_name,
        number=141,
        title="Task 141",
        body="body",
        state="open",
        labels=(),
        created_at=__import__("datetime").datetime(2026, 1, 1),
    )
    binding = GoalTaskBinding("a", repository.full_name, 141, "tasks/141-a.md", "task/141-a")
    request = ExecutionRequest("goal-141", "a")
    calls: list[str] = []

    class FakeGitHubClient:
        def get_issue(
            self, received_repository: RepositoryConfig, issue_number: int
        ) -> GitHubIssue:
            assert received_repository == repository
            assert issue_number == 141
            calls.append("issue")
            return issue

        def list_pull_requests(self, received_repository: RepositoryConfig, *, state: str) -> list:
            assert received_repository == repository
            assert state == "open"
            calls.append("prs")
            return []

    class FakeWorktreeManager:
        def prepare(
            self,
            received_repository: RepositoryConfig,
            received_issue: GitHubIssue,
            linked_pull_request: object,
        ) -> PreparedWorkspace:
            assert received_repository == repository
            assert received_issue == issue
            assert linked_pull_request is None
            calls.append("worktree")
            worktree = tmp_path / "worktree"
            contract_path = worktree / binding.contract_path
            contract_path.parent.mkdir(parents=True)
            contract_path.write_text(
                "# Task 141: Task 141\n\n## Goal\n\nLoad this Contract before dispatch.\n",
                encoding="utf-8",
            )
            return PreparedWorkspace(
                repository=received_repository,
                branch="task/141-a",
                base_branch="main",
                issue_number=141,
                pull_request=None,
                worktree_path=worktree,
                reused=False,
                contract_path=binding.contract_path,
            )

    class FakeRunner:
        def run_context(self, context: object, prompt: str) -> AgentRunResult:
            assert "Task 141" in prompt
            assert "Load this Contract before dispatch" in prompt
            assert getattr(context, "canonical_branch") == "task/141-a"
            calls.append("agent")
            return AgentRunResult(executed=True, dry_run=False, message="done", returncode=0)

    def parse_contract(contract_text: str) -> object:
        assert "Load this Contract before dispatch" in contract_text
        calls.append("contract")
        return object()

    adapter = DevBotGoalExecutionAdapter(
        repositories=(repository,),
        github_client=FakeGitHubClient(),  # type: ignore[arg-type]
        worktree_manager=FakeWorktreeManager(),  # type: ignore[arg-type]
        agent_runner=FakeRunner(),  # type: ignore[arg-type]
        parse_contract=parse_contract,
    )

    result = adapter.execute(request, binding)

    assert result.success is True
    assert result.evidence == "done"
    assert calls == ["issue", "prs", "worktree", "contract", "agent"]


def test_devbot_goal_verification_adapter_reaches_validation_seam(tmp_path: Path) -> None:
    repository = RepositoryConfig(
        owner="someone",
        repo="repo",
        enabled=True,
        local_path=tmp_path / "repo",
    )
    binding = GoalTaskBinding("a", repository.full_name, 141, "tasks/141-a.md", "task/141-a")
    commands: list[tuple[str, ...]] = []

    def run_command(
        received_repository: RepositoryConfig, command: Sequence[str]
    ) -> CommandExecution:
        assert received_repository == repository
        commands.append(tuple(command))
        return CommandExecution(tuple(command), 0, "ok")

    adapter = DevBotGoalVerificationAdapter(
        repositories=(repository,),
        run_command=run_command,
    )

    evidence = adapter.verify(VerificationRequest("goal-141", "a", GateKind.TECHNICAL), binding)

    assert evidence.outcome is VerificationOutcome.PASS
    assert commands[0] == ("uv", "sync")
    assert ("uv", "run", "pytest") in commands


def test_existing_devbot_execution_adapter_calls_workspace_and_agent_seams() -> None:
    calls: list[str] = []
    binding = GoalTaskBinding("a", "someone/repo", 141, "tasks/141-a.md", "task/141-a")
    request = ExecutionRequest("goal-141", "a")

    def prepare(
        received_request: ExecutionRequest, received_binding: GoalTaskBinding
    ) -> str:
        assert received_request == request
        assert received_binding == binding
        calls.append("prepare")
        return "prepared-workspace"

    def dispatch(
        context: object, received_request: ExecutionRequest, received_binding: GoalTaskBinding
    ) -> ExecutionResult:
        assert context == "prepared-workspace"
        assert received_request == request
        assert received_binding == binding
        calls.append("agent")
        return ExecutionResult("a", True, "agent completed")

    adapter = ExistingDevBotExecutionAdapter(
        prepare_execution_context=prepare,
        dispatch_agent=dispatch,
    )

    assert adapter.execute(request, binding).evidence == "agent completed"
    assert calls == ["prepare", "agent"]


def test_existing_devbot_verification_adapter_routes_validation_and_review_seams() -> None:
    calls: list[str] = []
    binding = GoalTaskBinding("a", "someone/repo", 141, "tasks/141-a.md", "task/141-a")

    def validation(
        request: VerificationRequest, received_binding: GoalTaskBinding
    ) -> VerificationEvidence:
        assert received_binding == binding
        calls.append(f"validation:{request.gate.value}")
        return VerificationEvidence(
            request.node_id, request.gate, VerificationOutcome.PASS, "validated"
        )

    def review(
        request: VerificationRequest, received_binding: GoalTaskBinding
    ) -> VerificationEvidence:
        assert received_binding == binding
        calls.append("review")
        return VerificationEvidence(
            request.node_id, request.gate, VerificationOutcome.PASS, "reviewed"
        )

    adapter = ExistingDevBotVerificationAdapter(
        run_validation_gate=validation,
        run_review_gate=review,
    )

    technical = adapter.verify(
        VerificationRequest("goal-141", "a", GateKind.TECHNICAL), binding
    )
    architecture = adapter.verify(
        VerificationRequest("goal-141", "a", GateKind.ARCHITECTURE), binding
    )

    assert technical.evidence == "validated"
    assert architecture.evidence == "reviewed"
    assert calls == ["validation:technical", "review"]


@pytest.mark.parametrize(
    ("left_repo", "right_repo", "expected_max_running"),
    [
        ("someone/repo", "someone/repo", 1),
        ("someone/repo-a", "someone/repo-b", 2),
    ],
)
def test_goal_execution_batch_uses_runtime_scheduler_repository_limits(
    tmp_path: Path,
    left_repo: str,
    right_repo: str,
    expected_max_running: int,
) -> None:
    left_document = _document(left_repo)
    right_document = _document(right_repo)
    state_a = tmp_path / "a.json"
    state_b = tmp_path / "b.json"
    start_goal_runtime(left_document, state_a)
    start_goal_runtime(right_document, state_b)
    scheduler = RuntimeScheduler(worker_count=2, ai_concurrency=2)
    running = 0
    max_running = 0
    lock = __import__("threading").Lock()

    class SlowAdapter(GoalExecutionAdapter):
        def execute(
            self, request: ExecutionRequest, binding: GoalTaskBinding
        ) -> ExecutionResult:
            nonlocal running, max_running
            with lock:
                running += 1
                max_running = max(max_running, running)
            time.sleep(0.02)
            with lock:
                running -= 1
            return ExecutionResult(request.node_id, True, "implemented")

    adapter = SlowAdapter()
    submit_pending_executions(
        plan_documents=(left_document, right_document),
        state_paths=(state_a, state_b),
        scheduler=scheduler,
        adapter=adapter,
    )

    assert max_running == expected_max_running
