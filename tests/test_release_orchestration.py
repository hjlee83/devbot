from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from devbot.github_client import GitHubClient, GitHubNotFoundError
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.release_classification import ReleaseRecommendation
from devbot.release_ops import PublishOutcome as WorkflowPublishOutcome
from devbot.release_ops import ReleaseOpsError, ReleasePreview, ReleaseReadiness, ReleaseStatus
from devbot.release_orchestration import (
    MissingDirectReleaseNotesError,
    NoReleaseRequiredError,
    ReleaseOrchestrationError,
    ReleaseRunOutcome,
    ReleaseRunStage,
    ReleaseRunStageError,
    build_release_run_plan,
    run_release,
)
from devbot.release_preparation import (
    MalformedProjectVersionError,
    ReleasePreparationResult,
    VersionSourceMismatchError,
)
from devbot.release_publish import (
    ConflictingTagError,
    DirtyWorktreeError,
    ReleasePublishResult,
    StaleMainError,
)
from devbot.release_publish import (
    PublishOutcome as DirectPublishOutcome,
)
from devbot.release_publish_strategy import InvalidReleasePublishStrategyError

# --------------------------------------------------------------------------
# Fixtures. `_write_project` writes plain files for pure-planning tests
# (no Git needed). `_init_repo_with_remote` mirrors test_release_publish.py's
# real-git fixture for the small set of true end-to-end tests below.
# --------------------------------------------------------------------------


def _write_project(directory: Path, *, version: str = "1.2.3") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pyproject.toml").write_text(
        f'[project]\nname = "devbot"\nversion = "{version}"\n', encoding="utf-8"
    )
    (directory / "uv.lock").write_text(
        f'version = 1\n\n[[package]]\nname = "devbot"\nversion = "{version}"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
    return result


def _init_repo_with_remote(tmp_path: Path, *, version: str = "1.2.3") -> Path:
    local = tmp_path / "local"
    remote = tmp_path / "remote.git"
    local.mkdir()
    _run(local, "init", "-q")
    _run(local, "config", "user.email", "test@example.com")
    _run(local, "config", "user.name", "Test")
    _write_project(local, version=version)
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "init")
    _run(local, "branch", "-M", "main")
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    _run(local, "remote", "add", "origin", str(remote))
    _run(local, "push", "-q", "origin", "main")
    return local


def _rev_parse(path: Path, ref: str) -> str:
    return _run(path, "rev-parse", ref).stdout.strip()


