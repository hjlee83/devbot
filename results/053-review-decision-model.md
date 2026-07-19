# Task 053 Result: Review Decision Model

## 구현 요약

`src/devbot/review_decision.py`를 신규 작성했다 — GitHub과 무관하고,
결정론적이고, 타입이 있는 리뷰 결과 모델이다. 다른 devbot 모듈에
의존하지 않는 완전히 독립적인 도메인 모듈이다. CLI로는 읽기 전용
`devbot review report --input report.json [--format text|json]`을
추가했다 — 기존 report JSON 파일을 검증·렌더링만 한다.

## 타입 모델

- `ReviewDecision(StrEnum)`: `approved`/`changes_required`/`comment_only`.
- `ReviewSeverity(StrEnum)`: `blocker`/`warning`/`comment`.
- `ReviewLocation` (frozen dataclass): path/line/start_line/side/symbol,
  모두 선택. 생성 시점(`__post_init__`)에 즉시 검증.
- `ReviewFinding` (frozen dataclass): severity/code/message/location.
  생성 시점에 code/message 비어있음 검증.
- `ReviewReport` (frozen dataclass): decision/findings/counts/summary/
  metadata. 생성 시점에 decision/counts가 findings로부터 파생된 값과
  정확히 일치하는지, 그리고 중복 finding이 없는지 재검증.
- 오류 계층: `ReviewDecisionError`(base) → `InvalidReviewLocationError`,
  `InvalidReviewFindingError`, `AmbiguousReviewFindingsError`,
  `InvalidReviewReportPayloadError`, `ReviewReportDerivationMismatchError`.

## 결정 도출 정책

`_derive_decision`/`_derive_counts` 두 순수 함수가 유일한 도출 로직이다:

- blocker가 하나 이상 → `changes_required`.
- blocker 없고 warning/comment가 하나 이상 → `comment_only`.
- findings 없음 → `approved`.

**counts와 decision은 findings로부터만 파생되고, 절대 독립적으로
신뢰되지 않는다.** 이 불변식을 두 곳에서 강제한다:

1. `build_review_report`(권장 생성 경로) — findings만 받아서 항상
   새로 도출한다. 호출자가 decision/counts를 직접 넘길 방법이 없다.
2. `ReviewReport.__post_init__` — `build_review_report`를 우회해서
   `ReviewReport(...)`를 직접 생성해도, 넘긴 decision/counts가 findings로부터
   다시 도출한 값과 다르면 `ReviewReportDerivationMismatchError`로 즉시
   거부한다. 이는 `ReviewReport`가 어떤 경로로 만들어지든 이 불변식이
   깨질 수 없게 한다(스모크 테스트로 직접 검증: 잘못된 decision/counts로
   직접 생성 시도 → 즉시 거부).

## 리뷰 반영: 생성 후 외부 mutation에 대한 진짜 불변성 (PR #112, hjlee83)

리뷰에서 실제 버그를 발견했다: `@dataclass(frozen=True)`는 **속성
재할당**만 막지, 그 속성에 저장된 mutable 객체(list/dict) 자체의
mutation은 막지 않는다. `ReviewReport.__post_init__`이 넘겨받은
`findings`/`counts`/`metadata`를 그대로 저장하고 있어서, `ReviewReport`를
직접 생성할 때 원본 list/dict를 넘기면 생성 이후에도 외부에서 그
원본을 바꿔서 `report.counts`/`report.findings`가 조용히 바뀔 수
있었다 — `report.decision`은 그대로인 채로. 직접 재현해서 확인했다:

```python
findings = []
counts = {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
report = ReviewReport(decision=ReviewDecision.APPROVED, findings=findings, counts=counts)
findings.append(ReviewFinding(...BLOCKER...))
counts[ReviewSeverity.BLOCKER] = 1
# 수정 전: report.decision은 여전히 approved인데 report.counts는 blocker=1을 보고함
```

