from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from devbot.automerge import AutomergeDecision, AutomergeService
from devbot.github_client import (
    CombinedCommitStatus,
    GitHubAPIError,
    GitHubAuthenticationError,
    GitHubIssue,
    PullRequest,
    WorkflowRun,
)
from devbot.github_write_client import MergePullRequestResult
from devbot.models import DevBotConfig, RepositoryConfig


def _repo(
    *,
    automerge_allowed: bool = True,
    is_self_repo: bool = False,
) -> RepositoryConfig:
    return RepositoryConfig(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/workspace/myrepo"),
        automerge_allowed=automerge_allowed,
        is_self_repo=is_self_repo,
    )


def _config(
    repo: RepositoryConfig, *, automerge_enabled: bool = True, dry_run: bool = False
) -> DevBotConfig:
    return DevBotConfig(
        workspace_root=Path("/tmp/workspace"),
        poll_interval_seconds=60,
        lock_file=Path("/tmp/devbot.lock"),
        default_agent="codex",
        implementer_agent="codex",
        reviewer_agent="codex",
        max_concurrent_jobs=1,
        dry_run=dry_run,
        github_token="token",
        repositories=(repo,),
        automerge_enabled=automerge_enabled,
    )


def _issue(repo: RepositoryConfig) -> GitHubIssue:
    return GitHubIssue(
        repository=repo.full_name,
        number=42,
        title="Task 042",
        body="Pull Request: #9",
        state="open",
        labels=("devbot:review",),
        created_at=datetime(2026, 1, 1),
    )


def _pull_request(*, labels: tuple[str, ...] = ("devbot:ready-to-merge",)) -> PullRequest:
    return PullRequest(
        number=9,
        head_ref="task/042-feature",
        head_sha="abc123",
        body="Closes #42",
        html_url="https://github.com/someone/myrepo/pull/9",
        labels=labels,
    )


def _workflow_run(
    head_sha: str, *, status: str = "completed", conclusion: str | None = "success"
) -> WorkflowRun:
    return WorkflowRun(
        id=1,
        name="ci",
        status=status,
        conclusion=conclusion,
        html_url="https://github.com/someone/myrepo/actions/runs/1",
        created_at=datetime(2026, 1, 1),
        head_sha=head_sha,
        event="pull_request",
    )


def _no_combined_status(_repo: RepositoryConfig, _ref: str) -> CombinedCommitStatus:
    return CombinedCommitStatus(state="pending", total_count=0)


def _no_check_runs(_repo: RepositoryConfig, _sha: str) -> list[dict[str, object]]:
    return []


def _green_ci_kwargs() -> dict[str, object]:
    """Every source consulted, only `workflow_runs` reports data - the
    minimal combination that yields an overall GREEN verdict."""
    return {
        "list_workflow_runs_for_ref": lambda _repo, sha: [_workflow_run(sha)],
        "get_combined_status_for_ref": _no_combined_status,
        "list_check_runs_for_ref": _no_check_runs,
    }


def test_automerge_blocks_when_kill_switch_is_off() -> None:
    repo = _repo()
    write_client = MagicMock()
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=False),
        write_client=write_client,
        state_writer=state_writer,
        **_green_ci_kwargs(),
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "킬스위치" in result.message
    write_client.merge_pull_request.assert_not_called()
    state_writer.mark_done.assert_not_called()


def test_automerge_block_comment_is_not_repeated_for_same_head_and_reason() -> None:
    repo = _repo()
    write_client = MagicMock()
    state_writer = MagicMock()
    create_comment = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=False),
        write_client=write_client,
        state_writer=state_writer,
        create_comment=create_comment,
        **_green_ci_kwargs(),
    )

    service.process(repo, _issue(repo), _pull_request())
    service.process(repo, _issue(repo), _pull_request())

    create_comment.assert_called_once()
    write_client.merge_pull_request.assert_not_called()


def test_automerge_blocks_self_repo_even_when_enabled_and_allowlisted() -> None:
    repo = _repo(is_self_repo=True)
    write_client = MagicMock()
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=state_writer,
        **_green_ci_kwargs(),
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "자기수정 저장소" in result.message
    write_client.merge_pull_request.assert_not_called()


# --------------------------------------------------------------------------
# Issue #124: when *every* CI source fails/is unavailable (403 permission
# gap, 404, transient API error, ...), the gate must fail closed - blocked,
# not merged - and never let the exception escape `AutomergeService.process()`
# uncaught. The previous behavior let this propagate all the way out of
# `PollingService.run_cycle()`, which crashes `devbot --once` (unlike
# `run_forever()`, which happens to catch it one level up).
# --------------------------------------------------------------------------


def _raise_github_error(_repo: RepositoryConfig, _ref: str):
    raise GitHubAuthenticationError("GitHub authentication failed: 403 Forbidden")


