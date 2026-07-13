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

## Phase 3 — Operations
- [ ] macOS launchd
- [ ] VPS systemd migration
- [ ] Logging, recovery, and observability
