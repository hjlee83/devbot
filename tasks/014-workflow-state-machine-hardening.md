# Task 014: Workflow State Machine Hardening

## 목표

Issue/PR 자동화의 구현·리뷰·재작업 상태 전이를 명시적인 상태 머신으로 정리하고,
실패·재시작·중복 폴링 상황에서도 작업이 `devbot:working`에 영구 정체되지 않도록 한다.
특히 `REQUEST CHANGES` 이후 재작업이 안정적으로 실행되고, 실패 시 관찰 가능한 상태로
복구되며, 같은 피드백이나 같은 PR head가 중복 처리되지 않아야 한다.

## 배경

Task 013 운영 중 다음 문제가 실제로 확인됐다.

- 리뷰 작업을 claim한 뒤 워크스페이스 검증이 실패하면 Issue가 `devbot:working`에 남았다.
- `devbot:working` 상태는 전역 동시 실행 보호 로직에 의해 다시 선택되지 않아 영구 정체됐다.
- `devbot:ready`로 수동 복구하면 신규 구현 경로가 실행돼 실제 재작업 없이 다시
  `devbot:review`로 전환될 수 있었다.
- `devbot:review`는 리뷰 대기와 수정 요청 대기를 동시에 표현해 운영자가 상태 의미를
  즉시 구분하기 어려웠다.
- 실패 시 원래 상태 복구, `blocked` 전환, 재시도 가능 여부가 코드 전반에 분산되어 있었다.

## Dependencies

- Task 008: PR feedback rework loop
- Task 010: rework polling integration
- Task 012: role-based polling orchestration 및 Job scheduler
- Task 013: 구조화 로그와 제외 사유
- `AGENTS.md`
- `docs/04-agent-system.md`
- `docs/07-decisions.md`
- `docs/09-task-contract-standard.md`
- `results/013-observability-debug-logging.md`

## 구현 범위

1. Issue 상태 라벨을 단일 상태로 취급하는 명시적 상태 전이 계층을 추가한다.
2. `devbot:rework` 라벨을 추가해 `REQUEST CHANGES` 이후 수정 대기 상태를
   `devbot:review`와 분리한다.
3. 상태별 허용 전이를 코드와 문서에 고정한다.

   ```text
   ready -> working -> review
   review -> working -> review        (자동 리뷰 실행)
   review -> rework                    (REQUEST CHANGES 게시 완료)
   rework -> working -> review         (수정 및 새 head push)
   working -> blocked                  (복구 불가능한 실행 실패)
   working -> previous stable state    (claim 이후 실행 전 검증 실패)
   ```

4. Job claim 전에 현재 상태와 목표 상태를 기록하고, claim 이후 실패 시 다음 정책을 적용한다.
   - Agent 실행 전 실패: claim 이전의 안정 상태로 원자적으로 복구한다.
   - Agent 실행 또는 delivery 실패: `devbot:blocked`로 전환하고 실패 이유를 남긴다.
   - 프로세스 예외가 발생해도 `devbot:working`을 방치하지 않는다.
5. rework 후보는 `devbot:rework` 상태에서만 선택하며, 연결 PR의 아직 처리하지 않은
   `@devbot` 피드백이 있어야 한다.
6. 자동 리뷰가 `REQUEST CHANGES`를 게시하면 Issue를 `devbot:rework`로 전환한다.
7. rework가 새 커밋을 push하고 delivery를 완료하면 Issue를 `devbot:review`로 전환한다.
8. 자동 리뷰가 `MERGE READY`를 게시하면 Issue는 `devbot:review`에 유지한다.
9. 상태 라벨은 `devbot:ready`, `devbot:working`, `devbot:review`,
   `devbot:rework`, `devbot:blocked` 중 최대 하나만 존재하도록 정규화한다.
10. 같은 Issue를 대상으로 중복 claim이 발생하지 않도록 상태 변경 실패와 경쟁 조건을
    감지하고 해당 Job을 실행하지 않는다.
