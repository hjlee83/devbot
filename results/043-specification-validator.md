# Task 043 Result: Specification Validator

## 완료 내용

- 새 `src/devbot/specification_validation.py`를 만들어 워크플로에
  **Validation** 단계를 추가했다: Goal → Planner → Issue → Contract →
  Specification → **Validation** → Dispatch.
  - `validate_specification_text(text, *, task_number, specification_path)` -
    순수 함수. Specification 텍스트만으로 결정론적인
    `SpecificationValidationResult`를 만든다 - 파일시스템/네트워크 접근이
    전혀 없다.
  - `validate_specification_file(repository_root, task_number)` -
    `specifications/NNN-*.md`를 정확히 하나 찾아 읽고 검증한다. 읽기
    전용.
  - `render_validation_report(result, *, output_format="text"|"json")` -
    사람이 읽는 리포트와 결정론적 JSON 리포트.
  - 전용 예외 4종(`InvalidTaskNumberError`/`SpecificationNotFoundError`/
    `AmbiguousSpecificationError`/`SpecificationLoadError`), 모두
    `SpecificationValidationError`의 서브클래스 - **운영 실패**(파일을
    찾거나 읽는 단계의 실패)와 **문서 검증 실패**(`passed=False`, 예외
    아님)를 구조적으로 구분한다.
