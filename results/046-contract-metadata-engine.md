# Task 046 Result: Contract Metadata Engine

## 리뷰 반영 (PR #97, hjlee83)

아키텍처 리뷰에서 blocking 지적을 2건 받았다:

1. **필수 Provenance가 파싱/반환되지 않음.** Task 046 Specification의
   Required Behaviour는 native 결과가 "provenance fields required by
   Schema v1"을 포함해야 한다고 명시하는데, 원래 구현은 `## Provenance`
   섹션이 존재하는지만 확인했을 뿐 본문을 전혀 파싱하지 않았다 -
   `ContractParseResult`에 Provenance 모델도, GitHub Issue/Branch 값도
   없었다. 비어 있거나 잘못된 Provenance도 그냥 통과했다.
2. **Task Identity 일관성 검사가 `id`만 확인하고 `title`은 무시함.**
   Task 045 Field Definitions는 "heading과 Task Identity 값이 일치해야
   한다"고 요구하는데, `_parse_task_identity()`가 `title` 필드를 전혀
   읽지 않아 `- title:`이 없거나 heading과 달라도 조용히 통과하고
   heading의 title을 그대로 썼다.

반영 내용:

- 새 불변 `ContractProvenance(github_issue, branch)` 모델을 추가하고,
  `ContractParseResult`에 `provenance` 필드를 추가했다(legacy는
  `None`, 기존 `metadata=None` 패턴과 동일).
- `## Provenance` 본문에서 `- GitHub Issue: #NN`과 `` - Branch: `...` ``
  두 필수 항목을 정확히 한 번씩 파싱한다 - 누락되면
  `MissingProvenanceFieldError`, 중복되면
  `DuplicateProvenanceFieldError`, GitHub Issue가 `#<숫자>` 형식이
  아니거나 Branch가(백틱을 벗겨낸 뒤) 비어 있으면
  `MalformedProvenanceFieldError`로 fail closed한다. 이 3개는 Task
  046 Error Model이 원래 나열한 9종에는 없던, 이번 리뷰로 새로 추가한
  전용 예외다 - Epic/Current Release 같은 선택 항목은 모델링하지
  않는다(리뷰가 요구한 "required" 항목만).
