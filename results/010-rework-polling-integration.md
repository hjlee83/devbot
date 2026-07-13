# Result: Task 010

## Status

- BLOCKED: 구현과 단위 검증은 완료했지만, 현재 샌드박스에서 GitHub 네트워크
  조회가 차단되어 `uv run devbot --once --dry-run` 표준 검증과 push/PR 생성은
  완료하지 못했다.

## 완료 내용

- `PollingService.run_once()`가 `review` 상태 Issue를 발견하면, `working`
  Issue가 없는 경우 PR/Issue 댓글을 조회하고 `ReworkService.process()`로
  연결하도록 했다.
- `main.py` 실제 CLI 경로에서 `ReworkService`를 구성하고, 기존
  `CodexRunner`를 재사용하는 rework `apply_changes` 콜백을 연결했다.
- `ReworkService`에 `dry_run` 옵션을 추가해 검증 통과 후에도 commit, push,
  reaction, review 전환을 건너뛰게 했다.
- 누락되어 있던 `docs/09-task-contract-standard.md`와
  `tasks/010-rework-polling-integration.md`를 추가했다.

## 수정 파일

- `src/devbot/polling.py`
- `src/devbot/main.py`
- `src/devbot/rework.py`
- `tests/test_polling.py`
- `tests/test_rework.py`
- `tests/test_main_loop.py`
- `docs/09-task-contract-standard.md`
- `tasks/010-rework-polling-integration.md`
- `results/010-rework-polling-integration.md`

## Checkpoint별 테스트

| Checkpoint | 테스트 이름 | 결과 |
|---|---|---|
| CP-010-1 | `test_review_issue_without_unprocessed_comment_waits` | PASS |
| CP-010-2 | `test_review_comment_triggers_rework` | PASS |
| CP-010-3 | `test_working_issue_blocks_rework_even_when_review_exists` | PASS |
| CP-010-4 | `test_rework_reuses_existing_branch_and_pr` | PASS |
| CP-010-5 | `test_rework_dry_run_does_not_push_or_mark_processed` | PASS |
| CP-010-6 | `test_cli_constructs_rework_service` | PASS |

## 검증 결과

| Command | Result |
|---|---|
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv sync` | PASS |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run ruff check .` | PASS |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache uv run pytest` | PASS, 110 passed |
| `UV_CACHE_DIR=/Users/luna/workspace/devbot/.uv-cache DEVBOT_LOCK_FILE=/Users/luna/workspace/devbot/.devbot-verify.lock uv run devbot --once --dry-run` | FAIL: `api.github.com` DNS 해석 실패. 현재 환경의 네트워크 차단으로 GitHub Issue 조회를 완료하지 못했다. |

참고: 기본 `uv` 캐시 경로(`/Users/luna/.cache/uv`)도 샌드박스 권한에 막혀,
검증 명령은 저장소 내부 `.uv-cache`를 지정해 실행했다.

## 기존 브랜치·PR 재사용 검증

- `PollingService`는 review rework 시 `generate_branch_name(repository,
  issue.number, issue.title)`로 기존 Task 브랜치명을 다시 계산해
  `ReworkService.process()`에 전달한다.
- `ReworkService`는 성공 rework에서 `push(repository, branch)`만 호출하고
  `create_pull_request()`를 호출하지 않는다.
- 기존 테스트 `test_rework_reuses_existing_branch_and_pr`가 이 동작을
  계속 검증한다.

## TODO

- 네트워크가 가능한 환경에서 `uv run devbot --once --dry-run`을 재실행한다.
- 검증 통과 후 작업 브랜치에 commit, push하고 구현 PR을 생성한다.
- PR 생성 후 Issue를 `devbot:review` 상태로 전환한다.

## 위험 요소

- 현재 `review` Issue가 여러 개라면 수집 순서상 첫 번째 review Issue만
  한 번의 폴링에서 처리한다.
- `ReworkService.process()` 내부 예외는 `PollingStatus.ITERATION_ERROR`로
  보고되며, 그 예외 발생 시 별도 blocked 전환은 수행하지 않는다.
- dry-run rework는 `IssueStateWriter.request_changes()`를 호출하지만 실제
  GitHub 쓰기는 dry-run writer가 막는다. 이후 commit/push/reaction/review
  전환도 `ReworkService.dry_run`이 막는다.

## Improvement Suggestions

- `uv run devbot --once --dry-run`이 운영 GitHub API에 의존하지 않는 로컬
  smoke 모드를 별도로 제공하면 샌드박스/CI 검증 재현성이 좋아진다.
- `config/repositories.yaml`이 enabled 저장소를 기본값으로 둘 때 표준 검증은
  네트워크와 토큰 상태에 민감해진다. 검증용 repository fixture 경로를 CLI
  옵션으로 받을 수 있게 하는 방안을 검토할 만하다.
