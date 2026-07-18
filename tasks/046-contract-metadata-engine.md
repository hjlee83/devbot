# Task 046 — Contract Metadata Engine

## Contract Version

1

## Provenance

- GitHub Issue: #96
- Branch: `task/046-contract-metadata-engine`
- Epic: Contract Platform
- Current Release: `v0.1.1`

## Task Identity

- id: 046
- title: Contract Metadata Engine

## Metadata

- specification_type: feature
- release_impact: internal
- risk_level: medium
- compatibility: backward
- migration: none

## Goal

Implement the runtime Contract Metadata Engine on top of Contract Schema v1 so DevBot can parse, type, and validate Contract metadata deterministically.

## Context

Task 045 established Contract Schema v1 as the authoritative standard. DevBot now needs one shared runtime boundary that interprets Schema v1 Contracts and clearly distinguishes them from legacy Contracts.

## Scope

- Add typed domain models for Contract metadata.
- Parse Schema v1 Contract version, identity, provenance, and metadata.
- Validate required metadata fields and canonical enum values.
- Reject unsupported schema versions and malformed Schema v1 input.
- Classify Contracts without `Contract Version` as legacy.
- Preserve experimental `x-` metadata fields without allowing them to affect core metadata.
- Add unit tests and integration tests.
- Add `results/046-contract-metadata-engine.md`.

## Out of Scope

- Release recommendation or semantic version changes.
- Contract migration or rewriting historical Contracts.
- CLI commands unless strictly required by an existing repository convention.
- Review Loop, Workflow Engine, GitHub automation, or agent dispatch changes.
- Policy decisions based on risk, compatibility, migration, or release impact.

## Deliverables

- Contract metadata domain model and canonical enums.
- Schema v1 parser and validation errors.
- Explicit legacy classification result.
- Tests covering valid, invalid, unsupported, extension, and legacy cases.
- Result document with validation evidence.

## Acceptance Criteria

1. A valid Schema v1 Contract parses into typed metadata deterministically.
2. All five required metadata fields use typed canonical values.
3. Unsupported `contract_version` values fail closed.
4. Missing or duplicate required Schema v1 sections fail closed.
5. Missing, duplicate, unknown non-extension, or invalid metadata values fail closed.
6. Contracts without `Contract Version` are explicitly classified as legacy rather than Schema v1.
7. Experimental `x-` metadata fields are preserved separately and do not alter core metadata.
8. Task identity consistency is validated.
9. Existing legacy Contracts continue to work through explicit legacy classification without forced migration.
10. No release, review, workflow, or dispatch policy is implemented.
11. Specification validation, lint, and the full test suite pass.

## Quality Gates

- `uv run devbot specification validate --task 46`
- `uv run ruff check .`
- `uv run pytest`
- New tests include valid Schema v1, invalid enum, missing field, duplicate field, unsupported version, legacy Contract, and `x-` extension cases.
- Existing Task 042–045 behavior remains compatible.

## Handoff

Use `specifications/046-contract-metadata-engine.md` and `specifications/045-contract-schema.md` as authoritative sources. Work only on `task/046-contract-metadata-engine`, create the Result document, run all quality gates, and open a PR targeting `main`.

## References

- Issue #96
- `specifications/045-contract-schema.md`
- `docs/09-task-contract-standard.md`
