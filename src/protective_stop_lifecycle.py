from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
from typing import Any

try:
    from .account_state import EquityOrder
    from .order_state import (
        OrderInstruction,
        OrderStatusDecision,
        interpret_equity_order,
    )
    from .trade_state import (
        StopRecoveryAction,
        StopRecoveryStep,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )
except ImportError:
    from account_state import EquityOrder
    from order_state import (
        OrderInstruction,
        OrderStatusDecision,
        interpret_equity_order,
    )
    from trade_state import (
        StopRecoveryAction,
        StopRecoveryStep,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )


class ProtectiveStopAction(str, Enum):
    WAIT = "wait"
    RECONCILE_FULL_STOP_FILL = (
        "reconcile_full_stop_fill"
    )
    RECOVER_PARTIAL_FILL = "recover_partial_fill"
    RECOVER_INACTIVE_STOP = "recover_inactive_stop"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class ProtectiveStopDecision:
    action: ProtectiveStopAction
    order_status: OrderStatusDecision
    local_transition_allowed: bool
    position_may_be_unprotected: bool
    requires_immediate_reconciliation: bool
    reason: str
    recovery_action: StopRecoveryAction
    recovery_step: StopRecoveryStep
    operator_ack_required: bool

    @property
    def fail_closed(self) -> bool:
        return self.action == ProtectiveStopAction.FAIL_CLOSED


@dataclass(frozen=True)
class ProtectiveStopResult:
    decision: ProtectiveStopDecision
    trade_before: TradeRecord
    trade_after: TradeRecord
    ledger_changed: bool
    recovery_event_recorded: bool


