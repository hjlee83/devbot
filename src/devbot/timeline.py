"""GitHub Status Timeline CLI support (Task 018).

Implements the manual-operation half of the `docs/10-github-status-timeline.md`
protocol Task 017 only specified: recording `devbot-timeline:v1` event
markers into a single per-Issue "Timeline comment" (identified by its own
`devbot-timeline:comment:v1` marker), and rendering the human-readable
Status Card (Task 017 section 7) from that event history.

This module owns no GitHub I/O policy beyond "read every comment, find the
one Timeline comment, write back its full body" - callers (`devbot.main`'s
`timeline` subcommand) decide dry-run and which repository/Issue to target.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from devbot.github_client import GitHubClient, GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig

Phase = Literal["queue", "dev", "review"]
EventType = Literal["ready", "start", "end"]

# Identifies the single Timeline comment on an Issue (Task 018 scope item 2),
# distinct from the per-event `devbot-timeline:v1` marker (Task 017 section 4).
COMMENT_MARKER = "<!-- devbot-timeline:comment:v1 -->"
EVENT_MARKER_NAME = "devbot-timeline:v1"
COMMENT_HEADER = "## DevBot Timeline"

_DEFAULT_TZ = ZoneInfo("Asia/Seoul")

_EVENT_LINE_RE = re.compile(r"<!--\s*" + re.escape(EVENT_MARKER_NAME) + r"\s+(.*?)\s*-->")
_TOKEN_RE = re.compile(r"(\w+)=(\S+)")

_RESULT_DISPLAY = {
    "merge-ready": "MERGE READY",
    "request-changes": "REQUEST CHANGES",
}


class TimelineError(RuntimeError):
    """Base class for `devbot timeline` operational errors."""


class TimelineOverlappingStartError(TimelineError):
    """Raised when `start` is requested for a phase/cycle that already has
    an unresolved `start` from a different actor/PR (CP-018-9)."""


class TimelineMissingStartError(TimelineError):
    """Raised when `end` is requested for a phase/cycle with no matching
    open `start` (CP-018-9)."""


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    """One parsed/rendered `devbot-timeline:v1` marker.

    `actor` is a Task 018 extension field, not part of Task 017's required
    field list (`docs/10-github-status-timeline.md` section 4.2) - that
    section explicitly requires parsers to ignore unknown fields, so adding
    it is forward-compatible rather than a protocol break.
    """

    issue: int
    cycle: int
    phase: Phase
    event: EventType
    result: str
    at: datetime
    pr: int | None = None
    actor: str | None = None

    def to_marker(self) -> str:
        fields = [
            EVENT_MARKER_NAME,
            f"issue={self.issue}",
            f"pr={self.pr if self.pr is not None else '-'}",
            f"cycle={self.cycle}",
            f"phase={self.phase}",
            f"event={self.event}",
            f"result={self.result}",
        ]
        if self.actor is not None:
            fields.append(f"actor={self.actor}")
        fields.append(f"at={self.at.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        return f"<!-- {' '.join(fields)} -->"


def parse_events(body: str) -> list[TimelineEvent]:
    """Parse every `devbot-timeline:v1` marker out of a comment body, in
    chronological order (stable sort preserves original order for equal
    timestamps, matching call order)."""
    events: list[TimelineEvent] = []
    for match in _EVENT_LINE_RE.finditer(body):
        tokens = dict(_TOKEN_RE.findall(match.group(1)))
        if "issue" not in tokens or "at" not in tokens:
            continue
        pr_raw = tokens.get("pr", "-")
        events.append(
            TimelineEvent(
                issue=int(tokens["issue"]),
                pr=None if pr_raw == "-" else int(pr_raw),
                cycle=int(tokens.get("cycle", "1")),
                phase=tokens.get("phase", "dev"),  # type: ignore[arg-type]
                event=tokens.get("event", "start"),  # type: ignore[arg-type]
                result=tokens.get("result", "-"),
                actor=tokens.get("actor"),
                at=datetime.strptime(tokens["at"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC),
            )
        )
    events.sort(key=lambda e: e.at)
    return events


def current_cycle(events: Sequence[TimelineEvent]) -> int:
    """The cycle any *new* event belongs to right now: 1 plus the number of
    `REQUEST CHANGES` review:end events recorded so far (Task 018 scope
    item 5 / CP-018-5). Works uniformly for dev and review events, since a
    cycle boundary is defined solely by a `request-changes` review:end."""
    return 1 + sum(
        1
        for e in events
        if e.phase == "review" and e.event == "end" and e.result == "request-changes"
    )


def _latest_for(events: Sequence[TimelineEvent], phase: str, cycle: int) -> TimelineEvent | None:
    matching = [e for e in events if e.phase == phase and e.cycle == cycle]
    return matching[-1] if matching else None


def _latest_pr(events: Sequence[TimelineEvent], fallback: int | None = None) -> int | None:
    for e in reversed(events):
        if e.pr is not None:
            return e.pr
    return fallback


def _format_result(result: str) -> str:
    return _RESULT_DISPLAY.get(result, result)


def _fmt_dt(dt: datetime, tz: ZoneInfo) -> str:
    return dt.astimezone(tz).strftime("%Y-%m-%d %H:%M")


def _minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60


def _fmt_duration(minutes: float) -> str:
    rounded = max(0, round(minutes))
    if rounded < 60:
        return f"{rounded}m"
    hours, mins = divmod(rounded, 60)
    return f"{hours}h {mins}m"


def _fmt_elapsed(start: datetime, now: datetime) -> str:
    rounded = max(0, round(_minutes_between(start, now)))
    hours, mins = divmod(rounded, 60)
    return f"{hours}h {mins}m 경과"


def _span_completed(label: str, start: datetime, end: datetime, tz: ZoneInfo) -> str:
    duration = _fmt_duration(_minutes_between(start, end))
    return f"{label}: {_fmt_dt(start, tz)} → {_fmt_dt(end, tz)} ({duration})"


def _span_in_progress(label: str, start: datetime, now: datetime, tz: ZoneInfo) -> str:
    return f"{label}: {_fmt_dt(start, tz)} → now ({_fmt_elapsed(start, now)})"


def _span_empty(label: str) -> str:
    return f"{label}: -"


@dataclass(frozen=True, slots=True)
class _CycleView:
    number: int
    dev_start: TimelineEvent | None = None
    dev_end: TimelineEvent | None = None
    review_start: TimelineEvent | None = None
    review_end: TimelineEvent | None = None


def _group_by_cycle(events: Sequence[TimelineEvent]) -> dict[int, _CycleView]:
    slots: dict[int, dict[str, TimelineEvent | None]] = {}
    for e in events:
        if e.phase not in ("dev", "review"):
            continue
        slot = slots.setdefault(
            e.cycle,
            {"dev_start": None, "dev_end": None, "review_start": None, "review_end": None},
        )
        slot[f"{e.phase}_{e.event}"] = e
    return {number: _CycleView(number=number, **fields) for number, fields in sorted(slots.items())}


def _describe_waiting(
    cycles: dict[int, _CycleView],
    cycle_numbers: Sequence[int],
    *,
    state_label: str | None,
    now: datetime,
) -> tuple[str | None, str]:
    """Returns `(active_phase, waiting_text)`. `active_phase` is `"dev"` or
    `"review"` only while that phase is running (used to annotate
    `devbot:working (phase=...)`, docs section 3)."""
    if not cycle_numbers:
        return None, "없음 (기록된 이벤트 없음)"

    latest_cycle = cycle_numbers[-1]
    cv = cycles.get(latest_cycle, _CycleView(number=latest_cycle))

    if cv.dev_start is not None and cv.dev_end is None:
        return "dev", f"없음 (Cycle {latest_cycle} Dev 진행 중)"
    if cv.review_start is not None and cv.review_end is None:
        return "review", f"없음 (Cycle {latest_cycle} Review 진행 중)"
    if cv.dev_end is not None and cv.review_start is None:
        return None, f"Wait reviewer 진행 중 ({_fmt_elapsed(cv.dev_end.at, now)})"
    if cv.review_end is not None and cv.review_end.result == "request-changes":
        return None, f"Wait implementer 진행 중 ({_fmt_elapsed(cv.review_end.at, now)})"
    if cv.review_end is not None and cv.review_end.result == "merge-ready":
        return None, "없음 (Merge 대기 중)"
    if state_label == "devbot:done":
        return None, "없음 (완료)"
    return None, "없음"


def render_status_card(
    events: Sequence[TimelineEvent],
    *,
    issue_number: int,
    pr_number: int | None,
    state_label: str | None,
    now: datetime,
    tz: ZoneInfo = _DEFAULT_TZ,
) -> str:
    """Render the Task 017 section 7 Status Card - never exposes any raw
    marker field, only values already computed per section 6's rules."""
    ready_event = next((e for e in events if e.phase == "queue" and e.event == "ready"), None)
    cycles = _group_by_cycle(events)
    cycle_numbers = sorted(cycles) or ([1] if events else [])

    active_phase, waiting_text = _describe_waiting(
        cycles, cycle_numbers, state_label=state_label, now=now
    )

    lines: list[str] = []
    lines.append(f"#{issue_number} / PR #{pr_number if pr_number is not None else '-'}")
    lines.append("")

    state_text = state_label or "알 수 없음"
    if state_label == "devbot:working" and active_phase is not None:
        state_text = f"{state_label} (phase={active_phase})"
    lines.append(f"State: {state_text}")
    lines.append(f"Waiting: {waiting_text}")
    lines.append("")

    total_active_minutes = 0.0
    total_waiting_minutes = 0.0
    active_in_progress = False
    waiting_in_progress = False

    first_cycle = cycles.get(1)
    if ready_event is not None:
        if first_cycle is not None and first_cycle.dev_start is not None:
            lines.append(_span_completed("Queue", ready_event.at, first_cycle.dev_start.at, tz))
            total_waiting_minutes += _minutes_between(ready_event.at, first_cycle.dev_start.at)
        else:
            lines.append(_span_in_progress("Queue", ready_event.at, now, tz))
            total_waiting_minutes += _minutes_between(ready_event.at, now)
            waiting_in_progress = True
    else:
        lines.append("Queue: - (ready 이벤트 기록 없음)")
    lines.append("")

    for number in cycle_numbers:
        cv = cycles.get(number, _CycleView(number=number))
        lines.append(f"Cycle {number}")

        if cv.dev_start is not None and cv.dev_end is not None:
            dev_line = _span_completed("Dev", cv.dev_start.at, cv.dev_end.at, tz)
            total_active_minutes += _minutes_between(cv.dev_start.at, cv.dev_end.at)
        elif cv.dev_start is not None:
            dev_line = _span_in_progress("Dev", cv.dev_start.at, now, tz)
            total_active_minutes += _minutes_between(cv.dev_start.at, now)
            active_in_progress = True
        else:
            dev_line = _span_empty("Dev")
        if cv.dev_start is not None and cv.dev_start.actor:
            dev_line += f" (actor: {cv.dev_start.actor})"
        lines.append("  " + dev_line)

        if cv.dev_end is not None and cv.review_start is not None:
            lines.append(
                "  " + _span_completed("Wait reviewer", cv.dev_end.at, cv.review_start.at, tz)
            )
            total_waiting_minutes += _minutes_between(cv.dev_end.at, cv.review_start.at)
        elif cv.dev_end is not None:
            lines.append("  " + _span_in_progress("Wait reviewer", cv.dev_end.at, now, tz))
            total_waiting_minutes += _minutes_between(cv.dev_end.at, now)
            waiting_in_progress = True
        else:
            lines.append("  " + _span_empty("Wait reviewer"))

        if cv.review_start is not None and cv.review_end is not None:
            review_line = _span_completed("Review", cv.review_start.at, cv.review_end.at, tz)
            total_active_minutes += _minutes_between(cv.review_start.at, cv.review_end.at)
        elif cv.review_start is not None:
            review_line = _span_in_progress("Review", cv.review_start.at, now, tz)
            total_active_minutes += _minutes_between(cv.review_start.at, now)
            active_in_progress = True
        else:
            review_line = _span_empty("Review")
        if cv.review_start is not None and cv.review_start.actor:
            review_line += f" (actor: {cv.review_start.actor})"
        lines.append("  " + review_line)

        next_cycle = cycles.get(number + 1)
        next_dev_start = next_cycle.dev_start if next_cycle is not None else None
        if cv.review_end is not None and cv.review_end.result == "request-changes":
            if next_dev_start is not None:
                lines.append(
                    "  "
                    + _span_completed("Wait implementer", cv.review_end.at, next_dev_start.at, tz)
                )
                total_waiting_minutes += _minutes_between(cv.review_end.at, next_dev_start.at)
            else:
                lines.append(
                    "  " + _span_in_progress("Wait implementer", cv.review_end.at, now, tz)
                )
                total_waiting_minutes += _minutes_between(cv.review_end.at, now)
                waiting_in_progress = True
        else:
            lines.append("  " + _span_empty("Wait implementer"))

        lines.append(f"  Result: {_format_result(cv.review_end.result) if cv.review_end else '-'}")
        lines.append("")

    lines.append(
        f"Total active: {_fmt_duration(total_active_minutes)}"
        + (" (진행 중)" if active_in_progress else "")
    )
    lines.append(
        f"Total waiting: {_fmt_duration(total_waiting_minutes)}"
        + (" (진행 중)" if waiting_in_progress else "")
    )

    if ready_event is not None:
        elapsed_start = ready_event.at
    elif events:
        elapsed_start = events[0].at
    else:
        elapsed_start = now
    lines.append(f"Total elapsed: {_fmt_duration(_minutes_between(elapsed_start, now))}")

    return "\n".join(lines).rstrip("\n") + "\n"


