# Task 051 Result: Release Orchestration

## 구현 요약

`src/devbot/release_orchestration.py`를 신규 작성해, Task 047의
`ReleaseRecommendation`, Task 048의 `plan_release_preparation`/
`prepare_release`, Task 050의 `resolve_release_publish_strategy`, 그리고
Task 037의 `publish_release`(workflow) 또는 Task 049의
`publish_prepared_release`(direct) 중 정확히 하나를 정책 로직 중복 없이
한 명령으로 엮었다. CLI로는 `devbot release run --level
major|minor|patch|none [--repo] [--notes-file] [--dry-run]`을 추가했다.

타입 모델:
- `ReleaseRunStage(StrEnum)`: `recommendation`/`preparation`/
  `strategy_resolution`/`workflow_publish`/`direct_publish` - Contract가
  요구한 5개 값 그대로.
- `ReleaseRunOutcome(StrEnum)`: `workflow_published`/`direct_published`/
  `prepared_pending_commit`.
- `ReleaseRunPlan` (frozen dataclass): repository/recommendation/
  current_version/target_version/effective_strategy/preparation_required/
  publish_route/direct_notes_available.
- `ReleaseRunResult` (frozen dataclass): plan/outcome/preparation/
  workflow_outcome/direct_result.
- 오류 계층: `ReleaseOrchestrationError`(base, 모든 인스턴스가 `.stage`를
  가짐) → `NoReleaseRequiredError`, `MissingDirectReleaseNotesError`,
  `ReleaseRunStageError`(원인 예외를 `from exc`로 체이닝).

## 구현 중 발견한 구조적 충돌과 해결 과정

설계 도중 실제 코드와 대조해 구조적 충돌을 하나 발견했고, 이를 사용자에게
한글로 보고한 뒤 승인을 받아 진행했다(대화 로그 참고). Task 049는 스스로
"pyproject.toml/uv.lock을 자신의 관점에서 읽기 전용으로 취급한다"고
설계돼 있어(`docs/07-decisions.md` 2026-07-19 Task 049 항목),
`preview_release_publish`/`publish_prepared_release`는 커밋되지 않은
로컬 변경이 있으면 무조건 `DirtyWorktreeError`로 거부한다. 그런데
Task 048의 `prepare_release()`는 major/minor/patch 어떤 추천값이든
`old_version != new_version`이 항상 성립하므로, 호출할 때마다 그 두
파일을 커밋 안 된 상태로 남긴다. 즉 direct 경로에서 "prepare → 곧바로
publish"를 한 프로세스 안에서 이어붙이면, 게시 시도는 구조적으로 항상
실패한다.

