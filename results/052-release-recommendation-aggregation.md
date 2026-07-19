# Task 052 Result: Release Recommendation Aggregation

## 구현 요약

`src/devbot/release_recommendation_aggregation.py`를 신규 작성해, 최근
stable GitHub Release 이후 병합된 모든 Task PR의 Contract를 집계해 하나의
권위 있는 `ReleaseRecommendation`을 계산한다. CLI로는 읽기 전용
`devbot release recommend [--repo owner/repo]`를 추가했다 — write client를
한 번도 생성하지 않고, 로컬 파일을 전혀 건드리지 않는다.

타입 모델:
- `ReleaseRecommendationEvidence`: pr_number/pr_url/merge_commit_sha/
  merged_at/task_number/contract_path/recommendation/classification_reasons.
- `ExcludedPullRequest` + `PullRequestExclusionReason(StrEnum)`
  (`not_a_task_branch`/`non_default_base_branch`): Task PR이 아니라고
  판단된 PR을 조용히 버리지 않고 명시적·타입 있는 사유와 함께 기록.
- `ReleaseRecommendationAggregation`: repository/boundary_version/
  boundary_tag/boundary_commit/inspected_pr_count/evidence/excluded/
  recommendation.
- 오류 계층: `ReleaseRecommendationAggregationError`(base) →
  `MissingContractError`, `AmbiguousContractError`, `InvalidContractError`
  (원인 예외를 `from exc`로 체이닝).

## 경계 탐색과 PR 열거: 기존 함수 재사용

경계(최근 stable Release) 탐색과 그 이후 병합된 PR 열거는
`release_ops.gather_release_context`(Task 037/048/051이 이미 쓰는 함수)를
그대로 재사용한다 — 직접 재구현하지 않았다. `ReleaseContext.commit_range_prs`가
경계 이후 각 커밋에 연결된 PR(또는 None)을 이미 올바르게 계산해서 준다.
boundary_version/tag/commit 표시를 위해서만 `devbot.release`의
`has_stable_release`/`latest_stable_version`(둘 다 이미 공개된 함수)을
한 번 더 호출해 일치하는 `ReleaseRecord`를 찾는다 — 이것도 정책 재구현이
아니라 같은 두 함수를 다른 목적(보고용)으로 한 번 더 부르는 것뿐이다.

## PR→Contract 연결: 새로 설계해야 했던 부분

이 저장소에는 "PR → Contract"를 끝까지 이어주는 기존 메커니즘이 없었다.
`devbot.planner`는 명명 규약(`task/NNN-slug` 브랜치, `tasks/NNN-slug.md`
Contract)을 만들 뿐이고, `devbot.worktree`의 파서들은 Task **Issue** 본문을
대상으로 하며 스스로 "best-effort"라고 문서화돼 있다(`docs/07-decisions.md`
2026-07-15 worktree ADR — Task 022 이전 또는 수동 작성된 Issue/PR은 이
규약을 따르지 않을 수 있다고 명시).

**1차 신호(필수)**: PR의 head 브랜치명. `task/NNN-slug` 패턴과 일치하면
`devbot.planner.canonical_contract_path(NNN, slug)`(재사용, 재구현 아님)로
기대 경로를 재구성하고, 병합 커밋 시점의 그 경로를 `get_file_content`로
읽는다. 패턴과 일치하지 않으면 — 애초에 Task PR이 아니었다는 뜻이므로 —
`PullRequestExclusionReason.NOT_A_TASK_BRANCH`로 명시적으로 기록하고 다음
PR로 넘어간다(조용히 버리지 않음, 오류도 아님). 패턴은 일치하는데 그
경로에 Contract가 없으면 — 브랜치명이 스스로 Task PR이라고 주장하는데
실제로는 못 찾은 것이므로 — 진짜 불일치로 보고 `MissingContractError`로
전체 집계를 fail closed한다.

