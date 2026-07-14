# Task 012: 역할 기반 폴링 오케스트레이션

Version: 1.0.0
Last Updated: 2026-07-14

## 목표

GitHub Issue의 `devbot:*` 상태 라벨을 실행 트리거로 사용해 구현 역할과 리뷰 역할을 데몬 폴링 루프에 자동 연결한다.

- `devbot:ready` Issue는 구현 역할이 가져가 구현, 검증, Commit, Push, Pull Request 생성까지 수행한다.
- 구현 Pull Request가 생성되고 Issue가 `devbot:review`가 되면 리뷰 역할이 해당 PR의 최신 head commit을 검토하고 PR에 Review Summary를 게시한다.
- 리뷰가 `REQUEST CHANGES`이면 기존 rework 경로가 같은 Issue/Branch/PR을 재사용해 수정하고, 변경된 head commit을 리뷰 역할이 다시 검토한다.
- 리뷰가 `MERGE READY`이면 Issue와 PR을 병합 가능한 상태로 유지하며 자동 Merge는 수행하지 않는다.
- 실행 주체의 제품명이나 모델명은 라벨, 상태 전이, 핵심 오케스트레이션 코드에 포함하지 않고 `implementer`와 `reviewer` 역할만 사용한다.
- 병렬 실행이 가능하도록 작업 스케줄러를 설계하되, 기본값 `MAX_CONCURRENT_JOBS=1`로 현재는 한 번에 한 작업만 실행한다.

---

## 배경

Task 011에서 구현 역할과 리뷰 역할을 서로 다른 `AgentRunner`로 설정·생성·주입할 수 있게 되었고, 기본 역할 구성도 분리되었다. 하지만 현재 실제 폴링 루프에서 실행되는 것은 구현 역할뿐이며, 리뷰 역할은 생성·주입만 되고 호출되지 않는다.

현재 `devbot:ready`는 최초 구현 트리거로 이미 사용되고 있고, `devbot:review`는 구현 완료 또는 rework 완료 후의 대기 상태로 사용된다. Task 010의 rework 경로는 `devbot:review` Issue에 연결된 PR에서 처리되지 않은 `@devbot` 댓글을 감지해 기존 브랜치와 PR을 재사용한다.

이 Task는 기존 상태와 rework 동작을 유지하면서 다음 릴레이를 완성한다.

```text
ready
  -> implementer
  -> working
  -> commit / push / PR
  -> review
  -> reviewer posts Review Summary for current PR head
      -> MERGE READY: review 상태 유지
      -> REQUEST CHANGES: @devbot 리뷰 댓글 게시
          -> implementer rework
          -> review
          -> reviewer re-review for new PR head
```

또한 기존 폴링은 사실상 전역 단일 작업을 전제로 한다. 향후 여러 저장소 또는 여러 독립 작업을 병렬 처리할 수 있도록 동시 실행 제한값과 저장소 단위 상호 배제 규칙을 도입하되, 첫 배포에서는 `MAX_CONCURRENT_JOBS=1`을 기본값으로 사용해 기존 직렬 실행과 동일한 안전성을 유지한다.

---

## Dependencies

- Task 003: 전역 Queue 선택
- Task 005: PollingService 메인 루프
- Task 006: Issue 상태 전환
- Task 007: 검증, Commit, Push, PR 생성
- Task 008: PR 피드백 rework
- Task 010: rework 폴링 연결 및 기존 PR 재사용
- Task 011: implementer/reviewer 역할별 AgentRunner 구성

---

## 구현 범위

### 포함

#### 1. 역할 기반 상태 트리거

- `devbot:ready`는 구현 역할의 최초 구현 트리거다.
- `devbot:review`는 다음 두 종류의 후보를 나타낸다.
  - 연결 PR의 현재 head commit이 아직 자동 리뷰되지 않은 경우: 리뷰 역할 실행 후보
  - 연결 PR에 처리되지 않은 `@devbot` 변경 요청 댓글이 있는 경우: 구현 역할 rework 후보
