"""Task 045: Contract Schema v1 documentation regression tests.

Task 045 defines Contract Schema v1 as documentation only - no parser, CLI,
or domain object is implemented (that is explicitly out of scope; see
`specifications/045-contract-schema.md`'s Safety section). These tests
therefore do two things, neither of which introduces reusable parsing code
in `src/devbot/`:

1. Reuse the *existing* Task 043 Specification Validator
   (`devbot.specification_validation.validate_specification_file`) to pin
   down Acceptance Criterion 8 ("The Specification validates under the
   existing Specification Validator") as an executable regression.
2. Run small, test-local string/regex checks - confined to this file, not
   exported as an importable module - confirming the Normative Example
   inside the Specification only uses the canonical enum values it itself
   declares, and that historical Contracts are correctly legacy (no
   `Contract Version` declared).
"""

from __future__ import annotations

import re
from pathlib import Path

from devbot.specification_validation import validate_specification_file

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The exact canonical enum values Task 045 declares for each Metadata
# field - copied from `specifications/045-contract-schema.md`'s "Canonical
# Metadata Values" subsection, not imported from any source module (there
# is none - Task 045 forbids adding one).
_CANONICAL_METADATA_VALUES: dict[str, tuple[str, ...]] = {
    "specification_type": ("feature", "bugfix", "refactor", "docs", "internal", "generic"),
    "release_impact": ("breaking", "feature", "fix", "docs", "internal", "none"),
    "risk_level": ("low", "medium", "high"),
    "compatibility": ("backward", "breaking"),
    "migration": ("none", "optional", "required"),
}

_NORMATIVE_AREAS_IN_ORDER = (
    "Contract Version",
    "Provenance",
    "Task Identity",
    "Metadata",
    "Goal",
    "Context",
    "Scope",
    "Out of Scope",
    "Deliverables",
    "Acceptance Criteria",
    "Quality Gates",
    "Handoff",
    "References",
)

# Maps each "Required and Optional Summary" table Area name to the phrase
# `## Required Behaviour` uses for it (Required Behaviour paraphrases some
# areas, e.g. "Contract metadata" for "Metadata"). `None` marks the one
# optional area (References), which must never appear in the numbered
# "must define" list itself. A PR review (2026-07-18, hjlee83) found these
# two sections contradicting each other on References' requiredness; this
# mapping pins them back together so that regression can't recur silently.
_SUMMARY_AREA_TO_REQUIRED_BEHAVIOUR_PHRASE: dict[str, str | None] = {
    "Contract Version": "schema version",
    "Provenance": "provenance",
    "Task Identity": "task identity",
    "Metadata": "contract metadata",
    "Goal": "goal",
    "Context": "context",
    "Scope": "scope",
    "Out of Scope": "out-of-scope",
    "Deliverables": "deliverables",
    "Acceptance Criteria": "acceptance criteria",
    "Quality Gates": "quality gates",
    "Handoff": "handoff",
    "References": None,
}


def _read(relative_path: str) -> str:
    return (_REPO_ROOT / relative_path).read_text(encoding="utf-8")


def _normative_example_block() -> str:
    spec = _read("specifications/045-contract-schema.md")
    match = re.search(r"### Normative Example\n\n```markdown\n(.*?)\n```", spec, re.DOTALL)
    assert match is not None, "Normative Example fenced block not found"
    return match.group(1)


# --------------------------------------------------------------------------
# AC-8: the Specification itself validates under the existing validator.
# --------------------------------------------------------------------------


def test_specification_045_passes_task_043_validator() -> None:
    result = validate_specification_file(_REPO_ROOT, 45)

    assert result.passed is True
    assert result.errors == ()


def test_specification_045_has_no_warnings() -> None:
    result = validate_specification_file(_REPO_ROOT, 45)

    assert result.warnings == ()


# --------------------------------------------------------------------------
# Normative Example self-consistency (test-local checks only).
# --------------------------------------------------------------------------


def test_normative_example_declares_contract_version_one() -> None:
    example = _normative_example_block()

    assert re.search(r"## Contract Version\n\n1\n", example) is not None


def test_normative_example_metadata_uses_only_canonical_values() -> None:
    example = _normative_example_block()
    metadata_match = re.search(r"## Metadata\n\n(.*?)\n\n## Goal", example, re.DOTALL)
    assert metadata_match is not None
    metadata_body = metadata_match.group(1)

    fields = dict(
        re.findall(r"^- (\w[\w-]*): (\S+)$", metadata_body, re.MULTILINE)
    )

    assert set(fields) == set(_CANONICAL_METADATA_VALUES)
    for field, value in fields.items():
        assert value in _CANONICAL_METADATA_VALUES[field], (
            f"{field}={value!r} is not one of the declared canonical values "
            f"{_CANONICAL_METADATA_VALUES[field]}"
        )


