# Task 005: Polling Main Loop

Version: 1.0.0
Last Updated: 2026-07-13

## 목표

기존 설정, GitHub 읽기 클라이언트, 전역 큐, 워크스페이스, AgentRunner를 연결해
DevBot의 1회 폴링과 연속 폴링 루프를 구현한다.

## 선행 조건

- Task 001 완료
- Task 002 완료
- Task 003 완료
- Task 004 완료
- 구현 전 `results/001-bootstrap.md`부터 `results/004-workspace-agent.md`까지 읽는다.

## 포함 범위

- `--once` 실행 모드
- 연속 폴링 모드
- 전역 활성 작업 확인
- 다음 `devbot:ready` Issue 하나 선택
- 워크스페이스 검증
- AgentRunner 호출
- 구조화 로그
- 종료 신호 처리
- 오류 격리
- 단위 테스트
- Result 문서
- PR 생성

## 제외 범위

- GitHub Issue 라벨 실제 변경
- `ready -> working`
- `working -> review`
- Git commit/push 자동화
- PR 생성 자동화
- PR 댓글 감지
- SQLite
- 자동 재시도 정책

상태 변경과 GitHub 쓰기는 Task 006 이후 범위다.

## 핵심 규칙

새 작업은 아래 조건에서만 선택한다.

```text
전체 저장소의 devbot:working 개수 == 0
AND
전체 저장소의 devbot:review 개수 == 0
```

선택 순서:

1. `priority:high`
2. `priority:medium`
3. `priority:low`
4. 우선순위 라벨 없음
5. 같은 우선순위는 오래된 Issue 우선

이번 Task에서는 Issue를 실제 claim하지 않는다.
따라서 연속 모드에서 같은 Issue가 반복 선택될 수 있으며, 이 한계는 Task 006에서 해결한다.

## CLI 요구사항

### 1회 실행

```bash
uv run devbot --once
```

순서:

1. 설정 로드
2. 프로세스 락 획득
3. 활성 작업 조회
4. 활성 작업이 있으면 로그 후 정상 종료
5. 활성 작업이 없으면 ready Issue 조회
6. 대상이 없으면 로그 후 정상 종료
7. 대상이 있으면 워크스페이스 검증
8. AgentRunner 호출
9. 결과 요약
10. 락 해제
11. 종료

### 연속 실행

```bash
uv run devbot
```

- 설정된 `POLL_INTERVAL_SECONDS`로 반복한다.
- SIGINT/SIGTERM 수신 시 안전하게 종료한다.
- 종료 시 락을 해제한다.

### Dry-run

기본값은 `DRY_RUN=true`.

Dry-run에서는:

- 외부 Agent 프로세스를 실행하지 않는다.
- GitHub 쓰기를 수행하지 않는다.
- 선택 및 실행 계획만 로그로 남긴다.

## 설계 요구사항

### PollingService

`main.py`에 모든 책임을 몰아넣지 않는다.

권장 인터페이스:

```python
class PollingService:
    def run_once(self) -> PollingResult:
        ...
```

### 의존성 주입

테스트에서 다음을 대체 가능하게 만든다.

- GitHubClient
- GlobalQueue
- WorkspaceManager
- AgentRunner
- Sleeper 또는 Clock
- Logger

### 결과 모델

예시 상태:

- `SKIPPED_ACTIVE_TASK`
- `NO_READY_TASK`
- `TASK_SELECTED`
- `AGENT_COMPLETED`
- `AGENT_FAILED`

문자열 남용 대신 Enum 또는 명확한 자료형을 사용한다.

### 오류 처리

- 한 Issue 처리 오류가 전체 상태를 손상시키지 않아야 한다.
- 오류 원인과 Issue 정보가 로그에 포함되어야 한다.
- 예외를 무조건 삼키지 않는다.
- `--once` 실패는 명확한 종료 코드로 표현한다.

## 로그 요구사항

사람이 읽는 로그는 한국어로 작성한다.

최소 포함:

- 폴링 시작
- 관리 저장소 수
- 활성 작업 여부
- ready Issue 수
- 선택 저장소와 Issue 번호
- AgentRunner 모드
- Dry-run 여부
- 실행 결과
- 다음 폴링 시간
- 종료 신호
- 오류 원인

