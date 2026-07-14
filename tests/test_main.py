from pathlib import Path

import pytest

from devbot.lock import ProcessLock
from devbot.main import main


def test_main_starts_and_exits_successfully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """CP-001-10: the CLI loads configuration and exits successfully.

    Task 005 replaced the CLI's eager "validate every enabled repository"
    startup step with a real polling iteration (see tests/test_polling.py
    and tests/test_main_loop.py for that behavior in detail). This test
    keeps validating the original checkpoint - config loads, the process
    runs to completion, and the lock is released - using `--once` with no
    enabled repositories so it stays fast and makes no network calls.
    """
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    lock_file = tmp_path / "devbot.lock"

    env_path = tmp_path / ".env"
    env_path.write_text(
        f"WORKSPACE_ROOT={workspace_root}\nGITHUB_TOKEN=test-token\nDEVBOT_LOCK_FILE={lock_file}\n",
        encoding="utf-8",
    )

    repositories_path = tmp_path / "repositories.yaml"
    repositories_path.write_text(
        "repositories:\n  - owner: someone\n    repo: myrepo\n    enabled: false\n",
        encoding="utf-8",
    )

    exit_code = main(["--once"], env_path=env_path, repositories_path=repositories_path)

    assert exit_code == 0

    # The lock must have been released on normal exit: re-acquiring it
    # immediately after main() returns must succeed.
    post_exit_lock = ProcessLock(lock_file)
    post_exit_lock.acquire()
    post_exit_lock.release()
