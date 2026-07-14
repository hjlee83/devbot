# Result: Task 012

## Status

- COMPLETE. 리뷰 역할이 실제로 실행된다: `devbot:review` Issue에 연결된 PR의
  현재 head commit이 아직 자동 리뷰되지 않았으면 `reviewer_runner`를 실행해
  `# Review Summary`를 marker와 함께 PR에 게시한다. `REQUEST CHANGES`는 같은
  댓글에 `@devbot` 언급을 포함시켜 기존 Task 010 rework 경로가 다음 사이클에
  그대로 감지하게 하고, `MERGE READY`는 자동 Merge 없이 `devbot:review`로
  남는다.
- 폴링의 선택 로직을 "전역 단일 작업" 게이트에서 "저장소별" 게이트로 재구성하고,
  `Job`/`JobType`(`devbot.models`) + `select_jobs`(`devbot.scheduler`, 순수
  함수)로 구현·rework·리뷰 후보를 한 사이클에서 함께 스케줄링한다.
  `MAX_CONCURRENT_JOBS`(기본값 1, 검증 추가)가 한 사이클에 실제로 실행할 job
  수를 제한하며, 같은 저장소·같은 Issue는 절대 동시에 실행되지 않는다.
- `PollingService.run_once()`는 `run_cycle()`의 결과 리스트 중 첫 번째를
  반환하는 얇은 래퍼로 유지해, 기본값(`MAX_CONCURRENT_JOBS=1`)에서 기존 호출부
  전체(140여 개 기존 테스트)가 동작 변경 없이 그대로 통과한다.

## 이번 세션에서 추가/변경한 내용

1. **`Job`/`JobType`** (`src/devbot/models.py`) — `JobType`은 `REWORK`/
   `REVIEW`/`IMPLEMENT` 세 값만 가지며 특정 Agent 제품명을 포함하지 않는다.
   `Job`은 스케줄링에 필요한 최소 정보(`job_type`, `task: IssueTask`)만
   가진다 — 연결된 PR/댓글 같은 무거운 상태는 선택된 뒤 실행 시점에
   재조회한다.
2. **`PullRequest.head_sha`** (`src/devbot/github_client.py`) — `head.sha`를
   파싱해 추가. 리뷰 marker가 PR head *commit*(브랜치가 아니라) 단위로
   동작하려면 필요하다.
3. **`MAX_CONCURRENT_JOBS` 검증** (`src/devbot/config.py`) — 정수 파싱은
   기존에 있었지만 범위 검증이 없었다. `_require_positive_int`를 추가해
   0/음수는 `ConfigError`로 설정 로딩 단계에서 실패한다(정수가 아닌 값은
   기존 `_parse_int`가 이미 처리).
4. **`src/devbot/scheduler.py`(신규)** — `select_jobs(candidates,
   max_concurrent_jobs)`: I/O 없는 순수 함수. REWORK > REVIEW > IMPLEMENT
   순서로 정렬하고(동순위는 기존 Queue 규칙: 우선순위 라벨 → 생성 시각 →
   Issue 번호), 저장소당 최대 1개만 선택한다. "동일 저장소는 동시에
   실행하지 않는다"는 이 한 함수의 저장소 dedup만으로 충족되어 별도
   락(lock) 구조가 필요 없다.
5. **`src/devbot/review.py`(신규)** — `ReviewService.process()`:
   - `state_writer.request_changes()`로 Issue를 `working`으로 claim.
   - `reviewer_runner.run()` 실행. 예외/`KeyboardInterrupt`는 `blocked`로
     흡수(Task 010 `ReworkService`와 동일 패턴).
   - `AgentRunResult.failed`(Task 011 리뷰에서 도입)로 실행 실패를 판정 —
     `blocked`.
   - stdout에서 `MERGE READY`/`REQUEST CHANGES` 중 정확히 하나만 있는지
     확인 — 둘 다 있거나 둘 다 없으면 `blocked`.
   - `<!-- devbot:auto-review head=<sha> -->` marker를 덧붙여 PR에 댓글
     게시. `REQUEST CHANGES`일 때만 `@devbot` 언급을 추가로 포함시킨다.
   - 성공하면 `mark_for_review()`로 Issue를 다시 `review`로 되돌린다.
   - `dry_run=True`면 위 어떤 단계도 실행하지 않고 즉시 반환한다(Agent
     실행도, 상태 쓰기도, 댓글 게시도 없음).
   - reviewer prompt(`build_review_prompt`)는 Task 계약서/Result 문서
     경로를 코드가 직접 계산하지 않고 **Issue 본문을 그대로 포함**시켜
     리뷰 Agent가 관례(`docs/09-task-contract-standard.md`)대로 그 안에서
     찾게 한다 — DevBot에는 Issue 번호→Task 번호 매핑이 따로 없기 때문.