def render_comment_body(
    events: Sequence[TimelineEvent],
    *,
    issue_number: int,
    pr_number: int | None,
    state_label: str | None,
    now: datetime,
    tz: ZoneInfo = _DEFAULT_TZ,
) -> str:
    """The full Timeline comment body: comment-identity marker, the human
    Status Card (fenced so raw markers never leak into it), then the
    machine event markers - always fully regenerated from `events`, never
    incrementally patched (CP-018-10: a partial write can never corrupt a
    previous state because we only ever attempt one whole-body write)."""
    card = render_status_card(
        events,
        issue_number=issue_number,
        pr_number=pr_number,
        state_label=state_label,
        now=now,
        tz=tz,
    )
    marker_lines = "\n".join(e.to_marker() for e in events)
    return (
        f"{COMMENT_MARKER}\n"
        f"{COMMENT_HEADER}\n\n"
        "```\n"
        f"{card}"
        "```\n\n"
        f"{marker_lines}\n"
    )


@dataclass(frozen=True, slots=True)
class TimelineOutcome:
    """Result of one `TimelineService` call: the rendered Status Card, plus
    whether this call was a no-op idempotent replay (CP-018-8)."""

    status_card: str
    idempotent: bool = False


ClockFn = Callable[[], datetime]


@dataclass
class TimelineService:
    """Reads/writes the single Timeline comment on an Issue and computes
    the Status Card from its event history.

    Unlike `IssueStateWriter`/`DeliveryService` (whose `dry_run=True`
    default gates the *daemon*'s automatic writes), `dry_run=False` is the
    default here: `timeline start`/`end` is a manual, single-shot command a
    human or Agent runs by explicitly typing it, and Task 018's contract
    requires that running it actually records to GitHub (Goal / Scope item
    2 / CP-018-2,3,4). `devbot.main` wires this to the CLI's own
    `--dry-run` flag, deliberately independent of the deployment's global
    `DRY_RUN` setting - see `_add_timeline_write_args` there."""

    read_client: GitHubClient
    write_client: GitHubWriteClient
    dry_run: bool = False
    clock: ClockFn = field(default=lambda: datetime.now(UTC))
    tz: ZoneInfo = field(default_factory=lambda: _DEFAULT_TZ)

    def _find_comment(self, repository: RepositoryConfig, issue_number: int):
        for comment in self.read_client.list_issue_comments(repository, issue_number):
            if COMMENT_MARKER in comment.body:
                return comment
        return None

    @staticmethod
    def _state_label(issue: GitHubIssue) -> str | None:
        for label in issue.labels:
            if label.startswith("devbot:"):
                return label
        return None

    def ready(
        self,
        repository: RepositoryConfig,
        issue_number: int,
        *,
        actor: str | None = None,
        pr: int | None = None,
    ) -> TimelineOutcome:
        """Record the initial `ready` event (Task 024 CP-024-1) - Queue
        start. Idempotent: a second call for an Issue that already has a
        `ready` marker is a no-op regardless of cycle (`ready` only ever
        happens once, unlike `dev`/`review` which repeat per cycle)."""
        return self._record(
            repository,
            issue_number,
            phase="queue",
            event_type="ready",
            actor=actor,
            pr=pr,
            result="-",
        )

    def start(
        self,
        repository: RepositoryConfig,
        issue_number: int,
        *,
        phase: Phase,
        actor: str,
        pr: int | None = None,
    ) -> TimelineOutcome:
        """Record a `phase` start (CP-018-4). Idempotent when replayed with
        the same `actor`/`pr` while still unresolved (CP-018-8); rejected
        when a *different* actor/pr tries to start the same open phase
        (CP-018-9)."""
        return self._record(
            repository,
            issue_number,
            phase=phase,
            event_type="start",
            actor=actor,
            pr=pr,
            result="-",
        )

    def end(
        self,
        repository: RepositoryConfig,
        issue_number: int,
        *,
        phase: Phase,
        actor: str,
        result: str,
        pr: int | None = None,
    ) -> TimelineOutcome:
        """Record a `phase` end (CP-018-4). Requires a matching open
        `start` (CP-018-9); idempotent when replayed identically
        (CP-018-8)."""
        return self._record(
            repository,
            issue_number,
            phase=phase,
            event_type="end",
            actor=actor,
            pr=pr,
            result=result,
        )

    def _record(
        self,
        repository: RepositoryConfig,
        issue_number: int,
        *,
        phase: Phase,
        event_type: EventType,
        actor: str | None,
        pr: int | None,
        result: str,
    ) -> TimelineOutcome:
        issue = self.read_client.get_issue(repository, issue_number)
        existing_comment = self._find_comment(repository, issue_number)
        events = parse_events(existing_comment.body) if existing_comment is not None else []

        cycle = current_cycle(events)
        latest = _latest_for(events, phase, cycle)
        now = self.clock()
        state_label = self._state_label(issue)

        if event_type == "ready" and any(
            e.phase == "queue" and e.event == "ready" for e in events
        ):
            # CP-024-1: `ready` happens at most once per Issue (unlike
            # `dev`/`review`, which repeat every cycle) - any replay is a
            # no-op regardless of which cycle is currently active.
            card = render_status_card(
                events,
                issue_number=issue_number,
                pr_number=_latest_pr(events, pr),
                state_label=state_label,
                now=now,
                tz=self.tz,
            )
            return TimelineOutcome(status_card=card, idempotent=True)

        if event_type == "start" and latest is not None and latest.event == "start":
            if latest.actor == actor and latest.pr == pr:
                card = render_status_card(
                    events,
                    issue_number=issue_number,
                    pr_number=_latest_pr(events, pr),
                    state_label=state_label,
                    now=now,
                    tz=self.tz,
                )
                return TimelineOutcome(status_card=card, idempotent=True)
            raise TimelineOverlappingStartError(
                f"Issue #{issue_number} phase={phase} cycle={cycle}는 이미 "
                f"actor={latest.actor}가 시작한 상태입니다 (end 없이 재시작할 수 없음)"
            )

        if event_type == "end" and (latest is None or latest.event != "start"):
            if (
                latest is not None
                and latest.event == "end"
                and latest.actor == actor
                and latest.pr == pr
                and latest.result == result
            ):
                card = render_status_card(
                    events,
                    issue_number=issue_number,
                    pr_number=_latest_pr(events, pr),
                    state_label=state_label,
                    now=now,
                    tz=self.tz,
                )
                return TimelineOutcome(status_card=card, idempotent=True)
            raise TimelineMissingStartError(
                f"Issue #{issue_number} phase={phase} cycle={cycle}에 대응하는 "
                "시작(start) 이벤트가 없어 종료를 기록할 수 없습니다"
            )

        new_event = TimelineEvent(
            issue=issue_number,
            pr=pr,
            cycle=cycle,
            phase=phase,
            event=event_type,
            result=result,
            actor=actor,
            at=now,
        )
        new_events = [*events, new_event]
        pr_number = _latest_pr(new_events, pr)

        body = render_comment_body(
            new_events,
            issue_number=issue_number,
            pr_number=pr_number,
            state_label=state_label,
            now=now,
            tz=self.tz,
        )

        if not self.dry_run:
            if existing_comment is None:
                self.write_client.create_comment(repository, issue_number, body)
            else:
                self.write_client.update_comment(repository, existing_comment.id, body)

        card = render_status_card(
            new_events,
            issue_number=issue_number,
            pr_number=pr_number,
            state_label=state_label,
            now=now,
            tz=self.tz,
        )
        return TimelineOutcome(status_card=card)

    def status(self, repository: RepositoryConfig, issue_number: int) -> TimelineOutcome:
        """Read-only Status Card render (CP-018-6/7); never writes."""
        issue = self.read_client.get_issue(repository, issue_number)
        existing_comment = self._find_comment(repository, issue_number)
        events = parse_events(existing_comment.body) if existing_comment is not None else []
        card = render_status_card(
            events,
            issue_number=issue_number,
            pr_number=_latest_pr(events),
            state_label=self._state_label(issue),
            now=self.clock(),
            tz=self.tz,
        )
        return TimelineOutcome(status_card=card)


