# Task 040 Result: Goal Executor

## 완료 내용

- `devbot goal execute "<goal>" [--task <order>] [--confirm] [--dry-run]` CLI를
  추가했다 (`src/devbot/main.py`의 `_build_goal_parser`(execute 서브파서 추가)/
  `_run_goal_execute_command`, 기존 명령들과 동일하게 daemon `ProcessLock` 없이
  실행된다).
- `src/devbot/goal_executor.py`를 새로 만들어 Task 038의 `fetch_goal_plan`을
  **정확히 한 번만** 호출하고 그 결과를 그대로 사용한다 - 계획을 다시 계산하거나
  재해석하지 않는다.
- `build_execution_plan()`(순수 함수, 이미 조회된 상태를 받아 계산, 네트워크 호출
  없이 단위 테스트 가능)과 `execute_goal()`(GitHub 조회/파일시스템 읽기/조건부
  쓰기 오케스트레이션)로 분리했다 - Task 037의 `release_ops.py` 구조를 그대로
  따랐다.
- 실행 가능 조건: `plan.decision`이 `single_task`(--task 생략 시 Task 1 자동
  선택) 또는 `multi_task`(명시적이고 유효한 `--task <order>` 필수)일 때만
  진행한다. 나머지(`already_completed`/`duplicate_open_work`/`ambiguous`)는
  즉시 거부한다.
- `multi_task` 계획은 `order` 순서를 강제한다: 아직 구체화되지 않은 첫 번째
  Task만 선택 가능하고, 더 이후 순서를 요청하면 "unmet dependencies"로 거부한다.
  "구체화됨"은 정확한 canonical 제목(`Task NNN: <planned Task title>`)을 가진
  Issue(열림/닫힘 무관)가 존재하는지로 판정한다.
- `--confirm` 없이는(--task/--dry-run 조합과 무관하게) 절대 아무것도 쓰지
  않는다. `--dry-run`이 있으면 `--confirm`과 무관하게 항상 읽기 전용이다(공유
  dest, Task 037 `release publish --dry-run`과 동일한 관례).
- `--confirm`으로 실행 가능하면 정확히 하나의 GitHub Issue, 최신 CI 검증된
  `origin/main`에서 분기한 Task Branch 하나, 초안 Task Contract 파일 하나를
  생성한다. PR은 생성하지 않는다.
- 멱등성: Issue는 정확한 제목으로, Branch는 ref 존재 여부로, Contract는 해당
  Branch 위 경로의 내용 존재 여부로 각각 독립적으로 확인 후 없는 것만
  생성한다 - 부분 실패 후 재시도해도 중복 생성하지 않는다.
- 방어 장치: 방금 새 Issue를 만들었는데 Branch가 (매칭되는 Issue 없이) 이미
  존재하면 - 이름 충돌 가능성으로 판단해 명확한 오류로 거부하고, Branch/
  Contract를 만들지 않는다.
- `devbot.agents`를 전혀 import하지 않는다(AST로 확인하는 전용 테스트로 고정) -
  구현 Agent를 호출하지 않는다.
- `GitHubWriteClient`에 새로 추가한 메서드는 `create_issue`/`create_branch`/
  `create_file` 세 개뿐이다 - PR 생성이나 머지 관련 메서드는 호출하지 않는다.

## 주요 설계 결정

- **Task 038의 계획 결과를 그대로 신뢰하고 재해석하지 않는다.** `execute_goal()`은
  `fetch_goal_plan()`을 호출 한 번으로 끝내고, 그 `GoalPlan`/`PlannedTask`
  객체를 그대로 사용한다. 새로운 판단 로직(예: 카탈로그 재매칭)을 추가하지
  않았다.
- **"구체화 완료"는 Task Issue 존재로만 판정한다(내용 검증 없음).** 실제 구현
  완료나 머지 여부가 아니라, `devbot goal execute`가 이전에 성공적으로 Issue를
  만들었는지만 확인한다 - 이 Task의 책임 범위(Issue+Branch+Contract 생성까지)와
  정확히 일치한다. 알려진 한계로 아래에 기록했다.
- **Contract 파일은 GitHub Contents API로 직접 커밋한다** (로컬 git checkout을
  조작하지 않는다). `PUT /repos/{owner}/{repo}/contents/{path}`로 지정한
  Branch에 단일 커밋을 만든다 - 로컬 git 상태(dirty/main 동기화)는 오직 "다음
  Task 번호를 결정하기 위해 로컬 `tasks/` 디렉터리를 신뢰할 수 있는가"라는
  안전 조건 확인에만 쓰인다.
- **Issue 본문은 Task 038의 `render_task_issue_body`를 그대로 재사용하지
  않고, 이 Task 전용 렌더러를 새로 만들었다.** 기존 템플릿은 이미 존재하는
  PR 번호(`Pull Request: #{pr_number}`)를 가정하는데, 이 Task는 PR을 만들지
  않으므로 그대로 쓰면 "Pull Request: #None"처럼 깨진 출력이 나온다. 대신
  Branch/naming 헬퍼(`canonical_branch_name`/`canonical_contract_path`/
  `canonical_result_path`/`canonical_issue_title`)는 그대로 재사용했다
  ("가능한 곳에서는 기존 Planner 관례를 재사용" 요구사항을 정확히 만족하는
  선에서).
