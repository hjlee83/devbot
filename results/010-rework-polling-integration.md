# Result: Task 010

## Status

- COMPLETE. `ReworkService`가 `PollingService.run_once()`에 연결되어,
  `devbot:review` 상태 Issue의 처리되지 않은 `@devbot` PR 댓글을 감지하면
  새 브랜치/PR 없이 기존 것을 재사용해 rework를 수행하고, 성공하면
  `review`로 복귀, 실패하면 원인과 함께 `blocked`로 전환한다.
- 이 결과 문서는 두 번의 작업 흐름이 합쳐진 것이다: 먼저 초기 계약서
  기준으로 폴링 연결 + 안전장치(브랜치 불일치 감지, Agent 예외 처리,
  구조화된 `ReworkResult`)를 구현했고, 이후 계약서가 v1.1.0으로 갱신되며
  체크포인트 이름이 `CP-010-1`~`CP-010-8`로 확정되어 그에 맞춰
  `tests/test_polling.py`의 테스트 이름을 정리/보강했다(아래 "체크포인트
  이름 정리" 참고). 구현 자체(코드)는 바뀌지 않았고, 테스트만 계약서
  이름에 맞춰 정리·추가했다.

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
  rework blocked 판정을 `issue_state` 기반으로 교체. **1차 리뷰 반영**:
  `find_linked_pull_request()`(신규) 추가, `_process_review_task()`가
  `generate_branch_name()` 재계산 대신 `list_pull_requests()` +
  `find_linked_pull_request()`로 실제 연결된 PR을 조회하도록 교체(연결된
  PR이 없으면 `ITERATION_ERROR`), "미처리 댓글 없음" 조기 반환을 PR 조회
  전으로 이동(불필요한 API 호출 방지).
- `src/devbot/github_client.py` — **1차 리뷰 반영**: `PullRequest`,
  `list_pull_requests()`(신규, 읽기 전용) 추가.
- `tests/test_rework.py` — 브랜치 불일치, Agent 예외/`KeyboardInterrupt`,
  구조화된 필드에 대한 신규/보강 테스트.
- `tests/test_polling.py` — `issue_state` 기반 blocked 판정 테스트, ready-task
  `KeyboardInterrupt` 테스트, CP-010-1~8 이름에 맞춘 rework-polling
  테스트 8개(리네임 3개 + 신규 5개).
- `tests/test_beta_smoke.py` — 신규 `current_branch` 주입 지점 반영(기존
  end-to-end rework 시나리오 유지).
- `src/devbot/main.py` — `ReworkService` 구성 및 `PollingService`에 주입
  (`_apply_rework_changes`로 `AgentRunner`와 연결).
- `docs/00-roadmap.md` — Phase 1/2 체크 상태를 실제 구현에 맞게 갱신,
  Task 009/010 항목 추가.
- `README.md` — Run 절에 review/rework 우선순위 흐름 반영.
- `docs/08-beta-runbook.md` — 전체 흐름 다이어그램에 rework 분기 추가,
  "ReworkService 미연결" 운영 체크리스트 항목을 실제 상태로 갱신.
- `results/010-rework-polling-integration.md`(본 파일).

## Checkpoint별 테스트 (v1.1.0 계약서 기준)

계약서가 v1.1.0으로 갱신되며 `CP-010-1`~`CP-010-8`의 필수 테스트 이름이
확정됐다. 구현 코드는 그대로 두고, `tests/test_polling.py`의 테스트
이름을 계약서와 정확히 맞췄다(의미가 그대로인 것은 이름만 변경, 계약서가
명시적으로 요구하지만 기존 테스트가 다른 각도에서만 다루던 체크포인트는
새 테스트를 추가).

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-010-1 | review Issue의 미처리 `@devbot` 댓글을 감지한다 | `test_polling_detects_unprocessed_devbot_review_comment` | PASS |
| CP-010-2 | rework가 ready 작업보다 우선 처리된다 | `test_rework_is_prioritized_over_ready_task` | PASS |
| CP-010-3 | 기존 브랜치와 기존 PR만 재사용한다 | `test_rework_reuses_existing_branch_and_pull_request` | PASS |
| CP-010-4 | 처리된 댓글은 다시 실행하지 않는다 | `test_processed_review_comment_is_not_reworked_again` | PASS |
| CP-010-5 | rework 성공 후 review 상태로 복귀한다 | `test_successful_polled_rework_returns_to_review` | PASS |
| CP-010-6 | rework 실패 시 blocked 상태와 원인을 기록한다 | `test_failed_polled_rework_moves_to_blocked_with_reason` | PASS |
| CP-010-7 | rework 대상이 없으면 기존 ready 폴링이 유지된다 | `test_ready_polling_still_runs_when_no_rework_exists` | PASS |
| CP-010-8 | dry-run에서는 GitHub와 Git에 쓰기 부작용이 없다 | `test_rework_polling_dry_run_has_no_side_effects` | PASS |

추가로 유지한 안전장치 테스트(체크포인트 필수 목록에는 없지만 "Risk"
절의 위험을 직접 방어함, 전부 `tests/test_rework.py`/`tests/test_polling.py`):
`test_working_issue_blocks_rework_even_when_review_exists`(working이
review보다 항상 우선 — 동시 활성 작업 1개 규칙),
`test_rework_blocks_when_local_branch_does_not_match_existing_pr_head`
(로컬 체크아웃이 기대 브랜치와 다르면 Agent 실행 전에 즉시 blocked),
`test_rework_blocks_when_agent_raises_exception`,
`test_rework_blocks_when_agent_raises_keyboard_interrupt`,
`test_iteration_reports_agent_keyboard_interrupt_as_failure`(ready-task
경로도 동일하게 보호).

## 검증 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS |
| `uv run pytest` | PASS, 122 passed |
| `DEVBOT_LOCK_FILE=<격리된 경로> uv run devbot --once --dry-run` | PASS, exit 0 (`config/repositories.yaml`에 활성화된 저장소가 없어 GitHub 네트워크 호출 자체가 발생하지 않음) |

참고: `/tmp/devbot.lock`을 실제로 실행 중인 다른 DevBot 프로세스가 쥐고
있어(`lsof`로 확인), 검증 시 `DEVBOT_LOCK_FILE`을 격리된 경로로 지정해
그 프로세스와 충돌하지 않도록 했다. `--once --dry-run`은 관리 저장소가
0개인 로컬 설정으로 실행되어 GitHub API를 호출하지 않고 종료했다 —
실제 저장소가 활성화된 환경에서의 재검증은 TODO로 남긴다.

## 기존 브랜치·PR 재사용 검증

**1차 리뷰 반영 (2026-07-14)**: 원래 `PollingService._process_review_task()`는
`generate_branch_name(repository, issue.number, issue.title)`로 브랜치명을
"재계산"만 했을 뿐, Issue에 실제로 연결된 PR을 조회하지 않았다. 리뷰에서
이것이 계약서의 "Issue와 연결된 기존 Pull Request 식별" 요구사항과
CP-010-3("기존 브랜치와 기존 PR만 재사용한다")을 충분히 만족하지 못한다는
blocker로 지적됐다 — 브랜치 네이밍 규칙이 바뀌거나 기존 PR의 실제 head가
다르면 이 재계산 값이 조용히 틀린 브랜치를 가리킬 수 있었다.

수정: `GitHubClient.list_pull_requests()`(신규, 읽기 전용)로 저장소의
열린 PR을 조회하고, `polling.find_linked_pull_request()`(신규 순수
함수)가 PR 본문에서 GitHub의 실제 closing-keyword 규약(`Closes #N`,
`Fixes #N`, `Resolves #N`, 대소문자 무관)으로 Issue를 참조하는 PR을
찾는다 — `delivery.build_pr_body()`가 항상 `Closes #{issue.number}`로
PR을 열기 때문에 DevBot이 만든 모든 PR이 이 방식으로 정확히 식별된다.
연결된 PR을 찾으면 그 PR의 실제 `head.ref`를 `ReworkService.process()`에
전달하고, 찾지 못하면(review Issue인데 연결된 PR이 없는 이상 상태)
브랜치를 추측하지 않고 `ITERATION_ERROR`로 실패를 구조화해서 반환한다
(`test_review_issue_without_linked_pull_request_is_reported_as_error`).

- `ReworkService.process()`는 여전히 전달받은 브랜치명(이제는 실제 PR
  head)과 로컬 워크스페이스의 실제 체크아웃 브랜치(`current_git_branch()`)가
  일치하는지 확인한다. 불일치하면 Agent조차 실행하지 않고 blocked
  처리한다(`test_rework_blocks_when_local_branch_does_not_match_existing_pr_head`).
- 성공 rework는 여전히 `push(repository, branch)`만 호출하고
  `create_pull_request()`를 호출하지 않는다
  (`test_rework_reuses_existing_branch_and_pull_request` — 이제 실제 PR의
  head가 `generate_branch_name()`이 만들었을 이름과 *다른* 값으로
  검증해, 재계산이 아니라 진짜 조회 결과가 쓰이는지 증명한다).

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
- `PollingService`가 `review` Issue를 고를 때 여러 개 중 하나만(수집
  순서상 첫 번째) 처리한다 — 우선순위 규칙(예: `priority:*` 라벨)을
  적용할지는 이후 Task로 남긴다.

## Improvement Suggestions

- `uv run devbot --once --dry-run`이 운영 GitHub API에 의존하지 않는 로컬
  smoke 모드를 별도로 제공하면 샌드박스/CI 검증 재현성이 좋아진다(이전
  세션과 동일한 제안, 여전히 유효).
- `current_branch` 검사를 브랜치 *이름*이 아니라 커밋 해시나 원격 추적
  정보까지 비교하도록 강화하면 "동일 이름, 다른 커밋" 위험을 추가로
  줄일 수 있다.
