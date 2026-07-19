"""Task 053: Review Decision Model.

A provider-neutral, deterministic, typed representation of a review's
findings and final decision. Later GitHub Review API submission, rework
triggering, and merge-readiness automation all consume this one
authoritative result - none of that integration exists yet; this module
only defines the decision model those future tasks will build on.

The decision and severity counts are never accepted as independently
supplied data - they are always derived from `findings`, and that
invariant is enforced both by `build_review_report` (the sanctioned
constructor) and by `ReviewReport.__post_init__` itself, so it cannot be
bypassed by constructing a `ReviewReport` directly.

Deliberately excluded: GitHub-specific types or event names anywhere in
this module, GitHub Review API calls, GitHub App/token handling, LLM
invocation, and the `approved`/`changes_required`/`comment_only` ->
`APPROVE`/`REQUEST_CHANGES`/`COMMENT` provider mapping - all left to a
later task.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath
from types import MappingProxyType


class ReviewDecision(StrEnum):
    APPROVED = "approved"
    CHANGES_REQUIRED = "changes_required"
    COMMENT_ONLY = "comment_only"


class ReviewSeverity(StrEnum):
    BLOCKER = "blocker"
    WARNING = "warning"
    COMMENT = "comment"


_SEVERITY_RANK: dict[ReviewSeverity, int] = {
    ReviewSeverity.BLOCKER: 0,
    ReviewSeverity.WARNING: 1,
    ReviewSeverity.COMMENT: 2,
}


class ReviewDecisionError(ValueError):
    """Base for review decision-model data that cannot be safely accepted."""


class InvalidReviewLocationError(ReviewDecisionError):
    """A `ReviewLocation`'s fields are invalid or internally inconsistent."""


class InvalidReviewFindingError(ReviewDecisionError):
    """A `ReviewFinding`'s fields are invalid."""


class AmbiguousReviewFindingsError(ReviewDecisionError):
    """Two or more findings share the same severity/code/message/location -
    reporting them both would be ambiguous."""


class InvalidReviewReportPayloadError(ReviewDecisionError):
    """A serialized payload could not be parsed into a valid `ReviewReport`."""


class ReviewReportDerivationMismatchError(ReviewDecisionError):
    """A declared `decision` or `counts` value contradicts what is actually
    derived from `findings`."""


@dataclass(frozen=True)
class ReviewLocation:
    path: str | None = None
    line: int | None = None
    start_line: int | None = None
    side: str | None = None
    symbol: str | None = None

    def __post_init__(self) -> None:
        if self.path is not None:
            if self.path == "" or self.path.strip() != self.path:
                raise InvalidReviewLocationError(
                    f"path must not be empty or have surrounding whitespace: {self.path!r}"
                )
            if self.path.startswith("/"):
                raise InvalidReviewLocationError(
                    f"path must be repository-relative, not absolute: {self.path!r}"
                )
            if any(part == ".." for part in PurePosixPath(self.path).parts):
                raise InvalidReviewLocationError(
                    f"path must not contain '..' segments: {self.path!r}"
                )
        if self.line is not None and self.line < 1:
            raise InvalidReviewLocationError(f"line must be a positive integer: {self.line!r}")
        if self.start_line is not None:
            if self.start_line < 1:
                raise InvalidReviewLocationError(
                    f"start_line must be a positive integer: {self.start_line!r}"
                )
            if self.line is None:
                raise InvalidReviewLocationError("start_line requires line to also be set")
            if self.start_line > self.line:
                raise InvalidReviewLocationError(
                    f"start_line ({self.start_line}) must be <= line ({self.line})"
                )
        if (self.line is not None or self.start_line is not None) and self.path is None:
            raise InvalidReviewLocationError("line/start_line require path to also be set")


@dataclass(frozen=True)
class ReviewFinding:
    severity: ReviewSeverity
    code: str
    message: str
    location: ReviewLocation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.severity, ReviewSeverity):
            raise InvalidReviewFindingError(f"severity must be a ReviewSeverity: {self.severity!r}")
        if not self.code or not self.code.strip():
            raise InvalidReviewFindingError(
                f"code must not be empty or whitespace-only: {self.code!r}"
            )
        if not self.message or not self.message.strip():
            raise InvalidReviewFindingError(
                f"message must not be empty or whitespace-only: {self.message!r}"
            )


def _derive_counts(findings: Sequence[ReviewFinding]) -> dict[ReviewSeverity, int]:
    counts = dict.fromkeys(ReviewSeverity, 0)
    for finding in findings:
        counts[finding.severity] += 1
    return counts


def _derive_decision(counts: Mapping[ReviewSeverity, int]) -> ReviewDecision:
    if counts[ReviewSeverity.BLOCKER] > 0:
        return ReviewDecision.CHANGES_REQUIRED
    if counts[ReviewSeverity.WARNING] > 0 or counts[ReviewSeverity.COMMENT] > 0:
        return ReviewDecision.COMMENT_ONLY
    return ReviewDecision.APPROVED


