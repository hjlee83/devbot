# Task 049 Result: Release Publish

## 구현 전 아키텍처 충돌 확인과 사용자 논의

구현을 시작하기 전에, Task 037의 ADR(`docs/07-decisions.md`, "Operator
release publishing dispatches the existing workflow and never builds
Releases itself")이 "`release_ops.py`는 구조적으로(by construction, 관례가
아니라) 태그나 Release를 직접 만드는 경로가 없다"고 명시하고 있다는 것을
확인했다. 반면 Task 049의 Contract/Specification은 정확히 그 반대를
요구한다 - "Create and push the Git tag", "Create the GitHub Release using
explicit release notes supplied by the caller"를 DevBot 프로세스 자신이
직접 수행하라는 것이다(필수 테스트 목록에 "태그 push 성공 후 Release
생성 실패"가 있는데, 이는 DevBot이 두 단계를 각각 순차 수행할 때만
의미 있는 시나리오다 - workflow dispatch는 두 단계를 CI 안에서 원자적으로
묶어 처리하므로 이런 부분 실패를 DevBot이 관찰할 수 없다).

이 충돌을 실제로 구현을 시작하기 전에 사용자에게 명시적으로 제시했다 -
"Contract대로 직접 경로 구현" / "기존 workflow-dispatch로 재해석" /
"저장소 소유자에게 먼저 확인" 세 선택지를 제안했다. 사용자는 "1이 맞다"고
확인한 뒤, "아직 미완성인 봇으로 인해 릴리스를 못하는 경우"의 실패 처리를
물었다 - Specification의 "Publication Order and Partial Failure"와
"Idempotency" 절이 정확히 이 상황(부분 실패 후 안전한 재시도)을 이미
설계해 두었음을 설명했고, 사용자가 이 설계에 동의한 뒤 진행했다.

## 완료 내용

Task 048이 준비한 버전을 Git 태그 + GitHub Release로 **직접** 게시하는
새 `src/devbot/release_publish.py`를 구현했다 - Task 037의
workflow-dispatch 경로(`release_ops.py`) **옆에 의도적으로 추가한 두 번째
경로**다.

- **버전은 다시 계산하지 않고 Task 048이 준비한 값만 읽는다**:
  `release_preparation.read_current_version`(이번에 promote)을 그대로
  재사용 - `pyproject.toml`/`uv.lock`을 절대 쓰지 않는다.
- **쓰기 전 전수 검증** (`preview_release_publish`, 읽기 전용):
  버전 형식/`pyproject.toml`↔`uv.lock` 일치(Task 048 로직 재사용),
  worktree가 깨끗한지, 로컬 main이 원격 main과 같은지, notes가 비어
  있지 않은지, 기존 태그가 검증된 대상 커밋과 다른 곳을 가리키지
  않는지, 기존 Release가 검증된 대상과 다른 곳을 가리키지 않는지 -
  하나라도 실패하면 어떤 쓰기도 일어나지 않는다.
- **게시 순서**: (1) 검증된 커밋에 annotated 태그 생성 (2) 태그 push
  (3) 같은 태그로 GitHub Release 생성. 절대 force로 옮기거나 덮어쓰지
  않는다.
- **멱등성**: 태그+Release가 이미 검증된 대상과 일치하면 `already_
  published`로 아무 것도 쓰지 않고 종료. 태그만 있고 Release가 없으면
  태그는 다시 만들지 않고 Release만 안전하게 완성(`completed_missing_
  release`).
- **부분 실패**: 태그 push는 성공했는데 Release 생성이 실패하면
  `PartialPublicationError`(태그/대상 커밋 정보 포함)를 던진다 - 거짓
  성공을 보고하지 않고, push된 태그를 자동 삭제하지도 않는다. 이후
  재호출은 태그를 재사용해 Release만 마저 만든다 - 실제로 이 순서
  (실패 → 재시도 → 완료)를 테스트로 재현했다.
- **CLI**: `devbot release publish-prepared --notes-file <path>
  [--repo] [--dry-run]` - 기존 `devbot release publish`(Task 037)와
  이름/플래그를 다르게 둬서 두 경로가 혼동되지 않게 했다.

## 새 `release_publish.py`가 도입한 것 외에 손댄 부분

- `src/devbot/release_preparation.py`: `read_current_version`을
  public으로 promote(Task 048에서 이미 있던 로직 재사용, 안전한
  추가)했고, **버전 형식(정확히 X.Y.Z) 검증을 `calculate_next_version`
  에서만이 아니라 `read_current_version` 자체에도 적용**하도록 고쳤다
  - 테스트 작성 중 발견한 진짜 공백이다: Task 048은 항상 `calculate_
  next_version`을 바로 이어서 호출하므로 형식 검증이 우연히 이루어졌지만,
  Task 049는 다음 버전을 계산하지 않으므로 이 공백이 그대로 드러났다
  ("not-a-version" 같은 값이 조용히 통과함). 기존 Task 048의 32개
  테스트가 전부 그대로 통과함을 확인해 이 변경이 완전히 투명함을
  검증했다.
- `src/devbot/github_write_client.py`: `create_release`(신규 write
  메서드)와 `ReleaseInfo` 모델 추가. 기존 write-only 메서드 목록을
  강제하는 `test_write_client_exposes_write_operations_only`(허용
  메서드를 명시적으로 열거하는 안전장치 테스트)에 `create_release`를
  의도적으로 추가했다 - 우회한 게 아니라, 이 새 쓰기 능력이 진짜로
  검토받아야 할 변경임을 그 테스트 자체가 보여주도록 했다.
- `GitHubClient`에는 새 메서드를 추가하지 않았다 - 이미 있던
  `get_commit_sha`(임의의 ref를 커밋 SHA로 해석, annotated 태그도
  자동으로 dereference)와 `get_release_by_tag`를 그대로 재사용해
  태그/Release 존재 확인을 모두 처리했다(AC #9 "기존 추상화 재사용"을
  가장 직접적으로 만족).

## Specification 소소한 수정

`devbot specification validate --task 49`가 처음엔 에러 1개("Files
Expected to Change" 서브섹션 누락)로 실패했다 - 그 서브섹션을 추가해
고쳤다(내용은 실제 변경 파일 목록과 정확히 일치하도록 작성). 나머지는
이미 canonical 구조였다. `# Full Task Contract Reference`가
`tasks/049-release-publish.md` 원문과 바이트 단위로 일치하는지 Python
diff로 확인했다(일치).

## Git/GitHub 안전 검증 전략

**GitHub는 항상 mock이다** (`unittest.mock.MagicMock(spec=GitHubClient)`
/ `spec=GitHubWriteClient)`) - 네트워크 호출이 전혀 없다.

**Git은 실제 `git` 명령을 쓰되, 매 테스트마다 만들고 버리는 완전히
격리된 로컬 저장소만 대상으로 한다** - `tmp_path` 안에 `git init`한
로컬 체크아웃과 `git init --bare`한 로컬 저장소를 `origin`으로 연결해,
실제 `git tag`/`git push`가 정말로 올바르게 동작하는지(annotated 태그
dereference, push 성공/실패 등) `subprocess`를 mock하는 것보다 훨씬
엄격하게 검증했다. 이 방식은 실제 devbot 저장소나 실제 GitHub 원격을
전혀 건드리지 않는다 - `tmp_path`와 함께 테스트 종료 시 완전히
삭제된다. `docs/07-decisions.md`의 새 ADR 항목에 이 선택을 명시적으로
기록했다(Contract의 "Tests use fakes/mocks for Git and GitHub writes"
요구를 "네트워크로 나가는 실제 GitHub/Git 인프라는 전혀 쓰지 않는다"로
해석한 것 - 순수 `subprocess` mock보다 회귀를 더 잘 잡아낸다는 판단).

## 수정 파일

- `src/devbot/release_publish.py` (신규)
- `src/devbot/github_write_client.py` (`create_release` 신규 메서드,
  `ReleaseInfo` 모델)
- `src/devbot/release_preparation.py` (`read_current_version` promote +
  버전 형식 검증 강화 - 안전한 추가/버그 수정)
- `src/devbot/main.py` (`devbot release publish-prepared` CLI 배선)
- `tests/test_release_publish.py` (신규, 15개 테스트)
- `tests/test_main.py` (5개 테스트 추가)
- `tests/test_github_write_client.py` (허용 메서드 목록에 `create_release`
  추가)
- `docs/07-decisions.md` (2026-07-19 ADR 항목 추가 - 두 번째 경로를
  의도적으로 도입한 이유와 두 경로의 공존 방식)
- `docs/00-roadmap.md` (Task 049 항목 추가)
- `specifications/049-release-publish.md` ("Files Expected to Change"
  서브섹션 추가)
- `results/049-release-publish.md` (본 문서)

`tasks/049-release-publish.md`는 수정하지 않았다. **`pyproject.toml`,
`uv.lock`(실제 저장소 파일)도 수정하지 않았다.**

## Checkpoint Evidence

| Acceptance Criteria (Specification) | Evidence |
| --- | --- |
| 1. 이미 준비된 버전을 재파싱 없이 미리보기 가능 | `test_dry_run_preview_performs_no_writes` |
| 2. 게시가 태그 1개+매칭 Release 1개를 만듦 | `test_successful_publish_creates_tag_and_release` |
| 3. 태그가 검증된 main 커밋을 가리킴 | `test_successful_publish_creates_tag_and_release`(remote ls-remote로 직접 확인) |
| 4. 버전 파일이 수정되지 않음 | `test_pyproject_and_uv_lock_remain_unchanged` |
| 5. 잘못된 저장소 상태가 게시 전에 실패 | `test_version_source_mismatch_raises_and_creates_no_tag`, `test_malformed_version_raises`, `test_dirty_worktree_raises`, `test_stale_main_raises`, `test_empty_release_notes_raises` |
| 6. 중복/부분 상태가 결정론적이고 안전하게 처리됨 | `test_existing_matching_tag_and_release_is_already_published`, `test_existing_matching_tag_missing_release_completes_release_only`, `test_existing_tag_at_wrong_sha_raises_conflicting_tag_error`, `test_release_creation_failure_after_tag_push_raises_partial_publication_error` |
| 7. 반복 성공 호출이 중복 리소스를 만들거나 태그를 옮기지 않음 | `test_retry_after_partial_publication_completes_safely_without_moving_tag` |
| 8. Release notes가 그대로 전달됨 | `test_successful_publish_creates_tag_and_release`(`create_release` 호출 인자 검증) |
| 9. 기존 추상화 재사용, 두 번째 GitHub client 없음 | `get_commit_sha`/`get_release_by_tag` 재사용(신규 GitHubClient 메서드 0개), `GitHubWriteClient`에만 1개 메서드 추가 |
| 10. 전체 검증 통과 | 아래 Validation 결과 |
| force 태그 연산 없음 | `test_no_force_flag_used_in_any_git_call`, `test_source_never_contains_a_force_git_flag` |

## Validation 결과

- `uv run devbot specification validate --task 49`: PASS, 에러/경고 0개
- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  1035 passed (Task 048 병합 후 기준 1015개 + `tests/test_release_publish.py`
  15개 + `tests/test_main.py` 5개 + `tests/test_github_write_client.py`
  갱신 1개[기존 테스트 수정, 신규 아님])
- 실제 `pyproject.toml`/`uv.lock`: 구현 전체 기간 동안
  `version = "0.1.2"`로 불변, `git status` 변경 없음 확인(마지막 확인은
  전체 스위트 실행 직후)

## 수동 검증 결과 (mock GitHub + 격리된 임시 git 저장소, 실제 GitHub 미사용)

```
$ uv run python -c "... preview_release_publish/publish_prepared_release 전체 흐름 ..."
fresh preview: absent absent
publish result: outcome=published, tag=v1.2.3, release_url=https://example.invalid/...
local tags: v1.2.3
remote tags: ...refs/tags/v1.2.3^{}  (target sha가 기대한 커밋과 정확히 일치)

idempotent result: already_published (create_release 호출 안 됨)

partial failure: PartialPublicationError - tag='v1.2.3' sha=<커밋>
  (태그는 push된 채로 남아 있음 - 삭제되지 않음)

retry result: completed_missing_release (태그를 다시 만들지 않고 Release만 완성)
final tags: 'v1.2.3'  (재시도 후에도 태그는 정확히 1개)

$ uv run devbot release publish-prepared --notes-file notes.md --dry-run
(GitHubClient/preview_release_publish mock으로 CLI 배선 확인 - 실제 저장소 대상
아님, tests/test_main.py의 5개 테스트로 고정)
```

## 남은 TODO와 제한 (계약이 명시적으로 범위 밖으로 남긴 것)

- 다음 버전 계산은 하지 않는다 - Task 048이 이미 계산/기록한 버전만
  읽는다.
- Git 히스토리/PR/Contract로부터 릴리스 노트를 자동 생성하지 않는다 -
  호출자가 명시적으로 제공한 notes만 그대로 사용한다.
- 패키지 배포, PR 병합/승인은 구현하지 않았다.
- 여러 Task/PR/Contract를 하나의 릴리스 결정으로 합치는 로직은
  구현하지 않았다.
- 부분 실패 후 push된 원격 태그의 자동 롤백/삭제는 구현하지 않았다 -
  계약이 명시적으로 금지했다.

## 위험 요소

- **두 개의 릴리스 게시 경로가 이제 공존한다.** `devbot release
  publish`(Task 037, workflow dispatch)와 `devbot release
  publish-prepared`(Task 049, 직접 게시) 중 어느 쪽을 실제 운영에서
  쓸지는 이번 Task가 결정하지 않는다 - 둘 다 안전하게 fail closed하도록
  설계됐지만, 운영자가 두 경로를 같은 릴리스에 대해 동시에/순서
  없이 실행하면(예: 한쪽으로 게시 후 다른 쪽으로 다시 시도) 예상치
  못한 상호작용이 생길 수 있다. 각 경로는 독립적으로 멱등하지만, 서로의
  존재를 인지하지 못한다 - 향후 운영 문서(`docs/08-beta-runbook.md`
  등)에 "두 경로 중 하나만 쓰라"는 운영 지침을 추가하는 것을 후속
  작업으로 권장한다(이번 Task 범위 밖으로 남김).
- `read_current_version`의 형식 검증 강화는 Task 048의 기존 동작을
  바꾸지 않지만(이미 우연히 검증되고 있었음), 향후 `plan_release_
  preparation` 호출 순서가 바뀌면(예: 형식 검증 없이 `read_current_
  version`만 단독 호출하는 새 코드가 추가되면) 이번에 추가한 검증이
  유일한 방어선이 된다는 점을 유지보수자가 알아야 한다.
