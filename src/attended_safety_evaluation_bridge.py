"""Non-mutating bridge from live preflight evidence to M14 safety evaluation."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from src.execution_safety_governance import (
    ACTION_FOR_ROLE, AUTHORITY_CEILING, UNATTENDED_PILOT_GATES,
    UNATTENDED_POLICY_VERSION, CommissioningGovernanceEvidence, CommissioningMode,
    ExecutionAction, ExecutionSafetyContext, ExecutionSafetyDecision,
    ExecutionSafetyOutcome, OperatorAcknowledgements, compute_decision_digest,
    evaluate_execution_safety,
)
from src.live_attended_preflight_inputs import (
    LiveAttendedPreflightResult, LiveAttendedPreflightState,
    compute_live_attended_preflight_digest,
)
from src.protection_observation_model import canonical_bytes
from src.read_only_authorization_manifest import checkpoint_implementation_digest

ATTENDED_SAFETY_EVALUATION_POLICY_VERSION = "attended-safety-evaluation-bridge-v1"
PROTECTIVE_STOP_MUTATION_BOUNDARY = "RobinhoodCommissioningAdapter._call"
_DIGEST_DOMAIN = b"orb-live/attended-safety-evaluation-bridge/v1"
_REQUIRED_ACKNOWLEDGEMENTS = (
    "acknowledges_loss_may_exceed_plan", "operator_present",
    "robinhood_ui_open", "no_second_trade",
)


class AttendedSafetyEvaluationState(str, Enum):
    SAFETY_EVALUATED_ALLOW = "SAFETY_EVALUATED_ALLOW"
    SAFETY_EVALUATED_DENY = "SAFETY_EVALUATED_DENY"
    SAFETY_INPUT_INCOMPLETE = "SAFETY_INPUT_INCOMPLETE"
    SOURCE_INVALID = "SOURCE_INVALID"


class ProtectiveStopSafetyState(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    INCOMPLETE = "INCOMPLETE"


class AttendedExitSafetyState(str, Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class ProtectiveStopSafetyInputs:
    decision_time: datetime
    run_id: str
    source_shadow_decision_id: str
    source_handoff_id: str
    entry_action_identity: str
    entry_order_identity: str
    broker_snapshot_identity: str
    symbol: str
    authoritative_filled_quantity: str
    protective_quantity: str
    stop_price: str
    implementation_digest: str
    release_sha: str
    account_fingerprint_reference: str
    suspension_state: str
    operator_acknowledgements: OperatorAcknowledgements | None
    operator_attended: bool
    source_hash_verified: bool


@dataclass(frozen=True)
class ProtectiveStopSafetyEvaluation:
    source_handoff_id: str
    source_shadow_decision_id: str
    safety_context_identity: str | None
    safety_decision_identity: str | None
    protective_action_identity: str | None
    safety_result: ProtectiveStopSafetyState
    reason_codes: tuple[str, ...]
    evaluated_at: datetime
    execution_authorized: bool = False
    broker_write_count: int = 0
    protection_grounded: bool = False
    authority_ceiling: str = AUTHORITY_CEILING

    def __post_init__(self) -> None:
        if self.execution_authorized or self.broker_write_count != 0 or self.protection_grounded:
            raise ValueError("protective-stop safety evidence cannot write or authorize")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")

@dataclass(frozen=True)
class AttendedExitSafetyInputs:
    decision_time: datetime
    run_id: str
    source_shadow_decision_id: str
    source_handoff_id: str
    protective_action_identity: str
    protective_order_identity: str
    broker_snapshot_identity: str
    symbol: str
    authoritative_position_quantity: str
    exit_quantity: str
    exit_reason: str
    stop_cancellation_confirmed: bool
    implementation_digest: str
    release_sha: str
    account_fingerprint_reference: str
    suspension_state: str
    operator_acknowledgements: OperatorAcknowledgements | None
    operator_attended: bool
    source_hash_verified: bool


@dataclass(frozen=True)
class AttendedExitSafetyEvaluation:
    source_handoff_id: str
    source_shadow_decision_id: str
    safety_context_identity: str | None
    safety_decision_identity: str | None
    exit_action_identity: str | None
    safety_result: AttendedExitSafetyState
    reason_codes: tuple[str, ...]
    evaluated_at: datetime
    execution_authorized: bool = False
    broker_write_count: int = 0
    protection_grounded: bool = False
    authority_ceiling: str = AUTHORITY_CEILING

    def __post_init__(self) -> None:
        if self.execution_authorized or self.broker_write_count != 0 or self.protection_grounded:
            raise ValueError("attended-exit safety evidence cannot write or authorize")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


@dataclass(frozen=True)
class AttendedSafetyInputs:
    """Explicit action-time facts; no acknowledgement has a permissive default."""

    decision_time: datetime
    operator_acknowledgements: OperatorAcknowledgements | None = None
    operator_attended: bool = False
    source_hash_verified: bool = False
    release_sha: str = ""


@dataclass(frozen=True)
class AttendedSafetyEvaluationResult:
    evaluation_id: str
    evaluation_digest: str
    source_live_preflight_id: str
    source_preflight_id: str
    source_review_id: str
    source_dry_run_id: str
    source_shadow_decision_id: str
    safety_context_id: str | None
    safety_context_digest: str | None
    safety_decision_id: str | None
    safety_decision_digest: str | None
    action_identity: str | None
    symbol: str
    trading_date: str
    implementation_digest: str
    strategy_version: str
    market_data_digest: str
    opening_range_identity: str
    quantity: str
    account_fingerprint_status: str
    suspension_status: str
    suspension_decision_identity: str | None
    candidate_freshness_status: str
    operator_acknowledgements_status: str
    required_operator_acknowledgements: tuple[str, ...]
    safety_result: str
    source_hash_status: str
    release_sha: str
    blocking_reasons: tuple[str, ...]
    evaluation_state: AttendedSafetyEvaluationState
    protection_grounded: bool
    execution_authorized: bool
    broker_write_count: int
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.evaluation_id != self.evaluation_digest:
            raise ValueError("evaluation identity must equal its digest")
        if self.quantity != "1":
            raise ValueError("bridge quantity must remain exactly one share")
        if self.protection_grounded is not False:
            raise ValueError("safety evaluation cannot ground protection")
        if self.execution_authorized is not False or self.broker_write_count != 0:
            raise ValueError("safety evaluation cannot authorize or record a broker write")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _dedupe(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _fields(result: AttendedSafetyEvaluationResult) -> dict[str, Any]:
    return {
        "source_live_preflight_id": result.source_live_preflight_id,
        "source_preflight_id": result.source_preflight_id,
        "source_review_id": result.source_review_id,
        "source_dry_run_id": result.source_dry_run_id,
        "source_shadow_decision_id": result.source_shadow_decision_id,
        "safety_context_id": result.safety_context_id or "",
        "safety_context_digest": result.safety_context_digest or "",
        "safety_decision_id": result.safety_decision_id or "",
        "safety_decision_digest": result.safety_decision_digest or "",
        "action_identity": result.action_identity or "",
        "symbol": result.symbol, "trading_date": result.trading_date,
        "implementation_digest": result.implementation_digest,
        "strategy_version": result.strategy_version,
        "market_data_digest": result.market_data_digest,
        "opening_range_identity": result.opening_range_identity,
        "quantity": result.quantity,
        "account_fingerprint_status": result.account_fingerprint_status,
        "suspension_status": result.suspension_status,
        "suspension_decision_identity": result.suspension_decision_identity or "",
        "candidate_freshness_status": result.candidate_freshness_status,
        "operator_acknowledgements_status": result.operator_acknowledgements_status,
        "required_operator_acknowledgements": result.required_operator_acknowledgements,
        "safety_result": result.safety_result,
        "source_hash_status": result.source_hash_status,
        "release_sha": result.release_sha,
        "blocking_reasons": result.blocking_reasons,
        "evaluation_state": result.evaluation_state.value,
        "protection_grounded": False, "execution_authorized": False,
        "broker_write_count": 0, "authority_ceiling": result.authority_ceiling,
    }


def compute_attended_safety_evaluation_digest(result: AttendedSafetyEvaluationResult) -> str:
    if not isinstance(result, AttendedSafetyEvaluationResult):
        raise TypeError("result must be an AttendedSafetyEvaluationResult")
    return hashlib.sha256(_DIGEST_DOMAIN + b"\x00evaluation\x00" + canonical_bytes(_fields(result))).hexdigest()


def _decision_is_authentic(decision: ExecutionSafetyDecision) -> bool:
    digest = compute_decision_digest(
        policy_version=decision.policy_version, action_identity=decision.action_identity,
        outcome=decision.outcome, satisfied=decision.satisfied_gates,
        failed=decision.failed_gates, reasons=decision.reason_codes,
        authority_ceiling=decision.authority_ceiling, bound_role=decision.bound_role,
        bound_symbol=decision.bound_symbol, bound_side=decision.bound_side,
        bound_order_type=decision.bound_order_type, bound_quantity=decision.bound_quantity,
        bound_stop_price=decision.bound_stop_price, bound_order_id=decision.bound_order_id,
    )
    return decision.decision_id == digest and decision.decision_digest == digest


def build_attended_safety_evaluation_result(
    live_preflight: LiveAttendedPreflightResult,
    *, inputs: AttendedSafetyInputs,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> AttendedSafetyEvaluationResult:
    """Evaluate the existing M14 gates only; never invoke a broker boundary."""
    if not isinstance(live_preflight, LiveAttendedPreflightResult):
        raise TypeError("live_preflight must be an authentic LiveAttendedPreflightResult")
    if inputs.decision_time.tzinfo is None or inputs.decision_time.utcoffset() is None:
        raise ValueError("decision_time must be timezone-aware")
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(root or Path(__file__).resolve().parents[1])
    reasons: list[str] = []
    if compute_live_attended_preflight_digest(live_preflight) != live_preflight.live_preflight_digest:
        reasons.append("LIVE_PREFLIGHT_DIGEST_MISMATCH")
    if live_preflight.result_state is not LiveAttendedPreflightState.LIVE_PREFLIGHT_READY:
        reasons.append("LIVE_PREFLIGHT_NOT_READY")
    if live_preflight.implementation_digest != trusted:
        reasons.append("IMPLEMENTATION_DIGEST_MISMATCH")
    if live_preflight.quantity != "1" or live_preflight.symbol != "AAPL":
        reasons.append("ONE_SHARE_ACTION_MISMATCH")
    if live_preflight.account_fingerprint_observation_status != "MATCHED" or not live_preflight.account_fingerprint_reference:
        reasons.append("ACCOUNT_IDENTITY_REQUIRED")
    if live_preflight.suspension_observation_status != "CLEAR":
        reasons.append("SUSPENSION_CLEARANCE_REQUIRED")
    if live_preflight.candidate_freshness_status != "FRESH":
        reasons.append("STALE_CANDIDATE")
    if live_preflight.protection_grounded:
        reasons.append("PROTECTION_GROUNDING_STATE_INVALID")

    acknowledgement_status = "REQUIRED"
    if inputs.operator_acknowledgements is not None:
        acknowledgement_status = "SATISFIED" if inputs.operator_acknowledgements.all_satisfied() else "REQUIRED"
    if reasons:
        state = AttendedSafetyEvaluationState.SOURCE_INVALID
        decision = None
        context = None
    elif inputs.operator_acknowledgements is None:
        state = AttendedSafetyEvaluationState.SAFETY_INPUT_INCOMPLETE
        reasons.append("OPERATOR_ACK_REQUIRED")
        decision = None
        context = None
    else:
        evidence = CommissioningGovernanceEvidence(
            commissioning_mode=CommissioningMode.ATTENDED,
            operator_attended=inputs.operator_attended,
            operator_acknowledgements=inputs.operator_acknowledgements,
            source_hash_verified=inputs.source_hash_verified,
            release_sha=inputs.release_sha,
            implementation_digest=live_preflight.implementation_digest,
            account_fingerprint_matched=True,
            account_fingerprint_reference=live_preflight.account_fingerprint_reference,
            suspension_state="CLEAR",
        )
        context = ExecutionSafetyContext(
            context_version="1", decision_time=inputs.decision_time,
            run_id=live_preflight.live_preflight_id,
            action=ExecutionAction.ENTRY_PLACE, symbol="AAPL", side="buy",
            order_type="market", quantity="1", stop_price=None, order_id=None,
            governance_evidence=evidence,
        )
        decision = evaluate_execution_safety(context)
        if not _decision_is_authentic(decision):
            reasons.append("SAFETY_DECISION_DIGEST_MISMATCH")
            state = AttendedSafetyEvaluationState.SOURCE_INVALID
        elif decision.outcome is ExecutionSafetyOutcome.ALLOW:
            state = AttendedSafetyEvaluationState.SAFETY_EVALUATED_ALLOW
        else:
            state = AttendedSafetyEvaluationState.SAFETY_EVALUATED_DENY
            reasons.extend(decision.reason_codes)

    action_identity = None if context is None else context.action_identity()
    preliminary = AttendedSafetyEvaluationResult(
        evaluation_id="0" * 64, evaluation_digest="0" * 64,
        source_live_preflight_id=live_preflight.live_preflight_id,
        source_preflight_id=live_preflight.source_preflight_id,
        source_review_id=live_preflight.source_review_id,
        source_dry_run_id=live_preflight.source_dry_run_id,
        source_shadow_decision_id=live_preflight.source_shadow_decision_id,
        safety_context_id=action_identity, safety_context_digest=action_identity,
        safety_decision_id=None if decision is None else decision.decision_id,
        safety_decision_digest=None if decision is None else decision.decision_digest,
        action_identity=action_identity, symbol=live_preflight.symbol,
        trading_date=live_preflight.trading_date, implementation_digest=live_preflight.implementation_digest,
        strategy_version=live_preflight.strategy_version, market_data_digest=live_preflight.market_data_digest,
        opening_range_identity=live_preflight.opening_range_identity, quantity="1",
        account_fingerprint_status=live_preflight.account_fingerprint_observation_status,
        suspension_status=live_preflight.suspension_observation_status,
        suspension_decision_identity=live_preflight.suspension_observation_id,
        candidate_freshness_status=live_preflight.candidate_freshness_status,
        operator_acknowledgements_status=acknowledgement_status,
        required_operator_acknowledgements=_REQUIRED_ACKNOWLEDGEMENTS,
        safety_result=("INCOMPLETE" if decision is None else decision.outcome.value),
        source_hash_status=("VERIFIED" if inputs.source_hash_verified and bool(inputs.release_sha) else "REQUIRED"),
        release_sha=inputs.release_sha,
        blocking_reasons=_dedupe(reasons), evaluation_state=state,
        protection_grounded=False, execution_authorized=False,
        broker_write_count=0, authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_attended_safety_evaluation_digest(preliminary)
    return AttendedSafetyEvaluationResult(**{**preliminary.__dict__, "evaluation_id": digest, "evaluation_digest": digest})


def evaluate_protective_stop_safety(inputs: ProtectiveStopSafetyInputs) -> ProtectiveStopSafetyEvaluation:
    """Evaluate one explicit stop action through the existing M14 gates only."""
    if not isinstance(inputs, ProtectiveStopSafetyInputs):
        raise TypeError("inputs must be ProtectiveStopSafetyInputs")
    incomplete: list[str] = []
    if inputs.operator_acknowledgements is None:
        incomplete.append("OPERATOR_ACK_REQUIRED")
    if not all((inputs.run_id, inputs.source_shadow_decision_id, inputs.source_handoff_id,
                inputs.entry_action_identity, inputs.entry_order_identity,
                inputs.broker_snapshot_identity, inputs.account_fingerprint_reference)):
        incomplete.append("SOURCE_IDENTITY_INCOMPLETE")
    if inputs.authoritative_filled_quantity != "1" or inputs.protective_quantity != "1":
        incomplete.append("ONE_SHARE_PROTECTION_INCOMPLETE")
    if inputs.suspension_state != "CLEAR":
        incomplete.append("SUSPENSION_NOT_CLEAR")
    if incomplete:
        return ProtectiveStopSafetyEvaluation(
            inputs.source_handoff_id, inputs.source_shadow_decision_id, None, None, None,
            ProtectiveStopSafetyState.INCOMPLETE, tuple(dict.fromkeys(incomplete)), inputs.decision_time,
        )
    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=inputs.operator_attended,
        operator_acknowledgements=inputs.operator_acknowledgements,
        source_hash_verified=inputs.source_hash_verified,
        release_sha=inputs.release_sha,
        implementation_digest=inputs.implementation_digest,
        account_fingerprint_matched=True,
        account_fingerprint_reference=inputs.account_fingerprint_reference,
        suspension_state=inputs.suspension_state,
    )
    context = ExecutionSafetyContext(
        context_version="1", decision_time=inputs.decision_time, run_id=inputs.run_id,
        action=ExecutionAction.STOP_PLACE, symbol=inputs.symbol, side="sell",
        order_type="stop_market", quantity=inputs.protective_quantity,
        stop_price=inputs.stop_price, order_id=None, governance_evidence=evidence,
    )
    decision = evaluate_execution_safety(context)
    outcome = ProtectiveStopSafetyState.ALLOW if decision.outcome is ExecutionSafetyOutcome.ALLOW else ProtectiveStopSafetyState.DENY
    return ProtectiveStopSafetyEvaluation(
        inputs.source_handoff_id, inputs.source_shadow_decision_id,
        context.action_identity(), decision.decision_id, context.action_identity(),
        outcome, decision.reason_codes, decision.evaluated_at,
    )

def evaluate_attended_exit_safety(inputs: AttendedExitSafetyInputs) -> AttendedExitSafetyEvaluation:
    """Evaluate one explicit attended exit after stop cancellation is proven."""
    if not isinstance(inputs, AttendedExitSafetyInputs):
        raise TypeError("inputs must be AttendedExitSafetyInputs")
    incomplete: list[str] = []
    if inputs.operator_acknowledgements is None:
        incomplete.append("OPERATOR_ACK_REQUIRED")
    if not all((inputs.run_id, inputs.source_shadow_decision_id, inputs.source_handoff_id,
                inputs.protective_action_identity, inputs.protective_order_identity,
                inputs.broker_snapshot_identity, inputs.account_fingerprint_reference)):
        incomplete.append("SOURCE_IDENTITY_INCOMPLETE")
    if inputs.authoritative_position_quantity != "1" or inputs.exit_quantity != "1":
        incomplete.append("ONE_SHARE_EXIT_INCOMPLETE")
    if inputs.exit_reason not in {"profit_target", "time_exit"}:
        incomplete.append("EXIT_REASON_UNSUPPORTED")
    if not inputs.stop_cancellation_confirmed:
        incomplete.append("STOP_CANCELLATION_NOT_CONFIRMED")
    if inputs.suspension_state != "CLEAR":
        incomplete.append("SUSPENSION_NOT_CLEAR")
    if incomplete:
        return AttendedExitSafetyEvaluation(
            inputs.source_handoff_id, inputs.source_shadow_decision_id, None, None, None,
            AttendedExitSafetyState.INCOMPLETE, tuple(dict.fromkeys(incomplete)), inputs.decision_time,
        )
    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=inputs.operator_attended,
        operator_acknowledgements=inputs.operator_acknowledgements,
        source_hash_verified=inputs.source_hash_verified,
        release_sha=inputs.release_sha,
        implementation_digest=inputs.implementation_digest,
        account_fingerprint_matched=True,
        account_fingerprint_reference=inputs.account_fingerprint_reference,
        suspension_state=inputs.suspension_state,
    )
    context = ExecutionSafetyContext(
        context_version="1", decision_time=inputs.decision_time, run_id=inputs.run_id,
        action=ExecutionAction.EXIT_PLACE, symbol=inputs.symbol, side="sell",
        order_type="market", quantity=inputs.exit_quantity,
        stop_price=None, order_id=None, governance_evidence=evidence,
    )
    decision = evaluate_execution_safety(context)
    outcome = AttendedExitSafetyState.ALLOW if decision.outcome is ExecutionSafetyOutcome.ALLOW else AttendedExitSafetyState.DENY
    return AttendedExitSafetyEvaluation(
        inputs.source_handoff_id, inputs.source_shadow_decision_id,
        context.action_identity(), decision.decision_id, context.action_identity(),
        outcome, decision.reason_codes, decision.evaluated_at,
    )


def evaluate_unattended_pilot_safety(
    *, policy: Any, decision_time: datetime, run_id: str, role: str,
    symbol: str, stop_price: str | None, order_id: str | None,
    attempt_id: str, broker_snapshot_identity: str,
    snapshot_fresh: bool, suspension_fresh: bool,
    no_conflicting_broker_state: bool,
    kill_state_clear: bool, risk_bounds_verified: bool,
    risk_state_allows_new_risk: bool, health_allows_new_risk: bool,
    source_hash_verified: bool, account_fingerprint_matched: bool,
    suspension_state: str,
) -> ExecutionSafetyDecision:
    """Evaluate one unattended action through the existing M14 engine."""
    if role not in ACTION_FOR_ROLE:
        raise ValueError("unattended action role is invalid")
    side, order_type = {
        "entry": ("buy", "market"), "stop": ("sell", "stop_market"),
        "exit": ("sell", "market"), "cancel": ("", ""),
    }[role]
    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.UNATTENDED_PILOT,
        operator_attended=False,
        operator_acknowledgements=OperatorAcknowledgements(False, False, False, False),
        source_hash_verified=source_hash_verified,
        release_sha=getattr(policy, "release_sha", ""),
        implementation_digest=getattr(policy, "implementation_digest", ""),
        account_fingerprint_matched=account_fingerprint_matched,
        account_fingerprint_reference=getattr(policy, "account_fingerprint_reference", ""),
        suspension_state=suspension_state, unattended_policy=policy,
        unattended_attempt_id=attempt_id,
        unattended_broker_snapshot_identity=broker_snapshot_identity,
        unattended_snapshot_fresh=snapshot_fresh,
        unattended_suspension_fresh=suspension_fresh,
        unattended_no_conflicting_broker_state=no_conflicting_broker_state,
        unattended_kill_state_clear=kill_state_clear,
        unattended_risk_bounds_verified=risk_bounds_verified,
        unattended_risk_state_allows_new_risk=risk_state_allows_new_risk,
        unattended_health_allows_new_risk=health_allows_new_risk,
    )
    context = ExecutionSafetyContext(
        context_version="bounded-unattended-pilot-context-v1",
        policy_version=UNATTENDED_POLICY_VERSION, decision_time=decision_time,
        run_id=run_id, action=ACTION_FOR_ROLE[role], symbol=symbol, side=side,
        order_type=order_type, quantity="1", stop_price=stop_price,
        order_id=order_id, governance_evidence=evidence,
    )
    return evaluate_execution_safety(context, gates=UNATTENDED_PILOT_GATES)

__all__ = [
    "ATTENDED_SAFETY_EVALUATION_POLICY_VERSION", "PROTECTIVE_STOP_MUTATION_BOUNDARY", "AttendedSafetyEvaluationResult",
    "AttendedSafetyEvaluationState", "AttendedSafetyInputs",
    "ProtectiveStopSafetyEvaluation", "ProtectiveStopSafetyInputs", "ProtectiveStopSafetyState",
    "AttendedExitSafetyEvaluation", "AttendedExitSafetyInputs", "AttendedExitSafetyState",
    "build_attended_safety_evaluation_result", "compute_attended_safety_evaluation_digest",
    "evaluate_protective_stop_safety", "evaluate_attended_exit_safety", "evaluate_unattended_pilot_safety",
]
