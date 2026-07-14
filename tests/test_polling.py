import logging
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.agents.base import AgentRunResult
from devbot.agents.codex import CodexRunner
from devbot.delivery import DeliveryResult, VerificationResult
from devbot.github_client import GitHubIssue, PullRequest, PullRequestComment
from devbot.github_write_client import GitHubWriteClient, PullRequestInfo
from devbot.issue_state import IssueStateWriter
from devbot.models import DevBotConfig, RepositoryConfig, TaskState
from devbot.polling import PollingService, PollingStatus
from devbot.review import build_review_marker
from devbot.rework import ReworkService
from devbot.workspace import WorkspaceValidationError


class FakeGitHubClient:
    """No-network stand-in for GitHubClient, keyed by repository full name."""

    def __init__(
        self,
        issues_by_repo: dict[str, list[GitHubIssue]] | None = None,
        comments_by_issue: dict[tuple[str, int], list[PullRequestComment]] | None = None,
        pull_requests_by_repo: dict[str, list[PullRequest]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._issues_by_repo = issues_by_repo or {}
        self._comments_by_issue = comments_by_issue or {}
        self._pull_requests_by_repo = pull_requests_by_repo or {}
        self._error = error

    def list_issues(self, repository: RepositoryConfig, *, state: str = "open", **_kwargs: object):
        if self._error is not None:
            raise self._error
        return self._issues_by_repo.get(repository.full_name, [])

    def list_issue_comments(self, repository: RepositoryConfig, issue_number: int):
        if self._error is not None:
            raise self._error
        return self._comments_by_issue.get((repository.full_name, issue_number), [])

    def list_pull_requests(self, repository: RepositoryConfig, **_kwargs: object):
        if self._error is not None:
            raise self._error
        return self._pull_requests_by_repo.get(repository.full_name, [])


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


def _pull_request(
    number: int,
    *,
    issue_number: int,
    head_ref: str = "devbot/existing-branch",
    head_sha: str = "deadbeef",
) -> PullRequest:
    return PullRequest(
        number=number,
        head_ref=head_ref,
        head_sha=head_sha,
        body=f"Closes #{issue_number}",
        html_url=f"https://github.com/someone/myrepo/pull/{number}",
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
        "implementer_agent": "codex",
        "reviewer_agent": "codex",
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
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    agent_runner.run.assert_not_called()


def test_iteration_skips_when_review_task_exists() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 1, labels=["devbot:rework"])
    github_client = FakeGitHubClient({repo.full_name: [review_issue]})
    agent_runner = MagicMock()
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    agent_runner.run.assert_not_called()


def test_processed_feedback_is_not_reworked_twice_at_polling_level() -> None:
    """CP-010-4: a comment already marked processed (an `eyes` reaction)
    must not trigger another rework cycle. Uses a *real* `ReworkService`
    (not a mock) so `find_unprocessed_devbot_comments`'s actual filtering
    is what's under test, not just the polling glue around a canned
    result."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 1, labels=["devbot:review"])
    processed_comment = _comment(reactions={"eyes": 1})
    linked_pr = _pull_request(101, issue_number=1)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [processed_comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        has_changes=lambda repository: True,
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    write_client.set_labels.assert_not_called()


def test_polling_detects_unprocessed_devbot_review_comment() -> None:
    """CP-010-1: a `review` Issue's unprocessed `@devbot` comment is
    detected and handed to `ReworkService.process()` along with the
    Issue's actual linked PR's head branch (looked up via
    `list_pull_requests`, not recomputed)."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    linked_pr = _pull_request(101, issue_number=7, head_ref="devbot/myrepo-7-fix-bug")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
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
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    ready_issue = _issue(repo.full_name, 8, labels=["devbot:ready"], title="New feature")
    comment = _comment(comment_id=3)
    linked_pr = _pull_request(101, issue_number=7)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue, ready_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    agent_runner = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    rework_service.process.assert_called_once()
    agent_runner.run.assert_not_called()


def test_rework_reuses_existing_branch_and_pull_request() -> None:
    """CP-010-3: the polled path identifies the Issue's *actual* linked PR
    (via `list_pull_requests` + `find_linked_pull_request`, matched on the
    `Closes #N` convention) and reuses its real head branch - not a
    recomputed/guessed name - and never opens a new pull request. The
    linked PR's head branch is deliberately different from what
    `generate_branch_name` would produce, so this only passes if the real
    lookup (not a naive recompute) is what's driving `push`."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    actual_pr_head = "devbot/myrepo-7-renamed-branch"
    linked_pr = _pull_request(101, issue_number=7, head_ref=actual_pr_head)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
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
        current_branch=lambda repository: actual_pr_head,
        has_changes=lambda repository: True,
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    push.assert_called_once_with(repo, actual_pr_head)
    write_client.create_pull_request.assert_not_called()


def test_rework_uses_implementer_runner() -> None:
    """CP-011-2: Task 010's rework path applies Agent changes through
    `implementer_runner` - the same role used for ready-task
    implementation - never through the separately configured
    `reviewer_runner`."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    linked_pr = _pull_request(101, issue_number=7, head_ref="devbot/myrepo-7-fix-bug")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    implementer_runner = MagicMock()
    reviewer_runner = MagicMock()
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=lambda repository, issue, comment: implementer_runner.run(
            repository, "prompt"
        ),
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
        current_branch=lambda repository: "devbot/myrepo-7-fix-bug",
        has_changes=lambda repository: True,
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        reviewer_runner=reviewer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    implementer_runner.run.assert_called_once()
    reviewer_runner.run.assert_not_called()


def test_reviewer_runner_is_not_used_for_implementation() -> None:
    """CP-011-3: `reviewer_runner` is injected (Task 011 scope: config +
    construction + injection only) but never invoked for implementation
    work, in either the ready-task path or the Task 010 rework path.
    Automatic review execution stays out of this Task's scope."""
    repo = _repo("myrepo")

    ready_config = _config([repo])
    ready_issue = _issue(repo.full_name, 7, labels=["devbot:ready"], title="Fix bug")
    ready_github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    ready_implementer = MagicMock()
    ready_implementer.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    ready_reviewer = MagicMock()
    ready_service = PollingService(
        config=ready_config,
        github_client=ready_github_client,
        implementer_runner=ready_implementer,
        reviewer_runner=ready_reviewer,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    ready_service.run_once()

    ready_reviewer.run.assert_not_called()
    assert ready_reviewer.method_calls == []

    rework_config = _config([repo])
    review_issue = _issue(repo.full_name, 9, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=5)
    linked_pr = _pull_request(102, issue_number=9, head_ref="devbot/myrepo-9-fix-bug")
    rework_github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 102): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    rework_reviewer = MagicMock()
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
        current_branch=lambda repository: "devbot/myrepo-9-fix-bug",
        has_changes=lambda repository: True,
    )
    rework_polling_service = PollingService(
        config=rework_config,
        github_client=rework_github_client,
        implementer_runner=MagicMock(),
        reviewer_runner=rework_reviewer,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    rework_polling_service.run_once()

    rework_reviewer.run.assert_not_called()
    assert rework_reviewer.method_calls == []


def test_successful_polled_rework_returns_to_review() -> None:
    """CP-010-5: a rework whose resulting `issue_state` is `review` is
    reported as `PollingStatus.REWORKED`."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    linked_pr = _pull_request(101, issue_number=7)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
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
        implementer_runner=agent_runner,
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
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    linked_pr = _pull_request(101, issue_number=7, head_ref="devbot/myrepo-7-fix-bug")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
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
        has_changes=lambda repository: True,
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
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
    review_issue = _issue(repo.full_name, 9, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=5)
    linked_pr = _pull_request(101, issue_number=9)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.BLOCKED, message="blocked: branch mismatch"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert result.message == "blocked: branch mismatch"


def test_review_issue_without_linked_pull_request_is_reported_as_error() -> None:
    """A `review` Issue with an unprocessed `@devbot` comment but no PR
    whose body references it (an anomalous data-integrity state - a real
    `review` Issue should always have one, opened by `DeliveryService`)
    must not silently guess a branch name; it's reported as a structured
    failure instead."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:rework"], title="Fix bug")
    comment = _comment(comment_id=3)
    unrelated_pr = _pull_request(101, issue_number=999)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [comment]},
        pull_requests_by_repo={repo.full_name: [unrelated_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR
    rework_service.process.assert_not_called()


def test_working_issue_blocks_rework_even_when_review_exists() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    working_issue = _issue(repo.full_name, 1, labels=["devbot:working"])
    review_issue = _issue(repo.full_name, 2, labels=["devbot:rework"])
    github_client = FakeGitHubClient({repo.full_name: [working_issue, review_issue]})
    rework_service = MagicMock(spec=ReworkService)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    rework_service.process.assert_not_called()


def test_rework_job_requires_rework_label_and_unprocessed_feedback() -> None:
    """CP-014-3: unprocessed PR feedback is not enough by itself; the
    tracked Issue must already be in `devbot:rework` before a REWORK job is
    selected."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    rework_issue = _issue(repo.full_name, 8, labels=["devbot:rework"], title="Fix bug again")
    comment = _comment(comment_id=3)
    review_pr = _pull_request(101, issue_number=7)
    rework_pr = _pull_request(102, issue_number=8)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue, rework_issue]},
        comments_by_issue={(repo.full_name, 101): [comment], (repo.full_name, 102): [comment]},
        pull_requests_by_repo={repo.full_name: [review_pr, rework_pr]},
    )
    rework_service = MagicMock(spec=ReworkService)
    rework_service.process.return_value = MagicMock(
        triggered=True, issue_state=TaskState.REVIEW, message="reworked"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REWORKED
    called_issue = rework_service.process.call_args.args[1]
    assert called_issue == rework_issue


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
        implementer_runner=agent_runner,
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
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

    result = service.run_once()

    assert result.status is PollingStatus.NO_READY_TASK
    agent_runner.run.assert_not_called()


def test_iteration_with_zero_repositories_returns_no_managed_repositories() -> None:
    """Task 013 (동작 규칙 #4) narrows this from `NO_READY_TASK`: zero
    *managed* repositories is a distinct, more actionable diagnostic than
    "no ready Issue among the repositories we did search" - see
    `test_zero_managed_repositories_logs_diagnostic_and_skips_polling` for
    the accompanying log assertion."""
    config = _config([])
    github_client = FakeGitHubClient({})
    agent_runner = MagicMock()
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

    result = service.run_once()

    assert result.status is PollingStatus.NO_MANAGED_REPOSITORIES


def test_issue_without_devbot_label_is_ignored() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    unrelated = _issue(repo.full_name, 100, labels=["bug"])
    github_client = FakeGitHubClient({repo.full_name: [unrelated]})
    agent_runner = MagicMock()
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

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
        implementer_runner=agent_runner,
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
        implementer_runner=agent_runner,
        ensure_workspace_ready=_raise,
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_INVALID
    assert result.task is not None
    agent_runner.run.assert_not_called()


def test_preflight_failure_restores_previous_stable_state() -> None:
    """CP-014-5: once a ready Issue is claimed, a workspace preflight
    failure restores the pre-claim stable state and never runs the Agent."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 3, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()

    def _raise(_repository: RepositoryConfig) -> None:
        raise WorkspaceValidationError("워크스페이스가 없습니다")

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_raise,
        state_writer=state_writer,
        delivery=MagicMock(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_INVALID
    assert write_client.set_labels.call_args_list[0].args == (repo, 3, ["devbot:working"])
    assert write_client.set_labels.call_args_list[1].args == (repo, 3, ["devbot:ready"])
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
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    service.run_once()

    agent_runner.run.assert_called_once()
    called_repository, called_prompt = agent_runner.run.call_args.args
    assert called_repository == repo
    assert "Unique Title XYZ" in called_prompt
    assert "#9" in called_prompt


def test_ready_task_uses_implementer_runner() -> None:
    """CP-011-1: a ready Issue's initial implementation run executes
    through `implementer_runner`, not `reviewer_runner`."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 7, labels=["devbot:ready"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    reviewer_runner = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        reviewer_runner=reviewer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    implementer_runner.run.assert_called_once()
    reviewer_runner.run.assert_not_called()


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
        implementer_runner=agent_runner,
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
        implementer_runner=agent_runner,
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
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    assert result.task is not None
    assert result.message


def test_unexpected_exception_never_leaves_issue_working() -> None:
    """CP-014-7: an unexpected delivery exception after claim and Agent
    success is converted to `devbot:blocked`, not left as `working`."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 6, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.side_effect = RuntimeError("delivery crashed")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 6, ["devbot:blocked"])
    assert "delivery crashed" in write_client.create_comment.call_args.args[2]


def test_approval_required_agent_output_skips_delivery() -> None:
    """CP-016-9: when the Agent's own output ends in an interactive
    approval request (e.g. a read-only command paused on human
    confirmation), DevBot must not run commit/push/PR delivery, even
    though the process itself exited without error. The Issue moves to
    `devbot:manual-action`, not `devbot:blocked` (this needs a human, and
    must not loop)."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 31, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(
        executed=True,
        dry_run=False,
        message="I ran `gh pr list` but this needs your approval before I can continue.",
    )
    delivery = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    delivery.deliver.assert_not_called()
    assert result.status is PollingStatus.BLOCKED
    assert result.message.startswith("approval_required:")
    assert write_client.set_labels.call_args_list[-1].args == (repo, 31, ["devbot:manual-action"])


def test_ready_implement_reuses_linked_pr_branch() -> None:
    """CP-016-10: when the Issue already has a linked open PR (e.g. a
    retried IMPLEMENT job), delivery must be called with that PR's own
    head branch, never a freshly generated `devbot/devbot-*` name."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 31, labels=["devbot:ready"])
    linked_pr = _pull_request(30, issue_number=31, head_ref="task/016-existing-branch")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=30, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    service.run_once()

    delivery.deliver.assert_called_once()
    args, kwargs = delivery.deliver.call_args
    assert args[2] == "task/016-existing-branch"
    assert kwargs["linked_pull_request"] is linked_pr


def test_implement_delivery_branch_invalid_does_not_mark_review() -> None:
    """A `delivery_branch_invalid` result (commit succeeded, but the
    intended branch never existed locally, so push/PR never happened)
    must not be treated as a successful delivery - the Issue must not
    move to `devbot:review` with nothing actually pushed behind it."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 8, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="delivery_branch_invalid: local branch 'devbot/myrepo-8-fix-bug' not found",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 8, ["devbot:blocked"])


def test_implement_no_repository_changes_without_pr_does_not_mark_review() -> None:
    """A fresh `devbot:ready` Issue with no linked PR whose delivery
    reports `no_repository_changes` must not move to `devbot:review` -
    there would be no PR at all to review. It goes to
    `devbot:manual-action` instead, not a `blocked` retry loop."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 9, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="no_repository_changes",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 9, ["devbot:manual-action"])


def test_implement_no_repository_changes_with_linked_pr_marks_review() -> None:
    """The intentional counterpart: when a linked PR already exists and
    delivery reports `no_repository_changes` (already implemented),
    review resumes on that existing PR instead of blocking."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 10, labels=["devbot:ready"])
    linked_pr = _pull_request(30, issue_number=10, head_ref="task/010-existing-branch")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="no_repository_changes",
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 10, ["devbot:review"])


def test_iteration_dry_run_has_no_external_side_effects() -> None:
    repo = _repo("myrepo")
    config = _config([repo], dry_run=True)
    issue = _issue(repo.full_name, 4, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=CodexRunner(dry_run=True),
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
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=agent_runner
    )

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
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.task is not None
    assert (result.task.repository, result.task.number) == (repo_b.full_name, 2)


# --- Task 012: role-based polling orchestration ----------------------------


def test_ready_issue_triggers_implement_job() -> None:
    """CP-012-1: a `devbot:ready` Issue is scheduled and run as an
    IMPLEMENT job through `implementer_runner`."""
    repo = _repo("myrepo")
    config = _config([repo])
    ready_issue = _issue(repo.full_name, 7, labels=["devbot:ready"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    implementer_runner.run.assert_called_once()


def test_unreviewed_pr_head_triggers_review_job() -> None:
    """CP-012-2: a `devbot:review` Issue whose linked PR head has no
    auto-review marker yet is scheduled and run as a REVIEW job."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    linked_pr = _pull_request(101, issue_number=7, head_sha="sha-1")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    review_service.process.return_value = MagicMock(
        status="MERGE READY", issue_state=TaskState.REVIEW, message="reviewed: MERGE READY"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REVIEWED
    review_service.process.assert_called_once()
    called_repository, called_issue, called_pr = review_service.process.call_args.args
    assert called_repository == repo
    assert called_issue == review_issue
    assert called_pr == linked_pr


def test_new_pr_head_triggers_review_again() -> None:
    """CP-012-4: a PR head with a marker for its *previous* head SHA still
    gets reviewed once the head moves to a new, unmarked SHA."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    linked_pr = _pull_request(101, issue_number=7, head_sha="sha-2")
    old_marker_comment = _comment(
        comment_id=99, body=f"# Review Summary\n\n{build_review_marker('sha-1')}"
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [old_marker_comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    review_service.process.return_value = MagicMock(
        status="MERGE READY", issue_state=TaskState.REVIEW, message="reviewed: MERGE READY"
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REVIEWED
    review_service.process.assert_called_once()


def test_already_reviewed_head_is_not_reviewed_again() -> None:
    """CP-012-3 (orchestration level): a PR head that already has a marker
    for its *current* head SHA produces no REVIEW candidate."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    linked_pr = _pull_request(101, issue_number=7, head_sha="sha-1")
    marker_comment = _comment(
        comment_id=99, body=f"# Review Summary\n\n{build_review_marker('sha-1')}"
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [marker_comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.SKIPPED_ACTIVE_TASK
    review_service.process.assert_not_called()


def test_failed_job_releases_concurrency_slot() -> None:
    """CP-012-13: a failing job in one repository does not prevent another
    repository's job from completing in the same cycle - the failure
    doesn't leak or hold a concurrency slot hostage."""
    repo_a = _repo("repo-a")
    repo_b = _repo("repo-b")
    config = _config([repo_a, repo_b], max_concurrent_jobs=2)
    failing_issue = _issue(repo_a.full_name, 1, labels=["devbot:ready"], title="Fails")
    succeeding_issue = _issue(repo_b.full_name, 1, labels=["devbot:ready"], title="Succeeds")
    github_client = FakeGitHubClient(
        {repo_a.full_name: [failing_issue], repo_b.full_name: [succeeding_issue]}
    )
    implementer_runner = MagicMock()

    def _run(repository, prompt):
        if repository.full_name == repo_a.full_name:
            raise RuntimeError("agent crashed")
        return AgentRunResult(executed=True, dry_run=False, message="ok")

    implementer_runner.run.side_effect = _run
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    results = service.run_cycle()

    results_by_repo = {result.task.repository: result for result in results}
    assert results_by_repo[repo_a.full_name].status is PollingStatus.AGENT_FAILED
    assert results_by_repo[repo_b.full_name].status is PollingStatus.AGENT_COMPLETED


def test_parallel_cycle_runs_jobs_for_different_repositories() -> None:
    """CP-012-11 (orchestration level): with `max_concurrent_jobs=2`, one
    cycle runs a ready job in each of two different repositories."""
    repo_a = _repo("repo-a")
    repo_b = _repo("repo-b")
    config = _config([repo_a, repo_b], max_concurrent_jobs=2)
    issue_a = _issue(repo_a.full_name, 1, labels=["devbot:ready"], title="A")
    issue_b = _issue(repo_b.full_name, 1, labels=["devbot:ready"], title="B")
    github_client = FakeGitHubClient({repo_a.full_name: [issue_a], repo_b.full_name: [issue_b]})
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    results = service.run_cycle()

    assert len(results) == 2
    assert {result.status for result in results} == {PollingStatus.AGENT_COMPLETED}
    assert implementer_runner.run.call_count == 2


def test_repository_error_during_candidate_collection_does_not_block_other_repositories() -> None:
    """A PR-lookup failure while gathering one repository's candidates is
    surfaced as its own error result but must not prevent a different
    repository's independent job from running in the same cycle."""
    repo_a = _repo("repo-a")
    repo_b = _repo("repo-b")
    config = _config([repo_a, repo_b], max_concurrent_jobs=2)
    review_issue = _issue(repo_a.full_name, 1, labels=["devbot:rework"], title="Broken")
    ready_issue = _issue(repo_b.full_name, 1, labels=["devbot:ready"], title="Fine")
    github_client = FakeGitHubClient(
        {repo_a.full_name: [review_issue], repo_b.full_name: [ready_issue]},
        error=None,
    )

    def _list_pull_requests(repository, **_kwargs):
        if repository.full_name == repo_a.full_name:
            raise RuntimeError("network exploded")
        return []

    github_client.list_pull_requests = _list_pull_requests  # type: ignore[method-assign]
    rework_service = MagicMock()
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=rework_service,
    )

    results = service.run_cycle()

    results_by_repo = {result.task.repository: result for result in results}
    assert results_by_repo[repo_a.full_name].status is PollingStatus.ITERATION_ERROR
    assert results_by_repo[repo_b.full_name].status is PollingStatus.AGENT_COMPLETED


# --- Task 013: observability / debug logging -------------------------------


def test_zero_managed_repositories_logs_diagnostic_and_skips_polling(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-013-5: zero managed repositories is diagnosed distinctly from
    `NO_READY_TASK` (`no_managed_repositories`) and the cycle is skipped
    before any GitHub call is made."""
    config = _config([])
    github_client = MagicMock()
    service = PollingService(
        config=config, github_client=github_client, implementer_runner=MagicMock()
    )

    with caplog.at_level(logging.WARNING, logger="devbot"):
        result = service.run_once()

    assert result.status is PollingStatus.NO_MANAGED_REPOSITORIES
    github_client.list_issues.assert_not_called()
    diagnostics = [
        r for r in caplog.records if getattr(r, "event", None) == "no_managed_repositories"
    ]
    assert len(diagnostics) == 1


def test_debug_log_contains_repository_search_diagnostics(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-013-6: DEBUG logs the per-repository search state/label filter
    and result count DevBot used to decide what to search for."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.DEBUG, logger="devbot"):
        service.run_once()

    search_records = [r for r in caplog.records if getattr(r, "event", None) == "repository_search"]
    assert len(search_records) == 1
    record = search_records[0]
    assert record.repository == repo.full_name
    assert record.state == "open"
    assert record.result_count == 1
    assert isinstance(record.cycle_id, str) and record.cycle_id


def test_info_logging_omits_debug_search_details(caplog: pytest.LogCaptureFixture) -> None:
    """CP-013-11: at INFO (the default), the DEBUG-only search diagnostics
    never appear - they are suppressed at the source, not merely hidden by
    the test."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    assert not any(getattr(r, "event", None) == "repository_search" for r in caplog.records)
    assert not any(r.levelno == logging.DEBUG for r in caplog.records)


def test_selected_job_log_contains_correlation_fields(caplog: pytest.LogCaptureFixture) -> None:
    """CP-013-8: a selected Job's log line carries the cycle/repository/
    issue/job-type correlation fields an operator needs to trace one Job
    across every log line it produced."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 5, labels=["devbot:ready"], title="Fix bug")
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    selected = [r for r in caplog.records if getattr(r, "event", None) == "job_selected"]
    assert len(selected) == 1
    record = selected[0]
    assert record.repository == repo.full_name
    assert record.issue_number == 5
    assert record.job_type == "implement"
    assert isinstance(record.cycle_id, str) and record.cycle_id


def test_job_log_records_elapsed_time(caplog: pytest.LogCaptureFixture) -> None:
    """CP-013-9: the Job end log records how long the Job took, in
    milliseconds."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    finished = [r for r in caplog.records if getattr(r, "event", None) == "job_finished"]
    assert len(finished) == 1
    record = finished[0]
    assert record.status == "agent_completed"
    assert isinstance(record.elapsed_ms, float)
    assert record.elapsed_ms >= 0


def test_candidate_exclusion_logged_for_already_reviewed_head(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-013-7 (polling-level): a review candidate skipped because its PR
    head is already reviewed logs a structured `already_reviewed_head`
    exclusion, not a free-form message."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug")
    linked_pr = _pull_request(101, issue_number=7, head_sha="sha-1")
    marker_comment = _comment(
        comment_id=99, body=f"# Review Summary\n\n{build_review_marker('sha-1')}"
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        comments_by_issue={(repo.full_name, 101): [marker_comment]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        review_service=MagicMock(),
    )

    with caplog.at_level(logging.DEBUG, logger="devbot"):
        service.run_once()

    excluded = [r for r in caplog.records if getattr(r, "event", None) == "candidate_excluded"]
    reasons = {r.reason for r in excluded if r.issue_number == 7}
    assert "already_reviewed_head" in reasons


def test_logging_failure_does_not_abort_job_execution() -> None:
    """CP-013-12: a broken log handler (raises on every `emit()`) must
    never change a Job's outcome - logging is a side channel, not part of
    the Job's own success/failure contract."""

    class _RaisingHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            raise RuntimeError("logging backend unavailable")

    broken_logger = logging.getLogger("devbot.test-cp-013-12")
    broken_logger.handlers.clear()
    broken_logger.addHandler(_RaisingHandler())
    broken_logger.setLevel(logging.DEBUG)
    broken_logger.propagate = False

    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        logger=broken_logger,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    assert result.task is not None
    assert result.task.number == 1
