# Task 024 Result: Timeline Auto Recording

## 완료 내용

Task 024 계약(`tasks/024-timeline-auto-recording.md`)의 CP-024-1부터
CP-024-13까지 전부 구현했다.

Task 017이 GitHub Status Timeline 규격을 정의하고, Task 018이 수동
`devbot timeline start/end/status` CLI를 추가했지만, daemon 자신은 이
comment를 전혀 갱신하지 않았다. 이번 Task는 그 마지막 연결을 완성한다:
`PollingService`/`ReviewService`/`ReworkService`가 IMPLEMENT/REVIEW/REWORK
Job의 생명주기 경계마다 `TimelineService`를 호출해 기존 Task 017/018
Timeline comment를 자동으로 갱신한다. 스케줄러 우선순위, 상태 머신 전이,
재시도 정책, delivery/rework/review 판정 로직은 전혀 바꾸지 않는다 - 오직
"그 판정이 일어날 때 Timeline에도 기록한다"만 추가했다.

- **자동 ready marker (CP-024-1)**: `PollingService`가 claim 직후
  `safe_ready()`를 호출한다. `TimelineService.ready()`는 이미 `phase=queue
  event=ready` marker가 존재하면 몇 번을 재호출해도 no-op이다(어떤 cycle이
  진행 중이든 무관) - Issue당 정확히 한 번만 기록된다.
- **Implement start/end marker (CP-024-2/3)**: `safe_start(phase="dev")`가
  Implementer Agent 실행 *직전*(워크스페이스 준비보다도 먼저 - 어떤 이후
  실패든 이 phase를 닫을 수 있도록) 기록되고, 모든 종료 경로(성공 push,
  `blocked`, `manual-action`, preflight/workspace 실패로 인한 claim
  복구 `restored`)가 `safe_end(phase="dev", result=...)`로 대응한다.
- **Rework 다음 cycle (CP-024-4)**: `ReworkService.process()`가 claim
  직후 `dev:start`, 모든 종료 경로(성공 `pushed`, `blocked`,
  `manual-action`)에서 `dev:end`를 기록한다 - `TimelineService`의 기존
  `current_cycle()` 규칙(가장 최근 `review:end result=request-changes`
  이후 cycle 번호 자동 증가, Task 018 CP-018-5)을 그대로 재사용하므로
  Task 024는 cycle 번호 계산을 새로 만들지 않았다.
- **Review start/end marker (CP-024-5/6)**: `ReviewService.process()`가
  Reviewer Agent 실행 직전 `review:start`, 모든 종료 경로(`MERGE READY` →
  `merge-ready`, `REQUEST CHANGES` → `request-changes`,
  manual-action-required review 결과 → `manual-action`, 게시/전이 실패 →
  `blocked`)에서 `review:end`를 기록한다.
- **설정된 Agent actor (CP-024-7)**: 모든 자동 marker의 `actor`는
  `config.implementer_agent`/`config.reviewer_agent`(예: `claude`,
  `codex`)에서 온다 - 하드코딩된 제품 이름 없음. `devbot.main`이
  `PollingService(actor=...)`/`ReviewService(actor=...)`/
  `ReworkService(actor=...)`에 그대로 주입한다(REWORK도 구현 역할이므로
  `implementer_agent` 사용).
- **재시도 멱등성 (CP-024-8)**: `TimelineService.start()`/`end()` 자신의
  기존 멱등 검사(Task 018 CP-018-8 - 마지막 이벤트가 동일 actor/pr의
  `start`면 재기록하지 않음, 동일 `phase/cycle/result`의 `end`가 이미
  있으면 재기록하지 않음)를 daemon의 모든 자동 호출 경로가 그대로
  재사용한다. Task 024는 이 규칙을 다시 구현하지 않고 `safe_start`/
  `safe_end`를 통해서만 호출한다.
