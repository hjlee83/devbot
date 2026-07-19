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
from devbot.release_ops import ReleaseOpsError
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
        release_url="https://example.invalid/releases/tag/v1.3.0",
        tag="v1.3.0",
        validated_assets=("SHA256SUMS",),
    )
    defaults.update(overrides)
    return WorkflowPublishOutcome(**defaults)  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# build_release_run_plan: pure, read-only, no GitHub, no writes.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("recommendation", "expected_target"),
    [
        (ReleaseRecommendation.MAJOR, "2.0.0"),
        (ReleaseRecommendation.MINOR, "1.3.0"),
        (ReleaseRecommendation.PATCH, "1.2.4"),
    ],
)
def test_plan_construction_for_each_recommendation(
    tmp_path: Path, recommendation: ReleaseRecommendation, expected_target: str
) -> None:
    _write_project(tmp_path, version="1.2.3")
    repository = _repository(tmp_path)

    plan = build_release_run_plan(repository, recommendation, local_checkout_path=tmp_path)

    assert plan.current_version == "1.2.3"
    assert plan.target_version == expected_target
    assert plan.recommendation is recommendation
    assert plan.preparation_required is True


def test_plan_none_recommendation_raises_before_touching_filesystem(tmp_path: Path) -> None:
    repository = _repository(tmp_path / "does-not-exist")

    with pytest.raises(NoReleaseRequiredError) as excinfo:
        build_release_run_plan(
            repository, ReleaseRecommendation.NONE, local_checkout_path=tmp_path / "does-not-exist"
        )

    assert excinfo.value.stage is ReleaseRunStage.RECOMMENDATION


def test_plan_workflow_route_when_strategy_omitted(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)

    plan = build_release_run_plan(
        repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
    )

    assert plan.publish_route is ReleaseRunStage.WORKFLOW_PUBLISH
    assert plan.effective_strategy.value == "workflow"