- **생성되는 Contract는 명시적으로 DRAFT다.** Quality Gates/필수 테스트 이름/
  Validation Gate가 없고, `devbot:ready` 라벨을 붙이지 않는다 - Task Contract
  Standard(`docs/09-task-contract-standard.md`)가 요구하는 "완성된 계약서"가
  아니라 사람 Planner가 이어서 채워야 하는 시작점임을 Contract 본문 자체에
  "## Status" 섹션으로 명시했다.
- **"최신 검증된 origin/main"은 Task 039의 `target_commit_is_ci_validated`를
  재사용한다** (private `_target_commit_is_ci_validated`를 public으로 이름
  변경, 기존 `release_ops.py` 동작은 변경 없음 - 기존 Task 039 테스트가 그대로
  통과함으로 확인). 두 번째 CI 검증 메커니즘을 새로 만들지 않았다.
- **"local main이 origin/main과 일치"는 dirty checkout과 별개의 새 안전
  조건이다** (`local_main_matches_origin`). 로컬 `tasks/` 디렉터리로 다음 Task
  번호를 계산하기 전에, 그 디렉터리 내용이 실제로 GitHub의 `main`과 같다는
  것을 보장하기 위함이다.

## 수정 파일

- `src/devbot/goal_executor.py` (신규)
- `src/devbot/github_client.py` (`get_branch_ref`, `get_file_content`)
- `src/devbot/github_write_client.py` (`create_issue`, `create_branch`,
  `create_file`, `IssueInfo`)
