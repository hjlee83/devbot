from __future__ import annotations

from types import MappingProxyType

import pytest

from devbot.review_decision import (
    AmbiguousReviewFindingsError,
    InvalidReviewFindingError,
    InvalidReviewLocationError,
    InvalidReviewReportPayloadError,
    ReviewDecision,
    ReviewDecisionError,
    ReviewFinding,
    ReviewLocation,
    ReviewReport,
    ReviewReportDerivationMismatchError,
    ReviewSeverity,
    build_review_report,
    render_review_report,
    review_report_from_dict,
    review_report_to_dict,
    validate_review_report_payload,
)


def _finding(**overrides: object) -> ReviewFinding:
    defaults: dict[str, object] = dict(
        severity=ReviewSeverity.WARNING,
        code="CODE-1",
        message="a message",
    )
    defaults.update(overrides)
    return ReviewFinding(**defaults)  # type: ignore[arg-type]


def _counts(
    *, blocker: object = 0, warning: object = 0, comment: object = 0
) -> dict[ReviewSeverity, object]:
    return {
        ReviewSeverity.BLOCKER: blocker,
        ReviewSeverity.WARNING: warning,
        ReviewSeverity.COMMENT: comment,
    }


# --------------------------------------------------------------------------
# ReviewLocation validation.
# --------------------------------------------------------------------------


def test_location_accepts_full_valid_fields() -> None:
    location = ReviewLocation(
        path="src/foo.py", line=10, start_line=8, side="RIGHT", symbol="my_func"
    )
    assert location.path == "src/foo.py"
    assert location.start_line == 8


def test_location_all_none_is_valid() -> None:
    ReviewLocation()


def test_location_rejects_absolute_path() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="/etc/passwd", line=1)


def test_location_rejects_parent_traversal_path() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="../secrets.py", line=1)


def test_location_rejects_empty_path() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="")


def test_location_rejects_whitespace_padded_path() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path=" src/foo.py ")


def test_location_rejects_non_positive_line() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="a.py", line=0)


def test_location_rejects_start_line_greater_than_line() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="a.py", line=5, start_line=10)


def test_location_rejects_start_line_without_line() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(path="a.py", start_line=5)


def test_location_rejects_line_without_path() -> None:
    with pytest.raises(InvalidReviewLocationError):
        ReviewLocation(line=5)


def test_location_start_line_equal_to_line_is_valid() -> None:
    ReviewLocation(path="a.py", line=5, start_line=5)


# --------------------------------------------------------------------------
# ReviewFinding validation.
# --------------------------------------------------------------------------


def test_finding_rejects_empty_code() -> None:
    with pytest.raises(InvalidReviewFindingError):
        _finding(code="")


def test_finding_rejects_whitespace_only_code() -> None:
    with pytest.raises(InvalidReviewFindingError):
        _finding(code="   ")


def test_finding_rejects_empty_message() -> None:
    with pytest.raises(InvalidReviewFindingError):
        _finding(message="")


def test_finding_rejects_whitespace_only_message() -> None:
    with pytest.raises(InvalidReviewFindingError):
        _finding(message="\t\n")


def test_finding_rejects_non_enum_severity() -> None:
    with pytest.raises(InvalidReviewFindingError):
        ReviewFinding(severity="blocker", code="C", message="m")  # type: ignore[arg-type]


# --------------------------------------------------------------------------
# Decision derivation.
# --------------------------------------------------------------------------


def test_empty_findings_produce_approved() -> None:
    report = build_review_report([])
    assert report.decision is ReviewDecision.APPROVED
    assert dict(report.counts) == {
        ReviewSeverity.BLOCKER: 0,
        ReviewSeverity.WARNING: 0,
        ReviewSeverity.COMMENT: 0,
    }
    assert report.findings == ()


