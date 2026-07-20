# Task 129: Bulk Worktree Cleanup

## Issue

GitHub Issue: #129 - Proposal: bulk/auto cleanup for worktrees of already-done Issues.

## Problem

`devbot worktree cleanup --issue <N>` removes one Issue worktree at a time. This preserves failed Job worktrees for diagnosis, but it gives operators no convenient explicit cleanup path for stale/prunable worktrees that Git already knows are safe to prune.

## Scope

- Add an explicit bulk cleanup CLI path for stale/prunable DevBot worktrees.
- Keep cleanup explicit; do not run it automatically during daemon polling or doctor.
- Use the same stale definition already reported by `WorktreeManager.health()` and `devbot doctor`: Git worktree entries under the repository-local worktree root marked `prunable`.
- Add a doctor cleanup hint when stale worktrees are reported.
- Add regression tests for WorktreeManager, CLI wiring, and doctor hint output.

## Out of Scope

- Automatically deciding that closed/done GitHub Issues should be cleaned up by contacting GitHub.
- Removing active registered worktrees.
- Per-repository cleanup scheduling or daemon-side automatic cleanup.

## Acceptance Criteria

1. `devbot worktree cleanup --stale` is accepted by the CLI.
2. `--issue` and `--stale` are mutually exclusive explicit cleanup targets.
3. Bulk cleanup prunes only Git-prunable worktrees under DevBot's worktree root.
4. Active/non-prunable worktrees are preserved.
5. `devbot doctor` reports a runnable cleanup hint when stale worktrees are present.
6. `uv run ruff check .` passes.
7. `uv run pytest` passes.
