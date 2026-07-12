"""Configuration loading for DevBot.

Loads environment values (via `.env` and the process environment) and the
repository list (via `config/repositories.yaml`), then merges them into a
single `DevBotConfig`.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from devbot.models import DevBotConfig, RepositoryConfig

DEFAULT_REPOSITORIES_PATH = Path("config/repositories.yaml")

_DEFAULTS: dict[str, str] = {
    "POLL_INTERVAL_SECONDS": "60",
    "DEVBOT_LOCK_FILE": "/tmp/devbot.lock",
    "DEFAULT_AGENT": "codex",
    "MAX_CONCURRENT_JOBS": "1",
    "DRY_RUN": "true",
}

_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get_env(name: str) -> str:
    return os.environ.get(name, _DEFAULTS.get(name, ""))


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in _TRUE_VALUES


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got: {value!r}") from exc


def _load_repositories(
    repositories_path: Path, workspace_root: Path
) -> tuple[RepositoryConfig, ...]:
    if not repositories_path.is_file():
        raise ConfigError(f"Missing required repositories file: {repositories_path}")

    with repositories_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    raw_repositories = raw.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ConfigError(
            f"{repositories_path} must define a non-empty 'repositories' list"
        )

    repositories: list[RepositoryConfig] = []
    for index, entry in enumerate(raw_repositories):
        if not isinstance(entry, dict):
            raise ConfigError(f"repositories[{index}] must be a mapping")

        owner = entry.get("owner")
        repo = entry.get("repo")
        if not owner or not repo:
            raise ConfigError(f"repositories[{index}] requires non-empty 'owner' and 'repo'")

        enabled = bool(entry.get("enabled", True))
        repositories.append(
            RepositoryConfig(
                owner=str(owner),
                repo=str(repo),
                enabled=enabled,
                local_path=workspace_root / str(repo),
            )
        )

    return tuple(repositories)


def load_config(
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
) -> DevBotConfig:
    """Load `.env` and `config/repositories.yaml` into a `DevBotConfig`.

    `env_path` and `repositories_path` allow tests (and alternate deployments)
    to point at fixture files instead of the real project files. Existing
    process environment variables always take precedence over `.env` values.
    """
    load_dotenv(dotenv_path=env_path, override=False)

    workspace_root_raw = os.environ.get("WORKSPACE_ROOT")
    if not workspace_root_raw:
        raise ConfigError("Missing required environment variable: WORKSPACE_ROOT")
    workspace_root = Path(workspace_root_raw).expanduser()

    poll_interval_seconds = _parse_int("POLL_INTERVAL_SECONDS", _get_env("POLL_INTERVAL_SECONDS"))
    max_concurrent_jobs = _parse_int("MAX_CONCURRENT_JOBS", _get_env("MAX_CONCURRENT_JOBS"))
    lock_file = Path(_get_env("DEVBOT_LOCK_FILE")).expanduser()
    default_agent = _get_env("DEFAULT_AGENT")
    dry_run = _parse_bool(_get_env("DRY_RUN"))

    resolved_repositories_path = (
        Path(repositories_path) if repositories_path is not None else DEFAULT_REPOSITORIES_PATH
    )
    repositories = _load_repositories(resolved_repositories_path, workspace_root)

    return DevBotConfig(
        workspace_root=workspace_root,
        poll_interval_seconds=poll_interval_seconds,
        lock_file=lock_file,
        default_agent=default_agent,
        max_concurrent_jobs=max_concurrent_jobs,
        dry_run=dry_run,
        repositories=repositories,
    )
