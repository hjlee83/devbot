from pathlib import Path

import pytest

from devbot.main import main


def test_main_starts_and_exits_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("WORKSPACE_ROOT", raising=False)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    enabled_repo_path = workspace_root / "myrepo"
    enabled_repo_path.mkdir()

    env_path = tmp_path / ".env"
    env_path.write_text(f"WORKSPACE_ROOT={workspace_root}\n", encoding="utf-8")

    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n"
        "  - owner: someone\n"
        "    repo: myrepo\n"
        "    enabled: true\n",
        encoding="utf-8",
    )

    exit_code = main(env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0
    captured = capsys.readouterr()
    assert "DevBot started" in captured.out
    assert "someone/myrepo" in captured.out
