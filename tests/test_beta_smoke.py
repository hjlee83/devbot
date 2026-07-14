"""Task 009 beta smoke tests.

These exercise the full `ready -> working -> Agent -> verify -> commit ->
push -> PR -> review` flow through `PollingService.run_once()` (wired via
`state_writer`/`delivery`, see `devbot.polling`), plus `ReworkService`
reusing an existing branch/PR. No new product behavior is added here —
this only proves Task 001-008's pieces work together as one flow.
"""

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.agents.base import AgentRunResult
from devbot.agents.codex import CodexRunner
from devbot.delivery import DeliveryResult, DeliveryService, VerificationResult
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient, PullRequestInfo
from devbot.issue_state import IssueStateWriter
from devbot.models import DevBotConfig, RepositoryConfig
from devbot.polling import PollingService, PollingStatus
from devbot.rework import ReworkService
from devbot.workspace import generate_branch_name


class FakeGitHubClient:
    """No-network stand-in for GitHubClient, keyed by repository full name."""

    def __init__(
        self,
        issues_by_repo: dict[str, list[GitHubIssue]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._issues_by_repo = issues_by_repo or {}
        self._error = error

    def list_issues(self, repository: RepositoryConfig, *, state: str = "open", **_kwargs: object):
        if self._error is not None:
            raise self._error
        return self._issues_by_repo.get(repository.full_name, [])


def _repo(name: str = "myrepo", *, enabled: bool = True) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo=name, enabled=enabled, local_path=Path(f"/tmp/workspace/{name}")
    )


def _issue(
    repository: str, number: int, *, labels: list[str], title: str = "Fix bug"
) -> GitHubIssue:
    return GitHubIssue(
        repository=repository,
        number=number,
        title=title,
        body="issue body",
        state="open",
        labels=tuple(labels),
        created_at=datetime(2026, 1, 1),
    )


def _config(repositories: list[RepositoryConfig], **overrides: object) -> DevBotConfig:
    values: dict[str, object] = {
        "workspace_root": Path("/tmp/workspace"),
        "poll_interval_seconds": 60,
        "lock_file": Path("/tmp/devbot.lock"),
        "default_agent": "codex",
        "max_concurrent_jobs": 1,
        "dry_run": True,
        "github_token": "test-token",
        "repositories": tuple(repositories),
    }
    values.update(overrides)
    return DevBotConfig(**values)  # type: ignore[arg-type]


def _no_op_workspace_check(_repository: RepositoryConfig) -> None:
    return None


def _delivered(pull_request: PullRequestInfo | None = None) -> DeliveryResult:
    return DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=pull_request is not None,
        pushed=pull_request is not None,
        pull_request=pull_request,
        dry_run=pull_request is None,
        message="delivered" if pull_request else "[dry-run] verification passed",
    )


def test_dry_run_has_no_side_effect() -> None:
    repo = _repo()
    config = _config([repo], dry_run=True)
    issue = _issue(repo.full_name, 4, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=True)
    delivery = DeliveryService(
        client=write_client,
        dry_run=True,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=CodexRunner(dry_run=True),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    with patch("devbot.agents.codex.subprocess.run") as mock_agent_run:
        result = service.run_once()

    mock_agent_run.assert_not_called()
    write_client.set_labels.assert_not_called()
    write_client.create_comment.assert_not_called()
    write_client.create_pull_request.assert_not_called()
    delivery.commit.assert_not_called()  # type: ignore[attr-defined]
    delivery.push.assert_not_called()  # type: ignore[attr-defined]
    assert result.status is PollingStatus.AGENT_COMPLETED


def test_skip_when_working_exists() -> None:
    repo = _repo()
    config = _config([repo])
    working_issue = _issue(repo.full_name, 1, labels=["devbot:working"])
    github_client = FakeGitHubClient({repo.full_name: [working_issue]})
    state_writer = MagicMock(spec=IssueStateWriter)
    delivery = MagicMock(spec=DeliveryService)
    agent_runner = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    state_writer.claim.assert_not_called()
    agent_runner.run.assert_not_called()
    delivery.deliver.assert_not_called()


def test_select_ready_issue() -> None:
    repo = _repo()
    config = _config([repo])
    issue = _issue(repo.full_name, 7, labels=["devbot:ready", "priority:high"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(
        repo.full_name, 7, labels=["devbot:working", "priority:high"], title="Fix bug"
    )
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    delivery = MagicMock(spec=DeliveryService)
    delivery.deliver.return_value = _delivered()
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.task is not None
    assert (result.task.repository, result.task.number) == (repo.full_name, 7)
    state_writer.claim.assert_called_once_with(repo, issue)


def test_agent_runner_called() -> None:
    repo = _repo()
    config = _config([repo])
    issue = _issue(repo.full_name, 9, labels=["devbot:ready"], title="Unique Title XYZ")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 9, labels=["devbot:working"], title="Unique Title XYZ")
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    delivery = MagicMock(spec=DeliveryService)
    delivery.deliver.return_value = _delivered()
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    service.run_once()

    agent_runner.run.assert_called_once()
    called_repository, called_prompt = agent_runner.run.call_args.args
    assert called_repository == repo
    assert "Unique Title XYZ" in called_prompt
    assert "#9" in called_prompt


def test_delivery_after_verification() -> None:
    call_order: list[str] = []
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 3, labels=["devbot:working"])

    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    state_writer.mark_for_review.side_effect = lambda repository, issue_arg: call_order.append(
        "mark_for_review"
    )

    def _run_agent(repository, prompt):
        call_order.append("agent")
        return AgentRunResult(executed=True, dry_run=False, message="ok")

    agent_runner = MagicMock()
    agent_runner.run.side_effect = _run_agent

    delivery = MagicMock(spec=DeliveryService)
    pull_request = PullRequestInfo(number=1, html_url="https://github.com/someone/myrepo/pull/1")

    def _deliver(repository, issue_arg, branch, evidence):
        call_order.append("deliver")
        return _delivered(pull_request)

    delivery.deliver.side_effect = _deliver

    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert call_order == ["agent", "deliver", "mark_for_review"]
    assert result.status is PollingStatus.DELIVERED


def test_stop_delivery_when_failed() -> None:
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 3, labels=["devbot:working"])
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock(spec=DeliveryService)
    failing_verification = VerificationResult(
        passed=False, failed_command=("uv", "run", "pytest"), output="1 failed"
    )
    delivery.deliver.return_value = DeliveryResult(
        verification=failing_verification,
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="Verification failed: uv run pytest",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    state_writer.block.assert_called_once()
    args, _ = state_writer.block.call_args
    assert args[0] is repo
    assert args[1] is working_issue
    state_writer.mark_for_review.assert_not_called()
    assert result.status is PollingStatus.BLOCKED


def test_block_failure_after_agent_exception_is_reported_without_crashing() -> None:
    """Regression: if `state_writer.block()` itself raises (network error,
    GitHub API error, ...) while recording an agent failure, `run_once()`
    must still return a structured `PollingResult`, not propagate."""
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 5, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 5, labels=["devbot:working"])
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    state_writer.block.side_effect = RuntimeError("GitHub API 오류")
    agent_runner = MagicMock()
    agent_runner.run.side_effect = RuntimeError("agent crashed")
    delivery = MagicMock(spec=DeliveryService)
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR
    assert result.task is not None
    delivery.deliver.assert_not_called()