- rework 후보는 동일 Issue의 신규 리뷰보다 우선한다. 처리되지 않은 변경 요청이 남아 있는 동안 같은 head commit을 다시 리뷰하지 않는다.
- `devbot:working`은 어떤 역할이든 해당 Issue에 대한 활성 작업이 진행 중임을 뜻한다.
- `devbot:blocked`는 실행 실패 또는 계약상 진행 불가 상태다.
- 라벨과 상태 전이에는 특정 제품명, 모델명 또는 공급자명을 사용하지 않는다.

#### 2. 리뷰 역할 자동 실행

- `PollingService` 또는 분리된 orchestration service가 `devbot:review` Issue와 연결된 열린 PR을 찾는다.
- PR의 현재 `head_sha`를 확인한다.
- 해당 `head_sha`에 대한 DevBot 자동 리뷰 marker가 없을 때만 `reviewer_runner`를 실행한다.
- reviewer prompt에는 최소 다음 정보가 포함된다.
  - `AGENTS.md` 리뷰 SOP 준수 지시
  - Task 계약서 경로
  - Result 문서 경로
  - Issue 번호
  - PR 번호
  - PR head SHA
  - 최종 출력이 `# Review Summary` 형식을 따라야 한다는 조건
  - Merge를 수행하지 말라는 조건
- reviewer 실행 결과가 실패하면 Issue를 `devbot:blocked`로 전환하고 원인을 댓글로 남긴다.
- reviewer 실행 결과가 성공하면 PR Conversation에 reviewer stdout을 Review Summary로 게시한다.
- 게시 댓글에는 idempotency marker를 포함한다.

```html
<!-- devbot:auto-review head=<FULL_HEAD_SHA> -->
```

- 동일 PR head SHA에 marker가 이미 있으면 reviewer를 다시 실행하거나 중복 댓글을 게시하지 않는다.
- PR head SHA가 변경되면 새로운 리뷰 대상으로 판단한다.

#### 3. 리뷰 결과와 rework 연결

- Review Summary가 `REQUEST CHANGES` 상태이면 게시 댓글에 `@devbot` 트리거를 포함해 기존 Task 010 rework 경로가 감지할 수 있게 한다.
- Review Summary가 `MERGE READY` 상태이면 `@devbot` 트리거를 포함하지 않는다.
- Review Summary가 두 상태 중 정확히 하나를 포함하지 않거나 둘 다 포함하면 결과를 유효하지 않은 리뷰로 처리하고 Issue를 `devbot:blocked`로 전환한다.
- `REQUEST CHANGES` 댓글은 기존 PR에 게시되고, rework는 기존 Issue/Branch/PR을 재사용한다.
- rework 성공으로 PR head SHA가 변경되면 이전 marker는 새 head에 적용되지 않으므로 reviewer가 다시 실행된다.
- 자동 Merge, Issue Close, 승인 review 제출은 이 Task에서 수행하지 않는다.

#### 4. 병렬 실행 가능한 Job Scheduler

- 구현 최초 실행, rework, 리뷰 실행을 공통 `Job` 개념으로 모델링한다.
- 최소 job type:
  - `IMPLEMENT`
  - `REWORK`
  - `REVIEW`
- 환경변수 `MAX_CONCURRENT_JOBS`를 추가한다.
  - 기본값: `1`
  - 허용값: 1 이상의 정수
  - 0, 음수, 정수가 아닌 값은 설정 로딩 단계에서 `ConfigError`
- 기본값 1에서는 기존과 동일하게 한 번에 하나의 Agent job만 실행한다.
- 값이 2 이상이면 서로 다른 저장소의 독립 작업을 병렬 실행할 수 있다.
- 같은 저장소에서는 동시에 둘 이상의 job을 실행하지 않는다. 기존 단일 local workspace에서 checkout, 수정, 검증, Commit이 충돌할 수 있기 때문이다.
- 같은 Issue에 대해서도 동시에 둘 이상의 job을 실행하지 않는다.
- concurrency slot은 Agent 실행 시작 전에 획득하고 성공, 실패, 예외, timeout 여부와 관계없이 반드시 반환한다.
- `--once`는 한 번의 스케줄링 사이클에서 최대 `MAX_CONCURRENT_JOBS`개 후보를 선택해 모두 종료한 뒤 구조화된 결과를 반환한다.
- 연속 데몬 모드는 빈 slot 수만큼 다음 후보를 채운다.
- dry-run에서는 병렬 실행 가능 후보를 계산할 수 있지만 Agent 실행, 상태 쓰기, 댓글 게시 등 부작용은 발생하지 않는다.

