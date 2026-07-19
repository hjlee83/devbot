# Task 119 Result: Reduce planner bootstrap responsibilities to Issue creation

## 완료 내용

- Planner가 Branch/Task Contract/PR을 미리 만들지 않은 `devbot:ready` Issue를
  DevBot host가 bootstrap할 수 있도록 했다.
- `devbot.bootstrap.BranchNamingPolicy`를 추가해 기존 Planner branch pattern인
  `task/<NNN>-<slug>` 생성을 중앙화했다.
- no-PR ready Issue에서 필수 metadata(objective, scope, constraints/non-goals,
  acceptance criteria, verification, implementation context)를 검증하고,
  누락 시 `bootstrap_validation_failed` workspace preparation failure로
  Agent 실행 전에 중단하도록 했다.
- bootstrap 통과 시 `origin/<default_branch>`에서 canonical branch를 만들고
  `tasks/<NNN>-<slug>.md` Task Contract를 Issue 본문 그대로의 의도로
  materialize한다.
- PR은 bootstrap에서 만들지 않고 기존 delivery 경로에 남겼다.
- `dry_run=True`에서는 bootstrap plan만 반환하고 fetch/worktree/contract write를
  수행하지 않도록 했다.
- 기존 Planner-prepared Issue(body의 Branch/Contract/PR metadata + linked PR)는
  계속 기존 경로를 탄다.

## 주요 설계 결정

- Task 번호는 bootstrap 대상 Issue 번호를 사용한다. 이로써 기존
  `task/<NNN>-<slug>`/`tasks/<NNN>-<slug>.md` 규칙과 release aggregation의 branch
  해석 정책을 유지한다.
- 기존 `devbot.workspace.generate_branch_name()`은 삭제하지 않고 새
  `BranchNamingPolicy` wrapper로 바꿔 기존 call site 호환성을 유지했다.
- collision은 같은 Issue worktree가 이미 checkout한 자기 branch를 제외하고,
  다른 기존 branch와 충돌할 때만 deterministic `-2`, `-3` suffix를 붙인다.

## 수정 파일

- `src/devbot/bootstrap.py`
- `src/devbot/planner.py` (리뷰 수정: `DEVBOT_BOOTSTRAP_RESPONSIBILITIES` 신설)
- `src/devbot/workspace.py`
- `src/devbot/worktree.py`
- `tests/test_bootstrap.py`
- `tests/test_planner.py` (리뷰 수정)
- `tests/test_workspace.py`
- `tests/test_worktree.py`
- `CONSTITUTION.md`
- `README.md`
- `docs/07-decisions.md`
- `docs/12-planner-workflow.md` (리뷰 수정: "1.1a DevBot Bootstrap" 절 신설)
- `docs/13-host-managed-workspace-preparation.md`
- `tests/test_main_loop.py` (fixture Issue body now includes required
  bootstrap metadata sections, since an empty body no longer passes
  validation)
- `results/119-reduce-planner-bootstrap-responsibilities.md`

## Checkpoint Evidence

- Naming/normalization/collision: `tests/test_bootstrap.py`
- Required metadata validation: `tests/test_bootstrap.py`,
  `test_ready_issue_without_required_metadata_fails_before_branch_creation`
- Branch + Contract bootstrap: `test_ready_issue_without_pr_bootstraps_canonical_branch_and_contract`
- Idempotent retry/reuse: `test_ready_issue_without_pr_reuses_bootstrapped_worktree_without_duplicate_contract`,
  existing failed-job preservation test
- Dry-run write prevention: `test_bootstrap_dry_run_reports_plan_without_git_writes`
- Backward compatibility: existing linked-PR and Planner metadata worktree tests

## Delivery 중 발견된 blocker와 해소

Delivery 단계에서 `uv run pytest` 실패가 `failure_category=forbidden_host_fallback`
으로 오분류되어 REWORK 대신 `manual-action`으로 빠지는 문제를 만났다. 원인은
`classify_validation_failure()`가 `host_checkout_path in output` 단순 문자열
포함만 검사해서, prepared worktree(`<host_checkout>/.worktrees/issue-119`)가
host checkout 경로를 접두사로 포함하는 구조상 워크트리 내부 파일만 언급하는
정상적인 pytest 실패도 오탐되는 버그였다. 이 저장소 자신의 검증 파이프라인
버그이므로 별도로 PR #120(`fix/119-forbidden-host-fallback-classification`)에서
경로 경계 기반 비교로 수정해 `main`에 먼저 머지했고, 이 브랜치에 `main`을
반영해 이어서 진행했다.

