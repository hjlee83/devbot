from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from devbot.contract_metadata import ContractMetadataError
from devbot.github_client import GitHubClient
from devbot.models import RepositoryConfig
from devbot.release import PullRequestMetadata, ReleaseRecord
from devbot.release_classification import ReleaseClassificationError, ReleaseRecommendation
from devbot.release_ops import ReleaseContext
from devbot.release_recommendation_aggregation import (
    AmbiguousContractError,
    ExcludedPullRequest,
    InvalidContractError,
    MissingContractError,
    PullRequestExclusionReason,
    ReleaseRecommendationAggregationError,
    ReleaseRecommendationEvidence,
    build_release_recommendation_aggregation,
    render_release_recommendation_aggregation,
)

_OPS = "devbot.release_recommendation_aggregation"

_SCHEMA_V1_CONTRACT = """# Task {n:03d} — Test Task

## Contract Version

1

## Provenance

- GitHub Issue: #{n}
- Branch: `task/{n:03d}-test-task`

## Task Identity

- id: {n:03d}
- title: Test Task

## Metadata

- specification_type: feature
- release_impact: {impact}
- risk_level: low
- compatibility: {compatibility}
- migration: none

## Goal

Test.

## Context

Test.

## Scope

- Test.

## Out of Scope

- Test.

## Deliverables

- Test.

## Acceptance Criteria

1. Test.

## Quality Gates

- Test.

## Handoff

- Test.
"""

_LEGACY_CONTRACT = """# Task {n:03d} — Legacy Task

## Goal

Legacy.
"""

_MALFORMED_VERSION_CONTRACT = """# Task {n:03d} — Malformed Task

## Contract Version

- contract_version: 1

## Goal

Malformed.
"""


def _repository(**overrides: object) -> RepositoryConfig:
    defaults: dict[str, object] = dict(
        owner="someone",
        repo="myrepo",
        enabled=True,
        local_path=None,
        default_branch="main",
    )
    defaults.update(overrides)
    return RepositoryConfig(**defaults)  # type: ignore[arg-type]


def _pr_meta(number: int, **overrides: object) -> PullRequestMetadata:
    defaults: dict[str, object] = dict(
        number=number,
        title=f"PR {number}",
        labels=(),
        merged=True,
        base_ref="main",
        merge_commit_sha=f"sha{number}",
    )
    defaults.update(overrides)
    return PullRequestMetadata(**defaults)  # type: ignore[arg-type]


def _context(commit_range_prs: tuple, **overrides: object) -> ReleaseContext:
    defaults: dict[str, object] = dict(
        target_commit="targetsha",
        target_commit_validated=True,
        release_records=(),
        main_commits=frozenset({"targetsha"}),
        initial_version="1.0.0",
        commit_range_prs=commit_range_prs,
    )
    defaults.update(overrides)
    return ReleaseContext(**defaults)  # type: ignore[arg-type]


def _pr_detail(
    number: int,
    *,
    head_ref: str,
    base_ref: str = "main",
    body: str = "",
    merged: bool = True,
    merge_commit_sha: str | None = None,
    merged_at: datetime | None = None,
) -> MagicMock:
    detail = MagicMock()
    detail.number = number
    detail.html_url = f"https://example.invalid/pull/{number}"
    detail.body = body
    detail.head_ref = head_ref
    detail.base_ref = base_ref
    detail.merged = merged
    detail.merge_commit_sha = merge_commit_sha if merge_commit_sha is not None else f"sha{number}"
    detail.merged_at = merged_at or datetime(2026, 7, 19, 0, 0, number, tzinfo=UTC)
    return detail


def _github_client(pr_details: dict[int, MagicMock]) -> MagicMock:
    client = MagicMock(spec=GitHubClient)
    client.get_pull_request.side_effect = lambda repo, number: pr_details[number]
    return client


# --------------------------------------------------------------------------
# Boundary resolution.
# --------------------------------------------------------------------------


def test_no_prior_release_and_no_merged_prs_returns_none(tmp_path: object = None) -> None:
    ctx = _context(())
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository()

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, repository)

    assert aggregation.boundary_version is None
    assert aggregation.boundary_tag is None
    assert aggregation.boundary_commit is None
    assert aggregation.recommendation is ReleaseRecommendation.NONE
    assert aggregation.evidence == ()
    assert aggregation.inspected_pr_count == 0


