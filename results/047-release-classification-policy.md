# Task 047 Result: Release Classification Policy

## 완료 내용

Task 046의 Contract Metadata Engine 위에 첫 정책 소비자를 구현했다 - 새
`src/devbot/release_classification.py`가 typed `ContractParseResult`를
받아 `major`/`minor`/`patch`/`none` 릴리스 추천을 결정론적으로 계산하는
순수 함수 `classify_release`를 제공한다.

- **타입화된 추천 모델**: `ReleaseRecommendation(StrEnum)` -
  `MAJOR`/`MINOR`/`PATCH`/`NONE`(canonical lowercase 토큰), 전용 예외
  `ReleaseClassificationError` 기반의 `LegacyContractClassificationError`/
  `MissingContractMetadataError` 2종.
- **우선순위 규칙**(Specification의 Required Behaviour/Classification
  Table을 정확히 구현):
  1. legacy Contract -> `LegacyContractClassificationError`(추측하지
     않음)
  2. metadata 없는 native 결과 -> `MissingContractMetadataError`
  3. `compatibility == BREAKING` -> `major`
  4. `migration == REQUIRED` -> `major`
  5. `release_impact == BREAKING` -> `major`
  6. `release_impact == FEATURE` -> `minor`
  7. `release_impact == FIX` -> `patch`
  8. `release_impact`가 `DOCS`/`INTERNAL`/`NONE` -> `none`
- **`risk_level`/`specification_type`은 절대 참조하지 않는다**:
  `classify_release`의 코드 자체가 이 두 필드를 읽지 않으므로 구조적으로
  결과에 영향을 줄 수 없다 - `RiskLevel` 3개 x `SpecificationType` 6개
  = 18개 조합을 파라미터화 테스트로 직접 증명했다(`release_impact`를
  고정한 채 나머지 두 필드만 바꿔도 추천이 절대 바뀌지 않음).
- **순수/부작용 없음**: Markdown을 다시 읽지 않고(Task 046의 typed
  결과만 소비), 버전 파일/Git 태그/GitHub Release/릴리스 노트 등
  어떤 외부 상태도 건드리지 않는다 - `import` 목록에 파일시스템/
  네트워크/GitHub 관련 모듈이 전혀 없음을 확인했다(아래 참고).

## 주요 설계 결정

- **`_RELEASE_IMPACT_TO_RECOMMENDATION`을 dict lookup으로 구현했다.**
  Classification Table을 그대로 옮긴 6개 항목짜리 딕셔너리이며, 두
  breaking override(`compatibility`/`migration`)를 먼저 확인한 뒤에만
  참조한다 - Specification이 명시한 "override 조건을 먼저 평가한다"는
  순서를 코드 구조 자체로 강제한다(override를 지나치면 dict lookup만
  남으므로 순서를 헷갈릴 여지가 없다).
- **`MissingContractMetadataError`는 정상적인 사용자 입력이 아니라
  불변식 방어용이다.** `ContractParseResult.metadata`가 `SCHEMA_V1`인데도
  `None`인 상태는 Task 046의 `parse_contract_metadata`가 정상적으로는
  절대 만들지 않는 조합이지만(native 성공 시 항상 metadata를 채움),
  타입 시스템(`ContractMetadata | None`)이 이를 허용하므로 fail
  closed로 방어했다 - "guess하지 않는다"는 Safety 원칙을 타입 수준의
  edge case까지 확장했다.
- **legacy와 missing-metadata를 서로 다른 예외로 분리했다.** 둘 다
  "분류할 metadata가 없다"는 점은 같지만, legacy는 정상적으로 발생하는
  입력(강제 마이그레이션이 없는 기존 Contract)인 반면 missing-metadata는
  Task 046 쪽 불변식이 깨졌을 때만 발생해야 하는 방어적 케이스다 -
  호출자가 둘을 구분해서 처리할 수 있도록 타입을 분리했다(둘 다
  공통 `ReleaseClassificationError`로 묶어서 한 번에 잡을 수도 있다).

