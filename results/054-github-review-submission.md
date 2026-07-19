# Task 054 Result: GitHub Review Submission

## 구현 요약

`src/devbot/github_review_submission.py`를 신규 작성했다 — Task 053의
provider-neutral `ReviewReport`를 정확히 하나의 공식 GitHub PR 리뷰로
제출하는 모듈이다. CLI로는
`devbot review submit --repo owner/repo --pr N --report report.json
[--dry-run]`을 추가했다. `risk_level: high` — 이 저장소에서 Issue/PR
댓글·라벨·Release가 아니라 진짜 GitHub Review를 처음으로 쓰는 태스크다.

## Decision → Event 매핑

`_DECISION_TO_EVENT` 딕셔너리 하나가 유일한 매핑 로직이다:

- `ReviewDecision.APPROVED` → `GitHubReviewEvent.APPROVE`
- `ReviewDecision.CHANGES_REQUIRED` → `GitHubReviewEvent.REQUEST_CHANGES`
- `ReviewDecision.COMMENT_ONLY` → `GitHubReviewEvent.COMMENT`

이 매핑은 이 모듈에만 있다 — Task 053의 `review_decision.py`는 여전히
GitHub 타입/이벤트명을 전혀 모른다.

## Report 신원과 Stale-Head 보호

제출 가능한 report는 `metadata["reviewed_head_sha"]`에 정확히 어떤
커밋을 리뷰했는지 반드시 명시해야 한다(없거나 공백뿐이면
`MissingReviewIdentityError`). `metadata["repository"]`/
`metadata["pr_number"]`는 선택적 교차 검증용이다 — report가 스스로
선언했는데 CLI가 지정한 저장소/PR과 다르면 `ReviewIdentityMismatchError`로
거부한다.

**Stale-head 보호는 읽기 전용이고, dry-run과 실제 제출이 정확히 같은
함수(`build_github_review_submission_plan`)를 거치므로 두 경로가 절대
갈라질 수 없다.** `GitHubClient.get_pull_request`(이미 있던 메서드,
Task 052)로 PR의 **현재** head SHA를 읽어, report가 리뷰했다고 주장하는
SHA와 다르면 write client를 만들기도 전에 `StaleReviewHeadError`로
거부한다. PR이 open이 아니거나(closed) 이미 merged면
`UnsupportedPullRequestStateError`로 거부한다.

### 리뷰 수정: write 직전 재검증 (TOCTOU)

최초 구현은 이 검사를 `build_github_review_submission_plan`(planning
단계) 딱 한 번만 했다. hjlee83가 PR #114에서 정확히 지적한 문제:
GitHub의 review 제출 API는 head-SHA-conditional create를 지원하지
않으므로, planning과 실제 write(`write_client.submit_pull_request_review`
호출) 사이의 간격 — 특히 그 사이에 새 커밋이 push되는 경우 — 에는
`commit_id`로 예전 SHA를 그대로 보내도 GitHub가 이를 그냥 받아들여버릴
수 있다. 즉 stale-head 검사가 "확인"만 하고 "확인한 대로 쓴다"는
보장이 없었다 — 전형적인 time-of-check-to-time-of-use 경쟁 조건이다.

고친 방법: 이전에 `build_github_review_submission_plan` 안에 있던
PR 조회+검증(현재 head SHA 일치 + open/unmerged) 로직을
`_fetch_and_verify_pull_request()` 헬퍼로 뽑아내고, 이걸 두 번 호출한다
— 한 번은 기존과 동일하게 planning 중에, 또 한 번은 `submit_github_review`
안에서 `write_client.submit_pull_request_review`를 호출하기
**직전**에. 두 번째 호출에서 head SHA가 달라졌으면 `StaleReviewHeadError`,
그 사이 PR이 closed/merged로 바뀌었으면 `UnsupportedPullRequestStateError`
로 write를 아예 시도하지 않고 중단한다 — 같은 헬퍼가 두 검사를 모두
하므로 "closed/merged로 바뀐 경우"도 별도 코드 없이 자동으로 커버된다.

**이건 TOCTOU 창을 좁히는 것이지 없애는 게 아니다** — 재검증과 실제
POST 사이에도 이론상 아주 짧은 간격은 남는다(GitHub API 자체에
head-SHA-conditional create가 없는 한 완전히 없앨 수 없음). 하지만
검사와 write 사이의 간격을 "report 작성부터 제출까지의 임의 시간"에서
"두 번째 HTTP 호출과 세 번째 HTTP 호출 사이의 네트워크 왕복 시간"으로
줄인다.

