"""Codex CLI agent runner.

This Task ships a dry-run skeleton only: real Codex execution (spawning the
`codex` CLI as a subprocess against a checked-out repository) is out of
scope and is implemented in a later Task.
"""

from __future__ import annotations

from dataclasses import dataclass

from devbot.agents.base import AgentRunner, AgentRunResult
from devbot.models import RepositoryConfig


@dataclass(frozen=True, slots=True)
class CodexRunner(AgentRunner):
    """Agent runner backed by the Codex CLI. Defaults to dry-run."""

    dry_run: bool = True

    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        if self.dry_run:
            return AgentRunResult(
                executed=False,
                dry_run=True,
                message=f"[dry-run] would run codex in {repository.local_path}",
            )

        raise NotImplementedError("Real Codex execution is out of scope for Task 001")
