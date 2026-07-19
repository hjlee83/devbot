# Task 051 Result: Release Orchestration

## 구현 요약

`src/devbot/release_orchestration.py`를 신규 작성해, Task 047의
`ReleaseRecommendation`, Task 048의 `plan_release_preparation`/
`prepare_release`, Task 050의 `resolve_release_publish_strategy`, 그리고
Task 037의 `publish_release`(workflow) 또는 Task 049의
`publish_prepared_release`(direct) 중 정확히 하나를 정책 로직 중복 없이
한 명령으로 엮었다. CLI로는 `devbot release run --level
major|minor|patch|none [--repo] [--notes-file] [--dry-run]`을 추가했다.

이 문서는 PR #108의 hjlee83 리뷰에서 발견된 두 가지 정합성 문제를 수정한
**최종 설계**를 기록한다. 최초 구현의 설계 시행착오(1차 시도의 폐기,
그로부터 얻은 교훈)는 `docs/07-decisions.md`의 2026-07-19 Task 051
항목(구 버전, 현재는 최종 설계로 교체됨)에 남아 있던 내용을 이번에
대체했다.

타입 모델:
- `ReleaseRunStage(StrEnum)`: `recommendation`/`preparation`/
  `strategy_resolution`/`workflow_publish`/`direct_publish` - Contract가
  요구한 5개 값 그대로.
- `ReleaseRunOutcome(StrEnum)`: `workflow_published`/`direct_published`/
  `prepared_pending_commit`.
