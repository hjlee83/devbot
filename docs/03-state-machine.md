# State Machine

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
- On metadata-only or external-verification work that cannot be solved by a repository commit, move Issue to `manual-action`.
- On unrecoverable failure, move Issue to `blocked`.

## REVIEW
No new Issue may start.

- If the linked PR head has not been auto-reviewed yet, dispatch REVIEW
  automatically.
- `REQUEST CHANGES` with repository-change scope moves to `rework` and makes
  REWORK runnable without a human command.
- `MERGE READY` keeps the Issue in `review`; if the result belongs to the
  current head and no safety gate fails, the linked PR receives the exclusive
  `devbot:ready-to-merge` label.
- Stale, contradictory, exhausted, metadata-only, external-verification, or
  otherwise unsafe review-loop outcomes move to `manual-action` without
  deleting the branch, PR, or worktree.
- Merge: move to `done`.

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
attempt limit is 3 posted auto-review results per PR conversation; exceeding
that limit requires `manual-action` and preserves all work.

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
