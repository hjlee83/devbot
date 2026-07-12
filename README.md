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

Edit `config/repositories.yaml` to list the repositories DevBot manages.
`local_path` for each repository is derived as `WORKSPACE_ROOT / repo`.
Only `enabled: true` repositories are validated and managed.

## Run

```bash
uv run devbot --once   # one polling iteration, then exit
uv run devbot          # continuous polling until SIGINT/SIGTERM
```

Each iteration: skip if any repository has a `devbot:working`/`devbot:review`
Issue, otherwise select the highest-priority (then oldest) `devbot:ready`
Issue across every enabled repository, validate its local Git workspace,
and hand it to the configured `AgentRunner`. `DRY_RUN=true` (the default)
runs the agent without spawning an external process or writing to GitHub.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot --once
```

## Project layout

```text
src/devbot/
  main.py           CLI entry point (--once / continuous)
  config.py         .env + config/repositories.yaml loader
  lock.py           single-process file lock
  models.py         configuration and queue data structures
  queue.py          global queue selection rules (no network)
  github_client.py  authenticated GitHub REST API read client (users, Issues)
  workspace.py      Git workspace checks, branch naming, prompt building
  polling.py        PollingService (one iteration) and the continuous loop
  agents/
    base.py         AgentRunner interface
    codex.py         Codex CLI runner (dry-run by default)
```

Target repositories supply their own root `AGENTS.md`; DevBot does not
duplicate project-specific rules into its own configuration.
