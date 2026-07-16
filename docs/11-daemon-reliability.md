# Daemon Reliability (Task 019)

이 문서는 Task 019(Daemon Reliability Baseline)가 도입한 실패 분류, 재시도
정책, 복구 정책, 시작 검증, `devbot doctor`, 진단 보고서를 설명한다. 세부
구현은 `src/devbot/reliability.py`, `src/devbot/startup.py`,
`src/devbot/doctor.py`를 참고한다.

이 Task는 새로운 기능이 아니라 기존 daemon/review/rework/delivery/timeline
동작의 안정성 계층이다 - `devbot:*` 상태 전이 자체는 Task 014가 이미
하드닝했고(`docs/07-decisions.md`의 "Working must be transient"), 이 Task는
그 위에 분류·재시도·시작 검증·진단을 얹는다.

## 1. 실패 분류 (Failure Classification)

`devbot.models.FailureCategory`:

| 값 | 의미 |
|---|---|
| `workspace_invalid` | 로컬 워크스페이스가 없거나 Git 저장소가 아니거나 미커밋 변경이 있음 |
| `startup_validation_failed` | 시작 검증 항목 실패 (현재는 항상 WARNING, 2절 참고) |
| `agent_session_limit` | Agent 세션/사용량 제한 (`devbot.agents.base.is_session_limit_output`) |
| `agent_execution_failed` | Agent 실행 자체가 실패(CLI 없음/nonzero 종료). Task 026 이후 CLI timeout/interruption은 먼저 `resumable_interruption` Agent outcome으로 분류되어 worktree 보존/resume 경로를 탄다. |
| `delivery_failed` | 검증/커밋/푸시/PR 단계 실패 |
| `review_failed` | 리뷰 Agent 실행 실패 또는 잘못된 Review Summary |
| `github_api_error` | GitHub API 조회/쓰기 실패 |
| `configuration_error` | 설정/환경 변수 오류 (fatal) |
| `unknown_error` | 위 분류에 해당하지 않는 예외 |

두 개의 분류기가 있다.

- `devbot.reliability.classify_exception(exc)`: 예외 타입 기준
  (`WorkspaceValidationError`, `ConfigError`, `GitHubClientError`,
  `DeliveryError`).
- `devbot.polling.classify_job_failure(job_type, status, message)`:
  `PollingResult`(status/message) 기준. `agent_session_limit`은 메시지
  텍스트 패턴(`is_session_limit_output`)으로 감지한다 - 이 텍스트 매칭은
  Agent CLI의 실제 출력 문구에 의존하는 최선 추정이며, Task 016의
  `is_approval_required_output`과 동일한 방식이다.

두 분류기 모두 진단/로깅 전용이며 `devbot:*` 상태 전이 자체를 바꾸지
않는다 - 그건 여전히 `devbot.issue_state`의 역할이다.

## 2. 재시도 정책 (Retry Policy)

`devbot.reliability.RETRY_POLICY` / `decide_retry(category, attempt)`.
Task 019 계약의 최소 규칙을 그대로 구현한다.

| 분류 | 재시도 | 비고 |
|---|---|---|
| `workspace_invalid` | 없음 | |
| `agent_session_limit` | 없음 | 운영자 조치 또는 제한 해제 대기 필요 (5절) |
| `delivery_failed` | 제한적 (최대 3회, 고정 60초 backoff) | |
| `github_api_error` | 제한적 (최대 5회, 지수 backoff 30s~900s) | |
| `configuration_error` | 없음 | fatal startup failure (3절) |
| `startup_validation_failed`/`agent_execution_failed`/`review_failed`/`unknown_error` | 없음 | |

`decide_retry()`는 순수 함수다 - 시도 횟수를 자체적으로 기록/영속화하지
않는다. 현재 daemon은 cycle 간 시도 횟수를 추적하는 상태 저장소가 없으므로
(범위 밖), 이 정책은 "다음에 같은 실패가 다시 발생하면 어떻게 할지"를
결정하는 선언적 테이블이며, 실제 폴링 재시도 여부는 여전히
`devbot:blocked`/`devbot:working` 상태 전이(3절)로 강제된다.

