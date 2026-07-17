# Task 031: Agent Execution Environment

## Goal

Standardize Agent execution so implementer and reviewer roles receive the same explicit, safe, observable execution context in Local and VPS-style environments.

## Scope

1. Introduce an immutable `AgentExecutionContext` shared by every Agent runner.
2. Include repository, PreparedWorkspace, canonical branch, Issue, PR, role, and sanitized environment metadata.
3. Require every production Agent process to use the PreparedWorkspace as its actual cwd.
4. Normalize a minimal set of `DEVBOT_*` environment variables for Agent processes.
5. Centralize safe execution-policy diagnostics.
6. Cache Agent CLI version and capability discovery without allowing stale or failed discovery to bypass safety checks.
7. Extend `devbot doctor` to validate configured Agent availability and unattended execution requirements.
8. Preserve existing Codex safety guarantees from Task 030 and apply the shared context without regression.

## Out of Scope

- Per-Agent or per-role model selection
- Model fallback or routing
- New Agent providers
- Admin API or UI
- systemd, Docker, or VPS installation
- GitHub App migration
- Automatic merge

## Planner Artifacts

- Task Issue: #64
- Branch: `task/031-agent-execution-environment`
- Contract: `tasks/031-agent-execution-environment.md`
- Pull Request: this Task's single Planner PR
- Result: `results/031-agent-execution-environment.md`

## Checkpoints

### CP-031-1 — Shared execution context

Implementer and reviewer runners receive a typed immutable context containing repository identity, PreparedWorkspace path, branch, Issue number, PR number when present, Agent role, and execution identifier.

Required test: `test_agent_execution_context_contains_canonical_task_metadata`

### CP-031-2 — PreparedWorkspace cwd invariant

All production Agent subprocesses execute with the PreparedWorkspace as cwd. The operator checkout must never be used after workspace preparation.

Required test: `test_all_agent_roles_execute_from_prepared_workspace`

### CP-031-3 — Normalized safe environment

Agent subprocesses receive explicit environment variables:

- `DEVBOT_REPOSITORY`
- `DEVBOT_BRANCH`
- `DEVBOT_ISSUE`
- `DEVBOT_PR`
- `DEVBOT_WORKSPACE`
- `DEVBOT_ROLE`
- `DEVBOT_EXECUTION_ID`

No token, secret, Authorization header, or unapproved credential value may be added by this layer.

Required test: `test_agent_environment_is_normalized_and_contains_no_secrets`

### CP-031-4 — Common launcher boundary

Subprocess construction and execution policy are coordinated through one common launcher or equivalent shared boundary while provider-specific command construction remains inside each Agent adapter.

Required test: `test_agent_launcher_applies_shared_context_to_provider_command`

### CP-031-5 — Safe diagnostics

Execution diagnostics expose Agent, role, version, cwd, workspace, branch, Issue, PR, sandbox, approval, network policy, and capability status without prompt contents or secrets.

Required test: `test_agent_execution_diagnostics_are_complete_and_redacted`

### CP-031-6 — Capability cache

CLI version and capability detection are cached for the process lifetime or a documented bounded lifetime. Failed or incomplete discovery is fail-closed and must not be cached as a successful result.

Required tests:
- `test_agent_capability_detection_is_cached`
- `test_failed_capability_detection_remains_fail_closed`

### CP-031-7 — Doctor checks

`devbot doctor` reports configured Agent executable availability, version discovery, required unattended capabilities, and actionable recovery guidance. Doctor must not execute a real development or review task.

Required test: `test_doctor_reports_agent_execution_readiness`

### CP-031-8 — Role parity

IMPLEMENT, REWORK, and REVIEW use the same context and launcher invariants. Provider-specific differences must not change workspace, environment, or diagnostic semantics.

Required test: `test_implement_rework_and_review_share_execution_invariants`

### CP-031-9 — Existing workflow compatibility

Task 030 retry, worktree, main synchronization, review integration validation, interactive-output classification, delivery, and state transitions remain compatible.

Required test: `test_existing_workflows_remain_compatible_with_agent_execution_context`

## Validation Gate

Run from the Task PreparedWorkspace:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
uv run devbot --once --dry-run
```

Also demonstrate that implementer and reviewer dry-runs report the same canonical workspace and sanitized context without launching a real Agent process.

## Definition of Done

- All checkpoints and required tests pass.
- Every Agent role consumes the shared execution context.
- Every production Agent subprocess uses the PreparedWorkspace as cwd.
- Environment and diagnostics are standardized and secret-safe.
- Capability discovery is cached and fail-closed.
- Doctor reports actionable Agent readiness.
- Result and PR Evidence record actual implementation and validation outcomes.
- DevBot reaches `devbot:ready-to-merge` on Issue #64 and the canonical PR.
- Final merge remains manual.

## Branch and PR Policy

Use exactly one Issue, one Branch, one Contract, and one Pull Request. Continue all implementation, review, and rework on `task/031-agent-execution-environment` and its linked Planner PR. Do not create a separate Execution Issue, branch, contract, or PR.
