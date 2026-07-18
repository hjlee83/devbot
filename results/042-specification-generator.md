# Task 042 Result: Specification Generator

## 완료 내용

- 새 `src/devbot/specification.py`를 만들어 워크플로에 **Specification** 단계를
  추가했다: Goal → Planner → Issue → Contract → **Specification** → Dispatch.
  - `gather_task_evidence(github_client, repository, task_number)` - 정확히 하나의
    `tasks/NNN-*.md` Contract, 제목이 정확히 일치하는 GitHub Issue
    (`Task NNN: <title>`), 존재한다면 `docs/00-roadmap.md`의 해당 항목만
    읽어 `TaskEvidence`를 만든다. 네트워크 호출은 이 함수 안에서만 일어난다.
  - `render_specification(evidence)` - 순수 함수. `TaskEvidence`만으로 전체
    Specification 마크다운을 결정적으로 만든다 - 타임스탬프/무작위성/네트워크
    호출이 전혀 없다.
  - `validate_specification_schema(content)` - `REQUIRED_TOP_LEVEL_SECTIONS`
    7개(`# Overview`/`# Functional Requirements`/`# Technical Design`/
    `# Validation`/`# Safety`/`# Completion`/`# Handoff`) 중 빠진 것을 반환한다.
  - `generate_specification`/`write_specification` - evidence 수집 → 렌더링 →
    스키마 검증 → (선택) `specifications/NNN-slug.md`에 쓰기.
- 헤딩 정규화: `_SECTION_ALIASES`가 이 저장소 41개 기존 Contract의 실제 헤딩을
  조사해 만든 영어/한국어 매핑 표다("Goal"/"목표", "Out of Scope"/"제외 범위",
  "Git Rules"/"Git 규칙" 등). 매핑되지 않는 개념은 절대 지어내지 않고
  `"Not specified in the Task Contract."`로 채우며, Contract 원문 전체가 항상
  Specification 맨 끝 "Full Task Contract Reference" 섹션에 그대로 붙는다 - 매핑이
  놓친 내용도 정보 손실 없이 남는다.
- fail-closed 예외 6종(`ContractMissingError`/`ContractAmbiguousError`/
  `InvalidTaskError`/`IssueMissingError`/`AmbiguousTaskError`/
  `PlannerEvidenceMissingError`), 모두 `SpecificationError`의 서브클래스.
- CLI: 읽기 전용 `devbot specification show --task <N>`(stdout에만 출력)과
  파일에 쓰는 `devbot specification generate --task <N>`(`--dry-run` 시 show와
  동일하게 아무것도 쓰지 않음, `release publish --dry-run`/`goal execute
  --dry-run`/`goal dispatch --dry-run`과 같은 관례). 두 명령 모두 daemon lock을
  잡지 않는다(`ProcessLock` 블록 이전에 dispatch됨).
- **라이브 검증 중 실제 버그를 하나 발견해 고쳤다** - 아래 "라이브 검증 중
  발견한 버그와 수정" 절 참고.

## 아키텍처

```
        Goal
         |
         v
  devbot goal plan/execute   (Task 038/040 - 변경 없음)
         |
         v
     Issue + Branch + 초안 Contract
         |
         v
  devbot specification generate --task <N>   (신규, 이번 Task)
         |
         |  1. gather_task_evidence: Contract 1개 + Issue 1개(제목 정확히
         |     일치) + (있으면) roadmap 항목만 읽는다 - 순수 evidence 수집
         |  2. render_specification: evidence만으로 결정적 렌더링 (순수 함수)
         |  3. validate_specification_schema: 7개 최상위 섹션 존재 확인
         v
  specifications/NNN-slug.md
         |
         v (향후, 이번 Task 범위 아님)
  Agent Dispatch  -  Issue + Contract + Specification을 근거로 구현 Agent에게
                      전달 (devbot.workspace/devbot.agents는 이번에 미변경)
```

## 주요 설계 결정

- **Contract가 유일한 원본이다.** Specification은 Contract를 재구성한
  프레젠테이션일 뿐, 새로운 사실의 출처가 아니다 - 그래서 매핑되지 않은 내용은
  지어내지 않고 "Not specified"로 채우고, Contract 전체 원문을 항상 verbatim으로
  첨부한다. 이 설계는 41개 기존 Contract의 이질적인 헤딩 관례(영어/한국어 혼용)
  문제를 "완벽한 파서"가 아니라 "안전망"으로 해결한다.
