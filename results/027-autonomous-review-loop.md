# Task 027 Result: Autonomous Review Loop

## 완료 내용

Task 027의 review → rework → re-review 자동 루프를 기존 `PollingService`,
`ReviewService`, `ReworkService` 경계에 맞춰 구현했다.

- IMPLEMENT/REWORK 성공 후 `devbot:review` 상태가 되면 기존 polling 후보 선택이
  자동 REVIEW를 실행한다.
- `REQUEST CHANGES` repository-change 결과는 `devbot:rework`로 전환하고
  `@devbot` PR 댓글을 남겨 다음 polling cycle의 REWORK 후보가 된다.
- REWORK 성공은 기존 branch/PR을 유지한 채 `devbot:review`로 복귀한다.
- 리뷰 루프는 PR auto-review marker 수 기준 기본 3회로 제한된다. 초과 시
  작업/PR/worktree를 삭제하지 않고 `devbot:manual-action`으로 전환한다.
- 현재 head의 유효한 `MERGE READY`만 PR 라벨을 `devbot:ready-to-merge`로
  독점 갱신한다.
- 미처리 `@devbot` 피드백, blocked/manual-action metadata, stale marker는
  ready-to-merge를 적용하지 않는다.
- Timeline actor/cycle 기록은 기존 `safe_start`/`safe_end`를 유지하며,
  `ReviewResult.diagnostic`에 cycle, last outcome, retry count, next action을
  노출한다.
- `WorktreeManager.prepare()`가 `PreparedWorkspace`를 반환한 뒤에는 모든 Agent
  역할이 `PreparedWorkspace.repository`만 사용한다는 단일 workspace contract를
  문서화하고 REVIEW/REWORK 경로에 공통 helper로 반영했다. REVIEW Job도 host main
  checkout이 아니라 linked PR 기준 prepared worktree를 검증/사용하므로 host
  repository의 현재 branch나 미커밋 파일이 REVIEW를 잘못 막지 않는다.

자동 merge는 구현하지 않았다.

## 수정 파일

- `src/devbot/review.py`
- `src/devbot/polling.py`
- `src/devbot/github_write_client.py`
- `tests/test_review.py`
- `tests/test_polling.py`
- `docs/00-roadmap.md`
- `docs/03-state-machine.md`
- `docs/10-github-status-timeline.md`
- `docs/13-host-managed-workspace-preparation.md`
- `results/027-autonomous-review-loop.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
|---|---|
| CP-027-1 | `test_successful_implement_delivery_automatically_dispatches_review` |
| CP-027-2 | `test_request_changes_automatically_dispatches_rework` |
| CP-027-3 | `test_successful_rework_automatically_dispatches_rereview` |
| CP-027-4 | `test_autonomous_review_loop_supports_multiple_cycles` |
| CP-027-5 | `test_autonomous_review_loop_is_idempotent_across_retries` |
| CP-027-6 | `test_review_loop_attempt_limit_requires_manual_action` |
| CP-027-7 | `test_stale_merge_ready_result_does_not_mark_pr_ready` |
| CP-027-8 | `test_merge_ready_applies_exclusive_ready_to_merge_label` |
| CP-027-9 | `test_merge_readiness_gate_rejects_incomplete_pr` |
| CP-027-10 | `test_review_loop_records_actors_and_cycles_idempotently` |
| CP-027-11 | `test_review_loop_metadata_failure_preserves_primary_outcome` |
| CP-027-12 | `test_existing_workflows_remain_compatible_with_autonomous_review_loop` |
| CP-027-13 | 이 Result 문서와 docs/roadmap/state/timeline 갱신 |
| REVIEW worktree regression | `test_review_uses_prepared_pr_worktree_for_workspace_validation` |
| Prepared dirty rejection | `test_review_rejects_dirty_prepared_worktree_even_when_host_is_clean` |
| Full loop regression | `test_autonomous_review_rework_rereview_loop_runs_without_manual_commands` |

## 검증 결과

- `python3 -m uv run pytest tests/test_polling.py::test_review_uses_prepared_pr_worktree_for_workspace_validation tests/test_polling.py::test_review_rejects_dirty_prepared_worktree_even_when_host_is_clean tests/test_polling.py::test_autonomous_review_rework_rereview_loop_runs_without_manual_commands tests/test_review.py::test_autonomous_review_loop_supports_multiple_cycles -q` 성공: 4 passed.
- `python3 -m uv run ruff check .` 성공.
- `python3 -m uv run pytest -q` 성공: 416 passed.
- `python3 -m uv sync` 성공.
- `python3 -m uv run devbot doctor` 실행: repository/GitHub/timeline/worktree checks는 OK였으나 `/tmp/devbot.lock` 점유로 `daemon_lock` FAIL, `safe_to_start: no`.
- `python3 -m uv run devbot --once --dry-run` 성공: Issue #53 / PR #54 REVIEW 후보를 선택하고 dry-run reviewer 경로로 `reviewed` 종료.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/ruff check tests/test_review.py src/devbot/review.py src/devbot/polling.py src/devbot/github_write_client.py` 성공.
- `PYTHONPATH=src /Users/luna/workspace/devbot/.venv/bin/pytest tests/test_polling.py tests/test_review.py -q` 성공: 132 passed.
- `python -m py_compile src/devbot/review.py src/devbot/polling.py src/devbot/github_write_client.py` 성공.

