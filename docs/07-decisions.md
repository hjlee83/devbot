# Architecture Decision Log

## 2026-07-18 — Automatic merge is policy-gated and self-modification stays manual
B2 changes the old "merge is always manual" boundary into a policy gate, not
an unconditional automation step. A `MERGE READY` review may mark a PR with
`devbot:ready-to-merge`, but DevBot may merge it only when all of these are
true for the same PR head:

- **Global kill-switch is on.** `AUTOMERGE_ENABLED` defaults to `false`, so a
  new deployment cannot merge by accident.
- **Repository allowlist is explicit.** `automerge_allowed` defaults to
  `false` per repository.
- **Self-modification is excluded.** `is_self_repo: true` means DevBot is
  changing itself, and the PR always remains on the human approval rail.
- **CI is API-backed.** The gate uses GitHub check-runs for the PR head; review
  text mentioning "CI" is not evidence.

When any gate fails, the ready-to-merge PR is left in `devbot:review` with its
`devbot:ready-to-merge` PR label intact and a clear log/comment reason. That
preserves the existing human merge workflow instead of converting a policy
block into `manual-action` or deleting work. Only a successful GitHub merge API
call may move the Task Issue to `devbot:done`.

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

## 2026-07-15 — Host-managed worktrees are keyed by Issue number, reused across failures, and never auto-removed
Task 023's motivating incident (Issue #41 again, via Task 022's daemon
attempt): the Implementer reached a real Agent invocation but had to
discover the existing Task Branch/PR itself (`git fetch`/`gh`), which
needed interactive network approval DevBot could not answer. Three
narrower decisions inside `devbot.worktree.WorktreeManager` are worth
recording because they are not obvious from the Task 023 contract text
alone:

- **Keyed by GitHub Issue number, not Task number.** A Job always has an
  Issue number; the Task number is only recoverable by best-effort
  regex-parsing the Task Issue body Planner rendered
  (`devbot.planner.render_task_issue_body`'s `` - Contract: `...` ``
  line), which is not guaranteed for a manually authored Issue. Using the
  Issue number as the worktree directory name (`issue-<N>`) avoids that
  dependency entirely.
- **A `prepare()` call never wipes an existing worktree for the same
  Issue/branch, even a dirty one.** A failed Job's uncommitted changes are
  exactly the evidence an operator needs to diagnose what happened
  (Scope §8 "preserve on failure for diagnostics/recovery") - only a
  *different* branch reusing the same path, while dirty, is treated as an
  unsafe conflict and rejected.
- **No code path ever calls `WorktreeManager.cleanup()` automatically** -
  not even after a successful delivery. Automatic merge detection is
  explicitly out of scope for this Task, and a worktree still backs the
  open PR's branch until it merges, so removing it right after delivery
  would delete exactly the Branch/PR content Delivery just pushed to.
  Cleanup is only ever explicit (`devbot worktree cleanup`), left for a
  human or a later Task once merge detection exists.

Separately, `devbot.polling`'s pre-existing `ensure_workspace_ready`
dirty/branch check on the *operator* checkout is skipped entirely for
IMPLEMENT/REWORK Jobs once `prepare_workspace` is configured, replaced by
a lighter `devbot.workspace.ensure_repository_present` (exists + is a Git
checkout, no cleanliness requirement) - the operator checkout's branch and
uncommitted files no longer gate a Job at all, only the isolated worktree
does (CP-023-11). `PollingService.prepare_workspace` defaults to `None`,
so this whole path - and every existing test that does not opt in - is
unaffected; `devbot.main` always wires the real
`WorktreeManager.prepare` in production.

PR #44's first automated review (`hjlee83`, head `6b2ee07`) returned
`REQUEST CHANGES` on two points worth recording here because the fix
changed intended behavior, not just added a missing check:

- **Delivery now verifies the currently checked-out branch before
  `commit`/`push`** (`DeliveryService.current_branch`, Scope §7's
  "reject branch mismatch before commit or push"). The gap: `commit()`
  always commits to whatever branch is actually checked out - Git has no
  notion of "the branch the caller meant" - so an unverified mismatch
  between a prepared worktree's actual branch and delivery's resolved
  `target_branch` could commit real work onto the wrong branch and then
  push `target_branch`'s unrelated, unmoved ref while still reporting
  `delivered`.
