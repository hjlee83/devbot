"""Provider-neutral CI status model (Issue #127).

A fine-grained PAT frequently cannot be granted the "Checks" repository
permission, so `GitHubClient.list_check_runs_for_ref` alone 403s and
cannot be trusted as the single source of truth for the automerge gate.
This module normalizes CI status from several independent sources -
GitHub Actions workflow runs, the combined Statuses API, and check-runs
as an optional bonus source - into one verdict, so the gate never depends
on a single API some deployments cannot reach. No source name is special-
cased in `evaluate_ci_status`; each source is reduced to the same
`CISourceReading`/`CISourceUnavailable` shape before combination.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from devbot.github_client import CombinedCommitStatus, WorkflowRun

_PASSING_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})


class CIVerdict(StrEnum):
    GREEN = "green"
    FAILING = "failing"
    PENDING = "pending"
    UNKNOWN = "unknown"


class CISource(StrEnum):
    WORKFLOW_RUNS = "workflow_runs"
    COMMIT_STATUS = "commit_status"
    CHECK_RUNS = "check_runs"


@dataclass(frozen=True, slots=True)
class CheckRunSummary:
    name: str
    status: str
    conclusion: str | None


@dataclass(frozen=True, slots=True)
class CISourceReading:
    """One source's own read of CI status for a single ref. `verdict` is
    always GREEN, FAILING, or PENDING here - a source that produced no
    usable data is a `CISourceUnavailable`, never a reading."""

    source: CISource
    verdict: CIVerdict
    detail: str


@dataclass(frozen=True, slots=True)
class CISourceUnavailable:
    """A source that could not be consulted at all (permission denied,
    API error) or had no data for this ref (no workflow runs triggered,
    no statuses posted)."""

    source: CISource
    reason: str


@dataclass(frozen=True, slots=True)
class CIStatusResult:
    verdict: CIVerdict
    reason: str
    readings: tuple[CISourceReading, ...]
    unavailable: tuple[CISourceUnavailable, ...]


def summarize_check_runs(
    raw_check_runs: Sequence[dict[str, object]],
) -> tuple[CheckRunSummary, ...]:
    return tuple(
        CheckRunSummary(
            name=str(raw.get("name") or raw.get("id") or "unknown"),
            status=str(raw.get("status") or "unknown"),
            conclusion=(str(raw["conclusion"]) if raw.get("conclusion") is not None else None),
        )
        for raw in raw_check_runs
    )


def classify_workflow_runs(
    runs: Sequence[WorkflowRun],
) -> CISourceReading | CISourceUnavailable:
    if not runs:
        return CISourceUnavailable(CISource.WORKFLOW_RUNS, "이 커밋에 대한 workflow run 없음")

    pending = [run for run in runs if run.status != "completed"]
    if pending:
        names = ", ".join(f"{run.name}: status={run.status}" for run in pending)
        return CISourceReading(CISource.WORKFLOW_RUNS, CIVerdict.PENDING, f"미완료: {names}")

    failing = [run for run in runs if run.conclusion not in _PASSING_CONCLUSIONS]
    if failing:
        names = ", ".join(f"{run.name}: conclusion={run.conclusion}" for run in failing)
        return CISourceReading(CISource.WORKFLOW_RUNS, CIVerdict.FAILING, f"실패: {names}")

    return CISourceReading(
        CISource.WORKFLOW_RUNS, CIVerdict.GREEN, f"workflow run {len(runs)}개 모두 green"
    )


def classify_combined_status(
    status: CombinedCommitStatus,
) -> CISourceReading | CISourceUnavailable:
    if status.total_count == 0:
        return CISourceUnavailable(CISource.COMMIT_STATUS, "등록된 커밋 상태 없음")

    if status.state == "success":
        return CISourceReading(
            CISource.COMMIT_STATUS,
            CIVerdict.GREEN,
            f"커밋 상태 {status.total_count}개 모두 success",
        )
    if status.state == "pending":
        return CISourceReading(CISource.COMMIT_STATUS, CIVerdict.PENDING, "커밋 상태 진행 중")
    return CISourceReading(
        CISource.COMMIT_STATUS, CIVerdict.FAILING, f"커밋 상태=state:{status.state}"
    )


def classify_check_runs(
    check_runs: Sequence[CheckRunSummary],
) -> CISourceReading | CISourceUnavailable:
    if not check_runs:
        return CISourceUnavailable(CISource.CHECK_RUNS, "check-run 없음")

    pending = [check_run for check_run in check_runs if check_run.status != "completed"]
    if pending:
        names = ", ".join(f"{c.name}: status={c.status}" for c in pending)
        return CISourceReading(CISource.CHECK_RUNS, CIVerdict.PENDING, f"미완료: {names}")

    failing = [
        check_run for check_run in check_runs if check_run.conclusion not in _PASSING_CONCLUSIONS
    ]
    if failing:
        names = ", ".join(f"{c.name}: conclusion={c.conclusion}" for c in failing)
        return CISourceReading(CISource.CHECK_RUNS, CIVerdict.FAILING, f"실패: {names}")

    return CISourceReading(
        CISource.CHECK_RUNS, CIVerdict.GREEN, f"check-run {len(check_runs)}개 모두 green"
    )


def evaluate_ci_status(
    readings: Sequence[CISourceReading],
    unavailable: Sequence[CISourceUnavailable],
) -> CIStatusResult:
    """Combine independent per-source CI readings into one verdict.

    - Any FAILING reading -> FAILING: a known failure from one source is
      never masked by another source being green.
    - Else any PENDING reading -> PENDING.
    - Else at least one GREEN reading -> GREEN.
    - No readings at all (every source was unavailable or had no data)
      -> UNKNOWN. Fail-closed (Issue #124's principle): DevBot must not
      merge when it cannot confirm CI status from any source.
    """
    failing = [reading for reading in readings if reading.verdict is CIVerdict.FAILING]
    if failing:
        detail = "; ".join(f"{reading.source.value}: {reading.detail}" for reading in failing)
        return CIStatusResult(CIVerdict.FAILING, detail, tuple(readings), tuple(unavailable))

    pending = [reading for reading in readings if reading.verdict is CIVerdict.PENDING]
    if pending:
        detail = "; ".join(f"{reading.source.value}: {reading.detail}" for reading in pending)
        return CIStatusResult(CIVerdict.PENDING, detail, tuple(readings), tuple(unavailable))

    green = [reading for reading in readings if reading.verdict is CIVerdict.GREEN]
    if green:
        detail = ", ".join(f"{reading.source.value}({reading.detail})" for reading in green)
        return CIStatusResult(CIVerdict.GREEN, detail, tuple(readings), tuple(unavailable))

    if unavailable:
        detail = "; ".join(f"{u.source.value}: {u.reason}" for u in unavailable)
    else:
        detail = "확인 가능한 CI 상태 소스 없음"
    return CIStatusResult(CIVerdict.UNKNOWN, detail, tuple(readings), tuple(unavailable))
