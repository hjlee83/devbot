"""Task 043: Specification Validator.

Determines whether a `specifications/NNN-slug.md` document produced by Task
042's generator is structurally complete, internally consistent, and safe to
hand off to an implementation Agent - the quality gate a future Workflow
Engine must pass before Dispatch:

    Goal -> Planner -> Issue -> Contract -> Specification -> Validation -> Dispatch

Task 042's Specification format (`devbot.specification`) is authoritative;
this module validates that format rather than defining a competing schema -
it imports `REQUIRED_TOP_LEVEL_SECTIONS` and `fenced_code_ranges` directly
from `devbot.specification` instead of redeclaring them.

**Read-only and deterministic.** `validate_specification_text` is a pure
function of its input text: no timestamps, no randomness, no filesystem or
network access. Issues are always ordered by document line, then rule code,
then message (SPV-013), so repeated validation of identical bytes returns
byte-for-byte equivalent results.

**Errors vs. warnings.** Any `ValidationIssue` with `ERROR` severity sets
`passed = False`; warnings alone never fail validation or CLI exit status.

**Out of scope (Task 043).** No code-to-Spec conformance validation, no
runtime verification, no Agent invocation, no Dispatch integration, no Task
044 template generation, no Task 045 release classification, no automatic
repair of invalid Specifications, and no `devbot.agents`/`devbot.workspace`/
`devbot.polling`/`devbot.review`/`devbot.rework` import or behaviour change.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from devbot.specification import (
    _NOT_SPECIFIED,
    REQUIRED_TOP_LEVEL_SECTIONS,
    SPECIFICATIONS_DIR,
    fenced_code_ranges,
)

FULL_CONTRACT_REFERENCE_HEADING = "# Full Task Contract Reference"

# Canonical Task 042 subsections per top-level section, keyed by the
# top-level heading text without its leading "# ". `# Full Task Contract
# Reference` has no required subsections - its own body is checked directly
# by SPV-009.
REQUIRED_SUBSECTIONS: dict[str, tuple[str, ...]] = {
    "Overview": ("Goal", "Scope", "Background", "Roadmap Context"),
    "Functional Requirements": ("Required Behaviour", "Acceptance Criteria", "Out of Scope"),
    "Technical Design": (
        "Architecture",
        "Files Expected to Change",
        "Dependencies",
        "Constraints",
        "Migration Notes",
    ),
    "Validation": ("Required Tests and Quality Gates", "Validation Commands", "Success Criteria"),
    "Safety": ("Things the Implementation Agent Must NOT Do",),
    "Completion": ("Expected Deliverables", "Result Document", "PR Expectations"),
    "Handoff": ("Required Handoff Procedure", "Token-Limit Behaviour"),
}

_SAFETY_BOUNDARY_SUBSECTION = "Things the Implementation Agent Must NOT Do"

# Normative sections scanned for unresolved markers (SPV-012). The verbatim
# Full Task Contract Reference is excluded on purpose - it is a historical
# copy of the Contract, not new normative prose this Task generated, and we
# must never ask an implementation agent to "fix" someone else's Contract.
_NORMATIVE_SECTION_HEADINGS = tuple(
    heading[2:]
    for heading in REQUIRED_TOP_LEVEL_SECTIONS
    if heading != FULL_CONTRACT_REFERENCE_HEADING
)


class ValidationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    severity: ValidationSeverity
    message: str
    section: str | None = None
    line: int | None = None


@dataclass(frozen=True)
class SpecificationValidationResult:
    task_number: int
    specification_path: Path
    passed: bool
    errors: tuple[ValidationIssue, ...]
    warnings: tuple[ValidationIssue, ...]


class SpecificationValidationError(RuntimeError):
    """Base for *operational* failures resolving or loading a Specification
    file - distinct from a document that loads fine but fails validation
    rules (reported via `SpecificationValidationResult.passed`, not raised)."""


class InvalidTaskNumberError(SpecificationValidationError):
    """`task_number` is not a valid positive integer."""


class SpecificationNotFoundError(SpecificationValidationError):
    """No `specifications/NNN-*.md` file exists for this Task number."""


class AmbiguousSpecificationError(SpecificationValidationError):
    """More than one `specifications/NNN-*.md` file matches this Task
    number."""


class SpecificationLoadError(SpecificationValidationError):
    """The matched Specification file exists but could not be read."""


_TOP_LEVEL_HEADING_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)
_SUBSECTION_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_TITLE_RE = re.compile(r"^Specification:\s*Task\s+(\d{3})\s*—\s*(.+?)\s*$")
_PATH_ONLY_RE = re.compile(r"^(see\s+)?`[^`]+`\.?$", re.IGNORECASE)
_CHECKPOINT_RE = re.compile(r"^###\s+(CP-[A-Za-z0-9]+-\d+)\b", re.MULTILINE)
_CHECKLIST_RE = re.compile(r"^\s*-\s*\[[ xX]\]\s*\S", re.MULTILINE)
_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*]|\d+\.)\s+\S", re.MULTILINE)
_FENCE_CONTENT_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
_UNRESOLVED_MARKER_RE = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|\{\{[^}]*\}\}")


@dataclass(frozen=True)
class _Block:
    heading: str
    line: int
    body: str
    subsections: tuple[_Block, ...] = ()


@dataclass(frozen=True)
class _ParsedSpecification:
    title_text: str
    title_line: int | None
    preamble_subsections: tuple[_Block, ...]
    sections: tuple[_Block, ...]


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _line_in_block(block: _Block, relative_offset: int) -> int:
    return block.line + block.body.count("\n", 0, relative_offset)


def _subsections_in(
    text: str, level2: list[re.Match[str]], start: int, end: int
) -> tuple[_Block, ...]:
    found = [match for match in level2 if start <= match.start() < end]
    blocks = []
    for index, match in enumerate(found):
        body_start = match.end()
        body_end = found[index + 1].start() if index + 1 < len(found) else end
        blocks.append(
            _Block(
                heading=match.group(1).strip(),
                line=_line_number(text, match.start()),
                body=text[body_start:body_end],
            )
        )
    return tuple(blocks)


def _parse_specification(text: str) -> _ParsedSpecification:
    fenced = fenced_code_ranges(text)

    def _in_fence(pos: int) -> bool:
        return any(start <= pos < end for start, end in fenced)

    level1 = [
        match for match in _TOP_LEVEL_HEADING_RE.finditer(text) if not _in_fence(match.start())
    ]
    level2 = [
        match for match in _SUBSECTION_HEADING_RE.finditer(text) if not _in_fence(match.start())
    ]

    if not level1:
        return _ParsedSpecification(
            title_text="",
            title_line=None,
            preamble_subsections=_subsections_in(text, level2, 0, len(text)),
            sections=(),
        )

    title_match = level1[0]
    title_text = title_match.group(1).strip()
    title_line = _line_number(text, title_match.start())

    section_headings = level1[1:]
    # Full Task Contract Reference is an opaque, verbatim dump of the
    # original Task Contract - which may itself contain ``` fenced code
    # blocks using the same delimiter as the outer ```markdown fence this
    # section is wrapped in. A flat fence-toggle can't represent that
    # nesting, so a heading-like line deep inside the dump can appear to sit
    # outside any fence. Once this section starts, everything to
    # end-of-document belongs to it - nothing after this heading is ever
    # treated as further document structure.
    for index, match in enumerate(section_headings):
        if match.group(1).strip() == "Full Task Contract Reference":
            section_headings = section_headings[: index + 1]
            break
    preamble_end = section_headings[0].start() if section_headings else len(text)
    preamble_subsections = _subsections_in(text, level2, title_match.end(), preamble_end)

    sections = []
    for index, match in enumerate(section_headings):
        start = match.end()
        end = (
            section_headings[index + 1].start()
            if index + 1 < len(section_headings)
            else len(text)
        )
        sections.append(
            _Block(
                heading=match.group(1).strip(),
                line=_line_number(text, match.start()),
                body=text[start:end],
                subsections=_subsections_in(text, level2, start, end),
            )
        )

    return _ParsedSpecification(
        title_text=title_text,
        title_line=title_line,
        preamble_subsections=preamble_subsections,
        sections=tuple(sections),
    )


def _error(
    code: str, message: str, *, section: str | None = None, line: int | None = None
) -> ValidationIssue:
    return ValidationIssue(
        code=code, severity=ValidationSeverity.ERROR, message=message, section=section, line=line
    )


def _warning(
    code: str, message: str, *, section: str | None = None, line: int | None = None
) -> ValidationIssue:
    return ValidationIssue(
        code=code, severity=ValidationSeverity.WARNING, message=message, section=section, line=line
    )


def _check_title(parsed: _ParsedSpecification, task_number: int) -> list[ValidationIssue]:
    if not parsed.title_text:
        return [_error("SPV-001", "missing canonical Specification title", line=parsed.title_line)]
    match = _TITLE_RE.match(parsed.title_text)
    if match is None:
        return [
            _error(
                "SPV-001",
                f"malformed Specification title: {parsed.title_text!r}",
                line=parsed.title_line,
            )
        ]
    found_task_number = int(match.group(1))
    if found_task_number != task_number:
        return [
            _error(
                "SPV-001",
                f"title Task number mismatch: expected {task_number:03d}, "
                f"found {found_task_number:03d}",
                line=parsed.title_line,
            )
        ]
    if not match.group(2).strip():
        return [_error("SPV-001", "Specification title is empty", line=parsed.title_line)]
    return []


def _check_provenance(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    provenance = next(
        (sub for sub in parsed.preamble_subsections if sub.heading == "Provenance"), None
    )
    if provenance is None:
        return [_error("SPV-002", "missing '## Provenance' section", section="Provenance")]
    missing = [
        label
        for label, pattern in (
            ("Task Issue", re.compile(r"issue", re.IGNORECASE)),
            ("Task Contract", re.compile(r"contract", re.IGNORECASE)),
            ("generation source", re.compile(r"generat", re.IGNORECASE)),
        )
        if not pattern.search(provenance.body)
    ]
    if missing:
        return [
            _error(
                "SPV-002",
                f"incomplete '## Provenance' section (missing: {', '.join(missing)})",
                section="Provenance",
                line=provenance.line,
            )
        ]
    return []


def _check_structure(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    by_heading: dict[str, list[_Block]] = {}
    for section in parsed.sections:
        by_heading.setdefault(f"# {section.heading}", []).append(section)

    for required in REQUIRED_TOP_LEVEL_SECTIONS:
        matches = by_heading.get(required, [])
        if not matches:
            issues.append(
                _error(
                    "SPV-003",
                    f"missing required top-level section: {required}",
                    section=required,
                )
            )
        elif len(matches) > 1:
            issues.append(
                _error(
                    "SPV-003",
                    f"duplicated required top-level section: {required} "
                    f"(found {len(matches)} times)",
                    section=required,
                    line=matches[0].line,
                )
            )

    for section in parsed.sections:
        heading = f"# {section.heading}"
        if heading not in REQUIRED_TOP_LEVEL_SECTIONS:
            issues.append(
                _warning(
                    "SPV-011",
                    f"unknown top-level section: {heading}",
                    section=section.heading,
                    line=section.line,
                )
            )

    seen: set[str] = set()
    present_order: list[str] = []
    for section in parsed.sections:
        heading = f"# {section.heading}"
        if heading in REQUIRED_TOP_LEVEL_SECTIONS and heading not in seen:
            seen.add(heading)
            present_order.append(heading)
    canonical_order = [heading for heading in REQUIRED_TOP_LEVEL_SECTIONS if heading in seen]
    if present_order != canonical_order:
        issues.append(
            _warning(
                "SPV-010",
                "required top-level sections are not in canonical order",
                line=parsed.sections[0].line if parsed.sections else None,
            )
        )

    for top_heading, required_subs in REQUIRED_SUBSECTIONS.items():
        matches = by_heading.get(f"# {top_heading}", [])
        if not matches:
            continue
        section = matches[0]
        for sub_name in required_subs:
            sub = next((s for s in section.subsections if s.heading == sub_name), None)
            if sub is None:
                issues.append(
                    _error(
                        "SPV-004",
                        f"missing required subsection '{sub_name}' under '# {top_heading}'",
                        section=top_heading,
                        line=section.line,
                    )
                )
                continue
            if sub_name == _SAFETY_BOUNDARY_SUBSECTION:
                continue  # SPV-008 owns this subsection's emptiness check.
            if not sub.body.strip():
                issues.append(
                    _error(
                        "SPV-005",
                        f"empty required body for subsection '{sub_name}' under '# {top_heading}'",
                        section=sub_name,
                        line=sub.line,
                    )
                )

    return issues


def _check_acceptance_criteria(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    fr = next((s for s in parsed.sections if s.heading == "Functional Requirements"), None)
    if fr is None:
        return []
    ac = next((s for s in fr.subsections if s.heading == "Acceptance Criteria"), None)
    if ac is None:
        return []

    issues: list[ValidationIssue] = []
    checkpoints = _CHECKPOINT_RE.findall(ac.body)
    has_form = (
        bool(checkpoints)
        or bool(_CHECKLIST_RE.search(ac.body))
        or bool(_LIST_ITEM_RE.search(ac.body))
    )
    if not has_form:
        issues.append(
            _error(
                "SPV-006",
                "Acceptance Criteria has no checkpoint heading, checklist, or list criterion",
                section="Acceptance Criteria",
                line=ac.line,
            )
        )

    seen: set[str] = set()
    for identifier in checkpoints:
        if identifier in seen:
            issues.append(
                _error(
                    "SPV-006",
                    f"duplicate checkpoint identifier: {identifier}",
                    section="Acceptance Criteria",
                    line=ac.line,
                )
            )
        seen.add(identifier)
    return issues


def _check_validation_commands(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    validation = next((s for s in parsed.sections if s.heading == "Validation"), None)
    if validation is None:
        return []
    vc = next((s for s in validation.subsections if s.heading == "Validation Commands"), None)
    if vc is None:
        return []
    if _NOT_SPECIFIED in vc.body:
        return []
    for match in _FENCE_CONTENT_RE.finditer(vc.body):
        if any(line.strip() for line in match.group(1).splitlines()):
            return []
    return [
        _error(
            "SPV-007",
            "Validation Commands has no executable command and does not state that "
            "none is specified",
            section="Validation Commands",
            line=vc.line,
        )
    ]


def _check_safety_boundary(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    safety = next((s for s in parsed.sections if s.heading == "Safety"), None)
    if safety is None:
        return []
    boundary = next(
        (s for s in safety.subsections if s.heading == _SAFETY_BOUNDARY_SUBSECTION), None
    )
    if boundary is None:
        return []
    if not boundary.body.strip():
        return [
            _error(
                "SPV-008",
                "Agent safety boundary is empty",
                section=_SAFETY_BOUNDARY_SUBSECTION,
                line=boundary.line,
            )
        ]
    return []


def _check_contract_reference(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    section = next(
        (s for s in parsed.sections if s.heading == "Full Task Contract Reference"), None
    )
    if section is None:
        return []
    body = section.body.strip()
    if not body:
        return [
            _error(
                "SPV-009",
                "Full Task Contract Reference is empty",
                section="Full Task Contract Reference",
                line=section.line,
            )
        ]
    if len(body.splitlines()) == 1 and _PATH_ONLY_RE.match(body):
        return [
            _error(
                "SPV-009",
                "Full Task Contract Reference contains only a path reference, not the "
                "Contract body",
                section="Full Task Contract Reference",
                line=section.line,
            )
        ]
    return []


def _check_unresolved_markers(parsed: _ParsedSpecification) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for section in parsed.sections:
        if section.heading not in _NORMATIVE_SECTION_HEADINGS:
            continue
        for match in _UNRESOLVED_MARKER_RE.finditer(section.body):
            issues.append(
                _warning(
                    "SPV-012",
                    f"unresolved marker {match.group(0)!r} in section '# {section.heading}'",
                    section=section.heading,
                    line=_line_in_block(section, match.start()),
                )
            )
    return issues


def _sort_key(issue: ValidationIssue) -> tuple[int, str, str]:
    return (issue.line if issue.line is not None else 2**31, issue.code, issue.message)


def validate_specification_text(
    text: str, *, task_number: int, specification_path: Path
) -> SpecificationValidationResult:
    """Pure: validates `text` as a Task 042-format Specification for
    `task_number`. No filesystem or network access - identical text always
    returns an equivalent result with stably ordered issues (SPV-013)."""
    parsed = _parse_specification(text)

    issues: list[ValidationIssue] = []
    issues.extend(_check_title(parsed, task_number))
    issues.extend(_check_provenance(parsed))
    issues.extend(_check_structure(parsed))
    issues.extend(_check_acceptance_criteria(parsed))
    issues.extend(_check_validation_commands(parsed))
    issues.extend(_check_safety_boundary(parsed))
    issues.extend(_check_contract_reference(parsed))
    issues.extend(_check_unresolved_markers(parsed))

    ordered = sorted(issues, key=_sort_key)
    errors = tuple(issue for issue in ordered if issue.severity is ValidationSeverity.ERROR)
    warnings = tuple(issue for issue in ordered if issue.severity is ValidationSeverity.WARNING)
    return SpecificationValidationResult(
        task_number=task_number,
        specification_path=specification_path,
        passed=not errors,
        errors=errors,
        warnings=warnings,
    )


def _find_specification_path(task_number: int, directory: Path) -> Path:
    matches = sorted(directory.glob(f"{task_number:03d}-*.md")) if directory.is_dir() else []
    if not matches:
        raise SpecificationNotFoundError(
            f"no Specification found for Task {task_number:03d} in {directory}"
        )
    if len(matches) > 1:
        joined = ", ".join(str(match) for match in matches)
        raise AmbiguousSpecificationError(
            f"multiple candidate Specifications for Task {task_number:03d}: {joined}"
        )
    return matches[0]


def validate_specification_file(
    repository_root: Path, task_number: int
) -> SpecificationValidationResult:
    """Resolves exactly one `specifications/NNN-*.md` file under
    `repository_root` and validates it. Read-only - never writes anything.
    Raises a dedicated `SpecificationValidationError` subclass for any
    operational failure (invalid Task number, missing/ambiguous file,
    unreadable file); a document that loads fine but fails validation rules
    is reported via the returned result's `passed = False`, not raised."""
    if task_number <= 0:
        raise InvalidTaskNumberError(f"invalid task number: {task_number}")
    directory = repository_root / SPECIFICATIONS_DIR
    path = _find_specification_path(task_number, directory)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecificationLoadError(f"failed to read {path}: {exc}") from exc
    return validate_specification_text(text, task_number=task_number, specification_path=path)


