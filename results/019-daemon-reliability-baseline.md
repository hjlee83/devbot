# Task 019 Result: Daemon Reliability Baseline

## 완료 내용

Task 019 계약(`tasks/019-daemon-reliability-baseline.md`)의 CP-019-1부터
CP-019-11까지 전부 구현했다. 새 기능이 아니라 기존 daemon/review/rework/
delivery/timeline 동작 위에 얹는 안정성 계층이며, 기존 `devbot:*` 라벨/
상태 전이는 하나도 바꾸지 않았다. 세부 설계는
`docs/11-daemon-reliability.md`를 참고한다.

- **실패 분류 (CP-019-1)**: `devbot.models.FailureCategory` 9종
  (`workspace_invalid`, `startup_validation_failed`, `agent_session_limit`,
  `agent_execution_failed`, `delivery_failed`, `review_failed`,
  `github_api_error`, `configuration_error`, `unknown_error`). 두 분류기:
  `devbot.reliability.classify_exception()`(예외 타입 기준)과
  `devbot.polling.classify_job_failure()`(`PollingResult` status/message
  기준, session-limit은 `devbot.agents.base.is_session_limit_output()`
  텍스트 패턴으로 감지). 둘 다 진단/로깅 전용이며 상태 전이 자체를 바꾸지
  않는다.
- **재시도 정책 (CP-019-2)**: `devbot.reliability.RETRY_POLICY` /
  `decide_retry()`. 계약의 5개 최소 규칙(`workspace_invalid` 재시도 없음,
  `agent_session_limit` 재시도 없음, `delivery_failed` 제한적 재시도(최대
  3회, 고정 60초), `github_api_error` 제한적 지수 백오프(최대 5회,
  30s~900s), `configuration_error` fatal)을 그대로 구현했고 9개 분류
  전체를 커버한다.
- **복구 정책 (CP-019-3)**: `devbot.reliability.RECOVERY_POLICY` /
  `recovery_outcome_for()`가 `devbot.models.RecoveryOutcome`(`RESTORE`/
  `BLOCKED`/`MANUAL_ACTION`/`REVIEW`) 중 claim된 워크플로가 도달해야 하는
  결과를 선언한다. `workspace_invalid`만 `RESTORE`, 나머지 8개는
  `BLOCKED` - Task 014가 이미 구조적으로 강제하는 것과 일치한다.
- **시작 검증 (CP-019-4)**: `devbot.startup.run_startup_checks()`가
  `ProcessLock` 획득 직후, 폴링 시작 전에 repository configuration/
  GitHub credentials/timeline configuration/저장소별 workspace
  cleanliness/current branch compatibility를 확인하고
  `observability.log_startup_validation()`으로 기록한다. 이미 존재하던
  두 개의 진짜 fatal 조건(`ConfigError`, `LockAcquisitionError`)은 이
  함수가 실행되기 전에, GitHub Write Client가 생성되기도 전에 이미
  `main()`을 멈추므로 다시 구현하지 않았다. 이 함수 자체의 검사는 모두
  WARNING이며 daemon 시작을 막지 않는다 - 이유는 아래 "위험 요소" 및
  `docs/07-decisions.md` 2026-07-15 결정 참고.
- **`doctor` 명령 (CP-019-5)**: `uv run devbot doctor`
  (`src/devbot/doctor.py`, `main.py`의 `doctor` 서브커맨드). 읽기 전용 -
  GitHub에 쓰지 않고 daemon lock을 실제로 잡지 않는다
  (`check_daemon_lock`은 non-blocking acquire 후 즉시 release). 관리
  저장소, 워크스페이스 상태, 현재 branch, GitHub 연결/인증, daemon lock
  점유 여부, 구성된 implementer/reviewer, `safe_to_start` 여부를 보고한다.
  `daemon_lock`만 `fatal=True`로 `safe_to_start`를 좌우한다.
- **진단 (CP-019-6)**: `devbot.reliability.build_diagnostic_report()` /
  `render_diagnostic_report()`가 repository, Issue/PR, 현재 branch,
  워크스페이스 상태/변경 파일, 실패 분류, 재시도 결정, 복구 권장 조치를
  담은 보고서를 만든다. `observability.log_diagnostic_report()`가 ERROR
  레벨로 기록하며(Secret은 기존과 동일하게 redact), `PollingService.
  _execute_job()`이 `_JOB_FAILURE_STATUSES`에 해당하는 모든 실패에 대해
  자동으로 호출한다.
