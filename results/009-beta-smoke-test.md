# Result: Task 009

## Status
- COMPLETE

## 구현 요약

`tasks/009-beta-smoke-test.md` 범위대로, 새 기능 없이 Task 001~008의
결과물을 하나의 실제 실행 흐름으로 연결했다. 핵심 변경은
`PollingService.run_once()`(Task 005) 하나뿐이다 — 원래 "select ->
workspace 검증 -> AgentRunner 실행"에서 멈추던 흐름을, 성공 시
"claim(ready→working) -> AgentRunner -> DeliveryService.deliver(검증
->커밋->푸시->PR) -> mark_for_review(→review)"까지, 실패 시
"claim -> AgentRunner 또는 검증 실패 -> IssueStateWriter.block(→blocked)"까지
이어지도록 확장했다.

- **`src/devbot/polling.py`(수정)**: `PollingService`에 `state_writer:
  IssueStateWriter | None = None`, `delivery: DeliveryService | None =
  None` 필드를 추가했다. 둘 다 있을 때만("full_flow") claim/deliver/
  block/mark_for_review를 수행하고, 하나라도 없으면 Task 005의 원래
  동작(select + AgentRunner만) 그대로 동작한다.
  - 왜 `None` 기본값인가: 이 두 협력자는 `GitHubWriteClient`(실제
    토큰)가 필요해 부작용 없는 "진짜" 기본값을 만들 수 없다. 반면
    기존 `select_task`/`ensure_workspace_ready`/`build_prompt`는
    순수하거나 로컬 전용이라 안전한 기본값이 있다. `None` 기본값 덕분에
    Task 001~008에서 이미 작성되고 리뷰된 `tests/test_polling.py`
    14개, `tests/test_main_loop.py`의 기존 5개 테스트를 단 한 줄도
    고치지 않고 그대로 통과시켰다(아래 "테스트 결과" 참고) — 이 필드가
    없을 때는 존재하지 않는 것처럼 동작해야 한다는 요구를 코드로 강제한
    것이다.
  - `PollingStatus`에 `DELIVERED`(성공적으로 review까지 전환)와
    `BLOCKED`(에이전트 또는 검증 실패로 blocked까지 전환)를 추가했다.
  - claim/deliver 호출은 각각 `try/except`로 감싸 `ITERATION_ERROR`로
    보고한다(`run_once()`는 예외를 던지지 않는다는 기존 계약 유지).
  - `dry_run`이면 `DeliveryService.deliver()`가 검증은 실제로 수행하되
    커밋/푸시/PR/댓글은 건너뛰므로(Task 007 계약), 그 결과를 받은 뒤
    `mark_for_review()`를 호출하지 않고 `AGENT_COMPLETED`로 보고한다 —
    "일어나지 않은 전환"을 GitHub에 기록하지 않기 위해서다.
- **`src/devbot/main.py`(수정)**: 항상 `GitHubWriteClient`/
  `IssueStateWriter`/`DeliveryService`를 만들어 `PollingService`에
  주입한다(둘 다 `dry_run=config.dry_run`). 즉 실제 CLI 경로는 항상
  "full_flow"로 동작한다. `--dry-run` CLI 플래그를 추가했다 — `DRY_RUN`
  환경변수 값과 무관하게 강제로 dry-run으로 실행한다(Task 명세의 검증
  명령 `uv run devbot --once --dry-run`을 지원하기 위해 필요했다 — 이전
  CLI에는 `--once`만 있었다). `PollingStatus.BLOCKED`를 실패 상태
  집합에 추가해 종료 코드에도 반영했다.
- 새 프로덕션 코드나 새 GitHub 쓰기 능력은 추가하지 않았다 — 모두
  Task 006(`IssueStateWriter`)/007(`DeliveryService`)에서 이미 만든
  것을 그대로 호출만 한다.

## 테스트 결과

`tests/test_beta_smoke.py`(신규)가 CP-009-1~8을 담당한다. 대부분은
`state_writer`/`delivery`를 `MagicMock(spec=...)`으로 주입해 호출
순서/인자를 검증하는 단위 테스트 스타일이고(Task 005~008과 동일한
패턴), 두 곳만 실제 객체를 쓴다:

