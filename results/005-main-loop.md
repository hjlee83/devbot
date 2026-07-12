# Result: Task 005

## Status
- COMPLETE

## 구현 요약

`tasks/005-main-loop.md`(한국어 v1.0.0) 범위대로, 기존 설정·GitHub 읽기
클라이언트·전역 큐·워크스페이스·AgentRunner를 연결해 1회 폴링과 연속
폴링 루프를 구현했다.

- **`src/devbot/polling.py`(신규)**: `PollingService`가 한 번의 폴링을 담당한다.
  - `config.enabled_repositories`를 순회하며 `GitHubClient.list_issues(state="open")`로 열린 Issue를 모두 가져온다.
  - `issue_to_task()`가 각 Issue의 라벨(`devbot:*`, `priority:*`)을 읽어 `IssueTask`로 변환한다. `devbot:*` 라벨이 전혀 없는 Issue는 DevBot이 관리하지 않는 것으로 보고 무시한다.
  - `queue.has_active_task`/`queue.select_global_ready_task`(Task 001/003, 미변경)로 전역 활성 작업 확인과 ready Issue 선택을 수행한다.
  - 선택된 Issue의 저장소에 `workspace.ensure_git_workspace_ready`(Task 004, 미변경)를 실행하고, `workspace.build_agent_prompt`(Task 004, 미변경)로 프롬프트를 만들어 `AgentRunner.run()`을 호출한다.
  - 결과를 `PollingStatus` Enum(`SKIPPED_ACTIVE_TASK`/`NO_READY_TASK`/`WORKSPACE_INVALID`/`AGENT_COMPLETED`/`AGENT_FAILED`/`ITERATION_ERROR`)과 `PollingResult`(status/task/message)로 구조화해 반환한다. 어떤 단계에서 예외가 나도 `run_once()` 자체는 예외를 던지지 않고 구조화된 실패 결과를 돌려준다.
  - GitHubClient, select_task, ensure_workspace_ready, build_prompt, agent_runner, logger 전부 생성자에서 교체 가능하다(의존성 주입).
  - `run_forever()`가 `SIGINT`/`SIGTERM`을 받아 `run_once()` 사이사이 안전하게 종료하는 연속 루프를 구현한다. `sleep_fn`을 주입 가능해 테스트에서 실제 `time.sleep`을 쓰지 않는다.
- **`src/devbot/main.py`(재작성)**: `argparse`로 `--once` 플래그를 받는다. 락 획득 후 `PollingService`를 구성해 `--once`면 `run_once()` 한 번, 아니면 `run_forever()`를 실행한다. `--once` 실패(`WORKSPACE_INVALID`/`AGENT_FAILED`/`ITERATION_ERROR`)는 종료 코드 1로 표현한다. 로그는 `logging` 모듈로 stdout에 한국어로 출력한다.
- **`src/devbot/config.py`/`models.py`**: `GITHUB_TOKEN`을 `WORKSPACE_ROOT`와 동일하게 필수 값으로 추가했다(누락 시 명확한 `ConfigError`).

## 생성/수정 파일

- `tasks/005-main-loop.md` — 파일에 섞여있던 구버전(영어, 체크포인트 6개) 제거, 한국어 v1.0.0만 남김(사용자 확인 후 처리)
- `src/devbot/polling.py` — 신규
- `src/devbot/main.py` — `--once`/연속 모드로 재작성
- `src/devbot/config.py` — `GITHUB_TOKEN` 필수 값 추가
- `src/devbot/models.py` — `DevBotConfig.github_token` 필드 추가
- `.env.example`, `README.md`, `scripts/verify.sh`, `.github/workflows/ci.yml` — `GITHUB_TOKEN` 및 `--once` 반영
- `tests/test_polling.py` — 신규 (`PollingService.run_once()` 단위 테스트)
- `tests/test_main_loop.py` — 신규 (`--once`/연속 루프/신호/락 CLI 통합 테스트)
- `tests/test_main.py` — CP-001-10 테스트를 새 CLI(`--once`)에 맞게 갱신(이름은 미변경)
- `tests/test_config.py`, `tests/conftest.py` — `GITHUB_TOKEN` 관련 fixture/누수 방지 갱신, `test_missing_github_token_raises` 추가

