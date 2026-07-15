# Task 021 Result: Agent Outcome Classification Hardening

## 완료 내용

Task 021 계약(`tasks/021-agent-outcome-classification.md`)의 CP-021-1부터
CP-021-12까지 전부 구현했다. Issue #41의 실제 사고(구현 Agent가 승인 대기로
멈췄는데 DevBot이 계약 커밋만 있는 기존 PR을 근거로 review로 진행하려던 사례)를
막는 것이 목적이며, 기존 scheduler/timeline/retry/delivery/review/state-machine
동작은 이 Task가 명시적으로 바꾼 지점(아래) 외에는 그대로 유지했다.

이전 실행은 `git`/`gh` 명령이 승인을 요구하는 환경 게이트에서 멈춰
(Issue #41 코멘트) 코드 변경 없이 계약서 커밋(`896308f`)만 있는 상태였다.
이번 실행에서 그 지점부터 이어서 구현했다.

- **정규화된 Agent 결과 모델 (CP-021-1)**: `devbot.models.AgentOutcome`
  (`StrEnum`) 9종 — `implementation_completed`, `implementation_skipped`,
  `no_repository_changes`, `approval_required`, `network_blocked`,
  `session_limit`, `repository_locked`, `agent_failed`, `unknown`.
  `devbot.agent_outcome.classify_agent_outcome()`가 `AgentRunResult` 하나를
  받아 이 중 정확히 하나로 분류한다.
- **구조화 신호 우선, 텍스트 매칭은 폴백 (Scope §2)**: `AgentRunResult`에
  `outcome_hint: AgentOutcome | None = None` 필드를 추가했다. 러너가 이 값을
  설정하면 텍스트 매칭 없이 그대로 사용한다(`signal_source="structured"`).
  현재 `ClaudeRunner`/`CodexRunner`는 아직 구조화 신호를 만들지 않으므로
  모든 실제 호출은 `devbot.agents.base`의 텍스트 매칭 함수로 폴백한다
  (`signal_source="text_fallback"`).
- **승인 대기 감지 강화 (CP-021-2)**: 기존
  `_APPROVAL_REQUIRED_PATTERNS`(5개)에 계약이 명시한 문구를 추가했다 —
  `requires approval`, `could you approve`, `approved to proceed`(`need
  ... approved to proceed` 커버), `hold off until you confirm`,
  `can i proceed`. 기존 패턴(`needs your approval` 등)은 그대로 유지해
  기존 회귀 테스트(`test_approval_required_agent_output_skips_delivery`)를
  깨지 않았다.
- **세션/사용량 제한 감지, `.failed`와 독립 (CP-021-3)**: 기존
  `is_session_limit_output()`(Task 019)은 그대로 재사용하되,
  `classify_agent_outcome()`이 이를 `AgentRunResult.failed` 검사보다
  *먼저* 확인한다 — 이전에는 IMPLEMENT 경로에서 세션 제한 힌트가
  `agent_result.failed`가 True일 때만 붙었는데(nonzero exit 전제), 이제는
  exit code 0인 세션 제한 메시지도 delivery 이전에 `devbot:blocked`로
  분류된다. 여전히 자동 재시도하지 않고
  `devbot.reliability.session_limit_block_reason()` 힌트를 붙인다
  (`docs/07-decisions.md`의 기존 "새 상태를 만들지 않는다" 결정 유지).
- **네트워크/권한 차단 감지 (CP-021-4)**: 신규
  `devbot.agents.base.is_network_blocked_output()` —
  네트워크/Git/GitHub/파일시스템/샌드박스 차단 문구(`network is
  unreachable`, `could not resolve host`, `connection refused`,
  `permission denied`, `blocked by the sandbox` 등). "문서화된 동등
  분류"로 신규 `is_repository_locked_output()`도 추가했다(`index.lock`,
  `another git process` 등) — 둘 다 `devbot:manual-action`으로 전이한다.