- **A worktree that is clean but on the wrong branch is now a hard
  `branch_pr_mismatch` failure, not silently deleted and recreated.** The
  original `WorktreeManager._create_or_reuse()` treated "registered
  worktree, branch differs, not dirty" as safe to auto-heal (remove +
  recreate on the newly resolved branch). The review correctly pointed out
  the contract's `branch_pr_mismatch` category (Scope §9) had no real
  trigger path or test - and on reflection, silently discarding a
  worktree's identity because its linked branch changed is exactly the
  kind of implicit action Task 023's "no preparation failure may leave the
  Issue in `devbot:working` permanently, use explicit failure categories"
  principle argues against. It now raises instead, requiring an explicit
  `devbot worktree cleanup` before retrying - consistent with CP-023-8's
  broader "cleanup is always explicit" rule (this file's entry above), not
  an exception to it.

## 2026-07-18 — Rework action-scope classification: attempt first, don't pre-judge technical claims (CP-B0)

`devbot.rework.classify_rework_action_scope()` decides, from a reviewer's
comment text alone, whether an unprocessed `@devbot` rework request can be
handled by a repository commit or needs to skip straight to
`devbot:manual-action` without the implementer ever running. Its
`_EXTERNAL_VERIFICATION_PATTERNS` included a bare `"ci"` substring - but
`AGENTS.md`'s section 12 (리뷰 결과) mandates every Review Summary include a
"CI" content item, with no fixed markdown-header structure guaranteed (it
could render as a header, a bullet, or an inline sentence - there is no safe
delimiter to strip it out). The result: a properly-formatted, compliant
`REQUEST CHANGES` review was structurally almost guaranteed to
misclassify as `external-verification` and route to `devbot:manual-action`
before the implementer was ever invoked - reproduced live on
devbot/devbot#69 and #70, and observed repeatedly as "the Agent said it
can't do this" when the comment was in fact an ordinary, solvable code fix.

The fix is not a better keyword list - it's removing the pre-judgment for
claims that are actually verifiable by attempting them. `"ci"`,
`"github actions"`, `"check run"`, `"network"`, `"dry-run"`, `"dry run"`,
and `"external verification"` are dropped from
`_EXTERNAL_VERIFICATION_PATTERNS` entirely: these are all technical claims
("CI failed", "needs network access") that the implementer Agent can
attempt, so it now runs first, and `devbot.main._apply_rework_changes` uses
`devbot.agent_outcome.classify_agent_outcome()` (Task 021's real-execution
classifier, previously wired only into the initial IMPLEMENT job) to catch
a genuine block afterward - raising the new `AgentOutcomeError`, caught by
`ReworkService.process()` and routed to `manual-action`/`blocked` with the
transition table's precise recovery hint. Only `"사람"`/`"승인"` remain in
`_EXTERNAL_VERIFICATION_PATTERNS`: an explicit claim that human authority is
required is not something a real attempt can discover or disprove, so
pre-classification is the correct (and only available) mechanism there.

This also closes an independent Issue #41-class false-success gap on the
rework path specifically: `_apply_rework_changes` previously only checked
`result.failed`, so an Agent that exited 0 but whose own output said it
needed approval (or was network-blocked, etc.) was silently treated as a
successful rework with nothing having actually changed.

One implementation note worth recording: a word-boundary regex
(`\bci\b`) was tried as defense-in-depth for the remaining patterns and
reverted - this codebase's review/rework text freely mixes Korean and
English (e.g. "PR body를", "label을", "사람이"), and Korean case/topic
particles fuse directly onto the *preceding* token with no whitespace,
including an English one. `\b` never matches at that fusion point
regardless of which script the pattern is in (confirmed empirically:
neither `\bpr evidence\b` nor `\blabel\b` nor `\b사람\b` matches text with
an attached Korean particle), so word-boundary matching is actively unsafe
here. Plain casefolded substring containment - the original mechanism,
just with the one structurally-guaranteed-to-collide token removed - is the
correct fix.

`review.py`'s `DEFAULT_REVIEW_LOOP_LIMIT = 3` was also, separately, wired
into `devbot.config`/`devbot.models` as the `REVIEW_LOOP_LIMIT` environment
variable (previously hardcoded, not configurable without a code change).

## 2026-07-18 — Startup self-update: bypass only a dirty operator checkout, only under dry-run (CP-B0-1)

`devbot.startup.run_startup_self_update()` was already a third fatal
startup condition alongside `ConfigError`/`LockAcquisitionError` (see
`docs/11-daemon-reliability.md` §4-1, added by this same entry) - the
2026-07-15 "Startup validation is informational" entry above describes
`run_startup_checks()` only and predates this gate; it is not being
corrected here, just superseded in scope by this one.

The gate's problem was not that it existed, but that it collapsed every
failure reason into one fatal outcome with no bypass: `git status
--porcelain` reporting a single uncommitted file (any file, tracked or
untracked, outside `.worktrees/`) aborted the entire run before the
polling cycle ever started - daemon, `--once`, dry-run, all alike. In
practice this meant a developer doing ordinary manual work on the devbot
repository itself (exactly the mode `docs/14-autonomy-first-roadmap.md`'s
Phase A/B0/B0-1 work happens in) could not run so much as a `--dry-run`
smoke test without first `git stash`-ing.

The fix adds a machine-matchable `StartupSelfUpdateResult.reason_code`
(`dirty_checkout`, `wrong_branch`, `status_check_failed`, `fetch_failed`,
`switch_failed`, `pull_failed`, `current_sha_failed`) and a new
`_run_startup_self_update(..., allow_dirty_skip: bool = False)` parameter.
Only `reason_code == "dirty_checkout"`, and only when the caller opts in,
is downgraded to a warning and treated as non-fatal. The daemon/`--once`
call site passes `allow_dirty_skip=config.dry_run` - since `DRY_RUN`
defaults to `"true"` (`devbot.config`), only a deployment that has
explicitly set `DRY_RUN=false` (opted into real writes) keeps the gate
unconditionally strict. Every other `reason_code` stays fatal regardless of
dry-run, since those indicate a more serious problem (wrong branch,
network failure, ...) an operator should see immediately rather than run
past silently. `devbot doctor` (without `--ci`) is deliberately *not*
given this bypass - its job is accurate diagnosis, not convenience, so it
keeps reporting a dirty checkout strictly.

Two related, independently-discovered safety gaps in the same Phase A/B0-1
investigation were fixed in the same pass:

- **`WorktreeManager` had no `dry_run` awareness at all** (every sibling
  service in `devbot.main`'s startup wiring - `DeliveryService`,
  `ReworkService`, `ReviewService`, `IssueStateWriter`, `TimelineService` -
  already receives `dry_run=config.dry_run`; `WorktreeManager` was the one
  outlier). Its `_sync_task_branch_with_main()` could run a real `git
  rebase` and, if that moved HEAD, a real `git push --force-with-lease` to
  the remote Task branch - regardless of `dry_run` - for REWORK jobs and
  IMPLEMENT jobs with an already-existing linked PR. Fixed by adding
  `dry_run: bool = False` to `WorktreeManager` and returning before the
  rebase (mirroring `DeliveryService.deliver()`'s existing precedent: run
  the non-mutating checks for real, then stop before the first local
  mutation under dry_run).
- **Timeline `dev:start`/`dev:end` pairing ignored `dry_run`.**
  `TimelineService`'s only `dry_run` check gates the final GitHub comment
  write; the "end without a matching start" validation ran unconditionally
  against freshly-fetched real state. Since `dry_run` also silently
  suppresses `start()`'s own write, a dry-run `start()`→`end()` pair always
  logged a spurious `TimelineMissingStartError` warning (via `safe_end`,
  which swallows it - never a functional bug, just log noise on every
  dry-run cycle). Fixed by guarding only the `raise` itself with `if not
  self.dry_run`, not the whole pairing block, so a dry-run `end()` against
  an Issue with *real* prior history still correctly honors the existing
  idempotent-duplicate-end passthrough above it.

## 2026-07-18 — Startup sweep for crashed-process `devbot:working`, and manual-action escalation for an unresolvable linked PR (CP-B1)

Live inspection of `hjlee83/devbot`'s own Issue history (`docs/14-autonomy-first-roadmap.md`'s B1 re-scoping) surfaced two real loop-stalling gaps beyond the label hygiene originally suspected.

