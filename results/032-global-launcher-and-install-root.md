# Task 032 Result: Global Launcher and Install Root Discovery

## 완료 내용

- `devbot.installation` 모듈을 추가해 설치 루트 검증, 메타데이터 저장/복구, PATH launcher 생성을 담당하게 했다.
- `devbot install-launcher --bin-dir <PATH>` CLI를 추가했다.
- launcher는 `DEVBOT_INSTALL_ROOT`, `DEVBOT_INSTALL_METADATA`를 export하고 설치 루트로 `cd`한 뒤 `uv run --project "$DEVBOT_INSTALL_ROOT" devbot "$@"`로 모든 인자를 전달한다.
- `load_config()`의 기본 `.env`와 `config/repositories.yaml` 경로를 현재 디렉터리 대신 설치 루트 기준으로 바꿨다.
- `main()`은 `--version`과 `install-launcher`를 runtime config 없이 처리하고, 나머지 명령은 설치 루트를 먼저 해석한다.
- Task 031 Startup Self Update가 전역 실행에서도 operator checkout인 설치 루트를 명시적으로 사용하게 했다.
- 설치 메타데이터가 없거나 잘못된 경우 복구 명령을 포함한 안전한 오류를 반환한다.
- README에 전역 launcher 설치와 실행 방법을 추가했다.

## 주요 설계 결정

- 배포 방식은 Homebrew/pipx가 아니라 작은 shell launcher와 메타데이터 파일로 제한했다.
- 기본 메타데이터 경로는 `~/.config/devbot/install-root`이며 테스트/대체 배포는 `DEVBOT_INSTALL_METADATA`로 바꿀 수 있다.
- 명시적 `DEVBOT_INSTALL_ROOT`가 있으면 메타데이터보다 우선한다.
- metadata가 없을 때 library-level discovery는 기존 개발 워크플로 호환을 위해 현재 디렉터리를 fallback으로 허용하지만, strict recovery 테스트는 fallback 없이 실패를 검증한다.

## 수정 파일

- `src/devbot/installation.py`
- `src/devbot/config.py`
- `src/devbot/main.py`
- `tests/conftest.py`
- `tests/test_installation.py`
- `tests/test_main.py`
- `README.md`
- `tasks/032-global-launcher-and-install-root.md`
- `results/032-global-launcher-and-install-root.md`

## Checkpoint Evidence

- CP-032-1 PATH launcher:
  - `tests/test_installation.py::test_install_launcher_writes_path_command_that_forwards_arguments`
- CP-032-2 persistent install root:
  - `tests/test_installation.py::test_install_metadata_persists_operator_checkout`
- CP-032-3 CWD-independent config:
  - `tests/test_installation.py::test_config_defaults_are_install_root_relative_from_any_cwd`
- CP-032-4 argument and exit-code forwarding:
  - `tests/test_installation.py::test_launcher_forwards_cli_arguments_and_exit_code`
- CP-032-5 startup self-update compatibility:
  - `tests/test_main.py::test_main_uses_install_root_for_startup_self_update`
- CP-032-6 diagnostics and recovery:
  - `tests/test_installation.py::test_missing_install_metadata_has_safe_recovery_hint`
- CP-032-7 existing CLI compatibility:
  - `tests/test_main.py::test_cli_version_does_not_load_runtime_config`
  - `tests/test_installation.py::test_install_launcher_command_does_not_load_runtime_config`

## Validation 결과

- `UV_CACHE_DIR=.uv-cache uv sync`: PASS
- `UV_CACHE_DIR=.uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=.uv-cache uv run pytest`: PASS, 492 passed
- `UV_CACHE_DIR=.uv-cache WORKSPACE_ROOT=<tmp>/workspace GITHUB_TOKEN=test-token DEVBOT_LOCK_FILE=<tmp>/devbot.lock DEVBOT_REPOSITORIES_PATH=<tmp>/repositories.yaml uv run devbot --once --dry-run`: PASS, `no_managed_repositories`
- `uv run devbot doctor`: NOT RUN. 이번 실행 지시가 원격 discovery/network 명령을 금지했고, 현재 `doctor` 경로는 Task 031 Startup Self Update를 먼저 수행해 Git remote 접근이 발생할 수 있어 실행하지 않았다.

## 수동 검증 결과

- PATH launcher script 내용이 `DEVBOT_INSTALL_ROOT`, `DEVBOT_INSTALL_METADATA`, `cd <install-root>`, `uv run --project "$DEVBOT_INSTALL_ROOT" devbot "$@"`를 포함하는지 테스트로 검증했다.
- fake `uv` executable을 사용해 실제 launcher script가 모든 CLI 인자를 전달하고 exit code 37을 보존하는지 검증했다.

## 남은 TODO와 제한

- PR Evidence 갱신과 `uv run devbot doctor`는 원격 조회/쓰기 금지 조건 때문에 이 실행에서 수행하지 않았다.

## 위험 요소

- launcher는 `uv`가 PATH에 있어야 동작한다.
- 여러 설치를 선택하는 기능은 범위 밖이므로 메타데이터는 단일 설치 루트만 저장한다.

## Improvement Suggestions

- 향후 `devbot init`이 생기면 `.env`, repositories 설정, launcher 설치를 한 번에 검증하는 wizard로 확장할 수 있다.