def decimal_price(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return None

    try:
        price = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if price <= 0:
        return None

    return price


def integer_quantity(value: Decimal) -> int | None:
    if value < 0 or value != value.to_integral_value():
        return None
    return int(value)


def protective_stop_observation_key(
    order: EquityOrder,
    average_fill_price: Any = None,
) -> str:
    """
    Stable evidence key derived from broker order content.

    Local observation time is deliberately excluded so
    an identical observation has the same key after restart.
    """

    payload = {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "state": order.state,
        "quantity": str(order.quantity),
        "cumulative_quantity": str(
            order.cumulative_quantity
        ),
        "limit_price": (
            str(order.limit_price)
            if order.limit_price is not None
            else None
        ),
        "stop_price": (
            str(order.stop_price)
            if order.stop_price is not None
            else None
        ),
        "trigger": order.trigger,
        "placed_agent": order.placed_agent,
        "average_fill_price": (
            str(average_fill_price)
            if average_fill_price not in {None, ""}
            else None
        ),
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def fail_closed_decision(
    order_status: OrderStatusDecision,
    reason: str,
    *,
    position_may_be_unprotected: bool = True,
) -> ProtectiveStopDecision:
    return ProtectiveStopDecision(
        action=ProtectiveStopAction.FAIL_CLOSED,
        order_status=order_status,
        local_transition_allowed=True,
        position_may_be_unprotected=(
            position_may_be_unprotected
        ),
        requires_immediate_reconciliation=True,
        reason=reason,
        recovery_action=(
            StopRecoveryAction.OPERATOR_INTERVENTION
        ),
        recovery_step=(
            StopRecoveryStep
            .INCONSISTENT_STATE_RECORDED
        ),
        operator_ack_required=True,
    )


def decide_protective_stop_lifecycle(
    *,
    trade: TradeRecord,
    order: EquityOrder,
    average_fill_price: Any = None,
) -> ProtectiveStopDecision:
    """
    Classify a broker stop observation without broker writes.

    Any evidence that does not prove an exact active,
    unfilled linked stop is routed to durable recovery.
    """

    order_status = interpret_equity_order(order)

    if trade.status not in {
        TradeStatus.OPEN_PROTECTED,
        TradeStatus.STOP_RECOVERY_REQUIRED,
    }:
        return fail_closed_decision(
            order_status,
            "The local trade is not in the open-protected state or durable stop-recovery state.",
        )

    if not trade.protective_stop_order_id:
        return fail_closed_decision(
            order_status,
            "The local trade has no linked protective-stop ID.",
        )

    if order.order_id != trade.protective_stop_order_id:
        return fail_closed_decision(
            order_status,
            "The observed broker order ID does not match "
            "the linked protective-stop ID.",
        )

    if order.symbol.upper() != trade.symbol.upper():
        return fail_closed_decision(
            order_status,
            "The protective-stop symbol does not match "
            "the local trade symbol.",
        )

    shape_checks = (
        (
            order.side.strip().lower() == "sell",
            "The observed protective order is not a sell order.",
        ),
        (
            order.trigger.strip().lower() == "stop",
            "The observed protective order does not have a stop trigger.",
        ),
        (
            order.order_type.strip().lower()
            == "stop_market",
            "The observed protective order is not a "
            "stop-market order.",
        ),
        (
            order.placed_agent.strip().lower() == "agentic",
            "The linked stop was not placed by the "
            "agentic broker channel.",
        ),
    )
    for passed, reason in shape_checks:
        if not passed:
            return fail_closed_decision(
                order_status,
                reason,
            )

    expected_position = (
        trade.remaining_broker_position_quantity
        if trade.remaining_broker_position_quantity
        is not None
        else trade.filled_quantity
    )
    requested = integer_quantity(
        order_status.requested_quantity
    )
    executed = integer_quantity(
        order_status.filled_quantity
    )

    if expected_position <= 0:
        return fail_closed_decision(
            order_status,
            "The local trade has no positive position "
            "quantity requiring protection.",
        )

    if requested is None or executed is None:
        return fail_closed_decision(
            order_status,
            "Protective-stop quantities must be "
            "non-negative whole-share values.",
        )

    if requested < expected_position:
        return fail_closed_decision(
            order_status,
            "The protective stop is undersized for the "
            "current position.",
        )

    if requested > expected_position:
        return fail_closed_decision(
            order_status,
            "The protective stop is oversized and could "
            "create an oversell.",
        )

    if executed > expected_position:
        return fail_closed_decision(
            order_status,
            "Broker stop execution exceeds the current "
            "position quantity.",
        )

    parsed_average = decimal_price(average_fill_price)
    if executed > 0 and parsed_average is None:
        return fail_closed_decision(
            order_status,
            "A positive stop execution has no valid "
            "broker-confirmed average fill price.",
        )

    if (
        order_status.normalized_state
        in {
            "pending_cancel",
            "pending_cancelled",
        }
    ):
        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .RECOVER_INACTIVE_STOP
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=True,
            requires_immediate_reconciliation=True,
            reason=(
                "A pending-cancel stop is not valid "
                "active protection."
            ),
            recovery_action=(
                StopRecoveryAction
                .REPLACE_PROTECTION_OR_FLATTEN
            ),
            recovery_step=(
                StopRecoveryStep
                .PROTECTION_LOST_RECORDED
            ),
            operator_ack_required=True,
        )

    if order_status.instruction == OrderInstruction.WAIT:
        return ProtectiveStopDecision(
            action=ProtectiveStopAction.WAIT,
            order_status=order_status,
            local_transition_allowed=False,
            position_may_be_unprotected=False,
            requires_immediate_reconciliation=False,
            reason=(
                "The linked protective stop remains "
                "active, exact-sized, and unfilled."
            ),
            recovery_action=StopRecoveryAction.NONE,
            recovery_step=(
                StopRecoveryStep.ACTIVE_CONFIRMED
            ),
            operator_ack_required=False,
        )

    if (
        order_status.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    ):
        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .RECOVER_PARTIAL_FILL
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The protective stop has partially "
                "filled; remaining broker exposure and "
                "coverage require fresh reconciliation."
            ),
            recovery_action=(
                StopRecoveryAction
                .PROTECT_REMAINING_POSITION
            ),
            recovery_step=(
                StopRecoveryStep
                .PARTIAL_FILL_RECORDED
            ),
            operator_ack_required=True,
        )

    if (
        order_status.instruction
        == OrderInstruction.CONFIRM_FULL_FILL
    ):
        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .RECONCILE_FULL_STOP_FILL
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=False,
            requires_immediate_reconciliation=True,
            reason=(
                "The stop reports a full fill, but a "
                "fresh broker snapshot must prove the "
                "account position is flat before close."
            ),
            recovery_action=(
                StopRecoveryAction
                .RECONCILE_BROKER_STATE
            ),
            recovery_step=(
                StopRecoveryStep.FULL_FILL_RECORDED
            ),
            operator_ack_required=False,
        )

    if (
        order_status.instruction
        == OrderInstruction
        .RELEASE_UNFILLED_RESERVATION
    ):
        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .RECOVER_INACTIVE_STOP
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The protective stop became inactive; "
                "the position requires replacement "
                "protection or flattening."
            ),
            recovery_action=(
                StopRecoveryAction
                .REPLACE_PROTECTION_OR_FLATTEN
            ),
            recovery_step=(
                StopRecoveryStep
                .PROTECTION_LOST_RECORDED
            ),
            operator_ack_required=True,
        )

    return fail_closed_decision(
        order_status,
        "The broker protective-stop state is unknown, "
        "malformed, or internally inconsistent.",
    )