def _repository(directory: Path, **overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=directory,
        default_branch="main",
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _fake_github_client(refs: dict[str, str], *, release: object | None = None) -> MagicMock:
    client = MagicMock(spec=GitHubClient)

    def get_commit_sha(repository: RepositoryConfig, ref: str) -> str:
        if ref in refs:
            return refs[ref]
        raise GitHubNotFoundError(f"no ref {ref!r}")

    client.get_commit_sha.side_effect = get_commit_sha
    client.get_release_by_tag.return_value = release
    return client


def _fake_write_client() -> MagicMock:
    client = MagicMock(spec=GitHubWriteClient)
    info = MagicMock()
    info.html_url = "https://example.invalid/r"
    client.create_release.return_value = info
    return client


def _release_status(**overrides: object) -> ReleaseStatus:
    defaults: dict[str, object] = dict(
        latest_stable_version="1.2.3",
        latest_release_url=None,
        latest_release_published_at=None,
        last_published_commit=None,
        latest_workflow_run=None,
        publication_state="up-to-date",
    )
    defaults.update(overrides)
    return ReleaseStatus(**defaults)  # type: ignore[arg-type]


def _release_preview(**overrides: object) -> ReleasePreview:
    defaults: dict[str, object] = dict(
        previous_version="1.2.3",
        next_version="1.2.4",
        increment="patch",
        target_commit="deadbeef",
        target_commit_validated=True,
        previous_release_commit="cafef00d",
        readiness=ReleaseReadiness(ready=True, blockers=()),
        expected_assets=(),
        changes=(),
        notes="notes",
    )
    defaults.update(overrides)
    return ReleasePreview(**defaults)  # type: ignore[arg-type]


def _preparation_result(**overrides: object) -> ReleasePreparationResult:
    defaults: dict[str, object] = dict(
        recommendation=ReleaseRecommendation.MINOR,
        old_version="1.2.3",
        new_version="1.3.0",
        changed_paths=("pyproject.toml", "uv.lock"),
    )
    defaults.update(overrides)
    return ReleasePreparationResult(**defaults)  # type: ignore[arg-type]


def _direct_result(**overrides: object) -> ReleasePublishResult:
    defaults: dict[str, object] = dict(
        version="1.3.0",
        tag="v1.3.0",
        target_sha="deadbeef",
        release_url="https://example.invalid/r",
        outcome=DirectPublishOutcome.PUBLISHED,
    )
    defaults.update(overrides)
    return ReleasePublishResult(**defaults)  # type: ignore[arg-type]


def _workflow_outcome(**overrides: object) -> WorkflowPublishOutcome:
    defaults: dict[str, object] = dict(
        preview=MagicMock(),
        workflow_run=MagicMock(html_url="https://example.invalid/actions/runs/1"),
        release_url="https://example.invalid/releases/tag/v1.2.4",
        tag="v1.2.4",
        validated_assets=("SHA256SUMS",),
    )
    defaults.update(overrides)
    return WorkflowPublishOutcome(**defaults)  # type: ignore[arg-type]


_OPS = "devbot.release_orchestration"


# --------------------------------------------------------------------------
# build_release_run_plan: read-only (no writes), but now performs read-only
# GitHub calls so the plan is anchored to the latest published release
# rather than to mutable local file content - see the module docstring.
# --------------------------------------------------------------------------


def test_plan_none_recommendation_raises_before_touching_filesystem(tmp_path: Path) -> None:
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path / "does-not-exist")

    with pytest.raises(NoReleaseRequiredError) as excinfo:
        build_release_run_plan(
            github_client,
            repository,
            ReleaseRecommendation.NONE,
            local_checkout_path=tmp_path / "does-not-exist",
        )

    assert excinfo.value.stage is ReleaseRunStage.RECOMMENDATION


def test_plan_workflow_route_sources_versions_from_preview(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)
    preview = _release_preview(previous_version="1.2.3", next_version="1.2.4", increment="patch")

    with patch(f"{_OPS}.fetch_release_preview", return_value=preview):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert plan.current_version == "1.2.3"
    assert plan.target_version == "1.2.4"
    assert plan.publish_route is ReleaseRunStage.WORKFLOW_PUBLISH
    assert plan.recommendation_conflict is None


def test_plan_workflow_route_reports_mismatched_increment_as_blocker(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)
    preview = _release_preview(next_version="1.3.0", increment="minor")

    with patch(f"{_OPS}.fetch_release_preview", return_value=preview):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert plan.recommendation_conflict is not None
    assert "minor" in plan.recommendation_conflict
    assert "patch" in plan.recommendation_conflict


def test_plan_workflow_route_reports_no_eligible_increment_as_blocker(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)
    preview = _release_preview(
        increment=None, readiness=ReleaseReadiness(ready=False, blockers=("no eligible PRs",))
    )

    with patch(f"{_OPS}.fetch_release_preview", return_value=preview):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert plan.recommendation_conflict is not None
    assert "no eligible" in plan.recommendation_conflict


def test_plan_direct_route_sources_versions_from_github_baseline(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", return_value=_release_status()):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.MINOR, local_checkout_path=tmp_path
        )

    assert plan.current_version == "1.2.3"
    assert plan.target_version == "1.3.0"
    assert plan.publish_route is ReleaseRunStage.DIRECT_PUBLISH


