"""Shared helpers for resolving DevBot Issue state labels.

Manual edits or partially failed GitHub label writes can leave an Issue with
more than one ``devbot:*`` state label. All read paths must resolve that
ambiguity the same way: later-workflow and terminal states win over earlier
ones, because stale labels are more likely to be leftovers from an earlier
state than from a later state.
"""

from __future__ import annotations

from collections.abc import Iterable

from devbot.models import TaskState

STATE_LABEL_PREFIX = "devbot:"
LABEL_PRECEDENCE: tuple[TaskState, ...] = (
    TaskState.DONE,
    TaskState.BLOCKED,
    TaskState.WORKING,
    TaskState.MANUAL_ACTION,
    TaskState.REWORK,
    TaskState.REVIEW,
    TaskState.READY,
)
ALL_STATE_LABELS = frozenset(f"{STATE_LABEL_PREFIX}{state.value}" for state in TaskState)


def state_label(state: TaskState) -> str:
    return f"{STATE_LABEL_PREFIX}{state.value}"


def matched_task_states(labels: Iterable[str]) -> list[TaskState]:
    """Return every present ``devbot:*`` state label in declaration order."""

    label_set = set(labels)
    return [state for state in TaskState if state_label(state) in label_set]


def task_state_from_labels(labels: Iterable[str]) -> TaskState | None:
    """Resolve labels to one TaskState using the project-wide precedence."""

    matched = matched_task_states(labels)
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]
    for state in LABEL_PRECEDENCE:
        if state in matched:
            return state
    return matched[0]
