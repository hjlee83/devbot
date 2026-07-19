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

## Background

DevBot's review-loop and merge-automation machinery does not yet have one typed, deterministic representation of "what did the review conclude, and why." Without it, each future consumer - GitHub Review API submission, rework triggering, merge readiness - would otherwise have to independently interpret raw findings, risking drift between what was found and what decision gets acted on. This Task defines that single, provider-neutral boundary: a review's outcome is always derived from its findings, never supplied independently.

## Roadmap Context

This Task precedes and is a prerequisite for later GitHub Review API submission, review/rework loop automation, and merge readiness/auto-merge work. It defines only the decision model those later tasks will consume - it does not implement any of those integrations itself (see Technical Design/Constraints for the explicit provider-mapping boundary this Task must not cross).

# Functional Requirements

## Required Behaviour

**Typed domain.** Add closed `StrEnum` values for `ReviewDecision` (`approved`/`changes_required`/`comment_only`) and `ReviewSeverity` (`blocker`/`warning`/`comment`). Add immutable models for `ReviewLocation` (optional repository-relative path, line, start line, side, and symbol), `ReviewFinding` (severity, stable non-empty code, non-empty message, optional location), and `ReviewReport` (derived decision, ordered findings, derived severity counts, optional summary and metadata). Exact names may follow repository conventions, but GitHub-specific types or event names must not appear in the core decision model.

**Decision policy.** The final decision is derived only from findings: one or more blockers produces `changes_required`; no blocker with one or more warnings/comments produces `comment_only`; no findings produces `approved`. Callers must not supply an arbitrary decision that can contradict the findings - counts are always derived, never independently trusted.

**Validation.** Fail closed on: unknown severity or decision values; empty/whitespace-only finding codes or messages; invalid line numbers or ranges; invalid repository-relative paths; duplicate findings with the same stable identity when duplicates would make reporting ambiguous; serialized reports whose declared decision/counts contradict derivation, if declared fields are accepted at the input boundary. Validation errors must be typed and explain the invalid field.

**Determinism.** Preserve a deterministic finding order - prefer severity rank (`blocker`, `warning`, `comment`), then code, location, and message, unless existing repository conventions require stable input order. JSON output must have a stable schema and deterministic ordering. Text rendering must always include the decision, counts, and each finding.

**Public API.** Add a dedicated module, preferably `src/devbot/review_decision.py`, exposing APIs equivalent to `build_review_report(findings, *, summary=None, metadata=None)`, `validate_review_report_payload(payload)`, `review_report_to_dict(report)`, `review_report_from_dict(payload)`, and `render_review_report(report)`. Avoid duplicating the same policy in the CLI.

**CLI.** Add a fully read-only CLI boundary following repository conventions. An acceptable shape is `devbot review report --input report.json [--format text|json]`. The CLI may validate and render an existing payload; it does not inspect a PR, invoke an AI, or write to GitHub. Valid reports exit 0. Invalid reports exit non-zero with a concise typed error.

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

## Out of Scope

- Calling GitHub Review APIs.
- GitHub App installation/authentication.
- Producing findings with an LLM.
- Automatically requesting implementation fixes.
- Merge readiness, branch protection, and auto-merge.
- Supporting GitLab or Jira in this Task.

# Technical Design

## Architecture

### Typed Domain

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

- `ReviewLocation`: optional repository-relative path, line, start line, side, and symbol.
- `ReviewFinding`: severity, stable non-empty code, non-empty message, optional location.
- `ReviewReport`: derived decision, ordered findings, derived severity counts, optional summary and metadata.

### Public API

```python
build_review_report(findings, *, summary=None, metadata=None)
validate_review_report_payload(payload)
review_report_to_dict(report)
review_report_from_dict(payload)
render_review_report(report)
```

### CLI

```bash
devbot review report --input report.json [--format text|json]
```

### Provider Boundary

This Task is GitHub-first in product scope but provider-neutral at the decision-model layer. Later GitHub submission maps `approved` → GitHub `APPROVE`, `changes_required` → GitHub `REQUEST_CHANGES`, `comment_only` → GitHub `COMMENT`. Those mappings must not be implemented here.

## Files Expected to Change

- `src/devbot/review_decision.py`
- `src/devbot/main.py`
- `tests/test_review_decision.py`
- `tests/test_main.py`
- `results/053-review-decision-model.md`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` only when an architectural decision is required

## Dependencies

None. This Task defines a new, self-contained domain module with no dependency on existing release/publish/contract-metadata modules.

## Constraints

- GitHub-specific types or event names must not appear in the core decision model.
- The GitHub-submission provider mappings (`approved`→`APPROVE`, `changes_required`→`REQUEST_CHANGES`, `comment_only`→`COMMENT`) must not be implemented in this Task.
- The CLI must not duplicate the decision/validation policy that lives in the domain module.
- Counts must always be derived from findings, never accepted as an independently trusted input.

## Migration Notes

No configuration migration is required. This is new, additive functionality; existing commands remain backward compatible (AC #11).

# Validation

## Required Tests and Quality Gates

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

## Validation Commands

```bash
uv run devbot specification validate --task 53
uv run ruff check .
uv run pytest
```

## Success Criteria

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

# Safety

## Things the Implementation Agent Must NOT Do

- Do not submit any real GitHub review.
- Do not create a GitHub App or token handling.
- Do not call an LLM.
- Do not merge or modify PRs.
- Do not infer findings from code in this Task.
- Do not expand into Task 054 or later autonomous workflow tasks.

# Completion

## Expected Deliverables

- Review decision domain module.
- JSON serialization and deterministic renderer.
- Read-only CLI integration.
- Unit and CLI tests.
- `results/053-review-decision-model.md`.
- Roadmap/ADR updates where required.

## Result Document

Create `results/053-review-decision-model.md` describing the model, derivation policy, validation guarantees, serialization schema, CLI, tests, and scope exclusions.

## PR Expectations

- One PR from `task/053-review-decision-model` to `main`.
- Reference and close Issue #111.
- Apply all review fixes to the same branch and PR.

# Handoff

## Required Handoff Procedure

1. Implement only on `task/053-review-decision-model`.
2. Keep Issue #111 as the task identity.
3. Produce `results/053-review-decision-model.md` before requesting review.
4. Open one PR to `main`, closing Issue #111.
5. Apply all review fixes to the same branch and PR.

## Token-Limit Behaviour

When implementation context is constrained, prioritize in this order:

1. Fail-closed validation and deterministic derivation.
2. Immutable typed models.
3. JSON round-trip fidelity.
4. Tests.
5. Documentation.

Do not silently reduce acceptance criteria or expand scope.

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