Task 026의 timeout/resume은 이 전역 retry table을 직접 재사용하지 않는다.
Agent runner가 `AgentOutcome.RESUMABLE_INTERRUPTION`을 반환하면
`PollingService`는 prepared worktree를 삭제하지 않고 Issue를 이전 안정
상태(`ready`/`rework`)로 복구한다. 다음 cycle은 같은 Issue/PR/branch/
contract metadata로 재사용된 dirty worktree만 resume 후보로 보고,
Issue comment의 `devbot-resume:v1` marker에서 attempt 수를 읽어 기본
3회(`RESUME_ATTEMPT_LIMIT`)까지만 continuation prompt를 붙인다. Cap 초과
또는 contract metadata 누락 같은 unsafe resume은 worktree를 보존한 채
`devbot:manual-action`으로 전환한다.

## 3. 복구 정책 (Recovery Policy)

`devbot.reliability.RECOVERY_POLICY` / `recovery_outcome_for(category)`는
`devbot.models.RecoveryOutcome`(`RESTORE`/`BLOCKED`/`MANUAL_ACTION`/
`REVIEW`) 중 claim된 워크플로가 도달해야 하는 결과를 선언한다. 이는
`docs/07-decisions.md`의 "Working must be transient" 결정과 Task 014
(CP-014-5/6/7)가 이미 구조적으로 강제하는 내용을 실패 분류 테이블로 다시
표현한 것이다.

- `workspace_invalid`: claim 직후 preflight 실패 → `RESTORE` (이전 안정
  상태로 복구).
- 나머지 8개 분류: `BLOCKED`.
- `resumable_interruption`은 `FailureCategory`가 아니라 `AgentOutcome`이다.
  claimed workflow는 `RESTORE`로 이전 안정 상태에 돌아가 다음 bounded
  resume cycle을 기다리며, cap 초과/unsafe metadata만 `MANUAL_ACTION`으로
  전환한다.

`MANUAL_ACTION`/`REVIEW`는 이 9개 실패 분류 어디에도 매핑되지 않는다 -
`manual-action`은 실패가 아니라 액션 스코프 분류(Task 016)의 결과이고,
`review`는 성공 경로이기 때문이다.

## 4. 시작 검증 (Startup Validation)

`devbot.startup.run_startup_checks(config)`는 폴링 시작 전에 실행되며
(`devbot.main.main()`, `ProcessLock` 획득 직후) 다음을 확인한다.

- `repository_configuration`, `github_credentials`, `timeline_configuration`
- 활성화된 저장소마다 `workspace_cleanliness[...]`,
  `current_branch_compatibility[...]`

**이미 존재하던 두 개의 진짜 fatal 조건은 이 함수가 다시 구현하지 않는다**:
잘못된/누락된 설정과 `GITHUB_TOKEN` 누락은 `devbot.config.load_config`가
`ConfigError`로, 중복 daemon 인스턴스는 `devbot.lock.ProcessLock.acquire()`가
`LockAcquisitionError`로 이미 처리하며, 둘 다 `run_startup_checks()`가 실행
되기 전에 - GitHub Write Client가 생성되기도 전에 - `main()`을 멈춘다
(8절/CP-019-8).

`run_startup_checks()` 자체의 모든 검사는 **WARNING**이다 - 실패해도
daemon을 멈추지 않는다. 이유:

- 활성화된 저장소가 0개인 것은 `PollingStatus.NO_MANAGED_REPOSITORIES`가
  이미 안전하게(그 cycle만 건너뜀) 처리하는 유효한 idle 배포 상태다.
- 워크스페이스가 dirty하거나 예상 밖 branch에 있는 것은 정상적인 WORKING
  중간 상태일 수 있고(`docs/03-state-machine.md`), 다른 모든 저장소의
  폴링을 막을 이유가 없다. `devbot.polling`은 이미 dirty 워크스페이스를
  per-job으로(`PollingStatus.WORKSPACE_INVALID`) 안전하게 처리한다.

daemon lock 상태는 `run_startup_checks()`에 포함되지 않는다 - 이 함수가
호출되는 시점에는 이미 자기 자신이 그 lock을 들고 있어 재검사가
자기참조적이기 때문이다(2번째 acquire 시도는 항상 "점유 중"으로 보고됨).
Lock 상태는 `devbot doctor`(5절)가 daemon이 lock을 얻기 *전에* 별도로
확인한다.