- **결정론적 전이 테이블 (CP-021-5)**: `devbot.agent_outcome.
  AGENT_OUTCOME_TRANSITIONS` — 9개 `AgentOutcome` 전부를 커버하는
  `dict[AgentOutcome, AgentOutcomeTransition]`. `approval_required`/
  `network_blocked`/`repository_locked`/`implementation_skipped` ->
  `manual-action`; `session_limit`/`agent_failed`/`unknown` -> `blocked`;
  `implementation_completed`(`proceeds_to_delivery=True`)와
  `no_repository_changes`는 `target_state=None`(하위 delivery/evidence
  결과에 따라 갈림). 어떤 outcome도 `devbot:working`을 목표로 하지 않는다.
- **Delivery는 완료된 구현 이후에만 (CP-021-6)**: `PollingService.
  _run_claimed_implement_job()`을 리팩터링해, Agent 실행 직후
  `classify_agent_outcome()`으로 단 한 번 분류하고
  `IMPLEMENTATION_COMPLETED`가 아니면 `self.delivery.deliver(...)`를 아예
  호출하지 않는다(이전에는 `is_approval_required_output()` 하나만
  delivery 이전에 걸렀다).
- **Review는 완료된 구현 이후에만, 계약 전용 PR만으로는 불충분 (CP-021-7)**:
  `delivery_result.message == "no_repository_changes"`이고 linked PR이
  있어도, 신규 `devbot.delivery.branch_has_implementation_evidence()`(해당
  PR branch가 `default_branch` 대비 커밋 1개 초과 — 즉 계약 작성 커밋
  하나만이 아님)가 True를 반환할 때만 review로 복귀한다. 증거가 없으면
  (없는 linked PR과 동일하게) `devbot:manual-action`으로 전환하고 review는
  절대 쓰지 않는다.
- **운영자 진단 (CP-021-8)**: `devbot.agent_outcome.
  AgentOutcomeDiagnostics`/`build_agent_outcome_diagnostics()`/
  `render_agent_outcome_diagnostics()` — 정규화된 outcome, 매칭된
  구조화/폴백 신호, 차단된 워크플로 단계(`blocked_phase`), 결과 Issue
  상태, 필요한 운영자 조치(`recovery_action`)를 모두 노출한다.
- **계약 우선 branch/PR 재사용 회귀 방지 (CP-021-9)**: 실제 구현 증거가
  있는 재사용 PR은 여전히 새 branch/PR을 만들지 않고 그 branch로 delivery가
  호출됨을 확인한다(Task 016 CP-016-10 보장과 이번 CP-021-7 안전장치가
  공존함을 검증).
- **회귀 안전성 (CP-021-10)**: 기존 315개 테스트 중 수정한 테스트 1개
  (`test_implement_no_repository_changes_with_linked_pr_marks_review` —
  아래 "의도적 동작 변경" 참고) 외 전부 통과. 최종 333개 전체 통과.
- **명시적 성공 필수 (CP-021-11)**: exit code 0, 예외 없음, 기존 linked
  PR, clean workspace가 전부 동시에 참이어도(Issue #41 사고 시나리오
  그대로) review로 진행하지 않음을 검증한다. 기본값
  `has_implementation_evidence`(실제 git 명령, 존재하지 않는 워크스페이스
  경로에서도 예외 없이 `False`로 안전하게 폴백)로 검증해, 명시적으로
  주입한 테스트뿐 아니라 기본 동작 자체의 보수적 fail-safe도 증명한다.
- **Result/PR Evidence 정합성 (CP-021-12)**: 이 문서 + PR #40 본문 갱신.

## 수정 파일

신규:
- `src/devbot/agent_outcome.py` — `AgentOutcomeClassification`,
  `classify_agent_outcome()`, `AgentOutcomeTransition`,
  `AGENT_OUTCOME_TRANSITIONS`, `transition_for()`,
  `AgentOutcomeDiagnostics`, `build_agent_outcome_diagnostics()`,
  `render_agent_outcome_diagnostics()`
- `tests/test_agent_outcome.py` — CP-021-1/2/3/4/5/8 테스트 8개

수정:
- `src/devbot/models.py` — `AgentOutcome` StrEnum 9종 추가
- `src/devbot/agents/base.py` — `AgentRunResult.outcome_hint` 필드,
  `_APPROVAL_REQUIRED_PATTERNS` 확장, 신규
  `is_network_blocked_output()`/`is_repository_locked_output()`/
  `is_implementation_skipped_output()`
