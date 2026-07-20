"""Configuration loading for DevBot.

Loads environment values (via `.env` and the process environment) and the
repository list, then merges them into a single `DevBotConfig`. Two
independent, additive repository sources exist (Issue #122):

- Legacy: `WORKSPACE_ROOT` + `config/repositories.yaml`, each repository's
  `local_path` derived as `WORKSPACE_ROOT / repo`. Unchanged in behavior -
  only loaded when `WORKSPACE_ROOT` is actually set, so an existing
  deployment that never touches `devbot init` sees no difference.
- Registered: `devbot init`-registered repositories
  (`devbot.repository_registry`), each with its own authoritative absolute
  path, independent of any shared parent directory.

Both sources may be present at once (a deployment can migrate repository by
repository); their `repositories` are simply concatenated, then checked for
`owner/repo` collisions across the combined set.
"""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from dotenv import load_dotenv

from devbot.agents import KNOWN_AGENT_NAMES
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.repository_registry import default_registry_path, resolve_registered_repositories

DEFAULT_REPOSITORIES_PATH = Path("config/repositories.yaml")

# "3" mirrors `devbot.review.DEFAULT_REVIEW_LOOP_LIMIT` - duplicated as a
# literal (not imported) to avoid a circular import: `devbot.review` ->
# `devbot.issue_state` -> `devbot.observability` -> `devbot.reliability` ->
# `devbot.config` (confirmed empirically, not just by direct-import
# inspection - the cycle is transitive, three modules deep).
_DEFAULTS: dict[str, str] = {
    "POLL_INTERVAL_SECONDS": "60",
    "DEVBOT_LOCK_FILE": "/tmp/devbot.lock",
    "DEFAULT_AGENT": "codex",
    "MAX_CONCURRENT_JOBS": "1",
    "AI_CONCURRENCY": "1",
    "DRY_RUN": "true",
    "AUTOMERGE_ENABLED": "false",
    "LOG_LEVEL": "INFO",
    "REVIEW_LOOP_LIMIT": "3",
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
        raw_publish_strategy = entry.get("publish_strategy")
        publish_strategy = str(raw_publish_strategy) if raw_publish_strategy is not None else None
        repositories.append(
            RepositoryConfig(
                owner=str(owner),
                repo=str(repo),
                enabled=enabled,
                local_path=workspace_root / str(repo),
                default_branch=default_branch,
                automerge_allowed=automerge_allowed,
                is_self_repo=is_self_repo,
                publish_strategy=publish_strategy,
            )
        )

    return tuple(repositories)


def _require_no_cross_source_duplicates(
    legacy_repositories: tuple[RepositoryConfig, ...],
    registry_repositories: tuple[RepositoryConfig, ...],
) -> None:
    """`resolve_registered_repositories()` already rejects a duplicate
    `owner/repo` *within* the registry; this closes the remaining gap - the
    same `owner/repo` declared once in `config/repositories.yaml` and once
    more via `devbot init`, which is exactly as ambiguous."""

    legacy_names = {repo.full_name for repo in legacy_repositories}
    for repo in registry_repositories:
        if repo.full_name in legacy_names:
            raise ConfigError(
                f"{repo.full_name} is configured both in config/repositories.yaml and in the "
                "devbot init registry - remove one to resolve the ambiguity"
            )


def load_config(
    env_path: Path | str | None = None,
    repositories_path: Path | str | None = None,
    registry_path: Path | str | None = None,
) -> DevBotConfig:
    """Load `.env`, `config/repositories.yaml`, and the `devbot init`
    registry into a `DevBotConfig`.

    `env_path`, `repositories_path`, and `registry_path` allow tests (and
    alternate deployments) to point at fixture files instead of the real
    project/home-directory files. Existing process environment variables
    always take precedence over `.env` values.
    """
    load_dotenv(dotenv_path=env_path, override=False)

    # Issue #122: `WORKSPACE_ROOT` is optional - a deployment that manages
    # only `devbot init`-registered repositories never needs it. `None`
    # here simply means "skip the legacy repositories.yaml source below."
    workspace_root_raw = os.environ.get("WORKSPACE_ROOT")
    workspace_root = Path(workspace_root_raw).expanduser() if workspace_root_raw else None

    github_token = os.environ.get("GITHUB_TOKEN")
    if not github_token:
        raise ConfigError("Missing required environment variable: GITHUB_TOKEN")

    poll_interval_seconds = _parse_int("POLL_INTERVAL_SECONDS", _get_env("POLL_INTERVAL_SECONDS"))
    max_concurrent_jobs = _require_positive_int(
        "MAX_CONCURRENT_JOBS", _parse_int("MAX_CONCURRENT_JOBS", _get_env("MAX_CONCURRENT_JOBS"))
    )
    ai_concurrency = _require_positive_int(
        "AI_CONCURRENCY", _parse_int("AI_CONCURRENCY", _get_env("AI_CONCURRENCY"))
    )
    # No positivity floor: `ReviewService.process()` (`devbot.review`)
    # already treats `review_loop_limit <= 0` as "unlimited", so 0/negative
    # are meaningful values here, not configuration errors.
    review_loop_limit = _parse_int("REVIEW_LOOP_LIMIT", _get_env("REVIEW_LOOP_LIMIT"))
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

    resolved_registry_path = (
        Path(registry_path) if registry_path is not None else default_registry_path()
    )
    registry_resolution = resolve_registered_repositories(resolved_registry_path)
    registry_diagnostics = tuple(
        f"{diagnostic.kind}: {diagnostic.message}" for diagnostic in registry_resolution.diagnostics
    )

    legacy_repositories: tuple[RepositoryConfig, ...] = ()
    if workspace_root is not None:
        repositories_path_raw = (
            str(repositories_path)
            if repositories_path is not None
            else os.environ.get("DEVBOT_REPOSITORIES_PATH")
        )
        resolved_repositories_path = (
            Path(repositories_path_raw) if repositories_path_raw else DEFAULT_REPOSITORIES_PATH
        )
        legacy_path_is_explicit = repositories_path_raw is not None
        if resolved_repositories_path.is_file() or legacy_path_is_explicit:
            legacy_repositories = _load_repositories(resolved_repositories_path, workspace_root)

    repositories = legacy_repositories + registry_resolution.repositories
    _require_no_cross_source_duplicates(legacy_repositories, registry_resolution.repositories)

    if not repositories:
        if workspace_root is not None:
            repositories_path_raw = (
                str(repositories_path)
                if repositories_path is not None
                else os.environ.get("DEVBOT_REPOSITORIES_PATH")
            )
            resolved_repositories_path = (
                Path(repositories_path_raw) if repositories_path_raw else DEFAULT_REPOSITORIES_PATH
            )
            if not resolved_repositories_path.is_file():
                raise ConfigError(
                    f"Missing required repositories file: {resolved_repositories_path}"
                )
        raise ConfigError(
            "No repositories configured: set WORKSPACE_ROOT + config/repositories.yaml, "
            "or register at least one repository with `devbot init`"
        )

    return DevBotConfig(
        workspace_root=workspace_root,
        poll_interval_seconds=poll_interval_seconds,
        lock_file=lock_file,
        default_agent=default_agent,
        implementer_agent=implementer_agent,
        reviewer_agent=reviewer_agent,
        max_concurrent_jobs=max_concurrent_jobs,
        ai_concurrency=ai_concurrency,
        dry_run=dry_run,
        automerge_enabled=automerge_enabled,
        github_token=github_token,
        repositories=repositories,
        log_level=log_level,
        review_loop_limit=review_loop_limit,
        registry_diagnostics=registry_diagnostics,
    )
