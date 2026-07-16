# DevBot AGENTS

Version: 2.0.0
Last Updated: 2026-07-16

> 이 문서는 DevBot 프로젝트에서 AI Agent가 따라야 하는 실행 규칙과 SOP를 정의한다.
> 안정적인 프로젝트 원칙은 `CONSTITUTION.md`가 최상위 기준이다.
> 충돌이 있으면 `CONSTITUTION.md`를 따르고, 구현 상세는 이 문서와 관련 `docs/`를 따른다.

이 규칙은 특정 제품이나 모델에 종속되지 않는다. Planner, Implementer,
Reviewer, Operator는 역할이며, 실제 Agent는 설정으로 교체할 수 있다.
Planner 역할의 세부 워크플로와 기계 검증 규칙은 `docs/12-planner-workflow.md`를 따른다.

---

# 1. 고정 운영 경계

## Human-first Planning

- 아이디어, 아키텍처, 범위, 위험, Acceptance Criteria는 프로젝트 소유자와 ChatGPT가 대화로 결정한다.
- Planner는 승인되지 않은 요구사항을 만들거나 범위를 확장하지 않는다.
- 프로젝트 소유자의 명시적 승인 후에만 Planner 산출물을 생성한다.

## Planner

Planner는 승인된 설계를 다음 산출물로 변환한다.

- Task Issue 1개
- Task Branch 1개
- Task Contract 1개
- Pull Request 1개
- 초기 라벨과 상호 링크

별도 Execution Issue는 생성하지 않는다.

## DevBot

Task가 `devbot:ready`가 된 뒤 DevBot은 다음 흐름을 소유한다.

```text
IMPLEMENT
→ REVIEW
→ REWORK when required
→ REVIEW
→ READY TO MERGE
```

Merge는 프로젝트 소유자가 정책을 명시적으로 바꾸기 전까지 사람이 수행한다.

---

# 2. 언어 정책

사람이 읽는 출력은 한국어로 작성한다.

한국어 대상:

- Task 결과와 Result
- PR 제목, 설명, 리뷰
- Issue 댓글
- Commit 설명
- 최종 보고와 개선 제안
- 에러 원인 설명

영어 사용 허용:

- 소스코드와 식별자
- 테스트 함수명
- API 이름
- GitHub Label
- Branch 이름
- Conventional Commit type

---

# 3. 개발 환경

필수 환경:

- Python 3.13
- uv
- pytest
- ruff

모든 공개 API는 타입 힌트를 작성한다.
macOS와 Linux VPS를 지원한다.

---

# 4. 안전 규칙

- Secret, Token, API Key를 Commit하지 않는다.
- 기본 실행 모드는 `DRY_RUN=true`다.
- main/master에 직접 Commit하거나 Push하지 않는다.
- 항상 Task Branch를 사용한다.
- 검증 실패 상태를 완료로 판단하지 않는다.
- 자동화가 확신할 수 없는 경우 안전한 실패 또는 `devbot:manual-action`으로 전환한다.
- 실패 시 작업물을 삭제하거나 초기화하지 않는다.
- 같은 Task에 두 번째 Issue, Branch, Contract, PR을 만들지 않는다.

---

# 5. Context Loading 순서

새 작업은 다음 순서로 읽는다.

1. `CONSTITUTION.md`
2. `AGENTS.md`
3. 현재 Task Contract
4. 관련 `docs/`
5. 이전 Result와 현재 Result
6. Pull Request Evidence와 CI
7. Source Code

불필요한 전체 문서 스캔보다 현재 Task와 직접 관련된 문서를 우선한다.

---

# 6. Single Task Model

한 Task는 정확히 다음 산출물을 사용한다.

```text
1 Task Issue
1 Task Branch
1 Task Contract
1 Pull Request
```

명명 규칙:

- Branch: `task/<task-number>-<slug>`
- Contract: `tasks/<task-number>-<slug>.md`
- Result: `results/<task-number>-<slug>.md`
- PR title: `Task <task-number>: <title>`

GitHub Issue 번호와 PR 번호는 Task 번호와 독립적이다.
모든 구현, 테스트, Result, 리뷰 반영은 같은 Branch와 PR에서 계속한다.

---

# 7. Workspace Contract

`WorktreeManager.prepare()`가 `PreparedWorkspace`를 반환한 뒤에는 모든 Agent와 실행 단계가 그 workspace만 사용한다.

적용 대상:

- Implementer
- Reviewer
- Reworker
- Validation
- Delivery
- 향후 추가되는 Agent 역할

금지 사항:

- 준비된 workspace가 있는데 host repository를 검증하거나 실행 대상으로 사용하는 것
- Agent가 별도로 Branch/PR을 discovery하는 것
- operator checkout의 현재 Branch나 미커밋 변경에 의존하는 것

필수 원칙:

```python
prepared = worktree_manager.prepare(...)
validate(prepared.repository)
runner.run(prepared.repository, ...)
deliver(prepared.repository, ...)
```

실제 함수 형태는 달라도 검증, 실행, 전달이 같은 `PreparedWorkspace`를 사용해야 한다.

---

# 8. 구현 역할 SOP

Implementer는 다음을 수행한다.

1. 현재 Task Issue, Contract, Branch, PR 연결을 확인한다.
2. 준비된 workspace에서만 작업한다.
3. Task 범위만 구현한다.
4. Contract의 필수 테스트 이름을 변경하지 않는다.
5. 각 Checkpoint를 증명하는 테스트를 작성한다.
6. Happy Path, Failure Path, Boundary Condition을 포함한다.
7. 필요한 Regression Test를 추가한다.
8. Validation Gate를 실행한다.
9. Result와 PR Evidence를 실제 변경 사항에 맞게 갱신한다.
10. 기존 Branch와 PR에 Commit하고 Push한다.

