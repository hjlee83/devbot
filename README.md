# DevBot

DevBot is a local, GitHub-issue-driven coding agent orchestrator. It polls
managed repositories, enforces one globally active task, runs a configured
coding agent (Codex first), and drives the change through to a reviewable
PR. See `docs/` for the full design and `tasks/` for the task contracts
that drive implementation.

This Task (001) delivers the bootstrap MVP: configuration, a process lock,
repository path validation, and non-networked queue/agent skeletons.

## Requirements
- Python 3.13
- [`uv`](https://docs.astral.sh/uv/)

## Setup

```bash
uv sync
cp .env.example .env
# edit .env and set WORKSPACE_ROOT to the directory that holds your
# managed repository checkouts
```

Edit `config/repositories.yaml` to list the repositories DevBot manages.
`local_path` for each repository is derived as `WORKSPACE_ROOT / repo`.
Only `enabled: true` repositories are validated and managed.

## Run

```bash
uv run devbot
```

This loads configuration, acquires the single-process lock
(`DEVBOT_LOCK_FILE`), validates enabled repository paths, prints the
managed repositories, prints `DevBot started`, and exits.

## Development

```bash
uv sync
uv run ruff check .
uv run pytest
uv run devbot
```

## Project layout

```text
src/devbot/
  main.py           CLI entry point
  config.py         .env + config/repositories.yaml loader
  lock.py           single-process file lock
  models.py         configuration and queue data structures
  queue.py          global queue selection rules (no network)
  github_client.py  authenticated GitHub REST API read client (users, Issues)
  workspace.py      local repository path validation
  agents/
    base.py         AgentRunner interface
    codex.py         dry-run Codex CLI runner skeleton
```

Target repositories supply their own root `AGENTS.md`; DevBot does not
duplicate project-specific rules into its own configuration.
