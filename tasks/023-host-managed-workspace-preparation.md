# Task 023: Host-Managed Workspace Preparation

## Goal

Make daemon-driven implementation independent from the operator checkout and from Agent-side GitHub/network discovery.

DevBot must prepare the exact Task branch, linked Pull Request context, and isolated working directory before invoking the Implementer. The Agent should be able to modify code and run local validation without needing `git fetch`, `gh pr view`, `curl`, branch discovery, or interactive network approval.

## Motivation

The Task 022 daemon attempt reached the Implementer but stopped because Claude requested approval for network commands while trying to discover the existing branch and PR. DevBot already knew the Issue, branch, PR, and repository, but that context was not prepared as an executable workspace before Agent invocation.

The daemon must own repository synchronization and workspace preparation. The Agent must own implementation inside the prepared workspace. Delivery remains a DevBot responsibility.

## Scope

### 1. Resolve the Planner workspace before Agent execution

For an implementation or rework Job, DevBot must resolve from GitHub and repository policy:

- repository
- execution Issue
- Task contract path
- existing Task branch
- linked open Pull Request
- base branch

When a Planner-owned branch and PR already exist, DevBot must reuse them and must not generate another branch or PR.

### 2. Host-managed remote synchronization

Before Agent invocation, DevBot performs required remote operations itself, including the minimum necessary fetch/ref refresh.

The Agent must not be responsible for discovering whether the remote Task branch or linked PR exists.

### 3. Isolated Job worktree

Create or reuse a dedicated Git worktree for the selected Job.

Recommended layout:

```text
<workspace-root>/<repo>                  # operator/main checkout
<workspace-root>/.devbot-worktrees/<repo>/<task-or-issue>
```

The operator checkout's current branch and uncommitted files must not be changed by the Job worktree.

### 4. Exact branch checkout

The Job worktree must check out the existing Planner branch when one is linked.

For a new Task without an existing branch, branch creation must follow repository Planner policy. This fallback must not run when an existing Task branch is available.

### 5. Prepared Agent context

The Agent prompt and execution context must state authoritatively:

- current repository path
- prepared branch
- linked PR number
- execution Issue number
- Task contract path
- Result path
- that remote discovery is already complete
- that the Agent must not create another branch or PR

The Agent runs with the Job worktree as its current working directory.

### 6. Networkless implementation compatibility

A normal implementation must be possible when the Agent can read/write local files and run local commands but cannot access GitHub or the network.

DevBot performs GitHub reads/writes, commit/push/PR updates, labels, and comments outside the Agent process.

This Task does not bypass Agent security controls or enable unrestricted network access.

### 7. Delivery from the prepared branch

Verification, commit, push, and PR reuse must operate on the same prepared worktree and branch.

Delivery must reject branch mismatch before commit or push. It must never push a generated `devbot/devbot-*` branch when an existing Planner branch is linked.

### 8. Worktree lifecycle

Define deterministic lifecycle behavior:

- create before Agent execution
- reuse only when repository, Issue, and branch match
- preserve on implementation or delivery failure for diagnostics/recovery
- remove after successful merge or explicit cleanup
- reject unsafe reuse when the worktree is dirty for an unrelated Job

Provide an explicit cleanup command or service API.

### 9. Failure and recovery integration

Workspace-preparation failures must occur before Agent invocation and use explicit failure categories and recovery guidance.

At minimum distinguish:

- remote synchronization failure
- linked branch missing
- branch/PR mismatch
- worktree creation failure
- worktree conflict
- prepared workspace dirty

No preparation failure may leave the Issue in `devbot:working` permanently.

### 10. Doctor and diagnostics

`devbot doctor` and Job diagnostics must report:

- operator checkout path and branch
- worktree root
- active Job worktrees
- branch/Issue/PR mapping
- stale or conflicting worktrees
- whether daemon Jobs can start safely

### 11. Existing workflow compatibility

Preserve existing behavior for:

