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

## 2026-07-15 — Queue summary reuses issue_to_task's state resolution; it does not add a second ambiguity rule
Task 020's queue summary counts each `IssueTask` into exactly one of six
stable-state buckets by summing `IssueTask.state` - the single `TaskState`
`devbot.polling.issue_to_task` (via `_matched_task_states`, first match in
`TaskState` declaration order: READY, WORKING, REVIEW, REWORK,
MANUAL_ACTION, BLOCKED, DONE) already resolves from an Issue's raw GitHub
labels for scheduling. This guarantees the summary can never double-count
an Issue across buckets (CP-020-8) without inventing a second resolution
rule that could disagree with what the scheduler itself sees. When an
Issue carries more than one `devbot:*` state label, `log_state_label_conflict`
(DEBUG) surfaces the anomaly and which state won - it does not change the
count. Note this first-match order is *not* the same precedence
`devbot.issue_state._current_state` uses when validating a label
*transition* (`_LABEL_PRECEDENCE`: DONE, BLOCKED, WORKING, MANUAL_ACTION,
REWORK, REVIEW, READY - closer to reverse order) - that pre-existing
inconsistency between candidate collection and transition validation is
unchanged by this Task and out of scope here; see Improvement Suggestions
in `results/020-daemon-queue-summary.md`.

## 2026-07-15 — Agent outcome classification closes the contract-only-PR false-review path
Task 021's motivating incident (Issue #41): an implementer Agent stopped
after reporting it needed approval to run Git/`gh` commands, DevBot
observed no repository changes, and because the Issue already had a linked
Task-contract-only PR (only the contract-authoring commit, no
implementation), `PollingService._run_claimed_implement_job` advanced the
Issue straight to `devbot:review` - a false-success transition with no
implementation ever having happened
(`devbot.polling`'s old unconditional `delivery_result.message ==
"no_repository_changes" and linked_pull_request is not None ->
mark_for_review` branch, previously asserted as *intentional* by
`test_implement_no_repository_changes_with_linked_pr_marks_review`, Task
016 CP-016-12).

`devbot.agent_outcome.classify_agent_outcome()` now turns every implementer
`AgentRunResult` into an explicit `AgentOutcome` *before* delivery is even
attempted, checking approval/session-limit/network-blocked/
repository-locked/implementation-skipped text patterns ahead of
`AgentRunResult.failed` (a genuine block can arrive from a process that
still exited 0). Only `implementation_completed` proceeds to delivery;
every other outcome resolves to `devbot:manual-action` or `devbot:blocked`
(never a new label, never `devbot:working`) with an operator recovery hint.

Separately, `devbot.polling` no longer treats a linked PR's mere existence
as proof of a completed implementation when delivery reports
`no_repository_changes`: it now also requires
`devbot.delivery.branch_has_implementation_evidence()` - more than one git
commit ahead of the repository's default branch on that PR's branch, i.e.
evidence beyond a single pre-existing contract-authoring commit - before
resuming review on the reused PR. Absent that evidence (a genuinely
never-implemented contract-only PR, or no linked PR at all), the Issue
moves to `devbot:manual-action` instead. This check is deliberately
conservative: any git failure (missing local ref, a workspace path that
does not exist) defaults to "no evidence", never to allowing review.

This does not introduce any new `devbot:*` label, and does not change
`devbot:working`'s transient-claim guarantee (Task 014) or the
`session_limit` -> `devbot:blocked` mapping (the 2026-07-15 "Agent
session-limit failures get a distinct block reason" entry above) - it only
makes the *evidence* required to leave `devbot:working` via delivery/review
explicit and typed instead of inferred from the absence of a problem.

## 2026-07-15 — Cycle result reports the first failure over a mixed cycle's successes
`devbot.polling._normalized_cycle_result()` scans `results` (candidate-
collection hard errors first, then executed Job results in selection
order) and returns the first genuine failure's `FailureCategory` if any
result failed, only falling back to a succeeded Job's `JobType` when none
did. With the default `max_concurrent_jobs=1` a cycle has at most one
result, so this only matters once concurrency is raised above 1 and two
different repositories' jobs disagree on outcome in the same cycle - a
case the Task 020 contract's examples do not cover. Surfacing the failure
prominently was chosen over surfacing whichever job happens to be first in
scheduling order, since a failure is normally the more actionable signal
for an operator glancing at one `Cycle Result` line.
