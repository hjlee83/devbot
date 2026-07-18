"""Task 046: Contract Metadata Engine.

The single runtime boundary that parses a Task Contract's Markdown into
typed metadata, built on Contract Schema v1 (Task 045,
`specifications/045-contract-schema.md`). Future policy, workflow, and
review consumers must use `parse_contract_metadata()`'s typed result
instead of independently re-parsing Contract Markdown - this module is the
only component allowed to interpret Schema v1 metadata syntax.

**Legacy vs. native Schema v1.** A Contract with no `## Contract Version`
section is classified as legacy (`ContractKind.LEGACY`) - this is not an
error, and no historical Contract needs to change. Once a Contract
declares any `Contract Version`, it is committed to the Schema v1 parsing
path: malformed or unsupported content from that point on is always an
error, never a silent fallback to legacy (Task 046 Safety: "Do not
silently downgrade malformed versioned Contracts to legacy").

**Fail closed.** Unsupported `contract_version` values, missing/duplicate
required sections, missing/duplicate/unknown metadata fields, invalid enum
values, and a Task Identity `id` that disagrees with the Contract's own
heading task id all raise a dedicated `ContractMetadataError` subclass.

**Extensions never affect core metadata.** `x-`-prefixed metadata fields
are preserved verbatim in a separate, immutable `extensions` mapping. They
are never consulted when resolving any of the five typed core fields, so a
`x-specification_type` line can never shadow or override the real
`specification_type` value.

**No policy.** This module only parses and validates - it makes no release,
review, workflow, or dispatch decision, and adds no CLI command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from devbot.specification import fenced_code_ranges

SUPPORTED_CONTRACT_VERSIONS: tuple[int, ...] = (1,)

_REQUIRED_SECTIONS: tuple[str, ...] = (
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
)


class ContractKind(StrEnum):
    SCHEMA_V1 = "schema_v1"
    LEGACY = "legacy"


class SpecificationType(StrEnum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    REFACTOR = "refactor"
    DOCS = "docs"
    INTERNAL = "internal"
    GENERIC = "generic"


class ReleaseImpact(StrEnum):
    BREAKING = "breaking"
    FEATURE = "feature"
    FIX = "fix"
    DOCS = "docs"
    INTERNAL = "internal"
    NONE = "none"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Compatibility(StrEnum):
    BACKWARD = "backward"
    BREAKING = "breaking"


class Migration(StrEnum):
    NONE = "none"
    OPTIONAL = "optional"
    REQUIRED = "required"


# Field name -> the StrEnum Task 045 declares canonical values for. Order
# here is Task 045's own Metadata field order.
_CORE_METADATA_FIELDS: dict[str, type[StrEnum]] = {
    "specification_type": SpecificationType,
    "release_impact": ReleaseImpact,
    "risk_level": RiskLevel,
    "compatibility": Compatibility,
    "migration": Migration,
}


class ContractMetadataError(RuntimeError):
    """Base for a Contract that cannot be safely parsed as Schema v1."""


class UnsupportedContractVersionError(ContractMetadataError):
    """`Contract Version` is a valid positive integer but not one this
    engine supports (only `1` today)."""


class MalformedContractVersionError(ContractMetadataError):
    """`Contract Version`'s body is not exactly one positive integer."""


class MissingSectionError(ContractMetadataError):
    """A required Schema v1 top-level section is absent."""


class DuplicateSectionError(ContractMetadataError):
    """A singleton Schema v1 top-level section occurs more than once."""


class MissingMetadataFieldError(ContractMetadataError):
    """A required core Metadata field is absent."""


class DuplicateMetadataFieldError(ContractMetadataError):
    """A core or `x-` Metadata field occurs more than once."""


class UnknownMetadataFieldError(ContractMetadataError):
    """A Metadata field is neither one of the five core fields nor
    `x-`-prefixed."""


class InvalidMetadataValueError(ContractMetadataError):
    """A core Metadata field's value is not one of its canonical enum
    values."""


class TaskIdentityMismatchError(ContractMetadataError):
    """The Contract heading's task id disagrees with Task Identity's own
    `id` field."""


@dataclass(frozen=True)
class ContractMetadata:
    """The five typed core Metadata fields plus preserved `x-` extensions.
    `extensions` never affects any core field's resolved value."""

    specification_type: SpecificationType
    release_impact: ReleaseImpact
    risk_level: RiskLevel
    compatibility: Compatibility
    migration: Migration
    extensions: MappingProxyType[str, str]


@dataclass(frozen=True)
class ContractParseResult:
    """The result of parsing one Contract's Markdown. `metadata` is
    `None` for `ContractKind.LEGACY` - legacy Contracts are explicitly
    represented, never silently treated as Schema v1."""

    kind: ContractKind
    task_id: str
    title: str
    contract_version: int | None
    metadata: ContractMetadata | None


