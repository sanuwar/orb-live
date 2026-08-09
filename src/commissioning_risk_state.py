"""Honest, fail-closed AccountRiskState / kill-switch evidence source for the
attended one-share commissioning safety-governance boundary (M14 Issue 2).

## The gap this module fills, and the gap it deliberately does not fill

`src/execution_safety_governance.py` explains why `src/risk_limits.py` is not
wired into `DEFAULT_COMMISSIONING_GATES`: `risk_limits.evaluate` requires a
realized-daily-loss figure and a `KillSwitchState`, and no durable,
repository-owned source for either exists yet for a synchronous, attended,
single-run commissioning flow. This module does not manufacture one. It
instead makes the gap structural and testable rather than implicit:

* six distinct `RiskEvidenceStatus` outcomes, so "risk state could not be
  established" is never silently reported as if it were a PASS;
* a `RiskStateEvidence` record cryptographically bound to the exact run and
  action it was computed for, mirroring
  `ExecutionSafetyContext.action_identity` in
  `src/execution_safety_governance.py`;
* `evaluate_commissioning_risk_state`, a provider that wraps
  `src.risk_limits.evaluate` but requires the caller to make an EXPLICIT,
  no-default admission (`RequiredRiskInputAvailability`) of which required
  inputs -- kill switch, realized daily loss, position exposure -- have an
  honest source *right now*. A caller cannot mark an input unavailable and
  then have a supplied stub value quietly win: any input marked unavailable
  forces the whole evaluation to `UNAVAILABLE` before any value is even
  inspected.

Today, for the live one-share commissioning flow, no caller can honestly set
`kill_switch_source_available=True` or
`realized_daily_loss_source_available=True`: there is no durable local
kill-switch state and no realized-P&L ledger for the commissioned account.
Position exposure (open AAPL quantity) is the one input this flow already
reads honestly, from the same broker position reads
`OneShareCommissioningOrchestrator` performs for preflight and
reconciliation -- so it is modelled as a separately toggleable input rather
than lumped in with the two that remain genuinely unavailable.

`make_commissioning_risk_state_gate`
(`src/execution_safety_governance_deferred_gates.py`) wraps this provider
and is real and independently tested, but -- exactly like the pre-existing
`make_risk_limit_gate` it complements -- is deliberately NOT wired into
`DEFAULT_COMMISSIONING_GATES`. Wiring it today would either fabricate the
missing kill-switch/daily-loss evidence (prohibited) or turn the gate into
an unconditional DENY that never reflects real state, which is not an
honest risk assessment either. Activating it is a future milestone's
decision, once a real kill-switch and realized-daily-loss source exists.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.protection_observation_model import canonical_bytes
from src.risk_limits import (
    AccountRiskState,
    ActionLedgerBase,
    ActionProposal,
    KillSwitchState,
    RiskDecision,
    RiskLimits,
    evaluate as evaluate_risk_limits,
)


class RiskStateError(RuntimeError):
    """This module's own construction contract was violated.

    Distinct from a non-passing `RiskEvidenceStatus`: this is raised only for
    a structurally invalid value passed to a constructor this module owns.
    Whether risk evidence should be treated as passing is always expressed as
    a `RiskStateEvidence.status`, never as an exception --
    `evaluate_commissioning_risk_state` never raises.
    """


class RiskEvidenceStatus(str, Enum):
    """Every conclusion this module can reach. Exactly one is a PASS.

    No status ever collapses into `AVAILABLE_VALID` by omission: a caller
    that forgets to declare an input, supplies a stale proposal, or hits an
    exception lands on a distinct, named non-passing status rather than a
    generic "not OK" that could be mistaken for permission.
    """

    #: Every required input was honestly available and the proposal is
    #: within every configured limit.
    AVAILABLE_VALID = "AVAILABLE_VALID"
    #: Every required input was honestly available, but the proposal (or the
    #: kill switch, or a duplicate action) failed a real, evaluated check.
    AVAILABLE_BLOCKING = "AVAILABLE_BLOCKING"
    #: At least one required input has no honest source right now. The most
    #: important status in this module: it is where kill-switch and
    #: realized-daily-loss evidence land for attended commissioning today.
    UNAVAILABLE = "UNAVAILABLE"
    #: Every input was available, but the observation backing the proposal is
    #: too old (or from the future) relative to the evaluation clock.
    STALE = "STALE"
    #: A caller declared an input available but did not actually supply the
    #: object that input requires -- an internal contradiction, not a normal
    #: "missing evidence" case, and reported distinctly from it.
    INCONSISTENT = "INCONSISTENT"
    #: Evidence construction or evaluation raised. Never propagated.
    ERROR = "ERROR"


#: Everything that is not `AVAILABLE_VALID`. Derived, so a new status cannot
#: be accidentally treated as passing.
NON_PASSING_RISK_EVIDENCE_STATUSES: frozenset[RiskEvidenceStatus] = frozenset(
    status
    for status in RiskEvidenceStatus
    if status is not RiskEvidenceStatus.AVAILABLE_VALID
)

_DIGEST_DOMAIN = b"orb-live/commissioning-risk-state/v1"


@dataclass(frozen=True)
class RequiredRiskInputAvailability:
    """An explicit, no-default admission of which required risk inputs have
    an honest source right now.

    Every field is required, with no default: a default here would be
    exactly the fabricated evidence this module exists to prevent. A caller
    that has not decided whether an input is honestly available cannot
    construct this object at all.
    """

    kill_switch_source_available: bool
    realized_daily_loss_source_available: bool
    position_exposure_source_available: bool

    def __post_init__(self) -> None:
        for name in (
            "kill_switch_source_available",
            "realized_daily_loss_source_available",
            "position_exposure_source_available",
        ):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise RiskStateError(f"{name} must be an explicit bool")

    def all_available(self) -> bool:
        return (
            self.kill_switch_source_available
            and self.realized_daily_loss_source_available
            and self.position_exposure_source_available
        )

    def missing_reason_codes(self) -> tuple[str, ...]:
        codes = []
        if not self.kill_switch_source_available:
            codes.append("RISK_STATE_KILL_SWITCH_SOURCE_UNAVAILABLE")
        if not self.realized_daily_loss_source_available:
            codes.append("RISK_STATE_REALIZED_DAILY_LOSS_SOURCE_UNAVAILABLE")
        if not self.position_exposure_source_available:
            codes.append("RISK_STATE_POSITION_EXPOSURE_SOURCE_UNAVAILABLE")
        return tuple(codes)


@dataclass(frozen=True)
class RiskStateEvidence:
    """The result of one risk-state evaluation, bound to the exact run and
    action it was computed for.

    `evidence_digest` is a deterministic hash over every field below it
    (excluding itself), the same domain-separated construction
    `src/execution_safety_governance.py` uses for its own decision digest.
    Two evaluations that agree on every field always agree on the digest;
    changing any field -- including `run_id` or `action_identity` alone --
    changes it. This is what lets a consuming gate detect a forged or
    mismatched evidence object without re-deriving it.
    """

    run_id: str
    action_identity: str
    status: RiskEvidenceStatus
    reason_codes: tuple[str, ...]
    risk_decision: str
    evidence_digest: str

    @property
    def passes(self) -> bool:
        return self.status is RiskEvidenceStatus.AVAILABLE_VALID


def _evidence_digest(
    *,
    run_id: str,
    action_identity: str,
    status: RiskEvidenceStatus,
    reason_codes: tuple[str, ...],
    risk_decision: str,
) -> str:
    payload: dict[str, Any] = {
        "run_id": run_id,
        "action_identity": action_identity,
        "status": status.value,
        "reason_codes": list(reason_codes),
        "risk_decision": risk_decision,
    }
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00evidence\x00" + canonical_bytes(payload)
    ).hexdigest()


def _evidence(
    *,
    run_id: str,
    action_identity: str,
    status: RiskEvidenceStatus,
    reason_codes: tuple[str, ...],
    risk_decision: str = "NONE",
) -> RiskStateEvidence:
    digest = _evidence_digest(
        run_id=run_id,
        action_identity=action_identity,
        status=status,
        reason_codes=reason_codes,
        risk_decision=risk_decision,
    )
    return RiskStateEvidence(
        run_id=run_id,
        action_identity=action_identity,
        status=status,
        reason_codes=reason_codes,
        risk_decision=risk_decision,
        evidence_digest=digest,
    )


def _safe_str(value: Any) -> str:
    try:
        return value if isinstance(value, str) else repr(type(value))
    except Exception:
        return "UNKNOWN"


def evaluate_commissioning_risk_state(
    *,
    run_id: str,
    action_identity: str,
    availability: RequiredRiskInputAvailability,
    proposal: ActionProposal | None = None,
    limits: RiskLimits | None = None,
    state: AccountRiskState | None = None,
    kill_switch: KillSwitchState | None = None,
    ledger: ActionLedgerBase | None = None,
    current_monotonic: float | None = None,
) -> RiskStateEvidence:
    """Evaluate risk state for exactly one run/action, never raising.

    Order is deliberate:

    1. `run_id` / `action_identity` must be non-empty strings -- otherwise no
       binding is even possible, and this is reported as `ERROR` rather than
       `UNAVAILABLE`, since it is a caller-construction problem, not a
       missing-evidence-source problem.
    2. Every required input must be explicitly declared available. Any
       omission short-circuits to `UNAVAILABLE` before any of `proposal`,
       `limits`, `state`, `kill_switch`, or `ledger` is even inspected --
       an unavailable declaration always wins over a supplied value.
    3. If every input is declared available but the objects themselves were
       not actually supplied, that is `INCONSISTENT`: a caller contradicting
       its own availability declaration, not a normal absence.
    4. Only then is `src.risk_limits.evaluate` invoked, and its outcome is
       mapped onto a status that never defaults to `AVAILABLE_VALID`: a
       staleness denial becomes `STALE`, every other denial becomes
       `AVAILABLE_BLOCKING`, and any exception becomes `ERROR`.
    """
    safe_run_id = _safe_str(run_id)
    safe_action_identity = _safe_str(action_identity)
    try:
        if not isinstance(run_id, str) or not run_id:
            return _evidence(
                run_id=safe_run_id, action_identity=safe_action_identity,
                status=RiskEvidenceStatus.ERROR,
                reason_codes=("RISK_STATE_RUN_ID_INVALID",),
            )
        if not isinstance(action_identity, str) or not action_identity:
            return _evidence(
                run_id=run_id, action_identity=safe_action_identity,
                status=RiskEvidenceStatus.ERROR,
                reason_codes=("RISK_STATE_ACTION_IDENTITY_INVALID",),
            )
        if not isinstance(availability, RequiredRiskInputAvailability):
            return _evidence(
                run_id=run_id, action_identity=action_identity,
                status=RiskEvidenceStatus.UNAVAILABLE,
                reason_codes=("RISK_STATE_AVAILABILITY_DECLARATION_MISSING",),
            )
        missing = availability.missing_reason_codes()
        if missing:
            return _evidence(
                run_id=run_id, action_identity=action_identity,
                status=RiskEvidenceStatus.UNAVAILABLE, reason_codes=missing,
            )
        if (
            proposal is None or limits is None or state is None
            or kill_switch is None or ledger is None
            or current_monotonic is None
        ):
            return _evidence(
                run_id=run_id, action_identity=action_identity,
                status=RiskEvidenceStatus.INCONSISTENT,
                reason_codes=("RISK_STATE_DECLARED_AVAILABLE_BUT_NOT_SUPPLIED",),
            )
        assessment = evaluate_risk_limits(
            proposal, limits, state, kill_switch=kill_switch, ledger=ledger,
            current_monotonic=current_monotonic,
        )
    except Exception:
        return _evidence(
            run_id=safe_run_id, action_identity=safe_action_identity,
            status=RiskEvidenceStatus.ERROR,
            reason_codes=("RISK_STATE_EVALUATION_EXCEPTION",),
        )

    if assessment.decision is RiskDecision.WITHIN_LIMITS:
        return _evidence(
            run_id=run_id, action_identity=action_identity,
            status=RiskEvidenceStatus.AVAILABLE_VALID, reason_codes=(),
            risk_decision=assessment.decision.value,
        )
    if assessment.decision is RiskDecision.DENIED_STALE_DATA:
        return _evidence(
            run_id=run_id, action_identity=action_identity,
            status=RiskEvidenceStatus.STALE,
            reason_codes=(f"RISK_STATE_{assessment.decision.value}",),
            risk_decision=assessment.decision.value,
        )
    return _evidence(
        run_id=run_id, action_identity=action_identity,
        status=RiskEvidenceStatus.AVAILABLE_BLOCKING,
        reason_codes=(f"RISK_STATE_{assessment.decision.value}",),
        risk_decision=assessment.decision.value,
    )


def risk_state_binds_action(
    evidence: Any, *, run_id: str, action_identity: str
) -> bool:
    """Whether `evidence` was computed for exactly this run and action.

    Used at a gate boundary as a defense-in-depth check independent of
    however `evidence` was produced -- mirrors
    `decision_binds_write`/`decision_is_fresh` in
    `src/execution_safety_governance.py`. Evidence computed for a different
    run or a different action (even one field different) never binds.
    """
    if not isinstance(evidence, RiskStateEvidence):
        return False
    return evidence.run_id == run_id and evidence.action_identity == action_identity


__all__ = [
    "NON_PASSING_RISK_EVIDENCE_STATUSES",
    "RequiredRiskInputAvailability",
    "RiskEvidenceStatus",
    "RiskStateError",
    "RiskStateEvidence",
    "evaluate_commissioning_risk_state",
    "risk_state_binds_action",
]
