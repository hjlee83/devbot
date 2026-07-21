# Task 152 Result: Pause Bootstrap Validation Failures Until Issue Update

## 완료 내용

- bootstrap metadata diagnostic marker에 Issue body SHA-256 fingerprint를 기록했다.
- `BOOTSTRAP_VALIDATION_FAILED` 복구 시 `devbot:validation-paused` 보조 라벨을 붙이고
  Issue state는 `devbot:ready`로 유지한다.
- ready candidate 수집 시 pause 라벨이 있고 현재 Issue body hash와 일치하는 diagnostic
  marker가 있으면 `validation_paused` exclusion으로 스케줄링에서 제외한다.
- Issue body가 바뀌어 marker hash와 달라지면 자동으로 다시 candidate가 되며, claim 시
  stale `devbot:validation-paused` 라벨을 제거한다.
- transient workspace preparation failure에는 pause label/comment를 적용하지 않는다.

## 주요 설계 결정

- `GitHubIssue`에 `updated_at`이 없으므로 body hash를 pause freshness 기준으로 사용했다.
- paused Issue도 `devbot:ready` state를 유지한다. 이렇게 해야 body 수정 후 별도 human label
  작업 없이 자동 재개할 수 있다.
- pause 라벨이 없는 Issue에는 comment 조회를 하지 않아 일반 ready candidate 경로의 API 비용을
  늘리지 않는다.

## 수정 파일

- `src/devbot/issue_state.py`
- `src/devbot/models.py`
- `src/devbot/polling.py`
- `tests/test_polling.py`
- `tasks/152-pause-bootstrap-validation-failures.md`
- `results/152-pause-bootstrap-validation-failures.md`

## Checkpoint Evidence

- Pause after deterministic metadata failure:
  `test_paused_bootstrap_metadata_failure_is_not_selected_again`
- Automatic resume after body update:
  `test_paused_bootstrap_metadata_failure_resumes_when_issue_body_changes`
- Duplicate suppression:
  `test_bootstrap_metadata_failure_comment_is_not_duplicated`
- Corrected Issue processing:
  `test_corrected_bootstrap_issue_processes_after_metadata_failure`
- Transient failures unchanged:
  `test_transient_workspace_preparation_failure_does_not_pause_issue`

## Validation 결과

- `uv run ruff check src/devbot/bootstrap.py src/devbot/worktree.py src/devbot/polling.py src/devbot/issue_state.py src/devbot/models.py tests/test_polling.py`
  - PASS
- `uv run pytest tests/test_polling.py::test_bootstrap_metadata_failure_posts_actionable_comment tests/test_polling.py::test_bootstrap_metadata_failure_comment_is_not_duplicated tests/test_polling.py::test_corrected_bootstrap_issue_processes_after_metadata_failure tests/test_polling.py::test_bootstrap_metadata_comment_failure_preserves_original_result tests/test_polling.py::test_paused_bootstrap_metadata_failure_is_not_selected_again tests/test_polling.py::test_paused_bootstrap_metadata_failure_resumes_when_issue_body_changes tests/test_polling.py::test_transient_workspace_preparation_failure_does_not_pause_issue tests/test_polling.py::test_workspace_preparation_failure_skips_agent_and_recovers_state`
  - PASS, 8 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1450 passed in 144.32s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - FAIL: 실제 queue에서 `hjlee83/vr-infinite-buy-tracker#2`가 선택되었고,
    bootstrap validation이 `missing required Issue metadata: objective`로 실패해
    `WORKSPACE_PREPARATION_FAILED`로 종료됨. dry-run이라 comment/label write는 수행되지
    않아 pause 상태는 원격에 남지 않는다.

## 수동 검증

- `_render_bootstrap_metadata_diagnostic()` marker가 현재 Issue body hash를 포함한다.
- `devbot:validation-paused` label과 현재 body hash marker가 모두 있을 때만 ready Issue가
  `validation_paused`로 제외된다.
- body가 바뀌면 marker hash가 불일치해 Issue가 다시 implement candidate가 되며,
  claim transition이 stale pause label을 제거한다.
- non-bootstrap workspace preparation failure는 `devbot:ready`로만 복구되고 pause label이나
  diagnostic comment를 만들지 않는다.

## 남은 TODO와 제한

- #151 PR이 아직 main에 없으므로 #152 PR은 #151 작업 브랜치를 base로 생성해야 한다.
  #151 머지 후 base를 `main`으로 변경하고 최종 diff/CI를 다시 확인해야 한다.

## 위험 요소

- Issue title/label 등 body 외 metadata 변경만으로는 pause freshness가 깨지지 않는다. 현재
  bootstrap validation 입력은 Issue body sections이므로 body hash를 기준으로 삼았다.

## Improvement Suggestions

- GitHub Issue `updated_at`을 모델에 추가하면 label/title 등 wider metadata freshness도
  pause 해제 조건으로 사용할 수 있다.
