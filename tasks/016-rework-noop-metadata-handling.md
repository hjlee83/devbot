# Task 016: Rework No-op and Metadata Action Handling

## 목표

리뷰 결과가 `REQUEST CHANGES`일 때 DevBot이 모든 변경 요청을 repository commit으로
처리하려고 하면서 `nothing to commit`으로 `devbot:blocked`에 빠지는 문제를 해결한다.

DevBot은 rework 결과가 repository file change인지, GitHub metadata-only action인지,
외부 검증/사람 개입이 필요한 action인지 구분해야 한다. repository 변경이 없다는 이유만으로
commit/push 단계에서 실패 처리하지 않는다.

## 배경

Task 015 운영 중 다음 문제가 반복됐다.

- Reviewer가 PR Evidence, PR body, CI 확인 같은 metadata/external action을 `REQUEST CHANGES`
  로 남겼다.
- Issue는 `devbot:rework`가 되었고 Implementer가 rework를 claim했다.
- 실제 repository file change가 없어서 `git commit`이 `nothing to commit, working tree clean`
  으로 실패했다.
- DevBot은 이를 `DeliveryError`로 보고 Issue를 `devbot:blocked`로 바꿨다.

이 흐름은 자동화가 고장난 것이 아니라 action scope 분류가 없는 상태에서 metadata-only
요청을 commit 경로로 보낸 결과다.

## Dependencies

- Task 010: ReworkService
- Task 012: role-based polling orchestration
- Task 014: workflow state machine hardening
- Task 015: review gate standardization
- `src/devbot/rework.py`
- `src/devbot/delivery.py`
- `src/devbot/review.py`
- `src/devbot/polling.py`
- `tests/test_rework.py`
- `tests/test_delivery.py`
- `tests/test_review.py`

## 구현 범위

1. Rework 요청을 action scope로 분류한다.
   - `repository-change`: code, test, docs, result file 등 git commit이 필요한 변경
   - `metadata-only`: PR title/body/evidence, labels, issue/PR comments, reactions 등 GitHub metadata 변경
   - `external-verification`: CI 확인, 네트워크 열린 환경에서 dry-run 확인, 사람 승인 등 repository commit으로 해결할 수 없는 검증
2. Reviewer prompt 또는 review posting policy에 `@devbot rework` 조건을 명확히 한다.
   - repository file change가 필요한 경우에만 `@devbot rework`를 포함한다.
   - metadata-only/external-verification action에는 `@devbot rework`를 포함하지 않는다.
   - 해당 경우에는 `Manual action required` 또는 `Metadata action required` 섹션을 사용한다.
3. Rework 실행 후 workspace가 clean이면 `git commit`을 시도하지 않는다.
   - clean workspace를 `DeliveryError`로 처리하지 않는다.
   - 처리 결과를 `no_repository_changes` 같은 구조화된 상태로 기록한다.
4. clean rework의 상태 전이를 정의한다.
   - metadata-only 또는 external-verification으로 분류된 경우 Issue를 `devbot:blocked`로 보내지 않는다.
   - 자동으로 해결할 수 없는 경우 `devbot:manual-action` 또는 명확한 comment를 남긴다.
   - 이미 metadata가 해결되어 있고 최신 head가 바뀌지 않았다면 `devbot:review`로 복귀할 수 있다.
5. 기존 repository-change rework는 유지한다.
   - 실제 파일 변경이 있으면 commit, push, 기존 PR 업데이트, Issue `devbot:review` 전환을 계속 수행한다.
6. 로그와 Result에 원인을 명확히 남긴다.
   - `nothing to commit`은 `commit/push failed`가 아니라 `no_repository_changes`로 로깅한다.
   - 어떤 action scope로 분류했는지 cycle/job 로그에 남긴다.

## 제외 범위

- PR body 자동 수정 기능 전체 구현
- GitHub metadata action executor 일반화
- Worktree Manager
- Agent registry/failover
- Merge-ready notification
- Dashboard/heartbeat UI

## 품질 게이트

### CP-016-1: Review prompt prevents invalid rework trigger

Reviewer prompt 또는 posting policy는 metadata-only/external-verification 요청에
`@devbot rework`를 붙이지 말라고 명시해야 한다.

