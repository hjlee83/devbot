from __future__ import annotations

from pathlib import Path
from types import MappingProxyType

import pytest

from devbot.contract_metadata import (
    Compatibility,
    ContractKind,
    ContractMetadata,
    ContractParseResult,
    ContractProvenance,
    Migration,
    ReleaseImpact,
    RiskLevel,
    SpecificationType,
    parse_contract_metadata,
)
from devbot.release_classification import (
    LegacyContractClassificationError,
    MissingContractMetadataError,
    ReleaseClassificationError,
    ReleaseRecommendation,
    classify_release,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_NON_MAJOR_IMPACTS = (
    ReleaseImpact.FEATURE,
    ReleaseImpact.FIX,
    ReleaseImpact.DOCS,
    ReleaseImpact.INTERNAL,
    ReleaseImpact.NONE,
)


def _metadata(**overrides: object) -> ContractMetadata:
    defaults: dict[str, object] = dict(
        specification_type=SpecificationType.FEATURE,
        release_impact=ReleaseImpact.NONE,
        risk_level=RiskLevel.LOW,
        compatibility=Compatibility.BACKWARD,
        migration=Migration.NONE,
        extensions=MappingProxyType({}),
    )
    defaults.update(overrides)
    return ContractMetadata(**defaults)  # type: ignore[arg-type]


def _result(**overrides: object) -> ContractParseResult:
    metadata = overrides.pop("metadata", _metadata())
    defaults: dict[str, object] = dict(
        kind=ContractKind.SCHEMA_V1,
        task_id="099",
        title="Sample",
        contract_version=1,
        provenance=ContractProvenance(github_issue="#1", branch="task/099-sample"),
        metadata=metadata,
    )
    defaults.update(overrides)
    return ContractParseResult(**defaults)  # type: ignore[arg-type]


def _legacy_result() -> ContractParseResult:
    return ContractParseResult(
        kind=ContractKind.LEGACY,
        task_id="099",
        title="Sample",
        contract_version=None,
        provenance=None,
        metadata=None,
    )


# --------------------------------------------------------------------------
# Every canonical release_impact value
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("impact", "expected"),
    [
        (ReleaseImpact.BREAKING, ReleaseRecommendation.MAJOR),
        (ReleaseImpact.FEATURE, ReleaseRecommendation.MINOR),
        (ReleaseImpact.FIX, ReleaseRecommendation.PATCH),
        (ReleaseImpact.DOCS, ReleaseRecommendation.NONE),
        (ReleaseImpact.INTERNAL, ReleaseRecommendation.NONE),
        (ReleaseImpact.NONE, ReleaseRecommendation.NONE),
    ],
)
def test_each_release_impact_value_maps_correctly(
    impact: ReleaseImpact, expected: ReleaseRecommendation
) -> None:
    result = _result(metadata=_metadata(release_impact=impact))

    assert classify_release(result) is expected


# --------------------------------------------------------------------------
# Breaking overrides: compatibility=breaking and migration=required both
# take precedence over release_impact, for every non-major impact.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("impact", _NON_MAJOR_IMPACTS)
def test_compatibility_breaking_overrides_every_non_major_impact(impact: ReleaseImpact) -> None:
    result = _result(
        metadata=_metadata(release_impact=impact, compatibility=Compatibility.BREAKING)
    )

    assert classify_release(result) is ReleaseRecommendation.MAJOR


@pytest.mark.parametrize("impact", _NON_MAJOR_IMPACTS)
def test_migration_required_overrides_every_non_major_impact(impact: ReleaseImpact) -> None:
    result = _result(metadata=_metadata(release_impact=impact, migration=Migration.REQUIRED))

    assert classify_release(result) is ReleaseRecommendation.MAJOR


def test_multiple_major_conditions_at_once_still_yields_major() -> None:
    result = _result(
        metadata=_metadata(
            release_impact=ReleaseImpact.FIX,
            compatibility=Compatibility.BREAKING,
            migration=Migration.REQUIRED,
        )
    )

    assert classify_release(result) is ReleaseRecommendation.MAJOR


def test_compatibility_backward_and_migration_none_do_not_force_major() -> None:
    result = _result(
        metadata=_metadata(
            release_impact=ReleaseImpact.FIX,
            compatibility=Compatibility.BACKWARD,
            migration=Migration.NONE,
        )
    )

    assert classify_release(result) is ReleaseRecommendation.PATCH


# --------------------------------------------------------------------------
# Legacy input / missing metadata: dedicated errors, never guessed.
# --------------------------------------------------------------------------


def test_legacy_input_raises_dedicated_error() -> None:
    with pytest.raises(LegacyContractClassificationError):
        classify_release(_legacy_result())


def test_legacy_classification_error_is_a_release_classification_error() -> None:
    assert issubclass(LegacyContractClassificationError, ReleaseClassificationError)


def test_native_result_with_missing_metadata_raises() -> None:
    result = _result(metadata=None)

    with pytest.raises(MissingContractMetadataError):
        classify_release(result)


def test_missing_metadata_error_is_a_release_classification_error() -> None:
    assert issubclass(MissingContractMetadataError, ReleaseClassificationError)


# --------------------------------------------------------------------------
# Determinism
# --------------------------------------------------------------------------


def test_deterministic_repeated_classification() -> None:
    result = _result(metadata=_metadata(release_impact=ReleaseImpact.FEATURE))

    assert classify_release(result) == classify_release(result)


# --------------------------------------------------------------------------
# risk_level and specification_type must never alter the recommendation.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("risk_level", list(RiskLevel))
@pytest.mark.parametrize("specification_type", list(SpecificationType))
def test_risk_level_and_specification_type_never_alter_recommendation(
    specification_type: SpecificationType, risk_level: RiskLevel
) -> None:
    result = _result(
        metadata=_metadata(
            release_impact=ReleaseImpact.FEATURE,
            risk_level=risk_level,
            specification_type=specification_type,
        )
    )

    assert classify_release(result) is ReleaseRecommendation.MINOR


# --------------------------------------------------------------------------
# Recommendation values are the canonical lowercase tokens.
# --------------------------------------------------------------------------


def test_release_recommendation_values_are_canonical_tokens() -> None:
    assert ReleaseRecommendation.MAJOR.value == "major"
    assert ReleaseRecommendation.MINOR.value == "minor"
    assert ReleaseRecommendation.PATCH.value == "patch"
    assert ReleaseRecommendation.NONE.value == "none"


# --------------------------------------------------------------------------
# End-to-end with real, on-disk Task Contracts (via Task 046's parser -
# classify_release itself never touches Markdown).
# --------------------------------------------------------------------------


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def test_real_schema_v1_contract_classifies_end_to_end() -> None:
    parsed = parse_contract_metadata(_read("tasks/047-release-classification-policy.md"))

    recommendation = classify_release(parsed)

    assert recommendation is ReleaseRecommendation.NONE  # release_impact: internal


def test_real_legacy_contract_raises_end_to_end() -> None:
    parsed = parse_contract_metadata(_read("tasks/037-release-operator-ux.md"))

    with pytest.raises(LegacyContractClassificationError):
        classify_release(parsed)
