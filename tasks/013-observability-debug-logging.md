# Task 013: 운영 관찰성 및 디버그 로깅

Version: 1.0.0
Last Updated: 2026-07-14

## 목표

DevBot 데몬이 왜 작업을 선택했거나 선택하지 않았는지 운영자가 로그만으로 판단할 수 있도록 시작 구성, 관리 저장소, GitHub 검색 조건, 후보 수집, 스케줄러 선택·제외 사유, 작업 실행 시간과 실패 원인을 구조화해 기록한다.

기본 로그는 운영에 필요한 핵심 정보만 남기고, `LOG_LEVEL=DEBUG` 또는 `--verbose`에서는 검색·선택 판단의 상세 근거를 출력한다.

## 배경

Task 012까지 구현·리뷰·rework 자동 릴레이와 병렬 스케줄러가 완성됐지만, 실제 데몬 실행 중 `관리 저장소 수: 0`, `ready 상태 Issue 수: 0`처럼 결과만 출력되어 원인이 저장소 설정인지, 라벨 검색인지, 후보 제외인지 구분하기 어렵다.

향후 자동화가 안정적으로 운영되려면 다음 질문에 로그가 답해야 한다.

- 데몬은 어떤 설정과 버전으로 시작했는가?
- 어떤 저장소를 관리 대상으로 읽었는가?
- 어떤 상태 라벨과 조건으로 Issue를 검색했는가?
- 검색 결과 중 어떤 후보가 왜 제외됐는가?
- 어떤 Job이 왜 선택됐는가?
- Agent와 GitHub 단계는 각각 얼마나 걸렸는가?
- 관리 저장소가 0개일 때 폴링을 건너뛴 이유가 무엇인가?

## Dependencies

- Task 003: 전역 Queue 선택
- Task 005: 폴링 메인 루프
- Task 011: implementer/reviewer 역할 설정
- Task 012: 역할 기반 Job 스케줄러와 자동 리뷰

## 구현 범위

### 포함

#### 1. 로그 설정

- `LOG_LEVEL` 환경변수를 추가한다.
- 허용값은 `DEBUG`, `INFO`, `WARNING`, `ERROR`이며 대소문자를 구분하지 않는다.
- 기본값은 `INFO`다.
- 알 수 없는 값은 설정 로딩 단계에서 `ConfigError`로 실패한다.
- CLI에 `--verbose`를 추가하고, 지정 시 해당 실행에서만 DEBUG 수준을 활성화한다.
- 토큰, API Key, 인증 헤더, 전체 환경변수 값은 로그에 출력하지 않는다.

#### 2. 시작 로그

데몬 시작 시 한 번만 다음 정보를 INFO 수준으로 기록한다.

- 실행 버전 또는 Git commit 식별값(확인 가능한 경우)
- implementer 역할 설정값
- reviewer 역할 설정값
- dry-run 여부
- poll interval
- `MAX_CONCURRENT_JOBS`
- 관리 저장소 수
- 관리 저장소의 `owner/name` 목록
- 저장소별 local path와 default branch

관리 저장소가 0개이면 다음을 명확히 기록하고 해당 cycle을 부작용 없이 건너뛴다.

- `no_managed_repositories`
- 설정 파일 또는 환경 설정을 확인하라는 복구 안내

#### 3. 폴링 Cycle 로그

각 cycle마다 고유한 `cycle_id`를 생성하고 다음을 기록한다.

INFO:

- cycle 시작/종료
- 총 소요 시간
- 수집 후보 수: REWORK, REVIEW, IMPLEMENT별
- 선택 Job 수와 사용 가능한 concurrency slot 수
- 실행 결과 요약

DEBUG:

- 저장소별 검색 시작/종료
- 검색 상태 라벨과 필터 조건
- GitHub API에 전달하는 논리적 검색 조건
- 검색 결과 수
- 후보 Issue 번호, PR 번호, JobType
- 제외된 후보와 제외 사유
- 최종 선택 순서와 선택 근거

