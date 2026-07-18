# Task 044: Specification Template Engine

## Goal

Introduce a deterministic Specification Template Engine that selects a template from explicit Task Contract evidence while preserving the canonical Specification schema established by Tasks 042 and 043.

The engine must improve task-specific guidance without creating incompatible Specification formats. Every rendered Specification must still pass `devbot specification validate --task <N>`.

## Context

Task 042 introduced deterministic Specification generation from repository evidence. Task 043 introduced the validation gate and made the canonical top-level schema enforceable.

The current generator renders every Task through one generic mapping. Task 044 adds task-type-aware guidance while retaining one shared structural contract:

`Goal → Planner → Issue → Contract → Template Selection → Specification → Validation → Dispatch`

Templates are policy overlays, not independent document schemas.

## In Scope

1. Add a first-class template registry.
2. Add deterministic template selection based only on explicit Task Contract evidence.
3. Support the following built-in template IDs:
   - `feature`
   - `bugfix`
   - `refactor`
   - `docs`
   - `internal`
   - `generic`
4. Keep the Task 042/043 canonical top-level sections and required subsections in every output.
5. Allow templates to influence guidance, section emphasis, and evidence mapping without removing canonical sections.
6. Integrate template selection into `devbot specification show|generate`.
7. Record the selected template in Specification Provenance.
8. Provide explicit CLI override support for controlled/manual use.
9. Add read-only template inspection commands.
10. Add deterministic tests and documentation.

## Out of Scope

- LLM- or heuristic-probability-based classification.
- Dispatch or Agent invocation.
- Workflow Engine integration.
- Code-to-Spec conformance validation.
- Contract-based release classification; that belongs to Task 045.
- User-defined external templates or plugin loading.
- Jinja2 or another general-purpose template language.
- Rewriting historical Contracts or Specifications.
- Making Task 043 template-aware through separate schemas.
- Automatic PR creation or merge.

## Template Policy

### Canonical schema remains authoritative

All template outputs must retain these top-level sections in canonical order:

1. `# Overview`
2. `# Functional Requirements`
3. `# Technical Design`
4. `# Validation`
5. `# Safety`
6. `# Completion`
7. `# Handoff`
8. `# Full Task Contract Reference`

All Task 043 required subsections must remain present. A template may alter how evidence is mapped or add explanatory/template-specific subsections, but it may not remove, rename, or reorder required canonical sections in normal generated output.

### Explicit evidence only

Template selection must use explicit normalized evidence from the Task Contract. No model call, network inference, repository-language inference, filename guess, or speculative semantic classifier is allowed.

The canonical Contract field is:

```markdown
## Specification Type

feature
```

Allowed values are the six built-in template IDs. Matching is case-insensitive after trimming; the stored/rendered canonical ID is lowercase.

### Generic fallback

Historical Contracts do not contain `## Specification Type`. Missing type must select `generic`, preserving Task 042 behaviour and backward compatibility.

An unknown explicit type must fail closed. It must not silently fall back to `generic`.

### Explicit CLI override

`--template <id>` may override Contract selection for `show` and `generate`.

The override must:

- accept only registered IDs;
- be visible in Provenance;
- not mutate the Contract;
- produce deterministic output;
- take precedence over the Contract field.

## Built-in Templates

### `generic`

Preserves Task 042 mapping behaviour as closely as possible and is the fallback for historical Contracts.

### `feature`

Emphasizes externally observable behaviour, inputs/outputs, compatibility, rollout, migration, and acceptance criteria.

### `bugfix`

Emphasizes problem statement, reproduction evidence, expected/actual behaviour, root-cause evidence when present, fix boundaries, and regression tests.

The engine must not invent a root cause when the Contract does not provide one.

### `refactor`

Emphasizes current design, target design, preserved behaviour, compatibility constraints, migration/rollback, and regression protection.

### `docs`

Emphasizes audience, documentation surface, examples, accuracy sources, compatibility/migration notes, and validation of links/examples where specified.

### `internal`

Emphasizes operational goal, internal interfaces, constraints, observability, safety, rollback, and validation. It must not imply public API changes unless explicitly stated.

## Functional Requirements

1. Define an immutable template model with stable template IDs.
2. Define a registry that rejects duplicate IDs.
3. Registry iteration and CLI listing must be deterministic.
4. Parse `Specification Type` through the existing bilingual Contract parsing approach where practical.
5. Select `generic` only when the Contract omits the type.
6. Reject an explicitly unknown template type with a dedicated error.
7. Reject an invalid CLI override with a dedicated error or existing CLI usage error.
8. Render the selected template ID and selection source in Provenance.
9. Identical evidence plus identical override must render byte-identical output.
10. Different built-in templates must produce observably different task-specific guidance while preserving the canonical schema.
11. Generated output for every built-in template must pass Task 043 validation.
12. Existing `show` and `generate` calls without new flags must remain compatible.
13. No Agent-related module may be imported or invoked.
14. Template listing and inspection must be read-only and must not acquire the daemon lock.

## CLI

Required commands:

```bash
devbot specification templates
devbot specification template show --template <id>
devbot specification show --task <N> [--template <id>]
devbot specification generate --task <N> [--template <id>] [--dry-run]
```

`templates` must list stable IDs and concise descriptions in deterministic order.

