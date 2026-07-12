from pathlib import Path
from unittest.mock import patch

from devbot.agents.codex import CodexRunner
from devbot.models import RepositoryConfig


def test_codex_runner_dry_run_does_not_execute() -> None:
    repository = RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/workspace/myrepo"),
    )
    runner = CodexRunner()  # dry_run defaults to True

    with patch("subprocess.run") as mock_run:
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_not_called()
    assert result.executed is False
    assert result.dry_run is True
