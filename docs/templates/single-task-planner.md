# Single-Issue Planner Template

This template supersedes the former Planning Issue + Execution Issue workflow.

## Canonical topology

One Task uses exactly:

- one Task Issue;
- one Task branch;
- one Task contract;
- one Pull Request;
- one Result document.

Do not create a separate Execution Issue.

## Task Issue

Title:

```text
Task <NNN>: <Title>
```

Body:

```markdown
## Summary

<Problem and goal>

## Task Artifacts

- Contract: `tasks/<NNN>-<slug>.md`
- Branch: `task/<NNN>-<slug>`
- Pull Request: #<PR>
- Result: `results/<NNN>-<slug>.md`

This Issue is both the planning and execution record.
Do not create another Issue, branch, or PR.

## Scope

<Scope>

## Out of Scope

<Out of scope>
```

After the contract and PR exist, replace `devbot:planning` with `devbot:ready` on this same Issue.

## Pull Request

The PR must target `main`, use the same Task branch throughout implementation and rework, and close the Task Issue:

```markdown
## Branch / PR Policy

- Branch: `task/<NNN>-<slug>`
- Pull Request: #<PR>
- Task Issue: #<ISSUE>
- Contract: `tasks/<NNN>-<slug>.md`
- Result: `results/<NNN>-<slug>.md`

Do not create another Issue, implementation branch, or PR.

Closes #<ISSUE>
```

## Invariants

- Issue creation count per Task: 1.
- PR creation count per Task: 1.
- The Task Issue progresses through planning, ready, working, review, rework, manual-action, and completion labels.
- Planner output must never contain `Execution Issue: pending` or request creation of an `Execute Task ...` Issue.
