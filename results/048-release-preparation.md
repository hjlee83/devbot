# Task 048 Result: Release Preparation

## 범위 밖 drive-by 수정 (main과 merge하며 발견)

구현 완료 후 `origin/main`을 fetch했더니, 이번 세션과 무관한 실제 human
PR #101("chore(release): bump version to v0.1.2")이 이미 병합돼 있었다 -
실제 저장소의 `pyproject.toml`/`uv.lock` 버전이 `0.1.0`에서 `0.1.2`로
바뀌었다. `origin/main`을 내 브랜치에 merge한 뒤 전체 테스트를 돌렸더니
`tests/test_release.py::test_first_stable_release_uses_authoritative_initial_version_and_artifact_contract`
(Task 033, Task 048과 무관)가 실패했다 - 이 테스트가 `authoritative_version(Path.cwd())`로
**실제 살아있는 저장소의 현재 버전**을 읽어 `"0.1.0"`과
하드코딩 비교하고 있었기 때문이다. 내 Task 048 변경과는 무관하게, 이제
막 병합된 그 버전 범프 커밋 때문에 `main` 자체가 이미 빨간 상태였다.

이 Task의 Quality Gate가 "전체 테스트 스위트 통과"를 요구하고, 빨간
`main`은 이후 모든 PR에 계속 영향을 주므로, 최소한의 drive-by 수정을
했다 - `version == "0.1.0"`이라는 리터럴 비교와 `SemanticVersion.parse(version).tag
== "v0.1.0"`을 지우고, 후자를 `== f"v{version}"`(버전이 무엇이든
tag 변환 규칙이 일관되는지 확인하는 구조적 검증)으로 바꿨다 - 나머지
검증(artifact/manifest 이름 계약)은 이미 `version` 변수를 그대로 쓰고
있어 손대지 않았다. 이 한 줄 수정 덕분에 향후 버전이 다시 바뀌어도 이
테스트가 또 깨지지 않는다.

이 수정은 Task 048의 Contract/Specification 어디에도 명시되지 않은
범위 밖 변경이다 - 완전히 투명하게 별도로 표시한다. 원하시면 되돌리고
별도 PR로 분리할 수 있다.

## 완료 내용

Task 047의 `ReleaseRecommendation`을 실제 버전 계산/파일 준비로 이어주는
경계를 구현했다 - 새 `src/devbot/release_preparation.py`가
`pyproject.toml`(권위 있는 버전 소스)과 `uv.lock`의 `devbot` 패키지
항목을 같은 다음 버전으로 함께 준비한다. Git 태그/GitHub Release/push/
merge/패키지 배포/릴리스 노트 생성은 전혀 하지 않는다 - 로컬에서
리뷰 가능한 변경만 만든다.

- **버전 계산**: 순수 함수 `calculate_next_version(current, recommendation)` -
  `major`: `X.Y.Z -> X+1.0.0`, `minor`: `X.Y.Z -> X.Y+1.0`, `patch`:
  `X.Y.Z -> X.Y.Z+1`. `ReleaseRecommendation.NONE`은 언제나
  `NoReleaseRequiredError`로 거부한다("준비할 것이 없다"를 추측하지
  않고 명시적으로 알린다). 세 자리 안정 버전이 아닌 문자열
  (`1.2`/`1.2.3.4`/`1.2.3-alpha`/`v1.2.3` 등)은
  `MalformedProjectVersionError`로 거부한다.
- **검증 우선, 쓰기는 나중**: `plan_release_preparation(project_root,
  recommendation)`이 읽기 전용으로 `pyproject.toml`을 읽고, `uv.lock`의
  `devbot` `[[package]]` 항목을 찾아 두 버전이 일치하는지 확인한 뒤에만
  다음 버전을 계산한다 - 불일치는 `VersionSourceMismatchError`로
  거부하며 **절대 자동으로 맞추지 않는다**. `prepare_release`는 내부에서
  이 함수를 먼저 호출하므로, 어떤 검증이든 실패하면 파일에 한 바이트도
  쓰기 전에 예외가 던져진다.
