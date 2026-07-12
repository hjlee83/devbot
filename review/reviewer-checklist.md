# Reviewer Checklist

> Purpose: Review a Pull Request against the Task contract, not just the
> code.

## 1. Task Contract

-   [ ] PR scope matches the Task.
-   [ ] No unrelated features or refactoring.
-   [ ] Out-of-scope items were not implemented.

## 2. Quality Gates

For every checkpoint:

  Checkpoint   Required Test   Exists   Meaningful   PASS
  ------------ --------------- -------- ------------ ------
  CP-XXX       test_xxx        ☐        ☐            ☐

Review: - \[ \] Every checkpoint has at least one required test. - \[ \]
Test names match the Task contract. - \[ \] Tests verify behavior, not
only execution.

## 3. Test Quality

-   [ ] Happy path covered.
-   [ ] Failure path covered.
-   [ ] Boundary conditions covered.
-   [ ] Regression risk considered.

## 4. Verification Gates

-   [ ] uv sync
-   [ ] uv run ruff check .
-   [ ] uv run pytest
-   [ ] uv run devbot

All must succeed.

## 5. Security

-   [ ] No secrets committed.
-   [ ] No hard-coded tokens.
-   [ ] No production configuration.

## 6. Documentation

-   [ ] Task unchanged unless intentionally revised.
-   [ ] Result document updated.
-   [ ] PR evidence table completed.

## 7. Git

-   [ ] Task branch only.
-   [ ] No direct main/master commit.

## Review Decision

-   [ ] Approve
-   [ ] Request Changes

## Review Notes

-   
