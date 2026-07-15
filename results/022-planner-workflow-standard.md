# Task 022 Result: Planner Workflow Standard

## 완료 내용

Task 022 계약(`tasks/022-planner-workflow-standard.md`)의 CP-022-1부터
CP-022-10까지 전부 구현했다. Planner-owned contract-first
워크플로(1 Task = 1 Branch = 1 Pull Request = 1 실행용 Issue)를 저장소
정책으로 문서화하고, 그 정책을 기계로 검증할 수 있는 순수 헬퍼 모듈
`devbot.planner`(`src/devbot/planner.py`)를 도입했다.

이전 실행(Issue #43 코멘트)은 이 세션이 네트워크 명령(`git fetch`, `gh`
등)에 승인을 요구하는 환경 게이트에서 멈춰 코드 변경 없이 계약서만 커밋된
상태(`d3558c3`)였다. 이번 실행은 그 지점부터 기존 Branch
(`task/022-planner-workflow-standard`)와 PR #42 위에서 이어서
구현했다 - 새 Branch/PR을 만들지 않았다.

- **Planner/Implementer/Reviewer/Operator 역할 문서화 (CP-022-1)**:
  `docs/12-planner-workflow.md` 1절이 네 역할의 책임을 서술하고,
  `devbot.planner.PLANNER_RESPONSIBILITIES` /
  `IMPLEMENTER_RESPONSIBILITIES` / `REVIEWER_RESPONSIBILITIES` /
  `OPERATOR_RESPONSIBILITIES` 튜플이 그 책임 목록을 기계로 검증 가능하게
  만든다. `AGENTS.md`에 신규 16절 "Planner 워크플로 (Task 022)"를
  추가해(기존 절 번호는 바꾸지 않도록 문서 끝에 추가) 역할 경계를
  최상위 운영 규칙에도 반영했다.
- **단일 Task 작업공간 정책 (CP-022-2)**: `docs/12-planner-workflow.md`
  3절이 "1 Task = 1 Branch = 1 Pull Request"를 정의하고,
  `devbot.planner.validate_planner_workspace()`가 같은 Task 번호에 대해
  이미 알려진 작업공간(`KnownWorkspace`)과 새 작업공간을 비교해 검증한다.
