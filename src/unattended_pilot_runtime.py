"""Disabled-by-default production wiring for the M21 bounded state machine.

This module owns no strategy, safety evaluator, broker parser, or dispatch
implementation.  It composes the M21 policy/state, approved M14 safety bridge,
durable M22 blocker store, and existing commissioning adapter.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from src.attended_safety_evaluation_bridge import evaluate_unattended_pilot_safety
from src.bounded_unattended_orchestrator import (
    PolicyEvidenceOrigin, UnattendedPilotPolicy, UnattendedState,
)
from src.broker_snapshot import BrokerSnapshot, validate_snapshot
from src.commissioning_suspension_identity import CommissioningSuspensionIdentity
from src.live_attended_preflight_inputs import (
    DEFAULT_MAXIMUM_SUSPENSION_OBSERVATION_AGE, SuspensionObservation,
    validate_suspension_observation,
)
from src.execution_safety_governance import ExecutionSafetyDecision, ExecutionSafetyOutcome
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError, CommissioningError, ExecutionSafetyDeniedError,
    RobinhoodCommissioningAdapter,
)
from src.unattended_pilot_state import (
    AttemptOutcome, DailyAttemptRecord, UnattendedPilotStateError, UnattendedPilotStateStore,
    UnattendedWritePermit,
)


class UnattendedPilotRuntimeError(RuntimeError):
    pass


class ProductionWriteState(str, Enum):
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WRITE_OUTCOME_UNKNOWN = "WRITE_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class ProductionWriteResult:
    permit_id: str
    role: str
    state: ProductionWriteState
    kill_state_entered: bool
    broker_write_count: int
    reason_code: str


@dataclass(frozen=True)
class ProductionSafetyInputs:
    broker_snapshot: BrokerSnapshot
    suspension_observation: SuspensionObservation
    kill_state_clear: bool
    risk_bounds_verified: bool
    risk_state_allows_new_risk: bool
    health_allows_new_risk: bool
    source_hash_verified: bool


def policy_is_production_active(policy: Any) -> bool:
    return bool(
        isinstance(policy, UnattendedPilotPolicy)
        and policy.evidence_origin is PolicyEvidenceOrigin.REVIEWED_PRODUCTION
        and policy.enabled is True
        and policy.activation_approved is True
        and policy.permits_automated_action is True
    )


def _validated_read_only_inputs(
    *, policy: UnattendedPilotPolicy, inputs: ProductionSafetyInputs,
    now: datetime, role: str, symbol: str, order_id: str | None,
) -> tuple[str, str | None, bool, bool, bool, bool]:
    validation = validate_snapshot(inputs.broker_snapshot, evaluated_at=now)
    account_matches = (
        validation.valid
        and inputs.broker_snapshot.metadata.account_fingerprint
        == policy.account_fingerprint_reference
    )
    expected_controller = CommissioningSuspensionIdentity.derive(
        release_sha=policy.release_sha,
        account_fingerprint_reference=policy.account_fingerprint_reference,
    ).controller_id
    observation = inputs.suspension_observation
    suspension_status, suspension_identity = validate_suspension_observation(
        observation, evaluated_at=now,
        maximum_age=DEFAULT_MAXIMUM_SUSPENSION_OBSERVATION_AGE,
    )
    suspension_fresh_clear = (
        observation.controller_id == expected_controller
        and suspension_status == "CLEAR"
        and suspension_identity is not None
    )
    positions = tuple(inputs.broker_snapshot.account_state.positions)
    orders = tuple(inputs.broker_snapshot.account_state.open_orders)
    entry_orders = tuple(
        item for item in orders if item.side == "buy" and item.trigger != "stop"
    )
    stop_orders = tuple(
        item for item in orders if item.side == "sell" and item.trigger == "stop"
    )
    position_matches = (
        len(positions) == 1 and positions[0].symbol == symbol
        and positions[0].quantity == Decimal("1")
    )
    if role == "entry":
        broker_state_ok = not positions and not orders
    elif role == "stop":
        broker_state_ok = position_matches and not orders
    elif role == "cancel":
        broker_state_ok = (
            position_matches and len(stop_orders) == 1 and len(orders) == 1
            and stop_orders[0].symbol == symbol
            and stop_orders[0].quantity == Decimal("1")
            and stop_orders[0].order_id == order_id
        )
    elif role == "exit":
        broker_state_ok = position_matches and not orders
    else:
        broker_state_ok = False
    return (
        validation.snapshot_digest, suspension_identity, validation.valid,
        suspension_fresh_clear, account_matches, broker_state_ok,
    )

def evaluate_production_action_safety(
    *, policy: UnattendedPilotPolicy, attempt: DailyAttemptRecord,
    inputs: ProductionSafetyInputs, decision_time: datetime, run_id: str,
    role: str, symbol: str, stop_price: str | None = None,
    order_id: str | None = None,
) -> ExecutionSafetyDecision:
    if not policy_is_production_active(policy):
        raise UnattendedPilotRuntimeError("production unattended policy is disabled")
    if attempt.account_fingerprint_reference != policy.account_fingerprint_reference:
        raise UnattendedPilotRuntimeError("attempt account does not match policy")
    if run_id != attempt.run_id:
        raise UnattendedPilotRuntimeError("run identity does not match durable attempt")
    if attempt.release_sha != policy.release_sha or (
        attempt.implementation_digest != policy.implementation_digest
    ):
        raise UnattendedPilotRuntimeError("attempt source identity mismatch")
    (
        snapshot_identity, _, snapshot_fresh, suspension_fresh,
        account_matches, broker_state_ok,
    ) = _validated_read_only_inputs(
        policy=policy, inputs=inputs, now=decision_time, role=role,
        symbol=symbol, order_id=order_id,
    )
    return evaluate_unattended_pilot_safety(
        policy=policy, decision_time=decision_time, run_id=run_id, role=role,
        symbol=symbol, stop_price=stop_price, order_id=order_id,
        attempt_id=attempt.attempt_id,
        broker_snapshot_identity=snapshot_identity,
        snapshot_fresh=snapshot_fresh, suspension_fresh=suspension_fresh,
        no_conflicting_broker_state=broker_state_ok,
        kill_state_clear=inputs.kill_state_clear,
        risk_bounds_verified=inputs.risk_bounds_verified,
        risk_state_allows_new_risk=inputs.risk_state_allows_new_risk,
        health_allows_new_risk=inputs.health_allows_new_risk,
        source_hash_verified=inputs.source_hash_verified,
        account_fingerprint_matched=account_matches,
        suspension_state=inputs.suspension_observation.decision.state.value,
    )


def issue_production_write_permit(
    *, store: UnattendedPilotStateStore, policy: UnattendedPilotPolicy,
    attempt: DailyAttemptRecord, safety_decision: ExecutionSafetyDecision,
    role: str, arguments: Mapping[str, Any], inputs: ProductionSafetyInputs,
    now: datetime,
) -> UnattendedWritePermit:
    if safety_decision.outcome is not ExecutionSafetyOutcome.ALLOW:
        raise UnattendedPilotRuntimeError("safety decision does not allow progression")
    (
        snapshot_identity, suspension_identity, snapshot_fresh,
        suspension_fresh, account_matches, broker_state_ok,
    ) = _validated_read_only_inputs(
        policy=policy, inputs=inputs, now=now, role=role,
        symbol=str(arguments.get("symbol", safety_decision.bound_symbol)),
        order_id=arguments.get("order_id"),
    )
    if not all((snapshot_fresh, suspension_fresh, account_matches,
                broker_state_ok, suspension_identity is not None)):
        raise UnattendedPilotRuntimeError("broker state conflicts with action")
    return store.issue_write_permit(
        policy=policy, attempt=attempt, safety_decision=safety_decision,
        role=role, arguments=arguments,
        broker_snapshot_identity=snapshot_identity,
        suspension_decision_identity=suspension_identity, now=now,
    )


async def dispatch_production_write(
    *, adapter: RobinhoodCommissioningAdapter, store: UnattendedPilotStateStore,
    policy: UnattendedPilotPolicy, permit: UnattendedWritePermit,
    safety_decision: ExecutionSafetyDecision, arguments: Mapping[str, Any],
    now: datetime,
) -> ProductionWriteResult:
    """Spend durable permission before the sole existing broker write call.

    Any transport/response ambiguity enters persistent kill state and is never
    retried.  A second call with the same permit fails before adapter dispatch.
    """
    if not isinstance(adapter, RobinhoodCommissioningAdapter):
        raise UnattendedPilotRuntimeError("existing commissioning adapter required")
    if not policy_is_production_active(policy):
        raise UnattendedPilotRuntimeError("production unattended policy is disabled")
    store.mark_dispatching(permit)
    try:
        if permit.role == "cancel":
            await adapter.cancel_equity_order(
                {"order_id": permit.order_id}, safety_decision=safety_decision,
                unattended_policy=policy, unattended_write_permit=permit,
                unattended_state_store=store,
            )
        else:
            await adapter.place_equity_order(
                arguments, role=permit.role, safety_decision=safety_decision,
                unattended_policy=policy, unattended_write_permit=permit,
                unattended_state_store=store,
            )
    except AmbiguousWriteError as exc:
        store.mark_write_outcome_unknown(
            permit, reason_code=exc.category, now=now
        )
        return ProductionWriteResult(
            permit.permit_id, permit.role,
            ProductionWriteState.WRITE_OUTCOME_UNKNOWN, True, 0, exc.category,
        )
    except ExecutionSafetyDeniedError as exc:
        store.mark_known_outcome(permit, accepted=False)
        return ProductionWriteResult(
            permit.permit_id, permit.role, ProductionWriteState.REJECTED,
            False, 0, type(exc).__name__,
        )
    except CommissioningError as exc:
        # The durable permit was marked DISPATCHING before entering the adapter.
        # A response-contract failure may follow a broker acceptance; it is not
        # safe to classify that as a rejection or to retry it.
        reason = "WRITE_RESPONSE_CONTRACT_AMBIGUOUS"
        store.mark_write_outcome_unknown(permit, reason_code=reason, now=now)
        return ProductionWriteResult(
            permit.permit_id, permit.role,
            ProductionWriteState.WRITE_OUTCOME_UNKNOWN, True, 0, reason,
        )
    store.mark_known_outcome(permit, accepted=True)
    return ProductionWriteResult(
        permit.permit_id, permit.role, ProductionWriteState.ACCEPTED,
        False, 1, "OK",
    )


def reconcile_startup(
    *, store: UnattendedPilotStateStore, policy: UnattendedPilotPolicy,
    snapshot: BrokerSnapshot, trading_date: date, now: datetime,
) -> UnattendedState:
    """Authoritative broker state is inspected before durable local state."""
    validation = validate_snapshot(snapshot, evaluated_at=now)
    fingerprint = policy.account_fingerprint_reference
    if not validation.valid or snapshot.metadata.account_fingerprint != fingerprint:
        store.enter_kill_state(
            fingerprint=fingerprint, trading_date=trading_date,
            reason_code="BROKER_SNAPSHOT_INVALID_OR_ACCOUNT_MISMATCH", now=now,
        )
        return UnattendedState.KILL_STATE
    if store.active_kill(fingerprint) is not None:
        return UnattendedState.KILL_STATE
    positions = tuple(snapshot.account_state.positions)
    orders = tuple(snapshot.account_state.open_orders)
    entry_orders = tuple(order for order in orders if order.side == "buy" and order.trigger != "stop")
    stop_orders = tuple(order for order in orders if order.side == "sell" and order.trigger == "stop")
    recognized_orders = entry_orders + stop_orders
    position_shape_valid = all(
        position.symbol in policy.symbol_allowlist and position.quantity == 1
        for position in positions
    )
    order_shape_valid = all(
        order.symbol in policy.symbol_allowlist and order.quantity == 1
        for order in recognized_orders
    )
    if (
        len(recognized_orders) != len(orders)
        or not position_shape_valid
        or not order_shape_valid
        or len(positions) > 1
        or len(entry_orders) > 1
        or len(stop_orders) > 1
        or (entry_orders and (stop_orders or positions))
    ):
        store.enter_kill_state(
            fingerprint=fingerprint, trading_date=trading_date,
            reason_code="CONFLICTING_BROKER_STATE", now=now,
        )
        return UnattendedState.KILL_STATE
    if positions:
        return UnattendedState.STOP_RECONCILING if stop_orders else UnattendedState.PROTECTION_REQUIRED
    if stop_orders:
        store.enter_kill_state(
            fingerprint=fingerprint, trading_date=trading_date,
            reason_code="STOP_WITHOUT_POSITION", now=now,
        )
        return UnattendedState.KILL_STATE
    if entry_orders:
        return UnattendedState.ENTRY_RECONCILING
    attempt = store.attempt_for(fingerprint, trading_date)
    if attempt is not None:
        if attempt.outcome in {
            AttemptOutcome.DISPATCHING, AttemptOutcome.WRITE_OUTCOME_UNKNOWN,
        }:
            store.enter_kill_state(
                fingerprint=fingerprint, trading_date=trading_date,
                reason_code="BROKER_LEDGER_DISAGREEMENT", now=now,
            )
            return UnattendedState.KILL_STATE
        return UnattendedState.POLICY_BLOCKED
    return UnattendedState.WATCHING if policy_is_production_active(policy) else UnattendedState.DISABLED


__all__ = ["ProductionSafetyInputs", "ProductionWriteResult", "ProductionWriteState",
    "UnattendedPilotRuntimeError", "dispatch_production_write",
    "evaluate_production_action_safety", "issue_production_write_permit",
    "policy_is_production_active", "reconcile_startup"]
