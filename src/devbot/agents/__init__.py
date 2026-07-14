"""Agent runner implementations, plus the implementer/reviewer role factory.

`build_agent_runner()` is the single place that maps a configured agent
name (`DevBotConfig.implementer_agent` / `.reviewer_agent`) to a concrete
`AgentRunner`. Callers must not silently fall back to a default
implementation for an unrecognized name - see `devbot.config.load_config`,
which validates agent names at config-load time using `KNOWN_AGENT_NAMES`
so a typo fails fast instead of surfacing as a confusing runtime error deep
in the polling loop.
"""

from __future__ import annotations

from devbot.agents.base import AgentRunner
from devbot.agents.claude import ClaudeRunner
from devbot.agents.codex import CodexRunner

_RUNNER_CLASSES: dict[str, type[AgentRunner]] = {
    "codex": CodexRunner,
    "claude": ClaudeRunner,
}

KNOWN_AGENT_NAMES = frozenset(_RUNNER_CLASSES)


class UnknownAgentError(ValueError):
    """Raised when an agent name isn't a recognized `AgentRunner` implementation."""


def build_agent_runner(agent_name: str, *, dry_run: bool) -> AgentRunner:
    """Construct the `AgentRunner` for `agent_name` ("codex" or "claude").

    Raises `UnknownAgentError` for anything else - never falls back to a
    default implementation for an unrecognized name.
    """
    try:
        runner_cls = _RUNNER_CLASSES[agent_name]
    except KeyError:
        raise UnknownAgentError(
            f"Unknown agent: {agent_name!r} (known agents: {sorted(KNOWN_AGENT_NAMES)})"
        ) from None
    return runner_cls(dry_run=dry_run)