def _issue_to_dict(issue: ValidationIssue) -> dict[str, object]:
    return {
        "code": issue.code,
        "severity": issue.severity.value,
        "message": issue.message,
        "section": issue.section,
        "line": issue.line,
    }


def _render_text_report(result: SpecificationValidationResult) -> str:
    lines = [
        f"task: {result.task_number:03d}",
        f"specification: {result.specification_path.as_posix()}",
        f"result: {'PASS' if result.passed else 'FAILED'}",
    ]
    if result.errors:
        lines.append(f"errors ({len(result.errors)}):")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in result.errors)
    if result.warnings:
        lines.append(f"warnings ({len(result.warnings)}):")
        lines.extend(f"  - [{issue.code}] {issue.message}" for issue in result.warnings)
    if not result.errors and not result.warnings:
        lines.append("no issues found.")
    return "\n".join(lines) + "\n"


def _render_json_report(result: SpecificationValidationResult) -> str:
    payload = {
        "task_number": result.task_number,
        "specification_path": result.specification_path.as_posix(),
        "passed": result.passed,
        "errors": [_issue_to_dict(issue) for issue in result.errors],
        "warnings": [_issue_to_dict(issue) for issue in result.warnings],
    }
    return json.dumps(payload, indent=2) + "\n"


def render_validation_report(
    result: SpecificationValidationResult, *, output_format: str = "text"
) -> str:
    if output_format == "json":
        return _render_json_report(result)
    return _render_text_report(result)
