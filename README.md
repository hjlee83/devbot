# DevBot

DevBot is a local, GitHub-issue-driven coding agent orchestrator.
Planning and architecture decisions are intentionally human-driven. After an
approved Task receives `devbot:ready`, DevBot runs the configured Implementer
and Reviewer roles and drives the existing Task Issue, Branch, Contract, and
Pull Request through implementation, review, rework, and a merge-ready state.

The stable operating principles are defined in [`CONSTITUTION.md`](CONSTITUTION.md).
Agent execution rules are defined in [`AGENTS.md`](AGENTS.md). See `docs/` for
the detailed design and `tasks/` for the Task Contracts that drive
implementation.

## Architecture at a glance

```text
Project owner + ChatGPT
  idea → architecture → scope → acceptance criteria → approval

Planner
  one Task Issue + one Branch + one Contract + one Pull Request

DevBot
  IMPLEMENT → REVIEW → REWORK when required → REVIEW → READY TO MERGE

Operator
  final merge decision
```

Key invariants:

- planning is human-first;
- one Task uses one Issue, one Branch, one Contract, and one Pull Request;
- separate Execution Issues are not used;
- after workspace preparation, every Agent and execution stage uses the same
  `PreparedWorkspace`;
- merge remains manual unless the project owner explicitly changes that policy.

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
permission errors in sandboxed review/agent environments that cannot read or
write `~/.cache/uv`.

Edit `config/repositories.yaml` to list the repositories DevBot manages.
`local_path` for each repository is derived as `WORKSPACE_ROOT / repo`.
Only `enabled: true` repositories are validated and managed.

## Run

```bash
uv run devbot --once             # one polling iteration, then exit
uv run devbot --once --dry-run   # same, but force dry-run regardless of DRY_RUN
uv run devbot                    # continuous polling until SIGINT/SIGTERM
uv run devbot --once --verbose   # same, but force DEBUG-level logs for this run only
uv run devbot --version          # print the installed package version and exit
```

Each iteration enforces one globally active Task and selects the next runnable
Job from the managed repositories. For a fully prepared Task, DevBot reuses the
existing Branch and Pull Request. For a newly approved `devbot:ready` Issue
without execution artifacts, DevBot validates the Issue metadata, creates the
canonical `task/<NNN>-<slug>` Branch and Task Contract, prepares an isolated
worktree, and runs the configured Agent role against that prepared workspace.
It does not create an empty PR during bootstrap; delivery opens or updates the
PR only after verified implementation output exists.

The normal workflow is:

```text
devbot:ready
→ IMPLEMENT
→ devbot:review
→ REVIEW
→ devbot:rework when changes are requested
→ REVIEW after successful rework
→ devbot:ready-to-merge
```

Claims occur before workspace preparation. If preparation or validation fails,
DevBot restores or safely transitions the Issue instead of leaving it stuck in
`devbot:working`. Verification and delivery failures produce visible
diagnostics and preserve work. `DRY_RUN=true` is the default; `--dry-run`
forces it for one run and prevents Agent, Git, and GitHub writes.

See `docs/08-beta-runbook.md` for the operational walkthrough and checklist.

Verification commands are currently hardcoded to `uv run ruff check .` and
`uv run pytest` (see `src/devbot/delivery.py`), so target repositories must be
`uv`-managed Python projects with those commands available.

## Logging

`LOG_LEVEL` (`.env`, default `INFO`) sets the daemon's log level; allowed values
are `DEBUG`, `INFO`, `WARNING`, `ERROR` (case-insensitive). An unrecognized
value fails configuration loading instead of silently falling back.
`--verbose` overrides the level to `DEBUG` for that process only.

- `INFO`: startup configuration, managed repositories, cycle summaries, one
  **Queue Summary**, the selected Job, the normalized **Cycle Result**, and
  failures.
- `DEBUG`: adds per-repository search conditions, candidate inclusion/exclusion
  reasons, and per-stage elapsed time.

Every log line in one polling cycle shares a `cycle_id`. Zero managed
repositories produces a distinct `no_managed_repositories` diagnostic and no
GitHub call. Secrets such as `GITHUB_TOKEN` and authorization header values are
never written to logs. See `docs/08-beta-runbook.md` for a diagnostic
walkthrough.

### Queue Summary / Selected / Cycle Result

Each cycle emits one operator-facing report:

```text
Queue Summary
  ready         : 0
  review        : 1
  rework        : 0
  blocked       : 1
  manual-action : 0
  working       : 0

Selected
  repo     : hjlee83/devbot
  issue    : #38
  pr       : #39
  job_type : review

Cycle Result
  REVIEW
  elapsed: 402ms
```

- **Queue Summary** counts every stable workflow state across managed
  repositories. Each Issue is counted once.
- **Selected** appears only when a Job was chosen and identifies the repository,
  Issue, Pull Request when known, and Job type.
- **Cycle Result** reports one normalized outcome such as
  `NO_RUNNABLE_TASK`, `IMPLEMENT`, `REVIEW`, `REWORK`, or a failure category.

DEBUG candidate diagnostics and structured cycle logs remain available for
investigation.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once --dry-run
```

## Project layout

```text
CONSTITUTION.md          stable project principles
AGENTS.md                AI Agent rules and SOPs
docs/                    architecture, standards, runbooks, decisions
tasks/                   Task Contracts
results/                 implementation evidence and handoff records
src/devbot/
  main.py                CLI entry point (--once / --dry-run / continuous)
  config.py              .env + config/repositories.yaml loader
  lock.py                single-process file lock
  models.py              configuration and queue data structures
  queue.py               global queue selection rules (no network)
  github_client.py       authenticated GitHub REST API read client
  github_write_client.py authenticated GitHub REST API write client
  issue_state.py         devbot:* label state machine
  workspace.py           Git workspace checks, naming, prompt building
  worktree.py            host-managed PreparedWorkspace lifecycle
  delivery.py            verify → commit → push → PR → Issue updates
  rework.py              review feedback → rework on the same Branch/PR
  polling.py             polling, Job selection, execution, review loop
  observability.py       structured logging and secret redaction
  agents/
    base.py              AgentRunner interface
    codex.py             Codex CLI runner
```

Target repositories may supply their own root `AGENTS.md`. DevBot does not
copy target-specific rules into configuration.
