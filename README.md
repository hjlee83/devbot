# DevBot

DevBot is a local, GitHub-issue-driven coding agent orchestrator. It polls
managed repositories, enforces one globally active task, runs a configured
coding agent (Codex first), and drives the change through to a reviewable
PR. See `docs/` for the full design and `tasks/` for the task contracts
that drive implementation.

## Requirements
- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env
# edit .env: set WORKSPACE_ROOT (directory holding your managed repository
# checkouts) and GITHUB_TOKEN (read access to those repositories)
```

`UV_CACHE_DIR` defaults to `.uv-cache` in `.env.example` and the bundled
verification scripts. Keeping the `uv` cache inside the repository avoids
permission errors in sandboxed review/agent environments that cannot read
or write `~/.cache/uv`.

Edit `config/repositories.yaml` to list the repositories DevBot manages.
`local_path` for each repository is derived as `WORKSPACE_ROOT / repo`.
Only `enabled: true` repositories are validated and managed.

## Run

```bash
uv run devbot --once             # one polling iteration, then exit
uv run devbot --once --dry-run   # same, but force dry-run regardless of DRY_RUN
uv run devbot                    # continuous polling until SIGINT/SIGTERM
uv run devbot --once --verbose   # same, but force DEBUG-level logs for this run only
```

Each iteration: if any repository has a `devbot:working` Issue, skip. If a
`devbot:review` Issue has an unprocessed `@devbot` PR comment, rework it
on the *existing* branch/PR (no new branch or PR); if it has none, wait.
Otherwise select the highest-priority (then oldest) `devbot:ready` Issue
across every enabled repository, validate its local Git workspace, claim
it (`ready` -> `working`), and hand it to the configured `AgentRunner`. On
success, verify (`uv run ruff check .` then `uv run pytest` in the target
repository), commit, push the task branch, open a PR, and move the Issue
to `review`. On agent or verification failure (either path), move the
Issue to `blocked` with the failure as a comment. `DRY_RUN=true` (the
default; `--dry-run` forces it) still runs verification but performs no
agent process, Git write, or GitHub write. See
`docs/08-beta-runbook.md` for a walkthrough and an operational checklist.

Verification commands are currently hardcoded to `uv run ruff check .` and
`uv run pytest` (see `src/devbot/delivery.py`), so target repositories must
themselves be `uv`-managed Python projects with those commands available.

## Logging

`LOG_LEVEL` (`.env`, default `INFO`) sets the daemon's log level; allowed
values are `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive - an
unrecognized value fails config loading with a clear error instead of
silently falling back). `--verbose` overrides it to `DEBUG` for that one
process only, without touching `.env` or the environment.

- `INFO` (default): startup configuration, managed repositories, cycle
  start/end summaries, selected/finished Jobs, and failures - what an
  operator needs during normal operation.
- `DEBUG`: adds per-repository search conditions and result counts, every
  candidate Job found or excluded (with a structured reason code such as
  `repository_busy`, `already_reviewed_head`, or `concurrency_limit`), and
  per-stage elapsed time within a Job.

Every log line in one polling cycle shares a `cycle_id` so related lines
can be correlated. Zero *managed* (enabled) repositories logs a distinct
`no_managed_repositories` diagnostic and skips that cycle without any
GitHub call - it is never conflated with "no ready Issue found". Secrets
(`GITHUB_TOKEN`, `Authorization`/`Bearer` header values) are never written
to any log line, at any level. See `docs/08-beta-runbook.md` for a
diagnostic walkthrough.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

## Project layout

```text
src/devbot/
  main.py                CLI entry point (--once / --dry-run / continuous)
  config.py               .env + config/repositories.yaml loader
  lock.py                 single-process file lock
  models.py                configuration and queue data structures
  queue.py                 global queue selection rules (no network)
  github_client.py         authenticated GitHub REST API read client (users, Issues, comments)
  github_write_client.py   authenticated GitHub REST API write client (labels, comments, PRs, reactions)
  issue_state.py            devbot:* label state machine (claim/block/mark_for_review/request_changes)
  workspace.py             Git workspace checks, branch naming, prompt building
  delivery.py              verify -> commit -> push -> PR -> Issue comment
  rework.py                 @devbot PR feedback -> rework on the same branch/PR
  polling.py               PollingService (one iteration, wired end to end) and the continuous loop
  observability.py          structured startup/cycle/Job logging, secret redaction, LOG_LEVEL/--verbose support
  agents/
    base.py               AgentRunner interface
    codex.py               Codex CLI runner (dry-run by default)
```

Target repositories supply their own root `AGENTS.md`; DevBot does not
duplicate project-specific rules into its own configuration.