- **Daemon 생존성 (CP-019-7)**: `PollingService._execute_job()`에
  `except Exception` 안전망을 추가했다 - 이전에는 여러 Job을 동시 실행하는
  `ThreadPoolExecutor` 경로만 이 안전망을 가지고 있었고, 기본값이자 가장
  흔한 단일 Job 경로(`max_concurrent_jobs=1`)는 `ensure_git_workspace_ready`류
  헬퍼가 `WorkspaceValidationError`가 아닌 예외(예: 예상 밖 `OSError`)를
  던지면 그대로 daemon 프로세스를 죽일 수 있었다. `run_forever()`도
  `polling_service.run_once()` 호출 자체를 다시 한번 방어해, 어떤 경로로든
  한 cycle이 완전히 실패해도 다음 폴링 간격 뒤 다음 cycle이 계속
  실행된다(2중 안전망).
- **Fatal 실패 처리 (CP-019-8)**: `ConfigError`/`LockAcquisitionError`는
  `devbot.main.main()`이 `PollingService`나 `GitHubWriteClient`를 생성하기
  전에 잡아 즉시 종료 코드 1로 반환한다 - 이 시점까지 GitHub에 대한 쓰기
  호출은 물리적으로 발생할 수 없다. 기존에도 이 동작은 있었지만 전용
  회귀 테스트가 없었다; 이번에 `GitHubWriteClient`가 한 번도 생성되지
  않음을 직접 검증하는 테스트를 추가했다.
- **Agent 세션 제한 처리 (CP-019-9)**: `is_session_limit_output()`이
  Agent 출력에서 사용량/세션/rate limit 패턴을 감지하면 `agent_session_limit`
  으로 분류하고, 자동 재시도하지 않으며(재시도 정책), IMPLEMENT
  (`devbot.polling`)/REVIEW(`devbot.review`)/REWORK(`devbot.rework` +
  신규 `devbot.agents.base.AgentSessionLimitError`, `devbot.main.
  _apply_rework_changes`가 구분해 raise) 세 경로 모두 기존과 동일하게
  `devbot:blocked`로 전이하되, `devbot.reliability.session_limit_block_reason()`
  이 블로킹 댓글에 `[failure_category=agent_session_limit]`와 함께 명확한
  복구 힌트를 덧붙인다.
- **회귀 안전성 (CP-019-10)**: 기존 272개 테스트 전부 통과, 신규 31개
  테스트 추가(총 303개). `uv run devbot --once`/`--help`/`timeline` 등
  기존 CLI 동작은 변경하지 않았다(신규 `doctor` 서브커맨드만 추가).
- **Result 문서 (CP-019-11)**: 이 문서.

## 수정 파일

신규:
- `src/devbot/reliability.py` - 실패 분류(예외 기준), 재시도 정책, 복구
  정책, 진단 보고서 빌더
- `src/devbot/startup.py` - 시작 검증 체크(fatal 아님) + `daemon_lock`
  probe(fatal, `doctor` 전용)
- `src/devbot/doctor.py` - `devbot doctor` 보고서
- `docs/11-daemon-reliability.md` - 이번 Task 설계 문서
- `tests/test_reliability.py`, `tests/test_startup.py`,
  `tests/test_doctor.py`

수정:
- `src/devbot/models.py` - `FailureCategory`, `RecoveryOutcome`
- `src/devbot/agents/base.py` - `is_session_limit_output()`,
  `AgentSessionLimitError`
- `src/devbot/workspace.py` - `WorkspaceStatus`, `inspect_workspace()`
  (non-raising 워크스페이스 상태 조회)
- `src/devbot/observability.py` - `log_startup_validation()`,
  `log_diagnostic_report()`
- `src/devbot/polling.py` - `classify_job_failure()`, `_execute_job()`/
  `run_forever()` 안전망, IMPLEMENT 경로 session-limit 힌트
- `src/devbot/review.py` - REVIEW 경로 session-limit 힌트
- `src/devbot/rework.py` - REWORK 경로 `AgentSessionLimitError` 구분 처리
- `src/devbot/main.py` - `doctor` 서브커맨드, 시작 검증 로그 호출,
  `_apply_rework_changes`의 `AgentSessionLimitError` raise
- `docs/00-roadmap.md`, `docs/03-state-machine.md`, `docs/07-decisions.md`,
  `docs/08-beta-runbook.md`
