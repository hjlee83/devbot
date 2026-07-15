"""Startup validation (Task 019 CP-019-4).

Runs once, right after the daemon acquires its `ProcessLock` and before the
first polling cycle, and is reused (read-only, before any lock is held) by
`devbot doctor` (`devbot.doctor`).

Two genuinely fatal startup conditions already exist and are intentionally
*not* duplicated here:

- Missing/invalid repository configuration or a missing `GITHUB_TOKEN`
  already raise `ConfigError` from `devbot.config.load_config`, before a
  `DevBotConfig` (this module's input) even exists.
- A duplicate daemon instance already raises `LockAcquisitionError` from
  `devbot.lock.ProcessLock.acquire()`, before this module runs.

Both already stop `devbot.main.main()` before any polling cycle or GitHub
write, satisfying CP-019-8. This module's checks are additive visibility,
not a second fatal gate: a repository with zero enabled entries is a valid
(if idle) deployment (`PollingStatus.NO_MANAGED_REPOSITORIES` already
handles it non-fatally), and a single dirty workspace or a workspace left
on a feature branch by a previous crashed run must not stop *every other*
managed repository from polling - `devbot.polling` already handles a dirty
workspace per-job, non-fatally (`PollingStatus.WORKSPACE_INVALID`). So
every check in `run_startup_checks()` is a WARNING when it fails, logged
for operator visibility, never an abort. `devbot.doctor` layers its own
`daemon_lock` probe (invasive to run inside an already-locked process, so
it stays out of this module) on top and is the place that decides what
"safe to start" means.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from devbot import timeline
from devbot.delivery import DeliveryError, current_git_branch
from devbot.lock import LockAcquisitionError, ProcessLock
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.workspace import inspect_workspace


@dataclass(frozen=True, slots=True)
class StartupCheck:
    """One named startup validation result."""

    name: str
    ok: bool
    detail: str
    fatal: bool = False


@dataclass(frozen=True, slots=True)
class StartupValidationReport:
    """Every `StartupCheck` run for one startup validation pass."""

    checks: tuple[StartupCheck, ...]

    @property
    def failed_checks(self) -> tuple[StartupCheck, ...]:
        return tuple(check for check in self.checks if not check.ok)

    @property
    def ok(self) -> bool:
        return not self.failed_checks


def check_repository_configuration(config: DevBotConfig) -> StartupCheck:
    """Visibility only - `load_config` already guarantees `repositories` is
    non-empty; zero *enabled* repositories is a valid, already-handled idle
    deployment (see module docstring), so this is never fatal."""
    enabled = config.enabled_repositories
    if not enabled:
        return StartupCheck(
            "repository_configuration", False, "enabled 저장소가 0개입니다 (idle 상태로 동작)"
        )
    return StartupCheck(
        "repository_configuration", True, f"{len(enabled)}개 저장소가 활성화되어 있습니다"
    )


def check_github_credentials(config: DevBotConfig) -> StartupCheck:
    """Presence only - `load_config` already guarantees `github_token` is
    non-empty. Live connectivity/authentication is `devbot doctor`'s job
    (a network call has no place in every daemon startup)."""
    if not config.github_token:
        return StartupCheck("github_credentials", False, "GITHUB_TOKEN이 비어 있습니다")
    return StartupCheck(
        "github_credentials", True, "GITHUB_TOKEN 설정됨 (연결 확인은 'devbot doctor' 참고)"
    )


def check_timeline_configuration() -> StartupCheck:
    """The GitHub Status Timeline protocol's (`docs/10`) required marker
    constants are present - a regression here would silently corrupt every
    Issue's Timeline comment, so it is the one non-lock check this module
    still flags distinctly even though it currently cannot fail in
    practice."""
    if not timeline.COMMENT_MARKER or not timeline.EVENT_MARKER_NAME:
        return StartupCheck("timeline_configuration", False, "Timeline marker 상수가 비어 있습니다")
    return StartupCheck("timeline_configuration", True, "Timeline marker 상수 확인됨")


def check_workspace_cleanliness(repository: RepositoryConfig) -> StartupCheck:
    name = f"workspace_cleanliness[{repository.full_name}]"
    status = inspect_workspace(repository)
    if not status.exists:
        return StartupCheck(
            name, False, "로컬 경로가 존재하지 않습니다 (아직 clone되지 않았을 수 있음)"
        )
    if not status.is_git_repository:
        return StartupCheck(name, False, "Git 저장소가 아닙니다")
    if status.has_uncommitted_changes is None:
        return StartupCheck(name, False, "Git 상태를 확인할 수 없습니다")
    if status.has_uncommitted_changes:
        preview = ", ".join(status.changed_files[:5])
        return StartupCheck(name, False, f"미커밋 변경 {len(status.changed_files)}건: {preview}")
    return StartupCheck(name, True, "clean")


def check_current_branch_compatibility(repository: RepositoryConfig) -> StartupCheck:
    name = f"current_branch_compatibility[{repository.full_name}]"
    if not repository.local_path.is_dir() or not (repository.local_path / ".git").exists():
        return StartupCheck(name, False, "워크스페이스가 없어 현재 branch를 확인할 수 없습니다")
    try:
        branch = current_git_branch(repository)
    except DeliveryError as exc:
        return StartupCheck(name, False, f"현재 branch 확인 실패: {exc}")
    if branch == repository.default_branch or branch.startswith("devbot/"):
        return StartupCheck(name, True, f"current_branch={branch}")
    return StartupCheck(
        name,
        False,
        f"current_branch={branch} (default={repository.default_branch}도 devbot/*도 아님 - "
        "이전 실행이 남긴 미정리 상태일 수 있음)",
    )


def check_daemon_lock(lock_file: Path) -> StartupCheck:
    """Non-invasive: attempt a non-blocking acquire, then immediately
    release it. Safe to call even while another DevBot process holds the
    lock - `flock(LOCK_NB)` fails immediately without disturbing the
    current holder. Only meaningful *before* this process itself acquires
    the real lock (`devbot doctor` calls this; `devbot.main`'s daemon path
    does not - it is already inside its own `with ProcessLock(...)` block
    by the time `run_startup_checks()` runs, which would make a second
    probe against the same file self-referential)."""
    probe = ProcessLock(lock_file)
    try:
        probe.acquire()
    except LockAcquisitionError:
        return StartupCheck(
            "daemon_lock",
            False,
            f"Lock이 이미 다른 프로세스에 의해 점유 중입니다: {lock_file}",
            fatal=True,
        )
    else:
        probe.release()
        return StartupCheck("daemon_lock", True, f"Lock 사용 가능: {lock_file}", fatal=True)


def run_startup_checks(config: DevBotConfig) -> StartupValidationReport:
    """Every startup check except the daemon lock (see module docstring).
    Every check is a WARNING, not a fatal gate - `devbot.main` logs this
    report and always continues into polling."""
    checks: list[StartupCheck] = [
        check_repository_configuration(config),
        check_github_credentials(config),
        check_timeline_configuration(),
    ]
    for repository in config.enabled_repositories:
        checks.append(check_workspace_cleanliness(repository))
        checks.append(check_current_branch_compatibility(repository))
    return StartupValidationReport(checks=tuple(checks))