- `src/devbot/release_ops.py` (`target_commit_is_ci_validated` public화)
- `src/devbot/main.py`
- `tests/test_goal_executor.py` (신규)
- `tests/test_main.py`
- `tests/test_release_ops.py` (rename 반영)
- `tests/test_github_client.py`, `tests/test_github_write_client.py` (allowlist)
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `tasks/040-goal-executor.md`
- `results/040-goal-executor.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-040-1 단일 Task dry-run/confirm 실행 | `test_single_task_ready_when_no_blockers`, `test_execute_goal_single_task_dry_run_never_writes`, `test_execute_goal_single_task_confirmed_execution`, `test_materialize_creates_all_three_when_none_exist` |
| CP-040-2 multi-task는 명시적이고 유효한 --task 필요 | `test_multi_task_requires_explicit_task_flag`, `test_multi_task_invalid_order_is_blocked`, `test_multi_task_first_task_is_selectable`, `test_execute_goal_multi_task_without_task_flag_refuses`, `test_execute_goal_multi_task_valid_first_selection_executes`, `test_execute_goal_invalid_task_order_refuses` |
| CP-040-3 multi-task 의존성 순서 | `test_multi_task_later_task_blocked_by_unmet_dependency`, `test_multi_task_later_task_selectable_once_earlier_materialized`, `test_execute_goal_later_task_blocked_by_unmet_dependency` |
| CP-040-4 실행 불가 decision은 깔끔히 거부 | `test_already_completed_decision_blocks_execution`, `test_duplicate_open_work_decision_blocks_execution`, `test_ambiguous_decision_blocks_execution`, `test_execute_goal_already_completed_refuses`, `test_execute_goal_duplicate_open_work_refuses`, `test_execute_goal_ambiguous_refuses` |
| CP-040-5 로컬 checkout 안전 조건 | `test_dirty_checkout_blocks_execution`, `test_local_main_not_synced_blocks_execution`, `test_local_main_matches_origin_true_when_synced`, `test_local_main_matches_origin_false_when_local_ahead`, `test_local_main_matches_origin_false_on_different_branch`, `test_local_main_matches_origin_none_for_non_git_path`, `test_execute_goal_dirty_checkout_refuses`, `test_execute_goal_local_main_diverged_refuses` |
| CP-040-6 확인 누락 시 절대 쓰지 않음 | `test_execute_goal_without_confirm_never_writes`, `test_goal_execute_dry_run_never_constructs_write_client`, `test_goal_execute_dry_run_flag_overrides_confirm` |
| CP-040-7 멱등성과 부분 실패 재시도 | `test_already_materialized_selected_task_is_ready_and_flagged`, `test_find_materialized_task_matches_exact_title`, `test_find_materialized_task_returns_none_when_no_match`, `test_materialize_reuses_existing_issue_and_branch_only_creates_contract`, `test_materialize_is_fully_idempotent_when_everything_exists`, `test_materialize_already_materialized_creates_nothing`, `test_materialize_refuses_branch_collision_after_creating_new_issue`, `test_execute_goal_existing_matching_issue_is_reused`, `test_execute_goal_existing_matching_branch_only_completes_contract` |
| CP-040-8 API 오류·비일관 상태에서 fail closed | `test_execute_goal_github_api_failure_fails_closed`, `test_materialize_refuses_when_plan_not_ready`, `test_next_task_number_scans_tasks_dir`, `test_next_task_number_defaults_to_one_when_dir_missing` |
| CP-040-9 PR 없음/Agent 호출 없음/daemon lock 없음 | `test_execute_goal_never_creates_a_pull_request_or_invokes_an_agent`, `test_goal_executor_module_never_imports_agents`, `test_goal_execute_does_not_acquire_daemon_lock`, `test_goal_execute_blocked_returns_failure_exit_code`, `test_goal_execute_confirm_constructs_write_client_and_calls_execute` |
| CP-040-10 문서와 근거 | 본 Result, `docs/00-roadmap.md`, `docs/08-beta-runbook.md` |
| CP-040-11 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 697 passed
  (기존 648개 + 이번 Task에서 추가한 49개: `tests/test_goal_executor.py` 44개,
  `tests/test_main.py` 5개)

## 수동 검증 결과 (Live dry-run, 읽기 전용)

`hjlee83/devbot` 실제 저장소를 대상으로 `devbot goal execute --dry-run`을
실행했다(아무것도 쓰지 않음). 실제 `--confirm` 실행은 하지 않았다.

- **single-task**: `uv run devbot goal execute "Add a global PATH launcher." --dry-run`
  → `decision: single_task`, `selected_task: [1] Global PATH Launcher`,
  `next_task_number: 41`, `proposed_branch: task/041-global-path-launcher`,
  `ready: no`(작업 중인 checkout 자체가 dirty하고 main과 다른 브랜치이므로
  예상된 결과). 종료 코드 1.
- **multi-task (Self Update, `--task` 생략)**:
  `uv run devbot goal execute "Implement Self Update." --dry-run` →
  `decision: multi_task`, blocker에 `multi_task plans require an explicit
  --task <order>` 포함. 종료 코드 1.
- **multi-task (Self Update, `--task 1`)**:
  `uv run devbot goal execute "Implement Self Update." --task 1 --dry-run` →
  `selected_task: [1] Self-Update Discovery`,
  `proposed_branch: task/041-self-update-discovery`.
- **already completed**:
  `uv run devbot goal execute "Publish the next stable release." --dry-run` →
  `decision: already_completed`, blocker에 `goal decision is
  'already_completed', not executable` 포함. 종료 코드 1.
- **ambiguous**: `uv run devbot goal execute "Make things better." --dry-run`
  → `decision: ambiguous`, blocker에 `goal decision is 'ambiguous', not
  executable` 포함. 종료 코드 1.

네 경우 모두 실제 GitHub 상태(로드맵/열린 Issue-PR/catalog)에 근거해 정확히
판정했고, `--confirm` 실행이 아니었으므로 아무것도 생성되지 않았다.

## 남은 TODO와 제한

- **멱등성은 존재 여부만 확인하고 내용을 검증하지 않는다.** 운영자가 매칭되는
  Issue/Branch/Contract를 수동으로 무관한 내용으로 바꿔치기하면, 이 Task는
  "이미 완료됨"으로 오판할 수 있다. 부분적으로는 Branch 충돌 방어
  장치(Functional Requirement 8)가 완화하지만, 완전한 내용 검증은 범위 밖으로
  남겨뒀다.
- **`devbot goal execute --confirm`의 실제(non-dry-run) 실행은 이 Task
  구현/검증 과정에서 한 번도 실행하지 않았다** - 계약에서 명시적으로
  금지했다. 실제 Issue/Branch/Contract 생성 경로는 mock을 사용한 단위
  테스트(`test_goal_executor.py`)로만 검증했다.
- **생성된 초안 Contract는 사람이 이어서 완성해야 한다** - Quality Gates, 필수
  테스트 이름, Validation Gate가 없으므로 `devbot:ready`를 붙이기 전에 반드시
  사람 Planner가 검토/보완해야 한다(Contract 본문에 명시).
- **계획 실행은 Task 038의 catalog에 의존한다** - catalog가 오래되면(Task 038
  Result 문서의 동일한 한계) 이 Task도 그 영향을 그대로 받는다.

## 위험 요소

- `local_main_matches_origin`은 `git fetch origin <branch>`를 실행한다 - 읽기
  전용이지만 네트워크 호출이며, 대상 저장소에 대한 fetch 권한이 필요하다(이미
  다른 명령들도 로컬 git 상태를 읽으므로 새로운 권한 요구사항은 아니다).
- 동일 Goal에 대해 동시에 여러 `devbot goal execute --confirm`이 실행되면(예:
  운영자와 자동화가 동시에 같은 명령을 실행), Issue/Branch 생성 사이에 경쟁
  조건이 있을 수 있다 - GitHub API 자체의 원자성에 의존하며, 이 Task는 별도의
  분산 락을 구현하지 않았다. 저사용 빈도(운영자가 수동으로 트리거)를 고려하면
  낮은 위험으로 판단했다.
