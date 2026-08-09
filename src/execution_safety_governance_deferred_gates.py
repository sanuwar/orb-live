"""Real, tested gate adapters for safety components not yet activated for
attended commissioning (M14 Issue 1, M14 Issue 2).

Deliberately **not imported** by `src/execution_safety_governance.py`,
`src/robinhood_commissioning_adapter.py`,
`src/one_share_commissioning_orchestrator.py`, or
`src/robinhood_commissioning_session.py` -- nothing on the live commissioning
write path reaches this module. That is a structural signal, not an
oversight: each gate below is real and independently tested against the
actual `src/risk_limits.py`, `src/commissioning_risk_state.py`,
`src/durable_suspension_controller.py`, and `src/operational_monitor.py`
APIs, but each also requires evidence that does not exist yet for a
synchronous, attended, single-run commissioning flow -- a
realized-daily-loss/kill-switch source, a commissioning-specific
suspension-controller identity and durable store, or a live heartbeat
process. Synthesizing that evidence to activate these gates today would be
exactly the "fake integration" the M14 Issue 1 handoff prohibits.

`make_commissioning_risk_state_gate` (M14 Issue 2) is the honest,
fail-closed alternative to hand-assembling `AccountRiskState`/
`KillSwitchState` inline: it wraps
`src.commissioning_risk_state.evaluate_commissioning_risk_state`, whose own
module docstring documents exactly which required risk inputs (kill switch,
realized daily loss) have no repository-owned source today. It remains
undeployed for the same reason `make_risk_limit_gate` does -- see that
module's docstring for the full accounting of what is and is not honestly
available.

Kept out of `src/execution_safety_governance.py`'s import graph specifically
so these deferrals never silently expand `PINNED_IMPLEMENTATION_CLOSURE`
(`src/read_only_authorization_manifest.py`): activating any gate here is a
future milestone's explicit decision, not an accidental side effect of an
unrelated edit to this file.
"""

from __future__ import annotations

from typing import Callable

from src.execution_safety_governance import (
    ExecutionSafetyContext,
    GateEvaluator,
    GateOutcome,
    GateResult,
)


def _satisfied(name: str) -> GateResult:
    return GateResult(name, GateOutcome.SATISFIED, "OK")


def _failed(name: str, reason_code: str) -> GateResult:
    return GateResult(name, GateOutcome.FAILED, reason_code)


def make_risk_limit_gate(
    *,
    proposal_for: Callable[[ExecutionSafetyContext], "ActionProposal"],  # noqa: F821
    limits: "RiskLimits",  # noqa: F821
    state: "AccountRiskState",  # noqa: F821
    kill_switch: "KillSwitchState",  # noqa: F821
    ledger: "ActionLedgerBase",  # noqa: F821
    current_monotonic: float,
) -> GateEvaluator:
    """Wrap `src.risk_limits.evaluate`. Not part of `DEFAULT_COMMISSIONING_
    GATES`: attended commissioning has no source for `AccountRiskState.
    realized_daily_loss` or `KillSwitchState` today."""

    from src.risk_limits import RiskDecision, evaluate

    def _gate(context: ExecutionSafetyContext) -> GateResult:
        name = "risk_limits"
        try:
            proposal = proposal_for(context)
            assessment = evaluate(
                proposal,
                limits,
                state,
                kill_switch=kill_switch,
                ledger=ledger,
                current_monotonic=current_monotonic,
            )
        except Exception:
            return _failed(name, "DENY_RISK_EVALUATION_FAILED")
        if assessment.decision is not RiskDecision.WITHIN_LIMITS:
            return _failed(name, f"DENY_RISK_{assessment.decision.value}")
        return _satisfied(name)

    return _gate