## CP-005-1 ~ CP-005-12별 테스트 이름과 결과

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-005-1 | working 존재 시 선택 금지 | `test_iteration_skips_when_working_task_exists` | PASS |
| CP-005-2 | review 존재 시 선택 금지 | `test_iteration_skips_when_review_task_exists` | PASS |
| CP-005-3 | 활성 작업 없으면 ready Issue 하나 선택 | `test_iteration_selects_one_ready_issue` | PASS |
| CP-005-4 | ready Issue 없으면 정상적으로 아무 작업도 안 함 | `test_iteration_handles_empty_queue` | PASS |
| CP-005-5 | 선택된 Issue의 워크스페이스 검증 | `test_iteration_validates_selected_workspace` | PASS |
| CP-005-6 | 선택된 Issue를 AgentRunner에 전달 | `test_iteration_invokes_agent_with_selected_task` | PASS |
| CP-005-7 | Dry-run은 외부 프로세스 미실행 | `test_iteration_dry_run_has_no_external_side_effects` | PASS |
| CP-005-8 | `--once`는 한 번만 실행 후 종료 | `test_run_once_exits_after_single_iteration` | PASS |
| CP-005-9 | 연속 모드는 설정된 폴링 간격 사용 | `test_continuous_loop_uses_configured_poll_interval` | PASS |
| CP-005-10 | SIGINT/SIGTERM 수신 시 안전 종료 | `test_shutdown_signal_stops_loop_gracefully` | PASS |
| CP-005-11 | 오류가 구조화된 실패 결과와 로그로 남음 | `test_iteration_error_is_reported_without_state_corruption` | PASS |
| CP-005-12 | 프로세스 락이 중복 실행을 막음 | `test_main_loop_respects_process_lock` | PASS |

추가로 작성한 테스트(필수 아님, 경계/실패 경로 보강):
`test_iteration_with_zero_repositories_returns_no_ready_task`,
`test_issue_without_devbot_label_is_ignored`,
`test_iteration_reports_workspace_validation_failure`,
`test_iteration_reports_agent_failure`,
`test_iteration_picks_oldest_among_equal_priority_across_repos`,
`test_missing_github_token_raises`.

`tests/test_polling.py` 13개, `tests/test_main_loop.py` 4개 전부 PASS. 전체 스위트 58개 전부 PASS.

## 검증 명령 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (58 passed) |
| `uv run devbot --once` | PASS (exit 0) |

로컬 `.env`(untracked)에 `GITHUB_TOKEN` 플레이스홀더를 추가했다. 커밋된
`config/repositories.yaml`은 샘플 저장소가 `enabled: false`라 실제
GitHub API 호출 없이 `NO_READY_TASK`로 정상 종료한다. 수동으로
`uv run devbot`(연속 모드)을 백그라운드로 띄우고 `SIGINT`를 보내 로그에
"종료 신호(SIGINT)를 받았습니다. 안전하게 종료합니다."가 찍히고 프로세스가
정상 종료되는 것도 별도로 확인했다.

## 남은 TODO

- `main.py`에서 `default_agent` 설정값에 따라 다른 `AgentRunner`를
  선택하는 디스패치가 없다 — 항상 `CodexRunner`를 생성한다(Task 001부터
  이어진 사항, `CodexRunner`가 유일한 구현체라 아직 문제 없음).
- 미처리 댓글은 항상 빈 리스트로 `build_agent_prompt`에 전달된다(Task
  002가 댓글 조회를 Out of scope로 명시, Task 004에서도 동일하게 이후로
  미룸).
- `workspace.validate_repository_paths`(Task 001)가 이제 `main.py`에서
  호출되지 않는다 — 개별 Issue 선택 시 `ensure_git_workspace_ready`로
  대체됐다. 함수 자체는 남겨뒀지만 CLI 경로에서는 죽은 코드다.

## Task 006으로 넘길 제약

- 이번 Task에서는 Issue를 실제 claim(라벨 변경)하지 않는다. 연속 모드에서
  같은 Issue가 반복 선택될 수 있다는 한계가 Task 문서에 이미 명시돼
  있다 — Task 006에서 `ready -> working` 전환을 구현해야 해결된다.
- GitHub 쓰기(라벨 변경, 댓글, PR 생성)는 전혀 하지 않는다.
- 미처리 댓글 판단/조회 로직도 Task 006 이후로 넘어간다.

## 위험 요소

- `run_forever`의 신호 처리 테스트(`test_shutdown_signal_stops_loop_gracefully`)는
  실제 `os.kill(os.getpid(), SIGINT)`로 스스로에게 신호를 보내는 방식이라,
  신호 전달이 제한된 샌드박스 환경에서는 동작하지 않을 수 있다. 로컬과
  일반적인 GitHub Actions ubuntu-latest 러너에서는 정상 동작을 확인했다.
- `main.py`가 `PollingService`/`run_forever`를 직접 생성해서 실행하므로,
  `--once`가 아닌 연속 모드 자체를 CLI 레벨에서 끝까지 통합 테스트하지는
  않는다(무한 루프이므로 직접 실행할 수 없음) — `run_forever`는
  `tests/test_main_loop.py`에서 별도로 검증했고, `main()`이
  `run_forever`를 올바른 인자로 호출하는지는 코드 리뷰로 확인해야 한다.
- CP-001-10(`test_main_starts_and_exits_successfully`, Task 001)의 테스트
  본문을 변경했다. Task 005가 CLI의 기본 동작 자체를(별다른 플래그 없는
  실행 = 1회 종료 → 연속 폴링) 바꿨기 때문에 불가피했다 — 원래 검증하던
  체크포인트("설정 로드 → 정상 종료 → 락 해제")는 그대로 `--once`로
  검증하도록 유지했다. 자세한 내용은
  `results/005-main-loop-improvements.md` 참고.

## Improvement Suggestions

`results/005-main-loop-improvements.md`에 별도로 기록했다.
