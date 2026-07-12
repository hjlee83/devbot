# Task 000: Development System

## Goal
Establish the shared documentation and quality-evidence system used by all later DevBot Tasks.

## Deliverables
- `AGENTS.md`
- architecture and workflow documents
- Task and Result conventions
- PR evidence template
- verification and doctor scripts

## Quality gates

| ID | Checkpoint | Required verification |
|---|---|---|
| CP-000-1 | All declared files exist | Manual file listing |
| CP-000-2 | Shell scripts are syntactically valid | `bash -n scripts/verify.sh scripts/doctor.sh` |
| CP-000-3 | Repository has no whitespace errors | `git diff --check` |

## Git rules
- Commit on `chore/task-000-development-system`.
- Push and PR creation are allowed.