def test_plan_direct_route_baseline_defaults_when_no_prior_release(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    status = _release_status(latest_stable_version=None)
    with patch(f"{_OPS}.build_release_status", return_value=status):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.MAJOR, local_checkout_path=tmp_path
        )

    assert plan.current_version == "0.0.0"
    assert plan.target_version == "1.0.0"


def test_plan_direct_route_preparation_required_true_when_not_yet_prepared(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, version="1.2.3")
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", return_value=_release_status()):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.MINOR, local_checkout_path=tmp_path
        )

    assert plan.preparation_required is True


def test_plan_direct_route_preparation_required_false_when_already_prepared(
    tmp_path: Path,
) -> None:
    # Local files already hold the target version, and this directory is
    # not a Git checkout at all - `local_checkout_is_dirty` returns `None`
    # (not a blocker) for that case, so this exercises the version-match
    # half of the "already prepared" check in isolation.
    _write_project(tmp_path, version="1.3.0")
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", return_value=_release_status()):
        plan = build_release_run_plan(
            github_client, repository, ReleaseRecommendation.MINOR, local_checkout_path=tmp_path
        )

    assert plan.preparation_required is False


def test_plan_invalid_strategy_wraps_as_stage_error(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="bogus")

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert excinfo.value.stage is ReleaseRunStage.STRATEGY_RESOLUTION
    assert isinstance(excinfo.value.__cause__, InvalidReleasePublishStrategyError)


def test_plan_preparation_mismatch_wraps_as_stage_error(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "devbot"\nversion = "1.2.3"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "devbot"\nversion = "9.9.9"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert excinfo.value.stage is ReleaseRunStage.PREPARATION
    assert isinstance(excinfo.value.__cause__, VersionSourceMismatchError)


def test_plan_malformed_version_wraps_as_stage_error(tmp_path: Path) -> None:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "devbot"\nversion = "not-a-version"\n', encoding="utf-8"
    )
    (tmp_path / "uv.lock").write_text(
        'version = 1\n\n[[package]]\nname = "devbot"\nversion = "not-a-version"\n'
        'source = { editable = "." }\n',
        encoding="utf-8",
    )
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert excinfo.value.stage is ReleaseRunStage.PREPARATION
    assert isinstance(excinfo.value.__cause__, MalformedProjectVersionError)


def test_plan_workflow_route_github_error_wraps_as_stage_error(tmp_path: Path) -> None:
    from devbot.github_client import GitHubAPIError

    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path)

    with patch(f"{_OPS}.fetch_release_preview", side_effect=GitHubAPIError("boom")):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            build_release_run_plan(
                github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
            )

    assert excinfo.value.stage is ReleaseRunStage.WORKFLOW_PUBLISH


def test_plan_direct_route_github_error_wraps_as_stage_error(tmp_path: Path) -> None:
    from devbot.github_client import GitHubAPIError

    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", side_effect=GitHubAPIError("boom")):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            build_release_run_plan(
                github_client, repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
            )

    assert excinfo.value.stage is ReleaseRunStage.DIRECT_PUBLISH


@pytest.mark.parametrize("notes", [None, "", "   "])
def test_plan_direct_notes_available_false_reported_not_raised(
    tmp_path: Path, notes: str | None
) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", return_value=_release_status()):
        plan = build_release_run_plan(
            github_client,
            repository,
            ReleaseRecommendation.PATCH,
            notes=notes,
            local_checkout_path=tmp_path,
        )

    assert plan.direct_notes_available is False


def test_plan_direct_notes_available_true_with_notes(tmp_path: Path) -> None:
    _write_project(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository(tmp_path, publish_strategy="direct")

    with patch(f"{_OPS}.build_release_status", return_value=_release_status()):
        plan = build_release_run_plan(
            github_client,
            repository,
            ReleaseRecommendation.PATCH,
            notes="notes",
            local_checkout_path=tmp_path,
        )

    assert plan.direct_notes_available is True


# --------------------------------------------------------------------------
# run_release: orchestration wiring, verified with mocked downstream calls.
# --------------------------------------------------------------------------


def test_run_release_none_recommendation_calls_nothing(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(f"{_OPS}.prepare_release") as mock_prepare,
        patch(f"{_OPS}.publish_release") as mock_workflow,
        patch(f"{_OPS}.publish_prepared_release") as mock_direct,
    ):
        with pytest.raises(NoReleaseRequiredError):
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.NONE,
                local_checkout_path=tmp_path,
            )

    mock_prepare.assert_not_called()
    mock_workflow.assert_not_called()
    mock_direct.assert_not_called()


