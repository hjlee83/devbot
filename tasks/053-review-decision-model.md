# Task 053 — Review Decision Model

## Provenance

- GitHub Issue: #111
- Branch: `task/053-review-decision-model`
- Specification: `specifications/053-review-decision-model.md`

## Goal

Define a provider-neutral, deterministic and typed model for review findings and final review decisions so later GitHub review submission, rework, and merge automation consume one authoritative result.

## Contract Metadata

- Specification Type: feature
- Release Impact: feature
- Risk Level: medium
- Migration Required: false
- Backward Compatible: true

## Contract Version

1

## Scope

- Define review decisions `approved`, `changes_required`, and `comment_only`.
- Define typed finding severities, machine-readable codes, messages, and optional locations.
- Add immutable review finding and report models.
- Derive final decisions deterministically from findings.
- Fail closed on invalid or contradictory data.
- Add deterministic JSON serialization and human-readable rendering.
- Add a read-only CLI boundary for parsing, validating, and rendering reports.
- Add tests and Result documentation.

## Out of Scope

- GitHub Review API writes.
- GitHub App authentication.
- Pull Request approval or request-changes submission.
- LLM review execution.
- Review/rework orchestration.
- Merge readiness and auto-merge.
- GitLab or Jira adapters.

## Acceptance Criteria

1. Review decisions and severities are closed typed enums.
2. A blocker produces `changes_required`.
3. With no blocker, a warning or comment produces `comment_only`.
4. With no findings, the result is `approved`.
5. Counts are derived from findings and cannot drift from them.
6. Findings preserve stable code, message, and optional location.
7. Serialization and rendering are deterministic.
8. Malformed or contradictory input fails closed.
9. CLI behavior is entirely read-only.
10. Specification validation, Ruff, and the complete test suite pass.

## Deliverables

- Review decision domain module.
- JSON serialization and deterministic renderer.
- Read-only CLI integration.
- Unit and CLI tests.
- `results/053-review-decision-model.md`.
- Roadmap/ADR updates where required.
