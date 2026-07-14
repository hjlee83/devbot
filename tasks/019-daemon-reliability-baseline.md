# Task 019: Daemon Reliability Baseline

## Goal

Stabilize DevBot daemon execution for long-running operation.

This task focuses on operational reliability rather than new user-facing features. Operators must always understand why execution stopped, whether recovery is automatic, what manual action is required, and whether it is safe to continue.

## Scope

### 1. Failure Classification

Introduce explicit failure categories for at least:

- `workspace_invalid`
- `startup_validation_failed`
- `agent_session_limit`
- `agent_execution_failed`
- `delivery_failed`
- `review_failed`
- `github_api_error`
- `configuration_error`
- `unknown_error`

### 2. Retry Policy

Define deterministic retry behavior for every failure type.

Minimum rules:

- `workspace_invalid`: no automatic retry
- `agent_session_limit`: no repeated polling retry; require operator action or a known retry-after boundary
- `delivery_failed`: bounded retry only
- `github_api_error`: bounded exponential backoff
- `configuration_error`: fatal startup failure

### 3. Recovery Policy

Every claimed workflow must leave `devbot:working` through one of these explicit outcomes:

- restore to the previous stable state
- `devbot:blocked`
- `devbot:manual-action`
- successful transition to `devbot:review`

No failure path may leave an Issue permanently in `devbot:working`.

### 4. Startup Validation

Before polling begins, validate:

- repository configuration
- required GitHub credentials
- duplicate daemon lock
- workspace cleanliness
- current branch compatibility
- required timeline configuration

Fatal startup validation failures must stop before the first polling cycle.

### 5. Doctor Command

Provide:

```bash
uv run devbot doctor
```

The command must report at least:

- configured repositories
- workspace status
- current branch
- GitHub connectivity/authentication
- daemon lock status
- configured implementer and reviewer
- whether the daemon is safe to start

### 6. Diagnostics

Every operational failure must produce an operator-friendly diagnostic report containing:

- repository
- Issue/PR when available
- current branch
- workspace status and changed files when relevant
- failure category
- retry decision
- recovery recommendation

### 7. Daemon Survival

A non-fatal Job failure must not terminate the daemon process. The next cycle must still run.

### 8. Fatal Failure Handling

A fatal configuration or startup failure must terminate safely before polling and must not mutate GitHub state.

### 9. Session Limit Handling

When an Agent reports a session or usage limit, DevBot must:

- classify it as `agent_session_limit`
- avoid infinite retries
- avoid leaving the Issue in `devbot:working`
- provide a clear recovery hint

## Out of Scope

- Worktree isolation
- Timeline automatic recording
- Dashboard/UI
- Slack or chat integrations
- Multi-agent failover
- VPS deployment

## Checkpoints

### CP-019-1 — Failure classification

Required test:

- `test_failure_classification`

### CP-019-2 — Retry policy

Required test:

- `test_retry_policy`

### CP-019-3 — Recovery policy

Required test:

- `test_recovery_policy`

### CP-019-4 — Startup validation

Required test:

- `test_startup_validation`

### CP-019-5 — Doctor command

Required test:

- `test_doctor_command`

### CP-019-6 — Diagnostic output

Required test:

- `test_diagnostics_output`

### CP-019-7 — Non-fatal failure survival

Required test:

- `test_daemon_survives_non_fatal_failure`

### CP-019-8 — Fatal failure handling

Required test:

- `test_daemon_stops_on_fatal_failure`

### CP-019-9 — Agent session limit handling

Required test:

- `test_agent_session_limit_handling`

### CP-019-10 — Regression safety

Required evidence:

- existing test suite passes
- existing daemon CLI behavior remains compatible

### CP-019-11 — Result documentation

Required artifact:

- `results/019-daemon-reliability-baseline.md`

## Validation Gate

All of the following must pass:

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot doctor
```

Also perform:

```bash
uv run devbot --once
```

If the live command cannot complete because of external credentials, network restrictions, or an existing daemon lock, the Result document and PR Evidence must record the exact limitation and the alternative verification used.

## Deliverables

- failure classification
- retry policy
- recovery policy
- startup validation
- `doctor` command
- diagnostics
- tests for every checkpoint
- updated documentation
- `results/019-daemon-reliability-baseline.md`
