# Specification: Task 043 — Specification Validator

## Provenance

- Task Contract: `tasks/043-specification-validator.md`
- Previous Task: Task 042 — Specification Generator
- Architecture direction: Specification-first workflow
- Implementation owner: Claude
- Architecture and review owner: DevBot Architect

# Overview

## Goal

Introduce a first-class Specification Validator that determines whether a generated
DevBot Specification is structurally complete, internally consistent, and safe to
hand off to an implementation Agent.

Target workflow:

`Goal → Planner → Issue → Contract → Specification → Validation → Dispatch`

## Scope

In scope:

1. Parse one canonical `specifications/NNN-*.md` document.
2. Validate title, provenance, required sections and subsections, content presence,
   Acceptance Criteria, validation commands, safety boundaries, and Contract traceability.
3. Distinguish errors from warnings.
4. Produce deterministic structured results and human-readable reports.
5. Add `devbot specification validate --task <N>`.
6. Add deterministic JSON output with `--format json`.
7. Fail closed on missing, ambiguous, unreadable, or invalid input.
8. Add unit, CLI, and regression tests.
9. Update roadmap and runbook documentation.

Out of scope:

- Code-to-Spec conformance validation.
- Runtime implementation verification.
- Agent invocation or automatic Dispatch.
- Role Dispatch, polling, review, or rework changes.
- Task 044 template generation.
- Task 045 release classification.
- Automatic repair of invalid Specifications.
- Historical Specification migration.
- GitHub Checks or CI workflow integration.

## Background

Task 042 introduced `specifications/NNN-slug.md` as the authoritative implementation
artifact and added a limited top-level schema check. Task 043 promotes validation into
an explicit domain capability with stable rule codes, deterministic results, CLI
support, and fail-closed file resolution.

The Task 042 Specification format is authoritative. Task 043 must validate that format
rather than define a competing schema.

## Roadmap Context

Task 043 is the second task in the Specification Platform sequence:

- Task 042: Specification Generator
- Task 043: Specification Validator
- Task 044: Specification Template Engine
- Task 045: Contract-based Release Classification

Task 043 establishes the quality gate required before a future Workflow Engine may
safely dispatch a Specification to an implementation Agent.

# Functional Requirements

## Required Behaviour

1. Add a validation API that accepts one Specification and returns:
   - Task number;
   - Specification path;
   - `passed`;
   - ordered errors;
   - ordered warnings.
2. Any error sets `passed = false`.
3. Warnings alone do not fail validation.
4. Repeated validation of identical bytes returns equivalent results with stable issue
   ordering.
5. Every issue includes a stable rule code, severity, message, and optional section or
   line location.
6. Validation is read-only.
7. Operational failures and document validation failures remain distinguishable.
8. Reuse Task 042 schema constants and parsing concepts where practical.
9. Do not create a second incompatible Specification schema.
10. Human-readable CLI output prints PASS or FAILED plus errors and warnings.
11. JSON output is deterministic and suitable for future workflow automation.
12. Provide:

    ```bash
    devbot specification validate --task <N>
    devbot specification validate --task <N> --format json
    ```

13. Expected exit behaviour:
    - `0`: valid document, including warnings-only results;
    - `1`: document validation failure;
    - another non-zero code: usage or operational failure, following existing CLI conventions.
14. The validation command must not acquire the daemon lock.

## Acceptance Criteria

### CP-043-1 — Valid Specification

- Canonical Task 042-style Specification passes.
- Result has `passed = true` and no errors.
- Repeated validation returns equivalent results.

### CP-043-2 — File resolution

- Invalid Task numbers are rejected.
- Missing Specification raises a dedicated error.
- Multiple matching files raise a dedicated ambiguity error.
- Unreadable input raises a dedicated load error.

### CP-043-3 — Canonical title

- Malformed, mismatched, or empty titles fail.
- Canonical title passes.

### CP-043-4 — Required structure

- Missing or duplicated required top-level sections fail.
- Missing required subsection fails.
- Empty required body fails.
- `Not specified in the Task Contract.` counts as explicit content.

### CP-043-5 — Acceptance Criteria validation

- Checkpoint, checklist, and list criteria pass.
- Empty Acceptance Criteria fail.
- Duplicate checkpoint identifiers fail.

### CP-043-6 — Validation and safety

- Validation Commands containing commands pass.
- Empty Validation Commands fail.
- Empty Agent safety boundary fails.

### CP-043-7 — Contract traceability

- Non-empty Full Task Contract Reference passes.
- Empty and path-only references fail.

