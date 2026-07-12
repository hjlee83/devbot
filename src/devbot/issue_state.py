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
"""

from __future__ import annotations

from dataclasses import dataclass

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


def _current_state(issue: GitHubIssue) -> TaskState | None:
    label_set = set(issue.labels)
    for state in TaskState:
        if _state_label(state) in label_set:
            return state
    return None


@dataclass(frozen=True, slots=True)
class IssueStateWriter:
    """Validates and performs `devbot:*` label/comment transitions for a
    single Issue at a time."""

    client: GitHubWriteClient
    dry_run: bool = True

    def _transition(
        self, repository: RepositoryConfig, issue: GitHubIssue, to_state: TaskState
    ) -> None:
        from_state = _current_state(issue)
        allowed = _ALLOWED_TRANSITIONS.get(from_state, ()) if from_state is not None else ()
        if to_state not in allowed:
            current = from_state.value if from_state is not None else "none"
            raise InvalidStateTransitionError(
                f"Cannot transition Issue {repository.full_name}#{issue.number} "
                f"from {current} to {to_state.value}"
            )

        if self.dry_run:
            return

        new_labels = [label for label in issue.labels if label != _state_label(from_state)]
        new_labels.append(_state_label(to_state))
        self.client.set_labels(repository, issue.number, new_labels)

    def claim(self, repository: RepositoryConfig, issue: GitHubIssue) -> None:
        """Move a `ready` Issue to `working`, claiming it for this run."""
        self._transition(repository, issue, TaskState.WORKING)

    def block(self, repository: RepositoryConfig, issue: GitHubIssue, reason: str) -> None:
        """Move a `working` Issue to `blocked`, recording `reason` as a
        comment explaining why."""
        self._transition(repository, issue, TaskState.BLOCKED)
        if not self.dry_run:
            self.client.create_comment(repository, issue.number, reason)

    def mark_for_review(self, repository: RepositoryConfig, issue: GitHubIssue) -> None:
        """Move a `working` Issue to `review` after a successful
        implementation."""
        self._transition(repository, issue, TaskState.REVIEW)

    def request_changes(self, repository: RepositoryConfig, issue: GitHubIssue) -> None:
        """Move a `review` Issue back to `working` for requested changes."""
        self._transition(repository, issue, TaskState.WORKING)