@pytest.mark.parametrize(
    ("severity", "expected"),
    [
        (ReviewSeverity.BLOCKER, ReviewDecision.CHANGES_REQUIRED),
        (ReviewSeverity.WARNING, ReviewDecision.COMMENT_ONLY),
        (ReviewSeverity.COMMENT, ReviewDecision.COMMENT_ONLY),
    ],
)
def test_single_finding_decision_per_severity(
    severity: ReviewSeverity, expected: ReviewDecision
) -> None:
    report = build_review_report([_finding(severity=severity)])
    assert report.decision is expected


def test_blocker_takes_precedence_over_warning_and_comment() -> None:
    report = build_review_report(
        [
            _finding(severity=ReviewSeverity.COMMENT, code="C1"),
            _finding(severity=ReviewSeverity.WARNING, code="C2"),
            _finding(severity=ReviewSeverity.BLOCKER, code="C3"),
        ]
    )
    assert report.decision is ReviewDecision.CHANGES_REQUIRED


def test_counts_are_derived_and_cannot_drift() -> None:
    report = build_review_report(
        [
            _finding(severity=ReviewSeverity.BLOCKER, code="C1"),
            _finding(severity=ReviewSeverity.BLOCKER, code="C2"),
            _finding(severity=ReviewSeverity.WARNING, code="C3"),
        ]
    )
    assert report.counts[ReviewSeverity.BLOCKER] == 2
    assert report.counts[ReviewSeverity.WARNING] == 1
    assert report.counts[ReviewSeverity.COMMENT] == 0


def test_counts_and_metadata_are_immutable_mappings() -> None:
    report = build_review_report([], metadata={"source": "test"})
    assert isinstance(report.counts, MappingProxyType)
    assert isinstance(report.metadata, MappingProxyType)
    with pytest.raises(TypeError):
        report.counts[ReviewSeverity.BLOCKER] = 5  # type: ignore[index]


# --------------------------------------------------------------------------
# Deterministic ordering.
# --------------------------------------------------------------------------


def test_findings_ordered_by_severity_rank_first() -> None:
    report = build_review_report(
        [
            _finding(severity=ReviewSeverity.COMMENT, code="C1"),
            _finding(severity=ReviewSeverity.BLOCKER, code="B1"),
            _finding(severity=ReviewSeverity.WARNING, code="W1"),
        ]
    )
    assert [f.severity for f in report.findings] == [
        ReviewSeverity.BLOCKER,
        ReviewSeverity.WARNING,
        ReviewSeverity.COMMENT,
    ]


def test_findings_with_same_severity_ordered_by_code() -> None:
    report = build_review_report(
        [
            _finding(severity=ReviewSeverity.WARNING, code="W2"),
            _finding(severity=ReviewSeverity.WARNING, code="W1"),
        ]
    )
    assert [f.code for f in report.findings] == ["W1", "W2"]


def test_ordering_is_deterministic_across_repeated_calls() -> None:
    findings = [
        _finding(severity=ReviewSeverity.WARNING, code="W2", message="b"),
        _finding(severity=ReviewSeverity.BLOCKER, code="B1", message="a"),
        _finding(severity=ReviewSeverity.COMMENT, code="C1", message="c"),
    ]
    first = build_review_report(list(findings))
    second = build_review_report(list(reversed(findings)))
    assert [f.code for f in first.findings] == [f.code for f in second.findings]


# --------------------------------------------------------------------------
# Ambiguous duplicates.
# --------------------------------------------------------------------------


def test_exact_duplicate_findings_raise_ambiguous_error() -> None:
    with pytest.raises(AmbiguousReviewFindingsError):
        build_review_report([_finding(code="B1"), _finding(code="B1")])


def test_same_code_different_message_is_not_ambiguous() -> None:
    report = build_review_report(
        [_finding(code="B1", message="m1"), _finding(code="B1", message="m2")]
    )
    assert len(report.findings) == 2


def test_same_code_different_location_is_not_ambiguous() -> None:
    report = build_review_report(
        [
            _finding(code="B1", location=ReviewLocation(path="a.py", line=1)),
            _finding(code="B1", location=ReviewLocation(path="b.py", line=1)),
        ]
    )
    assert len(report.findings) == 2


