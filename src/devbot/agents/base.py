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


class AgentRunner(ABC):
    """Runs a coding agent against a checked-out repository."""

    @abstractmethod
    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        """Run the agent for `repository` with the given task `prompt`."""
        raise NotImplementedError
