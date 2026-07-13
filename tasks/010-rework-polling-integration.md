# Task 010: ReworkService 폴링 루프 연결

Version: 1.1.0
Last Updated: 2026-07-14

## 목표

현재 구현되어 있으나 자동 실행 경로에 연결되지 않은 `ReworkService`를
`PollingService`의 실행 흐름에 연결한다.

`devbot:review` 상태의 Issue와 연결된 Pull Request에서 처리되지 않은
`@devbot` 댓글을 감지하면, 새 브랜치나 새 PR을 만들지 않고 기존 작업
브랜치와 기존 PR을 재사용하여 수정 작업을 수행해야 한다.

---

## 배경

현재 DevBot은 다음 흐름을 지원한다.

```text
ready -> working -> Agent 실행 -> 검증 -> Commit -> Push -> PR -> review
```

Task 008에서 `ReworkService`는 구현되었지만, `review` 상태 Issue의 PR 댓글을
자동으로 조회하고 `ReworkService.process()`를 호출하는 경로는 아직
`PollingService`에 연결되어 있지 않다.

따라서 운영 중 PR에 `@devbot` 수정 요청이 달려도 자동 재작업이 시작되지 않는다.

---

## Dependencies

- Task 005: Main Polling Loop
- Task 006: Issue 상태 전환
- Task 007: Commit, Push, PR 생성
- Task 008: PR Feedback Rework Loop
- Task 009: Beta Smoke Test

---

## 구현 범위

### 포함

- `devbot:review` 상태 Issue 조회
- Issue와 연결된 기존 Pull Request 식별
- Pull Request 댓글 조회
- 처리되지 않은 `@devbot` 댓글 감지
- `find_unprocessed_devbot_comments()`와 `ReworkService.process()` 연결
- 기존 Issue, Branch, Pull Request 재사용
- 이미 처리된 댓글의 중복 실행 방지
- 일반 `devbot:ready` 작업과 rework 작업 간 우선순위 정의
- 성공 시 `devbot:review` 복귀
- 실패 시 `devbot:blocked` 전환 및 원인 기록
- 관련 로그와 테스트 추가
- `results/010-rework-polling-integration.md` 작성
- 실제 구현 상태에 맞게 `docs/00-roadmap.md` 갱신

### 제외

- `ClaudeRunner` 구현
- Gemini Runner 구현
- 새로운 Agent 구현
- Codex 자동 리뷰 호출
- GitHub Actions 변경
- 저장소별 검증 명령 설정
- 새로운 PR 또는 새로운 작업 브랜치 생성 방식 도입

---

## 동작 규칙

1. 활성 `devbot:working` 작업이 있으면 새 ready 작업과 rework 작업을 시작하지 않는다.
2. `devbot:review` Issue에 처리되지 않은 `@devbot` 댓글이 있으면 rework를 우선 처리한다.
3. rework는 최초 구현에서 사용한 기존 브랜치와 기존 PR을 반드시 재사용한다.
4. 처리된 댓글은 기존 반응 표시 방식으로 재처리되지 않도록 한다.
5. rework 성공 시 Issue 상태는 다시 `devbot:review`가 된다.
6. rework 실패 시 새 커밋과 Push를 중단하고 Issue를 `devbot:blocked`로 전환한다.
7. rework 대상이 없을 때만 기존 `devbot:ready` 작업 선택 흐름으로 진행한다.

---

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-010-1 | review Issue의 미처리 `@devbot` 댓글을 감지한다 | `test_polling_detects_unprocessed_devbot_review_comment` |
| CP-010-2 | rework가 ready 작업보다 우선 처리된다 | `test_rework_is_prioritized_over_ready_task` |
| CP-010-3 | 기존 브랜치와 기존 PR만 재사용한다 | `test_rework_reuses_existing_branch_and_pull_request` |
| CP-010-4 | 처리된 댓글은 다시 실행하지 않는다 | `test_processed_review_comment_is_not_reworked_again` |
| CP-010-5 | rework 성공 후 review 상태로 복귀한다 | `test_successful_polled_rework_returns_to_review` |
| CP-010-6 | rework 실패 시 blocked 상태와 원인을 기록한다 | `test_failed_polled_rework_moves_to_blocked_with_reason` |
| CP-010-7 | rework 대상이 없으면 기존 ready 폴링이 유지된다 | `test_ready_polling_still_runs_when_no_rework_exists` |
| CP-010-8 | dry-run에서는 GitHub와 Git에 쓰기 부작용이 없다 | `test_rework_polling_dry_run_has_no_side_effects` |

