from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.agent_execution import (
    AgentExecutionContext,
    AgentExecutionPolicy,
    AgentLauncher,
    AgentRole,
)
from devbot.agents.base import AgentRunner, AgentRunResult
from devbot.agents.codex import CodexRunner
from devbot.github_client import GitHubIssue, PullRequest
from devbot.models import RepositoryConfig
from devbot.polling import build_agent_execution_context
from devbot.worktree import PreparedWorkspace


def _repo(path: Path = Path("/tmp/workspace/repo")) -> RepositoryConfig:
    return RepositoryConfig(owner="someone", repo="repo", enabled=True, local_path=path)


def _issue() -> GitHubIssue:
    return GitHubIssue(
        repository="someone/repo",
        number=64,
        title="Task 031",
        body="",
        state="open",
        labels=("devbot:review",),
        created_at=datetime(2026, 1, 1),
    )


def _pr() -> PullRequest:
    return PullRequest(
        number=65,
        head_ref="task/031-agent-execution-environment",
        head_sha="abc",
        body="Closes #64",
        html_url="https://github.com/someone/repo/pull/65",
    )


def _prepared(path: Path = Path("/tmp/workspace/repo/.worktrees/issue-64")) -> PreparedWorkspace:
    repo = _repo(path)
    return PreparedWorkspace(
        repository=repo,
        branch="task/031-agent-execution-environment",
        base_branch="main",
        issue_number=64,
        pull_request=_pr(),
        worktree_path=path,
        reused=True,
    )


def test_agent_execution_context_contains_canonical_task_metadata() -> None:
    prepared = _prepared()
    context = build_agent_execution_context(
        repository=_repo(),
        prepared=prepared,
        issue=_issue(),
        pull_request=_pr(),
        role=AgentRole.REVIEW,
        cycle_id="cycle-1",
    )

    assert context.repository.full_name == "someone/repo"
    assert context.prepared_workspace is prepared
    assert context.canonical_branch == "task/031-agent-execution-environment"
    assert context.issue.number == 64
    assert context.pull_request and context.pull_request.number == 65
    assert context.role is AgentRole.REVIEW
    assert context.execution_id == "cycle-1:review:someone/repo#64"


def test_agent_environment_is_normalized_and_contains_no_secrets() -> None:
    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-1",
        role=AgentRole.IMPLEMENT,
    )

    env = context.safe_environment()

    assert env["DEVBOT_REPOSITORY"] == "someone/repo"
    assert env["DEVBOT_BRANCH"] == "task/031-agent-execution-environment"
    assert env["DEVBOT_ISSUE"] == "64"
    assert env["DEVBOT_PR"] == "65"
    assert env["DEVBOT_ROLE"] == "implement"
    assert env["DEVBOT_EXECUTION_ID"] == "exec-1"
    assert not any("TOKEN" in key or "SECRET" in key or "AUTHORIZATION" in key for key in env)


def test_agent_environment_preserves_user_auth_context(
    monkeypatch,
) -> None:
    monkeypatch.setenv("HOME", "/Users/tester")
    monkeypatch.setenv("PATH", "/usr/local/bin:/usr/bin")
    monkeypatch.setenv("TMPDIR", "/tmp/tester")
    monkeypatch.setenv("USER", "tester")
    monkeypatch.setenv("XDG_CONFIG_HOME", "/Users/tester/.config")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/Users/tester/.claude")
    monkeypatch.setenv("GITHUB_TOKEN", "secret-token")

    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-auth",
        role=AgentRole.REVIEW,
    )

    env = context.safe_environment()

    assert env["HOME"] == "/Users/tester"
    assert env["PATH"] == "/usr/local/bin:/usr/bin"
    assert env["TMPDIR"] == "/tmp/tester"
    assert env["USER"] == "tester"
    assert env["XDG_CONFIG_HOME"] == "/Users/tester/.config"
    assert env["CLAUDE_CONFIG_DIR"] == "/Users/tester/.claude"
    assert env["DEVBOT_ROLE"] == "review"
    assert "GITHUB_TOKEN" not in env


