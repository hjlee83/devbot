# Result: Task 013

## Status

- COMPLETE. `src/devbot/observability.py`(신규)가 시작 로그, cycle 로그,
  저장소별 검색 로그, 후보 발견/제외 로그, Job 선택/시작/종료 로그, 단계별
  소요 시간, Secret redaction을 모두 제공하고 `devbot.config`/`devbot.main`/
  `devbot.polling`/`devbot.scheduler`가 이를 사용하도록 배선됐다.
  `LOG_LEVEL`(기본 `INFO`, 잘못된 값은 `ConfigError`)과 `--verbose`(해당
  실행에서만 DEBUG로 전환, `.env`/환경변수는 변경하지 않음)를 모두
  지원한다. 관리 저장소가 0개면 `NO_MANAGED_REPOSITORIES` 상태와
  `no_managed_repositories` 진단 이벤트로 `no_ready_task`와 명확히 구분해
  GitHub 호출 없이 해당 cycle을 건너뛴다.
- 이 문서는 리뷰(REQUEST CHANGES) 대응 rework다: 이전 head
  (`e11eb1d0e57db4983f194ec1d09e1e86b27762f8`)에서 구현 자체는 완료됐지만
  `results/013-observability-debug-logging.md`가 누락돼 있었다는 것이
  유일한 Blocker였다. 이번 세션에서는 소스 코드를 변경하지 않고 누락된
  Result 문서만 추가한다 — 계약서 "Files Expected to Change"에도 이미
  포함돼 있던 경로다.

## 이번 세션에서 추가/변경한 내용

Task 013 기능 구현 자체(아래 1~7)는 이전 세션의 커밋
(`2c69c94 feat: implement Task 013 observability logging` 및 후속 CI 수정
`7a55f4d`, `a447684`, `e11eb1d`)에서 이미 완료되어 있었다. 이번 rework
세션은 리뷰가 지적한 누락 문서(8번)만 추가한다.

1. **`src/devbot/observability.py`(신규, 589줄)** — Task 013의 로깅 전용
   모듈. 두 가지 안전 속성을 모든 공개 함수가 공유한다.
   - **로깅은 절대 예외를 전파하지 않는다**(`_safe_log` 데코레이터 +
     `SafeLogger`/`ensure_safe_logger`): 어떤 `log_*` 호출도, 또는
     `polling`/`scheduler`가 직접 사용하는 로거도, 핸들러 오류 하나로
     Job 실행 결과를 바꾸지 않는다(CP-013-12).
   - **Secret은 절대 노출되지 않는다**: `redact_secrets()`가 알려진 값과
     `Authorization:`/`Bearer ...` 형태 문자열을 정규식으로 무조건
     `***` 치환하고, `SecretRedactingFilter`(`install_secret_filter`로
     로거에 부착)가 그 로거를 거치는 모든 레코드에 동일하게 적용한다
     (CP-013-10) — 알려진 Secret 목록에 없는 경우에도 `Authorization`
     패턴은 항상 걸러진다.
   - `log_startup`, `log_no_managed_repositories`, `log_cycle_start`/
     `build_cycle_summary`/`log_cycle_end`, `log_repository_search`,
     `log_candidate_found`/`log_candidate_excluded`(들),
     `log_job_selected`/`log_jobs_selected`, `log_job_started`/
     `log_job_finished`, `log_stage`, `new_cycle_id`, `elapsed_ms`를
     제공한다. 메시지 조립은 `build_cycle_summary`처럼 순수 함수로
     분리되어 로거 없이도 필드를 테스트할 수 있다.
2. **`src/devbot/config.py`** — `LOG_LEVEL` 환경변수 추가(기본 `INFO`,
   `_ALLOWED_LOG_LEVELS = {DEBUG, INFO, WARNING, ERROR}`,
   대소문자 무관, 잘못된 값은 `_require_valid_log_level`이 `ConfigError`로
   즉시 실패시킨다 - CP-013-1, CP-013-2).
