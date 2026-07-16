# Task 029: Prepared Workspace Validation Environment

## Goal

Make validation reproducible and self-contained inside every Task PreparedWorkspace so Agents never fall back to the configured host checkout or its `.venv`.

## Background

Task 028 reached implementation and review successfully, but its first review required manual action because validation had been substituted through the host checkout environment. The code itself was correct; the failure was that the PreparedWorkspace did not provide a reliable execution environment for the literal Validation Gate commands.

Task 029 removes that operational gap.

## Scope

1. Extend PreparedWorkspace or its preparation result with authoritative validation-environment information.
2. Prepare a workspace-scoped Python/uv execution environment before Agent validation when required.
3. Ensure IMPLEMENT, REWORK, REVIEW validation, delivery validation, and explicit validation helpers execute with the PreparedWorkspace as cwd.
4. Prevent use of the configured host checkout's `.venv`, executable paths, or repository-relative fallbacks after `WorktreeManager.prepare()`.
5. Preserve shared package caches where safe, but keep repository state and environment selection workspace-bound.
6. Record the PreparedWorkspace path, exact commands, exit codes, and summarized results for Result and PR Evidence generation.
7. Classify failures distinctly, including environment preparation failure, dependency/network unavailability, command failure, and forbidden host fallback.
8. Preserve unfinished work and route only genuinely external requirements to manual action.
9. Keep all existing Planner, IMPLEMENT, REVIEW, REWORK, resume, delivery, Timeline, and ready-to-merge workflows compatible.

## Out of Scope

- Automatic merge
- External notifications
- Planner automation
- General packaging backend redesign
- Unrestricted Agent network or sandbox bypass
- Replacing uv with another dependency manager
- Distributed or remote build environments

## Dependencies

- Task 023 Host-Managed Workspace Preparation
- Task 026 Agent Resume & Timeout Recovery
- Task 027 Autonomous Review Loop and PreparedWorkspace contract
- Task 028 CLI Version Command operational evidence
- `CONSTITUTION.md` and `AGENTS.md`

## Planner Artifacts

- Task Issue: #59
- Branch: `task/029-prepared-workspace-validation`
- Contract: `tasks/029-prepared-workspace-validation.md`
- Pull Request: this Task's single Planner PR
- Result: `results/029-prepared-workspace-validation.md`

## Files Expected to Change

- `src/devbot/worktree.py`
- validation/execution environment code under `src/devbot/`
- Agent/polling/review/rework/delivery wiring where validation is invoked
- focused tests under `tests/`
- `docs/00-roadmap.md`
- relevant workspace/reliability documentation
- `results/029-prepared-workspace-validation.md`

## Checkpoints

### CP-029-1 — Authoritative validation workspace

Every validation invocation receives and uses the repository path from PreparedWorkspace.

Required test: `test_validation_uses_prepared_workspace_repository`

### CP-029-2 — Workspace-scoped environment preparation

DevBot prepares or selects a valid Python/uv execution environment for the PreparedWorkspace without relying on the host checkout's `.venv`.

Required test: `test_prepared_workspace_has_usable_validation_environment`

### CP-029-3 — Host environment fallback prevention

A host-checkout `.venv` or executable path is rejected once a PreparedWorkspace exists.

Required test: `test_validation_rejects_host_checkout_environment_fallback`

### CP-029-4 — Literal Validation Gate execution

Required commands are executed literally from the PreparedWorkspace rather than replaced with semantically similar Python calls.

Required test: `test_validation_executes_contract_commands_literally`

### CP-029-5 — Shared cache safety

A shared uv/package cache may be reused without changing cwd, repository selection, or virtual-environment ownership.

Required test: `test_shared_uv_cache_does_not_break_workspace_isolation`

### CP-029-6 — Structured validation evidence

Validation records workspace path, command, exit code, and summarized stdout/stderr suitable for Result and PR Evidence.

Required test: `test_validation_evidence_records_workspace_and_commands`

### CP-029-7 — Failure classification

Environment preparation, network/dependency, command, and forbidden-fallback failures are distinguishable and provide actionable diagnostics.

Required test: `test_validation_environment_failure_classification`

### CP-029-8 — Safe state routing

Repository-fixable validation failures route to rework; genuinely external verification routes to manual action; neither path loses work.

Required test: `test_validation_failure_routes_to_rework_or_manual_action`

### CP-029-9 — Resume compatibility

A resumed Task reuses the same PreparedWorkspace validation environment or reconstructs it deterministically without creating another branch or PR.

Required test: `test_resumed_task_reuses_validation_environment`

### CP-029-10 — Autonomous loop compatibility

The autonomous IMPLEMENT -> REVIEW -> REWORK -> REVIEW -> READY TO MERGE loop remains compatible and does not require host-checkout validation.

Required test: `test_autonomous_loop_validates_only_in_prepared_workspace`

### CP-029-11 — Existing workflow regression safety

Existing CLI, Planner, worktree, delivery, review, rework, Timeline, and reliability tests remain compatible.

Required test: `test_existing_workflows_remain_compatible_with_workspace_validation_environment`

### CP-029-12 — Documentation and Result

Architecture/workspace documentation, roadmap, Result, and PR Evidence accurately describe the final behavior and validation results.

## Validation Gate

Run sequentially from the Task 029 PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Also demonstrate:

- a dirty or unusable host checkout does not affect validation in a clean PreparedWorkspace;
- no command resolves to the host checkout's `.venv`;
- a dependency/network failure is reported precisely without silent fallback;
- Result and PR Evidence identify the actual PreparedWorkspace and literal commands used.

## Risk

- Creating separate environments per worktree may add disk usage and startup latency.
- Overly strict path checks could reject safe shared caches or installed tools.
- Environment reconstruction during resume could become non-deterministic if metadata is incomplete.
- Failure routing changes could regress the autonomous review loop.

## Rollback Strategy

Remove the new validation-environment abstraction and restore the previous PreparedWorkspace execution wiring. Keep the existing PreparedWorkspace-only repository contract and do not reintroduce host-checkout fallback.

## Reviewer Focus

- Verify every validation path uses PreparedWorkspace.repository as cwd.
- Verify no host checkout `.venv` or repository fallback remains.
- Verify shared caches do not weaken isolation.
- Verify failure categories and state routing are deterministic.
- Verify resume and autonomous review loops reuse the existing Issue, branch, PR, and worktree.
- Verify evidence is generated from actual command execution rather than assertions written by the Agent.

## Definition of Done

- All checkpoints and required tests pass.
- Full regression suite passes.
- Literal Validation Gate commands pass from the PreparedWorkspace.
- No validation path falls back to the host checkout after preparation.
- Result and PR Evidence contain truthful workspace and command evidence.
- DevBot reaches `devbot:ready-to-merge` on the same Issue, branch, PR, and PreparedWorkspace.
- Final merge remains manual.

## Branch and PR Policy

This Task uses exactly one Issue, one Branch, one Contract, and one Pull Request. Do not create a separate Execution Issue, implementation branch, contract, or PR. Continue all implementation, review, and rework on `task/029-prepared-workspace-validation` and its linked Planner PR.
