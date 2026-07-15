# Architecture Decision Log

## 2026-07-13 — GitHub as the queue
Use GitHub Issues, labels, comments, and PRs as the initial state store.
Do not introduce SQLite in the MVP.

## 2026-07-13 — One global active task
Even with multiple repositories, allow only one `working` or `review` Issue globally.

## 2026-07-13 — Project rules live in each repository
Each target repository stores project-specific rules in its root `AGENTS.md`.

## 2026-07-13 — Agent abstraction
DevBot knows an `AgentRunner` interface. Codex is the first implementation, not a permanent hard dependency.

## 2026-07-13 — Contract and quality gates are authored before implementation
The coordinating GPT writes Task contracts and checkpoint expectations.
The implementing AI writes code and tests, then opens a PR with evidence.

## 2026-07-14 — Rework is a separate stable Issue state
Use `devbot:rework` to represent PR feedback waiting for implementer
changes. `devbot:review` now means reviewer or human merge wait only.
`REQUEST CHANGES` moves `review -> working -> rework`; successful rework
moves `rework -> working -> review`.

## 2026-07-14 — Working must be transient
Any claim that reaches `devbot:working` must either restore the previous
stable state when preflight fails before Agent execution, or move to
`devbot:blocked` with a reason after execution, verification, delivery, or
unexpected failure. State writes normalize all `devbot:*` labels to one
state label and reject competing in-process claims for the same Issue.

## 2026-07-15 — Startup validation is informational, not a second fatal gate
Task 019's `run_startup_checks()` (workspace cleanliness, current branch
compatibility, repository/credential/timeline configuration) never aborts
daemon startup by itself - only the two conditions that already stopped
`devbot.main.main()` before this Task (`ConfigError`, `LockAcquisitionError`)
remain fatal. A dirty workspace or an unexpected branch on one managed
repository must not stop every other repository from polling; a repository
with zero enabled entries is already a valid idle deployment
(`PollingStatus.NO_MANAGED_REPOSITORIES`). `devbot doctor`'s daemon-lock
probe is the one check flagged `fatal=True` for its own `safe_to_start`
verdict, since it is the one condition ("another instance is already
running") that genuinely means "don't start". See
`docs/11-daemon-reliability.md`.

## 2026-07-15 — Agent session-limit failures get a distinct block reason, not a new state
A session/usage-limit Agent failure (`FailureCategory.AGENT_SESSION_LIMIT`)
still ends in `devbot:blocked` through the exact same transition as any
other execution failure (Task 014) - it does not introduce a new
`devbot:*` label. What changes is the blocking comment's text: a clear,
distinguishable recovery hint (`devbot.reliability.session_limit_block_reason`)
so an operator does not have to guess why automatic retry never happens.
This keeps the state machine unchanged while still satisfying "avoid
infinite retries" and "a clear recovery hint" (Task 019 CP-019-9).
