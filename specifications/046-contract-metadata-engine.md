# Specification: Task 046 — Contract Metadata Engine

## Provenance

- Task Issue: #96
- Task Contract: `tasks/046-contract-metadata-engine.md`
- Parent Standard: `specifications/045-contract-schema.md`
- Epic: Contract Platform
- Current Release: `v0.1.1`

# Overview

## Goal

Implement a single runtime boundary that parses Contract Schema v1 into typed metadata, validates schema invariants, and explicitly distinguishes native Schema v1 Contracts from legacy Contracts.

## Scope

In scope:

- Typed enums for the five canonical metadata fields.
- Immutable Contract metadata and parse-result models.
- Schema v1 parsing and validation.
- Legacy classification for unversioned Contracts.
- Preservation of `x-` extension metadata.
- Deterministic errors and tests.

Out of scope:

- Release recommendation or version mutation.
- Review, workflow, dispatch, merge, or release automation.
- Historical Contract migration.
- Policy behavior based on parsed metadata.

## Background

Task 045 fixed Contract Schema v1. Until this task, consumers can still interpret Contract Markdown independently. Task 046 must centralize that interpretation so future policy and workflow components depend on one typed representation rather than re-parsing documents.

# Functional Requirements

## Required Behaviour

The engine must expose a public operation equivalent to:

```python
parse_contract_metadata(text: str) -> ContractParseResult
```

A native Schema v1 result must include:

- `contract_version`
- task identity
- provenance fields required by Schema v1
- typed core metadata
- preserved `x-` extensions
- explicit classification as native Schema v1

An unversioned Contract must return an explicit legacy classification. It must not be silently treated as Schema v1.

## Acceptance Criteria

1. Valid Schema v1 input produces deterministic typed output.
2. The five metadata fields map to closed enums defined by Task 045.
3. Unsupported versions fail closed.
4. Missing or duplicate required sections fail closed.
5. Required metadata is present exactly once.
6. Unknown fields fail unless prefixed `x-`.
7. `x-` fields are preserved separately and cannot override core fields.
8. Heading task identity agrees with the Task Identity section.
9. Legacy Contracts are explicitly represented without forced migration.
10. No release or workflow policy is added.
11. Result documentation and all quality gates pass.

## Out of Scope

- Recommendation models such as major/minor/patch.
- Automatic migration or canonical rewriting.
- New agent roles or model-specific behavior.
- GitHub write operations.

# Technical Design

## Architecture

Recommended module boundary:

```text
Contract Markdown
      |
      v
Contract Metadata Parser
      |
      +-- native Schema v1 -> ContractMetadata
      +-- unversioned       -> LegacyContract
      +-- invalid           -> ContractMetadataError
```

The parser is the only new component allowed to interpret Schema v1 metadata syntax. Future consumers must use its typed result.

## Domain Model

Use immutable dataclasses or the repository's existing equivalent.

```python
class ContractKind(Enum):
    SCHEMA_V1 = "schema_v1"
    LEGACY = "legacy"

class SpecificationType(Enum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    DOCS = "docs"
    INTERNAL = "internal"
    GENERIC = "generic"

class ReleaseImpact(Enum):
    BREAKING = "breaking"
    FEATURE = "feature"
    FIX = "fix"
    DOCS = "docs"
    INTERNAL = "internal"
    NONE = "none"

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class Compatibility(Enum):
    BACKWARD = "backward"
    BREAKING = "breaking"

class Migration(Enum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"
```

The exact Python names may follow repository conventions, but canonical serialized values must match Task 045 exactly.

`ContractMetadata` must contain the five typed fields. Extensions must be a separate immutable mapping. The parse result must expose whether the Contract is native Schema v1 or legacy.

## Parsing Rules

- Recognize the exact Schema v1 level-2 section names defined by Task 045.
- `Contract Version` must contain exactly one positive integer; only `1` is supported.
- Singleton required sections must occur exactly once.
- Metadata lines use `- field: value`.
- Core field names and values are lowercase canonical tokens.
- Each core field occurs exactly once.
- Unknown non-extension fields are invalid.
- `x-` fields may occur once each and are preserved as string values.
- An `x-` field cannot shadow or replace a core field.
- The title task ID and Task Identity `id` must agree.
- A Contract with no `Contract Version` is legacy. Once a version section exists, malformed or unsupported content is an error, not legacy fallback.

## Error Model

Use stable machine-readable error codes or typed exceptions for at least:

- unsupported version
- malformed version
- missing section
- duplicate section
- missing metadata field
- duplicate metadata field
- unknown metadata field
- invalid metadata value
- task identity mismatch

Errors should identify the relevant section or field without depending on brittle full-message assertions.

## Files Expected to Change

Likely files include:

- a new module under `src/devbot/` for contract metadata models and parsing
- package exports where appropriate
- focused tests under `tests/`
- `results/046-contract-metadata-engine.md`
- `docs/00-roadmap.md`

Avoid unrelated refactors.

## Compatibility

Historical Contracts without `Contract Version` remain legacy. Existing specification generation, validation, and template behavior must not break. This task does not require migrating Tasks 001–045.

# Validation

## Required Tests

At minimum:

- valid Schema v1 Contract
- every canonical enum family
- unsupported version
- malformed version
- missing required section
- duplicate singleton section
- missing metadata field
- duplicate metadata field
- unknown metadata field
- invalid metadata value
- valid `x-` extension preservation
- attempted extension/core collision
- task identity mismatch
- representative legacy Contracts
- deterministic output and error codes

## Validation Commands

```bash
uv run devbot specification validate --task 46
uv run ruff check .
uv run pytest
```

## Success Criteria

All Acceptance Criteria pass, the full suite remains green, and no policy consumer is implemented.

# Safety

## Things the Implementation Agent Must NOT Do

- Do not implement release recommendation or version bumping.
- Do not add Review Loop or Workflow Engine behavior.
- Do not modify historical Contracts in bulk.
- Do not add model-specific names such as Claude or Codex to the domain model.
- Do not let multiple consumers independently parse Schema v1 metadata.
- Do not silently downgrade malformed versioned Contracts to legacy.

# Completion

## Definition of Done

- Typed models and parser are implemented.
- Native, legacy, and invalid paths are tested.
- Task 045 canonical values are followed exactly.
- Result document records files, decisions, tests, and exclusions.
- Specification validation, lint, and full tests pass.
- A PR targets `main` from `task/046-contract-metadata-engine`.

# Handoff

## Implementation Instruction

Implement Issue #96 using this Specification and Task 045's Contract Schema v1 as the authoritative sources. Keep all work on the existing branch, write `results/046-contract-metadata-engine.md`, run the quality gates, and open a PR. The implementation agent is a role; no model-specific behavior belongs in the code or documents.

# Full Task Contract Reference

The authoritative Task Contract is `tasks/046-contract-metadata-engine.md`.