- Task 042의 스키마를 그대로 재사용했다(계약서가 명시적으로 요구한
  "reuse Task 042 schema constants... must not introduce a second
  incompatible schema"를 만족):
  - `REQUIRED_TOP_LEVEL_SECTIONS`을 8번째 항목 `# Full Task Contract
    Reference`로 확장했다(`src/devbot/specification.py`) - SPV-003이
    Task 042의 `validate_specification_schema`와 동일한 튜플을 그대로
    import해서 쓴다. 기존 27개 test_specification.py 테스트는 그대로
    통과한다(렌더링 결과는 이미 이 8개를 전부 포함하고 있었으므로
    영향 없음).
  - `_fenced_code_ranges`를 `fenced_code_ranges`로 공개해(같은 로직,
    이름만 변경) 두 모듈이 같은 코드 펜스 인식 로직을 공유한다.
- 규칙 코드 13개(SPV-001~013)를 구현했다:
  - **에러** (`passed=False`를 만든다): `SPV-001` 제목 형식/Task 번호
    불일치/빈 제목, `SPV-002` Provenance 누락/불완전, `SPV-003` 필수
    최상위 섹션 누락/중복, `SPV-004` 필수 서브섹션 누락, `SPV-005` 필수
    본문 비어 있음(canonical fallback은 내용으로 인정), `SPV-006`
    Acceptance Criteria에 checkpoint/checklist/목록 형식이 전혀 없음
    또는 checkpoint id 중복, `SPV-007` Validation Commands에 실행
    명령도 명시적 미지정 진술도 없음, `SPV-008` Safety Agent 금지
    사항이 비어 있음, `SPV-009` Full Task Contract Reference가 비어
    있거나 경로 참조만 있음.
  - **경고** (검증을 실패시키지 않는다): `SPV-010` canonical 순서 위반,
    `SPV-011` 스키마에 없는 추가 최상위 섹션, `SPV-012` 정규 섹션(Full
    Task Contract Reference 제외)의 미해결 `TODO`/`TBD`/`FIXME`/`XXX`/
    `{{...}}` 마커.
  - `SPV-013`은 문서 규칙이 아니라 구현 요구사항이다 - 모든 이슈를
    (줄 번호 → 규칙 코드 → 메시지) 순으로 정렬해 반환한다.
- CLI: 읽기 전용 `devbot specification validate --task <N>`과
  `--format json`. GitHub API를 전혀 호출하지 않는다(`validate` 서브커맨드는
  `_run_specification_command`에서 GitHub client를 만들기 전에 분기해
  바로 반환한다). 종료 코드: `0` = 통과(경고만 있어도 통과), `1` = 문서
  검증 실패, `2` = 운영 오류(Task 번호 오류/파일 없음/모호함/읽기 실패) -
  daemon lock을 잡지 않는다.
- **라이브 검증 중 실제 버그를 하나 더 발견해 고쳤다** - 아래 "라이브
  검증 중 발견한 버그와 수정" 절 참고.

## 아키텍처

```
specifications/NNN-slug.md
         |
         v
devbot specification validate --task <N>   (신규, 이번 Task)
         |
         |  1. validate_specification_file: 파일 1개 해석/로드
         |     (운영 실패는 전용 예외로 fail closed)
         |  2. validate_specification_text: 순수 함수 - 파싱 후
         |     SPV-001~012 규칙 실행, SPV-013 정렬
         v
SpecificationValidationResult (passed, errors, warnings)
         |
         v
render_validation_report  ->  text 또는 결정론적 JSON
         |
         v (향후, 이번 Task 범위 아님)
Workflow Engine  -  Dispatch 전에 `passed` 필드를 게이트로 사용 가능
                     (devbot.workspace/devbot.agents는 이번에 미변경)
```

## 주요 설계 결정

- **Task 042 스키마가 유일한 원본이다.** `REQUIRED_TOP_LEVEL_SECTIONS`와
  `fenced_code_ranges`를 그대로 import해서 쓰고, 별도의 Validator 전용
  스키마 상수를 새로 만들지 않았다. `REQUIRED_TOP_LEVEL_SECTIONS`를
  8번째 항목으로 확장한 것이 이 Task가 `specification.py`에 가한 유일한
  "스키마" 변경이며, Task 042의 실제 렌더링 출력(모든 8개 섹션을 항상
  포함)과 항상 일치하므로 회귀 위험이 없다.
- **운영 실패와 문서 검증 실패를 타입으로 구분한다.** 파일을 찾거나
  읽을 수 없는 것은 예외(`SpecificationValidationError` 서브클래스)로
  던지고, 문서가 로드는 됐지만 규칙을 어긴 것은 예외를 던지지 않고
  `SpecificationValidationResult.passed=False`로 반환한다 - CLI 종료
  코드(2 vs 1)가 이 구분을 그대로 드러낸다.
- **에러와 경고는 서로 다른 실패 강도를 의도적으로 구현한다.** 예를 들어
  Acceptance Criteria가 `Not specified in the Task Contract.` 하나만
  있으면 SPV-005(일반 "본문이 비어 있지 않음" 체크)는 통과하지만
  SPV-006(Acceptance Criteria는 checkpoint/checklist/목록 형식이 실제로
  있어야 한다는 더 엄격한 체크)은 실패한다 - 두 규칙이 같은 서브섹션에서
  동시에 발동할 수 있고, 이는 의도된 동작이다(같은 결함을 서로 다른
  각도에서 설명하는 것이지 중복 버그가 아니다). 반대로 Safety의 Agent
  금지 사항 서브섹션은 일반 SPV-005 루프에서 명시적으로 제외하고
  SPV-008 하나만 담당하게 했다 - 이 서브섹션은 SPV-008이 이미 완전히
  커버하므로 같은 결함에 대해 코드 두 개가 동시에 뜨는 걸 피했다.
- **`## Provenance`는 Contract와 달리 최상위 섹션이 아니라 제목과 첫
  `# Overview` 사이의 "preamble"이다.** Specification 문서 구조가
  Contract와 다르므로(Contract는 `##`만 쓰고, Specification은 `#`가
  최상위-`##`가 서브섹션인 2단 구조를 쓴다) Task 042의 `parse_contract`를
  그대로 재사용하지 않고, Specification 전용의 새 파서
  (`_parse_specification`)를 만들었다 - 이것이 "reuse... where practical"
  조항 아래 새로 작성한 부분이다.
- **SPV-012는 의도적으로 좁게 정의했다.** `TODO`/`TBD`/`FIXME`/`XXX`와
  `{{...}}` 형태만 "미해결 마커"로 본다. `--task <N>` 같은 CLI 플레이스홀더
  각괄호는 절대 마커로 취급하지 않는다 - Task 042가 실제로 생성하는
  Specification들이 Contract의 CLI 사용 예시를 그대로 인용하면서 이런
  각괄호 표기를 정상적으로 포함하기 때문에, 이를 마커로 잘못 인식하면
  모든 정상 Specification에 스팸성 경고가 뜬다
  (`test_cli_placeholder_angle_brackets_are_not_flagged_as_unresolved`로
  고정).

## 라이브 검증 중 발견한 버그와 수정

Task 042의 실제 생성 Specification(`specifications/042-specification-generator.md`)을
검증하는 라이브 테스트(`devbot specification validate --task 42`) 도중,
실제 버그를 하나 더 발견했다.

**증상**: `# Full Task Contract Reference` 섹션은 원본 Task Contract 전체를
3-backtick(` ``` `) 코드 펜스로 감싼다. 그런데 Task 042의 Contract 자신이
"Specification Structure" 예시 안에 같은 3-backtick 펜스를 또 쓰고 있었다
(표준 CommonMark는 같은 길이의 펜스를 중첩할 수 없다 - 더 긴 펜스로
바깥을 감싸야 안전하게 중첩된다). 단순한 펜스 열림/닫힘 토글로는 이
중첩을 표현할 수 없어서, 안쪽 예시의 첫 ` ``` `를 만나는 순간 바깥
펜스가 "닫힌 것"으로 잘못 인식됐고, 그 뒤로 나오는 안쪽 예시의 `# Overview`/
`## Goal`/`# Technical Design` 같은 예시 텍스트가 바깥 Specification
문서의 **진짜 최상위 섹션**으로 오검출됐다. 그 결과 `devbot specification
validate --task 42`를 실행하면 `# Overview`/`# Functional Requirements`
등 7개 섹션 전부가 "중복 섹션(SPV-003)"으로 잘못 보고됐다.

**원인**: `fenced_code_ranges`(Task 042에서 재사용한 헬퍼)가 단순 토글
방식으로만 펜스를 추적해, 같은 길이의 펜스가 중첩되는 경우를 표현하지
못했다. Task 042는 `_split_level2_sections`가 원본 Contract 텍스트에만
이 헬퍼를 썼기 때문에 이 문제를 만난 적이 없었다 - Task 043이 처음으로
"Contract 전체를 다시 펜스로 감싼 결과물"에 이 헬퍼를 돌리면서 노출된
문제다.

**수정**: `render_specification`(Task 042)의 실제 렌더링 방식은 바꾸지
않았다 - 대신 `_parse_specification`(Task 043, 신규)에서 `# Full Task
Contract Reference` 헤딩을 만나면 그 지점 이후 문서 전체를 더 이상
구조로 재해석하지 않도록 했다. 이 섹션은 설계상 항상 마지막 섹션이고,
그 안의 내용은 통째로 불투명한 인용 블록이므로 안전하고 정확한 수정이다.

**검증**:
- `devbot specification validate --task 42`를 수정 전/후 직접 재실행해,
  수정 후 `passed: true`, 에러/경고 0개가 되는지 확인했다(수정 전에는
  7개의 가짜 SPV-003 에러와 2개의 가짜 SPV-011 경고가 나왔다).
- `tests/test_specification_validation.py`의 모든 테스트가 이 수정된
  파서를 대상으로 작성됐고(예:
  `test_canonical_generator_output_passes`가 실제 `render_specification`
  출력을 검증), `test_marker_inside_full_contract_reference_is_not_flagged`가
  Contract 인용 블록 내부의 텍스트가 별도 섹션으로 잘못 쪼개지지 않음을
  회귀 테스트로 고정한다.

## 배포된 초기 Specification 자체를 검증해 발견한 실제 이슈 (버그 아님)

이번 Task 043 자신의 `specifications/043-specification-validator.md`
(Task 042 파이프라인이 아니라 Architect 페르소나가 직접 작성한 문서)를
새로 만든 Validator로 검증해 봤더니 진짜로 실패했다 - Validator가 제대로
동작한다는 증거로서 그대로 기록한다:

```
$ uv run devbot specification validate --task 43
task: 043
specification: specifications/043-specification-validator.md
result: FAILED
errors (1):
  - [SPV-002] incomplete '## Provenance' section (missing: Task Issue)
warnings (2):
  - [SPV-012] unresolved marker 'TBD' in section '# Technical Design'
  - [SPV-012] unresolved marker 'TODO' in section '# Technical Design'
exit: 1
```

- **SPV-002 에러는 실제로 정확하다**: Task 043은 (이번 세션의 다른 Task와
  달리) GitHub Issue 없이 시작됐다 - `gh issue list --repo hjlee83/devbot
  --state all --search "Task 043"`로 직접 확인했고, 이 Task의 계약서
  Git Rules 절에도 "Task Issue: #NN" 항목이 없다. Validator가 Task 042
  canonical 형식(Task Issue/Task Contract/생성 출처)과의 편차를 정확히
  잡아낸 것이며, 이 Specification 파일 자체를 고치는 것은 이번 Task의
  범위 밖이라 수정하지 않았다.
- **SPV-012 경고 2개는 오탐이 아니라 자기참조 텍스트다**: 실제 위치는
  `specifications/043-specification-validator.md`의 "### Canonical rules"
  절, 그 서브섹션이 SPV-012 규칙 자체를 설명하는 문장
  ("`SPV-012`: unresolved TODO/TBD/template marker — WARNING.")이다 -
  즉 "TODO/TBD를 감지한다"는 규칙 설명 문장 안의 단어 자체가 감지된
  것이다. 경고이므로 `passed`에는 영향이 없고(에러 1개만 `passed=False`를
  만든다), 이런 자기참조적 오탐을 특별 처리하는 로직은 추가하지
  않았다 - 순수 문자열/정규식 매칭으로는 "규칙을 설명하는 문장"과 "진짜
  미해결 마커"를 구분할 수 없고, 계약서도 이런 의미론적 구분을 요구하지
  않는다.

## Future Dispatch integration (문서화 요구사항)

Task 043은 검증 결과를 소비하는 쪽(Dispatch)을 구현하지 않는다. 향후
Workflow Engine은 `devbot specification validate --task <N> --format
json`의 `passed` 필드를 Dispatch 전 게이트로 그대로 쓸 수 있다 -
`SpecificationValidationResult`/`ValidationIssue`는 이미 순수 데이터
구조이므로 공개 함수 시그니처를 바꿀 필요가 없다.
`devbot.workspace`/`devbot.agents`/`devbot.polling`/`devbot.review`/
`devbot.rework`는 이번에 한 줄도 바꾸지 않았다.

## 수정 파일

- `src/devbot/specification_validation.py` (신규)
- `src/devbot/specification.py` (`REQUIRED_TOP_LEVEL_SECTIONS`에
  `# Full Task Contract Reference` 추가, `_fenced_code_ranges` →
  `fenced_code_ranges` 공개 - 두 가지 모두 "safe schema/helper reuse"
  범위 안의 변경)
- `src/devbot/main.py` (`devbot specification validate` CLI 배선)
- `tests/test_specification_validation.py` (신규, 46개 테스트)
- `tests/test_main.py` (6개 테스트 추가)
- `docs/00-roadmap.md` (Task 043 항목 추가)
- `docs/08-beta-runbook.md` ("Specification 검증 절차 (Task 043)" 절
  추가)
- `results/043-specification-validator.md` (본 문서)

`tasks/043-specification-validator.md`와
`specifications/043-specification-validator.md`는 이 Task 시작 전에 이미
브랜치에 존재했다(architect 페르소나가 작성) - 이번 Task에서 수정하지
않았다.

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-043-1 유효한 Specification | `test_minimal_valid_skeleton_passes`, `test_canonical_generator_output_passes`, `test_repeated_validation_of_identical_bytes_is_equivalent` |
| CP-043-2 파일 해석 | `test_invalid_task_number_raises_for_zero_and_negative`, `test_missing_specification_raises`, `test_missing_specifications_directory_raises`, `test_ambiguous_specification_raises_when_multiple_files_match`, `test_unreadable_specification_raises_load_error`, `test_validate_specification_file_end_to_end_passes` |
| CP-043-3 제목/Provenance | `test_malformed_title_fails`, `test_title_task_number_mismatch_fails`, `test_empty_title_fails`, `test_canonical_title_passes`, `test_missing_provenance_fails`, `test_incomplete_provenance_fails` |
| CP-043-4 필수 구조 | `test_missing_required_top_level_section_fails`, `test_duplicated_required_top_level_section_fails`, `test_missing_required_subsection_fails`, `test_empty_required_subsection_body_fails`, `test_not_specified_fallback_counts_as_content` |
| CP-043-5 Acceptance Criteria | `test_acceptance_criteria_checkpoint_form_passes`, `test_acceptance_criteria_checklist_form_passes`, `test_acceptance_criteria_list_form_passes`, `test_empty_acceptance_criteria_fails`, `test_duplicate_checkpoint_identifiers_fail` |
| CP-043-6 Validation/Safety | `test_validation_commands_with_command_passes`, `test_validation_commands_not_specified_fallback_passes`, `test_empty_validation_commands_fails`, `test_safety_boundary_with_content_passes`, `test_empty_safety_boundary_fails` |
| CP-043-7 Contract traceability | `test_full_contract_reference_with_content_passes`, `test_empty_full_contract_reference_fails`, `test_path_only_full_contract_reference_fails` |
| CP-043-8 경고 동작 | `test_out_of_order_top_level_sections_warns_but_still_passes`, `test_unknown_top_level_section_warns_but_still_passes`, `test_unresolved_todo_marker_warns_but_still_passes`, `test_not_specified_fallback_is_not_an_unresolved_marker`, `test_marker_inside_full_contract_reference_is_not_flagged`, `test_cli_placeholder_angle_brackets_are_not_flagged_as_unresolved`, `test_warnings_alone_do_not_fail_validation`, `test_issues_are_ordered_by_line_then_code_then_message`, `test_severity_never_mixed_between_errors_and_warnings_tuples` |
| CP-043-9 CLI | `test_render_text_report_shows_pass`, `test_render_text_report_shows_failed_and_errors`, `test_render_json_report_is_valid_and_deterministic`, `test_render_json_report_includes_issue_fields`, `test_specification_validate_text_output_passes`, `test_specification_validate_json_output_is_valid`, `test_specification_validate_failure_exits_one`, `test_specification_validate_operational_error_exits_two`, `test_specification_validate_does_not_acquire_daemon_lock`, `test_specification_validate_does_not_call_github` |
| CP-043-10 회귀 | 기존 `tests/test_specification.py`(27개)와 기존 `tests/test_main.py`의 `specification` 테스트(5개) 그대로 통과 - `grep -n "^import\|^from" src/devbot/specification_validation.py`로 Agent 모듈 미import 확인 |
| CP-043-11 문서/근거 | 본 Result, `docs/00-roadmap.md`, `docs/08-beta-runbook.md` |
| CP-043-12 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS,
  815 passed (기존 763개 + `tests/test_specification_validation.py` 46개 +
  `tests/test_main.py` 6개)

## 수동 검증 결과 (읽기 전용, GitHub API 미호출, Agent 미호출)

`hjlee83/devbot` 실제 저장소를 대상으로 직접 실행했다 - `validate`는
GitHub API를 전혀 호출하지 않고 로컬 파일만 읽으므로 전부 오프라인
읽기 전용이다.

```
$ uv run devbot specification validate --task 42
task: 042
specification: specifications/042-specification-generator.md
result: PASS
no issues found.
(exit 0)

$ uv run devbot specification validate --task 42 --format json
{
  "task_number": 42,
  "specification_path": "specifications/042-specification-generator.md",
  "passed": true,
  "errors": [],
  "warnings": []
}
(exit 0)

$ uv run devbot specification validate --task 37
specification validate 오류: no Specification found for Task 037 in specifications
(exit 2)

$ uv run devbot specification validate --task 0
specification validate 오류: invalid task number: 0
(exit 2)

$ uv run devbot specification validate --task 43
(위 "배포된 초기 Specification 자체를 검증해 발견한 실제 이슈" 절 참고 -
 exit 1, SPV-002 에러 1개 + SPV-012 경고 2개)
```

Task 037은 `tasks/037-*.md` Contract는 있지만 `specifications/037-*.md`가
저장소에 커밋된 적이 없어서(Task 042는 자기 자신의 Specification만
커밋했다) `SpecificationNotFoundError`로 정상 fail closed된다 - 버그가
아니라 정확한 동작이다.

## 남은 TODO와 제한

- **Dispatch는 검증 결과를 아직 소비하지 않는다** - `devbot.workspace`/
  `devbot.agents`가 여전히 검증 여부와 무관하게 동작한다. 인터페이스만
  준비했다(위 "Future Dispatch integration" 참고).
- **SPV-012(미해결 마커)는 의미론적 구분을 하지 않는다** - "규칙을
  설명하는 문장 안의 TODO"와 "진짜 미해결 TODO"를 구분하지 못하고 둘 다
  경고한다(위 Task 043 자기 검증 사례 참고). 경고이므로 검증을 막지는
  않지만, 향후 더 정교한 휴리스틱(예: 코드 스팬 안의 규칙 이름은
  제외)이 필요할 수 있다.
- **Contract traceability(SPV-009)의 "path-only" 판정은 휴리스틱이다** -
  전체 본문이 백틱으로 감싼 단일 경로 한 줄인 경우만 잡아낸다. 더
  교묘하게 짧은 placeholder 텍스트를 쓰는 경우까지는 잡지 못할 수
  있다.
- **자동 복구/재작성은 구현하지 않았다** - 계약이 명시적으로 금지했다.
  검증 실패 시 사람이 Specification을 직접 고쳐야 한다.
- **code-to-Spec 검증, Task 044 템플릿 생성, Task 045 릴리스 분류는
  구현하지 않았다** - 계약과 이번 요청 범위 밖이다.

## 위험 요소

- `REQUIRED_TOP_LEVEL_SECTIONS` 확장은 Task 042의 `validate_specification_schema`
  동작도 (부수적으로) 더 엄격하게 만든다 - `# Full Task Contract
  Reference`가 없는 8번째 섹션 누락도 이제 이 함수가 잡아낸다. Task
  042의 실제 렌더링은 이 섹션을 항상 포함하므로 실전 영향은 없지만,
  향후 누군가 `render_specification`을 수정해 이 섹션을 빼면 Task 042의
  `generate_specification`이 즉시 `SpecificationError`로 fail closed된다
  (이미 있던 방어적 체크가 더 넓어진 것뿐 - 새로운 위험이 아니라 기존
  안전장치의 자연스러운 확장이다).
- 펜스 중첩 수정(`# Full Task Contract Reference` 이후 전체를 구조
  재해석 대상에서 제외)은 이 섹션이 "설계상 항상 마지막 섹션"이라는
  Task 042의 현재 렌더링 순서에 암묵적으로 의존한다. 향후 누군가 Task
  042의 섹션 순서를 바꿔 Full Task Contract Reference를 마지막이 아닌
  위치로 옮기면, 그 뒤에 오는 진짜 섹션들이 이 Validator에서 검증되지
  않고 조용히 건너뛰어질 수 있다 - 그런 순서 변경은 이번 Task의 범위
  밖이므로 지금은 안전하지만, 향후 렌더링 순서를 바꾸는 Task는 이
  가정을 다시 검토해야 한다.
