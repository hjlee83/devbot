import subprocess
from pathlib import Path

from devbot.models import DevBotConfig, RepositoryConfig
from devbot.startup import (
    check_current_branch_compatibility,
    check_daemon_lock,
    check_github_credentials,
    check_repository_configuration,
    check_timeline_configuration,
    check_workspace_cleanliness,
    run_startup_checks,
)


def _repo(
    local_path: Path, *, enabled: bool = True, default_branch: str = "main"
) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=enabled,
        local_path=local_path,
        default_branch=default_branch,
    )


def _config(repositories: list[RepositoryConfig], **overrides: object) -> DevBotConfig:
    values: dict[str, object] = {
        "workspace_root": Path("/tmp/workspace"),
        "poll_interval_seconds": 60,
        "lock_file": Path("/tmp/devbot-startup-test.lock"),
        "default_agent": "codex",
        "implementer_agent": "codex",
        "reviewer_agent": "codex",
        "max_concurrent_jobs": 1,
        "dry_run": True,
        "github_token": "test-token",
        "repositories": tuple(repositories),
    }
    values.update(overrides)
    return DevBotConfig(**values)  # type: ignore[arg-type]


def _run_git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True)


def _init_git_repo(path: Path, *, branch: str | None = None) -> None:
    """`git init`'s default initial branch name depends on the runner's
    `init.defaultBranch` config (`main` locally, `master` on the GitHub
    Actions runner used by CI) - explicitly renaming to `main` right after
    the first commit keeps every test deterministic regardless of that
    environment default, instead of only passing by coincidence wherever
    `main` already happens to be the default (CI failure discovered via
    PR #36 review, `test_startup_validation_passes_for_clean_enabled_repository`)."""
    path.mkdir(parents=True, exist_ok=True)
    _run_git("init", "-q", cwd=path)
    _run_git("config", "user.email", "test@example.com", cwd=path)
    _run_git("config", "user.name", "Test", cwd=path)
    (path / "README.md").write_text("hello\n", encoding="utf-8")
    _run_git("add", ".", cwd=path)
    _run_git("commit", "-q", "-m", "initial", cwd=path)
    _run_git("branch", "-m", "main", cwd=path)
    if branch is not None:
        _run_git("checkout", "-q", "-b", branch, cwd=path)


# ---- CP-019-4: startup validation ----


def test_startup_validation(tmp_path: Path) -> None:
    """CP-019-4: startup validation runs every required check before
    polling begins, and never fails/aborts by itself - a missing/dirty
    workspace is reported (WARNING-level), not fatal (see
    `devbot.startup`'s module docstring)."""
    missing_repo_path = tmp_path / "does-not-exist"
    repository = _repo(missing_repo_path)
    config = _config([repository])

    report = run_startup_checks(config)

    names = {check.name for check in report.checks}
    assert "repository_configuration" in names
    assert "github_credentials" in names
    assert "timeline_configuration" in names
    assert f"workspace_cleanliness[{repository.full_name}]" in names
    assert f"current_branch_compatibility[{repository.full_name}]" in names

    # A missing workspace never aborts startup validation itself.
    assert report.ok is False  # reported, but see below: `main()` never treats this as fatal.
    workspace_check = next(
        c for c in report.checks if c.name == f"workspace_cleanliness[{repository.full_name}]"
    )
    assert workspace_check.ok is False
    assert workspace_check.fatal is False


def test_startup_validation_passes_for_clean_enabled_repository(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    config = _config([_repo(repo_path)])

    report = run_startup_checks(config)

    assert report.ok is True


def test_startup_validation_zero_enabled_repositories_is_non_fatal_warning() -> None:
    config = _config([_repo(Path("/tmp/does-not-matter"), enabled=False)])

    check = check_repository_configuration(config)

    assert check.ok is False
    assert check.fatal is False


def test_startup_validation_reports_credentials_present() -> None:
    config = _config([], github_token="a-real-token")  # type: ignore[list-item]

    check = check_github_credentials(config)

    assert check.ok is True


def test_startup_validation_timeline_configuration_ok() -> None:
    assert check_timeline_configuration().ok is True


def test_workspace_cleanliness_detects_dirty_workspace(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    (repo_path / "untracked.txt").write_text("uncommitted\n", encoding="utf-8")

    check = check_workspace_cleanliness(_repo(repo_path))

    assert check.ok is False
    assert check.fatal is False


def test_current_branch_compatibility_accepts_default_branch(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path)
    default_branch = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=str(repo_path),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    check = check_current_branch_compatibility(_repo(repo_path, default_branch=default_branch))

    assert check.ok is True


def test_current_branch_compatibility_accepts_devbot_task_branch(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path, branch="devbot/myrepo-1-fix")

    check = check_current_branch_compatibility(_repo(repo_path, default_branch="main"))

    assert check.ok is True


def test_current_branch_compatibility_flags_unexpected_branch(tmp_path: Path) -> None:
    repo_path = tmp_path / "myrepo"
    _init_git_repo(repo_path, branch="some-random-feature-branch")

    check = check_current_branch_compatibility(_repo(repo_path, default_branch="main"))

    assert check.ok is False
    assert check.fatal is False


def test_daemon_lock_reports_free_when_unlocked(tmp_path: Path) -> None:
    lock_file = tmp_path / "devbot.lock"

    check = check_daemon_lock(lock_file)

    assert check.ok is True
    assert check.fatal is True


def test_daemon_lock_reports_busy_when_held(tmp_path: Path) -> None:
    from devbot.lock import ProcessLock

    lock_file = tmp_path / "devbot.lock"
    holder = ProcessLock(lock_file)
    holder.acquire()
    try:
        check = check_daemon_lock(lock_file)
    finally:
        holder.release()

    assert check.ok is False
    assert check.fatal is True
