# Task 145 Result: Resolve Startup Self-Update Checkout Independently of CWD

## 완료 내용

- `resolve_operator_checkout()` 기본 resolution을 `Path.cwd()`에서
  `DEVBOT_OPERATOR_CHECKOUT`, `DEVBOT_PROJECT_ROOT`, DevBot module path 후보로
  변경했다.
- runtime directory가 Git 저장소가 아니어도 DevBot source checkout을 기준으로
  startup self-update가 동작하게 했다.
- checkout resolution 실패를 uncaught `RuntimeError`가 아니라
  `StartupSelfUpdateError`와 `reason_code=checkout_resolution_failed`로 보고하게
  했다.
- 기존 dirty checkout, wrong branch, fetch, switch, ff-only pull safety gate는
  resolved checkout에 그대로 적용된다.
- README, beta runbook, repository registration 문서에 operator checkout resolution
  정책을 추가했다.

## 주요 설계 결정

- 기본 resolution에서 process CWD를 후보로 사용하지 않는다. runtime directory나
  managed repository를 operator checkout으로 오인하지 않기 위해서다.
- 명시 경로가 필요한 배포/서비스 매니저는 `DEVBOT_OPERATOR_CHECKOUT`을 사용할 수
  있고, 기존 release 계열에서 쓰던 `DEVBOT_PROJECT_ROOT`도 fallback으로 유지했다.
- Git checkout이 없는 미래 packaged install은 명확한 diagnostic으로 실패한다.

## 수정 파일

- `src/devbot/startup.py`
- `tests/test_startup_self_update.py`
- `tests/test_main.py`
- `README.md`
- `docs/08-beta-runbook.md`
- `docs/19-repository-registration.md`
- `tasks/145-cwd-independent-self-update.md`
- `results/145-cwd-independent-self-update.md`

## Checkpoint Evidence

- Non-Git runtime CWD:
  `test_startup_resolves_operator_checkout_from_module_when_cwd_is_not_git`,
  `test_daemon_dry_run_from_unrelated_runtime_dir_resolves_operator_checkout`
- Controlled failure:
  `test_startup_checkout_resolution_failure_is_controlled`
- Explicit operator checkout:
  `test_resolve_operator_checkout_prefers_explicit_env_over_cwd`
- Existing self-update safety:
  `test_startup_rejects_dirty_main_checkout`,
  `test_startup_failure_prevents_doctor_planner_and_agent_execution`

## Validation 결과

- `uv run ruff check src/devbot/startup.py tests/test_startup_self_update.py tests/test_main.py`
  - PASS
- `uv run pytest tests/test_startup_self_update.py tests/test_main.py::test_daemon_dry_run_from_unrelated_runtime_dir_resolves_operator_checkout tests/test_main.py::test_daemon_run_from_unrelated_runtime_dir_uses_registry_without_legacy_config`
  - PASS, 14 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1429 passed in 129.60s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: dirty checkout은 dry-run에서 skip하고 registry repository 2개를 로드한 뒤
    `NO_RUNNABLE_TASK`.

## 수동 검증

- `test_daemon_dry_run_from_unrelated_runtime_dir_resolves_operator_checkout`에서
  process CWD를 non-Git runtime directory로 바꾸고 startup self-update를 mock하지 않은
  상태로 `--once --dry-run --verbose`가 polling까지 도달함을 확인했다.
- `test_startup_checkout_resolution_failure_is_controlled`에서 Git checkout을 찾지 못하는
  packaged-install 형태가 uncaught traceback 대신 controlled startup result를 반환함을
  확인했다.

## 남은 TODO와 제한

- 없음.

## 위험 요소

- non-Git packaged install은 아직 self-update redesign 범위 밖이므로 명시 diagnostic으로
  실패한다.

## Improvement Suggestions

- packaged install용 self-update skip policy를 별도 Task에서 명시적으로 설계한다.
