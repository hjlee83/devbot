import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.agents.base import AgentRunResult
from devbot.agents.codex import CodexRunner
from devbot.automerge import AutomergeService
from devbot.delivery import DeliveryResult, VerificationResult
from devbot.github_client import (
    CombinedCommitStatus,
    GitHubAuthenticationError,
    GitHubIssue,
    PullRequest,
    PullRequestComment,
)
from devbot.github_retry import GitHubTransientError
from devbot.github_write_client import GitHubWriteClient, MergePullRequestResult, PullRequestInfo
from devbot.issue_state import IssueStateWriter, _current_state
from devbot.models import AgentOutcome, DevBotConfig, RepositoryConfig, TaskState
from devbot.polling import (
    RESUME_ATTEMPT_LIMIT,
    PollingService,
    PollingStatus,
    _task_state_from_labels,
    find_linked_pull_request,
)
from devbot.review import ReviewService, build_review_marker
from devbot.rework import ReworkService
from devbot.timeline import TimelineService, parse_events
from devbot.validation import ValidationFailureCategory
from devbot.workspace import WorkspaceValidationError
from devbot.worktree import (
    PreparedWorkspace,
    ReviewIntegrationValidation,
    WorkspacePreparationError,
    WorkspacePreparationFailure,
)


class FakeGitHubClient:
    """No-network stand-in for GitHubClient, keyed by repository full name."""

    def __init__(
        self,
        issues_by_repo: dict[str, list[GitHubIssue]] | None = None,
        comments_by_issue: dict[tuple[str, int], list[PullRequestComment]] | None = None,
        pull_requests_by_repo: dict[str, list[PullRequest]] | None = None,
        check_runs_by_ref: dict[tuple[str, str], list[dict[str, object]]] | None = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self._issues_by_repo = issues_by_repo or {}
        self._comments_by_issue = comments_by_issue or {}
        self._pull_requests_by_repo = pull_requests_by_repo or {}
        self._check_runs_by_ref = check_runs_by_ref or {}
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
        pull_requests = self._pull_requests_by_repo.get(repository.full_name, [])
        state = _kwargs.get("state", "open")
        if state == "closed":
            return [pull_request for pull_request in pull_requests if pull_request.merged]
        return [pull_request for pull_request in pull_requests if not pull_request.merged]

    def list_workflow_runs_for_ref(self, repository: RepositoryConfig, head_sha: str):
        if self._error is not None:
            raise self._error
        return []

    def get_combined_status_for_ref(self, repository: RepositoryConfig, ref: str):
        if self._error is not None:
            raise self._error
        return CombinedCommitStatus(state="pending", total_count=0)

    def list_check_runs_for_ref(self, repository: RepositoryConfig, ref: str):
        if self._error is not None:
            raise self._error
        return self._check_runs_by_ref.get((repository.full_name, ref), [])


def _repo(name: str, *, enabled: bool = True) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo=name, enabled=enabled, local_path=Path(f"/tmp/workspace/{name}")
    )


def _automerge_repo(name: str, *, is_self_repo: bool = False) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo=name,
        enabled=True,
        local_path=Path(f"/tmp/workspace/{name}"),
        automerge_allowed=True,
        is_self_repo=is_self_repo,
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
    merged: bool = False,
) -> PullRequest:
    return PullRequest(
        number=number,
        head_ref=head_ref,
        head_sha=head_sha,
        body=f"Closes #{issue_number}",
        html_url=f"https://github.com/someone/myrepo/pull/{number}",
        merged=merged,
    )


def _ready_to_merge_pull_request(number: int, *, issue_number: int) -> PullRequest:
    return replace(
        _pull_request(number, issue_number=issue_number),
        labels=("devbot:ready-to-merge",),
    )


def _planner_issue_body(*, branch: str, pr_number: int) -> str:
    return (
        "- Contract: `tasks/025-planner-linked-pr-resolution.md`\n"
        f"- Branch: `{branch}`\n"
        f"- Pull Request: #{pr_number}\n\n"
        "- Produce `results/025-planner-linked-pr-resolution.md`.\n"
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


def _successful_delivery() -> MagicMock:
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=1, html_url="https://github.com/someone/myrepo/pull/1"),
        dry_run=False,
        message="delivered",
    )
    return delivery


def _operator_repo(tmp_path: Path, *, name: str = "myrepo") -> RepositoryConfig:
    """A `RepositoryConfig` whose `local_path` is a real, existing
    directory with a `.git` entry - the Task 023 worktree-based preflight
    (`ensure_repository_present`) checks the filesystem for real, unlike
    the legacy `ensure_workspace_ready` (fully fakeable via injection)."""
    path = tmp_path / name
    path.mkdir(parents=True, exist_ok=True)
    (path / ".git").mkdir(exist_ok=True)
    return RepositoryConfig(owner="someone", repo=name, enabled=True, local_path=path)


def _prepared_workspace(
    repo: RepositoryConfig,
    *,
    branch: str,
    issue_number: int,
    pull_request: PullRequest | None,
    worktree_path: Path | None = None,
    contract_path: str | None = None,
    result_path: str | None = None,
    reused: bool = False,
    dirty: bool = False,
) -> PreparedWorkspace:
    """A canned `WorktreeManager.prepare()` outcome (Task 023) for tests
    that inject a fake `prepare_workspace` instead of running real Git."""
    path = worktree_path or Path(f"/tmp/workspace/{repo.repo}/.worktrees/issue-{issue_number}")
    return PreparedWorkspace(
        repository=replace(repo, local_path=path, host_checkout_path=repo.local_path),
        branch=branch,
        base_branch=repo.default_branch,
        issue_number=issue_number,
        pull_request=pull_request,
        worktree_path=path,
        reused=reused,
        dirty=dirty,
        contract_path=contract_path,
        result_path=result_path,
    )


class _TimelineBackend:
    """Minimal in-memory Issue-comment store for wiring a real
    `TimelineService` without HTTP mocking (Task 024) - duck-typed to only
    the `read_client`/`write_client` methods `TimelineService` calls."""

    def __init__(self, *, issue: GitHubIssue, seed_body: str | None = None) -> None:
        self._issue = issue
        self._comment_id: int | None = 1 if seed_body is not None else None
        self._body = seed_body or ""
        self.create_calls = 0
        self.update_calls = 0

    def get_issue(self, repository: RepositoryConfig, issue_number: int) -> GitHubIssue:
        return self._issue

    def list_issue_comments(self, repository: RepositoryConfig, issue_number: int):
        if self._comment_id is None:
            return []
        return [
            PullRequestComment(
                id=self._comment_id,
                author="devbot",
                body=self._body,
                created_at=self._issue.created_at,
                reactions={},
            )
        ]

    def create_comment(self, repository: RepositoryConfig, issue_number: int, body: str) -> None:
        self._comment_id = 1
        self._body = body
        self.create_calls += 1

    def update_comment(self, repository: RepositoryConfig, comment_id: int, body: str) -> None:
        self._body = body
        self.update_calls += 1

    @property
    def body(self) -> str:
        return self._body


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

def test_ready_to_merge_pr_is_merged_and_issue_marked_done() -> None:
    repo = _automerge_repo("myrepo")
    issue = _issue(
        repo.full_name,
        42,
        labels=["devbot:review"],
        body="Pull Request: #9",
    )
    pull_request = _ready_to_merge_pull_request(9, issue_number=42)
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]},
        pull_requests_by_repo={repo.full_name: [pull_request]},
        check_runs_by_ref={
            (repo.full_name, pull_request.head_sha): [
                {"name": "pytest", "status": "completed", "conclusion": "success"}
            ]
        },
    )
    config = _config([repo], dry_run=False, automerge_enabled=True)
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.merge_pull_request.return_value = MergePullRequestResult(
        sha="merge-sha", merged=True, message="merged"
    )
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        state_writer=state_writer,
        automerge_service=AutomergeService(
            config=config,
            write_client=write_client,
            state_writer=state_writer,
            list_workflow_runs_for_ref=github_client.list_workflow_runs_for_ref,
            get_combined_status_for_ref=github_client.get_combined_status_for_ref,
            list_check_runs_for_ref=github_client.list_check_runs_for_ref,
        ),
    )

    result = service.run_once()

    assert result.status is PollingStatus.MERGED
    write_client.merge_pull_request.assert_called_once_with(
        repo,
        9,
        expected_head_sha=pull_request.head_sha,
        commit_title="Merge PR #9",
        commit_message="Merged automatically by DevBot after MERGE READY and green CI.",
        merge_method="merge",
    )
    write_client.set_labels.assert_called_once_with(repo, 42, ["devbot:done"])