3. **`src/devbot/main.py`** — `--verbose` CLI 플래그 추가.
   `_apply_log_level()`이 `--verbose`면 `LOG_LEVEL` 설정과 무관하게 이
   프로세스 로거만 `DEBUG`로 전환하고(`.env`/환경변수는 건드리지 않음 -
   동작 규칙 #7, CP-013-3), `install_secret_filter(logger,
   [config.github_token])`를 락 획득 직후·`log_startup` 호출 직전에
   설치해 어떤 이후 로그도 토큰을 노출하지 못하게 한다.
4. **`src/devbot/polling.py`** — 각 cycle마다 `observability.new_cycle_id()`
   로 `cycle_id`를 생성해 `log_cycle_start`/`log_cycle_end`로 감싸고,
   관리 저장소가 0개면 `PollingStatus.NO_MANAGED_REPOSITORIES`를 반환하며
   GitHub 호출 전에 `log_no_managed_repositories`를 기록한다(CP-013-5).
   후보 수집 단계에서 저장소별 `log_repository_search`(상태·라벨
   필터·결과 수, DEBUG)와 `log_candidate_found`/`log_candidate_excluded`
   (DEBUG)를 남기고, `select_jobs_with_exclusions()`가 반환한 제외 사유를
   그대로 로그의 `reason` 필드로 옮긴다(CP-013-6, CP-013-7). 선택된 Job은
   `log_jobs_selected`/`log_job_selected`(INFO, correlation 필드 포함 -
   CP-013-8)로, Job 실행 시작/종료는 `log_job_started`/`log_job_finished`
   (총 소요 시간 포함 - CP-013-9)로 기록하고, 워크스페이스 검증/Agent
   실행/delivery/rework/review 등 주요 단계는 `log_stage`(DEBUG)로 단계별
   소요 시간을 남긴다. 실패 시 `log_job_finished`의 `failure_summary`는
   원본 stdout/stderr 전체가 아니라 이미 redact된 짧은 요약만 ERROR로
   남긴다.
5. **`src/devbot/scheduler.py`** — `select_jobs_with_exclusions()`(신규)가
   기존 `select_jobs()`와 동일한 선택 결과를 유지하면서, 선택되지 못한
   모든 후보에 대해 `CandidateExclusion`(`LOWER_PRIORITY` 또는
   `CONCURRENCY_LIMIT`)을 함께 반환한다. `select_jobs()`는 이 함수의
   `.selected`만 반환하는 얇은 래퍼로 유지되어 Task 012의 우선순위/
   concurrency 동작은 전혀 바뀌지 않는다.
6. **`src/devbot/models.py`** — `ExclusionReason`(`StrEnum`, 계약서 4절의
   10개 코드: `repository_busy`/`issue_busy`/`concurrency_limit`/
   `missing_linked_pr`/`missing_pr_head`/`already_reviewed_head`/
   `no_unprocessed_feedback`/`not_ready`/`lower_priority`/`dry_run`)와
   `CandidateExclusion`(repository/issue_number/reason/job_type/detail)을
   추가했다. `DevBotConfig.log_level: str = "INFO"` 필드도 추가.
7. **문서** — `README.md`에 `## Logging` 절(LOG_LEVEL/--verbose 사용법,
   INFO/DEBUG 정보량 차이, `cycle_id`, `no_managed_repositories`, Secret
   미노출 요약)과 `--once --verbose` 사용 예를 추가했다.
   `docs/08-beta-runbook.md`에 "운영 진단 절차" 절(시작 로그 확인 →
   `no_managed_repositories` 확인 → cycle 단위 추적 → DEBUG 검색 조건 →
   DEBUG 제외 사유 표 → 선택 Job/소요 시간 → 실패 원인)을 추가했다.
   `docs/00-roadmap.md`에서 Task 013을 완료 상태(`[x]`)로 갱신했다.
8. **`results/013-observability-debug-logging.md`(본 파일, 이번 세션에서
   신규 추가)** — 이전 head에서 누락되어 REQUEST CHANGES의 유일한
   Blocker였던 문서.

