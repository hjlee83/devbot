# Task 050 Result: Release Publish Strategy

## 구현 요약

Task 049로 두 개의 릴리스 게시 경로(`devbot release publish` — Task 037의
workflow-dispatch, `devbot release publish-prepared` — Task 049의 직접
태그+Release)가 공존하게 되면서 생긴 모호성을, 저장소마다 정확히 하나의
경로만 유효하도록 강제하는 정책 레이어로 닫았다.

새로 추가한 `src/devbot/release_publish_strategy.py`가 이 정책의 유일한
소유자다:

- `ReleasePublishStrategy(StrEnum)` — canonical 값 `workflow`/`direct`.
- `ReleasePublishStrategyResolution` — `repository`/`configured`/`effective`/
  `defaulted`를 담는 불변 결과.
- `resolve_release_publish_strategy(repository)` — 순수 함수(I/O 없음).
  값 파싱·기본값 결정·검증을 담당하는 유일한 곳.
- `require_workflow_strategy(repository)` / `require_direct_strategy
  (repository)` — 두 진입점이 쓰기 전에 호출해야 하는 가드. 구성상 최대
  하나만 성공한다.
- `ReleasePublishStrategyError` 계층 — `InvalidReleasePublishStrategyError`
  (알 수 없는/잘못된 설정값), `ReleasePublishStrategyMismatchError`(유효한
  값이지만 호출자가 요구하는 전략과 다름).

## 선택한 설정 형태

`RepositoryConfig`에 필드 하나만 추가했다:

```python
publish_strategy: str | None = None
```

- `repositories.yaml`의 저장소 항목에 `publish_strategy: workflow` 또는
  `publish_strategy: direct`를 선택적으로 쓸 수 있다.
- `config.py`의 `_load_repositories`는 이 값을 검증하거나 기본값을
  매기지 않고, `str(raw) if raw is not None else None`으로만 통과시킨다.
  파싱/기본값/검증 로직이 두 곳(로딩 시점과 사용 시점)에 나뉘면 서로
  어긋날 수 있으므로, 그 책임 전체를
  `release_publish_strategy.resolve_release_publish_strategy()` 한 곳에만
  두는 것이 Specification FR-005의 명시적 요구사항이었다.

## 기본값(defaulting) 근거

`publish_strategy`가 생략되면(`None`) `resolve_release_publish_strategy`는
`ReleasePublishStrategy.WORKFLOW`로 해석하고 `defaulted=True`를 반환한다.
`workflow`를 기본값으로 고른 이유:

- Task 037부터 지금까지 실제로 존재했던 유일한 경로이고, 모든 기존
  `repositories.yaml`이 이미 이 경로를 (암묵적으로) 쓰고 있었다.
- 생략을 `direct`로 기본값 매기면, 필드를 추가하기 전에 작성된 기존
  설정 파일들이 아무 변경 없이도 어느 날 갑자기 다른(더 위험한, 로컬
  Git 쓰기가 있는) 경로로 동작이 바뀌는 셈이 된다 — 이는 하위 호환성
  요구사항(FR-011)을 위반한다.
- `direct`는 Task 049 Specification의 Migration Notes가 명시한 대로,
  운영자가 그 경로를 이해한 뒤 명시적으로 opt-in해야 하는 경로로
  남긴다.

## 상호 배타성(mutual exclusion) 증거

`require_workflow_strategy`/`require_direct_strategy`는 둘 다
`resolve_release_publish_strategy`를 호출한 뒤 그 결과를 자신이 요구하는
값과 비교만 한다 — 별도의 판단 로직이 없다. 세 가지 유효 상태(생략,
`workflow`, `direct`) 각각에 대해 정확히 하나의 가드만 통과함을
`tests/test_release_publish_strategy.py::test_mutual_exclusivity_matrix`가
파라미터화 테스트로 직접 증명한다:

```python
@pytest.mark.parametrize("configured_strategy", [None, "workflow", "direct"])
def test_mutual_exclusivity_matrix(configured_strategy: str | None) -> None:
    ...
    assert workflow_accepted != direct_accepted, ...
```

잘못된 설정값(`bogus` 등)은 두 가드 모두에서 `InvalidReleasePublishStrategyError`로
실패한다 — `ReleasePublishStrategyMismatchError`(전략 불일치)로 잘못
분류되지 않는다는 것도 별도로 검증했다
(`test_invalid_configuration_fails_both_guards_before_a_mismatch_is_even_considered`).

## 가드 배치와 실패 동작

가드는 각 모듈에서 "실제로 모든 쓰기가 반드시 거치는 지점"에 두었다:

