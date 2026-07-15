# Task 023 Result: Host-Managed Workspace Preparation

## 완료 내용

Task 023 계약(`tasks/023-host-managed-workspace-preparation.md`)의
CP-023-1부터 CP-023-12까지 전부 구현했다.

Task 022 daemon 시도(Issue #41)는 Implementer까지 도달했지만, 이미 존재하는
Task Branch/PR을 스스로 찾기 위해 `git fetch`/`gh` 같은 네트워크 명령을
실행하려다 대화형 승인 요청에서 멈췄다. 이번 Task는 그 경계를 명시적으로
분리한다: DevBot host가 Agent 실행 전에 linked Branch/PR을 해석하고, 원격을
동기화하며, 격리된 Git worktree를 준비한다. Implementer Agent는 이제 준비된
worktree 안에서 파일을 읽고 쓰고 로컬 검증만 수행하면 된다.

- **Planner 작업공간 사전 해석 (CP-023-1)**: `PollingService`가 IMPLEMENT/
  REWORK Job마다 `github_client.list_pull_requests()`로 linked PR을 Agent
  실행 *전에* 조회하고, 그 결과를 `prepare_workspace(repository, issue,
  linked_pull_request)`에 넘긴다. 기존에는 이 조회가 delivery 직전(Agent
  실행 *이후*)에만 일어났다.
- **Host-managed 원격 동기화 (CP-023-2)**: `WorktreeManager._sync_remote()`
  (`src/devbot/worktree.py`)가 `git fetch origin <branch> <base_branch>`를
  operator checkout에서 직접 실행한다. linked PR이 없는 신규 Task는
  `base_branch`만 fetch하고(Scope §4 - 존재하지 않는 branch를 fetch하는
  fallback은 실행하지 않음), linked PR이 있으면 그 branch도 함께 fetch한다.
- **격리 Job worktree (CP-023-3)**: `<workspace_root>/.devbot-worktrees/
  <repo>/issue-<N>`에 전용 Git worktree를 만든다(`git worktree add`).
  operator checkout의 현재 branch/작업 트리는 건드리지 않는다.
- **기존 Branch 재사용 (CP-023-4)**: linked PR이 있으면 그 `head_ref`를
  그대로 checkout한다 - `devbot.workspace.generate_branch_name()` fallback은
  linked PR이 전혀 없을 때만 실행된다. 같은 Issue/branch로 재호출하면 기존
  worktree를 그대로 재사용한다(`reused=True`).
- **준비된 Agent 컨텍스트 (CP-023-5)**: `render_prepared_workspace_context()`
  가 worktree 경로, checkout된 branch/base branch, Issue/PR 번호, (Issue
  본문에서 파싱 가능하면) Task 계약서/Result 경로, 그리고 "원격 discovery는
  이미 끝났다"/"다른 Branch나 PR을 만들지 말라"는 명시적 문구를 프롬프트
  맨 앞에 렌더링한다.
- **네트워크 없는 구현 호환성 (CP-023-6)**: Agent 호출(`implementer_runner.run`)
  은 준비된 `RepositoryConfig`(worktree 경로)와 프롬프트만 받는다 -
  GitHub 조회는 Job당 정확히 한 번(`prepare_workspace` 내부)만 일어나고
  delivery도 그 결과(`prepared.pull_request`)를 재사용해 다시 조회하지
  않는다.
- **준비된 branch로 delivery (CP-023-7)**: delivery의 검증/commit/push/PR
  재사용이 모두 `prepared.repository`(worktree 경로)와 `prepared.branch`
  위에서 수행된다 - operator checkout이 아니다.
- **Worktree 생명주기와 명시적 cleanup (CP-023-8)**: 실패한 Job의 worktree는
  절대 자동으로 지우지 않는다(다음 `prepare()` 호출이 그대로 재사용,
  미커밋 변경도 보존). 성공적인 cleanup은 `WorktreeManager.cleanup()`
  (CLI: `devbot worktree cleanup --issue <N>`)으로만 일어난다. 같은
  경로에 다른 branch가 dirty 상태로 남아 있으면 `WORKTREE_CONFLICT`로
  거부한다.
- **준비 실패 분류와 복구 (CP-023-9)**: `WorkspacePreparationFailure`
  (`remote_sync_failed`/`linked_branch_missing`/`branch_pr_mismatch`/
  `worktree_creation_failed`/`worktree_conflict`/`workspace_dirty`)가
  세부 원인을 구분한다. `PollingService`는 이 실패를 Agent 실행 전에
  잡아 `_restore()`로 claim 이전 안정 상태로 되돌린다(`PollingStatus.
  WORKSPACE_PREPARATION_FAILED`, `FailureCategory.
  WORKSPACE_PREPARATION_FAILED` - `RESTORE` outcome, 재시도 없음).
  `devbot:working`에 영구히 머무르는 경우는 없다.
- **Doctor/진단 (CP-023-10)**: `devbot doctor`가 저장소마다
  `worktree_health[<repo>]` 항목으로 operator branch, worktree root,
  active/stale/conflicting worktree 수를 보고한다. Git이 모르는 on-disk
  디렉터리(orphaned)는 `conflicting`으로 표시되지만 daemon 전체를 막는
  `fatal` 조건은 아니다.
- **Operator checkout 독립성 (CP-023-11)**: `prepare_workspace`가
  설정되면 기존 `ensure_workspace_ready`(dirty/branch 검사)를 호출하지
  않고, `devbot.workspace.ensure_repository_present()`(존재 + Git
  저장소 여부만 확인)를 대신 사용한다. operator checkout이 dirty하거나
  전혀 다른 branch에 있어도 Job은 영향받지 않는다.
- **회귀와 문서 (CP-023-12)**: 기존 342개 테스트 전부 회귀 없이 통과
  (`test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
  는 Task 023이 실제로 host-managed git 호출을 추가했기 때문에 fixture를
  갱신해야 했다 - 상세는 "기존 테스트 조정" 절 참고). 새 checkpoint 테스트
  13개(`tests/test_worktree.py` 6개 + `tests/test_polling.py` 6개 +
  `tests/test_doctor.py` 1개) 추가. `docs/13-host-managed-workspace-preparation.md`
  (신규), `AGENTS.md` 17절, `docs/12-planner-workflow.md`,
  `docs/07-decisions.md`에 소유권 경계와 설계 결정을 기록했다.

## 수정 파일

신규:
- `src/devbot/worktree.py` - `WorktreeManager`(`prepare`/`cleanup`/`health`),
  `PreparedWorkspace`, `WorkspacePreparationError`,
  `WorkspacePreparationFailure`, `WorktreeHealthReport`,
  `render_prepared_workspace_context()`,
  `parse_contract_path_from_issue_body()`,
  `parse_result_path_from_issue_body()`
- `docs/13-host-managed-workspace-preparation.md` - 소유권 경계, worktree
  레이아웃/생명주기, 실패 분류, doctor/CLI 통합, 기존 워크플로 호환성
- `tests/test_worktree.py` - CP-023-2/3/4/8 필수 테스트 6개 + 보조 테스트
  5개(remote_sync_failed/linked_branch_missing 분류, 프롬프트 렌더링,
  Issue 본문 파싱)

수정:
- `src/devbot/polling.py` - `PollingService.prepare_workspace`(선택적
  주입, 기본 `None`) 추가. `_run_claimed_implement_job`/`_run_rework_job`
  이 Agent 실행 전에 linked PR을 조회하고 `prepare_workspace`를 호출,
  준비된 worktree로 Agent/delivery를 실행. `PollingStatus.
  WORKSPACE_PREPARATION_FAILED` 추가 및 `classify_job_failure()`/
  `_JOB_FAILURE_STATUSES`에 반영.
- `src/devbot/workspace.py` - `ensure_repository_present()`(존재 + Git
  저장소 여부만 확인, dirty 검사 없음) 추가.
- `src/devbot/models.py` - `FailureCategory.WORKSPACE_PREPARATION_FAILED`
  추가.
- `src/devbot/reliability.py` - `RECOVERY_POLICY`/`RETRY_POLICY`/
  `_RECOVERY_HINTS`에 `WORKSPACE_PREPARATION_FAILED` 항목 추가,
  `classify_exception()`에 `WorkspacePreparationError` 매핑 추가.
- `src/devbot/doctor.py` - `check_worktree_health()` 추가,
  `build_doctor_report()`가 저장소마다 이 체크를 포함.
- `src/devbot/main.py` - `devbot worktree status|cleanup` CLI 서브커맨드
  추가, `main()`이 `WorktreeManager`를 만들어 `PollingService.
  prepare_workspace`로 주입, `_FAILURE_STATUSES`에 새 상태 추가.
- `AGENTS.md` - 버전 1.3.0 -> 1.4.0. 문서 끝에 17절 "Host-Managed
  Workspace Preparation (Task 023)" 추가(기존 절 번호 변경 없음).
- `docs/12-planner-workflow.md` - 1.2절(Implementer 책임)에 Task 023
  cross-reference 추가 - "기존 Branch/PR 위에서만 계속 구현한다"는 원칙
  자체는 바뀌지 않고 그 Branch/PR을 누가 찾아두는지만 바뀜을 명시.
- `docs/07-decisions.md` - "Host-managed worktrees are keyed by Issue
  number..." 항목 추가(비자명한 설계 결정 3가지 + operator checkout
  preflight 완화 근거 기록).
- `tests/test_doctor.py` - CP-023-10 `test_doctor_reports_worktree_health`
  추가.
- `tests/test_polling.py` - CP-023-1/5/6/7/9/11 통합 테스트 6개 추가
  (`_operator_repo`/`_prepared_workspace` 헬퍼 포함).
- `tests/test_main_loop.py` - `test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
  fixture 갱신 (아래 "기존 테스트 조정" 절).

## 기존 테스트 조정

`devbot.worktree`가 process-wide `subprocess` 모듈을 `devbot.agents.codex`
와 공유하기 때문에, `test_run_once_exits_with_failure_code_when_agent_returncode_is_nonzero`
가 쓰던 `patch("devbot.agents.codex.subprocess.run")` 전면 mock이 내
`git fetch`/`git worktree add` 호출까지 가로채 "boom"으로 실패시켰다(Task
023 이전에는 이 preflight 자체가 없었으므로 드러나지 않던 문제). 다음과
같이 고쳤다: (1) 실제 bare `origin` 저장소를 만들어 host-managed fetch가
진짜로 성공할 수 있게 했고, (2) `subprocess.run`의 `side_effect`를
`args[0] == "codex"`일 때만 가짜 실패를 반환하고 그 외(= `git`)는 실제
`subprocess.run`으로 위임하도록 바꿨으며, (3) `list_pull_requests`를
빈 리스트로 mock해 불필요한 실제 GitHub 호출을 막았다. 테스트가 검증하는
행위(에이전트 실패 -> claim/blocked 전이 2회, comment 1회)는 그대로다 -
mock 정밀도만 개선했다.

## CI에서 발견한 문제

첫 push 후 PR #44의 `verify` CI가 실패했다: `tests/test_worktree.py`의
`_push_branch()`/`_push_to_main()` 헬퍼가 throwaway clone에서 `git
commit`을 실행하기 전에 `user.email`/`user.name`을 설정하지 않았다. 이
저장소의 로컬 개발 환경에는 global git identity가 이미 설정되어 있어
로컬 `uv run pytest`는 항상 통과했지만, CI runner에는 global identity가
없어 `git commit`이 종료 코드 128("Please tell me who you are")로
실패했다(`test_host_prepares_remote_branch_before_agent`,
`test_existing_task_branch_is_reused`,
`test_conflicting_dirty_worktree_is_rejected`,
`test_render_prepared_workspace_context_states_no_network_needed` 4개
실패). `_clone()` 공용 헬퍼를 추가해 모든 throwaway clone이 자체
`user.email`/`user.name`을 설정하도록 고쳤고, `HOME`/`GIT_CONFIG_GLOBAL`/
`GIT_CONFIG_SYSTEM`을 빈 값으로 override해 global identity가 전혀 없는
환경을 로컬에서 재현한 뒤 전체 스위트(360개)가 통과함을 재확인했다.

두 번째 push 후에도 CI가 실패했다(이번엔 로컬 재현 환경에서는 재현되지
않음): `test_host_prepares_remote_branch_before_agent`가
`git push -q origin HEAD:refs/heads/main`에서 종료 코드 1로 실패했다.
원인은 `git init --bare`가 만드는 bare `origin`의 symbolic HEAD가
`init.defaultBranch`를 따르는데, 이 값이 CI runner에서는(전형적으로
`master`로 귀결) 로컬 개발 환경과 다르게 해석된다는 점이었다 - `origin`
에 `main`만 push해두어도 `origin`의 HEAD 자체는 여전히 존재하지 않는
`master`를 가리킬 수 있고, 이름 없는 `git clone <origin> <dest>`
(`_clone()`이 쓰는 방식)는 바로 그 HEAD를 따라 checkout할 branch를
정하므로, CI에서는 `main`이 아닌 다른(대개 비어 있는/무관한 이력의)
checkout이 만들어졌다. 그 위에서 만든 커밋을 다시 `refs/heads/main`에
push하면 origin의 실제 `main` 이력과 무관한 fast-forward가 아닌 push가
되어 거부된다. `_init_bare_repo()`가 bare 저장소 생성 직후
`git symbolic-ref HEAD refs/heads/main`으로 HEAD를 명시적으로 고정하도록
고쳐, 이후 모든 이름 없는 clone이 runner의 기본값과 무관하게 항상
`main`을 checkout하게 만들었다. 세 번째 push 후 CI(`verify`)가 통과함을
확인했다(`https://github.com/hjlee83/devbot/actions/runs/29421682922`).

## Checkpoint별 테스트

| Checkpoint | 테스트 |
|---|---|
| CP-023-1 Planner 작업공간 사전 해석 | `test_existing_planner_workspace_is_resolved_before_agent` |
| CP-023-2 host-managed 원격 동기화 | `test_host_prepares_remote_branch_before_agent` |
| CP-023-3 격리 worktree 생성 | `test_job_uses_isolated_worktree` |
| CP-023-4 기존 branch 재사용 | `test_existing_task_branch_is_reused` |
| CP-023-5 준비된 Agent 컨텍스트 | `test_agent_prompt_contains_prepared_workspace_context` |
| CP-023-6 네트워크 없는 구현 호환성 | `test_implementation_does_not_require_agent_network_access` |
| CP-023-7 준비된 branch로 delivery | `test_delivery_uses_prepared_worktree_branch` |
| CP-023-8 worktree 생명주기/cleanup | `test_failed_job_preserves_worktree_for_recovery`, `test_successful_cleanup_removes_worktree`, `test_conflicting_dirty_worktree_is_rejected` |
| CP-023-9 준비 실패 복구 | `test_workspace_preparation_failure_skips_agent_and_recovers_state` |
| CP-023-10 doctor/진단 | `test_doctor_reports_worktree_health` |
| CP-023-11 operator checkout 독립성 | `test_daemon_job_is_independent_of_operator_checkout_branch` |
| CP-023-12 회귀/문서 | 기존 342개 테스트 전부 통과(회귀 없음) + 이 문서 |

## 검증 결과

```
uv sync
  Resolved 15 packages in 26ms
  Checked 14 packages in 7ms

uv run ruff check .
  All checks passed!

uv run pytest
  360 passed (기존 342개 + Task 023 신규 18개: test_worktree.py 11개
  [필수 6 + 보조 5] + test_polling.py 6개 + test_doctor.py 1개;
  test_main_loop.py 1개는 신규 테스트가 아니라 위 "기존 테스트 조정"에서
  설명한 fixture 갱신)

uv run devbot --dry-run doctor
  (실제 hjlee83/devbot 배포 설정으로 실행)
  safe_to_start: yes
  worktree_health[hjlee83/devbot]: operator_branch=task/023-host-managed-workspace-preparation
    root=/Users/luna/workspace/.devbot-worktrees/devbot active=0 stale=0 conflicting=0
  (workspace_cleanliness/current_branch_compatibility는 이 Task 자신의
  미커밋 변경 + task/023-* 작업 Branch가 원인인 FAIL이며, Task 019
  설계대로 daemon 시작을 막지 않는 정보성 체크다 - worktree_health는 OK)

uv run devbot --once --dry-run
  (실제 hjlee83/devbot 배포 설정 · 실제 GitHub 인증/조회로 실행, 종료 코드 0)
  Queue Summary: manual-action=1 (Issue #45 자신)
  cycle 종료: 결과=no_ready_task (선택 0/1)
  1회 실행 완료: no_ready_task

CI (`verify`, PR #44, head cc60320)
  pass (18s) - https://github.com/hjlee83/devbot/actions/runs/29421682922
```

### 수동 검증 (계약 Validation Gate "Manual verification" 항목)

실제 배포 저장소가 아니라 격리된 scratch 저장소(bare origin + operator
checkout, 스크래치패드에 별도 스크립트로 작성)를 만들어 아래 6개 항목을
전부 직접 실행/관찰했다 - 단위 테스트가 이미 각 항목을 개별적으로
검증하지만, 계약이 "manual verification"으로 명시한 절이라 실제 흐름을
end-to-end로 한 번 더 육안 확인했다.

- **operator checkout을 non-main branch에 둔 채 준비된 Job 1회 실행**:
  operator checkout을 `someone/unrelated-debugging` branch(미커밋 변경
  포함)에 둔 상태에서 `WorktreeManager.prepare()`를 호출했다. `prepare()`
  이후에도 operator checkout의 branch와 미커밋 파일은 그대로였다.
- **Agent가 linked branch로 checkout된 Task worktree 안에서 시작함을
  확인**: 준비된 worktree의 `git rev-parse --abbrev-ref HEAD`가 linked
  PR의 `head_ref`(`task/023-manual-check`)와 정확히 일치했고, Planner가
  미리 push해둔 파일(`feature.txt`)이 그 worktree 안에 실제로 존재했다.
- **`git fetch`/`gh`/`curl` 없이 구현 가능함을 확인**: 준비된 worktree
  안에서 `git add`/`git commit`만으로 새 파일(`implemented.txt`)을
  커밋했다 - 별도 discovery 명령이 전혀 필요하지 않았다.
- **delivery가 기존 Planner PR을 갱신함을 확인**: 그 worktree에서
  `git push origin task/023-manual-check:task/023-manual-check`를
  실행한 뒤, 완전히 별도의 fresh clone으로 `task/023-manual-check`를
  다시 clone해 Agent의 커밋(`implemented.txt`)이 origin에 실제로
  반영됐음을 확인했다.
- **실패한 Job이 worktree와 진단 매핑을 보존함을 확인**: worktree에
  "실패로 남겨진" 파일(`leftover-debug-output.txt`)을 만든 뒤
  `manager.health(repository)`로 active=1을 확인하고, 같은 Issue/PR로
  `prepare()`를 재호출해 같은 worktree가 재사용되며(`reused=True`) 그
  leftover 파일이 그대로 남아 있음을 확인했다.
- **명시적 cleanup이 worktree를 안전하게 제거함을 확인**: `manager.
  cleanup(repository, issue.number)` 호출 후 worktree 디렉터리가
  사라졌고, `git worktree list`에도 더 이상 나타나지 않음을 확인했다.

이 수동 검증 과정에서 실제 버그 하나를 발견해 수정했다: macOS는 `/var`가
`/private/var`의 심볼릭 링크이고, `git worktree list --porcelain`은
항상 realpath로 정규화된 경로를 보고하는 반면 `WorktreeManager`가
계산하던 경로는 정규화되지 않은 경로였다 - `workspace_root`가 심볼릭
링크 뒤에 있으면(예: macOS 기본 임시 디렉터리) 방금 만든 worktree를
"등록되지 않음"으로 오판해 재사용/health 판정이 모두 어긋났다.
`WorktreeManager.worktree_root()`와 `_parse_worktree_list()` 양쪽에
`.resolve()`를 적용해 고쳤다 - `tests/test_worktree.py`/
`tests/test_polling.py`가 쓰는 pytest `tmp_path`는 이미 정규화된 경로라
자동 테스트만으로는 이 문제가 드러나지 않았다(수동 검증이 실제로
유용했던 지점).

## TODO

없음 (이 Task 범위 내). Agent의 무제한 네트워크 접근, Claude/Codex 승인
또는 sandbox 통제 우회, Planner 모델 자동 실행, 다중 Agent Failover,
Timeline 자동 기록, 자동 Merge, VPS/launchd/systemd 배포, 같은 Task
Branch를 동시에 수정하는 병렬 Job은 계약의 명시적 제외 범위이며 후속
Task로 남긴다.

## 위험 요소

- **REWORK Job의 프롬프트에는 준비된 컨텍스트 블록이 없다.** IMPLEMENT
  Job은 `_run_claimed_implement_job`이 직접 프롬프트를 만들기 때문에
  `render_prepared_workspace_context()`를 앞에 붙일 수 있지만, REWORK
  Job은 `ReworkService.process()`가 주입받은 `apply_changes` 콜백
  (`devbot.main._apply_rework_changes`)이 프롬프트를 만든다 - 그 콜백은
  `PreparedWorkspace` 객체 자체를 받지 않고 이미 준비된 worktree
  `RepositoryConfig`만 받는다. Worktree 격리/branch 재사용/delivery
  정확성(CP-023-3/4/6/7)은 REWORK Job에도 동일하게 적용되지만, 명시적인
  "원격 discovery는 이미 끝났다"는 문구는 IMPLEMENT Job의 프롬프트에만
  나타난다. REWORK Agent도 여전히 이미 격리된 worktree 안에서만 동작하므로
  실질적인 네트워크 필요성은 없지만, 프롬프트 문구 자체는 비대칭이다.
- **`prepare_workspace`는 선택적 의존성**이라 `main.py`가 아닌 다른
  진입점(예: 향후 테스트 하네스나 스크립트)이 `PollingService`를 직접
  생성하면서 이 인자를 빠뜨리면 Task 023 이전 동작(operator checkout에서
  직접 실행)으로 조용히 되돌아간다 - 의도된 하위 호환성이지만, 새로운
  daemon 진입점을 추가할 때는 이 인자를 반드시 주입해야 한다는 점을
  놓치기 쉽다.
- **Worktree cleanup은 항상 명시적이다** - 성공적인 delivery 이후에도
  자동으로 지워지지 않는다(설계상 의도, `docs/07-decisions.md` 참고:
  자동 merge 감지가 없는 상태에서 지우면 아직 열려 있는 PR의 branch
  내용을 삭제하는 셈이다). 따라서 운영자가 주기적으로 `devbot worktree
  cleanup` 또는 `devbot doctor`의 `worktree_health`를 확인하지 않으면
  `.devbot-worktrees/` 아래에 worktree가 무기한 누적될 수 있다.
- **`parse_contract_path_from_issue_body()`/`parse_result_path_from_issue_body()`
  는 best-effort 정규식 파싱**이다 - Planner가 렌더링한 표준 형식
  (`` - Contract: `...` ``, `` Produce `...`. ``)에만 의존하며, 수동으로
  작성된 Issue 본문이나 형식이 다른 경우 `None`을 반환한다(계약 경로/
  Result 경로 없이도 프롬프트의 나머지 컨텍스트는 정상 렌더링된다).
- 공유 운영 배포(`hjlee83/devbot`, 이 Task 자신이 진행 중인 저장소)를
  대상으로 한 진짜 non-dry-run `uv run devbot --once`는 실행하지
  않았다 - Task 019/021/022 Result 문서와 동일한 판단 기준으로, 이 검증
  실행이 PR #44/Issue #45의 실제 상태를 예기치 않게 바꿀 위험을 피했다.
  `--dry-run` 실행과 격리된 scratch 저장소를 이용한 수동 검증이 실제
  Git/GitHub 경로가 정상 동작함을 이미 확인했다.

## Improvement Suggestions

- REWORK Job의 `apply_changes` 콜백 시그니처에 `PreparedWorkspace`(또는
  최소한 branch/PR/Issue 요약)를 함께 전달하도록 확장하면, 위 위험 요소의
  프롬프트 비대칭을 없애고 REWORK Agent에게도 동일한 "원격 discovery
  불필요" 문구를 보여줄 수 있다 - `devbot.rework.ApplyChangesFn`의
  시그니처 변경이 필요해 이번 Task 범위에서는 보수적으로 제외했다.
- `devbot worktree cleanup`을 PR 병합 감지와 연결하는 후속 Task
  (예: `devbot worktree gc --merged`)를 추가하면, 지금은 운영자가
  수동으로 확인해야 하는 "병합된 PR의 worktree 정리"를 자동화할 수
  있다 - 다만 이는 이 Task의 명시적 제외 범위("자동 merge 감지")와
  맞닿아 있어 별도 계약으로 다뤄야 한다.
- `devbot doctor`의 `worktree_health`가 지금은 "conflicting(등록되지
  않은 on-disk 디렉터리)"만 감지한다. `git worktree list --porcelain`의
  `locked` 플래그도 함께 보고하면, 사람이 의도적으로 잠가둔 worktree와
  단순히 오래된/충돌하는 worktree를 운영자가 더 쉽게 구분할 수 있다.
