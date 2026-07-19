# Task 050 Specification: Release Publish Strategy

## Provenance

- GitHub Issue: #105
- Branch: `task/050-release-publish-strategy`
- Generated from: `tasks/050-release-publish-strategy.md`

# Overview

## Problem Statement

DevBot has two independently valid release publication paths. Task 037 publishes by dispatching the release workflow, while Task 049 publishes directly by pushing a Git tag and creating a GitHub Release. Without one repository-level policy selecting the authoritative path, future automation could choose ambiguously or expose both paths as simultaneously valid.

## Goal

Introduce a deterministic, fail-closed release publish strategy policy that resolves exactly one effective strategy per repository: `workflow` or `direct`.

## Roadmap Context

Task 050 is the policy layer between completed release components and future release orchestration:

```text
Task 047: Release Recommendation
Task 048: Release Preparation
Task 049: Direct Release Publish
Task 050: Publish Strategy Selection
Task 051+: Release Orchestration
```

## Dependencies

- Task 037 workflow-dispatch release path.
- Task 049 direct publish path.
- Existing repository configuration model and loader.
- Existing CLI release command routing.

# Functional Requirements

## FR-001 — Canonical Strategy Domain

Provide a typed strategy domain with exactly these canonical values:

- `workflow`
- `direct`

No aliases, case folding, implicit synonyms, or guessed values are allowed.

## FR-002 — Repository Configuration

Add one optional repository-level configuration field representing the release publish strategy. The implementation must use the existing repository configuration loading boundary rather than reading configuration independently inside release modules.

## FR-003 — Safe Default

When the field is omitted, the effective strategy must be `workflow`. This preserves all existing installations and keeps the historically safer CI-based path authoritative by default.

## FR-004 — Fail-Closed Parsing

Unknown, malformed, duplicated, or structurally ambiguous strategy configuration must fail before any publish-side write operation. The error must identify the repository and invalid evidence without silently falling back.

## FR-005 — Central Resolver

Implement one central resolver or policy function that receives the repository configuration and returns the effective typed strategy. Existing publish entry points must consume this result; they must not duplicate parsing or defaulting logic.

## FR-006 — Workflow Command Guard

The existing workflow-based publish entry point must proceed only when the effective strategy is `workflow`. When the effective strategy is `direct`, it must return a dedicated strategy mismatch failure before workflow dispatch or any other GitHub write.

## FR-007 — Direct Command Guard

The existing direct publish entry point must proceed only when the effective strategy is `direct`. When the effective strategy is `workflow`, it must return a dedicated strategy mismatch failure before tag creation, tag push, or GitHub Release creation.

## FR-008 — Mutual Exclusivity

For any valid repository configuration, exactly one of the two publish entry points may pass its strategy gate. Tests must prove that no valid configuration allows both entry points to proceed.

## FR-009 — Read-Only Inspection

Add a read-only inspection command or equivalent existing CLI extension that reports:

- repository
- configured strategy evidence (`omitted`, `workflow`, or `direct`)
- effective strategy
- whether defaulting occurred

Inspection must not instantiate or call a write client.

## FR-010 — Dry-Run Preservation

Existing dry-run behavior remains read-only. Strategy validation must still occur so dry-run accurately reports whether the selected command is permitted.

## FR-011 — Backward Compatibility

Repository configuration without the new field must continue to load and must resolve to `workflow`. Existing non-release behavior must remain unchanged.

# Non-Functional Requirements

## Determinism

The same repository configuration must always resolve to the same effective strategy.

## Safety

Strategy mismatch and invalid configuration must be detected before any Git, GitHub Release, or workflow-dispatch write.

## Separation of Responsibilities

Task 050 decides which publisher is authoritative. It must not calculate versions, prepare files, generate notes, or perform publication itself.

## Observability

Errors and inspection output must identify the configured and expected strategy clearly enough for an operator to correct configuration without reading source code.

# Interfaces and Data Model

## Strategy Type

Recommended shape:

```python
class ReleasePublishStrategy(StrEnum):
    WORKFLOW = "workflow"
    DIRECT = "direct"
```

## Resolution Result

A typed immutable result should distinguish configured evidence from effective policy, for example:

```python
@dataclass(frozen=True)
class ReleasePublishStrategyResolution:
    repository: str
    configured: ReleasePublishStrategy | None
    effective: ReleasePublishStrategy
    defaulted: bool
```