#### 5. 선택 우선순위

사용 가능한 slot에 대해 후보는 다음 순서로 선택한다.

1. 처리되지 않은 `@devbot` 변경 요청이 있는 `REWORK`
2. 현재 head SHA가 아직 리뷰되지 않은 `REVIEW`
3. `devbot:ready` 상태의 `IMPLEMENT`

동일 우선순위에서는 기존 Queue의 결정적 정렬 규칙을 유지한다.

- 우선순위 라벨
- 생성 시각
- Issue 번호

동일 저장소 후보가 여러 개면 가장 앞선 하나만 선택하고 나머지는 다음 사이클로 미룬다.

#### 6. 상태와 실패 처리

- job을 실제 시작할 때 대상 Issue는 `devbot:working`으로 claim한다.
- 구현 및 rework 성공 후에는 `devbot:review`로 복귀한다.
- 리뷰 성공 후에는 결과와 관계없이 Issue를 `devbot:review`로 복귀한다.
  - `REQUEST CHANGES`는 PR 댓글의 `@devbot`이 다음 rework를 트리거한다.
  - `MERGE READY`는 사람이 Merge할 때까지 review 상태를 유지한다.
- Agent 실행 실패, reviewer 결과 형식 오류, 연결 PR 없음, PR head 없음, 댓글 게시 실패는 `devbot:blocked`로 전환하고 원인을 남긴다.
- 상태 쓰기 자체가 실패하면 예외를 밖으로 전파하지 않고 구조화된 iteration error를 반환한다.
- 부분 성공 가능성이 있는 경우 Result에 실제 GitHub 상태와 복구 방법을 기록한다.

#### 7. 문서와 설정

- `AGENTS.md`를 제품명이 아닌 역할 중심 표현으로 갱신한다.
  - 구현 AI -> 구현 역할
  - 리뷰 AI -> 리뷰 역할
  - 자동 트리거 규칙 추가
- `.env.example`에 `MAX_CONCURRENT_JOBS=1` 추가
- `docs/04-agent-system.md`에 역할 기반 자동 릴레이와 리뷰 marker 규칙 추가
- `docs/08-beta-runbook.md`에 기본 직렬 실행, 병렬 실행 전제, 복구 절차 추가
- `docs/00-roadmap.md`에 Task 012 상태 반영
- `results/012-role-based-polling-orchestration.md` 작성

### 제외

- 자동 Merge 또는 자동 Issue Close
- GitHub branch protection 설정 변경
- GitHub Actions에서 외부 Agent를 실행하는 구조
- 저장소 하나에 여러 worktree를 만들어 같은 저장소 작업을 병렬 처리하는 기능
- 분산 lock 또는 여러 VPS 데몬 간 동시 실행 조정
- Agent별 별도 concurrency 값
- reviewer가 GitHub의 formal APPROVE 또는 REQUEST_CHANGES review API를 호출하는 기능
- 공급자별 모델 선택, 비용 제한, 토큰 제한 정책
- 중단된 job의 프로세스 재부착 또는 resume

---

## 동작 규칙

