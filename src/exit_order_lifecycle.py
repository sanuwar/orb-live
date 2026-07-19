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
        ExitRecoveryAction,
        ExitRecoveryStep,
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
        ExitRecoveryAction,
        ExitRecoveryStep,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )


class ExitOrderAction(str, Enum):
    WAIT = "wait"
    RECORD_PARTIAL_FILL = "record_partial_fill"
    RECORD_FULL_FILL = "record_full_fill"
    RECORD_TERMINAL = "record_terminal"
    FAIL_CLOSED = "fail_closed"

    # Compatibility names for callers that only classify actions.
    RECONCILE_PARTIAL_FILL = "record_partial_fill"
    CLOSE_FULL_EXIT = "record_full_fill"
    RECONCILE_UNFILLED_TERMINAL = "record_terminal"


@dataclass(frozen=True)
class ExitOrderDecision:
    action: ExitOrderAction
    order_status: OrderStatusDecision
    local_transition_allowed: bool
    position_may_remain_open: bool
    requires_immediate_reconciliation: bool
    reason: str
    new_status: TradeStatus
    recovery_action: ExitRecoveryAction
    recovery_step: ExitRecoveryStep
    operator_ack_required: bool
    failure_code: str | None = None

    @property
    def fail_closed(self) -> bool:
        return self.action == ExitOrderAction.FAIL_CLOSED


@dataclass(frozen=True)
class ExitOrderResult:
    decision: ExitOrderDecision
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


def decimal_fees(value: Any) -> Decimal | None:
    if value in {None, ""}:
        return Decimal("0")

    try:
        fees = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None

    if fees < 0:
        return None

    return fees


def integer_quantity(value: Decimal) -> int | None:
    if value < 0 or value != value.to_integral_value():
        return None
    return int(value)


def valid_close_reason(value: Any) -> str | None:
    if value is None:
        return None
    reason = str(value).strip()
    return reason or None


def exit_observation_key(
    order: EquityOrder,
    average_fill_price: Any = None,
    fees: Any = None,
    recovery_ref_id: str | None = None,
) -> str:
    """Return restart-stable evidence identity without local time."""

    payload = {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side,
        "order_type": order.order_type,
        "state": order.state,
        "quantity": str(order.quantity),
        "cumulative_quantity": str(order.cumulative_quantity),
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
        "fees": (
            str(fees) if fees not in {None, ""} else "0"
        ),
        "recovery_ref_id": recovery_ref_id,
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
    failure_code: str = "invalid_exit_observation",
    position_may_remain_open: bool = True,
) -> ExitOrderDecision:
    return ExitOrderDecision(
        action=ExitOrderAction.FAIL_CLOSED,
        order_status=order_status,
        local_transition_allowed=True,
        position_may_remain_open=position_may_remain_open,
        requires_immediate_reconciliation=True,
        reason=reason,
        new_status=TradeStatus.EXIT_RECOVERY_REQUIRED,
        recovery_action=(
            ExitRecoveryAction.OPERATOR_INTERVENTION
        ),
        recovery_step=(
            ExitRecoveryStep.INCONSISTENT_STATE_RECORDED
        ),
        operator_ack_required=True,
        failure_code=failure_code,
    )