**Crashed-process `devbot:working` never self-heals.** `docs/08-beta-runbook.md`'s Task 014 claim ("no failure path should leave an Issue permanently stuck in `devbot:working`") only ever held for *in-process* failures - every code path inside a running job reaches `block()`/`restore()`. A *process* dying uncleanly (killed, OOM, host reboot) is different: the kernel releases `ProcessLock`'s `flock` immediately, so the next daemon start acquires the lock fine, but the GitHub label is untouched, and `polling.PollingService._collect_job_candidates` excludes every other Issue in that repository too whenever any Issue is `working` (`REPOSITORY_BUSY`) - so one crash silently stalls an entire repository's queue forever, with no log signal beyond a repeated exclusion line.

The fix (`main.py:_sweep_stuck_working_issues`) exploits a fact that was already true but unused: successfully acquiring `ProcessLock` *is* a proof that no other process sharing this deployment's lock file can be mid-job right now (the documented one-lock-file-per-deployment convention, `docs/08-beta-runbook.md`). So right after the lock is held, the daemon lists every `devbot:working` Issue across managed repositories and moves each to `devbot:blocked` via the existing `block()` transition - reusing infrastructure, not inventing a new one. This must live inside `main.py`'s `with ProcessLock(...):` block specifically, not `devbot.startup` - `devbot doctor` reuses `devbot.startup.run_startup_checks()` without ever holding the lock, and moving the sweep there would silently break the "lock held = no other live process" invariant the whole design depends on.

