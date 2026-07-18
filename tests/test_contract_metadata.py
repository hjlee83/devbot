from __future__ import annotations

from pathlib import Path

import pytest

from devbot.contract_metadata import (
    Compatibility,
    ContractKind,
    ContractMetadataError,
    DuplicateMetadataFieldError,
    DuplicateProvenanceFieldError,
    DuplicateSectionError,
    InvalidMetadataValueError,
    MalformedContractVersionError,
    MalformedProvenanceFieldError,
    Migration,
    MissingMetadataFieldError,
    MissingProvenanceFieldError,
    MissingSectionError,
    ReleaseImpact,
    RiskLevel,
    SpecificationType,
    TaskIdentityMismatchError,
    UnknownMetadataFieldError,
    UnsupportedContractVersionError,
    parse_contract_metadata,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_CONTRACT = """# Task 099 — Sample

## Contract Version

1

## Provenance

- GitHub Issue: #1
- Branch: `task/099-sample`

## Task Identity

- id: 099
- title: Sample

## Metadata

- specification_type: feature
- release_impact: internal
- risk_level: low
- compatibility: backward
- migration: none

## Goal

g

## Context

c

## Scope

s

## Out of Scope

o

## Deliverables

d

## Acceptance Criteria

a

## Quality Gates

q

## Handoff

h
"""


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Valid Schema v1 Contract
# --------------------------------------------------------------------------


def test_valid_schema_v1_contract_parses_deterministically() -> None:
    first = parse_contract_metadata(_BASE_CONTRACT)
    second = parse_contract_metadata(_BASE_CONTRACT)

    assert first == second
    assert first.kind is ContractKind.SCHEMA_V1
    assert first.task_id == "099"
    assert first.title == "Sample"
    assert first.contract_version == 1
    assert first.provenance is not None
    assert first.provenance.github_issue == "#1"
    assert first.provenance.branch == "task/099-sample"
    assert first.metadata is not None
    assert first.metadata.specification_type is SpecificationType.FEATURE
    assert first.metadata.extensions == {}


def test_real_schema_v1_contract_046_parses_natively() -> None:
    result = parse_contract_metadata(_read("tasks/046-contract-metadata-engine.md"))

    assert result.kind is ContractKind.SCHEMA_V1
    assert result.task_id == "046"
    assert result.title == "Contract Metadata Engine"
    assert result.contract_version == 1
    assert result.provenance is not None
    assert result.provenance.github_issue == "#96"
    assert result.provenance.branch == "task/046-contract-metadata-engine"
    assert result.metadata is not None
    assert result.metadata.specification_type is SpecificationType.FEATURE
    assert result.metadata.release_impact is ReleaseImpact.INTERNAL
    assert result.metadata.risk_level is RiskLevel.MEDIUM
    assert result.metadata.compatibility is Compatibility.BACKWARD
    assert result.metadata.migration is Migration.NONE
    assert result.metadata.extensions == {}


@pytest.mark.parametrize("separator", ["—", ":", "-"])
def test_title_separator_variants_all_parse(separator: str) -> None:
    text = _BASE_CONTRACT.replace("# Task 099 — Sample", f"# Task 099 {separator} Sample")

    result = parse_contract_metadata(text)

    assert result.task_id == "099"
    assert result.title == "Sample"


# --------------------------------------------------------------------------
# Every canonical enum family
# --------------------------------------------------------------------------


@pytest.mark.parametrize("value", list(SpecificationType))
def test_every_specification_type_value_parses(value: SpecificationType) -> None:
    text = _BASE_CONTRACT.replace(
        "specification_type: feature", f"specification_type: {value.value}"
    )

    result = parse_contract_metadata(text)

    assert result.metadata.specification_type is value


@pytest.mark.parametrize("value", list(ReleaseImpact))
def test_every_release_impact_value_parses(value: ReleaseImpact) -> None:
    text = _BASE_CONTRACT.replace("release_impact: internal", f"release_impact: {value.value}")

    result = parse_contract_metadata(text)

    assert result.metadata.release_impact is value


@pytest.mark.parametrize("value", list(RiskLevel))
def test_every_risk_level_value_parses(value: RiskLevel) -> None:
    text = _BASE_CONTRACT.replace("risk_level: low", f"risk_level: {value.value}")

    result = parse_contract_metadata(text)

    assert result.metadata.risk_level is value


@pytest.mark.parametrize("value", list(Compatibility))
def test_every_compatibility_value_parses(value: Compatibility) -> None:
    text = _BASE_CONTRACT.replace("compatibility: backward", f"compatibility: {value.value}")

    result = parse_contract_metadata(text)

    assert result.metadata.compatibility is value


@pytest.mark.parametrize("value", list(Migration))
def test_every_migration_value_parses(value: Migration) -> None:
    text = _BASE_CONTRACT.replace("migration: none", f"migration: {value.value}")

    result = parse_contract_metadata(text)

    assert result.metadata.migration is value


def test_invalid_metadata_value_raises() -> None:
    text = _BASE_CONTRACT.replace("risk_level: low", "risk_level: extreme")

    with pytest.raises(InvalidMetadataValueError):
        parse_contract_metadata(text)


# --------------------------------------------------------------------------
# Contract Version: unsupported / malformed
# --------------------------------------------------------------------------


def test_unsupported_version_raises() -> None:
    text = _BASE_CONTRACT.replace("## Contract Version\n\n1", "## Contract Version\n\n2")

    with pytest.raises(UnsupportedContractVersionError):
        parse_contract_metadata(text)


@pytest.mark.parametrize("body", ["abc", "1.5", "-1", "0", "", "1 2"])
def test_malformed_version_raises(body: str) -> None:
    text = _BASE_CONTRACT.replace("## Contract Version\n\n1", f"## Contract Version\n\n{body}")

    with pytest.raises(MalformedContractVersionError):
        parse_contract_metadata(text)


def test_malformed_version_does_not_silently_fall_back_to_legacy() -> None:
    # Safety: once a `Contract Version` section exists at all, malformed
    # content must fail closed - it must never be treated as an absent
    # version (i.e. legacy).
    text = _BASE_CONTRACT.replace("## Contract Version\n\n1", "## Contract Version\n\nnot-a-number")

    with pytest.raises(MalformedContractVersionError):
        result = parse_contract_metadata(text)
        assert result.kind is not ContractKind.LEGACY  # unreachable if it raises, as expected


# --------------------------------------------------------------------------
# Required sections: missing / duplicate
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "removed_block",
    [
        "## Context\n\nc\n\n",
        "## Deliverables\n\nd\n\n",
        "## Quality Gates\n\nq\n\n",
    ],
)
def test_missing_required_section_raises(removed_block: str) -> None:
    text = _BASE_CONTRACT.replace(removed_block, "")

    with pytest.raises(MissingSectionError):
        parse_contract_metadata(text)


