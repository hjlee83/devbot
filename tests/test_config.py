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


def _write_env(path: Path, workspace_root: Path | None, **extra: str) -> Path:
    file_path = path / ".env"
    lines = []
    if workspace_root is not None:
        lines.append(f"WORKSPACE_ROOT={workspace_root}")
    lines.append("GITHUB_TOKEN=test-token")
    lines.append("POLL_INTERVAL_SECONDS=15")
    lines.append("DEFAULT_AGENT=codex")
    for key, value in extra.items():
        lines.append(f"{key}={value}")
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return file_path


def test_load_valid_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    env_path = _write_env(tmp_path, workspace_root=None)
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="WORKSPACE_ROOT"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_missing_github_token_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = tmp_path / ".env"
    env_path.write_text(f"WORKSPACE_ROOT={workspace_root}\n", encoding="utf-8")
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_repository_path_is_derived_from_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "some-workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path, repo="myrepo", enabled=True)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.repositories[0].local_path == workspace_root / "myrepo"


def test_invalid_dry_run_value_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root, DRY_RUN="maybe")
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="DRY_RUN"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_max_concurrent_jobs_defaults_to_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-012-10: with no MAX_CONCURRENT_JOBS set at all, config loading
    defaults to 1 - the existing serial-execution safety default."""
    monkeypatch.delenv("MAX_CONCURRENT_JOBS", raising=False)
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.max_concurrent_jobs == 1


@pytest.mark.parametrize("bad_value", ["0", "-1", "not-a-number", "1.5"])
def test_invalid_max_concurrent_jobs_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_value: str
) -> None:
    """CP-012-14: zero, negative, and non-integer MAX_CONCURRENT_JOBS
    values all fail at config-loading time."""
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root, MAX_CONCURRENT_JOBS=bad_value)
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="MAX_CONCURRENT_JOBS"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_empty_default_agent_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root, DEFAULT_AGENT="")
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="DEFAULT_AGENT"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_config_rejects_unknown_implementer_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-011-7: an unrecognized IMPLEMENTER_AGENT fails config loading -
    it must not silently fall back to a default Runner.

    Set via `monkeypatch.setenv` (not the `.env` file) so pytest reverts it
    after the test - `load_dotenv(..., override=False)` never clears a
    value once it lands in the real process environment, and every later
    test in the session would otherwise inherit this bogus agent name."""
    monkeypatch.setenv("IMPLEMENTER_AGENT", "gpt5")
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="IMPLEMENTER_AGENT"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_config_rejects_unknown_reviewer_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-011-8: an unrecognized REVIEWER_AGENT fails config loading - it
    must not silently fall back to a default Runner."""
    monkeypatch.setenv("REVIEWER_AGENT", "gemini")
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path)

    with pytest.raises(ConfigError, match="REVIEWER_AGENT"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_role_agents_default_to_claude_and_codex_when_nothing_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A brand new deployment with no DEFAULT_AGENT and no role-specific
    env vars at all gets implementer=claude, reviewer=codex."""
    monkeypatch.delenv("DEFAULT_AGENT", raising=False)
    monkeypatch.delenv("IMPLEMENTER_AGENT", raising=False)
    monkeypatch.delenv("REVIEWER_AGENT", raising=False)
    workspace_root = tmp_path / "workspace"
    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n", encoding="utf-8"
    )
    repositories_path = _write_repositories_yaml(tmp_path)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.implementer_agent == "claude"
    assert config.reviewer_agent == "codex"


def test_role_agents_fall_back_to_default_agent_for_existing_deployments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CP-011 회귀 방지: DEFAULT_AGENT만 설정된 기존 배포는 역할별 설정 없이
    두 역할 모두 DEFAULT_AGENT 값을 그대로 쓴다."""
    monkeypatch.delenv("IMPLEMENTER_AGENT", raising=False)
    monkeypatch.delenv("REVIEWER_AGENT", raising=False)
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root, DEFAULT_AGENT="codex")
    repositories_path = _write_repositories_yaml(tmp_path)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.default_agent == "codex"
    assert config.implementer_agent == "codex"
    assert config.reviewer_agent == "codex"


def test_role_agents_can_be_set_independently_of_default_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMPLEMENTER_AGENT", "claude")
    monkeypatch.setenv("REVIEWER_AGENT", "codex")
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = _write_repositories_yaml(tmp_path)

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.implementer_agent == "claude"
    assert config.reviewer_agent == "codex"


def test_invalid_repository_enabled_value_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: maybe\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="enabled"):
        load_config(env_path=env_path, repositories_path=repositories_path)


def test_repository_enabled_quoted_false_string_is_parsed_as_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression test: a YAML string "false" must not be coerced truthy."""
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        'repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: "false"\n',
        encoding="utf-8",
    )

    config = load_config(env_path=env_path, repositories_path=repositories_path)

    assert config.repositories[0].enabled is False


def test_malformed_repositories_yaml_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_root = tmp_path / "workspace"
    env_path = _write_env(tmp_path, workspace_root)
    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text("repositories: [unterminated\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="repositories.yaml"):
        load_config(env_path=env_path, repositories_path=repositories_path)
