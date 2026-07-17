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

from devbot.models import AgentOutcome, RepositoryConfig

_APPROVAL_REQUIRED_PATTERNS = (
    "needs your approval",
    "need your approval",
    "should i proceed",
    "can i proceed",
    "approval required",
    "approval_required",
    "approve this command",
    "confirmation required",
    "permission required",
    "requires approval",
    "could you approve",
    "approved to proceed",
    "hold off until you confirm",
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


_NETWORK_BLOCKED_PATTERNS = (
    "network is unreachable",
    "could not resolve host",
    "connection refused",
    "connection timed out",
    "no internet access",
    "network access is disabled",
    "network access is restricted",
    "requires network access",
    "blocked by the sandbox",
    "blocked by firewall",
    "sandbox blocks",
    "sandboxed environment",
    "permission denied",
    "operation not permitted",
    "read-only file system",
    "not authorized to access",
)


def is_network_blocked_output(message: str) -> bool:
    """True when an Agent's own output indicates a required Git, GitHub,
    network, filesystem, or sandbox operation was blocked by the execution
    environment (Task 021 CP-021-4) - e.g. a sandboxed run with outbound
    network access disabled. This needs operator action (adjusting the
    execution environment), not a `blocked` retry loop or a silent
    no-op-implementation success."""
    lowered = message.casefold()
    return any(pattern in lowered for pattern in _NETWORK_BLOCKED_PATTERNS)


_REPOSITORY_LOCKED_PATTERNS = (
    "index.lock",
    "unable to create",
    "another git process",
    "repository is locked",
    "workspace is locked",
    "lock file exists",
)


def is_repository_locked_output(message: str) -> bool:
    """True when an Agent's own output indicates the local Git repository
    or workspace was locked by another process (Task 021 CP-021-4's
    "documented equivalent" of network-blocked) - e.g. a stale
    `.git/index.lock`. This needs operator action, not a commit/push
    attempt against a repository DevBot cannot safely write to."""
    lowered = message.casefold()
    return any(pattern in lowered for pattern in _REPOSITORY_LOCKED_PATTERNS)


_IMPLEMENTATION_SKIPPED_PATTERNS = (
    "no changes needed",
    "nothing to implement",
    "already implemented, skipping",
    "skipping implementation",
    "no action required",
    "no code changes required",
)


def is_implementation_skipped_output(message: str) -> bool:
    """True when an Agent's own output explicitly declines to implement
    anything this run (Task 021 Scope §1 `implementation_skipped`) - e.g.
    it decided the work was already done and intentionally made no
    changes. Distinct from `AgentRunResult.dry_run` (DevBot's own
    config-level no-op, never a real Agent decision) and from
    `no_repository_changes` (a `devbot.delivery` observation made *after*
    a run that did not self-report skipping)."""
    lowered = message.casefold()
    return any(pattern in lowered for pattern in _IMPLEMENTATION_SKIPPED_PATTERNS)


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
    """Outcome of a single agent invocation.

    `outcome_hint` is an optional structured `AgentOutcome` signal (Task 021
    Scope §2) a runner can set when it can determine the outcome more
    reliably than free-form text matching - e.g. a future runner that
    parses a machine-readable status from the CLI. No runner shipped today
    (`ClaudeRunner`, `CodexRunner`) sets it; `devbot.agent_outcome.classify_agent_outcome`
    falls back to text matching on `message` whenever it is `None`, so
    text matching remains a compatibility fallback rather than the only
    path, without requiring every runner to support structured signals
    immediately."""

    executed: bool
    dry_run: bool
    message: str
    returncode: int | None = None
    outcome_hint: AgentOutcome | None = None

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