# ---- Best-effort daemon recording (Task 024) --------------------------
#
# The daemon (`devbot.polling`, `devbot.review`, `devbot.rework`) records
# Timeline events as a side effect of its normal lifecycle, never as a
# precondition for it. CP-024-10 requires that a Timeline write failure
# (GitHub API error, an unexpected overlapping-start/missing-start
# conflict, ...) is diagnosed but never replaces or hides the primary Job
# outcome and never raises into the caller. These three wrappers are the
# single place that guarantee that for every automatic call site - callers
# pass `timeline=None` (the default on every affected dataclass) to opt out
# entirely, exactly reproducing pre-Task-024 behavior.


def safe_ready(
    timeline: TimelineService | None,
    repository: RepositoryConfig,
    issue_number: int,
    *,
    logger: logging.Logger | None = None,
) -> None:
    if timeline is None:
        return
    try:
        timeline.ready(repository, issue_number)
    except Exception as exc:  # noqa: BLE001 - CP-024-10: never corrupt the primary Job outcome
        if logger is not None:
            logger.warning("Timeline 자동 기록 실패 (ready, issue=#%d): %s", issue_number, exc)


def safe_start(
    timeline: TimelineService | None,
    repository: RepositoryConfig,
    issue_number: int,
    *,
    phase: Phase,
    actor: str,
    pr: int | None = None,
    logger: logging.Logger | None = None,
) -> None:
    if timeline is None:
        return
    try:
        timeline.start(repository, issue_number, phase=phase, actor=actor, pr=pr)
    except Exception as exc:  # noqa: BLE001 - CP-024-10: never corrupt the primary Job outcome
        if logger is not None:
            logger.warning(
                "Timeline 자동 기록 실패 (%s:start, issue=#%d): %s", phase, issue_number, exc
            )


def safe_end(
    timeline: TimelineService | None,
    repository: RepositoryConfig,
    issue_number: int,
    *,
    phase: Phase,
    actor: str,
    result: str,
    pr: int | None = None,
    logger: logging.Logger | None = None,
) -> None:
    if timeline is None:
        return
    try:
        timeline.end(repository, issue_number, phase=phase, actor=actor, result=result, pr=pr)
    except Exception as exc:  # noqa: BLE001 - CP-024-10: never corrupt the primary Job outcome
        if logger is not None:
            logger.warning(
                "Timeline 자동 기록 실패 (%s:end result=%s, issue=#%d): %s",
                phase,
                result,
                issue_number,
                exc,
            )


__all__ = [
    "COMMENT_MARKER",
    "EVENT_MARKER_NAME",
    "TimelineError",
    "TimelineEvent",
    "TimelineMissingStartError",
    "TimelineOutcome",
    "TimelineOverlappingStartError",
    "TimelineService",
    "current_cycle",
    "parse_events",
    "render_comment_body",
    "render_status_card",
    "safe_end",
    "safe_ready",
    "safe_start",
]
