"""Task 051: Release Orchestration.

Composes the existing Task 047 recommendation type, Task 048 preparation,
Task 050 publish-strategy resolution, and exactly one of Task 037's
workflow-dispatch publish or Task 049's direct tag+Release publish, into
one operator-facing command (`devbot release run`) - without duplicating
any of those modules' policy or safety logic.

Architecture note - why planning needs a `GitHubClient`, and why both
routes report a version anchored to the latest *published* release, not to
whatever a local checkout happens to contain: Task 037's workflow publish
computes its own release increment independently, from merged PR labels
since the last stable Release (`fetch_release_preview`) - it never reads
`pyproject.toml`/`uv.lock`. A plan that showed a target version computed
only from Task 048's local-file logic could therefore describe a release
that real workflow-route execution would never actually produce: dry-run
would promise one version, real execution would publish a different one
(or refuse), regardless of `--level`. `build_release_run_plan` closes that
gap by sourcing both routes' `current_version`/`target_version` from a
GitHub-anchored baseline - the workflow route's own computed preview for
that route, and the latest published stable Release (`build_release_status`)
for the direct route - and, for the workflow route, explicitly refusing
before any write when the operator's `--level` does not match the
independently computed increment (`ReleaseRunPlan.recommendation_conflict`).

Architecture note - why the direct route can now reach `DIRECT_PUBLISHED`
across at most two real invocations, without double-bumping: Task 049's
`preview_release_publish`/`publish_prepared_release` treat `pyproject.toml`/
`uv.lock` as read-only and refuse on any uncommitted local change
(`DirtyWorktreeError`) or a `main` that hasn't been pushed to the version
those files declare (`StaleMainError`) - by design, per Task 049's own
"read-only from Task 049's perspective" boundary (`docs/07-decisions.md`,
2026-07-19 Task 049 entry). `prepare_release()` necessarily leaves the
checkout with exactly those two files modified and uncommitted. Because
`ReleaseRunPlan.target_version` is anchored to the latest *published*
Release rather than to local file content, it stays stable across
invocations even after a local `prepare_release()` write - so a later
invocation can compare "does the local checkout already, cleanly, hold
`target_version`" and, when true, skip preparation entirely and proceed
straight to a real publish. The first invocation against a fresh checkout
prepares (exactly once) and reports `PREPARED_PENDING_COMMIT` - not a
failure - because the freshly prepared files are still uncommitted; the
operator commits and pushes them out of band, then a second invocation
(same `recommendation`) recognizes the checkout already matches the
(unchanged, since nothing new published) target and completes publication
for real. Re-running with a *different* `recommendation` before committing
is still safe: it simply re-derives a different target from the same
published baseline, never compounding an uncommitted bump. Adding a new
`git commit`/`git push` capability to close this in one call was
considered and rejected - it is a new, high-blast-radius, hard-to-reverse
capability nowhere authorized by this Task's Contract or Specification.
The workflow route needs no such resumption: Task 037 computes its version
from Git/PR history, and prepared local files are never consumed by it, so
whether preparation is required is unconditionally re-derived by
`prepare_release()` on every real workflow-route invocation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from devbot.github_client import GitHubClient, GitHubClientError
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig
from devbot.release_classification import ReleaseRecommendation
from devbot.release_ops import PublishOutcome as WorkflowPublishOutcome
from devbot.release_ops import (
    ReleaseOpsError,
    ReleasePreview,
    build_release_status,
    fetch_release_preview,
    local_checkout_is_dirty,
    publish_release,
)
from devbot.release_preparation import (
    ReleasePreparationError,
    ReleasePreparationResult,
    calculate_next_version,
    plan_release_preparation,
    prepare_release,
    read_current_version,
)
from devbot.release_publish import (
    DirtyWorktreeError,
    ReleasePublishError,
    ReleasePublishResult,
    StaleMainError,
    publish_prepared_release,
)
from devbot.release_publish_strategy import (
    ReleasePublishStrategy,
    ReleasePublishStrategyError,
    resolve_release_publish_strategy,
)
from devbot.startup import resolve_operator_checkout

_NO_PRIOR_RELEASE_BASELINE = "0.0.0"


class ReleaseRunStage(StrEnum):
    RECOMMENDATION = "recommendation"
    PREPARATION = "preparation"
    STRATEGY_RESOLUTION = "strategy_resolution"
    WORKFLOW_PUBLISH = "workflow_publish"
    DIRECT_PUBLISH = "direct_publish"


class ReleaseRunOutcome(StrEnum):
    WORKFLOW_PUBLISHED = "workflow_published"
    DIRECT_PUBLISHED = "direct_published"
    PREPARED_PENDING_COMMIT = "prepared_pending_commit"


@dataclass(frozen=True)
class ReleaseRunPlan:
    repository: str
    recommendation: ReleaseRecommendation
    current_version: str
    target_version: str
    effective_strategy: ReleasePublishStrategy
    preparation_required: bool
    publish_route: ReleaseRunStage
    direct_notes_available: bool
    # None for the direct route (not applicable) or when the workflow
    # route's independently computed increment matches `recommendation`.
    # Otherwise, a human-readable explanation - real execution refuses
    # before any write when this is set; dry-run reports it as a blocker.
    recommendation_conflict: str | None = None


@dataclass(frozen=True)
class ReleaseRunResult:
    plan: ReleaseRunPlan
    outcome: ReleaseRunOutcome
    preparation: ReleasePreparationResult | None = None
    workflow_outcome: WorkflowPublishOutcome | None = None
    direct_result: ReleasePublishResult | None = None


class ReleaseOrchestrationError(RuntimeError):
    """Base for a release run that cannot safely proceed. Always carries the
    stable stage identifier of whichever step raised it."""

    stage: ReleaseRunStage

    def __init__(self, stage: ReleaseRunStage, message: str) -> None:
        super().__init__(message)
        self.stage = stage


class NoReleaseRequiredError(ReleaseOrchestrationError):
    """`recommendation` is `none` - refused before Task 048 planning ever
    runs, before any write."""

    def __init__(self) -> None:
        super().__init__(
            ReleaseRunStage.RECOMMENDATION,
            "recommendation is 'none' - there is nothing to release",
        )


class MissingDirectReleaseNotesError(ReleaseOrchestrationError):
    """The effective strategy is `direct` but no non-empty release notes
    were supplied - refused before any preparation write."""

    def __init__(self, repository: str) -> None:
        super().__init__(
            ReleaseRunStage.DIRECT_PUBLISH,
            f"{repository}: the effective publish strategy is 'direct', which "
            "requires non-empty release notes via --notes-file before any "
            "preparation write",
        )


class ReleaseRunStageError(ReleaseOrchestrationError):
    """Wraps a downstream failure from `preparation`, `strategy_resolution`,
    `workflow_publish`, or `direct_publish`. Always raised with `from exc`,
    so the original exception remains available via `__cause__`."""


def _workflow_recommendation_conflict(
    preview: ReleasePreview, recommendation: ReleaseRecommendation
) -> str | None:
    if preview.increment is None:
        return (
            "no eligible merged Pull Requests since the last stable release - "
            f"nothing matches the requested recommendation {recommendation.value!r}"
        )
    if preview.increment != recommendation.value:
        return (
            f"the release increment computed from merged PR history is "
            f"{preview.increment!r}, not {recommendation.value!r} as requested via "
            "--level - refusing to publish an increment other than what was requested"
        )
    return None


def build_release_run_plan(
    github_client: GitHubClient,
    repository: RepositoryConfig,
    recommendation: ReleaseRecommendation,
    *,
    notes: str | None = None,
    local_checkout_path: Path | None = None,
) -> ReleaseRunPlan:
    """Read-only planning: never writes. Does perform read-only GitHub
    calls (unlike Task 048/050 alone) - see module docstring for why the
    plan cannot be trustworthy without them. Reuses Task 048's
    `plan_release_preparation` (local-file validation), Task 037's
    `fetch_release_preview` (workflow route) or `build_release_status`
    (direct route) for a GitHub-anchored version baseline, and Task 050's
    `resolve_release_publish_strategy`. Missing direct-route notes and a
    workflow-route recommendation conflict are both reported as plan
    fields, not raised - dry-run reports them as blockers rather than
    refusing to render a plan."""
    if recommendation is ReleaseRecommendation.NONE:
        raise NoReleaseRequiredError()

    checkout = local_checkout_path or resolve_operator_checkout()

    try:
        local_plan = plan_release_preparation(checkout, recommendation)
    except ReleasePreparationError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.PREPARATION, str(exc)) from exc

    try:
        resolution = resolve_release_publish_strategy(repository)
    except ReleasePublishStrategyError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.STRATEGY_RESOLUTION, str(exc)) from exc

    if resolution.effective is ReleasePublishStrategy.WORKFLOW:
        try:
            preview = fetch_release_preview(github_client, repository, local_checkout_path=checkout)
        except GitHubClientError as exc:
            raise ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, str(exc)) from exc

        return ReleaseRunPlan(
            repository=repository.full_name,
            recommendation=recommendation,
            current_version=preview.previous_version,
            target_version=preview.next_version,
            effective_strategy=resolution.effective,
            preparation_required=True,
            publish_route=ReleaseRunStage.WORKFLOW_PUBLISH,
            direct_notes_available=bool(notes and notes.strip()),
            recommendation_conflict=_workflow_recommendation_conflict(preview, recommendation),
        )

    try:
        status = build_release_status(github_client, repository)
    except GitHubClientError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.DIRECT_PUBLISH, str(exc)) from exc

    baseline = status.latest_stable_version or _NO_PRIOR_RELEASE_BASELINE
    try:
        target_version = calculate_next_version(baseline, recommendation)
    except ReleasePreparationError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.DIRECT_PUBLISH, str(exc)) from exc

    already_prepared = (
        local_plan.old_version == target_version and not local_checkout_is_dirty(checkout)
    )

    return ReleaseRunPlan(
        repository=repository.full_name,
        recommendation=recommendation,
        current_version=baseline,
        target_version=target_version,
        effective_strategy=resolution.effective,
        preparation_required=not already_prepared,
        publish_route=ReleaseRunStage.DIRECT_PUBLISH,
        direct_notes_available=bool(notes and notes.strip()),
    )


def run_release(
    github_client: GitHubClient,
    write_client: GitHubWriteClient,
    repository: RepositoryConfig,
    recommendation: ReleaseRecommendation,
    *,
    notes: str | None = None,
    local_checkout_path: Path | None = None,
    poll_interval_seconds: float = 15.0,
    timeout_seconds: float = 1800.0,
) -> ReleaseRunResult:
    """Real execution: (1) validate recommendation, (2) build and validate
    the preparation plan, (3) resolve the publish strategy - all three via
    `build_release_run_plan` - then (4)/(5) invoke exactly one existing
    publish path, (6) return a typed result. No publish function ever runs
    if preparation fails, and the workflow route refuses before any write
    if `recommendation` does not match the independently computed
    increment."""
    checkout = local_checkout_path or resolve_operator_checkout()
    plan = build_release_run_plan(
        github_client, repository, recommendation, notes=notes, local_checkout_path=checkout
    )

    if plan.publish_route is ReleaseRunStage.DIRECT_PUBLISH and not plan.direct_notes_available:
        raise MissingDirectReleaseNotesError(repository.full_name)

    if plan.publish_route is ReleaseRunStage.WORKFLOW_PUBLISH:
        # Re-fetch immediately before dispatch (not reused from `plan`) so
        # the checkout is still guaranteed clean at fetch time, and any
        # merge/label change between planning and now is caught rather
        # than dispatching a stale decision.
        try:
            preview = fetch_release_preview(github_client, repository, local_checkout_path=checkout)
        except GitHubClientError as exc:
            raise ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, str(exc)) from exc

        conflict = _workflow_recommendation_conflict(preview, recommendation)
        if conflict is not None:
            raise ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, conflict)

        try:
            preparation_result = prepare_release(checkout, recommendation)
        except ReleasePreparationError as exc:
            raise ReleaseRunStageError(ReleaseRunStage.PREPARATION, str(exc)) from exc

        try:
            outcome = publish_release(
                github_client,
                write_client,
                repository,
                preview=preview,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
            )
        except (ReleaseOpsError, ReleasePublishStrategyError, GitHubClientError) as exc:
            raise ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, str(exc)) from exc
        return ReleaseRunResult(
            plan=plan,
            outcome=ReleaseRunOutcome.WORKFLOW_PUBLISHED,
            preparation=preparation_result,
            workflow_outcome=outcome,
        )

    # Direct route. `plan.target_version` is anchored to the latest
    # *published* Release (see module docstring), so this comparison
    # survives across separate invocations even after a prior invocation's
    # `prepare_release()` write - it is what makes resumption possible
    # without double-bumping.
    already_prepared = (
        not local_checkout_is_dirty(checkout)
        and read_current_version(checkout) == plan.target_version
    )

    preparation_result: ReleasePreparationResult | None = None
    if not already_prepared:
        try:
            preparation_result = prepare_release(checkout, recommendation)
        except ReleasePreparationError as exc:
            raise ReleaseRunStageError(ReleaseRunStage.PREPARATION, str(exc)) from exc
        if preparation_result.new_version != plan.target_version:
            raise ReleaseRunStageError(
                ReleaseRunStage.PREPARATION,
                f"prepared {preparation_result.new_version!r} but the plan's target "
                f"(anchored to the latest published release) was "
                f"{plan.target_version!r} - the local checkout was inconsistent with "
                "the plan; run 'devbot release run --dry-run' again to see the "
                "current plan",
            )

    try:
        direct_result = publish_prepared_release(
            github_client,
            write_client,
            repository,
            notes or "",
            local_checkout_path=checkout,
        )
    except (DirtyWorktreeError, StaleMainError):
        # Expected on the first invocation against a fresh checkout: the
        # `prepare_release` call above necessarily left the checkout with
        # an uncommitted version bump, and Task 049's publish refuses on
        # principle to treat an uncommitted or unpushed change as
        # publishable (see module docstring). The operator commits and
        # pushes the prepared files; a second real invocation with the
        # same `recommendation` then finds `already_prepared` true above
        # and completes publication for real.
        return ReleaseRunResult(
            plan=plan,
            outcome=ReleaseRunOutcome.PREPARED_PENDING_COMMIT,
            preparation=preparation_result,
        )
    except (
        ReleasePublishError,
        ReleasePublishStrategyError,
        ReleasePreparationError,
        GitHubClientError,
    ) as exc:
        raise ReleaseRunStageError(ReleaseRunStage.DIRECT_PUBLISH, str(exc)) from exc

    return ReleaseRunResult(
        plan=plan,
        outcome=ReleaseRunOutcome.DIRECT_PUBLISHED,
        preparation=preparation_result,
        direct_result=direct_result,
    )
