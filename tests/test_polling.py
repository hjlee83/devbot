from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from devbot.agents.base import AgentRunResult
from devbot.agents.codex import CodexRunner
from devbot.delivery import VerificationResult
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import DevBotConfig, RepositoryConfig, TaskState
from devbot.polling import PollingService, PollingStatus
from devbot.rework import ReworkService
from devbot.workspace import WorkspaceValidationError


class FakeGitHubClient:
    """No-network stand-in for GitHubClient, keyed by repository full name."""

    def __init__(
        self,
        issues_by_repo: dict[str, list[GitHubIssue]] | None = None,
        comments_by_issue: dict[tuple[str, int], list[PullRequestComment]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._issues_by_repo = issues_by_repo or {}
        self._comments_by_issue = comments_by_issue or {}
        self._error = error

    def list_issues(self, repository: RepositoryConfig, *, state: str = "open", **_kwargs: object):
        if self._error is not None:
            raise self._error
        return self._issues_by_repo.get(repository.full_name, [])

    def list_issue_comments(self, repository: RepositoryConfig, issue_number: int):
        if self._error is not None:
            raise self._error
        return self._comments_by_issue.get((repository.full_name, issue_number), [])


def _repo(name: str, *, enabled: bool = True) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo=name, enabled=enabled, local_path=Path(f"/tmp/workspace/{name}")
    )


def _issue(
    repository: str,
    number: int,
    *,
    labels: list[str],
    title: str = "issue title",
    body: str = "issue body",
    state: str = "open",
    created_at: datetime | None = None,
) -> GitHubIssue:
    return GitHubIssue(
        repository=repository,
        number=number,
        title=title,
        body=body,
        state=state,
        labels=tuple(labels),
        created_at=created_at or datetime(2026, 1, 1),
    )


def _comment(
    *,
    comment_id: int = 1,
    body: str = "@devbot please update this",
    reactions: dict[str, int] | None = None,
) -> PullRequestComment:
    return PullRequestComment(
        id=comment_id,
        author="reviewer",
        body=body,
        created_at=datetime(2026, 1, 2),
        reactions=reactions or {},
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


def test_iteration_skips_when_working_task_exists() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    working_issue = _issue(repo.full_name, 1, labels=["devbot:working"])
    github_client = FakeGitHubClient({repo.full_name: [working_issue]})
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    agent_runner.run.assert_not_called()


def test_iteration_skips_when_review_task_exists() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 1, labels=["devbot:review"])
    github_client = FakeGitHubClient({repo.full_name: [review_issue]})
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    agent_runner.run.assert_not_called()


def test_processed_review_comment_is_not_reworked_again() -> None:
    """CP-010-4: a comment already marked processed (an `eyes` reaction)
    must not trigger another rework cycle. Uses a *real* `ReworkService`
    (not a mock) so `find_unprocessed_devbot_comments`'s actual filtering
    is what's under test, not just the polling glue around a canned
    result."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 1, labels=["devbot:review"])
    processed_comment = _comment(reactions={"eyes": 1})
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 1): [processed_comment]},
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = ReworkService(
        state_writer=state_writer, write_client=write_client, apply_changes=MagicMock()
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    write_client.set_labels.assert_not_called()


def test_polling_detects_unprocessed_devbot_review_comment() -> None:
    """CP-010-1: a `review` Issue's unprocessed `@devbot` comment is
    detected and handed to `ReworkService.process()` with the same
    deterministic branch name the original delivery used."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    comment = _comment(comment_id=3)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 7): [comment]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    rework_service.process.assert_called_once()
    (
        called_repository,
        called_issue,
        called_branch,
        called_comments,
    ) = rework_service.process.call_args.args
    assert called_repository == repo
    assert called_issue == review_issue
    assert called_branch == "devbot/myrepo-7-fix-bug"
    assert called_comments == [comment]


def test_rework_is_prioritized_over_ready_task() -> None:
    """CP-010-2: when a `review` Issue with an unprocessed `@devbot`
    comment and a `ready` Issue are both available, rework runs and the
    `ready` Issue is never even selected."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    ready_issue = _issue(repo.full_name, 8, labels=["devbot:ready"], title="New feature")
    comment = _comment(comment_id=3)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue, ready_issue]},
        comments_by_issue={(repo.full_name, 7): [comment]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    agent_runner = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    rework_service.process.assert_called_once()
    agent_runner.run.assert_not_called()


def test_rework_reuses_existing_branch_and_pull_request() -> None:
    """CP-010-3: the full polling path (not just `ReworkService` in
    isolation) pushes to the exact existing branch and never opens a new
    pull request."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    comment = _comment(comment_id=3)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 7): [comment]},
    )
    expected_branch = "devbot/myrepo-7-fix-bug"
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    push = MagicMock()
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=push,
        current_branch=lambda repository: expected_branch,
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    push.assert_called_once_with(repo, expected_branch)
    write_client.create_pull_request.assert_not_called()


