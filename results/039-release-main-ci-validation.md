# Task 039 Result: Fix Release Operator CI Validation for main push workflows

## 완료 내용

- `devbot.release_ops.gather_release_context()`가 `main` 커밋의 CI 검증 여부를
  판정하던 방식을 `GitHubClient.list_check_runs_for_ref()`(Check Runs API)에서
  `GitHubClient.list_workflow_runs()`(GitHub Actions Workflow Runs API)로
  교체했다.
- 새 판정 함수 `_target_commit_is_ci_validated()`는 다음 네 조건을 모두 만족하는
  CI 워크플로(`ci.yml`) 실행이 하나라도 있을 때만 `True`를 반환한다: `head_sha`가
  대상 커밋과 정확히 일치, `event == "push"`, `status == "completed"`,
  `conclusion == "success"`.
- `GitHubClient.list_workflow_runs()`에 `head_sha` 필터 매개변수를 추가해
  서버 사이드로 정확한 커밋만 조회한다(`event`/`created`와 동일한 패턴).
- Pull Request Check Run이나 다른 워크플로(특히 `release.yml`의 push 트리거 실행)의
  check run을 전혀 참조하지 않는다.
- API/인증 실패는 여전히 크래시가 아니라 명확한 블로커로 보고된다(Task 037이
  이미 확립한 패턴을 그대로 유지).
- `src/devbot/automerge.py`의 `list_check_runs_for_ref` 사용(PR head SHA의
  merge-전 CI 게이트)은 전혀 건드리지 않았다 - 그 용도는 Check Runs가 정확히
  맞는 사용처다.

## 근본 원인

