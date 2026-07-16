# Planner Workflow Standard

Version: 1.0.0
Last Updated: 2026-07-15

이 문서는 Task 022의 산출물이며, `docs/09-task-contract-standard.md`(Task
계약서 발행 표준)를 대체하지 않고 그 위에 Planner/Implementer/Reviewer/
Operator 네 역할의 책임 경계와, 그 경계를 기계로 검증하는
`devbot.planner` 모듈(`src/devbot/planner.py`)을 정의한다.

이 워크플로는 최근 Task들(특히 Task 015 Review Gate, Task 019 Reliability,
Task 021 Agent Outcome Classification)이 반복적으로 증명한 다음 순서를
저장소 정책으로 고정한다.

1. Planner가 범위, Checkpoint, Validation Gate를 정의한다.
2. Planner가 Task Issue, Branch, 계약서 파일, Pull Request를 생성한다.
3. Implementer는 기존 Branch와 Pull Request 위에서 계속 구현한다.
4. Reviewer는 저장소 정책과 Task 계약서를 기준으로 구현을 평가한다.
5. Operator가 최종 Merge를 수행한다.

---

## 1. 역할과 책임

### 1.1 Planner

Planner는 다음을 소유한다 (`devbot.planner.PLANNER_RESPONSIBILITIES`).

- Task 번호와 제목
- 범위(In scope)와 제외 범위(Out of scope)
- Checkpoint와 필수 테스트 이름
- Validation Gate
- Branch 생성
- 계약서 파일 생성
- Pull Request 생성
- Task Issue 생성
- Branch, PR, Issue, 계약서 간의 명시적 상호 링크(cross-link)

Planner는 계약서가 `docs/09-task-contract-standard.md`의 "계약서 완성도
검증" 항목을 모두 만족한 뒤에만 Task Issue와 PR을 생성한다.

### 1.2 Implementer

Implementer는 다음을 수행한다
(`devbot.planner.IMPLEMENTER_RESPONSIBILITIES`).

- 기존 Branch와 기존 Pull Request 위에서만 계속 구현한다 - 새 Branch나
  새 PR을 만들지 않는다.
- Task 범위만 구현한다.
- Task에 정의된 필수 테스트 이름을 변경하지 않고, 각 Checkpoint를
  증명하는 테스트를 작성한다.
- Validation Gate를 모두 통과시킨다.
- Result 문서를 작성한다.

daemon이 자동으로 IMPLEMENT/REWORK Job을 실행할 때는, 기존 Branch/PR을
스스로 `git fetch`/`gh`로 찾는 대신 DevBot host가 미리 준비한 격리
worktree 위에서 계속 구현한다(Task 023, `docs/13-host-managed-workspace-preparation.md`) -
"기존 Branch와 PR 위에서만 계속 구현한다"는 원칙 자체는 바뀌지 않고,
그 Branch/PR을 누가(사람 Agent가 스스로, 혹은 host가 미리) 찾아 두는지만
바뀐다.

### 1.3 Reviewer

Reviewer는 다음을 수행한다 (`devbot.planner.REVIEWER_RESPONSIBILITIES`).

- 최소 리뷰 요청(`Review PR #<number>.`)만으로 저장소 Review Gate,
  연결된 Task 계약서, Result, PR Evidence, CI를 스스로 찾아 읽는다
  (2절 "최소 리뷰 진입 계약" 참조).
- Task 계약서와 저장소 Review Gate 기준으로 구현을 평가한다.
- `MERGE READY` 또는 `REQUEST CHANGES`만 반환한다.
- 코드를 수정하거나 Merge하지 않는다.

### 1.4 Operator

Operator는 다음을 수행한다 (`devbot.planner.OPERATOR_RESPONSIBILITIES`).

- 최종 Merge를 수행한다.
- `devbot:manual-action` 등 자동화가 처리하지 못하는 상태를 사람이
  해결한다.

---

## 2. 최소 리뷰 진입 계약 (Minimal Review Entry Contract)