테스트는 Happy Path, Failure Path, Boundary Condition을 포함해야 한다.

---

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

---

## Files Expected to Change

아래 목록은 예상 범위이며, 실제 저장소 구조를 확인한 뒤 필요한 최소 파일만 수정한다.

- `src/devbot/polling.py`
- `src/devbot/rework.py`
- `src/devbot/github_client.py`
- `src/devbot/main.py`
- `tests/test_polling.py` 또는 신규 rework polling 테스트 파일
- `docs/00-roadmap.md`
- `README.md` 또는 `docs/08-beta-runbook.md`
- `results/010-rework-polling-integration.md`

---

## Risk

- Issue와 PR 연결 정보를 잘못 찾으면 다른 PR을 수정할 수 있다.
- 이미 처리한 댓글을 다시 실행하면 반복 수정 루프가 발생할 수 있다.
- ready 작업과 rework 작업의 우선순위가 불명확하면 작업 기아가 발생할 수 있다.
- GitHub API 오류가 폴링 루프 밖으로 전파되면 전체 프로세스가 중단될 수 있다.
- 기존 브랜치가 삭제되었거나 로컬 워크스페이스와 원격 상태가 다를 수 있다.

각 위험은 구조화된 실패 결과와 회귀 테스트로 방어한다.

---

## Rollback Strategy

- rework 폴링 연결 코드를 제거하면 Task 009까지의 ready 작업 흐름으로 복귀할 수 있어야 한다.
- 기존 `ReworkService` 공개 API와 상태 전환 계약은 변경하지 않는다.
- 새 설정이 필요하다면 선택적 기본값을 사용하여 기존 설정 파일과 호환성을 유지한다.
- 실패한 rework에서는 기존 PR과 브랜치의 마지막 정상 커밋을 보존한다.

---

## Reviewer Focus

- rework와 ready 작업의 우선순위가 명확한가
- 기존 브랜치와 PR 재사용이 강제되는가
- 댓글 중복 처리 방지가 신뢰할 수 있는가
- GitHub 오류가 폴링 루프를 중단시키지 않는가
- dry-run 안전성이 유지되는가
- 기존 Task 001~009 테스트에 회귀가 없는가
- Result와 로드맵이 실제 구현 상태를 반영하는가

---

## Definition of Done

- 모든 CP-010 체크포인트에 대응하는 테스트가 존재하고 통과한다.
- 전체 테스트와 Ruff가 통과한다.
- `uv run devbot --once --dry-run`이 성공한다.
- review 상태 PR의 새 `@devbot` 댓글이 자동으로 rework를 시작한다.
- 성공한 rework는 기존 PR에 커밋을 추가하고 review 상태로 복귀한다.
- 실패한 rework는 새 PR을 만들지 않고 blocked 상태와 실패 근거를 남긴다.
- 처리된 댓글은 이후 폴링에서 재실행되지 않는다.
- `results/010-rework-polling-integration.md`가 작성된다.
- 관련 운영 문서와 로드맵이 갱신된다.

---

## 계약서 완성도 규칙

이 문서는 실행용 Issue를 생성하기 전에 완성되어야 한다.

다음 항목이 누락된 초안, 빈 파일, 자리표시자 상태에서는 PR 또는
`devbot:ready` Issue를 생성하지 않는다.

- 목표
- 배경
- Dependencies
- 구현 범위와 제외 범위
- 품질 게이트와 필수 테스트 이름
- 검증 명령
- Files Expected to Change
- Risk
- Rollback Strategy
- Reviewer Focus
- Definition of Done
