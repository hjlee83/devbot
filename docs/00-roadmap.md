# DevBot Roadmap

## Phase 0 — Development system
- [x] Common project rules
- [x] Task contract format
- [x] Result and PR evidence format
- [x] Verification gate convention

## Phase 1 — Local MVP
- [x] Task 001: Bootstrap
- [x] Task 002: GitHub read client
- [x] Task 003: Global queue selection
- [x] Task 004: Workspace and agent runner
- [x] Task 005: Main polling loop

## Phase 2 — GitHub workflow
- [x] Task 006: Issue state transitions
- [x] Task 007: Commit, push, and PR creation
- [x] Task 008: PR feedback rework loop
- [x] Task 009: Beta smoke test (Task 001-008 wired into one flow via
      `PollingService.run_once`)
- [x] Task 010: ReworkService wired into the polling loop (`review`-state
      Issues' unprocessed `@devbot` PR comments are now detected and
      reworked automatically, reusing the existing branch/PR)
- [x] Task 011: `ClaudeRunner` added and Agent execution split into
      implementer/reviewer roles (default implementer=`claude`,
      reviewer=`codex`; unset role config falls back to the legacy
      `DEFAULT_AGENT`). Both the ready-Issue path and Task 010's rework
      path run through `implementer_runner`; `reviewer_runner` is
      constructed and injected but not yet invoked anywhere (automatic
      review execution is a later Task).

## Phase 3 — Operations
- [ ] macOS launchd
- [ ] VPS systemd migration
- [ ] Logging, recovery, and observability
