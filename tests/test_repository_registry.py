import multiprocessing
import shutil
import subprocess
from pathlib import Path

import pytest

from devbot.repository_registry import (
    RepositoryLocalConfig,
    RepositoryRegistrationError,
    detect_owner_repo_from_git_remote,
    find_git_repository_root,
    initialize_repository,
    load_registry,
    load_repository_local_config,
    register_repository,
    repo_local_config_path,
    resolve_registered_repositories,
    unregister_repository,
    write_repository_local_config,
)


def _init_git_repo(path: Path, *, remote_url: str | None = None) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    if remote_url is not None:
        subprocess.run(
            ["git", "remote", "add", "origin", remote_url], cwd=str(path), check=True
        )
    return path


def _register_repository_process(
    registry_path: str, repo_root: str, result_queue: multiprocessing.Queue
) -> None:
    from pathlib import Path

    from devbot.repository_registry import register_repository

    try:
        result_queue.put(("ok", register_repository(Path(registry_path), Path(repo_root))))
    except Exception as exc:  # noqa: BLE001 - test helper returns the child failure
        result_queue.put(("error", repr(exc)))


def _unregister_repository_process(
    registry_path: str, repo_root: str, result_queue: multiprocessing.Queue
) -> None:
    from pathlib import Path

    from devbot.repository_registry import unregister_repository

    try:
        result_queue.put(("ok", unregister_repository(Path(registry_path), Path(repo_root))))
    except Exception as exc:  # noqa: BLE001 - test helper returns the child failure
        result_queue.put(("error", repr(exc)))


def _hold_registry_lock_process(
    registry_path: str, ready_queue: multiprocessing.Queue, release_event: multiprocessing.Event
) -> None:
    import fcntl
    import os
    from pathlib import Path

    lock_path = Path(registry_path).with_name(f".{Path(registry_path).name}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        ready_queue.put("locked")
        release_event.wait(timeout=10)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# --------------------------------------------------------------------------
# find_git_repository_root
# --------------------------------------------------------------------------


def test_find_git_repository_root_from_nested_directory(tmp_path: Path) -> None:
    repo_root = _init_git_repo(tmp_path / "repo")
    nested = repo_root / "src" / "devbot"
    nested.mkdir(parents=True)

    assert find_git_repository_root(nested) == repo_root.resolve()


def test_find_git_repository_root_rejects_non_git_directory(tmp_path: Path) -> None:
    plain = tmp_path / "not-a-repo"
    plain.mkdir()

    with pytest.raises(RepositoryRegistrationError, match="not inside a Git repository"):
        find_git_repository_root(plain)


# --------------------------------------------------------------------------
# detect_owner_repo_from_git_remote
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remote_url",
    [
        "git@github.com:someone/myrepo.git",
        "https://github.com/someone/myrepo.git",
        "https://github.com/someone/myrepo",
    ],
)
def test_detect_owner_repo_from_git_remote_parses_common_url_forms(
    tmp_path: Path, remote_url: str
) -> None:
    repo_root = _init_git_repo(tmp_path / "repo", remote_url=remote_url)

    assert detect_owner_repo_from_git_remote(repo_root) == ("someone", "myrepo")


def test_detect_owner_repo_from_git_remote_returns_none_without_a_remote(
    tmp_path: Path,
) -> None:
    repo_root = _init_git_repo(tmp_path / "repo")

    assert detect_owner_repo_from_git_remote(repo_root) is None


# --------------------------------------------------------------------------
# Repository-local config (`.devbot/config.yaml`)
# --------------------------------------------------------------------------


