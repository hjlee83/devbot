# Task 021: Agent Outcome Classification Hardening

## Goal

Strengthen DevBot's interpretation of Agent execution results so workflow progression occurs only after explicit, successful implementation completion.

The daemon must distinguish completed implementation from approval waits, environment restrictions, session limits, skipped work, no-op execution, and other failure modes.

## Motivation

Operational testing exposed an unsafe path:

1. The implementer reported that Git/gh commands required approval and stopped without implementing.
2. DevBot observed no repository changes.
3. The existing contract PR was treated as a completed implementation.
4. The Issue advanced to review despite no implementation work.

Task 021 prevents this class of false-success transition.

## Scope

### 1. Explicit Agent outcomes

Introduce a normalized outcome model covering at least:

- `implementation_completed`
- `implementation_skipped`
- `no_repository_changes`
- `approval_required`
- `network_blocked`
- `session_limit`
- `repository_locked`
- `agent_failed`
- `unknown`

### 2. Structured outcome signals

Prefer structured Agent result signals over free-form text matching whenever available.

Text matching may remain as a compatibility fallback, but must not be the sole long-term contract.

### 3. Approval-required detection

Recognize common approval-wait language, including patterns such as:

- `requires approval`
- `approval required`
- `could you approve`
- `need ... approved to proceed`
- `hold off until you confirm`
- `should I proceed`

An approval-required result must not enter delivery or review.

### 4. Session-limit detection

Recognize provider usage/session limit outcomes, including reset-time messages.

A session-limit result must not be retried indefinitely and must provide an operator recovery hint.

### 5. Network- and permission-blocked detection

Recognize cases where required Git, GitHub, network, filesystem, or sandbox operations are blocked by the execution environment.

These outcomes require operator action and must not be treated as successful no-op implementation.

### 6. Workflow transition rules

Apply deterministic transitions:

- `implementation_completed` -> delivery
- `approval_required` -> `devbot:manual-action`
- `network_blocked` -> `devbot:manual-action`
- `repository_locked` -> `devbot:manual-action`
- `session_limit` -> non-retrying recoverable state with reset/recovery guidance
- `agent_failed` -> `devbot:blocked`
- `implementation_skipped` -> non-review state with explicit reason
- `unknown` -> safe failure state, never implicit success

No Agent outcome may leave an Issue permanently in `devbot:working`.

### 7. Delivery protection

Delivery may run only after an outcome classified as `implementation_completed`.

The presence of an existing linked PR is not proof that implementation completed. A contract-only PR must not satisfy implementation completion.

### 8. Review protection

An Issue may enter `devbot:review` only after all required implementation completion evidence exists.

At minimum, verify:

- explicit successful Agent outcome
- delivery success or a contract-approved no-change completion path
- required Result document existence when the Task contract requires one
- implementation evidence beyond the pre-existing contract-only commit

### 9. Diagnostics

When progression is prevented, operator-facing diagnostics must include:

- normalized Agent outcome
- matched structured signal or fallback reason
- blocked workflow phase
- resulting Issue state
- required operator action or retry condition

### 10. Contract-first workflow compatibility

Preserve the existing single Task = single branch = single PR policy.

When a Planner already created the branch and PR, DevBot must reuse them without interpreting their existence as implementation completion.

## Out of Scope

- Worktree isolation
- Timeline automatic recording
- Planner workflow automation
- Multi-agent failover
- Dashboard/UI
- Notifications
- VPS deployment

## Checkpoints

### CP-021-1 — Normalized outcome classification

Agent execution results are converted into explicit normalized outcomes.

Required test:

- `test_agent_outcome_classification`

### CP-021-2 — Approval-required detection

Approval-wait output is classified as `approval_required`.

Required test:

- `test_agent_approval_required_detection`

### CP-021-3 — Session-limit detection

Provider session/usage-limit output is classified without infinite retry.

Required test:

- `test_agent_session_limit_detection`

### CP-021-4 — Network-blocked detection

Network, Git/GitHub permission, or sandbox access blocking is classified as `network_blocked` or the documented equivalent.

Required test:

- `test_agent_network_blocked_detection`

### CP-021-5 — Outcome-driven state transitions

Each normalized outcome follows the documented workflow transition and never leaves the Issue stuck in `working`.

Required test:

- `test_agent_outcome_state_transition`

### CP-021-6 — Delivery requires completed implementation

Delivery does not run for approval-required, network-blocked, session-limit, skipped, failed, or unknown outcomes.

Required test:

- `test_delivery_requires_completed_implementation`

### CP-021-7 — Review requires completed implementation

An existing contract-only PR and a clean workspace are insufficient to enter review.

Required test:

- `test_review_requires_completed_implementation`

### CP-021-8 — Operator diagnostics

Diagnostics expose the normalized outcome, reason, resulting state, and recovery action.

Required test:

- `test_agent_outcome_diagnostics`

### CP-021-9 — Contract-first branch/PR reuse regression

Existing linked branches and PRs remain reusable without creating duplicate branches or PRs.

Required test:

- `test_contract_first_pr_reused_without_false_completion`

### CP-021-10 — Regression safety

Existing implementation, review, rework, delivery, retry, state-machine, and timeline tests pass without undocumented semantic changes.

Required validation:

- full existing test suite

### CP-021-11 — Explicit success is mandatory

DevBot must not infer implementation success solely from process exit success, absence of exceptions, an existing PR, or a clean workspace.

Successful implementation requires an explicit completion signal and the contract-required completion evidence.

Required test:

- `test_success_requires_explicit_completion`

### CP-021-12 — Result and evidence alignment

Create `results/021-agent-outcome-classification.md` and keep the PR body aligned with the final implementation, Checkpoint evidence, exact tests, Validation Gate results, and remaining limitations.

## Validation Gate

Must pass:

```bash
uv sync
uv run ruff check .
uv run pytest
```

Required scenario verification:

- approval-required output does not invoke delivery or review
- session-limit output does not loop indefinitely
- network-blocked output requests operator action
- explicit successful implementation follows delivery and review normally
- contract-only PR plus no implementation does not enter review

`uv run devbot --once --dry-run` must also be attempted and its exact outcome documented. Environment-related inability to reach GitHub must be recorded rather than misrepresented as success.

## Deliverables

- normalized Agent outcome model
- structured/fallback outcome detection
- outcome-driven workflow transitions
- delivery and review protection
- operator diagnostics
- tests for CP-021-1 through CP-021-11
- documentation updates
- `results/021-agent-outcome-classification.md`
