# Task 025: Planner-Linked PR Resolution Hardening

## 완료 내용

- 실행 Issue 본문의 `Pull Request: #<number>` Planner metadata를 파싱하고,
  PR body closing keyword보다 먼저 사용하도록 했다.
- Planner Issue가 명시한 PR을 찾지 못하면 fallback branch를 만들지 않고
  workspace preparation failure로 중단한다.
- 실행 Issue의 `Branch: ` metadata와 resolved PR head branch가 다르면
  `branch_pr_mismatch`로 거부한다.
- Planner PR metadata가 없는 legacy Issue는 기존 closing-keyword 해석과
  fallback branch 생성을 유지한다.
- host-managed workspace 문서에 Planner PR 해석 우선순위와 mismatch 거부
  규칙을 기록했다.
- Roadmap에 Task 025 완료 범위와 Planner-linked PR hardening 동작을 기록했다.
- Re-review 반영으로 Task 024는 완료가 아니라 PR #46 / Issue #47에서 진행
  중인 pending 작업으로 Roadmap에 표시했다.

## 수정 파일

- `src/devbot/polling.py`
- `src/devbot/worktree.py`
- `docs/00-roadmap.md`
- `tests/test_polling.py`
- `tests/test_worktree.py`
- `tests/test_planner.py`
- `docs/13-host-managed-workspace-preparation.md`
- `results/025-planner-linked-pr-resolution.md`

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-025-1 | `test_execution_issue_pr_number_is_parsed` |
| CP-025-2 | `test_explicit_issue_pr_resolves_without_closing_keyword` |
| CP-025-3 | `test_planner_pr_head_branch_is_reused` |
| CP-025-4 | `test_prepared_workspace_contains_planner_contract` |
| CP-025-5 | `test_missing_explicit_pr_rejects_fallback_branch` |
| CP-025-6 | `test_issue_branch_and_pr_head_mismatch_rejected` |
| CP-025-7 | `test_legacy_issue_without_pr_keeps_fallback_branch` |
| CP-025-8 | `test_planner_pr_template_includes_execution_issue_closing_link` |
| CP-025-9 | `test_planner_pr_resolution_diagnostics` |
| CP-025-10 | `test_existing_workflows_compatible_with_planner_pr_resolution` 및 전체 pytest |
| CP-025-11 | 이 Result 문서, `docs/13-host-managed-workspace-preparation.md`, `docs/00-roadmap.md` |

## 검증 결과

- `uv sync`: PASS
- `uv run ruff check .`: PASS
- `uv run pytest`: PASS, 374 passed
- `uv run devbot doctor`: PASS(exit 0, `safe_to_start: yes`)
  - 현재 작업 branch와 미커밋 변경 때문에 `workspace_cleanliness`와
    `current_branch_compatibility`는 FAIL 진단을 출력했다.
- `uv run devbot --once --dry-run`: PASS(exit 0, `no_ready_task`)

## Manual Verification

- Issue #47 본문에서 `Pull Request: #46`와
  `Branch: task/024-timeline-auto-recording` metadata를 확인했다.
- PR #46의 head branch가 `task/024-timeline-auto-recording`임을 확인했다.
- PR body closing keyword를 의도적으로 제거한 local fixture로
  `find_linked_pull_request()`를 호출해 Issue #47이 PR #46과
  `task/024-timeline-auto-recording` branch로 해석되는 것을 확인했다.
- Missing explicit PR의 fallback 차단은
  `test_missing_explicit_pr_rejects_fallback_branch`로 확인했다.
- Legacy non-Planner Issue fallback branch 유지 동작은
  `test_legacy_issue_without_pr_keeps_fallback_branch`로 확인했다.

## TODO

- 없음.

## 위험 요소

- `Pull Request: #<number>` metadata는 Planner Issue 형식에 의존한다. 수동
  Issue가 같은 문구를 잘못 포함하면 Planner Issue처럼 fallback이 차단된다.
- GitHub PR 목록 조회가 실패하면 명시 PR 존재 여부를 확정할 수 없으므로
  기존 best-effort 경고 경로는 `None`을 반환한다. 다만 Issue 본문에
  `Pull Request: #<number>` metadata가 있으면 그 직후 보수적으로 workspace
  preparation failure로 중단하고 fallback branch 생성을 막는다.

## Improvement Suggestions

- Planner Issue metadata를 자유 텍스트가 아니라 HTML marker나 구조화된
  block으로 기록하면 수동 문구 충돌 가능성을 더 줄일 수 있다.
