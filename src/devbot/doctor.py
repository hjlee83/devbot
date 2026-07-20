"""`devbot doctor` (Task 019 CP-019-5): a read-only diagnostic report an
operator runs before starting (or while troubleshooting) the daemon.

Never writes to GitHub, never blocks: the daemon-lock probe only attempts a
non-blocking acquire+immediate release (`devbot.startup.check_daemon_lock`),
and the GitHub connectivity check catches every exception (including
network failures) and reports them as a failed check rather than raising -
`doctor` must always finish and print a report, even with no network
access (the same "record the exact limitation" allowance the Task 019
Validation Gate gives `devbot --once`).
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from devbot.agent_execution import (
    AgentExecutionContext,
    AgentExecutionPolicy,
    AgentLauncher,
    AgentRole,
)
from devbot.agents.codex import CodexRunner
from devbot.github_client import GitHubClient, GitHubClientError, GitHubIssue
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.startup import StartupCheck, check_daemon_lock, run_startup_checks
from devbot.worktree import PreparedWorkspace, WorktreeManager


@dataclass(frozen=True, slots=True)
class DoctorReport:
    """Every check `devbot doctor` ran, plus the derived "safe to start"
    verdict."""

    checks: tuple[StartupCheck, ...]

    @property
    def safe_to_start(self) -> bool:
        """False only when a `fatal=True` check failed (currently just
        `daemon_lock` - see `devbot.startup`'s module docstring for why
        every other check is informational)."""
        return not any(not check.ok and check.fatal for check in self.checks)


def check_github_connectivity(config: DevBotConfig) -> StartupCheck:
    try:
        user = GitHubClient(config.github_token).get_authenticated_user()
    except GitHubClientError as exc:
        return StartupCheck("github_connectivity", False, f"GitHub API 오류: {exc}")
    except Exception as exc:  # noqa: BLE001 - network/env issues must not crash `doctor`
        return StartupCheck(
            "github_connectivity", False, f"연결 확인 불가(네트워크 제한 가능): {exc}"
        )
    return StartupCheck("github_connectivity", True, f"인증됨: {user.login}")


def check_agent_roles(config: DevBotConfig) -> StartupCheck:
    return StartupCheck(
        "agent_roles",
        True,
        f"implementer={config.implementer_agent} reviewer={config.reviewer_agent} "
        f"dry_run={config.dry_run}",
    )


def _doctor_execution_context(config: DevBotConfig, role: str) -> AgentExecutionContext:
    repository = (
        config.enabled_repositories[0]
        if config.enabled_repositories
        else RepositoryConfig(
            owner="devbot",
            repo="doctor",
            enabled=False,
            local_path=Path.cwd(),
        )
    )
    prepared = PreparedWorkspace(
        repository=repository,
        branch=repository.default_branch,
        base_branch=repository.default_branch,
        issue_number=0,
        pull_request=None,
        worktree_path=repository.local_path,
        reused=True,
    )
    issue = GitHubIssue(
        repository=repository.full_name,
        number=0,
        title="doctor",
        body="",
        state="open",
        labels=(),
        created_at=datetime.now(UTC),
    )
    return AgentExecutionContext(
        repository=repository,
        prepared_workspace=prepared,
        canonical_branch=repository.default_branch,
        issue=issue,
        pull_request=None,
        execution_id=f"doctor:{role}:{repository.full_name}",
        role=AgentRole.REVIEW if role == "reviewer" else AgentRole.IMPLEMENT,
    )


def _doctor_launcher(agent_name: str) -> AgentLauncher:
    return AgentLauncher(
        command_builder=lambda _context, _prompt: [agent_name, "--version"],
        policy=AgentExecutionPolicy(
            agent=agent_name,
            version="unknown",
            sandbox="doctor-readiness",
            approval="none",
            network="provider-default",
            capability_summary={"doctor": True},
        ),
    )


def check_agent_execution_readiness(
    agent_name: str, role: str, config: DevBotConfig | None = None
) -> StartupCheck:
    name = f"agent_execution_readiness[{role}:{agent_name}]"
    executable = "codex" if agent_name == "codex" else "claude" if agent_name == "claude" else ""
    if not executable or shutil.which(executable) is None:
        return StartupCheck(name, False, f"Agent executable not found: {agent_name}")
    launcher = _doctor_launcher(executable)
    context = _doctor_execution_context(config, role) if config is not None else None
    launcher_env = launcher.environment(context) if context is not None else None
    launcher_cwd = str(context.workspace) if context is not None else None
    version = subprocess.run(
        [executable, "--version"],
        cwd=launcher_cwd,
        env=launcher_env,
        capture_output=True,
        text=True,
        check=False,
    )
    if version.returncode != 0:
        return StartupCheck(name, False, f"version discovery failed: {version.stderr}")
    if agent_name == "codex":
        capabilities = CodexRunner(dry_run=True)._detect_capabilities()
        required = ("approval", "sandbox", "cd", "add_dir", "config")
        missing = [key for key in required if not capabilities.get(key)]
        if missing:
            return StartupCheck(
                name,
                False,
                f"unattended capability missing={','.join(missing)} "
                f"version={(version.stdout or version.stderr).strip()}",
            )
        return StartupCheck(
            name,
            True,
            f"version={(version.stdout or version.stderr).strip()} unattended_ready=True",
        )
    if agent_name == "claude":
        auth = subprocess.run(
            [executable, "auth", "status"],
            cwd=launcher_cwd,
            env=launcher_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if auth.returncode != 0:
            return StartupCheck(name, False, "authentication readiness failed")
    return StartupCheck(
        name,
        True,
        f"version={(version.stdout or version.stderr).strip()} availability=True auth_ready=True",
    )


def check_worktree_health(
    repository: RepositoryConfig, manager: WorktreeManager
) -> StartupCheck:
    """Task 023 CP-023-10: operator checkout path/branch, worktree root,
    active/stale Job worktrees, and any on-disk directory that is not a
    registered Git worktree (`conflicting` - a future `prepare()` for that
    path would fail). Never fatal: a stale/conflicting worktree affects only
    the Jobs that would reuse that exact path, not the whole daemon - the
    same "informational, not a fatal gate" policy every other startup check
    in this module already follows (see `devbot.startup`'s module
    docstring)."""
    name = f"worktree_health[{repository.full_name}]"
    report = manager.health(repository)
    detail = (
        f"operator_branch={report.operator_branch or 'unknown'} "
        f"root={report.worktree_root} active={len(report.active)} "
        f"stale={len(report.stale)} conflicting={len(report.conflicting)}"
    )
    if report.conflicting:
        detail += f" conflicting_paths={', '.join(str(path) for path in report.conflicting)}"
    return StartupCheck(name, report.safe_to_start, detail)


def check_repository_registrations(config: DevBotConfig) -> StartupCheck:
    """Issue #122: surfaces every problem `devbot.config.load_config()`
    already found (and safely skipped, rather than crashed on) while
    resolving `devbot init`-registered repositories - a moved/deleted
    registered path, two paths registered under the same `owner/repo`, or
    an unreadable `.devbot/config.yaml`. Informational, not fatal: exactly
    like `check_worktree_health`, one broken registration should not stop
    the daemon from managing every other (legacy or registered)
    repository."""
    if not config.registry_diagnostics:
        return StartupCheck("repository_registrations", True, "no registration problems found")
    return StartupCheck(
        "repository_registrations",
        False,
        "; ".join(config.registry_diagnostics),
    )


def build_doctor_report(config: DevBotConfig, *, ci: bool = False) -> DoctorReport:
    checks = list(run_startup_checks(config).checks)
    checks.append(check_daemon_lock(config.lock_file))
    checks.append(check_github_connectivity(config))
    checks.append(check_agent_roles(config))
    checks.append(check_repository_registrations(config))
    if ci:
        checks.append(
            StartupCheck(
                "agent_execution_readiness[ci]",
                True,
                "skipped Agent executable/auth checks in CI profile",
            )
        )
    else:
        checks.append(
            check_agent_execution_readiness(config.implementer_agent, "implementer", config)
        )
        checks.append(check_agent_execution_readiness(config.reviewer_agent, "reviewer", config))
    manager = WorktreeManager(workspace_root=config.workspace_root)
    for repository in config.enabled_repositories:
        checks.append(check_worktree_health(repository, manager))
    return DoctorReport(checks=tuple(checks))


def render_doctor_report(report: DoctorReport) -> str:
    lines = ["DevBot Doctor", ""]
    for check in report.checks:
        status = "OK" if check.ok else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.detail}")
    lines.append("")
    lines.append(f"safe_to_start: {'yes' if report.safe_to_start else 'no'}")
    return "\n".join(lines)
