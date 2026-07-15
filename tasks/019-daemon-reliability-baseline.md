# Task 019: Daemon Reliability Baseline

## Goal

Stabilize DevBot daemon execution for long-running operation.

This task focuses on operational reliability rather than new user-facing features. Operators must always understand why execution stopped, whether recovery is automatic, what manual action is required, and whether it is safe to continue.

## 배경

Task 014가 `devbot:working`이 영구적으로 남지 않도록 상태 전이 자체는
하드닝했지만(`docs/07-decisions.md` "Working must be transient"), 그
실패가 *왜* 일어났는지, 자동으로 재시도해도 되는지, 사람이 무엇을 해야
하는지를 운영자에게 알려주는 선언적 계층은 없었다. 또한 단일 Job 실행
경로(`max_concurrent_jobs=1`, 기본값)는 `ThreadPoolExecutor` 동시 실행
경로와 달리 예상 밖 예외 타입에 대한 안전망이 없어 daemon 프로세스
자체가 죽을 수 있는 공백이 있었다. 이 Task는 그 두 공백(운영 진단, daemon
생존성)을 메운다.

## Dependencies

- Task 013(운영 관찰성/디버그 로깅, `src/devbot/observability.py`)
- Task 014(workflow state machine hardening, `src/devbot/issue_state.py`)
- Task 016(rework action scope 분류, `src/devbot/rework.py`)

이 Task는 위 세 Task가 만든 로깅/상태 전이/액션 스코프 분류 인프라 위에
분류·재시도·복구·시작 검증·진단을 얹는다. 새로운 `devbot:*` 라벨이나
상태 전이를 추가하지 않는다.

## Scope

### 1. Failure Classification

Introduce explicit failure categories for at least:

- `workspace_invalid`
- `startup_validation_failed`
- `agent_session_limit`
- `agent_execution_failed`
- `delivery_failed`
- `review_failed`
- `github_api_error`
- `configuration_error`
- `unknown_error`

### 2. Retry Policy

Define deterministic retry behavior for every failure type.

Minimum rules:

- `workspace_invalid`: no automatic retry
- `agent_session_limit`: no repeated polling retry; require operator action or a known retry-after boundary
- `delivery_failed`: bounded retry only
- `github_api_error`: bounded exponential backoff
- `configuration_error`: fatal startup failure

### 3. Recovery Policy

Every claimed workflow must leave `devbot:working` through one of these explicit outcomes:

- restore to the previous stable state
- `devbot:blocked`
- `devbot:manual-action`
- successful transition to `devbot:review`

No failure path may leave an Issue permanently in `devbot:working`.

### 4. Startup Validation

Before polling begins, validate:

- repository configuration
- required GitHub credentials
- duplicate daemon lock
- workspace cleanliness
- current branch compatibility
- required timeline configuration

Fatal startup validation failures must stop before the first polling cycle.

### 5. Doctor Command

Provide:

```bash
uv run devbot doctor
```

The command must report at least:

- configured repositories
- workspace status
- current branch
- GitHub connectivity/authentication
- daemon lock status
- configured implementer and reviewer
- whether the daemon is safe to start

### 6. Diagnostics

Every operational failure must produce an operator-friendly diagnostic report containing:

- repository
- Issue/PR when available
- current branch
- workspace status and changed files when relevant
- failure category
- retry decision
- recovery recommendation

### 7. Daemon Survival

A non-fatal Job failure must not terminate the daemon process. The next cycle must still run.

### 8. Fatal Failure Handling

A fatal configuration or startup failure must terminate safely before polling and must not mutate GitHub state.

### 9. Session Limit Handling

When an Agent reports a session or usage limit, DevBot must:

- classify it as `agent_session_limit`
- avoid infinite retries
- avoid leaving the Issue in `devbot:working`
- provide a clear recovery hint

## Out of Scope

- Worktree isolation
- Timeline automatic recording
- Dashboard/UI
- Slack or chat integrations
- Multi-agent failover
- VPS deployment

## Checkpoints

### CP-019-1 — Failure classification

Required test:

- `test_failure_classification`

### CP-019-2 — Retry policy

Required test:

- `test_retry_policy`

### CP-019-3 — Recovery policy

Required test:

- `test_recovery_policy`

### CP-019-4 — Startup validation

Required test:

- `test_startup_validation`

### CP-019-5 — Doctor command

Required test:

- `test_doctor_command`

### CP-019-6 — Diagnostic output

Required test:

- `test_diagnostics_output`

### CP-019-7 — Non-fatal failure survival

Required test:

- `test_daemon_survives_non_fatal_failure`

### CP-019-8 — Fatal failure handling

Required test:

- `test_daemon_stops_on_fatal_failure`

### CP-019-9 — Agent session limit handling

Required test:

- `test_agent_session_limit_handling`

### CP-019-10 — Regression safety

Required evidence:

- existing test suite passes
- existing daemon CLI behavior remains compatible

### CP-019-11 — Result documentation

Required artifact:

- `results/019-daemon-reliability-baseline.md`

## Validation Gate

