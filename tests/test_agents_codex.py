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

    _, kwargs = mock_run.call_args_list[-1]
    assert kwargs["cwd"] == str(repository.local_path)
    assert result.executed is True
    assert result.dry_run is False


def test_codex_runner_builds_unattended_workspace_scoped_command() -> None:
    repository = _repository()
    runner = CodexRunner(
        dry_run=False,
        _capabilities={
            "cd": True,
            "add_dir": True,
            "sandbox": True,
            "approval": True,
            "config": True,
        },
    )

    def _fake_run(args, **_kwargs):
        if args[:2] == ["git", "rev-parse"] and args[2] == "--git-dir":
            return MagicMock(returncode=0, stdout=".git/worktrees/issue-62\n", stderr="")
        if args[:2] == ["git", "rev-parse"] and args[2] == "--git-common-dir":
            return MagicMock(returncode=0, stdout="/tmp/workspace/.git\n", stderr="")
        return MagicMock(returncode=0, stdout="ok", stderr="")

    with patch("devbot.agents.codex.subprocess.run", side_effect=_fake_run) as mock_run:
        result = runner.run(repository, prompt="do the thing")

    command = mock_run.call_args_list[-1].args[0]
    assert command[:7] == [
        "codex",
        "-a",
        "never",
        "-s",
        "workspace-write",
        "-C",
        str(repository.local_path),
    ]
    assert "--add-dir" in command
    assert str(Path("/tmp/workspace/.git").resolve()) in command
    assert "sandbox_workspace_write.network_access=true" in command
    assert "disk-full-read-access" not in command
    assert "danger-full-access" not in command
    assert command[-2:] == ["exec", "do the thing"]
    assert result.executed is True


def test_codex_runner_policy_reports_safe_effective_settings() -> None:
    repository = _repository()
    runner = CodexRunner(
        _capabilities={
            "cd": True,
            "add_dir": True,
            "sandbox": True,
            "approval": True,
            "config": True,
        }
    )

    def _fake_run(args, **_kwargs):
        if args == ["codex", "--version"]:
            return MagicMock(returncode=0, stdout="codex-cli 0.144.1\n", stderr="")
        if args[:2] == ["git", "rev-parse"] and args[2] == "--git-dir":
            return MagicMock(returncode=0, stdout=".git/worktrees/issue-62\n", stderr="")
        if args[:2] == ["git", "rev-parse"] and args[2] == "--git-common-dir":
            return MagicMock(returncode=0, stdout="/tmp/workspace/.git\n", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("devbot.agents.codex.subprocess.run", side_effect=_fake_run):
        policy = runner.execution_policy(repository)

    assert policy["cwd"] == str(repository.local_path)
    assert policy["approval_mode"] == "never"
    assert policy["sandbox_mode"] == "workspace-write"
    assert policy["network_mode"] == "enabled"
    assert policy["codex_version"] == "codex-cli 0.144.1"
    assert str(repository.local_path.resolve()) in policy["writable_roots"]
