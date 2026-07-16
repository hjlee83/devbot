# Host-Managed Workspace Preparation

Version: 1.0.0
Last Updated: 2026-07-15

이 문서는 Task 023의 산출물이며, DevBot이 Implementer Agent를 실행하기 전에
Task Branch/Pull Request 해석과 격리된 Git worktree 준비를 어떻게
소유하는지 정의한다. `docs/12-planner-workflow.md`(Task 022)가 정의한
Planner/Implementer/Reviewer/Operator 네 역할 경계를 대체하지 않고, 그
경계에 "DevBot host"라는 다섯 번째 소유자를 명시적으로 추가한다.

## 1. 배경

Task 022 daemon 시도(Issue #41)는 Implementer까지는 도달했지만, 이미 존재하는
Task Branch와 PR을 스스로 찾기 위해 `git fetch`/`gh` 같은 네트워크 명령을
실행하려다 대화형 승인 요청에서 멈췄다. DevBot은 Issue/Branch/PR/저장소를
이미 알고 있었지만, 그 정보를 Agent 실행 전에 "실행 가능한 작업공간"으로
준비해두지 않았다.

## 2. 소유권 경계 (Scope §12)

```text
Planner        : Task/Branch/PR/Issue 계약을 생성한다
DevBot host    : fetch, worktree 준비, delivery, GitHub 갱신을 수행한다
Implementer     : 준비된 로컬 저장소 파일을 수정하고 검증한다
Agent
Reviewer       : 결과 PR을 리뷰한다
Operator       : Merge를 수행한다
```

- **DevBot host**는 `src/devbot/worktree.py`(`WorktreeManager`)로 구현된다.
  IMPLEMENT/REWORK Job마다 GitHub에서 이미 조회한 linked PR 정보를 받아
  `git fetch`, `git worktree add`, branch 재사용/생성을 수행하고, Agent가
  실행될 `RepositoryConfig`(worktree 경로)를 만들어 넘긴다.
- **Implementer Agent**는 이제 `git fetch`/`gh`/`curl` 등 원격 discovery를
  전혀 수행할 필요가 없다 - 준비된 worktree 안에서 파일을 읽고 쓰고 로컬
  검증 명령만 실행한다.
- Delivery(검증/commit/push/PR 재사용)도 같은 준비된 worktree와 branch
  위에서 수행된다 (`devbot.delivery.DeliveryService`는 변경되지 않았고,
  `devbot.polling`이 operator checkout 대신 준비된 `RepositoryConfig`를
  넘긴다).

## 3. Worktree 레이아웃과 생명주기

```text
<workspace-root>/<repo>                                  # operator checkout
<workspace-root>/.devbot-worktrees/<repo>/issue-<N>       # Job worktree
```

Job worktree는 Task 번호가 아니라 GitHub Issue 번호로 식별한다 - Issue
번호는 모든 Job이 항상 갖고 있는 유일한 식별자이기 때문이다(Task 번호는
Issue 본문에서 best-effort로만 파싱된다, 4절 참고).

생명주기 (`WorktreeManager`):

- **PR 해석**: Agent 실행 전 `PollingService`가 실행 Issue 본문의
  `Pull Request: #<number>` Planner metadata를 먼저 확인한다. 명시 PR이
  있으면 그 PR이 authoritative이며, PR body에 `Closes #<issue>`가 없어도
  해당 PR을 사용한다. 명시 PR을 찾을 수 없으면 fallback branch를 만들지
  않고 `linked_branch_missing` workspace preparation failure로 중단한다.
  Planner PR metadata가 없는 legacy Issue만 기존 closing-keyword 해석과
  fallback branch 생성을 유지한다.
- **생성**: Agent 실행 전, `prepare()`가 linked PR이 있으면 그 branch를,
  없으면 `devbot.workspace.generate_branch_name()`으로 만든 새 branch를
  `origin/<default_branch>`에서 분기해 worktree를 만든다.
- **재사용**: 같은 저장소/Issue/branch로 다시 `prepare()`가 호출되면 기존
  worktree를 그대로 재사용한다 - 이전 실행이 남긴 미커밋 변경도 그대로
  보존된다(진단/복구 목적).
- **Resume 후보 표시**: Task 026 이후 `PreparedWorkspace`는 재사용 여부와
  현재 dirty 여부를 함께 전달한다. `PollingService`는 repository/Issue/PR/
  branch/contract metadata가 모두 맞고 기존 worktree가 dirty일 때만
  continuation prompt를 추가한다. Contract metadata 누락, branch/PR
  mismatch, unrelated dirty worktree는 삭제하지 않고 `manual-action`으로
  보낸다.
- **보존**: Job 실행/Delivery 실패 시 worktree를 자동으로 지우지 않는다.
  정리는 항상 명시적으로만 일어난다.
- **명시적 정리**: `devbot worktree cleanup --issue <N> [--repo owner/repo]
  [--force]` (서비스 API: `WorktreeManager.cleanup()`).
- **충돌 거부**: 같은 경로에 다른 branch가 dirty 상태로 이미 존재하면
  `WorkspacePreparationFailure.WORKTREE_CONFLICT`로 거부한다(안전하지 않은
  재사용 금지).
- **Issue branch/PR mismatch 거부**: 실행 Issue의 `Branch: ` metadata와
  resolved PR head branch가 모두 존재하고 서로 다르면
  `WorkspacePreparationFailure.BRANCH_PR_MISMATCH`로 거부한다.
- **branch/PR mismatch 거부**: 같은 경로가 이미 등록된 worktree이고
  clean하지만 branch가 새로 해석된 branch와 다르면(예: 같은 Issue의 linked
  PR이 다른 branch를 가리키도록 바뀐 경우) `WorkspacePreparationFailure.
  BRANCH_PR_MISMATCH`로 거부한다 - 조용히 삭제하고 새 branch로 재생성하지
  않는다(PR #44 리뷰 반영: 이전 구현은 이 경우를 실패로 보고하지 않고
  자동으로 지우고 재생성했다). 재시도하려면 `devbot worktree cleanup`으로
  명시적으로 정리해야 한다.
- **prepared workspace dirty 거부**: 새로 만든(재사용이 아닌) worktree가
  checkout 직후 예상과 달리 dirty하면 `WorkspacePreparationFailure.
  WORKSPACE_DIRTY`로 거부한다 - `git worktree add` 명령 자체는 성공했지만
  결과물이 안전하지 않은 경우를 `WORKTREE_CREATION_FAILED`(명령 실패)와
  구분한다.

## 4. 준비된 Agent 컨텍스트

`devbot.worktree.render_prepared_workspace_context()`가 Agent 프롬프트
앞에 붙는 블록을 렌더링한다. 포함 항목:

- 현재 작업 디렉터리(worktree 경로)
- 이미 checkout된 branch, base branch
- Issue 번호, (있으면) PR 번호
- (Issue 본문에서 파싱 가능하면) Task 계약서/Result 경로
- "원격 discovery는 이미 끝났으니 `git fetch`/`gh`/`curl`을 실행하지
  말라"는 명시적 문구
- "다른 Branch나 PR을 만들지 말라"는 명시적 금지 문구

Task 026의 resumed 실행에는 `render_resume_workspace_context()`가 그 앞에
추가된다. 이 블록은 이전 timeout/interruption 이후의 continuation임을
명시하고, 현재 diff/untracked file을 먼저 확인하며 완료된 작업을 reset,
delete, overwrite, discard하지 말고 기존 branch/PR에 commit/push하라고
지시한다.

## 5. 실패 분류 (Scope §9)

`devbot.worktree.WorkspacePreparationFailure`가 최소 다음을 구분한다:
`remote_sync_failed`, `linked_branch_missing`, `branch_pr_mismatch`,
`worktree_creation_failed`, `worktree_conflict`, `workspace_dirty`.

daemon 전체 재시도/복구 정책(`devbot.reliability`)에서는 이 모두가
`FailureCategory.WORKSPACE_PREPARATION_FAILED`(재시도 없음, `RESTORE`
outcome) 하나로 수렴한다 - 세부 카테고리는 `WorkspacePreparationError`
메시지와 GitHub `blocked`/복구 코멘트에 그대로 남아 사람이 원인을 바로
알 수 있게 한다. Agent 실행 전 실패이므로 Issue는 `working`에 머무르지
않고 이전 안정 상태(`ready`/`rework`)로 즉시 복구된다 - `WORKSPACE_INVALID`
preflight 실패와 동일한 복구 경로다.

## 6. Doctor/진단 (Scope §10)

`devbot doctor`는 활성화된 저장소마다 `worktree_health[<repo>]` 항목을
보고한다: operator 현재 branch, worktree root, active/stale/conflicting
worktree 수. `conflicting`(Git이 모르는, worktree root 아래의 디스크
디렉터리)이 있으면 그 항목만 `FAIL`로 표시되지만 - 다른 startup check와
마찬가지로 - daemon 전체를 막는 `fatal` 조건은 아니다.

## 7. 기존 워크플로와의 호환성

`PollingService.prepare_workspace`는 선택적 의존성이다 - 주입하지 않으면
(테스트를 포함한 모든 기존 호출자) 이 Task 이전과 동일하게 operator
checkout 위에서 직접 Agent/Delivery/Review를 실행한다. `devbot.main`은 실제
배포에서 항상 `WorktreeManager.prepare`를 주입하므로, 운영 daemon은 항상
새 경로를 탄다. Task 027 이후 REVIEW Job도 linked PR 기준으로 준비된
worktree를 검증/사용하며, host operator checkout의 현재 branch나 미커밋
파일은 REVIEW 실행을 막지 않는다.

Task 027 workspace invariant: after `WorktreeManager.prepare()` returns a
`PreparedWorkspace`, all Agent roles must execute exclusively against
`PreparedWorkspace.repository`. IMPLEMENT, REVIEW, REWORK, Delivery,
Validation, and future Agent roles must not independently fall back to the
configured host repository. The host checkout is only a source for Git
worktree management such as fetch/add/list operations.

## 8. 범위 밖 (Out of Scope)

- Agent의 무제한 네트워크 접근
- Claude/Codex 승인 또는 sandbox 통제 우회
- Planner 모델 자동 실행
- 다중 Agent Failover
- Timeline 자동 기록
- 자동 Merge
- VPS/launchd/systemd 배포
- 같은 Task Branch를 동시에 수정하는 병렬 Job