- **명명 및 번호 규칙 (CP-022-3)**: `canonical_branch_name()` /
  `canonical_contract_path()` / `canonical_result_path()` /
  `canonical_pr_title()` / `canonical_issue_title()`이 Branch/계약서/
  Result/PR 제목/실행용 Issue 제목의 표준 형식(Task 번호 3자리
  0-padding)을 구현하고, `validate_naming_and_numbering()`이 주어진
  작업공간이 이 표준과 일치하는지 검증한다. GitHub Issue 번호(#43)와 PR
  번호(#42)는 Task 번호(022)와 다른 독립적 식별자임을 문서와 코드
  양쪽에서 명시했다 - Task 022 자신의 실제 계약/PR/Issue가 그 예시다.
- **실행용 Issue 계약 (CP-022-4)**: `render_execution_issue_body()`가
  계약서 경로, Branch, PR 번호, Checkpoint 범위, Validation Gate,
  Result 경로, "다른 Branch/PR을 만들지 말라"는 금지 문구를 모두 포함한
  본문을 생성한다.
- **PR 계약 템플릿 (CP-022-5)**: `render_pr_body()`가 계약서 경로,
  Branch/PR 계속 진행 정책, Scope, 실행용 Issue 링크(`Closes #<번호>`
  포함)를 포함한 PR 본문을 생성한다.
- **최소 리뷰 진입 계약 (CP-022-6)**: `resolve_review_entry()`가
  `"Review PR #<number>."` 형태의 요청에서 PR 번호를 추출하고, Reviewer가
  찾아 읽어야 할 6개 소스(`AGENTS.md`, `docs/09-task-contract-standard.md`
  의 Review Gate 절, 연결된 Task 계약서, Result, PR Evidence, CI/Check
  상태)를 `required_sources`로 반환한다. PR 번호를 찾을 수 없으면
  `ValueError`를 발생시킨다. `AGENTS.md`(12절 Prompt Contract),
  `docs/09-task-contract-standard.md`(Review Gate 표준),
  `docs/12-planner-workflow.md`(2절) 세 곳에 동일한 계약을 문서화했다.
- **중복 작업공간 감지 (CP-022-7)**: `find_duplicate_workspaces()`가 같은
  Task 번호에 대해 다른 Branch나 다른 PR 번호가 이미 등록되어 있으면
  각각 별도 오류로 보고한다. 같은 Branch/PR을 재사용하는 것(계속 작업)은
  중복으로 판정하지 않는다.
- **누락 Evidence 감지 (CP-022-8)**: `validate_workspace_evidence()`가
  계약서 파일 누락(`contract_file_check` - 기본값
  `contract_file_exists()`가 현재 작업 트리에서 `contract_path`가 실제
  파일인지 직접 확인, GitHub 소스 등으로 주입 가능), Checkpoint 없음,
  Validation Gate 없음, Result 경로 없음, 실행용 Issue cross-link 없음,
  PR cross-link 없음을 각각 독립된 오류 메시지로 보고한다. (아래 "PR #42
  REQUEST CHANGES 대응" 절 참고 - 최초 구현은 "missing contract file"을
  helper가 아니라 호출자 책임으로 문서화해 계약(Scope 6절)과
  불일치했다.)
- **기존 워크플로 호환성 (CP-022-9)**: `devbot.planner`는
  `devbot.polling` / `devbot.scheduler` / `devbot.review` /
  `devbot.rework` / `devbot.delivery` / `devbot.timeline` /
  `devbot.issue_state` 어디에서도 import되지 않는다 - Planner 워크플로
  검증은 항상 명시적으로 호출되며 daemon 자동 폴링 루프에 연결되지
  않는다. `TaskState`/`JobType`의 실제 값, `PollingService.run_cycle`,
  `ReviewService`/`has_review_marker_for_head`,
  `ReworkService.process`, `branch_has_implementation_evidence`,
  `IssueStateWriter`가 이 Task로 전혀 바뀌지 않았음을 테스트로
  고정했다.
- **Result와 문서 (CP-022-10)**: 이 문서 + `AGENTS.md` +
  `docs/09-task-contract-standard.md` +
  `docs/12-planner-workflow.md`(신규) + `docs/00-roadmap.md` + PR #42
  본문 갱신.

## 수정 파일

신규:
- `docs/12-planner-workflow.md` - Planner Workflow Standard 본문
  (역할, 최소 리뷰 진입 계약, 단일 Task 작업공간 정책, 명명/번호 규칙,
  실행용 Issue/PR 템플릿, Planner 체크리스트/검증, 기존 워크플로와의
  호환성, 범위 밖)
- `src/devbot/planner.py` - `PlannerWorkspace`, `KnownWorkspace`,
  `PlannerValidationResult`, `canonical_branch_name()`,
  `canonical_contract_path()`, `canonical_result_path()`,
  `canonical_pr_title()`, `canonical_issue_title()`,
  `contract_file_exists()`, `validate_naming_and_numbering()`,
  `validate_workspace_evidence()`, `find_duplicate_workspaces()`,
  `validate_planner_workspace()`, `render_execution_issue_body()`,
  `render_pr_body()`, `resolve_review_entry()`, `ReviewEntryContext`,
  `REQUIRED_REVIEW_SOURCES`, 4개 역할 책임 튜플
- `tests/test_planner.py` - CP-022-1~9 필수 테스트 9개

수정:
- `AGENTS.md` - 버전 1.2.0 -> 1.3.0. 12절(Prompt Contract) 리뷰 역할에
  `Review PR #<number>.` 최소 요청 문구 추가. 문서 끝에 16절 "Planner
  워크플로 (Task 022)" 신규 추가(기존 절 번호 변경 없음).
- `docs/09-task-contract-standard.md` - 버전 1.1.0 -> 1.2.0.
  `docs/12-planner-workflow.md`와의 관계를 서두에 명시. "단일 Task
  추적 정책"에 GitHub Issue/PR 번호와 Task 번호의 독립성을 명시.
  "Review Gate 표준"에 최소 리뷰 진입 계약 문단 추가.
- `docs/00-roadmap.md` - Task 021(기존에 누락되어 있던 항목, 이번에
  정합성을 위해 함께 채움)과 Task 022 항목 추가.

## PR #42 REQUEST CHANGES 대응 (CP-022-8)

자동 리뷰(`hjlee83`, head `7def271`)가 `REQUEST CHANGES`를 반환했다.
지적 사항: Task 022 계약 Scope 6절("Planner checklist and validation")은
validation helper가 최소 `missing contract file`을 감지해야 한다고
명시하는데, 최초 구현의 `validate_workspace_evidence()`는 Checkpoint/
Validation Gate/Result 경로/Issue·PR cross-link만 검사했고 계약서 파일
자체의 존재 여부는 검사하지 않았다. `docs/12-planner-workflow.md`도
"계약서 파일 누락 여부는 호출자가 확인한다"고 적어 helper 책임 범위
밖으로 명시적으로 밀어냈는데, 이는 계약과 정면으로 불일치했다 - 리뷰
지적이 정확했다.

수정 내용:
- `src/devbot/planner.py`에 `contract_file_exists(contract_path: str) ->
  bool`(기본: `Path(contract_path).is_file()`)을 추가하고,
  `validate_workspace_evidence()`/`validate_planner_workspace()`에
  `contract_file_check: Callable[[str], bool] = contract_file_exists`
  키워드 인자로 주입했다(Task 021의 `branch_has_implementation_evidence`
  주입 패턴과 동일한 설계). 기본 동작은 현재 작업 트리에서 실제 파일
  존재를 확인하고, 없으면 `"missing contract file: '<path>'"` 오류를
  추가한다. 주입 가능하게 만든 이유는 GitHub에서 읽은 계약서 존재 여부
  등 로컬 파일시스템이 아닌 소스로도 검증할 수 있게 하기 위함이다.
- `tests/test_planner.py::test_planner_contract_missing_evidence`에
  `contract_file_exists()` 자체의 happy/failure path, 존재하지 않는
  계약서 경로에 대한 `validate_workspace_evidence()`/
  `validate_planner_workspace()` 실패 경로, `contract_file_check` 주입
  boundary(강제 True/강제 False) 케이스를 추가했다. 테스트 **이름**은
  그대로 유지했다 - 계약의 필수 테스트 이름 목록에는 없는 세부
  검증이므로 AGENTS.md "필수 테스트 이름은 변경하지 않는다" 규칙과
  충돌하지 않는다.
- `docs/12-planner-workflow.md` 7절에서 "호출자가 확인" 문구를 제거하고,
  helper가 기본적으로 로컬 파일시스템을 직접 확인하며 필요 시 주입할 수
  있다고 정정했다.

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-022-1 Planner 역할 문서화 | `test_planner_role_contract` |
| CP-022-2 단일 Task 작업공간 정책 | `test_single_task_workspace_policy` |
| CP-022-3 명명 및 번호 규칙 | `test_planner_naming_and_numbering_policy` |
| CP-022-4 실행용 Issue 템플릿 | `test_execution_issue_contract_template` |
| CP-022-5 PR 계약 템플릿 | `test_planner_pr_contract_template` |
| CP-022-6 최소 리뷰 진입 계약 | `test_minimal_review_entry_contract` |
| CP-022-7 중복 작업공간 방지 | `test_duplicate_task_workspace_rejected` |
| CP-022-8 누락 Evidence 감지(계약서 파일 누락 포함) | `test_planner_contract_missing_evidence` |
| CP-022-9 기존 워크플로 호환성 | `test_existing_workflows_compatible_with_planner_standard` |
| CP-022-10 Result/문서 | 이 문서 + PR #42 본문 |

## 검증 결과

```
uv sync
  Resolved 15 packages in 29ms
  Checked 14 packages in 5ms

uv run ruff check .
  All checks passed!

uv run pytest
  342 passed (신규 assertion 포함 tests/test_planner.py 9개 - CP-022-8
  테스트에 계약서 파일 누락 실패 경로 추가, 테스트 개수 자체는 동일 -
  기존 333개 전부 회귀 없음)

uv run devbot --dry-run doctor
  safe_to_start: yes
  (workspace_cleanliness/current_branch_compatibility는 이 Task 자신의
  미커밋 변경 + task/022-* 작업 Branch가 원인인 FAIL이며, Task 019
  설계대로 daemon 시작을 막지 않는 정보성 체크다)

uv run devbot --once --dry-run
  (실제 hjlee83/devbot 배포 설정 · 실제 GitHub 인증/조회로 실행, 종료 코드 0)
  DevBot 시작: version=0.1.0 implementer=claude reviewer=codex dry_run=True ...
  Queue Summary: review=1 (Issue #43 자신 - REQUEST CHANGES 리뷰 이후
    devbot:review 라벨로 전환된 실제 상태와 일치)
  cycle 종료: 결과=no_ready_task (선택 0/1, 직전 head는 이미 자동 리뷰됨)
  1회 실행 완료: skipped_active_task
```

### 수동 검증 (계약 Validation Gate "Manual verification" 항목)

- **샘플 Planner 작업공간 검증**: Task 022 자신을 나타내는
  `PlannerWorkspace`(task_number=22, branch=`task/022-planner-workflow-standard`,
  contract_path=`tasks/022-planner-workflow-standard.md`,
  result_path=`results/022-planner-workflow-standard.md`,
  checkpoints=CP-022-1..10, issue_number=43, pr_number=42)를
  `validate_planner_workspace()`에 통과시켜 `is_valid=True, errors=()`를
  확인했다 - Issue #43/PR #42 번호가 Task 번호 022와 달라도 정상
  통과한다(독립 식별자 정책).
- **중복 Branch/PR 감지**: 위 샘플이 이미 `known_workspaces`에 등록된
  상태에서 같은 Task 번호에 대해 다른 Branch(`-v2`)와 다른 PR
  번호(#100)를 가진 두 번째 작업공간을 검증하면 `is_valid=False`와 함께
  "duplicate workspace"/"duplicate Pull Request" 오류 2건이 각각
  보고됨을 확인했다.
- **누락 cross-link 감지**: `issue_number`/`pr_number`/`checkpoints`/
  `validation_gate`/`result_path`를 모두 비운 작업공간을 검증하면
  "missing Checkpoints", "missing Validation Gate", "missing Result
  path", "missing execution Issue cross-link", "missing Pull Request
  cross-link" 5개 오류가 각각 독립적으로 보고됨을 확인했다.
- **계약서 파일 누락 감지 (REQUEST CHANGES 반영)**: 실제 존재하는
  `tasks/022-planner-workflow-standard.md`는
  `contract_file_exists()`가 `True`를 반환하고, 존재하지 않는 경로
  (`tasks/999-nonexistent.md`)는 `False`를 반환함을 확인했다. 존재하지
  않는 계약서 경로를 가진 작업공간을 `validate_planner_workspace()`에
  통과시키면 `is_valid=False`와 `"missing contract file: '...'"` 오류가
  보고됨을 확인했다.
- **`Review PR #<number>.` 해석**: `resolve_review_entry("Review PR
  #42.")`가 `pr_number=42`와 함께 `AGENTS.md`,
  `docs/09-task-contract-standard.md`(Review Gate), 연결된 Task 계약서,
  Result, PR Evidence, CI/Check 상태 6개 `required_sources`를 반환함을
  확인했다. PR 번호가 없는 요청(`"Please take a look at this"`)은
  `ValueError`를 발생시킴을 확인했다(실패 경로).

## TODO

없음 (이 Task 범위 내). Planner 모델의 자동 실행, 자동 우선순위 결정,
Worktree 격리, Timeline 자동 기록, Dashboard/UI, Notifications, 다중
Agent Failover, 자동 Merge는 계약의 명시적 제외 범위이며 후속 Task로
남긴다.

## 위험 요소

- `devbot.planner`는 GitHub API를 직접 호출하지 않는 순수 헬퍼다(계약서
  파일 존재 확인만 로컬 파일시스템을 직접 읽는다) - 호출자(Planner
  역할을 맡은 사람이나 Agent)가 실제 GitHub의 Branch/PR 목록을
  `KnownWorkspace`로 변환해 넘겨야 중복 감지가 동작한다. 이 변환 자체를
  자동화하는 것(예: `gh api`로 Branch/PR 목록을 조회해 `KnownWorkspace`
  리스트를 만드는 CLI)은 이번 Task의 "Planner 모델 자동 실행"/
  "Dashboard/UI" 제외 범위와 맞닿아 있어 의도적으로 범위 밖에 남겼다.
  후속 Task에서 다룰 수 있다.
- `contract_file_exists()`의 기본 구현은 현재 작업 디렉터리 기준
  상대경로(`Path(contract_path).is_file()`)로 계약서를 찾는다. Planner
  helper를 저장소 루트가 아닌 다른 작업 디렉터리에서 호출하면 실제
  계약서가 있어도 `missing contract file`로 오탐할 수 있다 - 호출자가
  저장소 루트에서 호출하거나 `contract_file_check`를 주입해야 한다.
- `render_execution_issue_body()` / `render_pr_body()`는 정적 문자열
  템플릿이다. `docs/09-task-contract-standard.md`의 "PR Evidence 필수
  항목"(Checkpoint별 대응 테스트, 실행한 검증 명령과 결과 등)까지 자동
  생성하지는 않는다 - 이는 Implementer가 구현을 마친 뒤에만 채울 수 있는
  내용이라 Planner 단계의 템플릿에는 자리표시자로도 넣지 않았다(계약서
  표준의 "TODO/TBD 금지" 원칙과 충돌하지 않도록).
- `validate_naming_and_numbering()`의 slug 정규식(`^[a-z0-9]+(-[a-z0-9]+)*$`)
  은 소문자 kebab-case만 허용한다. 기존 저장소의 모든 Task 번호(001~021)
  slug는 이 패턴을 만족하지만, 향후 숫자로 시작하는 단어가 포함된 제목
  (예: "3d-something")도 이 정규식을 통과한다 - 의도된 범위이며 별도
  위험은 없다.
- 공유 운영 배포(`hjlee83/devbot`, 이 Task 자신이 진행 중인 저장소)를
  대상으로 한 진짜 non-dry-run `uv run devbot --once`는 실행하지
  않았다 - Task 019/021 Result 문서와 동일한 판단 기준으로, 이 검증
  실행이 PR #42/Issue #43의 실제 상태를 예기치 않게 바꿀 위험을
  피했다. `--dry-run` 실행이 실제 GitHub 인증/조회 경로가 정상 동작함을
  이미 확인했다.

## Improvement Suggestions

- `devbot.planner`가 `gh`/`GitHubClient`를 이용해 저장소의 실제
  `task/*` Branch와 열린 PR 목록을 조회해 `KnownWorkspace` 리스트를
  자동 구성하는 얇은 CLI(`devbot planner validate <task-number>`)를
  추가하면, 지금은 호출자가 수동으로 채워야 하는 `known_workspaces`
  인자를 자동화할 수 있다.
- `render_execution_issue_body()` / `render_pr_body()`의 출력을 실제
  `gh issue create` / `gh pr create` 호출과 연결하면 Planner의 6~7단계
  (계약서 완성도 검증 이후 PR/Issue 생성)까지 절반 자동화할 수 있다 -
  단, 이 Task의 "Planner 모델 자동 실행" 제외 범위를 벗어나므로 후속
  Task로 남긴다.
- `resolve_review_entry()`가 지금은 PR 번호만 추출한다. 저장소 이름
  (`owner/repo`)까지 요청 문자열에서 파싱해 여러 저장소를 동시에 관리할
  때도 `Review hjlee83/devbot#42.`처럼 명시할 수 있게 하면 다중 저장소
  운영에 더 안전해진다.