### CP-016-2: Rework action scope classification

ReworkService 또는 관련 helper는 rework comment를 `repository-change`, `metadata-only`,
`external-verification` 중 하나로 분류할 수 있어야 한다.

### CP-016-3: Clean workspace is not DeliveryError

Rework 실행 후 `git status`가 clean이면 `git commit`을 호출하지 않고
`no_repository_changes` 결과로 종료해야 한다.

### CP-016-4: Metadata-only action does not block queue

metadata-only rework는 Issue를 `devbot:blocked`에 남겨 새 작업 큐를 막지 않아야 한다.
명확한 comment와 라벨 상태를 남겨 사람이 처리할 수 있어야 한다.

### CP-016-5: Repository-change rework still commits

파일 변경이 있는 기존 rework 경로는 commit, push, PR update, Issue `devbot:review`
전환을 계속 수행해야 한다.

### CP-016-6: State labels remain single and recoverable

`devbot:rework -> devbot:working -> devbot:review/manual-action` 전이에서 상태 라벨은
항상 하나만 유지되어야 하며, 실패 시 `devbot:working`에 영구 정체되지 않아야 한다.

### CP-016-7: Logs explain no-op rework

`nothing to commit` 상황은 `no_repository_changes`와 action scope를 로그에 남겨야 한다.

### CP-016-8: Result documents operational limitation

`results/016-rework-noop-metadata-handling.md`는 metadata-only rework와 no-op rework 처리
결과, 남은 위험 요소를 기록해야 한다.

## 필수 테스트 이름

- `test_review_prompt_does_not_trigger_rework_for_metadata_only_actions`
- `test_rework_classifies_repository_change_comment`
- `test_rework_classifies_metadata_only_comment`
- `test_rework_classifies_external_verification_comment`
- `test_clean_rework_does_not_commit`
- `test_metadata_only_rework_does_not_block_queue`
- `test_repository_change_rework_still_commits_and_returns_review`
- `test_no_repository_changes_is_logged`

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

CI에서는 실제 관리 저장소를 조회하지 않는 비활성 저장소 설정을 사용한다.

## Files Expected to Change

- `src/devbot/rework.py`
- `src/devbot/review.py`
- `src/devbot/delivery.py`
- `src/devbot/polling.py`
- `src/devbot/models.py`
- `tests/test_rework.py`
- `tests/test_review.py`
- `tests/test_delivery.py`
- `docs/00-roadmap.md`
- `results/016-rework-noop-metadata-handling.md`

## Risk

- metadata-only 요청을 자동 처리하지 않고 manual-action으로 분리하면 사람이 해야 할 일이 남을 수 있다.
- scope classifier가 너무 단순하면 애매한 리뷰 댓글을 잘못 분류할 수 있다.
- rework no-op을 너무 관대하게 처리하면 실제 필요한 코드 변경을 놓칠 수 있다.

## Rollback Strategy

- no-op/metadata 분류가 오작동하면 rework scope classifier와 clean workspace handling만 되돌린다.
- 기존 repository-change rework 경로는 테스트로 보호하고, rollback 후에도 Task 014의 state recovery
  동작은 유지한다.

## Reviewer Focus

- metadata-only/external-verification 요청이 더 이상 commit 경로로 들어가지 않는가.
- `nothing to commit`이 `blocked` 루프를 만들지 않는가.
- 실제 파일 변경이 필요한 rework는 기존처럼 PR을 업데이트하는가.
- 상태 라벨이 단일성과 복구 가능성을 유지하는가.
- 로그와 Result가 운영자가 이해할 수 있을 만큼 구체적인가.

## Definition of Done

- CP-016-1~8에 대응하는 필수 테스트가 모두 존재하고 통과한다.
- metadata-only 또는 external-verification rework가 `blocked` 루프를 만들지 않는다.
- clean workspace rework에서 commit을 시도하지 않는다.
- repository-change rework 회귀가 없다.
- `results/016-rework-noop-metadata-handling.md`가 작성된다.
- 구현 PR이 생성되고 Issue가 `devbot:review`로 전환된다.

## Result 문서 경로

`results/016-rework-noop-metadata-handling.md`
