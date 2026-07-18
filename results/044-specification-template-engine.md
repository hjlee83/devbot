# Task 044 Result: Specification Template Engine

## 완료 내용

- `src/devbot/specification_templates.py`를 추가해 immutable built-in template model, deterministic registry, selection API를 구현했다.
- Built-in template ID 6개를 정확히 등록했다: `feature`, `bugfix`, `refactor`, `docs`, `internal`, `generic`.
- `## Specification Type` Contract field 기반 selection을 추가했다. 값은 trim + lowercase로 정규화한다.
- Contract field가 없으면 historical compatibility를 위해 `generic`을 선택하고, unknown explicit 값은 fail closed한다.
- `devbot specification show|generate --template <id>` override를 추가했다.
- generated Specification Provenance에 selected template ID와 source(`contract`/`override`/`default`)를 기록한다.
- Template-specific guidance를 canonical `# Technical Design` 안의 `## Template Guidance`로 렌더링한다. Task 043 top-level schema와 required subsection은 변경하지 않았다.
- Read-only inspection CLI를 추가했다:
  - `devbot specification templates`
  - `devbot specification template show --template <id>`
- `docs/00-roadmap.md`와 `docs/08-beta-runbook.md`에 Task 044 운영/검증 evidence를 반영했다.

## 주요 설계 결정

- Template은 별도 schema가 아니라 canonical Specification 위의 policy overlay다.
- Selection은 Contract의 명시적 `## Specification Type` 또는 CLI override만 사용한다. filename, repository language, LLM, network inference는 사용하지 않는다.
- `generic`은 missing field fallback 전용이며, unknown explicit value는 `UnknownSpecificationTemplateError`로 실패한다.
- Template guidance는 fixed policy prose만 사용하고, missing facts를 root cause, rollout, compatibility claim처럼 지어내지 않는다.
- Inspection commands는 config load 이후 바로 처리되고 daemon lock, GitHub client, Agent/Dispatch path를 사용하지 않는다.

## 수정 파일

- `src/devbot/specification_templates.py`
- `src/devbot/specification.py`
- `src/devbot/main.py`
- `tests/test_specification_templates.py`
- `tests/test_specification.py`
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `results/044-specification-template-engine.md`

## Public API

- `SpecificationTemplate`
- `SpecificationTemplateRegistry`
- `TemplateSelection`
- `TemplateSelectionSource`
- `list_specification_templates()`
- `get_specification_template()`
- `select_specification_template()`
- `render_template_policy()`

## Checkpoint Evidence

| Checkpoint | Evidence |
| --- | --- |
| CP-044-1 Registry | `test_builtin_registry_contains_exactly_six_deterministic_ids`, `test_registry_rejects_duplicate_ids`, `test_unknown_template_lookup_fails_clearly` |
| CP-044-2 Contract selection | `test_explicit_contract_type_selects_matching_template`, `test_missing_contract_type_selects_generic`, `test_unknown_explicit_contract_type_fails_closed`, `test_repeated_selection_is_deterministic` |
| CP-044-3 CLI override | `test_cli_override_takes_precedence_and_records_source`, `test_invalid_cli_override_fails`, `test_specification_show_passes_template_override`, `test_specification_generate_passes_template_override` |
| CP-044-4 Schema compatibility | `test_every_template_output_preserves_schema_and_passes_validator` |
| CP-044-5 Template differentiation | `test_templates_are_observably_different_without_invented_bugfix_root_cause` |
| CP-044-6 Generator integration | `test_generate_specification_uses_contract_selected_template`, `test_generate_specification_override_is_deterministic`, `test_generate_dry_run_style_render_does_not_write`, existing Task 042 generator tests |
| CP-044-7 Inspection CLI | `test_specification_templates_lists_registered_templates`, `test_specification_template_show_is_read_only`, `test_specification_template_show_unknown_returns_nonzero`, `test_template_policy_render_is_deterministic` |
| CP-044-8 Scope protection | Import/code inspection: no Agent, Dispatch, polling, review, rework, external template, or Task 045 integration added |
| CP-044-9 Live validation | Six `devbot specification show --task 44 --template <id>` outputs validated with Task 043 validator |
| CP-044-10 Quality gate | `uv run ruff check .`, `uv run pytest` |

## Live Template Validation

Representative outputs were generated with:

```bash
uv run devbot specification show --task 44 --template bugfix
uv run devbot specification show --task 44 --template docs
uv run devbot specification show --task 44 --template feature
uv run devbot specification show --task 44 --template generic
uv run devbot specification show --task 44 --template internal
uv run devbot specification show --task 44 --template refactor
```

Each output was validated through `validate_specification_text(..., task_number=44)`:

```text
bugfix: PASS errors=0 warnings=0
docs: PASS errors=0 warnings=0
feature: PASS errors=0 warnings=0
generic: PASS errors=0 warnings=0
internal: PASS errors=0 warnings=0
refactor: PASS errors=0 warnings=0
```

Additional CLI evidence:

```text
$ uv run devbot specification templates
bugfix: Bug fix work with reproduction and regression-protection guidance.
docs: Documentation work with audience, examples, and source-accuracy guidance.
feature: User-visible feature work with compatibility and rollout guidance.
generic: Historical Task 042-compatible fallback for Contracts without a Specification Type.
internal: Internal operational work with safety, observability, and rollback guidance.
refactor: Refactor work that preserves behaviour while changing structure.

$ uv run devbot specification template show --template bugfix
template: bugfix
description: Bug fix work with reproduction and regression-protection guidance.
guidance:
- Emphasize problem reproduction, expected behaviour, and actual behaviour from Contract evidence.
- Define the fix boundary and regression protection without inventing a root cause.
- Keep validation tied to the failing scenario and any named regression tests.

$ uv run devbot specification validate --task 44
task: 044
specification: specifications/044-specification-template-engine.md
result: PASS
no issues found.
```

## Validation 결과

- `UV_CACHE_DIR=/private/tmp/devbot-task044-uv-cache uv run ruff check .`: PASS
- `UV_CACHE_DIR=/private/tmp/devbot-task044-uv-cache uv run pytest`: PASS, 847 passed in 230.80s

## 수동 검증 결과

- Issue: https://github.com/hjlee83/devbot/issues/92
- Branch: `task/044-specification-template-engine`
- PR: Pending
- `Dispatch`, Task 045, external templates, code-to-Spec validation 범위는 구현하지 않았다.

## 남은 TODO와 제한

- PR 생성 후 architectural review가 필요하다.
- Final merge는 수동이다.

## 위험 요소

- Template guidance는 fixed prose이므로 Contract에서 세부 rollout, root cause, migration fact가 제공되지 않으면 구체 값을 만들지 않는다.
- 향후 template별 더 세밀한 evidence mapping이 필요하면 canonical schema와 Task 043 validator compatibility를 먼저 유지해야 한다.