@dataclass(frozen=True)
class ReviewReport:
    decision: ReviewDecision
    findings: tuple[ReviewFinding, ...]
    counts: Mapping[ReviewSeverity, int]
    summary: str | None = None
    metadata: Mapping[str, str] | None = None

    def __post_init__(self) -> None:
        # Re-derive and compare, rather than trust `decision`/`counts` -
        # this is what makes the "never independently supplied" invariant
        # hold even against a `ReviewReport` constructed directly, not just
        # through `build_review_report`.
        _check_no_ambiguous_duplicates(self.findings)
        expected_counts = _derive_counts(self.findings)
        for severity in ReviewSeverity:
            actual = self.counts.get(severity, 0)
            if actual != expected_counts[severity]:
                raise ReviewReportDerivationMismatchError(
                    f"counts[{severity.value!r}]={actual!r} does not match the count derived "
                    f"from findings ({expected_counts[severity]!r})"
                )
        expected_decision = _derive_decision(expected_counts)
        if self.decision is not expected_decision:
            raise ReviewReportDerivationMismatchError(
                f"decision {self.decision.value!r} does not match the decision derived from "
                f"findings ({expected_decision.value!r})"
            )


def _location_identity(location: ReviewLocation | None) -> tuple | None:
    if location is None:
        return None
    return (location.path, location.line, location.start_line, location.side, location.symbol)


def _check_no_ambiguous_duplicates(findings: Sequence[ReviewFinding]) -> None:
    seen: set[tuple] = set()
    for finding in findings:
        identity = (
            finding.severity,
            finding.code,
            finding.message,
            _location_identity(finding.location),
        )
        if identity in seen:
            raise AmbiguousReviewFindingsError(
                f"duplicate finding with the same severity/code/message/location: "
                f"[{finding.severity.value}] {finding.code}: {finding.message}"
            )
        seen.add(identity)


def _sort_key(finding: ReviewFinding) -> tuple:
    location = finding.location
    location_key = (
        (location.path or "", location.line or -1, location.start_line or -1)
        if location is not None
        else ("", -1, -1)
    )
    return (_SEVERITY_RANK[finding.severity], finding.code, location_key, finding.message)


def build_review_report(
    findings: Sequence[ReviewFinding],
    *,
    summary: str | None = None,
    metadata: Mapping[str, str] | None = None,
) -> ReviewReport:
    """The sanctioned constructor: derives `decision`/`counts` from
    `findings` - never accept them as separate inputs. Raises
    `AmbiguousReviewFindingsError` on duplicate findings before deriving
    anything."""
    findings_list = list(findings)
    _check_no_ambiguous_duplicates(findings_list)
    ordered = tuple(sorted(findings_list, key=_sort_key))
    counts = _derive_counts(ordered)
    decision = _derive_decision(counts)
    return ReviewReport(
        decision=decision,
        findings=ordered,
        counts=MappingProxyType(counts),
        summary=summary,
        metadata=MappingProxyType(dict(metadata)) if metadata is not None else None,
    )


def _location_to_dict(location: ReviewLocation) -> dict:
    return {
        "path": location.path,
        "line": location.line,
        "start_line": location.start_line,
        "side": location.side,
        "symbol": location.symbol,
    }


def _finding_to_dict(finding: ReviewFinding) -> dict:
    return {
        "severity": finding.severity.value,
        "code": finding.code,
        "message": finding.message,
        "location": _location_to_dict(finding.location) if finding.location is not None else None,
    }


def review_report_to_dict(report: ReviewReport) -> dict:
    """Deterministic schema and key order - safe to `json.dumps` directly."""
    return {
        "decision": report.decision.value,
        "counts": {severity.value: report.counts[severity] for severity in ReviewSeverity},
        "findings": [_finding_to_dict(finding) for finding in report.findings],
        "summary": report.summary,
        "metadata": dict(report.metadata) if report.metadata is not None else None,
    }


def _require_str_or_none(payload: dict, field: str, *, context: str) -> str | None:
    value = payload.get(field)
    if value is not None and not isinstance(value, str):
        raise InvalidReviewReportPayloadError(f"{context}.{field} must be a string or null")
    return value


def _require_int_or_none(payload: dict, field: str, *, context: str) -> int | None:
    value = payload.get(field)
    if value is not None and not isinstance(value, int):
        raise InvalidReviewReportPayloadError(f"{context}.{field} must be an integer or null")
    return value


def _location_from_dict(payload: object, *, context: str) -> ReviewLocation:
    if not isinstance(payload, dict):
        raise InvalidReviewReportPayloadError(f"{context} must be an object")
    path = _require_str_or_none(payload, "path", context=context)
    line = _require_int_or_none(payload, "line", context=context)
    start_line = _require_int_or_none(payload, "start_line", context=context)
    side = _require_str_or_none(payload, "side", context=context)
    symbol = _require_str_or_none(payload, "symbol", context=context)
    try:
        return ReviewLocation(path=path, line=line, start_line=start_line, side=side, symbol=symbol)
    except InvalidReviewLocationError as exc:
        raise InvalidReviewReportPayloadError(f"{context}: {exc}") from exc


