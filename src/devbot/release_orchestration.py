"""Task 051: Release Orchestration.

Composes the existing Task 047 recommendation type, Task 048 preparation,
Task 050 publish-strategy resolution, and exactly one of Task 037's
workflow-dispatch publish or Task 049's direct tag+Release publish, into
one operator-facing command (`devbot release run`) - without duplicating
any of those modules' policy or safety logic.

Architecture note - why the direct route cannot always complete in one
call: Task 049's `preview_release_publish`/`publish_prepared_release` treat
`pyproject.toml`/`uv.lock` as read-only and refuse on any uncommitted local
change (`DirtyWorktreeError`) or a `main` that hasn't been pushed to the
version those files declare (`StaleMainError`) - by design, per Task 049's
own "read-only from Task 049's perspective" boundary (`docs/07-decisions
.md`, 2026-07-19 Task 049 entry). Task 048's `prepare_release()`
necessarily leaves the checkout with exactly those two files modified and
uncommitted - `old_version != new_version` always, for every recommendation
this Task accepts. `run_release` therefore always prepares *before*
attempting a direct publish (publishing first would silently publish
whatever un-bumped version happened to already be committed, ignoring
`recommendation` entirely), but a publish immediately following a fresh
`prepare_release()` call will then always raise `DirtyWorktreeError`, with
no way to bypass it (unlike the workflow route, `publish_prepared_release`
has no "already-computed preview" injection point). Rather than add a new,
undocumented capability to this Task (committing/pushing to `main` on the
operator's behalf), `run_release` treats that specific refusal as expected,
not a failure: it reports `PREPARED_PENDING_COMMIT` - the operator commits
and pushes the prepared files out of band, then completes publication with
the existing `release publish-prepared` command (Task 049), not by
re-running this command (re-running would prepare, and therefore bump, a
second time). The workflow route needs no such dance: Task 037 computes
its version from Git/PR history, never from local files, so `run_release`
never writes local files for that route at all.
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
from devbot.release_ops import ReleaseOpsError, publish_release
from devbot.release_preparation import (
    ReleasePreparationError,
    ReleasePreparationResult,
    plan_release_preparation,
    prepare_release,
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


def build_release_run_plan(
    repository: RepositoryConfig,
    recommendation: ReleaseRecommendation,
    *,
    notes: str | None = None,
    local_checkout_path: Path | None = None,
) -> ReleaseRunPlan:
    """Pure read-only planning: reuses Task 048's `plan_release_preparation`
    and Task 050's `resolve_release_publish_strategy`. Never writes, never
    contacts GitHub. Missing direct-route notes are reported as
    `direct_notes_available=False`, not raised - dry-run reports this as a
    blocker rather than refusing to render a plan."""
    if recommendation is ReleaseRecommendation.NONE:
        raise NoReleaseRequiredError()

    checkout = local_checkout_path or resolve_operator_checkout()

    try:
        preparation_plan = plan_release_preparation(checkout, recommendation)
    except ReleasePreparationError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.PREPARATION, str(exc)) from exc

    try:
        resolution = resolve_release_publish_strategy(repository)
    except ReleasePublishStrategyError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.STRATEGY_RESOLUTION, str(exc)) from exc

    route = (
        ReleaseRunStage.WORKFLOW_PUBLISH
        if resolution.effective is ReleasePublishStrategy.WORKFLOW
        else ReleaseRunStage.DIRECT_PUBLISH
    )

    return ReleaseRunPlan(
        repository=repository.full_name,
        recommendation=recommendation,
        current_version=preparation_plan.old_version,
        target_version=preparation_plan.new_version,
        effective_strategy=resolution.effective,
        preparation_required=preparation_plan.old_version != preparation_plan.new_version,
        publish_route=route,
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
    if preparation fails."""
    checkout = local_checkout_path or resolve_operator_checkout()
    plan = build_release_run_plan(
        repository, recommendation, notes=notes, local_checkout_path=checkout
    )

    if plan.publish_route is ReleaseRunStage.DIRECT_PUBLISH and not plan.direct_notes_available:
        raise MissingDirectReleaseNotesError(repository.full_name)

    if plan.publish_route is ReleaseRunStage.WORKFLOW_PUBLISH:
        try:
            outcome = publish_release(
                github_client,
                write_client,
                repository,
                local_checkout_path=checkout,
                poll_interval_seconds=poll_interval_seconds,
                timeout_seconds=timeout_seconds,
            )
        except (ReleaseOpsError, ReleasePublishStrategyError, GitHubClientError) as exc:
            raise ReleaseRunStageError(ReleaseRunStage.WORKFLOW_PUBLISH, str(exc)) from exc
        return ReleaseRunResult(
            plan=plan,
            outcome=ReleaseRunOutcome.WORKFLOW_PUBLISHED,
            workflow_outcome=outcome,
        )

    # Preparation must run before publish is even attempted, never after:
    # `publish_prepared_release` treats whatever is currently committed as
    # the version to publish, with no knowledge of `recommendation` at all.
    # Attempting publish first (and only preparing as a fallback) would
    # silently publish the current, un-bumped version whenever the checkout
    # happened to start clean - ignoring `recommendation` entirely.
    try:
        preparation_result = prepare_release(checkout, recommendation)
    except ReleasePreparationError as exc:
        raise ReleaseRunStageError(ReleaseRunStage.PREPARATION, str(exc)) from exc

    try:
        direct_result = publish_prepared_release(
            github_client,
            write_client,
            repository,
            notes or "",
            local_checkout_path=checkout,
        )
    except (DirtyWorktreeError, StaleMainError):
        # Expected, not a failure: `prepare_release` above necessarily left
        # the checkout with an uncommitted version bump, and Task 049's
        # publish refuses on principle to treat an uncommitted or unpushed
        # change as publishable (see module docstring). The operator
        # commits and pushes the prepared files, then completes publication
        # with the existing `release publish-prepared` command.
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
