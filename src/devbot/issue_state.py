"""Issue state writes: claim, block, and advance an Issue through the
DevBot label state machine (`devbot:ready` -> `devbot:working` -> ...).

The allowed transitions mirror `docs/02-workflow.md` and
`docs/03-state-machine.md`:

    ready   -> working
    working -> review
    working -> blocked
    review  -> working   # review feedback
    review  -> done
    blocked -> ready      # after human clarification

Every write is validated against the Issue's *current* label state before
touching GitHub. `IssueStateWriter(dry_run=True)` (the default) still
validates the transition but performs no label or comment write.

Every method returns the `GitHubIssue` with its state label already
updated to match what was (or, in dry-run, would be) written, so callers
can chain multiple transitions (e.g. `devbot.rework`) without re-fetching
from GitHub between steps.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from devbot.github_client import GitHubIssue
from devbot.github_write_client import GitHubWriteClient
from devbot.models import RepositoryConfig, TaskState

_STATE_LABEL_PREFIX = "devbot:"

_ALLOWED_TRANSITIONS: dict[TaskState, tuple[TaskState, ...]] = {
    TaskState.READY: (TaskState.WORKING,),
    TaskState.WORKING: (TaskState.REVIEW, TaskState.BLOCKED),
    TaskState.REVIEW: (TaskState.WORKING, TaskState.DONE),
    TaskState.BLOCKED: (TaskState.READY,),
    TaskState.DONE: (),
}


class InvalidStateTransitionError(RuntimeError):
    """Raised when the Issue's current label state does not allow the
    requested transition."""


def _state_label(state: TaskState) -> str:
    return f"{_STATE_LABEL_PREFIX}{state.value}"


def _matched_state_labels(issue: GitHubIssue) -> list[TaskState]:
    """Return every `devbot:*` state label present on `issue`. Exactly one
    is a well-formed Issue; zero or more than one is never a valid
    transition source (ambiguous/conflicting state)."""
    label_set = set(issue.labels)
    return [state for state in TaskState if _state_label(state) in label_set]


@dataclass(frozen=True, slots=True)
class IssueStateWriter:
    """Validates and performs `devbot:*` label/comment transitions for a
    single Issue at a time."""

    client: GitHubWriteClient
    dry_run: bool = True

    def _transition(
        self, repository: RepositoryConfig, issue: GitHubIssue, to_state: TaskState
    ) -> GitHubIssue:
        matched = _matched_state_labels(issue)
        from_state = matched[0] if len(matched) == 1 else None
        allowed = _ALLOWED_TRANSITIONS.get(from_state, ()) if from_state is not None else ()
        if to_state not in allowed:
            if not matched:
                current = "no devbot:* label"
            elif len(matched) > 1:
                current = "conflicting labels (" + ", ".join(_state_label(s) for s in matched) + ")"
            else:
                current = from_state.value
            raise InvalidStateTransitionError(
                f"Cannot transition Issue {repository.full_name}#{issue.number} "
                f"from {current} to {to_state.value}"
            )

        if self.dry_run:
            return issue

        new_labels = [label for label in issue.labels if label != _state_label(from_state)]
        new_labels.append(_state_label(to_state))
        self.client.set_labels(repository, issue.number, new_labels)
        return replace(issue, labels=tuple(new_labels))

    def claim(self, repository: RepositoryConfig, issue: GitHubIssue) -> GitHubIssue:
        """Move a `ready` Issue to `working`, claiming it for this run."""
        return self._transition(repository, issue, TaskState.WORKING)

    def block(self, repository: RepositoryConfig, issue: GitHubIssue, reason: str) -> GitHubIssue:
        """Move a `working` Issue to `blocked`, recording `reason` as a
        comment explaining why."""
        updated = self._transition(repository, issue, TaskState.BLOCKED)
        if not self.dry_run:
            self.client.create_comment(repository, issue.number, reason)
        return updated

    def mark_for_review(self, repository: RepositoryConfig, issue: GitHubIssue) -> GitHubIssue:
        """Move a `working` Issue to `review` after a successful
        implementation."""
        return self._transition(repository, issue, TaskState.REVIEW)

    def request_changes(self, repository: RepositoryConfig, issue: GitHubIssue) -> GitHubIssue:
        """Move a `review` Issue back to `working` for requested changes."""
        return self._transition(repository, issue, TaskState.WORKING)
