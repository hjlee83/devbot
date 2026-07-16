# Task 029 Result: Prepared Workspace Validation Environment

## 완료 내용

- PR #61의 유용한 구현 커밋 `a9f8abd2014cd0d6e8cdd9194eeffad3e9581a5d`에서
  implementation/test 변경을 canonical branch `task/029-prepared-workspace-validation`로
  복구했다.
- PR #61이 축약한 `tasks/029-prepared-workspace-validation.md`는 가져오지 않고,
  PR #60의 canonical full contract를 보존했다.
- `devbot.validation` helper를 추가해 validation command가 PreparedWorkspace cwd와
  workspace-scoped environment를 사용하도록 했다.
- delivery/rework의 기존 `run_verification` 주입 구조는 유지하면서 기본 검증이
  `uv sync` 후 literal command를 실행하도록 변경했다.
- 부모 process의 host `VIRTUAL_ENV`를 제거하고 PreparedWorkspace `.venv/bin`을 PATH
  앞에 배치해 host checkout `.venv` fallback을 방지했다.
- validation evidence에 workspace path, command, exit code, output summary를 남길 수
  있게 했다.
- failure category를 `environment_preparation_failed`,
  `dependency_network_unavailable`, `validation_command_failed`,
  `forbidden_host_fallback`으로 구분했다.
- `PreparedWorkspace`가 원본 host checkout path를 `RepositoryConfig.host_checkout_path`로
  보존하고 delivery validation의 forbidden-host-fallback 탐지에 전달하도록 했다.
- validation failure category를 polling/rework state routing에 연결했다.
  repository-fixable command failure는 `devbot:rework`, dependency/network,
  environment preparation, forbidden host fallback은 `devbot:manual-action`으로 보낸다.
- PR #60 본문 Evidence를 canonical Issue/branch/contract/Result, CP-029 checkpoint,
  validation 결과 기준으로 갱신했다.
- 중복 PR #61은 fallback branch creation으로 생긴 accidental duplicate임을 댓글로
  설명하고 닫았다. 원격 duplicate branch
  `devbot/devbot-59-task-029-prepared-workspace-validation-e`도 삭제했다.

## 주요 설계 결정

- validation 환경 선택은 `src/devbot/validation.py`에 집중시켰다.
- shared uv cache는 보존하되 cwd, workspace marker, `.venv/bin` 우선순위는
  PreparedWorkspace 기준으로 고정했다.
- `DeliveryService`는 기존 public shape를 깨지 않도록 `VerificationResult`에 optional
  evidence field를 추가하는 방식으로 확장했다.
- repository-fixable command failure와 external dependency/network failure를 구분해
  rework/manual-action routing 판단에 사용할 수 있게 했다.

## 수정 파일

- `src/devbot/validation.py`
- `src/devbot/delivery.py`
- `src/devbot/models.py`
- `src/devbot/worktree.py`
- `src/devbot/polling.py`
- `src/devbot/rework.py`
- `tests/test_delivery.py`
- `tests/test_polling.py`
- `tests/test_worktree.py`
- `docs/00-roadmap.md`
- `results/029-prepared-workspace-validation.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-029-1 Authoritative validation workspace | `test_validation_uses_prepared_workspace_repository` |
| CP-029-2 Workspace-scoped environment preparation | `test_prepared_workspace_has_usable_validation_environment` |
| CP-029-3 Host environment fallback prevention | `test_prepared_workspace_preserves_host_checkout_path`, `test_validation_rejects_host_checkout_environment_fallback` |
| CP-029-4 Literal Validation Gate execution | `test_validation_executes_contract_commands_literally` |
| CP-029-5 Shared cache safety | `test_shared_uv_cache_does_not_break_workspace_isolation` |
| CP-029-6 Structured validation evidence | `test_validation_evidence_records_workspace_and_commands` |
| CP-029-7 Failure classification | `test_validation_environment_failure_classification` |
| CP-029-8 Safe state routing | `test_validation_command_failure_routes_implement_to_rework`, `test_external_validation_failure_routes_implement_to_manual_action` |
| CP-029-9 Resume compatibility | `test_resumed_task_reuses_validation_environment` |
| CP-029-10 Autonomous loop compatibility | `test_autonomous_loop_validates_only_in_prepared_workspace` |
| CP-029-11 Existing workflow regression safety | `test_existing_workflows_remain_compatible_with_workspace_validation_environment` + full test suite |
| CP-029-12 Documentation and Result | `docs/00-roadmap.md`, this Result, PR #60 Evidence |

## Validation 결과

Workspace:
`/Users/luna/workspace/.devbot-worktrees/devbot/issue-59`

| Command | Result |
| --- | --- |
| `uv sync` | PASS |
| `uv run ruff check .` | PASS |
| `uv run pytest` | PASS, 439 passed |
| `uv run devbot doctor` | PASS |
| `uv run devbot --once --dry-run` | PASS, `NO_RUNNABLE_TASK` |

추가 demonstration:

- Dirty/unusable host checkout influence: PASS. 모든 validation command를 canonical
  PreparedWorkspace cwd에서 실행했고 host checkout cwd를 사용하지 않았다.
- Host `.venv` fallback prevention: PASS. tests verify parent `VIRTUAL_ENV` removal,
  PreparedWorkspace `.venv/bin` PATH priority, preserved host checkout path propagation, and
  production `run_verification_commands()` fallback detection.
- Dependency/network failure classification: PASS. tests verify dependency/network output
  maps to `dependency_network_unavailable` without silent fallback.
- Safe state routing: PASS. polling tests verify validation command failures route to
  `devbot:rework` and external dependency/network failures route to `devbot:manual-action`.
- Result/PR Evidence workspace and literal commands: PASS. this Result and PR #60 Evidence
  identify the actual PreparedWorkspace and commands.

## 수동 검증 결과

- PR #60 head가 recovered implementation commit을 포함함을 확인했다.
- `devbot --once --dry-run`은 당시 Issue #59가 `devbot:manual-action` 상태임을
  관측하고 실행 가능한 작업 없음(`NO_RUNNABLE_TASK`)으로 정상 종료했다.
- PR #61은 accidental duplicate로 닫았고, duplicate 원격 branch도 삭제했다.
- `git ls-remote --heads origin '*029*' '*devbot-59*'` 결과 Task 029 원격 branch는
  `task/029-prepared-workspace-validation` 하나만 남았다.
- canonical Task contract는 PR #60의 full contract 그대로 유지했다.

## 남은 TODO와 제한

- 없음.

## 위험 요소

- `uv sync`가 delivery 검증 앞에 추가되어 dependency/network가 없는 새 workspace에서는
  빠르게 실패한다. 이는 host `.venv` fallback을 막기 위한 의도된 safe failure다.
- PATH 우선순위가 너무 엄격해지는 경우 shared cache와 workspace local `.venv` 구분을
  추가로 조정해야 할 수 있다.

## Improvement Suggestions

- `VerificationResult` evidence를 PR Evidence renderer와 직접 연결하면 수동 metadata
  갱신 단계를 줄일 수 있다.