def test_run_release_missing_direct_notes_raises_before_preparation(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.prepare_release") as mock_prepare,
    ):
        with pytest.raises(MissingDirectReleaseNotesError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.PATCH,
                notes=None,
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.DIRECT_PUBLISH
    mock_prepare.assert_not_called()


def test_run_release_workflow_route_success_calls_prepare_and_publish(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    preview = _release_preview(increment="patch")
    outcome = _workflow_outcome()
    prep_result = _preparation_result(old_version="1.2.3", new_version="1.2.4")

    with (
        patch(f"{_OPS}.fetch_release_preview", return_value=preview),
        patch(f"{_OPS}.prepare_release", return_value=prep_result) as mock_prepare,
        patch(f"{_OPS}.publish_release", return_value=outcome) as mock_wf,
        patch(f"{_OPS}.publish_prepared_release") as mock_direct,
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.PATCH,
            local_checkout_path=tmp_path,
        )

    assert result.outcome is ReleaseRunOutcome.WORKFLOW_PUBLISHED
    assert result.workflow_outcome is outcome
    assert result.preparation is prep_result
    mock_prepare.assert_called_once()
    mock_wf.assert_called_once()
    assert mock_wf.call_args.kwargs["preview"] is preview
    mock_direct.assert_not_called()


def test_run_release_workflow_route_conflict_refuses_before_any_write(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    preview = _release_preview(next_version="1.3.0", increment="minor")

    with (
        patch(f"{_OPS}.fetch_release_preview", return_value=preview),
        patch(f"{_OPS}.prepare_release") as mock_prepare,
        patch(f"{_OPS}.publish_release") as mock_wf,
    ):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.PATCH,
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.WORKFLOW_PUBLISH
    mock_prepare.assert_not_called()
    mock_wf.assert_not_called()


def test_run_release_workflow_route_failure_wraps_stage(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    preview = _release_preview(increment="patch")
    original = ReleaseOpsError("main is dirty")

    with (
        patch(f"{_OPS}.fetch_release_preview", return_value=preview),
        patch(f"{_OPS}.prepare_release", return_value=_preparation_result()),
        patch(f"{_OPS}.publish_release", side_effect=original),
    ):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.PATCH,
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.WORKFLOW_PUBLISH
    assert excinfo.value.__cause__ is original


def test_run_release_direct_route_already_prepared_skips_prepare_and_publishes(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, version="1.3.0")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    direct_result = _direct_result(tag="v1.3.0")

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(f"{_OPS}.prepare_release") as mock_prepare,
        patch(f"{_OPS}.publish_prepared_release", return_value=direct_result) as mock_direct,
        patch(f"{_OPS}.publish_release") as mock_workflow,
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=tmp_path,
        )

    assert result.outcome is ReleaseRunOutcome.DIRECT_PUBLISHED
    assert result.preparation is None
    assert result.direct_result is direct_result
    mock_prepare.assert_not_called()
    mock_direct.assert_called_once()
    mock_workflow.assert_not_called()


def test_run_release_direct_route_not_yet_prepared_calls_prepare_once(tmp_path: Path) -> None:
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    prep_result = _preparation_result(old_version="1.2.3", new_version="1.3.0")

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(f"{_OPS}.prepare_release", return_value=prep_result) as mock_prepare,
        patch(
            f"{_OPS}.publish_prepared_release", side_effect=DirtyWorktreeError("dirty")
        ) as mock_direct,
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=tmp_path,
        )

    assert result.outcome is ReleaseRunOutcome.PREPARED_PENDING_COMMIT
    assert result.preparation is prep_result
    mock_prepare.assert_called_once()
    mock_direct.assert_called_once()
    write_client.create_release.assert_not_called()


def test_run_release_direct_route_stale_main_reports_prepared_pending_commit(
    tmp_path: Path,
) -> None:
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(f"{_OPS}.prepare_release", return_value=_preparation_result()),
        patch(f"{_OPS}.publish_prepared_release", side_effect=StaleMainError("stale")),
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=tmp_path,
        )

    assert result.outcome is ReleaseRunOutcome.PREPARED_PENDING_COMMIT


def test_run_release_direct_route_other_failure_wraps_stage(tmp_path: Path) -> None:
    _write_project(tmp_path, version="1.3.0")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    original = ConflictingTagError("tag points elsewhere")

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(f"{_OPS}.prepare_release") as mock_prepare,
        patch(f"{_OPS}.publish_prepared_release", side_effect=original),
    ):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.MINOR,
                notes="some notes",
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.DIRECT_PUBLISH
    assert excinfo.value.__cause__ is original
    mock_prepare.assert_not_called()