**수정**: `ReviewReport.__post_init__`이 이제 모든 입력을 `object.__setattr__`로
정규화한다:
- `findings` → 각 원소가 진짜 `ReviewFinding` 인스턴스인지 검증한 뒤,
  중복 검사·정렬을 거쳐 새 `tuple`로 교체(원본 리스트와 완전히 독립).
- `counts` → 정확히 3개 enum 키만 가지고, 각 값이 int인지 검증한 뒤
  findings로부터 재도출한 값과 대조, 새 `MappingProxyType`으로 교체.
- `metadata` → 문자열 키/값인지 검증한 뒤 복사한 새 `MappingProxyType`으로
  교체(있는 경우).

이제 원본이 어떻게 바뀌어도 이미 생성된 `report`는 전혀 영향받지 않는다.
`build_review_report`는 이 정규화 로직을 중복하지 않도록 단순화했다 —
findings/counts만 계산해서 `ReviewReport(...)`에 넘기고, 실제 검증·정렬·
불변화는 전부 `__post_init__` 한 곳에서만 일어난다(단일 진실 공급원).

검증: `test_mutating_original_findings_list_after_construction_does_not
_affect_report`, `test_mutating_original_counts_dict_after_construction
_does_not_affect_report`, `test_mutating_original_metadata_dict_after
_construction_does_not_affect_report` — 생성 후 원본을 바꿔도 report가
그대로임을 직접 확인. `test_direct_construction_findings_element_wrong
_type_rejected`, `test_direct_construction_counts_non_int_value_rejected`,
`test_direct_construction_counts_not_a_mapping_rejected`,
`test_direct_construction_metadata_not_a_mapping_rejected`,
`test_direct_construction_metadata_non_string_value_rejected`,
`test_direct_construction_decision_wrong_type_rejected` — 직접 생성 시
잘못된 타입/원소가 각각 타입 있는 오류로 fail closed됨을 확인.

## Location 검증

`ReviewLocation`은 생성 시점에 다음을 즉시 거부한다:
- 절대 경로(`/`로 시작)나 `..` 상위 디렉터리 탐색이 포함된 path.
- 빈 문자열이거나 앞뒤 공백이 있는 path.
- 1 미만의 line/start_line.
- start_line이 line보다 큰 경우(잘못된 범위).
- line 없이 start_line만 있는 경우.
- path 없이 line/start_line만 있는 경우(파일 없는 줄 번호는 의미 없음).

`side`/`symbol`은 문자열이면 자유롭게 허용한다(GitHub 전용 diff-side
개념에 결합하지 않고 provider-neutral하게 유지).

## 중복 처리

"같은 stable identity"를 (severity, code, message, location) 전체
동일로 정의했다 — 넷 다 완전히 같아야 진짜 중복(모호함)으로 보고
`AmbiguousReviewFindingsError`로 거부한다. code만 같고 message나
location이 다르면(예: 같은 lint 코드가 서로 다른 줄에서 발생) 정당한
별개 finding으로 허용한다 — 이 구분을 스모크 테스트와 pytest 양쪽에서
직접 검증했다.

## 결정론적 정렬

`_sort_key`가 유일한 정렬 로직이다: severity 우선순위(blocker=0,
warning=1, comment=2) → code → location(path, line, start_line) →
message 순. Python의 안정 정렬(`sorted`)을 쓰므로 완전 동일한 키를 가진
항목은 입력 순서를 보존한다. 같은 findings를 다른 입력 순서로 넣어도
결과 순서가 동일함을 테스트로 확인했다.

## JSON 직렬화/역직렬화

`review_report_to_dict`는 고정된 키 순서로 dict를 만든다(Python dict는
삽입 순서를 보존하므로 항상 결정론적). `review_report_from_dict`는:

1. 각 필드의 타입을 엄격히 검증(payload가 dict인지, findings가 list인지,
   각 finding의 severity가 유효한 enum 값인지, code/message가 문자열인지 등).
2. 검증된 findings로 `build_review_report`를 호출해 decision/counts를
   **다시 도출**한다.
