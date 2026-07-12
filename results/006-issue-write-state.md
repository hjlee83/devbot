# Result: Task 006

## Status
- COMPLETE

## 구현 요약

`tasks/006-issue-write-state.md` 범위대로, Issue를 claim/block/review 전환
시키는 안전한 GitHub 쓰기 기능을 추가했다. 기존 `GitHubClient`(Task 002)는
읽기 전용으로 유지해야 한다는 계약(`test_client_exposes_read_operations_only`)이
있어, 쓰기는 별도 모듈로 분리했다.

- **`src/devbot/github_write_client.py`(신규)**: `GitHubWriteClient`가
  라벨 전체 교체(`PUT .../issues/{n}/labels`)와 댓글 작성
  (`POST .../issues/{n}/comments`) 두 가지 저수준 GitHub REST 호출만
  담당한다. 어떤 전환이 유효한지는 전혀 모른다 — 호출자가 이미 검증했다고
  가정한다. 에러 변환(401/404/기타)은 `github_client.py`의 것과 동일하게
  동작하지만, 기존 파일을 건드리지 않기 위해(읽기 클라이언트 계약을 깨지
  않기 위해) 작게 중복 구현했다.
- **`src/devbot/issue_state.py`(신규)**: `IssueStateWriter`가 상태 전환
  검증과 실제 쓰기를 담당한다.
  - `_ALLOWED_TRANSITIONS`가 `docs/02-workflow.md`의 상태 흐름
    (`ready→working`, `working→review`, `working→blocked`,
    `review→working`, `review→done`, `blocked→ready`)을 그대로 표현한다.
  - `_current_state()`가 `GitHubIssue.labels`에서 현재 `devbot:*` 라벨을
    읽어 전환 시작 상태를 결정한다(라벨이 없거나 허용되지 않는 전환이면
    `InvalidStateTransitionError`를 발생시키며, 이 검증은 `dry_run`
    여부와 무관하게 항상 수행된다).
  - `claim()`/`block(reason)`/`mark_for_review()`/`request_changes()`
    네 개의 공개 메서드가 각각 하나의 전환에 대응한다. `block()`만 라벨
    전환 후 `reason`을 설명 댓글로 추가한다.
  - `dry_run=True`(기본값)면 검증은 그대로 수행하되 `GitHubWriteClient`
    호출(라벨 변경, 댓글 작성)을 전혀 하지 않는다.
  - 라벨 교체 시 상태 라벨(`devbot:*`)만 바뀌고, `priority:*` 등 다른
    라벨은 그대로 보존된다(기존 라벨 목록에서 이전 상태 라벨만 제거하고
    새 상태 라벨을 추가).

이번 Task는 `PollingService`(Task 005)에 이 쓰기 기능을 연결하지 않는다.
`docs/03-state-machine.md`가 명시하듯 "성공 시 PR을 만들고 review로
전환"하는데, PR 생성은 Task 007 범위라 지금 연결해도 실제 트리거 지점이
없다. Task 006은 쓰기 능력 자체(claim/block/review 전환 + 검증 +
dry-run)를 제공하고, 실제 폴링 루프 연결은 PR 생성이 준비되는 Task
007/008에서 이어진다.

## 생성/수정 파일

- `src/devbot/github_write_client.py` — 신규 (라벨 교체, 댓글 작성)
- `src/devbot/issue_state.py` — 신규 (전환 검증 + `IssueStateWriter`)
- `tests/test_github_write_client.py` — 신규
- `tests/test_issue_state.py` — 신규
- `results/006-issue-write-state.md` — 본 파일

기존 파일(`github_client.py`, `polling.py`, `models.py` 등)은 변경하지
않았다.

## CP-006-1 ~ CP-006-6별 테스트 이름과 결과

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-006-1 | ready가 claim 시 working으로 교체됨 | `test_claim_replaces_ready_with_working` | PASS |
| CP-006-2 | dry-run은 GitHub 쓰기를 하지 않음 | `test_claim_dry_run_performs_no_write` | PASS |
| CP-006-3 | working이 blocked로 이동하며 설명 댓글이 달림 | `test_blocked_transition_adds_reason_comment` | PASS |
| CP-006-4 | 구현 성공 후 working이 review로 이동 | `test_success_transition_moves_working_to_review` | PASS |
| CP-006-5 | 리뷰 피드백 시 review가 working으로 되돌아감 | `test_review_feedback_returns_issue_to_working` | PASS |
| CP-006-6 | 잘못된 상태 전환은 거부됨 | `test_invalid_state_transition_is_rejected` | PASS |