신규 회귀 테스트(`tests/test_github_review_submission.py`): PR을 두
번 조회하도록 mock해서 두 번째 조회에서만 head SHA가 바뀌는 경우
(`test_head_changed_between_plan_and_write_blocks_submission`), 두
번째 조회에서 PR이 closed로 바뀌는 경우
(`test_pr_closed_between_plan_and_write_blocks_submission`), merged로
바뀌는 경우
(`test_pr_merged_between_plan_and_write_blocks_submission`) 각각
`StaleReviewHeadError`/`UnsupportedPullRequestStateError`가 발생하고
`write_client.submit_pull_request_review`가 전혀 호출되지 않음을
검증한다. 드리프트가 없는 정상 경로도 `get_pull_request`가 정확히
두 번(planning + pre-write) 호출되고 write는 정확히 한 번만 일어남을
확인하며(`test_no_drift_between_plan_and_write_reads_pr_twice_and_
succeeds`), dry-run은 애초에 write가 없으므로 재검증 없이 한 번만
조회함을 별도로 확인한다(`test_dry_run_only_reads_pr_once`).

## Self-Approval: 선제적으로 차단, 절대 다운그레이드하지 않음

GitHub API는 인증된 identity가 PR 작성자와 같으면 `APPROVE`를 거부한다
(HTTP 422). 이걸 사후에만 처리하면 운영자는 불투명한 GitHub 오류
메시지로만 원인을 알게 되고, Specification은 명시적으로 "실패한
APPROVE를 조용히 COMMENT로 다운그레이드하지 말 것"을 요구한다.

`build_github_review_submission_plan`은 **event가 APPROVE일 때만**
(`GitHubClient.get_authenticated_user`, 읽기 전용) 인증된 identity와
PR 작성자를 비교해, 같으면 **어떤 쓰기도 하기 전에**
`SelfApprovalError`로 거부한다 — REQUEST_CHANGES/COMMENT에는 이 검사를
아예 하지 않아(Specification의 "self-approval"이라는 표현 자체가
approve에 국한됨) 불필요한 API 호출도 없다. 2차 안전망으로,
`submit_github_review`는 GitHub 자체가 반환한 422 오류 메시지에서
"own pull request"/"can(not) approve" 같은 문구를 인식하면(선제 검사가
어떤 이유로든 못 잡은 경우 - 예: planning과 submission 사이에 인증된
identity가 바뀐 경우) 이것도 같은 타입 오류로 승격시킨다 — 일반
API 오류로 뭉뚱그리지 않는다.

## Inline Comment 변환: 부분적으로만 유효한 위치는 전체를 fail closed

- **위치가 아예 없거나 `path`/`line` 중 하나라도 없으면** — GitHub가
  앵커링할 대상이 없으므로 — 에러가 아니라 body-only로 처리한다(가장
  흔한, 정상적인 경우).
- **`side`가 있는데 `LEFT`/`RIGHT`가 아니면** — 데이터가 겉보기엔
  완전해서 inline으로 배치될 것처럼 보이지만 값이 틀렸으므로, 엉뚱한
  diff 쪽에 잘못 배치될 위험이 있다 — 어느 쪽이 맞는지 추측하거나
  조용히 body-only로 떨어뜨리지 않고 `UnsupportedInlineLocationError`로
  **전체 제출**을 fail closed한다.
- `side`가 없으면 GitHub 기본값과 동일하게 `RIGHT`로 채운다.
- `start_line`이 `line`과 다르면 멀티라인 댓글로(`start_line`/
  `start_side` 포함), 같으면(또는 없으면) 단일 줄 댓글로 변환한다.
- **inline으로 변환됐는지와 무관하게, 모든 finding은 항상 review body에도
  다시 나타난다** — GitHub가 어떤 이유로든 inline 댓글을 렌더링하지
  못해도 아무것도 사라지지 않는다.

## Dry-Run과 실제 제출

- `build_github_review_submission_plan(github_client, repository,
  pr_number, report)` — 순수 읽기 전용, write client 파라미터 자체가
  없다. dry-run과 실제 제출 둘 다 이 함수를 먼저 호출한다.
- `submit_github_review(github_client, write_client, repository,
  pr_number, report, *, dry_run=False)` — `dry_run=True`면
  `write_client`를 전혀 쓰지 않고(`None`이어도 됨) plan만 계산해
  `submitted=False`인 결과를 반환한다. `dry_run=False`면 `write_client`가
  필수이고, `GitHubWriteClient.submit_pull_request_review`를 정확히
  한 번만 호출한다.
- CLI는 `--dry-run`일 때 `GitHubWriteClient`를 아예 생성하지 않는다
  (mock 기반 테스트로 직접 확인).

## 새 GitHub Client 변경

