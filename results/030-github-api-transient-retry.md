# Task 030: GitHub API Transient Retry Result

## 완료 내용

- GitHub read/write 클라이언트가 공유하는 `devbot.github_retry` 정책을 추가했다.
- HTTP 429, 500, 502, 503, 504와 `requests.Timeout`, `requests.ConnectionError`를 transient로 분류하고 bounded exponential backoff + jitter로 재시도한다.
- GitHub `Retry-After`가 있으면 계산된 backoff보다 우선 적용하되 `max_delay_seconds`를 넘지 않게 제한한다.
- 401/403은 인증/권한 오류, 404는 not found로 유지했다.
- transient GitHub 실패가 polling claim/read 단계에서 `blocked`/`manual-action` 라벨 전이로 이어지지 않도록 `GitHubTransientError`를 별도 처리했다.
- retry 진단 로그는 status, attempt, delay, endpoint category, outcome, error type만 기록하고 Authorization/token은 기록하지 않는다.

## 주요 설계 결정

- read/write 클라이언트의 HTTP 메서드 래퍼에서 같은 `execute_with_github_retry()`를 호출하도록 해 정책 drift를 막았다.
- retry 설정은 `GitHubRetryConfig`로 주입 가능하게 해 테스트에서 sleep과 random을 deterministic하게 대체했다.
- transient exhaustion은 `GitHubTransientError`로 표면화해 polling/reliability가 GitHub API 계열 실패로 다루되 상태 mutation은 만들지 않게 했다.

## 수정 파일

- `src/devbot/github_retry.py`
- `src/devbot/github_client.py`
- `src/devbot/github_write_client.py`
- `src/devbot/polling.py`
- `tests/test_github_client.py`
- `tests/test_polling.py`
- `tests/test_timeline.py`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-030-1 transient classification | `test_github_failure_classification_distinguishes_transient_and_permanent_errors` |
| CP-030-2 bounded retry | `test_github_transient_failure_retries_with_bounded_backoff` |
| CP-030-3 Retry-After | `test_github_retry_after_header_is_honored` |
| CP-030-4 read/write parity | `test_github_read_and_write_clients_share_retry_policy` |
| CP-030-5 state preservation | `test_transient_github_failure_preserves_task_state` |
| CP-030-6 idempotent recovery | `test_github_retry_recovery_does_not_duplicate_side_effects` |
| CP-030-7 safe diagnostics | `test_github_retry_diagnostics_are_structured_and_redacted` |
| CP-030-8 workflow compatibility | `test_existing_workflows_remain_compatible_with_github_retry`, full `uv run pytest` |

## Validation 결과

- `uv sync` PASS
- `uv run ruff check .` PASS
- `uv run pytest` PASS: 447 passed
- `uv run devbot doctor` FAIL: `/tmp/devbot.lock`이 다른 프로세스에 의해 점유되어 `daemon_lock`만 실패. GitHub connectivity와 workspace checks는 OK.
- `uv run devbot --once --dry-run` FAIL: 같은 `/tmp/devbot.lock` 점유로 시작하지 못함.

## 수동 검증 결과

- 429, 500, 502, 503, 504는 deterministic unit test로 transient retry를 검증했다.
- timeout/connection failure 분류는 `classify_github_failure()`에서 `requests.Timeout`, `requests.ConnectionError`를 transient로 다루도록 구현했다.
- 401/403/404는 retry 대상이 아니며 각각 인증/권한, not found로 구분된다.
- 실제 sleep이나 외부 네트워크에 의존하지 않도록 retry sleep/random은 테스트에서 주입했다.

## 남은 TODO와 제한

- 현재 작업 환경의 `/tmp/devbot.lock` 점유가 해제된 뒤 `uv run devbot doctor`와 `uv run devbot --once --dry-run`을 재실행해야 한다.
- GitHub PR 본문 Evidence 갱신은 이 환경 지시상 네트워크/`gh` 사용이 금지되어 로컬 Result에만 기록했다.

## 위험 요소

- transient failure가 claim 이후 downstream write에서 발생하면 현재 cycle은 보류되고 다음 poll cycle의 GitHub 최신 상태를 기준으로 복구한다. 이 동작은 중복 side effect 방지에는 안전하지만, 기존에 이미 성공한 원격 write가 있었다면 다음 cycle이 최신 label state를 다시 읽는 것이 중요하다.

## Improvement Suggestions

- retry diagnostics를 observability의 JSON/structured event 스키마로 승격하면 운영 로그 필터링이 쉬워진다.
- `GitHubRetryConfig`를 config/env로 노출하면 운영 환경별 attempt/delay tuning이 가능하다.