# --------------------------------------------------------------------------
# Direct ReviewReport construction bypassing build_review_report.
# --------------------------------------------------------------------------


def test_direct_construction_with_wrong_decision_rejected() -> None:
    with pytest.raises(ReviewReportDerivationMismatchError):
        ReviewReport(
            decision=ReviewDecision.APPROVED,
            findings=(_finding(severity=ReviewSeverity.BLOCKER, code="B1"),),
            counts=MappingProxyType(
                {ReviewSeverity.BLOCKER: 1, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
            ),
        )


def test_direct_construction_with_wrong_counts_rejected() -> None:
    with pytest.raises(ReviewReportDerivationMismatchError):
        ReviewReport(
            decision=ReviewDecision.CHANGES_REQUIRED,
            findings=(_finding(severity=ReviewSeverity.BLOCKER, code="B1"),),
            counts=MappingProxyType(
                {ReviewSeverity.BLOCKER: 99, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
            ),
        )


def test_direct_construction_with_duplicate_findings_rejected() -> None:
    finding = _finding(code="B1")
    with pytest.raises(AmbiguousReviewFindingsError):
        ReviewReport(
            decision=ReviewDecision.COMMENT_ONLY,
            findings=(finding, finding),
            counts=MappingProxyType(
                {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 2, ReviewSeverity.COMMENT: 0}
            ),
        )


def test_direct_construction_with_correct_derivation_succeeds() -> None:
    report = ReviewReport(
        decision=ReviewDecision.APPROVED,
        findings=(),
        counts=MappingProxyType(
            {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
        ),
    )
    assert report.decision is ReviewDecision.APPROVED


# --------------------------------------------------------------------------
# Immutability: `frozen=True` alone does not stop a mutable container
# passed directly from being mutated afterward - `__post_init__` must
# normalize into genuinely immutable, independent copies.
# --------------------------------------------------------------------------


def test_mutating_original_findings_list_after_construction_does_not_affect_report() -> None:
    findings: list[ReviewFinding] = []
    counts = {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
    report = ReviewReport(decision=ReviewDecision.APPROVED, findings=findings, counts=counts)  # type: ignore[arg-type]

    findings.append(_finding(severity=ReviewSeverity.BLOCKER, code="B1"))
    counts[ReviewSeverity.BLOCKER] = 1

    assert report.findings == ()
    assert report.decision is ReviewDecision.APPROVED
    assert dict(report.counts)[ReviewSeverity.BLOCKER] == 0


def test_mutating_original_counts_dict_after_construction_does_not_affect_report() -> None:
    counts = {ReviewSeverity.BLOCKER: 0, ReviewSeverity.WARNING: 0, ReviewSeverity.COMMENT: 0}
    report = ReviewReport(decision=ReviewDecision.APPROVED, findings=[], counts=counts)  # type: ignore[arg-type]

    counts[ReviewSeverity.BLOCKER] = 99

    assert dict(report.counts)[ReviewSeverity.BLOCKER] == 0


def test_mutating_original_metadata_dict_after_construction_does_not_affect_report() -> None:
    metadata = {"source": "ci"}
    report = ReviewReport(
        decision=ReviewDecision.APPROVED,
        findings=[],  # type: ignore[arg-type]
        counts=_counts(),
        metadata=metadata,
    )

    metadata["source"] = "tampered"

    assert dict(report.metadata or {}) == {"source": "ci"}


def test_report_counts_is_immune_to_mapping_proxy_mutation_attempt() -> None:
    report = build_review_report([])
    with pytest.raises(TypeError):
        report.counts[ReviewSeverity.BLOCKER] = 5  # type: ignore[index]


def test_direct_construction_findings_element_wrong_type_rejected() -> None:
    with pytest.raises(InvalidReviewFindingError):
        ReviewReport(
            decision=ReviewDecision.APPROVED,
            findings=[{"not": "a finding"}],  # type: ignore[list-item]
            counts=_counts(),  # type: ignore[arg-type]
        )


def test_direct_construction_counts_non_int_value_rejected() -> None:
    with pytest.raises(ReviewReportDerivationMismatchError):
        ReviewReport(
            decision=ReviewDecision.APPROVED,
            findings=(),
            counts=_counts(blocker="0"),  # type: ignore[arg-type]
        )


def test_direct_construction_counts_not_a_mapping_rejected() -> None:
    with pytest.raises(ReviewReportDerivationMismatchError):
        ReviewReport(decision=ReviewDecision.APPROVED, findings=(), counts=["not", "a", "mapping"])  # type: ignore[arg-type]


def test_direct_construction_metadata_not_a_mapping_rejected() -> None:
    with pytest.raises(ReviewDecisionError):
        ReviewReport(
            decision=ReviewDecision.APPROVED,
            findings=(),
            counts=_counts(),
            metadata=["not", "a", "mapping"],  # type: ignore[arg-type]
        )


def test_direct_construction_metadata_non_string_value_rejected() -> None:
    with pytest.raises(ReviewDecisionError):
        ReviewReport(
            decision=ReviewDecision.APPROVED,
            findings=(),
            counts=_counts(),
            metadata={"key": 42},  # type: ignore[dict-item]
        )


def test_direct_construction_decision_wrong_type_rejected() -> None:
    with pytest.raises(ReviewReportDerivationMismatchError):
        ReviewReport(
            decision="approved",  # type: ignore[arg-type]
            findings=(),
            counts=_counts(),
        )


def test_build_review_report_findings_are_independent_of_caller_list() -> None:
    findings = [_finding(code="C1")]
    report = build_review_report(findings)
    findings.append(_finding(code="C2"))
    assert len(report.findings) == 1


# --------------------------------------------------------------------------
# JSON round-trip.
# --------------------------------------------------------------------------


def test_json_round_trip_preserves_report() -> None:
    report = build_review_report(
        [
            _finding(
                severity=ReviewSeverity.BLOCKER,
                code="B1",
                message="bad",
                location=ReviewLocation(path="a.py", line=1, start_line=1, side="RIGHT"),
            ),
            _finding(severity=ReviewSeverity.COMMENT, code="C1", message="fyi"),
        ],
        summary="found issues",
        metadata={"reviewer": "devbot"},
    )

    payload = review_report_to_dict(report)
    restored = review_report_from_dict(payload)

    assert restored.decision == report.decision
    assert dict(restored.counts) == dict(report.counts)
    assert restored.summary == report.summary
    assert dict(restored.metadata or {}) == dict(report.metadata or {})
    assert len(restored.findings) == len(report.findings)
    for original, round_tripped in zip(report.findings, restored.findings, strict=True):
        assert original.severity == round_tripped.severity
        assert original.code == round_tripped.code
        assert original.message == round_tripped.message
        assert original.location == round_tripped.location


def test_json_round_trip_with_no_location_or_summary_or_metadata() -> None:
    report = build_review_report([_finding()])
    payload = review_report_to_dict(report)
    restored = review_report_from_dict(payload)
    assert restored.findings[0].location is None
    assert restored.summary is None
    assert restored.metadata is None


def test_validate_review_report_payload_returns_none_on_success() -> None:
    payload = review_report_to_dict(build_review_report([]))
    assert validate_review_report_payload(payload) is None


# --------------------------------------------------------------------------
# Malformed payloads fail closed.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "not-a-dict",
        123,
        None,
        [],
    ],
)
def test_non_dict_payload_rejected(payload: object) -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(payload)


def test_findings_not_a_list_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict({"findings": "nope"})


def test_finding_not_an_object_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict({"findings": ["not-an-object"]})


def test_finding_invalid_severity_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {"findings": [{"severity": "critical", "code": "C1", "message": "m"}]}
        )


def test_finding_non_string_code_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {"findings": [{"severity": "blocker", "code": 123, "message": "m"}]}
        )


