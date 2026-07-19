# Specification: Task 054 — GitHub Review Submission

## Provenance

- GitHub Issue: #113
- Branch: `task/054-github-review-submission`
- Task Contract: `tasks/054-github-review-submission.md`
- Generation Source: Architect-authored Specification for Task 054.

# Overview

## Goal

Add a GitHub-specific submission boundary that converts one validated Task 053 ReviewReport into exactly one official Pull Request review without allowing stale evidence, silent finding loss, or unrelated repository writes.

## Scope

- Load ReviewReport JSON through Task 053 parsing and validation APIs.
- Resolve repository and Pull Request.
- Bind the report to the exact PR head commit it reviewed.
- Map the provider-neutral decision to one GitHub review event.
- Render a deterministic body and supported inline comments.
- Provide dry-run and typed execution results.
- Submit through the existing GitHub write-client boundary.
- Add tests and Result documentation.

## Background

Task 053 created the authoritative provider-neutral review result. DevBot still cannot express that result as GitHub's formal APPROVE, REQUEST_CHANGES, or COMMENT review state. A plain issue comment cannot satisfy review requirements or support later merge-readiness automation. This Task adds only that submission boundary.

## Roadmap Context

This Task precedes the review/rework loop, merge-readiness policy, auto-merge, GitHub App installation-token authentication, and autonomous task execution.

# Functional Requirements

## Required Behaviour

**Decision mapping.** Map `ReviewDecision.APPROVED` only to GitHub `APPROVE`, `CHANGES_REQUIRED` only to `REQUEST_CHANGES`, and `COMMENT_ONLY` only to `COMMENT`. Keep this mapping in the GitHub submission module, not in Task 053's provider-neutral model.

**Report identity and stale protection.** A submittable report must identify the repository, Pull Request number, and exact reviewed head SHA in validated metadata or an equivalent typed envelope. Read the current PR before constructing a write client. Reject mismatched repository/PR identity, closed or merged PRs, and any current head SHA different from the report's reviewed SHA. Never submit a review against a newer commit than the evidence examined.

**Review body.** Produce a deterministic body containing the decision, severity counts, summary when present, every finding, and reviewed head SHA. No finding may disappear silently.

**Inline comments.** Convert a finding to an inline comment only when its location can be represented safely for the current GitHub review API and reviewed commit. Findings without a supported diff location must remain visible in the top-level body. Unsupported or contradictory location data must fail closed when conversion would otherwise imply it was submitted inline. Do not guess diff positions.

**Dry-run.** `--dry-run` validates the report and current PR state, produces the exact event/body/comments that real execution would submit, and creates no GitHub write client or write call.

**Submission.** Real execution creates the write client only after all read-side validation succeeds and submits exactly one review request. It must not separately add issue comments, labels, approvals, merges, or other mutations. Return an immutable typed result containing repository, PR number, head SHA, mapped event, dry-run/submitted state, review identifier or URL when available, and inline-comment count.

**Failures.** Use typed errors for invalid report payload, identity mismatch, stale head, unsupported PR state/location, GitHub permission failure, and self-approval rejection. GitHub refusing approval because the authenticated identity authored the PR must be reported explicitly; do not silently downgrade APPROVE to COMMENT.

**CLI.** Add:

```bash
devbot review submit --repo owner/repo --pr N --report report.json [--dry-run]
```

The command exits 0 for a successful dry-run or submission, non-zero for any validation or GitHub failure.

## Acceptance Criteria

1. All three Task 053 decisions map deterministically to their corresponding GitHub review events.
2. Invalid report payloads fail before network writes.
3. Repository, PR, and reviewed-head identity are validated.
4. Stale reports never submit.
5. Dry-run creates no write client and returns the exact intended submission.
6. Real execution performs exactly one GitHub review write.
7. Every finding appears either inline or in the review body.
8. Self-approval and permissions failures remain failures and are not downgraded.
9. Existing review-report CLI remains backward compatible.
10. Specification validation, Ruff, and the complete test suite pass.

## Out of Scope

- Generating ReviewReport findings with an LLM.
- GitHub App registration, installation, private-key handling, or token exchange.
- Automatic rework requests or code changes.
- Merge-readiness and auto-merge.
- Review polling or orchestration.
- GitLab and Jira support.

# Technical Design

## Architecture

### Suggested Models

- `GitHubReviewEvent`: `APPROVE`, `REQUEST_CHANGES`, `COMMENT`.
- `GitHubReviewSubmissionPlan`: repository, PR, reviewed head SHA, event, body, inline comments.
- `GitHubReviewSubmissionResult`: immutable dry-run/submission outcome.
- Typed errors rooted at `GitHubReviewSubmissionError`.

### Suggested API

```python
build_github_review_submission_plan(...)
submit_github_review(..., dry_run=False)
render_github_review_submission_plan(...)
```

Reuse `review_report_from_dict` and Task 053 rendering/typed models. Do not reimplement decision derivation or report validation.

### Write Boundary

Add the minimum GitHub write-client method required for `POST /repos/{owner}/{repo}/pulls/{pull_number}/reviews` if it does not already exist. The service owns event mapping and payload construction; the client only transports validated payloads.

### Authentication Boundary

Use the existing write-client construction path for now. Do not encode assumptions that require a personal user token. Keep submission code compatible with a later GitHub App installation token. Authentication provisioning is a later Task.

## Files Expected to Change