def make_commissioning_risk_state_gate(
    *,
    evidence_provider: Callable[[ExecutionSafetyContext], "RiskStateEvidence"],  # noqa: F821
) -> GateEvaluator:
    """Wrap `src.commissioning_risk_state.evaluate_commissioning_risk_state`
    output (M14 Issue 2). Not part of `DEFAULT_COMMISSIONING_GATES`: see
    `src/commissioning_risk_state.py` for exactly which required risk inputs
    (kill switch, realized daily loss) have no honest source for attended
    one-share commissioning today.

    `evidence_provider` is called with the exact `ExecutionSafetyContext`
    under evaluation, so it can build a `RequiredRiskInputAvailability` and
    the underlying `risk_limits` inputs (or admit they are unavailable) using
    the run and action this specific write is for. The returned evidence is
    checked for exact identity binding independently of whatever the
    provider claims, exactly as `decision_binds_write` re-checks a governance
    decision at the write boundary.
    """

    from src.commissioning_risk_state import (
        RiskEvidenceStatus,
        RiskStateEvidence,
        risk_state_binds_action,
    )

    def _gate(context: ExecutionSafetyContext) -> GateResult:
        name = "commissioning_risk_state"
        try:
            evidence = evidence_provider(context)
        except Exception:
            return _failed(name, "DENY_RISK_STATE_PROVIDER_EXCEPTION")
        if not isinstance(evidence, RiskStateEvidence):
            return _failed(name, "DENY_RISK_STATE_EVIDENCE_MALFORMED")
        if not risk_state_binds_action(
            evidence, run_id=context.run_id, action_identity=context.action_identity()
        ):
            return _failed(name, "DENY_RISK_STATE_IDENTITY_MISMATCH")
        if evidence.status is not RiskEvidenceStatus.AVAILABLE_VALID:
            return _failed(name, f"DENY_RISK_STATE_{evidence.status.value}")
        return _satisfied(name)

    return _gate


def make_suspension_gate(
    *, controller: "DurableSuspensionController", controller_id: str  # noqa: F821
) -> GateEvaluator:
    """Wrap `DurableSuspensionController.assess`. Not part of
    `DEFAULT_COMMISSIONING_GATES`: commissioning has no registered
    controller identity or durable store convention today."""

    from src.durable_suspension_controller import ControllerState

    def _gate(context: ExecutionSafetyContext) -> GateResult:
        name = "suspension_state"
        try:
            decision = controller.assess(controller_id)
        except Exception:
            return _failed(name, "DENY_SUSPENSION_READ_FAILED")
        if decision.state is not ControllerState.CLEAR:
            return _failed(name, f"DENY_SUSPENSION_{decision.state.value}")
        return _satisfied(name)

    return _gate


def make_operational_health_gate(
    *,
    last_beat_monotonic: float,
    current_monotonic: float,
    interval_seconds: float,
    missed_beats_before_alert: int = 2,
) -> GateEvaluator:
    """Wrap `src.operational_monitor.assess_heartbeat`. Not part of
    `DEFAULT_COMMISSIONING_GATES`: a synchronous, single-run attended
    commissioning invocation has no persistent heartbeat process to assess."""

    from src.operational_monitor import HeartbeatStatus, assess_heartbeat

    def _gate(context: ExecutionSafetyContext) -> GateResult:
        name = "operational_health"
        try:
            assessment = assess_heartbeat(
                last_beat_monotonic,
                current_monotonic=current_monotonic,
                interval_seconds=interval_seconds,
                missed_beats_before_alert=missed_beats_before_alert,
            )
        except Exception:
            return _failed(name, "DENY_OPERATIONAL_HEALTH_EVALUATION_FAILED")
        if assessment.status is not HeartbeatStatus.HEALTHY:
            return _failed(name, f"DENY_OPERATIONAL_HEALTH_{assessment.status.value}")
        return _satisfied(name)

    return _gate


__all__ = [
    "make_risk_limit_gate",
    "make_commissioning_risk_state_gate",
    "make_suspension_gate",
    "make_operational_health_gate",
]
