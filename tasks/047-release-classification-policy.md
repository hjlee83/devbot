# Task 047 — Release Classification Policy

## Contract Version

1

## Provenance

- GitHub Issue: #98
- Branch: `task/047-release-classification-policy`
- Epic: Contract Platform
- Current Release: `v0.1.1`

## Task Identity

- id: 047
- title: Release Classification Policy

## Metadata

- specification_type: feature
- release_impact: internal
- risk_level: medium
- compatibility: backward
- migration: none

## Goal

Implement a deterministic policy that converts typed Contract Metadata into a release recommendation of `major`, `minor`, `patch`, or `none`.

## Context

Task 045 established Contract Schema v1 and Task 046 implemented the single runtime metadata parser. DevBot can now make policy decisions without re-reading Markdown. The first consumer is release classification.

## Scope

- Add a typed release recommendation enum or equivalent immutable model.
- Classify native Schema v1 parse results using typed metadata only.
- Apply breaking overrides before ordinary `release_impact` mapping.
- Handle legacy Contract input explicitly and safely.
- Keep classification pure, deterministic, and side-effect free.
- Add focused tests and `results/047-release-classification-policy.md`.

## Out of Scope

- Mutating version files.
- Creating Git tags or GitHub Releases.
- Generating release notes.
- Combining multiple Tasks or PRs into one release decision.
- Review Loop, Workflow Engine, merge automation, or agent dispatch changes.
- CLI commands unless strictly required by an existing repository convention.

## Deliverables

- Release classification policy module.
- Typed recommendation and stable policy errors.
- Tests for every canonical metadata combination required by this Contract.
- Result document with validation evidence.

## Acceptance Criteria

1. A native Schema v1 `ContractParseResult` can be classified without re-parsing Markdown.
2. `compatibility=breaking` always recommends `major`.
3. `migration=required` always recommends `major`.
4. Otherwise `release_impact=breaking` recommends `major`.
5. Otherwise `release_impact=feature` recommends `minor`.
6. Otherwise `release_impact=fix` recommends `patch`.
7. Otherwise `release_impact=docs`, `internal`, or `none` recommends `none`.
8. Legacy input fails with a dedicated policy error rather than guessing.
9. Missing metadata on a native result fails closed.
10. The policy is deterministic and introduces no filesystem, network, GitHub, or version mutation side effects.
11. Specification validation, lint, and the full test suite pass.

## Quality Gates

- `uv run devbot specification validate --task 47`
- `uv run ruff check .`
- `uv run pytest`
- Tests cover all release-impact values and both breaking overrides.
- Existing Task 042–046 behavior remains compatible.

## Handoff

Use `specifications/047-release-classification-policy.md` as the implementation authority. Work only on `task/047-release-classification-policy`, create the Result document, run all quality gates, and open a PR targeting `main`.

## References

- Issue #98
- `specifications/045-contract-schema.md`
- `src/devbot/contract_metadata.py`