- `_parse_task_identity()`가 이제 `id`와 `title` 둘 다 정확히 한 번씩
  요구하고, 둘 다 heading과 비교한다 - 어느 쪽이 다르든
  `TaskIdentityMismatchError` 하나로 fail closed한다(새 예외 타입을
  추가하지 않고 기존 타입을 재사용 - 두 경우 모두 "Task Identity가
  heading과 불일치"라는 같은 종류의 결함이므로). `title` 누락/중복도
  이제 `id`와 동일하게 `Missing`/`DuplicateMetadataFieldError`로
  거부한다(이전에는 애초에 검사하지 않았음).
- 회귀 테스트 16개 추가: Provenance 파싱/누락/중복/형식 오류(GitHub
  Issue 4가지 잘못된 형식 포함)/선택 항목 무시, Task Identity title
  불일치/누락/중복, 그리고 기존 valid-contract 테스트들에 provenance
  값 검증을 추가했다.
- `tasks/046-contract-metadata-engine.md`(Full Task Contract Reference에
  인용된 원본)는 건드리지 않았다.
- 재구성 중 실제 Task 046 Contract로 다시 파싱해 `provenance=
  ContractProvenance(github_issue='#96', branch=
  'task/046-contract-metadata-engine')`가 정확히 나오는 것을 직접
  확인했다(아래 "수동 검증 결과" 참고).

## 완료 내용

Task 045의 Contract Schema v1 위에 실제 런타임 엔진을 구현했다 - 새
`src/devbot/contract_metadata.py`가 `parse_contract_metadata(text) ->
ContractParseResult` 하나로 Task Contract Markdown을 결정론적으로
타입화된 메타데이터로 파싱하는 유일한 컴포넌트다.

- **타입화된 도메인 모델**: `ContractKind`(`schema_v1`/`legacy`),
  `SpecificationType`/`ReleaseImpact`/`RiskLevel`/`Compatibility`/
  `Migration`(Task 045의 5개 canonical enum과 정확히 일치하는 값),
  `ContractMetadata`(5개 typed 필드 + `extensions` 불변 매핑),
  `ContractParseResult`(kind/task_id/title/contract_version/metadata) 전부
  `StrEnum`/`frozen dataclass`로 구현했다.
- **Schema v1 파싱**: `Contract Version`/`Task Identity`/`Metadata`를
  포함한 Task 045의 11개 필수 섹션을 정확한 이름으로 인식하고,
  Metadata의 `- field: value` 줄을 파싱해 5개 핵심 필드를 enum으로
  변환한다.
- **`contract_version` 검증**: 값이 정확히 하나의 양의 정수가 아니면
  `MalformedContractVersionError`, 정수이지만 지원하지 않는 값(현재
  `1`만 지원)이면 `UnsupportedContractVersionError`로 fail closed한다.
- **canonical 필드명/enum 값 검증**: 5개 핵심 필드 중 하나가 없으면
  `MissingMetadataFieldError`, 같은 필드가 두 번 나오면
  `DuplicateMetadataFieldError`, `x-` 접두사가 없는 알 수 없는 필드는
  `UnknownMetadataFieldError`, enum에 없는 값은
  `InvalidMetadataValueError`로 각각 구분해 fail closed한다.
- **native Schema v1과 legacy 구분**: `## Contract Version` 섹션이 아예
  없으면 `ContractKind.LEGACY`(`metadata=None`)로 명시적으로 분류한다 -
  에러가 아니고 강제 마이그레이션도 요구하지 않는다. 반대로 일단
  `Contract Version` 섹션이 하나라도 존재하면, 그 뒤로 형식이 잘못됐거나
  미지원 버전이어도 절대 legacy로 조용히 되돌아가지 않고 항상 예외를
  던진다(`test_malformed_version_does_not_silently_fall_back_to_legacy`로
  고정 - Safety 요구사항 "Do not silently downgrade malformed versioned
  Contracts to legacy"를 그대로 구현).
- **`x-` 확장 필드 보존**: `x-`로 시작하는 필드는 별도의 불변
  `MappingProxyType`에 원본 문자열 그대로 보존되며, 핵심 5개 필드
  해석에는 어떤 경로로도 관여하지 않는다 - `x-specification_type: bogus`
  같은 필드가 동시에 존재해도 실제 `specification_type` 값은 전혀
  영향받지 않음을 직접 테스트로 확인했다
  (`test_extension_cannot_shadow_or_override_a_core_field`).
- **Task Identity 일관성 검증**: Contract 제목 헤딩의 Task 번호와 `##
  Task Identity`의 `id` 필드가 다르면 `TaskIdentityMismatchError`로
  fail closed한다.

## 주요 설계 결정

- **파서를 Schema v1을 해석하는 유일한 컴포넌트로 만들었다.** 기존
  `devbot.specification`(Task 042/043의 Contract/Specification 파서)과는
  의도적으로 다른 파싱 semantics를 쓴다 - `devbot.specification`은
  중복 헤딩을 관대하게 이어붙이지만(bilingual Specification 생성이라는
  다른 목적), Contract Metadata Engine은 중복 섹션을 반드시 에러로
  거부해야 한다(Task 045 Field Definitions: 각 필수 섹션은 "정확히
  하나"). 그래서 `_split_top_level_sections`를 새로 작성해 헤딩 발생
  순서를 그대로 리스트로 반환하고(병합하지 않음), 호출부에서 중복
  여부를 직접 판단하게 했다 - 다만 fence 인식 로직(`fenced_code_ranges`,
  Task 042/043에서 이미 public)은 그대로 재사용해 `Quality Gates`
  섹션 안에 예시 코드 블록이 있어도 안전하다.
- **알 수 없는 최상위 섹션은 허용한다.** Task 045가 PR #95 리뷰 이후
  명시한 호환성 원칙("Schema v1의 Normative Contract Structure는 자신의
  13개 영역 사이의 canonical 순서만 고정하며, `docs/09`가 요구하는 추가
  섹션(Risk 등)이 공존하는 것을 막지 않는다")을 그대로 구현했다 - 파서는
  11개 필수 섹션의 존재/중복 여부만 검사하고, 그 외의 임의의 추가 `##`
  섹션(Risk, Rollback Strategy 등)은 조용히 무시한다
  (`test_unknown_non_canonical_top_level_section_is_tolerated`).
- **제목 구분자를 관대하게 받는다.** Task 045의 Normative Example은
  em dash(`—`)를 쓰지만, 이 저장소의 실제 기존 Contract(Task
  001-044)는 대부분 콜론(`:`)을 쓴다. `_TITLE_RE`가 `[—:-]` 세 가지
  구분자를 모두 인식하도록 해 실제 저장소 데이터와 호환되게 했다
  (`test_title_separator_variants_all_parse`).
- **Task Identity의 title 텍스트 일치는 검증하지 않는다.** Task 045
  Field Definitions는 "heading과 Task Identity 값이 일치해야 한다"고
  포괄적으로 말하지만, Task 046 Specification의 더 구체적인 Parsing
  Rules 절은 "title task ID와 Task Identity id가 일치해야 한다"고만
  명시한다 - 더 구체적인 규칙을 따라 `id` 일치만 검증하고 title 텍스트
  비교는 추가하지 않았다(명시되지 않은 요구사항을 지어내지 않기 위한
  의도적 선택).

## Specification 재구성 (Task 045와 동일한 패턴)

구현 전 `specifications/046-contract-metadata-engine.md`를 실제로
검증했더니(`devbot specification validate --task 46`) 11개 에러로
실패했다 - `# Overview`의 `## Roadmap Context`, `# Technical Design`의
`## Dependencies`/`## Constraints`/`## Migration Notes`, `# Validation`의
`## Required Tests and Quality Gates`, `# Completion`의 `## Expected
Deliverables`/`## Result Document`/`## PR Expectations`, `# Handoff`의
`## Required Handoff Procedure`/`## Token-Limit Behaviour` 서브섹션이
빠져 있었고, Provenance에 생성 출처가 없었고, `# Full Task Contract
Reference`가 실제 Contract 본문이 아니라 경로 문자열 한 줄뿐이었다.

Task 046의 Quality Gates 자체가 `uv run devbot specification validate
--task 46`을 명시적으로 요구하므로, Task 045에서 확립한 것과 같은
방식으로 - 기존 내용을 전혀 삭제하지 않고 canonical 8-섹션 envelope
으로 재배치 - 고쳤다. 빠졌던 서브섹션은 Contract의 대응 절(Deliverables/
Handoff/References 등)에서 그대로 가져와 채웠고, `# Full Task Contract
Reference`는 `tasks/046-contract-metadata-engine.md` 전체를 verbatim
인용했다 - Python으로 직접 diff해 완전히 일치함을 확인했다(아래 참고).
재구성 과정에서 프로즈 안에 실수로 독립된 ` ``` ` 마커가 섞여 들어가
fence 추적을 깨뜨릴 뻔한 것을 작성 중 직접 발견해 고쳤다(Dependencies
절의 한 문장을 재작성).

## 수정 파일

- `src/devbot/contract_metadata.py` (신규)
- `specifications/046-contract-metadata-engine.md` (재구성 - Task
  042/043 canonical Specification 스키마를 만족하도록 누락된 서브섹션
  추가, 내용은 보존)
- `tests/test_contract_metadata.py` (신규, 76개 테스트 - 리뷰 반영
  16개 포함)
- `docs/00-roadmap.md` (Task 046 항목 추가)
- `results/046-contract-metadata-engine.md` (본 문서)

`tasks/046-contract-metadata-engine.md`는 수정하지 않았다(이미 브랜치에
존재하던 권위 있는 소스, Schema v1 형식으로 이미 작성돼 있었다).

## Checkpoint Evidence

| Acceptance Criteria (Specification) | Evidence |
| --- | --- |
| 1. 유효한 Schema v1 Contract가 결정론적으로 파싱됨 | `test_valid_schema_v1_contract_parses_deterministically`, `test_real_schema_v1_contract_046_parses_natively`, `test_deterministic_output_across_repeated_parses_of_real_contract` |
| 2. 5개 필수 메타데이터 필드가 typed canonical 값을 씀 | `test_every_specification_type_value_parses` 외 4개(enum family별) |
| 3. 미지원 버전이 fail closed | `test_unsupported_version_raises` |
| 4. 필수 섹션 누락/중복이 fail closed | `test_missing_required_section_raises`(3종), `test_duplicate_singleton_section_raises`, `test_duplicate_contract_version_section_raises`, `test_duplicate_references_section_raises` |
| 5. 메타데이터 누락/중복/알수없음/잘못된값이 fail closed | `test_missing_metadata_field_raises`, `test_duplicate_metadata_field_raises`, `test_unknown_metadata_field_raises`, `test_invalid_metadata_value_raises` |
| 6. Contract Version 없는 Contract가 legacy로 명시 분류 | `test_contract_with_no_contract_version_is_legacy`, `test_representative_real_legacy_contracts_classify_as_legacy`(6개 실제 Contract) |
| 7. `x-` 확장이 분리 보존되고 핵심 필드에 영향 없음 | `test_valid_extension_is_preserved_separately`, `test_extension_cannot_shadow_or_override_a_core_field`, `test_duplicate_extension_field_raises` |
| 8. Task identity 일관성 검증 (id + title, 리뷰 반영) | `test_task_identity_mismatch_raises`, `test_missing_task_identity_id_raises`, `test_task_identity_title_mismatch_raises`, `test_missing_task_identity_title_raises`, `test_duplicate_task_identity_title_raises`, `test_duplicate_task_identity_id_raises` |
| 9. 기존 legacy Contract가 강제 마이그레이션 없이 계속 동작 | `test_representative_real_legacy_contracts_classify_as_legacy` |
| 10. release/workflow/dispatch 정책 없음 | grep으로 `devbot.agents`/`devbot.workspace`/`devbot.polling`/`devbot.review`/`devbot.rework` 미import 확인, CLI 명령 미추가 확인 |
| 11. Specification 검증/lint/전체 테스트 통과 | 아래 Validation 결과 |
| 리뷰 반영: Provenance가 파싱/반환됨 | `test_provenance_is_parsed_into_the_result`, `test_provenance_branch_without_backticks_still_parses`, `test_missing_github_issue_provenance_field_raises`, `test_missing_branch_provenance_field_raises`, `test_duplicate_github_issue_provenance_field_raises`, `test_duplicate_branch_provenance_field_raises`, `test_malformed_github_issue_provenance_field_raises`(4가지 형식), `test_empty_branch_provenance_field_raises`, `test_optional_provenance_entries_are_ignored_but_do_not_break_parsing` |

추가로 계약에 명시되지 않았지만 안전을 위해 작성한 테스트:
`test_malformed_version_does_not_silently_fall_back_to_legacy`(Safety
요구사항 직접 고정), `test_missing_or_malformed_title_raises`,
`test_title_separator_variants_all_parse`,
`test_unknown_non_canonical_top_level_section_is_tolerated`,
`test_optional_references_section_is_accepted_once`.

## Validation 결과

- `uv run devbot specification validate --task 46`: PASS (text/json 모두,
  에러/경고 0개, 리뷰 반영 이후에도 불변 - Provenance/Task Identity
  파싱 로직 변경은 Specification 구조에 영향 없음)
- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  933 passed (Task 045 병합 후 기준 857개 + `tests/test_contract_metadata.py`
  76개[parametrize 포함, 함수 정의 44개])

## 수동 검증 결과 (읽기 전용, 실제 저장소 대상)

```
$ uv run python -c "
from devbot.contract_metadata import parse_contract_metadata
from pathlib import Path
result = parse_contract_metadata(Path('tasks/046-contract-metadata-engine.md').read_text(encoding='utf-8'))
print(result.kind, result.task_id, result.title, result.contract_version, result.metadata)
"
schema_v1 046 Contract Metadata Engine 1 ContractMetadata(specification_type=<SpecificationType.FEATURE: 'feature'>, release_impact=<ReleaseImpact.INTERNAL: 'internal'>, risk_level=<RiskLevel.MEDIUM: 'medium'>, compatibility=<Compatibility.BACKWARD: 'backward'>, migration=<Migration.NONE: 'none'>, extensions=mappingproxy({}))

$ uv run python -c "
from devbot.contract_metadata import parse_contract_metadata
from pathlib import Path
result = parse_contract_metadata(Path('tasks/037-release-operator-ux.md').read_text(encoding='utf-8'))
print(result.kind, result.task_id, result.metadata)
"
legacy 037 None

$ uv run python -c "
from devbot.contract_metadata import parse_contract_metadata
from pathlib import Path
result = parse_contract_metadata(Path('tasks/046-contract-metadata-engine.md').read_text(encoding='utf-8'))
print(result.provenance)
"
ContractProvenance(github_issue='#96', branch='task/046-contract-metadata-engine')
```

리뷰 반영 후 Task 046 자신의 실제 Contract를 다시 파싱해 Provenance가
정확히 `github_issue='#96'`, `branch='task/046-contract-metadata-engine'`로
채워지는 것을 직접 확인했다. Task 046 자신의 실제 Contract(Schema v1로
이미 작성돼 있었다)가
native로 정확히 파싱되고, 실제 legacy Contract(Task 037)가 에러 없이
`ContractKind.LEGACY`로 분류되는 것을 직접 확인했다. `# Full Task
Contract Reference`에 인용한 내용이 `tasks/046-contract-metadata-engine.md`
원문과 바이트 단위로 일치하는지도 Python diff로 확인했다(차이 없음).

## 남은 TODO와 제한 (계약이 명시적으로 범위 밖으로 남긴 것)

- Release Recommendation(major/minor/patch 추천), Review Loop, Workflow
  Engine, GitHub 자동화, Agent dispatch 변경은 전혀 구현하지 않았다.
- CLI 명령을 추가하지 않았다 - "기존 저장소 관례상 엄격히 필요한
  경우가 아니면" 추가하지 말라는 계약 조건에 따라, 이 엔진은 순수
  라이브러리로만 제공된다.
- 기존 Contract를 Schema v1으로 일괄 마이그레이션하지 않았다 - legacy
  분류로 충분하다(Task 045/046 둘 다 명시).
- `risk_level`/`compatibility`/`migration`/`release_impact` 값에 기반한
  어떤 정책 판단도 구현하지 않았다 - 파싱된 값을 그대로 노출할 뿐이다.

## 위험 요소

- `_split_top_level_sections`가 `devbot.specification`의 파서와 별도로
  존재하므로, 향후 두 파서 중 하나만 수정되면 서로 다른 fence-처리
  behavior로 갈라질 수 있다 - 둘 다 `fenced_code_ranges`라는 같은
  헬퍼를 재사용하므로 fence 인식 자체는 항상 동기화되지만, 섹션
  분할/중복 처리 semantics는 의도적으로 다르다(위 "주요 설계 결정"
  참고) - 향후 유지보수자는 이 차이가 실수가 아니라 설계임을 알아야
  한다.
- Task Identity의 title 텍스트 일치는 검증하지 않으므로, heading의
  title과 Task Identity의 `title` 필드가 서로 달라도 파싱은 성공한다 -
  Task 046 Parsing Rules가 명시적으로 id 일치만 요구했기 때문에 의도된
  범위이지만, 향후 더 엄격한 일관성이 필요하면 별도 Task로 추가해야
  한다.
