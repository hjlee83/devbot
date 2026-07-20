# State Machine

## Startup Recovery (CP-B1, 2026-07-18)

Right after acquiring `ProcessLock` (guaranteeing no other process sharing
this deployment's lock file can be mid-job), the daemon sweeps every
managed repository for Issues still labeled `devbot:working` and moves each
to `blocked` (`main.py:_sweep_stuck_working_issues`). A crashed process
(killed, OOM, host reboot) leaves the label behind - the kernel releases
`flock` on crash, but nothing else ever re-evaluates it - and a single
stuck `working` Issue otherwise excludes every other Issue in that
repository from every future poll cycle (`REPOSITORY_BUSY`). This only
self-heals on the next daemon **restart**, not the next poll cycle. See
`docs/07-decisions.md`.

## IDLE
No Issue is `working` or `review`.

- Query all enabled repositories.
- Select one `ready` Issue using the global priority rules.
- Atomically claim it by moving it to `working`.

## WORKING
The DevBot owns one Issue.

- Prepare branch and prompt.
- Run the agent.
- Run verification.
- Repeat repair until success or a defined stop condition.
- On success, create PR and move Issue to `review`.
- On metadata-only work (PR title/body/labels - not a repository commit), or an explicit request for human approval/authority ("사람"/"승인" wording), move Issue to `manual-action` without attempting.
- On unrecoverable failure, move Issue to `blocked`.

CP-B0 (2026-07-18): "CI"/network/dry-run-shaped wording is no longer
pre-classified from comment text - it's a technical claim the Agent can
attempt, so the Agent runs first and a genuine block is caught afterward via
`classify_agent_outcome()` (Task 021, extended to the rework path). Only
"사람"/"승인" wording pre-empts the attempt, since no post-attempt signal can
substitute for an explicit request for human authority. See
`docs/07-decisions.md`.

## REVIEW
No new Issue may start.

- If the linked PR head has not been auto-reviewed yet, dispatch REVIEW
  automatically.
- `REQUEST CHANGES` with repository-change scope moves to `rework` and makes
  REWORK runnable without a human command.
- `MERGE READY` keeps the Issue in `review`; if the result belongs to the
  current head and no safety gate fails, the linked PR receives the exclusive
  `devbot:ready-to-merge` label.
- If automatic merge is enabled, the repository is allowlisted, the repository
  is not marked as DevBot's self repository, and CI status for the PR head is
  confirmed green by at least one provider-neutral source (GitHub Actions
  workflow runs, combined commit status, or check-runs - see
  `docs/06-review-policy.md` and Issue #127 in `docs/07-decisions.md`),
  DevBot merges the PR and moves the Issue to `done`.
- If any automatic merge gate fails, DevBot logs/comments the reason, keeps
  `devbot:ready-to-merge`, and leaves the Issue in `review` for human merge.
- Stale, contradictory, exhausted, metadata-only, external-verification,
  unresolvable-linked-PR (CP-B1: closed/merged outside DevBot, or the Issue
  was reopened after its PR was already resolved - retrying can never fix
  this on its own), or otherwise unsafe review-loop outcomes move to
  `manual-action` without deleting the branch, PR, or worktree.
- Human merge of a ready PR also moves the Issue to `done` once recorded.

## BLOCKED
No automated retry unless explicitly returned to `ready`.

## MANUAL_ACTION
The Issue needs GitHub metadata work, external verification, or human
approval. It is not selected for automatic repository rework and does not
mean the Agent or delivery path failed.

## Autonomous review loop (Task 027)
The stable loop is:

`review` → REVIEW → `rework` → REWORK → `review`.

The loop is idempotent because REVIEW is keyed by the PR head marker
`<!-- devbot:auto-review head=<sha> -->`, and REWORK is keyed by unprocessed
`@devbot` PR comments without DevBot's processed reaction. The default review
attempt limit is 3 posted auto-review results per PR conversation, configurable
via the `REVIEW_LOOP_LIMIT` environment variable (`devbot.config`; source of
truth is `devbot.review.DEFAULT_REVIEW_LOOP_LIMIT`; `<= 0` means unlimited);
exceeding that limit requires `manual-action` and preserves all work.

## Failure classification, retry, and recovery policy (Task 019)
No new state or label is introduced by Task 019 - every `BLOCKED`
transition above is unchanged. `docs/11-daemon-reliability.md` documents
the failure-category taxonomy, retry policy, and the declarative recovery
mapping (`FailureCategory` -> `RESTORE`/`BLOCKED`) layered on top of these
same transitions for diagnostics and operator guidance.

## Agent outcome classification (Task 021)
No new state or label is introduced by Task 021 either - `WORKING`'s
"On success, create PR and move Issue to `review`" step now requires an
explicit `implementation_completed` `AgentOutcome` before delivery even
runs, and (when delivery finds nothing new to commit against a reused
linked PR) implementation evidence beyond that PR's pre-existing
Task-contract-authoring commit before actually moving to `review`. A
linked PR's mere existence is never itself that evidence. See
`docs/04-agent-system.md`'s "Agent outcome classification" section and
`docs/07-decisions.md`'s 2026-07-15 "Agent outcome classification closes
the contract-only-PR false-review path" entry.