def test_run_cycle_does_not_crash_when_check_runs_lookup_fails() -> None:
    """Issue #124: a `GitHubClientError` from the automerge check-runs
    lookup (e.g. the deployed token's real, confirmed 403 on `.../check
    -runs`) must not propagate out of `run_cycle()` - unlike
    `run_forever()`, `devbot --once`'s call site has no top-level
    try/except of its own, so this used to crash the whole process."""
    repo = _automerge_repo("myrepo")
    issue = _issue(
        repo.full_name,
        42,
        labels=["devbot:review"],
        body="Pull Request: #9",
    )
    pull_request = _ready_to_merge_pull_request(9, issue_number=42)
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]},
        pull_requests_by_repo={repo.full_name: [pull_request]},
    )
    config = _config([repo], dry_run=False, automerge_enabled=True)
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)

    def _raise_check_runs_error(
        _repo: RepositoryConfig, _ref: str
    ) -> list[dict[str, object]]:
        raise GitHubAuthenticationError("GitHub authentication failed: 403 Forbidden")

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        state_writer=state_writer,
        automerge_service=AutomergeService(
            config=config,
            write_client=write_client,
            state_writer=state_writer,
            list_workflow_runs_for_ref=github_client.list_workflow_runs_for_ref,
            get_combined_status_for_ref=github_client.get_combined_status_for_ref,
            list_check_runs_for_ref=_raise_check_runs_error,
        ),
    )

    result = service.run_once()  # must not raise

    assert result.status is PollingStatus.MERGE_BLOCKED
    write_client.merge_pull_request.assert_not_called()


def test_merged_linked_pr_reconciles_review_issue_to_done() -> None:
    repo = _automerge_repo("myrepo")
    issue = _issue(
        repo.full_name,
        42,
        labels=["devbot:review"],
        body="Pull Request: #9",
    )
    merged_pull_request = _pull_request(9, issue_number=42, merged=True)
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]},
        pull_requests_by_repo={repo.full_name: [merged_pull_request]},
    )
    config = _config([repo], dry_run=False, automerge_enabled=True)
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        state_writer=state_writer,
        automerge_service=AutomergeService(
            config=config,
            write_client=write_client,
            state_writer=state_writer,
            list_workflow_runs_for_ref=github_client.list_workflow_runs_for_ref,
            get_combined_status_for_ref=github_client.get_combined_status_for_ref,
            list_check_runs_for_ref=github_client.list_check_runs_for_ref,
        ),
    )

    result = service.run_once()

    assert result.status is PollingStatus.MERGED
    write_client.merge_pull_request.assert_not_called()
    write_client.set_labels.assert_called_once_with(repo, 42, ["devbot:done"])


def test_rework_issue_without_linked_pull_request_escalates_to_manual_action() -> None:
    """CP-B1: a `rework` Issue with an unprocessed `@devbot` comment but no
    PR whose body references it (an anomalous data-integrity state - the
    PR may have been closed/merged outside DevBot, or this Issue was
    reopened after its PR was already resolved) can never resolve on its
    own - nothing changes between cycles. Escalate to `devbot:manual-action`
    (via claim() then require_manual_action(), since rework->manual-action
    is not itself a legal direct transition) instead of repeating the same
    hard error forever."""
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
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = MagicMock(spec=ReworkService)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    rework_service.process.assert_not_called()
    assert write_client.set_labels.call_args_list[0].args == (repo, 7, ["devbot:working"])
    assert write_client.set_labels.call_args_list[-1].args == (repo, 7, ["devbot:manual-action"])