GitHub 토큰이나 인증 헤더는 어떤 수준에서도 출력하지 않는다.

#### 4. 표준 제외 사유

후보 제외 사유는 자유 문자열 대신 최소 다음 코드 중 하나로 구조화한다.

- `repository_busy`
- `issue_busy`
- `concurrency_limit`
- `missing_linked_pr`
- `missing_pr_head`
- `already_reviewed_head`
- `no_unprocessed_feedback`
- `not_ready`
- `lower_priority`
- `dry_run`

필요한 경우 새 코드를 추가할 수 있으나 테스트와 문서에 반영해야 한다.

#### 5. Job 실행 로그

각 Job 시작/종료 시 다음을 기록한다.

- `cycle_id`
- repository
- issue number
- PR number(존재하는 경우)
- JobType
- Agent 역할
- 시작 시각
- 종료 상태
- 총 소요 시간

DEBUG에서는 Agent 실행, 검증, Commit, Push, PR/댓글 쓰기 등 주요 단계별 소요 시간을 기록한다.

Agent stdout/stderr 전체는 INFO에 출력하지 않는다. 실패 시에는 비밀값을 노출하지 않는 범위에서 마지막 오류 요약과 구조화된 실패 코드를 기록한다.

#### 6. 테스트 가능한 로깅 구조

- 로그 메시지 조립과 후보 진단 정보 생성은 가능한 한 순수 함수 또는 구조화된 모델로 분리한다.
- 테스트는 문자열 전체 비교보다 필수 필드와 이벤트 코드 중심으로 검증한다.
- 기존 `PollingResult`와 Job 실행 동작은 변경하지 않는다.
- 로깅 실패가 Job 실행 자체를 중단시키지 않도록 한다.

#### 7. 문서

- `README.md`에 `LOG_LEVEL`과 `--verbose` 사용법을 추가한다.
- `docs/08-beta-runbook.md`에 운영 진단 절차를 추가한다.
- `docs/00-roadmap.md`에서 Task 013을 완료 상태로 갱신한다.
- `results/013-observability-debug-logging.md`를 작성한다.

### 제외

- 외부 로그 수집 서비스, OpenTelemetry, Prometheus, Grafana 연동
- 웹 대시보드
- 로그 파일 rotation 또는 장기 보관 정책
- 자동 alert 전송
- `devbot:working` 고아 상태 자동 복구
- GitHub API 호출 재시도 정책 변경
- 자동 Merge 정책 변경

## 동작 규칙

1. 기본 INFO 로그는 정상 운영에 필요한 요약만 출력한다.
2. DEBUG 로그는 검색 조건, 후보, 제외 사유와 단계별 시간을 출력한다.
3. Secret 또는 인증 정보는 로그에 절대 출력하지 않는다.
4. 관리 저장소 0개는 정상적인 `no_ready_task`로 뭉개지 않고 `no_managed_repositories`로 구분한다.
5. 같은 cycle의 로그는 동일한 `cycle_id`로 연관할 수 있어야 한다.
6. 기존 자동화의 상태 전이, 우선순위, Agent 선택, concurrency 동작은 바꾸지 않는다.
7. `--verbose`는 영구 설정을 변경하지 않고 현재 프로세스에만 적용한다.

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-013-1 | 기본 로그 수준은 INFO다 | `test_log_level_defaults_to_info` |
| CP-013-2 | 알 수 없는 LOG_LEVEL은 설정 단계에서 거부한다 | `test_invalid_log_level_is_rejected` |
| CP-013-3 | `--verbose`가 현재 실행을 DEBUG로 전환한다 | `test_verbose_flag_enables_debug_logging` |
| CP-013-4 | 시작 로그에 역할, dry-run, concurrency, 관리 저장소 목록이 포함된다 | `test_startup_log_contains_runtime_configuration` |
| CP-013-5 | 관리 저장소가 0개면 명확한 진단 이벤트를 남기고 cycle을 건너뛴다 | `test_zero_managed_repositories_logs_diagnostic_and_skips_polling` |
| CP-013-6 | DEBUG 로그에 저장소별 검색 조건과 결과 수가 포함된다 | `test_debug_log_contains_repository_search_diagnostics` |
| CP-013-7 | 제외 후보에 구조화된 제외 사유가 기록된다 | `test_scheduler_logs_structured_candidate_exclusion_reason` |
| CP-013-8 | 선택된 Job에 cycle/repository/issue/job type 정보가 기록된다 | `test_selected_job_log_contains_correlation_fields` |
| CP-013-9 | Job 및 주요 단계의 소요 시간이 기록된다 | `test_job_log_records_elapsed_time` |
| CP-013-10 | Secret과 인증 헤더가 로그에 노출되지 않는다 | `test_logs_do_not_expose_secrets_or_authorization_headers` |
| CP-013-11 | INFO 수준에서는 상세 검색 진단이 출력되지 않는다 | `test_info_logging_omits_debug_search_details` |
| CP-013-12 | 로깅 예외가 Job 실행 결과를 변경하지 않는다 | `test_logging_failure_does_not_abort_job_execution` |

