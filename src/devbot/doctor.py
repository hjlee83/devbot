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

from devbot.agents.codex import CodexRunner
from devbot.github_client import GitHubClient, GitHubClientError
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.startup import StartupCheck, check_daemon_lock, run_startup_checks
from devbot.worktree import WorktreeManager


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


def check_agent_execution_readiness(agent_name: str, role: str) -> StartupCheck:
    name = f"agent_execution_readiness[{role}:{agent_name}]"
    executable = "codex" if agent_name == "codex" else "claude" if agent_name == "claude" else ""
    if not executable or shutil.which(executable) is None:
        return StartupCheck(name, False, f"Agent executable not found: {agent_name}")
    version = subprocess.run(
        [executable, "--version"], capture_output=True, text=True, check=False
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
    return StartupCheck(
        name,
        True,
        f"version={(version.stdout or version.stderr).strip()} availability=True",
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


def build_doctor_report(config: DevBotConfig) -> DoctorReport:
    checks = list(run_startup_checks(config).checks)
    checks.append(check_daemon_lock(config.lock_file))
    checks.append(check_github_connectivity(config))
    checks.append(check_agent_roles(config))
    checks.append(check_agent_execution_readiness(config.implementer_agent, "implementer"))
    checks.append(check_agent_execution_readiness(config.reviewer_agent, "reviewer"))
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
