from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.agents.codex import CodexRunner
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/workspace/myrepo"),
    )


def test_codex_runner_dry_run_does_not_execute() -> None:
    repository = _repository()
    runner = CodexRunner()  # dry_run defaults to True

    with patch("subprocess.run") as mock_run:
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_not_called()
    assert result.executed is False
    assert result.dry_run is True


def test_agent_dry_run_does_not_execute_process() -> None:
    repository = _repository()
    runner = CodexRunner(dry_run=True)

    with patch("devbot.agents.codex.subprocess.run") as mock_run:
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_not_called()
    assert result.executed is False


def test_runner_uses_target_repository_directory() -> None:
    repository = _repository()
    runner = CodexRunner(dry_run=False)

    with patch("devbot.agents.codex.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_called_once()
    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(repository.local_path)
    assert result.executed is True
    assert result.dry_run is False
