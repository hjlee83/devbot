# Task 155 Result: Standardize Blocked Job Diagnostics and Resume

## 완료 내용

- `devbot.blocked_recovery` 모듈을 추가해 blocked diagnostic comment 렌더링과 resume
  안전성 검증을 분리했다.
- delivery/unexpected exception block 경로가 structured blocked diagnostic comment를
  Issue에 남기도록 변경했다.
- diagnostic comment에는 failed stage, exception type/message, missing path, repository,
  Issue, branch, worktree path, changed files, recovery steps, traceback excerpt가 포함된다.
- traceback excerpt에서 흔한 secret 환경 변수 값을 redaction한다.
- `devbot resume <repository> <issue-number>` CLI를 추가했다.
- resume command는 blocked Issue, preserved worktree, Task Contract 존재를 검증한 뒤
  `devbot:blocked -> devbot:ready`로 복구하고 timeline start/end를 기록한다.
- Task Contract path가 Issue 본문에 없거나, worktree 밖으로 이탈하거나, 선언된 파일이
  없으면 resume을 fail-closed로 거부한다.
- unsafe resume은 fail-closed로 거부하고 cleanup/restart 안내를 출력한다.

## 주요 설계 결정

- resume command는 직접 Agent를 실행하지 않는다. 안전한 label 복구만 수행하고, 다음 daemon
  polling cycle이 기존 worktree reuse/resume 경로를 사용하게 한다.
- duplicate blocked comment는 blocked Issue가 scheduler 후보에서 제외되는 기존 상태 모델을
  이용한다. diagnostic body에는 향후 update/dedupe에 쓸 수 있는 hidden marker를 포함했다.
- arbitrary remediation command 자동 실행은 범위 밖으로 유지했다.

## 수정 파일

- `src/devbot/blocked_recovery.py`
- `src/devbot/polling.py`
- `src/devbot/main.py`
- `tests/test_blocked_recovery.py`
- `tests/test_polling.py`
- `tests/test_main.py`
- `tasks/155-blocked-job-diagnostics-resume.md`
- `results/155-blocked-job-diagnostics-resume.md`

## Checkpoint Evidence

- Structured diagnostic:
  `test_blocked_diagnostic_comment_extracts_missing_path`,
  `test_unexpected_exception_never_leaves_issue_working`
- Safe resume:
  `test_validate_blocked_resume_accepts_preserved_contract`,
  `test_resume_command_restores_blocked_issue_when_worktree_is_safe`
- Unsafe resume refusal:
  `test_validate_blocked_resume_rejects_missing_worktree`,
  `test_validate_blocked_resume_rejects_missing_contract_metadata`,
  `test_validate_blocked_resume_rejects_missing_declared_contract`,
  `test_validate_blocked_resume_rejects_contract_path_traversal`,
  `test_resume_command_refuses_missing_worktree`

## Validation 결과

- `uv run ruff check src/devbot/blocked_recovery.py src/devbot/polling.py src/devbot/main.py tests/test_blocked_recovery.py tests/test_polling.py tests/test_main.py`
  - PASS
- `uv run pytest tests/test_blocked_recovery.py tests/test_polling.py::test_unexpected_exception_never_leaves_issue_working tests/test_main.py::test_resume_command_restores_blocked_issue_when_worktree_is_safe tests/test_main.py::test_resume_command_refuses_missing_worktree`
  - PASS, 9 passed
- `uv run ruff check .`
  - PASS
- `uv run pytest`
  - PASS, 1458 passed in 156.38s
- `uv run devbot doctor`
  - FAIL: startup self-update가 dirty operator checkout에서 중단됨
    (`skip_reason=operator checkout dirty`). 현재 Task 변경 파일 때문에 발생한 운영
    전제 조건 실패이며 코드 테스트 실패는 아님.
- `uv run devbot --once --dry-run`
  - PASS: 실제 queue에서 blocked Issue 1개가 관측되었고 scheduler 후보에서 제외되어
    `NO_RUNNABLE_TASK`로 종료됨.

## 수동 검증

- `FileNotFoundError`의 `filename`을 diagnostic의 missing path로 렌더링한다.
- `devbot resume`은 blocked label, worktree existence, Git checkout, declared Task Contract
  metadata, path containment, file presence를 모두 확인한 뒤에만 `devbot:ready`로 복구한다.
- unsafe resume은 label write 없이 stderr에 거부 사유와 cleanup/restart 안내를 출력한다.

## 남은 TODO와 제한

- Current implementation resumes by returning the Issue to `devbot:ready`; the next daemon cycle
  performs the actual preserved-worktree continuation.
- Duplicate suppression for repeated identical block writes is covered by blocked Issues being
  unschedulable. A future update can actively update an existing diagnostic marker instead.

## Review Follow-up

- PR review comment 반영: Task Contract path 미선언 시 resume을 거부하도록 변경했다.
- PR review comment 반영: declared Task Contract path가 preserved worktree 밖으로 이탈하면
  resume을 거부하도록 변경했다.
- Regression 추가:
  `test_validate_blocked_resume_rejects_missing_contract_metadata`,
  `test_validate_blocked_resume_rejects_missing_declared_contract`,
  `test_validate_blocked_resume_rejects_contract_path_traversal`.

## 위험 요소

- Resume validation is intentionally conservative and refuses missing worktrees or missing declared
  Task Contracts. Operators may need `devbot worktree cleanup` plus reapplying `devbot:ready` for
  unrecoverable worktree states.

## Improvement Suggestions

- Extend structured diagnostics to review/rework block paths.
- Store richer resume checkpoint metadata when delivery fails after partial progress.