추가로 작성한 테스트(필수 아님, 경계/실패 경로 보강):
`test_blocked_transition_dry_run_skips_comment`,
`test_invalid_state_transition_is_rejected_regardless_of_dry_run`,
`test_invalid_state_transition_with_no_state_label_is_rejected`,
`test_transition_with_multiple_state_labels_is_rejected`(리뷰 반영, 아래
참고),
`test_set_labels_sends_put_with_full_label_set`,
`test_create_comment_sends_post_with_body`,
`test_write_not_found_error_is_translated`,
`test_write_authentication_error_is_translated`.

`tests/test_issue_state.py` 10개, `tests/test_github_write_client.py` 4개
전부 PASS. 전체 스위트 74개 전부 PASS.

## 검증 명령 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (74 passed) |
| `uv run devbot --once` | PASS (exit 0, `no_ready_task`) |

## 남은 TODO

- `PollingService`는 여전히 claim/block/review 전환을 호출하지 않는다
  (Task 007/008에서 PR 생성·푸시와 함께 연결 예정).
- `blocked → ready`(사람이 직접 라벨을 바꾸는 것으로 가정) 전환에 대응하는
  전용 메서드는 만들지 않았다 — 사람이 수동으로 라벨을 바꾸는 시나리오라
  DevBot이 쓰기를 수행할 필요가 없다고 판단했다. 필요해지면
  `IssueStateWriter`에 메서드를 추가하면 된다.
- `review → done`(PR 머지 시 전환)도 이번 Task에서 다루지 않는다 — 머지
  감지 자체가 아직 없다(Task 008 범위로 예상).

## 위험 요소

- `GitHubWriteClient`의 에러 변환 로직(`_raise_for_status`)이
  `github_client.py`와 거의 동일한 코드를 별도 파일에 중복 구현한
  것이다. 기존 `GitHubClient`가 읽기 전용만 노출해야 한다는 테스트
  계약을 깨지 않기 위한 의도적인 선택이며, 두 클라이언트를 하나의 공유
  HTTP 헬퍼로 합치는 리팩터링은 이번 Task 범위 밖으로 남겨뒀다.
- 로컬 샌드박스에 `uv`가 설치돼 있지 않아 `pip install --user uv`로
  설치한 뒤 검증 명령을 실행했다(`uv sync`/`ruff`/`pytest`/`devbot --once`
  모두 정상 동작 확인).

## 리뷰 반영

1차 PR 리뷰(REQUEST CHANGES)에서 다음 blocker가 지적되어 수정했다:

- **버그**: `_current_state()`가 `devbot:*` 상태 라벨이 두 개 이상인
  Issue를 오류로 취급하지 않고, `TaskState` enum 순서상 먼저 나오는
  라벨을 현재 상태로 채택했다. 이후 `_transition()`은 그렇게 채택된
  라벨 하나만 제거하고 새 상태 라벨을 추가하므로, 예를 들어
  `["devbot:ready", "devbot:review"]` 상태에서 `claim()`을 호출하면
  `["devbot:review", "devbot:working"]`처럼 충돌하는 상태 라벨이 남는
  안전하지 않은 쓰기가 발생할 수 있었다.
- **수정**: `_current_state()`를 제거하고 `_matched_state_labels()`가
  Issue의 `devbot:*` 라벨을 전부 수집하도록 했다. `_transition()`은 이
  목록의 길이가 정확히 1일 때만 `from_state`를 확정하고, 0개 또는 2개
  이상이면 `InvalidStateTransitionError`로 거부한다(에러 메시지도
  "라벨 없음"과 "충돌 라벨"을 구분해 표시).
- **추가한 테스트**: `test_transition_with_multiple_state_labels_is_rejected`
  (`tests/test_issue_state.py`, 리뷰 코멘트에서 제안한 이름 그대로) —
  `["devbot:ready", "devbot:review"]` 라벨을 가진 Issue에 `claim()`을
  호출하면 `InvalidStateTransitionError`가 발생하고 `set_labels`가 전혀
  호출되지 않는지 검증.
- 리뷰의 Warning(`block()`이 라벨 변경 후 댓글 작성에 실패하면 `blocked`
  상태만 남고 설명 댓글이 없는 부분 성공이 될 수 있음)은 GitHub API
  자체가 두 호출에 걸친 원자성을 보장하지 않아 이번 Task에서 구조적으로
  해결하지 않았다 — "남은 TODO"에 남겨둔 항목과 별개로, 후속 Task에서
  재시도/보정 로직이 필요하면 이 지점부터 시작하면 된다.
