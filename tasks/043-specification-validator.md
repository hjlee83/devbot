# Task 043: Specification Validator

## Goal

Introduce a first-class Specification Validator that determines whether a generated
DevBot Specification is structurally complete, internally consistent, and safe to
hand off to an implementation Agent.

Target workflow:

`Goal → Planner → Issue → Contract → Specification → Validation → Dispatch`

## Context

Task 042 introduced `specifications/NNN-slug.md` as the authoritative implementation
artifact and added a limited top-level schema check. Task 043 promotes validation into
an explicit domain capability with stable rules, deterministic results, CLI support,
and fail-closed file resolution.

The Task 042 Specification format is authoritative. Task 043 must validate that format
rather than define a competing schema.

## In Scope

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

## Out of Scope

- Code-to-Spec conformance validation.
- Runtime implementation verification.
- Agent invocation or automatic Dispatch.
- Role Dispatch, polling, review, or rework changes.
- Task 044 template generation.
- Task 045 release classification.
- Automatic repair of invalid Specifications.
- Historical Specification migration.
- GitHub Checks or CI workflow integration.
- Third-party validation rule plugins.

## Functional Requirements

1. Add a validation API that accepts one Specification and returns:
   - Task number;
   - Specification path;
   - `passed`;
   - ordered errors;
   - ordered warnings.
2. Any error sets `passed = false`.
3. Warnings alone do not fail validation.
4. Repeated validation of identical bytes must return equivalent results with stable
   issue ordering.
5. Each validation issue must include a stable rule code, severity, message, and
   optional section or line location.
6. Validation must be read-only.
7. Operational failures and document validation failures must remain distinguishable.
8. The validator must reuse Task 042 schema constants and parsing concepts where
   practical and must not introduce a second incompatible schema.
9. Human-readable CLI output must print PASS or FAILED plus errors and warnings.
10. JSON output must be deterministic and suitable for future workflow automation.

## Validation Rules

### SPV-001 — Canonical title

The document must begin with `# Specification: Task NNN — <Title>`. The Task number
must match the requested Task and the title must not be empty. Violation: ERROR.

### SPV-002 — Provenance

`## Provenance` must exist and identify the Task Issue, Task Contract, and generation
source or statement. Violation: ERROR.

### SPV-003 — Required top-level sections

Use Task 042's canonical `REQUIRED_TOP_LEVEL_SECTIONS`:

1. `# Overview`
2. `# Functional Requirements`
3. `# Technical Design`
4. `# Validation`
5. `# Safety`
6. `# Completion`
7. `# Handoff`
8. `# Full Task Contract Reference`

Missing or duplicated required top-level sections: ERROR.

### SPV-004 — Required subsections

Validate the canonical Task 042 subsections:

- Overview: Goal, Scope, Background, Roadmap Context
- Functional Requirements: Required Behaviour, Acceptance Criteria, Out of Scope
- Technical Design: Architecture, Files Expected to Change, Dependencies, Constraints,
  Migration Notes
- Validation: Required Tests and Quality Gates, Validation Commands, Success Criteria
- Safety: Things the Implementation Agent Must NOT Do
- Completion: Expected Deliverables, Result Document, PR Expectations
- Handoff: Required Handoff Procedure, Token-Limit Behaviour

Missing required subsection: ERROR.

### SPV-005 — Empty section body

Required sections and subsections must contain meaningful content. The Task 042
fallback `Not specified in the Task Contract.` counts as content. Empty body: ERROR.

### SPV-006 — Acceptance Criteria

Acceptance Criteria must contain at least one checkpoint heading, Markdown checklist,
or non-empty numbered/bulleted criterion. Duplicate checkpoint identifiers: ERROR.

### SPV-007 — Validation commands

Validation Commands must contain at least one executable command or explicitly state
that none is specified in the Contract. Empty content: ERROR.

### SPV-008 — Safety boundary

The Agent safety subsection must contain explicit restrictions or the canonical
not-specified fallback. Missing or empty content: ERROR.

### SPV-009 — Contract traceability

Full Task Contract Reference must contain the Contract body, not merely a path or empty
placeholder. Structural absence or path-only content: ERROR. Byte-for-byte external
comparison is optional only when existing Task 042 APIs expose it safely.