def test_block_failure_after_verification_failure_is_reported_without_crashing() -> None:
    """Regression: same as above, but for the `block()` call after a
    verification failure (not an agent exception)."""
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 6, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 6, labels=["devbot:working"])
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    state_writer.block.side_effect = RuntimeError("GitHub API 오류")
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock(spec=DeliveryService)
    failing_verification = VerificationResult(
        passed=False, failed_command=("uv", "run", "pytest"), output="1 failed"
    )
    delivery.deliver.return_value = DeliveryResult(
        verification=failing_verification,
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="Verification failed: uv run pytest",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR
    assert result.task is not None


def test_mark_for_review_failure_is_reported_without_crashing() -> None:
    """Regression: if `state_writer.mark_for_review()` itself raises after
    a successful delivery, `run_once()` must still return a structured
    `PollingResult`, not propagate."""
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 7, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 7, labels=["devbot:working"])
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    state_writer.mark_for_review.side_effect = RuntimeError("GitHub API 오류")
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock(spec=DeliveryService)
    pull_request = PullRequestInfo(number=1, html_url="https://github.com/someone/myrepo/pull/1")
    delivery.deliver.return_value = _delivered(pull_request)
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR
    assert result.task is not None


def test_move_to_review() -> None:
    repo = _repo()
    config = _config([repo], dry_run=False)
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    working_issue = _issue(repo.full_name, 3, labels=["devbot:working"])
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = working_issue
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock(spec=DeliveryService)
    pull_request = PullRequestInfo(number=1, html_url="https://github.com/someone/myrepo/pull/1")
    delivery.deliver.return_value = _delivered(pull_request)
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    state_writer.mark_for_review.assert_called_once_with(repo, working_issue)
    assert result.status is PollingStatus.DELIVERED


def test_reuse_existing_pr() -> None:
    """The first successful delivery opens PR #1; a later `@devbot` rework
    on the same Issue/branch must push to that same branch and never open
    a second PR."""
    repo = _repo()
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.create_pull_request.return_value = PullRequestInfo(
        number=1, html_url="https://github.com/someone/myrepo/pull/1"
    )
    state_writer = IssueStateWriter(client=write_client, dry_run=False)

    # Phase 1: PollingService delivers the first successful implementation.
    config = _config([repo], dry_run=False)
    ready_issue = _issue(repo.full_name, 3, labels=["devbot:ready"], title="Add feature")
    github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    delivery = DeliveryService(
        client=write_client,
        dry_run=False,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
    )
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    first_result = service.run_once()

    assert first_result.status is PollingStatus.DELIVERED
    write_client.create_pull_request.assert_called_once()

    # Phase 2: an unprocessed `@devbot` PR comment triggers rework on the
    # SAME branch/PR (the Issue is now `review`, per phase 1's result).
    review_issue = _issue(repo.full_name, 3, labels=["devbot:review"], title="Add feature")
    comment = PullRequestComment(
        id=1,
        author="someone",
        body="@devbot please rename this variable",
        created_at=datetime(2026, 1, 2),
        reactions={},
    )
    branch = generate_branch_name(repo, 3, "Add feature")
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
        current_branch=lambda repository: branch,
    )

    rework_result = rework_service.process(repo, review_issue, branch, [comment])

    assert rework_result.message == "reworked"
    write_client.create_pull_request.assert_called_once()  # still only once total
