# Result: Task 014

## Status

- COMPLETE locally, with one environment-limited verification caveat:
  `uv run devbot --once --dry-run` reaches the GitHub lookup path but this
  sandbox cannot resolve `api.github.com`, so it exits with
  `iteration_error`. `uv sync`, `ruff`, and the full test suite pass.

## 완료 내용

1. `devbot:rework`를 리뷰 대기와 분리된 수정 대기 상태로 고정했다.
   `REQUEST CHANGES`는 `review -> working -> rework`, 성공 rework는
   `rework -> working -> review`, `MERGE READY`는 `review` 유지로 동작한다.
2. delivery 예외와 review/rework 최종 상태 전이 예외가 `working`에 남지
   않도록 `blocked` 전환과 사유 기록 경로를 보강했다.
3. rework 후보는 `devbot:rework` Issue와 처리되지 않은 `@devbot` PR 댓글이
   함께 있을 때만 생성되도록 테스트를 보강했다.
4. 상태 라벨 단일성, 경쟁 claim 방지, 동일 피드백 중복 방지, 기존
   `REWORK > REVIEW > IMPLEMENT` 우선순위와 concurrency를 회귀 테스트로
   고정했다.
5. 운영 문서와 결정 기록을 새 상태 머신에 맞게 갱신했다.

## 수정 파일

- `src/devbot/polling.py`
- `src/devbot/review.py`
- `src/devbot/rework.py`
- `src/devbot/issue_state.py`
- `tests/test_polling.py`
- `tests/test_review.py`
- `tests/test_rework.py`
- `tests/test_scheduler.py`
- `tests/test_beta_smoke.py`
- `tests/test_main_loop.py`
- `docs/00-roadmap.md`
- `docs/04-agent-system.md`
- `docs/07-decisions.md`
- `docs/08-beta-runbook.md`
- `results/014-workflow-state-machine-hardening.md`

## Checkpoint별 테스트

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-014-1 | `test_state_transition_keeps_exactly_one_devbot_state_label` | PASS |
| CP-014-2 | `test_request_changes_moves_issue_from_review_to_rework` | PASS |
| CP-014-2 | `test_merge_ready_keeps_issue_in_review_state` | PASS |
| CP-014-3 | `test_rework_job_requires_rework_label_and_unprocessed_feedback` | PASS |
| CP-014-4 | `test_successful_rework_reuses_pr_and_returns_issue_to_review` | PASS |
| CP-014-5 | `test_preflight_failure_restores_previous_stable_state` | PASS |
| CP-014-6 | `test_execution_failure_moves_issue_to_blocked_with_reason` | PASS |
| CP-014-7 | `test_unexpected_exception_never_leaves_issue_working` | PASS |
| CP-014-8 | `test_competing_claim_runs_only_one_job` | PASS |
| CP-014-9 | `test_processed_feedback_is_not_reworked_twice` | PASS |
| CP-014-10 | `test_state_machine_preserves_job_priority_and_concurrency` | PASS |
| CP-014-11 | `test_state_transition_log_contains_correlation_and_reason` | PASS |
| CP-014-12 | `docs/04-agent-system.md`, `docs/08-beta-runbook.md`, `docs/07-decisions.md` 갱신 | PASS |

## 검증 결과

| Command | Result |
|---|---|
| `UV_CACHE_DIR=/private/tmp/devbot-uv-cache uv sync` | PASS |
| `UV_CACHE_DIR=/private/tmp/devbot-uv-cache uv run ruff check .` | PASS |
| `UV_CACHE_DIR=/private/tmp/devbot-uv-cache uv run pytest` | PASS, 224 passed |
| `uv run devbot --once --dry-run` | FAIL: 기본 `/tmp/devbot.lock`이 이미 사용 중 |
| `DEVBOT_LOCK_FILE=/private/tmp/devbot-task014.lock UV_CACHE_DIR=/private/tmp/devbot-uv-cache uv run devbot --once --dry-run` | FAIL: sandbox DNS 제한으로 `api.github.com` 조회 실패, `iteration_error` |

## TODO

- 네트워크가 허용되는 CI 또는 운영 환경에서
  `uv run devbot --once --dry-run`을 다시 확인한다.
- 실제 GitHub Issue/PR에서 `REQUEST CHANGES -> rework -> working ->
  review -> MERGE READY` 릴레이를 한 번 수동 관찰한다.

## 위험 요소

- 상태 전이 실패 후 `blocked` 처리도 같은 GitHub label API에 의존한다. API
  자체가 계속 실패하면 Result는 실패를 반환하지만 원격 라벨 복구는 다음
  cycle 또는 수동 조치가 필요하다.
- 기존에 `devbot:review`에 남아 있던 처리되지 않은 `@devbot` 댓글은 이제
  자동 rework 후보가 아니다. 운영자는 해당 Issue를 `devbot:rework`로
  수동 전환해야 한다.

## Improvement Suggestions

- `devbot --once --dry-run`이 네트워크 없이 설정/스케줄러까지만 검증하는
  `--offline-smoke` 모드를 제공하면 CI와 sandbox 검증이 더 안정적이다.
- GitHub label API 실패가 반복되는 경우를 위해 마지막 관측 상태와 복구
  권장 상태를 별도 진단 로그로 더 명확히 남길 수 있다.