## Specification 소소한 수정 (Task 045/046과 같은 패턴, 이번엔 작음)

구현 전 `devbot specification validate --task 47`을 실행했더니 이번엔
에러 2개뿐이었다 - Task 045/046에서 확립한 canonical 8-섹션 구조를
이미 제대로 따르고 있었다(이전 두 Task의 리뷰 피드백이 반영된 것으로
보인다). 고친 것:

- Provenance에 "생성 출처" 문장 추가(Task 045/046과 동일한 반복
  패턴 - `SPV-002`가 "Issue"/"Contract"/"Generat" 세 키워드를 모두
  요구함).
- `# Validation`에 `## Validation Commands` 서브섹션이 따로 없고
  `## Required Tests and Quality Gates` 안에 bash 블록이 섞여 있었다 -
  블록을 분리해 `## Validation Commands`로 독립시켰다(`## Required
  Tests and Quality Gates`는 필수 테스트 목록만 남기고 유지).

`# Full Task Contract Reference`가 `tasks/047-release-classification-policy.md`
원문과 바이트 단위로 일치하는지 Python diff로 확인했다(수정 전부터
이미 일치 - 이번엔 건드리지 않았다).

## 수정 파일

- `src/devbot/release_classification.py` (신규)
- `specifications/047-release-classification-policy.md` (Provenance
  생성 출처 + `## Validation Commands` 서브섹션만 추가, 나머지 내용은
  이미 canonical 구조였으므로 무변경)
- `tests/test_release_classification.py` (신규, 44개 테스트)
- `docs/00-roadmap.md` (Task 047 항목 추가)
- `results/047-release-classification-policy.md` (본 문서)

`tasks/047-release-classification-policy.md`는 수정하지 않았다(이미
브랜치에 존재하던 권위 있는 소스, Schema v1 형식으로 이미 작성돼
있었다).

## Checkpoint Evidence

| Acceptance Criteria (Specification) | Evidence |
| --- | --- |
| 1. Native 입력이 결정론적으로 하나의 추천을 반환 | `test_each_release_impact_value_maps_correctly`, `test_deterministic_repeated_classification`, `test_real_schema_v1_contract_classifies_end_to_end` |
| 2. compatibility/migration breaking override가 release_impact보다 우선 | `test_compatibility_breaking_overrides_every_non_major_impact`(5종), `test_migration_required_overrides_every_non_major_impact`(5종), `test_multiple_major_conditions_at_once_still_yields_major` |
| 3. 6개 canonical release_impact 값 전부 커버 | `test_each_release_impact_value_maps_correctly`(6종) |
| 4. legacy 입력이 전용 typed 에러로 실패 | `test_legacy_input_raises_dedicated_error`, `test_legacy_classification_error_is_a_release_classification_error`, `test_real_legacy_contract_raises_end_to_end` |
| 5. metadata 없는 native 입력이 fail closed | `test_native_result_with_missing_metadata_raises`, `test_missing_metadata_error_is_a_release_classification_error` |
| 6. Markdown을 다시 파싱하지 않음 | 코드 검토 - `classify_release`는 `ContractParseResult`만 받고 `str`/파일 인자가 없음; `test_real_schema_v1_contract_classifies_end_to_end`가 파싱은 Task 046 쪽에서만 일어남을 보여줌 |
| 7. 버전/GitHub/파일시스템/네트워크 상태 변경 없음 | grep으로 import 목록 확인(아래) |
| 8. Task 046 모델이 유일한 source of truth | `src/devbot/release_classification.py`가 `devbot.contract_metadata`의 enum/모델을 그대로 import, 재정의 없음 |
| 9. Result 문서와 전체 Quality Gate 통과 | 아래 Validation 결과 |
| (Required Behaviour) risk_level/specification_type이 추천에 영향 없음 | `test_risk_level_and_specification_type_never_alter_recommendation`(18개 조합) |