All of the following must pass:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
```

Also perform:

```bash
uv run devbot --once
```

If the live command cannot complete because of external credentials, network restrictions, or an existing daemon lock, the Result document and PR Evidence must record the exact limitation and the alternative verification used.

## Deliverables

- failure classification
- retry policy
- recovery policy
- startup validation
- `doctor` command
- diagnostics
- tests for every checkpoint
- updated documentation
- `results/019-daemon-reliability-baseline.md`

## Files Expected to Change

- `src/devbot/models.py` (`FailureCategory`, `RecoveryOutcome`)
- `src/devbot/agents/base.py` (`is_session_limit_output`, `AgentSessionLimitError`)
- `src/devbot/workspace.py` (`WorkspaceStatus`, `inspect_workspace`)
- `src/devbot/reliability.py` (신규)
- `src/devbot/startup.py` (신규)
- `src/devbot/doctor.py` (신규)
- `src/devbot/observability.py` (`log_startup_validation`, `log_diagnostic_report`)
- `src/devbot/polling.py` (`classify_job_failure`, `_execute_job` 안전망, `run_forever` 안전망, session-limit 힌트)
- `src/devbot/review.py`, `src/devbot/rework.py` (session-limit 힌트)
- `src/devbot/main.py` (`doctor` 서브커맨드, 시작 검증 로그, `_apply_rework_changes`)
- `docs/00-roadmap.md`, `docs/03-state-machine.md`, `docs/07-decisions.md`, `docs/08-beta-runbook.md`, `docs/11-daemon-reliability.md` (신규)
- `tests/test_reliability.py`, `tests/test_startup.py`, `tests/test_doctor.py` (신규), `tests/test_main_loop.py` 추가
- `tasks/019-daemon-reliability-baseline.md` (이 문서 - 필수 항목 보강)
- `results/019-daemon-reliability-baseline.md` (신규)

## Risk

- Session-limit/승인-필요(Task 016) 감지는 Agent CLI의 원본 출력 문구에
  대한 텍스트 패턴 매칭이다(`is_session_limit_output`,
  `is_approval_required_output`과 동일 방식) - CLI 출력 문구가 바뀌면
  분류가 어긋날 수 있다. 다만 분류가 틀려도 최악의 경우 일반
  `agent_execution_failed`로만 분류되어 `devbot:blocked` 전이 자체는
  항상 안전하게 유지된다(회귀 없음).
- 시작 검증(4절)을 fatal 게이트가 아닌 WARNING으로 설계한 것은 의도적
  선택이다(`docs/07-decisions.md` 2026-07-15 결정). 여러 저장소를 운영할
  때 한 저장소의 dirty workspace가 다른 저장소의 폴링까지 막지 않게
  하려는 목적이지만, 그만큼 "시작 검증"이라는 이름이 주는 기대(뭔가
  막아준다)보다 실제로는 로그만 남긴다는 점을 리뷰어와 운영자 모두
  분명히 인지해야 한다.
- 재시도 정책(`decide_retry`)은 순수 함수이며 cycle 간 시도 횟수를
  영속화하지 않는다 - "제한된 횟수만 재시도"라는 계약 요구는 정책
  테이블 수준에서는 정확히 구현되었지만, 실제 daemon이 그 횟수를
  세면서 자동 재시도를 수행하는 상태 저장소는 이 Task 범위에 없다
  (Improvement Suggestions 참고).

## Rollback Strategy

이 Task는 기존 `devbot:*` 라벨/상태 전이를 하나도 바꾸지 않고 순수
추가(신규 모듈 3개, 기존 모듈에 대한 additive 함수/훅)로 구성되어 있다.
문제가 생기면 이 Task의 커밋만 되돌리면(`git revert`) Task 018 시점의
daemon 동작으로 완전히 복귀한다 - 별도의 데이터 마이그레이션이나 GitHub
라벨 정리가 필요 없다.

## Reviewer Focus

- `PollingService._execute_job()`과 `run_forever()`에 추가된 두 개의
  `except Exception` 안전망(CP-019-7)이 기존 실패 경로(WORKSPACE_INVALID,
  AGENT_FAILED, BLOCKED, ITERATION_ERROR)의 결과/메시지를 하나도 바꾸지
  않고 오직 "이전에 잡히지 않던 예외"만 추가로 잡는지 확인한다.
- 시작 검증(`devbot.startup`)이 어떤 경우에도 daemon startup을 막지
  않는지, 반대로 `devbot.doctor`의 `daemon_lock`만 `safe_to_start`를
  좌우하는지 확인한다.
- session-limit 힌트 삽입(`session_limit_block_reason`)이 기존 block
  reason 텍스트를 대체하지 않고 덧붙이기만 하는지, 그리고 IMPLEMENT/
  REWORK/REVIEW 세 경로 모두에서 일관되게 동작하는지 확인한다.
- CP-019-1~9에 대응하는 필수 테스트 이름 9개가 정확히 그 이름 그대로
  존재하는지 확인한다(`docs/09-task-contract-standard.md`).

## Definition of Done

- CP-019-1부터 CP-019-11까지 모두 만족.
- Validation Gate(`uv sync`, `uv run ruff check .`, `uv run pytest`,
  `uv run devbot doctor`, `uv run devbot --once`)가 모두 통과하거나, 통과
  못한 경우 정확한 제약과 대안 검증 방법이 Result 문서와 PR Evidence에
  기록됨.
- 기존 daemon/review/rework/delivery/timeline 동작과 기존 테스트 스위트에
  회귀가 없음.
- `results/019-daemon-reliability-baseline.md`가 작성되고 PR Evidence와
  일치함.

## Result 문서 경로

`results/019-daemon-reliability-baseline.md`
