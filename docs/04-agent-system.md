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
- `reviewer_agent` — constructed into a `reviewer_runner` and injected into
  `PollingService`, but not invoked anywhere yet. Automatically triggering
  a review run is a later Task; this Task only wires the role through.

Resolution order for each role (`load_config`, see `devbot.config`):

1. The role's own env var (`IMPLEMENTER_AGENT` / `REVIEWER_AGENT`), if set.
2. Otherwise the legacy `DEFAULT_AGENT`, if set — this is what keeps an
   existing single-agent deployment (`DEFAULT_AGENT=codex` and nothing
   else) behaving exactly as before.
3. Otherwise the built-in default: implementer=`claude`, reviewer=`codex`.

An agent name that resolves to anything other than `codex`/`claude` fails
config loading with a `ConfigError` — never a silent fallback.

The target repository's root `AGENTS.md` is the project-specific source of truth.
DevBot does not duplicate those rules into its own repository-specific configuration.
