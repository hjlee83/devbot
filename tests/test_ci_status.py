from datetime import datetime

from devbot.ci_status import (
    CISource,
    CISourceReading,
    CISourceUnavailable,
    CIVerdict,
    classify_check_runs,
    classify_combined_status,
    classify_workflow_runs,
    evaluate_ci_status,
    summarize_check_runs,
)
from devbot.github_client import CombinedCommitStatus, WorkflowRun


def _workflow_run(
    *, status: str = "completed", conclusion: str | None = "success", name: str = "ci"
) -> WorkflowRun:
    return WorkflowRun(
        id=1,
        name=name,
        status=status,
        conclusion=conclusion,
        html_url="https://github.com/someone/myrepo/actions/runs/1",
        created_at=datetime(2026, 1, 1),
        head_sha="abc123",
        event="pull_request",
    )


# --- classify_workflow_runs --------------------------------------------


def test_classify_workflow_runs_unavailable_when_no_runs() -> None:
    result = classify_workflow_runs(())

    assert isinstance(result, CISourceUnavailable)
    assert result.source is CISource.WORKFLOW_RUNS


def test_classify_workflow_runs_pending_when_incomplete() -> None:
    result = classify_workflow_runs([_workflow_run(status="in_progress", conclusion=None)])

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.PENDING


def test_classify_workflow_runs_failing_on_bad_conclusion() -> None:
    result = classify_workflow_runs([_workflow_run(conclusion="failure")])

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.FAILING


def test_classify_workflow_runs_green_when_all_passing() -> None:
    result = classify_workflow_runs(
        [_workflow_run(conclusion="success"), _workflow_run(conclusion="neutral")]
    )

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.GREEN


# --- classify_combined_status -------------------------------------------


def test_classify_combined_status_unavailable_when_no_statuses_posted() -> None:
    result = classify_combined_status(CombinedCommitStatus(state="pending", total_count=0))

    assert isinstance(result, CISourceUnavailable)
    assert result.source is CISource.COMMIT_STATUS


def test_classify_combined_status_green_on_success() -> None:
    result = classify_combined_status(CombinedCommitStatus(state="success", total_count=2))

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.GREEN


def test_classify_combined_status_pending() -> None:
    result = classify_combined_status(CombinedCommitStatus(state="pending", total_count=1))

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.PENDING


def test_classify_combined_status_failing() -> None:
    result = classify_combined_status(CombinedCommitStatus(state="failure", total_count=1))

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.FAILING


# --- classify_check_runs -------------------------------------------------


def test_classify_check_runs_unavailable_when_empty() -> None:
    result = classify_check_runs(())

    assert isinstance(result, CISourceUnavailable)
    assert result.source is CISource.CHECK_RUNS


def test_classify_check_runs_green_when_all_passing() -> None:
    check_runs = summarize_check_runs(
        [
            {"name": "ruff", "status": "completed", "conclusion": "success"},
            {"name": "pytest", "status": "completed", "conclusion": "neutral"},
        ]
    )

    result = classify_check_runs(check_runs)

    assert isinstance(result, CISourceReading)
    assert result.verdict is CIVerdict.GREEN


# --- evaluate_ci_status ---------------------------------------------------


def test_evaluate_ci_status_unknown_when_nothing_available() -> None:
    result = evaluate_ci_status(
        [],
        [
            CISourceUnavailable(CISource.WORKFLOW_RUNS, "403"),
            CISourceUnavailable(CISource.COMMIT_STATUS, "no data"),
            CISourceUnavailable(CISource.CHECK_RUNS, "403"),
        ],
    )

    assert result.verdict is CIVerdict.UNKNOWN


def test_evaluate_ci_status_green_when_one_source_confirms_and_rest_unavailable() -> None:
    """Issue #127: a permission gap in one source (e.g. check-runs 403
    under a fine-grained PAT) must not prevent another healthy source
    from confirming CI status."""
    result = evaluate_ci_status(
        [CISourceReading(CISource.WORKFLOW_RUNS, CIVerdict.GREEN, "2개 run 모두 green")],
        [
            CISourceUnavailable(CISource.COMMIT_STATUS, "등록된 커밋 상태 없음"),
            CISourceUnavailable(CISource.CHECK_RUNS, "403 Forbidden"),
        ],
    )

    assert result.verdict is CIVerdict.GREEN


def test_evaluate_ci_status_failing_overrides_green_from_another_source() -> None:
    result = evaluate_ci_status(
        [
            CISourceReading(CISource.WORKFLOW_RUNS, CIVerdict.GREEN, "green"),
            CISourceReading(CISource.COMMIT_STATUS, CIVerdict.FAILING, "실패"),
        ],
        [],
    )

    assert result.verdict is CIVerdict.FAILING


def test_evaluate_ci_status_pending_when_no_failure_but_something_still_running() -> None:
    result = evaluate_ci_status(
        [
            CISourceReading(CISource.WORKFLOW_RUNS, CIVerdict.GREEN, "green"),
            CISourceReading(CISource.COMMIT_STATUS, CIVerdict.PENDING, "진행 중"),
        ],
        [],
    )

    assert result.verdict is CIVerdict.PENDING
