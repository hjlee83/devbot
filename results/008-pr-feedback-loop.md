# Result: Task 008

## Status
- COMPLETE

## 구현 요약

`tasks/008-pr-feedback-loop.md` 범위대로, `review` 상태 Issue의 PR에
새로운 `@devbot` 요청이 달렸을 때 이를 감지해 같은 브랜치/PR 위에서
다시 작업하고, 성공하면 `review`로 복귀·실패하면 `blocked`로 이동시키는
"rework" 루프를 구현했다. Task 006(`IssueStateWriter`)·Task
007(`delivery.py`의 검증/커밋/푸시 함수)을 그대로 재사용하고, PR을
새로 만들지 않는다는 점이 Task 007의 `DeliveryService.deliver()`와의
핵심 차이다.

- **`src/devbot/rework.py`(신규)**: `ReworkService.process()`가 전체
  흐름을 담당한다.
  1. `find_unprocessed_devbot_comments()`가 댓글 목록에서 본문에
     `@devbot`이 포함되고 아직 `eyes` 리액션이 없는(=처리되지 않은)
     댓글만 골라낸다. 로컬 상태 파일 없이 GitHub 네이티브 메커니즘
     (리액션)으로 "처리됨"을 추적한다(`docs/07-decisions.md`의
     "GitHub as the queue" 원칙 유지).
  2. 처리할 댓글이 없으면 `triggered=False`로 즉시 반환하고 아무 것도
     하지 않는다.
  3. 처리할 댓글이 있으면 `IssueStateWriter.request_changes()`로
     Issue를 `review → working`으로 되돌린 뒤(에이전트 실행 *전에*),
     주입된 `apply_changes(repository, issue, comment)`를 호출해 실제
     수정을 적용한다.
  4. `run_verification_commands()`(Task 007과 동일한 `uv run ruff
     check .` → `uv run pytest`)를 다시 실행한다.
  5. 검증 실패 시 커밋/푸시/댓글 리액션/`review` 복귀를 전혀 하지 않고
     `IssueStateWriter.block()`으로 실패한 커맨드와 출력을 근거로 남긴
     채 `blocked`로 이동한다.
  6. 검증 통과 시 `commit_all_changes()`/`push_task_branch()`로 **기존
     브랜치에만** 추가 커밋을 푸시한다(새 브랜치·새 PR을 만들지
     않음 — 같은 브랜치에 푸시하면 GitHub가 기존 PR의 diff를 자동으로
     갱신한다). `GitHubWriteClient.add_reaction_to_comment()`로 처리한
     댓글에 `eyes` 리액션을 남겨 다음 폴링에서 같은 댓글이 다시
     트리거되지 않게 하고, `IssueStateWriter.mark_for_review()`로
     Issue를 다시 `review`로 되돌린다.
  - `run_verification`/`commit`/`push`는 Task 007의 `delivery.py`가
    정의한 것과 동일한 타입(`RunVerificationFn`/`CommitFn`/`PushFn`)을
    그대로 재사용해 주입 가능하다.
- **`src/devbot/github_client.py`(수정, 읽기 전용 유지)**:
  `PullRequestComment`(id, author, body, created_at, `reactions: dict[str,
  int]`)와 `list_issue_comments()`를 추가했다. `reactions`는 GitHub가
  주는 리액션별 개수를 그대로 담을 뿐 "처리됨"의 의미는 부여하지 않는다
  — 그 해석은 `rework.py`가 담당한다(`polling.py`가 `devbot:*` 라벨을
  해석하는 것과 같은 분리 원칙). `test_client_exposes_read_operations_only`
  (Task 002)의 허용 목록에 `list_issue_comments`를 추가해 여전히
  "쓰기 없음" 계약을 유지한다.
- **`src/devbot/github_write_client.py`(수정)**: `add_reaction_to_comment()`
  추가(`POST .../issues/comments/{id}/reactions`).
- **`src/devbot/issue_state.py`(수정, Task 006)**: `claim`/`block`/
  `mark_for_review`/`request_changes`가 이제 (기존처럼 `None`이 아니라)
  갱신된 `GitHubIssue`를 반환한다. 라벨을 실제로 바꾼 뒤(또는 dry-run이면
  변경 없이) 그 결과를 돌려주므로, `ReworkService`처럼 한 호출 안에서
  여러 번 전환을 이어갈 때 GitHub를 다시 조회하지 않고도 최신 라벨
  상태로 다음 전환을 검증할 수 있다. 기존 Task 006 테스트는 반환값을
  사용하지 않으므로 전부 그대로 통과한다.