- **`release_ops.dispatch_release()`** — 이 함수의 기존 docstring이 이미
  스스로를 "모든 `devbot release publish` 경로가 거치는 유일한 안전
  관문"이라고 밝히고 있었다. `require_workflow_strategy(repository)`를
  함수 본문 첫 줄, 기존 `readiness` 검사보다도 먼저 추가했다. 저장소가
  `direct`로 설정돼 있으면 workflow dispatch가 한 번도 시도되지 않고
  `ReleasePublishStrategyMismatchError`가 즉시 발생한다.
- **`release_publish.preview_release_publish()`** — 함수 본문 첫 줄,
  `resolve_operator_checkout()` 호출보다도 먼저 `require_direct_strategy
  (repository)`를 추가했다. `publish_prepared_release()`는 항상 이
  함수를 먼저 호출하므로, 이 한 곳의 가드가 dry-run 미리보기와 실제
  게시 양쪽을 로컬 파일시스템/Git 접근 이전에 함께 막는다.
- **`devbot.main`의 CLI 레이어** — `release_ops.py`의 미리보기 함수
  (`fetch_release_preview`)는 게이트가 없는 `release preview` 명령과
  게이트가 있는 `release publish` 명령이 공유한다. 그래서 도메인
  함수 안에는 가드를 둘 수 없었다(두면 `release preview`까지 막힌다).
  대신 `_run_release_command`에서 `args.release_command == "publish"`일
  때만 `require_workflow_strategy`를 호출하도록 CLI 레벨에 중복
  가드를 하나 더 두었다 — `GitHubClient(...)`를 생성하기도 전에
  거부한다. `release_publish.py` 쪽은 `preview_release_publish` 하나만
  가드해도 두 명령(`preview`/`prepared`) 모두를 이미 커버하므로 이런
  중복이 필요 없었다.