def test_no_merged_prs_after_boundary_with_stable_release_returns_none() -> None:
    record = ReleaseRecord(tag_name="v1.2.3", target_commitish="boundarysha")
    ctx = _context(
        (), release_records=(record,), main_commits=frozenset({"boundarysha", "targetsha"})
    )
    github_client = MagicMock(spec=GitHubClient)
    repository = _repository()

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, repository)

    assert aggregation.boundary_version == "1.2.3"
    assert aggregation.boundary_tag == "v1.2.3"
    assert aggregation.boundary_commit == "boundarysha"
    assert aggregation.recommendation is ReleaseRecommendation.NONE
    assert aggregation.evidence == ()


def test_boundary_resolution_ignores_draft_and_prerelease_records() -> None:
    stable = ReleaseRecord(tag_name="v1.0.0", target_commitish="stablesha")
    draft = ReleaseRecord(tag_name="v2.0.0", target_commitish="draftsha", draft=True)
    prerelease = ReleaseRecord(tag_name="v3.0.0", target_commitish="presha", prerelease=True)
    ctx = _context(
        (),
        release_records=(stable, draft, prerelease),
        main_commits=frozenset({"stablesha", "draftsha", "presha", "targetsha"}),
    )
    github_client = MagicMock(spec=GitHubClient)

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert aggregation.boundary_version == "1.0.0"