def test_normative_example_includes_all_required_areas() -> None:
    example = _normative_example_block()

    for area in _NORMATIVE_AREAS_IN_ORDER:
        if area == "References":
            continue  # optional; the Normative Example omits it.
        assert f"## {area}" in example, f"Normative Example is missing '## {area}'"


def test_required_behaviour_is_consistent_with_required_optional_summary() -> None:
    spec = _read("specifications/045-contract-schema.md")

    summary_match = re.search(
        r"\| Area \| Required \| Cardinality \|\n\|[-:| ]+\|\n((?:\|.+\|\n)+)", spec
    )
    assert summary_match is not None
    summary = {
        area: (required == "yes")
        for area, required in re.findall(r"\| (.+?) \| (yes|no) \| .+? \|", summary_match.group(1))
    }
    assert set(summary) == set(_SUMMARY_AREA_TO_REQUIRED_BEHAVIOUR_PHRASE)

    behaviour_match = re.search(
        r"## Required Behaviour\n\n(.*?)\n\n## Acceptance Criteria", spec, re.DOTALL
    )
    assert behaviour_match is not None
    behaviour_section = behaviour_match.group(1)
    behaviour_text = behaviour_section.lower()

    numbered_items_match = re.search(r"required areas:\n\n((?:\d+\. .*\n)+)", behaviour_section)
    assert numbered_items_match is not None
    numbered_items_text = numbered_items_match.group(1).lower()

    for area, is_required in summary.items():
        phrase = _SUMMARY_AREA_TO_REQUIRED_BEHAVIOUR_PHRASE[area]
        if is_required:
            assert phrase is not None
            assert phrase in behaviour_text, (
                f"required area '{area}' is not mentioned in Required Behaviour"
            )
            assert phrase in numbered_items_text, (
                f"required area '{area}' must appear in the numbered 'must define' list"
            )
        else:
            assert "optional" in behaviour_text and area.lower() in behaviour_text, (
                f"optional area '{area}' must be explicitly called out as optional in "
                "Required Behaviour, not silently omitted"
            )
            assert area.lower() not in numbered_items_text, (
                f"optional area '{area}' must not be listed among the numbered "
                "'must define' required items"
            )


def test_canonical_metadata_representation_block_uses_only_canonical_values() -> None:
    spec = _read("specifications/045-contract-schema.md")
    match = re.search(
        r"### Canonical Metadata Representation\n\n.*?```markdown\n(.*?)\n```",
        spec,
        re.DOTALL,
    )
    assert match is not None
    fields = dict(re.findall(r"^- (\w[\w-]*): (\S+)$", match.group(1), re.MULTILINE))

    assert set(fields) == set(_CANONICAL_METADATA_VALUES)
    for field, value in fields.items():
        assert value in _CANONICAL_METADATA_VALUES[field]


# --------------------------------------------------------------------------
# Legacy compatibility: historical Contracts are correctly unversioned.
# --------------------------------------------------------------------------


def test_historical_contracts_have_no_contract_version_and_are_legacy() -> None:
    for slug in (
        "001-bootstrap",
        "022-planner-workflow-standard",
        "037-release-operator-ux",
        "042-specification-generator",
        "044-specification-template-engine",
    ):
        contract = _read(f"tasks/{slug}.md")
        assert not re.search(r"^## Contract Version$", contract, re.MULTILINE), (
            f"tasks/{slug}.md unexpectedly declares a Contract Version - "
            "it should remain legacy for this to be a valid compatibility claim"
        )


def test_contract_045_itself_is_legacy_not_schema_v1() -> None:
    # tasks/045-contract-schema.md is a regular (legacy-style) Task Contract
    # - only the Specification defines what a *Schema v1* Contract instance
    # would look like (see its Normative Example). Task 045 does not require
    # rewriting its own Contract into Schema v1 form.
    contract = _read("tasks/045-contract-schema.md")

    assert not re.search(r"^## Contract Version$", contract, re.MULTILINE)


# --------------------------------------------------------------------------
# docs/09-task-contract-standard.md required-item list is unchanged.
# --------------------------------------------------------------------------


def test_docs_09_required_items_list_is_unchanged() -> None:
    standard = _read("docs/09-task-contract-standard.md")
    match = re.search(r"## 필수 항목\n\n.*?\n\n((?:\d+\. .*\n)+)", standard)
    assert match is not None
    items = [line.split(". ", 1)[1].strip() for line in match.group(1).splitlines()]

    assert items == [
        "목표",
        "배경",
        "Dependencies",
        "구현 범위",
        "제외 범위",
        "품질 게이트",
        "필수 테스트 이름",
        "검증 명령",
        "Files Expected to Change",
        "Risk",
        "Rollback Strategy",
        "Reviewer Focus",
        "Definition of Done",
        "Result 문서 경로",
    ]