- **Issue 제목이 Contract 제목과 정확히 일치해야 한다.** 한쪽만 믿고 다른 쪽을
  무시하는 대신, 둘이 다르면 `AmbiguousTaskError`로 fail closed한다 - 어느 쪽이
  맞는지 추측하지 않는다는 원칙을 지키기 위한 보수적 선택.
- **`render_specification`은 순수 함수다.** `gather_task_evidence`(I/O)와
  분리해, 같은 `TaskEvidence` 값이면 몇 번을 호출하든 완전히 같은 문자열이
  나온다 - `test_render_specification_is_deterministic`이 직접 비교로,
  `test_repeated_generation_produces_identical_files`가 실제 파일 바이트
  비교로 고정한다.
- **`specification.py`는 `devbot.agents`/`devbot.workspace`/`devbot.polling`/
  `devbot.review`/`devbot.rework`를 전혀 import하지 않는다.** 계약이 요구한
  "Dispatch는 이번에 건드리지 않는다"는 제약을 import 자체로 강제했다 - 리뷰어가
  파일 import 목록만 봐도 확인할 수 있다.

## 라이브 검증 중 발견한 버그와 수정

Task 042 자신의 Specification을 생성하는 라이브 검증(`devbot specification
generate --task 42`) 도중, 실제 버그를 하나 발견했다.

**증상**: `tasks/042-specification-generator.md`에는 사람이 읽을 "Specification
Structure" 예시를 마크다운 코드펜스(``` ``` ```) 안에 적어 두었는데, 그 예시
안에 `# Overview`/`## Goal`/`# Technical Design` 같은 진짜 헤딩처럼 보이는
텍스트가 들어 있다. `_split_level2_sections`가 코드펜스를 전혀 인식하지 못하고
전체 텍스트에 `^##\s+` 정규식을 그대로 돌리다 보니, 예시 안의 `## Out of Scope`
같은 가짜 헤딩이 진짜 "Out of Scope" 섹션과 같은 키로 병합(concatenate)됐고,
그 사이에 낀 `# Technical Design`(레벨 1이라 헤딩으로 인식되지 않음) 같은 텍스트가
그대로 "Out of Scope" 섹션의 **본문**으로 흘러 들어갔다. 결과적으로 생성된
Specification에서 `## Background`/`## Roadmap Context`가 `# Overview`가 아니라
가짜로 삽입된 `# Technical Design` 헤딩 아래에 렌더링되는 순서 오염이 나타났다.

**원인**: `_SECTION_HEADING_RE`가 코드펜스를 인식하지 못하는 순수 텍스트 정규식
매칭이었다. Contract 안에 예시용 마크다운 헤딩이 포함된 경우를 테스트 데이터로
검증하지 않았었다.

**수정**: `_split_level2_sections`가 먼저 ``` ``` ``` 펜스로 감싸인 문자 범위를
계산(`_fenced_code_ranges`)한 뒤, 그 범위 안에서 시작하는 헤딩 매치는 전부
제외하도록 바꿨다. 펜스 밖의 실제 헤딩만 섹션 경계로 인정한다.

**검증**:
- 새 회귀 테스트 `test_parse_contract_ignores_headings_inside_fenced_code_blocks`
  추가 - 펜스 안의 가짜 `## Out of Scope`/`## Architecture`가 섹션으로 잡히지
  않고, 펜스 안 텍스트는 그 펜스를 담고 있는 진짜 섹션("Example Template")의
  본문으로만 남는지 확인한다.
- 수정 전/후 `devbot specification generate --task 42`를 실제로 재실행해 출력
  순서가 `# Overview` → `# Functional Requirements` → `# Technical Design` →
  `# Validation` → `# Safety` → `# Completion` → `# Handoff` → `# Full Task
  Contract Reference`로 정확히 한 번씩만 나타나는지 직접 확인했다(수정 전에는
  `# Overview` 섹션 중간에 가짜 `# Technical Design`이 끼어 있었다).
- 수정 후에도 동일 Task를 반복 생성해 바이트가 완전히 같은지(md5 비교) 재확인했다.

이 버그는 Task 042 자신의 Contract에 "Specification Structure" 절을 코드펜스로
적어 두었기 때문에 자기 자신을 대상으로 생성할 때만 노출됐다 - 라이브 검증
요구사항이 실제로 잡아낸, 유닛 테스트만으로는 미리 알 수 없었던 결함이다.

## Future Dispatch integration (문서화 요구사항)

