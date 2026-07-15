# Task 022: Planner Workflow Standard

## Goal

Standardize the Planner-owned contract-first workflow used by DevBot.

The Planner must prepare the Task before implementation begins so that one Task has one branch, one Pull Request, one execution Issue, one Validation Gate, and one evidence trail.

## Motivation

Recent Tasks proved that the most reliable workflow is:

1. Planner defines scope, Checkpoints, and Validation Gate.
2. Planner creates the branch, contract file, Pull Request, and execution Issue.
3. Implementer continues on the existing branch and Pull Request.
4. Reviewer evaluates the implementation against repository policy and the Task contract.
5. Operator performs the final merge.

This workflow currently depends on convention and repeated prompts. It must become repository policy and be machine-checkable where practical.

## Scope

### 1. Planner responsibilities

Document that the Planner owns:

- Task number and title
- scope and out-of-scope boundaries
- Checkpoints and required tests
- Validation Gate
- branch creation
- contract file creation
- Pull Request creation
- execution Issue creation
- cross-linking branch, PR, Issue, and contract

### 2. Single Task workspace policy

Define and enforce:

- one Task = one branch = one Pull Request
- contract, implementation, tests, Result, and rework continue on the same branch and PR
- Implementers must not create a second implementation branch or PR when the Planner workspace already exists

### 3. Naming and numbering policy

Define canonical names:

- branch: `task/<task-number>-<slug>`
- contract: `tasks/<task-number>-<slug>.md`
- Result: `results/<task-number>-<slug>.md`
- PR title: `Task <number>: <title>`
- execution Issue title: `Execute Task <number>: <title>`

GitHub Issue and PR numbers are independent identifiers and must not be forced to match the Task number. All identifiers must be cross-linked explicitly.

### 4. Execution Issue contract

The execution Issue must include:

- contract path
- branch name
- PR number
- required Checkpoint range
- Validation Gate requirement
- Result path
- prohibition on creating another branch or PR

### 5. Review entry contract

A reviewer request as short as `Review PR #<number>.` must be sufficient when the repository context is available.

Repository policy must require the Reviewer to discover and read:

- `AGENTS.md`
- the repository Review Gate documentation
- the linked Task contract
- the Result document
- PR Evidence
- CI/check status

The Reviewer must return `MERGE READY` or `REQUEST CHANGES` and must not modify code or merge.

### 6. Planner checklist and validation

Provide a reusable checklist or validation helper that detects at minimum:

- missing contract file
- missing branch/PR/Issue cross-links
- duplicate Task branches or PRs
- missing Validation Gate
- missing Checkpoints
- inconsistent Task number or slug

### 7. Documentation

Update `AGENTS.md` and the relevant workflow/task standards so Planner, Implementer, Reviewer, and Operator responsibilities are unambiguous.

## Out of Scope

- Automatic Planner model execution
- automatic product/task prioritization
- Worktree isolation
- Timeline automatic recording
- Dashboard/UI
- notifications
- multi-agent failover
- automatic merge

## Checkpoints

### CP-022-1 — Planner role documented

Planner responsibilities are explicitly documented.

Required test: `test_planner_role_contract`

### CP-022-2 — Single Task workspace policy

One Task = one branch = one PR is documented and validated.

Required test: `test_single_task_workspace_policy`

### CP-022-3 — Naming and numbering policy

Canonical paths and names are validated, and GitHub numbers are treated independently from Task numbers.

Required test: `test_planner_naming_and_numbering_policy`

### CP-022-4 — Execution Issue template

Execution Issue content includes all required links and constraints.

Required test: `test_execution_issue_contract_template`

### CP-022-5 — PR contract template

Planner-created PR Evidence contains the contract, branch policy, scope, and execution Issue link.

Required test: `test_planner_pr_contract_template`

### CP-022-6 — Review entry contract

Repository policy defines how a Reviewer resolves Review Gate, Task contract, Result, PR Evidence, and CI from a minimal review request.

Required test: `test_minimal_review_entry_contract`

### CP-022-7 — Duplicate workspace protection

Validation detects an additional branch or PR for a Task that already has a Planner-owned workspace.

Required test: `test_duplicate_task_workspace_rejected`

### CP-022-8 — Missing evidence detection

Validation detects missing Checkpoints, Validation Gate, Result path, or cross-links.

Required test: `test_planner_contract_missing_evidence`

### CP-022-9 — Existing workflows remain compatible

Daemon implementation, review, rework, delivery, timeline, and status behavior remain unchanged except for explicit Planner workflow validation.

Required test: `test_existing_workflows_compatible_with_planner_standard`

### CP-022-10 — Result and documentation

Create `results/022-planner-workflow-standard.md` and update all referenced repository documentation.

## Validation Gate

Must pass:

- `uv sync`
- `uv run ruff check .`
- `uv run pytest`

Manual verification:

- create or validate a sample Planner workspace
- verify duplicate branch/PR detection
- verify missing cross-link detection
- verify that `Review PR #<number>.` resolves the required repository review sources

## Deliverables

- Planner workflow policy
- role responsibility documentation
- naming and numbering rules
- Planner PR and execution Issue templates
- minimal review entry contract
- validation/checklist implementation
- required tests
- `results/022-planner-workflow-standard.md`