**A REVIEW/REWORK Issue with no resolvable linked PR looped forever.** `polling._fetch_linked_pull_request_and_comments` already had two well-formed failure branches for this (a Planner Issue's declared PR unresolvable, or a plain Issue with nothing referencing it) - both logged a structured error every cycle and never touched a label. Unlike almost every other unrecoverable condition in `review.py`/`rework.py`, neither escalated to `devbot:manual-action`, so an Issue whose PR was closed/merged outside DevBot (or an Issue reopened after its PR had already been resolved and pruned from the open-PR list) repeated the identical failure indefinitely with no operator signal beyond a log line.

The fix escalates both branches to `manual-action` via the same `claim()`-then-`require_manual_action()` idiom already used twice elsewhere for structurally-unresolvable states (`review.py`'s review-loop-attempt-limit-exceeded path, `rework.py`'s non-repository-change-scope path) - `review`/`rework` cannot transition directly to `manual-action` (`issue_state._ALLOWED_TRANSITIONS` only allows it from `working`), so claiming first is required, not optional. This needed its own local `try/except` around the write, unlike its two precedents - both of those already run inside `_execute_job`'s outer catch-all, while this fires during candidate *collection*, which has no equivalent enclosing net. A transient `list_pull_requests` API failure (the third, pre-existing branch of the same function) is deliberately left unescalated - that's a legitimately retryable condition, not a structural one.

**Deliberately deferred, not fixed in this pass** (tracked in `docs/14-autonomy-first-roadmap.md`'s B1 backlog): `devbot:done` is still never written (merge detection remains explicitly out of scope, per the 2026-07-15 "Host-managed worktrees" entry above); a narrow race where a human's mid-job manual label fix can be silently clobbered by DevBot's own stale-snapshot write; `_run_review_job`/`_run_rework_job` only checking `TaskState.BLOCKED` (not `MANUAL_ACTION`) when deciding whether a cycle counts as a failure, so `review.py`'s/`rework.py`'s own service-level manual-action paths still don't surface as `PollingStatus` failures; and IMPLEMENT jobs hitting the identical "Planner PR unresolvable" condition still loop back to `ready` forever rather than escalating (a structurally identical gap, but a different code path, out of scope here).

## 2026-07-18 — Operator release publishing dispatches the existing workflow and never builds Releases itself (Task 037)

Task 032's `.github/workflows/release.yml` already supports `workflow_dispatch` for
manual/recovery releases, but the operator still has to know the exact validated `main`
commit SHA, pick `patch`/`minor`/`major` from a dropdown, and accepts whatever
`manual_release_plan()` generates as notes - a single line, e.g. `v0.1.0`'s real
published body is literally `- patch: manual release`. `devbot release preview|publish|
status` (`src/devbot/release_ops.py`) removes all three manual inputs without adding a
second way to create a Release.

**`release_ops.py` only ever calls `GitHubWriteClient.dispatch_workflow` - no
`create_release`/`create_tag` method exists.** `publish_release()`'s only write is a
`workflow_dispatch` POST with computed `increment`/`commit_sha`/`notes` inputs; it then
polls the run to completion and validates the *result* (tag, Release, assets,
`SHA256SUMS`) against the plan it computed beforehand. A workflow failure or timeout is
reported (with the run URL) and raises `ReleaseOpsError` - there is no local fallback
path that creates a tag or Release directly, by construction, not just by convention.

**The next version is computed by aggregating every merged main PR since the last
stable Release, not just the one PR a push event carries.** The existing push-triggered
path (`release_plan_for_pr`) only ever sees the single PR that triggered it. An
operator-invoked "publish the next stable release" instead walks every commit between
the last stable Release's target commit and the latest CI-validated `main` commit
(`GitHubClient.compare_commits`, falling back to full history via `list_commits` when
no prior stable Release exists), resolves each commit's merged PR
(`get_commit_pull_request_metadata`), and takes the maximum `release:*` increment
(`major` > `minor` > `patch`) as the overall bump - reusing `release_increment_for_pr`'s
existing "exactly one label or fail closed" rule per PR, so an unlabeled or
multiply-labeled PR blocks readiness instead of being silently skipped.

**Release Notes are generated from structured PR data only, never invented prose.**
`release.aggregate_release_notes()` buckets each `ChangeEntry` into one of the six
standard sections purely by its `release:*` label (`major`/`minor` -> What's New,
`patch` -> Fixes, `none` -> Operational Changes) and renders `- <label>: #<PR number>
<PR title>` under both a Korean and an English heading - the same literal PR data in
both halves, so the two languages are guaranteed to describe identical changes without
attempting machine translation of prose. This is intentionally coarser than
hand-written notes (there is no `Improvements` section content, for example) in
exchange for being fully deterministic and auditable back to `merged PRs, Task
contracts, Result documents`.

## 2026-07-19 — A second, direct release-publish path is added deliberately, alongside (not replacing) the workflow-dispatch path (Task 049)

The entry above states that `release_ops.py` has "no local fallback path that creates a
tag or Release directly, by construction, not just by convention" - that guarantee no
longer describes the whole repository. Task 049 (`src/devbot/release_publish.py`)
implements exactly that second path: DevBot's own process creates and pushes an
annotated Git tag, then calls `GitHubWriteClient.create_release` directly, without
dispatching or waiting on `.github/workflows/release.yml`. This section documents why
the second path was added and how the two are kept from conflicting with each other.

**This was a deliberate, discussed decision, not an oversight.** Task 049's own Task
Contract and Specification explicitly required "Create and push the Git tag" and
"Create the GitHub Release using explicit release notes supplied by the caller" -
requirements that only make sense for a direct, two-step local publish (its own
required-tests list includes "GitHub Release creation failure after successful tag
push," a failure mode that cannot occur from DevBot's perspective when it only
dispatches a workflow that does both steps atomically inside one CI job). Implementing
Task 049 as written meant knowingly introducing the second path the original ADR was
written to prevent. The operator confirmed this tradeoff explicitly before
implementation began, having weighed how an incomplete/still-maturing implementation
should fail: never silently claim success, never auto-delete or force-move a real
pushed tag, and always leave a safe, explicit retry path - see Task 049's "Publication
Order and Partial Failure" and "Idempotency" sections
(`specifications/049-release-publish.md`), which `release_publish.py` implements
exactly.

**The two paths are named distinctly so they are never invoked interchangeably.**
`devbot release publish` (Task 037, `release_ops.py`) is unchanged: it still only
calls `dispatch_workflow`, builds artifacts, and waits on CI. The new path is
`devbot release publish-prepared` (Task 049, `release_publish.py`) - a different
subcommand with a different, non-overlapping flag set (`--notes-file` is required;
there is no `--poll-interval-seconds`/`--timeout-seconds`, since there is no workflow
run to wait on). Neither command's code path can invoke the other's write operation.

**The new path reuses the version Task 048 already prepared - it never computes one.**
`release_publish.read_current_version` (promoted from `release_preparation`, the same
function `plan_release_preparation` itself uses) is the only version source Task 049
reads; it is never allowed to calculate a version like Task 048 does. `pyproject.toml`
and `uv.lock` are read-only from Task 049's perspective (regression-tested).

**Never overwrite, never force, never silently repair.** An existing tag or Release
that disagrees with the verified target commit fails closed
(`ConflictingTagError`/`ConflictingReleaseError`); no `git` invocation in
`release_publish.py` ever passes `-f`/`--force` (statically and behaviourally
regression-tested). If the tag push succeeds but Release creation fails,
`PartialPublicationError` identifies the pushed tag and the tag is never deleted -
`publish_prepared_release` is safe to call again and will only create the missing
Release, never re-tag or move it.

**Tests never touch a real GitHub remote or the real devbot tag/Release history.**
`tests/test_release_publish.py` runs real `git tag`/`git push` against a throwaway
local bare repository created and destroyed per test (`tmp_path`) standing in for
`origin` - this exercises actual `git` semantics (annotated-tag dereferencing, `git
push` failure modes) more rigorously than mocking `subprocess` would, while never
creating a tag or Release that touches this project's real history. All GitHub API
interaction (`GitHubClient`/`GitHubWriteClient`) is a `MagicMock` with no network
access.

**`release.yml` gained one new optional `workflow_dispatch` input, `notes`, rather than
a second workflow or a post-hoc `gh release edit`.** The existing manual dispatch path
(`manual_release_plan`) still defaults to its one-line notes when `notes` is absent or
blank (`scripts/release_pipeline.py plan --notes-file`, gated on `[ -s
release-notes-override.md ]`), so an operator recovering a release by hand through the
GitHub Actions UI is unaffected. `devbot release publish` always supplies the full
bilingual body it generated, so the created Release never has a placeholder body even
transiently - avoiding a second write (edit-after-publish) that would have to be its
own safety-gated operation.

**Publish refuses, without touching GitHub, on any of: a dirty local checkout
(`git status --porcelain` on the resolved operator checkout, `devbot.startup
.resolve_operator_checkout`), the latest `main` commit missing an all-success
check-run set, a stable Release already existing for that commit, any commit in range
without a resolvable single-label merged PR, or generated notes that are empty.** All
of `ReleaseReadiness.blockers` is computed by the pure `build_release_preview()` before
`dispatch_release()` ever runs, and `devbot release preview` renders the identical
computation read-only - so "what would publish do" and "what does it actually check
before publishing" can never drift apart.

## 2026-07-19 — One authoritative release publish strategy per repository, resolved in exactly one place (Task 050)

Task 049's entry above closes with "두 개의 릴리스 게시 경로가 이제 공존한다" as an
open risk: `devbot release publish` (workflow-dispatch, `release_ops.py`) and `devbot
release publish-prepared` (direct tag+Release, `release_publish.py`) both exist, and
nothing yet stopped an operator from running either one against a repository regardless
of which path that repository was actually meant to use. Task 050
(`src/devbot/release_publish_strategy.py`) closes that gap with a policy layer, not by
removing either path - both remain fully intact and independently useful.

**A repository declares its strategy with one optional config field; an omitted field
is not ambiguity, it is the documented default.** `RepositoryConfig.publish_strategy:
str | None` is new in `repositories.yaml`. `None` resolves to `ReleasePublishStrategy
.WORKFLOW` - the path every existing repository was already using before this task
existed, so every config file written before Task 050 continues to load and behave
exactly as before with zero edits required (regression-tested against `tests
/test_config.py`'s full existing fixture set).

**Exactly one function parses and defaults the raw config value; nothing else is allowed
to.** `resolve_release_publish_strategy()` is pure (no I/O), and is the only code in the
repository that reads `RepositoryConfig.publish_strategy` and turns it into a typed
`ReleasePublishStrategy`. `RepositoryConfig` itself and `config.py`'s loader deliberately
keep the field as an untyped, unvalidated `str | None` - validating or defaulting it at
the config-loading boundary would create a second place this logic could drift from the
resolver. An unrecognized value (anything other than exactly `"workflow"` or `"direct"`,
including case variants, surrounding whitespace, or a non-string) always raises
`InvalidReleasePublishStrategyError` - it is never silently coerced to the default,
because a typo that quietly fell back to `workflow` is exactly the kind of ambiguity this
task exists to remove.

**Two guard functions make the two paths mutually exclusive by construction, not by
convention.** `require_workflow_strategy()` and `require_direct_strategy()` both call
the same resolver and then compare its result against what the caller requires; for any
possible configuration (omitted, `workflow`, `direct`, or invalid) at most one of the two
guards can ever succeed, and an invalid configuration fails both
(`tests/test_release_publish_strategy.py::test_mutual_exclusivity_matrix` parametrizes
over all three valid states and asserts this directly). Neither guard performs any I/O
beyond reading the already-loaded `RepositoryConfig`.

**Guards sit at the actual write choke point of each module, not at whichever function
felt convenient.** `release_ops.dispatch_release()` calls `require_workflow_strategy()`
as its first line - its own pre-existing docstring already identified it as "the single
safety choke point every `devbot release publish` path goes through," so this is the one
place a guard covers every caller, present and future, without being bypassable.
`release_publish.preview_release_publish()` calls `require_direct_strategy()` as its
first line for the same reason: `publish_prepared_release()` always calls
`preview_release_publish()` before any write, so gating the shared preview covers both
the read-only preview and the real publish call with no duplicated check.
`release_ops.py`'s equivalent preview function (`fetch_release_preview`) could not host
the guard the same way, because it is shared with the ungated, read-only `devbot release
preview` command - gating it there would have incorrectly blocked `release preview` too.
That one asymmetry is why `devbot.main`'s CLI layer also carries an explicit,
`publish`-scoped guard check before `GitHubClient` is even constructed: it is
intentional duplication at the one point where the domain-level guard alone would not
give a fast, network-free CLI refusal for that specific command.

**Strategy inspection is read-only and never constructs a GitHub client.** `devbot
release strategy` calls only `resolve_release_publish_strategy()` and prints its result;
it exists so an operator (or a future orchestration task) can ask "which path would this
repository actually use" without side effects, matching the read-only posture of `devbot
release preview`/`status`.

**Explicitly out of scope, left for later tasks:** this task does not touch version
calculation, does not perform any tag/Release/workflow-dispatch write itself, does not
merge the two publish commands into one, and does not remove either existing path -
`devbot release publish` and `devbot release publish-prepared` both remain, gated so that
only one is ever valid per repository.

## 2026-07-19 — `devbot release run`'s plan is anchored to the latest published Release, not to local file content, for both routes (Task 051)

Task 051 (`src/devbot/release_orchestration.py`) adds `devbot release run --level
major|minor|patch|none`, composing Task 047's `ReleaseRecommendation`, Task 048's
preparation, Task 050's strategy resolution, and exactly one of Task 037's or Task 049's
publish paths. The first implementation (PR #108) was reviewed and found to have two
real correctness gaps; both were fixed on the same PR, and this entry replaces the
original 2026-07-19 Task 051 entry with the design that actually shipped.

**Gap 1: the workflow route silently ignored `--level`.** The original implementation
skipped Task 048 preparation entirely for the workflow route and dispatched through
Task 037 unchanged - but Task 037 independently computes its own release increment from
merged PR labels since the last stable Release (`fetch_release_preview`), never reading
`--level` at all. `devbot release run --level patch` could therefore publish a `minor`
or `major` increment, or refuse, regardless of what the operator asked for - and the
rendered dry-run plan, computed from Task 048's local-file logic, had no way to reflect
that divergence. **Fix:** `build_release_run_plan` now fetches the same
`ReleasePreview` the workflow route would actually dispatch, and reports the plan's
`current_version`/`target_version` from `preview.previous_version`/`preview.next_version`
- what Task 037 will actually publish, not what a local version bump would compute. If
`preview.increment` does not match the requested `recommendation`, `recommendation
_conflict` is set: dry-run reports it as a blocker, real execution
(`run_release`) refuses before any write. `prepare_release()` is now also called
(exactly once) for the workflow route, satisfying the Specification's "Task 048
preparation exactly once" literally for both routes, even though Task 037 never
consumes the written files.

**Gap 2: `PREPARED_PENDING_COMMIT` as "the normal direct outcome" did not meet the
completion contract.** The original implementation anchored the direct route's target
version to whatever `plan_release_preparation` computed from *local* file content -
which is always exactly one increment ahead of local `current_version`, by construction
(`calculate_next_version` never returns its input unchanged). That made "is the
checkout already prepared for this target" undecidable across separate invocations: a
second invocation's freshly-recomputed target was always one *further* increment ahead
of whatever the first invocation had just written, so real execution could never
recognize its own completed preparation and always stopped short of publishing.
**Fix:** the direct route's `current_version`/`target_version` are now anchored to
`build_release_status(...).latest_stable_version` (the GitHub Releases API - the same
source `devbot release status` already reads) rather than to local file content. That
baseline does not change merely because a local `prepare_release()` write happened; it
only changes once an actual Release is published. `run_release` compares the local
checkout's current version and dirtiness against this stable target: when they already
match, `prepare_release()` is skipped entirely and the direct publish proceeds for
real, right away. When they don't, `prepare_release()` runs (exactly once) and
`run_release` reports `PREPARED_PENDING_COMMIT` - the operator commits and pushes the
prepared files out of band, then a *second* real invocation with the *same*
`recommendation` now finds the checkout already matching the (unchanged) target and
completes publication for real, without recomputing a further bump. A defensive
consistency check compares `prepare_release()`'s actual output against the plan's
target and fails closed (a `preparation`-stage error) if a stale or inconsistent local
checkout would otherwise cause a different version to be prepared than the plan
promised.

**A new capability - `run_release` committing/pushing to `main` itself, to close both
routes in exactly one call - was considered again during review and rejected again**,
for the same reason as the first attempt: it is nowhere authorized by Task 051's
Contract or Specification, and is a high-blast-radius, hard-to-reverse capability on a
self-hosted repository that manages its own release history. The chosen design instead
makes the *existing* two-invocation resumption path in the first attempt actually work
correctly (real, verified `DIRECT_PUBLISHED` on the second invocation - it did not
before), rather than adding a new write capability to collapse it to one.

**Planning now performs read-only GitHub calls.** `build_release_run_plan` (used by
both `--dry-run` and as the first step of real execution) calls `fetch_release_preview`
(workflow route) or `build_release_status` (direct route) - both read-only, never
writes. This is a deliberate, necessary use of the Specification's allowance to create
a network client during dry-run "when an existing read-only plan absolutely requires
one": without it, the plan cannot truthfully describe what real execution would do,
which is exactly the defect this rework fixes. A `GitHubWriteClient` is still never
constructed during dry-run.

**Regression coverage added for both fixes:**
`tests/test_release_orchestration.py::test_run_release_workflow_route_real_repo_recommendation_governs_dispatch`
proves a `--level patch` request against an independently-computed `minor` increment
refuses before any write, against a real throwaway Git repository; `test_run_release
_direct_route_real_resume_reaches_direct_published_without_double_bump` proves two real,
sequential `run_release` invocations (with an out-of-band commit/push between them) reach
`PREPARED_PENDING_COMMIT` then genuinely `DIRECT_PUBLISHED`, exercising the real
`publish_prepared_release`/`GitHubWriteClient.create_release` path with exactly one
Release created.

**Re-review found a third, related gap in the same PR: matching `recommendation` against
`preview.increment` was not sufficient, because `prepare_release()`'s actual local
output was never checked against what `preview` would actually publish.** These are two
independently-computed baselines - `prepare_release()` derives its target from local
`pyproject.toml`/`uv.lock`, `preview` derives its target from the latest *published*
Release - that can agree on `increment` while still disagreeing on the exact version,
whenever local files are already ahead of (or behind) the latest publication for an
unrelated reason. This was confirmed non-theoretical against this very repository:
`pyproject.toml` read `0.1.2` while the latest published Release was `v0.1.1` at the
time of review. `run_release` now compares `preparation_result.new_version` against
`preview.next_version` immediately after preparing and refuses, before any dispatch,
when they differ - `preparation` stage, no exception chained (this is a consistency
violation detected after the fact, not a wrapped downstream failure). Regression tests:
`test_run_release_workflow_route_local_preparation_diverges_from_published_baseline`
(mocked wiring) and `test_run_release_workflow_route_real_repo_local_ahead_of_published
_baseline_refuses` (real throwaway Git checkout, mirroring the actual repository's
observed drift) both confirm `publish_release` is never called when the two baselines
disagree.

## 2026-07-19 — Release recommendation aggregation resolves PRs to Contracts by branch name; two historical Contracts were found and fixed to match Task 046's own parser (Task 052)

Task 052 (`src/devbot/release_recommendation_aggregation.py`) adds `devbot release
recommend`, a read-only command that aggregates every Task PR merged since the latest
stable GitHub Release into one authoritative `ReleaseRecommendation`, by resolving each
PR to its Task Contract and reusing Task 046's `parse_contract_metadata` and Task 047's
`classify_release` - never inferring impact from PR titles, labels, or diffs.

**Boundary-finding and PR enumeration reuse `release_ops.gather_release_context`
unchanged**, the same function Tasks 037/048/051 already rely on. Recomputing "the
latest reachable stable Release" and "every commit merged into the default branch since
it" independently would have duplicated policy this Task's own Specification forbids
duplicating.

**There was no existing end-to-end "PR → Contract" mechanism to reuse.**
`devbot.planner` only generates the naming convention (`task/<NNN>-<slug>` branches,
`tasks/<NNN>-<slug>.md` Contracts - `docs/12-planner-workflow.md` §4); `devbot.worktree`'s
parsers target Task *Issue* bodies, not PR bodies, and are explicitly documented as
best-effort, not authoritative, in the 2026-07-15 worktree ADR entry above (a manually
authored Issue or PR - or anything predating Task 022 - won't follow the template at
all). Task 052 treats a PR's head branch name as the primary, required signal: a branch
matching `task/<NNN>-<slug>` implies the Contract lives at the canonical path
(`devbot.planner.canonical_contract_path`, reused, not reimplemented). A branch that
doesn't match was never a Task branch and is recorded as an explicit, typed exclusion -
never silently dropped. A branch that does match but whose Contract does not exist at
that path, at the merge commit, is a genuine inconsistency and fails the entire
aggregation closed. A Planner-rendered PR body's own `## Contract` declaration, when
present, is cross-checked against the branch-derived path as a second, independent
signal; disagreement is an ambiguity error - fail closed, never guess which one is
right.

**A new `GitHubClient.get_pull_request` method was added** because no existing method
returns a merged PR's `merge_commit_sha`, `merged_at`, `body`, and `html_url` together -
`list_pull_requests`'s `PullRequest` and `get_commit_pull_request_metadata`'s
`PullRequestMetadata` each carry only a subset. This is the only GitHub-client change
this Task required.

**A real, non-theoretical Contract format inconsistency was discovered and fixed while
verifying this Task against the actual repository.** Running `parse_contract_metadata`
directly against every recent Contract file showed `tasks/050-release-publish-strategy.md`
and `tasks/051-release-orchestration.md` both raise `MalformedContractVersionError` -
their `## Contract Version` section body reads `- contract_version: 1` (bulleted, like
every other metadata field), but the parser's `_VERSION_RE` requires the section body to
be *only* a bare positive integer, matching Task 046's own Contract (`tasks/046-contract
-metadata-engine.md`, body reads exactly `1`) and Tasks 048/049 (also bare integers,
already correct). This means, as merged, running `devbot release recommend` against this
repository right now would fail closed the moment it reached Task 050's PR - not a bug
in Task 052's code (fail-closed on a malformed Contract is exactly its Specification's
required behavior), but a real, immediate practical blocker.

**Resolution, confirmed with the operator before proceeding:** Task 052's own code was
left unchanged - fail-closed-on-malformed-Contract is correct, working as designed, and
this discovery is exactly the kind of data-quality problem that behavior exists to
catch. `tasks/050-release-publish-strategy.md` and `tasks/051-release-orchestration.md`
were corrected in place (`- contract_version: 1` → `1`, a pure format fix with no
semantic change), as a small, transparently-flagged drive-by fix bundled into this
Task's PR. The corresponding embedded "Full Task Contract Reference" copies inside
`specifications/050-release-publish-strategy.md` and `specifications/051-release
-orchestration.md` were updated identically and re-verified byte-for-byte against the
corrected Contract files, and both Specifications were re-validated
(`devbot specification validate --task 50`/`--task 51`, both still PASS).

## 2026-07-19 — GitHub review submission: stale-head and self-approval are checked read-only, before any write client exists (Task 054)

Task 054 (`src/devbot/github_review_submission.py`) submits a validated Task 053
`ReviewReport` as exactly one official GitHub PR review. Marked `risk_level: high` in
its own Contract, since this is the first Task in this repository that writes a real
GitHub review rather than an Issue/PR comment, label, or Release. Three safety
decisions are worth recording explicitly.

**Stale-head protection and self-approval detection are both read-only, and both run
inside the same `build_github_review_submission_plan` function that dry-run and real
submission both call first.** A report only carries evidence about one exact commit
(`reviewed_head_sha`, required in its `metadata`); the PR's *current* head SHA is read
via the already-existing `GitHubClient.get_pull_request` before anything else happens,
and a mismatch refuses before any `GitHubWriteClient` is constructed. Because dry-run
and real submission share this one function, there is no way for the two paths to
enforce this protection differently - what dry-run reports as "the exact intended
submission" is guaranteed to be what real execution would actually attempt.

**Self-approval is checked proactively, not only reactively.** GitHub's API rejects
`APPROVE` when the authenticated identity authored the PR (HTTP 422), but relying only
on that after the fact means an operator only learns why from an opaque GitHub error
string - and the Specification explicitly forbids silently downgrading a failed
`APPROVE` into `COMMENT` to work around it. `build_github_review_submission_plan`
therefore compares the authenticated user (`GitHubClient.get_authenticated_user`,
read-only) against the PR's author *before* returning a plan whose event is `APPROVE` -
raising `SelfApprovalError` early, with no write attempted. This check is scoped to
`APPROVE` only, matching the Specification's own language ("self-approval", "refusing
approval") - it does not extend to `REQUEST_CHANGES`/`COMMENT`, and does not call
`get_authenticated_user` at all for those events, avoiding an unnecessary read. As a
second, independent layer, `submit_github_review` also recognizes GitHub's own 422
self-approval message text if the proactive check somehow did not catch it (e.g. the
authenticated identity changed between planning and submission) and raises the same
typed error rather than letting it surface as an undifferentiated API failure.

**An inline comment location that is only partially usable fails the whole submission
closed, rather than silently falling back to body-only.** A finding with no location at
all, or a location missing either `path` or `line`, is not an error - GitHub simply has
nothing to anchor a comment to, so it stays body-only (this is the common, expected
case, not a failure). But a finding whose `side` is set to something other than
GitHub's two accepted values (`LEFT`/`RIGHT`) is a different situation: the data
*looks* complete enough to place a comment, and doing so with a wrong or nonsensical
side risks silently mis-anchoring it to the wrong half of a diff. `UnsupportedInline
LocationError` refuses the entire submission in that case rather than guessing which
side was intended or quietly dropping the finding to the body only. Independently of
inline placement, every finding is always also written into the top-level review body -
so even a successfully inline-placed finding is never the *only* place it appears,
guarding against GitHub itself failing to render an inline comment for any reason.

**`GitHubClient.PullRequestDetail` gained `head_sha`, `state`, and `author_login`
(Task 052 introduced the type without them - only `merge_commit_sha`/`merged_at`,
useful once a PR is already merged, existed).** `GitHubWriteClient.submit_pull_request
_review` is the one new write method, following the same shape as every other write
method in that client (`create_pull_request`, `merge_pull_request`, ...): it owns no
policy of its own and only transports the caller's already-validated `commit_id`/
`event`/`body`/`comments`.

