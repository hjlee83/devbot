from __future__ import annotations

import os
from pathlib import Path

import pytest

from devbot.config import ConfigError, load_config
from devbot.registry_reload import RegistryReloadMonitor
from devbot.repository_registry import initialize_repository, unregister_repository


def _write_env(tmp_path: Path) -> Path:
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"GITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}\n",
        encoding="utf-8",
    )
    return env_path


def _repo_root(path: Path) -> Path:
    path.mkdir()
    (path / ".git").mkdir()
    return path


def _touch(path: Path) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))


def test_registry_reload_monitor_reports_addition_and_removal(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path)
    registry_path = tmp_path / "registry.yaml"
    first = _repo_root(tmp_path / "first")
    initialize_repository(first, owner="someone", repo="first", registry_path=registry_path)
    initial_config = load_config(env_path=env_path, registry_path=registry_path)
    monitor = RegistryReloadMonitor(
        initial_config=initial_config,
        env_path=env_path,
        registry_path=registry_path,
    )

    assert monitor.check() is None

    second = _repo_root(tmp_path / "second")
    initialize_repository(second, owner="someone", repo="second", registry_path=registry_path)
    added = monitor.check()

    assert added is not None
    assert added.added == ("someone/second",)
    assert added.removed == ()
    assert added.unchanged_count == 1

    unregister_repository(registry_path, first)
    removed = monitor.check()

    assert removed is not None
    assert removed.added == ()
    assert removed.removed == ("someone/first",)
    assert removed.unchanged_count == 1


def test_registry_reload_monitor_allows_last_repository_removal(tmp_path: Path) -> None:
    env_path = _write_env(tmp_path)
    registry_path = tmp_path / "registry.yaml"
    repo_path = _repo_root(tmp_path / "only")
    initialize_repository(repo_path, owner="someone", repo="only", registry_path=registry_path)
    initial_config = load_config(env_path=env_path, registry_path=registry_path)
    monitor = RegistryReloadMonitor(
        initial_config=initial_config,
        env_path=env_path,
        registry_path=registry_path,
    )

    unregister_repository(registry_path, repo_path)
    reloaded = monitor.check()

    assert reloaded is not None
    assert reloaded.added == ()
    assert reloaded.removed == ("someone/only",)
    assert reloaded.unchanged_count == 0
    assert reloaded.config.enabled_repositories == ()


def test_registry_reload_monitor_reuses_legacy_duplicate_validation(
    tmp_path: Path,
) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            (
                f"WORKSPACE_ROOT={workspace_root}",
                "GITHUB_TOKEN=test-token",
                f"DEVBOT_LOCK_FILE={tmp_path / 'devbot.lock'}",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: first\n    enabled: true\n",
        encoding="utf-8",
    )
    registry_path = tmp_path / "registry.yaml"
    registered = _repo_root(tmp_path / "registered")
    initialize_repository(
        registered, owner="someone", repo="second", registry_path=registry_path
    )
    initial_config = load_config(
        env_path=env_path,
        repositories_path=repositories_path,
        registry_path=registry_path,
    )
    monitor = RegistryReloadMonitor(
        initial_config=initial_config,
        env_path=env_path,
        repositories_path=repositories_path,
        registry_path=registry_path,
    )

    duplicate = _repo_root(tmp_path / "duplicate")
    initialize_repository(duplicate, owner="someone", repo="first", registry_path=registry_path)

    with pytest.raises(ConfigError, match="someone/first"):
        monitor.check()


def test_registry_reload_monitor_retries_after_malformed_registry_changes(
    tmp_path: Path,
) -> None:
    env_path = _write_env(tmp_path)
    registry_path = tmp_path / "registry.yaml"
    first = _repo_root(tmp_path / "first")
    initialize_repository(first, owner="someone", repo="first", registry_path=registry_path)
    initial_config = load_config(env_path=env_path, registry_path=registry_path)
    monitor = RegistryReloadMonitor(
        initial_config=initial_config,
        env_path=env_path,
        registry_path=registry_path,
    )
    registry_path.write_text("repositories: [unterminated\n", encoding="utf-8")
    _touch(registry_path)

    with pytest.raises(Exception):
        monitor.check()

    second = _repo_root(tmp_path / "second")
    (second / ".devbot").mkdir()
    (second / ".devbot" / "config.yaml").write_text(
        "owner: someone\nrepo: second\nenabled: true\n", encoding="utf-8"
    )
    registry_path.write_text(
        f"repositories:\n  - path: {second.resolve()}\n    registered_at: now\n",
        encoding="utf-8",
    )
    _touch(registry_path)
    reloaded = monitor.check()

    assert reloaded is not None
    assert {repo.full_name for repo in reloaded.config.enabled_repositories} == {
        "someone/second"
    }