### CP-043-8 — Warning behaviour

- Incorrect top-level order creates a warning.
- Unknown top-level sections create a warning.
- Unresolved template markers create a warning.
- Warnings alone do not fail validation or CLI exit status.

### CP-043-9 — CLI behaviour

- Human-readable output is covered.
- JSON output is valid and deterministic.
- Valid exits 0.
- Validation failure exits 1.
- Operational errors use a distinct non-zero code.
- Validation is read-only and does not acquire daemon lock.

### CP-043-10 — Regression protection

- Existing Task 042 generator and CLI tests continue to pass.
- Validator remains compatible with Task 042 schema definitions.
- No Agent module is imported or invoked by validation code.

### CP-043-11 — Documentation and evidence

Required artifacts:

- `tasks/043-specification-validator.md`
- `specifications/043-specification-validator.md`
- `results/043-specification-validator.md`
- roadmap update
- runbook update

### CP-043-12 — Quality gate

```bash
uv run ruff check .
uv run pytest
```

Both must pass before PR review.

## Out of Scope

- Code-to-Spec conformance validation.
- API runtime verification.
- Automatic issue, branch, PR, or release creation by DevBot.
- Automatic Specification repair.
- Dispatch integration.
- Release classification.
- Template selection or generation changes assigned to Task 044.
- Historical Specification migration.
- Making warnings fatal through configuration.
- Third-party rule plugins.

# Technical Design

## Architecture

Recommended module boundary:

```text
src/devbot/
├── specification.py
└── specification_validation.py
```

`specification.py` remains responsible for generation and canonical schema concepts.

`specification_validation.py` is responsible for:

- Specification file resolution;
- parsing needed for validation;
- validation rule execution;
- deterministic result construction;
- human and JSON report rendering.

Recommended immutable model:

```python
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"

@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    section: str | None = None
    line: int | None = None

@dataclass(frozen=True)
class SpecificationValidationResult:
    task_number: int
    specification_path: Path
    passed: bool
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]
```

Recommended public functions:

```python
def validate_specification_file(repository_root: Path, task_number: int) -> SpecificationValidationResult: ...
def validate_specification_text(text: str, *, task_number: int, specification_path: Path) -> SpecificationValidationResult: ...
def render_validation_report(result: SpecificationValidationResult, *, output_format: str = "text") -> str: ...
```

Names may change to match repository style, but behaviour must remain equivalent.

### Canonical rules

- `SPV-001`: canonical title mismatch or empty title — ERROR.
- `SPV-002`: missing or incomplete Provenance — ERROR.
- `SPV-003`: missing or duplicated required top-level section — ERROR.
- `SPV-004`: missing required subsection — ERROR.
- `SPV-005`: empty required body — ERROR.
- `SPV-006`: missing concrete Acceptance Criteria or duplicate checkpoint — ERROR.
- `SPV-007`: empty Validation Commands — ERROR.
- `SPV-008`: missing or empty Agent safety boundary — ERROR.
- `SPV-009`: empty or path-only Full Task Contract Reference — ERROR.
- `SPV-010`: non-canonical top-level order — WARNING.
- `SPV-011`: unknown top-level section — WARNING.
- `SPV-012`: unresolved TODO/TBD/template marker — WARNING.
- `SPV-013`: deterministic issue ordering requirement.

Canonical required top-level sections are the Task 042 sections:

1. Overview
2. Functional Requirements
3. Technical Design
4. Validation
5. Safety
6. Completion
7. Handoff
8. Full Task Contract Reference

Canonical required subsections:

- Overview: Goal, Scope, Background, Roadmap Context
- Functional Requirements: Required Behaviour, Acceptance Criteria, Out of Scope
- Technical Design: Architecture, Files Expected to Change, Dependencies, Constraints,
  Migration Notes
- Validation: Required Tests and Quality Gates, Validation Commands, Success Criteria
- Safety: Things the Implementation Agent Must NOT Do
- Completion: Expected Deliverables, Result Document, PR Expectations
- Handoff: Required Handoff Procedure, Token-Limit Behaviour

## Files Expected to Change

Expected, not mandatory:

- `src/devbot/specification_validation.py`
- `src/devbot/specification.py` only for safe schema/helper reuse
- `src/devbot/main.py`
- `tests/test_specification_validation.py`
- `tests/test_main.py`
- `docs/00-roadmap.md`
- `docs/08-beta-runbook.md`
- `tasks/043-specification-validator.md`
- `specifications/043-specification-validator.md`
- `results/043-specification-validator.md`