- `src/devbot/delivery.py` — 신규
  `branch_has_implementation_evidence()`(git rev-list 기반, best-effort)
- `src/devbot/polling.py` — `PollingService.has_implementation_evidence`
  필드 추가(`HasImplementationEvidenceFn`, 기본값
  `branch_has_implementation_evidence`);
  `_run_claimed_implement_job()`의 `agent_result.failed` +
  `is_approval_required_output()` 2단 검사를 `classify_agent_outcome()`
  기반 단일 분기(AGENT_FAILED/SESSION_LIMIT/{APPROVAL_REQUIRED,
  NETWORK_BLOCKED, REPOSITORY_LOCKED, IMPLEMENTATION_SKIPPED}/UNKNOWN/
  IMPLEMENTATION_COMPLETED)로 교체; `no_repository_changes` 분기에 구현
  증거 확인 추가
- `tests/test_polling.py` — 기존
  `test_implement_no_repository_changes_with_linked_pr_marks_review`
  수정(증거 주입 추가, 아래 참고) + 신규
  `test_review_requires_completed_implementation`,
  `test_success_requires_explicit_completion`,
  `test_contract_first_pr_reused_without_false_completion`,
  `test_delivery_requires_completed_implementation`(7-case
  parametrize) = 10개
- `docs/03-state-machine.md`, `docs/04-agent-system.md`,
  `docs/07-decisions.md` — Task 021 설계/결정 기록

## 의도적 동작 변경 (CP-021-10 관련)

`test_implement_no_repository_changes_with_linked_pr_marks_review`(Task 016
CP-016-12 evidence, 계약의 "필수 테스트 이름" 목록에는 없음 — `tasks/016-*.md`
157행 확인)의 픽스처를 수정했다. 원래 이 테스트는 "linked PR이 있으면
`no_repository_changes`는 항상 review로 복귀한다"를 의도적으로 검증했는데,
이것이 바로 Task 021이 막으려는 false-success 경로다. 테스트 **이름**은
그대로 두고(AGENTS.md "필수 테스트 이름은 변경하지 않는다"는 각 Task 계약의
"필수 테스트 이름" 목록에 적용되며, 이 이름은 그 목록에 없었다), `docstring`과
`has_implementation_evidence=lambda *_: True` 주입을 추가해 "구현 증거가 있는
PR 재사용" 케이스로 좁혔다. 증거가 없는 대칭 케이스는 신규
`test_review_requires_completed_implementation`이 검증한다. 이 변경은
`docs/07-decisions.md`의 2026-07-15 "Agent outcome classification closes
the contract-only-PR false-review path" 항목에 기록했다.

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-021-1 정규화된 outcome 분류 | `test_agent_outcome_classification` (+ `test_agent_outcome_classification_prefers_structured_signal`) |
| CP-021-2 승인 대기 감지 | `test_agent_approval_required_detection` |
| CP-021-3 세션 제한 감지 | `test_agent_session_limit_detection` |
| CP-021-4 네트워크 차단 감지 | `test_agent_network_blocked_detection` (+ `test_agent_repository_locked_detection`) |
| CP-021-5 outcome 기반 상태 전이 | `test_agent_outcome_state_transition` |
| CP-021-6 완료된 구현 이후에만 delivery | `test_delivery_requires_completed_implementation`(7 cases) |
| CP-021-7 완료된 구현 이후에만 review | `test_review_requires_completed_implementation` |
| CP-021-8 운영자 진단 | `test_agent_outcome_diagnostics` |
| CP-021-9 계약 우선 PR 재사용 회귀 방지 | `test_contract_first_pr_reused_without_false_completion` |
| CP-021-10 회귀 안전성 | 기존 스위트 전체(수정 1건 제외) 통과 (아래 검증 결과) |
| CP-021-11 명시적 성공 필수 | `test_success_requires_explicit_completion` |
| CP-021-12 Result/Evidence 정합성 | 이 문서 + PR #40 본문 |

## 검증 결과