def test_review_issue_without_linked_pull_request_escalates_to_manual_action() -> None:
    """Same as the rework case above, for a `devbot:review` Issue."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue = _issue(repo.full_name, 8, labels=["devbot:review"], title="Fix bug")
    unrelated_pr = _pull_request(101, issue_number=999)
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [unrelated_pr]},
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    review_service = MagicMock(spec=ReviewService)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    review_service.process.assert_not_called()
    assert write_client.set_labels.call_args_list[0].args == (repo, 8, ["devbot:working"])
    assert write_client.set_labels.call_args_list[-1].args == (repo, 8, ["devbot:manual-action"])


def test_planner_issue_unresolved_pr_escalates_to_manual_action() -> None:
    """CP-B1: a Planner Issue's explicitly-declared PR that can't be
    resolved (`_planner_pr_resolution_failure`) is the same class of
    structurally-unresolvable state as a plain missing linked PR - also
    escalates instead of looping forever."""
    repo = _repo("myrepo")
    config = _config([repo])
    planner_issue = _issue(
        repo.full_name,
        9,
        labels=["devbot:rework"],
        title="Fix bug",
        body=_planner_issue_body(branch="devbot/planner-branch", pr_number=42),
    )
    github_client = FakeGitHubClient({repo.full_name: [planner_issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    rework_service = MagicMock(spec=ReworkService)
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        rework_service=rework_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    rework_service.process.assert_not_called()
    assert write_client.set_labels.call_args_list[-1].args == (repo, 9, ["devbot:manual-action"])


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


def test_transient_github_failure_preserves_task_state() -> None:
    repo = _repo("myrepo")
    issue = _issue(repo.full_name, 30, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.set_labels.side_effect = GitHubTransientError(
        "Transient GitHub API failure 503 exhausted",
        status=503,
        endpoint_category="write",
        attempts=3,
    )
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    implementer = MagicMock()
    service = PollingService(
        config=_config([repo], dry_run=False),
        github_client=github_client,
        implementer_runner=implementer,
        reviewer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=_successful_delivery(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.ITERATION_ERROR
    write_client.set_labels.assert_called_once_with(repo, 30, ["devbot:working"])
    write_client.create_comment.assert_not_called()
    implementer.run.assert_not_called()


def test_github_retry_recovery_does_not_duplicate_side_effects() -> None:
    repo = _repo("myrepo")
    issue = _issue(repo.full_name, 31, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    write_client.set_labels.side_effect = [
        GitHubTransientError(
            "Transient GitHub API failure 503 exhausted",
            status=503,
            endpoint_category="write",
            attempts=3,
        ),
        None,
        None,
    ]
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    implementer = MagicMock()
    implementer.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    service = PollingService(
        config=_config([repo], dry_run=False),
        github_client=github_client,
        implementer_runner=implementer,
        reviewer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=_successful_delivery(),
    )

    first = service.run_once()
    second = service.run_once()

    assert first.status is PollingStatus.ITERATION_ERROR
    assert second.status is PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list == [
        ((repo, 31, ["devbot:working"]),),
        ((repo, 31, ["devbot:working"]),),
        ((repo, 31, ["devbot:review"]),),
    ]
    write_client.create_comment.assert_not_called()
    implementer.run.assert_called_once()


def test_existing_workflows_remain_compatible_with_github_retry() -> None:
    repo = _repo("myrepo")
    issue = _issue(repo.full_name, 32, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    implementer = MagicMock()
    implementer.run.return_value = AgentRunResult(executed=True, dry_run=True, message="ok")
    service = PollingService(
        config=_config([repo]),
        github_client=github_client,
        implementer_runner=implementer,
        reviewer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    implementer.run.assert_called_once()


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


# ---- Task 023: host-managed workspace preparation ----


def test_explicit_issue_pr_resolves_without_closing_keyword() -> None:
    issue = _issue(
        "someone/myrepo",
        49,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch="task/025-planner-linked-pr-resolution", pr_number=48),
    )
    planner_pr = replace(
        _pull_request(48, issue_number=999, head_ref="task/025-planner-linked-pr-resolution"),
        body="Planner-created PR without a closing keyword yet.",
    )

    assert find_linked_pull_request(issue, [planner_pr]) is planner_pr


def test_planner_pr_head_branch_is_reused(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(
        repo.full_name,
        49,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch="task/025-planner-linked-pr-resolution", pr_number=48),
    )
    linked_pr = replace(
        _pull_request(48, issue_number=999, head_ref="task/025-planner-linked-pr-resolution"),
        body="No closing keyword.",
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch=linked_pr.head_ref, issue_number=49, pull_request=linked_pr
    )

    def fake_prepare(
        repository: RepositoryConfig,
        issue_arg: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        assert issue_arg.number == issue.number
        assert issue_arg.body == issue.body
        assert linked_pull_request is linked_pr
        return prepared

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=48, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=fake_prepare,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    delivery.deliver.assert_called_once()
    assert delivery.deliver.call_args.args[2] == "task/025-planner-linked-pr-resolution"


def test_missing_explicit_pr_rejects_fallback_branch(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(
        repo.full_name,
        49,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch="task/025-planner-linked-pr-resolution", pr_number=48),
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: []}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    prepare_workspace = MagicMock()

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=prepare_workspace,
        state_writer=state_writer,
        delivery=MagicMock(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_PREPARATION_FAILED
    assert "expected_pr=#48" in result.message
    assert "expected_branch='task/025-planner-linked-pr-resolution'" in result.message
    prepare_workspace.assert_not_called()
    agent_runner.run.assert_not_called()


def test_legacy_issue_without_pr_keeps_fallback_branch(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 50, labels=["devbot:ready"], title="Legacy fallback")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: []}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    fallback_prepared = _prepared_workspace(
        repo,
        branch="devbot/myrepo-50-legacy-fallback",
        issue_number=50,
        pull_request=None,
    )

    def fake_prepare(
        repository: RepositoryConfig,
        issue_arg: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        assert linked_pull_request is None
        return fallback_prepared

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=50, html_url="https://example.test/pr/50"),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=fake_prepare,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    delivery.deliver.assert_called_once()
    assert delivery.deliver.call_args.args[2] == "devbot/myrepo-50-legacy-fallback"


def test_existing_workflows_compatible_with_planner_pr_resolution() -> None:
    issue = _issue("someone/myrepo", 51, labels=["devbot:ready"], body="manual issue")
    linked_pr = _pull_request(51, issue_number=51, head_ref="devbot/myrepo-51-manual")

    assert find_linked_pull_request(issue, [linked_pr]) is linked_pr


def test_planner_pr_resolution_diagnostics(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(
        repo.full_name,
        49,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch="task/025-planner-linked-pr-resolution", pr_number=404),
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: []}
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=MagicMock(),
        state_writer=IssueStateWriter(client=MagicMock(spec=GitHubWriteClient), dry_run=False),
        delivery=MagicMock(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_PREPARATION_FAILED
    assert "linked_branch_missing" in result.message
    assert "expected_pr=#404" in result.message
    assert "expected_branch='task/025-planner-linked-pr-resolution'" in result.message


def test_existing_planner_workspace_is_resolved_before_agent(tmp_path: Path) -> None:
    """CP-023-1: the linked Task branch/PR is resolved (via
    `prepare_workspace`, itself given whatever `github_client` already
    fetched) strictly before the Agent runs - never the other way round."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 50, labels=["devbot:ready"])
    linked_pr = _pull_request(44, issue_number=50, head_ref="task/023-host-managed")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)

    call_order: list[str] = []

    def fake_prepare(
        repository: RepositoryConfig,
        issue_arg: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        call_order.append("prepare")
        assert linked_pull_request is linked_pr
        return _prepared_workspace(
            repo, branch=linked_pull_request.head_ref, issue_number=issue_arg.number,
            pull_request=linked_pull_request,
        )

    def fake_run(repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        call_order.append("agent")
        return AgentRunResult(executed=True, dry_run=False, message="ok")

    agent_runner = MagicMock()
    agent_runner.run.side_effect = fake_run
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=44, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=fake_prepare,
        state_writer=state_writer,
        delivery=delivery,
    )

    service.run_once()

    assert call_order == ["prepare", "agent"]


def test_agent_prompt_contains_prepared_workspace_context(tmp_path: Path) -> None:
    """CP-023-5: the Agent prompt states the prepared branch, PR, Issue,
    contract, and Result paths, plus the explicit "don't discover/create
    another branch or PR" policy - the exact context Task 022's daemon
    attempt was missing."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(
        repo.full_name,
        51,
        labels=["devbot:ready"],
        body="- Contract: `tasks/023-x.md`\n- Produce `results/023-x.md`.\n",
    )
    linked_pr = _pull_request(44, issue_number=51, head_ref="task/023-x")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo,
        branch="task/023-x",
        issue_number=51,
        pull_request=linked_pr,
        contract_path="tasks/023-x.md",
        result_path="results/023-x.md",
    )

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=44, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
    )

    service.run_once()

    prompt = agent_runner.run.call_args.args[1]
    assert "task/023-x" in prompt
    assert "#44" in prompt
    assert "#51" in prompt
    assert "tasks/023-x.md" in prompt
    assert "results/023-x.md" in prompt
    assert "do not create another branch" in prompt.lower()


def test_implementation_does_not_require_agent_network_access(tmp_path: Path) -> None:
    """CP-023-6: once the workspace is prepared, the Agent invocation
    itself never needs to (re-)discover the branch/PR over the network -
    `github_client.list_pull_requests` is called exactly once per Job
    (inside `prepare_workspace`'s resolution, not again for delivery), and
    the prompt tells the Agent so explicitly."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 52, labels=["devbot:ready"])
    linked_pr = _pull_request(44, issue_number=52, head_ref="task/023-net")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    original_list_pull_requests = github_client.list_pull_requests
    call_count = {"n": 0}

    def counted(*args: object, **kwargs: object) -> list[PullRequest]:
        call_count["n"] += 1
        return original_list_pull_requests(*args, **kwargs)  # type: ignore[arg-type]

    github_client.list_pull_requests = counted  # type: ignore[method-assign]

    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/023-net", issue_number=52, pull_request=linked_pr
    )

    def networkless_agent_run(repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        assert "git fetch" in prompt  # told explicitly it need not discover anything itself
        assert str(prepared.worktree_path) in prompt
        return AgentRunResult(executed=True, dry_run=False, message="implemented")

    agent_runner = MagicMock()
    agent_runner.run.side_effect = networkless_agent_run
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=44, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    assert call_count["n"] == 1


def test_delivery_uses_prepared_worktree_branch(tmp_path: Path) -> None:
    """CP-023-7: verification/commit/push/PR-reuse run against the prepared
    worktree's `RepositoryConfig` (its `local_path`, not the operator
    checkout's) and its resolved branch - never re-derived."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 53, labels=["devbot:ready"])
    linked_pr = _pull_request(44, issue_number=53, head_ref="task/023-deliver")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    worktree_path = Path("/tmp/workspace/.devbot-worktrees/myrepo/issue-53")
    prepared = _prepared_workspace(
        repo,
        branch="task/023-deliver",
        issue_number=53,
        pull_request=linked_pr,
        worktree_path=worktree_path,
    )

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=44, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
    )

    service.run_once()

    delivery.deliver.assert_called_once()
    args, kwargs = delivery.deliver.call_args
    assert args[0].local_path == worktree_path
    assert args[2] == "task/023-deliver"
    assert kwargs["linked_pull_request"] is linked_pr
    # the Agent ran in that same worktree, not the operator checkout.
    assert agent_runner.run.call_args.args[0].local_path == worktree_path


def test_agent_timeout_is_classified_resumable(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
    )
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(
        executed=False,
        dry_run=False,
        message="Agent timed out",
        outcome_hint=AgentOutcome.RESUMABLE_INTERRUPTION,
    )
    delivery = MagicMock()

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=write_client, dry_run=False),
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    assert "resumable_interruption" in result.message
    delivery.deliver.assert_not_called()
    assert write_client.set_labels.call_args_list[-1].args == (repo, 52, ["devbot:ready"])
    comment = write_client.create_comment.call_args.args[2]
    assert "devbot-resume:v1" in comment
    assert "attempt=1" in comment
    assert "reason=timeout" in comment


def test_timeout_preserves_unfinished_worktree(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    worktree_path = tmp_path / ".devbot-worktrees" / "myrepo" / "issue-52"
    worktree_path.mkdir(parents=True)
    unfinished = worktree_path / "unfinished.txt"
    unfinished.write_text("kept\n", encoding="utf-8")
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        worktree_path=worktree_path,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        dirty=True,
    )
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(
        executed=False,
        dry_run=False,
        message="timeout",
        outcome_hint=AgentOutcome.RESUMABLE_INTERRUPTION,
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=MagicMock(spec=GitHubWriteClient), dry_run=False),
        delivery=MagicMock(),
    )

    service.run_once()

    assert unfinished.read_text(encoding="utf-8") == "kept\n"
    assert worktree_path.exists()


def test_matching_dirty_worktree_is_detected_as_resumable(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        reused=True,
        dirty=True,
    )
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="done")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=51, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=MagicMock(spec=GitHubWriteClient), dry_run=False),
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    prompt = runner.run.call_args.args[1]
    assert "Resume Context (Task 026)" in prompt
    assert "Resume attempt: 1" in prompt
    assert "inspect the current repository diff" in prompt


def test_resume_prompt_includes_existing_work_instructions(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        reused=True,
        dirty=True,
    )
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="done")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=51, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=MagicMock(spec=GitHubWriteClient), dry_run=False),
        delivery=delivery,
    )

    service.run_once()

    prompt = runner.run.call_args.args[1].lower()
    assert "preserve completed work" in prompt
    assert "do not recreate, reset, delete, overwrite, or discard" in prompt
    assert "do not create a fallback `devbot/devbot-*` branch" in prompt


def test_resume_reuses_existing_branch_and_pr(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-agent-resume-timeout-recovery"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        reused=True,
        dirty=True,
    )
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="done")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=51, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=MagicMock(spec=GitHubWriteClient), dry_run=False),
        delivery=delivery,
    )

    service.run_once()

    delivery.deliver.assert_called_once()
    args, kwargs = delivery.deliver.call_args
    assert args[2] == branch
    assert kwargs["linked_pull_request"] is linked_pr


def test_resume_attempt_limit_requires_manual_action(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    marker = (
        "<!-- devbot-resume:v1 issue=52 pr=51 "
        f"attempt={RESUME_ATTEMPT_LIMIT} branch={branch} reason=timeout -->"
    )
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        reused=True,
        dirty=True,
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    runner = MagicMock()

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]},
            comments_by_issue={(repo.full_name, 52): [_comment(body=marker)]},
            pull_requests_by_repo={repo.full_name: [linked_pr]},
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=write_client, dry_run=False),
        delivery=MagicMock(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    runner.run.assert_not_called()
    assert write_client.set_labels.call_args_list[-1].args == (
        repo,
        52,
        ["devbot:manual-action"],
    )
    assert "resume attempt limit exceeded" in write_client.create_comment.call_args.args[2]


def test_timeout_and_resume_are_recorded_idempotently(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    marker = "<!-- devbot-resume:v1 issue=52 pr=51 attempt=1 branch=other reason=timeout -->"
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        dirty=True,
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(
        executed=False,
        dry_run=False,
        message="timeout",
        outcome_hint=AgentOutcome.RESUMABLE_INTERRUPTION,
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]},
            comments_by_issue={(repo.full_name, 52): [_comment(body=marker)]},
            pull_requests_by_repo={repo.full_name: [linked_pr]},
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=write_client, dry_run=False),
        delivery=MagicMock(),
    )

    service.run_once()

    comment = write_client.create_comment.call_args.args[2]
    assert "devbot-resume:v1 issue=52 pr=51 attempt=1" in comment
    assert "branch=task/026-resume" in comment
    assert "failure_category: agent_execution_failed" in comment
    assert "changed_files:" in comment


def test_resumed_execution_completes_existing_pr_delivery(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    branch = "task/026-resume"
    issue = _issue(
        repo.full_name,
        52,
        labels=["devbot:ready"],
        body=_planner_issue_body(branch=branch, pr_number=51),
    )
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        contract_path="tasks/026-agent-resume-timeout-recovery.md",
        reused=True,
        dirty=True,
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="completed")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=51, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=write_client, dry_run=False),
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 52, ["devbot:review"])
    assert delivery.deliver.call_args.kwargs["linked_pull_request"] is linked_pr


def test_unsafe_resume_is_rejected_without_deleting_work(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    worktree_path = tmp_path / ".devbot-worktrees" / "myrepo" / "issue-52"
    worktree_path.mkdir(parents=True)
    kept = worktree_path / "kept.txt"
    kept.write_text("do not delete\n", encoding="utf-8")
    branch = "task/026-resume"
    issue = _issue(repo.full_name, 52, labels=["devbot:ready"], body=f"- Branch: `{branch}`\n")
    linked_pr = _pull_request(51, issue_number=52, head_ref=branch)
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=52,
        pull_request=linked_pr,
        worktree_path=worktree_path,
        reused=True,
        dirty=True,
        contract_path=None,
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    runner = MagicMock()

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient(
            {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
        ),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=IssueStateWriter(client=write_client, dry_run=False),
        delivery=MagicMock(),
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    runner.run.assert_not_called()
    assert kept.read_text(encoding="utf-8") == "do not delete\n"
    assert write_client.set_labels.call_args_list[-1].args == (
        repo,
        52,
        ["devbot:manual-action"],
    )


def test_existing_workflows_compatible_with_resume_recovery(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    issue = _issue(repo.full_name, 60, labels=["devbot:ready"])
    runner = MagicMock()
    runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")

    service = PollingService(
        config=_config([repo]),
        github_client=FakeGitHubClient({repo.full_name: [issue]}),
        implementer_runner=runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_COMPLETED
    assert "Resume Context (Task 026)" not in runner.run.call_args.args[1]


def test_workspace_preparation_failure_skips_agent_and_recovers_state(tmp_path: Path) -> None:
    """CP-023-9: a workspace-preparation failure is classified explicitly,
    never reaches the Agent or delivery, and restores the Issue to its
    pre-claim stable state - never left stuck in `devbot:working`."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 54, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)

    def failing_prepare(
        repository: RepositoryConfig,
        issue_arg: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        raise WorkspacePreparationError(
            WorkspacePreparationFailure.WORKTREE_CREATION_FAILED,
            "git worktree add failed: boom",
        )

    agent_runner = MagicMock()
    delivery = MagicMock()

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=failing_prepare,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_PREPARATION_FAILED
    assert "worktree_creation_failed" in result.message
    agent_runner.run.assert_not_called()
    delivery.deliver.assert_not_called()
    assert write_client.set_labels.call_args_list[0].args == (repo, 54, ["devbot:working"])
    assert write_client.set_labels.call_args_list[-1].args == (repo, 54, ["devbot:ready"])


def test_daemon_job_is_independent_of_operator_checkout_branch(tmp_path: Path) -> None:
    """CP-023-11: a Job succeeds regardless of the operator checkout's
    current branch or uncommitted files - the old `ensure_workspace_ready`
    dirty/branch-sensitive preflight is never even called once
    `prepare_workspace` is configured; only the isolated worktree matters."""
    operator_path = tmp_path / "operator"
    operator_path.mkdir()
    (operator_path / ".git").mkdir()  # only needs to look like a checkout to the real preflight
    repo = RepositoryConfig(owner="someone", repo="myrepo", enabled=True, local_path=operator_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 55, labels=["devbot:ready"])
    linked_pr = _pull_request(44, issue_number=55, head_ref="task/023-independent")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/023-independent", issue_number=55, pull_request=linked_pr
    )

    def poison_workspace_check(_repository: RepositoryConfig) -> None:
        raise WorkspaceValidationError(
            "operator checkout is dirty / on an unrelated branch - must never block a "
            "worktree-based Job"
        )

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=44, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=poison_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED


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


def test_validation_command_failure_routes_implement_to_rework() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 59, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(
            passed=False,
            failed_command=("uv", "run", "pytest"),
            output="1 failed",
            failure_category=ValidationFailureCategory.VALIDATION_COMMAND_FAILED,
        ),
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="Verification failed: uv run pytest",
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
    assert write_client.set_labels.call_args_list[-1].args == (repo, 59, ["devbot:rework"])


def test_external_validation_failure_routes_implement_to_manual_action() -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 60, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(
            passed=False,
            failed_command=("uv", "sync"),
            output="network unavailable",
            failure_category=ValidationFailureCategory.DEPENDENCY_NETWORK_UNAVAILABLE,
        ),
        committed=False,
        pushed=False,
        pull_request=None,
        dry_run=False,
        message="Verification failed: uv sync",
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
    assert write_client.set_labels.call_args_list[-1].args == (
        repo,
        60,
        ["devbot:manual-action"],
    )


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
    """When a linked PR already exists, delivery reports
    `no_repository_changes`, and (Task 021 Scope §7/§8) that PR's branch
    already carries git history evidence beyond its pre-existing
    contract-only commit, review resumes on that existing PR instead of
    blocking - the PR's mere existence is not enough on its own (see
    `test_review_requires_completed_implementation` for the case where
    that evidence is absent)."""
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
        has_implementation_evidence=lambda *_args: True,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 10, ["devbot:review"])


def test_review_requires_completed_implementation() -> None:
    """CP-021-7: an existing linked PR whose branch carries *no*
    implementation evidence beyond its own pre-existing contract-only
    commit, plus a clean workspace (`no_repository_changes`), must not
    enter `devbot:review` - this is Task 021's motivating incident
    (Issue #41/PR #40): the implementer never actually ran, DevBot saw no
    repository changes, and the pre-existing contract PR was wrongly
    treated as a completed implementation. It goes to `devbot:manual-action`
    instead, and `devbot:review` must never be written."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 12, labels=["devbot:ready"])
    contract_only_pr = _pull_request(
        40, issue_number=12, head_ref="task/021-agent-outcome-classification"
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [contract_only_pr]}
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
        has_implementation_evidence=lambda *_args: False,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 12, ["devbot:manual-action"])
    assert all(
        call.args[2] != ["devbot:review"] for call in write_client.set_labels.call_args_list
    )


def test_success_requires_explicit_completion() -> None:
    """CP-021-11: none of (a) a zero Agent process exit code, (b) the
    complete absence of any raised exception, (c) an existing linked PR, or
    (d) a clean/no-diff workspace - alone or all combined - may be treated
    as proof of a completed implementation. All four are true in this
    scenario (Task 021's exact motivating incident: a contract-only PR
    already exists, the Agent "ran" cleanly, and nothing changed) and the
    Issue must still not reach `devbot:review`. Unlike
    `test_review_requires_completed_implementation`, this relies on the
    *default* `has_implementation_evidence` (a real git check against a
    workspace path that does not exist in this test) to prove the
    conservative fail-safe default itself, not just an injected fake."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 14, labels=["devbot:ready"])
    contract_only_pr = _pull_request(41, issue_number=14, head_ref="task/contract-only-branch")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [contract_only_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    # (a) zero exit code, (b) no exception raised:
    agent_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="ok", returncode=0
    )
    delivery = MagicMock()
    # (d) clean workspace, (c) linked_pr above is the existing PR:
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

    assert result.status is not PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list[-1].args != (repo, 14, ["devbot:review"])


def test_contract_first_pr_reused_without_false_completion() -> None:
    """CP-021-9: a Task-contract-first Issue that already has a linked
    branch/PR (Planner-created) is reused - no duplicate branch/PR ever
    generated - and, when that branch/PR already carries real
    implementation evidence beyond its contract-only commit, a no-op
    re-verification run still safely resumes review on the *same* PR.
    Task 021's false-completion fix (CP-021-7) must not regress Task 016's
    CP-016-10 branch/PR reuse guarantee."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 11, labels=["devbot:ready"])
    linked_pr = _pull_request(30, issue_number=11, head_ref="task/021-existing-branch")
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
        has_implementation_evidence=lambda *_args: True,
    )

    result = service.run_once()

    delivery.deliver.assert_called_once()
    args, kwargs = delivery.deliver.call_args
    assert args[2] == "task/021-existing-branch"  # reused verbatim, never a fresh branch name
    assert kwargs["linked_pull_request"] is linked_pr
    assert result.status is PollingStatus.DELIVERED
    assert write_client.set_labels.call_args_list[-1].args == (repo, 11, ["devbot:review"])


@pytest.mark.parametrize(
    ("message", "returncode"),
    [
        ("I ran `gh pr list` but this needs your approval before I can continue.", 0),
        ("Network is unreachable: could not resolve host github.com", 0),
        ("fatal: Unable to create '.git/index.lock': File exists.", 0),
        ("No changes needed - this task is already implemented, skipping.", 0),
        ("Usage limit reached, resets at 09:00 UTC", 0),
        ("agent crashed midway", 1),
        ("", 0),
    ],
)
def test_delivery_requires_completed_implementation(message: str, returncode: int) -> None:
    """CP-021-6: delivery must never run for approval-required, network-
    blocked, repository-locked, session-limit, skipped, agent-failed, or
    unknown (empty-message) outcomes - even when the Agent process itself
    exited 0 (`AgentRunResult.failed` is False), matching Task 021 Scope
    §7 ("Delivery may run only after an outcome classified as
    implementation_completed")."""
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 13, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message=message, returncode=returncode
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
    assert result.status not in (PollingStatus.DELIVERED, PollingStatus.AGENT_COMPLETED)


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


def test_review_uses_prepared_pr_worktree_for_workspace_validation(tmp_path: Path) -> None:
    """Regression: REVIEW with host-managed worktrees must validate only the
    operator checkout's presence, prepare the linked PR worktree, and pass
    that prepared repository to ReviewService instead of validating or using
    the host checkout on main."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/027-autonomous-review-loop"
    review_issue = _issue(
        repo.full_name,
        53,
        labels=["devbot:review"],
        title="Task 027",
        body=_planner_issue_body(branch=branch, pr_number=54),
    )
    linked_pr = _pull_request(54, issue_number=53, head_ref=branch, head_sha="sha-027")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    review_service.process.return_value = MagicMock(
        status="MERGE READY", issue_state=TaskState.REVIEW, message="reviewed: MERGE READY"
    )
    worktree_path = tmp_path / ".devbot-worktrees" / "myrepo" / "issue-53"
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=53,
        pull_request=linked_pr,
        worktree_path=worktree_path,
    )
    legacy_validation = MagicMock(side_effect=WorkspaceValidationError("host main is dirty"))

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=legacy_validation,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REVIEWED
    legacy_validation.assert_not_called()
    review_service.process.assert_called_once()
    called_repository, called_issue, called_pr = review_service.process.call_args.args
    assert called_repository.local_path == worktree_path
    assert called_issue == review_issue
    assert called_pr is linked_pr


def test_review_validates_latest_main_integration_before_agent(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/030-review-integration"
    review_issue = _issue(
        repo.full_name,
        63,
        labels=["devbot:review"],
        title="Task 030",
        body=_planner_issue_body(branch=branch, pr_number=64),
    )
    linked_pr = _pull_request(64, issue_number=63, head_ref=branch, head_sha="sha-030")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=63,
        pull_request=linked_pr,
        worktree_path=tmp_path / "myrepo" / ".worktrees" / "issue-63",
    )
    validate_review_integration = MagicMock(
        return_value=ReviewIntegrationValidation(
            mergeable=True,
            method="git merge-tree --write-tree origin/main HEAD",
            message="mergeable tree=abc123",
        )
    )
    review_service.process.return_value = MagicMock(
        status="MERGE READY", issue_state=TaskState.REVIEW, message="reviewed: MERGE READY"
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        validate_review_integration=validate_review_integration,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.REVIEWED
    validate_review_integration.assert_called_once_with(prepared)
    review_service.process.assert_called_once()


def test_review_stops_when_latest_main_integration_conflicts(tmp_path: Path) -> None:
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/030-review-conflict"
    review_issue = _issue(
        repo.full_name,
        64,
        labels=["devbot:review"],
        title="Task 030",
        body=_planner_issue_body(branch=branch, pr_number=65),
    )
    linked_pr = _pull_request(65, issue_number=64, head_ref=branch, head_sha="sha-031")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=64,
        pull_request=linked_pr,
        worktree_path=tmp_path / "myrepo" / ".worktrees" / "issue-64",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        validate_review_integration=lambda _prepared: ReviewIntegrationValidation(
            mergeable=False,
            method="git merge-tree --write-tree origin/main HEAD",
            message="CONFLICT README.md",
        ),
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.BLOCKED
    assert "latest-main integration validation failed" in result.message
    review_service.process.assert_not_called()


def test_review_rejects_dirty_prepared_worktree_even_when_host_is_clean(tmp_path: Path) -> None:
    """Regression: once prepare returns, REVIEW validates the prepared
    worktree state. A clean host checkout must not mask a dirty prepared PR
    worktree."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/027-autonomous-review-loop"
    review_issue = _issue(
        repo.full_name,
        53,
        labels=["devbot:review"],
        title="Task 027",
        body=_planner_issue_body(branch=branch, pr_number=54),
    )
    linked_pr = _pull_request(54, issue_number=53, head_ref=branch, head_sha="sha-027")
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue]},
        pull_requests_by_repo={repo.full_name: [linked_pr]},
    )
    review_service = MagicMock()
    prepared = _prepared_workspace(
        repo,
        branch=branch,
        issue_number=53,
        pull_request=linked_pr,
        worktree_path=tmp_path / ".devbot-worktrees" / "myrepo" / "issue-53",
        dirty=True,
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        review_service=review_service,
    )

    result = service.run_once()

    assert result.status is PollingStatus.WORKSPACE_INVALID
    assert "prepared worktree has uncommitted changes" in result.message
    review_service.process.assert_not_called()


