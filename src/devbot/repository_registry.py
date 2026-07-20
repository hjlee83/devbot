"""Repository registration (Issue #122).

Replaces mandatory `WORKSPACE_ROOT`-based repository discovery with
explicit registration initiated from each target repository via
`devbot init`. Two files are involved, deliberately kept separate:

- Repository-local config (`<repo>/.devbot/config.yaml`) - policy and
  settings for exactly one repository (owner/repo/enabled/default_branch/
  automerge_allowed/is_self_repo/publish_strategy), the same fields
  `config/repositories.yaml` already carries per entry.
- The global registry (`~/.devbot/registry.yaml` by default, overridable
  via `DEVBOT_REGISTRY_PATH`) - only absolute repository *paths*, so the
  long-running daemon can find registered repositories regardless of
  where its own checkout lives or what parent directory each repository
  sits under. It never duplicates the policy fields above.

`resolve_registered_repositories()` is the read side `devbot.config` calls:
it never raises for a single bad entry (a missing/moved path, a duplicate
owner/repo across two paths, an unreadable `.devbot/config.yaml`) - one
broken registration must not prevent every other registered repository
from being managed. Each problem is instead returned as a
`RegistryDiagnostic` the caller can log or surface via `devbot doctor`.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import yaml

from devbot.models import RepositoryConfig

REPO_LOCAL_CONFIG_DIRNAME = ".devbot"
REPO_LOCAL_CONFIG_FILENAME = "config.yaml"
DEFAULT_REGISTRY_DIRNAME = ".devbot"
DEFAULT_REGISTRY_FILENAME = "registry.yaml"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}

# Matches both `git@github.com:owner/repo.git` and
# `https://github.com/owner/repo.git` (and the same without `.git`).
_REMOTE_URL_RE = re.compile(
    r"[:/](?P<owner>[^/:]+)/(?P<repo>[^/]+?)(?:\.git)?/?$"
)


class RepositoryRegistrationError(RuntimeError):
    """Raised for a registration action that cannot proceed safely - never
    for a single read-side entry that should instead become a
    `RegistryDiagnostic` (see module docstring)."""


@dataclass(frozen=True, slots=True)
class RepositoryLocalConfig:
    """The contents of `<repo>/.devbot/config.yaml`."""

    owner: str
    repo: str
    enabled: bool = True
    default_branch: str = "main"
    automerge_allowed: bool = False
    is_self_repo: bool = False
    publish_strategy: str | None = None


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One row of the global registry - a path, nothing else."""

    path: Path
    registered_at: str


@dataclass(frozen=True, slots=True)
class RegistryDiagnostic:
    """One actionable problem found while resolving registered
    repositories - never raised, always collected and returned."""

    kind: str  # "missing_path" | "invalid_config" | "duplicate_repository"
    path: Path
    message: str


