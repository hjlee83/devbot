# DevBot Project History

This document is the canonical human-readable project history for DevBot. Stable Release
Notes should summarize user-facing changes from this history, while Task contracts and
Results remain the detailed audit records.

## Release Notes Format

Future generated Release Notes use these sections in order:

1. What's New
2. Improvements
3. Fixes
4. Operational Changes
5. Upgrade Notes
6. Known Limitations

## Stable Releases

### v0.1.0 - Initial Stable Release

- Source commit: `6526cfea27e4a5fd0003df6eb698cd202dedcf57`
- Package version: `0.1.0`
- Tag: `v0.1.0`
- Artifact: `devbot-0.1.0-portable-python.tar.gz`
- SHA-256: `8a2a30bb78f1f06da0b80519efc13aa7003a055d93a4896aa2aa38deb6d7401a`
- Release URL: `https://github.com/hjlee83/devbot/releases/tag/v0.1.0`

Task 033 bootstraps the first stable Release from the validated Task 032 `main` commit.
Earlier development is preserved below as project history, not as retrospective stable
Release tags.

## Development Milestones Through Task 032

### Foundation and Local MVP

- Task 000 established the development system, project rules, and validation convention.
- Tasks 001-005 built the local GitHub Issue driven MVP: bootstrap configuration,
  GitHub read access, global queue selection, workspace/Agent execution, and the main
  polling loop.

### GitHub Workflow Automation

- Tasks 006-009 added Issue state transitions, commit/push/PR delivery, PR feedback
  rework, and the first beta smoke path.
- Tasks 010-012 connected rework polling, split implementer/reviewer Agent roles, and
  introduced role-based polling orchestration.

### Review, State, and Reliability Hardening

- Tasks 013-020 added observability, workflow state-machine hardening, standardized
  review gates, no-op rework handling, timeline status protocol/CLI, daemon reliability,
  and queue summary logging.
- Tasks 021-027 hardened Agent outcome classification, Planner workflow rules,
  host-managed workspaces, automatic timeline recording, linked PR resolution, Agent
  resume/timeout recovery, and the autonomous IMPLEMENT -> REVIEW -> REWORK loop.

### Versioning, Workspace Validation, and Release Pipeline

- Task 028 added `devbot --version` backed by one authoritative package version source.
- Task 029 made validation run from the prepared workspace with explicit evidence.
- Task 030 added transient GitHub API retry behavior.
- Task 031 standardized Agent execution context, safe environment propagation, doctor
  readiness checks, and operator-main startup self-update.
- Task 032 added the automated release pipeline: semantic version policy, validated main
  gate, deterministic portable Python artifact, checksum manifest, draft-first stable
  publication, idempotent retry behavior, and minimal release permissions.

## Initial Release Notes

## devbot 0.1.0

Source commit: `6526cfea27e4a5fd0003df6eb698cd202dedcf57`

### What's New

- GitHub Issue driven task intake, branch preparation, implementation, review, rework,
  and ready-to-merge orchestration.
- Role-aware Agent execution for IMPLEMENT, REWORK, and REVIEW with a shared
  PreparedWorkspace contract.
- Portable Python release artifact with embedded package metadata and a deterministic
  SHA-256 checksum manifest.

### Improvements

- Timeline markers, queue summaries, structured failure categories, retry policy, and
  startup diagnostics make daemon operation auditable.
- Planner workflow artifacts now use a single Task Issue, branch, contract, Result, and
  Pull Request model.

### Fixes

- Hardened state transitions, duplicate feedback handling, no-op rework handling, and
  transient GitHub API retry behavior.
- Prepared workspace validation prevents operator checkout state from leaking into Task
  execution.

### Operational Changes

- Stable Releases are produced only from validated main commits after the Task 032
  validation gate succeeds.
- Release publication is draft-first, checksum verified, idempotent, and refuses to move
  existing tags.

### Upgrade Notes

- This is the first stable DevBot Release. Install or update from the published portable
  Python artifact and verify it against `SHA256SUMS`.

### Known Limitations

- Runtime automatic update discovery, package-manager distribution, launchd/systemd
  installers, and automatic merge remain out of scope.
