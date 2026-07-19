from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from devbot.github_client import (
    GitHubAuthenticationError,
    GitHubRelease,
    GitHubReleaseAsset,
    WorkflowRun,
)
from devbot.models import RepositoryConfig
from devbot.release import PullRequestMetadata, ReleaseRecord
from devbot.release_ops import (
    CI_WORKFLOW_FILE,
    ReleaseContext,
    ReleaseOpsError,
    ReleasePreview,
    ReleaseReadiness,
    build_release_preview,
    build_release_status,
    dispatch_release,
    fetch_release_preview,
    gather_release_context,
    local_checkout_is_dirty,
    publish_release,
    target_commit_is_ci_validated,
    validate_published_release,
    wait_for_dispatched_run,
)
from devbot.release_publish_strategy import ReleasePublishStrategyMismatchError


def _repository(**overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=Path("/tmp/nonexistent-devbot-repo"),
        default_branch="main",
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _pr(number: int, title: str, label: str, merge_commit_sha: str) -> PullRequestMetadata:
    return PullRequestMetadata(
        number=number,
        title=title,
        labels=(label,),
        merged=True,
        base_ref="main",
        merge_commit_sha=merge_commit_sha,
    )


def _context(**overrides: object) -> ReleaseContext:
    defaults: dict[str, object] = dict(
        target_commit="ccc",
        target_commit_validated=True,
        release_records=(
            ReleaseRecord(tag_name="v0.1.0", target_commitish="aaa", draft=False, prerelease=False),
        ),
        main_commits=frozenset({"aaa", "bbb", "ccc"}),
        initial_version="0.1.0",
        commit_range_prs=(_pr(80, "Task 037: Release Operator UX", "release:minor", "bbb"),),
    )
    defaults.update(overrides)
    return ReleaseContext(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# build_release_preview (pure)
# --------------------------------------------------------------------------


def test_build_release_preview_ready_when_all_checks_pass() -> None:
    preview = build_release_preview(_context())

    assert preview.readiness.ready is True
    assert preview.readiness.blockers == ()
    assert preview.previous_version == "0.1.0"
    assert preview.next_version == "0.2.0"
    assert preview.increment == "minor"
    assert "devbot-0.2.0-linux-x86_64.tar.gz" in preview.expected_assets
    assert "SHA256SUMS" in preview.expected_assets
    assert "# 한국어" in preview.notes
    assert "# English" in preview.notes


def test_build_release_preview_blocks_on_dirty_checkout() -> None:
    preview = build_release_preview(_context(), local_dirty=True)

    assert preview.readiness.ready is False
    assert any("uncommitted changes" in blocker for blocker in preview.readiness.blockers)


def test_build_release_preview_blocks_on_unvalidated_ci() -> None:
    preview = build_release_preview(_context(target_commit_validated=False))

    assert preview.readiness.ready is False
    assert any("not CI-validated" in blocker for blocker in preview.readiness.blockers)


def test_build_release_preview_blocks_when_release_already_exists_for_target() -> None:
    context = _context(
        release_records=(
            ReleaseRecord(tag_name="v0.1.0", target_commitish="aaa", draft=False, prerelease=False),
            ReleaseRecord(tag_name="v0.2.0", target_commitish="ccc", draft=False, prerelease=False),
        )
    )

    preview = build_release_preview(context)

    assert preview.readiness.ready is False
    assert any("already exists" in blocker for blocker in preview.readiness.blockers)


def test_build_release_preview_blocks_on_missing_release_label() -> None:
    unlabeled = PullRequestMetadata(
        number=81,
        title="Task 038: unlabeled",
        labels=(),
        merged=True,
        base_ref="main",
        merge_commit_sha="bbb",
    )
    preview = build_release_preview(_context(commit_range_prs=(unlabeled,)))

    assert preview.readiness.ready is False
    assert any("PR #81" in blocker for blocker in preview.readiness.blockers)


def test_build_release_preview_blocks_when_commit_has_no_associated_pr() -> None:
    preview = build_release_preview(_context(commit_range_prs=(None,)))

    assert preview.readiness.ready is False
    assert any(
        "no associated merged Pull Request" in blocker for blocker in preview.readiness.blockers
    )


def test_build_release_preview_not_ready_when_no_eligible_changes() -> None:
    only_none = _pr(82, "Task 039: internal only", "release:none", "bbb")
    preview = build_release_preview(_context(commit_range_prs=(only_none,)))

    assert preview.readiness.ready is False
    assert preview.increment is None
    assert preview.expected_assets == ()
    assert any("no eligible" in blocker for blocker in preview.readiness.blockers)


def test_build_release_preview_handles_first_release_bootstrap() -> None:
    context = _context(
        release_records=(),
        main_commits=frozenset({"bbb", "ccc"}),
    )

    preview = build_release_preview(context)

    assert preview.previous_version == "0.1.0"
    assert preview.previous_release_commit is None
    assert preview.readiness.ready is True


def test_build_release_preview_notes_describe_same_prs_in_both_languages() -> None:
    changes = (
        _pr(80, "Task 037: Release Operator UX", "release:minor", "bbb"),
        _pr(81, "Task 038: Fix retry bug", "release:patch", "bcb"),
    )
    preview = build_release_preview(_context(commit_range_prs=changes))

    ko_section, en_section = preview.notes.split("# English")
    assert "#80" in ko_section and "#81" in ko_section
    assert "#80" in en_section and "#81" in en_section


# --------------------------------------------------------------------------
# local_checkout_is_dirty
# --------------------------------------------------------------------------


def test_local_checkout_is_dirty_detects_clean_and_dirty_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "file.txt").write_text("hello", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    assert local_checkout_is_dirty(repo) is False

    (repo / "file.txt").write_text("changed", encoding="utf-8")

    assert local_checkout_is_dirty(repo) is True


def test_local_checkout_is_dirty_returns_none_for_non_git_path(tmp_path: Path) -> None:
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    assert local_checkout_is_dirty(not_a_repo) is None


# --------------------------------------------------------------------------
# target_commit_is_ci_validated (Task 039): the CI workflow's own push-
# triggered run for the exact target commit is the only source of truth -
# never the aggregate Check Runs API, which mixes in every other workflow
# triggered for the same commit (see Task 039's Result doc for how this
# broke live: Release workflow jobs' check runs were poisoning the result).
# --------------------------------------------------------------------------


def test_ci_validated_true_for_exact_push_success() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]

    result = target_commit_is_ci_validated(
        client, _repository(), "target-sha", workflow_file=CI_WORKFLOW_FILE
    )

    assert result is True
    client.list_workflow_runs.assert_called_once_with(
        _repository(), CI_WORKFLOW_FILE, event="push", head_sha="target-sha"
    )


def test_ci_validated_false_for_pr_only_success_at_premerge_sha() -> None:
    """A successful run exists, but only for the pre-merge PR head SHA
    (`event="pull_request"`), not the post-merge commit on main - must not
    count, even if a defensive client-side check ever saw it."""
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="pull_request",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_for_success_on_another_sha() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="some-other-sha",
            event="push",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_when_no_run_found() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = []

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_for_queued_run() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="queued",
            conclusion=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_for_in_progress_run() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="in_progress",
            conclusion=None,
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_for_failed_run() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="failure",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_ci_validated_false_for_cancelled_run() -> None:
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="cancelled",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]

    assert target_commit_is_ci_validated(client, _repository(), "target-sha") is False


