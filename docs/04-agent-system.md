# Agent System

DevBot depends on an `AgentRunner` interface, not on one vendor.

Implementations (`devbot.agents`):
- `CodexRunner` — Codex CLI (`codex exec <prompt>`).
- `ClaudeRunner` — non-interactive Claude Code CLI (`claude -p <prompt>
  --permission-mode acceptEdits`), run with the target repository's
  `local_path` as its working directory. CLI-missing, auth failure,
  nonzero exit, and timeout are all returned as a structured
  `AgentRunResult` failure, never raised.

Possible later implementations:
- `DeepSeekRunner`

`devbot.agents.build_agent_runner(name, *, dry_run)` maps a configured
agent name (`"codex"` or `"claude"`) to its `AgentRunner`. An unrecognized
name raises `UnknownAgentError` — there is no silent fallback.

## Implementer / reviewer roles

`DevBotConfig` carries two independent agent selections:

- `implementer_agent` — runs the actual coding work, for **both** a ready
  Issue's initial implementation and Task 010's PR-feedback rework. Both
  paths share one `implementer_runner` instance (built once in
  `devbot.main`).
- `reviewer_agent` — constructed into a `reviewer_runner`, wrapped in a
  `ReviewService` (`devbot.review`), and injected into `PollingService` as
  `review_service`. Task 012 is what actually *calls* it — see "Automatic
  review relay" below.

Resolution order for each role (`load_config`, see `devbot.config`):

1. The role's own env var (`IMPLEMENTER_AGENT` / `REVIEWER_AGENT`), if set.
2. Otherwise the legacy `DEFAULT_AGENT`, if set — this is what keeps an
   existing single-agent deployment (`DEFAULT_AGENT=codex` and nothing
   else) behaving exactly as before.
3. Otherwise the built-in default: implementer=`claude`, reviewer=`codex`.

An agent name that resolves to anything other than `codex`/`claude` fails
config loading with a `ConfigError` — never a silent fallback.

## Automatic review relay and state machine (Task 014)

`PollingService.run_cycle()` (see `devbot.polling`) turns `devbot:ready` /
`devbot:review` Issues into scheduled `Job`s of type `IMPLEMENT`, `REWORK`,
or `REVIEW` (`devbot.models.JobType` — deliberately role-neutral, no
vendor/product name), and runs up to `MAX_CONCURRENT_JOBS` of them per
cycle (`devbot.scheduler.select_jobs`; default `1`, the same serial
behavior as before this Task). At most one job runs per repository at a
time, regardless of concurrency.

- A `devbot:review` Issue's linked PR is a `REVIEW` candidate whenever its
  *current* head commit SHA has no auto-review marker comment yet
  (`devbot.review.has_review_marker_for_head`).
- A `devbot:rework` Issue is a `REWORK` candidate only when its linked PR
  has an unprocessed `@devbot` comment. `review` and `rework` are separate
  stable states: review waits for reviewer/merge, rework waits for
  implementer changes. REWORK still outranks REVIEW and IMPLEMENT in the
  scheduler.
- `ReviewService.process()` runs `reviewer_runner`, requires the reviewer's
  output to contain exactly one of the literal strings `MERGE READY` /
  `REQUEST CHANGES` (`AGENTS.md` section 10's `# Review Summary` format —
  both or neither is an invalid review, and blocks the Issue), and posts
  that output back to the PR with a trailing idempotency marker:

  ```html
  <!-- devbot:auto-review head=<FULL_HEAD_SHA> -->
  ```

  On `REQUEST CHANGES`, the same posted comment also contains a literal
  `@devbot` mention and the Issue moves to `devbot:rework`; the rework path
  detects that comment on the next cycle. `MERGE READY` never contains that
  mention and the Issue remains `devbot:review`. Both rework and review
  detection read the **linked PR's** comments (not the tracked Issue's own
  comments) — see the `devbot.polling` module docstring.
- Automatic Merge and automatic Issue Close are out of scope for every
  Task through 012; `MERGE READY` leaves the Issue in `devbot:review` for
  a human to merge.

Allowed state transitions are enforced by `IssueStateWriter`:

```text
ready  -> working -> review
review -> working -> review
review -> working -> rework
rework -> working -> review
working -> ready|review|rework   # preflight restore to the claimed state
working -> blocked               # execution, verification, delivery, or unexpected failure
```

Every successful state write removes all other `devbot:*` state labels
before adding the target state, so an Issue ends with exactly one state
label. Concurrent in-process claims for the same repository/Issue are
rejected before any Agent is run.

The target repository's root `AGENTS.md` is the project-specific source of truth.
DevBot does not duplicate those rules into its own repository-specific configuration.
