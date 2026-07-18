# Task 045 Result: Contract Schema

## 리뷰 반영 (PR #95, hjlee83)

아키텍처 리뷰에서 blocking 지적을 받았다: `# Functional Requirements >
Required Behaviour`가 "A Contract conforming to Schema v1 must define
these areas"라며 10번 항목으로 "References and provenance"를 나열해
References도 필수인 것처럼 읽혔는데, `# Technical Design > Field
Definitions`와 Required and Optional Summary 표는 References를 선택
(`0..1`)으로 정의하고 있었고 Normative Example도 References 없이
conforming example로 취급하고 있어 서로 모순이었다.

반영 내용:

- `Required Behaviour`를 Provenance(필수)와 References(선택)를 분리하는
  방향으로 수정했다 - 번호 목록 10개를 Schema Version/Provenance/Task
  Identity/Metadata/Goal and context/Scope and out-of-scope
  boundaries/Deliverables/Acceptance criteria/Quality gates/Handoff
  instructions로 재구성하고(Provenance를 목록에 명시적으로 추가),
  References는 목록에서 완전히 빼고 별도 문장으로 "선택 사항이며
  Field Definitions/Summary 표를 참고하라"고 명시했다. 리뷰가 제시한
  두 방향(Provenance만 필수로 분리 vs References를 진짜 필수로 승격)
  중 전자를 택했다 - Field Definitions/Summary 표/Normative Example이
  이미 일관되게 References를 선택으로 정의하고 있었으므로, 그쪽을
  기준점으로 Required Behaviour만 맞추는 것이 더 작고 안전한 수정이었다.
- 회귀 테스트 1개 추가:
  `test_required_behaviour_is_consistent_with_required_optional_summary`
  - Required and Optional Summary 표를 파싱해 각 영역의 필수/선택
    여부를 확인하고, Required Behaviour의 번호 목록에 선택 영역
    (References)이 나타나지 않는지, 필수 영역 12개가 전부 Required
    Behaviour 어딘가에 언급되는지 정규식으로 직접 검증한다. 수정 전
    원문으로 되돌려 이 테스트가 실제로 실패하는지 확인한 뒤(라이브로
    직접 재현), 수정된 버전으로 다시 통과하는지 재확인했다.
- `tasks/045-contract-schema.md`(원본 Contract, Full Task Contract
  Reference에 그대로 인용된 부분)는 건드리지 않았다 - 이 모순은
  Specification이 Contract 내용을 재서술하는 과정에서 생긴 것이지,
  원본 Contract 자체의 문제가 아니었다.
- `devbot specification validate --task 45`는 수정 전후 모두 PASS였다
  (Task 043 Validator는 required/optional **의미**의 모순까지는 검사하지
  않고 구조만 검사하므로, 이번 리뷰 지적은 Validator가 잡을 수 있는
  종류의 문제가 아니었다 - 새 회귀 테스트가 이 구조적 공백을 메운다).

## 완료 내용

Contract Schema v1을 DevBot의 권위 있고 버전 관리되는 Task Contract
표준으로 확정했다. 이번 Task는 코드가 아니라 **문서**로만 구현된다 -
Issue #94와 `specifications/045-contract-schema.md`가 명시적으로 요구한
대로, Contract Metadata Parser/Domain Object/Release Recommendation/CLI/
Review Loop/Workflow Engine/Release Automation은 전혀 구현하지 않았다.

- **Contract Schema v1 정의**: 13개 normative 영역(Contract Version/
  Provenance/Task Identity/Metadata/Goal/Context/Scope/Out of Scope/
  Deliverables/Acceptance Criteria/Quality Gates/Handoff/References)의
  순서, 의미, 필수 여부, cardinality를 `specifications/045-contract-schema.md`
  "Technical Design > Architecture"에 확정했다. References만 선택(0..1),
  나머지 12개는 모두 필수(정확히 1개).
- **필수/선택 필드 구분**: "Required and Optional Summary" 표로 13개
  영역 전부를 명시했고, Metadata 내부 5개 필드(specification_type/
  release_impact/risk_level/compatibility/migration)는 전부 필수로
  정의했다. Metadata의 `x-` 접두사 필드만 실험적 확장으로 허용된다.
