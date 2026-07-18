from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

from devbot.automerge import (
    AutomergeDecision,
    AutomergeService,
    check_runs_are_green,
    summarize_check_runs,
)
from devbot.github_client import GitHubIssue, PullRequest
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


def test_check_runs_must_exist_and_be_completed_green() -> None:
    assert check_runs_are_green(()) == (False, "GitHub check-runs가 없습니다")

    checks = summarize_check_runs(
        [
            {"name": "ruff", "status": "completed", "conclusion": "success"},
            {"name": "pytest", "status": "completed", "conclusion": "neutral"},
        ]
    )

    assert check_runs_are_green(checks) == (True, "모든 check-run이 초록입니다")


def test_automerge_blocks_when_kill_switch_is_off() -> None:
    repo = _repo()
    write_client = MagicMock()
    state_writer = MagicMock()
    service = AutomergeService(
        config=_config(repo, automerge_enabled=False),
        write_client=write_client,
        state_writer=state_writer,
        list_check_runs_for_ref=lambda _repo, _sha: [
            {"name": "pytest", "status": "completed", "conclusion": "success"}
        ],
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
        list_check_runs_for_ref=lambda _repo, _sha: [
            {"name": "pytest", "status": "completed", "conclusion": "success"}
        ],
        create_comment=create_comment,
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
        list_check_runs_for_ref=lambda _repo, _sha: [
            {"name": "pytest", "status": "completed", "conclusion": "success"}
        ],
    )

    result = service.process(repo, _issue(repo), _pull_request())

    assert result.decision is AutomergeDecision.BLOCKED
    assert "자기수정 저장소" in result.message
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
        list_check_runs_for_ref=lambda _repo, sha: [
            {"name": "pytest", "status": "completed", "conclusion": "success", "head_sha": sha}
        ],
    )

    result = service.process(repo, issue, pull_request)

    assert result.decision is AutomergeDecision.MERGED
    write_client.merge_pull_request.assert_called_once()
    state_writer.mark_done.assert_called_once_with(
        repo, issue, reason="PR #9 자동 머지 완료: merge-sha"
    )