토큰, API 키, 인증 헤더는 출력하지 않는다.

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-005-1 | working Issue가 있으면 새 작업을 선택하지 않는다 | `test_iteration_skips_when_working_task_exists` |
| CP-005-2 | review Issue가 있으면 새 작업을 선택하지 않는다 | `test_iteration_skips_when_review_task_exists` |
| CP-005-3 | 활성 작업이 없으면 ready Issue 하나를 선택한다 | `test_iteration_selects_one_ready_issue` |
| CP-005-4 | ready Issue가 없으면 정상적으로 아무 작업도 하지 않는다 | `test_iteration_handles_empty_queue` |
| CP-005-5 | 선택된 Issue의 워크스페이스를 검증한다 | `test_iteration_validates_selected_workspace` |
| CP-005-6 | 선택된 Issue를 AgentRunner에 전달한다 | `test_iteration_invokes_agent_with_selected_task` |
| CP-005-7 | Dry-run은 외부 프로세스를 실행하지 않는다 | `test_iteration_dry_run_has_no_external_side_effects` |
| CP-005-8 | `--once`는 한 번만 실행하고 종료한다 | `test_run_once_exits_after_single_iteration` |
| CP-005-9 | 연속 모드는 설정된 폴링 간격을 사용한다 | `test_continuous_loop_uses_configured_poll_interval` |
| CP-005-10 | SIGINT/SIGTERM 수신 시 안전하게 종료한다 | `test_shutdown_signal_stops_loop_gracefully` |
| CP-005-11 | 오류가 구조화된 실패 결과와 로그로 남는다 | `test_iteration_error_is_reported_without_state_corruption` |
| CP-005-12 | 프로세스 락이 중복 실행을 막는다 | `test_main_loop_respects_process_lock` |

## 테스트 품질 요구사항

반드시 검증:

### Happy Path
- ready Issue 선택
- 워크스페이스 검증
- AgentRunner 호출
- 정상 결과 반환

### Failure Path
- GitHub 조회 실패
- 워크스페이스 검증 실패
- AgentRunner 실패
- 중복 프로세스 락

### Boundary Condition
- 저장소 0개
- ready Issue 0개
- 최소 polling interval
- sleep 중 종료 신호
- 같은 우선순위 Issue 여러 개

실제 GitHub 계정과 Codex 실행 없이 테스트 가능해야 한다.

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once
```

## Result 문서

다음 파일을 작성한다.

```text
results/005-main-loop.md
```

반드시 포함:

- 구현 요약
- 생성/수정 파일
- CP-005-1 ~ CP-005-12별 테스트 이름
- 테스트 결과
- 검증 명령 결과
- 남은 TODO
- Task 006으로 넘길 제약
- 위험 요소
- Improvement Suggestions

## Git 규칙

- 브랜치: `feature/task-005-main-loop`
- 모든 검증 성공 후 Commit
- Push 허용
- PR 생성 허용
- main/master 직접 Commit 및 Push 금지

## PR 요구사항

PR 본문에 CP-005-1부터 CP-005-12까지 다음 표를 작성한다.

| 체크포인트 | 필수 동작 | 테스트 이름 | 결과 |
|---|---|---|---|
| CP-005-1 | working 존재 시 선택 금지 | `test_iteration_skips_when_working_task_exists` | PASS/FAIL |

추가 포함:

- 요약
- 설계 선택
- 검증 결과
- 알려진 한계
- Task 006과의 연결점

## Reviewer Focus

- `main.py`에 책임이 집중되지 않았는가
- 한 번 실행 로직이 독립적으로 테스트 가능한가
- 실제 네트워크와 외부 프로세스 없이 테스트 가능한가
- 활성 작업 차단 규칙이 전체 저장소 기준인가
- Dry-run이 실제 부작용을 막는가
- 연속 루프가 테스트에서 무한 실행되지 않는가
- 종료 신호가 락과 리소스를 해제하는가
- Task 006 범위의 GitHub 쓰기가 섞이지 않았는가

## 완료 조건

- CP-005-1 ~ CP-005-12 테스트 존재
- 모든 필수 테스트 PASS
- Ruff PASS
- Pytest PASS
- `uv run devbot --once` PASS
- Result 문서 완료
- PR 생성
- CI 성공