**1차 시도(폐기)**: "먼저 게시를 시도하고, dirty로 실패하면 그때
prepare"하는 순서로 구현했었다. 임시 Git 저장소(진짜 `git init`/`git
push`)로 직접 스모크 테스트를 돌리며 검증하던 중, 실제 버그를 발견했다 -
체크아웃이 애초에 깨끗한 상태로 시작하면, 게시가 `recommendation`을
완전히 무시하고 **아직 올리지 않은 현재 버전을 그대로 게시**해버렸다
(예: 1.2.3에서 `--level minor`를 줘도 1.3.0이 아니라 1.2.3을 게시).

**최종 설계**: `prepare_release()`를 항상 먼저 호출한 뒤에만 게시를
시도하도록 순서를 뒤집었다. 이러면 게시가 성공할 때는 언제나
`recommendation`이 계산한 정확한 목표 버전만 게시되고, 잘못된(아직
올리지 않은) 버전을 게시할 가능성 자체가 없어진다. 대신, `prepare_release()`
직후의 게시는 (앞서 설명한 이유로) 거의 항상 `DirtyWorktreeError`/
`StaleMainError`로 거부된다 - 이를 실패가 아니라 `PREPARED_PENDING_COMMIT`
이라는 정상적인, 실행 가능한 결과로 보고한다: 준비된 파일은 디스크에
남아 있고, 아무것도 게시되지 않았으며, 운영자가 커밋·push한 뒤 기존
`devbot release publish-prepared`(Task 049, 변경 없음)로 게시를 완료하면
된다 - `release run`을 다시 실행하면 버전이 한 번 더 올라가므로 그렇게
하지 않도록 CLI 출력에 명시했다.

**main에 직접 commit+push하는 새 권한을 추가하는 방안도 검토했지만
채택하지 않았다.** Task 051의 Contract/Specification 어디에도 이 능력을
언급하지 않고, self-hosted 봇이 자신의 release 히스토리에 직접 쓰는
파급력이 큰 새 능력이라 이번 태스크 범위에서 조용히 추가하는 것은
부적절하다고 판단했다.

**결과적으로, direct 전략의 실제(non-dry-run) 실행은 현재 한 번의 호출로
`DIRECT_PUBLISHED`까지 도달하지 못한다** - `prepare_release()`가 항상
디스크를 변경하므로, 그 직후의 게시 시도는 구조적으로 항상 거부된다.
`ReleaseRunOutcome.DIRECT_PUBLISHED`는 여전히 정의되고 테스트되지만
(`publish_prepared_release`를 mock한 orchestration-wiring 레벨 테스트로
검증), 실제 트리거 조건은 이번 태스크가 갖지 않은 능력(commit+push)을
필요로 한다. 이 내용은 `docs/07-decisions.md`(2026-07-19 Task 051 항목)에
투명하게 기록했다.

**workflow 전략은 이 제약이 없다.** Task 037의 버전은 로컬 파일이 아니라
Git/PR 히스토리로 독립적으로 계산되므로, `run_release`는 workflow
경로에서는 `prepare_release()`를 전혀 호출하지 않는다(불필요한 데다,
호출하면 아무 이득 없이 체크아웃만 지저분해진다). 그래서 workflow
전략의 실제 실행은 한 번의 호출로 끝까지 완료된다.

## 실행 순서 (Real Execution Order)

`run_release`는 다음 순서로 진행한다:

1. `build_release_run_plan` 호출 - 내부적으로: recommendation이 `none`이면
   즉시 `NoReleaseRequiredError`(파일시스템 접근 전); `plan_release_preparation`
   (읽기 전용, Task 048 재사용)으로 현재/목표 버전 계산; `resolve_release_publish_strategy`
   (Task 050 재사용)로 유효 전략과 게시 경로 결정.
2. direct 경로인데 notes가 없으면(공백만 있어도) `MissingDirectReleaseNotesError` -
   `prepare_release()` 호출보다 먼저.
3. **workflow 경로**: `publish_release`(Task 037, 기존 함수 그대로) 호출 →
   성공 시 `WORKFLOW_PUBLISHED`, 실패 시 `ReleaseRunStageError(stage=workflow_publish)`로
   원인 예외를 체이닝해 래핑.
4. **direct 경로**: `prepare_release`(Task 048) 정확히 한 번 호출 → 성공 시
   `publish_prepared_release`(Task 049) 호출 시도 → `DirtyWorktreeError`/
   `StaleMainError`면 `PREPARED_PENDING_COMMIT`(정상 결과), 그 외 실패면
   `ReleaseRunStageError(stage=direct_publish)`, 성공하면 `DIRECT_PUBLISHED`.

"준비가 실패하면 게시 함수는 절대 실행되지 않는다"는 두 경로 모두
try/except의 순차 구조로 보장된다(준비 예외는 즉시 raise, 게시 단계에
도달하지 않음).

## Dry-Run 증거 (쓰기 없음)

`build_release_run_plan`은 `plan_release_preparation`(읽기 전용)과
`resolve_release_publish_strategy`(순수 함수, I/O 없음)만 호출한다 -
GitHub client를 전혀 생성하지 않고 Git/파일 쓰기가 없다. CLI의
`--dry-run`은 `build_release_run_plan`만 호출하고 `run_release`는 절대
호출하지 않는다(`src/devbot/main.py`의 `_run_release_run_command`).

검증:
- `tests/test_main.py::test_release_run_dry_run_workflow_strategy` -
  dry-run 시 `GitHubClient`/`GitHubWriteClient`가 생성되지 않음을 mock으로
  확인.
- `tests/test_main.py::test_release_run_does_not_acquire_daemon_lock` -
  `ProcessLock`도 생성되지 않음.
- direct 전략에서 notes가 없으면 dry-run은 거부하지 않고 "blocker"로만
  보고한다(`test_release_run_dry_run_direct_strategy_reports_notes_blocker`).
- 실제 devbot 저장소 자체를 대상으로 dry-run CLI를 직접 실행해
  `pyproject.toml`/`uv.lock`이 실행 전후로 `git status --porcelain` 기준
  전혀 변경되지 않았음을 수동으로도 확인했다.

## 두 게시 경로 (Both Strategy Paths)

- **workflow**: `test_run_release_workflow_route_success`,
  `test_run_release_workflow_route_never_writes_local_files`(진짜 git
  저장소, prepare_release가 전혀 호출되지 않고 파일이 실행 전후로
  byte-identical),
  `test_run_release_workflow_route_real_repo_leaves_files_untouched`(진짜
  git 저장소, `git status --porcelain`이 실행 후에도 빈 문자열).
- **direct**: `test_run_release_direct_route_success`(orchestration-wiring
  레벨, prepare_release/publish_prepared_release 각각 정확히 한 번 호출),
  `test_run_release_direct_route_real_prepare_never_publishes_prematurely`(진짜
  git 저장소 - 앞서 발견한 버그의 회귀 테스트, `1.2.3`→`1.3.0` 정확히
  준비되고 `create_release`는 호출되지 않음을 확인),
  `test_run_release_direct_route_real_prepare_leaves_checkout_dirty`(진짜
  git 저장소, 준비 후 체크아웃이 dirty함을 확인 - `PREPARED_PENDING_COMMIT`이
  왜 정상 결과인지 뒷받침).
- **상호 배타성**: `test_run_release_exactly_one_publish_route_ever_called`
  가 `workflow`/`direct` 두 상태를 파라미터화해, 선택된 경로가 아닌
  쪽의 게시 함수가 절대 호출되지 않음을 직접 검증.

## 실패/복구 동작 (Failure and Recovery Behavior)

- `none` recommendation → `NoReleaseRequiredError`(stage=recommendation),
  파일시스템 접근 전.
- 잘못된 `publish_strategy` 값 → `ReleaseRunStageError(stage=strategy_resolution)`,
  원인 예외 `InvalidReleasePublishStrategyError` 체이닝.
- 버전 소스 불일치/손상된 버전 문자열 → `ReleaseRunStageError(stage=preparation)`,
  원인 예외 `VersionSourceMismatchError`/`MalformedProjectVersionError` 체이닝.
- direct 전략에서 notes 누락 → `MissingDirectReleaseNotesError(stage=direct_publish)`,
  `prepare_release()` 호출 전.
- workflow 게시 실패(`ReleaseOpsError` 등) → `ReleaseRunStageError(stage=workflow_publish)`,
  원인 예외 체이닝.
- direct 게시 실패(`ConflictingTagError` 등, dirty/stale 제외) →
  `ReleaseRunStageError(stage=direct_publish)`, 원인 예외 체이닝.
- direct 게시가 dirty/stale로 거부 → 예외가 아니라
  `PREPARED_PENDING_COMMIT`(정상 결과, 위 "구조적 충돌" 절 참고).

CLI는 `ReleaseOrchestrationError` 하나만 잡으면 된다 - 모든 하위 실패가
이미 이 base class로 래핑되므로, 다른 release 명령들처럼 여러 예외
타입을 나열할 필요가 없다(`_run_release_run_command`).

## 테스트 결과

```
$ uv run devbot specification validate --task 51
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1113 passed in 73.19s
```

- `tests/test_release_orchestration.py` (신규, 30개): plan 구성(추천값별
  3개 파라미터화), `none` 거부, workflow/direct 경로 선택, notes 가용성
  보고(공백만 있는 경우 포함), 잘못된 전략/버전 불일치/손상된 버전이
  올바른 stage로 래핑되는지, `run_release`의 각 경로 성공/실패/
  prepared_pending_commit, 준비 실패 시 게시가 전혀 호출되지 않음,
  상호 배타성 매트릭스, 진짜 Git 저장소를 쓰는 3개의 end-to-end 테스트
  (앞서 발견한 버그의 회귀 테스트 포함), 오류 계층 검증.
- `tests/test_main.py` (+9): dry-run 출력(workflow/direct, notes
  blocker), `none` 실패 종료 코드, notes 파일 누락 실패, workflow/direct
  성공 출력, `prepared_pending_commit` 다음 단계 안내 출력, stage 오류
  종료 코드, daemon lock 미획득.
- 전체 스위트 1113개(직전 태스크 종료 시점 1074개 + 이번 태스크 39개)
  회귀 없이 통과.

## 범위 제외 사항

Contract/Specification이 명시한 대로 다음은 이번 태스크에 포함하지
않았다: 여러 Contract/Issue/PR/라벨을 종합한 추천값 자동 계산, Git
히스토리로부터의 release notes 생성, release PR 생성/승인/병합, 패키지
배포, 기존 release 명령들의 공개 동작 변경, 실패한 release의 자동
재시도, review-loop/workflow-engine/agent-dispatch 변경.

## 수정/추가 파일

- `src/devbot/release_orchestration.py` (신규)
- `src/devbot/main.py` (`release run` 서브커맨드 + 핸들러)
- `tests/test_release_orchestration.py` (신규, 30개)
- `tests/test_main.py` (+9)
- `specifications/051-release-orchestration.md` (canonical 8-섹션
  구조로 재구성)
- `docs/07-decisions.md`, `docs/00-roadmap.md` (Task 051 항목 추가)
