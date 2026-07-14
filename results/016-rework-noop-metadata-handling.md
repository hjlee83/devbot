# Task 016 Result: Rework No-op and Metadata Action Handling

## 완료 내용

- rework comment를 `repository-change`, `metadata-only`, `external-verification` action scope로 분류하는 helper를 추가했다.
- 자동 리뷰 프롬프트와 게시 정책을 갱신해 repository file change가 필요한 경우에만 rework trigger가 생성되도록 했다.
- `devbot:manual-action` 상태를 추가해 metadata-only/external-verification 요청을 commit/push 경로와 `devbot:blocked` 루프에서 분리했다.
- repository-change rework에서 검증은 통과했지만 Git workspace가 clean이면 `no_repository_changes`로 기록하고 commit/push 없이 comment를 processed 처리한 뒤 `devbot:review`로 복귀하게 했다.
- 실제 repository 변경이 있는 rework는 기존처럼 commit, push, comment reaction, `devbot:review` 전환을 유지했다.
- 운영 문서와 roadmap에 Task 016 상태와 `manual-action` 진단 절차를 반영했다.

## 수정 파일

- `docs/00-roadmap.md`
- `docs/02-workflow.md`
- `docs/03-state-machine.md`
- `docs/04-agent-system.md`
- `docs/08-beta-runbook.md`
- `results/016-rework-noop-metadata-handling.md`
- `src/devbot/delivery.py`
- `src/devbot/issue_state.py`
- `src/devbot/models.py`
- `src/devbot/polling.py`
- `src/devbot/review.py`
- `src/devbot/rework.py`
- `tests/test_delivery.py`
- `tests/test_issue_state.py`
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

## 검증 결과

- `uv sync`: 통과
- `uv run ruff check .`: 통과
- `uv run pytest`: 통과, 240 passed
- `uv run devbot --once --dry-run`: 기본 lock 파일(`/tmp/devbot.lock`)이 이미 점유되어 1차 실행 실패.
- `DEVBOT_LOCK_FILE=/Users/luna/workspace/devbot/.devbot-task016.lock uv run devbot --once --dry-run`: 실행되었으나 `iteration_error`로 종료.
  - 원인: 이 실행 환경에서 `api.github.com` DNS 해석/아웃바운드 네트워크가 차단되어 관리 저장소 Issue 조회가 실패했다.
  - Task 016 변경 범위는 GitHub 조회 전 설정/네트워크 경로가 아니라 rework scope 분류, clean workspace 처리, 상태 전이, 리뷰 게시 정책이므로 이 실패는 구현 회귀가 아니라 실행 환경 제약이다.

## TODO

- 없음

## 위험 요소

- action scope classifier는 휴리스틱 기반이다. 명확한 repository-change 문구 없이 metadata/external 요청을 애매하게 작성하면 기본값인 `repository-change`로 분류될 수 있다.
- `devbot:manual-action`은 자동 처리 완료가 아니라 사람 또는 GitHub metadata 조치가 필요하다는 신호다.
- clean rework는 검증 통과 후 commit 없이 review로 복귀하므로, reviewer가 실제로 요구한 repository 변경이 누락되지 않았는지 다음 review에서 확인해야 한다.

## Improvement Suggestions

- 리뷰어 출력에 machine-readable action scope 필드를 추가하면 휴리스틱 분류 의존도를 줄일 수 있다.
- PR body/evidence 갱신 같은 제한된 metadata action은 향후 별도 executor로 자동화할 수 있다.
