# Result: Task 007

## Status
- COMPLETE

## 구현 요약

`tasks/007-push-and-pr.md` 범위대로, 검증 통과 후 커밋·푸시·PR 생성·Issue에
PR 링크 댓글까지 이어지는 "delivery" 파이프라인을 구현했다. Task 006과
마찬가지로 실제 쓰기는 `GitHubWriteClient`를 통해서만 이뤄지고, 모든 단계는
검증 실패 또는 `dry_run`이면 안전하게 중단된다.

- **`src/devbot/delivery.py`(신규)**: `DeliveryService.deliver()`가 전체
  흐름을 담당한다.
  1. `run_verification_commands()`가 `uv run ruff check .` →
     `uv run pytest` 순서로 저장소 로컬 경로에서 실행하고, 첫 실패
     지점에서 즉시 멈춘다(`VerificationResult.passed=False`).
  2. 검증 실패 시 커밋/푸시/PR/댓글을 전혀 수행하지 않고 실패 결과를
     반환한다.
  3. `dry_run=True`(기본값)면 검증까지는 수행하되 이후 단계를 전부
     건너뛴다.
  4. 검증 통과 + `dry_run=False`면 `commit_all_changes()`(`git add -A` →
     `git commit -m`)로 커밋하고, `push_task_branch()`가 `git push origin
     <branch>:<branch>` 형태로 **그 브랜치 하나만** 푸시한다(`--all`이나
     다른 브랜치를 건드리지 않음).
  5. `GitHubWriteClient.create_pull_request(head=branch,
     base=repository.default_branch)`로 PR을 열고, `build_pr_body()`가
     `Closes #<issue_number>`와 체크포인트별 테스트 매핑 표를 PR 본문에
     채운다.
  6. `GitHubWriteClient.create_comment()`로 Issue에 PR 링크 댓글을 남긴다.
  - 검증 실행 함수/커밋 함수/푸시 함수 모두 `DeliveryService` 생성자에서
    교체 가능하다(Task 005의 `PollingService`와 동일한 의존성 주입
    패턴).
- **`src/devbot/github_write_client.py`(수정)**: `create_pull_request()`와
  `PullRequestInfo`(number, html_url) 추가. 기존 `set_labels`/
  `create_comment`는 변경 없음.
- **`src/devbot/models.py`(수정)**: `RepositoryConfig`에 `default_branch:
  str = "main"` 필드 추가 — "PR이 설정된 기본 브랜치를 대상으로 한다"는
  CP-007-4 요구사항을 저장소별로 다르게 설정할 수 있어야 하기 때문.
- **`src/devbot/config.py`(수정)**: `config/repositories.yaml`의 각
  저장소 항목에서 선택적 `default_branch` 키를 읽는다(생략 시 `"main"`).

이번 Task도 `PollingService`(Task 005)나 `IssueStateWriter`(Task 006)에
연결하지 않았다 — Task 006과 동일한 이유로, 실제 폴링 루프에서 "에이전트
성공 → delivery → review 전환"을 잇는 것은 이 Task의 체크포인트에
없고, `mark_for_review()` 호출까지 포함한 통합은 후속 Task(008, PR
피드백 루프)에서 자연스럽게 이어지는 편이 안전하다고 판단했다.

## 생성/수정 파일

- `src/devbot/delivery.py` — 신규 (검증/커밋/푸시/PR/댓글 파이프라인)
- `src/devbot/github_write_client.py` — `create_pull_request()`,
  `PullRequestInfo` 추가
- `src/devbot/models.py` — `RepositoryConfig.default_branch` 필드 추가
- `src/devbot/config.py` — `repositories.yaml`의 `default_branch` 파싱
- `tests/test_delivery.py` — 신규
- `tests/test_github_write_client.py` — `create_pull_request` 테스트 추가
- `results/007-push-and-pr.md` — 본 파일

기존 파일 중 `polling.py`, `issue_state.py`, `workspace.py`는 변경하지
않았다.

