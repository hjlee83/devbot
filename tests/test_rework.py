from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from devbot.delivery import VerificationResult
from devbot.github_client import GitHubIssue, PullRequestComment
from devbot.github_write_client import GitHubWriteClient
from devbot.issue_state import IssueStateWriter
from devbot.models import JobType, RepositoryConfig, TaskState
from devbot.rework import ReworkActionScope, ReworkService, classify_rework_action_scope
from devbot.timeline import COMMENT_MARKER, TimelineService, parse_events

BRANCH = "devbot/myrepo-42-existing-branch"


def _repo() -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone", repo="myrepo", enabled=True, local_path=Path("/tmp/workspace/myrepo")
    )


def _issue(*, labels: tuple[str, ...] = ("devbot:rework",)) -> GitHubIssue:
    return GitHubIssue(
        repository="someone/myrepo",
        number=42,
        title="Add feature X",
        body="issue body",
        state="open",
        labels=labels,
        created_at=datetime(2026, 1, 1),
    )


def _comment(
    *, comment_id: int = 1, body: str = "@devbot please fix the typo", reactions: dict | None = None
) -> PullRequestComment:
    return PullRequestComment(
        id=comment_id,
        author="someone",
        body=body,
        created_at=datetime(2026, 1, 1),
        reactions=reactions or {},
    )


def _service(
    *,
    state_writer: MagicMock | None = None,
    write_client: MagicMock | None = None,
    apply_changes: MagicMock | None = None,
    run_verification=None,
    commit: MagicMock | None = None,
    push: MagicMock | None = None,
    current_branch=None,
    has_changes=None,
    timeline: TimelineService | None = None,
    actor: str | None = None,
) -> tuple[ReworkService, IssueStateWriter | MagicMock, MagicMock]:
    state_writer = state_writer or MagicMock(spec=IssueStateWriter)
    if isinstance(state_writer, MagicMock):
        state_writer.claim.return_value = _issue(labels=("devbot:working",))
    write_client = write_client or MagicMock(spec=GitHubWriteClient)
    service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=apply_changes or MagicMock(),
        run_verification=run_verification or (lambda repository: VerificationResult(passed=True)),
        commit=commit or MagicMock(),
        push=push or MagicMock(),
        current_branch=current_branch or (lambda repository: BRANCH),
        has_changes=has_changes or (lambda repository: True),
        timeline=timeline,
        actor=actor,
    )
    return service, state_writer, write_client


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


def test_rework_classifies_repository_change_comment() -> None:
    assert (
        classify_rework_action_scope("@devbot update tests and result file")
        is ReworkActionScope.REPOSITORY_CHANGE
    )


def test_rework_classifies_metadata_only_comment() -> None:
    assert (
        classify_rework_action_scope("@devbot update the PR Evidence and PR body")
        is ReworkActionScope.METADATA_ONLY
    )


def test_rework_classifies_external_verification_comment() -> None:
    assert (
        classify_rework_action_scope("@devbot verify CI and rerun dry-run with network access")
        is ReworkActionScope.EXTERNAL_VERIFICATION
    )


def test_only_unprocessed_devbot_comments_trigger_rework() -> None:
    service, state_writer, _ = _service()
    comments = [
        _comment(comment_id=1, body="looks good, thanks"),
        _comment(comment_id=2, body="@devbot please rename this", reactions={"eyes": 1}),
        _comment(comment_id=3, body="@devbot please also fix the typo"),
    ]

    result = service.process(_repo(), _issue(), BRANCH, comments)

    assert result.triggered is True
    assert result.comment is not None
    assert result.comment.id == 3
    state_writer.claim.assert_called_once()


def test_no_unprocessed_devbot_comments_does_not_trigger_rework() -> None:
    service, state_writer, write_client = _service()
    comments = [
        _comment(comment_id=1, body="looks good, thanks"),
        _comment(comment_id=2, body="@devbot please rename this", reactions={"eyes": 1}),
    ]

    result = service.process(_repo(), _issue(), BRANCH, comments)

    assert result.triggered is False
    assert result.comment is None
    assert result.pr_reused is False
    assert result.issue_state == TaskState.REWORK
    state_writer.claim.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()