def test_gather_release_context_reports_api_failure_as_validation_error() -> None:
    client = MagicMock()
    client.get_commit_sha.return_value = "target-sha"
    client.list_workflow_runs.side_effect = GitHubAuthenticationError(
        "GitHub authentication failed: Resource not accessible by personal access token"
    )
    client.list_releases.return_value = []
    client.list_commits.return_value = ["target-sha"]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("devbot.release_ops.authoritative_version", lambda: "0.1.0")
        context = gather_release_context(client, _repository())

    assert context.target_commit_validated is False
    assert context.target_commit_validation_error is not None
    assert "Resource not accessible" in context.target_commit_validation_error

    preview = build_release_preview(context)
    assert preview.readiness.ready is False
    assert any(
        "could not verify" in blocker and "Resource not accessible" in blocker
        for blocker in preview.readiness.blockers
    )


def test_release_preview_is_ready_with_valid_ci_push_run_and_labels() -> None:
    """End-to-end (CP-039 scenario 10): a CI-validated push run for the
    exact target commit, plus a properly labeled merged PR and no other
    blockers, makes `devbot release preview` report ready."""
    client = MagicMock()
    client.get_commit_sha.return_value = "target-sha"
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="target-sha",
            event="push",
        )
    ]
    client.list_releases.return_value = [
        GitHubRelease(
            id=1,
            tag_name="v0.1.0",
            target_commitish="aaa",
            name="v0.1.0",
            body="",
            draft=False,
            prerelease=False,
            html_url="https://example/releases/v0.1.0",
            assets=(),
            published_at=None,
        )
    ]
    client.list_commits.return_value = ["target-sha", "aaa"]
    client.compare_commits.return_value = ["target-sha"]
    client.get_commit_pull_request_metadata.return_value = _pr(
        80, "Task 039: Fix CI validation", "release:patch", "target-sha"
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("devbot.release_ops.authoritative_version", lambda: "0.1.0")
        preview = fetch_release_preview(client, _repository(), local_checkout_path=Path("/tmp"))

    assert preview.readiness.ready is True
    assert preview.readiness.blockers == ()
    assert preview.target_commit_validated is True


# --------------------------------------------------------------------------
# gather_release_context (mocked GitHubClient)
# --------------------------------------------------------------------------


def test_gather_release_context_computes_commit_range_from_compare_api() -> None:
    client = MagicMock()
    client.get_commit_sha.return_value = "ccc"
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="ccc",
            event="push",
        )
    ]
    client.list_releases.return_value = [
        GitHubRelease(
            id=1,
            tag_name="v0.1.0",
            target_commitish="aaa",
            name="v0.1.0",
            body="",
            draft=False,
            prerelease=False,
            html_url="https://example/releases/v0.1.0",
            assets=(),
            published_at=None,
        )
    ]
    client.list_commits.return_value = ["ccc", "bbb", "aaa"]
    client.compare_commits.return_value = ["bbb", "ccc"]
    client.get_commit_pull_request_metadata.side_effect = [
        _pr(80, "Task 037: X", "release:minor", "bbb"),
        None,
    ]

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("devbot.release_ops.authoritative_version", lambda: "0.1.0")
        context = gather_release_context(client, _repository())

    client.compare_commits.assert_called_once_with(_repository(), "aaa", "ccc")
    client.list_workflow_runs.assert_called_once_with(
        _repository(), "ci.yml", event="push", head_sha="ccc"
    )
    client.list_check_runs_for_ref.assert_not_called()
    assert context.target_commit == "ccc"
    assert context.target_commit_validated is True
    assert context.commit_range_prs[0] is not None
    assert context.commit_range_prs[0].number == 80
    assert context.commit_range_prs[1] is None