- `ReleaseRunPlan` (frozen dataclass): repository/recommendation/
  current_version/target_version/effective_strategy/preparation_required/
  publish_route/direct_notes_available/**recommendation_conflict**(신규).
- `ReleaseRunResult` (frozen dataclass): plan/outcome/preparation/
  workflow_outcome/direct_result.
- 오류 계층: `ReleaseOrchestrationError`(base, 모든 인스턴스가 `.stage`를
  가짐) → `NoReleaseRequiredError`, `MissingDirectReleaseNotesError`,
  `ReleaseRunStageError`(원인 예외를 `from exc`로 체이닝).

## 리뷰에서 발견된 문제와 수정 (2라운드)

### 문제 1: workflow 경로가 `--level`을 무시함

Task 037의 `publish_release`/`fetch_release_preview`는 merged PR 라벨
히스토리로 자체적으로 release increment를 계산하고, `pyproject.toml`을
전혀 읽지 않는다. 최초 구현은 workflow 경로에서 Task 048 준비를
건너뛰고, plan의 `target_version`을 로컬 파일 기준(Task 048 로직)으로만
계산했다 - 그 결과 dry-run이 보여주는 계획과 실제 게시 결과가 서로
어긋날 수 있었다. `devbot release run --level patch`를 실행해도, 실제로는
merged PR 히스토리가 minor나 major를 가리키면 그 버전이 게시되거나,
아무 PR도 없으면 거부됐다 - `--level`이 사실상 아무 영향도 주지 못했다.

**수정**: `build_release_run_plan`이 workflow 경로에서 실제로 게시될
`ReleasePreview`를 미리 계산해(`fetch_release_preview`), plan의
`current_version`/`target_version`을 `preview.previous_version`/
`preview.next_version`에서 가져온다 - Task 048의 로컬 계산이 아니라
Task 037이 실제로 게시할 값 그대로. 계산된 `preview.increment`가 요청한
`recommendation`과 다르면(또는 게시할 PR이 아예 없으면)
`recommendation_conflict` 필드에 이유를 담는다 - dry-run은 이를 blocker로
보여주고, 실제 실행(`run_release`)은 이 필드가 설정돼 있으면 어떤 쓰기도
하기 전에 거부한다. workflow 경로도 이제 `prepare_release()`를 정확히
한 번 호출한다 - Task 037이 그 파일들을 실제로 소비하지는 않지만,
Contract의 "Task 048 준비를 정확히 한 번 실행"이라는 요구사항을 두 경로
모두에서 문자 그대로 만족시키기 위함이다.

검증: `test_run_release_workflow_route_real_repo_recommendation_governs_dispatch`가
진짜 임시 Git 저장소를 대상으로, computed increment가 `minor`인데
`--level patch`를 요청하면 어떤 쓰기도 없이 거부됨을, 그리고 `--level
minor`(일치)는 실제로 dispatch까지 진행됨을 직접 검증한다.

### 문제 2: `PREPARED_PENDING_COMMIT`이 완성 계약을 충족하지 못함

최초 구현은 direct 경로의 `target_version`을 로컬 파일 기준
(`plan_release_preparation`)으로 계산했다. 그런데 `calculate_next_version`은
항상 "현재 로컬 버전 + 1 증가"를 계산하므로, 첫 호출에서
`prepare_release()`가 로컬 파일을 바꾸고 나면, **두 번째 호출은 그 바뀐
로컬 버전을 "현재"로 보고 또 한 단계 더 bump된 새 target을 계산**해버린다
- "이 체크아웃이 이미 이 target을 위해 준비됐는지"를 판단할 방법이
원천적으로 없었다. 그 결과 direct 경로의 실제 실행은 몇 번을 재실행해도
`DIRECT_PUBLISHED`에 도달할 수 없었고, `PREPARED_PENDING_COMMIT`을 "정상
결과"라고 부르는 것은 사실상 "이 명령은 direct 전략에서 절대 완결되지
않는다"는 뜻이었다 - Task 051의 "한 번에 준비→게시까지"라는 완성 계약을
충족하지 못했다.

**수정**: direct 경로의 `current_version`/`target_version`을 로컬 파일이
아니라 **`build_release_status(...).latest_stable_version`**(GitHub
Releases API 기준 - `devbot release status`가 읽는 것과 같은 소스)에
앵커링했다. 이 값은 로컬에서 `prepare_release()`를 몇 번을 실행해도
바뀌지 않고, 실제로 GitHub Release가 게시돼야만 바뀐다. `run_release`는
로컬 체크아웃의 현재 버전(`read_current_version`)과 dirty 여부
(`local_checkout_is_dirty`)를 이 안정적인 target과 비교한다:

- **이미 일치**(로컬이 이미 target 버전이고 dirty하지 않음): `prepare_release()`를
  아예 건너뛰고 곧바로 `publish_prepared_release()`를 시도한다 - 즉시
  `DIRECT_PUBLISHED`.
- **불일치**: `prepare_release()`를 정확히 한 번 호출한 뒤
  `PREPARED_PENDING_COMMIT`을 보고한다. 운영자가 준비된 파일을
  커밋·push한 뒤 **같은 recommendation으로 다시 실행**하면, 이번엔
  로컬이 이미 target과 일치하므로(위 "latest_stable_version" 값이
  그대로이므로 target도 그대로) 첫 번째 분기를 타서 진짜로
  `DIRECT_PUBLISHED`까지 도달한다 - 중복 bump 없이.

방어적으로, `prepare_release()`가 실제로 계산한 `new_version`이 plan의
`target_version`과 다르면(오래되거나 다른 recommendation으로 준비된
채 방치된 로컬 상태 등 드문 경우) 게시를 시도하지 않고
`preparation` stage 오류로 즉시 실패한다 - plan이 약속한 것과 다른
버전을 조용히 게시하는 일은 없다.

**main에 직접 commit+push하는 새 권한을 추가하는 방안은 리뷰 중 다시
검토했지만 또 채택하지 않았다.** Contract/Specification 어디에도 근거가
없고, self-hosted 봇이 자신의 release 히스토리에 직접 쓰는 파급력이 큰
새 능력이기 때문이다. 대신 기존에 있던 "두 번 재실행하는" 경로 자체가
실제로 올바르게 동작하도록 고쳤다 - 새 능력을 추가해 한 번에 끝내는
대신, 기존 경로의 버그를 고쳐 두 번째 실행이 진짜로 완결되게 했다.

검증: `test_run_release_direct_route_real_resume_reaches_direct_published_without_double_bump`가
진짜 임시 Git 저장소 + 진짜 bare remote를 대상으로, 첫 실행이
`PREPARED_PENDING_COMMIT`(1.2.3→1.3.0 준비)으로 끝나고, 커밋·push 후
같은 recommendation으로 두 번째 실행하면 `plan.preparation_required is
False`가 되고 실제로 `publish_prepared_release`/
`write_client.create_release`까지 정확히 한 번 호출되어
`DIRECT_PUBLISHED`에 도달함을 end-to-end로 직접 검증한다.

### 문제 3 (재리뷰): `prepare_release()` 실제 출력과 `preview`의 불일치를 검사하지 않음

문제 1의 수정(계산된 increment와 recommendation 비교)만으로는 충분하지
않았다. `prepare_release()`는 로컬 파일 기준으로, `preview`는 "가장 최근
게시된 Release" 기준으로 **서로 독립적으로** target을 계산하기 때문에,
increment(예: 둘 다 "patch")는 같아도 정확한 버전은 다를 수 있다 - 로컬
파일이 어떤 이유로든 이미 최근 게시본보다 앞서 있으면 그렇다. 이건
이론적인 우려가 아니었다: 리뷰 시점에 실제로 이 저장소의
`pyproject.toml`은 `0.1.2`였는데 가장 최근 게시된 GitHub Release는
`v0.1.1`이었다.

**수정**: `run_release`가 `prepare_release()` 호출 직후, 그 실제 결과
(`preparation_result.new_version`)를 dispatch 직전에 다시 읽은
`preview.next_version`과 비교한다. 다르면 어떤 dispatch도 하지 않고
`preparation` stage 오류로 즉시 실패한다(원인 예외 체이닝은 없음 - 이건
하위 실패를 감싸는 게 아니라 사후 정합성 검사이므로).

검증: `test_run_release_workflow_route_local_preparation_diverges_from_published_baseline`
(mock 기반, 두 baseline이 다를 때 `publish_release`가 호출되지 않음을
확인)과 `test_run_release_workflow_route_real_repo_local_ahead_of_published
_baseline_refuses`(진짜 Git 저장소로, 이 저장소에서 실제 관찰된 것과
같은 드리프트 패턴을 재현해 검증).

## 실행 순서 (Real Execution Order)

`run_release`는 다음 순서로 진행한다:

1. `build_release_run_plan` 호출 - recommendation이 `none`이면 즉시
   `NoReleaseRequiredError`; `plan_release_preparation`(로컬 파일 검증,
   Task 048 재사용)으로 로컬 상태 유효성 확인; `resolve_release_publish_strategy`
   (Task 050 재사용)로 유효 전략과 게시 경로 결정; workflow 경로면
   `fetch_release_preview`로, direct 경로면 `build_release_status`로
   GitHub 앵커링된 current/target 버전을 계산.
2. direct 경로인데 notes가 없으면(공백만 있어도) `MissingDirectReleaseNotesError`.
3. **workflow 경로**: 게시 직전에 `ReleasePreview`를 다시 한 번 fetch하고
   (체크아웃이 확실히 clean한 시점에), 요청한 recommendation과 여전히
   일치하는지 재확인 → 불일치면 거부 → `prepare_release`(Task 048) 정확히
   한 번 → `publish_release`(Task 037, 미리 fetch한 preview를 그대로
   넘겨 재검사 없이 dispatch).
4. **direct 경로**: 로컬 상태가 이미 target과 일치하면 `prepare_release`를
   건너뛰고 곧바로 게시 시도, 아니면 `prepare_release` 정확히 한 번 →
   `publish_prepared_release`(Task 049) 시도 → `DirtyWorktreeError`/
   `StaleMainError`면 `PREPARED_PENDING_COMMIT`(정상 결과), 그 외 실패면
   `ReleaseRunStageError(stage=direct_publish)`, 성공하면 `DIRECT_PUBLISHED`.

"준비가 실패하면 게시 함수는 절대 실행되지 않는다"는 두 경로 모두
try/except의 순차 구조로 보장된다.

## Dry-Run 증거

`build_release_run_plan`은 `plan_release_preparation`(읽기 전용)과
`resolve_release_publish_strategy`(순수 함수)에 더해, 이제 **읽기 전용
GitHub 호출**(`fetch_release_preview` 또는 `build_release_status`)도
수행한다 - 어떤 쓰기도 하지 않는다. 이는 Specification이 명시적으로
허용한 예외("Do not create network clients during a dry-run unless an
existing read-only plan absolutely requires one")에 해당한다 - 이
호출이 없으면 plan이 실제 게시 결과와 어긋날 수 있다는 것이 바로 이번
리뷰에서 발견된 문제였으므로, 이 호출은 "절대적으로 필요"한 경우다.
`GitHubWriteClient`는 dry-run 중에는 여전히 전혀 생성되지 않는다.

검증:
- `tests/test_main.py::test_release_run_dry_run_workflow_strategy` -
  `GitHubClient`는 생성되지만(mock으로 확인) `GitHubWriteClient`는
  생성되지 않음.
- `test_release_run_does_not_acquire_daemon_lock` - `ProcessLock`도
  생성되지 않음.
- `test_release_run_dry_run_workflow_reports_recommendation_conflict_blocker`,
  `test_release_run_dry_run_direct_strategy_reports_notes_blocker` -
  두 blocker 모두 dry-run에서 거부가 아니라 정보로만 보고됨.

## 두 게시 경로 (Both Strategy Paths)

- **workflow**: `test_run_release_workflow_route_success_calls_prepare_and_publish`
  (prepare_release + publish_release 각각 정확히 한 번, 미리 fetch한
  preview 그대로 사용), `test_run_release_workflow_route_conflict_refuses_before_any_write`
  (increment 불일치 시 prepare_release/publish_release 모두 호출 안 됨),
  `test_run_release_workflow_route_real_repo_recommendation_governs_dispatch`
  (진짜 git 저장소, 불일치 거부 + 일치 시 실제 dispatch 둘 다 검증).
- **direct**: `test_run_release_direct_route_already_prepared_skips_prepare_and_publishes`,
  `test_run_release_direct_route_not_yet_prepared_calls_prepare_once`,
  `test_run_release_direct_route_stale_main_reports_prepared_pending_commit`,
  `test_run_release_direct_route_inconsistent_local_state_wraps_stage_error`
  (방어적 일관성 검사), `test_run_release_direct_route_real_resume_reaches
  _direct_published_without_double_bump`(진짜 git 저장소, 2단계 재실행
  전체 시나리오).
- **상호 배타성**: `test_run_release_exactly_one_publish_route_ever_called`
  가 `workflow`/`direct` 두 상태를 파라미터화해, 선택된 경로가 아닌
  쪽의 게시 함수가 절대 호출되지 않음을 검증.

## 실패/복구 동작

- `none` recommendation → `NoReleaseRequiredError(stage=recommendation)`.
- 잘못된 `publish_strategy` 값 → `ReleaseRunStageError(stage=strategy_resolution)`.
- 버전 소스 불일치/손상된 버전 문자열 → `ReleaseRunStageError(stage=preparation)`.
- direct 전략에서 notes 누락 → `MissingDirectReleaseNotesError(stage=direct_publish)`,
  `prepare_release()` 호출 전.
- workflow 경로 GitHub 조회 실패 → `ReleaseRunStageError(stage=workflow_publish)`.
- workflow 경로 recommendation 불일치 → `ReleaseRunStageError(stage=workflow_publish)`,
  어떤 쓰기도 하기 전.
- workflow 게시 실패(`ReleaseOpsError` 등) → `ReleaseRunStageError(stage=workflow_publish)`.
- direct 경로 GitHub 조회 실패 → `ReleaseRunStageError(stage=direct_publish)`.
- direct 게시 실패(`ConflictingTagError` 등, dirty/stale 제외) →
  `ReleaseRunStageError(stage=direct_publish)`.
- direct 로컬 상태가 plan과 불일치 → `ReleaseRunStageError(stage=preparation)`
  (방어적 일관성 검사).
- direct 게시가 dirty/stale로 거부(아직 준비 안 됐거나 커밋 안 됨) →
  예외가 아니라 `PREPARED_PENDING_COMMIT`(정상 결과).

CLI는 `ReleaseOrchestrationError` 하나만 잡으면 된다.

## 테스트 결과

```
$ uv run devbot specification validate --task 51
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1120 passed in 69.83s
```

- `tests/test_release_orchestration.py` (36개): plan 구성(workflow/direct
  두 경로의 GitHub 앵커링된 버전 소스, recommendation 불일치/부재 blocker
  보고, `preparation_required`가 이미 준비된 상태를 올바르게 반영),
  `none` 거부, 잘못된 전략/버전 불일치/손상된 버전 stage 래핑, GitHub
  조회 실패 stage 래핑, `run_release`의 각 경로 성공/실패/
  prepared_pending_commit/일관성 검사, 준비 실패 시 게시 미호출, 상호
  배타성 매트릭스, `prepare_release()`와 `preview` 간 baseline 불일치
  검사(mock + 진짜 저장소), 진짜 Git 저장소를 쓰는 4개의 end-to-end
  테스트(세 리뷰 발견 사항 각각의 회귀 테스트), 오류 계층 검증.
- `tests/test_main.py` (+10): dry-run 출력(workflow/direct, notes
  blocker, recommendation_conflict blocker 신규), `none` 실패 종료
  코드, notes 파일 누락 실패, workflow/direct 성공 출력,
  `prepared_pending_commit` 다음 단계 안내 출력, stage 오류 종료 코드,
  daemon lock 미획득, dry-run 시 GitHubClient는 생성되지만
  GitHubWriteClient는 생성되지 않음.
- 전체 스위트 1120개 회귀 없이 통과.

## 범위 제외 사항

Contract/Specification이 명시한 대로 다음은 이번 태스크에 포함하지
않았다: 여러 Contract/Issue/PR/라벨을 종합한 추천값 자동 계산, Git
히스토리로부터의 release notes 생성, release PR 생성/승인/병합, 패키지
배포, 기존 release 명령들의 공개 동작 변경, 실패한 release의 자동
재시도, review-loop/workflow-engine/agent-dispatch 변경. main에 직접
commit/push하는 새 권한도 세 차례(1차 구현, 1차 리뷰, 재리뷰) 검토 후
채택하지 않았다(위 "리뷰에서 발견된 문제와 수정" 참고).

## 아키텍처 결정 업데이트

`docs/07-decisions.md`의 2026-07-19 Task 051 항목을 리뷰 수정을 반영해
전면 교체하고, 재리뷰에서 발견된 세 번째 문제(간 `prepare_release()`와
`preview` baseline 불일치)에 대한 절을 추가했다 - 세 문제의 정확한 원인,
수정 방법, 왜 새 git 쓰기 권한을 다시 검토하고도 채택하지 않았는지를
기록했다. `docs/00-roadmap.md`의 Task 051 항목도 같은 내용으로 갱신했다.

## 수정/추가 파일 (리뷰 수정 포함 최종)

- `src/devbot/release_orchestration.py` (신규, 두 차례 리뷰 수정으로
  전면 재작성)
- `src/devbot/main.py` (`release run` 서브커맨드 + 핸들러, dry-run도
  GitHubClient 생성하도록 수정)
- `tests/test_release_orchestration.py` (신규, 36개, 두 차례 리뷰
  수정으로 전면 재작성)
- `tests/test_main.py` (+10)
- `specifications/051-release-orchestration.md` (canonical 8-섹션
  구조로 재구성)
- `docs/07-decisions.md`, `docs/00-roadmap.md` (Task 051 항목 - 리뷰
  수정 반영해 교체)