def decide_exit_order_lifecycle(
    *,
    trade: TradeRecord,
    order: EquityOrder,
    average_fill_price: Any = None,
    close_reason: Any = None,
    fees: Any = None,
) -> ExitOrderDecision:
    """Classify exit evidence without broker or database writes."""

    order_status = interpret_equity_order(order)

    if trade.status not in {
        TradeStatus.EXIT_PENDING,
        TradeStatus.EXIT_PARTIALLY_FILLED,
        TradeStatus.EXIT_RECOVERY_REQUIRED,
    }:
        return fail_closed_decision(
            order_status,
            "The local trade has no durable exit lifecycle state.",
            failure_code="invalid_local_exit_status",
        )

    if not trade.exit_order_id:
        return fail_closed_decision(
            order_status,
            "The durable exit lifecycle has no broker order ID.",
            failure_code="missing_exit_order_id",
        )

    if not (
        trade.exit_close_reason
        and trade.exit_intent_type
        and trade.exit_recovery_ref_id
        and trade.exit_requested_quantity is not None
    ):
        return fail_closed_decision(
            order_status,
            "The durable exit intent is incomplete.",
            failure_code="missing_durable_exit_intent",
        )

    supplied_reason = valid_close_reason(close_reason)
    if (
        close_reason is not None
        and supplied_reason != trade.exit_close_reason
    ):
        return fail_closed_decision(
            order_status,
            "The supplied close reason does not match durable intent.",
            failure_code="exit_close_reason_mismatch",
        )

    identity_checks = (
        (
            order.order_id == trade.exit_order_id,
            "exit_order_id_mismatch",
            "The observed order ID does not match the durable exit ID.",
        ),
        (
            order.symbol.strip().upper()
            == trade.symbol.strip().upper(),
            "exit_symbol_mismatch",
            "The observed exit symbol does not match the trade.",
        ),
        (
            order.side.strip().lower() == "sell",
            "exit_side_mismatch",
            "The observed exit is not a sell order.",
        ),
        (
            order.trigger.strip().lower() == "immediate",
            "exit_trigger_mismatch",
            "The observed exit has an unexpected trigger.",
        ),
        (
            order.order_type.strip().lower() == "limit",
            "exit_type_mismatch",
            "The observed exit is not a limit order.",
        ),
        (
            order.placed_agent.strip().lower() == "agentic",
            "exit_origin_mismatch",
            "The observed exit was not placed by the agentic channel.",
        ),
    )
    for passed, code, reason in identity_checks:
        if not passed:
            return fail_closed_decision(
                order_status,
                reason,
                failure_code=code,
            )

    if trade.average_entry_price is None or trade.filled_quantity <= 0:
        return fail_closed_decision(
            order_status,
            "The local trade lacks confirmed entry accounting.",
            failure_code="missing_entry_accounting",
        )

    if trade.protective_stop_order_id:
        return fail_closed_decision(
            order_status,
            "A protective stop remains linked during a separate exit.",
            failure_code="conflicting_sell_order",
        )

    requested = integer_quantity(order_status.requested_quantity)
    executed = integer_quantity(order_status.filled_quantity)
    if requested is None or executed is None:
        return fail_closed_decision(
            order_status,
            "Exit quantities must be non-negative whole-share values.",
            failure_code="invalid_exit_quantity",
        )

    if requested != trade.exit_requested_quantity:
        return fail_closed_decision(
            order_status,
            "Broker exit quantity does not match durable intent.",
            failure_code="exit_requested_quantity_mismatch",
        )

    if executed < trade.exit_executed_quantity:
        return fail_closed_decision(
            order_status,
            "Broker cumulative exit quantity decreased.",
            failure_code="exit_quantity_decreased",
        )

    if (
        trade.exit_attempt_base_quantity + executed
        > trade.filled_quantity
    ):
        return fail_closed_decision(
            order_status,
            "Observed sell execution exceeds the original position.",
            failure_code="exit_execution_exceeds_position",
            position_may_remain_open=False,
        )

    parsed_average = decimal_price(average_fill_price)
    if executed > 0 and parsed_average is None:
        return fail_closed_decision(
            order_status,
            "Positive exit execution lacks a valid average price.",
            failure_code="exit_average_price_missing",
            position_may_remain_open=(
                trade.exit_attempt_base_quantity + executed
                < trade.filled_quantity
            ),
        )

    if decimal_fees(fees) is None:
        return fail_closed_decision(
            order_status,
            "Exit fees are invalid or negative.",
            failure_code="invalid_exit_fees",
        )

    if (
        trade.exit_intended_price is not None
        and order.limit_price != trade.exit_intended_price
    ):
        return fail_closed_decision(
            order_status,
            "Broker limit price does not match durable exit intent.",
            failure_code="exit_price_mismatch",
        )

    if order_status.instruction == OrderInstruction.WAIT:
        return ExitOrderDecision(
            action=ExitOrderAction.WAIT,
            order_status=order_status,
            local_transition_allowed=True,
            position_may_remain_open=True,
            requires_immediate_reconciliation=False,
            reason="The exact linked exit remains active and unfilled.",
            new_status=TradeStatus.EXIT_PENDING,
            recovery_action=ExitRecoveryAction.WAIT,
            recovery_step=ExitRecoveryStep.OBSERVATION_RECORDED,
            operator_ack_required=False,
        )

    if (
        order_status.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    ):
        terminal = order_status.terminal
        return ExitOrderDecision(
            action=ExitOrderAction.RECORD_PARTIAL_FILL,
            order_status=order_status,
            local_transition_allowed=True,
            position_may_remain_open=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The exit partially filled; fresh snapshot proof must "
                "show the remaining position is exactly protected."
            ),
            new_status=(
                TradeStatus.EXIT_RECOVERY_REQUIRED
                if terminal
                else TradeStatus.EXIT_PARTIALLY_FILLED
            ),
            recovery_action=(
                ExitRecoveryAction.PROTECT_REMAINING_POSITION
            ),
            recovery_step=ExitRecoveryStep.PARTIAL_FILL_RECORDED,
            operator_ack_required=terminal,
            failure_code=(
                "terminal_partial_exit" if terminal else None
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.CONFIRM_FULL_FILL
    ):
        return ExitOrderDecision(
            action=ExitOrderAction.RECORD_FULL_FILL,
            order_status=order_status,
            local_transition_allowed=True,
            position_may_remain_open=False,
            requires_immediate_reconciliation=True,
            reason=(
                "The exit reports a full fill; a fresh complete broker "
                "snapshot must prove the account position is flat."
            ),
            new_status=TradeStatus.EXIT_RECOVERY_REQUIRED,
            recovery_action=(
                ExitRecoveryAction.RECONCILE_BROKER_STATE
            ),
            recovery_step=ExitRecoveryStep.FULL_FILL_RECORDED,
            operator_ack_required=False,
        )

    if (
        order_status.instruction
        == OrderInstruction.RELEASE_UNFILLED_RESERVATION
    ):
        return ExitOrderDecision(
            action=ExitOrderAction.RECORD_TERMINAL,
            order_status=order_status,
            local_transition_allowed=True,
            position_may_remain_open=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The exit became inactive; fresh snapshot proof must "
                "show the position flat or exactly protected."
            ),
            new_status=TradeStatus.EXIT_RECOVERY_REQUIRED,
            recovery_action=(
                ExitRecoveryAction.PROTECT_REMAINING_POSITION
            ),
            recovery_step=ExitRecoveryStep.TERMINAL_RECORDED,
            operator_ack_required=True,
            failure_code="exit_inactive",
        )

    return fail_closed_decision(
        order_status,
        "The broker exit state is unknown or inconsistent.",
        failure_code="unknown_exit_state",
    )