**2차 신호(있으면 교차검증)**: Planner가 생성한 PR 본문의 `## Contract`
선언(`devbot.planner.render_pr_body`가 실제로 쓰는 정확한 포맷을 재현하는
새 정규식으로 파싱 — 기존 `devbot.worktree._CONTRACT_PATH_RE`는 Task Issue
본문용 포맷을 겨냥해서 PR 본문에는 매칭되지 않음을 직접 확인했다). 있으면
브랜치 유래 경로와 대조하고, 서로 다르면 `AmbiguousContractError`로
fail closed(어느 쪽이 맞는지 추측하지 않음).

**base branch 필터**: PR의 실제 `base_ref`가 저장소의 `default_branch`와
다르면 `PullRequestExclusionReason.NON_DEFAULT_BASE_BRANCH`로 명시적
제외(Spec의 "PR merged to a non-default branch exclusion" 요구사항).

## 새 GitHubClient 메서드

`GitHubClient.get_pull_request(repository, number) -> PullRequestDetail`을
신규 추가했다 — 기존 어떤 메서드도 `merge_commit_sha`/`merged_at`/`body`/
`html_url`을 한 번에 반환하지 않았다(`list_pull_requests`의 `PullRequest`,
`get_commit_pull_request_metadata`의 `PullRequestMetadata` 각각 일부만
보유). `GET /repos/{owner}/{repo}/pulls/{number}` 한 번으로 필요한 모든
필드를 얻는다. 이 저장소의 "read-only 메서드만 허용" 화이트리스트 테스트
(`tests/test_github_client.py::test_client_exposes_read_operations_only`)에도
추가했다.

## 구현 중 발견한 실제 문제와 수정 (사용자 승인 후 진행)

Task 052 자체를 실제 저장소 데이터로 검증하던 중, `parse_contract_metadata`를
최근 Contract 파일들에 직접 돌려봤다:

| 파일 | 결과 |
|---|---|
| `tasks/046-contract-metadata-engine.md` | `schema_v1` (Contract Version 본문이 순수 `1`) |
| `tasks/048-release-preparation.md` | `schema_v1` (순수 `1`) |
| `tasks/049-release-publish.md` | `schema_v1` (순수 `1`) |
| `tasks/050-release-publish-strategy.md` | **`MalformedContractVersionError`** (본문이 `- contract_version: 1`) |
| `tasks/051-release-orchestration.md` | **`MalformedContractVersionError`** (본문이 `- contract_version: 1`) |

파서(`contract_metadata._VERSION_RE = re.compile(r"^\s*(\d+)\s*$")`)는
"Contract Version" 섹션 본문이 **순수 정수 하나여야** 한다고 요구하는데,
Task 050/051의 Contract는 다른 필드들처럼 불릿(`- contract_version: 1`)로
써져 있어서 이 요구를 만족하지 못했다. Task 046 자신의 Contract와
048/049는 이미 순수 정수 형식이었다.