class ProtectiveStopLifecycleCoordinator:
    """
    Persists broker-write-free protective-stop recovery.

    The coordinator cannot place, replace, cancel, or
    otherwise mutate a broker order.
    """

    def __init__(self, trade_store: TradeStateStore):
        self.trade_store = trade_store

    def apply(
        self,
        *,
        trade_id: str,
        order: EquityOrder,
        average_fill_price: Any = None,
        observation_key: str | None = None,
        observation_at: datetime | None = None,
    ) -> ProtectiveStopResult:
        before = self.trade_store.get_trade(trade_id)
        decision = decide_protective_stop_lifecycle(
            trade=before,
            order=order,
            average_fill_price=average_fill_price,
        )

        if decision.action == ProtectiveStopAction.WAIT:
            return ProtectiveStopResult(
                decision=decision,
                trade_before=before,
                trade_after=before,
                ledger_changed=False,
                recovery_event_recorded=False,
            )

        key = (
            observation_key
            or protective_stop_observation_key(
                order,
                average_fill_price,
            )
        )
        requested = integer_quantity(
            decision.order_status.requested_quantity
        )
        executed = integer_quantity(
            decision.order_status.filled_quantity
        )
        remaining = integer_quantity(
            decision.order_status.remaining_quantity
        )
        parsed_average = decimal_price(
            average_fill_price
        )

        after, recorded = (
            self.trade_store
            .persist_stop_recovery_observation(
                trade_id,
                observation_key=key,
                broker_order_id=order.order_id,
                broker_state=(
                    decision.order_status.normalized_state
                    or str(order.state)
                ),
                requested_quantity=requested,
                executed_quantity=executed or 0,
                remaining_order_quantity=remaining,
                average_fill_price=parsed_average,
                required_action=decision.recovery_action,
                last_completed_step=(
                    decision.recovery_step
                ),
                failure_reason=decision.reason,
                operator_ack_required=(
                    decision.operator_ack_required
                ),
                observed_at=(
                    observation_at
                    or datetime.now(timezone.utc)
                ),
            )
        )

        return ProtectiveStopResult(
            decision=decision,
            trade_before=before,
            trade_after=after,
            ledger_changed=recorded,
            recovery_event_recorded=recorded,
        )


def main() -> None:
    print("\nPROTECTIVE-STOP RECOVERY COORDINATOR\n")
    print(
        "Exact active stop: wait. Any fill, inactive, "
        "unknown, or mismatched evidence: persist durable "
        "recovery and require fresh broker reconciliation."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
