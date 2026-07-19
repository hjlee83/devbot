"""Task 050: Release Publish Strategy.

DevBot has two independently valid release-publish mechanisms: Task 037's
`devbot release publish` (dispatches `.github/workflows/release.yml`) and
Task 049's `devbot release publish-prepared` (creates and pushes a Git tag,
then creates a GitHub Release directly). Without a single repository-level
policy, both could be treated as simultaneously valid for the same
repository - this module is that policy boundary.

**One central resolver.** `resolve_release_publish_strategy` is the only
place that interprets `RepositoryConfig.publish_strategy` (a raw,
unvalidated string set by the config loader) and applies the safe default.
Neither existing publish entry point parses or defaults this value itself.

**Safe default, fail closed.** An omitted `publish_strategy` resolves to
`workflow` - the historically existing, CI-based path - so every repository
that predates Task 050 keeps behaving exactly as before. Any configured
value other than the two canonical strings (`workflow`/`direct`) raises
`InvalidReleasePublishStrategyError` rather than silently defaulting or
guessing.

**Mutual exclusivity.** `require_workflow_strategy`/`require_direct_strategy`
are the only two ways a caller can confirm it is allowed to proceed. For any
given repository configuration, at most one of them succeeds - never both -
so `release_ops.publish_release` and `release_publish.publish_prepared_release`
can never both act as a valid route for the same repository at the same
time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from devbot.models import RepositoryConfig


class ReleasePublishStrategy(StrEnum):
    WORKFLOW = "workflow"
    DIRECT = "direct"


@dataclass(frozen=True)
class ReleasePublishStrategyResolution:
    repository: str
    configured: ReleasePublishStrategy | None
    effective: ReleasePublishStrategy
    defaulted: bool


class ReleasePublishStrategyError(RuntimeError):
    """Base for a release publish strategy that cannot be safely resolved
    or that a caller is not authorized to proceed under."""


class InvalidReleasePublishStrategyError(ReleasePublishStrategyError):
    """`RepositoryConfig.publish_strategy` is configured but is not one of
    the canonical values (`workflow`/`direct`) - never silently defaulted
    or guessed."""


class ReleasePublishStrategyMismatchError(ReleasePublishStrategyError):
    """The repository's effective strategy is not the one a given publish
    entry point requires - raised before any Git or GitHub write."""


_CANONICAL_VALUES = frozenset(member.value for member in ReleasePublishStrategy)


def resolve_release_publish_strategy(
    repository: RepositoryConfig,
) -> ReleasePublishStrategyResolution:
    """Pure: the single central resolver every publish entry point and the
    read-only inspection command must use. Never performs I/O.

    An omitted (`None`) `publish_strategy` resolves to `workflow` with
    `defaulted=True`. A configured value that is not exactly `"workflow"`
    or `"direct"` (wrong type, unknown string, or anything else) raises
    `InvalidReleasePublishStrategyError` - there is no fallback from an
    invalid explicit configuration back to `workflow`."""
    raw = repository.publish_strategy
    if raw is None:
        return ReleasePublishStrategyResolution(
            repository=repository.full_name,
            configured=None,
            effective=ReleasePublishStrategy.WORKFLOW,
            defaulted=True,
        )

    if not isinstance(raw, str) or raw not in _CANONICAL_VALUES:
        raise InvalidReleasePublishStrategyError(
            f"{repository.full_name}: publish_strategy must be one of "
            f"{sorted(_CANONICAL_VALUES)}, got {raw!r}"
        )

    effective = ReleasePublishStrategy(raw)
    return ReleasePublishStrategyResolution(
        repository=repository.full_name,
        configured=effective,
        effective=effective,
        defaulted=False,
    )


def _require_strategy(
    repository: RepositoryConfig, expected: ReleasePublishStrategy
) -> ReleasePublishStrategyResolution:
    resolution = resolve_release_publish_strategy(repository)
    if resolution.effective is not expected:
        raise ReleasePublishStrategyMismatchError(
            f"{repository.full_name}: effective release publish strategy is "
            f"{resolution.effective.value!r}, but this command requires "
            f"{expected.value!r} (configure `publish_strategy: {expected.value}` "
            f"in repositories.yaml, or use the matching command)"
        )
    return resolution


def require_workflow_strategy(repository: RepositoryConfig) -> ReleasePublishStrategyResolution:
    """Raises `ReleasePublishStrategyMismatchError` unless `repository`'s
    effective strategy is `workflow`. Task 037's `release_ops.publish_release`
    must call this before dispatching the release workflow or performing any
    other GitHub write."""
    return _require_strategy(repository, ReleasePublishStrategy.WORKFLOW)


def require_direct_strategy(repository: RepositoryConfig) -> ReleasePublishStrategyResolution:
    """Raises `ReleasePublishStrategyMismatchError` unless `repository`'s
    effective strategy is `direct`. Task 049's
    `release_publish.publish_prepared_release` must call this before
    creating a Git tag, pushing it, or creating a GitHub Release."""
    return _require_strategy(repository, ReleasePublishStrategy.DIRECT)