def test_autonomous_review_rework_rereview_loop_runs_without_manual_commands(
    tmp_path: Path,
) -> None:
    """Task 027 regression: IMPLEMENT delivery, REVIEW REQUEST CHANGES,
    REWORK, and re-REVIEW are selected by successive polling cycles from
    DevBot's own state writes, with the same Issue/PR/branch/worktree."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    branch = "task/027-autonomous-review-loop"
    issue_holder = {
        "issue": _issue(
            repo.full_name,
            53,
            labels=["devbot:ready"],
            title="Task 027",
            body=_planner_issue_body(branch=branch, pr_number=54),
        )
    }
    pr_holder = {
        "pr": _pull_request(54, issue_number=53, head_ref=branch, head_sha="sha-1")
    }
    pr_comments: list[PullRequestComment] = []
    github_client = FakeGitHubClient(
        {repo.full_name: [issue_holder["issue"]]},
        comments_by_issue={(repo.full_name, 54): pr_comments},
        pull_requests_by_repo={repo.full_name: [pr_holder["pr"]]},
    )

    def list_issues(repository: RepositoryConfig, *, state: str = "open", **kwargs: object):
        return [issue_holder["issue"]]

    def list_pull_requests(repository: RepositoryConfig, **kwargs: object):
        return [pr_holder["pr"]]

    github_client.list_issues = list_issues  # type: ignore[method-assign]
    github_client.list_pull_requests = list_pull_requests  # type: ignore[method-assign]

    write_client = MagicMock(spec=GitHubWriteClient)

    def set_labels(repository: RepositoryConfig, issue_number: int, labels: list[str]) -> None:
        issue_holder["issue"] = replace(issue_holder["issue"], labels=tuple(labels))

    write_client.set_labels.side_effect = set_labels
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared_paths: list[Path] = []

    def prepare(repository: RepositoryConfig, issue: GitHubIssue, linked: PullRequest | None):
        assert linked is pr_holder["pr"]
        prepared = _prepared_workspace(
            repo,
            branch=branch,
            issue_number=53,
            pull_request=linked,
            worktree_path=tmp_path / ".devbot-worktrees" / "myrepo" / "issue-53",
        )
        prepared_paths.append(prepared.worktree_path)
        return prepared

    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(
        executed=True, dry_run=False, message="implemented"
    )
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=54, html_url=pr_holder["pr"].html_url),
        dry_run=False,
        message="delivered",
    )
    review_service = MagicMock()

    def review_process(
        repository: RepositoryConfig,
        issue: GitHubIssue,
        pull_request: PullRequest,
        *,
        comments: list[PullRequestComment],
    ):
        assert repository.local_path == prepared_paths[-1]
        if pull_request.head_sha == "sha-1":
            pr_comments.append(
                _comment(
                    comment_id=99,
                    body=f"@devbot rework\n\n{build_review_marker('sha-1')}",
                )
            )
            set_labels(repo, issue.number, ["devbot:rework"])
            return MagicMock(
                status="REQUEST CHANGES",
                issue_state=TaskState.REWORK,
                message="reviewed: REQUEST CHANGES",
            )
        set_labels(repo, issue.number, ["devbot:review"])
        return MagicMock(
            status="MERGE READY",
            issue_state=TaskState.REVIEW,
            message="reviewed: MERGE READY",
        )

    review_service.process.side_effect = review_process
    rework_service = MagicMock(spec=ReworkService)

    def rework_process(
        repository: RepositoryConfig,
        issue: GitHubIssue,
        branch_arg: str,
        comments: list[PullRequestComment],
    ):
        assert repository.local_path == prepared_paths[-1]
        assert branch_arg == branch
        assert comments == pr_comments
        pr_holder["pr"] = _pull_request(54, issue_number=53, head_ref=branch, head_sha="sha-2")
        set_labels(repo, issue.number, ["devbot:review"])
        return MagicMock(triggered=True, issue_state=TaskState.REVIEW, message="reworked")

    rework_service.process.side_effect = rework_process
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=MagicMock(side_effect=WorkspaceValidationError("host main dirty")),
        prepare_workspace=prepare,
        state_writer=state_writer,
        delivery=delivery,
        review_service=review_service,
        rework_service=rework_service,
    )

    implemented = service.run_once()
    reviewed = service.run_once()
    reworked = service.run_once()
    rereviewed = service.run_once()

    assert implemented.status is PollingStatus.DELIVERED
    assert reviewed.status is PollingStatus.REVIEWED
    assert reworked.status is PollingStatus.REWORKED
    assert rereviewed.status is PollingStatus.REVIEWED
    assert review_service.process.call_count == 2
    rework_service.process.assert_called_once()
    assert delivery.deliver.call_args.args[0].local_path == prepared_paths[0]
    assert all(path == prepared_paths[0] for path in prepared_paths)
    assert issue_holder["issue"].labels == ("devbot:review",)


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


def test_list_pull_requests_called_once_per_repository_per_cycle_for_multiple_review_issues() -> (
    None
):
    """Issue #126: candidate collection previously called `list_pull_requests`
    once per REVIEW/REWORK Issue (N+1 API calls per cycle). With two
    devbot:review Issues sharing one repository, the fetch must now happen
    exactly once per repository per cycle and be reused for both."""
    repo = _repo("myrepo")
    config = _config([repo])
    review_issue_a = _issue(repo.full_name, 7, labels=["devbot:review"], title="Fix bug A")
    review_issue_b = _issue(repo.full_name, 8, labels=["devbot:review"], title="Fix bug B")
    linked_pr_a = _pull_request(101, issue_number=7, head_sha="sha-1")
    linked_pr_b = _pull_request(102, issue_number=8, head_sha="sha-2")
    marker_a = _comment(
        comment_id=99, body=f"# Review Summary\n\n{build_review_marker('sha-1')}"
    )
    marker_b = _comment(
        comment_id=100, body=f"# Review Summary\n\n{build_review_marker('sha-2')}"
    )
    github_client = FakeGitHubClient(
        {repo.full_name: [review_issue_a, review_issue_b]},
        comments_by_issue={
            (repo.full_name, 101): [marker_a],
            (repo.full_name, 102): [marker_b],
        },
        pull_requests_by_repo={repo.full_name: [linked_pr_a, linked_pr_b]},
    )
    list_pull_requests_calls = 0
    original_list_pull_requests = github_client.list_pull_requests

    def _counting_list_pull_requests(repository, **kwargs):
        nonlocal list_pull_requests_calls
        list_pull_requests_calls += 1
        return original_list_pull_requests(repository, **kwargs)

    github_client.list_pull_requests = _counting_list_pull_requests

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
    assert list_pull_requests_calls == 1


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


def test_shared_list_pull_requests_failure_reports_error_for_every_affected_task() -> None:
    """Issue #126: the single per-repository `list_pull_requests` fetch now
    guards multiple REWORK/REVIEW Issues at once - a failure there must
    still surface as its own `ITERATION_ERROR` for *each* affected task
    (matching the old per-task try/except's external behaviour), not just
    the first one."""
    repo = _repo("myrepo")
    config = _config([repo], max_concurrent_jobs=2)
    rework_issue = _issue(repo.full_name, 1, labels=["devbot:rework"], title="Needs rework")
    review_issue = _issue(repo.full_name, 2, labels=["devbot:review"], title="Needs review")
    github_client = FakeGitHubClient(
        {repo.full_name: [rework_issue, review_issue]},
    )

    def _list_pull_requests(repository, **_kwargs):
        raise RuntimeError("network exploded")

    github_client.list_pull_requests = _list_pull_requests  # type: ignore[method-assign]
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        rework_service=MagicMock(),
        review_service=MagicMock(),
    )

    results = service.run_cycle()

    results_by_issue = {result.task.number: result for result in results}
    assert results_by_issue[1].status is PollingStatus.ITERATION_ERROR
    assert results_by_issue[2].status is PollingStatus.ITERATION_ERROR


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


# --- Task 020: daemon queue summary and cycle logging ----------------------


def test_cycle_logs_queue_summary_once(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-1: exactly one operator-level queue summary is emitted per
    cycle, regardless of how many repositories/Issues are involved."""
    repo_a = _repo("repo-a")
    repo_b = _repo("repo-b")
    config = _config([repo_a, repo_b])
    ready_issue = _issue(repo_a.full_name, 1, labels=["devbot:ready"])
    blocked_issue = _issue(repo_b.full_name, 2, labels=["devbot:blocked"])
    github_client = FakeGitHubClient(
        {repo_a.full_name: [ready_issue], repo_b.full_name: [blocked_issue]}
    )
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    summaries = [r for r in caplog.records if getattr(r, "event", None) == "queue_summary"]
    assert len(summaries) == 1


def test_queue_summary_includes_all_workflow_states(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-2: the queue summary reports a count for every stable
    workflow state the scheduler uses (ready/review/rework/blocked/
    manual-action/working), each counted independently."""
    repo = _repo("myrepo")
    config = _config([repo])
    issues = [
        _issue(repo.full_name, 1, labels=["devbot:ready"]),
        _issue(repo.full_name, 2, labels=["devbot:review"]),
        _issue(repo.full_name, 3, labels=["devbot:rework"]),
        _issue(repo.full_name, 4, labels=["devbot:blocked"]),
        _issue(repo.full_name, 5, labels=["devbot:manual-action"]),
        _issue(repo.full_name, 6, labels=["devbot:working"]),
    ]
    github_client = FakeGitHubClient({repo.full_name: issues})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    record = next(r for r in caplog.records if getattr(r, "event", None) == "queue_summary")
    assert record.ready == 1
    assert record.review == 1
    assert record.rework == 1
    assert record.blocked == 1
    assert record.manual_action == 1
    assert record.working == 1


def test_no_runnable_task_is_reported_once(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-3: an empty cycle emits a single normalized `NO_RUNNABLE_TASK`
    cycle result rather than multiple free-form messages restating the
    same "nothing to do" fact."""
    repo = _repo("myrepo")
    config = _config([repo])
    github_client = FakeGitHubClient({repo.full_name: []})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        result = service.run_once()

    assert result.status is PollingStatus.NO_READY_TASK
    results = [r for r in caplog.records if getattr(r, "event", None) == "cycle_result"]
    assert len(results) == 1
    assert results[0].result == "NO_RUNNABLE_TASK"
    no_work_prose = [
        r
        for r in caplog.records
        if getattr(r, "event", None) is None
        and ("Issue가 없습니다" in r.getMessage() or "선택하지 않습니다" in r.getMessage())
    ]
    assert no_work_prose == []


def test_selected_job_summary_contains_identity_fields(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-4: a selected job's summary reports repository, Issue, PR
    (when already known), and job type together in one structured
    entry."""
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

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    selected = [r for r in caplog.records if getattr(r, "event", None) == "job_selected"]
    assert len(selected) == 1
    record = selected[0]
    assert record.repository == repo.full_name
    assert record.issue_number == 7
    assert record.pr_number == 101
    assert record.job_type == "review"


def test_cycle_result_is_reported_separately_from_queue_summary(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-020-5: the cycle result is its own log entry, independent of the
    queue-summary counts - a successful IMPLEMENT job reports
    result=IMPLEMENT regardless of how many other Issues are queued, and
    the cycle-result record itself carries no queue-count fields."""
    repo = _repo("myrepo")
    config = _config([repo])
    ready_issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    blocked_issue = _issue(repo.full_name, 2, labels=["devbot:blocked"])
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    github_client = FakeGitHubClient({repo.full_name: [ready_issue, blocked_issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    queue = next(r for r in caplog.records if getattr(r, "event", None) == "queue_summary")
    result = next(r for r in caplog.records if getattr(r, "event", None) == "cycle_result")
    assert queue.ready == 1
    assert queue.blocked == 1
    assert result.result == "IMPLEMENT"
    assert not hasattr(result, "ready")
    assert not hasattr(result, "blocked")


def test_debug_logging_preserves_candidate_details(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-6: DEBUG still carries per-candidate found/excluded detail
    even though INFO now only shows the queue summary/selected/cycle
    result triad."""
    repo = _repo("myrepo")
    config = _config([repo])
    ready_issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.DEBUG, logger="devbot"):
        service.run_once()

    assert any(getattr(r, "event", None) == "candidate_found" for r in caplog.records)
    assert any(getattr(r, "event", None) == "queue_summary" for r in caplog.records)

    with caplog.at_level(logging.INFO, logger="devbot"):
        caplog.clear()
        service.run_once()

    assert not any(getattr(r, "event", None) == "candidate_found" for r in caplog.records)
    assert any(getattr(r, "event", None) == "queue_summary" for r in caplog.records)


def test_queue_logging_preserves_structured_context(caplog: pytest.LogCaptureFixture) -> None:
    """CP-020-7: the new queue-summary/selected/cycle-result logs keep the
    Task 013 correlation fields (cycle_id, repository, Issue, job type,
    elapsed time) alongside their new content."""
    repo = _repo("myrepo")
    config = _config([repo])
    ready_issue = _issue(repo.full_name, 1, labels=["devbot:ready"])
    implementer_runner = MagicMock()
    implementer_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    github_client = FakeGitHubClient({repo.full_name: [ready_issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=implementer_runner,
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    queue = next(r for r in caplog.records if getattr(r, "event", None) == "queue_summary")
    selected = next(r for r in caplog.records if getattr(r, "event", None) == "job_selected")
    result = next(r for r in caplog.records if getattr(r, "event", None) == "cycle_result")

    assert isinstance(queue.cycle_id, str) and queue.cycle_id
    assert selected.cycle_id == queue.cycle_id
    assert selected.repository == repo.full_name
    assert selected.issue_number == 1
    assert selected.job_type == "implement"
    assert result.cycle_id == queue.cycle_id
    assert isinstance(result.elapsed_ms, float)


def test_polling_and_issue_state_use_same_multi_label_precedence() -> None:
    issue = _issue(
        "someone/myrepo",
        8,
        labels=["devbot:ready", "devbot:done"],
        title="Conflicting terminal label",
    )

    assert _task_state_from_labels(issue.labels) is TaskState.DONE
    assert _current_state(issue) is TaskState.DONE

def test_queue_summary_does_not_double_count_issue_state(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """CP-020-8: an Issue carrying more than one `devbot:*` state label
    (a malformed/stale manual edit) is still counted into exactly one
    queue-summary bucket - the same single-state resolution
    `devbot.polling.issue_to_task` already applies for scheduling, not a
    second independent rule per bucket."""
    repo = _repo("myrepo")
    config = _config([repo])
    conflicted_issue = _issue(
        repo.full_name, 9, labels=["devbot:review", "devbot:blocked"], title="Conflicting labels"
    )
    github_client = FakeGitHubClient({repo.full_name: [conflicted_issue]})
    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
    )

    with caplog.at_level(logging.DEBUG, logger="devbot"):
        service.run_once()

    record = next(r for r in caplog.records if getattr(r, "event", None) == "queue_summary")
    total = (
        record.ready
        + record.review
        + record.rework
        + record.blocked
        + record.manual_action
        + record.working
    )
    assert total == 1
    # Shared state-label precedence resolves the conflict to BLOCKED, matching
    # `devbot.issue_state` instead of `TaskState` declaration order.
    assert record.review == 0
    assert record.blocked == 1

    conflicts = [r for r in caplog.records if getattr(r, "event", None) == "state_label_conflict"]
    assert len(conflicts) == 1
    assert conflicts[0].repository == repo.full_name
    assert conflicts[0].issue_number == 9
    assert conflicts[0].resolved_state == "blocked"
    assert set(conflicts[0].matched_states) == {"review", "blocked"}


# --- Task 024: automatic Timeline recording -------------------------------


def test_daemon_records_ready_event_once(tmp_path: Path) -> None:
    """CP-024-1: the first daemon claim of a `devbot:ready` Issue records
    exactly one `ready` event - a retried claim (e.g. after a workspace
    preparation failure restores the Issue back to `devbot:ready`) must not
    append a second one."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 60, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    backend = _TimelineBackend(issue=issue)
    timeline = TimelineService(
        read_client=backend,
        write_client=backend,
        dry_run=False,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    def failing_prepare(
        repository: RepositoryConfig,
        issue_arg: GitHubIssue,
        linked_pull_request: PullRequest | None,
    ) -> PreparedWorkspace:
        raise WorkspacePreparationError(
            WorkspacePreparationFailure.WORKTREE_CREATION_FAILED, "boom"
        )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=MagicMock(),
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=failing_prepare,
        state_writer=state_writer,
        delivery=MagicMock(),
        timeline=timeline,
    )

    service.run_once()
    service.run_once()

    events = parse_events(backend.body)
    ready_events = [e for e in events if e.phase == "queue" and e.event == "ready"]
    assert len(ready_events) == 1
    assert backend.create_calls == 1


def test_implement_job_records_dev_start(tmp_path: Path) -> None:
    """CP-024-2: an IMPLEMENT Job records `dev:start` before the
    Implementer Agent runs."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 61, labels=["devbot:ready"])
    linked_pr = _pull_request(45, issue_number=61, head_ref="task/024-dev-start")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/024-dev-start", issue_number=61, pull_request=linked_pr
    )
    backend = _TimelineBackend(issue=issue)
    timeline = TimelineService(
        read_client=backend,
        write_client=backend,
        dry_run=False,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )

    call_order: list[str] = []
    real_create_comment = backend.create_comment
    real_update_comment = backend.update_comment

    def _tracked_create(*args: object, **kwargs: object) -> None:
        call_order.append("timeline_write")
        real_create_comment(*args, **kwargs)  # type: ignore[arg-type]

    def _tracked_update(*args: object, **kwargs: object) -> None:
        call_order.append("timeline_write")
        real_update_comment(*args, **kwargs)  # type: ignore[arg-type]

    backend.create_comment = _tracked_create  # type: ignore[method-assign]
    backend.update_comment = _tracked_update  # type: ignore[method-assign]

    agent_runner = MagicMock()

    def _run(repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        call_order.append("agent_run")
        return AgentRunResult(executed=True, dry_run=False, message="ok")

    agent_runner.run.side_effect = _run
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=45, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
        timeline=timeline,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    events = parse_events(backend.body)
    dev_start_events = [e for e in events if e.phase == "dev" and e.event == "start"]
    assert len(dev_start_events) == 1
    assert dev_start_events[0].actor == config.implementer_agent
    assert call_order.index("timeline_write") < call_order.index(
        "agent_run"
    ), "dev:start는 Implementer 실행 전에 기록되어야 한다"


def test_implement_job_records_dev_end(tmp_path: Path) -> None:
    """CP-024-3: a completed IMPLEMENT Job records `dev:end` with actor,
    cycle, timestamp, and a normalized result."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 62, labels=["devbot:ready"])
    linked_pr = _pull_request(46, issue_number=62, head_ref="task/024-dev-end")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/024-dev-end", issue_number=62, pull_request=linked_pr
    )
    backend = _TimelineBackend(issue=issue)
    fixed_now = datetime(2026, 1, 1, tzinfo=UTC)
    timeline = TimelineService(
        read_client=backend, write_client=backend, dry_run=False, clock=lambda: fixed_now
    )
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=46, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
        timeline=timeline,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    events = parse_events(backend.body)
    dev_end_events = [e for e in events if e.phase == "dev" and e.event == "end"]
    assert len(dev_end_events) == 1
    dev_end = dev_end_events[0]
    assert dev_end.actor == config.implementer_agent
    assert dev_end.cycle == 1
    assert dev_end.result == "pushed"
    assert dev_end.pr == 46
    assert dev_end.at == fixed_now


def test_timeline_uses_configured_agent_actor(tmp_path: Path) -> None:
    """CP-024-7: Timeline events use the configured Implementer Agent name
    as the actor, not a hardcoded product assumption."""
    repo = _operator_repo(tmp_path)
    config = _config([repo], implementer_agent="claude-custom")
    issue = _issue(repo.full_name, 63, labels=["devbot:ready"])
    linked_pr = _pull_request(47, issue_number=63, head_ref="task/024-actor")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/024-actor", issue_number=63, pull_request=linked_pr
    )
    backend = _TimelineBackend(issue=issue)
    timeline = TimelineService(
        read_client=backend,
        write_client=backend,
        dry_run=False,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=47, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
        timeline=timeline,
    )

    service.run_once()

    events = parse_events(backend.body)
    actors = {e.actor for e in events if e.actor is not None}
    assert actors == {"claude-custom"}


def test_failed_job_closes_open_timeline_phase(tmp_path: Path) -> None:
    """CP-024-9: an Agent execution failure closes the open `dev` phase
    with a normalized `blocked` result instead of leaving it open."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 64, labels=["devbot:ready"])
    github_client = FakeGitHubClient({repo.full_name: [issue]})
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    backend = _TimelineBackend(issue=issue)
    timeline = TimelineService(
        read_client=backend,
        write_client=backend,
        dry_run=False,
        clock=lambda: datetime(2026, 1, 1, tzinfo=UTC),
    )
    agent_runner = MagicMock()
    agent_runner.run.side_effect = RuntimeError("agent crashed")

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        state_writer=state_writer,
        delivery=MagicMock(),
        timeline=timeline,
    )

    result = service.run_once()

    assert result.status is PollingStatus.AGENT_FAILED
    events = parse_events(backend.body)
    dev_start_events = [e for e in events if e.phase == "dev" and e.event == "start"]
    dev_end_events = [e for e in events if e.phase == "dev" and e.event == "end"]
    assert len(dev_start_events) == 1
    assert len(dev_end_events) == 1
    assert dev_end_events[0].result == "blocked"


def test_timeline_write_failure_preserves_primary_job_outcome(tmp_path: Path) -> None:
    """CP-024-10: a GitHub Timeline write failure is diagnosed but never
    replaces or hides the primary Job outcome, and never leaves the Issue
    stuck in `devbot:working`."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 65, labels=["devbot:ready"])
    linked_pr = _pull_request(48, issue_number=65, head_ref="task/024-timeline-fail")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/024-timeline-fail", issue_number=65, pull_request=linked_pr
    )
    timeline = MagicMock(spec=TimelineService)
    timeline.ready.side_effect = RuntimeError("timeline boom")
    timeline.start.side_effect = RuntimeError("timeline boom")
    timeline.end.side_effect = RuntimeError("timeline boom")

    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=48, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
        timeline=timeline,
    )

    result = service.run_once()

    assert result.status is PollingStatus.DELIVERED
    labels_written = [call.args[2] for call in write_client.set_labels.call_args_list]
    assert labels_written[-1] == ["devbot:review"]


def test_existing_workflows_compatible_with_timeline_auto_recording(tmp_path: Path) -> None:
    """CP-024-12: omitting `timeline` (the default) reproduces pre-Task-024
    behavior exactly - queue selection, delivery, and the state machine are
    unaffected by automatic Timeline recording being wired in."""
    repo = _operator_repo(tmp_path)
    config = _config([repo])
    issue = _issue(repo.full_name, 66, labels=["devbot:ready"])
    linked_pr = _pull_request(49, issue_number=66, head_ref="task/024-compat")
    github_client = FakeGitHubClient(
        {repo.full_name: [issue]}, pull_requests_by_repo={repo.full_name: [linked_pr]}
    )
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    prepared = _prepared_workspace(
        repo, branch="task/024-compat", issue_number=66, pull_request=linked_pr
    )
    agent_runner = MagicMock()
    agent_runner.run.return_value = AgentRunResult(executed=True, dry_run=False, message="ok")
    delivery = MagicMock()
    delivery.deliver.return_value = DeliveryResult(
        verification=VerificationResult(passed=True),
        committed=True,
        pushed=True,
        pull_request=PullRequestInfo(number=49, html_url=linked_pr.html_url),
        dry_run=False,
        message="delivered",
    )

    service = PollingService(
        config=config,
        github_client=github_client,
        implementer_runner=agent_runner,
        ensure_workspace_ready=_no_op_workspace_check,
        prepare_workspace=lambda repository, issue_arg, linked: prepared,
        state_writer=state_writer,
        delivery=delivery,
        # `timeline` intentionally omitted - must default to `None`.
    )

    result = service.run_once()

    assert service.timeline is None
    assert result.status is PollingStatus.DELIVERED


def test_implement_job_emits_lifecycle_stage_logs(caplog: pytest.LogCaptureFixture) -> None:
    repo = _repo("myrepo")
    config = _config([repo])
    issue = _issue(repo.full_name, 130, labels=["devbot:ready"], title="Lifecycle logging")
    linked_pr = _pull_request(130, issue_number=130, head_ref="task/130-lifecycle")
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
        pull_request=PullRequestInfo(number=130, html_url=linked_pr.html_url),
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

    with caplog.at_level(logging.INFO, logger="devbot"):
        service.run_once()

    started = [
        getattr(record, "stage", None)
        for record in caplog.records
        if getattr(record, "event", None) == "lifecycle_stage_started"
        and getattr(record, "issue_number", None) == 130
    ]
    finished = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "lifecycle_stage_finished"
        and getattr(record, "issue_number", None) == 130
    ]
    assert {"bootstrap", "implement", "verify", "delivery"}.issubset(set(started))
    finished_by_stage = {record.stage: record for record in finished}
    for stage in ("bootstrap", "implement", "verify", "delivery"):
        assert stage in finished_by_stage
        assert finished_by_stage[stage].status == "completed"
        assert finished_by_stage[stage].elapsed_ms >= 0