- `src/devbot/github_review_submission.py`
- `src/devbot/github_write_client.py` only when endpoint support is absent
- `src/devbot/github_client.py` only for missing read-only PR head/state data
- `src/devbot/main.py`
- `tests/test_github_review_submission.py`
- relevant GitHub client and CLI tests
- `results/054-github-review-submission.md`
- `docs/00-roadmap.md`
- `docs/07-decisions.md` only when an ADR is required

## Constraints

- Task 053 remains provider-neutral.
- The current PR head SHA must be read and compared before write-client construction.
- Never submit against a branch name alone; use the exact commit SHA.
- Never downgrade or change the report decision because of GitHub identity restrictions.
- Never silently omit a finding.
- One command invocation may submit at most one review.

## Migration Notes

No configuration migration is required. Existing review, release, planner, and workflow commands remain unchanged.

# Validation

## Required Tests and Quality Gates

Tests must cover:

- each decision/event mapping;
- malformed and contradictory reports;
- missing repository/PR/head identity;
- identity mismatch;
- stale head SHA;
- closed and merged PR rejection;
- deterministic body;
- findings with no location;
- supported inline location conversion;
- unsupported inline location behavior without silent loss;
- dry-run with no write client;
- exactly one review write on success;
- GitHub API, permission, and self-approval failures;
- no downgrade from APPROVE;
- CLI output and exit codes;
- no unrelated GitHub mutation.

Use mocks and fixtures only. Tests must not submit a real review.

## Validation Commands

```bash
uv run devbot specification validate --task 54
uv run ruff check .
uv run pytest
```

## Success Criteria

The Task is successful when a validated, current-head-bound ReviewReport can be previewed or submitted as exactly one official GitHub review, with every finding preserved and no unrelated writes.

# Safety

## Things the Implementation Agent Must NOT Do

- Do not create or configure a GitHub App.
- Do not submit a real review during tests or implementation verification.
- Do not call an LLM.
- Do not merge, label, close, or modify the PR beyond the single review submission.
- Do not infer missing head SHA, PR identity, or diff positions.
- Do not silently convert a failed APPROVE into COMMENT.
- Do not expand into rework, merge readiness, or auto-merge.

# Completion

## Expected Deliverables

- GitHub review submission module and typed results/errors.
- Required GitHub client endpoint support.
- `devbot review submit` with dry-run.
- Unit and CLI tests.
- `results/054-github-review-submission.md`.
- Roadmap/ADR updates where required.

## Result Document

Document event mapping, stale-head guarantees, finding preservation, inline-comment policy, write boundary, authentication assumptions, tests, and scope exclusions.

## PR Expectations

- One PR from `task/054-github-review-submission` to `main`.
- Reference and close Issue #113.
- Apply review fixes to the same branch and PR.

# Handoff

## Required Handoff Procedure

1. Work only on `task/054-github-review-submission`.
2. Keep Issue #113 as the task identity.
3. Reuse Task 053 report validation and decision types.
4. Produce the Result document before requesting review.
5. Open one PR to `main`, closing Issue #113.
6. Apply all review fixes to the same PR.

## Token-Limit Behaviour

Prioritize stale-head safety, exact event mapping, no silent finding loss, dry-run/write separation, tests, then documentation. Do not reduce acceptance criteria silently.

# Full Task Contract Reference

```markdown
# Task 054 — GitHub Review Submission

## Provenance

- GitHub Issue: #113
- Branch: `task/054-github-review-submission`
- Specification: `specifications/054-github-review-submission.md`

## Goal

Submit a validated Task 053 ReviewReport as exactly one official GitHub Pull Request review with deterministic mapping and stale-head protection.

## Contract Metadata

- Specification Type: feature
- Release Impact: feature
- Risk Level: high
- Migration Required: false
- Backward Compatible: true

## Contract Version

1

## Scope

- Map ReviewDecision to GitHub review events.
- Validate ReviewReport input through Task 053 APIs.
- Require report metadata to identify repository, PR number, and reviewed head SHA.
- Compare the recorded SHA with the PR's current head SHA and fail closed when stale.
- Render deterministic review body and supported inline comments.
- Add typed dry-run and submission results.
- Submit exactly one review through the existing GitHub write boundary.
- Surface self-approval and permission failures clearly.
- Add tests and Result documentation.

## Out of Scope

- LLM review execution or finding generation.
- GitHub App creation, installation, or token exchange.
- Review/rework loops.
- Merge readiness and auto-merge.
- Automatic code modification.
- GitLab or Jira support.

## Acceptance Criteria

1. `approved`, `changes_required`, and `comment_only` map only to `APPROVE`, `REQUEST_CHANGES`, and `COMMENT` respectively.
2. Invalid or contradictory reports fail before any GitHub write client is used.
3. A report whose reviewed head SHA differs from the current PR head SHA is rejected.
4. Dry-run performs no GitHub write and displays the exact intended event/body/comments.
5. Real execution submits exactly one review and returns a typed result.
6. Unsupported inline locations fail closed or are explicitly rendered as body-only findings; they are never silently dropped.
7. GitHub self-approval and permission failures are surfaced as typed, actionable errors.
8. No merge, code modification, rework, or additional PR mutation occurs.
9. Specification validation, Ruff, and the complete test suite pass.

## Deliverables

- GitHub review-submission service and typed models.
- GitHub write-client review endpoint support when absent.
- `devbot review submit` CLI with `--dry-run`.
- Unit and CLI tests.
- `results/054-github-review-submission.md`.
- Roadmap/ADR updates where required.
```