- CP-009-1은 실제 `IssueStateWriter`/`DeliveryService`/`CodexRunner`를
  전부 `dry_run=True`로 구성하고 `subprocess.run`(에이전트)까지
  패치해서, "아무 데도 쓰지 않는다"를 목(mock) 호출 여부가 아니라 실제
  객체 조합으로 증명한다.
- CP-009-8은 실제 `DeliveryService` + `ReworkService` + 공유
  `GitHubWriteClient` 목을 조합해, 1단계(`PollingService`가 처음
  구현을 배달해 PR #1을 연다) 다음 2단계(`ReworkService`가 같은
  브랜치에 rework 커밋을 푸시)까지 실행하고 `create_pull_request`가
  전체 흐름에서 정확히 한 번만 호출됐는지 확인한다 — "기존 PR을
  재사용한다"를 Task 007/008 각각의 단위 테스트가 아니라 두 서비스가
  실제로 맞물릴 때 검증한다.

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-009-1 | Dry-run은 외부 변경이 없다 | `test_dry_run_has_no_side_effect` | PASS |
| CP-009-2 | Working이 있으면 새 작업을 시작하지 않는다 | `test_skip_when_working_exists` | PASS |
| CP-009-3 | Ready를 정상 선택한다 | `test_select_ready_issue` | PASS |
| CP-009-4 | AgentRunner를 호출한다 | `test_agent_runner_called` | PASS |
| CP-009-5 | 검증 성공 후 Commit/Push/PR 순서를 지킨다 | `test_delivery_after_verification` | PASS |
| CP-009-6 | 실패 시 Commit하지 않는다 | `test_stop_delivery_when_failed` | PASS |
| CP-009-7 | Review 상태로 변경된다 | `test_move_to_review` | PASS |
| CP-009-8 | 기존 PR을 재사용한다 | `test_reuse_existing_pr` | PASS |

추가로 작성/수정한 테스트:
- `test_cli_dry_run_flag_forces_dry_run_regardless_of_env`
  (`tests/test_main_loop.py`, 신규) — `--dry-run`이 `DRY_RUN=false`
  환경에서도 `PollingService`에 전달되는 모든 협력자(`agent_runner`,
  `state_writer`, `delivery`)를 강제로 dry-run으로 구성하는지 검증.
