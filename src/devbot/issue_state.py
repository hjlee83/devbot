"""Issue state writes: claim, restore, block, and advance an Issue through
the DevBot label state machine (Task 014 hardening).

The allowed transitions (see `docs/04-agent-system.md` for the narrative
version):

    ready   -> working
    working -> review                 # successful implement, successful
                                       # rework, or MERGE READY review
    working -> rework                 # REQUEST CHANGES review posted
    working -> manual-action           # metadata/external action required
    working -> ready | review | rework  # claim-preflight-failure restore
                                       # (back to whichever stable state
                                       # was claimed from)
    working -> blocked                # unrecoverable execution failure
    review  -> working                # claim for automatic review or rework
    review  -> rework                 # REQUEST CHANGES posted
    rework  -> working                # claim for rework
    blocked -> ready                  # after human clarification

Every write is validated against the Issue's *current* label state before
touching GitHub. If more than one `devbot:*` state label is present (a
stale manual edit, or a previous write that partially applied), the
transition is validated against a single label chosen deterministically by
`_LABEL_PRECEDENCE` rather than failing closed - and every successful write
strips *every* `devbot:*` state label before adding the target, so the
Issue always ends a transition with exactly one (CP-014-1).

`IssueStateWriter(dry_run=True)` (the default) still validates the
transition but performs no label or comment write. Every method returns the
`GitHubIssue` with its state label already updated to match what was (or,
in dry-run, would be) written, so callers can chain multiple transitions
(e.g. `devbot.rework`) without re-fetching from GitHub between steps.

Competing claims: `claim()` (any transition *into* `working`) is guarded by
an in-process lock plus a "currently claimed" set kept on this instance.
Two threads sharing one `IssueStateWriter` (as `devbot.main` wires it - one
instance shared by `PollingService`, `ReworkService`, and `ReviewService`)
racing to claim the same Issue at the same time: only one reserves the
claim and proceeds to write; the other raises `ClaimConflictError`
immediately, before running any Agent (CP-014-8). This is intentionally
process-local, not a distributed lock (see Task 014 계약서 "제외 범위") -
`devbot.lock.ProcessLock` already ensures only one DevBot process runs
against a given deployment.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field, replace

from devbot import observability
from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.models import JobType, RepositoryConfig, TaskState

_LOGGER_NAME = "devbot"

_STATE_LABEL_PREFIX = "devbot:"

_ALLOWED_TRANSITIONS: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.READY: (TaskState.WORKING,),
    TaskState.WORKING: (
        TaskState.REVIEW,
        TaskState.REWORK,
        TaskState.MANUAL_ACTION,
        TaskState.READY,
        TaskState.BLOCKED,
    ),
    TaskState.REVIEW: (TaskState.WORKING, TaskState.REWORK, TaskState.DONE),
    TaskState.REWORK: (TaskState.WORKING,),
    TaskState.MANUAL_ACTION: (TaskState.REVIEW, TaskState.REWORK, TaskState.READY),
    TaskState.BLOCKED: (TaskState.READY,),
    TaskState.DONE: (),
}

# Deterministic precedence used only when an Issue carries more than one
# `devbot:*` state label at once. Later-workflow / terminal states win over
# earlier ones, since a leftover label from an earlier step is the more
# likely source of the ambiguity than a leftover from a later one.
_LABEL_PRECEDENCE: tuple[TaskState, ...] = (
    TaskState.DONE,
    TaskState.BLOCKED,
    TaskState.WORKING,
    TaskState.MANUAL_ACTION,
    TaskState.REWORK,
    TaskState.REVIEW,
    TaskState.READY,
)

_ALL_STATE_LABELS = frozenset(f"{_STATE_LABEL_PREFIX}{state.value}" for state in TaskState)


class InvalidStateTransitionError(RuntimeError):
    """Raised when the Issue's current label state does not allow the
    requested transition."""


class ClaimConflictError(InvalidStateTransitionError):
    """Raised when a competing `claim()` already reserved this Issue in
    this process (CP-014-8). The caller must not run any Agent for this
    Job - the other claim owns it."""


def _state_label(state: TaskState) -> str:
    return f"{_STATE_LABEL_PREFIX}{state.value}"


def _matched_state_labels(issue: GitHubIssue) -> list[TaskState]:
    """Return every `devbot:*` state label present on `issue`, in
    `TaskState` declaration order."""
    label_set = set(issue.labels)
    return [state for state in TaskState if _state_label(state) in label_set]


def _current_state(issue: GitHubIssue) -> TaskState | None:
    """Resolve `issue`'s single current state, normalizing away ambiguity
    (CP-014-1) via `_LABEL_PRECEDENCE` when more than one `devbot:*` label
    is present. `None` means no `devbot:*` label at all."""
    matched = _matched_state_labels(issue)
    if not matched:
        return None
    if len(matched) == 1:
        return matched[0]
    for state in _LABEL_PRECEDENCE:
        if state in matched:
            return state
    return matched[0]  # unreachable: _LABEL_PRECEDENCE covers every TaskState


@dataclass(frozen=True, slots=True)
class IssueStateWriter:
    """Validates and performs `devbot:*` label/comment transitions for
    Issues, guarding `working` claims against in-process races and logging
    every transition (CP-014-11)."""

    client: GitHubWriteClient
    dry_run: bool = True
    logger: logging.Logger = field(
        default_factory=lambda: observability.ensure_safe_logger(logging.getLogger(_LOGGER_NAME))
    )
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)
    _claimed: set[tuple[str, int]] = field(default_factory=set, repr=False, compare=False)

    def _transition(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        to_state: TaskState,
        *,
        job_type: JobType | None = None,
        reason: str = "",
    ) -> GitHubIssue:
        key = (repository.full_name, issue.number)
        from_state = _current_state(issue)
        with self._lock:
            if to_state is TaskState.WORKING and key in self._claimed:
                raise ClaimConflictError(
                    f"Issue {repository.full_name}#{issue.number} is already claimed "
                    "by a concurrent Job in this process"
                )

            allowed = _ALLOWED_TRANSITIONS.get(from_state, ()) if from_state is not None else ()
            if to_state not in allowed:
                matched = _matched_state_labels(issue)
                if not matched:
                    current = "no devbot:* label"
                elif len(matched) > 1:
                    current = (
                        f"conflicting labels (normalized to {from_state.value}): "
                        + ", ".join(_state_label(s) for s in matched)
                    )
                else:
                    current = from_state.value
                raise InvalidStateTransitionError(
                    f"Cannot transition Issue {repository.full_name}#{issue.number} "
                    f"from {current} to {to_state.value}"
                )

            if to_state is TaskState.WORKING:
                self._claimed.add(key)

        new_labels = [label for label in issue.labels if label not in _ALL_STATE_LABELS]
        new_labels.append(_state_label(to_state))

        try:
            if not self.dry_run:
                self.client.set_labels(repository, issue.number, new_labels)
        except Exception:
            if to_state is TaskState.WORKING:
                with self._lock:
                    self._claimed.discard(key)
            raise

        if to_state is not TaskState.WORKING:
            with self._lock:
                self._claimed.discard(key)

        observability.log_state_transition(
            self.logger,
            repository=repository.full_name,
            issue_number=issue.number,
            from_state=from_state.value if from_state is not None else "unknown",
            to_state=to_state.value,
            job_type=job_type.value if job_type is not None else None,
            reason=reason,
        )
        return replace(issue, labels=tuple(new_labels))

    def claim(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        *,
        job_type: JobType | None = None,
    ) -> GitHubIssue:
        """Move a `ready`, `review`, or `rework` Issue to `working`,
        claiming it for this run. Raises `ClaimConflictError` if another
        claim in this process already reserved this Issue."""
        return self._transition(
            repository, issue, TaskState.WORKING, job_type=job_type, reason="claim"
        )

    def restore(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        to_state: TaskState,
        *,
        job_type: JobType | None = None,
        reason: str = "preflight validation failed before Agent execution",
    ) -> GitHubIssue:
        """Move a `working` Issue back to `to_state` (whichever stable
        state - `ready`/`review`/`rework` - it was claimed from). Used when
        a preflight check (before any Agent execution) fails right after
        `claim()` (CP-014-5): the claim never should have happened, so it
        is undone rather than recorded as a `blocked` failure."""
        return self._transition(repository, issue, to_state, job_type=job_type, reason=reason)

    def block(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        reason: str,
        *,
        job_type: JobType | None = None,
    ) -> GitHubIssue:
        """Move a `working` Issue to `blocked`, recording `reason` as a
        comment explaining why (CP-014-6)."""
        updated = self._transition(
            repository, issue, TaskState.BLOCKED, job_type=job_type, reason=reason
        )
        if not self.dry_run:
            self.client.create_comment(repository, issue.number, reason)
        return updated

    def mark_for_review(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        *,
        job_type: JobType | None = None,
        reason: str = "",
    ) -> GitHubIssue:
        """Move a `working` Issue to `review`: after a successful
        implementation, a successful rework, or a `MERGE READY` automatic
        review."""
        return self._transition(
            repository, issue, TaskState.REVIEW, job_type=job_type, reason=reason
        )

    def send_to_rework(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        *,
        job_type: JobType | None = None,
        reason: str = "REQUEST CHANGES posted",
    ) -> GitHubIssue:
        """Move a `working` Issue to `rework`: an automatic review posted
        `REQUEST CHANGES` (CP-014-2)."""
        return self._transition(
            repository, issue, TaskState.REWORK, job_type=job_type, reason=reason
        )

    def require_manual_action(
        self,
        repository: RepositoryConfig,
        issue: GitHubIssue,
        reason: str,
        *,
        job_type: JobType | None = None,
    ) -> GitHubIssue:
        """Move a `working` Issue to `manual-action` and post the reason
        when metadata-only or external verification work cannot be solved
        by committing repository files."""
        updated = self._transition(
            repository, issue, TaskState.MANUAL_ACTION, job_type=job_type, reason=reason
        )
        if not self.dry_run:
            self.client.create_comment(repository, issue.number, reason)
        return updated
