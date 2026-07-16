"""Normalized Agent outcome classification (Task 021: Agent Outcome
Classification Hardening).

Motivating incident (Issue #41): an implementer Agent stopped after
reporting it needed approval to run Git/`gh` commands. DevBot observed no
repository changes. The Issue already had a linked Task-contract-only PR
(only the contract-authoring commit, no implementation). Because a PR
existed and the workspace was clean, DevBot advanced the Issue straight to
`devbot:review` - a false-success transition with no implementation ever
having happened.

`classify_agent_outcome()` closes the *pre-delivery* half of that gap: it
turns one `AgentRunResult` (`devbot.agents.base`) into an explicit
`AgentOutcome` (`devbot.models`) instead of the previous implicit
"`AgentRunResult.failed` is False, and the message doesn't match the one
approval-wait pattern list we happened to check" chain. It prefers a
structured `AgentRunResult.outcome_hint` (Scope §2) when a runner sets one,
and falls back to text-matching predicates in `devbot.agents.base`
otherwise - the same predicates remain the single source of truth for what
each pattern means, this module only sequences them into one normalized
result.

Classification alone is still not proof of a completed implementation
(CP-021-11): `IMPLEMENTATION_COMPLETED` here only means "this run executed,
did not fail, and its own output matched no known blocking pattern" - it
gates whether delivery may even be *attempted*
(`AGENT_OUTCOME_TRANSITIONS[...].proceeds_to_delivery`), not whether the
Issue may enter `devbot:review`. That stronger guarantee - real commit/push
evidence, or (for a reused PR with nothing new to commit) implementation
evidence beyond a pre-existing contract-only commit
(`devbot.delivery.branch_has_implementation_evidence`) - is enforced by
`devbot.polling` at the delivery/review call sites, using this module's
classification as one input among several, never the sole one.
"""

from __future__ import annotations

from dataclasses import dataclass

from devbot.agents.base import (
    AgentRunResult,
    is_approval_required_output,
    is_implementation_skipped_output,
    is_network_blocked_output,
    is_repository_locked_output,
    is_session_limit_output,
)
from devbot.models import AgentOutcome, TaskState


@dataclass(frozen=True, slots=True)
class AgentOutcomeClassification:
    """CP-021-8: the normalized outcome plus enough operator-facing detail
    to explain *why* - never just the bare enum member."""

    outcome: AgentOutcome
    signal_source: str  # "structured" | "text_fallback"
    matched_reason: str


def classify_agent_outcome(result: AgentRunResult) -> AgentOutcomeClassification:
    """Classify one Agent invocation's result into an explicit
    `AgentOutcome` (CP-021-1).

    Order of precedence:

    1. A structured `result.outcome_hint`, when set (Scope §2) - wins over
       every text pattern, since it is a more reliable signal by
       construction.
    2. `result.dry_run` (DevBot's own config-level no-op, not a real Agent
       decision) passes through as `IMPLEMENTATION_COMPLETED` so the
       existing dry-run pipeline preview (delivery still runs verification
       under its own `dry_run` flag) is unaffected by this Task.
    3. Blocking text patterns, checked in the order the Task 021 contract
       introduces them (approval, session-limit, network-blocked,
       repository-locked), then `implementation_skipped` - checked before
       `result.failed` deliberately: a genuine block can arrive from a
       process that still exited 0 (Scope §3/§4/§5 all call this out
       explicitly), so a nonzero exit code must never suppress a more
       specific, more actionable classification.
    4. `result.failed` (nonzero exit, or unexecuted and not a dry-run) ->
       `AGENT_FAILED`.
    5. An empty/blank message from an executed, non-failed run -> `UNKNOWN`
       (CP-021-11: "succeeded and said nothing" is not an explicit
       completion signal).
    6. Otherwise -> `IMPLEMENTATION_COMPLETED` (tentative - see module
       docstring).
    """
    if result.outcome_hint is not None:
        return AgentOutcomeClassification(
            outcome=result.outcome_hint,
            signal_source="structured",
            matched_reason=f"AgentRunResult.outcome_hint={result.outcome_hint.value}",
        )

    if result.dry_run:
        return AgentOutcomeClassification(
            outcome=AgentOutcome.IMPLEMENTATION_COMPLETED,
            signal_source="text_fallback",
            matched_reason="DevBot 자체 dry-run 파이프라인 미리보기 (실제 Agent 판단 아님)",
        )

    message = result.message or ""

    if is_approval_required_output(message):
        return AgentOutcomeClassification(
            outcome=AgentOutcome.APPROVAL_REQUIRED,
            signal_source="text_fallback",
            matched_reason=f"승인 대기 패턴이 출력에서 매칭됨: {message!r}",
        )

    if is_session_limit_output(message):
        return AgentOutcomeClassification(
            outcome=AgentOutcome.SESSION_LIMIT,
            signal_source="text_fallback",
            matched_reason=f"세션/사용량 제한 패턴이 출력에서 매칭됨: {message!r}",
        )

    if is_network_blocked_output(message):
        return AgentOutcomeClassification(
            outcome=AgentOutcome.NETWORK_BLOCKED,
            signal_source="text_fallback",
            matched_reason=f"네트워크/권한 차단 패턴이 출력에서 매칭됨: {message!r}",
        )

    if is_repository_locked_output(message):
        return AgentOutcomeClassification(
            outcome=AgentOutcome.REPOSITORY_LOCKED,
            signal_source="text_fallback",
            matched_reason=f"repository 잠금 패턴이 출력에서 매칭됨: {message!r}",
        )

    if is_implementation_skipped_output(message):
        return AgentOutcomeClassification(
            outcome=AgentOutcome.IMPLEMENTATION_SKIPPED,
            signal_source="text_fallback",
            matched_reason=f"구현 건너뜀 패턴이 출력에서 매칭됨: {message!r}",
        )

    if result.failed:
        return AgentOutcomeClassification(
            outcome=AgentOutcome.AGENT_FAILED,
            signal_source="text_fallback",
            matched_reason=(
                f"AgentRunResult.failed (executed={result.executed}, "
                f"returncode={result.returncode})"
            ),
        )

    if not message.strip():
        return AgentOutcomeClassification(
            outcome=AgentOutcome.UNKNOWN,
            signal_source="text_fallback",
            matched_reason="Agent 출력이 비어 있어 명시적 완료 신호가 없음",
        )

    return AgentOutcomeClassification(
        outcome=AgentOutcome.IMPLEMENTATION_COMPLETED,
        signal_source="text_fallback",
        matched_reason=f"차단 패턴과 매칭되지 않은 정상 실행 결과: {message!r}",
    )