Equivalent naming is acceptable if the semantics remain explicit.

## Errors

Provide dedicated errors beneath one strategy-policy base error, including at minimum:

- invalid strategy configuration
- strategy mismatch for a requested publish path

Do not reuse generic `ValueError` at the CLI boundary.

# Command Behaviour

## Strategy Inspection

A recommended command shape is:

```bash
devbot release strategy [--repo owner/name]
```

Equivalent placement is acceptable if it remains read-only and unambiguous.

## Existing Workflow Publish

```bash
devbot release publish ...
```

- Effective `workflow`: retain existing behavior.
- Effective `direct`: refuse before dispatch.

## Existing Direct Publish

```bash
devbot release publish-prepared ...
```

- Effective `direct`: retain existing behavior.
- Effective `workflow`: refuse before any tag or Release write.

# Validation and Failure Rules

1. Omitted configuration resolves to `workflow`.
2. Explicit `workflow` resolves to `workflow` without defaulting.
3. Explicit `direct` resolves to `direct` without defaulting.
4. Any other value fails closed.
5. Strategy inspection is always read-only.
6. A mismatch failure occurs before constructing or invoking write-side effects where practical; tests must at least prove no write method is called.
7. No fallback from invalid explicit configuration to `workflow` is permitted.

# Required Tests and Quality Gates

## Unit Tests

- Omitted field defaults to `workflow`.
- Explicit `workflow` resolution.
- Explicit `direct` resolution.
- Unknown value rejection.
- Malformed configuration rejection.
- Resolution result records whether defaulting occurred.
- Workflow guard accepts only `workflow`.
- Direct guard accepts only `direct`.
- Mutual exclusivity matrix proves exactly one accepted path for each valid strategy.

## Integration/CLI Tests

- Strategy inspection output for omitted configuration.
- Strategy inspection output for explicit `workflow`.
- Strategy inspection output for explicit `direct`.
- Workflow command with `direct` strategy performs no workflow dispatch.
- Direct command with `workflow` strategy performs no Git tag, push, or Release creation.
- Dry-run still enforces mismatch policy without writes.
- Existing configuration files without the field continue loading.

## Validation Commands

```bash
uv run devbot specification validate --task 50
uv run ruff check .
uv run pytest
```

# Safety Boundary

Task 050 must not:

- change project versions;
- edit `pyproject.toml` or `uv.lock`;
- create or move Git tags;
- create GitHub Releases;
- dispatch a real release workflow;
- publish packages;
- combine release stages into one orchestration command;
- remove either existing publisher.

Tests must use mocks or isolated fixtures and must never touch the real repository's release state.

# Migration Notes

No migration is required. Existing repositories omit the new field and therefore resolve to `workflow`. Operators may opt into `direct` explicitly after understanding the Task 049 path.

# Expected Deliverables

- Strategy domain and resolution policy.
- Repository configuration support.
- Guards integrated into both existing publish entry points.
- Read-only strategy inspection.
- Complete tests.
- Roadmap and ADR updates.
- `results/050-release-publish-strategy.md`.

# PR Expectations

- One PR from `task/050-release-publish-strategy` to `main`.
- Link and close Issue #105.
- Keep review rework on the same branch and PR.
- Explicitly document how both existing paths are preserved while only one is enabled per repository.
- Report exact validation commands and results.
- Do not include Task 051 orchestration work.

# Result Document

Create `results/050-release-publish-strategy.md` containing:

- implementation summary;
- selected configuration shape;
- defaulting rationale;
- mutual-exclusion evidence;
- failure behavior;
- tests and command results;
- scope exclusions;
- any architecture decision updates.

# Required Handoff Procedure

1. Implement only from Issue #105, this Specification, and the embedded Contract.
2. Stay on `task/050-release-publish-strategy`.
3. Run all quality gates.
4. Create the Result document.
5. Open one PR to `main`.
6. Request review and apply all fixes to the same PR.

# Token-Limit Behaviour

When context is constrained, preserve this priority order:

1. Mutual exclusivity and fail-closed strategy enforcement.
2. Backward-compatible `workflow` default.
3. No-write guarantees on mismatch and inspection.
4. Required tests.
5. Documentation.

Do not omit safety tests to save context.

