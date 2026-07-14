import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.agents.claude import ClaudeRunner
from devbot.models import RepositoryConfig


def _repository() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/workspace/myrepo"),
    )


def test_claude_runner_dry_run_has_no_side_effect() -> None:
    repository = _repository()
    runner = ClaudeRunner()  # dry_run defaults to True

    with patch("devbot.agents.claude.subprocess.run") as mock_run:
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_not_called()
    assert result.executed is False
    assert result.dry_run is True


def test_claude_runner_invokes_claude_cli() -> None:
    repository = _repository()
    runner = ClaudeRunner(dry_run=False)

    with patch("devbot.agents.claude.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        result = runner.run(repository, prompt="do the thing")

    mock_run.assert_called_once()
    args, _ = mock_run.call_args
    assert args[0][0] == "claude"
    assert "do the thing" in args[0]
    assert result.executed is True
    assert result.dry_run is False
    assert result.returncode == 0


def test_claude_runner_uses_repository_as_working_directory() -> None:
    """CP-011-4: `ClaudeRunner` runs the Claude CLI with
    `repository.local_path` as its working directory."""
    repository = _repository()
    runner = ClaudeRunner(dry_run=False)

    with patch("devbot.agents.claude.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        runner.run(repository, prompt="do the thing")

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(repository.local_path)


def test_claude_runner_reports_missing_cli() -> None:
    """CP-011-5: a missing Claude CLI binary must not raise - it comes back
    as a structured failure result."""
    repository = _repository()
    runner = ClaudeRunner(dry_run=False)

    with patch("devbot.agents.claude.subprocess.run", side_effect=FileNotFoundError()):
        result = runner.run(repository, prompt="do the thing")

    assert result.executed is False
    assert result.dry_run is False
    assert "claude" in result.message.lower() or "설치" in result.message


def test_claude_runner_reports_timeout() -> None:
    """CP-011-6: a Claude CLI run that exceeds the configured timeout must
    not raise - it comes back as a structured failure result."""
    repository = _repository()
    runner = ClaudeRunner(dry_run=False, timeout_seconds=5)

    with patch(
        "devbot.agents.claude.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=5),
    ):
        result = runner.run(repository, prompt="do the thing")

    assert result.executed is False
    assert result.dry_run is False
    assert "5" in result.message


def test_claude_runner_reports_nonzero_exit() -> None:
    """A nonzero exit (including what an auth failure would look like from
    the outside - see the module docstring) is reported as a structured
    failure, not raised."""
    repository = _repository()
    runner = ClaudeRunner(dry_run=False)

    with patch("devbot.agents.claude.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="Invalid API key · Please run /login"
        )
        result = runner.run(repository, prompt="do the thing")

    assert result.executed is True
    assert result.returncode == 1
    assert "Invalid API key" in result.message
