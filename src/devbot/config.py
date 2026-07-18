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

from devbot.agents import KNOWN_AGENT_NAMES
from devbot.models import DevBotConfig, RepositoryConfig

DEFAULT_REPOSITORIES_PATH = Path("config/repositories.yaml")

_DEFAULTS: dict[str, str] = {
    "POLL_INTERVAL_SECONDS": "60",
    "DEVBOT_LOCK_FILE": "/tmp/devbot.lock",
    "DEFAULT_AGENT": "codex",
    "MAX_CONCURRENT_JOBS": "1",
    "DRY_RUN": "true",
    "AUTOMERGE_ENABLED": "false",
    "LOG_LEVEL": "INFO",
}

_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}

# Fallback used only when neither a role-specific agent nor the legacy
# DEFAULT_AGENT is configured at all (a brand new deployment). Existing
# deployments that already set DEFAULT_AGENT keep using that value for both
# roles - see `_resolve_role_agent`.
_DEFAULT_IMPLEMENTER_AGENT = "claude"
_DEFAULT_REVIEWER_AGENT = "codex"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


def _get_env(name: str) -> str:
    return os.environ.get(name, _DEFAULTS.get(name, ""))


def _require_nonempty(name: str, value: str) -> str:
    if not value.strip():
        raise ConfigError(f"{name} must not be empty")
    return value


def _parse_bool(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigError(f"{name} must be a boolean-like value (true/false), got: {value!r}")


def _parse_int(name: str, value: str) -> int:
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got: {value!r}") from exc


def _require_positive_int(name: str, value: int) -> int:
    if value < 1:
        raise ConfigError(f"{name} must be >= 1, got: {value}")
    return value


def _require_valid_log_level(name: str, value: str) -> str:
    normalized = value.strip().upper()
    if normalized not in _ALLOWED_LOG_LEVELS:
        raise ConfigError(f"{name} must be one of {sorted(_ALLOWED_LOG_LEVELS)}, got: {value!r}")
    return normalized


def _resolve_role_agent(
    role_env_name: str, raw_default_agent: str | None, built_in_default: str
) -> str:
    """Resolve one role's agent name: explicit role env var, else the
    legacy `DEFAULT_AGENT` (unset-role-config compatibility with existing
    single-agent deployments), else the role's own built-in default."""
    raw_role_value = os.environ.get(role_env_name)
    return raw_role_value or raw_default_agent or built_in_default


def _require_known_agent(name: str, value: str) -> str:
    if value not in KNOWN_AGENT_NAMES:
        raise ConfigError(f"{name} must be one of {sorted(KNOWN_AGENT_NAMES)}, got: {value!r}")
    return value


def _parse_repository_enabled(value: object, index: int) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE_VALUES | _FALSE_VALUES:
        return value.strip().lower() in _TRUE_VALUES
    raise ConfigError(f"repositories[{index}].enabled must be a boolean, got: {value!r}")


def _load_repositories(
    repositories_path: Path, workspace_root: Path
) -> tuple[RepositoryConfig, ...]:
    if not repositories_path.is_file():
        raise ConfigError(f"Missing required repositories file: {repositories_path}")

    with repositories_path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise ConfigError(f"Invalid YAML in {repositories_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{repositories_path} must contain a mapping at the top level")

    raw_repositories = raw.get("repositories")
    if not isinstance(raw_repositories, list) or not raw_repositories:
        raise ConfigError(f"{repositories_path} must define a non-empty 'repositories' list")

    repositories: list[RepositoryConfig] = []
    for index, entry in enumerate(raw_repositories):
        if not isinstance(entry, dict):
            raise ConfigError(f"repositories[{index}] must be a mapping")

        owner = entry.get("owner")
        repo = entry.get("repo")
        if not owner or not repo:
            raise ConfigError(f"repositories[{index}] requires non-empty 'owner' and 'repo'")

        enabled = _parse_repository_enabled(entry.get("enabled", True), index)
        automerge_allowed = _parse_repository_enabled(
            entry.get("automerge_allowed", False), index
        )
        is_self_repo = _parse_repository_enabled(entry.get("is_self_repo", False), index)
        default_branch = str(entry.get("default_branch") or "main")
        repositories.append(
            RepositoryConfig(
                owner=str(owner),
                repo=str(repo),
                enabled=enabled,
                local_path=workspace_root / str(repo),
                default_branch=default_branch,
                automerge_allowed=automerge_allowed,
                is_self_repo=is_self_repo,
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

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise ConfigError("Missing required environment variable: GITHUB_TOKEN")

    poll_interval_seconds = _parse_int("POLL_INTERVAL_SECONDS", _get_env("POLL_INTERVAL_SECONDS"))
    max_concurrent_jobs = _require_positive_int(
        "MAX_CONCURRENT_JOBS", _parse_int("MAX_CONCURRENT_JOBS", _get_env("MAX_CONCURRENT_JOBS"))
    )
    lock_file_raw = _require_nonempty("DEVBOT_LOCK_FILE", _get_env("DEVBOT_LOCK_FILE"))
    lock_file = Path(lock_file_raw).expanduser()
    default_agent = _require_nonempty("DEFAULT_AGENT", _get_env("DEFAULT_AGENT"))
    dry_run = _parse_bool("DRY_RUN", _get_env("DRY_RUN"))
    automerge_enabled = _parse_bool("AUTOMERGE_ENABLED", _get_env("AUTOMERGE_ENABLED"))
    log_level = _require_valid_log_level("LOG_LEVEL", _get_env("LOG_LEVEL"))

    # `os.environ.get` (not `_get_env`) here so an *unset* DEFAULT_AGENT is
    # distinguishable from one explicitly set to its own package default -
    # only an unset DEFAULT_AGENT falls through to the role's built-in
    # default below.
    raw_default_agent = os.environ.get("DEFAULT_AGENT")
    implementer_agent = _require_known_agent(
        "IMPLEMENTER_AGENT",
        _resolve_role_agent("IMPLEMENTER_AGENT", raw_default_agent, _DEFAULT_IMPLEMENTER_AGENT),
    )
    reviewer_agent = _require_known_agent(
        "REVIEWER_AGENT",
        _resolve_role_agent("REVIEWER_AGENT", raw_default_agent, _DEFAULT_REVIEWER_AGENT),
    )

    repositories_path_raw = (
        str(repositories_path)
        if repositories_path is not None
        else os.environ.get("DEVBOT_REPOSITORIES_PATH")
    )
    resolved_repositories_path = (
        Path(repositories_path_raw) if repositories_path_raw else DEFAULT_REPOSITORIES_PATH
    )
    repositories = _load_repositories(resolved_repositories_path, workspace_root)

    return DevBotConfig(
        workspace_root=workspace_root,
        poll_interval_seconds=poll_interval_seconds,
        lock_file=lock_file,
        default_agent=default_agent,
        implementer_agent=implementer_agent,
        reviewer_agent=reviewer_agent,
        max_concurrent_jobs=max_concurrent_jobs,
        dry_run=dry_run,
        automerge_enabled=automerge_enabled,
        github_token=github_token,
        repositories=repositories,
        log_level=log_level,
    )
