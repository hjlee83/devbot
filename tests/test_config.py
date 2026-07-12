from pathlib import Path

import pytest

from devbot.config import ConfigError, load_config


def _write_repositories_yaml(path: Path, repo: str = "myrepo", enabled: bool = True) -> Path:
    file_path = path / "repositories.yaml"
    file_path.write_text(
        f"""
repositories:
  - owner: someone
    repo: {repo}
    enabled: {str(enabled).lower()}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return file_path


def _write_env(path: Path, workspace_root: Path | None) -> Path:
    file_path = path / ".env"
    lines = []
    if workspace_root is not None:
        lines.append(f"WORKSPACE_ROOT={workspace_root}")
    lines.append("POLL_INTERVAL_SECONDS=15")
    lines.append("DEFAULT_AGENT=codex")
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


def test_load_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path, repo="myrepo", enabled=True)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.workspace_root == workspace_root
    assert config.poll_interval_seconds == 15
    assert config.default_agent == "codex"
    assert config.max_concurrent_jobs == 1  # default
    assert config.dry_run is True  # default
    assert config.lock_file == Path("/tmp/devbot.lock")  # default
    assert len(config.repositories) == 1
    assert config.repositories[0].owner == "someone"
    assert config.repositories[0].repo == "myrepo"
    assert config.repositories[0].enabled is True


def test_missing_required_config_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    env_path = _write_env(tmp_path, workspace_root=None)
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="WORKSPACE_ROOT"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_repository_path_is_derived_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)
    workspace_root = tmp_path / "some-workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path, repo="myrepo", enabled=True)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.repositories[0].local_path == workspace_root / "myrepo"
