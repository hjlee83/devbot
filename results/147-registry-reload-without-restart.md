# Task 147 Result: Reload Repository Registry Without Restarting the Daemon

## 완료 내용

- `RegistryReloadMonitor`를 추가해 registry file metadata(`mtime`, size, inode)를
  polling 방식으로 추적한다.
- `PollingService`가 cycle 시작 전에 optional reload hook을 호출하고, 변경이 있을 때
  새 `DevBotConfig`를 적용한다.
- reload는 기존 `load_config()`를 사용하므로 legacy + registry merge, duplicate
  validation, registry diagnostics를 startup과 동일하게 재사용한다.
- reload 실패 시 예외를 polling cycle 밖으로 전파하지 않고 이전 valid config를 유지한다.
- runtime reload에서는 마지막 repository unregister로 인한 빈 repository set을 valid
  config로 받아들여 다음 cycle이 `NO_MANAGED_REPOSITORIES`로 전환되게 했다.
- reload 성공 시 added/removed/unchanged repository count와 repository names를
  로그로 남긴다.
- daemon main wiring에 `RegistryReloadMonitor.check`를 연결했다.
- repository registration 문서에 runtime registry reload 정책을 추가했다.

## 주요 설계 결정

- reload 시점은 polling-cycle boundary다. cycle 내 job execution은 cycle 시작 시점의
  `repositories` snapshot을 사용하므로 active job config를 중간에 바꾸지 않는다.
- `.env`/agent/provider reload는 범위 밖이지만, repository effective config는 기존
  `load_config()`를 통해 재구성해 startup과 같은 validation을 적용한다.
- malformed registry는 monitor에서 state를 관측한 뒤 reload 실패로 기록한다. 다음 파일
  metadata 변경 시 다시 reload를 시도한다.
- startup config load는 repository 없음 오류를 유지하고, runtime registry reload만 빈
  repository set을 허용한다.

## 수정 파일

- `src/devbot/registry_reload.py`
- `src/devbot/polling.py`
- `src/devbot/main.py`
- `tests/test_registry_reload.py`
- `tests/test_polling.py`
- `docs/19-repository-registration.md`
- `tasks/147-registry-reload-without-restart.md`
- `results/147-registry-reload-without-restart.md`

## Checkpoint Evidence

- Registry monitor:
  `test_registry_reload_monitor_reports_addition_and_removal`,
  `test_registry_reload_monitor_allows_last_repository_removal`,
  `test_registry_reload_monitor_reuses_legacy_duplicate_validation`,
  `test_registry_reload_monitor_retries_after_malformed_registry_changes`
- Polling integration:
  `test_polling_cycle_reloads_registry_addition_before_discovery`,
  `test_polling_cycle_removes_repository_after_registry_reload`,
  `test_polling_cycle_unregistered_last_repository_skips_previous_repo`,
  `test_polling_cycle_does_not_reload_when_registry_unchanged`,
  `test_polling_cycle_keeps_previous_config_when_registry_reload_fails`

## Validation 결과

- `uv run ruff check src/devbot/registry_reload.py src/devbot/polling.py src/devbot/main.py tests/test_registry_reload.py tests/test_polling.py`
  - PASS
- `uv run pytest tests/test_registry_reload.py tests/test_polling.py::test_polling_cycle_reloads_registry_addition_before_discovery tests/test_polling.py::test_polling_cycle_removes_repository_after_registry_reload tests/test_polling.py::test_polling_cycle_unregistered_last_repository_skips_previous_repo tests/test_polling.py::test_polling_cycle_does_not_reload_when_registry_unchanged tests/test_polling.py::test_polling_cycle_keeps_previous_config_when_registry_reload_fails`
  - PASS, 9 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1438 passed in 253.67s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: dirty checkout은 dry-run에서 skip하고 registry repository 2개를 로드한 뒤
    `NO_RUNNABLE_TASK`.

## 수동 검증

- `PollingService.run_cycle()` 시작 직전에 reload hook을 호출하므로 reload는 polling
  boundary에서만 발생한다.
- `_execute_jobs()`는 cycle 시작 시점의 local `repositories` snapshot을 인자로 받아
  실행하므로, reload가 job 실행 중간에 active job repository config를 바꾸지 않는다.
- `RegistryReloadMonitor`가 `load_config()`를 호출하는 것을 확인해 legacy merge,
  duplicate validation, registry diagnostics가 startup과 동일한 경로를 사용한다.

## 남은 TODO와 제한

- `.env`/agent/provider reload는 범위 밖이다.

## 위험 요소

- registry file metadata가 바뀌지 않는 외부 편집 방식은 reload trigger가 되지 않는다.
  atomic write 기반 `devbot init`/`--unregister`와 일반 파일 수정은 mtime/size/inode
  변경을 발생시킨다.

## Improvement Suggestions

- `status` 또는 `doctor`에 마지막 registry reload 시각과 결과를 표시할 수 있다.