```
uv sync
  Resolved 15 packages in 26ms
  Checked 14 packages in 5ms

uv run ruff check .
  All checks passed!

uv run pytest
  333 passed (신규 18개: test_agent_outcome.py 8개 + test_polling.py 10개
  [parametrize 7 cases 포함] — 기존 315개 중 수정 1개 제외 회귀 없음)

uv run devbot --once --dry-run
  (실제 hjlee83/devbot 배포 설정·실제 GitHub 인증/조회로 실행, 종료 코드 0)
  DevBot 시작: version=0.1.0 implementer=claude reviewer=codex dry_run=True ...
  시작 검증: workspace_cleanliness/current_branch_compatibility 둘 다 WARNING
    (이 Task 자신의 미커밋 변경 + task/021-* 작업 branch — 정상, Task 019
    설계대로 daemon 시작을 막지 않음)
  Queue Summary: manual-action=1 (Issue #41 자신 — 실제 devbot:manual-action
    라벨과 일치)
  cycle 종료: 결과=no_ready_task (ready 상태 Issue 없음 — 정상, 실제 GitHub
    API 응답)
  1회 실행 완료: no_ready_task
```

### 시나리오 검증 (계약 Validation Gate 요구 항목)

- **approval-required 출력은 delivery/review를 트리거하지 않음**:
  `test_delivery_requires_completed_implementation`의 첫 case +
  `test_approval_required_agent_output_skips_delivery`(기존, 회귀 없음
  재확인)가 `delivery.deliver.assert_not_called()`를 직접 검증한다.
- **session-limit 출력은 무한 반복하지 않음**: `test_agent_session_limit_detection`이
  `transition_for(SESSION_LIMIT).retryable is False`를 검증하고,
  `test_delivery_requires_completed_implementation`의 session-limit case가
  실제 `PollingService` 경로에서 delivery를 호출하지 않음을 확인한다 —
  `devbot:blocked`로 전이되며 `devbot.reliability`의 기존 "재시도 없음"
  정책(Task 019)과 동일하게 자동 재시도되지 않는다.
- **network-blocked 출력은 운영자 조치를 요구함**:
  `test_agent_network_blocked_detection` +
  `transition_for(NETWORK_BLOCKED).target_state is TaskState.MANUAL_ACTION`
  (`test_agent_outcome_state_transition`) — `devbot:manual-action`으로
  전이하며 `recovery_hint`에 "실행 환경 권한을 확인" 문구를 포함한다.
- **명시적으로 성공한 구현은 정상적으로 delivery와 review를 따름**:
  `test_ready_implement_reuses_linked_pr_branch`,
  `test_contract_first_pr_reused_without_false_completion`,
  `test_implement_no_repository_changes_with_linked_pr_marks_review`(구현
  증거가 있는 경우) 모두 회귀 없이 통과 — 정상 경로는 바뀌지 않았다.
- **계약 전용 PR + 미구현은 review에 진입하지 않음**:
  `test_review_requires_completed_implementation` +
  `test_success_requires_explicit_completion`이 Issue #41의 정확한 사고
  시나리오(exit 0, 예외 없음, linked PR 존재, clean workspace)를 재현해
  `devbot:review`가 절대 쓰이지 않음을 직접 검증한다.

## TODO

없음 (이 Task 범위 내). Planner 자동화, Worktree 격리, Timeline 자동 기록,
Dashboard/UI, Notifications, VPS 배포는 계약의 명시적 제외 범위이며 후속
Task로 남긴다.

## 위험 요소

- 네트워크/권한 차단, repository 잠금, 구현 건너뜀 감지는 모두 Agent CLI의
  원본 출력 문구에 대한 텍스트 패턴 매칭이다(기존
  `is_approval_required_output`/`is_session_limit_output`과 동일 방식,
  Task 021 계약도 "text matching may remain as a compatibility fallback"로
  이를 명시적으로 허용). CLI 출력 문구가 바뀌거나 예상 밖 표현을 쓰면
  분류가 틀릴 수 있다 — 최악의 경우 `UNKNOWN`(→ `blocked`,
  "safe failure state, never implicit success")으로 떨어지므로 상태
  머신 안전성 자체는 항상 유지된다.