@dataclass(frozen=True, slots=True)
class RegistryResolution:
    """`resolve_registered_repositories()`'s full result: every repository
    that resolved cleanly, plus every problem found along the way."""

    repositories: tuple[RepositoryConfig, ...]
    diagnostics: tuple[RegistryDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class InitResult:
    """What `devbot init` did, for the CLI to render."""

    repo_root: Path
    owner: str
    repo: str
    local_config_path: Path
    registry_path: Path
    local_config_created: bool
    already_registered: bool


def default_registry_path() -> Path:
    """`DEVBOT_REGISTRY_PATH` if set, else `~/.devbot/registry.yaml` - a
    fixed, well-known location independent of any single repository or of
    `WORKSPACE_ROOT`, so the daemon checkout can find it regardless of
    where it itself lives (Issue #122's Implementation Context)."""

    override = os.environ.get("DEVBOT_REGISTRY_PATH")
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_REGISTRY_DIRNAME / DEFAULT_REGISTRY_FILENAME


def find_git_repository_root(start: Path) -> Path:
    """Walk upward from `start` to the nearest directory containing
    `.git` - mirrors how `git` itself locates a repository root regardless
    of the current working directory within it."""

    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    raise RepositoryRegistrationError(
        f"{start} is not inside a Git repository (no `.git` found in any parent directory)"
    )


def detect_owner_repo_from_git_remote(
    repo_root: Path, *, remote: str = "origin"
) -> tuple[str, str] | None:
    """Best-effort `owner`/`repo` from `git remote get-url <remote>`.
    Returns `None` (never raises) when the remote is missing or its URL
    does not look like a GitHub-style `owner/repo` - the caller falls back
    to requiring `--owner`/`--repo`."""

    completed = subprocess.run(
        ["git", "remote", "get-url", remote],
        cwd=str(repo_root),
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        return None
    match = _REMOTE_URL_RE.search(completed.stdout.strip())
    if match is None:
        return None
    return match.group("owner"), match.group("repo")


def _parse_bool(name: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in _TRUE_VALUES | _FALSE_VALUES:
        return value.strip().lower() in _TRUE_VALUES
    raise RepositoryRegistrationError(f"{name} must be a boolean, got: {value!r}")


def repo_local_config_path(repo_root: Path) -> Path:
    return repo_root / REPO_LOCAL_CONFIG_DIRNAME / REPO_LOCAL_CONFIG_FILENAME


def load_repository_local_config(repo_root: Path) -> RepositoryLocalConfig:
    config_path = repo_local_config_path(repo_root)
    if not config_path.is_file():
        raise RepositoryRegistrationError(f"missing repository-local config: {config_path}")

    with config_path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise RepositoryRegistrationError(f"invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RepositoryRegistrationError(f"{config_path} must contain a mapping at the top level")

    owner = raw.get("owner")
    repo = raw.get("repo")
    if not owner or not repo:
        raise RepositoryRegistrationError(f"{config_path} requires non-empty 'owner' and 'repo'")

    raw_publish_strategy = raw.get("publish_strategy")
    return RepositoryLocalConfig(
        owner=str(owner),
        repo=str(repo),
        enabled=_parse_bool("enabled", raw.get("enabled", True)),
        default_branch=str(raw.get("default_branch") or "main"),
        automerge_allowed=_parse_bool("automerge_allowed", raw.get("automerge_allowed", False)),
        is_self_repo=_parse_bool("is_self_repo", raw.get("is_self_repo", False)),
        publish_strategy=str(raw_publish_strategy) if raw_publish_strategy is not None else None,
    )


def write_repository_local_config(repo_root: Path, config: RepositoryLocalConfig) -> bool:
    """Write `<repo>/.devbot/config.yaml`. Returns `True` if the file's
    content changed (created or updated), `False` if a re-run found the
    file already identical - idempotent either way."""

    config_path = repo_local_config_path(repo_root)
    payload: dict[str, object] = {
        "owner": config.owner,
        "repo": config.repo,
        "enabled": config.enabled,
        "default_branch": config.default_branch,
        "automerge_allowed": config.automerge_allowed,
        "is_self_repo": config.is_self_repo,
    }
    if config.publish_strategy is not None:
        payload["publish_strategy"] = config.publish_strategy
    new_content = yaml.safe_dump(payload, sort_keys=False)

    if config_path.is_file() and config_path.read_text(encoding="utf-8") == new_content:
        return False

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(new_content, encoding="utf-8")
    return True


def load_registry(registry_path: Path) -> tuple[RegistryEntry, ...]:
    """Never raises for a missing file - an empty/absent registry is a
    valid state (no repository has been registered yet)."""

    if not registry_path.is_file():
        return ()

    with registry_path.open("r", encoding="utf-8") as handle:
        try:
            raw = yaml.safe_load(handle) or {}
        except yaml.YAMLError as exc:
            raise RepositoryRegistrationError(f"invalid YAML in {registry_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RepositoryRegistrationError(
            f"{registry_path} must contain a mapping at the top level"
        )

    raw_entries = raw.get("repositories") or []
    if not isinstance(raw_entries, list):
        raise RepositoryRegistrationError(f"{registry_path}'s 'repositories' must be a list")

    entries: list[RegistryEntry] = []
    for index, entry in enumerate(raw_entries):
        if not isinstance(entry, dict) or not entry.get("path"):
            raise RepositoryRegistrationError(
                f"{registry_path}'s repositories[{index}] requires a non-empty 'path'"
            )
        entries.append(
            RegistryEntry(
                path=Path(str(entry["path"])),
                registered_at=str(entry.get("registered_at") or ""),
            )
        )
    return tuple(entries)


def _write_registry(registry_path: Path, entries: tuple[RegistryEntry, ...]) -> None:
    payload = {
        "repositories": [
            {"path": str(entry.path), "registered_at": entry.registered_at} for entry in entries
        ]
    }
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(payload, sort_keys=False)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{registry_path.name}.", suffix=".tmp", dir=registry_path.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, registry_path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def register_repository(registry_path: Path, repo_root: Path) -> bool:
    """Idempotent: re-registering the same (resolved) path is a no-op and
    returns `False`. Returns `True` when a new entry was actually added."""

    resolved = repo_root.resolve()
    entries = load_registry(registry_path)
    if any(entry.path == resolved for entry in entries):
        return False

    new_entry = RegistryEntry(path=resolved, registered_at=datetime.now(UTC).isoformat())
    _write_registry(registry_path, (*entries, new_entry))
    return True


def unregister_repository(registry_path: Path, repo_root: Path) -> bool:
    """Returns `True` if an entry was removed, `False` if the path was not
    registered - never an error, matching Issue #122's "define
    deterministic behavior for ... unregistration" requirement."""

    resolved = repo_root.resolve()
    entries = load_registry(registry_path)
    remaining = tuple(entry for entry in entries if entry.path != resolved)
    if len(remaining) == len(entries):
        return False
    _write_registry(registry_path, remaining)
    return True


def resolve_registered_repositories(registry_path: Path) -> RegistryResolution:
    """Read side: resolves every registry entry into a `RepositoryConfig`,
    collecting rather than raising on a per-entry problem (see module
    docstring)."""

    entries = load_registry(registry_path)
    diagnostics: list[RegistryDiagnostic] = []
    candidates: list[tuple[RegistryEntry, RepositoryLocalConfig]] = []
    paths_by_full_name: dict[str, list[Path]] = {}

    for entry in entries:
        if not entry.path.is_dir():
            diagnostics.append(
                RegistryDiagnostic(
                    kind="missing_path",
                    path=entry.path,
                    message=(
                        f"registered repository path does not exist (moved or deleted?): "
                        f"{entry.path}"
                    ),
                )
            )
            continue

        try:
            local_config = load_repository_local_config(entry.path)
        except RepositoryRegistrationError as exc:
            diagnostics.append(
                RegistryDiagnostic(kind="invalid_config", path=entry.path, message=str(exc))
            )
            continue

        full_name = f"{local_config.owner}/{local_config.repo}"
        candidates.append((entry, local_config))
        paths_by_full_name.setdefault(full_name, []).append(entry.path)

    duplicate_full_names = {
        full_name
        for full_name, registered_paths in paths_by_full_name.items()
        if len(registered_paths) > 1
    }
    for full_name in sorted(duplicate_full_names):
        registered_paths = paths_by_full_name[full_name]
        diagnostics.append(
            RegistryDiagnostic(
                kind="duplicate_repository",
                path=registered_paths[0],
                message=(
                    f"{full_name} is registered at multiple paths: "
                    f"{', '.join(str(path) for path in registered_paths)} - remove duplicate "
                    "registrations to resolve the ambiguity"
                ),
            )
        )

    repositories: list[RepositoryConfig] = []
    for entry, local_config in candidates:
        full_name = f"{local_config.owner}/{local_config.repo}"
        if full_name in duplicate_full_names:
            continue
        repositories.append(
            RepositoryConfig(
                owner=local_config.owner,
                repo=local_config.repo,
                enabled=local_config.enabled,
                local_path=entry.path,
                default_branch=local_config.default_branch,
                automerge_allowed=local_config.automerge_allowed,
                is_self_repo=local_config.is_self_repo,
                publish_strategy=local_config.publish_strategy,
            )
        )

    return RegistryResolution(repositories=tuple(repositories), diagnostics=tuple(diagnostics))


def initialize_repository(
    start: Path,
    *,
    owner: str | None = None,
    repo: str | None = None,
    default_branch: str | None = None,
    automerge_allowed: bool = False,
    registry_path: Path | None = None,
) -> InitResult:
    """`devbot init`'s core logic: locate the repository, resolve owner/
    repo, write `.devbot/config.yaml`, and register the repository - all
    idempotent on re-run."""

    repo_root = find_git_repository_root(start)
    resolved_registry_path = registry_path or default_registry_path()

    resolved_owner, resolved_repo = owner, repo
    if resolved_owner is None or resolved_repo is None:
        detected = detect_owner_repo_from_git_remote(repo_root)
        if detected is None:
            raise RepositoryRegistrationError(
                f"could not determine owner/repo for {repo_root}: no readable 'origin' remote "
                "and neither --owner nor --repo was given"
            )
        resolved_owner = resolved_owner or detected[0]
        resolved_repo = resolved_repo or detected[1]

    existing_config: RepositoryLocalConfig | None = None
    if repo_local_config_path(repo_root).is_file():
        try:
            existing_config = load_repository_local_config(repo_root)
        except RepositoryRegistrationError:
            existing_config = None

    local_config = RepositoryLocalConfig(
        owner=resolved_owner,
        repo=resolved_repo,
        enabled=existing_config.enabled if existing_config is not None else True,
        default_branch=default_branch
        or (existing_config.default_branch if existing_config is not None else "main"),
        automerge_allowed=automerge_allowed
        or (existing_config.automerge_allowed if existing_config is not None else False),
        is_self_repo=existing_config.is_self_repo if existing_config is not None else False,
        publish_strategy=existing_config.publish_strategy if existing_config is not None else None,
    )
    created = write_repository_local_config(repo_root, local_config)
    newly_registered = register_repository(resolved_registry_path, repo_root)

    return InitResult(
        repo_root=repo_root,
        owner=resolved_owner,
        repo=resolved_repo,
        local_config_path=repo_local_config_path(repo_root),
        registry_path=resolved_registry_path,
        local_config_created=created,
        already_registered=not newly_registered,
    )