`Review PR #<number>.`처럼 짧은 리뷰 요청만으로 충분하다. 저장소
컨텍스트(이 저장소를 Clone/Checkout한 상태)가 있으면 Reviewer는 추가
설명 없이 아래를 스스로 찾아 읽어야 한다.

1. `AGENTS.md` (8절 리뷰 역할 SOP, 9절 리뷰 기준, 10절 리뷰 결과 형식)
2. 저장소 Review Gate 문서 - `docs/09-task-contract-standard.md`의
   "Review Gate 표준" 절
3. PR이 연결된 Task 계약서 (`tasks/<번호>-<slug>.md`)
4. Result 문서 (`results/<번호>-<slug>.md`)
5. PR Evidence (PR 본문 또는 최신 댓글의 Checkpoint-테스트 대응표, 검증
   명령 결과)
6. CI/Check 상태

`devbot.planner.resolve_review_entry(request: str) -> ReviewEntryContext`가
이 계약을 기계로 검증할 수 있게 한다: `Review PR #<number>.` 형태의
문자열에서 PR 번호를 추출하고, 위 6개 항목을 `required_sources`로
반환한다. PR 번호를 추출할 수 없는 요청은 `ValueError`를 발생시킨다 -
Reviewer는 그 경우 사람에게 PR 번호를 되물어야 한다.

Reviewer는 이 6개 항목 중 하나라도 실제 변경 사항과 불일치하면
`AGENTS.md` 9절/`docs/09-task-contract-standard.md` "Review Gate 표준"에
따라 코드와 테스트가 통과했더라도 `REQUEST CHANGES`로 판단한다.

---

## 3. 단일 Task 작업공간 정책 (Single Task Workspace Policy)

하나의 Task는 하나의 Task Issue, 하나의 Branch, 하나의 계약서, 하나의
Pull Request로 추적한다 - **1 Task = 1 Issue = 1 Branch = 1 Contract = 1 Pull Request**.

- 계약서 작성, 구현, 테스트, Result 작성, 리뷰 반영(rework)은 모두 같은
  Branch와 PR 위에서 계속된다.
- Implementer는 Planner가 이미 만든 작업공간이 있는 Task에 대해 두 번째
  구현 Branch나 두 번째 PR을 만들지 않는다.
- 별도 Execution Issue는 만들지 않는다. Task Issue가 계획 승인, 실행,
  Timeline, 상태 라벨을 모두 추적한다.
- `devbot.planner.validate_planner_workspace()`가 이미 알려진
  작업공간(`KnownWorkspace`) 목록과 새 작업공간을 비교해 같은 Task
  번호에 서로 다른 Branch나 PR 번호가 등록되면 오류를 반환한다
  (4절 "중복 작업공간 감지" 참조).

---

## 4. 명명 및 번호 규칙 (Naming and Numbering Policy)

Task 번호는 3자리 0-padding으로 표기한다 (`022`).

| 대상 | 형식 | 예 |
|---|---|---|
| Branch | `task/<task-number>-<slug>` | `task/022-planner-workflow-standard` |
| 계약서 | `tasks/<task-number>-<slug>.md` | `tasks/022-planner-workflow-standard.md` |
| Result | `results/<task-number>-<slug>.md` | `results/022-planner-workflow-standard.md` |
| PR 제목 | `Task <task-number>: <title>` | `Task 022: Planner Workflow Standard` |
| Task Issue 제목 | `Task <task-number>: <title>` | `Task 022: Planner Workflow Standard` |

**GitHub Issue 번호와 PR 번호는 Task 번호와 독립적인 식별자다.** 예를
들어 Task 022의 Task Issue는 `#43`, PR은 `#42`로 Task 번호 `022`와
다르다 - 이것은 정상이다. 세 식별자를 일치시키려 하지 말고, 대신 서로를
명시적으로 cross-link한다:

- 계약서와 PR 본문에 Task Issue 번호를 명시한다.
- Task Issue 본문에 계약서 경로, Branch 이름, PR 번호를 명시한다.
- Result 문서에 Task 번호, Branch, PR 번호, Issue 번호를 모두 명시한다.