_TITLE_RE = re.compile(r"^#\s+Task\s+(\d{3})\s*[—:-]\s*(.+?)\s*$")
_SECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_VERSION_RE = re.compile(r"^\s*(\d+)\s*$")
_FIELD_LINE_RE = re.compile(r"^-\s+([\w-]+):\s*(\S.*?)\s*$", re.MULTILINE)


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _split_top_level_sections(text: str) -> list[tuple[str, int, str]]:
    """Every `## ` heading in document order as `(heading, line, body)` -
    duplicates are returned as separate entries (not merged), so callers can
    detect and reject them. Headings inside ``` fenced code blocks (e.g. an
    example inside a `Quality Gates` section) are ignored."""
    fenced = fenced_code_ranges(text)

    def _in_fence(pos: int) -> bool:
        return any(start <= pos < end for start, end in fenced)

    matches = [m for m in _SECTION_HEADING_RE.finditer(text) if not _in_fence(m.start())]
    sections = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        heading = match.group(1).strip()
        line = _line_number(text, match.start())
        sections.append((heading, line, text[start:end]))
    return sections


def _parse_field_lines(body: str) -> list[tuple[str, str]]:
    return [(match.group(1), match.group(2)) for match in _FIELD_LINE_RE.finditer(body)]


def _parse_contract_version(body: str) -> int:
    stripped = body.strip()
    match = _VERSION_RE.match(stripped)
    if match is None:
        raise MalformedContractVersionError(
            f"Contract Version must be exactly one positive integer, found {stripped!r}"
        )
    version = int(match.group(1))
    if version <= 0:
        raise MalformedContractVersionError(
            f"Contract Version must be a positive integer, found {version}"
        )
    if version not in SUPPORTED_CONTRACT_VERSIONS:
        raise UnsupportedContractVersionError(f"unsupported contract_version: {version}")
    return version


def _parse_task_identity(body: str, heading_task_id: str) -> None:
    fields = dict(_parse_field_lines(body))
    identity_id = fields.get("id")
    if identity_id is None:
        raise MissingMetadataFieldError("Task Identity.id")
    if identity_id != heading_task_id:
        raise TaskIdentityMismatchError(
            f"heading task id {heading_task_id!r} does not match Task Identity id "
            f"{identity_id!r}"
        )


def _parse_metadata(body: str) -> ContractMetadata:
    core_values: dict[str, str] = {}
    extensions: dict[str, str] = {}
    for field_name, value in _parse_field_lines(body):
        if field_name.startswith("x-"):
            if field_name in extensions:
                raise DuplicateMetadataFieldError(field_name)
            extensions[field_name] = value
            continue
        if field_name not in _CORE_METADATA_FIELDS:
            raise UnknownMetadataFieldError(field_name)
        if field_name in core_values:
            raise DuplicateMetadataFieldError(field_name)
        core_values[field_name] = value

    missing = [name for name in _CORE_METADATA_FIELDS if name not in core_values]
    if missing:
        raise MissingMetadataFieldError(", ".join(missing))

    typed: dict[str, StrEnum] = {}
    for field_name, enum_cls in _CORE_METADATA_FIELDS.items():
        raw = core_values[field_name]
        try:
            typed[field_name] = enum_cls(raw)
        except ValueError as exc:
            raise InvalidMetadataValueError(f"{field_name}={raw!r}") from exc

    return ContractMetadata(
        specification_type=typed["specification_type"],  # type: ignore[arg-type]
        release_impact=typed["release_impact"],  # type: ignore[arg-type]
        risk_level=typed["risk_level"],  # type: ignore[arg-type]
        compatibility=typed["compatibility"],  # type: ignore[arg-type]
        migration=typed["migration"],  # type: ignore[arg-type]
        extensions=MappingProxyType(extensions),
    )


def parse_contract_metadata(text: str) -> ContractParseResult:
    """Pure: parses `text` (one Task Contract's full Markdown) into a
    `ContractParseResult`. No filesystem or network access - identical text
    always returns an equivalent result.

    A Contract with no `Contract Version` section is legacy
    (`ContractKind.LEGACY`, `metadata=None`) - not an error. Once a
    `Contract Version` section exists, every Schema v1 rule is enforced and
    any violation raises a `ContractMetadataError` subclass; the Contract is
    never silently downgraded back to legacy."""
    lines = text.splitlines()
    title_line = lines[0] if lines else ""
    title_match = _TITLE_RE.match(title_line)
    if title_match is None:
        raise ContractMetadataError(f"missing or malformed Contract title heading: {title_line!r}")
    heading_task_id = title_match.group(1)
    title = title_match.group(2).strip()

    sections_by_heading: dict[str, list[tuple[int, str]]] = {}
    for heading, line, body in _split_top_level_sections(text):
        sections_by_heading.setdefault(heading, []).append((line, body))

    version_entries = sections_by_heading.get("Contract Version", [])
    if not version_entries:
        return ContractParseResult(
            kind=ContractKind.LEGACY,
            task_id=heading_task_id,
            title=title,
            contract_version=None,
            metadata=None,
        )
    if len(version_entries) > 1:
        raise DuplicateSectionError("Contract Version")

    contract_version = _parse_contract_version(version_entries[0][1])

    for required in _REQUIRED_SECTIONS:
        entries = sections_by_heading.get(required, [])
        if not entries:
            raise MissingSectionError(required)
        if len(entries) > 1:
            raise DuplicateSectionError(required)

    if len(sections_by_heading.get("References", [])) > 1:
        raise DuplicateSectionError("References")

    _parse_task_identity(sections_by_heading["Task Identity"][0][1], heading_task_id)
    metadata = _parse_metadata(sections_by_heading["Metadata"][0][1])

    return ContractParseResult(
        kind=ContractKind.SCHEMA_V1,
        task_id=heading_task_id,
        title=title,
        contract_version=contract_version,
        metadata=metadata,
    )