def test_finding_non_string_message_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {"findings": [{"severity": "blocker", "code": "C1", "message": None}]}
        )


def test_finding_empty_code_rejected_via_payload() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {"findings": [{"severity": "blocker", "code": "", "message": "m"}]}
        )


def test_location_invalid_line_type_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {
                "findings": [
                    {
                        "severity": "blocker",
                        "code": "C1",
                        "message": "m",
                        "location": {"path": "a.py", "line": "ten"},
                    }
                ]
            }
        )


def test_location_absolute_path_rejected_via_payload() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(
            {
                "findings": [
                    {
                        "severity": "blocker",
                        "code": "C1",
                        "message": "m",
                        "location": {"path": "/etc/passwd", "line": 1},
                    }
                ]
            }
        )


def test_summary_non_string_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict({"findings": [], "summary": 42})


def test_metadata_non_string_values_rejected() -> None:
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict({"findings": [], "metadata": {"key": 42}})


def test_declared_decision_contradicting_derivation_rejected() -> None:
    payload = review_report_to_dict(
        build_review_report([_finding(severity=ReviewSeverity.BLOCKER, code="B1")])
    )
    payload["decision"] = "approved"
    with pytest.raises(ReviewReportDerivationMismatchError):
        review_report_from_dict(payload)


