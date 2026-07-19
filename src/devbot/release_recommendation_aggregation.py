"""Task 052: Release Recommendation Aggregation.

Computes one authoritative `ReleaseRecommendation` from every merged Task
PR since the latest stable GitHub Release, by resolving each PR to its
Task Contract and reusing Task 046's Contract parser and Task 047's
`classify_release` - never inferring release impact from PR titles,
labels, commit messages, or diffs. Entirely read-only: no write client is
ever constructed, no filesystem or GitHub write ever happens.

Architecture note - why this reuses `release_ops.gather_release_context`
for boundary-finding and PR enumeration rather than reimplementing it:
Tasks 037/048/051 already established, tested logic for "the latest
reachable stable Release" and "every commit merged into the default
branch since it" (`ReleaseContext.commit_range_prs`). Recomputing that
independently would duplicate policy this Task's own Specification
forbids duplicating. This module only adds what `gather_release_context`
does not provide: resolving each distinct PR number to a Contract,
classifying it, and aggregating.

Architecture note - PR-to-Contract resolution. There is no existing
end-to-end "PR -> Contract" mechanism in this repository (`devbot.planner`
only generates the convention; `devbot.worktree`'s parsers target Task
Issue bodies, not PR bodies, and are explicitly documented as best-effort,
not authoritative - see `docs/07-decisions.md`'s 2026-07-15 worktree ADR
entry). This module treats a PR's head branch name as the primary,
required signal: `task/<NNN>-<slug>` (`devbot.planner.canonical_branch_name`'s
own format) implies the Contract lives at `tasks/<NNN>-<slug>.md`
(`devbot.planner.canonical_contract_path`). A PR whose head branch does
not match that pattern was never a Task PR and is recorded as an explicit,
typed exclusion - never silently dropped. A PR whose head branch does
match but whose Contract does not exist at that path, at the merge
commit, is a genuine inconsistency and fails the entire aggregation
closed - a branch that self-identifies as a Task branch must resolve. As
a second, independent signal, a Planner-rendered PR body also states the
Contract path under a `## Contract` heading (`devbot.planner.render_pr_body`);
when present, it is cross-checked against the branch-derived path, and any
disagreement is an ambiguity error - fail closed, never guess which one
is right.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from devbot.contract_metadata import (
    ContractMetadataError,
    ContractParseResult,
    parse_contract_metadata,
)
from devbot.github_client import GitHubClient, GitHubClientError
from devbot.models import RepositoryConfig
from devbot.planner import canonical_contract_path
from devbot.release import (
    PullRequestMetadata,
    ReleasePolicyError,
    SemanticVersion,
    has_stable_release,
)
from devbot.release import latest_stable_version as _latest_stable_version
from devbot.release_classification import (
    ReleaseClassificationError,
    ReleaseRecommendation,
    classify_release,
)
from devbot.release_ops import ReleaseContext, gather_release_context

_TASK_BRANCH_RE = re.compile(r"^task/(\d{3})-(.+)$")
_PR_BODY_CONTRACT_RE = re.compile(r"##\s*Contract\s*\n+\s*-\s*`([^`]+)`")

_RECOMMENDATION_RANK: dict[ReleaseRecommendation, int] = {
    ReleaseRecommendation.NONE: 0,
    ReleaseRecommendation.PATCH: 1,
    ReleaseRecommendation.MINOR: 2,
    ReleaseRecommendation.MAJOR: 3,
}


class ReleaseRecommendationAggregationError(RuntimeError):
    """Base for an aggregation that cannot be safely computed."""


class MissingContractError(ReleaseRecommendationAggregationError):
    """A PR's head branch identifies it as a Task PR, but no Contract
    exists at the expected path at the merge commit."""


class AmbiguousContractError(ReleaseRecommendationAggregationError):
    """A PR's branch-derived Contract path disagrees with the Contract
    path its own PR body declares."""


class InvalidContractError(ReleaseRecommendationAggregationError):
    """A resolved Contract could not be classified - legacy, malformed, or
    otherwise invalid. Wraps the original `ContractMetadataError` or
    `ReleaseClassificationError`."""


@dataclass(frozen=True)
class ReleaseRecommendationEvidence:
    pr_number: int
    pr_url: str
    merge_commit_sha: str
    merged_at: datetime
    task_number: int
    contract_path: str
    recommendation: ReleaseRecommendation
    classification_reasons: tuple[str, ...]


class PullRequestExclusionReason(StrEnum):
    NOT_A_TASK_BRANCH = "not_a_task_branch"
    NON_DEFAULT_BASE_BRANCH = "non_default_base_branch"


@dataclass(frozen=True)
class ExcludedPullRequest:
    pr_number: int
    pr_url: str
    reason: PullRequestExclusionReason
    detail: str


@dataclass(frozen=True)
class ReleaseRecommendationAggregation:
    repository: str
    boundary_version: str | None
    boundary_tag: str | None
    boundary_commit: str | None
    inspected_pr_count: int
    evidence: tuple[ReleaseRecommendationEvidence, ...]
    excluded: tuple[ExcludedPullRequest, ...]
    recommendation: ReleaseRecommendation


def _resolve_boundary(context: ReleaseContext) -> tuple[str | None, str | None, str | None]:
    """`(version, tag, target_commitish)`, or `(None, None, None)` for the
    explicit no-release baseline - reuses the same two public functions
    `gather_release_context` itself uses internally, so "is there a
    boundary" and "what commit range was actually enumerated" can never
    disagree."""
    main_commits = set(context.main_commits)
    if not has_stable_release(context.release_records, main_commits=main_commits):
        return None, None, None

    version = _latest_stable_version(
        context.release_records,
        main_commits=main_commits,
        initial_version=context.initial_version,
    )
    for record in context.release_records:
        if record.draft or record.prerelease or record.target_commitish not in main_commits:
            continue
        try:
            record_version = SemanticVersion.parse_tag(record.tag_name)
        except ReleasePolicyError:
            continue
        if record_version == version:
            return str(version), record.tag_name, record.target_commitish

    raise ReleaseRecommendationAggregationError(
        f"internal inconsistency: latest stable version {version} computed but no "
        "matching Release record could be found"
    )


def _unique_ordered_pr_numbers(
    commit_range_prs: tuple[PullRequestMetadata | None, ...],
) -> list[int]:
    seen: dict[int, None] = {}
    for metadata in commit_range_prs:
        if metadata is None:
            continue
        if metadata.number not in seen:
            seen[metadata.number] = None
    return list(seen.keys())


def _classification_reasons(result: ContractParseResult) -> tuple[str, ...]:
    assert result.metadata is not None
    metadata = result.metadata
    return (
        f"specification_type={metadata.specification_type.value}",
        f"release_impact={metadata.release_impact.value}",
        f"compatibility={metadata.compatibility.value}",
        f"migration={metadata.migration.value}",
    )


def build_release_recommendation_aggregation(
    github_client: GitHubClient,
    repository: RepositoryConfig,
) -> ReleaseRecommendationAggregation:
    """Read-only: no write client, no filesystem mutation, no GitHub
    write. Raises `ReleaseRecommendationAggregationError` (or a subclass)
    on any incomplete or ambiguous evidence rather than guessing."""
    try:
        context = gather_release_context(github_client, repository)
    except GitHubClientError as exc:
        raise ReleaseRecommendationAggregationError(
            f"{repository.full_name}: could not determine the release boundary: {exc}"
        ) from exc

    boundary_version, boundary_tag, boundary_commit = _resolve_boundary(context)
    pr_numbers = _unique_ordered_pr_numbers(context.commit_range_prs)

    evidence: list[ReleaseRecommendationEvidence] = []
    excluded: list[ExcludedPullRequest] = []

    for pr_number in pr_numbers:
        try:
            detail = github_client.get_pull_request(repository, pr_number)
        except GitHubClientError as exc:
            raise ReleaseRecommendationAggregationError(
                f"{repository.full_name}: could not fetch PR #{pr_number}: {exc}"
            ) from exc

        if detail.base_ref != repository.default_branch:
            excluded.append(
                ExcludedPullRequest(
                    pr_number=pr_number,
                    pr_url=detail.html_url,
                    reason=PullRequestExclusionReason.NON_DEFAULT_BASE_BRANCH,
                    detail=(
                        f"merged into {detail.base_ref!r}, not the default branch "
                        f"{repository.default_branch!r}"
                    ),
                )
            )
            continue

        match = _TASK_BRANCH_RE.match(detail.head_ref)
        if match is None:
            excluded.append(
                ExcludedPullRequest(
                    pr_number=pr_number,
                    pr_url=detail.html_url,
                    reason=PullRequestExclusionReason.NOT_A_TASK_BRANCH,
                    detail=f"head branch {detail.head_ref!r} does not match task/NNN-slug",
                )
            )
            continue

        task_number = int(match.group(1))
        slug = match.group(2)
        contract_path = canonical_contract_path(task_number, slug)

        body_match = _PR_BODY_CONTRACT_RE.search(detail.body)
        if body_match is not None and body_match.group(1) != contract_path:
            raise AmbiguousContractError(
                f"PR #{pr_number}: branch-derived Contract path {contract_path!r} "
                f"disagrees with the PR body's declared Contract path "
                f"{body_match.group(1)!r}"
            )

        if not detail.merged or not detail.merge_commit_sha or detail.merged_at is None:
            raise ReleaseRecommendationAggregationError(
                f"PR #{pr_number}: expected a merged Pull Request with a merge commit "
                "and merge timestamp, but the fetched detail does not report one"
            )

        text = github_client.get_file_content(
            repository, contract_path, ref=detail.merge_commit_sha
        )
        if text is None:
            raise MissingContractError(
                f"PR #{pr_number}: expected Contract {contract_path!r} does not exist "
                f"at merge commit {detail.merge_commit_sha!r}"
            )

        try:
            parse_result = parse_contract_metadata(text)
            recommendation = classify_release(parse_result)
        except (ContractMetadataError, ReleaseClassificationError) as exc:
            raise InvalidContractError(
                f"PR #{pr_number}: Contract {contract_path!r} could not be classified: "
                f"{exc}"
            ) from exc

        evidence.append(
            ReleaseRecommendationEvidence(
                pr_number=pr_number,
                pr_url=detail.html_url,
                merge_commit_sha=detail.merge_commit_sha,
                merged_at=detail.merged_at,
                task_number=task_number,
                contract_path=contract_path,
                recommendation=recommendation,
                classification_reasons=_classification_reasons(parse_result),
            )
        )

    evidence.sort(key=lambda item: (item.merged_at, item.pr_number))

    final_recommendation = ReleaseRecommendation.NONE
    for item in evidence:
        if _RECOMMENDATION_RANK[item.recommendation] > _RECOMMENDATION_RANK[final_recommendation]:
            final_recommendation = item.recommendation

    return ReleaseRecommendationAggregation(
        repository=repository.full_name,
        boundary_version=boundary_version,
        boundary_tag=boundary_tag,
        boundary_commit=boundary_commit,
        inspected_pr_count=len(pr_numbers),
        evidence=tuple(evidence),
        excluded=tuple(excluded),
        recommendation=final_recommendation,
    )


def render_release_recommendation_aggregation(
    aggregation: ReleaseRecommendationAggregation,
) -> str:
    lines = [
        f"repository: {aggregation.repository}",
        f"boundary_version: {aggregation.boundary_version or 'none'}",
        f"boundary_tag: {aggregation.boundary_tag or 'none'}",
        f"boundary_commit: {aggregation.boundary_commit or 'none'}",
        f"inspected_pr_count: {aggregation.inspected_pr_count}",
        f"evidence ({len(aggregation.evidence)}):",
    ]
    for item in aggregation.evidence:
        lines.append(
            f"  - PR #{item.pr_number} [{item.recommendation}] task={item.task_number:03d} "
            f"contract={item.contract_path} merged_at={item.merged_at.isoformat()} "
            f"sha={item.merge_commit_sha}"
        )
        for reason in item.classification_reasons:
            lines.append(f"      {reason}")
    lines.append(f"excluded ({len(aggregation.excluded)}):")
    for excluded in aggregation.excluded:
        lines.append(f"  - PR #{excluded.pr_number} [{excluded.reason}] {excluded.detail}")
    lines.append(f"recommendation: {aggregation.recommendation}")
    return "\n".join(lines)
