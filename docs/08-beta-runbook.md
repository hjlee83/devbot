# Beta Runbook

Task 009 wires Task 001-008's pieces into one flow. Task 010 adds the
`review`-state rework branch: polled `@devbot` PR feedback reworks the
existing branch/PR automatically. Task 011 adds a second Agent role
(reviewer) alongside the implementer. Task 012 turns all of this into a
per-repository job scheduler (`PollingService.run_cycle()`, see
`src/devbot/polling.py`) that also actually *runs* the reviewer role, so
the full ready -> review -> merge relay needs no manual trigger at any
step except the final Merge. This is a manual walkthrough for confirming
the flow against a real target repository, plus the operational checklist
for running DevBot beyond a single smoke test.

## Full flow (per repository, per cycle)

Each enabled repository independently contributes at most one candidate
job per cycle; `devbot.scheduler.select_jobs` then picks up to
`MAX_CONCURRENT_JOBS` of them (default `1` — the same serial behavior as
every earlier Task), never two for the same repository:

```text
Any devbot:working Issue in this repository? --yes--> no candidate this cycle

no
 |
 v
Any devbot:review Issue in this repository?
 |
 +--yes--> its linked PR has an unprocessed @devbot comment?
 |          |
 |          +--yes--> REWORK job (Task 010, unchanged mechanics -
 |          |          reuses the EXISTING branch/PR, reacts "eyes" when
 |          |          done) -> review -> reviewer re-reviews the new head
 |          |
 |          +--no --> that PR's current head SHA has no auto-review
 |                     marker yet?
 |                      |
 |                      +--yes--> REVIEW job (Task 012):
 |                      |          reviewer_runner runs, posts
 |                      |          `# Review Summary` + a
 |                      |          `<!-- devbot:auto-review head=... -->`
 |                      |          marker back to the PR.
 |                      |            REQUEST CHANGES -> same comment also
 |                      |            contains "@devbot", so next cycle's
 |                      |            rework branch above picks it up.
 |                      |            MERGE READY -> stays `devbot:review`,
 |                      |            waiting on a human Merge.
 |                      |
 |                      +--no --> fully caught up, no candidate this cycle
 |
 |  (a devbot:review Issue in this repository - rework OR review OR
 |   neither - always blocks a fresh `ready` implementation from
 |   starting in the SAME repository this cycle)
 |
 no review Issue in this repository
 |
 v
ready --(claim)--> working --(implementer_runner)--> [agent output]
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

`state_writer`, `delivery`, `rework_service`, and `review_service` are
optional constructor arguments on `PollingService`. `devbot.main` always
supplies all four in production; any subset being omitted falls back to
progressively earlier Task behavior (down to Task 005's select + run the
agent only) - this is what every earlier Task's tests still exercise,
unchanged. Automatic Merge and automatic Issue Close are never performed
by any of this - `MERGE READY` always waits for a human.

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
- [ ] `review`-state Issues are polled every iteration for both an
      unprocessed `@devbot` PR comment (Task 010 rework) and an unreviewed
      PR head commit (Task 012 auto-review) — no manual trigger needed for
      either. Each *repository* contributes at most one candidate per
      cycle (rework outranks review outranks a fresh `ready`
      implementation); a repository with multiple eligible Issues defers
      the rest to the next cycle, but a slow/stuck repository no longer
      blocks other repositories the way the pre-Task-012 global gate did.
- [ ] `MAX_CONCURRENT_JOBS` (default `1`) bounds how many *different*
      repositories' jobs run in the same cycle; leave it at `1` unless
      you've confirmed every managed repository has its own independent
      local workspace and Git remote credentials — two jobs never run for
      the *same* repository regardless of this value.
- [ ] If a `devbot:review` Issue looks stuck (no new auto-review comment
      appears across several polls), check the PR for a
      `<!-- devbot:auto-review head=... -->` marker matching its *current*
      head SHA — if one is missing/mismatched, the reviewer role should
      pick it up on the next cycle; if the Issue instead shows
      `devbot:blocked`, the blocking comment names the reviewer failure
      (missing/failed Agent CLI, or a Review Summary that didn't contain
      exactly one of `MERGE READY`/`REQUEST CHANGES`).
