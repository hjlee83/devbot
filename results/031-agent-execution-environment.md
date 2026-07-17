# Task 031 Result: Agent Execution Environment

## 완료 내용

- 모든 Agent가 공유하는 불변 `AgentExecutionContext`를 추가했다.
- IMPLEMENT, REWORK, REVIEW 실행 경로가 PreparedWorkspace 기반 context를 만들고 Agent에 전달한다.
- Codex/Claude 실행은 공통 `AgentLauncher`를 통해 cwd, 환경, 실행 정책, 안전 진단을 표준화한다.
- Agent 환경 변수는 `DEVBOT_*` 안전 메타데이터와 `PATH`만 전달하며 credential/token/prompt는 전달하지 않는다.
- Codex capability discovery는 process lifetime cache를 사용하되, 모든 필수 capability가 확인된 성공 결과만 cache한다.
- Codex unattended 실행은 capability가 부족하거나 Git metadata 경로를 확인할 수 없으면 fail-closed로 `agent_configuration_invalid`를 반환한다.
- doctor가 Agent availability, capability readiness, unattended execution readiness를 보고한다.
- Startup Self Update를 추가해 doctor/daemon 실행 전 DevBot operator checkout만 `git fetch origin`, `git switch main`, `git pull --ff-only origin main` 순서로 갱신한다.
- Startup Self Update는 Git common-dir 기준으로 operator checkout을 해석하며, `config.enabled_repositories`의 managed repositories를 순회하거나 변경하지 않는다.
- Startup Self Update로 HEAD가 바뀌면 현재 프로세스를 `exec`로 재시작하고 `DEVBOT_STARTUP_SELF_UPDATED`로 재시작 루프를 방지한다.
- Startup Self Update 실패 시 doctor/planner/workspace preparation/Agent 실행 전에 즉시 중단한다.

## 주요 설계 결정

- Provider별 command construction은 Codex/Claude runner 안에 유지하고, 공통 launcher는 cwd/env/policy/diagnostics만 담당한다.
- `AgentRunner.run_context()` 기본 구현은 기존 `run(repository, prompt)`로 위임해 기존 runner contract와 테스트 더블 호환성을 유지한다.
- PollingService는 실제 context-aware runner에는 context를 넘기고, 기존 runner/service double에는 기존 signature로 fallback한다.
- Startup Self Update는 Task/PR/PreparedWorkspace/managed repository를 갱신하지 않고 Git common-dir로 해석한 DevBot operator checkout만 대상으로 한다.
- Startup Self Update 진단은 SHA, 결과, skip reason만 남기며 secret/prompt/token은 기록하지 않는다.

## Checkpoint Evidence

- CP-031-1 Shared context:
  - `src/devbot/agent_execution.py`
  - `tests/test_agent_execution.py::test_agent_execution_context_contains_canonical_task_metadata`
- CP-031-2 PreparedWorkspace cwd:
  - `AgentLauncher.run(..., cwd=context.workspace, ...)`
  - `tests/test_agent_execution.py::test_all_agent_roles_execute_from_prepared_workspace`
- CP-031-3 Safe environment:
  - `AgentExecutionContext.safe_environment()`
  - `tests/test_agent_execution.py::test_agent_environment_is_normalized_and_contains_no_secrets`
- CP-031-4 Common launcher:
  - `AgentLauncher`
  - `tests/test_agent_execution.py::test_agent_launcher_applies_shared_context_to_provider_command`
- CP-031-5 Safe diagnostics:
  - `AgentExecutionDiagnostics`
  - `tests/test_agent_execution.py::test_agent_execution_diagnostics_are_complete_and_redacted`
- CP-031-6 Capability cache:
  - `CodexRunner._CAPABILITY_CACHE`
  - `tests/test_agent_execution.py::test_agent_capability_detection_is_cached`
  - `tests/test_agent_execution.py::test_failed_capability_detection_remains_fail_closed`
- CP-031-7 Doctor:
  - `check_agent_execution_readiness`
  - `tests/test_doctor.py::test_doctor_reports_agent_execution_readiness`
- CP-031-8 Role parity:
  - `PollingService` IMPLEMENT/REWORK/REVIEW context construction
  - `tests/test_agent_execution.py::test_implement_rework_and_review_share_execution_invariants`
- CP-031-9 Compatibility:
  - Existing polling/review/rework tests remain passing.
  - `tests/test_agent_execution.py::test_existing_workflows_remain_compatible_with_agent_execution_context`
- CP-031-10 Startup Self Update:
  - `startup_self_update_repository`
  - `tests/test_startup_self_update.py`
  - `tests/test_main.py::test_startup_update_restarts_process_when_head_changes`
  - `tests/test_main.py::test_startup_update_does_not_restart_twice`

## Validation

- `uv sync`: PASS
- `uv run ruff check .`: PASS
- `uv run pytest`: PASS, 484 passed
- `uv run devbot doctor`: PASS
- `uv run devbot --once --dry-run`: PASS

Note: an initial parallel run of `doctor` and `--once --dry-run` caused a Git remote ref lock during concurrent Startup Self Update fetch. The serial rerun of `uv run devbot --once --dry-run` passed.

## 수정 파일

- `src/devbot/agent_execution.py`
- `src/devbot/agents/base.py`
- `src/devbot/agents/claude.py`
- `src/devbot/agents/codex.py`
- `src/devbot/doctor.py`
- `src/devbot/main.py`
- `src/devbot/polling.py`
- `src/devbot/review.py`
- `src/devbot/rework.py`
- `src/devbot/startup.py`
- `tests/test_agent_execution.py`
- `tests/test_doctor.py`
- `tests/test_main.py`
- `tests/test_main_loop.py`
- `tests/test_startup_self_update.py`
- `tests/test_timeline.py`

## 남은 TODO와 제한

- 없음.

## 위험 요소

- Startup Self Update는 operator checkout의 Git remote ref를 갱신하므로 같은 checkout에서 동시에 여러 DevBot CLI를 시작하면 Git ref lock으로 fail-safe 중단될 수 있다.

## Improvement Suggestions

- operator checkout 단위 startup update lock을 별도로 두면 동시에 시작된 doctor/daemon의 fetch lock 실패를 더 명확한 진단으로 줄일 수 있다.