# --------------------------------------------------------------------------
# Aggregation: one PR per level, precedence, deterministic ordering.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("impact", "compatibility", "expected"),
    [
        ("fix", "backward", ReleaseRecommendation.PATCH),
        ("feature", "backward", ReleaseRecommendation.MINOR),
        ("breaking", "backward", ReleaseRecommendation.MAJOR),
        ("docs", "backward", ReleaseRecommendation.NONE),
    ],
)
def test_single_pr_recommendation_for_each_level(
    impact: str, compatibility: str, expected: ReleaseRecommendation
) -> None:
    ctx = _context((_pr_meta(1),))
    github_client = _github_client({1: _pr_detail(1, head_ref="task/001-test-task")})
    github_client.get_file_content.return_value = _SCHEMA_V1_CONTRACT.format(
        n=1, impact=impact, compatibility=compatibility
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert aggregation.recommendation is expected
    assert len(aggregation.evidence) == 1


def test_mixed_recommendations_aggregate_by_precedence_and_deterministic_order() -> None:
    ctx = _context((_pr_meta(3), _pr_meta(1), _pr_meta(2)))
    github_client = _github_client(
        {
            1: _pr_detail(1, head_ref="task/001-a", merged_at=datetime(2026, 7, 19, 3, tzinfo=UTC)),
            2: _pr_detail(2, head_ref="task/002-b", merged_at=datetime(2026, 7, 19, 1, tzinfo=UTC)),
            3: _pr_detail(3, head_ref="task/003-c", merged_at=datetime(2026, 7, 19, 2, tzinfo=UTC)),
        }
    )
    impacts = {1: "fix", 2: "breaking", 3: "feature"}

    def get_file_content(repo: object, path: str, ref: str) -> str:
        n = int(path.split("/")[1][:3])
        return _SCHEMA_V1_CONTRACT.format(n=n, impact=impacts[n], compatibility="backward")

    github_client.get_file_content.side_effect = get_file_content

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert aggregation.recommendation is ReleaseRecommendation.MAJOR
    # Ordered by merged_at ascending regardless of commit_range_prs order.
    assert [e.pr_number for e in aggregation.evidence] == [2, 3, 1]


def test_classification_reasons_are_preserved() -> None:
    ctx = _context((_pr_meta(1),))
    github_client = _github_client({1: _pr_detail(1, head_ref="task/001-test-task")})
    github_client.get_file_content.return_value = _SCHEMA_V1_CONTRACT.format(
        n=1, impact="feature", compatibility="backward"
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    reasons = aggregation.evidence[0].classification_reasons
    assert "specification_type=feature" in reasons
    assert "release_impact=feature" in reasons
    assert "compatibility=backward" in reasons
    assert "migration=none" in reasons


def test_duplicate_commit_entries_for_same_pr_deduplicated() -> None:
    ctx = _context((_pr_meta(1), _pr_meta(1), _pr_meta(1)))
    github_client = _github_client({1: _pr_detail(1, head_ref="task/001-test-task")})
    github_client.get_file_content.return_value = _SCHEMA_V1_CONTRACT.format(
        n=1, impact="fix", compatibility="backward"
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert len(aggregation.evidence) == 1
    assert aggregation.inspected_pr_count == 1
    assert github_client.get_pull_request.call_count == 1


def test_large_pr_set_all_processed_and_ordered() -> None:
    numbers = list(range(1, 51))
    ctx = _context(tuple(_pr_meta(n) for n in numbers))
    details = {
        n: _pr_detail(
            n,
            head_ref=f"task/{n:03d}-slug",
            merged_at=datetime(2026, 7, 19, 0, 0, 51 - n, tzinfo=UTC),
        )
        for n in numbers
    }
    github_client = _github_client(details)
    github_client.get_file_content.return_value = _SCHEMA_V1_CONTRACT.format(
        n=1, impact="fix", compatibility="backward"
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert len(aggregation.evidence) == 50
    merged_ats = [e.merged_at for e in aggregation.evidence]
    assert merged_ats == sorted(merged_ats)


# --------------------------------------------------------------------------
# Exclusions: explicit, typed, never silent.
# --------------------------------------------------------------------------


def test_non_task_branch_excluded_not_error() -> None:
    ctx = _context((_pr_meta(10),))
    github_client = _github_client({10: _pr_detail(10, head_ref="chore/bump-version")})

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert aggregation.evidence == ()
    assert aggregation.excluded == (
        ExcludedPullRequest(
            pr_number=10,
            pr_url="https://example.invalid/pull/10",
            reason=PullRequestExclusionReason.NOT_A_TASK_BRANCH,
            detail="head branch 'chore/bump-version' does not match task/NNN-slug",
        ),
    )
    assert aggregation.recommendation is ReleaseRecommendation.NONE


def test_non_default_base_branch_excluded_not_error() -> None:
    ctx = _context((_pr_meta(11),))
    github_client = _github_client(
        {11: _pr_detail(11, head_ref="task/011-something", base_ref="release-branch")}
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert aggregation.evidence == ()
    assert len(aggregation.excluded) == 1
    assert aggregation.excluded[0].reason is PullRequestExclusionReason.NON_DEFAULT_BASE_BRANCH


# --------------------------------------------------------------------------
# Fail-closed: missing, ambiguous, malformed, legacy Contracts.
# --------------------------------------------------------------------------


def test_missing_contract_fails_closed() -> None:
    ctx = _context((_pr_meta(20),))
    github_client = _github_client({20: _pr_detail(20, head_ref="task/020-missing")})
    github_client.get_file_content.return_value = None

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(MissingContractError) as excinfo:
            build_release_recommendation_aggregation(github_client, _repository())

    assert "tasks/020-missing.md" in str(excinfo.value)


def test_ambiguous_contract_path_fails_closed() -> None:
    ctx = _context((_pr_meta(21),))
    github_client = _github_client(
        {
            21: _pr_detail(
                21,
                head_ref="task/021-real-slug",
                body="## Contract\n\n- `tasks/021-different-slug.md`\n\n",
            )
        }
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(AmbiguousContractError):
            build_release_recommendation_aggregation(github_client, _repository())


def test_matching_pr_body_contract_path_is_not_ambiguous() -> None:
    ctx = _context((_pr_meta(23),))
    github_client = _github_client(
        {
            23: _pr_detail(
                23,
                head_ref="task/023-test-task",
                body="## Contract\n\n- `tasks/023-test-task.md`\n\n",
            )
        }
    )
    github_client.get_file_content.return_value = _SCHEMA_V1_CONTRACT.format(
        n=23, impact="fix", compatibility="backward"
    )

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        aggregation = build_release_recommendation_aggregation(github_client, _repository())

    assert len(aggregation.evidence) == 1


def test_legacy_contract_fails_closed() -> None:
    ctx = _context((_pr_meta(22),))
    github_client = _github_client({22: _pr_detail(22, head_ref="task/022-legacy")})
    github_client.get_file_content.return_value = _LEGACY_CONTRACT.format(n=22)

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(InvalidContractError) as excinfo:
            build_release_recommendation_aggregation(github_client, _repository())

    assert isinstance(excinfo.value.__cause__, ReleaseClassificationError)


def test_malformed_contract_version_fails_closed() -> None:
    ctx = _context((_pr_meta(24),))
    github_client = _github_client({24: _pr_detail(24, head_ref="task/024-malformed")})
    github_client.get_file_content.return_value = _MALFORMED_VERSION_CONTRACT.format(n=24)

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(InvalidContractError) as excinfo:
            build_release_recommendation_aggregation(github_client, _repository())

    assert isinstance(excinfo.value.__cause__, ContractMetadataError)


def test_no_evidence_when_first_pr_fails_closed_is_recorded_partial() -> None:
    # The whole aggregation fails closed on the first bad PR - no partial
    # ReleaseRecommendationAggregation is ever returned.
    ctx = _context((_pr_meta(1), _pr_meta(2)))
    github_client = _github_client(
        {
            1: _pr_detail(1, head_ref="task/001-good"),
            2: _pr_detail(2, head_ref="task/002-missing"),
        }
    )

    def get_file_content(repo, path, ref):
        if "001" in path:
            return _SCHEMA_V1_CONTRACT.format(n=1, impact="fix", compatibility="backward")
        return None

    github_client.get_file_content.side_effect = get_file_content

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(MissingContractError):
            build_release_recommendation_aggregation(github_client, _repository())


# --------------------------------------------------------------------------
# GitHub call failures propagate as ReleaseRecommendationAggregationError.
# --------------------------------------------------------------------------


def test_boundary_github_failure_wraps_error() -> None:
    from devbot.github_client import GitHubAPIError

    github_client = MagicMock(spec=GitHubClient)
    with patch(f"{_OPS}.gather_release_context", side_effect=GitHubAPIError("boom")):
        with pytest.raises(ReleaseRecommendationAggregationError):
            build_release_recommendation_aggregation(github_client, _repository())


def test_pr_detail_github_failure_wraps_error() -> None:
    from devbot.github_client import GitHubAPIError

    ctx = _context((_pr_meta(1),))
    github_client = MagicMock(spec=GitHubClient)
    github_client.get_pull_request.side_effect = GitHubAPIError("boom")

    with patch(f"{_OPS}.gather_release_context", return_value=ctx):
        with pytest.raises(ReleaseRecommendationAggregationError):
            build_release_recommendation_aggregation(github_client, _repository())


# --------------------------------------------------------------------------
# No write client, no write operation.
# --------------------------------------------------------------------------


def test_no_write_client_type_ever_used(tmp_path: object = None) -> None:
    import inspect

    from devbot import release_recommendation_aggregation as module

    source = inspect.getsource(module)
    assert "GitHubWriteClient" not in source
    assert "write_client" not in source


# --------------------------------------------------------------------------
# Error hierarchy and rendering.
# --------------------------------------------------------------------------


def test_all_aggregation_errors_share_base_class() -> None:
    for error_type in (MissingContractError, AmbiguousContractError, InvalidContractError):
        assert issubclass(error_type, ReleaseRecommendationAggregationError)


def test_render_includes_all_evidence_and_exclusions() -> None:
    aggregation_evidence = ReleaseRecommendationEvidence(
        pr_number=1,
        pr_url="https://example.invalid/pull/1",
        merge_commit_sha="sha1",
        merged_at=datetime(2026, 7, 19, tzinfo=UTC),
        task_number=1,
        contract_path="tasks/001-test-task.md",
        recommendation=ReleaseRecommendation.PATCH,
        classification_reasons=("release_impact=fix",),
    )
    excluded = ExcludedPullRequest(
        pr_number=2,
        pr_url="https://example.invalid/pull/2",
        reason=PullRequestExclusionReason.NOT_A_TASK_BRANCH,
        detail="not a task branch",
    )
    from devbot.release_recommendation_aggregation import ReleaseRecommendationAggregation

    aggregation = ReleaseRecommendationAggregation(
        repository="someone/myrepo",
        boundary_version="1.0.0",
        boundary_tag="v1.0.0",
        boundary_commit="sha0",
        inspected_pr_count=2,
        evidence=(aggregation_evidence,),
        excluded=(excluded,),
        recommendation=ReleaseRecommendation.PATCH,
    )

    rendered = render_release_recommendation_aggregation(aggregation)

    assert "PR #1" in rendered
    assert "PR #2" in rendered
    assert "release_impact=fix" in rendered
    assert "recommendation: patch" in rendered