이번 Task도 `PollingService`에 연결하지 않았다 — `ReworkService`가
실제로 트리거되려면 "review 상태인 Issue의 PR 댓글을 주기적으로
조회"하는 별도의 폴링 경로가 필요한데, 그 경로 자체는 이번 체크포인트
어디에도 없다. Task 005~007과 동일한 이유로, 능력 자체(감지 + 상태
전환 + 브랜치/PR 재사용 + 처리 표시)를 독립적으로 구현하고 실제
폴링 루프 연결은 이후로 남겼다.

## 생성/수정 파일

- `src/devbot/rework.py` — 신규
- `src/devbot/github_client.py` — `PullRequestComment`, `list_issue_comments()` 추가
- `src/devbot/github_write_client.py` — `add_reaction_to_comment()` 추가
- `src/devbot/issue_state.py` — 전환 메서드가 갱신된 `GitHubIssue`를 반환하도록 수정
- `tests/test_rework.py` — 신규
- `tests/test_github_client.py` — `list_issue_comments` 테스트 추가, 읽기 전용 계약 목록 갱신
- `tests/test_github_write_client.py` — `add_reaction_to_comment` 테스트 추가
- `results/008-pr-feedback-loop.md` — 본 파일

`polling.py`, `delivery.py`, `config.py`, `models.py`는 변경하지 않았다.

## CP-008-1 ~ CP-008-6별 테스트 이름과 결과

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-008-1 | 처리되지 않은 `@devbot` 댓글만 rework를 트리거함 | `test_only_unprocessed_devbot_comments_trigger_rework` | PASS |
| CP-008-2 | 에이전트 실행 전에 review가 working으로 돌아감 | `test_rework_moves_review_to_working` | PASS |
| CP-008-3 | 기존 브랜치와 PR을 재사용함 | `test_rework_reuses_existing_branch_and_pr` | PASS |
| CP-008-4 | 성공적인 업데이트 후 댓글이 처리됨으로 표시됨 | `test_successful_rework_marks_comment_processed` | PASS |
| CP-008-5 | 실패한 rework는 근거와 함께 blocked로 이동함 | `test_failed_rework_moves_to_blocked` | PASS |
| CP-008-6 | 성공한 rework는 review로 복귀함 | `test_successful_rework_returns_to_review` | PASS |

추가로 작성한 테스트(필수 아님, 경계/보강):
`test_no_unprocessed_devbot_comments_does_not_trigger_rework`
(`tests/test_rework.py`),
`test_list_issue_comments_follows_pagination_and_parses_reactions`
(`tests/test_github_client.py`),
`test_add_reaction_to_comment_sends_post_with_content`
(`tests/test_github_write_client.py`).

`tests/test_rework.py` 8개(리뷰 반영으로 회귀 테스트 1개 추가),
`tests/test_issue_state.py` 회귀 테스트 1개 전부 PASS. 전체 스위트
93개 전부 PASS.

## 검증 명령 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (93 passed) |
| `uv run devbot --once` | PASS (exit 0, `no_ready_task`) |

## 남은 TODO

- `PollingService`가 `review` 상태 Issue에 대해 PR 댓글을 조회하고
  `ReworkService.process()`를 호출하는 경로는 아직 없다.
- `apply_changes`(실제로 에이전트를 다시 실행해 수정하는 부분)는 이번
  Task에서 호출자가 주입하는 콜백일 뿐, `AgentRunner`/`build_agent_prompt`와
  실제로 연결하는 배선은 없다.
- 여러 개의 처리되지 않은 `@devbot` 댓글이 동시에 쌓여 있으면
  `unprocessed[0]`(가장 오래된 것, GitHub가 댓글을 생성 순으로 반환)
  하나만 이번 호출에서 처리한다 — 나머지는 다음 폴링 사이클에서 다시
  감지된다. 여러 댓글을 한 번에 합쳐 처리하는 로직은 없다.

## 위험 요소

