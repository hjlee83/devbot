# Task 143 Result: Run DevBot Independently of the Current Working Directory

## 완료 내용

- registry repository resolution을 legacy `config/repositories.yaml`보다 먼저 수행하도록
  config loading 순서를 조정했다.
- `WORKSPACE_ROOT`가 설정되어 있어도 기본 `config/repositories.yaml`이 없고 registry
  repository가 유효하면 fatal error로 처리하지 않게 했다.
- `DEVBOT_REPOSITORIES_PATH` 또는 명시 `repositories_path`는 계속 strict하게
  missing/malformed file을 오류로 처리한다.
- runtime directory에서 registry 기반으로 `devbot --once --dry-run`이 동작하는
  회귀 테스트를 추가했다.
- README, beta runbook, `docs/19-repository-registration.md`에 runtime directory
  실행 정책을 문서화했다.

## 주요 설계 결정

- Optional로 완화한 대상은 CWD-relative default legacy path뿐이다. 명시된 legacy
  path는 operator intent로 보고 기존처럼 fail closed한다.
- registry diagnostics는 기존과 같이 non-fatal diagnostic으로 유지한다.
- legacy와 registry가 모두 존재할 때 duplicate `owner/repo` 검증은 기존 경로를
  그대로 사용한다.

## 수정 파일

- `src/devbot/config.py`
- `tests/test_config.py`
- `tests/test_main.py`
- `README.md`
- `docs/08-beta-runbook.md`
- `docs/19-repository-registration.md`
- `tasks/143-cwd-independent-runtime-config.md`
- `results/143-cwd-independent-runtime-config.md`

## Checkpoint Evidence

- Registry-only runtime directory:
  `test_default_legacy_repositories_file_is_optional_with_registered_repository`,
  `test_daemon_run_from_unrelated_runtime_dir_uses_registry_without_legacy_config`
- Existing registry and legacy behavior:
  `test_workspace_root_is_optional_when_a_repository_is_registered`,
  `test_legacy_and_registered_repositories_are_merged`,
  `test_same_repository_in_both_sources_raises`

## Validation 결과

- `uv run ruff check src/devbot/config.py tests/test_config.py tests/test_main.py`
  - PASS
- `uv run pytest tests/test_config.py::test_default_legacy_repositories_file_is_optional_with_registered_repository tests/test_config.py::test_workspace_root_is_optional_when_a_repository_is_registered tests/test_config.py::test_legacy_and_registered_repositories_are_merged tests/test_config.py::test_same_repository_in_both_sources_raises tests/test_main.py::test_daemon_run_from_unrelated_runtime_dir_uses_registry_without_legacy_config tests/test_main.py::test_daemon_run_sees_a_devbot_init_registered_repository`
  - PASS, 6 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1425 passed in 55.70s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: registry repository 2개를 로드하고 dirty checkout/current branch 진단 후
    `NO_RUNNABLE_TASK`.

## 수동 검증

- Issue #143 재현 조건인 “runtime directory에 `config/`가 없는 상태”를 테스트에서
  `monkeypatch.chdir(runtime_dir)`와 `assert not (runtime_dir / "config" /
  "repositories.yaml").exists()`로 고정했다.
- 명시 legacy path는 optional로 완화하지 않고 `_load_repositories()`를 그대로 호출해
  missing/malformed file을 계속 fatal로 처리하는지 확인했다.

## 남은 TODO와 제한

- 없음.

## 위험 요소

- 명시 legacy path와 default legacy path의 strictness 차이를 문서와 테스트로 고정했다.

## Improvement Suggestions

- `devbot doctor`에 resolved config source summary를 별도 항목으로 추가하면 운영자가
  registry/legacy source를 더 쉽게 확인할 수 있다.
