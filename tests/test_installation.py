import os
import subprocess
from pathlib import Path

import pytest

from devbot.config import load_config
from devbot.installation import (
    INSTALL_METADATA_ENV,
    INSTALL_ROOT_ENV,
    InstallationError,
    install_launcher,
    read_install_metadata,
    resolve_install_root,
    write_install_metadata,
)
from devbot.main import main


def _make_install_root(path: Path) -> Path:
    (path / "src" / "devbot").mkdir(parents=True)
    (path / "src" / "devbot" / "main.py").write_text("# main\n", encoding="utf-8")
    (path / "pyproject.toml").write_text("[project]\nname = 'devbot'\n", encoding="utf-8")
    (path / "config").mkdir()
    return path


def _write_runtime_config(root: Path, workspace_root: Path) -> None:
    (root / ".env").write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\n",
        encoding="utf-8",
    )
    (root / "config" / "repositories.yaml").write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )


def test_install_metadata_persists_operator_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_install_root(tmp_path / "operator")
    metadata = tmp_path / "metadata" / "install-root"
    monkeypatch.setenv(INSTALL_METADATA_ENV, str(metadata))

    written = write_install_metadata(root)

    assert written == metadata
    assert read_install_metadata() == root.resolve()
    assert resolve_install_root(allow_current_working_directory=False) == root.resolve()


def test_missing_install_metadata_has_safe_recovery_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(INSTALL_METADATA_ENV, str(tmp_path / "missing"))

    with pytest.raises(InstallationError, match="install-launcher"):
        resolve_install_root(allow_current_working_directory=False)


def test_config_defaults_are_install_root_relative_from_any_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_install_root(tmp_path / "operator")
    workspace_root = tmp_path / "workspace"
    _write_runtime_config(root, workspace_root)
    other_cwd = tmp_path / "elsewhere"
    other_cwd.mkdir()
    monkeypatch.chdir(other_cwd)
    monkeypatch.setenv(INSTALL_ROOT_ENV, str(root))

    config = load_config()

    assert config.workspace_root == workspace_root
    assert config.repositories[0].local_path == workspace_root / "myrepo"


def test_install_launcher_writes_path_command_that_forwards_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_install_root(tmp_path / "operator")
    metadata = tmp_path / "metadata" / "install-root"
    monkeypatch.setenv(INSTALL_METADATA_ENV, str(metadata))

    result = install_launcher(bin_dir=tmp_path / "bin", install_root=root)

    script = result.launcher_path.read_text(encoding="utf-8")
    assert f"export {INSTALL_ROOT_ENV}=" in script
    assert 'exec uv run --project "$DEVBOT_INSTALL_ROOT" devbot "$@"' in script
    assert result.launcher_path.stat().st_mode & 0o111


def test_launcher_forwards_cli_arguments_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _make_install_root(tmp_path / "operator")
    monkeypatch.setenv(INSTALL_METADATA_ENV, str(tmp_path / "metadata" / "install-root"))
    result = install_launcher(bin_dir=tmp_path / "bin", install_root=root)
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    recorder = tmp_path / "uv-args.txt"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$@" > "$UV_ARGS_RECORDER"\n'
        'printf "%s\\n" "$PWD" >> "$UV_ARGS_RECORDER"\n'
        "exit 37\n",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("UV_ARGS_RECORDER", str(recorder))

    completed = subprocess.run(
        [str(result.launcher_path), "--once", "--dry-run"], check=False
    )

    recorded = recorder.read_text(encoding="utf-8").splitlines()
    assert completed.returncode == 37
    assert recorded[:5] == ["run", "--project", str(root.resolve()), "devbot", "--once"]
    assert recorded[5:] == ["--dry-run", str(root.resolve())]


def test_install_launcher_command_does_not_load_runtime_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _make_install_root(tmp_path / "operator")
    monkeypatch.setenv(INSTALL_ROOT_ENV, str(root))
    monkeypatch.setenv(INSTALL_METADATA_ENV, str(tmp_path / "metadata" / "install-root"))

    exit_code = main(["install-launcher", "--bin-dir", str(tmp_path / "bin")])

    assert exit_code == 0
    assert "launcher:" in capsys.readouterr().out