# Full Task Contract Reference

```markdown
# Task 050: Release Publish Strategy

## Contract Version

- contract_version: 1

## Provenance

- GitHub Issue: #105
- Branch: `task/050-release-publish-strategy`

## Task Identity

- id: 050
- title: Release Publish Strategy

## Metadata

- specification_type: feature
- release_impact: internal
- risk_level: high
- compatibility: backward
- migration: none

## Goal

Define one authoritative release-publish strategy per repository so DevBot never ambiguously chooses between the workflow-dispatch path introduced by Task 037 and the direct tag/GitHub Release path introduced by Task 049.

## Context

DevBot currently exposes two valid release publication mechanisms:

1. `devbot release publish`, which dispatches `.github/workflows/release.yml`.
2. `devbot release publish-prepared`, which creates and pushes a Git tag and then creates a GitHub Release directly.

Both mechanisms are individually guarded, but there is no single repository policy selecting which mechanism is authoritative. Task 050 introduces that policy boundary before any future orchestration combines classification, preparation, and publication.

## Scope

- Define a typed `ReleasePublishStrategy` with canonical values `workflow` and `direct`.
- Add one optional repository-level publish strategy configuration field.
- Resolve omitted configuration to `workflow` for backward-compatible safety.
- Reject unknown, malformed, duplicated, or ambiguous strategy configuration.
- Centralize strategy resolution in a dedicated policy component.
- Add a read-only inspection API and CLI output showing configured and effective strategy.
- Gate the existing workflow publish command so it refuses repositories resolved to `direct`.
- Gate the existing direct publish command so it refuses repositories resolved to `workflow`.
- Keep dry-run operations read-only.
- Add regression tests proving only one publish path is valid for a repository at a time.
- Update architecture decisions, roadmap, and Result documentation.

## Out of Scope

- Release recommendation calculation.
- Version calculation or mutation.
- Release preparation.
- Creating or pushing tags as part of Task 050 tests or implementation flow.
- Creating GitHub Releases as part of Task 050 tests or implementation flow.
- Dispatching a real GitHub Actions release workflow.
- Combining Tasks 047–050 into one command.
- Release-note generation or aggregation.
- Package publication.
- Automatic PR review, approval, merge, or agent dispatch.
- Removing either existing publish implementation.

## Deliverables

- Typed publish-strategy domain model.
- Repository configuration parsing and safe defaulting.
- Strategy resolver/policy boundary.
- Strategy mismatch error taxonomy.
- Read-only CLI strategy inspection.
- Guards on both existing publish entry points.
- Unit and CLI regression tests.
- `results/050-release-publish-strategy.md`.
- Roadmap and ADR updates.

## Acceptance Criteria

1. A repository with no explicit publish strategy resolves to `workflow`.
2. A repository configured with `workflow` resolves only to the workflow path.
3. A repository configured with `direct` resolves only to the direct path.
4. Unknown or malformed strategy values fail closed before any GitHub or Git write.
5. `devbot release publish` refuses a repository whose effective strategy is `direct`.
6. `devbot release publish-prepared` refuses a repository whose effective strategy is `workflow`.
7. Dry-run and strategy inspection perform no write operations.
8. Existing repositories without the new field remain backward compatible.
9. Tests prove the two write paths cannot both be accepted for the same repository configuration.
10. Task 050 does not calculate or mutate versions and does not publish a real release.

## Quality Gates

- `uv run devbot specification validate --task 50`
- `uv run ruff check .`
- `uv run pytest`
- Dedicated tests for omitted, `workflow`, `direct`, invalid, and ambiguous configuration.
- Dedicated tests proving each command refuses the non-selected strategy before any write client operation.
- Static or behavioral evidence that Task 050 does not create tags, Releases, or workflow dispatches during tests.

## Handoff

- Continue all implementation and rework on `task/050-release-publish-strategy`.
- Use Issue #105 as the only Task Issue.
- Open one PR to `main` and keep review fixes on the same PR.
- Create `results/050-release-publish-strategy.md` before requesting review.
- Do not begin release orchestration or Task 051 scope in this branch.

## References

- `src/devbot/release_ops.py`
- `src/devbot/release_publish.py`
- `src/devbot/models.py`
- `docs/07-decisions.md`
- `tasks/049-release-publish.md`
```