실제 `hjlee83/devbot` 저장소에서 라이브로 재현/확인했다. 보고된 커밋
`d0fdb23b89d90fe15662413b5c141b374569b5f0`(PR #82 merge 커밋)은 정확히
`main`으로의 push 하나로 **두 워크플로**를 동시에 트리거한다: `CI`(`ci.yml`,
`verify` job)와 `Release`(`release.yml` - 이 워크플로도 `on: push: branches:
[main]`이기 때문). 기존 코드는 `GET .../commits/{sha}/check-runs`로 이 커밋의
**모든** check run을 가져와 전부 `conclusion == "success"`일 것을 요구했다:

```
$ gh api repos/hjlee83/devbot/commits/d0fdb23.../check-runs \
    --jq '.check_runs[]|{name,conclusion}'
{"name":"publish-release","conclusion":"skipped"}
{"name":"build-artifacts","conclusion":"skipped"}
{"name":"plan-release","conclusion":"failure"}
{"name":"validate-main","conclusion":"success"}
{"name":"verify","conclusion":"success"}
```

`CI` 워크플로의 `verify`는 성공했지만, `Release` 워크플로의 `plan-release` job이
실패(아래 참고)했고 `build-artifacts`/`publish-release`는 (publish=false라서)
정상적으로 skip됐다 - 이 둘 다 `all(conclusion == "success")`를 깨뜨려, 운영자가
실제로 관심 있는 `CI` 워크플로가 정상 통과했는데도 "검증 안 됨"으로 잘못
보고됐다.

```
$ gh run list --repo hjlee83/devbot --workflow CI \
    --commit d0fdb23... --json databaseId,event,status,conclusion,url
[{"conclusion":"success","event":"push","status":"completed", ...,
  "url":"https://github.com/hjlee83/devbot/actions/runs/29637320040"}]
```

**부수 발견 (이 Task 범위 밖, 별도 이슈):** `plan-release`가 실패한 이유를
로그로 확인했다 - `release_increment_for_pr`가 "merged main PR must have exactly
one release:* label"로 실패했다. PR #82는 **현재** `release:patch` 라벨을
갖고 있지만(`gh pr view 82 --json labels`로 확인), push 이벤트 처리 시점에
`gh api commits/$SHA/pulls`로 라벨을 읽는 순간에는 아직 라벨이 붙지 않았을
가능성이 있다 - push 트리거 자동 릴리스 파이프라인의 라벨 타이밍 경쟁 조건으로
보인다. `devbot release preview`의 CI 검증 로직을 고치면 더는 이 실패의
영향을 받지 않지만, `release.yml`의 자동 per-push 파이프라인 자체는 여전히
같은 이유로 실패할 수 있다 - 이 Task는 그 파이프라인을 고치지 않는다(범위 밖).

## 검증 소스

- **이전**: `GET /repos/{owner}/{repo}/commits/{sha}/check-runs` (해당 커밋의
  모든 워크플로의 모든 check run을 반환).
- **이후**: `GET /repos/{owner}/{repo}/actions/workflows/ci.yml/runs?event=push&head_sha=<sha>`
  (설정된 CI 워크플로 하나만의, 정확히 그 커밋을 대상으로 한, push로 트리거된
  실행만 반환).

## 주요 설계 결정

- **워크플로 파일명을 매개변수화했다** (`ci_workflow_file`, 기본값
  `CI_WORKFLOW_FILE = "ci.yml"`) - `RELEASE_WORKFLOW_FILE`과 동일한 기존
  패턴을 그대로 따랐다. 저장소가 CI 워크플로 파일명을 바꾸면 명시적으로
  override해야 한다(자동 탐지하지 않음 - 여러 워크플로 중 무엇이 "CI"인지
  추측하지 않기 위함).
- **`list_check_runs_for_ref` 자체는 삭제하지 않았다.** `src/devbot/automerge.py`의
  B2 자동 머지 게이트가 여전히 이를 사용하며(PR head SHA의 merge-전 체크
  확인 - Check Runs가 정확히 맞는 용도), 이 Task의 범위는 오직
  `release_ops.gather_release_context`의 검증 로직뿐이다.
- **`event == "push"`를 명시적으로 재확인한다** (서버 사이드 `event="push"`
  쿼리 필터에 더해). Workflow Runs API 응답을 신뢰하되, 방어적으로 클라이언트
  사이드에서도 `head_sha`/`event`/`status`/`conclusion` 네 조건을 전부
  재검사해, 모킹된 테스트나 API 동작 변화에도 안전하다.
- **API/인증 실패는 여전히 별도 사유로 구분된다** (`target_commit_validation_error`).
  이번에 새로 만든 게 아니라 Task 037이 이미 만든 패턴을 그대로 재사용했다 -
  검증 자체를 할 수 없는 것과 검증에 실패한 것은 다른 사실이다.

## 수정 파일

- `src/devbot/github_client.py`
- `src/devbot/release_ops.py`
- `tests/test_release_ops.py`
- `docs/00-roadmap.md`
- `tasks/039-release-main-ci-validation.md`
- `results/039-release-main-ci-validation.md`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-039-1 정확한 push 성공 인식 | `test_ci_validated_true_for_exact_push_success` |
| CP-039-2 pre-merge SHA의 PR 실행은 불인정 | `test_ci_validated_false_for_pr_only_success_at_premerge_sha` |
| CP-039-3 다른 커밋의 성공은 불인정 | `test_ci_validated_false_for_success_on_another_sha` |
| CP-039-4 매칭 없음/진행 중/실패 모두 fail closed | `test_ci_validated_false_when_no_run_found`, `test_ci_validated_false_for_queued_run`, `test_ci_validated_false_for_in_progress_run`, `test_ci_validated_false_for_failed_run`, `test_ci_validated_false_for_cancelled_run` |
| CP-039-5 API/인증 실패는 명확한 블로커 | `test_gather_release_context_reports_api_failure_as_validation_error` |
| CP-039-6 라벨/조건 충족 시 ready | `test_release_preview_is_ready_with_valid_ci_push_run_and_labels` |
| CP-039-7 Workflow Runs API 사용, Check Runs 미사용 | `test_gather_release_context_computes_commit_range_from_compare_api` (list_workflow_runs 호출 인자 및 list_check_runs_for_ref 미호출 검증) |
| CP-039-8 문서와 근거 | 본 Result, `docs/00-roadmap.md` |
| CP-039-9 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 648 passed
  (기존 638개 + 이번 Task에서 추가한 10개, `tests/test_release_ops.py`)

## 수동 검증 결과

`hjlee83/devbot` 실제 저장소를 대상으로, 보고된 정확한 커밋에 대해 수정 전/후를
직접 비교했다(읽기 전용, 아무것도 쓰지 않음). `devbot release publish`(실제
게시)는 실행하지 않았고 `v0.1.1`은 게시하지 않았다.

**수정 전** (`uv run devbot release preview`, 동일 커밋):

```
target_commit_validated: no
ready: no
  blocker: could not verify Check Runs for d0fdb23...: GitHub authentication
    failed: Resource not accessible by personal access token
```

(이 저장소에 배포된 `GITHUB_TOKEN`이 Check Runs 엔드포인트에 대해 별도의 403
권한 문제를 갖고 있어 - `project_devbot_token_missing_checks_permission`
메모리 기록 참고 - 사용자가 보고한 정확한 문구("is not CI-validated")와는
다르게 나타났지만, 근본 원인은 동일하다: check-runs API 자체가 이 용도에
부적절하다. `gh api`로 다른(권한 문제 없는) 토큰을 사용해 직접 확인한 결과
위 "근본 원인" 절의 5개 check run(성공/실패/skip 혼재)이 실제로 존재함을
확인했고, 이는 사용자가 보고한 증상과 정확히 일치하는 근본 원인이다.)

**수정 후** (동일 커밋, 동일 명령):

```
target_commit_validated: yes
ready: no
  blocker: operator checkout has uncommitted changes (git status --porcelain is non-empty)
```

남은 유일한 blocker는 이 Task를 구현 중인 작업 트리 자체가 dirty하기 때문이며
(커밋되지 않은 변경사항 존재), CI 검증과는 무관하다 - 커밋 후에는 이 blocker도
사라진다(CP-039-6 자동화 테스트로 별도 확인).

## 남은 TODO와 제한

- **`release.yml`의 push 트리거 자동 릴리스 파이프라인의 라벨 타이밍 경쟁
  조건은 고치지 않았다.** "근본 원인" 절에 기록한 대로, `plan-release` job이
  실제로 실패하는 원인이며, `devbot release preview`의 CI 검증에는 더 이상
  영향을 주지 않지만 자동 파이프라인 자체는 여전히 실패할 수 있다. 후속
  Task가 필요하다.
- **`.env`의 `GITHUB_TOKEN`이 Check Runs 엔드포인트에 대해 갖고 있는 403 권한
  문제**(`project_devbot_token_missing_checks_permission` 메모리 참고)는
  `list_check_runs_for_ref`를 여전히 쓰는 B2 자동 머지 게이트에는 계속 영향을
  줄 수 있다 - 이 Task의 범위 밖이다.
- CI 워크플로 파일명(`ci.yml`)은 여전히 고정 상수(오버라이드 가능)다 - 이
  저장소에 워크플로가 여러 개 추가되면 재검토가 필요할 수 있다.

## 위험 요소

- 이 Task는 "CI 검증됨"의 정의를 바꿨다(Check Runs 집계 → 특정 워크플로의
  push 실행). 이 저장소의 유일한 CI 워크플로는 `ci.yml`이므로 위험은 낮지만,
  향후 워크플로 구성이 크게 바뀌면(예: CI 워크플로 파일 이름 변경, 여러 필수
  워크플로 도입) `ci_workflow_file` 매개변수 사용법을 재검토해야 한다.
- `release.yml`의 라벨 타이밍 경쟁 조건(위 "남은 TODO" 참고)은 여전히 실제
  프로덕션 동작에 영향을 준다 - `devbot release preview`는 더 이상 이로 인해
  잘못된 판정을 내리지 않지만, 자동 per-push 릴리스 자체는 여전히 조용히
  실패할 수 있다.