def test_gather_release_context_falls_back_to_full_history_without_prior_release() -> None:
    client = MagicMock()
    client.get_commit_sha.return_value = "bbb"
    client.list_workflow_runs.return_value = [
        _run(
            status="completed",
            conclusion="success",
            created_at=datetime(2026, 7, 18, tzinfo=UTC),
            head_sha="bbb",
            event="push",
        )
    ]
    client.list_releases.return_value = []
    client.list_commits.return_value = ["bbb", "aaa"]
    client.get_commit_pull_request_metadata.return_value = _pr(
        1, "Task 001: bootstrap", "release:minor", "aaa"
    )

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr("devbot.release_ops.authoritative_version", lambda: "0.1.0")
        context = gather_release_context(client, _repository())

    client.compare_commits.assert_not_called()
    assert context.commit_range_prs == (
        _pr(1, "Task 001: bootstrap", "release:minor", "aaa"),
        _pr(1, "Task 001: bootstrap", "release:minor", "aaa"),
    )


# --------------------------------------------------------------------------
# dispatch_release
# --------------------------------------------------------------------------


def test_dispatch_release_refuses_when_not_ready() -> None:
    preview = build_release_preview(_context(target_commit_validated=False))
    write_client = MagicMock()

    with pytest.raises(ReleaseOpsError):
        dispatch_release(write_client, _repository(), preview)

    write_client.dispatch_workflow.assert_not_called()


def test_dispatch_release_sends_expected_inputs() -> None:
    preview = build_release_preview(_context())
    write_client = MagicMock()

    dispatch_release(write_client, _repository(), preview)

    write_client.dispatch_workflow.assert_called_once_with(
        _repository(),
        "release.yml",
        ref="main",
        inputs={
            "increment": "minor",
            "commit_sha": "ccc",
            "notes": preview.notes,
        },
    )


