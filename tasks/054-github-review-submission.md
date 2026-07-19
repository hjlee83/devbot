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