- `test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
  (`tests/test_main_loop.py`, Task 005부터 존재, 이번에 수정) — 아래
  "발견된 문제" 참고.

`tests/test_beta_smoke.py` 8개 전부 PASS. 전체 스위트 102개 전부 PASS.
기존 93개 테스트(Task 001~008)는 단 하나도 수정 없이 그대로 통과했다
(위 한 개 예외 제외, "발견된 문제" 참고).

## 발견된 문제

**버그: `DRY_RUN=false` CLI 통합 테스트가 실제 GitHub API로 나가려
했다.** `main.py`가 이제 항상 `IssueStateWriter`/`DeliveryService`를
`config.dry_run` 그대로(`False`) 구성해 주입하므로,
`tests/test_main_loop.py`의 기존 테스트
(`test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`,
`DRY_RUN=false`로 AgentRunner 실패 시 종료 코드 1을 검증)가 실제
네트워크 요청(`GitHubWriteClient.set_labels`/`create_comment`)을 보내게
됐다. `GitHubClient.list_issues`만 패치돼 있고 쓰기 클라이언트는 전혀
목이 아니었기 때문이다.

- **증상**: 이 샌드박스에는 외부 네트워크가 없어 요청이 즉시
  실패했고, 그 예외가 `run_once()`의 claim 단계 `try/except`에 잡혀
  `ITERATION_ERROR`로 변환됐다. `ITERATION_ERROR`도 실패 상태 집합에
  있어 종료 코드는 우연히 1이 나왔다 — 즉 테스트는 "통과"했지만 원래
  검증하려던 경로(AgentRunner 실패 → `AGENT_FAILED` → `block()`)가
  아니라 다른 이유(네트워크 오류 → `ITERATION_ERROR`)로 통과한
  거짓 양성이었다. 실제 배포 환경(진짜 인터넷 접근이 있는 CI 러너 등)
  이었다면 각 HTTP 호출이 `timeout=30`까지 걸릴 수 있어 테스트가 느려지거나,
  최악의 경우 유효한 토큰이 실수로 섞이면 실제 GitHub 저장소에 라벨/댓글을
  남길 수 있는 위험한 설계였다.
- **수정**: 해당 테스트에
  `patch("devbot.github_write_client.GitHubWriteClient.set_labels")`와
  `patch("devbot.github_write_client.GitHubWriteClient.create_comment")`를
  추가하고, `set_labels`가 정확히 2번(claim + block), `create_comment`가
  1번(block 사유) 호출됐는지 단언을 추가했다 — 이제 이 테스트는 실제
  `AGENT_FAILED` 경로를 검증하고, 네트워크 호출 없이 결정적으로
  동작한다.
- **검증**: 수정 전/후 모두 `assert exit_code == 1`은 통과하지만, 수정
  전에는 그 이유가 잘못됐다는 것을 `mock_set_labels.call_count == 2`
  단언 추가로 확인했다(수정 전 코드였다면 이 단언이 실패했을 것 —
  claim 단계에서 이미 네트워크 예외로 중단돼 `set_labels`가 한 번도
  실제로 시도되지 않거나, mock이 없어 아예 실제 요청이 나갔을 것이기
  때문).

## 수정 내용

- `src/devbot/polling.py` — `PollingService`에 `state_writer`/`delivery`
  선택적 필드 추가, `run_once()`에 claim/deliver/block/mark_for_review
  연결, `PollingStatus.DELIVERED`/`BLOCKED` 추가.
- `src/devbot/main.py` — 항상 `state_writer`/`delivery` 구성해 주입,
  `--dry-run` CLI 플래그 추가, `BLOCKED`를 실패 상태에 추가.
- `tests/test_main_loop.py` — `test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
  수정(위 "발견된 문제" 참고), `test_cli_dry_run_flag_forces_dry_run_regardless_of_env` 추가.
- `tests/test_beta_smoke.py` — 신규, CP-009-1~8.
- `scripts/verify.sh`, `.github/workflows/ci.yml` — `uv run devbot
  --once` → `uv run devbot --once --dry-run`로 갱신(Task 명세의 검증
  명령과 일치, dry-run을 명시적으로 강제해 CI가 절대 쓰기 경로를 타지
  않도록 보강).
- `README.md` — Run/Development 절에 `--dry-run` 반영, Project layout에
  Task 006~008에서 추가된 모듈(`github_write_client.py`,
  `issue_state.py`, `delivery.py`, `rework.py`) 반영, 검증 명령이
  `uv`/Python 대상 저장소를 전제한다는 제약 명시.
- `docs/08-beta-runbook.md` — 신규. 전체 흐름 다이어그램, 실제 로컬
  `uv` 프로젝트를 대상으로 한 dry-run 수동 재현 절차, 운영 체크리스트.

`issue_state.py`/`delivery.py`/`rework.py`/`github_write_client.py`
자체는 수정하지 않았다 — 이번 Task는 연결만 한다.

## 운영 체크리스트

`docs/08-beta-runbook.md`의 "Operational checklist" 절에 전체 목록이
있다. 핵심만 요약하면:

- `WORKSPACE_ROOT` 아래 각 관리 저장소가 실제로 클론돼 있고 깨끗한
  워킹 트리여야 한다.
- 관리 저장소는 전부 `uv run ruff check .`/`uv run pytest`가 성공하는
  `uv` 기반 Python 프로젝트여야 한다(검증 명령이 하드코딩돼 있음 — 아래
  "알려진 제약" 참고).
- `GITHUB_TOKEN`은 라벨/댓글/PR 쓰기 권한이 있어야 한다(읽기 전용이면
  claim/block/deliver 전부 실패한다).
- 새 환경에서는 항상 `DRY_RUN=true`(또는 `--dry-run`)로 먼저 로그를
  확인한 뒤 실제 쓰기를 켜야 한다.