## 리뷰 수정 (PR #121)

- **Planner 책임 모델 코드-문서 불일치**: `CONSTITUTION.md`/`README.md`는
  Planner를 Issue+라벨로 축소했지만, `devbot.planner.PLANNER_RESPONSIBILITIES`
  는 여전히 `branch_creation`/`contract_file_creation`/`pull_request_creation`
  /`cross_linking`을 Planner 책임으로 선언해 코드와 문서가 모순됐다. 이
  네 항목을 새 `devbot.planner.DEVBOT_BOOTSTRAP_RESPONSIBILITIES`로 옮기고,
  `docs/12-planner-workflow.md`에 "1.1a DevBot Bootstrap" 절을 신설해
  누가 무엇을 소유하는지 문서와 코드가 다시 일치하도록 했다.
  `tests/test_planner.py`의 `test_planner_role_contract`도 두 상수 모두
  검증하도록 갱신했다.
- **Issue 번호 1000 이상에서 bootstrap 실패**: `BranchNamingPolicy.
  parse_slug()`의 정규식이 `\d{3}`(정확히 3자리)만 허용해서,
  `canonical_branch_name()`이 `task_number:03d`로 4자리 이상 번호도
  그대로 생성하는데 `parse_slug()`가 그걸 못 읽어 `generated branch does
  not match task pattern`으로 중단됐다. `\d{3,}`(3자리 이상)로 고치고
  999/1000 경계 회귀 테스트(`test_branch_naming_policy_handles_four_digit
  _issue_numbers`, `test_bootstrap_plan_succeeds_for_four_digit_issue
  _number`)를 추가했다.

## Validation 결과

- `uv sync` - PASS
- `uv run ruff check src/devbot/bootstrap.py src/devbot/workspace.py src/devbot/worktree.py tests/test_bootstrap.py tests/test_workspace.py tests/test_worktree.py` - PASS
- `uv run pytest tests/test_planner.py tests/test_bootstrap.py -q` - PASS, 18 passed (리뷰 수정 반영 후)
- `uv run ruff check .` - PASS (PR #120 병합 및 리뷰 수정 반영 후 재검증 포함)
- `uv run pytest` - PASS, 1298 passed (PR #120의 forbidden_host_fallback 수정
  반영 후 1296 + 리뷰 수정으로 추가된 999/1000 경계 테스트 2개)
- `uv run devbot doctor` - NOT RUN, 이번 작업 지시가 `gh`/`curl`을 포함한 원격
  discovery 금지를 명시했고 doctor는 설정에 따라 GitHub/API 상태 확인으로
  확장될 수 있어 실행하지 않았다.
- `uv run devbot --once --dry-run` - NOT RUN, polling cycle은 GitHub Issue/PR
  조회를 수행하므로 이번 작업 지시의 remote discovery 금지 조건과 충돌한다.

## 수동 검증 결과

- 원격 discovery 금지 조건 때문에 `gh`, `curl`, 운영 GitHub polling 명령은
  실행하지 않았다. 테스트 fixture 내부의 로컬 bare repository 대상 `git fetch`는
  network discovery가 아니라 로컬 통합 테스트의 일부로 실행되었다.
- 로컬 git fixture 기반 테스트로 branch 생성, worktree 생성, contract write,
  dry-run no-write를 검증했다.

## 남은 TODO와 제한

- Issue metadata parser는 markdown heading 기반이다. 요구 metadata가 자유형
  문장으로만 존재하면 안전하게 실패한다.

## 위험 요소

- Bootstrap 대상 Issue 번호를 Task 번호로 사용하므로, 사람이 별도 Task 번호를
  Issue 번호와 다르게 운영하려면 추가 metadata 정책이 필요하다.

## Improvement Suggestions

- Issue template에 bootstrap 필수 metadata heading을 명시해 validation 실패를
  줄일 수 있다.
- `devbot doctor`에 bootstrap-ready Issue format 검사 요약을 추가하면 운영자가
  daemon 실행 전에 누락 metadata를 확인할 수 있다.