def test_dispatch_release_refuses_direct_strategy_before_any_dispatch() -> None:
    # Task 050: a repository configured for the direct publish path must
    # never be dispatched through the workflow path, even when the
    # preview is otherwise ready.
    preview = build_release_preview(_context())
    write_client = MagicMock()

    with pytest.raises(ReleasePublishStrategyMismatchError):
        dispatch_release(write_client, _repository(publish_strategy="direct"), preview)

    write_client.dispatch_workflow.assert_not_called()


def test_dispatch_release_accepts_explicit_workflow_strategy() -> None:
    preview = build_release_preview(_context())
    write_client = MagicMock()

    dispatch_release(write_client, _repository(publish_strategy="workflow"), preview)

    write_client.dispatch_workflow.assert_called_once()


# --------------------------------------------------------------------------
# wait_for_dispatched_run
# --------------------------------------------------------------------------


def _run(
    *,
    status: str,
    conclusion: str | None,
    created_at: datetime,
    run_id: int = 1,
    head_sha: str = "ccc",
    event: str = "workflow_dispatch",
) -> WorkflowRun:
    return WorkflowRun(
        id=run_id,
        name="Release",
        status=status,
        conclusion=conclusion,
        html_url=f"https://example/actions/runs/{run_id}",
        created_at=created_at,
        head_sha=head_sha,
        event=event,
    )


def test_wait_for_dispatched_run_polls_until_success() -> None:
    base = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(status="queued", conclusion=None, created_at=base)
    ]
    client.get_workflow_run.side_effect = [
        _run(status="in_progress", conclusion=None, created_at=base),
        _run(status="completed", conclusion="success", created_at=base),
    ]
    sleeps: list[float] = []
    clock = iter([base, base, base, base, base])

    run = wait_for_dispatched_run(
        client,
        _repository(),
        dispatched_after=base - timedelta(seconds=1),
        sleep=sleeps.append,
        now=lambda: next(clock, base),
    )

    assert run.status == "completed"
    assert run.conclusion == "success"
    assert len(sleeps) == 2


def test_wait_for_dispatched_run_raises_on_failure_conclusion() -> None:
    base = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_workflow_runs.return_value = [
        _run(status="completed", conclusion="failure", created_at=base)
    ]

    with pytest.raises(ReleaseOpsError, match="conclusion='failure'"):
        wait_for_dispatched_run(
            client,
            _repository(),
            dispatched_after=base - timedelta(seconds=1),
            sleep=lambda _seconds: None,
            now=lambda: base,
        )


def test_wait_for_dispatched_run_times_out_waiting_for_run_to_appear() -> None:
    base = datetime(2026, 7, 18, 0, 0, 0, tzinfo=UTC)
    client = MagicMock()
    client.list_workflow_runs.return_value = []
    clock = iter([base, base + timedelta(seconds=10)])

    with pytest.raises(ReleaseOpsError, match="timed out"):
        wait_for_dispatched_run(
            client,
            _repository(),
            dispatched_after=base,
            timeout_seconds=5,
            sleep=lambda _seconds: None,
            now=lambda: next(clock, base + timedelta(seconds=10)),
        )


# --------------------------------------------------------------------------
# validate_published_release
# --------------------------------------------------------------------------


def _release(
    *,
    draft: bool = False,
    target_commitish: str = "ccc",
    assets: tuple[GitHubReleaseAsset, ...],
) -> GitHubRelease:
    return GitHubRelease(
        id=1,
        tag_name="v0.2.0",
        target_commitish=target_commitish,
        name="v0.2.0",
        body="",
        draft=draft,
        prerelease=False,
        html_url="https://example/releases/v0.2.0",
        assets=assets,
        published_at=datetime(2026, 7, 18, tzinfo=UTC),
    )


def _valid_assets() -> tuple[GitHubReleaseAsset, ...]:
    return (
        GitHubReleaseAsset(id=10, name="devbot-0.2.0-linux-x86_64.tar.gz", size=1),
        GitHubReleaseAsset(id=11, name="SHA256SUMS", size=1),
    )


def _sums_text() -> str:
    return "0" * 64 + "  devbot-0.2.0-linux-x86_64.tar.gz\n"


def _preview_for_validation(
    assets: tuple[str, ...] = ("devbot-0.2.0-linux-x86_64.tar.gz", "SHA256SUMS"),
) -> ReleasePreview:
    return ReleasePreview(
        previous_version="0.1.0",
        next_version="0.2.0",
        increment="minor",
        target_commit="ccc",
        target_commit_validated=True,
        previous_release_commit="aaa",
        readiness=ReleaseReadiness(ready=True, blockers=()),
        expected_assets=assets,
        changes=(),
        notes="## devbot 0.2.0\n",
    )