def test_automerge_fails_closed_when_every_ci_source_is_unavailable() -> None:
    repo = _repo()
    write_client = MagicMock()
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=state_writer,
        list_workflow_runs_for_ref=_raise_github_error,
        get_combined_status_for_ref=_raise_github_error,
        list_check_runs_for_ref=_raise_github_error,
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "CI gate 확인 불가" in result.message
    write_client.merge_pull_request.assert_not_called()
    state_writer.mark_done.assert_not_called()


def test_automerge_reports_blocked_comment_when_every_ci_source_is_unavailable() -> None:
    repo = _repo()
    write_client = MagicMock()
    state_writer = MagicMock()
    create_comment = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True, dry_run=False),
        write_client=write_client,
        state_writer=state_writer,
        list_workflow_runs_for_ref=_raise_github_error,
        get_combined_status_for_ref=_raise_github_error,
        list_check_runs_for_ref=_raise_github_error,
        create_comment=create_comment,
    )

    service.process(repo, _issue(repo), _pull_request())

    create_comment.assert_called_once()
    posted_body = create_comment.call_args.args[2]
    assert "devbot:ready-to-merge" in posted_body


def test_automerge_fails_closed_for_generic_github_api_error_too() -> None:
    """Not just the 403 case - any `GitHubClientError` subtype must be
    caught, since the underlying cause (network hiccup, rate limit, 5xx)
    can vary."""
    repo = _repo()

    def _raise_api_error(_repo: RepositoryConfig, _ref: str):
        raise GitHubAPIError("GitHub API error 500: internal error")

    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=MagicMock(),
        state_writer=MagicMock(),
        list_workflow_runs_for_ref=_raise_api_error,
        get_combined_status_for_ref=_raise_api_error,
        list_check_runs_for_ref=_raise_api_error,
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED


# --------------------------------------------------------------------------
# Issue #127: a fine-grained PAT frequently cannot be granted the "Checks"
# repository permission, so `list_check_runs_for_ref` alone 403s. That must
# no longer block automerge by itself when another source (Actions workflow
# runs, the combined Statuses API) can still confirm CI status.
# --------------------------------------------------------------------------


def test_automerge_proceeds_when_check_runs_403s_but_workflow_runs_are_green() -> None:
    repo = _repo()
    issue = _issue(repo)
    pull_request = _pull_request()
    write_client = MagicMock()
    write_client.merge_pull_request.return_value = MergePullRequestResult(
        sha="merge-sha", merged=True, message="merged"
    )
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=state_writer,
        list_workflow_runs_for_ref=lambda _repo, sha: [_workflow_run(sha)],
        get_combined_status_for_ref=_no_combined_status,
        list_check_runs_for_ref=_raise_github_error,
    )

    result = service.process(repo, issue, pull_request)

    assert result.decision is AutomergeDecision.MERGED
    write_client.merge_pull_request.assert_called_once()


def test_automerge_blocks_when_a_ci_source_reports_failure() -> None:
    """A known failure from one source must not be masked by another
    source being green or unavailable."""
    repo = _repo()
    write_client = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=MagicMock(),
        list_workflow_runs_for_ref=lambda _repo, sha: [
            _workflow_run(sha, conclusion="failure")
        ],
        get_combined_status_for_ref=_no_combined_status,
        list_check_runs_for_ref=_raise_github_error,
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "CI gate 실패" in result.message
    write_client.merge_pull_request.assert_not_called()


def test_automerge_blocks_while_ci_is_still_pending() -> None:
    repo = _repo()
    write_client = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=MagicMock(),
        list_workflow_runs_for_ref=lambda _repo, sha: [
            _workflow_run(sha, status="in_progress", conclusion=None)
        ],
        get_combined_status_for_ref=_no_combined_status,
        list_check_runs_for_ref=_no_check_runs,
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "CI gate 실패" in result.message
    write_client.merge_pull_request.assert_not_called()


def test_automerge_merges_and_marks_issue_done_when_all_gates_pass() -> None:
    repo = _repo()
    issue = _issue(repo)
    pull_request = _pull_request()
    write_client = MagicMock()
    write_client.merge_pull_request.return_value = MergePullRequestResult(
        sha="merge-sha", merged=True, message="merged"
    )
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=True),
        write_client=write_client,
        state_writer=state_writer,
        **_green_ci_kwargs(),
    )

    result = service.process(repo, issue, pull_request)

    assert result.decision is AutomergeDecision.MERGED
    write_client.merge_pull_request.assert_called_once_with(
        repo,
        9,
        expected_head_sha="abc123",
        commit_title="Merge PR #9",
        commit_message="Merged automatically by DevBot after MERGE READY and green CI.",
        merge_method="merge",
    )
    state_writer.mark_done.assert_called_once_with(
        repo, issue, reason="PR #9 자동 머지 완료: merge-sha"
    )