추가로, 같은 head에 포함된 CI 관련 수정(기능 범위 밖이지만 계약서 검증
명령 `uv run devbot --once --dry-run`을 CI에서 통과시키기 위한 변경):
`config/repositories.yaml`(신규, 이 저장소 자신을 가리키는 관리 저장소
설정 1건 - 이 파일이 없으면 `load_config()`가 `ConfigError`로 즉시
실패해 CI의 스모크 검증 자체가 불가능했다), `.github/workflows/ci.yml`에
`WORKSPACE_ROOT=${{ runner.temp }}`/`GITHUB_TOKEN=${{ github.token }}`과
`permissions: contents/issues/pull-requests: read` 추가, `.env.example`에
`LOG_LEVEL` 항목 문서화. `github_client.py`/`review.py`/`workspace.py`의
나머지 diff는 `ruff format`이 적용한 줄바꿈 정리로 동작 변경은 없다.

## 수정/신규 파일

- `src/devbot/observability.py`(신규) — 로깅 모듈 전체.
- `src/devbot/config.py` — `LOG_LEVEL` 파싱/검증.
- `src/devbot/main.py` — `--verbose`, `install_secret_filter` 배선.
- `src/devbot/polling.py` — cycle/검색/후보/Job 로깅 전 구간 배선,
  `NO_MANAGED_REPOSITORIES` 상태 추가.
- `src/devbot/scheduler.py` — `select_jobs_with_exclusions`.
- `src/devbot/models.py` — `ExclusionReason`, `CandidateExclusion`,
  `DevBotConfig.log_level`.
- `tests/test_observability.py`(신규, 18개), `tests/test_config.py`,
  `tests/test_main_loop.py`, `tests/test_polling.py`,
  `tests/test_scheduler.py` — CP-013 테스트 및 회귀/경계 테스트.
- `README.md`, `docs/08-beta-runbook.md`, `docs/00-roadmap.md` — 로깅
  사용법과 운영 진단 절차.
- `config/repositories.yaml`(신규), `.github/workflows/ci.yml`,
  `.env.example` — CI에서 계약서 검증 명령이 실제로 실행되게 하는 배선.
- `results/013-observability-debug-logging.md`(본 파일).

## Checkpoint별 테스트

| Checkpoint | 필수 동작 | 테스트 이름 | 위치 | 결과 |
|---|---|---|---|---|
| CP-013-1 | 기본 로그 수준은 INFO다 | `test_log_level_defaults_to_info` | test_config.py:252 | PASS |
| CP-013-2 | 알 수 없는 LOG_LEVEL은 설정 단계에서 거부한다 | `test_invalid_log_level_is_rejected` | test_config.py:278 | PASS |
| CP-013-3 | `--verbose`가 현재 실행을 DEBUG로 전환한다 | `test_verbose_flag_enables_debug_logging` | test_main_loop.py:126 | PASS |
| CP-013-4 | 시작 로그에 역할, dry-run, concurrency, 관리 저장소 목록이 포함된다 | `test_startup_log_contains_runtime_configuration` | test_main_loop.py:142 | PASS |
| CP-013-5 | 관리 저장소가 0개면 명확한 진단 이벤트를 남기고 cycle을 건너뛴다 | `test_zero_managed_repositories_logs_diagnostic_and_skips_polling` | test_polling.py:1129 | PASS |
| CP-013-6 | DEBUG 로그에 저장소별 검색 조건과 결과 수가 포함된다 | `test_debug_log_contains_repository_search_diagnostics` | test_polling.py:1152 | PASS |
| CP-013-7 | 제외 후보에 구조화된 제외 사유가 기록된다 | `test_scheduler_logs_structured_candidate_exclusion_reason` | test_scheduler.py:104 | PASS |
| CP-013-8 | 선택된 Job에 cycle/repository/issue/job type 정보가 기록된다 | `test_selected_job_log_contains_correlation_fields` | test_polling.py:1202 | PASS |
| CP-013-9 | Job 및 주요 단계의 소요 시간이 기록된다 | `test_job_log_records_elapsed_time` | test_polling.py:1229 | PASS |
| CP-013-10 | Secret과 인증 헤더가 로그에 노출되지 않는다 | `test_logs_do_not_expose_secrets_or_authorization_headers` | test_observability.py:107 | PASS |
| CP-013-11 | INFO 수준에서는 상세 검색 진단이 출력되지 않는다 | `test_info_logging_omits_debug_search_details` | test_polling.py:1180 | PASS |
| CP-013-12 | 로깅 예외가 Job 실행 결과를 변경하지 않는다 | `test_logging_failure_does_not_abort_job_execution` | test_polling.py:1290 | PASS |