새 Branch, PR, Issue를 생성하지 않는다.

---

# 9. Validation Gate

기본 검증:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Task Contract가 다른 명령을 요구하면 함께 수행한다.
검증 결과는 Result와 PR Evidence에 기록한다.

---

# 10. Result 작성 규칙

모든 Task는 `results/`에 Result를 작성한다.

필수 항목:

- 완료 내용
- 주요 설계 결정
- 수정 파일
- Checkpoint별 구현과 테스트 Evidence
- Validation 결과
- 수동 검증 결과
- 남은 TODO와 제한
- 위험 요소
- Improvement Suggestions

Result는 실제 코드와 PR Evidence와 일치해야 한다.

---

# 11. 리뷰 역할 SOP

Reviewer는 다음 순서로 검토한다.

1. `CONSTITUTION.md`
2. `AGENTS.md`
3. Task Contract
4. Result
5. PR diff와 현재 head SHA
6. PR Evidence
7. CI/check status
8. 관련 운영 문서

검토 항목:

- 계약 범위
- Checkpoint와 필수 테스트
- Happy/Failure/Boundary 테스트 품질
- Workspace Contract 준수
- 상태 전이와 idempotency
- 보안
- Result와 PR Evidence 일치
- CI
- 문서

Reviewer는 코드를 수정하거나 Merge하지 않는다.
Task 계약, Result, PR Evidence, CI, 운영 정책 중 하나라도 만족하지 않으면
코드와 테스트가 통과했더라도 `REQUEST CHANGES`로 판단한다.
이 기준은 모든 리뷰어에게 예외 없이 적용되며, 구현 관여 여부나 Agent 종류에
따라 기준을 완화하거나 강화하지 않는다.

---

# 12. 리뷰 결과

리뷰는 정확히 다음 중 하나로 끝난다.

```text
MERGE READY
```

또는

```text
REQUEST CHANGES
```

Review Summary 필수 항목:

- 계약 검토
- 품질 게이트
- 테스트
- CI
- 보안
- 문서
- Blocker
- Warning
- 비고

하나라도 충족하지 않으면 코드와 테스트가 통과해도 `REQUEST CHANGES`다.
같은 기준을 모든 Reviewer에게 적용한다.

---

# 13. Autonomous Review Loop

폴링 데몬은 상태 라벨과 현재 PR head를 기준으로 자동 릴레이한다.

1. `devbot:ready` → IMPLEMENT
2. 성공한 IMPLEMENT delivery → REVIEW
3. `REQUEST CHANGES` → 같은 Issue/Branch/PR에서 REWORK
4. 성공한 REWORK delivery → 새 head에 대해 REVIEW
5. 현재 head에 대한 유효한 `MERGE READY`와 merge-readiness gate 통과 → PR에 `devbot:ready-to-merge`
6. stale review, 불일치, 반복 한도 초과, 안전하지 않은 상태 → retry, blocked, 또는 manual-action

동일 head와 동일 feedback은 중복 처리하지 않는다.
Review/Rework 반복은 bounded하고 idempotent해야 한다.

자동 Merge와 자동 Issue Close는 현재 범위가 아니다.

---

# 14. Prompt Contract

## 구현

사용자가 다음처럼 요청하면 추가 설명 없이 현재 Task와 연결된 산출물을 찾아 SOP를 수행한다.

```text
Task-XXX 수행해.
```

Task가 실제로 모호할 때만 질문한다.

## 리뷰

다음 요청만으로 충분하다.

```text
Review PR #<number>.
```

Reviewer는 Contract, Result, PR Evidence, CI를 스스로 찾아 읽는다.

## Planner

프로젝트 소유자가 설계를 승인한 뒤 Planner 실행을 요청하면 승인된 내용만 산출물로 변환한다.
별도 Execution Issue를 생성하지 않는다.

---

# 15. 문서 규칙

- 안정 원칙은 `CONSTITUTION.md`에 기록한다.
- Agent 실행 규칙과 SOP는 `AGENTS.md`에 기록한다.
- 상세 설계와 운영 절차는 `docs/`에 기록한다.
- 중요한 설계 결정은 `docs/07-decisions.md`에 기록한다.
- 구현과 관련 문서는 함께 수정한다.
- Task를 수정하면 Result와 PR Evidence도 함께 수정한다.
- 문서 간 중복은 최소화하고 Source of Truth를 명시한다.

---

# 16. 상태 질문 응답 규칙

현재 상태 질문은 GitHub Issue, Pull Request, Label, Comment, Check, Commit을 기준으로 답한다.

- 로컬/VPS 로그는 사용자가 요청했을 때 보조 자료로만 사용한다.
- Timeline marker가 없는 구간은 GitHub timestamp 기반 추정임을 명시한다.
- 가능한 경우 `docs/10-github-status-timeline.md`의 상태 카드 형식을 따른다.

---

# 17. DevBot 철학

- Task는 계약이다.
- Checkpoint는 품질 게이트다.
- Test는 계약을 증명한다.
- Result는 인수인계와 Evidence다.
- Review는 계약 준수를 검증한다.
- 역할은 Agent 제품이나 모델보다 우선한다.
- 자동화는 traceability와 safety를 약화시키면 안 된다.
- 사람은 Planning과 최종 Merge 결정을 소유한다.