- **Canonical enum 값 정의**: 5개 Metadata 필드 각각의 허용 값을
  소문자 ASCII 토큰으로 고정했다 - `specification_type`(6개, Task 044의
  template 여섯 값과 정확히 일치), `release_impact`(6개), `risk_level`
  (3개), `compatibility`(2개), `migration`(3개).
- **contract_version 기반 버전 관리 규칙**: Backward-Compatible(문서
  명확화, 선택 필드/섹션 추가, `x-` 확장 추가 - `contract_version`을
  올리지 않음)과 Breaking(필수 필드 추가, 필드 제거/재정의, enum 값
  제거/재정의, cardinality 변경, canonical 표현 변경 - 새 정수
  `contract_version`과 마이그레이션 안내 필요)을 구분했다.
- **기존 DevBot 문서 구조와의 호환성 검증**: 아래 "호환성 검증 결과"
  절 참고 - 실제로 실행해서 확인했다.
- **필요한 테스트**: `tests/test_contract_schema_045.py`(10개, 리뷰 반영
  포함) - 아래 "테스트" 절 참고.
- **`results/045-contract-schema.md` 작성**: 본 문서.

## 권위 있는 문서 처리에 대한 결정 - Specification 재구성

구현 전 `specifications/045-contract-schema.md`를 읽었을 때, 이 문서는
Task 042/043이 확립한 canonical Specification 스키마(`# Overview`/
`# Functional Requirements`/`# Technical Design`/`# Validation`/
`# Safety`/`# Completion`/`# Handoff`/`# Full Task Contract Reference`
8개 최상위 섹션)를 전혀 따르지 않고 있었다 - 모든 절이 `##`(level-2)로만
작성되어 있었다. 그런데 이 문서 자신의 Acceptance Criteria #8이 "The
Specification validates under the existing Specification Validator"를
명시적으로 요구하고, `tasks/045-contract-schema.md`의 Quality Gates도
"Specification validation passes"를 요구한다. 실제로 구현 전
`devbot specification validate --task 45`를 실행해 확인한 결과 SPV-001
(제목 형식)/SPV-002(Provenance 불완전)/SPV-003(8개 최상위 섹션 전부
누락) 에러 10개로 완전히 실패했다.

Specification을 최우선 기준으로 삼되, 이 문서 자신이 선언한 Acceptance
Criteria(검증 통과)를 실제로 만족시키는 것이 올바른 구현이라고 판단해
`specifications/045-contract-schema.md`를 canonical 8-섹션 envelope으로
**재구성**했다 - 기존에 작성돼 있던 Field Definitions/Canonical Metadata
Values/Canonical Metadata Representation/Required and Optional Summary/
Versioning Rules/Legacy Compatibility/Normative Example/Validation
Expectations for Future Tasks 내용은 단 한 문장도 삭제하지 않고 전부
canonical 섹션 아래로 재배치했다(예: 스키마 구조/필드 정의/enum
값/normative 예시는 `# Technical Design > ## Architecture`로, Versioning
Rules는 `## Migration Notes`로, Implementation Constraints는
`# Safety`로, 원본 Specification의 Handoff 절은 `# Handoff >
## Required Handoff Procedure`로 그대로 이동). `tasks/045-contract-schema.md`
(Task Contract)는 건드리지 않았다 - `# Full Task Contract Reference`에
원본 전체를 바이트 단위로 그대로 인용했다(직접 diff로 확인, 아래 참고).
이 결정은 Contract Schema v1의 **내용**을 전혀 바꾸지 않았고, 오직 그
내용을 담는 문서의 **형식**만 이 저장소의 기존 Specification 표준에
맞춘 것이다.

## 호환성 검증 결과