def test_declared_decision_invalid_value_rejected() -> None:
    payload = review_report_to_dict(build_review_report([]))
    payload["decision"] = "maybe"
    with pytest.raises(InvalidReviewReportPayloadError):
        review_report_from_dict(payload)


def test_declared_counts_contradicting_derivation_rejected() -> None:
    payload = review_report_to_dict(
        build_review_report([_finding(severity=ReviewSeverity.BLOCKER, code="B1")])
    )
    payload["counts"]["blocker"] = 99
    with pytest.raises(ReviewReportDerivationMismatchError):
        review_report_from_dict(payload)


def test_declared_decision_matching_derivation_is_accepted() -> None:
    payload = review_report_to_dict(
        build_review_report([_finding(severity=ReviewSeverity.BLOCKER, code="B1")])
    )
    report = review_report_from_dict(payload)
    assert report.decision is ReviewDecision.CHANGES_REQUIRED


def test_duplicate_findings_in_payload_rejected() -> None:
    with pytest.raises(AmbiguousReviewFindingsError):
        review_report_from_dict(
            {
                "findings": [
                    {"severity": "blocker", "code": "B1", "message": "m"},
                    {"severity": "blocker", "code": "B1", "message": "m"},
                ]
            }
        )


# --------------------------------------------------------------------------
# Deterministic text rendering.
# --------------------------------------------------------------------------


def test_render_includes_decision_counts_and_all_findings() -> None:
    report = build_review_report(
        [
            _finding(
                severity=ReviewSeverity.BLOCKER,
                code="B1",
                message="bad thing",
                location=ReviewLocation(path="a.py", line=5),
            )
        ],
        summary="one blocker found",
    )
    rendered = render_review_report(report)
    assert "decision: changes_required" in rendered
    assert "blocker=1" in rendered
    assert "warning=0" in rendered
    assert "comment=0" in rendered
    assert "one blocker found" in rendered
    assert "B1" in rendered
    assert "bad thing" in rendered
    assert "a.py:5" in rendered


def test_render_is_deterministic_across_repeated_calls() -> None:
    report = build_review_report(
        [
            _finding(severity=ReviewSeverity.WARNING, code="W1"),
            _finding(severity=ReviewSeverity.COMMENT, code="C1"),
        ]
    )
    assert render_review_report(report) == render_review_report(report)


def test_render_empty_findings() -> None:
    report = build_review_report([])
    rendered = render_review_report(report)
    assert "decision: approved" in rendered
    assert "findings (0):" in rendered


# --------------------------------------------------------------------------
# Error hierarchy.
# --------------------------------------------------------------------------


def test_all_review_errors_share_base_class() -> None:
    error_types = (
        InvalidReviewLocationError,
        InvalidReviewFindingError,
        AmbiguousReviewFindingsError,
        InvalidReviewReportPayloadError,
        ReviewReportDerivationMismatchError,
    )
    for error_type in error_types:
        assert issubclass(error_type, ReviewDecisionError)