이는 **이론적 우려가 아니라 실제 사실**이었다 — 지금 이 저장소를 대상으로
`devbot release recommend`를 실행하면, 최근 stable Release(v0.1.1) 이후
병합된 PR 중 Task 050의 PR(#106)을 만나는 순간 fail closed된다. Task 052
자신의 코드가 잘못된 게 아니라 — "손상된 Contract는 전체 집계를 fail
closed시킨다"는 것 자체가 Specification이 요구하는 정확한 동작이다 — 하지만
그 결과 이 기능이 오늘 이 저장소에서는 끝까지 성공하지 못한다는 실질적
문제였다.

사용자에게 한글로 보고하고 승인받은 뒤 다음과 같이 진행했다:
- **Task 052 자신의 코드는 손대지 않았다** — fail-closed 동작이 정확히
  의도대로다.
- **`tasks/050-release-publish-strategy.md`, `tasks/051-release-orchestration.md`
  두 파일만** `- contract_version: 1` → `1`로 수정했다(의미 변경 없는
  순수 포맷 수정, 한 줄씩).
- `specifications/050-*.md`, `specifications/051-*.md`에 내장된 "Full
  Task Contract Reference" 사본도 동일하게 수정하고, Python diff 스크립트로
  각 Contract 파일과 바이트 단위로 다시 일치함을 확인했다.
- `uv run devbot specification validate --task 50`/`--task 51` 재실행,
  둘 다 PASS 확인.
- `docs/07-decisions.md`에 2026-07-19 항목으로 이 발견과 수정 근거를
  상세히 기록했다.

## 테스트와 명령 결과

```
$ uv run devbot specification validate --task 52
PASS (0 errors, 0 warnings)

$ uv run devbot specification validate --task 50
PASS (0 errors, 0 warnings)

$ uv run devbot specification validate --task 51
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1150 passed in 64.58s
```

- `tests/test_release_recommendation_aggregation.py` (신규, 24개): 경계
  없음/PR 없음/stable Release 있음(draft·prerelease 무시 검증 포함),
  major/minor/patch/none 각 레벨, 혼합 recommendation과 우선순위,
  결정적 정렬(merged_at 오름차순), classification reason 보존, 커밋-PR
  중복 항목 dedup, 대량(50개) PR 처리, non-task 브랜치 제외(오류 아님),
  non-default base branch 제외, missing/ambiguous/legacy/malformed
  Contract 각각의 fail-closed, PR 본문 Contract 선언 일치 시 통과,
  GitHub 호출 실패 래핑, write client 미사용(소스 코드에 `GitHubWriteClient`
  문자열이 아예 없음을 직접 확인), 오류 계층, 렌더링.
- `tests/test_github_client.py` (+2): `get_pull_request`가 전체 필드를
  올바르게 파싱, merge 안 된 PR은 `merged=False`/`merged_at=None`을
  올바르게 반환. read-only 화이트리스트 테스트에 새 메서드 등록.
- `tests/test_main.py` (+4): CLI 배선, write client/daemon lock 미획득,
  오류 종료 코드, evidence/최종 recommendation 렌더링.
- 전체 스위트 1150개 회귀 없이 통과.

## 범위 제외 사항

Contract/Specification이 명시한 대로 다음은 이번 태스크에 포함하지
않았다: `pyproject.toml`/`uv.lock` 쓰기, release 준비/게시, `devbot release
run --auto`, release notes 생성, PR 생성/검토/병합, PR 제목/라벨/커밋
메시지/diff로부터의 release impact 추측, 다중 저장소 집계.

## 아키텍처 결정 업데이트

`docs/07-decisions.md`에 2026-07-19 Task 052 항목을 추가해, 왜
`gather_release_context`를 재사용했는지, PR→Contract 연결을 어떻게
설계했는지(브랜치명 1차 신호 + PR 본문 2차 신호), `get_pull_request`를
왜 새로 추가했는지, 그리고 위에서 설명한 Contract 포맷 발견과 수정
근거를 기록했다. `docs/00-roadmap.md`에도 Task 052 항목을 같은 스타일로
추가했다.

## 수정/추가 파일

- `src/devbot/release_recommendation_aggregation.py` (신규)
- `src/devbot/github_client.py` (`PullRequestDetail` + `get_pull_request` 추가)
- `src/devbot/main.py` (`release recommend` 서브커맨드 + 핸들러)
- `tests/test_release_recommendation_aggregation.py` (신규, 24개)
- `tests/test_github_client.py` (+2, 화이트리스트 갱신)
- `tests/test_main.py` (+4)
- `specifications/052-release-recommendation-aggregation.md` (canonical
  8-섹션 구조로 재구성)
- `docs/07-decisions.md`, `docs/00-roadmap.md` (Task 052 항목 추가)
- **드라이브바이 수정**: `tasks/050-release-publish-strategy.md`,
  `tasks/051-release-orchestration.md`, `specifications/050-*.md`,
  `specifications/051-*.md` — `## Contract Version` 본문 포맷 수정
  (`- contract_version: 1` → `1`), 의미 변경 없음, 사용자 승인 후 진행.