def test_agent_launcher_applies_shared_context_to_provider_command() -> None:
    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-2",
        role=AgentRole.REVIEW,
    )
    launcher = AgentLauncher(
        command_builder=lambda _context, prompt: ["agent", prompt],
        policy=AgentExecutionPolicy(
            agent="agent",
            version="1",
            sandbox="workspace-write",
            approval="never",
            network="enabled",
            capability_summary={"ok": True},
        ),
    )

    with patch("devbot.agent_execution.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        launcher.run(context, "prompt text")

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(context.workspace)
    assert kwargs["env"]["DEVBOT_WORKSPACE"] == str(context.workspace)
    assert kwargs["env"]["DEVBOT_ROLE"] == "review"


def test_claude_execution_inherits_home_and_prepared_workspace(monkeypatch) -> None:
    monkeypatch.setenv("HOME", "/Users/tester")
    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-claude",
        role=AgentRole.REVIEW,
    )
    launcher = AgentLauncher(
        command_builder=lambda _context, prompt: ["claude", "-p", prompt],
        policy=AgentExecutionPolicy(
            agent="claude",
            version="1",
            sandbox="provider-default",
            approval="acceptEdits",
            network="provider-default",
            capability_summary={"non_interactive": True},
        ),
    )

    with patch("devbot.agent_execution.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        launcher.run(context, "prompt")

    _, kwargs = mock_run.call_args
    assert kwargs["cwd"] == str(context.workspace)
    assert kwargs["env"]["HOME"] == "/Users/tester"
    assert kwargs["env"]["DEVBOT_EXECUTION_ID"] == "exec-claude"


def test_agent_execution_diagnostics_are_complete_and_redacted() -> None:
    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-3",
        role=AgentRole.REVIEW,
    )
    launcher = AgentLauncher(
        command_builder=lambda _context, prompt: ["agent", prompt],
        policy=AgentExecutionPolicy(
            agent="codex",
            version="0.144.1",
            sandbox="workspace-write",
            approval="never",
            network="enabled",
            capability_summary={"approval": True},
        ),
    )

    diagnostics = launcher.diagnostics(context)

    assert diagnostics.agent == "codex"
    assert diagnostics.role == "review"
    assert diagnostics.workspace == str(context.workspace)
    assert diagnostics.issue == 64
    assert diagnostics.pr == 65
    assert "prompt" not in repr(diagnostics).lower()
    assert "token" not in repr(diagnostics).lower()


def test_agent_capability_detection_is_cached() -> None:
    CodexRunner._CAPABILITY_CACHE = None
    runner = CodexRunner(dry_run=True)
    help_text = "--cd\n--add-dir\n--sandbox\n--ask-for-approval\n--config\n"

    with patch("devbot.agents.codex.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout=help_text, stderr="")
        first = runner._detect_capabilities()
        second = runner._detect_capabilities()

    assert first == second
    assert all(first.values())
    assert mock_run.call_count == 2


def test_failed_capability_detection_remains_fail_closed() -> None:
    CodexRunner._CAPABILITY_CACHE = None
    runner = CodexRunner(dry_run=False)

    with patch("devbot.agents.codex.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = runner.run(_repo(), "prompt")

    assert result.outcome_hint is not None
    assert result.outcome_hint.value == "agent_configuration_invalid"
    assert not any(
        call.args[0][0:2] == ["codex", "exec"] and "--help" not in call.args[0]
        for call in mock_run.call_args_list
    )


def test_all_agent_roles_execute_from_prepared_workspace() -> None:
    class Runner(AgentRunner):
        def run(self, _repository, _prompt):
            raise AssertionError("context path must be used")

        def run_context(self, received_context, _prompt):
            assert received_context.workspace == received_context.prepared_workspace.worktree_path
            return AgentRunResult(executed=False, dry_run=True, message="ok")

    for role in AgentRole:
        context = AgentExecutionContext(
            repository=_repo(),
            prepared_workspace=_prepared(),
            canonical_branch="task/031-agent-execution-environment",
            issue=_issue(),
            pull_request=_pr(),
            execution_id=f"exec-{role.value}",
            role=role,
        )
        assert Runner().run_context(context, "prompt").message == "ok"


def test_implement_rework_and_review_share_execution_invariants() -> None:
    observed: list[tuple[str, str, str]] = []

    for role in AgentRole:
        context = AgentExecutionContext(
            repository=_repo(),
            prepared_workspace=_prepared(),
            canonical_branch="task/031-agent-execution-environment",
            issue=_issue(),
            pull_request=_pr(),
            execution_id=f"cycle:{role.value}",
            role=role,
        )
        launcher = AgentLauncher(
            command_builder=lambda _context, _prompt: ["agent"],
            policy=AgentExecutionPolicy(
                agent="agent",
                version="1",
                sandbox="workspace-write",
                approval="never",
                network="enabled",
                capability_summary={"ok": True},
            ),
        )
        with patch("devbot.agent_execution.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            launcher.run(context, "prompt")
        _, kwargs = mock_run.call_args
        observed.append(
            (role.value, kwargs["cwd"], kwargs["env"]["DEVBOT_EXECUTION_ID"])
        )

    assert observed == [
        ("implement", str(_prepared().worktree_path), "cycle:implement"),
        ("rework", str(_prepared().worktree_path), "cycle:rework"),
        ("review", str(_prepared().worktree_path), "cycle:review"),
    ]


def test_existing_workflows_remain_compatible_with_agent_execution_context() -> None:
    class LegacyRunner(AgentRunner):
        def run(self, repository, prompt):
            assert repository.local_path == _prepared().repository.local_path
            assert prompt == "prompt"
            return AgentRunResult(executed=True, dry_run=False, message="legacy")

    context = AgentExecutionContext(
        repository=_repo(),
        prepared_workspace=_prepared(),
        canonical_branch="task/031-agent-execution-environment",
        issue=_issue(),
        pull_request=_pr(),
        execution_id="exec-compat",
        role=AgentRole.IMPLEMENT,
    )

    result = LegacyRunner().run_context(context, "prompt")

    assert result.message == "legacy"