`devbot.planner.canonical_branch_name()` /
`canonical_contract_path()` / `canonical_result_path()` /
`canonical_pr_title()` / `canonical_issue_title()`이 이 표를 구현하고,
`validate_naming_and_numbering()`이 주어진 작업공간이 이 표와
일치하는지 검증한다 (불일치 시 "Task 번호 또는 slug 불일치"로 보고).

---

## 5. Task Issue 계약 (Task Issue Contract)

Task Issue 본문은 최소 다음을 포함해야 한다
(`devbot.planner.render_task_issue_body()`가 이 형식을 생성한다).

- 계약서 경로
- Branch 이름
- PR 번호
- 필수 Checkpoint 범위 (예: `CP-022-1 through CP-022-10`)
- Validation Gate 요구사항
- Result 경로
- "다른 Branch나 PR을 만들지 말라"는 명시적 금지 문구

## 6. PR 계약 템플릿 (PR Contract Template)

Planner가 생성하는 PR Evidence는 최소 다음을 포함해야 한다
(`devbot.planner.render_pr_body()`가 이 형식을 생성한다).

- 계약서 경로
- Branch/PR 정책("이 Branch와 PR 위에서 계속 진행") 문구
- 범위(Scope) 요약
- Task Issue 링크(`Closes #<issue-number>` 포함)

---

## 7. Planner 체크리스트와 검증 (Validation)

`devbot.planner.validate_planner_workspace(workspace, *,
known_workspaces=(), contract_file_check=contract_file_exists)`가 아래를
한 번에 검증해 `PlannerValidationResult`
(`errors: tuple[str, ...]`, `is_valid: bool`)를 반환한다.

- 계약서 파일 누락 (`validate_workspace_evidence`) - 기본값
  `contract_file_exists()`가 현재 작업 트리에서 `contract_path`가 실제
  파일인지 직접 확인한다. `contract_file_check`를 주입하면 GitHub에서
  읽은 계약서 존재 여부 등 로컬 파일시스템이 아닌 다른 소스로도 검증할
  수 있다.
- Branch/PR/Issue cross-link 누락 (`validate_workspace_evidence`)
- 같은 Task 번호에 대한 중복 Branch 또는 PR (`find_duplicate_workspaces`)
- Validation Gate 누락 (`validate_workspace_evidence`)
- Checkpoint 누락 (`validate_workspace_evidence`)
- Task 번호 또는 slug 불일치 (`validate_naming_and_numbering`)

이 모듈은 순수 함수/데이터클래스로만 구성되며 GitHub API를 직접
호출하지 않는다 - 계약서 파일 존재 확인만 로컬 파일시스템을 직접 읽고,
그 외(Branch/PR 목록 등)는 호출자(Planner 역할을 수행하는 사람 또는
Agent)가 GitHub에서 읽어 `KnownWorkspace` 목록으로 변환해 전달한다.

---

## 8. 기존 워크플로와의 호환성

`devbot.planner`는 daemon 자동 폴링 루프(`devbot.polling`,
`devbot.scheduler`, `devbot.main`)에서 import되지 않는다. Planner
워크플로 검증은 명시적으로(Planner 역할을 맡은 사람이나 Agent가 직접)
호출하는 것이며, 자동 daemon 사이클이 트리거하지 않는다 - 이 Task의
제외 범위("automatic Planner model execution")를 지키기 위함이다.
daemon 구현, 리뷰, rework, delivery, timeline, 상태 머신, 재시도 동작은
이 Task로 전혀 변경되지 않는다.

---

## 9. 범위 밖 (Out of Scope)

- Planner 모델의 자동 실행
- 자동 우선순위 결정
- Worktree 격리
- Timeline 자동 기록
- Dashboard/UI
- 알림(Notifications)
- 다중 Agent Failover
- 자동 Merge

이 항목들은 후속 Task로 남는다.
