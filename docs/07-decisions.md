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
