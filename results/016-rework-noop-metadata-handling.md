# Task 016 Result: Rework No-op and Metadata Action Handling

## 완료 내용

- rework comment를 `repository-change`, `metadata-only`, `external-verification` action scope로 분류하는 helper를 추가했다.
- 자동 리뷰 프롬프트와 게시 정책을 갱신해 repository file change가 필요한 경우에만 rework trigger가 생성되도록 했다.
- `devbot:manual-action` 상태를 추가해 metadata-only/external-verification 요청을 commit/push 경로와 `devbot:blocked` 루프에서 분리했다.
- repository-change rework에서 검증은 통과했지만 Git workspace가 clean이면 `no_repository_changes`로 기록하고 commit/push 없이 comment를 processed 처리한 뒤 `devbot:review`로 복귀하게 했다.
- 실제 repository 변경이 있는 rework는 기존처럼 commit, push, comment reaction, `devbot:review` 전환을 유지했다.
- 운영 문서와 roadmap에 Task 016 상태와 `manual-action` 진단 절차를 반영했다.
- `devbot.agents.base`에 `is_approval_required_output()`을 추가하고 IMPLEMENT job(`_run_claimed_implement_job`)에 연결해, Agent output이 대화형 승인 대기로 끝나면(`needs your approval`, `should i proceed`, `approval required` 등) delivery(commit/push/PR)를 실행하지 않고 `devbot:manual-action`으로 전환하도록 했다.
- `DeliveryService.deliver()`에 `linked_pull_request` 파라미터를 추가했다. Issue에 연결된 open PR이 있으면 새 `devbot/devbot-<issue>-...` branch를 생성/push하지 않고 그 PR의 head branch를 재사용하며, PR을 새로 열지 않고 기존 PR에 댓글만 남긴다. IMPLEMENT job은 delivery 호출 전에 `list_pull_requests` + `find_linked_pull_request`로 linked PR을 best-effort 조회해 이 branch를 넘긴다(조회 실패 시 기존처럼 신규 branch명으로 진행, job을 실패시키지 않음).
- `DeliveryService.deliver()`에 `has_changes`/`branch_exists` 게이트를 추가했다. clean workspace면 `commit`을 호출하지 않고 `no_repository_changes`로 종료하고(rework 경로와 동일한 보호를 implement 경로에도 적용), push 대상 local branch가 없으면 `git push`를 실행하기 전에 `delivery_branch_invalid`로 종료해 `src refspec ... does not match any`가 사후 `DeliveryError`로 새는 경로를 없앴다. push는 commit이 실제로 성공한 뒤에만 실행된다.

## 수정 파일

- `docs/00-roadmap.md`
- `docs/02-workflow.md`
- `docs/03-state-machine.md`
- `docs/04-agent-system.md`
- `docs/08-beta-runbook.md`
- `results/016-rework-noop-metadata-handling.md`
- `src/devbot/agents/base.py`
- `src/devbot/delivery.py`
- `src/devbot/issue_state.py`
- `src/devbot/models.py`
- `src/devbot/polling.py`
- `src/devbot/review.py`
- `src/devbot/rework.py`
- `tests/test_beta_smoke.py`
- `tests/test_delivery.py`
- `tests/test_issue_state.py`
- `tests/test_polling.py`
- `tests/test_review.py`
- `tests/test_rework.py`

## Checkpoint별 테스트

- CP-016-1: `test_review_prompt_does_not_trigger_rework_for_metadata_only_actions`
- CP-016-2: `test_rework_classifies_repository_change_comment`, `test_rework_classifies_metadata_only_comment`, `test_rework_classifies_external_verification_comment`
- CP-016-3: `test_clean_rework_does_not_commit`
- CP-016-4: `test_metadata_only_rework_does_not_block_queue`
- CP-016-5: `test_repository_change_rework_still_commits_and_returns_review`
- CP-016-6: `test_manual_action_transition_keeps_single_state_label`, `test_metadata_only_rework_does_not_block_queue`
- CP-016-7: `test_no_repository_changes_is_logged`
- CP-016-8: 이 Result 문서
- CP-016-9: `test_approval_required_agent_output_skips_delivery`
- CP-016-10: `test_delivery_uses_linked_pr_head_branch`, `test_ready_implement_reuses_linked_pr_branch`
- CP-016-11: `test_delivery_rejects_missing_local_branch_before_push`, `test_local_branch_exists_uses_show_ref`
- CP-016-12: `test_delivery_does_not_push_when_commit_created_no_changes`

## 검증 결과

- `uv sync`: 통과
- `uv run ruff check .`: 통과
- `uv run pytest`: 통과, 246 passed (CP-016-9~12 신규 테스트 6개 포함, 기존 240개 회귀 없음)
- `uv run devbot --once --dry-run`: `DEVBOT_LOCK_FILE=/tmp/devbot-task016-fix.lock`로 실행. 이번 환경에서는 `api.github.com` 접근이 가능해 정상적으로 관리 저장소를 조회했고 `no_ready_task`로 정상 종료했다(이전 라운드에서 기록된 네트워크 차단은 이번 실행에서는 재현되지 않았다).

## TODO

- 없음

## 위험 요소

- action scope classifier는 휴리스틱 기반이다. 명확한 repository-change 문구 없이 metadata/external 요청을 애매하게 작성하면 기본값인 `repository-change`로 분류될 수 있다.
- `devbot:manual-action`은 자동 처리 완료가 아니라 사람 또는 GitHub metadata 조치가 필요하다는 신호다.
- clean rework는 검증 통과 후 commit 없이 review로 복귀하므로, reviewer가 실제로 요구한 repository 변경이 누락되지 않았는지 다음 review에서 확인해야 한다.
- `is_approval_required_output()`도 휴리스틱 문자열 매칭이다(`needs your approval`, `should i proceed`, `approval required` 등). Agent가 다른 표현으로 승인을 요청하면 여전히 delivery로 새어나갈 수 있다.
- linked PR 재사용은 `list_pull_requests` + `find_linked_pull_request`(PR body의 `Closes #N`) 조회에 의존한다. 조회가 실패하면(네트워크 오류 등) IMPLEMENT job은 안전하게 기존 동작(신규 branch명 생성)으로 폴백하지만, 그 경우 CP-016-10이 막으려던 branch 불일치가 다시 발생할 수 있다 - 이 job은 실패시키지 않고 경고 로그만 남기는 trade-off다.
- delivery의 `branch_exists` 검사는 실제로 체크아웃된 branch가 `target_branch`와 일치하는지까지는 검증하지 않는다(단일 checkout 구조의 기존 제약, Task 016 Risk 섹션에 이미 명시됨). 이는 이번 Task의 범위(Worktree Manager 제외)를 벗어난다.

## Improvement Suggestions

- 리뷰어 출력에 machine-readable action scope 필드를 추가하면 휴리스틱 분류 의존도를 줄일 수 있다.
- PR body/evidence 갱신 같은 제한된 metadata action은 향후 별도 executor로 자동화할 수 있다.
- Agent 승인 요청을 구조화된(JSON) 신호로 표준화하면 `is_approval_required_output()`의 문자열 매칭 의존도를 줄일 수 있다.
