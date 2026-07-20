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
4. CI status for the PR head is confirmed green by at least one
   provider-neutral source (`devbot/ci_status.py`), and no consulted source
   reports a failure or pending run:
   - GitHub Actions workflow runs for the head SHA.
   - The combined commit-status API (for external CI providers).
   - GitHub check-runs, as an optional bonus source.

   Each source is read independently; a permission gap or API error on one
   source (e.g. a fine-grained PAT without the "Checks" permission, which
   403s on check-runs) does not block the gate as long as another source
   can confirm status. A classic PAT with the `repo` scope can see all
   three sources and remains a valid, but optional, way to grant check-runs
   access - it is not required.

If no source can confirm CI status at all, the gate fails closed exactly
like an unmet gate (Issue #127).

If any gate fails, DevBot logs and comments the reason, keeps
`devbot:ready-to-merge`, and leaves the PR available for human merge.