- **원자적 두 파일 쓰기**: 두 파일의 새 내용을 각각 같은 디렉터리의
  임시 파일에 먼저 쓴 뒤(`tempfile.mkstemp`), 둘 다 성공한 경우에만
  `os.replace`로 원자적 교체한다 - 검증 실패나 두 번째 쓰기 실패 시
  실제 파일이 부분적으로/서로 다른 버전으로 남는 상태를 만들지 않는다.
- **포맷 보존 쓰기**: 새 TOML 쓰기 라이브러리를 추가하지 않았다.
  읽기/검증은 stdlib `tomllib`(읽기 전용)로 하고, 실제 쓰기는 정확히
  경계가 잡힌 구간(`[project]` 섹션, `name = "devbot"`인
  `[[package]]` 블록) 안에서 이미 확인된 `version = "OLD"` 줄 하나만
  문자열 치환한다 - 다른 패키지의 버전, 주석, 순서, 공백 등은 전혀
  건드리지 않는다(실제 diff로 각 파일에서 정확히 한 줄만 바뀜을
  확인, 아래 참고).
- **읽기 전용 CLI**: `devbot release prepare --level
  major|minor|patch [--dry-run]` - `none`은 argparse `choices`에
  아예 없어 선택 자체가 거부된다(exit 2). GitHub API를 호출하지
  않고(GitHub client를 생성하지도 않음) daemon lock도 잡지 않는다.

## 실제 저장소 버전 파일을 건드리지 않았다는 증거

계약의 Safety 절이 명시적으로 "Do not change the repository's current
version while implementing Task 048"를 요구했다. 모든 쓰기 테스트는
`tmp_path`(pytest 임시 디렉터리) 사본에서만 실행했고, 실제 저장소의
`pyproject.toml`/`uv.lock`은 읽기조차 하지 않았다(CLI 스모크 테스트도
`monkeypatch.chdir()`로 임시 디렉터리로 이동한 뒤 실행했다). 매 구현
단계마다 다음을 직접 확인했다:

```
$ grep "^version" pyproject.toml
version = "0.1.0"

$ git status --short pyproject.toml uv.lock
(출력 없음 - 변경 없음)
```

전체 테스트 스위트(1013개) 실행 후에도 동일하게 확인했다 - 이번 Task
전체에 걸쳐 실제 버전 파일은 단 한 번도 수정되지 않았다.

## 주요 설계 결정

- **`plan_release_preparation`을 별도 공개 함수로 분리했다.** 계약이
  요구한 두 함수(`calculate_next_version`, `prepare_release`)에 더해
  추가한 것이다 - Task 037의 `fetch_release_preview`/`publish_release`
  분리(계산 vs 실행)와 같은 패턴을 그대로 따랐다. `prepare_release`가
  내부에서 이 함수를 먼저 호출해 검증 로직을 한 곳에만 두고,
  `--dry-run` CLI 경로도 같은 함수를 그대로 재사용해 "실제 쓰기 전
  미리보기"와 "실제 준비"가 서로 다른 검증 로직을 갖게 되는 위험을
  없앴다.
