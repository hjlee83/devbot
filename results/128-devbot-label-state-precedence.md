# Task 128 Result: DevBot State Label Precedence

## 완료 내용

- `src/devbot/state_labels.py`를 추가해 `devbot:*` 라벨 매칭과 다중 라벨 해석 precedence를 한 곳으로 모았다.
- 공통 precedence는 기존 `issue_state.py`의 안전한 기준을 유지한다: `DONE`, `BLOCKED`, `WORKING`, `MANUAL_ACTION`, `REWORK`, `REVIEW`, `READY`.
- `src/devbot/polling.py`가 더 이상 `TaskState` 선언 순서의 첫 매치를 상태로 선택하지 않고, `state_labels.task_state_from_labels()`를 사용한다.
- `src/devbot/issue_state.py`도 같은 shared resolver를 사용한다.
- `state_label_conflict` 로그의 `resolved_state`도 실제 shared resolver 결과와 일치하도록 고쳤다.

## 주요 설계 결정

- 다중 `devbot:*` 라벨은 자동으로 GitHub 라벨을 정리하지 않는다. 이번 Task는 읽기/판단 경로의 불일치를 제거하고, 실제 라벨 정규화는 기존 상태 전이 write 경로가 계속 담당한다.
- precedence는 나중/터미널 상태가 이기는 기존 `issue_state.py` 정책을 authoritative 기준으로 채택했다. stale label은 이후 상태보다 이전 상태가 남아 있을 가능성이 더 높기 때문이다.

## 수정 파일

- `src/devbot/state_labels.py`
- `src/devbot/issue_state.py`
- `src/devbot/polling.py`
- `tests/test_polling.py`
- `tasks/128-devbot-label-state-precedence.md`
- `results/128-devbot-label-state-precedence.md`
- `docs/07-decisions.md`
- `docs/00-roadmap.md`

## 테스트 Evidence

- `test_polling_and_issue_state_use_same_multi_label_precedence`: `devbot:ready` + `devbot:done`이 polling과 issue-state 모두에서 `DONE`으로 해석됨을 검증한다.
- `test_queue_summary_counts_conflicted_state_label_issue_once`: `devbot:review` + `devbot:blocked`가 queue summary와 conflict log 모두에서 `BLOCKED`로 해석되고 한 버킷에만 집계됨을 검증한다.

## Validation 결과

```bash
$ uv run ruff check .
All checks passed!

$ uv run pytest tests/test_task_contract_docs.py tests/test_polling.py tests/test_issue_state.py
142 passed in 0.37s

$ uv run pytest
1371 passed in 186.47s
```

## 남은 TODO와 제한

- 다중 라벨을 발견했을 때 GitHub 라벨을 자동 교정하는 기능은 이번 범위가 아니다.
- 정상 상태 전이의 단일 라벨 보장 정책은 기존 `IssueStateWriter` 동작을 그대로 유지한다.