11. 상태 전이와 복구 결과를 Task 013 구조화 로그에 포함한다.
12. 기존 `MAX_CONCURRENT_JOBS` 및 저장소별 동시 실행 제한은 유지한다.
13. `docs/04-agent-system.md`, `docs/07-decisions.md`, `docs/08-beta-runbook.md`,
    `docs/00-roadmap.md`를 새 상태 머신에 맞게 갱신한다.
14. 구현 결과를 `results/014-workflow-state-machine-hardening.md`에 기록한다.

## 제외 범위

- `MERGE READY` 푸시 알림과 `devbot:merge-ready` 라벨
- 자동 merge 및 자동 Issue close
- Heartbeat, 단계별 진행률 댓글, 웹 대시보드
- 검증 실패를 Agent가 자동 수정하는 일반적인 self-healing 반복 루프
- macOS launchd 또는 VPS systemd 배포
- 여러 프로세스가 공유하는 외부 분산 락 도입

## 품질 게이트

### CP-014-1: 상태 라벨 단일성

상태 전이 후 DevBot 상태 라벨은 정확히 하나만 남아야 한다. 기존에 여러 상태 라벨이
붙어 있어도 목표 상태 하나로 정규화해야 한다.

### CP-014-2: 리뷰와 재작업 상태 분리

`REQUEST CHANGES`가 게시되면 Issue는 `devbot:rework`가 되고,
`MERGE READY`이면 `devbot:review`에 유지돼야 한다.

### CP-014-3: rework 선택 조건

rework Job은 `devbot:rework` Issue와 처리되지 않은 `@devbot` PR 피드백이 모두 있을
때만 선택해야 한다. `ready`, `working`, `review`, `blocked`에서는 rework Job을 만들지 않는다.

### CP-014-4: rework 성공 전이

rework가 수정·검증·push·delivery에 성공하면 같은 Branch/PR을 재사용하고 Issue를
`devbot:review`로 전환해야 한다.

### CP-014-5: claim 전 검증 실패 복구

워크스페이스 검증처럼 Agent 실행 전에 발생한 실패는 Issue를 claim 이전 안정 상태로
복구해야 하며 `devbot:working`에 남기지 않아야 한다.

### CP-014-6: 실행 실패의 blocked 전이

Agent 실행, 검증, commit, push 또는 PR delivery에서 복구 불가능한 실패가 발생하면
Issue를 `devbot:blocked`로 전환하고 단계와 원인을 Issue 댓글 또는 구조화 상태 기록으로
남겨야 한다.

### CP-014-7: 예외 안전성

예상하지 못한 예외가 Job 실행 중 발생해도 최종화 로직이 동작해 Issue가
`devbot:working`에 방치되지 않아야 한다.

### CP-014-8: 경쟁 claim 방지

두 폴링 cycle이 같은 후보를 동시에 claim하려 할 때 하나만 성공해야 하며,
상태 변경에 실패한 cycle은 Agent를 실행하지 않아야 한다.

### CP-014-9: 동일 피드백 중복 방지

처리된 rework 댓글은 다시 실행되지 않아야 하며, 새 피드백 또는 새 head가 있을 때만
다음 rework/review가 실행돼야 한다.

### CP-014-10: 기존 우선순위와 concurrency 회귀 방지

`REWORK > REVIEW > IMPLEMENT` 우선순위, 저장소별 단일 Job 제한,
`MAX_CONCURRENT_JOBS` 기본값 1과 병렬 처리 동작을 유지해야 한다.

### CP-014-11: 상태 전이 관찰성

상태 변경 로그에는 repository, issue number, from state, to state, job type, reason이
포함돼야 하며 Secret을 노출하지 않아야 한다.

### CP-014-12: 운영 문서 일치

설계 문서와 runbook의 상태 전이 표, 장애 복구 절차, 수동 복구 명령이 실제 구현과
일치해야 한다.

## 필수 테스트 이름

