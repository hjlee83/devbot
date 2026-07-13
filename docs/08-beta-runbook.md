# Beta Runbook

Task 009 wires Task 001-008's pieces into one flow inside
`PollingService.run_once()` (see `src/devbot/polling.py`). Task 010 adds
the `review`-state branch of that same flow: polled `@devbot` PR feedback
now reworks the existing branch/PR automatically instead of waiting for a
human to trigger it. This is a manual walkthrough for confirming both
flows against a real target repository, plus the operational checklist
for running DevBot beyond a single smoke test.

## Full flow

```text
Any devbot:working Issue? --yes--> skip this iteration

no
 |
 v
Any devbot:review Issue with an unprocessed @devbot comment?
 |
 +--yes--> ReworkService.process() (Task 010)
 |          reuses the EXISTING branch/PR (never creates a new one)
 |             |
 |             v
 |         request_changes (review -> working) -> AgentRunner
 |             |
 |             v
 |         run_verification_commands
 |             |
 |     passed? +----- failed?
 |        |               |
 |        v               v
 |  commit -> push   block (+ reason comment)
 |  -> react "eyes"
 |  on the comment
 |        |
 |        v
 |  mark_for_review (-> review)
 |
 no unprocessed comment / no review Issue
 |
 v
ready --(claim)--> working --(AgentRunner)--> [agent output]
                                    |
                                    v
                          run_verification_commands
                       (uv run ruff check . && uv run pytest,
                        in the target repository)
                                    |
                       passed? -----+----- failed?
                          |                   |
                          v                   v
                commit -> push -> PR    block (+ comment)
                          |
                          v
                  mark_for_review (-> review)
```

`state_writer`, `delivery`, and `rework_service` are optional constructor
arguments on `PollingService`. When `state_writer`/`delivery` are both
supplied (as `devbot.main` always does), the `ready` flow above runs.
When `rework_service` is also supplied (also always true in
`devbot.main`), a `review` Issue with an unprocessed `@devbot` comment is
reworked before any `ready` Issue is even considered - rework always takes
priority over starting new `ready` work. Any of the three being omitted
falls back to progressively earlier Task behavior (down to Task 005's
select + run the agent only) - this is what every earlier Task's tests
still exercise, unchanged.

## Manual dry-run walkthrough

This reproduces what `results/009-beta-smoke-test.md` records: a real
local Git + `uv` project as the "target repository", a stubbed `ready`
Issue (no real GitHub Issue needed), and `--dry-run` so nothing writes to
GitHub or pushes anywhere.

```bash
# 1. Build a minimal target repository DevBot can run verification in.
mkdir -p /tmp/devbot-smoke/workspace/myrepo && cd /tmp/devbot-smoke/workspace/myrepo
git init -q && git config user.email a@b.c && git config user.name test
cat > pyproject.toml <<'EOF'
[project]
name = "smoke-target"
version = "0.1.0"
requires-python = ">=3.13"

[tool.uv]
package = false

[dependency-groups]
dev = ["pytest>=8.0", "ruff>=0.6"]
EOF
mkdir tests && echo 'def test_ok() -> None:\n    assert True' > tests/test_dummy.py
uv sync -q && git add -A && git commit -q -m init

# 2. Point DevBot at it.
cd /path/to/devbot
cat > /tmp/devbot-smoke/.env <<EOF
WORKSPACE_ROOT=/tmp/devbot-smoke/workspace
GITHUB_TOKEN=fake-token-for-smoke-test
EOF
cat > /tmp/devbot-smoke/repositories.yaml <<EOF
repositories:
  - owner: someone
    repo: myrepo
    enabled: true
EOF

# 3. Run --once --dry-run with GitHubClient.list_issues stubbed to return
#    one devbot:ready Issue (see results/009-beta-smoke-test.md for the
#    exact Python snippet used).
```

Expected log output: Issue selected -> `AgentRunner 실행: ... dry_run=True`
-> `Delivery 시작: branch=...` -> real verification runs in
`/tmp/devbot-smoke/workspace/myrepo` and passes -> `Delivery 결과:
[dry-run] verification passed; no commit, push, or PR` -> exit code 0. No
label, comment, commit, push, or PR ever touches GitHub or the target
repo's Git history.

## Operational checklist

- [ ] `WORKSPACE_ROOT` points at a directory containing one checked-out
      clone per managed repository (`WORKSPACE_ROOT/<repo>`), each a clean
      Git working tree on a branch DevBot may commit to.
- [ ] Every managed repository is itself a `uv`-managed Python project
      where `uv run ruff check .` and `uv run pytest` both succeed on a
      clean checkout — `delivery.py`'s verification commands are
      hardcoded, not per-repository configurable (see "Known limitations"
      in `results/009-beta-smoke-test.md`).
- [ ] `GITHUB_TOKEN` has write access (labels, comments, PRs) on every
      managed repository, not just read access.
- [ ] `config/repositories.yaml` lists only repositories DevBot is
      authorized to write to and open PRs against; double-check
      `default_branch` per repository if it isn't `main`.
- [ ] `DEVBOT_LOCK_FILE` points at a path only this DevBot process uses,
      to prevent two instances from claiming the same Issue concurrently.
- [ ] Start with `DRY_RUN=true` (or `--dry-run`) in any new environment
      and confirm the full flow's log output looks right before flipping
      to `DRY_RUN=false`.
- [ ] `review`-state Issues are polled for unprocessed `@devbot` PR
      comments every iteration (Task 010) and reworked automatically on
      the existing branch/PR — no manual trigger needed. Only the first
      review Issue found in collection order is reworked per iteration;
      others wait for the next poll.