- **실패 시 phase 닫기 (CP-024-9)**: Agent 실행 실패, 검증 실패, delivery
  실패, workspace 준비/검증 실패, review 게시/전이 실패 등 이 Task
  범위에서 다루는 모든 실패 경로가 `blocked`/`manual-action`/`restored`
  중 하나로 열린 phase를 닫는다. 단, `state_writer`(`mark_for_review`/
  `require_manual_action`) 자체가 예외를 던지는(즉 devbot 자신의 GitHub
  쓰기가 실패하는) 극히 드문 `ITERATION_ERROR` 경로는 Timeline을 닫지
  않는다 - 이 경로는 Job 판정이 아니라 devbot 자신의 인프라 오류이고,
  대응하는 정규화된 result가 없어(계약 문구 "when possible") Job 결과
  자체도 이미 미확정 상태로 daemon을 완전히 멈추지는 않는 최후 안전망(Task
  014 CP-014-7)이기 때문이다 - 다음 정상 cycle이 같은 Issue를 재선택하면
  기존 열린 phase 위에서 계속 갱신된다.
- **Timeline 쓰기 실패 격리 (CP-024-10)**: `safe_ready`/`safe_start`/
  `safe_end`(`src/devbot/timeline.py`)가 모든 자동 호출의 유일한
  진입점이다 - `TimelineService`가 예외를 던지면 로그로만 경고하고
  삼켜서, Job의 실제 판정(state 전이, `PollingResult`/`ReviewResult`/
  `ReworkResult`)을 절대 대체하거나 가리지 않는다. `timeline=None`(모든
  기존 호출자/테스트의 기본값)은 완전한 no-op이라 Task 024 이전 동작과
  100% 동일하다.
- **수동 CLI 회귀 없음 (CP-024-11)**: `devbot timeline start/end/status`는
  변경하지 않았다 - `_run_timeline_command`가 여전히 자신만의 `--dry-run`
  플래그로 독립적으로 동작하고, daemon의 자동 기록과 동일한 comment/marker
  형식을 공유한다.
- **기존 워크플로 호환성 (CP-024-12)**: 큐 선택, host-managed worktree
  준비, delivery, review, rework, 재시도, 상태 머신 동작은 전혀 바뀌지
  않았다 - 기존 342개(Task 023 기준) + Task 025의 21개 테스트 전부
  회귀 없이 통과. `timeline`이 주입되지 않은 모든 기존 테스트는 아무
  Timeline 호출도 관측하지 않는다.
- **결과와 문서 (CP-024-13)**: 이 문서, `docs/00-roadmap.md`,
  `docs/10-github-status-timeline.md`(Version 1.1.0 - Task 018/024가
  이미 구현을 채운 "후속 구현 범위" 항목 정정) 갱신.

## 수정 파일

- `src/devbot/timeline.py` - `TimelineService.ready()`(CP-024-1),
  `safe_ready`/`safe_start`/`safe_end`(CP-024-10). 함께 발견한 버그 수정:
  `_describe_waiting()`이 `cycles[latest_cycle]`로 직접 인덱싱해
  `ready`만 기록되고 아직 `dev`/`review` 이벤트가 없는 Issue의 Status
  Card 렌더링이 `KeyError`로 죽었다 - `render_status_card()`의 다른 모든
  자리처럼 `cycles.get(latest_cycle, _CycleView(number=latest_cycle))`로
  고쳤다(테스트로 발견 - "검증 결과" 절 참고).
- `src/devbot/polling.py` - `PollingService.timeline` 필드, IMPLEMENT
  Job의 모든 claim/종료 경로에 `safe_ready`/`safe_start`/`safe_end` 삽입.
- `src/devbot/review.py` - `ReviewService.timeline`/`actor`/`logger`
  필드, `process()`의 모든 종료 경로에 `safe_start`/`safe_end` 삽입.
- `src/devbot/rework.py` - `ReworkService.timeline`/`actor`/`logger`
  필드, `process()`의 모든 종료 경로에 `safe_start`/`safe_end` 삽입.
- `src/devbot/main.py` - `TimelineService` 생성 후 `PollingService`/
  `ReviewService`(`ReworkService`는 `PollingService` 내부에서 이미
  구성)에 `timeline`/`actor`/`logger` 주입.
- `tests/test_polling.py`/`tests/test_review.py`/`tests/test_rework.py`/
  `tests/test_timeline.py` - 필수 checkpoint 테스트 12개 + 보조 테스트
  (idempotency 단위 테스트, safe helper 예외 흡수 테스트 등) 4개.
- `docs/10-github-status-timeline.md` - Version 1.1.0, "후속 구현 범위"
  절에서 자동 기록/CLI 항목을 구현 완료로 정정.