이 Task는 Dispatch를 구현하지 않지만, 향후 Agent Dispatch가 소비할 인터페이스는
이미 순수 데이터로 존재한다: `generate_specification`이 반환하는 `Specification`
(`task_number`/`slug`/`path`/`content`)과 디스크의 `specifications/NNN-slug.md`
둘 다 그대로 프롬프트 조립에 쓸 수 있다. 향후 Dispatch는 수동 프롬프트 대신
Issue 본문 + Contract + 이 `Specification.content`를 조합하도록 바뀔 수 있으며,
`specification.py`의 공개 함수 시그니처를 바꿀 필요가 없다.
`devbot.workspace.build_agent_prompt`/`devbot.agents`/`devbot.polling`/
`devbot.review`/`devbot.rework`는 이번에 한 줄도 바꾸지 않았다.

## 수정 파일

- `src/devbot/specification.py` (신규)
- `src/devbot/main.py` (`devbot specification generate|show` CLI 배선)
- `tests/test_specification.py` (신규, 27개 테스트)
- `tests/test_main.py` (5개 테스트 추가)
- `docs/00-roadmap.md` (Task 042 항목 추가)
- `docs/08-beta-runbook.md` ("Specification 생성 절차 (Task 042)" 절 추가)
- `tasks/042-specification-generator.md` (신규)
- `results/042-specification-generator.md` (본 문서)
- `specifications/042-specification-generator.md` (신규 - 이 Task 자신의
  Contract로부터 생성한 실제 Specification, 라이브 검증 산출물이자 정식 산출물)

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-042-1 생성과 결정론 | `test_gather_task_evidence_succeeds_for_well_formed_task`, `test_generate_specification_end_to_end`, `test_render_specification_is_deterministic`, `test_repeated_generation_produces_identical_files` |
| CP-042-2 Contract/Issue 없음 | `test_missing_contract_raises`, `test_missing_issue_raises` |
| CP-042-3 유효하지 않은/모호한 Task | `test_invalid_task_number_raises_for_zero_and_negative`, `test_invalid_task_raises_when_contract_lacks_canonical_title`, `test_ambiguous_contract_raises_when_multiple_files_match`, `test_ambiguous_task_raises_when_multiple_issues_match`, `test_ambiguous_task_raises_on_contract_issue_title_mismatch`, `test_planner_evidence_missing_raises_for_empty_contract` |
| CP-042-4 출력 스키마 검증 | `test_render_specification_includes_required_top_level_sections`, `test_validate_specification_schema_reports_missing_sections`, `test_validate_specification_schema_empty_when_complete` |
| CP-042-5 저장소 근거만 사용, 환각 없음 | `test_render_specification_uses_not_specified_default_for_missing_sections`, `test_render_specification_includes_full_contract_verbatim`, `test_render_specification_only_contains_grounded_content`, `test_goal_text_extracted_from_goal_executor_issue_body`, `test_roadmap_excerpt_included_when_entry_exists`, `test_roadmap_excerpt_none_when_roadmap_missing` |
| CP-042-6 Contract 파싱 정확성 | `test_parse_contract_extracts_title_and_sections`, `test_parse_contract_handles_missing_title`, `test_parse_contract_keeps_level3_headings_inside_parent_body`, `test_parse_contract_concatenates_duplicate_headings`, `test_parse_contract_ignores_headings_inside_fenced_code_blocks`(라이브 검증에서 발견한 버그의 회귀 테스트) |
| CP-042-7 CLI: 읽기 전용 show, 로컬 쓰기 generate | `test_specification_show_is_read_only`, `test_specification_generate_writes_file`, `test_specification_generate_dry_run_does_not_write`, `test_specification_error_returns_failure_exit_code`, `test_specification_command_does_not_acquire_daemon_lock`, `test_write_specification_creates_directory_if_missing` |
| CP-042-8 문서와 근거 | 본 Result, `docs/00-roadmap.md`, `docs/08-beta-runbook.md` |
| CP-042-9 검증 게이트 | `uv run ruff check .`, `uv run pytest` |

## Validation 결과

- `uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task037-uv-cache uv run pytest`: PASS, 763
  passed (기존 731개 + `tests/test_specification.py` 27개[최초 26개 + 펜스 버그
  회귀 테스트 1개] + `tests/test_main.py` 5개)

## 수동 검증 결과 (읽기 전용, Agent 미호출, 기존 Task 미수정)

