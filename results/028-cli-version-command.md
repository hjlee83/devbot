# Task 028 Result: CLI Version Command and End-to-End Workflow Validation

## 완료 내용

- `devbot --version`을 추가했다.
- 출력 버전은 `importlib.metadata.version("devbot")`에서 읽는다.
- `--version`은 `load_config()`, daemon lock, GitHub client, polling service,
  Agent runner 초기화보다 먼저 `0`으로 종료한다.
- README 사용 예시와 roadmap을 갱신했다.

## 주요 설계 결정

- 별도 버전 상수를 만들지 않고 Python package metadata를 단일 소스로 사용했다.
- `main()`에서 인자 파싱 직후 `--version`을 처리해 runtime configuration과 daemon
  startup 경로를 완전히 우회했다.
- 기존 `--once`, `--dry-run`, `--verbose` 경로는 변경하지 않았다.

## 수정 파일

- `src/devbot/main.py`
- `tests/test_main.py`
- `README.md`
- `docs/00-roadmap.md`
- `results/028-cli-version-command.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-028-1 Version output | `test_cli_version_prints_package_version` |
| CP-028-2 Single source of truth | `test_cli_version_uses_authoritative_version_source` |
| CP-028-3 Configuration independence | `test_cli_version_does_not_load_runtime_config` |
| CP-028-4 Lock independence | `test_cli_version_does_not_acquire_daemon_lock` |
| CP-028-5 Network independence | `test_cli_version_does_not_contact_github` |
| CP-028-6 Polling independence | `test_cli_version_does_not_start_polling_or_agents` |
| CP-028-7 Existing CLI compatibility | `test_existing_cli_workflows_remain_compatible_with_version_command` + full test suite |
| CP-028-8 Documentation and evidence | README, roadmap, this Result |

## Validation 결과

- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/ruff check .`: PASS
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/pytest tests/test_main.py tests/test_main_loop.py`: PASS, 21 passed
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/pytest`: PASS, 423 passed
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/python -c "from devbot.main import main; raise SystemExit(main(['--version']))"`: PASS, `devbot 0.1.0`
- 임시 config/lock 파일로 `main(['--once', '--dry-run'])`: PASS, `no_managed_repositories`

`uv sync`, `uv run ruff check .`, `uv run pytest`는 sandbox의 네트워크 제한 때문에
의존성 다운로드 단계에서 실패했다. 같은 checkout 코드는 기존 로컬 venv와
`PYTHONPATH=src`로 검증했다.

## 수동 검증 결과

- 유효하지 않은/누락된 runtime config를 넘겨도 `--version`이 `0`으로 종료함을 테스트로
  확인했다.
- daemon lock, GitHub client, polling service, Agent runner가 호출되지 않음을 mock
  assertion으로 확인했다.

## 남은 TODO와 제한

- PR Evidence는 GitHub PR 본문 metadata이므로 이 sandbox에서 갱신하지 못했다. PR #58
  본문에는 위 Validation 결과와 checkpoint evidence를 반영해야 한다.
- 실제 `uv sync`/`uv run ...` validation은 네트워크 접근 가능한 환경에서 재실행해야 한다.

## 위험 요소

- `importlib.metadata.version("devbot")`는 package metadata가 설치된 실행 환경을 전제로
  한다. 프로젝트의 console script 실행 경로에서는 이미 이 전제를 만족한다.

## Improvement Suggestions

- Validation runner가 sandbox 환경에서 `UV_CACHE_DIR=.uv-cache`와 사전 동기화된 venv를
  일관되게 사용하도록 표준화하면 네트워크 제약 환경의 재현성이 좋아진다.