`template show` must display the registered template policy/guidance without writing files.

## Architecture

Recommended structure, adjusted as needed to match the current repository:

```text
src/devbot/
├── specification.py
├── specification_validation.py
└── specification_templates.py
```

Do not perform a broad package migration of `specification.py` and `specification_validation.py` in this Task. A future package split may be appropriate, but Task 044 should add the template domain with minimal churn.

Recommended concepts:

- `SpecificationTemplate`
- `SpecificationTemplateRegistry`
- `TemplateSelection`
- `TemplateSelectionSource`
- `select_specification_template(...)`
- `get_specification_template(...)`
- `list_specification_templates(...)`

Names may follow existing project conventions, but the boundaries must remain explicit and testable.

## Quality Gates

### CP-044-1 — Registry

Required tests:

- all six built-in IDs are registered;
- IDs are unique;
- registry order is deterministic;
- unknown lookup fails clearly.

### CP-044-2 — Contract selection

Required tests:

- each explicit Contract type selects its matching template;
- matching is case-insensitive after trimming;
- missing type selects `generic`;
- unknown explicit type fails closed;
- repeated selection is deterministic.

### CP-044-3 — CLI override

Required tests:

- valid override takes precedence;
- override source is recorded in Provenance;
- invalid override fails;
- override does not mutate Contract or other repository artifacts.

### CP-044-4 — Canonical schema compatibility

Required tests:

- every built-in template renders every required top-level section;
- every built-in template includes all Task 043 required subsections;
- every built-in template output passes `validate_specification_text`;
- canonical section order remains unchanged.

### CP-044-5 — Template differentiation

Required tests:

- feature guidance differs from bugfix guidance;
- bugfix includes regression/reproduction emphasis without invented root cause;
- refactor emphasizes preserved behaviour/compatibility;
- docs emphasizes audience/examples/accuracy;
- internal emphasizes operational constraints and safety;
- generic preserves historical behaviour.

### CP-044-6 — Generator integration

Required tests:

- `show` uses Contract-selected template;
- `generate` writes Contract-selected output;
- `--dry-run` remains read-only;
- identical inputs produce identical bytes;
- existing Task 042 generator tests remain valid or are deliberately updated without losing coverage.

### CP-044-7 — Template inspection CLI

Required tests:

- template list is deterministic;
- template show is read-only;
- unknown template returns non-zero;
- commands do not acquire daemon lock.

### CP-044-8 — Safety and scope

Required tests or inspection evidence:

- no Agent invocation;
- no Dispatch, polling, review, or rework behaviour change;
- no Task 045 release classification;
- no external template execution;
- no network call in selection or rendering.

### CP-044-9 — Live validation

Required evidence:

- generate or show representative Specifications for all six templates;
- validate each representative output with Task 043 validator;
- all six pass;
- record commands and outcomes in the Result document.

### CP-044-10 — Quality gate

```bash
uv run ruff check .
uv run pytest
```

Both must pass.

## Files Expected to Change

Expected, not mandatory:

- `src/devbot/specification_templates.py`
- `src/devbot/specification.py`
- `src/devbot/main.py`
- `tests/test_specification_templates.py`
- `tests/test_specification.py`
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `results/044-specification-template-engine.md`

The following authoritative artifacts already exist before implementation:

- `tasks/044-specification-template-engine.md`
- `specifications/044-specification-template-engine.md`

## Risk

- Template-specific output can accidentally create multiple incompatible schemas. Mitigation: canonical sections/subsections remain mandatory and Task 043 validates every output.
- Weak heuristics can silently choose the wrong template. Mitigation: explicit Contract field only, generic fallback only for omission, fail closed for unknown explicit values.
- A general-purpose template language can introduce injection and maintenance risk. Mitigation: immutable Python policy objects and deterministic render functions only.
- Broad refactoring can destabilize Tasks 042 and 043. Mitigation: add a focused module and preserve existing public behaviour.

## Rollback Strategy

Revert Task 044 commits. Contracts without `Specification Type` continue to use the existing generic path. No persistent data migration is required.

## Reviewer Focus

- Confirm templates are overlays on one canonical schema, not separate schemas.
- Confirm unknown explicit types fail closed.
- Confirm `generic` preserves historical generation.
- Confirm all six template outputs pass Task 043 validation.
- Confirm no speculative root-cause or requirement text is invented.
- Confirm selection and rendering remain deterministic and read-only.
- Confirm Task 045 and Dispatch are untouched.

## Definition of Done

- Issue #92 remains the canonical Task Issue.
- All CP-044 checkpoints pass.
- All six built-in templates are implemented and documented.
- Existing Specification generation remains backward compatible.
- Every template output passes Task 043 validation.
- `results/044-specification-template-engine.md` records actual implementation and test evidence.
- PR title is `Task 044: Specification Template Engine`.
- Branch is `task/044-specification-template-engine`.
- Final merge remains manual.

## Result 문서 경로

`results/044-specification-template-engine.md`

## Git Rules

- Task Issue: #92
- Branch: `task/044-specification-template-engine`
- Pull Request title: `Task 044: Specification Template Engine`
- Result: `results/044-specification-template-engine.md`
- Reuse this Issue and branch.
- Do not create another Issue or branch for Task 044.
- Final merge remains manual.