`hjlee83/devbot` 실제 저장소를 대상으로 여러 과거 Task에 대해
`devbot specification show`/`generate`를 직접 실행했다 - `show`는 항상 stdout에만
출력하며(파일 미생성, 아래에서 `specifications/` 디렉터리 부재로 확인), `generate`는
오직 Task 042 자신에 대해서만 실행해 정식 산출물로 커밋했다. 어떤 과거 Task의
`tasks/*.md`/`results/*.md`도 수정하지 않았고, 어떤 구현 Agent도 호출하지 않았다.

```
$ uv run devbot specification show --task 5
specification 오류: no GitHub Issue titled 'Task 005: ...' found

$ uv run devbot specification show --task 20
specification 오류: no GitHub Issue titled 'Task 020: ...' found

$ uv run devbot specification show --task 37
# Specification: Task 037 — Release Operator UX
...(정상 렌더링, 영어 헤딩 Contract)

$ uv run devbot specification show --task 38
# Specification: Task 038 — Goal-based Planning (Operator Planner)
...(정상 렌더링)

$ uv run devbot specification show --task 40
# Specification: Task 040 — Goal Executor
...(정상 렌더링)

$ git status --short specifications/   # show만 실행한 시점
ls: specifications/: No such file or directory

$ uv run devbot specification generate --task 42
task: 042
path: specifications/042-specification-generator.md
bytes: 31970

$ uv run devbot specification generate --task 42   # 반복 실행
task: 042
path: specifications/042-specification-generator.md
bytes: 31970
# md5 비교: 두 번의 실행이 완전히 동일한 바이트를 생성함을 확인
```

Task 005/020에서 나온 `IssueMissingError`는 버그가 아니라 저장소의 실제 이슈
이력을 정확히 반영한 결과다: Task 001-009는 GitHub Issue 자체가 없고(이슈 추적은
#13부터 시작), Task 010-026은 Task 022 이전의 `Execute Task NNN: ...` 두-Issue
명명 규칙을 썼다(`gh issue list --repo hjlee83/devbot --state all`로 직접 확인).
정식 단일 `Task NNN: <title>` Issue 관례는 Task 022부터다 - 그래서 Task
037/038/040처럼 그 이후 Task만 성공적으로 렌더링되고, 이전 Task는 정확히 계약이
요구한 대로 fail closed된다.

## 남은 TODO와 제한

- **`specifications/` 디렉터리에는 이번에 Task 042 자신의 산출물 1개만 커밋했다.**
  계약대로 "여러 과거 Task에 대해 생성"은 라이브 검증(위 로그, 읽기 전용
  `show`)으로 수행했고, 그 파일들은 저장소에 커밋하지 않았다 - 벌크 생성/커밋은
  이번 범위 밖이다.
- **Dispatch는 Specification을 아직 소비하지 않는다** - `devbot.workspace`/
  `devbot.agents`가 여전히 수동으로 조립한 프롬프트를 쓴다. 인터페이스만
  준비했다(위 "Future Dispatch integration" 참고).
- **헤딩 alias 표는 최선-노력이다** - 이 저장소가 실제로 쓴 헤딩 문구를 조사해
  만들었을 뿐, 완전한 문법이 아니다. 새 Contract가 표에 없는 새로운 헤딩 문구를
  쓰면 해당 개념은 "Not specified"로 렌더링된다(정보 손실 없음 - Full Task
  Contract Reference에는 항상 남는다) - alias 표를 갱신하면 완전성이 개선될
  뿐, 안전성에는 영향이 없다.

## 위험 요소

- Contract/Issue 제목 정확 일치 요구가 지나치게 엄격할 수 있다 - 사람이 Issue
  제목을 조금이라도 고치면(오타 수정 등) `AmbiguousTaskError`로 막힌다. 의도된
  보수적 설계지만, 실제 운영에서 불편하면 향후 정규화(공백/구두점 무시 등) 완화가
  필요할 수 있다.
- 펜스-인식 파싱 수정(`_fenced_code_ranges`)은 ` ``` ` 세 개로 여는/닫는 표준
  마크다운 펜스만 인식한다 - 물결표(`~~~`) 펜스나 들여쓰기 코드 블록은 여전히
  펜스로 인식되지 않는다. 이 저장소의 모든 기존 Contract는 백틱 펜스만 쓰므로
  현재는 문제가 없지만, 후속 Task가 다른 펜스 문법을 쓰는 Contract를 추가하면
  같은 종류의 헤딩 오염이 재발할 수 있다.
