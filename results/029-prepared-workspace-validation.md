# Task 029 Result: Prepared Workspace Validation Environment

## 완료 내용

- validation 전용 helper `devbot.validation`을 추가했다.
- `DeliveryService` 기본 검증이 먼저 `uv sync`를 실행하고, 이후 기존 `ruff`/`pytest`
  검증을 같은 workspace에서 실행하도록 변경했다.
- 검증 subprocess 환경에서 부모 `VIRTUAL_ENV`를 제거하고, PreparedWorkspace
  `.venv/bin`을 PATH 앞에 배치해 host checkout `.venv` fallback을 막았다.
- `VerificationResult`에 workspace path, 실행 command 목록, 실패 분류를 기록한다.
- `uv sync` 실패를 `environment_preparation_failed`로 분류하고, 이후 명령 실패와
  구분한다.

## 주요 설계 결정

- delivery/rework의 기존 주입형 `run_verification` 인터페이스는 유지했다.
- daemon 내부 delivery 기본 검증 명령은 기존 `ruff`/`pytest`를 유지하고, 그 앞에
  workspace 환경 준비용 `uv sync`만 추가했다.
- `uv run devbot --once --dry-run`은 실행 중인 daemon lock과 충돌할 수 있어 delivery
  기본 검증 명령에는 넣지 않고 Validation Gate evidence로 별도 기록한다.

## 수정 파일

- `src/devbot/validation.py`
- `src/devbot/delivery.py`
- `tests/test_delivery.py`
- `tasks/029-prepared-workspace-validation.md`
- `results/029-prepared-workspace-validation.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-029-1 Workspace environment preparation | `test_verification_prepares_workspace_environment_and_ignores_host_venv` |
| CP-029-2 Host virtualenv isolation | `test_verification_prepares_workspace_environment_and_ignores_host_venv` |
| CP-029-3 Environment failure classification | `test_uv_sync_failure_is_classified_as_environment_failure` |
| CP-029-4 Existing workflow compatibility | `tests/test_delivery.py`, `tests/test_rework.py`, full regression suite |
| CP-029-5 Evidence | This Result |

## Validation 결과

Workspace:
`/Users/luna/workspace/.devbot-worktrees/devbot/issue-59`

| Command | Result |
| --- | --- |
| `UV_CACHE_DIR=.uv-cache uv run pytest tests/test_delivery.py tests/test_rework.py` | BLOCKED: network-restricted environment could not download `urllib3==2.7.0` while creating workspace `.venv` |
| `UV_CACHE_DIR=.uv-cache uv run ruff check src/devbot/validation.py src/devbot/delivery.py tests/test_delivery.py` | BLOCKED: network-restricted environment could not resolve `hatchling` while building the local package |
| `PYTHONPATH=src python -m ruff check src/devbot/validation.py src/devbot/delivery.py tests/test_delivery.py` | PASS |
| `PYTHONPATH=src python -m pytest tests/test_delivery.py tests/test_rework.py` | PASS, 44 passed |
| `PYTHONPATH=src python -m pytest` | PASS, 425 passed |

`uv sync`, `uv run ruff check .`, `uv run pytest`, `uv run devbot doctor`, and
`uv run devbot --once --dry-run` could not be completed in this sandbox because new dependency
resolution requires external network access. The failure is an environment preparation failure,
not a test failure.

## 수동 검증 결과

- Mocked subprocess tests confirm every validation command uses the PreparedWorkspace cwd.
- Mocked environment tests confirm parent `VIRTUAL_ENV=/host/.venv` is removed before validation.
- Mocked environment tests confirm PATH starts with `<workspace>/.venv/bin`.
- Failure classification test confirms `uv sync` failure stops validation and records
  `environment_preparation_failed`.

## 남은 TODO와 제한

- PR Evidence는 네트워크/remote discovery 금지 조건 때문에 로컬에서 갱신하지 않았다.
- 네트워크가 허용된 운영 환경에서 전체 `uv` 기반 Validation Gate를 재실행하고 PR
  Evidence에 동일한 command/outcome을 반영해야 한다.

## 위험 요소

- `uv sync`를 delivery 검증 앞에 추가했으므로 네트워크가 없는 새 workspace에서는
  환경 준비 실패로 delivery가 중단된다. 이는 host `.venv` fallback을 막기 위한 의도된
  실패 방식이다.

## Improvement Suggestions

- `VerificationResult`를 PR Evidence 렌더링에 직접 연결하면 workspace path와 command
  evidence를 수동으로 옮기는 단계를 줄일 수 있다.
