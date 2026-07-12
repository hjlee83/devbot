"""Codex CLI agent runner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from devbot.agents.base import AgentRunner, AgentRunResult
from devbot.models import RepositoryConfig

CODEX_COMMAND = "codex"


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

        completed = subprocess.run(
            [CODEX_COMMAND, "exec", prompt],
            cwd=str(repository.local_path),
            capture_output=True,
            text=True,
            check=False,
        )
        return AgentRunResult(
            executed=True,
            dry_run=False,
            message=completed.stdout or completed.stderr,
            returncode=completed.returncode,
        )
