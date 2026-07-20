# Task 118 Result: Approved Goal Plan Execution Foundation

## 완료 내용

- 승인된 `GoalExecutionPlan`을 입력으로 받는 provider-neutral 실행 기반을
  추가했다.
- Task Graph DAG 검증, 안정적인 ready-node 선택, Goal 상태 전이, 실행 요청,
  검증 요청과 결과 기록을 순수 도메인 로직으로 구현했다.
- PASS, RETRY, FAIL, ESCALATE 결과와 retry/architecture-review budget 소비를
  모델링하고 강제했다.
- 완료 snapshot이 완료 Task, verification evidence, budget consumption,
  unresolved findings, final state, reason을 제공한다.

## 주요 설계 결정

- 기존 `goal_planner.py`와 `goal_executor.py`는 변경하지 않았다. Issue #118은
  재계획 없는 approved-plan 실행 기반이므로, 새 모듈
  `goal_execution_foundation.py`에 순수 domain API를 두었다.
- 실제 Codex, Claude Code, API, local runtime 호출은 하지 않고
  `ExecutionRequest`와 `VerificationRequest`만 발행한다.
- provider 이름은 test fixture의 예시 resource 문자열에만 있고 core enum/type은
  `ExecutionMode`, `ResourceStrategy`, `ExecutionPolicy` 중심으로 중립성을 유지한다.

## 수정 파일

- `src/devbot/goal_execution_foundation.py`
- `tests/test_goal_execution_foundation.py`
- `tasks/118-approved-goal-plan-execution-foundation.md`
- `results/118-approved-goal-plan-execution-foundation.md`
- `docs/00-roadmap.md`

## Checkpoint Evidence

- Success path: `test_approved_plan_runs_dependent_tasks_to_review_requested`
- No required verification request path:
  `test_task_with_no_required_verification_requests_completes_without_stalling`
- Task Graph validation: `test_task_graph_rejects_missing_dependency`,
  `test_task_graph_accepts_valid_dag_independent_of_input_order`
- Stable ready ordering: `test_task_graph_ready_nodes_are_stable_and_dependency_aware`
- Illegal transition: `test_illegal_transition_is_typed`
- RETRY: `test_retry_returns_node_to_retryable_and_consumes_retry_budget`
- FAIL: `test_fail_stops_goal_as_failed`
- ESCALATE: `test_escalate_stops_autonomous_progress`
- Budget enforcement:
  `test_retry_budget_exhaustion_follows_configured_behavior`,
  `test_fallback_request_result_can_reenter_verification`,
  `test_architecture_review_budget_is_consumed_and_limited`,
  `test_architecture_review_budget_exhaustion_follows_configured_behavior`,
  `test_architecture_review_budget_fallback_retries_verification_without_ai_budget`,
  `test_architecture_review_plan_must_fit_budget`

## Validation 결과

- `uv run ruff check src/devbot/goal_execution_foundation.py tests/test_goal_execution_foundation.py`
  - PASS
- `uv run pytest tests/test_goal_execution_foundation.py`
  - PASS, 20 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1408 passed in 151.43s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 rework 변경 파일 때문에 발생한
    운영 전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: startup self-update는 dirty checkout 때문에 dry-run에서 skip,
    workspace cleanliness/current branch compatibility 진단 후 `NO_RUNNABLE_TASK`.

## 수동 검증

- Issue #118 요구사항과 `docs/15`, `docs/16`, `docs/17`, `docs/18`의 경계를
  대조해 runtime adapter와 GitHub orchestration을 범위 밖으로 유지했다.

## 남은 TODO와 제한

- approved plan persistence는 아직 없다.
- CLI/daemon integration은 아직 없다.
- Codex/Claude/API/local runtime adapter는 후속 Goal 범위다.
- Final Goal audit 구현은 후속 Goal 범위다.

## 위험 요소

- 현재 구현은 순수 domain skeleton이므로 운영 daemon에서 자동 사용되지는 않는다.
- 후속 integration에서 기존 Task Issue/Branch/Contract/PR 산출물과 snapshot evidence를
  연결하는 저장소 계층이 필요하다.

## Improvement Suggestions

- 다음 Goal에서 approved `GoalExecutionPlan` 저장 포맷과 CLI 로딩 경로를 추가한다.
- 그 다음 Goal에서 execution/verification request를 기존 WorktreeManager,
  Validation Gate, ReviewReport evidence에 연결한다.