이전 실행에서는 sandbox DNS 제한으로 `uv run ...`이 실패했지만, 이번 재검증에서는
로컬 uv 환경으로 validation gate를 실행했다. 남은 제한은 현재 환경의 daemon lock
점유뿐이다.

## Manual Verification Evidence

- IMPLEMENT → REVIEW: delivery 성공 후 Issue가 `devbot:review`가 되면 polling의
  review 후보 선택이 head marker 부재를 보고 REVIEW를 실행한다.
- REQUEST CHANGES → REWORK → REVIEW: `REQUEST CHANGES`는 `@devbot` PR 댓글과
  `devbot:rework` 상태를 만들고, REWORK 성공은 processed reaction 후 `review`로
  돌아간다.
- Full loop: `test_autonomous_review_rework_rereview_loop_runs_without_manual_commands`가
  같은 Issue #53/PR #54/branch/worktree에서 IMPLEMENT → REVIEW → REWORK →
  REVIEW가 사람 label 조작 없이 polling cycle만으로 이어지는 것을 검증한다.
- 두 번째 cycle: 이전 head marker가 있는 comments와 새 head에서 `cycle=2`
  diagnostic이 생성되는 테스트로 확인했다.
- Current-head MERGE READY: current head marker가 포함된 결과만
  `devbot:ready-to-merge` PR 라벨을 적용한다.
- Stale/failed gate: stale marker 또는 미처리 피드백은 ready-to-merge를 적용하지
  않는다.
- Loop exhaustion: review marker 수가 기본 limit을 초과하면 Reviewer를 실행하지
  않고 `manual-action`으로 전환한다.

## TODO / 위험 요소

- GitHub Checks API를 아직 read model로 수집하지 않으므로 "required CI/check status
  successful when available"은 현재 사용 가능한 PR/Issue/comment evidence 범위에서만
  적용된다. Checks read model이 추가되면 merge-readiness gate에 연결해야 한다.
- PR label read model이 별도로 없어서 ready-to-merge 적용 시 DevBot PR state label의
  독점성은 write payload 기준으로 보장한다.

## Improvement Suggestions

- `PullRequest` read model에 labels/check conclusion을 추가해 ready-to-merge gate를
  GitHub UI 상태와 더 정밀하게 일치시킨다.
- review loop diagnostic을 observability cycle summary에도 별도 필드로 노출한다.