def test_validate_published_release_success() -> None:
    client = MagicMock()
    client.get_release_by_tag.return_value = _release(assets=_valid_assets())
    client.download_release_asset.return_value = _sums_text().encode("utf-8")

    release = validate_published_release(client, _repository(), _preview_for_validation())

    assert release.tag_name == "v0.2.0"


def test_validate_published_release_rejects_draft() -> None:
    client = MagicMock()
    client.get_release_by_tag.return_value = _release(draft=True, assets=_valid_assets())

    with pytest.raises(ReleaseOpsError, match="draft"):
        validate_published_release(client, _repository(), _preview_for_validation())


def test_validate_published_release_rejects_target_commit_mismatch() -> None:
    client = MagicMock()
    client.get_release_by_tag.return_value = _release(
        target_commitish="wrong-sha", assets=_valid_assets()
    )

    with pytest.raises(ReleaseOpsError, match="targets"):
        validate_published_release(client, _repository(), _preview_for_validation())


def test_validate_published_release_rejects_asset_mismatch() -> None:
    client = MagicMock()
    client.get_release_by_tag.return_value = _release(
        assets=(GitHubReleaseAsset(id=1, name="devbot-0.2.0-linux-x86_64.tar.gz", size=1),)
    )

    with pytest.raises(ReleaseOpsError, match="assets mismatch"):
        validate_published_release(client, _repository(), _preview_for_validation())


def test_validate_published_release_rejects_malformed_checksum_manifest() -> None:
    client = MagicMock()
    client.get_release_by_tag.return_value = _release(assets=_valid_assets())
    client.download_release_asset.return_value = b"not-a-checksum-line\n"

    with pytest.raises(ReleaseOpsError, match="malformed"):
        validate_published_release(client, _repository(), _preview_for_validation())


# --------------------------------------------------------------------------
# build_release_status
# --------------------------------------------------------------------------


def test_build_release_status_up_to_date() -> None:
    client = MagicMock()
    client.list_releases.return_value = [_release(assets=_valid_assets())]
    client.list_workflow_runs.return_value = [
        _run(status="completed", conclusion="success", created_at=datetime(2026, 7, 18, tzinfo=UTC))
    ]

    status = build_release_status(client, _repository())

    assert status.publication_state == "up-to-date"
    assert status.latest_stable_version == "0.2.0"


def test_build_release_status_never_run() -> None:
    client = MagicMock()
    client.list_releases.return_value = []
    client.list_workflow_runs.return_value = []

    status = build_release_status(client, _repository())

    assert status.publication_state == "never-run"
    assert status.latest_stable_version is None


def test_build_release_status_failed() -> None:
    client = MagicMock()
    client.list_releases.return_value = []
    client.list_workflow_runs.return_value = [
        _run(status="completed", conclusion="failure", created_at=datetime(2026, 7, 18, tzinfo=UTC))
    ]

    status = build_release_status(client, _repository())

    assert status.publication_state == "failed"


def test_build_release_status_in_progress() -> None:
    client = MagicMock()
    client.list_releases.return_value = []
    client.list_workflow_runs.return_value = [
        _run(status="in_progress", conclusion=None, created_at=datetime(2026, 7, 18, tzinfo=UTC))
    ]

    status = build_release_status(client, _repository())

    assert status.publication_state == "in-progress"


# --------------------------------------------------------------------------
# publish_release: only ever writes via dispatch_workflow
# --------------------------------------------------------------------------


def test_publish_release_never_calls_any_write_method_other_than_dispatch() -> None:
    github_client = MagicMock()
    write_client = MagicMock(spec=["dispatch_workflow"])

    base = datetime(2026, 7, 18, tzinfo=UTC)
    github_client.list_workflow_runs.return_value = [
        _run(status="completed", conclusion="success", created_at=base)
    ]
    github_client.get_release_by_tag.return_value = _release(assets=_valid_assets())
    github_client.download_release_asset.return_value = _sums_text().encode("utf-8")

    outcome = publish_release(
        github_client,
        write_client,
        _repository(),
        preview=_preview_for_validation(),
        sleep=lambda _seconds: None,
        now=lambda: base,
    )

    write_client.dispatch_workflow.assert_called_once()
    assert outcome.tag == "v0.2.0"
    assert outcome.workflow_run.conclusion == "success"
