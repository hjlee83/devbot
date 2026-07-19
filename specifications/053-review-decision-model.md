# Specification: Task 053 — Review Decision Model

## Provenance

- GitHub Issue: #111
- Branch: `task/053-review-decision-model`
- Task Contract: `tasks/053-review-decision-model.md`
- Generation Source: Architect-authored Specification for Task 053.

# Overview

## Goal

Add a provider-neutral domain boundary that converts typed review findings into one deterministic review decision. This is the authoritative input for later GitHub review submission, rework loops, and merge automation.

## Scope

- Typed decisions: `approved`, `changes_required`, `comment_only`.
- Typed severities: `blocker`, `warning`, `comment`.
- Immutable review finding, location, summary, and report models.
- Deterministic decision derivation and counts.
- Validation, JSON serialization/deserialization, and text rendering.
- Read-only CLI integration.
- Tests and Result documentation.

## Out of Scope

- Calling GitHub Review APIs.
- GitHub App installation/authentication.
- Producing findings with an LLM.
- Automatically requesting implementation fixes.
- Merge readiness, branch protection, and auto-merge.
- Supporting GitLab or Jira in this Task.

# Functional Requirements

## Typed Domain

Define closed enums equivalent to:

```python
class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUIRED = "changes_required"
    COMMENT_ONLY = "comment_only"

class ReviewSeverity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    COMMENT = "comment"
```

Add immutable models equivalent to:

- `ReviewLocation`: optional repository-relative path, line, start line, side, and symbol.
- `ReviewFinding`: severity, stable non-empty code, non-empty message, optional location.
- `ReviewReport`: derived decision, ordered findings, derived severity counts, optional summary and metadata.

Exact names may follow repository conventions, but GitHub-specific types or event names must not appear in the core decision model.

## Decision Policy

The final decision is derived only from the findings:

1. One or more blockers → `changes_required`.
2. No blocker and one or more warnings/comments → `comment_only`.
3. No findings → `approved`.

Callers must not supply an arbitrary decision that can contradict the findings. Counts are always derived, never independently trusted.

## Validation

Fail closed on:

- unknown severity or decision values;
- empty/whitespace-only finding codes or messages;
- invalid line numbers or ranges;
- invalid repository-relative paths;
- duplicate findings with the same stable identity when duplicates would make reporting ambiguous;
- serialized reports whose declared decision/counts contradict derivation, if declared fields are accepted at the input boundary.

Validation errors must be typed and explain the invalid field.

## Determinism

- Preserve a deterministic finding order. Prefer severity rank (`blocker`, `warning`, `comment`), then code, location, and message, unless existing repository conventions require stable input order.
- JSON output must have a stable schema and deterministic ordering.
- Text rendering must always include the decision, counts, and each finding.

## Public API

Add a dedicated module, preferably `src/devbot/review_decision.py`, exposing APIs equivalent to:

```python
build_review_report(findings, *, summary=None, metadata=None)
validate_review_report_payload(payload)
review_report_to_dict(report)
review_report_from_dict(payload)
render_review_report(report)
```

Avoid duplicating the same policy in the CLI.

## CLI

Add a fully read-only CLI boundary following repository conventions. An acceptable shape is:

```bash
devbot review report --input report.json [--format text|json]
```

The CLI may validate and render an existing payload; it does not inspect a PR, invoke an AI, or write to GitHub. Valid reports exit 0. Invalid reports exit non-zero with a concise typed error.

# Acceptance Criteria

1. Closed typed review decision and severity models exist.
2. Final decision is deterministically derived from findings.
3. Blockers always produce `changes_required`.
4. Warnings/comments without blockers produce `comment_only`.
5. Empty findings produce `approved`.
6. Severity counts cannot drift from findings.
7. Finding codes/messages/locations are validated.
8. JSON round-trip preserves the complete report.
9. Rendering is deterministic and explainable.
10. CLI performs no filesystem mutation other than reading its explicit input file and performs no network or GitHub write.
11. Existing commands remain backward compatible.
12. Specification validation, Ruff, and the complete test suite pass.

# Technical Design

## Expected Files

- `src/devbot/review_decision.py`
- `src/devbot/main.py`
- `tests/test_review_decision.py`
- `tests/test_main.py`
- `results/053-review-decision-model.md`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` only when an architectural decision is required

## Provider Boundary

This Task is GitHub-first in product scope but provider-neutral at the decision-model layer. Later GitHub submission maps:

- `approved` → GitHub `APPROVE`
- `changes_required` → GitHub `REQUEST_CHANGES`
- `comment_only` → GitHub `COMMENT`

Those mappings must not be implemented here.

# Validation

Tests must cover:

- empty findings;
- each severity individually;
- mixed findings and blocker precedence;
- deterministic ordering;
- derived counts;
- valid and invalid locations;
- empty codes/messages;
- duplicate/ambiguous findings;
- JSON round-trip;
- malformed/contradictory serialized payloads;
- deterministic text output;
- CLI output and exit codes;
- proof that no GitHub client/write client is created.

Validation commands:

```bash
uv run devbot specification validate --task 53
uv run ruff check .
uv run pytest
```

# Safety

- Do not submit any real GitHub review.
- Do not create a GitHub App or token handling.
- Do not call an LLM.
- Do not merge or modify PRs.
- Do not infer findings from code in this Task.
- Do not expand into Task 054 or later autonomous workflow tasks.

# Completion

Create `results/053-review-decision-model.md` describing the model, derivation policy, validation guarantees, serialization schema, CLI, tests, and scope exclusions. Open one PR from `task/053-review-decision-model` to `main`, reference and close Issue #111, and apply all review fixes to the same branch and PR.

# Full Task Contract Reference

```markdown
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
```
