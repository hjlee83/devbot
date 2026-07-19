"""Task 047: Release Classification Policy.

A pure policy boundary that consumes Task 046's typed
`ContractParseResult` and recommends a release level - `major`, `minor`,
`patch`, or `none` - without reading Contract Markdown, mutating any
version file, creating a Git tag or GitHub Release, or generating release
notes. This is a recommendation only; execution belongs to a future
release task.

**Precedence**, evaluated highest to lowest (Task 047 Required Behaviour):

1. Legacy Contract -> `LegacyContractClassificationError` (never guessed).
2. Native result without metadata -> `MissingContractMetadataError`.
3. `compatibility == BREAKING` -> `major`.
4. `migration == REQUIRED` -> `major`.
5. `release_impact == BREAKING` -> `major`.
6. `release_impact == FEATURE` -> `minor`.
7. `release_impact == FIX` -> `patch`.
8. `release_impact` in `DOCS`, `INTERNAL`, `NONE` -> `none`.

`risk_level` and `specification_type` are intentionally never consulted -
`classify_release` never reads either field, so they can never change the
recommendation even though they exist on `ContractMetadata`.
"""

from __future__ import annotations

from enum import StrEnum

from devbot.contract_metadata import (
    Compatibility,
    ContractKind,
    ContractParseResult,
    Migration,
    ReleaseImpact,
)


class ReleaseRecommendation(StrEnum):
    MAJOR = "major"
    MINOR = "minor"
    PATCH = "patch"
    NONE = "none"


class ReleaseClassificationError(RuntimeError):
    """Base for a `ContractParseResult` that cannot be safely classified."""


class LegacyContractClassificationError(ReleaseClassificationError):
    """The Contract is legacy (no `Contract Version`) - there is no Schema
    v1 metadata to classify, and no classification is ever guessed for
    it."""


class MissingContractMetadataError(ReleaseClassificationError):
    """The result claims `ContractKind.SCHEMA_V1` but has no metadata -
    an invariant Task 046 itself is expected to uphold; fails closed
    rather than silently classifying an incomplete result."""


_RELEASE_IMPACT_TO_RECOMMENDATION: dict[ReleaseImpact, ReleaseRecommendation] = {
    ReleaseImpact.BREAKING: ReleaseRecommendation.MAJOR,
    ReleaseImpact.FEATURE: ReleaseRecommendation.MINOR,
    ReleaseImpact.FIX: ReleaseRecommendation.PATCH,
    ReleaseImpact.DOCS: ReleaseRecommendation.NONE,
    ReleaseImpact.INTERNAL: ReleaseRecommendation.NONE,
    ReleaseImpact.NONE: ReleaseRecommendation.NONE,
}


def classify_release(result: ContractParseResult) -> ReleaseRecommendation:
    """Pure: no filesystem, network, GitHub, or version-mutation side
    effects. Consumes Task 046's typed `ContractParseResult` only - never
    re-parses Contract Markdown."""
    if result.kind is ContractKind.LEGACY:
        raise LegacyContractClassificationError(
            "legacy Contracts declare no Contract Schema v1 metadata to classify"
        )
    if result.metadata is None:
        raise MissingContractMetadataError(
            "native Schema v1 result has no metadata to classify"
        )

    metadata = result.metadata
    if metadata.compatibility is Compatibility.BREAKING:
        return ReleaseRecommendation.MAJOR
    if metadata.migration is Migration.REQUIRED:
        return ReleaseRecommendation.MAJOR
    return _RELEASE_IMPACT_TO_RECOMMENDATION[metadata.release_impact]