### SPV-010 — Heading order

Required top-level sections appearing outside canonical Task 042 order: WARNING.

### SPV-011 — Unknown top-level sections

Additional top-level sections are allowed but reported as WARNING.

### SPV-012 — Unresolved markers

`TODO`, `TBD`, or unresolved template tokens in normative sections: WARNING. The
canonical `Not specified in the Task Contract.` phrase is not an unresolved marker.

### SPV-013 — Deterministic ordering

Issues must be ordered by document position when available, then rule code, then
message.

## CLI

```bash
devbot specification validate --task <N>
devbot specification validate --task <N> --format json
```

Expected exit behaviour:

- `0`: valid document, including warnings-only results;
- `1`: document validation failed;
- other non-zero: usage or operational failure, following existing CLI conventions.

The command must not acquire the daemon lock.

## Recommended Architecture

```text
src/devbot/
├── specification.py
└── specification_validation.py
```

Recommended immutable model:

```python
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

## Quality Gates

### CP-043-1 — Valid Specification

- Canonical Task 042-style Specification passes.
- Result has `passed = true` and no errors.
- Repeated validation returns equivalent results.

### CP-043-2 — File resolution

- Invalid Task numbers are rejected.
- Missing Specification raises a dedicated error.
- Multiple matching files raise a dedicated ambiguity error.
- Unreadable input raises a dedicated load error.

### CP-043-3 — Title

- Malformed, mismatched, or empty titles fail.
- Canonical title passes.

### CP-043-4 — Required structure

- Missing or duplicated top-level sections fail.
- Missing required subsection fails.
- Empty required body fails.
- Canonical fallback counts as content.

### CP-043-5 — Acceptance Criteria

- Checkpoint, checklist, and list criteria pass.
- Empty criteria fail.
- Duplicate checkpoint identifiers fail.

### CP-043-6 — Validation and safety

- Valid commands pass.
- Empty Validation Commands fail.
- Empty Agent safety boundary fails.

### CP-043-7 — Contract traceability

- Non-empty full Contract reference passes.
- Empty and path-only references fail.

### CP-043-8 — Warnings

- Incorrect order, unknown top-level sections, and unresolved markers create warnings.
- Warnings alone do not fail validation or CLI exit status.

### CP-043-9 — CLI

- Human output is covered.
- JSON output is valid and deterministic.
- Valid exits 0, validation failure exits 1, operational errors use a distinct non-zero code.
- Validation is read-only and does not acquire daemon lock.

### CP-043-10 — Regression

- Existing Task 042 generator and CLI tests continue to pass.
- No Agent module is imported or invoked by validation code.

### CP-043-11 — Evidence

Required artifacts:

- `tasks/043-specification-validator.md`
- `specifications/043-specification-validator.md`
- `results/043-specification-validator.md`
- roadmap update
- runbook update

### CP-043-12 — Validation gate

```bash
uv run ruff check .
uv run pytest
```

Both must pass.

## Files Expected to Change

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

## Constraints and Safety

- Task 042 format remains authoritative.
- Validation is deterministic and read-only.
- Missing and ambiguous input fails closed.
- Do not import or invoke Agent modules.
- Do not alter Dispatch, polling, review, or rework behaviour.
- Do not implement Task 044 or Task 045.
- Do not perform code-to-Spec validation.
- Do not rewrite invalid or historical artifacts.
- Do not add timestamps, randomness, or unstable ordering.
- Do not make warnings fatal.
- Do not auto-merge the PR.

## Result Document

`results/043-specification-validator.md`

Record implementation summary, actual files changed, implemented rule codes, command
outcomes, CLI examples, deviations, PR number, and commit evidence.

## Git Rules

- Branch: `task/043-specification-validator`
- Pull Request title: `Task 043: Specification Validator`
- Keep the PR limited to Task 043.
- Final merge remains manual.

## Definition of Done

- All CP-043 checkpoints are implemented and tested.
- `uv run ruff check .` and `uv run pytest` pass.
- Human and JSON reports are deterministic.
- Validation is read-only and invokes no Agent.
- Required documentation and evidence artifacts exist.
- PR is ready for architectural review.
- Final merge remains manual.
