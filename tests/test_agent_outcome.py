"""Tests for Task 021: Agent Outcome Classification Hardening."""

from devbot.agent_outcome import (
    AGENT_OUTCOME_TRANSITIONS,
    AgentOutcomeClassification,
    build_agent_outcome_diagnostics,
    classify_agent_outcome,
    render_agent_outcome_diagnostics,
    transition_for,
)
from devbot.agents.base import (
    AgentRunResult,
    is_network_blocked_output,
    is_repository_locked_output,
)
from devbot.models import AgentOutcome, TaskState

# ---- CP-021-1: normalized outcome classification ----


def test_agent_outcome_classification() -> None:
    """CP-021-1: every contract-required `AgentOutcome` value is a real
    enum member, and a representative `AgentRunResult` for each reachable
    case classifies to the expected outcome."""
    assert {member.value for member in AgentOutcome} == {
        "implementation_completed",
        "implementation_skipped",
        "no_repository_changes",
        "approval_required",
        "network_blocked",
        "session_limit",
        "repository_locked",
        "agent_failed",
        "unknown",
    }

    # Happy path: executed, not failed, no blocking pattern.
    completed = classify_agent_outcome(
        AgentRunResult(
            executed=True, dry_run=False, message="Implemented the feature.", returncode=0
        )
    )
    assert completed.outcome is AgentOutcome.IMPLEMENTATION_COMPLETED
    assert completed.signal_source == "text_fallback"

    # DevBot's own dry-run pipeline preview is not a real Agent decision.
    dry_run_preview = classify_agent_outcome(
        AgentRunResult(executed=False, dry_run=True, message="[dry-run] would run codex")
    )
    assert dry_run_preview.outcome is AgentOutcome.IMPLEMENTATION_COMPLETED

    # Failure path: nonzero returncode.
    failed = classify_agent_outcome(
        AgentRunResult(executed=True, dry_run=False, message="boom", returncode=1)
    )
    assert failed.outcome is AgentOutcome.AGENT_FAILED

    # Failure path: never executed and not a dry-run (missing CLI/timeout).
    unexecuted = classify_agent_outcome(
        AgentRunResult(executed=False, dry_run=False, message="CLI가 설치되어 있지 않습니다.")
    )
    assert unexecuted.outcome is AgentOutcome.AGENT_FAILED

    # Boundary: executed, not failed, but says nothing - not implicit success.
    unknown = classify_agent_outcome(
        AgentRunResult(executed=True, dry_run=False, message="", returncode=0)
    )
    assert unknown.outcome is AgentOutcome.UNKNOWN

    skipped = classify_agent_outcome(
        AgentRunResult(
            executed=True,
            dry_run=False,
            message="No changes needed - this task is already implemented, skipping.",
            returncode=0,
        )
    )
    assert skipped.outcome is AgentOutcome.IMPLEMENTATION_SKIPPED


def test_agent_outcome_classification_prefers_structured_signal() -> None:
    """Scope §2: a structured `outcome_hint` wins over text matching, even
    when the free-form message would otherwise match a different
    pattern."""
    result = AgentRunResult(
        executed=True,
        dry_run=False,
        message="this looks like it needs your approval",
        returncode=0,
        outcome_hint=AgentOutcome.IMPLEMENTATION_COMPLETED,
    )

    classification = classify_agent_outcome(result)

    assert classification.outcome is AgentOutcome.IMPLEMENTATION_COMPLETED
    assert classification.signal_source == "structured"


# ---- CP-021-2: approval-required detection ----


def test_agent_approval_required_detection() -> None:
    """CP-021-2: every approval-wait phrasing the Task 021 contract names
    is classified as `approval_required`, and ordinary output is not."""
    contract_patterns = [
        "This requires approval before I continue.",
        "Approval required to proceed.",
        "Could you approve this change?",
        "I need this approved to proceed.",
        "Please hold off until you confirm the plan.",
        "Should I proceed with the deletion?",
    ]
    for message in contract_patterns:
        classification = classify_agent_outcome(
            AgentRunResult(executed=True, dry_run=False, message=message, returncode=0)
        )
        assert classification.outcome is AgentOutcome.APPROVAL_REQUIRED, message

    ordinary = classify_agent_outcome(
        AgentRunResult(
            executed=True, dry_run=False, message="Implemented and tests pass.", returncode=0
        )
    )
    assert ordinary.outcome is not AgentOutcome.APPROVAL_REQUIRED


# ---- CP-021-3: session-limit detection ----


def test_agent_session_limit_detection() -> None:
    """CP-021-3: a session/usage-limit message is classified as
    `session_limit` even when the process exited 0 (`.failed` is False),
    and its transition is never retryable."""
    classification = classify_agent_outcome(
        AgentRunResult(
            executed=True,
            dry_run=False,
            message="Usage limit reached, resets at 09:00 UTC",
            returncode=0,
        )
    )

    assert classification.outcome is AgentOutcome.SESSION_LIMIT
    assert transition_for(AgentOutcome.SESSION_LIMIT).retryable is False
    assert transition_for(AgentOutcome.SESSION_LIMIT).target_state is TaskState.BLOCKED

    ordinary = classify_agent_outcome(
        AgentRunResult(executed=True, dry_run=False, message="ok", returncode=0)
    )
    assert ordinary.outcome is not AgentOutcome.SESSION_LIMIT


# ---- CP-021-4: network-blocked detection ----


