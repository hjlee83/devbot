# Task 015: Review Gate Standardization

## 목표

리뷰 역할의 `MERGE READY` / `REQUEST CHANGES` 판단 기준을 메모리나 특정
대화 맥락이 아니라 저장소 문서, 자동 리뷰 프롬프트, 테스트로 고정한다.

리뷰어가 GPT, Codex, Claude, DeepSeek 등 어떤 Agent로 교체되더라도 동일한
검증 게이트를 적용해야 하며, 코드와 테스트가 통과해도 PR Evidence, Result,
Task 계약, CI, 운영 정책 중 하나라도 불일치하면 `REQUEST CHANGES`를 내야 한다.

## 배경

Task 014 리뷰 과정에서 다음 문제가 확인됐다.

- 구현자는 Codex, 리뷰어는 GPT 역할로 분리했지만 같은 대화 맥락에서 리뷰 기준이
  느슨해질 수 있었다.
- PR 제목/본문이 실제 구현 결과와 맞지 않는 상태에서도 기능 blocker가 없다는
  이유로 `MERGE READY`가 게시될 수 있었다.
- 이 기준을 대화 메모리에만 남기면 다른 리뷰 Agent로 교체될 때 다시 흔들릴 수 있다.
- DevBot의 자동 리뷰 프롬프트는 AGENTS.md 리뷰 SOP를 따르라고 하지만, PR Evidence
  불일치나 구현 관여 리뷰어의 엄격성 같은 기준을 충분히 구체적으로 강제하지 않는다.

## Dependencies

- Task 009: Task 계약 / Result / PR Evidence convention
- Task 012: 자동 리뷰 relay
- Task 013: 구조화 로그와 운영 증거
- Task 014: 상태 머신 강화
- `AGENTS.md`
- `docs/09-task-contract-standard.md`
- `src/devbot/review.py`
- `tests/test_review.py`

## 구현 범위

1. `AGENTS.md`의 리뷰 SOP와 리뷰 기준을 강화한다.
   - `MERGE READY`는 모든 검증 게이트가 일치할 때만 허용한다.
   - PR 제목/본문/Evidence 불일치는 `REQUEST CHANGES`로 처리한다.
   - Result 문서가 구현 내용, 검증 결과, 위험 요소와 불일치하면
     `REQUEST CHANGES`로 처리한다.
   - dry-run 실패, sandbox 제한, 수동 개입, 브랜치 혼선 같은 caveat가 있으면
     merge 판단 영향이 명시되어야 한다.
   - 구현 과정에 관여한 Agent가 리뷰할 경우 evidence 기준을 더 엄격히 적용한다.
2. `docs/09-task-contract-standard.md`에 PR Evidence와 Review Gate 표준을 추가한다.
   - Task 하나는 브랜치 하나와 PR 하나로 추적한다.
   - `-impl`, 계약 전용 PR 같은 별도 브랜치/PR 분리는 기본값이 아니다.
   - PR 본문은 Task 목표, 구현 요약, CP별 검증, Result 경로, CI 결과, 위험 요소를
     포함해야 한다.
3. 자동 리뷰 프롬프트(`build_review_prompt`)에 엄격한 Review Gate 체크리스트를
   명시한다.
   - 리뷰어는 코드/테스트뿐 아니라 Task 계약, Result, PR 본문, CI, 운영 정책
     일치 여부를 판단해야 한다.
   - 하나라도 불일치하면 `REQUEST CHANGES`를 출력해야 한다.
4. 자동 리뷰 프롬프트 테스트를 추가/수정한다.
   - PR Evidence 불일치가 `REQUEST CHANGES` 조건으로 프롬프트에 포함되는지 검증한다.
   - Task 하나 = 브랜치 하나 = PR 하나 정책이 프롬프트 또는 문서에 포함되는지 검증한다.
   - 구현 관여 리뷰어는 evidence를 더 엄격하게 본다는 기준이 포함되는지 검증한다.
5. `docs/00-roadmap.md`에 Task 015를 추가한다.
6. 구현 결과를 `results/015-review-gate-standardization.md`에 기록한다.

## 제외 범위

- 실제 PR 본문 자동 수정 기능
- GitHub branch protection 설정 변경
- 자동 merge 도입
- `devbot:merge-ready` 라벨 및 멘션 알림
- Worktree Manager
- Agent failover 또는 quota/session limit 분류

## 품질 게이트

### CP-015-1: AGENTS 리뷰 기준 강화

`AGENTS.md`에는 `MERGE READY`와 `REQUEST CHANGES` 조건이 명확히 분리되어야 한다.
PR Evidence, Result, CI, Task 계약, 운영 정책 불일치가 `REQUEST CHANGES` 사유임을
명시해야 한다.

