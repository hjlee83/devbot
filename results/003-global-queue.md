# Result: Task 003

## Status
- COMPLETE

## 요약
`tasks/003-global-queue.md` 범위대로, 모든 활성화된(enabled) 저장소를
가로지르는 전역 큐에서 최대 1개의 `devbot:ready` Issue를 선택하는 로직을
구현했다.

`src/devbot/queue.py`에 새 함수 `select_global_ready_task(tasks,
repositories)`를 추가했다. Task 001에서 이미 구현된 `select_ready_task`
(단일 평면 리스트에 대한 blocking + priority/age 정렬 규칙)를 그대로
재사용하고, 그 앞단에 "비활성화된 저장소에 속한 Task는 통째로 무시"하는
필터링 단계를 추가하는 방식으로 구현했다. 즉:

1. `repositories` 중 `enabled=True`인 저장소의 `full_name` 집합을 구한다.
2. `tasks` 중 그 집합에 속하지 않는 저장소의 Task는 완전히 제거한다(마치
   존재하지 않았던 것처럼 — working/review 블로킹 판정에도 포함되지
   않는다).
3. 남은 Task에 기존 `select_ready_task`를 그대로 적용한다.

기존 `has_active_task`/`select_ready_task`(Task 001)는 시그니처와 동작을
전혀 변경하지 않았다 — 새 함수로 감싸기만 했다.

## 수정 파일
- `src/devbot/queue.py` — `select_global_ready_task` 추가(기존 함수는 미변경)
- `tests/test_global_queue.py` — 신규
- `results/003-global-queue.md` — 본 문서
- `results/003-global-queue-improvements.md` — 개선 제안

## Checkpoint별 테스트 근거

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-003-1 | `test_working_issue_in_any_repo_blocks_selection` | PASS |
| CP-003-2 | `test_review_issue_in_any_repo_blocks_selection` | PASS |
| CP-003-3 | `test_global_queue_prefers_highest_priority` | PASS |
| CP-003-4 | `test_global_queue_prefers_oldest_with_same_priority` | PASS |
| CP-003-5 | `test_disabled_repository_is_ignored` (추가 경계 테스트: `test_disabled_repository_active_task_does_not_block_selection`) | PASS |
| CP-003-6 | `test_no_ready_issue_returns_none` | PASS |

`tests/test_global_queue.py` 7개 전부 PASS. 기존 `tests/test_queue.py`(Task
001, 2개)도 변경 없이 그대로 PASS. 전체 스위트 30개 전부 PASS.

## 검증 결과

Task 003 문서에는 `uv run ruff check .` / `uv run pytest` 두 개만
명시되어 있지만, `AGENTS.md` §5 및 `templates/result-template.md`의 표준
검증 표를 따라 4개 모두 실행했다(Task 002에서와 동일한 처리).

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (30 passed) |
| `uv run devbot` | PASS (exit 0) — `main.py`/CLI 미변경, 회귀 없음 확인용 |

## 남은 TODO
- `select_global_ready_task`는 아직 `main.py`(CLI)나 실제 GitHub 데이터
  소스(Task 002의 `GitHubClient`)와 연결되지 않았다. Task 005(메인 폴링
  루프)에서 실제 저장소 순회 → `GitHubIssue` → `IssueTask` 변환 → 이
  함수 호출까지 이어질 것으로 예상.
- Task 002에서 남겨둔 `GitHubIssue`(원문 라벨/상태) → `IssueTask`
  (`TaskState`/`Priority`) 매핑 로직은 여전히 미구현 상태다. Task 003은
  이미 `TaskState`/`Priority`가 부여된 `IssueTask`를 입력으로 받는다는
  전제로 선택 로직만 구현했다.

## 위험 요소
- "비활성화된 저장소는 통째로 무시한다"를 "working/review 블로킹
  판정에서도 제외"로 해석했다(Task 003 원문은 이 순서를 명시하지 않음).
  즉 비활성화된 저장소에 `working` Issue가 남아있어도 다른 활성화된
  저장소의 `ready` Issue 선택을 막지 않는다. 이 해석이 의도와 다르면
  `select_global_ready_task`의 필터링 순서만 바꾸면 되므로 영향 범위는
  작다. 자세한 내용은 `results/003-global-queue-improvements.md` 참고.
- 개선 제안은 별도 파일에 기록했으며, 이번 구현 범위를 임의로 바꾸지
  않았다.
