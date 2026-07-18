# Task 038 Result: Goal-based Planning (Operator Planner)

## 완료 내용

- `devbot goal plan "<goal>"` CLI를 추가했다 (`src/devbot/main.py`의
  `_build_goal_parser`/`_run_goal_command`, 기존 `release`/`timeline`/`worktree`/
  `doctor`와 동일하게 daemon `ProcessLock` 없이 실행되고 `main()`에서 `ProcessLock`
  진입 전에 분기된다).
- `src/devbot/goal_planner.py`를 새로 만들어 순수 결정 로직(`plan_goal`)과
  GitHub/파일시스템 읽기(`fetch_goal_plan`)를 분리했다 - Task 037의
  `build_release_preview`/`fetch_release_preview` 구조를 그대로 따른다.
- 결정은 다섯 가지 중 하나로 결정론적으로 계산된다: `already_completed`,
  `duplicate_open_work`, `single_task`, `multi_task`, `ambiguous`.
- 판단 순서: (1) Goal에 유의미한 단어가 2개 미만이거나 실행 동사가 하나도 없으면
  즉시 `ambiguous`. (2) 열린 GitHub Issue/PR과의 텍스트 중첩이 임계값(0.6) 이상이면
  `duplicate_open_work`(가장 먼저 확인 - 진행 중인 작업을 이미 끝난 것으로 오판하지
  않기 위함). (3) 손으로 정리한 capability catalog
  (`CAPABILITY_CATALOG`)에 일치하면: 이미 구현됐으면 `already_completed`,
  아니면 그 도메인의 고정 Task 템플릿 개수에 따라 `single_task`/`multi_task`.
  (4) catalog에 없으면 `docs/00-roadmap.md`의 완료된(`[x]`) 항목과의 텍스트 중첩을
  확인해(임계값 0.5) 일치하면 `already_completed`. (5) 그래도 없으면 `ambiguous`.
- catalog는 6개 도메인을 포함한다: `release_publish`/`release_notes_generation`/
  `release_operator_ux`(모두 Task 037로 구현됨), `github_api_reliability`(Task
  030으로 구현됨), `automatic_merge`(B2로 구현됨), `global_path_launcher`(미구현,
  Task 032 Out of Scope 항목 - 단일 Task), `self_update_runtime`(미구현,
  `docs/history.md` Known Limitations의 "Runtime automatic update discovery" -
  Discovery→Fetch and Verify→Apply 3개 Task, 의존성 순서대로).
- `multi_task` 계획의 각 Task는 `dependencies`가 항상 더 앞선 `order`의 Task만
  가리키도록 보장하며, `dependency_order_is_valid()`로 구조적으로 검증 가능하다.
- 계획된 Task의 Issue/Branch/Contract/PR을 만들거나 실행하지 않는다 - 읽기 전용
  계획 수립만 한다.

## 주요 설계 결정

- **키워드/구절 포함 매칭과 토큰 중첩 비율만 사용하고, LLM이나 자유 텍스트
  요약을 쓰지 않는다.** "지어낸 기능을 만들지 않는다"는 안전 요구사항을 코드
  구조 자체로 강제하기 위함이다 - `planned_tasks`의 모든 문구는 catalog에
  미리 고정된 템플릿에서만 나오고, Goal 텍스트에서 생성되지 않는다. 이는
  또한 결정론성과 오프라인 테스트 가능성(모킹 없이 `plan_goal()` 단위 테스트
  가능)을 보장한다.
- **catalog 밖 Goal은 로드맵 텍스트 중첩으로 한 번 더 확인한 뒤, 그래도
  없으면 항상 `ambiguous`다.** catalog가 아직 모르는 완료된 작업도
  `docs/00-roadmap.md` 직접 비교로 잡아내되, 미구현 작업을 위한 분해
  (deliverables/acceptance criteria)는 오직 catalog에서만 나온다 - 근거 없이
  "어떻게 나눌지"를 지어내는 것을 막기 위함이다.