추가로 Happy Path, Failure Path, Boundary Condition을 포함하고 기존 Task 012 스케줄러 및 폴링 테스트가 모두 통과해야 한다.

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
uv run devbot --once --dry-run --verbose
```

## Files Expected to Change

- `src/devbot/config.py`
- `src/devbot/main.py`
- `src/devbot/polling.py`
- `src/devbot/scheduler.py`
- `src/devbot/models.py`
- `src/devbot/logging.py` 또는 동등한 신규 모듈
- `tests/test_config.py`
- `tests/test_main_loop.py`
- `tests/test_polling.py`
- `tests/test_scheduler.py`
- 신규 logging 테스트 파일
- `README.md`
- `docs/08-beta-runbook.md`
- `docs/00-roadmap.md`
- `results/013-observability-debug-logging.md`

예상 목록이며 실제 구조를 확인해 필요한 최소 파일만 수정한다.

## Risk

- DEBUG 로그가 지나치게 많아 운영 로그 가독성을 떨어뜨릴 수 있다.
- Agent 오류 메시지에 민감 정보가 포함될 수 있어 sanitize 정책이 필요하다.
- 시간 측정과 로그 코드가 핵심 실행 흐름에 과도하게 침투할 수 있다.
- 문자열 기반 테스트가 구현 세부사항에 취약해질 수 있다.

## Rollback Strategy

- 로깅 변경은 기존 상태 전이와 Job 실행 인터페이스를 유지한다.
- 문제 발생 시 신규 logging 모듈, `LOG_LEVEL`, `--verbose` 배선을 되돌리고 기존 INFO 메시지로 복구한다.
- 데이터 또는 GitHub 상태 스키마 변경은 없으므로 별도 데이터 롤백은 필요 없다.

## Reviewer Focus

- DEBUG와 INFO의 정보량 경계가 적절한지
- Secret, token, Authorization header가 어떤 오류 경로에서도 노출되지 않는지
- `no_managed_repositories`와 `no_ready_task`가 명확히 구분되는지
- 후보 제외 사유가 실제 scheduler 판단과 일치하는지
- 로깅이 기존 Task 012의 우선순위와 concurrency 동작을 변경하지 않는지
- 로그 실패가 작업 실패로 전파되지 않는지

## Definition of Done

- CP-013-1~12 필수 테스트가 모두 존재하고 통과한다.
- 전체 기존 테스트가 통과한다.
- 시작 로그에서 실제 관리 저장소와 실행 구성을 확인할 수 있다.
- DEBUG 모드에서 검색 조건, 후보 수, 제외 사유, 선택 Job을 추적할 수 있다.
- 관리 저장소가 0개일 때 명확한 진단 로그가 출력된다.
- 로그에 Secret과 인증 정보가 포함되지 않는다.
- Result와 운영 문서가 최신 상태다.
- 구현 PR이 생성되고 실행 Issue가 `devbot:review`로 전환된다.

## Result 문서 경로

`results/013-observability-debug-logging.md`