- `is_implementation_skipped_output()`의 패턴(`no changes needed`,
  `already implemented, skipping` 등)은 "구현을 다 하고 나서 일부만 스킵
  했다"는 긴 응답의 일부로도 우연히 매칭될 수 있다(예: "테스트는 변경 필요
  없음"). 실제로는 구현이 끝났는데 `IMPLEMENTATION_SKIPPED`로 잘못
  분류되면 `devbot:manual-action`으로 보수적으로 빠지므로(성공을 잘못
  차단하는 방향의 오류이지, false-success 방향은 아니다) 이 Task의 핵심
  안전 목표는 지켜지지만 불필요한 수동 개입을 유발할 수 있다.
- `branch_has_implementation_evidence()`(구현 증거 확인)는 로컬 git
  workspace가 대상 branch와 `default_branch` 양쪽을 실제로 갖고 있어야
  정확하다. Fetch가 안 된 얕은 클론이나 존재하지 않는 워크스페이스에서는
  보수적으로 `False`(증거 없음)로 폴백한다 — false-success를 막는
  방향으로는 안전하지만, 실제로 구현이 끝난 PR인데도 워크스페이스 상태
  때문에 일시적으로 `manual-action`으로 빠질 가능성은 있다(다음 정상
  polling cycle에서 워크스페이스가 최신화되면 재확인 가능).
- `AGENT_OUTCOME_TRANSITIONS`의 `session_limit -> devbot:blocked` 매핑은
  Task 021 계약의 "non-retrying recoverable state" 문구를 새 라벨이
  아니라 기존 `docs/07-decisions.md`의 2026-07-15 "새 상태를 만들지 않는다"
  결정과 일치시키는 방향으로 해석했다 — 계약이 명시적으로 새 `devbot:*`
  라벨을 요구하지는 않았고, 기존 결정과의 일관성을 우선했다.
- 공유 운영 배포(`hjlee83/devbot`, 이 Task 자신이 진행 중인 저장소)를
  대상으로 한 진짜 non-dry-run `uv run devbot --once`는 실행하지 않았다 —
  credentials나 network 제약이 아니라, PR #40/Issue #41 자체의 실제 상태를
  이 검증 실행이 예기치 않게 바꿀 위험이 제약이다(Task 019 Result 문서와
  동일한 판단 기준). `--dry-run` 실행이 실제 GitHub 인증/조회 경로가
  동작함을 이미 확인했고(위 검증 결과), non-dry-run 전체 흐름은 mock 기반
  통합 테스트(`tests/test_polling.py`)로 광범위하게 커버된다.

## Improvement Suggestions

- `ClaudeRunner`/`CodexRunner`가 CLI의 구조화된(JSON) 출력을 파싱해
  `AgentRunResult.outcome_hint`를 직접 설정하게 하면, 이번 Task가 도입한
  텍스트 패턴 매칭 의존도를 실질적으로 낮출 수 있다(결과 문서
  016번이 이미 예견한 방향이며, Task 021의 구조화 신호 필드가 그 다음
  단계를 위한 기반이 되도록 설계했다).
- `branch_has_implementation_evidence()`의 "커밋 개수 > 1" 휴리스틱은
  단순하고 보수적이지만, 계약 커밋 자체가 여러 개의 작은 커밋으로
  나뉘어 있는 경우(예: 계약서 오타 수정 커밋이 추가로 있는 경우) 실제
  구현이 없어도 오탐할 수 있다. 향후에는 계약서 커밋 자체에 식별
  가능한 marker(예: 커밋 메시지 접두어)를 남기고 "그 marker 이후 커밋이
  있는지"를 확인하는 방식으로 정밀도를 높일 수 있다.
- `devbot.agent_outcome`의 `AgentOutcomeDiagnostics`를 로그뿐 아니라
  `devbot:manual-action`/`devbot:blocked` 댓글 본문에도 구조화된 형태로
  포함시키면(현재는 reason 문자열에 `matched_reason`만 자연어로 녹아
  있음), 운영자가 GitHub Issue만 보고도 어떤 패턴이 매칭되어 차단됐는지
  바로 알 수 있다.