1. 상태 라벨은 Issue마다 정확히 하나만 존재해야 한다.
2. 특정 제품명은 설정값에서만 허용하며 라벨, 상태 enum, scheduler job type, 상태 전이 규칙에는 들어가지 않는다.
3. `MAX_CONCURRENT_JOBS` 기본값은 반드시 `1`이다.
4. 기본 설정에서는 한 번에 한 job만 실행한다.
5. 병렬 설정에서도 같은 저장소 또는 같은 Issue job은 동시에 실행하지 않는다.
6. rework가 review보다 우선하고 review가 신규 implement보다 우선한다.
7. reviewer는 동일 PR head SHA를 한 번만 리뷰한다.
8. PR head SHA가 바뀌면 새 리뷰가 필요하다.
9. `REQUEST CHANGES`만 `@devbot` rework trigger를 생성한다.
10. `MERGE READY`는 자동 Merge를 수행하지 않는다.
11. dry-run은 GitHub 쓰기, Agent 실행, Git 변경을 수행하지 않는다.
12. 모든 job 실패는 다른 slot과 다음 폴링 사이클을 막지 않아야 한다.

---

## 품질 게이트

| ID | 체크포인트 | 필수 테스트 이름 |
|---|---|---|
| CP-012-1 | `devbot:ready` Issue가 구현 역할 job으로 선택된다 | `test_ready_issue_triggers_implement_job` |
| CP-012-2 | 아직 리뷰되지 않은 PR head가 리뷰 역할 job으로 선택된다 | `test_unreviewed_pr_head_triggers_review_job` |
| CP-012-3 | 동일 PR head SHA는 marker로 중복 리뷰되지 않는다 | `test_review_marker_prevents_duplicate_review_for_same_head` |
| CP-012-4 | PR head SHA가 바뀌면 리뷰 역할이 다시 실행된다 | `test_new_pr_head_triggers_review_again` |
| CP-012-5 | `REQUEST CHANGES` 리뷰 댓글은 rework trigger를 포함한다 | `test_request_changes_review_comment_triggers_rework` |
| CP-012-6 | `MERGE READY` 리뷰 댓글은 rework trigger를 포함하지 않는다 | `test_merge_ready_review_comment_does_not_trigger_rework` |
| CP-012-7 | rework 후보가 review와 신규 구현보다 우선한다 | `test_rework_job_has_highest_priority` |
| CP-012-8 | reviewer 실패는 Issue를 blocked로 전환한다 | `test_reviewer_failure_moves_issue_to_blocked` |
| CP-012-9 | 유효하지 않은 Review Summary 상태는 blocked 처리된다 | `test_invalid_review_summary_moves_issue_to_blocked` |
| CP-012-10 | concurrency 기본값은 1이다 | `test_max_concurrent_jobs_defaults_to_one` |
| CP-012-11 | concurrency가 2 이상이면 서로 다른 저장소 job을 병렬 실행할 수 있다 | `test_parallel_jobs_run_for_different_repositories` |
| CP-012-12 | 같은 저장소 job은 concurrency가 남아도 동시에 실행하지 않는다 | `test_same_repository_jobs_are_not_run_in_parallel` |
| CP-012-13 | 실패한 job도 concurrency slot을 반환한다 | `test_failed_job_releases_concurrency_slot` |
| CP-012-14 | 잘못된 concurrency 설정은 로딩 단계에서 실패한다 | `test_invalid_max_concurrent_jobs_is_rejected` |
| CP-012-15 | dry-run review polling은 Agent와 GitHub write를 수행하지 않는다 | `test_review_polling_dry_run_has_no_side_effects` |

추가 회귀 테스트로 다음을 포함한다.

- 기존 ready 최초 구현 흐름
- 기존 rework 흐름
- 기존 `MAX_CONCURRENT_JOBS` 미설정 배포
- 연결 PR이 없는 review Issue
- reviewer stdout이 비어 있는 경우
- marker 댓글 게시 후 상태 전환 실패의 부분 성공 처리
- 여러 저장소 중 하나의 job 실패가 다른 job 완료를 막지 않는 경우
- `KeyboardInterrupt` 또는 timeout에도 slot이 반환되는 경우

---

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

병렬 scheduler의 테스트는 실제 외부 Agent CLI 또는 GitHub 네트워크를 호출하지 않고 deterministic fake runner와 fake client를 사용한다.

---

## Files Expected to Change