class ExitOrderLifecycleCoordinator:
    """Persist exit evidence without any broker-write capability."""

    def __init__(self, trade_store: TradeStateStore):
        self.trade_store = trade_store

    def apply(
        self,
        *,
        trade_id: str,
        order: EquityOrder,
        average_fill_price: Any = None,
        close_reason: Any = None,
        fees: Any = None,
        observation_key: str | None = None,
        observation_at: datetime | None = None,
    ) -> ExitOrderResult:
        before = self.trade_store.get_trade(trade_id)
        decision = decide_exit_order_lifecycle(
            trade=before,
            order=order,
            average_fill_price=average_fill_price,
            close_reason=close_reason,
            fees=fees,
        )
        key = observation_key or exit_observation_key(
            order,
            average_fill_price,
            fees,
            before.exit_recovery_ref_id,
        )
        requested = integer_quantity(
            decision.order_status.requested_quantity
        )
        executed = integer_quantity(
            decision.order_status.filled_quantity
        )
        parsed_average = decimal_price(average_fill_price)
        parsed_fees = decimal_fees(fees)

        after, recorded = self.trade_store.persist_exit_observation(
            trade_id,
            observation_key=key,
            broker_order_id=order.order_id,
            broker_state=(
                decision.order_status.normalized_state
                or str(order.state)
            ),
            requested_quantity=requested,
            executed_quantity=executed or 0,
            average_fill_price=parsed_average,
            order_fees=parsed_fees or Decimal("0"),
            new_status=decision.new_status,
            required_action=decision.recovery_action,
            last_completed_step=decision.recovery_step,
            failure_code=decision.failure_code,
            failure_reason=(
                decision.reason if decision.failure_code else None
            ),
            operator_ack_required=(
                decision.operator_ack_required
            ),
            observed_at=(
                observation_at or datetime.now(timezone.utc)
            ),
        )

        return ExitOrderResult(
            decision=decision,
            trade_before=before,
            trade_after=after,
            ledger_changed=recorded,
            recovery_event_recorded=recorded,
        )


def main() -> None:
    print("\nEXIT-ORDER RECOVERY COORDINATOR\n")
    print(
        "Exit observations are persisted durably. Full fills require "
        "fresh flat-account proof; partial or inactive exits require "
        "fresh proof of flatness or exact remaining protection."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()