- **열린 Issue/PR 중첩 확인이 catalog/로드맵 확인보다 먼저 실행된다.** 진행
  중인 작업이 있다면 "이미 끝났다"고 잘못 보고하는 대신 "이미 다루고 있다"고
  보고하는 것이 더 안전하고 정확하기 때문이다.
- **저장소 이름과 Task/Issue/PR 템플릿 상투어를 불용어로 제외한다
  (`_BOILERPLATE_STOPWORDS`).** 실제 라이브 검증 중 발견한 문제: "devbot"과
  `devbot.planner.render_task_issue_body`/`render_pr_body`가 모든 Task
  Issue/PR 본문에 넣는 고정 문구("Task", "Branch", "Contract", "Pull",
  "Request", "Result")가 거의 모든 Goal과 거의 모든 열린 Issue/PR 사이의
  중첩 점수를 부풀려, 실제로 무관한 Goal도 `duplicate_open_work`로 오판하게
  만들었다. 아래 "수동 검증 결과"에 실제 발견 경위를 기록했다.
- **`PlannedTask`/`GoalPlan`은 Task 037의 `ReleasePreview`/`PublishOutcome`과
  동일한 스타일의 얕은 frozen dataclass다.** 새로운 추상화를 만들지 않고
  기존 컨벤션을 재사용했다.

## 수정 파일

- `src/devbot/goal_planner.py` (신규)
- `src/devbot/main.py`
- `tests/test_goal_planner.py` (신규)
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `tasks/038-goal-based-planning.md`
- `results/038-goal-based-planning.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-038-1 완료된 Goal 판정 | `test_goal_matching_implemented_catalog_domain_is_already_completed`, `test_goal_matching_completed_roadmap_entry_is_already_completed`, `test_incomplete_roadmap_entry_is_not_treated_as_completed_evidence` |
| CP-038-2 단일 Task Goal | `test_single_task_goal_produces_exactly_one_planned_task` |
| CP-038-3 다중 Task Goal | `test_multi_task_goal_produces_multiple_ordered_planned_tasks` |
| CP-038-4 애매한 Goal은 fail closed | `test_short_goal_is_ambiguous`, `test_goal_without_an_actionable_verb_is_ambiguous`, `test_actionable_but_unrecognized_goal_is_ambiguous_not_invented`, `test_goal_plan_ambiguous_goal_returns_failure_exit_code` |
| CP-038-5 중복/겹치는 Goal 판정 | `test_goal_overlapping_open_issue_is_duplicate_open_work`, `test_open_work_overlap_takes_priority_over_catalog_match`, `test_low_overlap_open_work_does_not_count_as_duplicate`, `test_repo_name_and_task_template_boilerplate_do_not_inflate_overlap` |
| CP-038-6 의존성 순서 | `test_multi_task_plan_dependencies_reference_strictly_earlier_tasks`, `test_dependency_order_is_valid_detects_a_forward_reference`, `test_every_catalog_domain_has_valid_dependency_ordering` |
| CP-038-7 읽기 전용 동작 | `test_fetch_goal_plan_never_calls_a_write_method`, `test_fetch_goal_plan_reads_open_issues_and_pull_requests`, `test_fetch_goal_plan_tolerates_a_missing_roadmap_file`, `test_goal_plan_command_is_wired`, `test_goal_plan_command_does_not_acquire_daemon_lock` |
| CP-038-8 로드맵 파싱 | `test_parse_roadmap_extracts_completed_and_incomplete_entries` |
| CP-038-9 문서와 근거 | 본 Result, `docs/00-roadmap.md`, `docs/08-beta-runbook.md` |
| CP-038-10 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 632 passed
  (기존 609개 + 이번 Task에서 추가한 23개: `tests/test_goal_planner.py` 20개,
  `tests/test_main.py` 3개)

## 수동 검증 결과

`hjlee83/devbot` 실제 저장소를 대상으로 `devbot goal plan`을 여러 번 직접
실행했다(읽기 전용, 아무것도 쓰지 않음). 계획된 Task의 Issue/Branch/Contract/PR은
생성하지 않았다.

- **already_completed**: `uv run devbot goal plan "Reduce GitHub API failures."`
  → `decision: already_completed`, evidence: Task 030. (종료 코드 0)
- **multi_task**: `uv run devbot goal plan "Build a self-update mechanism for
  devbot."` → `decision: multi_task`, 3개 Task(Self-Update Discovery → Fetch and
  Verify → Apply), 의존성 순서대로. (종료 코드 0)