# ---- Outcome-driven workflow transitions (Task 021 CP-021-5) ----


@dataclass(frozen=True, slots=True)
class AgentOutcomeTransition:
    """One `AgentOutcome`'s deterministic workflow instruction (Scope §6).

    `target_state` is `None` for the two outcomes whose concrete
    `devbot:*` destination depends on evidence gathered *after* this
    classification, not on the outcome alone:

    - `IMPLEMENTATION_COMPLETED`: `proceeds_to_delivery` is True: the
      actual destination depends on `devbot.delivery.DeliveryService`'s
      result.
    - `NO_REPOSITORY_CHANGES`: only ever produced post-delivery
      (`devbot.polling`, not this module) - the destination depends on
      whether the reused PR carries implementation evidence beyond its
      pre-existing contract-only commit.
    - `RESUMABLE_INTERRUPTION`: the caller decides whether the bounded
      resume attempt can restore the Issue to its prior stable state or
      must route it to manual action.

    Every other outcome never leaves an Issue in `devbot:working` and never
    targets it either - `target_state` is always a stable terminal-for-
    this-cycle state (`manual-action` or `blocked`), matching Scope §6's
    "No Agent outcome may leave an Issue permanently in `devbot:working`."
    """

    proceeds_to_delivery: bool
    target_state: TaskState | None
    retryable: bool
    recovery_hint: str


