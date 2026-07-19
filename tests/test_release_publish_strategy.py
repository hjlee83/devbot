from __future__ import annotations

from pathlib import Path

import pytest

from devbot.models import RepositoryConfig
from devbot.release_publish_strategy import (
    InvalidReleasePublishStrategyError,
    ReleasePublishStrategy,
    ReleasePublishStrategyError,
    ReleasePublishStrategyMismatchError,
    require_direct_strategy,
    require_workflow_strategy,
    resolve_release_publish_strategy,
)


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


# --------------------------------------------------------------------------
# resolve_release_publish_strategy
# --------------------------------------------------------------------------


def test_omitted_field_defaults_to_workflow() -> None:
    resolution = resolve_release_publish_strategy(_repository())

    assert resolution.effective is ReleasePublishStrategy.WORKFLOW
    assert resolution.configured is None
    assert resolution.defaulted is True
    assert resolution.repository == "someone/myrepo"


def test_explicit_workflow_resolution() -> None:
    resolution = resolve_release_publish_strategy(_repository(publish_strategy="workflow"))

    assert resolution.effective is ReleasePublishStrategy.WORKFLOW
    assert resolution.configured is ReleasePublishStrategy.WORKFLOW
    assert resolution.defaulted is False


def test_explicit_direct_resolution() -> None:
    resolution = resolve_release_publish_strategy(_repository(publish_strategy="direct"))

    assert resolution.effective is ReleasePublishStrategy.DIRECT
    assert resolution.configured is ReleasePublishStrategy.DIRECT
    assert resolution.defaulted is False


def test_unknown_value_rejected() -> None:
    with pytest.raises(InvalidReleasePublishStrategyError):
        resolve_release_publish_strategy(_repository(publish_strategy="bogus"))


@pytest.mark.parametrize("malformed", ["", "Workflow", "DIRECT", "workflow ", " direct", "auto"])
def test_malformed_string_values_rejected(malformed: str) -> None:
    with pytest.raises(InvalidReleasePublishStrategyError):
        resolve_release_publish_strategy(_repository(publish_strategy=malformed))


def test_malformed_non_string_value_rejected() -> None:
    # RepositoryConfig is a plain dataclass with no runtime type
    # enforcement - a caller that bypasses config.py's str() cast (e.g. a
    # future loader bug) must still be rejected, not silently coerced.
    with pytest.raises(InvalidReleasePublishStrategyError):
        resolve_release_publish_strategy(_repository(publish_strategy=123))  # type: ignore[arg-type]


def test_invalid_strategy_error_identifies_repository_and_value() -> None:
    with pytest.raises(InvalidReleasePublishStrategyError) as excinfo:
        resolve_release_publish_strategy(_repository(publish_strategy="bogus"))

    message = str(excinfo.value)
    assert "someone/myrepo" in message
    assert "bogus" in message


def test_all_errors_are_release_publish_strategy_errors() -> None:
    for error_type in (InvalidReleasePublishStrategyError, ReleasePublishStrategyMismatchError):
        assert issubclass(error_type, ReleasePublishStrategyError)


def test_resolution_is_deterministic() -> None:
    repository = _repository(publish_strategy="direct")

    first = resolve_release_publish_strategy(repository)
    second = resolve_release_publish_strategy(repository)

    assert first == second


# --------------------------------------------------------------------------
# Guards: workflow accepts only workflow, direct accepts only direct
# --------------------------------------------------------------------------


def test_workflow_guard_accepts_workflow() -> None:
    resolution = require_workflow_strategy(_repository(publish_strategy="workflow"))

    assert resolution.effective is ReleasePublishStrategy.WORKFLOW


def test_workflow_guard_accepts_omitted_default() -> None:
    resolution = require_workflow_strategy(_repository())

    assert resolution.effective is ReleasePublishStrategy.WORKFLOW
    assert resolution.defaulted is True


def test_workflow_guard_rejects_direct() -> None:
    with pytest.raises(ReleasePublishStrategyMismatchError):
        require_workflow_strategy(_repository(publish_strategy="direct"))


def test_direct_guard_accepts_direct() -> None:
    resolution = require_direct_strategy(_repository(publish_strategy="direct"))

    assert resolution.effective is ReleasePublishStrategy.DIRECT


def test_direct_guard_rejects_workflow() -> None:
    with pytest.raises(ReleasePublishStrategyMismatchError):
        require_direct_strategy(_repository(publish_strategy="workflow"))


def test_direct_guard_rejects_omitted_default() -> None:
    with pytest.raises(ReleasePublishStrategyMismatchError):
        require_direct_strategy(_repository())


def test_invalid_configuration_fails_both_guards_before_a_mismatch_is_even_considered() -> None:
    repository = _repository(publish_strategy="bogus")

    with pytest.raises(InvalidReleasePublishStrategyError):
        require_workflow_strategy(repository)
    with pytest.raises(InvalidReleasePublishStrategyError):
        require_direct_strategy(repository)


# --------------------------------------------------------------------------
# FR-008: Mutual exclusivity matrix - exactly one guard ever accepts a
# given valid repository configuration.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("configured_strategy", [None, "workflow", "direct"])
def test_mutual_exclusivity_matrix(configured_strategy: str | None) -> None:
    repository = _repository(publish_strategy=configured_strategy)

    workflow_accepted = True
    direct_accepted = True
    try:
        require_workflow_strategy(repository)
    except ReleasePublishStrategyMismatchError:
        workflow_accepted = False
    try:
        require_direct_strategy(repository)
    except ReleasePublishStrategyMismatchError:
        direct_accepted = False

    assert workflow_accepted != direct_accepted, (
        f"exactly one guard must accept publish_strategy={configured_strategy!r}, "
        f"got workflow_accepted={workflow_accepted}, direct_accepted={direct_accepted}"
    )