- `GitHubClient.PullRequestDetail`에 `head_sha`/`state`/`author_login`
  필드를 추가했다(Task 052가 도입했을 때는 이미 merge된 PR에나 쓸모
  있는 `merge_commit_sha`/`merged_at`만 있었고, open PR의 현재 head나
  작성자를 알 방법이 없었다). 기존 테스트 2개(`test_get_pull_request_
  parses_full_detail`, `test_get_pull_request_unmerged_reports_merged
  _false`)의 fixture/assertion을 새 필드에 맞게 갱신했다.
- `GitHubWriteClient.submit_pull_request_review`를 신규 추가했다 —
  기존 write 메서드들(`create_pull_request`, `merge_pull_request` 등)과
  같은 패턴: 정책을 전혀 갖지 않고, 이미 검증된 `commit_id`/`event`/
  `body`/`comments`만 그대로 전달한다. read-only 화이트리스트 테스트
  (`test_write_client_exposes_write_operations_only`)에 등록했다.

## CLI 외 PR 변경 없음

`submit_github_review`는 `GitHubWriteClient.submit_pull_request_review`
**단 하나**만 호출한다 — merge, label, comment, close 등 다른 어떤 쓰기도
하지 않는다(`test_real_submission_makes_no_other_write_client_call`로
직접 확인: `write_client.method_calls`에 잡힌 메서드가 정확히
`{"submit_pull_request_review"}`뿐임을 검증).

## 범위 제외 사항

Contract/Specification이 명시한 대로 다음은 이번 태스크에 포함하지
않았다: LLM으로 finding 생성, GitHub App 생성/설치/토큰 교환,
review/rework 루프, merge readiness/auto-merge, 자동 코드 수정,
GitLab/Jira 지원. 인증은 기존 개인 토큰 write client 생성 경로를
그대로 썼다 — 나중에 GitHub App installation token으로 바뀌어도 이
모듈의 코드는 그대로 호환되도록, 토큰 종류에 대한 가정을 넣지 않았다.

## 테스트와 명령 결과

```
$ uv run devbot specification validate --task 54
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1281 passed in 77.22s
```

- `tests/test_github_review_submission.py` (44개, TOCTOU 재검증 관련
  5개 추가): report 신원 요구사항(누락/공백/repository·pr_number
  불일치/일치), stale-head 거부/통과, PR 상태(closed/merged/open)
  거부/통과, 세 decision→event 매핑 각각, self-approval 선제 차단 +
  non-APPROVE에는 검사 스킵 + 다른 작성자는 통과 + GitHub 422 메시지
  인식(다운그레이드 없음), 결정론적 body(모든 필드·모든 finding 포함,
  반복 호출 간 일관성), inline 변환(위치 없음/path만/멀티라인/단일라인
  /명시적 side/잘못된 side fail-closed), dry-run이 write client
  불필요 + PR을 한 번만 조회, 실제 제출 없이 write_client=None이면
  거부, 실제 제출이 정확히 한 번만 호출, 다른 write 메서드 호출 없음,
  **write 직전 재검증**(head 변경/PR closed/PR merged 각각 write 없이
  중단, 드리프트 없으면 조회 2회·write 1회로 정상 성공),
  GitHubAuthenticationError/GitHubAPIError 래핑, PR 조회 실패 래핑,
  오류 계층, 렌더링.
- `tests/test_github_client.py` (+2개 필드 갱신): `get_pull_request`가
  `head_sha`/`state`/`author_login`을 올바르게 파싱.
- `tests/test_github_write_client.py` (+2): `submit_pull_request_review`가
  POST를 올바르게 보내고 응답을 파싱, comments가 없으면 payload에서
  키 자체를 생략.
- `tests/test_main.py` (+7): dry-run 시 write client 미생성, 실제 제출
  1회, stale head 실패 종료 코드, metadata 누락 실패, malformed report
  가 GitHub 호출 전에 실패, report 파일 누락 실패, daemon lock 미획득.
- 전체 스위트 1281개 회귀 없이 통과.

## 수정/추가 파일

- `src/devbot/github_review_submission.py` (신규)
- `src/devbot/github_client.py` (`PullRequestDetail` 필드 3개 추가)
- `src/devbot/github_write_client.py` (`submit_pull_request_review` +
  `PullRequestReviewInfo` 추가)
- `src/devbot/main.py` (`review submit` 서브커맨드 + 핸들러,
  `_run_review_command`에 `config` 파라미터 추가)
- `tests/test_github_review_submission.py` (44개)
- `tests/test_github_client.py` (기존 2개 테스트 갱신)
- `tests/test_github_write_client.py` (+2, 화이트리스트 갱신)
- `tests/test_main.py` (+7)
- `specifications/054-github-review-submission.md` (`## Dependencies`
  서브섹션 하나만 추가 — 나머지는 이미 canonical 구조였음)
- `docs/00-roadmap.md`, `docs/07-decisions.md` (Task 054 항목 추가)
