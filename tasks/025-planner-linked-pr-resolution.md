# Task 025: Planner-Linked PR Resolution Hardening

## Goal

Make Planner-created Issue/PR/branch mappings authoritative during daemon execution so DevBot never creates a fallback branch when an execution Issue already names an existing Pull Request.

## Background

Task 024 Issue #47 explicitly named PR #46 and branch `task/024-timeline-auto-recording`, but DevBot resolved linked PRs only from closing keywords in the PR body. Because PR #46 initially lacked `Closes #47`, linked PR resolution returned `None`, and WorktreeManager created `devbot/devbot-47-...` from `origin/main`. The prepared workspace therefore lacked the approved contract and branch history.

## Scope

1. Parse `Pull Request: #<number>` from the execution Issue body.
2. Resolve that explicit PR number before closing-keyword fallback.
3. Verify that the resolved PR is open and belongs to the managed repository.
4. Verify that Issue branch metadata and PR head branch agree when both are present.
5. If an Issue explicitly names a PR but it cannot be resolved, fail workspace preparation instead of generating a fallback branch.
6. Preserve legacy fallback branch creation only for Issues that do not declare a Planner PR.
7. Update Planner PR/Issue templates so the PR body includes `Closes #<execution-issue>` once the Issue number is known.
8. Add diagnostics identifying expected PR, resolved PR, expected branch, and failure reason.
9. Preserve queue, state-machine, review, rework, delivery, Timeline, retry, and worktree behavior outside this resolution boundary.

## Out of Scope

- Timeline auto recording
- automatic Planner model execution
- automatic merge
- multi-agent failover
- dashboard/UI
- deployment

## Checkpoints

### CP-025-1 — Explicit PR metadata parsing

Parse `Pull Request: #<number>` from a Planner execution Issue.

Required test: `test_execution_issue_pr_number_is_parsed`

### CP-025-2 — Explicit PR resolution precedence

An Issue-declared PR is resolved even when the PR body has no closing keyword.

Required test: `test_explicit_issue_pr_resolves_without_closing_keyword`

### CP-025-3 — Existing branch reuse

The resolved PR head branch is passed to WorktreeManager and reused.

Required test: `test_planner_pr_head_branch_is_reused`

### CP-025-4 — Contract workspace correctness

The prepared worktree contains the approved Task contract from the Planner branch.

Required test: `test_prepared_workspace_contains_planner_contract`

### CP-025-5 — Missing explicit PR fails safely

If an Issue names a PR that cannot be found, no fallback branch is created and workspace preparation fails with operator guidance.

Required test: `test_missing_explicit_pr_rejects_fallback_branch`

### CP-025-6 — Branch/PR mismatch fails safely

Conflicting Issue branch metadata and PR head branch are rejected.

Required test: `test_issue_branch_and_pr_head_mismatch_rejected`

### CP-025-7 — Legacy fallback compatibility

Issues without Planner PR metadata retain the existing new-branch fallback behavior.

Required test: `test_legacy_issue_without_pr_keeps_fallback_branch`

### CP-025-8 — Planner metadata template

Planner templates cross-link Issue, PR, branch, contract, and Result, and produce `Closes #<issue>` for the PR body when an Issue number is available.

Required test: `test_planner_pr_template_includes_execution_issue_closing_link`

### CP-025-9 — Diagnostics

Resolution failures report expected PR and branch values without hiding the primary failure.

Required test: `test_planner_pr_resolution_diagnostics`

### CP-025-10 — Regression safety

Existing daemon, worktree, review, rework, delivery, Timeline, retry, and state-machine tests pass.

Required test: `test_existing_workflows_compatible_with_planner_pr_resolution`

### CP-025-11 — Result and documentation

Create `results/025-planner-linked-pr-resolution.md` and update relevant workflow documentation and roadmap.

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`
- `uv run devbot doctor`
- `uv run devbot --once --dry-run`

Manual verification:

- verify Issue #47 resolves PR #46 without relying on `Closes #47`
- verify the prepared branch is `task/024-timeline-auto-recording`
- verify a missing explicit PR does not create `devbot/devbot-*`
- verify a legacy non-Planner Issue can still create a fallback branch

## Branch and PR Policy

- Branch: `task/025-planner-linked-pr-resolution`
- One Planner-created PR for contract, implementation, tests, Result, and rework
- Do not create another branch or PR

## Result

- `results/025-planner-linked-pr-resolution.md`

## Definition of Done

Planner metadata is authoritative, explicit PR resolution is deterministic, and DevBot cannot silently replace an approved Planner workspace with a fallback branch.