def test_successful_polled_rework_returns_to_review() -> None:
    """CP-010-5: a rework whose resulting `issue_state` is `review` is
    reported as `PollingStatus.REWORKED`."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    comment = _comment(comment_id=3)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 7): [comment]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    assert result.message == "reworked"


def test_ready_polling_still_runs_when_no_rework_exists() -> None:
    """CP-010-7: with `rework_service` configured but no `review` Issues
    at all, ordinary `ready` selection runs exactly as before Task 010."""
    repo = _repo("myrepo")
    config = _config([repo])
    ready_issue = _issue(repo.full_name, 7, labels=["devbot:ready"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    rework_service = MagicMock(spec=ReworkService)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    rework_service.process.assert_not_called()
    agent_runner.run.assert_called_once()


def test_rework_polling_dry_run_has_no_side_effects() -> None:
    """CP-010-8: a dry-run rework triggered through the polling loop makes
    no GitHub write and no commit/push."""
    repo = _repo("myrepo")
    config = _config([repo], dry_run=True)
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    comment = _comment(comment_id=3)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 7): [comment]},
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=True)
    commit = MagicMock()
    push = MagicMock()
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        dry_run=True,
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=commit,
        push=push,
        current_branch=lambda repository: "devbot/myrepo-7-fix-bug",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    service.run_once()

    write_client.set_labels.assert_not_called()
    write_client.create_comment.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    write_client.create_pull_request.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()


def test_failed_polled_rework_moves_to_blocked_with_reason() -> None:
    """CP-010-6: the polling layer must key off the structured
    `issue_state` field, not a magic `message == "blocked"` string - so a
    rework blocked for any reason (branch mismatch, Agent failure,
    verification failure, ...) reports `PollingStatus.BLOCKED` with the
    reason in `message`."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 9, labels=["devbot:review"], title="Fix bug")
    comment = _comment(comment_id=5)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 9): [comment]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.BLOCKED, message="blocked: branch mismatch"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert result.message == "blocked: branch mismatch"


def test_working_issue_blocks_rework_even_when_review_exists() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    working_issue = _issue(repo.full_name, 1, labels=["devbot:working"])
    review_issue = _issue(repo.full_name, 2, labels=["devbot:review"])
    github_client = FakeGitHubClient({repo.full_name: [working_issue, review_issue]})
    rework_service = MagicMock(spec=ReworkService)
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=MagicMock(),
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    rework_service.process.assert_not_called()