def test_processed_feedback_is_not_reworked_twice() -> None:
    """CP-014-9: a comment already marked processed (an `eyes` reaction)
    must not trigger another rework run, even if it is the only `@devbot`
    comment present - the same guarantee `find_unprocessed_devbot_comments`
    has always provided, re-verified against the new `devbot:rework`
    state's `process()` entry point."""
    service, state_writer, write_client = _service()
    already_processed = _comment(comment_id=9, reactions={"eyes": 1})

    first = service.process(_repo(), _issue(), BRANCH, [already_processed])
    second = service.process(_repo(), _issue(), BRANCH, [already_processed])

    assert first.triggered is False
    assert second.triggered is False
    assert first.issue_state == TaskState.REWORK
    assert second.issue_state == TaskState.REWORK
    state_writer.claim.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()


def test_rework_moves_review_to_working() -> None:
    call_order: list[str] = []

    def _apply_changes(repository, issue, comment) -> None:
        call_order.append("apply_changes")
        assert issue.labels == ("devbot:working",)

    state_writer = MagicMock(spec=IssueStateWriter)

    def _claim(repository, issue, *, job_type=None):
        call_order.append("claim")
        return _issue(labels=("devbot:working",))

    state_writer.claim.side_effect = _claim

    service, _, _ = _service(state_writer=state_writer, apply_changes=_apply_changes)
    repository = _repo()
    issue = _issue()

    service.process(repository, issue, BRANCH, [_comment()])

    assert call_order == ["claim", "apply_changes"]
    state_writer.claim.assert_called_once_with(repository, issue, job_type=JobType.REWORK)


def test_rework_reuses_existing_branch_and_pr() -> None:
    push = MagicMock()
    service, _, write_client = _service(push=push)
    repository = _repo()

    service.process(repository, _issue(), BRANCH, [_comment()])

    push.assert_called_once_with(repository, BRANCH)
    write_client.create_pull_request.assert_not_called()


def test_successful_rework_reuses_pr_and_returns_issue_to_review() -> None:
    """CP-014-4: a successful rework reuses the existing branch/PR (no new
    PR is ever opened) and returns the Issue to `devbot:review`."""
    push = MagicMock()
    commit = MagicMock()
    service, state_writer, write_client = _service(push=push, commit=commit)
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    commit.assert_called_once()
    push.assert_called_once_with(repository, BRANCH)
    write_client.create_pull_request.assert_not_called()
    state_writer.mark_for_review.assert_called_once()
    assert result.pr_reused is True
    assert result.issue_state == TaskState.REVIEW


