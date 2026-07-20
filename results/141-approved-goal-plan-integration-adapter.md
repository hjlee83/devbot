# Task 141 Result: Approved GoalExecutionPlan Integration Adapter

## 완료 내용

- 승인된 `GoalExecutionPlan`을 versioned JSON 문서로 로드/검증하는 adapter
  계층을 추가했다.
- `GoalRun` runtime state를 versioned JSON으로 원자 저장하고 resume할 수 있게
  했다.
- `ExecutionRequest`를 `RuntimeScheduler`에 제출하고 adapter protocol을 통해
  기존 실행 seam으로 연결할 수 있게 했다.
- `VerificationRequest`를 verification adapter protocol로 처리하고 결과를
  `GoalRun`에 기록한다.
- 완료/실패/에스컬레이션 상태에서 `CompletionSnapshot`을 persisted state에
  저장한다.
- 외부 side effect 실행 전 `in_flight_side_effect` marker를 원자 저장하고,
  재시작 시 marker가 남아 있으면 자동 재실행하지 않고 `ESCALATED`로 fail closed한다.
- execution뿐 아니라 verification side effect도 marker로 보호한다.
- 기존 Worktree/agent 및 validation/review seam을 주입해 호출하는 concrete
  adapter(`ExistingDevBotExecutionAdapter`, `ExistingDevBotVerificationAdapter`)를
  추가했다.
- GitHubClient, WorktreeManager, AgentRunner, validation command seam을 조립하는
  `DevBotGoalExecutionAdapter`와 `DevBotGoalVerificationAdapter`를 추가했다.
- `DevBotGoalExecutionAdapter`가 prepared workspace의 approved Task Contract를
  로드/검증한 뒤 agent prompt evidence로 전달한다.
- `advance-approved` verification 경로가 architecture gate에서 기존
  `ReviewService.process()` seam을 호출하도록 review gate를 조립한다.
- `devbot goal advance-approved` CLI가 persisted state의 pending execution 또는
  verification을 한 단계 처리한다.
- `devbot goal validate-approved/start-approved/status-approved/resume-approved`
  CLI를 추가했다.

## 주요 설계 결정

- `goal_execution_foundation.py`는 순수 domain module로 유지하고 runtime,
  persistence, scheduler 연결은 새 `goal_runtime_adapter.py`에 둔다.
- 실제 provider/runtime 호출은 adapter protocol에 맡긴다. Core domain type에는
  named provider dependency를 추가하지 않았다.
- pending execution은 기존 `RuntimeScheduler.execute()`를 통과하도록 `Job`으로
  변환한다. 이로써 같은 repository 직렬화와 AI concurrency 제한을 재사용한다.
- corrupt state와 unsupported schema는 typed error로 fail closed한다.
- duplicate task binding은 첫 항목을 암묵 선택하지 않고 validation에서 거부한다.

## 수정 파일

- `src/devbot/goal_runtime_adapter.py`
- `src/devbot/main.py`
- `tests/test_goal_runtime_adapter.py`
- `tests/test_main.py`
- `tasks/141-approved-goal-plan-integration-adapter.md`
- `results/141-approved-goal-plan-integration-adapter.md`
- `docs/00-roadmap.md`

## Checkpoint Evidence

- Plan load/schema validation:
  `test_approved_plan_document_round_trips_and_validates`,
  `test_unsupported_plan_schema_fails_closed`,
  `test_duplicate_task_bindings_are_rejected`
- Corrupt state fail-closed:
  `test_corrupt_runtime_state_fails_closed`,
  `test_in_flight_execution_marker_fails_closed_on_resume`,
  `test_in_flight_verification_marker_fails_closed_on_resume`
- Start/status/resume and completion:
  `test_start_status_resume_and_successful_completion`
- RuntimeScheduler repository limits:
  `test_goal_execution_batch_uses_runtime_scheduler_repository_limits`
- Existing seam adapters:
  `test_existing_devbot_execution_adapter_calls_workspace_and_agent_seams`,
  `test_existing_devbot_verification_adapter_routes_validation_and_review_seams`,
  `test_devbot_goal_execution_adapter_reaches_worktree_and_agent_seams`,
  `test_devbot_goal_verification_adapter_reaches_validation_seam`
- CLI wiring:
  `test_goal_approved_validate_start_status_resume_commands_are_wired`,
  `test_goal_review_gate_reaches_review_service`

## Validation 결과

- `uv run ruff check src/devbot/goal_runtime_adapter.py src/devbot/main.py tests/test_goal_runtime_adapter.py tests/test_main.py`
  - PASS
- `uv run pytest tests/test_goal_runtime_adapter.py tests/test_main.py::test_goal_approved_validate_start_status_resume_commands_are_wired tests/test_main.py::test_goal_review_gate_reaches_review_service`
  - PASS, 15 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1423 passed in 56.55s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 rework 변경 파일 때문에 발생한
    운영 전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: startup self-update는 dirty checkout 때문에 dry-run에서 skip,
    workspace cleanliness/current branch compatibility 진단 후 `NO_RUNNABLE_TASK`.

## 수동 검증

- Issue #141 요구사항과 비교해 runtime concern이 foundation module로 침투하지
  않도록 확인했다.
- Scheduler 제출은 기존 `RuntimeScheduler.execute()`를 사용한다.
- Execution adapter가 `GoalTaskBinding.contract_path`와 prepared workspace의
  contract path 일치를 확인하고 Contract parser를 호출하는 것을 테스트로 확인했다.
- Architecture verification이 기존 `ReviewService.process()` seam에 도달하는 것을
  테스트로 확인했다.

## 남은 TODO와 제한

- concrete adapter는 기존 seam을 주입받아 호출하는 composition layer이며, 배포별
  service wiring은 daemon discovery 정책과 함께 후속 end-to-end validation에서
  확정해야 한다.
- Final Goal audit과 `GOAL_ACCEPTED`는 범위 밖이다.
- GitHub webhook orchestration은 범위 밖이다.

## 위험 요소

- CLI start/status/resume은 로컬 persisted state 중심이며 daemon polling loop에
  자동 discovery되지는 않는다.
- `in_flight_side_effect` marker는 ambiguous replay를 막지만, marker가 남으면
  자동 복구가 아니라 운영자 검토가 필요한 `ESCALATED`로 전환한다.

## Improvement Suggestions

- 다음 Task에서 fixture repository를 대상으로 approved Goal plan end-to-end release
  validation을 실행한다.
- end-to-end validation에서 `ESCALATED` marker 복구 runbook을 문서화한다.