- `docs/00-roadmap.md` - Task 024 항목을 `[ ]`(PR #46/Issue #47 진행 중)
  에서 `[x]`(완료)로 갱신.

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-024-1 자동 ready marker | `test_daemon_records_ready_event_once` |
| CP-024-2 implement start marker | `test_implement_job_records_dev_start` |
| CP-024-3 implement end marker | `test_implement_job_records_dev_end` |
| CP-024-4 rework 다음 cycle | `test_rework_job_records_next_dev_cycle` |
| CP-024-5 review start marker | `test_review_job_records_review_start` |
| CP-024-6 review end 결과 | `test_review_job_records_review_end_result` |
| CP-024-7 설정된 actor | `test_timeline_uses_configured_agent_actor` |
| CP-024-8 재시도 멱등성 | `test_automatic_timeline_recording_is_idempotent` |
| CP-024-9 실패 시 phase 닫기 | `test_failed_job_closes_open_timeline_phase` |
| CP-024-10 Timeline 쓰기 실패 격리 | `test_timeline_write_failure_preserves_primary_job_outcome`, `test_review_timeline_write_failure_preserves_primary_job_outcome`, `test_rework_timeline_write_failure_preserves_primary_outcome`, `test_safe_helpers_swallow_timeline_write_failures` |
| CP-024-11 수동 CLI 회귀 없음 | `test_manual_timeline_cli_remains_compatible` |
| CP-024-12 기존 워크플로 호환성 | `test_existing_workflows_compatible_with_timeline_auto_recording` + 기존 테스트 전부 회귀 없이 통과 |
| CP-024-13 결과/문서 | 이 문서 + `docs/00-roadmap.md` + `docs/10-github-status-timeline.md` |

## 검증 결과

```
uv sync
  Resolved 15 packages, Installed 14 packages (변경 없음)

uv run ruff check .
  최초 구현에서 E501(line too long) 2건 발견(tests/test_review.py:439,
  tests/test_timeline.py:678) - 줄바꿈으로 수정 후 All checks passed!

uv run pytest
  390 passed (Task 025 merge 시점 374개 + Task 024 신규 16개
  [test_polling.py 5개(필수 4 + `test_existing_workflows_compatible_...`
  자체가 필수) + test_review.py 2개 + test_rework.py 2개 + test_timeline.py
  4개 + 나머지 필수 항목은 최초 구현에 이미 포함]; 회귀 없음)

  최초 구현 검증 중 실패 1건 발견: test_ready_records_exactly_one_event
  (KeyError: 1, timeline.py:_describe_waiting) - `ready`만 기록되고
  dev/review 이벤트가 없는 cycle_numbers=[1]인데 `cycles`가 빈 dict라서
  발생. `.get()` fallback으로 수정 후 재실행 시 전체 통과.

uv run devbot doctor
  (실제 hjlee83/devbot 배포 설정으로 실행)
  safe_to_start: yes
  timeline_configuration: OK

uv run devbot --once --dry-run
  (실제 hjlee83/devbot 배포 설정 · 실제 GitHub 인증/조회로 실행, 종료
  코드 0)
  Queue Summary: blocked=1(Issue #47 자신), 나머지 0
  cycle 종료: 후보(rework=0 review=0 implement=0) 선택=0/1 결과=no_ready_task
  1회 실행 완료: no_ready_task
```

### 수동 검증 (계약 Validation Gate "Manual verification" 항목)

실제 배포 저장소에 쓰지 않고, 스크래치패드에 별도 스크립트를 작성해 진짜
`PollingService`/`ReviewService`/`TimelineService` 배선을 인메모리 fake
GitHub 백엔드(HTTP 대신 comment 저장소 하나로 동작하는 `read_client`/
`write_client` 이중 역할 객체) 위에서 그대로 실행했다 - 단위 테스트가 이미
각 항목을 개별적으로 검증하지만, 계약이 "manual verification"으로 명시한
절이라 여러 phase를 이어붙인 실제 흐름을 한 번 더 눈으로 확인했다.

- **IMPLEMENT cycle 실행 후 단일 Timeline comment 확인**: `ready` →
  `dev:start`(actor=codex) → Agent 실행 → `dev:end`(result=pushed)가
  같은 comment에 순서대로 append되고, Status Card가 `Dev: ... (40m)
  (actor: codex)`를 정확히 렌더링했다.
- **REVIEW cycle 실행 후 actor/결과 marker 확인**: 같은 comment 위에
  `review:start`(actor=codex) → `review:end`(result=merge-ready)가
  추가되고, Status Card `Result` 줄이 `MERGE READY`로, `Waiting` 줄이
  `없음 (Merge 대기 중)`으로 바뀌었다.
- **재시도가 marker를 중복시키지 않음 확인**: 이미 기록된 `review:end`를
  동일 인자로 재호출했을 때 `write_client.update_comment` 호출이 늘지
  않고, 파싱된 이벤트 개수도 그대로였다(`TimelineService.end()`의 기존
  멱등 검사가 daemon 재시도 경로에서도 그대로 작동함을 확인).
- **Timeline status가 자동 기록 이벤트로부터 active/waiting/elapsed를
  계산함 확인**: 위 두 phase(Dev 40분, Wait reviewer 20분, Review 5분)
  이후 렌더링된 카드가 `Total active: 45m`, `Total waiting: 20m`,
  `Total elapsed: 1h 5m`을 정확히 표시했다 - 6절 계산 규칙이 수동
  기록뿐 아니라 자동 기록에도 동일하게 적용됨을 확인했다.

이 수동 검증 과정에서 위 "검증 결과" 절에 적은 `_describe_waiting()`
`KeyError` 버그를 실제로 발견했다 - `ready` 단독 이벤트로 Status Card를
렌더링하는 경로는 기존 342개(Task 023 기준) 테스트 중 어느 것도 지나가지
않았던 조합이라, 이 수동 실행이 실제로 유용했던 지점이다.

## TODO

없음 (이 Task 범위 내). Dashboard/UI, 알림, 자동 merge, 다중 Agent
failover, Task 024 이전 완료 Task 구간에 대한 historical backfill,
GitHub comment 갱신 모델을 넘어서는 분산 락, 스케줄러 우선순위/상태
머신/Review Gate 정책 변경은 계약의 명시적 제외 범위이며 후속 Task로
남긴다.

## 위험 요소

- **`state_writer`(`mark_for_review`/`require_manual_action`) 자신이
  예외를 던지는 `ITERATION_ERROR` 경로는 열린 Timeline phase를 닫지
  않는다.** 이 경로는 devbot 자신의 GitHub 쓰기 실패(네트워크/API 오류
  등)로 극히 드물게만 발생하고, 이미 Job 판정 자체가 미확정으로 다음
  cycle이 같은 Issue를 재선택해 이어서 갱신하므로 Timeline이 영구히 깨진
  상태로 남지는 않는다 - 다만 그 사이 Status Card의 `Waiting` 줄은 실제
  상황보다 낙관적으로 보일 수 있다.
- **스크래치 검증에 쓰인 fake GitHub 백엔드는 라벨 상태를 실제로 갱신하지
  않는다** (`write_client`가 `MagicMock`) - 두 phase를 이어붙이는 데는
  문제가 없었지만, "같은 Issue를 반복 claim"하는 시나리오를 스크래치로
  재현하려면 실제 라벨 전이를 반영하는 fake가 추가로 필요했다(이번에는
  단위 테스트의 `_TimelineBackend` + 실제 `IssueStateWriter` 조합이 이미
  이 경로를 커버해 범위에서 제외했다).

## Improvement Suggestions

- `_describe_waiting()`과 `render_status_card()`의 `cycles.get(...,
  _CycleView(...))` 패턴이 지금 3곳에서 반복된다 - 작은 헬퍼로 묶으면
  향후 새 phase(`queue` 외 추가 등)를 넣을 때 같은 종류의 `KeyError`
  회귀를 구조적으로 막을 수 있다. 이번 Task는 발견한 버그 하나만 최소
  수정으로 고치고 범위를 넘는 리팩터는 하지 않았다.
- REWORK Job의 `dev:start`/`dev:end`는 claim 시점에 항상 기록되지만,
  `action_scope`가 `REPOSITORY_CHANGE`가 아니어서 즉시
  `manual-action`으로 끝나는 경로도 `dev:start`를 남긴다 - 프로토콜상
  틀리지는 않지만(Implementer가 claim은 했으므로), Status Card에는
  "손대지 않은 아주 짧은 Dev 구간"으로 보일 수 있다. 원한다면 후속 Task에서
  `action_scope` 분류를 `dev:start` 이전으로 당기는 것을 고려할 수 있다.