- `AGENTS.md`
- `.env.example`
- `src/devbot/config.py`
- `src/devbot/models.py`
- `src/devbot/polling.py`
- `src/devbot/main.py`
- `src/devbot/rework.py` 또는 신규 orchestration module
- `src/devbot/github_client.py`
- `src/devbot/github_write_client.py`
- `tests/test_config.py`
- `tests/test_polling.py`
- 신규 scheduler/reviewer orchestration 테스트 파일
- `docs/00-roadmap.md`
- `docs/04-agent-system.md`
- `docs/08-beta-runbook.md`
- `results/012-role-based-polling-orchestration.md`

실제 구조를 확인한 뒤 필요한 최소 파일만 수정한다.

---

## Risk

- reviewer stdout을 PR 댓글로 게시하므로 출력에 불필요한 로그가 섞일 수 있다.
- marker 댓글 게시 후 상태 전환이 실패하면 리뷰는 완료됐지만 Issue는 working에 남는 부분 성공이 발생할 수 있다.
- `REQUEST CHANGES` 판정 문자열이 엄격하지 않으면 rework 누락 또는 오작동이 생길 수 있다.
- concurrency를 2 이상으로 올리면 CPU, 메모리, Agent rate limit, API rate limit 사용량이 증가한다.
- 같은 저장소 병렬 실행을 허용하면 local workspace가 충돌하므로 이번 Task에서는 금지한다.
- 여러 데몬 프로세스가 동시에 실행되면 프로세스 내부 semaphore만으로는 중복 실행을 막을 수 없다. 기존 process lock을 유지해야 한다.

---

## Rollback Strategy

- `MAX_CONCURRENT_JOBS=1`로 설정해 병렬 실행을 즉시 비활성화한다.
- reviewer 자동 실행을 별도 feature flag로 구현한 경우 해당 값을 끄고 기존 구현/rework 폴링만 유지한다.
- Task 012 변경 Commit을 revert하면 Task 011 상태인 역할별 runner 주입 구조로 돌아간다.
- 중복 자동 리뷰 댓글은 marker로 식별해 수동 삭제할 수 있다.
- blocked Issue는 원인 수정 후 정확히 하나의 `devbot:review` 또는 `devbot:ready` 라벨로 수동 복구한다.

---

## Reviewer Focus

- 특정 제품명에 종속된 라벨, enum, job type 또는 상태 전이가 추가되지 않았는지
- 동일 head SHA의 reviewer 중복 실행이 확실히 방지되는지
- `REQUEST CHANGES`와 `MERGE READY` 판정이 정확히 하나만 허용되는지
- rework -> review -> re-review 루프가 같은 Issue/Branch/PR을 재사용하는지
- `MAX_CONCURRENT_JOBS=1`에서 기존 직렬 동작이 유지되는지
- concurrency 2 이상에서 같은 저장소가 동시에 선택되지 않는지
- 예외, timeout, cancellation 후에도 slot과 상태가 정리되는지
- dry-run이 완전히 무부작용인지
- 자동 Merge가 추가되지 않았는지

---

## Definition of Done

- `devbot:ready`만으로 구현 역할이 작업을 시작한다.
- 구현 PR의 현재 head SHA가 아직 리뷰되지 않았으면 리뷰 역할이 자동 실행된다.
- 리뷰 결과가 PR에 marker와 함께 게시된다.
- `REQUEST CHANGES`는 기존 rework 경로를 자동 트리거한다.
- 수정된 PR head는 다시 자동 리뷰된다.
- `MERGE READY`는 자동 Merge 없이 사람이 처리할 상태로 남는다.
- 특정 Agent 제품명이 workflow 상태와 라벨에 포함되지 않는다.
- `MAX_CONCURRENT_JOBS`가 구현되고 기본값이 1이다.
- 서로 다른 저장소 job은 설정값 범위에서 병렬 실행 가능하다.
- 같은 저장소 또는 같은 Issue job은 동시에 실행되지 않는다.
- CP-012-1부터 CP-012-15까지 필수 테스트가 모두 존재하고 통과한다.
- 전체 검증 명령이 통과한다.
- Result와 관련 문서가 최신 상태다.

---

## Result 문서 경로

`results/012-role-based-polling-orchestration.md`