6. **`src/devbot/polling.py` 대규모 리팩터**:
   - `_collect_job_candidates()`: 저장소별로 `devbot:working` Issue가
     있으면 그 저장소는 이번 사이클에 후보를 만들지 않는다(회귀:
     "working이 rework/ready를 막는다"가 저장소 단위로 좁혀짐 - 단일
     저장소 테스트는 전부 기존과 동일하게 통과).
   - `devbot:review` Issue가 있는 저장소는 그 Issue(들)에 대해서만
     REWORK/REVIEW 후보를 만들고, `ready` Issue는 후보에서 제외한다(기존
     "review가 ready를 막는다" 규칙을 저장소 단위로 유지).
   - **rework/review 후보 판정이 이제 연결된 PR의 댓글을 본다**(이전에는
     추적 중인 Issue 자체의 댓글을 봤다). 이 저장소의 실제 리뷰 피드백은
     항상 PR에 달리므로, "Issue 댓글"이 아니라 "PR 댓글"을 보는 것이
     맞는 동작이다. 기존 rework 테스트 fixture들의 `comments_by_issue`
     키를 Issue 번호에서 PR 번호로 옮겨 이 변경을 반영했다.
   - `run_cycle()`(신규): 후보 수집 → `select_jobs` → 선택된 job 실행 →
     `list[PollingResult]` 반환. 선택된 job이 1개면 순차 실행(기존과
     동일한 경로), 2개 이상이면 `ThreadPoolExecutor`로 병렬 실행한다.
   - `run_once()`: `run_cycle()[0]`의 얇은 래퍼로 축소.
   - `run_forever()`는 (기존 테스트 호환을 위해) 여전히 `run_once()`를
     반복 호출한다 — `run_once()`가 내부적으로 `run_cycle()`을 그대로
     실행하므로 선택된 job은 전부 실행되고, 반환값만 첫 번째로 줄어든다.