**Legacy 분류 검증** (읽기 전용, 실제 저장소 대상): 저장소 전체 역사를
아우르는 5개 Task Contract(001/022/037/042/044)를 직접 확인한 결과,
어느 것도 `## Contract Version`을 선언하지 않았다 - Schema v1의 자체
규칙("Contract Version을 선언하지 않은 기존 Contract는 legacy로
분류되며 강제 마이그레이션이 필요 없다")에 따라 전부 legacy로 균일하게
분류되며, 이는 Task 045가 명시한 대로 어떤 기존 Contract도 강제로
재작성할 필요가 없음을 실증한다.

**`docs/09-task-contract-standard.md`와의 관계 검증**: 이 문서의 필수
항목 14개(목표/배경/Dependencies/구현 범위/제외 범위/품질 게이트/필수
테스트 이름/검증 명령/Files Expected to Change/Risk/Rollback
Strategy/Reviewer Focus/Definition of Done/Result 문서 경로)와 Schema
v1의 13개 normative 영역을 직접 비교했다. 완전히 겹치지 않는다 - Schema
v1은 identity/metadata/versioning(Contract Version/Provenance/Task
Identity/Metadata)을 새로 형식화하고, `docs/09`는 운영 완성도
항목(Risk/Rollback Strategy/Reviewer Focus/Definition of Done/필수 테스트
이름/검증 명령 등)을 계속 요구한다 - 어느 쪽도 다른 쪽과 모순되지
않지만, 실제 Task 계약서는 두 표준을 **동시에** 만족해야 한다. 이
관계를 `specifications/045-contract-schema.md`("Dependencies" 절)와
`docs/09-task-contract-standard.md`(문서 도입부) 양쪽에 상호 참조로
명시했다 - `docs/09`의 필수 항목 14개 목록 자체는 한 글자도 바꾸지
않았다(`test_docs_09_required_items_list_is_unchanged`로 고정).

## 테스트

`tests/test_contract_schema_045.py`(신규, 10개 - 리뷰 반영으로 1개
추가) - Task 045는 파서/CLI를 추가하지 않으므로, 재사용 가능한 파싱
로직을 `src/devbot/`에 새로 두지 않고 세 종류로 나눠 작성했다:

1. **기존 Task 043 Validator 재사용** (새 파서 아님): `devbot.
   specification_validation.validate_specification_file`을 그대로 호출해
   Acceptance Criteria #8을 실행 가능한 회귀로 고정한다
   (`test_specification_045_passes_task_043_validator`,
   `test_specification_045_has_no_warnings`).
2. **테스트 파일 안에만 있는 자체 점검** (재사용 모듈 아님): Normative
   Example과 Canonical Metadata Representation 블록이 스스로 선언한
   canonical enum 값만 쓰는지, 13개 normative 영역이 전부 있는지, 5개
   역사적 Contract와 Task 045 자신의 Contract가 legacy로 남아 있는지,
   `docs/09`의 필수 항목 목록이 그대로인지를 정규식으로 직접 확인한다.
3. **리뷰 반영 회귀**
   (`test_required_behaviour_is_consistent_with_required_optional_summary`):
   위 "리뷰 반영" 절 참고 - Required and Optional Summary 표와 Required
   Behaviour의 번호 목록을 정규식으로 직접 비교해, References(유일한
   선택 영역)가 필수 목록에 나타나지 않고 별도로 선택으로 명시되는지,
   나머지 12개 필수 영역이 전부 Required Behaviour에 언급되는지
   검증한다.

## 수정 파일

- `specifications/045-contract-schema.md` (재구성 - Task 042/043
  canonical Specification 스키마를 만족하도록 최상위 8섹션으로
  재편, 내용은 보존; 리뷰 반영으로 Required Behaviour의 References/
  Provenance 필수 여부 모순도 수정)
- `docs/09-task-contract-standard.md` (Schema v1과의 관계를 설명하는
  상호 참조 2문단 추가 - 기존 필수 항목 14개는 무변경)
- `docs/00-roadmap.md` (Task 045 항목 추가)
- `tests/test_contract_schema_045.py` (신규, 10개 테스트 - 리뷰 반영
  회귀 1개 포함)
- `results/045-contract-schema.md` (본 문서)

`tasks/045-contract-schema.md`는 수정하지 않았다(이미 브랜치에 존재하던
권위 있는 소스).

## Checkpoint Evidence

| Acceptance Criteria (Specification) | Evidence |
| --- | --- |
| 1. Contract Schema v1이 완전히 정의됨 | `specifications/045-contract-schema.md` Technical Design > Architecture |
| 2. 모든 normative 영역에 의미/필수여부/cardinality가 있음 | 같은 절의 Field Definitions + Required and Optional Summary 표 |
| 3. Canonical metadata 값과 표현이 고정됨 | Canonical Metadata Values, Canonical Metadata Representation |
| 4. 버전 규칙이 호환/breaking을 구분함 | Migration Notes (Backward-Compatible / Breaking Schema Change) |
| 5. Legacy Contract가 강제 마이그레이션 없이 분류됨 | Dependencies 절 + `test_historical_contracts_have_no_contract_version_and_are_legacy`, `test_contract_045_itself_is_legacy_not_schema_v1` |
| 6. Normative conforming example 제공 | Architecture > Normative Example + `test_normative_example_*` 4개 |
| 7. 미래 파서 검증 기대사항이 문서화됨 | Validation > Required Tests and Quality Gates (Validation Expectations for Future Tasks) |
| 8. Specification이 기존 Validator를 통과함 | `test_specification_045_passes_task_043_validator`, 라이브 검증(아래) |
| 9. 파서/CLI/정책/워크플로/리뷰/릴리스 자동화 없음 | Safety 절 + grep으로 신규 `src/devbot/*.py` 없음 확인 |
| 10. Result 문서가 구현/검증 근거를 기록함 | 본 문서 |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  857 passed (Task 044 병합 후 기준 847개 + `tests/test_contract_schema_045.py`
  10개, 리뷰 반영 회귀 1개 포함)

## 수동 검증 결과 (읽기 전용)

```
$ uv run devbot specification validate --task 45
task: 045
specification: specifications/045-contract-schema.md
result: PASS
no issues found.
(exit 0)

$ uv run devbot specification validate --task 45 --format json
{
  "task_number": 45,
  "specification_path": "specifications/045-contract-schema.md",
  "passed": true,
  "errors": [],
  "warnings": []
}
(exit 0)
```

Full Task Contract Reference에 인용된 내용이 `tasks/045-contract-schema.md`
원문과 바이트 단위로 정확히 일치하는지 Python으로 직접 diff해 확인했다
(차이 없음).

## 남은 TODO와 제한 (계약이 명시적으로 범위 밖으로 남긴 것)

- Contract Metadata Parser, Metadata Domain Object는 구현하지 않았다 -
  Task 046(또는 이후 Task)이 이 Specification을 근거로 구현해야 한다.
- Release Recommendation 로직, CLI 명령, Review Loop, Workflow Engine,
  Release Automation은 이번에 전혀 손대지 않았다.
- 기존 44개 Task Contract를 Schema v1으로 재작성하지 않았다 - Schema
  v1 자체가 이를 요구하지 않는다(legacy 분류로 충분).
- Schema v1의 Normative Contract Structure는 자신의 13개 영역 사이의
  canonical 순서만 고정하며, `docs/09`가 요구하는 추가 섹션(Risk 등)이
  그 사이 어디에 와야 하는지는 아직 정의하지 않았다 - 향후 두 표준을
  하나의 완전한 순서로 통합하는 작업은 이번 범위 밖으로 남긴다.

## 위험 요소

- Metadata 재구성은 문서 형식(헤딩 구조)만 바꿨고 Schema v1 자체의
  의미 있는 정의는 전혀 바꾸지 않았지만, 향후 이 Specification을 다시
  읽는 사람은 원본과 순서가 달라졌다는 점을 알아야 한다 - 원본과의
  diff는 본 Result 문서와 PR 설명에 남겨 추적 가능하게 했다.
- `docs/09-task-contract-standard.md`에 추가한 상호 참조 문단은
  순수 설명문이며 필수 항목 검증 로직이나 Review Gate 기준을 전혀
  바꾸지 않았다 - `test_docs_09_required_items_list_is_unchanged`로
  고정했지만, 향후 그 문서를 편집하는 사람은 이 회귀 테스트를 계속
  통과시켜야 한다.
