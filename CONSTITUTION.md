# DevBot Project Constitution

Version: 1.1.0
Last Updated: 2026-07-18

This document defines the stable operating principles of the DevBot project.
When implementation details, task documents, or agent instructions conflict with
this document, this document is the source of truth unless the project owner
explicitly approves a change.

---

## 1. Human-first planning

Planning is intentionally human-driven.

Feature ideas, architecture, trade-offs, scope, risks, and acceptance criteria
are decided collaboratively by the project owner and ChatGPT before execution
artifacts are created.

The Planner must not invent requirements, expand scope, or begin implementation
without explicit approval from the project owner.

---

## 2. Planner boundary

After the project owner approves a design, the Planner converts that approved
design into GitHub artifacts.

The Planner owns only:

- one Task Issue;
- one Task Branch;
- one Task Contract;
- one Pull Request;
- initial labels and cross-links.

The Planner does not implement the Task and does not create a separate
Execution Issue.

---

## 3. Single Task model

Every Task uses exactly:

- 1 Task Issue;
- 1 Branch;
- 1 Task Contract;
- 1 Pull Request.

All implementation, tests, Result updates, review feedback, and rework continue
on the same Task Branch and Pull Request.

Duplicate Issues, Branches, Contracts, or Pull Requests for the same Task are
prohibited unless the project owner explicitly approves an exception.

---

## 4. DevBot responsibility

After a Task receives `devbot:ready`, DevBot owns the automated execution flow:

```text
IMPLEMENT
→ REVIEW
→ REWORK when required
→ REVIEW
→ READY TO MERGE
```

DevBot must preserve the same Task Issue, Branch, Contract, and Pull Request
throughout the flow.

Planning remains human-driven.

---

## 5. Workspace contract

After `WorktreeManager` prepares a workspace, every Agent and every execution
stage must operate exclusively on the resulting `PreparedWorkspace`.

No Agent, validation step, delivery step, or review step may fall back to the
configured host repository after a prepared workspace exists.

The operator checkout may be on another branch or contain unrelated local
changes without affecting a prepared Task workspace.

---

## 6. Review and merge boundary

Review verifies the Task Contract, tests, Result, PR Evidence, CI, security, and
documentation.

A review outcome is either:

- `MERGE READY`; or
- `REQUEST CHANGES`.

`REQUEST CHANGES` returns the same Task and Pull Request to rework.
`MERGE READY` means the change is eligible for the merge gate.

Merge is manual by default. Automatic merge is allowed only when the project
owner enables the explicit policy flag and every merge gate passes:

- the current PR head has a valid `MERGE READY` review and the
  `devbot:ready-to-merge` label;
- GitHub check-runs for that exact head are complete and green;
- the global kill-switch is enabled;
- the repository is explicitly allowlisted;
- the PR is not a DevBot self-modification PR.

DevBot self-modification PRs always require human merge approval, regardless of
the global kill-switch or repository allowlist.

---

## 7. Traceability

The Task Issue, Branch, Task Contract, Pull Request, Result, and Timeline must be
mutually traceable.

Automation must not reduce visibility into:

- what was requested;
- what was implemented;
- what was tested;
- what was reviewed;
- why a state transition occurred;
- what requires manual action.

---

## 8. Safety and determinism

DevBot prioritizes:

- safety before automation;
- deterministic state transitions;
- idempotent retries;
- bounded loops;
- reproducible validation;
- preservation of work on failure;
- explicit manual-action states instead of unsafe guessing.

No automation may silently discard work, create duplicate workspaces, or merge
an unverified change. A change is verified for automatic merge only after the
review, CI, kill-switch, allowlist, and self-modification gates in Section 6 all
pass for the same PR head.

---

## 9. Agent neutrality

Planner, Implementer, and Reviewer are roles, not vendor-specific products.

The configured model or agent may change, but every role must follow the same
Task Contract, workspace rules, state transitions, and quality gates.

---

## 10. Document hierarchy

- `CONSTITUTION.md` defines stable project principles.
- `AGENTS.md` defines executable rules and SOPs for AI Agents.
- `README.md` introduces the project and documents setup and operation.
- `docs/` contains detailed architecture, runbooks, standards, and decisions.
- `tasks/` contains Task Contracts.
- `results/` contains implementation evidence and handoff records.

Changes to this Constitution require an explicit architecture decision by the
project owner.