def test_run_release_direct_route_inconsistent_local_state_wraps_stage_error(
    tmp_path: Path,
) -> None:
    # `prepare_release` (mocked) returns a version other than the plan's
    # GitHub-anchored target - simulating a stale/abandoned local checkout
    # inconsistent with the plan. Must fail closed rather than publish a
    # different version than the plan promised.
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(
            f"{_OPS}.prepare_release",
            return_value=_preparation_result(old_version="1.2.3", new_version="1.4.0"),
        ),
        patch(f"{_OPS}.publish_prepared_release") as mock_direct,
    ):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.MINOR,
                notes="some notes",
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.PREPARATION
    mock_direct.assert_not_called()


def test_run_release_no_publish_when_preparation_fails(tmp_path: Path) -> None:
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(f"{_OPS}.prepare_release", side_effect=VersionSourceMismatchError("mismatch")),
        patch(f"{_OPS}.publish_prepared_release") as mock_direct,
    ):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.MINOR,
                notes="some notes",
                local_checkout_path=tmp_path,
            )

    assert excinfo.value.stage is ReleaseRunStage.PREPARATION
    mock_direct.assert_not_called()


@pytest.mark.parametrize("strategy", ["workflow", "direct"])
def test_run_release_exactly_one_publish_route_ever_called(tmp_path: Path, strategy: str) -> None:
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(
        tmp_path, publish_strategy=None if strategy == "workflow" else strategy
    )
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    preview = _release_preview(previous_version="1.2.3", next_version="1.2.4", increment="patch")

    with (
        patch(f"{_OPS}.fetch_release_preview", return_value=preview),
        patch(f"{_OPS}.build_release_status", return_value=_release_status()),
        patch(f"{_OPS}.local_checkout_is_dirty", return_value=False),
        patch(
            f"{_OPS}.prepare_release",
            return_value=_preparation_result(old_version="1.2.3", new_version="1.2.4"),
        ),
        patch(f"{_OPS}.publish_release", return_value=_workflow_outcome()) as mock_workflow,
        patch(
            f"{_OPS}.publish_prepared_release", return_value=_direct_result()
        ) as mock_direct,
    ):
        run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.PATCH,
            notes="some notes",
            local_checkout_path=tmp_path,
        )

    if strategy == "workflow":
        mock_workflow.assert_called_once()
        mock_direct.assert_not_called()
    else:
        mock_direct.assert_called_once()
        mock_workflow.assert_not_called()


# --------------------------------------------------------------------------
# Real end-to-end: a genuine throwaway Git repo + throwaway bare remote,
# GitHub API fully mocked. Proves both reviewer-flagged behaviours for
# real: the workflow route's `--level` actually governs what would be
# dispatched, and the direct route genuinely reaches `DIRECT_PUBLISHED` on
# a second real invocation, without double-bumping.
# --------------------------------------------------------------------------