3. payload가 `decision`이나 `counts`를 스스로 선언했다면, 그 값을
   신뢰하지 않고 방금 도출한 값과 대조한다 — 모순되면
   `ReviewReportDerivationMismatchError`로 거부한다.

`validate_review_report_payload`는 `review_report_from_dict`를 호출하고
결과를 버리는 얇은 래퍼다(성공 시 `None` 반환, 실패 시 예외).

## 결정론적 텍스트 렌더링

`render_review_report`는 항상 decision, 세 severity 모두의 count, 각
finding(severity/code/message/location)을 포함한다. 같은 report를
반복 렌더링하면 항상 동일한 문자열이 나옴을 테스트로 확인했다.

## CLI

```
devbot review report --input report.json [--format text|json]
```

`--input` 파일 하나만 읽는다 — GitHub client도, write client도 생성하지
않는다(`tests/test_main.py`의 mock 기반 테스트로 직접 확인). 유효한
report는 종료 코드 0, 손상되거나 모순된 payload는 종료 코드 1과 함께
구체적인 오류 메시지를 낸다.

## 범위 제외 사항

Contract/Specification이 명시한 대로 다음은 이번 태스크에 포함하지
않았다: GitHub Review API 호출(승인/변경요청 제출), GitHub App
인증/토큰 처리, LLM으로 finding 생성, PR 자동 수정 요청, merge
readiness/branch protection/auto-merge, GitLab/Jira 어댑터. `approved`
→ GitHub `APPROVE` 등의 provider 매핑도 의도적으로 여기 구현하지
않았다 — 이 태스크는 순수 결정 모델만 정의한다.

## 테스트와 명령 결과

```
$ uv run devbot specification validate --task 53
PASS (0 errors, 0 warnings)

$ uv run ruff check .
All checks passed!

$ uv run pytest -q
1228 passed in 63.08s
```

- `tests/test_review_decision.py` (신규+리뷰 수정, 70개): Location 검증(절대
  경로/상위 탐색/빈 경로/공백/잘못된 줄 범위/line-without-path 등
  10여 개), Finding 검증(빈 code/message, 잘못된 severity 타입),
  각 severity별 decision 도출, blocker 우선순위, counts 파생과
  MappingProxyType 불변성, 결정론적 정렬(severity→code, 반복 호출 간
  일관성), 정확한 중복만 모호함으로 거부하고 code-only-same은 허용,
  `ReviewReport` 직접 생성 우회 시도가 모두 거부됨(잘못된 decision/
  counts/중복 findings 각각), **생성 후 원본 list/dict를 mutation해도
  report가 불변임을 확인하는 회귀 테스트 3개**, **직접 생성 시 잘못된
  타입/원소가 각각 거부됨을 확인하는 테스트 6개**, JSON 완전 왕복
  (location/summary/metadata 포함·미포함 둘 다), 다양한 malformed
  payload(non-dict, findings non-list, 잘못된 severity/타입, 빈 code)
  fail-closed, 선언된 decision/counts가 파생값과 모순되면 거부하고
  일치하면 통과, 결정론적 렌더링, 오류 계층.
- `tests/test_main.py` (+8): text/json 출력, 빈 findings 승인, malformed
  JSON 파일, invalid payload, 모순된 declared decision, 입력 파일
  누락, GitHub client/write client/daemon lock 미생성.
- 전체 스위트 1228개 회귀 없이 통과.

## 수정/추가 파일

- `src/devbot/review_decision.py` (신규, 리뷰 수정으로 `ReviewReport
  .__post_init__` 전면 재작성)
- `src/devbot/main.py` (`review report` 서브커맨드 + 핸들러)
- `tests/test_review_decision.py` (신규+리뷰 수정, 70개)
- `tests/test_main.py` (+8)
- `specifications/053-review-decision-model.md` (canonical 8-섹션
  구조로 재구성)
- `docs/00-roadmap.md` (Task 053 항목 추가)