def test_duplicate_singleton_section_raises() -> None:
    text = _BASE_CONTRACT.replace("## Goal\n\ng\n\n", "## Goal\n\ng\n\n## Goal\n\ng2\n\n")

    with pytest.raises(DuplicateSectionError):
        parse_contract_metadata(text)


def test_duplicate_contract_version_section_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "## Contract Version\n\n1\n\n", "## Contract Version\n\n1\n\n## Contract Version\n\n1\n\n"
    )

    with pytest.raises(DuplicateSectionError):
        parse_contract_metadata(text)


def test_duplicate_references_section_raises() -> None:
    text = _BASE_CONTRACT + "\n## References\n\nr1\n\n## References\n\nr2\n"

    with pytest.raises(DuplicateSectionError):
        parse_contract_metadata(text)


def test_optional_references_section_is_accepted_once() -> None:
    text = _BASE_CONTRACT + "\n## References\n\n- some link\n"

    result = parse_contract_metadata(text)

    assert result.kind is ContractKind.SCHEMA_V1


def test_unknown_non_canonical_top_level_section_is_tolerated() -> None:
    # Task 045's own compatibility note: Schema v1 permits additional
    # non-canonical top-level sections (e.g. docs/09's Risk, Rollback
    # Strategy, ...) to coexist without being rejected.
    text = _BASE_CONTRACT.replace("## Goal\n\ng\n\n", "## Goal\n\ng\n\n## Risk\n\nsome risk\n\n")

    result = parse_contract_metadata(text)

    assert result.kind is ContractKind.SCHEMA_V1