def test_clean_rework_does_not_commit() -> None:
    commit = MagicMock()
    push = MagicMock()
    service, state_writer, write_client = _service(
        commit=commit,
        push=push,
        has_changes=lambda repository: False,
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    commit.assert_not_called()
    push.assert_not_called()
    write_client.add_reaction_to_comment.assert_called_once_with(repository, 1, content="eyes")
    state_writer.mark_for_review.assert_called_once()
    state_writer.block.assert_not_called()
    assert result.message == "no_repository_changes"
    assert result.action_scope is ReworkActionScope.REPOSITORY_CHANGE
    assert result.committed is False
    assert result.pushed is False
    assert result.issue_state is TaskState.REVIEW


def test_metadata_only_rework_does_not_block_queue() -> None:
    commit = MagicMock()
    push = MagicMock()
    service, state_writer, write_client = _service(commit=commit, push=push)

    result = service.process(
        _repo(), _issue(), BRANCH, [_comment(body="@devbot update PR Evidence only")]
    )

    commit.assert_not_called()
    push.assert_not_called()
    state_writer.require_manual_action.assert_called_once()
    state_writer.block.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    assert result.action_scope is ReworkActionScope.METADATA_ONLY
    assert result.issue_state is TaskState.MANUAL_ACTION


def test_repository_change_rework_still_commits_and_returns_review() -> None:
    commit = MagicMock()
    push = MagicMock()
    service, state_writer, _ = _service(commit=commit, push=push)
    repository = _repo()

    result = service.process(
        repository, _issue(), BRANCH, [_comment(body="@devbot update tests for this bug")]
    )

    commit.assert_called_once()
    push.assert_called_once_with(repository, BRANCH)
    state_writer.mark_for_review.assert_called_once()
    assert result.action_scope is ReworkActionScope.REPOSITORY_CHANGE
    assert result.committed is True
    assert result.pushed is True
    assert result.issue_state is TaskState.REVIEW


def test_no_repository_changes_is_logged() -> None:
    service, _, _ = _service(has_changes=lambda repository: False)

    result = service.process(_repo(), _issue(), BRANCH, [_comment()])

    assert result.message == "no_repository_changes"
    assert result.action_scope is ReworkActionScope.REPOSITORY_CHANGE


def test_rework_dry_run_does_not_push_or_mark_processed() -> None:
    commit = MagicMock()
    push = MagicMock()
    service, state_writer, write_client = _service(commit=commit, push=push)
    service.dry_run = True
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    commit.assert_not_called()
    push.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    assert result.triggered is True
    assert result.message.startswith("[dry-run]")
    assert result.code_changed is True
    assert result.verification_passed is True
    assert result.committed is False
    assert result.pushed is False
    assert result.pr_reused is True
    assert result.issue_state == TaskState.WORKING


def test_successful_rework_marks_comment_processed() -> None:
    service, _, write_client = _service()
    repository = _repo()
    comment = _comment(comment_id=7)

    service.process(repository, _issue(), BRANCH, [comment])

    write_client.add_reaction_to_comment.assert_called_once_with(repository, 7, content="eyes")


def test_failed_rework_moves_to_blocked() -> None:
    commit = MagicMock()
    push = MagicMock()
    failing_verification = VerificationResult(
        passed=False, failed_command=("uv", "run", "pytest"), output="1 failed"
    )
    service, state_writer, write_client = _service(
        run_verification=lambda repository: failing_verification, commit=commit, push=push
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    state_writer.block.assert_called_once()
    args, kwargs = state_writer.block.call_args
    assert args[0] is repository
    assert args[1].labels == ("devbot:working",)
    assert "pytest" in args[2]
    assert kwargs["job_type"] is JobType.REWORK
    commit.assert_not_called()
    push.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    assert result.message == "blocked"
    assert result.verification is failing_verification
    assert result.code_changed is True
    assert result.verification_passed is False
    assert result.committed is False
    assert result.pushed is False
    assert result.pr_reused is True
    assert result.issue_state == TaskState.BLOCKED


def test_execution_failure_moves_issue_to_blocked_with_reason() -> None:
    """CP-014-6: a verification failure during rework blocks the Issue and
    records the reason (both in the state-write call and in the posted
    comment `IssueStateWriter.block()` sends)."""
    write_client = MagicMock(spec=GitHubWriteClient)
    state_writer = IssueStateWriter(client=write_client, dry_run=False)
    failing_verification = VerificationResult(
        passed=False, failed_command=("uv", "run", "pytest"), output="AssertionError: boom"
    )
    service, _, _ = _service(
        state_writer=state_writer,
        write_client=write_client,
        run_verification=lambda repository: failing_verification,
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    assert result.issue_state == TaskState.BLOCKED
    assert write_client.set_labels.call_args_list[-1].args == (
        repository,
        _issue().number,
        ["devbot:blocked"],
    )
    posted_reason = write_client.create_comment.call_args.args[2]
    assert "AssertionError: boom" in posted_reason


def test_successful_rework_returns_to_review() -> None:
    service, state_writer, _ = _service()
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    state_writer.mark_for_review.assert_called_once()
    args, kwargs = state_writer.mark_for_review.call_args
    assert args[0] is repository
    assert args[1].labels == ("devbot:working",)
    assert kwargs["job_type"] is JobType.REWORK
    assert result.message == "reworked"
    assert result.code_changed is True
    assert result.verification_passed is True
    assert result.committed is True
    assert result.pushed is True
    assert result.pr_reused is True
    assert result.issue_state == TaskState.REVIEW


def test_rework_blocks_when_local_branch_does_not_match_existing_pr_head() -> None:
    """CP-010-branch-guard: if the workspace isn't actually checked out on
    the branch the existing PR uses, rework must not run the Agent, commit,
    or push - it must block instead of silently acting on the wrong branch."""
    apply_changes = MagicMock()
    commit = MagicMock()
    push = MagicMock()
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, write_client = _service(
        state_writer=state_writer,
        apply_changes=apply_changes,
        commit=commit,
        push=push,
        current_branch=lambda repository: "some-other-local-branch",
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    apply_changes.assert_not_called()
    commit.assert_not_called()
    push.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    state_writer.block.assert_called_once()
    args, _ = state_writer.block.call_args
    assert args[0] is repository
    assert args[1].labels == ("devbot:working",)
    assert BRANCH in args[2]
    assert "some-other-local-branch" in args[2]
    assert result.triggered is True
    assert result.message == "blocked: branch mismatch"
    assert result.code_changed is False
    assert result.verification_passed is False
    assert result.committed is False
    assert result.pushed is False
    assert result.pr_reused is False
    assert result.issue_state == TaskState.BLOCKED


def test_rework_blocks_when_current_branch_lookup_raises() -> None:
    """CP-014-7: an unexpected exception looking up the current branch
    (before any Agent runs) must not leave the Issue stuck in `working`."""

    def _raise(repository):
        raise RuntimeError("git rev-parse crashed")

    apply_changes = MagicMock()
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, _ = _service(
        state_writer=state_writer, apply_changes=apply_changes, current_branch=_raise
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    apply_changes.assert_not_called()
    state_writer.block.assert_called_once()
    assert "git rev-parse crashed" in state_writer.block.call_args.args[2]
    assert result.issue_state == TaskState.BLOCKED


def test_rework_blocks_when_agent_raises_exception() -> None:
    """CP-010-agent-guard: an Agent exception during rework must not
    crash the polling loop - it must be recorded as a blocked reason."""

    def _raise(repository, issue, comment) -> None:
        raise RuntimeError("agent process crashed")

    commit = MagicMock()
    push = MagicMock()
    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, write_client = _service(
        state_writer=state_writer, apply_changes=_raise, commit=commit, push=push
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    commit.assert_not_called()
    push.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    state_writer.mark_for_review.assert_not_called()
    state_writer.block.assert_called_once()
    args, _ = state_writer.block.call_args
    assert args[0] is repository
    assert args[1].labels == ("devbot:working",)
    assert "agent process crashed" in args[2]
    assert result.triggered is True
    assert result.message == "blocked: agent execution failed"
    assert result.code_changed is False
    assert result.verification_passed is False
    assert result.pr_reused is True
    assert result.issue_state == TaskState.BLOCKED


def test_rework_blocks_when_agent_raises_keyboard_interrupt() -> None:
    """CP-010-agent-guard: a KeyboardInterrupt during Agent execution is
    treated the same as any other Agent failure - blocked, with the reason
    recorded - rather than crashing the whole polling process."""

    def _interrupt(repository, issue, comment) -> None:
        raise KeyboardInterrupt()

    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, _ = _service(state_writer=state_writer, apply_changes=_interrupt)
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    state_writer.block.assert_called_once()
    assert result.message == "blocked: agent execution failed"
    assert result.issue_state == TaskState.BLOCKED


def test_rework_blocks_when_verification_raises() -> None:
    """CP-014-7: an unexpected exception while running verification (not
    a normal `VerificationResult(passed=False)`) must not leave the Issue
    stuck in `working`."""

    def _raise(repository):
        raise OSError("uv binary not found")

    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, _ = _service(state_writer=state_writer, run_verification=_raise)
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    state_writer.block.assert_called_once()
    assert "uv binary not found" in state_writer.block.call_args.args[2]
    assert result.issue_state == TaskState.BLOCKED


def test_rework_blocks_when_push_raises() -> None:
    """CP-014-7: an unexpected exception during commit/push/reaction
    (after verification passed) must not leave the Issue stuck in
    `working`."""

    def _raise(repository, branch):
        raise RuntimeError("push rejected: non-fast-forward")

    state_writer = MagicMock(spec=IssueStateWriter)
    state_writer.claim.return_value = _issue(labels=("devbot:working",))
    service, _, write_client = _service(state_writer=state_writer, push=_raise)
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    state_writer.block.assert_called_once()
    assert "non-fast-forward" in state_writer.block.call_args.args[2]
    state_writer.mark_for_review.assert_not_called()
    write_client.add_reaction_to_comment.assert_not_called()
    assert result.issue_state == TaskState.BLOCKED


def test_rework_with_real_dry_run_state_writer_completes_full_cycle() -> None:
    """Regression test: `IssueStateWriter`'s default `dry_run=True` must
    still return a would-be-updated `GitHubIssue` from `claim()`, or the
    chained `mark_for_review()` call below sees the stale `rework` label
    and rejects the transition."""
    state_writer = IssueStateWriter(client=MagicMock(spec=GitHubWriteClient))
    write_client = MagicMock(spec=GitHubWriteClient)
    service = ReworkService(
        state_writer=state_writer,
        write_client=write_client,
        apply_changes=MagicMock(),
        run_verification=lambda repository: VerificationResult(passed=True),
        commit=MagicMock(),
        push=MagicMock(),
        current_branch=lambda repository: BRANCH,
        has_changes=lambda repository: True,
    )
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    assert result.message == "reworked"


# --- Task 024: automatic Timeline recording -------------------------------


_CYCLE_1_REQUEST_CHANGES = "\n".join(
    [
        "<!-- devbot-timeline:v1 issue=42 pr=- cycle=1 phase=queue event=ready "
        "result=- at=2026-01-01T00:00:00Z -->",
        "<!-- devbot-timeline:v1 issue=42 pr=- cycle=1 phase=dev event=start "
        "result=- actor=claude at=2026-01-01T00:01:00Z -->",
        "<!-- devbot-timeline:v1 issue=42 pr=16 cycle=1 phase=dev event=end "
        "result=pushed actor=claude at=2026-01-01T00:10:00Z -->",
        "<!-- devbot-timeline:v1 issue=42 pr=16 cycle=1 phase=review event=start "
        "result=- actor=codex at=2026-01-01T00:11:00Z -->",
        "<!-- devbot-timeline:v1 issue=42 pr=16 cycle=1 phase=review event=end "
        "result=request-changes actor=codex at=2026-01-01T00:20:00Z -->",
    ]
)


def test_rework_job_records_next_dev_cycle() -> None:
    """CP-024-4: a REWORK Job's `dev:start`/`dev:end` land on the *existing*
    Timeline comment as cycle 2 - the cycle number a fresh `REQUEST CHANGES`
    review:end (cycle 1) already implies, never overwriting cycle 1's
    history."""
    backend = _TimelineBackend(
        issue=_issue(labels=("devbot:rework",)),
        seed_body=f"{COMMENT_MARKER}\n{_CYCLE_1_REQUEST_CHANGES}\n",
    )
    timeline = TimelineService(
        read_client=backend,
        write_client=backend,
        dry_run=False,
        clock=lambda: datetime(2026, 1, 1, 0, 25, tzinfo=UTC),
    )
    service, _, _ = _service(timeline=timeline, actor="claude")
    repository = _repo()

    result = service.process(repository, _issue(labels=("devbot:rework",)), BRANCH, [_comment()])

    assert result.message == "reworked"
    events = parse_events(backend.body)
    dev_starts = [e for e in events if e.phase == "dev" and e.event == "start"]
    dev_ends = [e for e in events if e.phase == "dev" and e.event == "end"]
    assert len(dev_starts) == 2, "cycle 1의 dev:start를 덮어쓰지 않고 새 marker를 추가해야 한다"
    assert dev_starts[-1].cycle == 2
    assert dev_starts[-1].actor == "claude"
    assert dev_ends[-1].cycle == 2
    assert dev_ends[-1].result == "pushed"


def test_rework_timeline_write_failure_preserves_primary_outcome() -> None:
    """CP-024-10 (rework side): a Timeline write failure never blocks or
    otherwise changes the rework's own GitHub outcome."""
    timeline = MagicMock(spec=TimelineService)
    timeline.start.side_effect = RuntimeError("timeline boom")
    timeline.end.side_effect = RuntimeError("timeline boom")
    service, state_writer, _ = _service(timeline=timeline, actor="claude")
    repository = _repo()

    result = service.process(repository, _issue(), BRANCH, [_comment()])

    assert result.message == "reworked"
    assert result.issue_state == TaskState.REVIEW
    state_writer.mark_for_review.assert_called_once()
