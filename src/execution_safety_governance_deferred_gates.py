"""Real, tested gate adapters for safety components not yet activated for
attended commissioning (M14 Issue 1, M14 Issue 2, M14 Issue 3).

Deliberately **not imported** by `src/execution_safety_governance.py`,
`src/robinhood_commissioning_adapter.py`,
`src/one_share_commissioning_orchestrator.py`, or
`src/robinhood_commissioning_session.py` -- nothing on the live commissioning
write path reaches this module. That is a structural signal, not an
oversight: each gate below is real and independently tested against the
actual `src/risk_limits.py`, `src/commissioning_risk_state.py`,
`src/durable_suspension_controller.py`, and `src/operational_monitor.py`
APIs.

`make_risk_limit_gate` and `make_commissioning_risk_state_gate` (M14 Issues
1/2) remain undeployed because attended commissioning still has no
repository-owned source for `AccountRiskState.realized_daily_loss` or
`KillSwitchState` -- see `src/commissioning_risk_state.py`'s own module
docstring for exactly which required risk inputs are missing.
`make_commissioning_risk_state_gate` is the honest, fail-closed alternative
to hand-assembling those inputs inline; it is undeployed for the same
missing-evidence reason `make_risk_limit_gate` is, not a separate one.

`make_operational_health_gate` (M14 Issue 1) remains undeployed because a
synchronous, single-run attended commissioning invocation has no persistent
heartbeat process to assess -- see `src/attended_commissioning_operational_
health.py` (M14 Issue 5) for the full, independently-reasoned classification
of every operational-monitor-adjacent signal this repository has.

`make_suspension_gate` is different from the other three: it is NOT
deferred for missing evidence. M14 Issue 3 (`src/commissioning_suspension_
identity.py`) established the commissioning-specific suspension-controller
identity and durable-store convention it previously lacked, and M14 Issue 7
activated durable commissioning suspension as an authoritative
`DEFAULT_COMMISSIONING_GATES` gate. That activation uses a separate,
purpose-built pure gate (`execution_safety_governance.
gate_commissioning_suspension_clear`) fed by a fact the orchestrator reads
fresh immediately before each write, not this module's `make_suspension_
gate` -- which performs its own I/O (`controller.assess(...)`) inside gate
evaluation, unlike every gate actually in `DEFAULT_COMMISSIONING_GATES`.
`make_suspension_gate` therefore remains real, tested, and genuinely
unused: a second, I/O-performing implementation of the same capability,
kept out of the live path by design rather than by missing evidence. See
that function's own docstring.

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
    `DEFAULT_COMMISSIONING_GATES` -- and, unlike every other gate in this
    module, not because required evidence is missing.

    M14 Issue 3 established the identity/durable-store convention this
    docstring used to say was missing (`src/commissioning_suspension_
    identity.py`; see `CommissioningSuspensionStateProvider.make_gate()`,
    which builds exactly this evaluator against a real identity and store).
    M14 Issue 7 then activated durable commissioning suspension as an
    authoritative `DEFAULT_COMMISSIONING_GATES` gate -- but via a separate,
    purpose-built pure gate (`execution_safety_governance.
    gate_commissioning_suspension_clear`) that only inspects an
    already-read fact, not via this function, which performs I/O
    (`controller.assess(...)`) inside gate evaluation itself. This function
    remains real, tested, and deliberately unwired: a second,
    I/O-performing implementation of the same capability that the live
    path does not use."""

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