## Dependencies

Use the Python standard library and dependencies already present in the repository.
Do not add a Markdown parsing dependency without explicit architectural justification.

## Constraints

- Task 042 format remains authoritative.
- Validation is deterministic and read-only.
- Missing and ambiguous input fails closed.
- Rule codes are stable.
- Ordering must not depend on sets, dictionaries, or filesystem enumeration.
- No Agent-related module may be imported or invoked.
- Dispatch, polling, review, and rework behaviour must not change.
- Source Specifications must not be normalized or rewritten.
- Avoid speculative semantic validation.

## Migration Notes

This feature is additive. No historical Specification must be rewritten or made to
pass automatically.

If Task 042's `validate_specification_schema` becomes redundant, retain a compatible
wrapper or migrate callers and tests explicitly. Do not leave two definitions of
canonical validity.

Rollback consists of reverting Task 043 commits.

# Validation

## Required Tests and Quality Gates

All CP-043 checkpoints require direct tests. Include compact text fixtures for each
rule plus at least one complete canonical Specification fixture.

Do not rely only on this large Specification as the valid fixture; rule behaviour must
remain easy to isolate.

## Validation Commands

```bash
uv run ruff check .
uv run pytest
```

## Success Criteria

- All required validation rules are implemented.
- Valid Task 042-format Specifications pass.
- Invalid Specifications produce stable, actionable rule-coded errors.
- Warnings never fail validation in Task 043.
- Human and JSON reports are deterministic.
- CLI exit behaviour is tested.
- Validation performs no file mutation.
- Existing Task 042 behaviour remains compatible.
- No Agent is invoked.
- Required documentation and evidence artifacts exist.
- PR is ready for review and merge remains manual.

# Safety

## Things the Implementation Agent Must NOT Do

- Do not invoke any implementation or review Agent.
- Do not connect validation to automatic Dispatch.
- Do not modify Role Dispatch, polling, review, or rework flows.
- Do not implement Task 044 template selection.
- Do not implement Task 045 release classification.
- Do not perform code-to-Spec validation.
- Do not rewrite invalid or historical Specifications automatically.
- Do not invent requirements missing from the Contract.
- Do not add timestamps, random identifiers, or unstable ordering.
- Do not make warnings fatal.
- Do not create a competing canonical Specification schema.
- Do not merge the PR automatically.

# Completion

## Expected Deliverables

1. Specification validation domain module.
2. `devbot specification validate --task <N>`.
3. Human-readable validation report.
4. Deterministic JSON validation report.
5. Dedicated resolution and loading exceptions.
6. Unit tests for every rule.
7. CLI and regression tests.
8. Documentation updates.
9. Task Contract, Specification, and Result document.

## Result Document

Create `results/043-specification-validator.md` containing:

- implementation summary;
- actual files changed;
- rule codes implemented;
- test commands and outcomes;
- CLI examples and exit outcomes;
- deviations from this Specification;
- PR number and commit evidence.

## PR Expectations

- Branch: `task/043-specification-validator`
- PR title: `Task 043: Specification Validator`
- Keep the PR limited to Task 043.
- Explain changes to Task 042 APIs or schema constants.
- Include actual validation evidence.
- Final merge remains manual.

# Handoff

## Required Handoff Procedure

Claude must:

1. Read this entire Specification before changing code.
2. Inspect Task 042 implementation and tests.
3. Implement only Task 043 scope.
4. Run all quality gates.
5. Create the Result document with actual evidence.
6. Open the required PR.
7. Stop when the PR is ready for architectural review.

Implementation instruction:

```text
Implement Task 043 using specifications/043-specification-validator.md as the
authoritative source. Read the full Specification and Task 042 implementation first.
Satisfy every CP-043 checkpoint, run all required validation commands, create the
Result document, and open the required PR. Do not expand into Dispatch, template
generation, release classification, or code-to-Spec validation.
```

## Token-Limit Behaviour

When nearing a context or token limit, preserve progress in repository artifacts and
provide a concise handoff containing completed work, remaining checkpoints, failing
tests or unresolved decisions, and current branch/commit state.

Do not silently reduce scope or mark incomplete checkpoints complete.

# Full Task Contract Reference

The authoritative Task Contract is `tasks/043-specification-validator.md` on the same
branch. Its full body defines the Goal, Scope, rule codes, Quality Gates, safety
boundaries, files, Result path, branch, PR title, and manual-merge requirement for
Task 043. Claude must read the complete Contract before implementation.