def _finding_from_dict(payload: object, index: int) -> ReviewFinding:
    context = f"findings[{index}]"
    if not isinstance(payload, dict):
        raise InvalidReviewReportPayloadError(f"{context} must be an object")

    severity_raw = payload.get("severity")
    try:
        severity = ReviewSeverity(severity_raw)
    except ValueError:
        raise InvalidReviewReportPayloadError(
            f"{context}.severity is not a valid ReviewSeverity: {severity_raw!r}"
        ) from None

    code = payload.get("code")
    if not isinstance(code, str):
        raise InvalidReviewReportPayloadError(f"{context}.code must be a string")
    message = payload.get("message")
    if not isinstance(message, str):
        raise InvalidReviewReportPayloadError(f"{context}.message must be a string")

    location_payload = payload.get("location")
    location = (
        _location_from_dict(location_payload, context=f"{context}.location")
        if location_payload is not None
        else None
    )

    try:
        return ReviewFinding(severity=severity, code=code, message=message, location=location)
    except InvalidReviewFindingError as exc:
        raise InvalidReviewReportPayloadError(f"{context}: {exc}") from exc


def review_report_from_dict(payload: object) -> ReviewReport:
    """Parses and validates `payload` into a `ReviewReport`, deriving
    `decision`/`counts` from its findings exactly as `build_review_report`
    would. If the payload also declares `decision`/`counts`, they are
    cross-checked and rejected (`ReviewReportDerivationMismatchError`) if
    they contradict the derived values - a declared field is never
    trusted over derivation."""
    if not isinstance(payload, dict):
        raise InvalidReviewReportPayloadError(
            f"payload must be an object, got {type(payload).__name__}"
        )

    raw_findings = payload.get("findings")
    if not isinstance(raw_findings, list):
        raise InvalidReviewReportPayloadError("payload.findings must be a list")
    findings = [_finding_from_dict(item, index) for index, item in enumerate(raw_findings)]

    summary = _require_str_or_none(payload, "summary", context="payload")

    metadata_raw = payload.get("metadata")
    metadata: dict[str, str] | None = None
    if metadata_raw is not None:
        if not isinstance(metadata_raw, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in metadata_raw.items()
        ):
            raise InvalidReviewReportPayloadError(
                "payload.metadata must be an object of string to string"
            )
        metadata = metadata_raw

    report = build_review_report(findings, summary=summary, metadata=metadata)

    if "decision" in payload and payload["decision"] is not None:
        declared_raw = payload["decision"]
        try:
            declared_decision = ReviewDecision(declared_raw)
        except ValueError:
            raise InvalidReviewReportPayloadError(
                f"payload.decision is not a valid ReviewDecision: {declared_raw!r}"
            ) from None
        if declared_decision is not report.decision:
            raise ReviewReportDerivationMismatchError(
                f"payload.decision {declared_decision.value!r} contradicts the decision "
                f"derived from findings ({report.decision.value!r})"
            )

    if "counts" in payload and payload["counts"] is not None:
        declared_counts = payload["counts"]
        if not isinstance(declared_counts, dict):
            raise InvalidReviewReportPayloadError("payload.counts must be an object")
        for severity in ReviewSeverity:
            declared_value = declared_counts.get(severity.value, 0)
            if declared_value != report.counts[severity]:
                raise ReviewReportDerivationMismatchError(
                    f"payload.counts[{severity.value!r}]={declared_value!r} contradicts the "
                    f"derived count ({report.counts[severity]!r})"
                )

    return report


def validate_review_report_payload(payload: object) -> None:
    """Raises a `ReviewDecisionError` subclass if `payload` cannot become a
    valid `ReviewReport`; returns nothing on success."""
    review_report_from_dict(payload)


def _render_location(location: ReviewLocation) -> str:
    parts: list[str] = []
    if location.path:
        parts.append(location.path)
    if location.line is not None:
        if location.start_line is not None and location.start_line != location.line:
            parts.append(f"{location.start_line}-{location.line}")
        else:
            parts.append(str(location.line))
    text = ":".join(parts)
    if location.symbol:
        text = f"{text} ({location.symbol})" if text else f"({location.symbol})"
    return text


def render_review_report(report: ReviewReport) -> str:
    """Deterministic text rendering - always includes the decision, every
    severity's count, and every finding, in `report.findings`'s (already
    deterministic) order."""
    lines = [
        f"decision: {report.decision.value}",
        "counts: "
        + " ".join(f"{severity.value}={report.counts[severity]}" for severity in ReviewSeverity),
    ]
    if report.summary:
        lines.append(f"summary: {report.summary}")
    lines.append(f"findings ({len(report.findings)}):")
    for finding in report.findings:
        location_text = _render_location(finding.location) if finding.location else ""
        suffix = f" [{location_text}]" if location_text else ""
        lines.append(f"  - [{finding.severity.value}] {finding.code}: {finding.message}{suffix}")
    return "\n".join(lines)