def test_run_release_direct_route_real_resume_reaches_direct_published_without_double_bump(
    tmp_path: Path,
) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    repository = _repository(local, publish_strategy="direct")
    write_client = _fake_write_client()
    status = _release_status()

    # First real invocation: nothing published yet, checkout starts clean.
    main_sha = _rev_parse(local, "main")
    github_client_1 = _fake_github_client({"main": main_sha})
    with patch(f"{_OPS}.build_release_status", return_value=status):
        result_1 = run_release(
            github_client_1,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=local,
        )

    assert result_1.outcome is ReleaseRunOutcome.PREPARED_PENDING_COMMIT
    assert result_1.preparation.new_version == "1.3.0"
    write_client.create_release.assert_not_called()

    # Operator commits and pushes the prepared files out of band.
    _run(local, "add", "-A")
    _run(local, "commit", "-q", "-m", "chore: prepare 1.3.0")
    _run(local, "push", "-q", "origin", "main")

    # Second real invocation, same --level: `status` is unchanged (mocked
    # statically, matching reality since no GitHub Release was actually
    # created yet), so the plan's target is unchanged too - the checkout
    # now matches it exactly, so this completes publication for real,
    # without recomputing a further bump.
    new_main_sha = _rev_parse(local, "main")
    github_client_2 = _fake_github_client({"main": new_main_sha})
    with patch(f"{_OPS}.build_release_status", return_value=status):
        plan_2 = build_release_run_plan(
            github_client_2,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=local,
        )
        assert plan_2.target_version == "1.3.0"
        assert plan_2.preparation_required is False

        result_2 = run_release(
            github_client_2,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            notes="some notes",
            local_checkout_path=local,
        )

    assert result_2.outcome is ReleaseRunOutcome.DIRECT_PUBLISHED
    assert result_2.preparation is None
    assert result_2.direct_result.tag == "v1.3.0"
    write_client.create_release.assert_called_once()


def test_run_release_workflow_route_real_repo_recommendation_governs_dispatch(
    tmp_path: Path,
) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    before = (local / "pyproject.toml").read_text()
    repository = _repository(local)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    preview = _release_preview(previous_version="1.2.3", next_version="1.3.0", increment="minor")

    # Requesting patch when the real computed increment is minor must
    # refuse before any write - proves `--level` is not silently ignored.
    with patch(f"{_OPS}.fetch_release_preview", return_value=preview):
        with pytest.raises(ReleaseRunStageError) as excinfo:
            run_release(
                github_client,
                write_client,
                repository,
                ReleaseRecommendation.PATCH,
                local_checkout_path=local,
            )
    assert excinfo.value.stage is ReleaseRunStage.WORKFLOW_PUBLISH
    assert (local / "pyproject.toml").read_text() == before

    # Requesting minor (matching) succeeds and does dispatch.
    with (
        patch(f"{_OPS}.fetch_release_preview", return_value=preview),
        patch(f"{_OPS}.publish_release", return_value=_workflow_outcome()) as mock_publish,
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.MINOR,
            local_checkout_path=local,
        )
    assert result.outcome is ReleaseRunOutcome.WORKFLOW_PUBLISHED
    mock_publish.assert_called_once()
    assert mock_publish.call_args.kwargs["preview"] is preview


# --------------------------------------------------------------------------
# Error hierarchy.
# --------------------------------------------------------------------------


def test_all_orchestration_errors_share_base_class() -> None:
    error_types = (NoReleaseRequiredError, MissingDirectReleaseNotesError, ReleaseRunStageError)
    for error_type in error_types:
        assert issubclass(error_type, ReleaseOrchestrationError)


def test_missing_notes_error_message_identifies_repository() -> None:
    error = MissingDirectReleaseNotesError("someone/myrepo")

    assert "someone/myrepo" in str(error)
    assert error.stage is ReleaseRunStage.DIRECT_PUBLISH