7. **`src/devbot/main.py`** — `ReviewService`를 구성해 `PollingService`에
   `review_service=`로 주입. `--once`는 `run_once()` 대신 `run_cycle()`을
   호출해 한 사이클에서 선택된 모든 job의 결과를 로그로 남기고, 하나라도
   실패 상태면 종료 코드 1을 반환한다(계약서: "`--once`는 한 사이클에서
   최대 MAX_CONCURRENT_JOBS개 후보를 선택해 모두 종료한 뒤 구조화된
   결과를 반환한다").

## 수정/신규 파일

- `src/devbot/models.py` — `JobType`, `Job`.
- `src/devbot/github_client.py` — `PullRequest.head_sha`.
- `src/devbot/config.py` — `MAX_CONCURRENT_JOBS` 범위 검증.
- `src/devbot/scheduler.py`(신규) — `select_jobs`.
- `src/devbot/review.py`(신규) — `ReviewService`, marker/프롬프트 헬퍼.
- `src/devbot/polling.py` — 저장소별 후보 수집 + `run_cycle` 재구성.
- `src/devbot/main.py` — `ReviewService` 배선, `--once`가 `run_cycle` 사용.
- `tests/test_scheduler.py`(신규), `tests/test_review.py`(신규).
- `tests/test_config.py`, `tests/test_polling.py`, `tests/test_main_loop.py`,
  `tests/test_github_client.py` — 신규/보강 테스트, PR-댓글 기준 rework
  fixture 이전.
- `AGENTS.md` — "구현 AI"/"리뷰 AI" → "구현 역할"/"리뷰 역할", 11절
  "자동 트리거 규칙" 신설, 서문의 특정 제품명 언급 제거.
- `docs/04-agent-system.md`, `docs/08-beta-runbook.md`, `docs/00-roadmap.md`
  — 자동 리뷰 릴레이, marker 규칙, 저장소별 스케줄링 반영.
- `results/012-role-based-polling-orchestration.md`(본 파일).

`.env.example`은 `MAX_CONCURRENT_JOBS=1`이 Task 011 이전부터 이미 있어
변경하지 않았다.

## Checkpoint별 테스트

| Checkpoint | 필수 동작 | 테스트 이름 | 위치 | 결과 |
|---|---|---|---|---|
| CP-012-1 | `devbot:ready` Issue가 구현 역할 job으로 선택된다 | `test_ready_issue_triggers_implement_job` | test_polling.py | PASS |
| CP-012-2 | 아직 리뷰되지 않은 PR head가 리뷰 역할 job으로 선택된다 | `test_unreviewed_pr_head_triggers_review_job` | test_polling.py | PASS |
| CP-012-3 | 동일 PR head SHA는 marker로 중복 리뷰되지 않는다 | `test_review_marker_prevents_duplicate_review_for_same_head` | test_review.py | PASS |
| CP-012-4 | PR head SHA가 바뀌면 리뷰 역할이 다시 실행된다 | `test_new_pr_head_triggers_review_again` | test_polling.py | PASS |
| CP-012-5 | `REQUEST CHANGES` 리뷰 댓글은 rework trigger를 포함한다 | `test_request_changes_review_comment_triggers_rework` | test_review.py | PASS |
| CP-012-6 | `MERGE READY` 리뷰 댓글은 rework trigger를 포함하지 않는다 | `test_merge_ready_review_comment_does_not_trigger_rework` | test_review.py | PASS |
| CP-012-7 | rework 후보가 review와 신규 구현보다 우선한다 | `test_rework_job_has_highest_priority` | test_scheduler.py | PASS |
| CP-012-8 | reviewer 실패는 Issue를 blocked로 전환한다 | `test_reviewer_failure_moves_issue_to_blocked` | test_review.py | PASS |
| CP-012-9 | 유효하지 않은 Review Summary는 blocked 처리된다 | `test_invalid_review_summary_moves_issue_to_blocked` | test_review.py | PASS |
| CP-012-10 | concurrency 기본값은 1이다 | `test_max_concurrent_jobs_defaults_to_one` | test_config.py | PASS |
| CP-012-11 | concurrency가 2 이상이면 서로 다른 저장소 job을 병렬 실행할 수 있다 | `test_parallel_jobs_run_for_different_repositories` | test_scheduler.py | PASS |
| CP-012-12 | 같은 저장소 job은 concurrency가 남아도 동시에 실행하지 않는다 | `test_same_repository_jobs_are_not_run_in_parallel` | test_scheduler.py | PASS |
| CP-012-13 | 실패한 job도 concurrency slot을 반환한다 | `test_failed_job_releases_concurrency_slot` | test_polling.py | PASS |
| CP-012-14 | 잘못된 concurrency 설정은 로딩 단계에서 실패한다 | `test_invalid_max_concurrent_jobs_is_rejected` | test_config.py | PASS |
| CP-012-15 | dry-run review polling은 Agent와 GitHub write를 수행하지 않는다 | `test_review_polling_dry_run_has_no_side_effects` | test_review.py | PASS |

추가로 작성한 회귀/경계 테스트(계약서 "추가 회귀 테스트" 8개 항목 대응):

- 기존 ready 최초 구현 흐름: 기존 스위트 전체(예:
  `test_iteration_invokes_agent_with_selected_task`) 무변경 통과로 확인.
- 기존 rework 흐름: `test_polling_detects_unprocessed_devbot_review_comment`
  외 CP-010 테스트 전체(댓글 소스만 PR로 이전, 동작은 동일) 통과.
- 기존 `MAX_CONCURRENT_JOBS` 미설정 배포:
  `test_max_concurrent_jobs_defaults_to_one`.
- 연결 PR이 없는 review Issue:
  `test_review_issue_without_linked_pull_request_is_reported_as_error`(기존,
  PR 댓글 기준으로 여전히 유효).
- reviewer stdout이 비어 있는 경우:
  `test_empty_reviewer_stdout_is_an_invalid_review`.
- marker 댓글 게시 후 상태 전환 실패의 부분 성공 처리:
  `test_state_transition_failure_after_posting_comment_is_not_silently_lost`
  (댓글은 이미 게시된 뒤 `mark_for_review`가 실패해도 그 게시 자체는
  사라지지 않고, 예외는 `devbot.polling`이 잡아 `ITERATION_ERROR`로 보고함
  — `ReworkService`가 자신의 `mark_for_review` 호출을 감싸지 않는 것과
  동일한 선례를 따름).
- 여러 저장소 중 하나의 job 실패가 다른 job 완료를 막지 않는 경우:
  `test_failed_job_releases_concurrency_slot`,
  `test_repository_error_during_candidate_collection_does_not_block_other_repositories`.
- `KeyboardInterrupt` 또는 timeout에도 slot이 반환되는 경우: 단일 job
  경로는 기존 `test_iteration_reports_agent_keyboard_interrupt_as_failure`가
  계속 커버한다(예외를 흡수해 `AGENT_FAILED`로 반환). 병렬 경로는
  `ThreadPoolExecutor`가 각 job을 독립된 worker에서 실행하고 `with` 블록이
  항상 worker 완료를 기다린 뒤 반환하므로, 한 worker의 실패/예외가 다른
  worker의 slot을 붙잡지 않는다 — 이는 `test_failed_job_releases_concurrency_slot`가
  구조적으로 증명한다. Executor 레벨의 명시적 timeout은 이번 Task
  범위에서 추가하지 않았다(개별 Agent Runner 자체의 timeout으로 완화됨 —
  Risk 절 참고).

## 검증 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS |
| `uv run pytest` | PASS, 172 passed |
| `uv run devbot --once --dry-run` | PASS, `no_ready_task`; 시작 로그에 `실행 구성: implementer=claude reviewer=codex dry_run=True` 출력 확인 |

## 회귀 확인

- `PollingService.run_once()`는 `run_cycle()[0]`로 축소됐지만, 반환값은
  `MAX_CONCURRENT_JOBS=1`(기본값)에서 이전과 100% 동일하다 — 기존
  테스트(Task 001~011, 약 140여 개)가 무변경으로 전부 통과했다.
- `run_forever()`는 여전히 `run_once()`를 호출한다(내부적으로
  `run_cycle()`을 실행하므로 선택된 job은 모두 실행되고 반환값만 첫
  번째로 줄어든다) — `run_forever`가 `run_once`를 호출한다고 가정하는
  기존 테스트(`test_shutdown_signal_stops_loop_gracefully` 등)와의 호환을
  위한 의도적 선택.
- `devbot.main`의 `--once` 경로만 `run_cycle()`을 직접 호출하도록
  바꿨다(계약서가 명시적으로 요구) — 이 변경에 맞춰
  `tests/test_main_loop.py`의 관련 테스트 3개를 `run_cycle` mock으로
  갱신했다.
- rework/review 후보가 이제 PR 댓글을 조회한다는 변경은 CP-010 기존 테스트
  전부의 fixture(`comments_by_issue` 키)를 Issue 번호에서 PR 번호로
  옮기는 것으로 반영했다 — 코드 동작(재사용 브랜치, blocked 판정 등)은
  바뀌지 않았다.

## 위험 요소

- reviewer stdout을 PR 댓글로 그대로 게시하므로, Agent가 불필요한 로그를
  섞어 출력하면 그대로 PR에 남는다.
- marker 댓글 게시 후 `mark_for_review`가 실패하면 리뷰는 완료됐지만
  Issue 라벨은 `working`에 남는 부분 성공이 생길 수 있다(위 회귀 테스트로
  방어는 했지만 자동 복구는 하지 않는다 — 운영자가 수동으로 라벨을
  `devbot:review`로 되돌려야 한다).
- `REQUEST CHANGES`/`MERGE READY` 판정은 정확히 하나의 리터럴 문자열
  포함 여부로만 결정한다. 리뷰 Agent가 예시로 두 단어를 모두 언급하는
  등 형식을 지키지 않으면 `blocked`로 안전하게 막히지만, 그 경우 매
  사이클 같은 head에 대해 반복 리뷰를 시도하지는 않는다(marker가 이미
  게시된 댓글에 남아있지 않으므로 - 실패 시에는 댓글 자체를 게시하지
  않기 때문에 marker도 없어, 다음 사이클에 실제로 다시 시도된다는 점은
  의도된 동작이다).
- `MAX_CONCURRENT_JOBS`를 2 이상으로 올리면 CPU/메모리/Agent API rate
  limit 사용량이 늘어난다. 같은 저장소 병렬 실행은 이번 Task에서 여전히
  금지된다(로컬 workspace 충돌 방지).
- 여러 데몬 프로세스가 동시에 뜨면 프로세스 내부 `ThreadPoolExecutor`만으로는
  프로세스 간 중복 실행을 막을 수 없다 — 기존 `ProcessLock`(단일 프로세스
  락)을 계속 유지해야 한다(변경 없음).
- reviewer prompt가 Task 계약서 경로를 코드로 계산하지 않고 Issue 본문에
  의존하므로, Issue 본문이 관례(계약서 경로 명시)를 따르지 않으면 리뷰
  Agent가 계약서를 못 찾을 수 있다 — `docs/09-task-contract-standard.md`의
  기존 관례에 이미 의존하고 있다.

## TODO

- Executor 레벨 job timeout(현재는 개별 Agent Runner의 자체 timeout에만
  의존).
- reviewer stdout을 게시 전에 정리(strip 불필요 로그)하는 후처리.
- marker 게시 후 상태 전환 실패를 자동 복구(현재는 사람이 라벨을 수동
  정정).
- 실제 GitHub 저장소 + 실제 Claude/Codex CLI가 설치된 환경에서 전체
  ready → review → 자동 리뷰 → REQUEST CHANGES → rework → 재리뷰 →
  MERGE READY 릴레이를 1회 수동 재검증.