# --------------------------------------------------------------------------
# Metadata fields: missing / duplicate / unknown
# --------------------------------------------------------------------------


def test_missing_metadata_field_raises() -> None:
    text = _BASE_CONTRACT.replace("- migration: none\n", "")

    with pytest.raises(MissingMetadataFieldError):
        parse_contract_metadata(text)


def test_duplicate_metadata_field_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "- migration: none\n", "- migration: none\n- migration: required\n"
    )

    with pytest.raises(DuplicateMetadataFieldError):
        parse_contract_metadata(text)


def test_unknown_metadata_field_raises() -> None:
    text = _BASE_CONTRACT.replace("- migration: none\n", "- migration: none\n- bogus_field: foo\n")

    with pytest.raises(UnknownMetadataFieldError):
        parse_contract_metadata(text)


# --------------------------------------------------------------------------
# `x-` extensions
# --------------------------------------------------------------------------


def test_valid_extension_is_preserved_separately() -> None:
    text = _BASE_CONTRACT.replace("- migration: none\n", "- migration: none\n- x-owner: alice\n")

    result = parse_contract_metadata(text)

    assert result.metadata.extensions == {"x-owner": "alice"}


def test_extension_cannot_shadow_or_override_a_core_field() -> None:
    text = _BASE_CONTRACT.replace(
        "- migration: none\n", "- migration: none\n- x-specification_type: not-a-real-value\n"
    )

    result = parse_contract_metadata(text)

    assert result.metadata.specification_type is SpecificationType.FEATURE
    assert result.metadata.extensions == {"x-specification_type": "not-a-real-value"}


def test_duplicate_extension_field_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "- migration: none\n", "- migration: none\n- x-owner: alice\n- x-owner: bob\n"
    )

    with pytest.raises(DuplicateMetadataFieldError):
        parse_contract_metadata(text)


# --------------------------------------------------------------------------
# Task identity consistency
# --------------------------------------------------------------------------


def test_task_identity_mismatch_raises() -> None:
    text = _BASE_CONTRACT.replace("- id: 099\n", "- id: 100\n")

    with pytest.raises(TaskIdentityMismatchError):
        parse_contract_metadata(text)


def test_missing_task_identity_id_raises() -> None:
    text = _BASE_CONTRACT.replace("- id: 099\n", "")

    with pytest.raises(MissingMetadataFieldError):
        parse_contract_metadata(text)


def test_task_identity_title_mismatch_raises() -> None:
    text = _BASE_CONTRACT.replace("- title: Sample\n", "- title: Something Else\n")

    with pytest.raises(TaskIdentityMismatchError):
        parse_contract_metadata(text)


def test_missing_task_identity_title_raises() -> None:
    text = _BASE_CONTRACT.replace("- title: Sample\n", "")

    with pytest.raises(MissingMetadataFieldError):
        parse_contract_metadata(text)


def test_duplicate_task_identity_title_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "- title: Sample\n", "- title: Sample\n- title: Sample\n"
    )

    with pytest.raises(DuplicateMetadataFieldError):
        parse_contract_metadata(text)


def test_duplicate_task_identity_id_raises() -> None:
    text = _BASE_CONTRACT.replace("- id: 099\n", "- id: 099\n- id: 099\n")

    with pytest.raises(DuplicateMetadataFieldError):
        parse_contract_metadata(text)


# --------------------------------------------------------------------------
# Provenance: required GitHub Issue / Branch entries
# --------------------------------------------------------------------------


def test_provenance_is_parsed_into_the_result() -> None:
    result = parse_contract_metadata(_BASE_CONTRACT)

    assert result.provenance is not None
    assert result.provenance.github_issue == "#1"
    assert result.provenance.branch == "task/099-sample"


def test_provenance_branch_without_backticks_still_parses() -> None:
    text = _BASE_CONTRACT.replace(
        "- Branch: `task/099-sample`", "- Branch: task/099-sample"
    )

    result = parse_contract_metadata(text)

    assert result.provenance.branch == "task/099-sample"


