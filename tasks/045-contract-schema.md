# Task 045 — Contract Schema

## Status

READY

## Provenance

- GitHub Issue: #94
- Branch: `task/045-contract-schema`
- Epic: Contract Platform
- Current Release: `v0.1.1`

## Goal

Define Contract Schema v1 as the authoritative, versioned structure for DevBot task contracts before implementing metadata parsing, policy evaluation, or workflow consumers.

## Context

DevBot already generates and validates Specifications, but Task Contracts do not yet have a formal schema boundary. Several components currently interpret Contract content independently. Task 045 establishes one stable standard so future parsers and policy engines can depend on a single contract definition.

## Scope

- Define Contract Schema v1.
- Define required and optional fields.
- Define canonical enum values.
- Define schema versioning and compatibility rules.
- Define the relationship between task identity, metadata, deliverables, acceptance criteria, quality gates, and handoff instructions.
- Add normative examples and validation expectations.

## Out of Scope

- Contract metadata parser implementation.
- Metadata domain objects or enums in application code.
- Release recommendation logic.
- CLI commands.
- Workflow, review, dispatch, merge, or release automation.
- Migration of all historical contracts unless required to demonstrate compatibility.

## Deliverables

- `tasks/045-contract-schema.md`
- `specifications/045-contract-schema.md`
- Contract Schema v1 definition within the Specification.
- Result document produced by the implementer.

## Required Schema Areas

1. Schema version
2. Task identity
3. Contract metadata
4. Goal and context
5. Scope and out-of-scope boundaries
6. Deliverables
7. Acceptance criteria
8. Quality gates
9. Handoff instructions
10. References and provenance

## Canonical Metadata Values

### Specification Type

- `feature`
- `bugfix`
- `refactor`
- `docs`
- `internal`
- `generic`

### Release Impact

- `breaking`
- `feature`
- `fix`
- `docs`
- `internal`
- `none`

### Risk Level

- `low`
- `medium`
- `high`

### Compatibility

- `backward`
- `breaking`

### Migration

- `none`
- `optional`
- `required`

## Acceptance Criteria

- Contract Schema v1 is explicit and versioned.
- Every field has a documented meaning and cardinality.
- Required and optional fields are clearly separated.
- Canonical enum values are deterministic and lowercase.
- Unknown enum values are defined as invalid.
- Versioning rules distinguish backward-compatible and breaking schema changes.
- The schema can represent existing Task Contracts without losing essential information.
- The Specification is implementation-ready for the next metadata parser task.
- No parser, CLI, workflow, review, or release automation is implemented in this task.

## Quality Gates

- Specification validation passes.
- Documentation is internally consistent.
- Examples conform to the declared schema.
- No scope expansion beyond schema standardization.

## Handoff

Implement this task using `specifications/045-contract-schema.md` as the authoritative source. Produce a Result document and open a pull request targeting `main` from `task/045-contract-schema`.
