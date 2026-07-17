"""Claude Code CLI agent runner."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from devbot.agent_execution import AgentExecutionContext, AgentExecutionPolicy, AgentLauncher
from devbot.agents.base import AgentRunner, AgentRunResult
from devbot.models import AgentOutcome, RepositoryConfig

CLAUDE_COMMAND = "claude"
DEFAULT_TIMEOUT_SECONDS = 1800.0


@dataclass(frozen=True, slots=True)
class ClaudeRunner(AgentRunner):
    """Agent runner backed by the non-interactive Claude Code CLI.

    Defaults to dry-run. When actually executed, runs the CLI as a
    subprocess with `repository.local_path` as its working directory, in
    non-interactive/print mode so it never blocks waiting on a TTY prompt.

    Claude CLI missing, an auth failure, a nonzero exit, and a timeout are
    all reported as a structured `AgentRunResult` rather than a raised
    exception - the caller (the polling loop) must never crash because an
    Agent process misbehaved. Auth failure has no documented, stable exit
    code of its own, so it is surfaced through the same "nonzero exit"
    result as any other CLI failure, with the CLI's full stdout/stderr kept
    in `message` so the caller can tell what actually went wrong.
    """

    dry_run: bool = True
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS

    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        if self.dry_run:
            return AgentRunResult(
                executed=False,
                dry_run=True,
                message=f"[dry-run] would run claude in {repository.local_path}",
            )

        try:
            completed = subprocess.run(
                [CLAUDE_COMMAND, "-p", prompt, "--permission-mode", "acceptEdits"],
                cwd=str(repository.local_path),
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_seconds,
            )
        except FileNotFoundError:
            return AgentRunResult(
                executed=False,
                dry_run=False,
                message=f"Claude CLI({CLAUDE_COMMAND})가 설치되어 있지 않습니다.",
            )
        except subprocess.TimeoutExpired:
            return AgentRunResult(
                executed=False,
                dry_run=False,
                message=f"Claude CLI 실행이 {self.timeout_seconds}초 안에 끝나지 않았습니다.",
                outcome_hint=AgentOutcome.RESUMABLE_INTERRUPTION,
            )

        return AgentRunResult(
            executed=True,
            dry_run=False,
            message=completed.stdout or completed.stderr,
            returncode=completed.returncode,
        )

    def command_for_context(self, _context: AgentExecutionContext, prompt: str) -> list[str]:
        return [CLAUDE_COMMAND, "-p", prompt, "--permission-mode", "acceptEdits"]

    def run_context(self, context: AgentExecutionContext, prompt: str) -> AgentRunResult:
        if self.dry_run:
            return AgentRunResult(
                executed=False,
                dry_run=True,
                message=f"[dry-run] would run claude in {context.workspace}",
            )
        launcher = AgentLauncher(
            command_builder=self.command_for_context,
            policy=AgentExecutionPolicy(
                agent="claude",
                version="unknown",
                sandbox="provider-default",
                approval="acceptEdits",
                network="provider-default",
                capability_summary={"non_interactive": True},
            ),
        )
        try:
            completed = launcher.run(context, prompt, timeout=self.timeout_seconds)
        except FileNotFoundError:
            return AgentRunResult(
                executed=False,
                dry_run=False,
                message=f"Claude CLI({CLAUDE_COMMAND})가 설치되어 있지 않습니다.",
            )
        except subprocess.TimeoutExpired:
            return AgentRunResult(
                executed=False,
                dry_run=False,
                message=f"Claude CLI 실행이 {self.timeout_seconds}초 안에 끝나지 않았습니다.",
                outcome_hint=AgentOutcome.RESUMABLE_INTERRUPTION,
            )
        return AgentRunResult(
            executed=True,
            dry_run=False,
            message=completed.stdout or completed.stderr,
            returncode=completed.returncode,
        )
