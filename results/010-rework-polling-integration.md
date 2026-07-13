# Result: Task 010

## Status

- DONE (unit-level): `ReworkService`는 이제 (1) 기존 PR head 브랜치와 로컬
  브랜치 불일치, (2) Agent 실행 중 예외/`KeyboardInterrupt`를 모두 rework
  시작 전/중에 감지해 `devbot:blocked`로 전환하고 원인을 기록한다. 검증
  실패 시 commit/push/PR 갱신을 건너뛰는 기존 게이트는 그대로 유지되며,
  `ReworkResult`가 `code_changed`, `verification_passed`, `committed`,
  `pushed`, `pr_reused`, `issue_state`를 구조화된 필드로 반환하도록
  확장했다.
- 이번 세션은 기존 `feature/task-010-rework-polling-integration` 브랜치를
  그대로 재사용했고, 새 브랜치나 새 PR을 만들지 않았다. 이 브랜치에 대해
  이미 열려 있는 구현 PR은 없다(Task 010의 계약서만 다루는 PR #14는
  merge되어 종료됨). 실제 push와 PR 생성/갱신은 사용자 확인 후 진행한다.

## 이번 세션에서 추가한 내용

1. **Agent 실행 예외/KeyboardInterrupt → blocked**
   - `ReworkService.process()`가 `apply_changes(...)` 호출을
     `try/except (Exception, KeyboardInterrupt)`로 감싸고, 예외 발생 시
     `state_writer.block()`로 원인을 기록한 뒤 `ReworkResult(issue_state=
     TaskState.BLOCKED, message="blocked: agent execution failed")`를
     반환한다. 루프를 중단시키지 않는다.
   - 동일한 보호를 `PollingService.run_once()`의 ready-task Agent 실행
     지점(`self.agent_runner.run(...)`)에도 적용해
     `except (Exception, KeyboardInterrupt)`로 확장했다.

2. **검증 실패 시 commit/push/PR 갱신 금지**
   - 기존 게이트(`DeliveryService.deliver()`, `ReworkService.process()`
     모두 `verification.passed`를 확인한 뒤에만 commit/push를 호출)가
     이미 이 요구를 충족하고 있음을 확인했고, 회귀 테스트를 유지/보강했다.
     추가 변경은 없음.

3. **Rework 시작 전 기존 PR head 브랜치 검증**
   - `devbot/delivery.py`에 `current_git_branch(repository)`와
     `CurrentBranchFn` 타입을 추가했다(`git rev-parse --abbrev-ref HEAD`).
   - `ReworkService`에 주입 가능한 `current_branch` 필드(기본값
     `current_git_branch`)를 추가했다. `process()`는 `request_changes()`
     직후, `apply_changes()` 호출 전에 로컬 브랜치와 인자로 받은 `branch`
     (기존 PR head로 간주되는 결정론적 브랜치명)를 비교하고, 불일치 시
     Agent를 실행하지 않고 즉시 `devbot:blocked`로 전환한다.

4. **`ReworkResult` 구조화**
   - `code_changed`, `verification_passed`, `committed`, `pushed`,
     `pr_reused`, `issue_state`(`TaskState`) 필드를 추가했다.
   - `PollingService._process_review_task()`의 blocked 판정을 문자열
     비교(`message == "blocked"`)에서 구조화된 `issue_state ==
     TaskState.BLOCKED` 비교로 교체했다. branch mismatch, agent 실행
     실패, 검증 실패 등 원인과 무관하게 `issue_state`만 보고 blocked를
     판정한다.

## 수정 파일

- `src/devbot/delivery.py` — `current_git_branch()`, `CurrentBranchFn` 추가.
- `src/devbot/rework.py` — 브랜치 검증, 예외 처리, `ReworkResult` 구조화.
- `src/devbot/polling.py` — ready-task Agent 실행에 `KeyboardInterrupt` 포함,
  rework blocked 판정을 `issue_state` 기반으로 교체.
- `tests/test_rework.py` — 브랜치 불일치, Agent 예외/`KeyboardInterrupt`,
  구조화된 필드에 대한 신규/보강 테스트.
- `tests/test_polling.py` — `issue_state` 기반 blocked 판정 테스트, ready-task
  `KeyboardInterrupt` 테스트.
- `tests/test_beta_smoke.py` — 신규 `current_branch` 주입 지점 반영(기존
  end-to-end rework 시나리오 유지).
- `results/010-rework-polling-integration.md`(본 파일).