- **읽기 전용 `devbot release strategy [--repo]`** — `resolve_release_publish
_strategy`만 호출하고 GitHub client를 전혀 만들지 않는다. `repository`/
  `configured`/`effective`/`defaulted`를 출력한다. 잘못된 설정값은
  `try/except ReleasePublishStrategyError`로 잡아 깨끗한 오류 메시지와
  종료 코드 1을 반환한다(구현 중 발견한 실제 버그 수정 — 아래 "구현 중
  발견한 문제" 참고).

모든 실패 경로는 예외를 던지는 시점 이전에 어떤 Git 쓰기·GitHub API 쓰기도
일어나지 않는다는 것이 계약(Contract)의 핵심 안전 요구였고, 아래
"수동 검증 결과"에서 이를 직접 확인했다.

## 구현 중 발견한 문제와 수정

`_run_release_strategy_command`를 처음 작성했을 때 `resolve_release_publish
_strategy(repository)` 호출에 예외 처리가 없어서, `repositories.yaml`에
잘못된 값(예: `publish_strategy: bogus`)이 있으면 CLI가 깔끔한 오류 대신
전체 Python traceback으로 죽었다. 임시 `repositories.yaml`을 이용한
스모크 테스트로 직접 발견했고, `try/except ReleasePublishStrategyError`로
감싸 종료 코드 1과 `release strategy 오류: ...` 메시지를 출력하도록
수정했다. 이 회귀를 막는 테스트가
`test_release_strategy_command_rejects_invalid_configuration`이다.

## 테스트와 명령 결과

### 새 테스트 파일

`tests/test_release_publish_strategy.py` — 24개, 전부 신규:
- 해석(resolution) 정확성: 생략→workflow, 명시적 workflow/direct, 알 수
  없는 값, 형식이 잘못된 문자열(대소문자/공백 변형 파라미터화), 비문자열
  값, 오류 메시지에 저장소명/값 포함 여부, 결정론성(같은 입력→같은 출력).
- 가드 정확성: 각 가드가 자신의 전략만 받아들이고 나머지는 거부, 잘못된
  설정이 두 가드 모두에서 (mismatch가 아니라) invalid로 실패.
- `test_mutual_exclusivity_matrix` — 세 가지 유효 상태 전체에 대한
  상호 배타성 증명.

### 기존 테스트 파일에 추가한 회귀 테스트

`tests/test_release_ops.py` (+2, 총 40개 통과):
- `test_dispatch_release_refuses_direct_strategy_before_any_dispatch` —
  `direct`로 설정된 저장소는 `dispatch_workflow`가 한 번도 호출되지
  않고 거부됨을 증명.
- `test_dispatch_release_accepts_explicit_workflow_strategy`.

`tests/test_release_publish.py` (+2, 총 19개 통과, 기존 17개는 픽스처
`_repository()`가 `publish_strategy="direct"`를 명시하도록 갱신):
- `test_preview_refuses_workflow_configured_repository` — `workflow`로
  설정된 저장소는 로컬 태그가 하나도 생기지 않고 거부됨을 증명
  (`_local_tags(local) == []`).
- `test_publish_refuses_omitted_default_strategy` — 필드를 생략한(기본값
  `workflow`인) 저장소는 `publish_prepared_release`가 태그도 Release도
  만들지 않고 거부됨을 증명(`write_client.create_release.assert_not
_called()`).

`tests/test_main.py` (+9, 총 73개 통과) — CLI 통합 테스트:
- `release strategy`가 생략/`workflow`/`direct` 설정 각각에 대해 올바른
  `repository`/`configured`/`effective`/`defaulted` 출력을 내는지 3개.
- `release strategy`가 잘못된 설정에 종료 코드 1과 한글 오류 메시지를
  내는지 1개(위 버그의 회귀 테스트).
- `release strategy`가 `GitHubClient`/`GitHubWriteClient`/`ProcessLock`을
  전혀 생성하지 않는지 1개.
- `release publish`가 `direct`로 설정된 저장소에서 `GitHubClient`/
  `fetch_release_preview`/`publish_release`를 전혀 호출하지 않고 종료
  코드 1로 거부하는지 1개.
- `release publish --dry-run`도 여전히 전략 불일치를 강제하는지 1개.
- `release publish-prepared`가 기본값(`workflow`)인 저장소에서 실제
  Mock 없이(진짜 호출 경로로) 종료 코드 1과 오류 메시지를 내는지 1개
  — 가드가 `preview_release_publish`의 첫 줄이라 파일시스템/Git 접근
  전에 거부되므로 임시 Git 저장소 없이도 안전하게 테스트 가능함을
  실증.
- `release publish-prepared --dry-run`도 여전히 전략 불일치를 강제하는지
  1개.

### 명령 실행 결과

```
$ uv run devbot specification validate --task 50
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1074 passed in 66.90s
```

(1074 = 이 태스크 이전 1037 + 신규/수정 37: `test_release_publish_strategy.py`
24개 + `test_release_ops.py` 2개 + `test_release_publish.py` 2개 +
`test_main.py` 9개.)

## 범위 제외 사항 (Contract/Specification이 명시적으로 범위 밖으로 남긴 것)

- 버전 계산/변경 — Task 048의 영역, 이 태스크는 건드리지 않는다.
- 실제 태그/Release/workflow-dispatch 쓰기 — 이 태스크 자신의 구현/테스트
  범위에는 포함되지 않는다(가드만 추가했고, 가드 뒤의 실제 쓰기 로직은
  Task 037/049가 이미 구현·검증했다).
- Task 047~050을 하나의 커맨드로 합치는 것 — 하지 않았다.
- 기존 두 퍼블리셔 중 하나를 제거하는 것 — 하지 않았다. `devbot release
  publish`(Task 037)와 `devbot release publish-prepared`(Task 049) 모두
  그대로 남아 있고, 저장소마다 정확히 하나만 유효하도록 게이트만
  추가했다.
- Task 051 오케스트레이션 작업 — 포함하지 않았다.

## 아키텍처 결정 업데이트

`docs/07-decisions.md`에 2026-07-19자 새 항목("One authoritative release
publish strategy per repository, resolved in exactly one place")을
추가해, Task 049 항목이 남긴 "두 경로가 공존한다"는 위험이 이 정책
레이어로 어떻게 닫히는지, 가드가 왜 그 위치들에 배치됐는지, CLI 레벨
중복 가드가 왜 필요했는지를 기록했다. `docs/00-roadmap.md`에도 Task 050
항목을 같은 스타일로 추가했다.

## 수정/추가 파일

- `src/devbot/release_publish_strategy.py` (신규)
- `src/devbot/models.py` (`RepositoryConfig.publish_strategy` 필드 추가)
- `src/devbot/config.py` (`publish_strategy` 로딩, 검증 없이 원시값 통과)
- `src/devbot/release_ops.py` (`dispatch_release`에 `require_workflow_strategy` 가드)
- `src/devbot/release_publish.py` (`preview_release_publish`에 `require_direct_strategy` 가드)
- `src/devbot/main.py` (`release strategy` 서브커맨드, `publish` CLI 레벨 가드, `publish-prepared` 예외 처리 확장)
- `tests/test_release_publish_strategy.py` (신규, 24개)
- `tests/test_release_ops.py` (+2)
- `tests/test_release_publish.py` (+2, 픽스처 갱신)
- `tests/test_main.py` (+9, `_release_env` 헬퍼에 `publish_strategy` 파라미터 추가)
- `specifications/050-release-publish-strategy.md` (canonical 8-섹션 구조로 재구성)
- `docs/07-decisions.md`, `docs/00-roadmap.md` (Task 050 항목 추가)