### CP-015-2: Task/PR 단일 추적 정책

문서에는 Task 하나를 기본적으로 브랜치 하나, PR 하나로 추적한다는 정책이 있어야 한다.
별도 구현 브랜치나 계약 전용 PR을 기본 흐름으로 설명해서는 안 된다.

### CP-015-3: PR Evidence 필수 항목

PR 본문 표준에는 Task 목표, 구현 요약, CP별 검증, Result 문서, CI 결과, 위험 요소가
포함되어야 한다.

### CP-015-4: 자동 리뷰 프롬프트 강화

`build_review_prompt()`는 리뷰 Agent에게 코드/테스트 외에 Task 계약, Result, PR 본문,
CI, 운영 정책을 함께 검증하라고 지시해야 한다.

### CP-015-5: 불일치 시 REQUEST CHANGES

자동 리뷰 프롬프트는 검증 게이트 중 하나라도 불일치하면 `MERGE READY`가 아니라
`REQUEST CHANGES`를 출력하라고 명시해야 한다.

### CP-015-6: 구현 관여 리뷰어 엄격성

문서 또는 프롬프트에는 구현 과정에 관여한 Agent가 리뷰할 경우 evidence 기준을 더
엄격히 적용해야 한다는 규칙이 포함되어야 한다.

### CP-015-7: Result 문서 일치

`results/015-review-gate-standardization.md`는 실제 수정 파일, 테스트, 검증 결과,
남은 위험 요소와 일치해야 한다.

### CP-015-8: 기존 리뷰 상태 파싱 회귀 방지

`MERGE READY` / `REQUEST CHANGES` 중 정확히 하나만 허용하는 기존 리뷰 결과 파싱과
Task 014의 `review -> rework` 상태 전이는 회귀하지 않아야 한다.

## 필수 테스트 이름

- `test_agents_review_gate_requires_request_changes_for_evidence_mismatch`
- `test_task_contract_standard_documents_single_task_branch_pr_policy`
- `test_review_prompt_requires_contract_result_pr_ci_alignment`
- `test_review_prompt_requires_request_changes_for_any_gate_mismatch`
- `test_review_prompt_mentions_stricter_evidence_for_involved_reviewer`
- `test_review_status_parsing_still_requires_exactly_one_status`

## 검증 명령

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

CI에서는 실제 관리 저장소를 조회하지 않는 비활성 저장소 설정을 사용한다.

## Files Expected to Change

- `AGENTS.md`
- `docs/09-task-contract-standard.md`
- `docs/00-roadmap.md`
- `src/devbot/review.py`
- `tests/test_review.py`
- 문서 검증 테스트 파일(필요한 경우 신규)
- `results/015-review-gate-standardization.md`

## Risk

- 리뷰 프롬프트가 너무 길어져 자동 리뷰 비용이 증가할 수 있다.
- PR Evidence 불일치를 모두 blocker로 처리하면 초기에는 `REQUEST CHANGES`가 늘어날 수 있다.
- 문서 규칙과 실제 DevBot 브랜치 생성 정책이 아직 완전히 일치하지 않을 수 있다.

## Rollback Strategy

- 자동 리뷰 프롬프트 변경이 과도하게 엄격해 정상 PR을 막으면 `build_review_prompt()`의
  추가 체크리스트만 되돌린다.
- 문서에 추가된 Review Gate 표준은 운영 규칙이므로, rollback 시에도 이전 기준으로
  되돌리는 이유를 `docs/07-decisions.md` 또는 Result에 남긴다.

## Reviewer Focus

- `MERGE READY` 조건이 모호하지 않고 실행 가능한가.
- PR Evidence 불일치가 명확히 `REQUEST CHANGES` 사유로 고정됐는가.
- Task 하나 = 브랜치 하나 = PR 하나 정책이 문서와 프롬프트에 일관되게 반영됐는가.
- 자동 리뷰 프롬프트가 실제 리뷰 Agent에게 충분히 구체적인 체크리스트를 주는가.
- 기존 리뷰 상태 파싱과 Task 014 상태 전이가 회귀하지 않았는가.

## Definition of Done

- CP-015-1~8에 대응하는 필수 테스트가 모두 존재하고 통과한다.
- `AGENTS.md`와 `docs/09-task-contract-standard.md`에 엄격한 Review Gate가 기록된다.
- 자동 리뷰 프롬프트가 계약/Result/PR/CI/운영 정책 일치 여부를 검증하도록 갱신된다.
- PR Evidence 불일치가 `REQUEST CHANGES` 사유임이 코드 테스트로 고정된다.
- `results/015-review-gate-standardization.md`가 작성된다.
- 구현 PR이 생성되고 Issue가 `devbot:review`로 전환된다.

## Result 문서 경로

`results/015-review-gate-standardization.md`