## Checkpoint별 테스트

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-010-1 | `test_review_issue_without_unprocessed_comment_waits` | PASS |
| CP-010-2 | `test_review_comment_triggers_rework` | PASS |
| CP-010-3 (working 우선) | `test_working_issue_blocks_rework_even_when_review_exists` | PASS |
| CP-010-3 (브랜치 재사용) | `test_rework_reuses_existing_branch_and_pr` | PASS |
| CP-010-4 (중복 방지) | `test_successful_rework_marks_comment_processed` | PASS |
| CP-010-5 (성공 시 review 복귀) | `test_successful_rework_returns_to_review` | PASS |
| CP-010-6 (검증 실패 → blocked) | `test_failed_rework_moves_to_blocked` | PASS |
| CP-010-8 (dry-run 무부작용) | `test_rework_dry_run_does_not_push_or_mark_processed` | PASS |
| 신규: 브랜치 불일치 → blocked | `test_rework_blocks_when_local_branch_does_not_match_existing_pr_head` | PASS |
| 신규: Agent 예외 → blocked | `test_rework_blocks_when_agent_raises_exception` | PASS |
| 신규: Agent KeyboardInterrupt → blocked | `test_rework_blocks_when_agent_raises_keyboard_interrupt` | PASS |
| 신규: 구조화된 blocked 판정 | `test_rework_blocked_issue_state_moves_polling_to_blocked` | PASS |
| 신규: ready-task KeyboardInterrupt | `test_iteration_reports_agent_keyboard_interrupt_as_failure` | PASS |

## 검증 결과

| Command | Result |
|---|---|
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv sync` | PASS |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run ruff check .` | PASS |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run pytest` | PASS, 115 passed |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache DEVBOT_LOCK_FILE=/Users/luna/workspace/devbot/.devbot-verify.lock uv run devbot --once --dry-run` | PASS, exit 0 (`config/repositories.yaml`에 활성화된 저장소가 없어 GitHub 네트워크 호출 자체가 발생하지 않음) |

참고: 기본 `uv` 캐시 경로(`/Users/luna/.cache/uv`)가 샌드박스 권한에 막혀,
검증 명령은 저장소 내부 `.uv-cache`를 지정해 실행했다. `--once --dry-run`은
관리 저장소가 0개인 로컬 설정으로 실행되어 GitHub API를 호출하지 않고
종료했다 — 실제 저장소가 활성화된 환경에서의 재검증은 TODO로 남긴다.

## 기존 브랜치·PR 재사용 검증

- `PollingService._process_review_task()`는 `generate_branch_name(repository,
  issue.number, issue.title)`로 기존 Task 브랜치명을 결정론적으로 다시
  계산해 `ReworkService.process()`에 전달한다.
- `ReworkService.process()`는 이제 그 브랜치명과 로컬 워크스페이스의 실제
  체크아웃 브랜치(`current_git_branch()`)가 일치하는지 먼저 확인한다.
  불일치하면 Agent조차 실행하지 않고 blocked 처리한다(`test_rework_blocks_
  when_local_branch_does_not_match_existing_pr_head`).
- 성공 rework는 여전히 `push(repository, branch)`만 호출하고
  `create_pull_request()`를 호출하지 않는다
  (`test_rework_reuses_existing_branch_and_pr`,
  `test_reuse_existing_pr`).

## 위험 요소

- 현재 `review` Issue가 여러 개라면 수집 순서상 첫 번째 review Issue만
  한 번의 폴링에서 처리한다(기존 위험, 변경 없음).
- 브랜치 일치 검증은 `git rev-parse --abbrev-ref HEAD`로 로컬 체크아웃
  이름만 비교한다. 로컬 브랜치 이름이 우연히 기대값과 같지만 실제로는
  다른 커밋을 가리키는 경우(예: 강제 리셋된 동일 이름 브랜치)는 이 검사로
  걸러지지 않는다.
- Agent 실행 중 `KeyboardInterrupt`를 잡아 `blocked`로 전환한 뒤 흡수한다
  (재전파하지 않는다). `--once` 실행에서는 해당 반복이 끝나고 정상
  종료하므로 운영상 문제는 없지만, 장시간 폴링 루프에서 순수 Python 레벨
  Ctrl-C가 이 지점에서 발생하면 프로세스를 즉시 종료시키는 대신 다음
  반복으로 넘어간다는 점을 운영 시 인지해야 한다.

## TODO

- 실제로 GitHub 저장소가 `config/repositories.yaml`에 활성화된 환경에서
  `uv run devbot --once --dry-run`과 실사용 rework 시나리오(실제 PR
  댓글 → rework → push)를 재검증한다.
- 사용자 확인 후, 이 브랜치의 커밋을 push하고 (기존 PR이 없으므로) 구현
  PR을 새로 연다. Issue #15(`devbot:working`)를 이 PR과 연결한다.

## Improvement Suggestions

- `uv run devbot --once --dry-run`이 운영 GitHub API에 의존하지 않는 로컬
  smoke 모드를 별도로 제공하면 샌드박스/CI 검증 재현성이 좋아진다(이전
  세션과 동일한 제안, 여전히 유효).
- `current_branch` 검사를 브랜치 *이름*이 아니라 커밋 해시나 원격 추적
  정보까지 비교하도록 강화하면 "동일 이름, 다른 커밋" 위험을 추가로
  줄일 수 있다.