def test_plan_direct_route_when_strategy_direct(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")

    plan = build_release_run_plan(
        repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
    )

    assert plan.publish_route is ReleaseRunStage.DIRECT_PUBLISH


def test_plan_direct_notes_available_true_with_notes(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")

    plan = build_release_run_plan(
        repository, ReleaseRecommendation.PATCH, notes="notes", local_checkout_path=tmp_path
    )

    assert plan.direct_notes_available is True


@pytest.mark.parametrize("notes", [None, "", "   "])
def test_plan_direct_notes_available_false_reported_not_raised(
    tmp_path: Path, notes: str | None
) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")

    # Dry-run reports missing notes as a blocker rather than refusing to
    # build a plan at all.
    plan = build_release_run_plan(
        repository, ReleaseRecommendation.PATCH, notes=notes, local_checkout_path=tmp_path
    )

    assert plan.direct_notes_available is False


def test_plan_invalid_strategy_wraps_as_stage_error(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="bogus")

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
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
    repository = _repository(tmp_path)

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
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
    repository = _repository(tmp_path)

    with pytest.raises(ReleaseRunStageError) as excinfo:
        build_release_run_plan(
            repository, ReleaseRecommendation.PATCH, local_checkout_path=tmp_path
        )

    assert excinfo.value.stage is ReleaseRunStage.PREPARATION
    assert isinstance(excinfo.value.__cause__, MalformedProjectVersionError)


# --------------------------------------------------------------------------
# run_release: orchestration wiring, verified with mocked downstream calls.
# --------------------------------------------------------------------------


def test_run_release_none_recommendation_calls_nothing(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch("devbot.release_orchestration.prepare_release") as mock_prepare,
        patch("devbot.release_orchestration.publish_release") as mock_workflow,
        patch("devbot.release_orchestration.publish_prepared_release") as mock_direct,
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

    with patch("devbot.release_orchestration.prepare_release") as mock_prepare:
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


def test_run_release_workflow_route_success(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    outcome = _workflow_outcome()

    with (
        patch("devbot.release_orchestration.publish_release", return_value=outcome) as mock_wf,
        patch("devbot.release_orchestration.prepare_release") as mock_prepare,
        patch("devbot.release_orchestration.publish_prepared_release") as mock_direct,
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
    assert result.preparation is None
    mock_wf.assert_called_once()
    mock_prepare.assert_not_called()
    mock_direct.assert_not_called()


def test_run_release_workflow_route_never_writes_local_files(tmp_path: Path) -> None:
    _write_project(tmp_path)
    before = (tmp_path / "pyproject.toml").read_text()
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with patch(
        "devbot.release_orchestration.publish_release", return_value=_workflow_outcome()
    ):
        run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.PATCH,
            local_checkout_path=tmp_path,
        )

    assert (tmp_path / "pyproject.toml").read_text() == before


def test_run_release_workflow_route_failure_wraps_stage(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    original = ReleaseOpsError("main is dirty")

    with patch("devbot.release_orchestration.publish_release", side_effect=original):
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


def test_run_release_direct_route_success(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    prep_result = _preparation_result()
    direct_result = _direct_result()

    with (
        patch(
            "devbot.release_orchestration.prepare_release", return_value=prep_result
        ) as mock_prepare,
        patch(
            "devbot.release_orchestration.publish_prepared_release", return_value=direct_result
        ) as mock_direct,
        patch("devbot.release_orchestration.publish_release") as mock_workflow,
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
    assert result.direct_result is direct_result
    assert result.preparation is prep_result
    mock_prepare.assert_called_once()
    mock_direct.assert_called_once()
    mock_workflow.assert_not_called()


@pytest.mark.parametrize("failure", [DirtyWorktreeError("dirty"), StaleMainError("stale")])
def test_run_release_direct_route_reports_prepared_pending_commit(
    tmp_path: Path, failure: Exception
) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    prep_result = _preparation_result()

    with (
        patch(
            "devbot.release_orchestration.prepare_release", return_value=prep_result
        ) as mock_prepare,
        patch(
            "devbot.release_orchestration.publish_prepared_release", side_effect=failure
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
    assert result.direct_result is None
    mock_prepare.assert_called_once()
    mock_direct.assert_called_once()
    write_client.create_release.assert_not_called()


def test_run_release_direct_route_other_failure_wraps_stage(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)
    original = ConflictingTagError("tag points elsewhere")

    with (
        patch(
            "devbot.release_orchestration.prepare_release", return_value=_preparation_result()
        ),
        patch("devbot.release_orchestration.publish_prepared_release", side_effect=original),
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


def test_run_release_no_publish_when_preparation_fails(tmp_path: Path) -> None:
    _write_project(tmp_path)
    repository = _repository(tmp_path, publish_strategy="direct")
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(
            "devbot.release_orchestration.prepare_release",
            side_effect=VersionSourceMismatchError("mismatch"),
        ),
        patch("devbot.release_orchestration.publish_prepared_release") as mock_direct,
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
def test_run_release_exactly_one_publish_route_ever_called(
    tmp_path: Path, strategy: str
) -> None:
    _write_project(tmp_path)
    repository = _repository(
        tmp_path, publish_strategy=None if strategy == "workflow" else strategy
    )
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with (
        patch(
            "devbot.release_orchestration.publish_release", return_value=_workflow_outcome()
        ) as mock_workflow,
        patch(
            "devbot.release_orchestration.prepare_release", return_value=_preparation_result()
        ),
        patch(
            "devbot.release_orchestration.publish_prepared_release",
            return_value=_direct_result(),
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
# GitHub API fully mocked. Proves the actual safety behaviour discovered
# while designing this module - see release_orchestration.py's module
# docstring.
# --------------------------------------------------------------------------


def test_run_release_direct_route_real_prepare_never_publishes_prematurely(
    tmp_path: Path,
) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    main_sha = _rev_parse(local, "main")
    repository = _repository(local, publish_strategy="direct")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client()

    result = run_release(
        github_client,
        write_client,
        repository,
        ReleaseRecommendation.MINOR,
        notes="some notes",
        local_checkout_path=local,
    )

    # Regression test for the bug found while designing this module: an
    # earlier "attempt publish, then prepare on failure" ordering would
    # publish the CURRENT (un-bumped) 1.2.3 as-is, silently ignoring
    # `recommendation` entirely, whenever the checkout started clean.
    assert result.outcome is ReleaseRunOutcome.PREPARED_PENDING_COMMIT
    assert result.preparation is not None
    assert result.preparation.old_version == "1.2.3"
    assert result.preparation.new_version == "1.3.0"
    write_client.create_release.assert_not_called()
    assert "1.3.0" in (local / "pyproject.toml").read_text()


def test_run_release_direct_route_real_prepare_leaves_checkout_dirty(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    main_sha = _rev_parse(local, "main")
    repository = _repository(local, publish_strategy="direct")
    github_client = _fake_github_client({"main": main_sha})
    write_client = _fake_write_client()

    run_release(
        github_client,
        write_client,
        repository,
        ReleaseRecommendation.PATCH,
        notes="some notes",
        local_checkout_path=local,
    )

    status = _run(local, "status", "--porcelain").stdout
    assert "pyproject.toml" in status
    assert "uv.lock" in status


def test_run_release_workflow_route_real_repo_leaves_files_untouched(tmp_path: Path) -> None:
    local = _init_repo_with_remote(tmp_path, version="1.2.3")
    before = (local / "pyproject.toml").read_text()
    repository = _repository(local)
    github_client = MagicMock(spec=GitHubClient)
    write_client = MagicMock(spec=GitHubWriteClient)

    with patch(
        "devbot.release_orchestration.publish_release", return_value=_workflow_outcome()
    ):
        result = run_release(
            github_client,
            write_client,
            repository,
            ReleaseRecommendation.PATCH,
            local_checkout_path=local,
        )

    assert result.outcome is ReleaseRunOutcome.WORKFLOW_PUBLISHED
    assert (local / "pyproject.toml").read_text() == before
    status = _run(local, "status", "--porcelain").stdout
    assert status == ""


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
