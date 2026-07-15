"""Abstract agent runner interface.

DevBot depends on this interface, not on any single vendor. `CodexRunner`
(see `devbot.agents.codex`) is the first implementation.

Target repositories supply their own project-specific rules in a root
`AGENTS.md` file. DevBot does not duplicate or override those rules; it only
passes the Issue prompt to the agent and lets the agent read the target
repository's own `AGENTS.md`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from devbot.models import RepositoryConfig

_APPROVAL_REQUIRED_PATTERNS = (
    "needs your approval",
    "need your approval",
    "should i proceed",
    "approval required",
    "approval_required",
)


def is_approval_required_output(message: str) -> bool:
    """True when an Agent's own output ends in an interactive approval
    request DevBot cannot answer (Task 016 CP-016-9) - e.g. a read-only
    command the CLI paused on for human confirmation - rather than a
    genuine failure or a completed change. Callers must not proceed to
    commit/push/PR delivery when this is True, even though the process
    itself may have exited 0 (`AgentRunResult.failed` is False)."""
    lowered = message.casefold()
    return any(pattern in lowered for pattern in _APPROVAL_REQUIRED_PATTERNS)


_SESSION_LIMIT_PATTERNS = (
    "usage limit",
    "session limit",
    "rate limit",
    "quota exceeded",
    "quota_exceeded",
    "resets at",
    "try again later",
    "limit reached",
)


def is_session_limit_output(message: str) -> bool:
    """True when a failed Agent run's own output indicates a session/usage
    limit rather than a genuine execution error (Task 019 CP-019-9) - e.g.
    the Claude Code or Codex CLI reporting it has hit a usage/rate limit.
    Callers must classify this as `FailureCategory.AGENT_SESSION_LIMIT`
    (`devbot.models`) and must not schedule an automatic polling retry for
    it - see `devbot.reliability`'s retry policy."""
    lowered = message.casefold()
    return any(pattern in lowered for pattern in _SESSION_LIMIT_PATTERNS)


class AgentSessionLimitError(RuntimeError):
    """Raised by callers (not `AgentRunner` itself) when a failed Agent run's
    output matches `is_session_limit_output()` - lets a generic
    `except Exception` still catch it (Task 014 CP-014-7's "never leave
    `working`" contract is unaffected), while giving a handler that wants to
    label the failure distinctly (Task 019 CP-019-9's "clear recovery hint")
    something to `isinstance`-check for."""


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    """Outcome of a single agent invocation."""

    executed: bool
    dry_run: bool
    message: str
    returncode: int | None = None

    @property
    def failed(self) -> bool:
        """True for a genuine execution failure that callers must treat as
        `AGENT_FAILED`/`blocked`, not let flow through to delivery/verify/
        commit/push.

        A dry-run's `executed=False` is an intentional no-op, never a
        failure. Everything else that didn't execute (`executed=False`,
        e.g. `ClaudeRunner` reporting a missing CLI or a timeout - see
        `devbot.agents.claude`) *is* a failure even though it has no
        `returncode` to check (`returncode is None` alone is not
        sufficient - it's also the correct value for a real exit code of
        0's sibling case: no process ever ran). A nonzero `returncode` from
        an execution that did run is always a failure.
        """
        if self.dry_run:
            return False
        return not self.executed or self.returncode not in (None, 0)


class AgentRunner(ABC):
    """Runs a coding agent against a checked-out repository."""

    @abstractmethod
    def run(self, repository: RepositoryConfig, prompt: str) -> AgentRunResult:
        """Run the agent for `repository` with the given task `prompt`."""
        raise NotImplementedError