- queue selection and concurrency
- state transitions
- retry and recovery policy
- review and rework
- Timeline CLI and status
- Review Gate
- Planner validation

### 12. Documentation and Result

Document the ownership boundary:

```text
Planner: creates Task/branch/PR/Issue contract
DevBot host: fetches, prepares worktree, delivers, updates GitHub
Implementer Agent: edits and validates local repository files
Reviewer: reviews the resulting PR
Operator: merges
```

Create `results/023-host-managed-workspace-preparation.md`.

## Out of Scope

- unrestricted Agent network access
- bypassing Claude/Codex approval or sandbox controls
- automatic Planner model execution
- multi-agent failover
- Timeline automatic recording
- automatic merge
- VPS or launchd/systemd deployment
- parallel Jobs modifying the same Task branch

## Checkpoints

### CP-023-1 — Existing Planner workspace resolution

DevBot resolves the linked Task branch and open PR before Agent invocation.

Required test: `test_existing_planner_workspace_is_resolved_before_agent`

### CP-023-2 — Host-managed synchronization

Required remote synchronization is performed by DevBot, not delegated to the Agent.

Required test: `test_host_prepares_remote_branch_before_agent`

### CP-023-3 — Isolated worktree creation

A Job receives a dedicated worktree and does not change the operator checkout.

Required test: `test_job_uses_isolated_worktree`

### CP-023-4 — Existing branch reuse

A linked Planner branch is checked out and no duplicate branch is created.

Required test: `test_existing_task_branch_is_reused`

### CP-023-5 — Prepared Agent context

The Agent receives authoritative branch, PR, Issue, contract, Result, and workspace context.

Required test: `test_agent_prompt_contains_prepared_workspace_context`

### CP-023-6 — Networkless Agent compatibility

Implementation can complete without Agent-side GitHub or network commands.

Required test: `test_implementation_does_not_require_agent_network_access`

### CP-023-7 — Delivery uses prepared branch

Verification, commit, push, and PR reuse use the prepared worktree branch and reject mismatch.

Required test: `test_delivery_uses_prepared_worktree_branch`

### CP-023-8 — Worktree lifecycle and cleanup

Worktrees are reused, preserved, rejected, or cleaned according to the lifecycle policy.

Required tests:

- `test_failed_job_preserves_worktree_for_recovery`
- `test_successful_cleanup_removes_worktree`
- `test_conflicting_dirty_worktree_is_rejected`

### CP-023-9 — Preparation failure recovery

Preparation failures are classified before Agent invocation and restore a stable Issue state.

Required test: `test_workspace_preparation_failure_skips_agent_and_recovers_state`

### CP-023-10 — Doctor and diagnostics

Doctor and diagnostics expose active, stale, and conflicting worktrees with mappings.

Required test: `test_doctor_reports_worktree_health`

### CP-023-11 — Operator checkout independence

Daemon execution works regardless of the operator checkout's current branch, provided the repository metadata and worktree root are usable.

Required test: `test_daemon_job_is_independent_of_operator_checkout_branch`

### CP-023-12 — Regression and documentation

All existing tests pass and the Result accurately records implementation, validation, and limitations.

Required artifact: `results/023-host-managed-workspace-preparation.md`

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`

Manual verification:

- keep the operator checkout on a non-main branch and run one prepared Job
- verify the Agent starts inside the Task worktree on the linked branch
- verify the Agent can implement without `git fetch`, `gh`, or `curl`
- verify delivery updates the existing Planner PR
- verify a failed Job preserves its worktree and diagnostic mapping
- verify explicit cleanup removes the worktree safely

## Deliverables

- Planner workspace resolver
- host-managed remote synchronization
- isolated worktree manager
- prepared Agent execution context
- networkless Agent-compatible implementation path
- prepared-branch delivery integration
- lifecycle cleanup support
- doctor and diagnostic integration
- required tests
- documentation updates
- `results/023-host-managed-workspace-preparation.md`