- `ReworkService`(Task 008)는 아직 폴링 루프에 연결돼 있지 않다 —
  `review` 상태 Issue의 `@devbot` 댓글은 자동으로 감지되지 않는다.

## 알려진 제약 (다음 Task로 이어짐)

- **검증 명령이 `uv`/Python 전용으로 하드코딩돼 있다**
  (`delivery.DEFAULT_VERIFICATION_COMMANDS`). 저장소별로 다른 언어/툴체인을
  쓰는 대상 저장소는 현재 지원되지 않는다 — `config/repositories.yaml`에
  검증 명령을 지정할 수 있게 하는 것이 다음으로 자연스러운 확장이다.
- **`ReworkService`가 `PollingService`에 연결돼 있지 않다.** `review`
  상태 Issue들의 PR 댓글을 주기적으로 조회해 `ReworkService.process()`를
  호출하는 경로가 없다 — Task 008의 "남은 TODO"가 그대로 남아 있다.
- **여러 저장소에 걸친 동시 claim 경쟁 조건은 다루지 않는다.** 두
  DevBot 인스턴스가 동시에 실행되는 상황은 `DEVBOT_LOCK_FILE`(단일
  프로세스 락, Task 001)로만 막는다 — 분산 락은 범위 밖이다.
- **`commit_all_changes()`가 `git add -A`로 전체 변경을 커밋한다**
  (Task 007에서 이미 기록된 위험 요소, 이번 Task에서도 그대로 남음).

## 다음 Task 제안

1. **`ReworkService`를 폴링 루프에 연결** — `review` 상태 Issue의 PR
   댓글을 조회해 `find_unprocessed_devbot_comments()` → `ReworkService.process()`로
   이어지는 경로 추가. Task 008의 남은 TODO를 마무리하는 가장 자연스러운
   다음 단계다.
2. **저장소별 검증 명령 설정** — `config/repositories.yaml`에
   `verification_commands` 같은 선택 필드를 추가해 `uv`/Python이
   아닌 저장소도 지원.
3. **운영(Phase 3)**: macOS `launchd`/Linux `systemd` 유닛 파일, 구조화
   로깅과 실패 시 알림 — `docs/00-roadmap.md`의 Phase 3 항목.

## 검증 명령 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (102 passed) |
| `uv run devbot --once --dry-run` | PASS (exit 0, `no_ready_task`) |

추가로 로컬에 실제 Git 저장소 + 최소 `uv` 프로젝트(`pyproject.toml` +
`tests/test_dummy.py`)를 만들고, `GitHubClient.list_issues`만 목으로
`devbot:ready` Issue 하나를 주입해 `main(["--once", "--dry-run"], ...)`를
끝까지 실행했다. 로그가 claim(암시적, dry-run이라 실제 쓰기는
없음) → `AgentRunner 실행: ... dry_run=True` →
`Delivery 시작: branch=...` → 대상 저장소에서 실제로 실행된
`uv run ruff check .`/`uv run pytest`가 통과 →
`Delivery 결과: [dry-run] verification passed; no commit, push, or PR`
순서로 찍히고 종료 코드 0으로 끝나는 것을 확인했다(자세한 재현 절차는
`docs/08-beta-runbook.md` 참고).

## 위험 요소

- 로컬 샌드박스에 `uv`가 기본 설치돼 있지 않아 `pip install --user uv`로
  설치한 뒤 검증 명령을 실행했다(Task 006~008과 동일).
- `PollingService`의 `state_writer`/`delivery`를 `None` 기본값으로 둔
  설계는 "설정 안 하면 조용히 아무 것도 안 한다"는 형태라, 프로덕션
  경로(`main.py`)가 항상 둘 다 주입한다는 사실에 의존한다. `main.py`
  테스트(`test_cli_dry_run_flag_forces_dry_run_regardless_of_env`)로
  그 배선 자체는 검증했지만, 향후 `main.py` 외에 `PollingService`를
  직접 구성해 쓰는 코드가 생긴다면 이 필드를 깜빡 빠뜨리기 쉽다는 점을
  남겨둔다.