## CP-007-1 ~ CP-007-7별 테스트 이름과 결과

| Checkpoint | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-007-1 | 검증 실패 시 커밋 안 함 | `test_failed_verification_prevents_commit` | PASS |
| CP-007-2 | 커밋 메시지가 Issue를 참조함 | `test_commit_message_references_issue` | PASS |
| CP-007-3 | 푸시가 task 브랜치만 대상으로 함 | `test_push_targets_task_branch_only` | PASS |
| CP-007-4 | PR이 설정된 기본 브랜치를 대상으로 함 | `test_pr_targets_default_branch` | PASS |
| CP-007-5 | PR 본문에 모든 체크포인트-테스트 매핑이 포함됨 | `test_pr_body_contains_checkpoint_evidence` | PASS |
| CP-007-6 | Issue가 PR 링크를 받음 | `test_issue_receives_pr_link_comment` | PASS |
| CP-007-7 | dry-run은 커밋/푸시/PR 생성을 전혀 안 함 | `test_delivery_dry_run_has_no_side_effects` | PASS |

추가로 작성한 테스트(필수 아님, `GitHubWriteClient` 쓰기 경로 보강):
`test_create_pull_request_sends_post_and_parses_response`
(`tests/test_github_write_client.py`).

`tests/test_delivery.py` 7개, `tests/test_github_write_client.py`
(create_pull_request 관련) 1개 전부 PASS. 전체 스위트 82개 전부 PASS.

## 검증 명령 결과

| Command | Result |
|---|---|
| `uv sync` | PASS |
| `uv run ruff check .` | PASS (All checks passed!) |
| `uv run pytest` | PASS (82 passed) |
| `uv run devbot --once` | PASS (exit 0, `no_ready_task`) |

## 남은 TODO

- `PollingService`가 에이전트 성공 후 `DeliveryService.deliver()`와
  `IssueStateWriter.mark_for_review()`를 호출하도록 연결하는 작업은
  아직 없다.
- 체크포인트 근거(`CheckpointEvidence`)를 실제 Task 결과 파일이나 에이전트
  출력에서 자동으로 파싱해 `deliver()`에 넘기는 로직도 없다 — 지금은
  호출자가 직접 리스트를 구성해서 전달해야 한다.
- "Repeat repair until success or a defined stop condition"(에이전트
  실패 시 재시도)은 이번 Task 범위에도 없다 — `run_verification_commands`가
  실패하면 `deliver()`는 즉시 실패를 반환할 뿐, 재시도하지 않는다.

## 위험 요소

- `commit_all_changes()`가 `git add -A`로 워크스페이스의 모든 변경 사항을
  스테이징한다. 에이전트가 의도치 않은 파일을 남겼다면 그대로 커밋에
  포함된다 — 이 필터링은 이번 Task 범위 밖이며, 에이전트가 작업 범위를
  스스로 지키는 것에 의존한다.
- `_run_git()`이 `check=False`로 `subprocess.run`을 호출하고 반환 코드로
  직접 실패를 판단해 `DeliveryError`를 던진다(다른 모듈처럼 `check=True`
  예외를 그대로 전파하지 않음) — 실패 시 stderr/stdout을 에러 메시지에
  포함시켜 디버깅 정보를 보존하기 위한 선택이다.
- Task 006과 마찬가지로 로컬 샌드박스에 `uv`가 기본 설치돼 있지 않아
  `pip install --user uv`로 설치한 뒤 검증 명령을 실행했다.
- 이 PR은 아직 병합되지 않은 Task 006 브랜치(`feature/task-006-issue-write-state`)
  위에 쌓았다 — `delivery.py`가 Task 006에서 추가된
  `GitHubWriteClient`/`RepositoryConfig` 변경을 그대로 사용하기 때문.
  베이스 브랜치가 머지되면 이 PR의 베이스도 `main`으로 재설정해야 한다.