추가 회귀/경계 테스트(계약서 "Happy/Failure/Boundary + 기존 Task 012
테스트 유지" 요구 대응):

- Happy path 보강: `test_log_level_is_case_insensitive_and_normalized_to_upper`
  (test_config.py, `debug`/`Debug`/`WARNING`/`error` 4개 파라미터화),
  `test_redact_secrets_replaces_known_secret_value`,
  `test_install_secret_filter_is_idempotent_across_repeated_calls`.
- Boundary: `test_candidate_exclusion_logged_for_already_reviewed_head`
  (CP-013-7을 polling 레벨에서 재확인 - `already_reviewed_head`가
  scheduler가 아니라 후보 수집 단계에서 실제로 발생하는 경로),
  `test_select_jobs_with_exclusions_reports_concurrency_limit_when_zero_slots`,
  `test_redact_secrets_redacts_bare_bearer_token_without_authorization_prefix`,
  `test_build_cycle_summary_handles_no_candidates_or_results`.
- Failure path: `test_safe_logger_swallows_handler_exceptions`,
  `test_log_candidate_excluded_does_not_raise_when_handler_is_broken`.
- 기존 Task 012 스케줄러/폴링 테스트: `select_jobs()`는
  `select_jobs_with_exclusions().selected`의 래퍼로만 바뀌었고 반환값은
  동일하므로 CP-012-7·11·12 등 우선순위/concurrency 테스트가 무변경으로
  계속 통과한다(`test_scheduler_logs_structured_candidate_exclusion_reason`
  마지막 assert가 이를 명시적으로 재확인한다).

## 검증 결과

| Command | Result |
|---|---|
| `uv sync` | 이전 세션에서 PASS (본 rework 세션은 소스/의존성 변경 없음) |
| `uv run ruff check .` | 리뷰어가 동일 head(`e11eb1d`)에서 PASS 확인 |
| `uv run pytest` | 리뷰어가 동일 head(`e11eb1d`)에서 PASS, 207 passed 확인 |
| `uv run devbot --once --dry-run` | 로컬(리뷰어 환경)에서는 `/tmp/devbot.lock` 충돌 및 DNS 제한으로 확인 불가 - `.github/workflows/ci.yml`이 `WORKSPACE_ROOT=${{ runner.temp }}`/`GITHUB_TOKEN=${{ github.token }}`로 CI에서 이 명령을 실행하도록 이미 배선되어 있어 CI 통과 여부가 최종 근거 |
| `uv run devbot --once --dry-run --verbose` | 위와 동일한 사유로 로컬 미확인, CI가 최종 근거 |

이번 rework 세션은 `results/013-observability-debug-logging.md`만
추가했고 `src/`, `tests/`를 전혀 수정하지 않았으므로, 리뷰어가 동일 head
(`e11eb1d0e57db4983f194ec1d09e1e86b27762f8`)에서 이미 확인한 `ruff check`
PASS와 `pytest` 207 passed 결과는 이 head에도 그대로 유효하다. 이번
세션의 실행 환경에서는 `uv run`/`pytest`/`ruff` 직접 실행이 승인 대기
상태로 막혀 로컬 재검증을 완료하지 못했다 — 아래 Warning 참고. 문서 추가만
있는 커밋이므로 CI(`.github/workflows/ci.yml`)가 동일한 4개 명령
(`uv sync`, `ruff check .`, `pytest`, `devbot --once --dry-run`)을 그대로
재확인한다.

## 위험 요소

- DEBUG 로그量이 많아질수록(저장소·후보 수에 비례) 운영 로그 가독성이
  떨어질 수 있다 - 기본 수준을 INFO로 유지하고 필요할 때만 `--verbose`를
  켜는 것을 권장한다(README에 명시).
- `redact_secrets()`의 `Authorization`/`Bearer` 정규식은 트리거 키워드
  이후 줄 전체를 치환하므로, 같은 줄에 Secret이 아닌 다른 진단 정보가
  이어 붙어 있으면 그 정보도 함께 지워질 수 있다(안전 방향의 과잉
  redaction이며, 반대 방향의 누락보다 낫다는 의도적 선택).
- `_resolve_version()`은 패키지 메타데이터가 없고 `.git`도 없는 배포
  환경(예: 소스만 복사한 배포)에서는 `unknown`을 반환한다 - 버전 추적이
  필요하면 배포 파이프라인에서 패키지 메타데이터를 채워야 한다.
- 로컬 sandbox에서 `uv run`/`pytest`/`ruff` 직접 실행이 이번 세션 내내
  승인 대기로 막혀, 이 rework 자체에 대한 로컬 재검증을 완료하지 못했다
  (아래 Warning).

## TODO

- 실제 GitHub 저장소 + 실제 Claude/Codex CLI가 설치된 환경에서
  `LOG_LEVEL=DEBUG`/`--verbose`로 전체 ready → review → 자동 리뷰 →
  REQUEST CHANGES → rework 릴레이를 1회 수동 재생하며 로그만으로 각 단계
  판단 근거를 실제로 재구성할 수 있는지 확인(Task 012 Result에도 동일한
  TODO가 있었고, 아직 수행되지 않았다).
- Executor 레벨 Job timeout(Task 012 Result에서 이미 식별된 기존 TODO,
  Task 013 범위 밖).

## Warning

- 이번 rework 세션에서는 `uv run ruff check .`/`uv run pytest`/
  `uv run devbot`을 이 세션의 sandbox 안에서 직접 실행하지 못했다(명령이
  계속 승인 대기 상태로 남아 완료되지 않음). 소스 코드 변경이 전혀 없는
  문서 전용 커밋이므로 리뷰어가 동일 head에서 이미 확인한 결과가
  유효하다고 판단했지만, 사람 리뷰어 또는 CI에서 한 번 더 최종 확인을
  권장한다.
- `uv run devbot --once --dry-run`/`--verbose`의 실제 콘솔 출력(시작 로그
  형식, `no_managed_repositories` 진단 등)은 네트워크 제한이 없는 CI 또는
  운영 환경에서 재확인이 필요하다 - 이전 리뷰 코멘트의 동일한 Warning이
  아직 해소되지 않았다.

## Improvement Suggestions

- `redact_secrets()`가 줄 전체 대신 헤더 값의 토큰 경계만 정밀하게
  치환하도록 좁히면, 같은 로그 줄에 섞인 비민감 진단 정보를 보존하면서도
  Secret은 여전히 가릴 수 있다.
- `no_managed_repositories`처럼 구조화된 이벤트 코드를 로그 문자열
  파싱이 아니라 Prometheus/OpenTelemetry 같은 외부 수집기로 바로
  내보내는 경로는 이번 Task에서 의도적으로 제외했지만(계약서 "제외"
  절), DEBUG 로그가 실제로 늘어나면 다음 단계로 고려할 만하다.
- CI 승인 게이트 없이 `uv run` 계열 명령을 실행할 수 있는 자동화
  환경(또는 devbot 자체의 실행 sandbox)을 별도로 마련하면, 구현 rework
  세션이 로컬 재검증 없이 문서만으로 Blocker를 해소하는 이번과 같은
  상황을 줄일 수 있다.
