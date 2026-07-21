"""Registry reload support for long-running daemon polling."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from devbot.config import load_config
from devbot.models import DevBotConfig
from devbot.repository_registry import default_registry_path


@dataclass(frozen=True, slots=True)
class RegistryFileState:
    exists: bool
    mtime_ns: int | None
    size: int | None
    inode: int | None


@dataclass(frozen=True, slots=True)
class RegistryReloadResult:
    config: DevBotConfig
    added: tuple[str, ...]
    removed: tuple[str, ...]
    unchanged_count: int


class RegistryReloadMonitor:
    """Poll registry file metadata and reload config when it changes."""

    def __init__(
        self,
        *,
        initial_config: DevBotConfig,
        env_path: Path | str | None = None,
        repositories_path: Path | str | None = None,
        registry_path: Path | str | None = None,
    ) -> None:
        self._env_path = env_path
        self._repositories_path = repositories_path
        self._registry_path = (
            Path(registry_path) if registry_path is not None else default_registry_path()
        )
        self._state = self._snapshot()
        self._last_config = initial_config

    @property
    def registry_path(self) -> Path:
        return self._registry_path

    def check(self) -> RegistryReloadResult | None:
        current_state = self._snapshot()
        if current_state == self._state:
            return None
        self._state = current_state
        old_config = self._last_config
        new_config = load_config(
            env_path=self._env_path,
            repositories_path=self._repositories_path,
            registry_path=self._registry_path,
        )
        self._last_config = new_config
        old_names = {repository.full_name for repository in old_config.enabled_repositories}
        new_names = {repository.full_name for repository in new_config.enabled_repositories}
        return RegistryReloadResult(
            config=new_config,
            added=tuple(sorted(new_names - old_names)),
            removed=tuple(sorted(old_names - new_names)),
            unchanged_count=len(old_names & new_names),
        )

    def _snapshot(self) -> RegistryFileState:
        try:
            stat = self._registry_path.stat()
        except OSError:
            return RegistryFileState(False, None, None, None)
        return RegistryFileState(
            exists=True,
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size,
            inode=getattr(stat, "st_ino", None),
        )