def test_missing_github_issue_provenance_field_raises() -> None:
    text = _BASE_CONTRACT.replace("- GitHub Issue: #1\n", "")

    with pytest.raises(MissingProvenanceFieldError):
        parse_contract_metadata(text)


def test_missing_branch_provenance_field_raises() -> None:
    text = _BASE_CONTRACT.replace("- Branch: `task/099-sample`\n", "")

    with pytest.raises(MissingProvenanceFieldError):
        parse_contract_metadata(text)


def test_duplicate_github_issue_provenance_field_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "- GitHub Issue: #1\n", "- GitHub Issue: #1\n- GitHub Issue: #2\n"
    )

    with pytest.raises(DuplicateProvenanceFieldError):
        parse_contract_metadata(text)


def test_duplicate_branch_provenance_field_raises() -> None:
    text = _BASE_CONTRACT.replace(
        "- Branch: `task/099-sample`\n",
        "- Branch: `task/099-sample`\n- Branch: `task/099-other`\n",
    )

    with pytest.raises(DuplicateProvenanceFieldError):
        parse_contract_metadata(text)


@pytest.mark.parametrize("malformed", ["96", "issue-96", "# 96", "#ninety-six"])
def test_malformed_github_issue_provenance_field_raises(malformed: str) -> None:
    text = _BASE_CONTRACT.replace("- GitHub Issue: #1", f"- GitHub Issue: {malformed}")

    with pytest.raises(MalformedProvenanceFieldError):
        parse_contract_metadata(text)


def test_empty_branch_provenance_field_raises() -> None:
    text = _BASE_CONTRACT.replace("- Branch: `task/099-sample`\n", "- Branch: ``\n")

    with pytest.raises(MalformedProvenanceFieldError):
        parse_contract_metadata(text)


def test_optional_provenance_entries_are_ignored_but_do_not_break_parsing() -> None:
    text = _BASE_CONTRACT.replace(
        "- Branch: `task/099-sample`\n",
        "- Branch: `task/099-sample`\n- Epic: Sample Epic\n- Current Release: `v0.1.1`\n",
    )

    result = parse_contract_metadata(text)

    assert result.provenance.github_issue == "#1"
    assert result.provenance.branch == "task/099-sample"


# --------------------------------------------------------------------------
# Legacy classification
# --------------------------------------------------------------------------


def test_contract_with_no_contract_version_is_legacy() -> None:
    text = _BASE_CONTRACT.replace("## Contract Version\n\n1\n\n", "")

    result = parse_contract_metadata(text)

    assert result.kind is ContractKind.LEGACY
    assert result.contract_version is None
    assert result.provenance is None
    assert result.metadata is None
    assert result.task_id == "099"
    assert result.title == "Sample"


@pytest.mark.parametrize(
    "slug",
    [
        "001-bootstrap",
        "022-planner-workflow-standard",
        "037-release-operator-ux",
        "042-specification-generator",
        "044-specification-template-engine",
        "045-contract-schema",
    ],
)
def test_representative_real_legacy_contracts_classify_as_legacy(slug: str) -> None:
    result = parse_contract_metadata(_read(f"tasks/{slug}.md"))

    assert result.kind is ContractKind.LEGACY
    assert result.metadata is None


# --------------------------------------------------------------------------
# Malformed title
# --------------------------------------------------------------------------


def test_missing_or_malformed_title_raises() -> None:
    text = _BASE_CONTRACT.replace("# Task 099 — Sample", "Not a title at all")

    with pytest.raises(ContractMetadataError):
        parse_contract_metadata(text)


# --------------------------------------------------------------------------
# Deterministic output and stable error types
# --------------------------------------------------------------------------


def test_deterministic_error_type_across_repeated_parses() -> None:
    text = _BASE_CONTRACT.replace("risk_level: low", "risk_level: extreme")

    for _ in range(2):
        with pytest.raises(InvalidMetadataValueError):
            parse_contract_metadata(text)


def test_deterministic_output_across_repeated_parses_of_real_contract() -> None:
    text = _read("tasks/046-contract-metadata-engine.md")

    first = parse_contract_metadata(text)
    second = parse_contract_metadata(text)

    assert first == second