- "처리됨" 표시를 `eyes` 리액션 하나로만 판단한다. 만약 사람이 실수로
  같은 댓글에 직접 👀 리액션을 남기면 DevBot이 그 댓글을 이미 처리된
  것으로 오인해 건너뛸 수 있다 — 로컬 상태를 두지 않기로 한
  트레이드오프(`docs/07-decisions.md`)를 따른 결과다.
- Task 006의 `IssueStateWriter` 메서드 반환 타입을 `None → GitHubIssue`로
  바꿨다. Task 006은 이미 병합됐으므로 이건 이미 배포된 코드에 대한
  수정이다 — 다만 하위 호환(기존 호출부는 반환값을 쓰지 않음)이고,
  `ReworkService`처럼 한 호출 안에서 여러 전환을 이어가려면 꼭 필요한
  변경이라 판단해 포함시켰다.
- Task 006/007과 마찬가지로 로컬 샌드박스에 `uv`가 기본 설치돼 있지
  않아 `pip install --user uv`로 설치한 뒤 검증 명령을 실행했다.
- 처음 PR을 열 때는 아직 병합되지 않은 Task 007 브랜치
  (`feature/task-007-push-and-pr`, #8) 위에 쌓았다 — 아래 "리뷰 반영"에
  기록된 대로 #8이 merge된 뒤 base를 `main`으로 정리했다.

## 리뷰 반영

1차 PR 리뷰(REQUEST CHANGES)에서 다음 두 가지 blocker가 지적되어
수정했다:

- **Blocker 1 (base 브랜치)**: PR #9를 열 때 아직 병합되지 않은 Task
  007 브랜치(#8) 위에 쌓았는데, 리뷰 시점에는 #8이 이미 merge된
  상태였다. Task 007(#8)에서 했던 것과 동일한 절차로 해결했다: 두
  커밋(`a1b6821`, `b6cc4cd`)을 `git rebase --onto origin/main
  feature/task-007-push-and-pr feature/task-008-pr-feedback-loop`로
  최신 `origin/main`(Task 007 squash-merge 포함) 위로 재배치하고,
  `git push --force-with-lease`로 갱신한 뒤 `gh pr edit 9 --base
  main`으로 base를 변경했다. PR 커밋 수가 2개로 줄고 diff도 Task 008
  변경분만 남는 것을 확인했다. PR 본문의 낡은 "베이스 브랜치 안내"
  문단도 제거했다.
- **Blocker 2 (dry-run 상태 체이닝 버그)**: `IssueStateWriter`의 기본값인
  `dry_run=True`로 `ReworkService`를 구성하면, `_transition()`이
  dry-run일 때 원본 `issue`를 그대로 반환해 `request_changes()`
  이후에도 라벨이 여전히 `devbot:review`로 보였다. 그 반환값을 그대로
  `mark_for_review()`에 넘기면 "review에서 review로" 전환처럼 검증돼
  `InvalidStateTransitionError`가 발생했다 — `ReworkService`의 기본
  사용 시나리오(주입 없이 실제 `IssueStateWriter` 기본값을 쓰는 경우)가
  깨지는 버그였다. `src/devbot/issue_state.py`의 `_transition()`을
  고쳐 dry-run 여부와 무관하게 새 라벨을 계산해서 반환하고,
  `client.set_labels()` 호출만 dry-run이면 건너뛰도록 했다. 회귀
  테스트 두 개를 추가했다:
  - `test_dry_run_transitions_can_be_chained_using_returned_issue`
    (`tests/test_issue_state.py`) — 실제 `IssueStateWriter(dry_run=True)`로
    `request_changes()` → `mark_for_review()`를 연쇄 호출해도 라벨이
    올바르게 이어지는지 검증.
  - `test_rework_with_real_dry_run_state_writer_completes_full_cycle`
    (`tests/test_rework.py`) — `MagicMock`이 아닌 실제
    `IssueStateWriter(dry_run=True 기본값)`를 `ReworkService`에 연결해
    전체 사이클(`request_changes` → `apply_changes` → 검증 →
    `mark_for_review`)이 예외 없이 끝나는지 검증. 이 테스트는 수정 전
    코드에서는 실패했다.
- 리뷰 반영 후 재검증: `uv run ruff check .` PASS, `uv run pytest`
  PASS(93 passed, 회귀 테스트 2개 추가로 91 → 93).