- **ambiguous**: `uv run devbot goal plan "Make things better."` →
  `decision: ambiguous`. (종료 코드 1)

**실제 라이브 검증 중 발견한 문제와 수정:** 처음에는 `"Implement Self Update."`와
`"Publish the next stable release."`(Task 038 Issue #81 본문에 예시로 그대로 인용된
문구들)를 실행하면 `duplicate_open_work`(Issue #81과 겹침)로 잘못 판정됐다. 원인을
`_tokenize`/`_overlap_score`로 직접 재현해 확인한 결과, "devbot"이라는 저장소 이름과
Task Issue/PR 템플릿의 고정 문구("Task", "Branch", "Contract" 등)가 거의 모든
Goal과 거의 모든 열린 Issue/PR 사이의 중첩 점수를 부풀리고 있었다(예:
`"Build a self-update mechanism for devbot."` vs 무관한 Issue #81 본문 사이의
중첩이 0.6으로 임계값에 걸림 - 겹친 단어가 "devbot"/"self"/"update"뿐이었다).
`_BOILERPLATE_STOPWORDS`(devbot/task/issue/branch/contract/pull/request/result)를
추가해 수정했고, 이 정확한 시나리오를 회귀 테스트
`test_repo_name_and_task_template_boilerplate_do_not_inflate_overlap`로 고정했다.
수정 후 위 세 가지 예시는 모두 의도한 대로 판정된다.

## 남은 TODO와 제한

- **capability catalog는 손으로 유지 관리되며 자동으로 최신 상태를 유지하지
  않는다.** 새 Task가 catalog에 없는 기능을 구현하면, 그 정확한 문구가
  `docs/00-roadmap.md`에 등록되기 전까지는 `already_completed`로 잡히지 않을 수
  있다(로드맵 텍스트 중첩 fallback이 있지만, catalog만큼 정밀하지는 않다). 후속
  Task가 새 기능을 추가할 때 catalog에도 도메인을 추가하는 습관이 필요하다.
- **중첩 임계값(0.6/0.5)은 고정된 값이며, 이 저장소의 실제 Issue/PR/로드맵 텍스트
  분량을 기준으로 손으로 고른 값이다.** 저장소가 커지거나 Issue 본문 스타일이
  크게 바뀌면 재조정이 필요할 수 있다.
- **자연어 이해가 아니라 결정론적 텍스트 매칭이다.** 동의어나 다른 표현으로 쓰인
  Goal은 catalog 문구와 정확히 겹치지 않으면 `ambiguous`로 처리될 수 있다 -
  이는 의도된 설계(지어내지 않기 위함)이며 버그가 아니다.
- 계획 실행(계획된 Task의 실제 Issue/Branch/Contract/PR 생성)은 계약에서 명시적
  범위 밖으로 뒀다 - 후속 Task가 필요하다.

## 위험 요소

- 중첩 임계값 근처의 Goal은 향후 catalog나 로드맵 텍스트가 바뀌면 판정이
  달라질 수 있다(예: 이번에 발견한 "devbot" 불용어 문제처럼). 완화책으로
  `_BOILERPLATE_STOPWORDS`를 추가했지만, 유사한 새로운 저품질 신호 단어가
  나중에 발견될 가능성은 남아 있다.
- catalog와 실제 저장소 상태가 어긋나면(위 "남은 TODO" 참고) `already_completed`
  판정을 놓치고 이미 구현된 기능을 다시 `multi_task`/`single_task`로 잘못
  계획할 위험이 있다 - 다만 이 Task는 계획을 실행하지 않으므로(읽기 전용),
  실제 중복 구현으로 이어지려면 사람이 그 계획을 보고 별도로 승인해야 한다.