- `tests/test_main_loop.py` - CP-019-7/8 테스트 추가
- `tasks/019-daemon-reliability-baseline.md` - `docs/09-task-contract-standard.md`
  필수 14개 항목 중 누락되어 있던 8개(배경, Dependencies, Files Expected
  to Change, Risk, Rollback Strategy, Reviewer Focus, Definition of Done,
  Result 문서 경로) 보강. Goal/Scope/Out of Scope/Checkpoints/Validation
  Gate/Deliverables 내용은 문구 하나 바꾸지 않았다 - CP-019 11개, 필수
  테스트 9개 이름 모두 그대로다.

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-019-1 실패 분류 | `test_failure_classification` (+ `test_session_limit_output_detection`) |
| CP-019-2 재시도 정책 | `test_retry_policy` (+ `test_non_retryable_categories_never_suggest_retry_regardless_of_attempt`) |
| CP-019-3 복구 정책 | `test_recovery_policy` |
| CP-019-4 시작 검증 | `test_startup_validation` (+ 5개 보조 테스트, `tests/test_startup.py`) |
| CP-019-5 doctor 명령 | `test_doctor_command` (+ 4개 보조 테스트, `tests/test_doctor.py`) |
| CP-019-6 진단 출력 | `test_diagnostics_output` (+ `test_diagnostics_output_without_issue_or_pr_uses_placeholders`) |
| CP-019-7 비-fatal 실패 생존성 | `test_daemon_survives_non_fatal_failure` (+ `test_run_forever_continues_after_run_once_raises`) |
| CP-019-8 fatal 실패 처리 | `test_daemon_stops_on_fatal_failure` |
| CP-019-9 Agent 세션 제한 처리 | `test_agent_session_limit_handling` (+ `test_agent_session_limit_handling_rework_path`, `test_session_limit_block_reason_includes_category_and_hint`) |
| CP-019-10 회귀 안전성 | 기존 272개 전체 통과 (아래 검증 결과) + `uv run devbot --once`/`timeline`/`doctor` CLI 정상 동작 |
| CP-019-11 Result 문서 | 이 문서 자체 (전용 unit test 없음 - 계약에도 CP-019-11에 대응하는 별도 필수 테스트 이름이 없다) |

## 검증 결과

```
uv sync
  Resolved 15 packages in 25ms
  Checked 14 packages in 6ms

uv run ruff check .
  All checks passed!

uv run pytest
  303 passed (신규 31개 + 기존 272개, 회귀 없음) - macOS(로컬)/Linux(CI) 양쪽
  확인. 최초 push(`df343f3`)는 CI(Linux, GitHub Actions)에서
  `1 failed, 302 passed`였다 - 아래 "리뷰 피드백 반영" 절 참고. 이 검증
  결과는 그 수정 반영 이후 기준이다.

uv run devbot doctor
  (실제 hjlee83/devbot 배포 설정 대상으로 실행, 종료 코드 0)
  [OK] repository_configuration: 1개 저장소가 활성화되어 있습니다
  [OK] github_credentials: GITHUB_TOKEN 설정됨
  [OK] timeline_configuration: Timeline marker 상수 확인됨
  [FAIL] workspace_cleanliness[hjlee83/devbot]: 미커밋 변경 21건 (이 Task
         자체의 구현 중 변경 - 정상)
  [FAIL] current_branch_compatibility[hjlee83/devbot]: current_branch=
         task/019-daemon-reliability-baseline (Task 계약에 따라 사람이
         만든 작업 branch - devbot/* 자동 생성 branch가 아니므로 정상)
  [OK] daemon_lock: Lock 사용 가능
  [OK] github_connectivity: 인증됨: hjlee83
  [OK] agent_roles: implementer=claude reviewer=codex dry_run=False
  safe_to_start: yes
  (workspace/branch가 WARNING으로 잡혀도 daemon lock이 자유로우므로
  safe_to_start=yes - 설계대로 동작함을 실제 배포 설정으로 확인)

uv run devbot --once --dry-run
  (실제 hjlee83/devbot 배포 설정·실제 GitHub 인증/조회로 실행, 종료 코드 0)
  DevBot 시작: version=0.1.0 implementer=claude reviewer=codex dry_run=True ...
  시작 검증: name=... (5개 항목 모두 로그로 확인, 위 doctor 결과와 일치)
  cycle 시작 → ready 상태 Issue 수: 0 → 선택 가능한 ready Issue가 없습니다
  → cycle 종료: 결과=no_ready_task
  (실시간 GitHub 인증/조회가 실제로 동작함을 확인 - "ready 상태 Issue 수: 0"은
  로컬 상태가 아니라 실제 hjlee83/devbot API 응답이다)
```

### `uv run devbot --once`(실제 non-dry-run) 관련 제약과 대안 검증