AGENT_OUTCOME_TRANSITIONS: dict[AgentOutcome, AgentOutcomeTransition] = {
    AgentOutcome.IMPLEMENTATION_COMPLETED: AgentOutcomeTransition(
        proceeds_to_delivery=True,
        target_state=None,
        retryable=False,
        recovery_hint="구현이 완료되어 delivery를 진행합니다.",
    ),
    AgentOutcome.APPROVAL_REQUIRED: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.MANUAL_ACTION,
        retryable=False,
        recovery_hint=(
            "Agent 실행 결과가 승인 대기 상태입니다. 운영자가 확인/승인한 뒤 "
            "Issue를 devbot:ready로 되돌리세요."
        ),
    ),
    AgentOutcome.NETWORK_BLOCKED: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.MANUAL_ACTION,
        retryable=False,
        recovery_hint=(
            "Git/GitHub/네트워크/파일시스템/샌드박스 접근이 실행 환경에서 차단되었습니다. "
            "실행 환경 권한을 확인한 뒤 Issue를 devbot:ready로 되돌리세요."
        ),
    ),
    AgentOutcome.REPOSITORY_LOCKED: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.MANUAL_ACTION,
        retryable=False,
        recovery_hint=(
            "Repository/워크스페이스가 잠겨 있습니다. 잠금을 해제한 뒤 "
            "Issue를 devbot:ready로 되돌리세요."
        ),
    ),
    AgentOutcome.SESSION_LIMIT: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.BLOCKED,
        retryable=False,
        recovery_hint=(
            "Agent 세션/사용량 제한입니다. 자동 재시도하지 않습니다. "
            "제한이 해제된 뒤 Issue를 devbot:ready(또는 이전 상태)로 되돌리세요."
        ),
    ),
    AgentOutcome.RESUMABLE_INTERRUPTION: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=None,
        retryable=True,
        recovery_hint=(
            "Agent 실행이 타임아웃/중단되어 기존 worktree 변경을 보존했습니다. "
            "다음 bounded resume 시도에서 같은 Branch/PR/worktree를 이어서 사용합니다."
        ),
    ),
    AgentOutcome.AGENT_FAILED: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.BLOCKED,
        retryable=False,
        recovery_hint="Agent 실행이 실패했습니다. 원인을 확인한 뒤 Issue를 이전 상태로 되돌리세요.",
    ),
    AgentOutcome.IMPLEMENTATION_SKIPPED: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.MANUAL_ACTION,
        retryable=False,
        recovery_hint=(
            "Agent가 구현을 건너뛰었습니다. 실제로 구현이 필요한지 확인한 뒤 "
            "Issue를 devbot:ready로 되돌리세요."
        ),
    ),
    AgentOutcome.NO_REPOSITORY_CHANGES: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=None,
        retryable=False,
        recovery_hint=(
            "신규 repository 변경이 없습니다. 연결된 PR에 계약(contract) 커밋 이외의 "
            "구현 증거가 있는지 확인하세요 - 없다면 실제 구현을 진행한 뒤 다시 시도하세요."
        ),
    ),
    AgentOutcome.UNKNOWN: AgentOutcomeTransition(
        proceeds_to_delivery=False,
        target_state=TaskState.BLOCKED,
        retryable=False,
        recovery_hint="Agent 실행 결과를 분류할 수 없습니다. 로그를 확인한 뒤 사람이 검토하세요.",
    ),
}


def transition_for(outcome: AgentOutcome) -> AgentOutcomeTransition:
    """The `AgentOutcomeTransition` for `outcome`. Every `AgentOutcome` is
    covered (`KeyError` otherwise signals an outcome was added without
    updating this table)."""
    return AGENT_OUTCOME_TRANSITIONS[outcome]


# ---- Operator diagnostics (Task 021 CP-021-8/Scope §9) ----


@dataclass(frozen=True, slots=True)
class AgentOutcomeDiagnostics:
    """Operator-facing detail for one classified outcome: the normalized
    outcome, the matched structured/fallback signal, which workflow phase
    it blocked (if any), the resulting Issue state, and the required
    recovery action (Scope §9)."""

    outcome: AgentOutcome
    signal_source: str
    matched_reason: str
    blocked_phase: str
    resulting_state: str
    recovery_action: str


def build_agent_outcome_diagnostics(
    classification: AgentOutcomeClassification,
    *,
    blocked_phase: str | None = None,
    resulting_state_override: str | None = None,
) -> AgentOutcomeDiagnostics:
    """Build `AgentOutcomeDiagnostics` for `classification`.

    `blocked_phase`/`resulting_state_override` let a caller describe a
    classification made outside this module's own transition table (e.g.
    `devbot.polling`'s post-delivery `NO_REPOSITORY_CHANGES` handling,
    whose actual destination depends on evidence this module never sees) -
    both default to what `AGENT_OUTCOME_TRANSITIONS` alone implies."""
    transition = AGENT_OUTCOME_TRANSITIONS[classification.outcome]

    if resulting_state_override is not None:
        resulting_state = resulting_state_override
    elif transition.proceeds_to_delivery:
        resulting_state = "delivery"
    elif transition.target_state is not None:
        resulting_state = f"devbot:{transition.target_state.value}"
    else:
        resulting_state = "unresolved"

    resolved_phase = blocked_phase
    if resolved_phase is None:
        resolved_phase = "none" if transition.proceeds_to_delivery else "pre_delivery"

    return AgentOutcomeDiagnostics(
        outcome=classification.outcome,
        signal_source=classification.signal_source,
        matched_reason=classification.matched_reason,
        blocked_phase=resolved_phase,
        resulting_state=resulting_state,
        recovery_action=transition.recovery_hint,
    )


def render_agent_outcome_diagnostics(diagnostics: AgentOutcomeDiagnostics) -> str:
    """Render `diagnostics` as operator-facing text (log lines)."""
    return "\n".join(
        [
            "[DevBot Agent Outcome]",
            f"outcome: {diagnostics.outcome.value}",
            f"signal_source: {diagnostics.signal_source}",
            f"matched_reason: {diagnostics.matched_reason}",
            f"blocked_phase: {diagnostics.blocked_phase}",
            f"resulting_state: {diagnostics.resulting_state}",
            f"recovery_action: {diagnostics.recovery_action}",
        ]
    )