- `test_state_transition_keeps_exactly_one_devbot_state_label`
- `test_request_changes_moves_issue_from_review_to_rework`
- `test_merge_ready_keeps_issue_in_review_state`
- `test_rework_job_requires_rework_label_and_unprocessed_feedback`
- `test_successful_rework_reuses_pr_and_returns_issue_to_review`
- `test_preflight_failure_restores_previous_stable_state`
- `test_execution_failure_moves_issue_to_blocked_with_reason`
- `test_unexpected_exception_never_leaves_issue_working`
- `test_competing_claim_runs_only_one_job`
- `test_processed_feedback_is_not_reworked_twice`
- `test_state_machine_preserves_job_priority_and_concurrency`
- `test_state_transition_log_contains_correlation_and_reason`

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

CI에서는 실제 관리 저장소를 조회하지 않는 비활성 저장소 설정을 사용한다.

## Files Expected to Change

- `src/devbot/models.py`
- `src/devbot/github_client.py`
- `src/devbot/polling.py`
- `src/devbot/rework.py`
- `src/devbot/review.py`
- `src/devbot/scheduler.py`
- `src/devbot/observability.py`
- `tests/test_polling.py`
- `tests/test_rework.py`
- `tests/test_review.py`
- `tests/test_scheduler.py`
- 상태 전이 전용 모듈 및 테스트 파일(필요한 경우 신규)
- `docs/00-roadmap.md`
- `docs/04-agent-system.md`
- `docs/07-decisions.md`
- `docs/08-beta-runbook.md`
- `results/014-workflow-state-machine-hardening.md`

## Risk

- 기존 Issue에 여러 DevBot 라벨이 붙은 경우 정규화 과정에서 의도하지 않은 상태를
  선택할 수 있다.
- 상태 전이 순서가 GitHub API 호출 실패와 맞물리면 로컬 실행 결과와 원격 라벨이
  일시적으로 불일치할 수 있다.
- rework 라벨 도입 시 기존 `devbot:review + @devbot` 데이터와 호환되지 않을 수 있다.
- 상태 복구가 과도하면 실제 실패를 반복 실행하는 루프가 생길 수 있다.

## Rollback Strategy

- `devbot:rework` 후보 수집과 새 상태 전이 계층을 feature flag 없이 한 Task에서
  도입하되, 문제가 발생하면 Task 013의 `review + @devbot` 선택 방식으로 코드와 문서를
  함께 되돌린다.
- rollback 시 새 라벨이 붙은 열린 Issue는 `devbot:review` 또는 `devbot:blocked`로
  명시적으로 마이그레이션한다.
- 상태 전이 로그와 실패 댓글은 삭제하지 않아 장애 분석 증거를 보존한다.

## Reviewer Focus

- 모든 실패 경로에서 `devbot:working` 잔류 가능성이 제거됐는가.
- `REQUEST CHANGES -> rework -> working -> review`가 실제 댓글과 PR head 기준으로
  종단 간 동작하는가.
- 상태 라벨 변경이 원자적 claim 의미를 제공하고 경쟁 실행을 막는가.
- 기존 우선순위·concurrency·동일 head 중복 리뷰 방지가 유지되는가.
- `blocked`와 이전 상태 복구의 경계가 명확하고 테스트되는가.
- 기존 열린 `devbot:review + @devbot` Issue에 대한 호환 또는 마이그레이션 절차가 있는가.

## Definition of Done

- CP-014-1~12에 대응하는 필수 테스트가 모두 존재하고 통과한다.
- 전체 검증 명령이 성공한다.
- `devbot:rework` 라벨이 생성되고 상태 전이 코드에서 사용된다.
- 리뷰 요청, 수정 요청, 재작업 성공, 사전 검증 실패, 실행 실패의 상태 전이가
  문서와 코드에서 일치한다.
- 어떤 테스트 경로에서도 Issue가 의도치 않게 `devbot:working`에 남지 않는다.
- Task 012의 자동 리뷰와 concurrency 회귀 테스트가 통과한다.
- 운영 문서와 Decision 문서가 갱신된다.
- `results/014-workflow-state-machine-hardening.md`가 작성된다.
- 구현 PR이 생성되고 Issue가 `devbot:review`로 전환된다.

## Result 문서 경로

`results/014-workflow-state-machine-hardening.md`
