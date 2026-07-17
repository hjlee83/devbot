"""Shared Agent execution context, environment, launcher, and diagnostics."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from devbot.github_client import GitHubIssue, PullRequest
from devbot.models import RepositoryConfig
from devbot.worktree import PreparedWorkspace


class AgentRole(StrEnum):
    IMPLEMENT = "implement"
    REWORK = "rework"
    REVIEW = "review"


@dataclass(frozen=True, slots=True)
class AgentExecutionContext:
    repository: RepositoryConfig
    prepared_workspace: PreparedWorkspace
    canonical_branch: str
    issue: GitHubIssue
    pull_request: PullRequest | None
    execution_id: str
    role: AgentRole

    @property
    def workspace(self) -> Path:
        return self.prepared_workspace.worktree_path

    def safe_environment(self) -> dict[str, str]:
        env = {
            "DEVBOT_REPOSITORY": self.repository.full_name,
            "DEVBOT_BRANCH": self.canonical_branch,
            "DEVBOT_ISSUE": str(self.issue.number),
            "DEVBOT_PR": str(self.pull_request.number if self.pull_request else ""),
            "DEVBOT_WORKSPACE": str(self.workspace),
            "DEVBOT_ROLE": self.role.value,
            "DEVBOT_EXECUTION_ID": self.execution_id,
        }
        if "PATH" in os.environ:
            env["PATH"] = os.environ["PATH"]
        return env


@dataclass(frozen=True, slots=True)
class AgentExecutionPolicy:
    agent: str
    version: str
    sandbox: str
    approval: str
    network: str
    capability_summary: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class AgentExecutionDiagnostics:
    agent: str
    role: str
    version: str
    workspace: str
    branch: str
    issue: int
    pr: int | None
    sandbox: str
    approval: str
    network: str
    capability_summary: Mapping[str, bool]


CommandBuilder = Callable[[AgentExecutionContext, str], Sequence[str]]


@dataclass(frozen=True, slots=True)
class AgentLauncher:
    command_builder: CommandBuilder
    policy: AgentExecutionPolicy

    def diagnostics(self, context: AgentExecutionContext) -> AgentExecutionDiagnostics:
        return AgentExecutionDiagnostics(
            agent=self.policy.agent,
            role=context.role.value,
            version=self.policy.version,
            workspace=str(context.workspace),
            branch=context.canonical_branch,
            issue=context.issue.number,
            pr=context.pull_request.number if context.pull_request else None,
            sandbox=self.policy.sandbox,
            approval=self.policy.approval,
            network=self.policy.network,
            capability_summary=dict(self.policy.capability_summary),
        )

    def run(
        self, context: AgentExecutionContext, prompt: str, **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            list(self.command_builder(context, prompt)),
            cwd=str(context.workspace),
            env=context.safe_environment(),
            capture_output=True,
            text=True,
            check=False,
            **kwargs,
        )
