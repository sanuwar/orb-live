"""Read-only protective-stop reconciliation and exit rehearsal.

This module consumes already-observed broker facts. It has no broker client,
transport, adapter, or write capability.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.attended_safety_evaluation_bridge import (
    PROTECTIVE_STOP_MUTATION_BOUNDARY,
    AttendedExitSafetyInputs,
    AttendedExitSafetyState,
    evaluate_attended_exit_safety,
)
from src.broker_action_plan import BrokerActionKind
from src.broker_snapshot import BrokerSnapshot, validate_snapshot
from src.config import load_config
from src.exit_engine import ExitAction, ExitEngine, PositionState
from src.live_broker_capabilities import assess_absolute_protection_invariant
from src.order_lifecycle_model import assess_protection, normalize_label
from src.order_observer import ObservedEquityOrder
from src.order_state import OrderPhase, interpret_equity_order
from src.post_entry_protection_rehearsal import (
    PostEntryProtectionRehearsal,
    ProtectionRehearsalState,
    compute_post_entry_protection_rehearsal_digest,
)
from src.protection_observation_model import canonical_bytes
from src.protective_stop_lifecycle import (
    ProtectiveStopAction,
    decide_protective_stop_lifecycle,
    protective_stop_observation_key,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.trade_state import TradeStatus

POLICY_VERSION = "protective-stop-exit-rehearsal-v1"
_DOMAIN = b"orb-live/protective-stop-exit-rehearsal/v1"
MAX_OBSERVATION_AGE = timedelta(seconds=30)
MAX_SAFETY_AGE = timedelta(seconds=30)
AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"

SAFE_EXIT_SEQUENCE = tuple(
    item.value
    for item in (
        BrokerActionKind.CANCEL_PROTECTIVE_STOP,
        BrokerActionKind.CONFIRM_STOP_CANCELLED,
        BrokerActionKind.REVIEW_EXIT,
        BrokerActionKind.PLACE_EXIT,
    )
)


class StopExitRehearsalState(str, Enum):
    OPEN_PROTECTED = "OPEN_PROTECTED"
    STOP_FILLED_EXIT_RECONCILED = "STOP_FILLED_EXIT_RECONCILED"
    EXIT_REHEARSAL_READY = "EXIT_REHEARSAL_READY"
    STOP_NOT_FOUND = "STOP_NOT_FOUND"
    STOP_PENDING = "STOP_PENDING"
    STOP_ACKNOWLEDGED = "STOP_ACKNOWLEDGED"
    STOP_WORKING = "STOP_WORKING"
    STOP_PARTIALLY_FILLED = "STOP_PARTIALLY_FILLED"
    STOP_CANCELLED = "STOP_CANCELLED"
    STOP_REJECTED = "STOP_REJECTED"
    STOP_REPLACED = "STOP_REPLACED"
    STOP_IDENTITY_MISMATCH = "STOP_IDENTITY_MISMATCH"
    STOP_QUANTITY_MISMATCH = "STOP_QUANTITY_MISMATCH"
    STOP_PRICE_MISMATCH = "STOP_PRICE_MISMATCH"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    UNPROTECTED_POSITION = "UNPROTECTED_POSITION"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    POSITION_ALREADY_CLOSED = "POSITION_ALREADY_CLOSED"
    SAFETY_DENY = "SAFETY_DENY"
    SAFETY_INCOMPLETE = "SAFETY_INCOMPLETE"
    SAFETY_STALE = "SAFETY_STALE"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    UNKNOWN_EXIT_STATE = "UNKNOWN_EXIT_STATE"


@dataclass(frozen=True)
class ProtectiveStopExitRehearsal:
    rehearsal_id: str
    rehearsal_digest: str
    source_protection_rehearsal_id: str
    source_handoff_id: str
    source_shadow_decision_id: str
    entry_action_identity: str
    entry_order_identity: str
    protective_action_identity: str
    protective_order_identity: str | None
    stop_observation_identity: str | None
    broker_snapshot_identity: str
    symbol: str
    authoritative_position_quantity: str
    protective_quantity: str
    protective_filled_quantity: str
    remaining_position_quantity: str
    expected_stop_price: str
    observed_stop_price: str | None
    stop_lifecycle_state: str
    stop_reconciliation_state: str
    m13_protection_reason: str
    exit_intent: str
    exit_reason: str | None
    exit_action_identity: str | None
    exit_safety_result: str
    exit_safety_evaluated_at: datetime | None
    safe_exit_sequence: tuple[str, ...]
    trade_lifecycle_id: str
    entry_fill_price: str | None
    entry_fill_time: datetime | None
    exit_fill_price: str | None
    exit_fill_time: datetime | None
    authoritative_fees: str | None
    realized_pnl: str | None
    realized_pnl_source: str | None
    release_sha: str
    implementation_digest: str
    account_fingerprint_reference: str
    blocking_reasons: tuple[str, ...]
    rehearsal_state: StopExitRehearsalState
    mutation_boundary: str
    broker_write_count: int
    execution_authorized: bool
    protection_grounded: bool
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.rehearsal_id != self.rehearsal_digest:
            raise ValueError("rehearsal identity must equal digest")
        if self.broker_write_count != 0 or self.execution_authorized:
            raise ValueError("exit rehearsal cannot write or authorize")
        if self.protection_grounded:
            raise ValueError("M13 evidence cannot promote protection grounding")
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ValueError("authority ceiling drift")


def _fields(value: ProtectiveStopExitRehearsal) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in value.__dataclass_fields__:
        if name in {"rehearsal_id", "rehearsal_digest"}:
            continue
        item = getattr(value, name)
        result[name] = item.isoformat() if isinstance(item, datetime) else (
            item.value if isinstance(item, Enum) else item
        )
    return result


def compute_protective_stop_exit_rehearsal_digest(
    value: ProtectiveStopExitRehearsal,
) -> str:
    if not isinstance(value, ProtectiveStopExitRehearsal):
        raise TypeError("value must be a ProtectiveStopExitRehearsal")
    return hashlib.sha256(
        _DOMAIN + b"\0rehearsal\0" + canonical_bytes(_fields(value))
    ).hexdigest()


def _timestamp(observation: ObservedEquityOrder) -> datetime | None:
    values = [
        item.timestamp
        for item in observation.executions
        if item.timestamp is not None
    ]
    return max(values) if values else observation.last_transaction_at


def _position_quantity(
    snapshot: BrokerSnapshot,
    symbol: str,
) -> tuple[Decimal, bool]:
    rows = [
        item
        for item in snapshot.account_state.positions
        if item.symbol.upper() == symbol.upper()
    ]
    if len(rows) > 1:
        return Decimal("0"), False
    return (Decimal("0") if not rows else rows[0].quantity), True


def build_protective_stop_exit_rehearsal(
    source: PostEntryProtectionRehearsal,
    *,
    snapshot: BrokerSnapshot,
    protective_stop_order_id: str,
    stop_observation: ObservedEquityOrder | None,
    stop_observed_at: datetime,
    entry_observation: ObservedEquityOrder | None,
    evaluated_at: datetime,
    release_sha: str,
    exit_position: PositionState | None = None,
    exit_stop_cancellation_confirmed: bool = False,
    suspension_decision: Any = None,
    governance_evidence: Any = None,
    safety_decision_time: datetime | None = None,
    trusted_implementation_digest: str | None = None,
    root: Path | None = None,
) -> ProtectiveStopExitRehearsal:
    """Reconcile broker facts and rehearse exits without invoking a write path."""
    trusted = trusted_implementation_digest or checkpoint_implementation_digest(
        root or Path(__file__).resolve().parents[1]
    )
    reasons: list[str] = []
    validation = validate_snapshot(snapshot, evaluated_at=evaluated_at)
    if not validation.valid:
        reasons.append(
            "SNAPSHOT_STALE"
            if validation.result_code == "stale_snapshot"
            else "SOURCE_MISMATCH"
        )
    if (
        compute_post_entry_protection_rehearsal_digest(source)
        != source.rehearsal_digest
        or source.rehearsal_state
        is not ProtectionRehearsalState.PROTECTION_REHEARSAL_READY
        or source.implementation_digest != trusted
        or source.release_sha != release_sha
    ):
        reasons.append("SOURCE_MISMATCH")
    if (
        snapshot.metadata.account_fingerprint
        != source.account_fingerprint_reference
    ):
        reasons.append("ACCOUNT_MISMATCH")

    position, unique_position = _position_quantity(snapshot, source.symbol)
    if not unique_position or position < 0 or position > Decimal("1"):
        reasons.append("POSITION_MISMATCH")

    order = None if stop_observation is None else stop_observation.order
    observed_stop = (
        None
        if order is None or order.stop_price is None
        else format(order.stop_price, "f")
    )
    expected_stop = source.stop_price
    status = None if order is None else interpret_equity_order(order)
    lifecycle = normalize_label(None if order is None else order.state)
    grounding = assess_protection(lifecycle)
    stop_key = (
        None
        if stop_observation is None
        else protective_stop_observation_key(
            order, stop_observation.average_price
        )
    )
    stop_state = "STOP_NOT_FOUND"
    state = StopExitRehearsalState.STOP_NOT_FOUND

    if (
        stop_observed_at.tzinfo is None
        or stop_observed_at > evaluated_at
        or evaluated_at - stop_observed_at > MAX_OBSERVATION_AGE
    ):
        reasons.append("SNAPSHOT_STALE")
        state = StopExitRehearsalState.SNAPSHOT_STALE
    elif order is None:
        state = (
            StopExitRehearsalState.UNPROTECTED_POSITION
            if position > 0
            else StopExitRehearsalState.STOP_NOT_FOUND
        )
        stop_state = state.value
        reasons.append(state.value)
    else:
        if order.order_id != protective_stop_order_id or not protective_stop_order_id:
            reasons.append("STOP_IDENTITY_MISMATCH")
            state = StopExitRehearsalState.STOP_IDENTITY_MISMATCH
        elif (
            order.symbol.upper() != source.symbol.upper()
            or order.side.lower() != "sell"
            or order.trigger.lower() != "stop"
            or order.order_type.lower() != "stop_market"
            or order.placed_agent.lower() != "agentic"
        ):
            reasons.append("STOP_IDENTITY_MISMATCH")
            state = StopExitRehearsalState.STOP_IDENTITY_MISMATCH
        elif (
            status is None
            or status.requested_quantity != Decimal("1")
            or status.filled_quantity > Decimal("1")
        ):
            reasons.append("STOP_QUANTITY_MISMATCH")
            state = StopExitRehearsalState.STOP_QUANTITY_MISMATCH
        elif (
            order.stop_price is None
            or format(order.stop_price, "f") != expected_stop
        ):
            reasons.append("STOP_PRICE_MISMATCH")
            state = StopExitRehearsalState.STOP_PRICE_MISMATCH
        else:
            trade = SimpleNamespace(
                status=TradeStatus.OPEN_PROTECTED,
                protective_stop_order_id=protective_stop_order_id,
                symbol=source.symbol,
                remaining_broker_position_quantity=None,
                filled_quantity=1,
            )
            lifecycle_decision = decide_protective_stop_lifecycle(
                trade=trade,
                order=order,
                average_fill_price=stop_observation.average_price,
            )
            if (
                status.phase is OrderPhase.PENDING
                and status.filled_quantity == 0
            ):
                stop_state = {
                    "new": "STOP_PENDING",
                    "unconfirmed": "STOP_PENDING",
                    "queued": "STOP_ACKNOWLEDGED",
                    "locating": "STOP_ACKNOWLEDGED",
                }.get(status.normalized_state, "STOP_WORKING")
                state = StopExitRehearsalState.OPEN_PROTECTED
                if position != Decimal("1"):
                    state = StopExitRehearsalState.POSITION_MISMATCH
                    reasons.append("POSITION_MISMATCH")
            elif status.phase is OrderPhase.PARTIALLY_FILLED:
                stop_state = "STOP_PARTIALLY_FILLED"
                state = StopExitRehearsalState.STOP_PARTIALLY_FILLED
                if position != status.remaining_quantity:
                    reasons.append("POSITION_MISMATCH")
            elif (
                status.phase is OrderPhase.FILLED
                and lifecycle_decision.action
                is ProtectiveStopAction.RECONCILE_FULL_STOP_FILL
            ):
                stop_state = "STOP_FILLED"
                if position != 0:
                    state = StopExitRehearsalState.POSITION_MISMATCH
                    reasons.append("POSITION_MISMATCH")
                else:
                    state = (
                        StopExitRehearsalState
                        .STOP_FILLED_EXIT_RECONCILED
                    )
            elif status.phase is OrderPhase.CANCELLED:
                stop_state = "STOP_CANCELLED"
                state = StopExitRehearsalState.STOP_CANCELLED
                if position > 0 and not exit_stop_cancellation_confirmed:
                    reasons.append("UNPROTECTED_POSITION")
            elif status.phase is OrderPhase.REJECTED:
                stop_state = "STOP_REJECTED"
                state = StopExitRehearsalState.STOP_REJECTED
                if position > 0:
                    reasons.append("UNPROTECTED_POSITION")
            else:
                stop_state = "UNKNOWN_STOP_STATE"
                state = StopExitRehearsalState.UNKNOWN_EXIT_STATE
                reasons.append("UNKNOWN_STOP_STATE")

    exit_intent = "NONE"
    exit_reason = None
    exit_action_identity = None
    exit_safety_result = "NOT_EVALUATED"
    exit_safety_at = None
    if exit_position is not None:
        try:
            if exit_position.symbol.upper() != source.symbol.upper():
                raise ValueError("symbol mismatch")
            exit_decision = ExitEngine(load_config()).evaluate(exit_position)
            exit_intent = exit_decision.action.value
            exit_reason = exit_decision.reason
        except (ValueError, InvalidOperation, KeyError):
            reasons.append("SOURCE_MISMATCH")
            state = StopExitRehearsalState.SOURCE_MISMATCH
        else:
            if exit_decision.action is ExitAction.MOVE_STOP_TO_BREAKEVEN:
                reasons.append("STOP_REPLACEMENT_GAP")
            elif exit_decision.action in {
                ExitAction.EXIT_PROFIT_TARGET,
                ExitAction.EXIT_TIME,
            }:
                if (
                    stop_state != "STOP_CANCELLED"
                    or not exit_stop_cancellation_confirmed
                ):
                    reasons.extend(
                        assess_absolute_protection_invariant().reason_codes
                    )
                else:
                    decision_time = safety_decision_time or evaluated_at
                    suspension_state = getattr(
                        getattr(suspension_decision, "state", None),
                        "value",
                        getattr(suspension_decision, "state", ""),
                    )
                    bridge = evaluate_attended_exit_safety(
                        AttendedExitSafetyInputs(
                            decision_time=decision_time,
                            run_id=hashlib.sha256(
                                _DOMAIN
                                + b"\0exit-action\0"
                                + canonical_bytes(
                                    {
                                        "source": source.rehearsal_id,
                                        "stop": protective_stop_order_id,
                                        "snapshot": validation.snapshot_digest,
                                        "reason": exit_decision.action.value,
                                    }
                                )
                            ).hexdigest(),
                            source_shadow_decision_id=(
                                source.source_shadow_decision_id
                            ),
                            source_handoff_id=source.source_handoff_id,
                            protective_action_identity=(
                                source.protective_action_identity or ""
                            ),
                            protective_order_identity=(
                                protective_stop_order_id
                            ),
                            broker_snapshot_identity=(
                                validation.snapshot_digest
                            ),
                            symbol=source.symbol,
                            authoritative_position_quantity=format(
                                position, "f"
                            ),
                            exit_quantity="1",
                            exit_reason=(
                                "profit_target"
                                if exit_decision.action
                                is ExitAction.EXIT_PROFIT_TARGET
                                else "time_exit"
                            ),
                            stop_cancellation_confirmed=True,
                            implementation_digest=trusted,
                            release_sha=release_sha,
                            account_fingerprint_reference=(
                                source.account_fingerprint_reference
                            ),
                            suspension_state=str(suspension_state),
                            operator_acknowledgements=(
                                None
                                if governance_evidence is None
                                else governance_evidence
                                .operator_acknowledgements
                            ),
                            operator_attended=(
                                False
                                if governance_evidence is None
                                else governance_evidence.operator_attended
                            ),
                            source_hash_verified=(
                                False
                                if governance_evidence is None
                                else governance_evidence
                                .source_hash_verified
                            ),
                        )
                    )
                    exit_action_identity = bridge.exit_action_identity
                    exit_safety_result = bridge.safety_result.value
                    exit_safety_at = bridge.evaluated_at
                    if (
                        evaluated_at < bridge.evaluated_at
                        or evaluated_at - bridge.evaluated_at
                        > MAX_SAFETY_AGE
                    ):
                        state = StopExitRehearsalState.SAFETY_STALE
                        reasons.append("SAFETY_STALE")
                    elif (
                        bridge.safety_result
                        is AttendedExitSafetyState.ALLOW
                    ):
                        state = (
                            StopExitRehearsalState
                            .EXIT_REHEARSAL_READY
                        )
                    elif (
                        bridge.safety_result
                        is AttendedExitSafetyState.DENY
                    ):
                        state = StopExitRehearsalState.SAFETY_DENY
                        reasons.extend(bridge.reason_codes)
                    else:
                        state = (
                            StopExitRehearsalState.SAFETY_INCOMPLETE
                        )
                        reasons.extend(bridge.reason_codes)

    foundational_state = next(
        (
            mapped
            for reason, mapped in (
                ("SNAPSHOT_STALE", StopExitRehearsalState.SNAPSHOT_STALE),
                ("SOURCE_MISMATCH", StopExitRehearsalState.SOURCE_MISMATCH),
                ("ACCOUNT_MISMATCH", StopExitRehearsalState.ACCOUNT_MISMATCH),
            )
            if reason in reasons
        ),
        None,
    )
    if foundational_state is not None:
        state = foundational_state

    entry_price = entry_time = exit_price = exit_time = None
    fees = pnl = pnl_source = None
    if (
        state
        is StopExitRehearsalState.STOP_FILLED_EXIT_RECONCILED
    ):
        valid_entry = (
            entry_observation is not None
            and entry_observation.order.order_id == source.entry_order_id
            and entry_observation.order.symbol.upper()
            == source.symbol.upper()
            and entry_observation.order.side.lower() == "buy"
            and interpret_equity_order(
                entry_observation.order
            ).fully_filled
            and entry_observation.average_price is not None
        )
        if (
            not valid_entry
            or stop_observation is None
            or stop_observation.average_price is None
        ):
            state = StopExitRehearsalState.UNKNOWN_EXIT_STATE
            reasons.append("AUTHORITATIVE_FILL_DETAILS_INCOMPLETE")
        else:
            entry_price = format(
                entry_observation.average_price, "f"
            )
            exit_price = format(
                stop_observation.average_price, "f"
            )
            entry_time = _timestamp(entry_observation)
            exit_time = _timestamp(stop_observation)
            fee_value = (
                entry_observation.fees + stop_observation.fees
            )
            pnl_value = (
                stop_observation.average_price
                - entry_observation.average_price
                - fee_value
            )
            fees = format(fee_value, "f")
            pnl = format(pnl_value, "f")
            pnl_source = (
                "BROKER_AUTHORITATIVE_FILLS_LESS_REPORTED_FEES"
            )

    preliminary = ProtectiveStopExitRehearsal(
        rehearsal_id="0" * 64,
        rehearsal_digest="0" * 64,
        source_protection_rehearsal_id=source.rehearsal_id,
        source_handoff_id=source.source_handoff_id,
        source_shadow_decision_id=(
            source.source_shadow_decision_id
        ),
        entry_action_identity=source.entry_action_identity,
        entry_order_identity=source.entry_order_id or "",
        protective_action_identity=(
            source.protective_action_identity or ""
        ),
        protective_order_identity=(
            None if order is None else order.order_id
        ),
        stop_observation_identity=stop_key,
        broker_snapshot_identity=validation.snapshot_digest,
        symbol=source.symbol,
        authoritative_position_quantity=format(position, "f"),
        protective_quantity=(
            "0"
            if status is None
            else format(status.requested_quantity, "f")
        ),
        protective_filled_quantity=(
            "0"
            if status is None
            else format(status.filled_quantity, "f")
        ),
        remaining_position_quantity=format(position, "f"),
        expected_stop_price=expected_stop,
        observed_stop_price=observed_stop,
        stop_lifecycle_state=lifecycle.value,
        stop_reconciliation_state=stop_state,
        m13_protection_reason=grounding.reason.value,
        exit_intent=exit_intent,
        exit_reason=exit_reason,
        exit_action_identity=exit_action_identity,
        exit_safety_result=exit_safety_result,
        exit_safety_evaluated_at=exit_safety_at,
        safe_exit_sequence=SAFE_EXIT_SEQUENCE,
        trade_lifecycle_id=hashlib.sha256(
            _DOMAIN
            + b"\0trade\0"
            + canonical_bytes(
                {
                    "candidate": source.source_shadow_decision_id,
                    "entry": source.entry_order_id or "",
                    "stop": protective_stop_order_id,
                }
            )
        ).hexdigest(),
        entry_fill_price=entry_price,
        entry_fill_time=entry_time,
        exit_fill_price=exit_price,
        exit_fill_time=exit_time,
        authoritative_fees=fees,
        realized_pnl=pnl,
        realized_pnl_source=pnl_source,
        release_sha=release_sha,
        implementation_digest=trusted,
        account_fingerprint_reference=(
            source.account_fingerprint_reference
        ),
        blocking_reasons=tuple(dict.fromkeys(reasons)),
        rehearsal_state=state,
        mutation_boundary=PROTECTIVE_STOP_MUTATION_BOUNDARY,
        broker_write_count=0,
        execution_authorized=False,
        protection_grounded=False,
        authority_ceiling=AUTHORITY_CEILING,
    )
    digest = compute_protective_stop_exit_rehearsal_digest(
        preliminary
    )
    return ProtectiveStopExitRehearsal(
        **{
            **preliminary.__dict__,
            "rehearsal_id": digest,
            "rehearsal_digest": digest,
        }
    )


__all__ = [
    "POLICY_VERSION",
    "SAFE_EXIT_SEQUENCE",
    "ProtectiveStopExitRehearsal",
    "StopExitRehearsalState",
    "build_protective_stop_exit_rehearsal",
    "compute_protective_stop_exit_rehearsal_digest",
]