def test_write_and_load_repository_local_config_round_trips(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = RepositoryLocalConfig(
        owner="someone", repo="myrepo", enabled=True, default_branch="develop"
    )

    created = write_repository_local_config(repo_root, config)
    loaded = load_repository_local_config(repo_root)

    assert created is True
    assert loaded == config
    assert repo_local_config_path(repo_root).is_file()


def test_write_repository_local_config_is_idempotent(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    config = RepositoryLocalConfig(owner="someone", repo="myrepo")

    first = write_repository_local_config(repo_root, config)
    second = write_repository_local_config(repo_root, config)

    assert first is True
    assert second is False


def test_load_repository_local_config_requires_owner_and_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    config_path = repo_local_config_path(repo_root)
    config_path.parent.mkdir(parents=True)
    config_path.write_text("enabled: true\n", encoding="utf-8")

    with pytest.raises(RepositoryRegistrationError, match="owner"):
        load_repository_local_config(repo_root)


# --------------------------------------------------------------------------
# Global registry (register/unregister/load)
# --------------------------------------------------------------------------


def test_register_repository_is_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    first = register_repository(registry_path, repo_root)
    second = register_repository(registry_path, repo_root)

    assert first is True
    assert second is False
    assert len(load_registry(registry_path)) == 1


def test_register_repository_keeps_existing_registry_when_atomic_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    register_repository(registry_path, repo_a)
    original_contents = registry_path.read_text(encoding="utf-8")

    def fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr("devbot.repository_registry.os.replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        register_repository(registry_path, repo_b)

    assert registry_path.read_text(encoding="utf-8") == original_contents
    assert [entry.path for entry in load_registry(registry_path)] == [repo_a.resolve()]


def test_concurrent_register_repository_preserves_both_entries(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_register_repository_process,
            args=(str(registry_path), str(repo_a), result_queue),
        ),
        multiprocessing.Process(
            target=_register_repository_process,
            args=(str(registry_path), str(repo_b), result_queue),
        ),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert [result_queue.get(timeout=5)[0] for _ in processes] == ["ok", "ok"]
    assert {entry.path for entry in load_registry(registry_path)} == {
        repo_a.resolve(),
        repo_b.resolve(),
    }


def test_concurrent_register_and_unregister_does_not_lose_updates(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    register_repository(registry_path, repo_a)
    result_queue: multiprocessing.Queue = multiprocessing.Queue()
    processes = [
        multiprocessing.Process(
            target=_unregister_repository_process,
            args=(str(registry_path), str(repo_a), result_queue),
        ),
        multiprocessing.Process(
            target=_register_repository_process,
            args=(str(registry_path), str(repo_b), result_queue),
        ),
    ]

    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert [result_queue.get(timeout=5)[0] for _ in processes] == ["ok", "ok"]
    assert [entry.path for entry in load_registry(registry_path)] == [repo_b.resolve()]


def test_register_repository_lock_timeout_preserves_existing_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_a.mkdir()
    repo_b.mkdir()
    register_repository(registry_path, repo_a)
    original_contents = registry_path.read_text(encoding="utf-8")
    ready_queue: multiprocessing.Queue = multiprocessing.Queue()
    release_event = multiprocessing.Event()
    holder = multiprocessing.Process(
        target=_hold_registry_lock_process,
        args=(str(registry_path), ready_queue, release_event),
    )
    holder.start()
    try:
        assert ready_queue.get(timeout=5) == "locked"
        monkeypatch.setenv("DEVBOT_REGISTRY_LOCK_TIMEOUT_SECONDS", "0.01")

        with pytest.raises(RepositoryRegistrationError, match="registry lock timeout"):
            register_repository(registry_path, repo_b)

        assert registry_path.read_text(encoding="utf-8") == original_contents
        assert [entry.path for entry in load_registry(registry_path)] == [repo_a.resolve()]
    finally:
        release_event.set()
        holder.join(timeout=10)
        if holder.is_alive():
            holder.terminate()
            holder.join(timeout=10)


def test_unregister_repository_returns_false_when_not_registered(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    removed = unregister_repository(registry_path, repo_root)

    assert removed is False


def test_load_registry_returns_empty_tuple_for_missing_file(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "does-not-exist.yaml") == ()


# --------------------------------------------------------------------------
# resolve_registered_repositories: happy path + diagnostics
# --------------------------------------------------------------------------


def test_resolve_registered_repositories_happy_path(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_repository_local_config(
        repo_root, RepositoryLocalConfig(owner="someone", repo="myrepo")
    )
    register_repository(registry_path, repo_root)

    resolution = resolve_registered_repositories(registry_path)

    assert resolution.diagnostics == ()
    assert len(resolution.repositories) == 1
    assert resolution.repositories[0].owner == "someone"
    assert resolution.repositories[0].repo == "myrepo"
    assert resolution.repositories[0].local_path == repo_root.resolve()


def test_resolve_registered_repositories_reports_missing_path(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    write_repository_local_config(
        repo_root, RepositoryLocalConfig(owner="someone", repo="myrepo")
    )
    register_repository(registry_path, repo_root)
    shutil.rmtree(repo_root)

    resolution = resolve_registered_repositories(registry_path)

    assert resolution.repositories == ()
    assert len(resolution.diagnostics) == 1
    assert resolution.diagnostics[0].kind == "missing_path"


def test_resolve_registered_repositories_reports_duplicate_owner_repo(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_a = tmp_path / "a"
    repo_b = tmp_path / "b"
    repo_c = tmp_path / "c"
    repo_a.mkdir()
    repo_b.mkdir()
    repo_c.mkdir()
    write_repository_local_config(repo_a, RepositoryLocalConfig(owner="someone", repo="myrepo"))
    write_repository_local_config(repo_b, RepositoryLocalConfig(owner="someone", repo="myrepo"))
    write_repository_local_config(repo_c, RepositoryLocalConfig(owner="someone", repo="otherrepo"))
    register_repository(registry_path, repo_a)
    register_repository(registry_path, repo_b)
    register_repository(registry_path, repo_c)

    resolution = resolve_registered_repositories(registry_path)

    assert len(resolution.repositories) == 1
    assert resolution.repositories[0].local_path == repo_c.resolve()
    assert len(resolution.diagnostics) == 1
    assert resolution.diagnostics[0].kind == "duplicate_repository"
    assert str(repo_a.resolve()) in resolution.diagnostics[0].message
    assert str(repo_b.resolve()) in resolution.diagnostics[0].message


def test_resolve_registered_repositories_reports_invalid_config(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    register_repository(registry_path, repo_root)  # no .devbot/config.yaml ever written

    resolution = resolve_registered_repositories(registry_path)

    assert resolution.repositories == ()
    assert len(resolution.diagnostics) == 1
    assert resolution.diagnostics[0].kind == "invalid_config"


# --------------------------------------------------------------------------
# initialize_repository: devbot init's core logic
# --------------------------------------------------------------------------


def test_initialize_repository_detects_owner_repo_from_remote(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )

    result = initialize_repository(repo_root, registry_path=registry_path)

    assert result.owner == "someone"
    assert result.repo == "myrepo"
    assert result.local_config_created is True
    assert result.already_registered is False
    assert load_repository_local_config(repo_root).owner == "someone"


def test_initialize_repository_explicit_owner_repo_overrides_remote(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )

    result = initialize_repository(
        repo_root, owner="other", repo="renamed", registry_path=registry_path
    )

    assert result.owner == "other"
    assert result.repo == "renamed"


def test_initialize_repository_without_remote_or_override_raises(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = _init_git_repo(tmp_path / "repo")

    with pytest.raises(RepositoryRegistrationError, match="owner/repo"):
        initialize_repository(repo_root, registry_path=registry_path)


def test_initialize_repository_is_idempotent(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.yaml"
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )

    first = initialize_repository(repo_root, registry_path=registry_path)
    second = initialize_repository(repo_root, registry_path=registry_path)

    assert first.local_config_created is True
    assert first.already_registered is False
    assert second.local_config_created is False
    assert second.already_registered is True
    assert len(load_registry(registry_path)) == 1


def test_initialize_repository_preserves_existing_settings_on_rerun(tmp_path: Path) -> None:
    """Re-running `devbot init` (e.g. after `--automerge-allowed` was set
    once) must not silently revert a previously-set field back to its
    default."""
    registry_path = tmp_path / "registry.yaml"
    repo_root = _init_git_repo(
        tmp_path / "repo", remote_url="git@github.com:someone/myrepo.git"
    )
    initialize_repository(
        repo_root, automerge_allowed=True, registry_path=registry_path
    )

    initialize_repository(repo_root, registry_path=registry_path)

    assert load_repository_local_config(repo_root).automerge_allowed is True
