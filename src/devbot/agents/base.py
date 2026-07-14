"""Abstract agent runner interface.

DevBot depends on this interface, not on any single vendor. `CodexRunner`
(see `devbot.agents.codex`) is the first implementation.

Target repositories supply their own project-specific rules in a root
`AGENTS.md` file. DevBot does not duplicate or override those rules; it only
passes the Issue prompt to the agent and lets the agent read the target
repository's own `AGENTS.md`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from devbot.models import RepositoryConfig


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Outcome of a single agent invocation."""

    executed: bool
    dry_run: bool
    message: str
    returncode: int | None = None

    @property
    def failed(self) -> bool:
        """True for a genuine execution failure that callers must treat as
        `AGENT_FAILED`/`blocked`, not let flow through to delivery/verify/
        commit/push.

        A dry-run's `executed=False` is an intentional no-op, never a
        failure. Everything else that didn't execute (`executed=False`,
        e.g. `ClaudeRunner` reporting a missing CLI or a timeout - see
        `devbot.agents.claude`) *is* a failure even though it has no
        `returncode` to check (`returncode is None` alone is not
        sufficient - it's also the correct value for a real exit code of
        0's sibling case: no process ever ran). A nonzero `returncode` from
        an execution that did run is always a failure.
        """
        if self.dry_run:
            return False
        return not self.executed or self.returncode not in (None, 0)


class AgentRunner(ABC):
    """Runs a coding agent against a checked-out repository."""

    @abstractmethod
    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        """Run the agent for `repository` with the given task `prompt`."""
        raise NotImplementedError