def test_agent_network_blocked_detection() -> None:
    """CP-021-4: Git/GitHub/network/filesystem/sandbox blocking is
    classified as `network_blocked`, requiring operator action."""
    for message in [
        "Network is unreachable: could not resolve host github.com",
        "git push failed: Connection refused",
        "Permission denied: sandbox blocks outbound network access",
        "fatal: unable to access 'https://github.com/...': Could not resolve host",
    ]:
        assert is_network_blocked_output(message), message
        classification = classify_agent_outcome(
            AgentRunResult(executed=True, dry_run=False, message=message, returncode=0)
        )
        assert classification.outcome is AgentOutcome.NETWORK_BLOCKED, message

    assert not is_network_blocked_output("Implemented and tests pass.")
    assert transition_for(AgentOutcome.NETWORK_BLOCKED).target_state is TaskState.MANUAL_ACTION


def test_agent_repository_locked_detection() -> None:
    """CP-021-4's "documented equivalent": a locked local repository is
    also classified so delivery never attempts a commit against it."""
    for message in [
        "fatal: Unable to create '.git/index.lock': File exists.",
        "Another git process seems to be running in this repository.",
    ]:
        assert is_repository_locked_output(message), message
        classification = classify_agent_outcome(
            AgentRunResult(executed=True, dry_run=False, message=message, returncode=0)
        )
        assert classification.outcome is AgentOutcome.REPOSITORY_LOCKED, message

    assert transition_for(AgentOutcome.REPOSITORY_LOCKED).target_state is TaskState.MANUAL_ACTION


# ---- CP-021-5: outcome-driven state transitions ----


def test_agent_outcome_state_transition() -> None:
    """CP-021-5: every `AgentOutcome` has a documented transition, and none
    ever targets `devbot:working` - an Issue is never left claimed
    (Scope §6: "No Agent outcome may leave an Issue permanently in
    `devbot:working`")."""
    assert set(AGENT_OUTCOME_TRANSITIONS) == set(AgentOutcome)
    for transition in AGENT_OUTCOME_TRANSITIONS.values():
        assert transition.target_state is not TaskState.WORKING
        if transition.target_state is not None:
            assert transition.target_state in (TaskState.MANUAL_ACTION, TaskState.BLOCKED)

    assert transition_for(AgentOutcome.APPROVAL_REQUIRED).target_state is TaskState.MANUAL_ACTION
    assert transition_for(AgentOutcome.NETWORK_BLOCKED).target_state is TaskState.MANUAL_ACTION
    assert transition_for(AgentOutcome.REPOSITORY_LOCKED).target_state is TaskState.MANUAL_ACTION
    assert transition_for(AgentOutcome.SESSION_LIMIT).target_state is TaskState.BLOCKED
    assert transition_for(AgentOutcome.AGENT_FAILED).target_state is TaskState.BLOCKED
    assert (
        transition_for(AgentOutcome.IMPLEMENTATION_SKIPPED).target_state is TaskState.MANUAL_ACTION
    )
    assert transition_for(AgentOutcome.UNKNOWN).target_state is TaskState.BLOCKED

    completed = transition_for(AgentOutcome.IMPLEMENTATION_COMPLETED)
    assert completed.proceeds_to_delivery is True
    assert completed.target_state is None

    no_changes = transition_for(AgentOutcome.NO_REPOSITORY_CHANGES)
    assert no_changes.proceeds_to_delivery is False
    assert no_changes.target_state is None


# ---- CP-021-8: operator diagnostics ----


def test_agent_outcome_diagnostics() -> None:
    """CP-021-8: diagnostics expose the normalized outcome, matched
    reason, blocked phase, resulting Issue state, and recovery action
    (Scope §9)."""
    classification = classify_agent_outcome(
        AgentRunResult(
            executed=True,
            dry_run=False,
            message="Usage limit reached, resets at 09:00 UTC",
            returncode=0,
        )
    )

    diagnostics = build_agent_outcome_diagnostics(classification)

    assert diagnostics.outcome is AgentOutcome.SESSION_LIMIT
    assert diagnostics.signal_source == "text_fallback"
    assert "Usage limit" in diagnostics.matched_reason
    assert diagnostics.blocked_phase == "pre_delivery"
    assert diagnostics.resulting_state == "devbot:blocked"
    assert diagnostics.recovery_action

    rendered = render_agent_outcome_diagnostics(diagnostics)
    assert "session_limit" in rendered
    assert "devbot:blocked" in rendered
    assert "recovery_action" in rendered

    completed_diagnostics = build_agent_outcome_diagnostics(
        classify_agent_outcome(
            AgentRunResult(executed=True, dry_run=False, message="ok", returncode=0)
        )
    )
    assert completed_diagnostics.blocked_phase == "none"
    assert completed_diagnostics.resulting_state == "delivery"

    # Scope §9: an override lets a caller describe a classification made
    # outside this module's own transition table (e.g. `devbot.polling`'s
    # post-delivery `no_repository_changes` handling).
    no_changes_diagnostics = build_agent_outcome_diagnostics(
        AgentOutcomeClassification(
            outcome=AgentOutcome.NO_REPOSITORY_CHANGES,
            signal_source="delivery_result",
            matched_reason="DeliveryResult.message == 'no_repository_changes'",
        ),
        blocked_phase="review_entry",
        resulting_state_override="devbot:manual-action",
    )
    assert no_changes_diagnostics.blocked_phase == "review_entry"
    assert no_changes_diagnostics.resulting_state == "devbot:manual-action"
