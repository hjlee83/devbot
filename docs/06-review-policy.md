# PR Review Policy

The reviewer checks:

1. Implementation matches the Task contract.
2. Every checkpoint maps to actual test code.
3. Named tests assert meaningful behavior rather than superficial execution.
4. Boundary conditions and failure paths are covered.
5. Verification commands passed.
6. No unrelated refactoring or scope expansion occurred.
7. Secrets and environment-specific values were not committed.

The PR must contain a checkpoint-to-test evidence table.

## Automatic Merge Gate

`MERGE READY` does not merge by itself. It only allows DevBot to place the
exclusive `devbot:ready-to-merge` PR label when the existing review-readiness
gates pass:

1. The review belongs to the current PR head.
2. No unprocessed `@devbot` feedback remains.
3. The PR does not carry blocked/manual-action metadata.

After that label exists, automatic merge is still opt-in and must pass every
B2 gate:

1. `AUTOMERGE_ENABLED=true`.
2. The repository config has `automerge_allowed: true`.
3. The repository config is not `is_self_repo: true`.
4. GitHub check-runs for the PR head exist, are completed, and have only
   passing conclusions (`success`, `skipped`, or `neutral`).

If any gate fails, DevBot logs and comments the reason, keeps
`devbot:ready-to-merge`, and leaves the PR available for human merge.