def test_iteration_selects_one_ready_issue() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 7, labels=["devbot:ready", "priority:high"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.task is not None
    assert (result.task.repository, result.task.number) == (repo.full_name, 7)
    assert result.status is PollingStatus.AGENT_COMPLETED


def test_iteration_handles_empty_queue() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    github_client = FakeGitHubClient({repo.full_name: []})
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    result = service.run_once()

    assert result.status is PollingStatus.NO_READY_TASK
    agent_runner.run.assert_not_called()


def test_iteration_with_zero_repositories_returns_no_ready_task() -> None:
    config = _config([])
    github_client = FakeGitHubClient({})
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    result = service.run_once()

    assert result.status is PollingStatus.NO_READY_TASK


def test_issue_without_devbot_label_is_ignored() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    unrelated = _issue(repo.full_name, 100, labels=["bug"])
    github_client = FakeGitHubClient({repo.full_name: [unrelated]})
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    result = service.run_once()

    assert result.status is PollingStatus.NO_READY_TASK


def test_iteration_validates_selected_workspace() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    ensure_workspace_ready = MagicMock()

    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=ensure_workspace_ready,
    )
    service.run_once()

    ensure_workspace_ready.assert_called_once_with(repo)


def test_iteration_reports_workspace_validation_failure() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()

    def _raise(_repository: RepositoryConfig) -> None:
        raise WorkspaceValidationError("워크스페이스가 없습니다")

    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_raise,
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_INVALID
    assert result.task is not None
    agent_runner.run.assert_not_called()


def test_iteration_invokes_agent_with_selected_task() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 9, labels=["devbot:ready"], title="Unique Title XYZ")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    service.run_once()

    agent_runner.run.assert_called_once()
    called_repository, called_prompt = agent_runner.run.call_args.args
    assert called_repository == repo
    assert "Unique Title XYZ" in called_prompt
    assert "#9" in called_prompt


def test_iteration_reports_agent_failure() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 5, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.side_effect = RuntimeError("agent crashed")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    assert result.task is not None


def test_iteration_reports_agent_keyboard_interrupt_as_failure() -> None:
    """A `KeyboardInterrupt` raised while the Agent is running must be
    treated the same as any other Agent failure - reported and (when the
    write path is wired up) blocked - never left to crash the polling
    loop uncaught."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 5, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.side_effect = KeyboardInterrupt()
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    assert result.task is not None


def test_iteration_reports_nonzero_agent_returncode_as_failure() -> None:
    """A subprocess that exits without raising (e.g. Codex CLI returning 1)
    must still be treated as a failure, not silently reported as success."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 6, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="agent crashed midway", returncode=1
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    assert result.task is not None
    assert result.message


def test_iteration_dry_run_has_no_external_side_effects() -> None:
    repo = _repo("myrepo")
    config = _config([repo], dry_run=True)
    issue = _issue(repo.full_name, 4, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=CodexRunner(dry_run=True),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with patch("devbot.agents.codex.subprocess.run") as mock_run:
        result = service.run_once()

    mock_run.assert_not_called()
    assert result.status is PollingStatus.AGENT_COMPLETED


def test_iteration_error_is_reported_without_state_corruption() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    github_client = FakeGitHubClient(error=RuntimeError("network exploded"))
    agent_runner = MagicMock()
    service = PollingService(config=config, github_client=github_client, agent_runner=agent_runner)

    first = service.run_once()
    second = service.run_once()

    assert first.status is PollingStatus.ITERATION_ERROR
    assert second.status is PollingStatus.ITERATION_ERROR
    assert first.task is None
    assert second.task is None
    agent_runner.run.assert_not_called()


def test_iteration_picks_oldest_among_equal_priority_across_repos() -> None:
    repo_a = _repo("repo-a")
    repo_b = _repo("repo-b")
    config = _config([repo_a, repo_b])
    high_priority = ["devbot:ready", "priority:high"]
    newer = _issue(repo_a.full_name, 1, labels=high_priority, created_at=datetime(2026, 1, 5))
    older = _issue(repo_b.full_name, 2, labels=high_priority, created_at=datetime(2026, 1, 1))
    github_client = FakeGitHubClient({repo_a.full_name: [newer], repo_b.full_name: [older]})
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        agent_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.task is not None
    assert (result.task.repository, result.task.number) == (repo_b.full_name, 2)
