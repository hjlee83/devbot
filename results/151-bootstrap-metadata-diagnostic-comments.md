# Task 151 Result: Comment on Bootstrap Metadata Validation Failures

## 완료 내용

- `BootstrapValidationError`가 누락 metadata field를 구조화해 전달하도록 확장했다.
- `WorktreeManager.prepare()`가 bootstrap validation failure를
  `WorkspacePreparationError`로 변환할 때 누락 field 정보를 보존한다.
- `PollingService`가 `BOOTSTRAP_VALIDATION_FAILED`에 한해 deterministic diagnostic
  Issue comment를 best-effort로 작성한다.
- hidden marker로 같은 누락 field 조합의 중복 diagnostic comment를 억제한다.
- 댓글 조회/작성 실패는 warning으로만 기록하고 기존 workspace preparation failure 결과를
  보존한다.

## 주요 설계 결정

- AI를 호출하지 않고 고정 템플릿으로만 안내한다.
- 기존 `working -> ready` restore와 `WORKSPACE_PREPARATION_FAILED` 반환은 변경하지 않는다.
- 다른 workspace preparation failure category에는 metadata template comment를 남기지 않는다.

## 수정 파일

- `src/devbot/bootstrap.py`
- `src/devbot/worktree.py`
- `src/devbot/polling.py`
- `tests/test_polling.py`
- `tasks/151-bootstrap-metadata-diagnostic-comments.md`
- `results/151-bootstrap-metadata-diagnostic-comments.md`

## Checkpoint Evidence

- First diagnostic comment:
  `test_bootstrap_metadata_failure_posts_actionable_comment`
- Duplicate suppression:
  `test_bootstrap_metadata_failure_comment_is_not_duplicated`
- Corrected Issue retry:
  `test_corrected_bootstrap_issue_processes_after_metadata_failure`
- Comment failure safety:
  `test_bootstrap_metadata_comment_failure_preserves_original_result`
- Non-bootstrap failure exclusion:
  `test_workspace_preparation_failure_skips_agent_and_recovers_state`

## Validation 결과

- `uv run ruff check src/devbot/bootstrap.py src/devbot/worktree.py src/devbot/polling.py tests/test_polling.py tests/test_bootstrap.py tests/test_worktree.py`
  - PASS
- `uv run pytest tests/test_bootstrap.py tests/test_worktree.py::test_ready_issue_without_required_metadata_fails_before_branch_creation tests/test_polling.py::test_workspace_preparation_failure_skips_agent_and_recovers_state tests/test_polling.py::test_bootstrap_metadata_failure_posts_actionable_comment tests/test_polling.py::test_bootstrap_metadata_failure_comment_is_not_duplicated tests/test_polling.py::test_corrected_bootstrap_issue_processes_after_metadata_failure tests/test_polling.py::test_bootstrap_metadata_comment_failure_preserves_original_result`
  - PASS, 14 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1447 passed in 165.68s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - FAIL: 실제 queue에서 `hjlee83/vr-infinite-buy-tracker#2`가 선택되었고,
    bootstrap validation이 `missing required Issue metadata: objective`로 실패해
    `WORKSPACE_PREPARATION_FAILED`로 종료됨. 이 Task가 대상으로 삼은 failure 경로를
    dry-run에서 재현한 결과이며, state는 `working -> ready`로 복구됨.

## 수동 검증

- `BOOTSTRAP_VALIDATION_FAILED` category에서만 metadata diagnostic comment helper가
  호출된다.
- helper는 기존 Issue comments에서 hidden marker를 확인한 뒤 같은 missing field set이면
  comment를 다시 쓰지 않는다.
- comment read/write exception은 warning으로만 처리되어 original
  `WORKSPACE_PREPARATION_FAILED` result를 바꾸지 않는다.

## 남은 TODO와 제한

- 로컬 `.git` ref 생성/lock 쓰기가 제한되어 로컬 Task branch 생성은 실패했다.
  PR 생성 시 GitHub API로 `main` 기준 원격 branch를 만들 예정이다.

## 위험 요소

- Duplicate suppression keys on the hidden marker and missing field list. If the validation
  reason changes while the missing field set stays identical, DevBot will not post another
  diagnostic comment for that Issue.

## Improvement Suggestions

- Future validation errors could expose richer machine-readable details for malformed sections,
  not only missing required sections.
