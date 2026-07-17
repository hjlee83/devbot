"""Codex CLI agent runner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from devbot.agents.base import AgentRunner, AgentRunResult
from devbot.models import AgentOutcome, RepositoryConfig

CODEX_COMMAND = "codex"


class CodexConfigurationError(RuntimeError):
    """Raised when Codex CLI cannot satisfy DevBot's unattended policy."""


@dataclass(frozen=True, slots=True)
class CodexRunner(AgentRunner):
    """Agent runner backed by the Codex CLI. Defaults to dry-run."""

    dry_run: bool = True
    extra_writable_roots: tuple[Path, ...] = ()
    sandbox_mode: str = "workspace-write"
    approval_mode: str = "never"
    network_mode: str = "enabled"
    _capabilities: dict[str, bool] | None = field(default=None, repr=False)

    def _help(self, *args: str) -> str:
        completed = subprocess.run(
            [CODEX_COMMAND, *args, "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        return f"{completed.stdout}\n{completed.stderr}"

    def _detect_capabilities(self) -> dict[str, bool]:
        if self._capabilities is not None:
            return self._capabilities
        root_help = self._help()
        exec_help = self._help("exec")
        combined = f"{root_help}\n{exec_help}"
        return {
            "cd": "--cd" in combined or "-C," in combined,
            "add_dir": "--add-dir" in combined,
            "sandbox": "--sandbox" in combined,
            "approval": "--ask-for-approval" in root_help,
            "config": "--config" in combined,
        }

    def _git_path(self, repository: RepositoryConfig, arg: str) -> Path | None:
        completed = subprocess.run(
            ["git", "rev-parse", arg],
            cwd=str(repository.local_path),
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode != 0:
            return None
        raw = completed.stdout.strip()
        path = Path(raw)
        if not path.is_absolute():
            path = repository.local_path / path
        return path.resolve()

    def _writable_roots(self, repository: RepositoryConfig) -> tuple[Path, ...]:
        roots = [repository.local_path.resolve()]
        git_dir = self._git_path(repository, "--git-dir")
        common_dir = self._git_path(repository, "--git-common-dir")
        if git_dir is None:
            raise CodexConfigurationError(
                "agent_configuration_invalid: missing required Git metadata writable root "
                "`git rev-parse --git-dir`. Recovery: run Codex from a prepared Git "
                "worktree and grant the resolved worktree Git directory as writable."
            )
        if common_dir is None:
            raise CodexConfigurationError(
                "agent_configuration_invalid: missing required Git metadata writable root "
                "`git rev-parse --git-common-dir`. Recovery: run Codex from a prepared "
                "Git worktree and grant the resolved Git common directory as writable."
            )
        for path in (git_dir, common_dir, *self.extra_writable_roots):
            if path is not None and path not in roots:
                roots.append(path)
        return tuple(roots)

    def command_for(self, repository: RepositoryConfig, prompt: str) -> list[str]:
        caps = self._detect_capabilities()
        required = {
            "approval": "approval=never via --ask-for-approval/-a",
            "sandbox": "workspace sandbox via --sandbox/-s",
            "cd": "PreparedWorkspace cwd via --cd/-C",
            "add_dir": "Git metadata writable roots via --add-dir",
            "config": "network enforcement via --config/-c",
        }
        missing = [description for key, description in required.items() if not caps[key]]
        if missing:
            raise CodexConfigurationError(
                "agent_configuration_invalid: Codex CLI is missing required unattended "
                f"capabilities: {', '.join(missing)}. Recovery: upgrade Codex CLI or "
                "adjust the runner integration before retrying; DevBot did not launch "
                "the Agent process."
            )
        command = [CODEX_COMMAND]
        command.extend(["-a", self.approval_mode])
        command.extend(["-s", self.sandbox_mode])
        command.extend(["-C", str(repository.local_path)])
        command.extend(
            [
                "-c",
                "shell_environment_policy.inherit=\"all\"",
                "-c",
                "sandbox_workspace_write.network_access=true",
            ]
        )
        for root in self._writable_roots(repository)[1:]:
            command.extend(["--add-dir", str(root)])
        command.extend(["exec", prompt])
        return command

    def execution_policy(self, repository: RepositoryConfig) -> dict[str, object]:
        version = subprocess.run(
            [CODEX_COMMAND, "--version"], capture_output=True, text=True, check=False
        )
        writable_roots = [str(root) for root in self._writable_roots(repository)]
        return {
            "cwd": str(repository.local_path),
            "writable_roots": writable_roots,
            "approval_mode": self.approval_mode,
            "sandbox_mode": self.sandbox_mode,
            "network_mode": self.network_mode,
            "codex_version": (version.stdout or version.stderr).strip(),
        }

    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        if self.dry_run:
            return AgentRunResult(
                executed=False,
                dry_run=True,
                message=f"[dry-run] would run codex in {repository.local_path}",
            )

        try:
            command = self.command_for(repository, prompt)
        except CodexConfigurationError as exc:
            return AgentRunResult(
                executed=False,
                dry_run=False,
                message=str(exc),
                returncode=None,
                outcome_hint=AgentOutcome.AGENT_CONFIGURATION_INVALID,
            )

        completed = subprocess.run(
            command,
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