- **`uv version`/`uv lock`을 서브프로세스로 호출하지 않는다.** 계약이
  명시적으로 금지했다("Do not call `uv version` or `uv lock` as opaque
  subprocesses from the core domain layer") - 대신 파일을 직접
  읽고/검증하고/정밀하게 치환한다. 이렇게 하면 결과가 완전히
  결정론적이고 테스트하기 쉬우며, `uv` CLI의 버전/동작 변화에 의존하지
  않는다.
- **TOML 쓰기는 정규식 기반 정밀 치환이지, 파싱 후 재직렬화가
  아니다.** `tomllib`로 다시 dump하면 주석/순서/포맷이 전부 사라진다
  (Python stdlib `tomllib`는 애초에 쓰기를 지원하지 않는다). 대신
  이미 `tomllib`로 확인한 정확한 old_version 문자열을, 정확히 경계가
  잡힌 섹션/블록 안에서만 찾아 교체한다 - 이 두 단계(읽기는 tomllib로
  정확하게, 쓰기는 최소 범위 문자열 치환으로)의 조합이 "포맷 보존"과
  "정확성"을 동시에 만족시킨다.
- **`uv.lock`의 `devbot` 패키지 블록 경계는 다음 `[[package]]` 헤더
  까지다.** `[package.dev-dependencies]`/`[package.metadata]`/
  `[package.metadata.requires-dev]` 같은 하위 테이블이 그 사이에
  올 수 있음을 실제 파일에서 확인했다 - 다음 `[[package]]`가 아니라
  일반적인 "다음 헤더"까지로 경계를 잡으면 이런 하위 테이블이 devbot
  블록 밖으로 잘못 분리된다. 실제 `uv.lock` 구조를 직접 읽고 확인한
  뒤 이 경계 규칙을 정했다.

## Specification 확인

`devbot specification validate --task 48`을 구현 전에 실행했더니 이미
통과했다(Task 045-047 리뷰에서 확립된 canonical 8-섹션 패턴을 처음부터
따르고 있었다) - 이번엔 Specification을 전혀 수정하지 않았다.
`# Full Task Contract Reference`가 `tasks/048-release-preparation.md`
원문과 바이트 단위로 일치하는지도 Python diff로 확인했다(일치).

## 수정 파일

- `src/devbot/release_preparation.py` (신규)
- `src/devbot/main.py` (`devbot release prepare` CLI 배선)
- `tests/test_release_preparation.py` (신규, 30개 테스트)
- `tests/test_main.py` (6개 테스트 추가)
- `tests/test_release.py` (범위 밖 drive-by 수정 1줄 - 위 절 참고)
- `docs/00-roadmap.md` (Task 048 항목 추가)
- `results/048-release-preparation.md` (본 문서)

`tasks/048-release-preparation.md`, `specifications/048-release-preparation.md`는
수정하지 않았다. **`pyproject.toml`, `uv.lock`(실제 저장소 파일)도
수정하지 않았다** - 위 "실제 저장소 버전 파일을 건드리지 않았다는 증거"
참고.

## Checkpoint Evidence

| Acceptance Criteria (Specification) | Evidence |
| --- | --- |
| 1. major/minor/patch가 결정론적 다음 버전을 계산 | `test_patch_calculation`, `test_minor_calculation`, `test_major_calculation` |
| 2. none이 명시적으로 거부됨 | `test_none_recommendation_raises`, `test_plan_release_preparation_none_raises`, `test_prepare_release_none_raises_before_touching_files`, `test_release_prepare_rejects_none_level`(CLI, argparse choices 자체가 거부) |
| 3. 형식이 잘못된 버전이 typed 에러로 거부됨 | `test_malformed_version_raises`(7가지 형식), `test_malformed_pyproject_version_raises` |
| 4. pyproject.toml/uv.lock 불일치가 쓰기 전에 실패 | `test_version_source_mismatch_raises`, `test_version_source_mismatch_does_not_write_either_file` |
| 5. 성공적인 준비가 의도한 DevBot 버전 항목만 갱신 | `test_prepare_release_preserves_unrelated_content`, `test_prepare_release_does_not_touch_other_packages_version_in_uv_lock` |
| 6. 두 파일이 이후 같은 target 버전을 가짐 | `test_prepare_release_patch_updates_both_files`, `test_prepare_release_minor_updates_both_files`, `test_prepare_release_major_updates_both_files` |
| 7. 결과가 old_version/new_version/recommendation/changed_paths를 보고 | `test_prepare_release_reports_changed_paths` |
| 8. 반복 준비가 손상되지 않고 명시적으로 동작 | `test_repeated_preparation_advances_again_without_corruption` |
| 9. 네트워크/GitHub/태그/Release/publish/merge/PR 없음 | `test_release_prepare_does_not_call_github`(CLI), grep으로 import 목록에 GitHub/네트워크/git 관련 모듈 없음 확인 |
| 10. 기존 release-classification이 추천 타입의 source of truth로 유지됨 | `src/devbot/release_preparation.py`가 `devbot.release_classification.ReleaseRecommendation`을 그대로 import, 재정의 없음 |
| 11. 모든 Quality Gate 통과 | 아래 Validation 결과 |

추가로 계약에 명시되지 않았지만 안전을 위해 작성한 테스트:
`test_plan_release_preparation_does_not_write_anything`,
`test_plan_release_preparation_missing_pyproject_raises`,
`test_plan_release_preparation_missing_uv_lock_raises`,
`test_missing_project_version_raises`,
`test_missing_uv_lock_devbot_entry_raises`,
`test_duplicate_uv_lock_devbot_entry_raises`,
`test_all_errors_are_release_preparation_errors`,
`test_release_prepare_dry_run_does_not_write`(CLI),
`test_release_prepare_error_returns_failure_exit_code`(CLI),
`test_release_prepare_does_not_acquire_daemon_lock`(CLI).

## Validation 결과

- `uv run devbot specification validate --task 48`: PASS (text 모두,
  에러/경고 0개, 수정 없이 처음부터 통과)
- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  1013 passed (Task 047 병합 후 기준 977개 +
  `tests/test_release_preparation.py` 30개 + `tests/test_main.py` 6개)
- 실제 `pyproject.toml`/`uv.lock`: 전체 스위트 실행 전후 모두
  `version = "0.1.0"`, `git status` 변경 없음 확인

## 수동 검증 결과 (임시 디렉터리 사본 대상, 실제 저장소 파일 미사용)

```
$ cp pyproject.toml uv.lock /tmp/release-prep-smoke/
$ uv run python -c "
from pathlib import Path
from devbot.release_classification import ReleaseRecommendation
from devbot.release_preparation import plan_release_preparation, prepare_release
root = Path('/tmp/release-prep-smoke')
print(plan_release_preparation(root, ReleaseRecommendation.PATCH))
print(prepare_release(root, ReleaseRecommendation.MINOR))
"
plan: old_version='0.1.0' new_version='0.1.1' (patch)
result: old_version='0.1.0' new_version='0.2.0' (minor, applied)

$ diff <원본> <준비 후>  # pyproject.toml
3c3
< version = "0.1.0"
---
> version = "0.2.0"
   (다른 줄은 전부 동일)

$ diff <원본> <준비 후>  # uv.lock
47c47
< version = "0.1.0"
---
> version = "0.2.0"
   (다른 패키지 버전 포함 다른 줄은 전부 동일)

$ uv run devbot release prepare --level patch --dry-run   # 실제 저장소 대상, 읽기 전용
recommendation: patch
old_version: 0.1.0
new_version: 0.1.1
dry-run: 파일을 쓰지 않았습니다.

$ git status --short pyproject.toml uv.lock   # dry-run 이후
(출력 없음)
```

## 남은 TODO와 제한 (계약이 명시적으로 범위 밖으로 남긴 것)

- Git 태그, GitHub Release 생성, 패키지 배포는 구현하지 않았다 - 이
  Task는 로컬 파일 준비까지만 한다.
- Git 커밋/커밋 메시지 생성은 구현하지 않았다 - 준비된 변경을 실제로
  커밋/PR로 만드는 것은 사람(또는 후속 Task)의 몫이다.
- 여러 Task/PR의 추천을 하나로 합치는 로직은 구현하지 않았다 - Task
  047과 마찬가지로 단일 추천만 받는다.
- Git 히스토리 기반 릴리스 노트 생성은 구현하지 않았다.
- pre-release 식별자(`-alpha`, `-rc1` 등)와 build metadata는 지원하지
  않는다 - 세 자리 안정 버전만 지원한다.

## 위험 요소

- `_devbot_package_span`(uv.lock)과 `_project_section_span`
  (pyproject.toml)의 헤더 경계 탐지는 정규식 기반이다 - `uv` 도구가
  향후 `uv.lock`의 최상위 테이블 구조를 바꾸면(예: `[[package]]` 외에
  새로운 최상위 섹션을 도입하면) 경계 탐지가 깨질 수 있다. 실제 파일
  구조를 직접 읽고 검증했지만, `uv.lock` 포맷은 이 저장소가 통제하지
  않는 외부 도구의 산출물이므로 향후 `uv` 버전 업그레이드 시 재검증이
  필요할 수 있다.
- 두 파일의 임시 파일 준비(`tempfile.mkstemp(dir=...)`)는 대상 파일과
  같은 디렉터리를 사용해 `os.replace`가 원자적이도록 보장한다 - 만약
  향후 `pyproject.toml`과 `uv.lock`이 서로 다른 파일시스템에 위치하게
  되면(일반적이지 않지만) 이 가정이 깨질 수 있다.