## 5. `devbot doctor`

```bash
uv run devbot doctor
```

읽기 전용 - GitHub에 쓰지 않고, daemon lock을 실제로 잡지 않는다
(`devbot.startup.check_daemon_lock`은 non-blocking acquire 후 즉시
release하는 probe다 - 이미 실행 중인 daemon 옆에서 안전하게 실행 가능).
`run_startup_checks()`의 모든 항목에 다음을 더해 보고한다.

- `daemon_lock`: lock 사용 가능 여부 (fatal)
- `github_connectivity`: 실제 `GET /user` 호출 결과. 네트워크 제한
  환경에서도 예외를 잡아 실패로 보고할 뿐 절대 크래시하지 않는다.
- `agent_roles`: 구성된 implementer/reviewer, dry_run 여부

`safe_to_start`는 `fatal=True`로 표시된 검사(현재는 `daemon_lock`만)가
모두 통과했을 때만 `yes`다 - `github_connectivity`를 포함한 나머지는
운영자 참고용이며 시작 가능 여부 자체를 좌우하지 않는다(네트워크 제한
환경에서도 `doctor`가 유용한 보고서를 내야 하기 때문).

## 6. 진단 보고서 (Diagnostics)

`devbot.reliability.build_diagnostic_report()` /
`render_diagnostic_report()`는 다음을 포함한 텍스트 보고서를 만든다:
repository, Issue/PR 번호, 현재 branch, 워크스페이스 상태/변경 파일,
실패 분류, 재시도 결정, 복구 권장 조치. `devbot.observability.log_diagnostic_report()`가
ERROR 레벨로 기록하며 (Secret은 기존과 동일하게 redact됨), `PollingService._execute_job()`이
`_JOB_FAILURE_STATUSES`에 해당하는 모든 실패에 대해 자동으로 호출한다.

## 7. 데몬 생존성 (Daemon Survival)

`PollingService._execute_job()`은 이제 IMPLEMENT/REWORK/REVIEW Job 실행
전체를 `except Exception`으로 감싼다 - 이전에는 여러 Job을 동시 실행하는
경로(`ThreadPoolExecutor`)만 이 안전망을 가지고 있었고, 기본값이자 가장
흔한 단일 Job 경로(`max_concurrent_jobs=1`)는 `ensure_git_workspace_ready`류
헬퍼가 문서화되지 않은 예외 타입(예: 예상 밖의 `OSError`)을 던지면 그대로
daemon 전체를 죽일 수 있었다. `run_forever()`도 `polling_service.run_once()`
호출 자체를 다시 한번 방어해, 어떤 경로로든 한 cycle이 완전히 실패해도
다음 폴링 간격 뒤 다음 cycle이 계속 실행된다.

## 8. Fatal 실패 처리

`ConfigError`/`LockAcquisitionError`는 `devbot.main.main()`이 `PollingService`나
`GitHubWriteClient`를 생성하기 전에 잡아 즉시 종료 코드 1로 반환한다 - 이
시점까지 GitHub에 대한 쓰기 호출은 물리적으로 발생할 수 없다
(`tests/test_main_loop.py::test_daemon_stops_on_fatal_failure`가
`GitHubWriteClient`가 한 번도 생성되지 않음을 직접 검증한다).

## 9. Agent 세션 제한 처리

`devbot.agents.base.is_session_limit_output(message)`가 Agent 출력에서
사용량/세션/rate limit 패턴을 감지하면:

- `classify_job_failure`/`classify_exception`이 `agent_session_limit`으로
  분류한다.
- 자동 재시도하지 않는다(2절).
- IMPLEMENT(`devbot.polling`)/REVIEW(`devbot.review`)/REWORK(`devbot.rework`
  + `devbot.agents.base.AgentSessionLimitError`, `devbot.main._apply_rework_changes`가
  구분해 raise) 세 경로 모두 기존과 동일하게 `devbot:blocked`로 전이하되,
  `devbot.reliability.session_limit_block_reason()`이 블로킹 댓글에
  `[failure_category=agent_session_limit]`와 함께 "제한 해제 후 이전
  상태로 되돌리라"는 명확한 복구 힌트를 덧붙인다.
