"""Installation-root discovery and PATH launcher support."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

INSTALL_ROOT_ENV = "DEVBOT_INSTALL_ROOT"
INSTALL_METADATA_ENV = "DEVBOT_INSTALL_METADATA"
DEFAULT_METADATA_PATH = Path("~/.config/devbot/install-root").expanduser()


class InstallationError(RuntimeError):
    """Raised when installation metadata is missing or invalid."""


@dataclass(frozen=True, slots=True)
class LauncherInstallResult:
    launcher_path: Path
    install_root: Path
    metadata_path: Path


def _metadata_path() -> Path:
    raw = os.environ.get(INSTALL_METADATA_ENV)
    return Path(raw).expanduser() if raw else DEFAULT_METADATA_PATH


def _validate_install_root(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.is_dir():
        raise InstallationError(f"설치 루트가 디렉터리가 아닙니다: {root}")
    if not (root / "pyproject.toml").is_file():
        raise InstallationError(f"DevBot 설치 루트가 아닙니다(pyproject.toml 없음): {root}")
    if not (root / "src" / "devbot" / "main.py").is_file():
        raise InstallationError(f"DevBot 설치 루트가 아닙니다(src/devbot/main.py 없음): {root}")
    return root


def write_install_metadata(install_root: Path, metadata_path: Path | None = None) -> Path:
    root = _validate_install_root(install_root)
    target = metadata_path or _metadata_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(root) + "\n", encoding="utf-8")
    except OSError as exc:
        raise InstallationError(f"설치 메타데이터를 쓸 수 없습니다: {target}: {exc}") from exc
    return target


def read_install_metadata(metadata_path: Path | None = None) -> Path:
    target = metadata_path or _metadata_path()
    try:
        raw = target.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise InstallationError(
            f"설치 메타데이터가 없습니다: {target}. "
            "`devbot install-launcher --bin-dir <PATH>`를 operator checkout에서 실행하세요."
        ) from exc
    if not raw:
        raise InstallationError(f"설치 메타데이터가 비어 있습니다: {target}")
    return _validate_install_root(Path(raw))


def resolve_install_root(
    explicit_root: Path | str | None = None,
    *,
    allow_current_working_directory: bool = True,
) -> Path:
    if explicit_root is not None:
        return _validate_install_root(Path(explicit_root))

    env_root = os.environ.get(INSTALL_ROOT_ENV)
    if env_root:
        return _validate_install_root(Path(env_root))

    try:
        return read_install_metadata()
    except InstallationError:
        if not allow_current_working_directory:
            raise

    return _validate_install_root(Path.cwd())


def default_env_path(install_root: Path) -> Path:
    return install_root / ".env"


def default_repositories_path(install_root: Path) -> Path:
    return install_root / "config" / "repositories.yaml"


def install_launcher(
    *,
    bin_dir: Path,
    install_root: Path,
    metadata_path: Path | None = None,
) -> LauncherInstallResult:
    root = _validate_install_root(install_root)
    metadata = write_install_metadata(root, metadata_path)
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher_path = bin_dir / "devbot"
    script = "\n".join(
        [
            "#!/bin/sh",
            f"export {INSTALL_ROOT_ENV}={shlex.quote(str(root))}",
            f"export {INSTALL_METADATA_ENV}={shlex.quote(str(metadata))}",
            f"cd {shlex.quote(str(root))} || exit 1",
            'exec uv run --project "$DEVBOT_INSTALL_ROOT" devbot "$@"',
            "",
        ]
    )
    launcher_path.write_text(script, encoding="utf-8")
    launcher_path.chmod(0o755)
    return LauncherInstallResult(
        launcher_path=launcher_path,
        install_root=root,
        metadata_path=metadata,
    )