계약의 "If the live command cannot complete because of external
credentials, network restrictions, or an existing daemon lock..." 조항에
따라, 실제 `.env`가 `DRY_RUN=false`인 **공유 운영 배포**(`hjlee83/devbot`,
이 Task 자신을 포함해 다른 Agent가 동시에 폴링/리뷰할 수 있는 저장소)를
대상으로 하는 진짜 non-dry-run `uv run devbot --once`는 의도적으로
실행하지 않았다 - credentials나 network 제약이 아니라, **의도하지 않은
실제 GitHub 상태 변경(라벨/댓글/브랜치 push) 위험**이 제약이다. 대신:

1. `uv run devbot --once --dry-run`을 같은 실제 배포 설정으로 실행해 실제
   GitHub 인증/조회 경로가 동작함을 확인했다(위 결과, `ready 상태 Issue
   수: 0`은 실시간 API 응답).
2. non-dry-run 전체 흐름(claim → Agent 실행 → verify → commit/push/PR →
   review/blocked 전이, 그리고 이번에 추가된 안전망/진단/session-limit
   경로)은 `tests/test_main_loop.py`,
   `tests/test_polling.py`, `tests/test_review.py`,
   `tests/test_rework.py`, `tests/test_reliability.py`의 mock 기반
   통합 테스트로 이미 광범위하게 커버된다 - 특히
   `test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
   (`tests/test_main_loop.py`)가 `DRY_RUN=false`에서 실제
   `GitHubWriteClient.set_labels`/`create_comment` 호출 경로를 mock으로
   검증하는 기존 회귀 테스트다.

이 판단 자체가 CP-019-7/CP-019-8이 요구하는 신중함(비-fatal 실패로 daemon을
죽이지 않기, fatal 실패는 GitHub 상태를 건드리기 전에 멈추기)의 연장선이라고
본다 - 검증 목적이라도 공유 운영 저장소에 예정에 없던 실제 쓰기를 만들지
않았다.

## TODO

없음 (이 Task 범위 내). Worktree 격리, Timeline 자동 기록, 멀티 Agent
failover, VPS 배포는 계약의 명시적 제외 범위이며 후속 Task로 남긴다.

## 위험 요소

- Session-limit/승인-필요(Task 016) 감지는 Agent CLI의 원본 출력 문구에
  대한 텍스트 패턴 매칭이다(`is_session_limit_output`,
  `is_approval_required_output`과 동일 방식) - CLI 출력 문구가 바뀌면
  분류가 어긋날 수 있다. 분류가 틀려도 최악의 경우 일반
  `agent_execution_failed`로만 분류되어 `devbot:blocked` 전이 자체는
  항상 안전하게 유지된다(회귀 없음) - 분류 실패가 상태 머신 안전성에
  영향을 주지 않는다.
- 시작 검증(CP-019-4)을 fatal 게이트가 아닌 WARNING으로 설계한 것은
  의도적 선택이다(`docs/07-decisions.md` 2026-07-15 결정). 여러 저장소를
  운영할 때 한 저장소의 dirty workspace가 다른 저장소의 폴링까지 막지
  않게 하려는 목적이지만, "시작 검증"이라는 이름이 주는 기대(뭔가
  막아준다)보다 실제로는 로그만 남긴다는 점을 운영자가 분명히 인지해야
  한다 - 실제로 이번 검증에서 `devbot doctor`/`--once --dry-run` 둘 다
  이 Task 자신의 dirty workspace를 WARNING으로만 보고하고 정상 진행했다.
- 재시도 정책(`decide_retry`)은 순수 함수이며 cycle 간 시도 횟수를
  영속화하지 않는다 - "제한된 횟수만 재시도"라는 계약 요구는 정책
  테이블 수준에서는 정확히 구현되었지만, 실제 daemon이 그 횟수를 세면서
  자동 재시도를 수행하는 상태 저장소는 이 Task 범위에 없다(Improvement
  Suggestions 참고). 현재는 `devbot:blocked` 전이 자체가 재시도를
  구조적으로 막는 방식으로 "무한 재시도 없음"을 보장한다.
- `current_branch_compatibility` 체크는 `default_branch` 또는 `devbot/*`
  접두어만 "정상"으로 인정한다. 이 저장소처럼 사람이 `task/*` 이름의
  작업 branch를 쓰는 워크플로에서는 항상 WARNING이 뜬다(위 `doctor` 실행
  결과 참고) - 의도적으로 fatal이 아니게 설계했으므로 daemon 동작에는
  영향이 없지만, 향후 `task/*`도 정상 패턴에 포함할지는 별도 논의가
  필요하다.
- 공유 운영 배포(`hjlee83/devbot`)를 대상으로 한 진짜 non-dry-run
  `uv run devbot --once`는 실행하지 않았다(위 "관련 제약과 대안 검증"
  참고) - 이는 이 Task의 코드 동작에 대한 위험이 아니라 검증 방법론의
  의도적 제약이다.

## 리뷰 피드백 반영 (PR #36, `hjlee83` REQUEST CHANGES, head `df343f3` 리뷰)

`hjlee83`의 `REQUEST CHANGES` 리뷰(Task Contract/Checkpoints/Validation
Gate/Required Tests/Result Document/PR Evidence/CI/Regression/
Documentation 9개 항목 모두 FAIL로 지적됨, 근본 원인은 동일한 CI 실패
하나)를 반영했다.

- **Blocker — CI 실패 (`uv run pytest`가 최신 head에서 1개 실패)**:
  GitHub Actions(Linux) 러너의 `git init` 기본 branch가 `master`인데
  `tests/test_startup.py::_init_git_repo`가 초기 branch 이름을 명시적으로
  고정하지 않아, 로컬(macOS, 기본 `main`)에서는 우연히 통과하고 CI에서는
  `test_startup_validation_passes_for_clean_enabled_repository`가
  `current_branch_compatibility` 체크에서 `ok=False`로 실패했다(`_repo()`
  기본값 `default_branch="main"`과 실제 branch `master`가 불일치).
  - **수정**: `_init_git_repo()`가 첫 commit 직후 `git branch -m main`으로
    항상 `main`으로 정규화하도록 `tests/test_startup.py`,
    `tests/test_doctor.py`(같은 잠재 버그를 가진 동일 헬퍼, 아직 어떤
    assertion도 이를 걸리게 하지 않았지만 일관성 있게 함께 수정) 두
    파일을 고쳤다. `src/devbot/startup.py`의 실제 구현
    (`check_current_branch_compatibility`)은 변경하지 않았다 - 버그는
    테스트 픽스처의 환경 의존성이었지 구현 로직이 아니었다.
  - **검증**: `git -c init.defaultBranch=master init` + `git branch -m
    main`으로 CI와 동일한 조건을 로컬에서 재현해 수정이 실제로 동작함을
    직접 확인했다. `uv run pytest` 303 passed(회귀 없음).
  - **CP-019-4/10 영향**: 테스트 이름/범위는 전혀 바뀌지 않았다 - 같은
    필수 테스트(`test_startup_validation`)와 같은 checkpoint 커버리지를
    유지한 채 픽스처만 환경 독립적으로 고쳤다.
- **Result 문서/PR Evidence 최신화**: 리뷰가 지적한 대로, 최초 PR Evidence는
  로컬에서만 확인한 `303 passed`를 CI 결과 확인 없이 기록했다 - 이번에
  로컬/CI 양쪽 결과를 구분해 기록하도록 이 문서와 PR #36 본문을 갱신했다.

## Improvement Suggestions

- `decide_retry()`가 만드는 재시도 결정을 실제로 cycle 간에 소비하는
  상태 저장소(예: Issue별 최근 실패 분류/시도 횟수를 GitHub 댓글 marker나
  로컬 파일에 기록)를 후속 Task로 추가하면, `delivery_failed`/
  `github_api_error`의 "제한된 횟수 내 자동 재시도"를 daemon이 실제로
  스스로 재시도하는 형태로 발전시킬 수 있다.
- `current_branch_compatibility`가 `task/*` 같은 조직 고유의 브랜치 네이밍
  컨벤션도 허용 목록에 추가할 수 있도록 `RepositoryConfig`에 선택적
  설정(예: `compatible_branch_prefixes`)을 추가하는 방안을 검토한다.
- `devbot doctor`에 `--json` 출력 옵션을 추가하면 사람이 읽는 텍스트
  보고서 대신 자동화된 헬스체크(launchd/systemd 상태 점검, 모니터링 도구
  연동)에서 파싱하기 쉬워진다 - Phase 3 로드맵의 macOS launchd/VPS systemd
  마이그레이션(`docs/00-roadmap.md`)과 자연스럽게 이어진다.
- 진단 보고서(`DiagnosticReport`)를 로그뿐 아니라 `devbot:blocked` 댓글
  본문에도 구조화된 형태로 포함시키면(현재는 session-limit만 특별
  처리), 운영자가 GitHub Issue만 보고도(로그에 접근하지 않고) 실패
  분류/재시도/복구 권장을 바로 확인할 수 있다.
