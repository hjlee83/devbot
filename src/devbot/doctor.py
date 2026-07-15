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

from dataclasses import dataclass

from devbot.github_client import GitHubClient, GitHubClientError
from devbot.models import DevBotConfig
from devbot.startup import StartupCheck, check_daemon_lock, run_startup_checks


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


def build_doctor_report(config: DevBotConfig) -> DoctorReport:
    checks = list(run_startup_checks(config).checks)
    checks.append(check_daemon_lock(config.lock_file))
    checks.append(check_github_connectivity(config))
    checks.append(check_agent_roles(config))
    return DoctorReport(checks=tuple(checks))


def render_doctor_report(report: DoctorReport) -> str:
    lines = ["DevBot Doctor", ""]
    for check in report.checks:
        status = "OK" if check.ok else "FAIL"
        lines.append(f"[{status}] {check.name}: {check.detail}")
    lines.append("")
    lines.append(f"safe_to_start: {'yes' if report.safe_to_start else 'no'}")
    return "\n".join(lines)