## Validation 결과

- `uv run devbot specification validate --task 47`: PASS (text/json 모두,
  에러/경고 0개)
- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  977 passed (Task 046 리뷰 반영 후 기준 933개 +
  `tests/test_release_classification.py` 44개)
- `grep -n "^import\|^from" src/devbot/release_classification.py`:
  `__future__`, `enum.StrEnum`, `devbot.contract_metadata`뿐 - 파일시스템/
  네트워크/GitHub/CLI 관련 import가 전혀 없음을 확인했다.

## 수동 검증 결과 (읽기 전용, 실제 저장소 대상)

```
$ uv run python -c "
from devbot.contract_metadata import parse_contract_metadata
from devbot.release_classification import classify_release, LegacyContractClassificationError
from pathlib import Path

for slug in ['046-contract-metadata-engine', '047-release-classification-policy']:
    result = parse_contract_metadata(Path(f'tasks/{slug}.md').read_text(encoding='utf-8'))
    rec = classify_release(result)
    print(slug, '->', rec, '(release_impact=', result.metadata.release_impact, ')')

legacy = parse_contract_metadata(Path('tasks/037-release-operator-ux.md').read_text(encoding='utf-8'))
try:
    classify_release(legacy)
    print('FAIL: expected LegacyContractClassificationError')
except LegacyContractClassificationError as e:
    print('OK legacy rejected:', e)
"
046-contract-metadata-engine -> none (release_impact= internal )
047-release-classification-policy -> none (release_impact= internal )
OK legacy rejected: legacy Contracts declare no Contract Schema v1 metadata to classify
```

Task 046/047 자신의 실제 Contract가 둘 다 `release_impact: internal`이므로
`none` 추천을 정확히 반환했고, 실제 legacy Contract(Task 037)는 추측 없이
전용 에러로 거부됐다.

## 남은 TODO와 제한 (계약이 명시적으로 범위 밖으로 남긴 것)

- 실제 버전 문자열 계산(`major.minor.patch` 다음 값)은 구현하지
  않았다 - `ReleaseRecommendation`은 방향(major/minor/patch/none)만
  나타낸다.
- 여러 Task/PR의 추천을 하나로 합치는 로직은 구현하지 않았다 -
  `classify_release`는 Contract 하나만 받는다.
- Git 태그, GitHub Release, 릴리스 노트 생성, 버전 파일 변경은 전혀
  구현하지 않았다 - 이 정책은 추천만 하고 실행은 하지 않는다.
- CLI 명령을 추가하지 않았다 - 계약이 "기존 저장소 관례상 불가피한
  경우가 아니면 기본은 CLI 없음"이라고 명시했고, 그런 불가피한 사례를
  찾지 못했다.

## 위험 요소

- `MissingContractMetadataError`가 방어하는 상태(native인데 metadata가
  `None`)는 현재 Task 046 코드로는 절대 만들어지지 않는다 - 향후 누군가
  `parse_contract_metadata`를 수정해 이 불변식을 깨면, 이 정책은
  이미 fail closed로 대응하도록 준비돼 있지만, 그 코드 변경 자체가
  Task 046의 다른 불변식도 함께 깨뜨릴 수 있으므로 Task 046을 수정하는
  사람은 이 가정을 알고 있어야 한다.
- Classification Table이 하드코딩된 6-항목 딕셔너리이므로, 향후 Task
  045/046이 `ReleaseImpact` enum에 새 값을 추가하면 이 딕셔너리도 함께
  갱신해야 한다 - 갱신하지 않으면 `KeyError`(처리되지 않은 예외)가
  발생한다. 이는 의도적으로 "새 enum 값을 조용히 무시하지 않고 즉시
  드러나게" 하기 위한 설계이지만, 향후 유지보수자가 이 결합을 인지해야
  한다.
