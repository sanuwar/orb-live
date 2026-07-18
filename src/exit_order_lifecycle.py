from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

try:
    from .account_state import EquityOrder
    from .order_state import (
        OrderInstruction,
        OrderStatusDecision,
        interpret_equity_order,
    )
    from .trade_state import (
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
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )


class ExitOrderAction(str, Enum):
    WAIT = "wait"
    CLOSE_FULL_EXIT = "close_full_exit"
    RECONCILE_PARTIAL_FILL = (
        "reconcile_partial_fill"
    )
    RECONCILE_UNFILLED_TERMINAL = (
        "reconcile_unfilled_terminal"
    )
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class ExitOrderDecision:
    action: ExitOrderAction
    order_status: OrderStatusDecision
    local_transition_allowed: bool
    position_may_remain_open: bool
    requires_immediate_reconciliation: bool
    reason: str

    @property
    def fail_closed(self) -> bool:
        return (
            self.action
            == ExitOrderAction.FAIL_CLOSED
        )


@dataclass(frozen=True)
class ExitOrderResult:
    decision: ExitOrderDecision
    trade_before: TradeRecord
    trade_after: TradeRecord
    ledger_changed: bool


def decimal_price(
    value: Any,
) -> Decimal | None:
    if value in {None, ""}:
        return None

    try:
        price = Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None

    if price <= 0:
        return None

    return price


def valid_close_reason(
    value: Any,
) -> str | None:
    if value is None:
        return None

    reason = str(value).strip()

    if not reason:
        return None

    return reason


def fail_closed_decision(
    order_status: OrderStatusDecision,
    reason: str,
    *,
    position_may_remain_open: bool = True,
) -> ExitOrderDecision:
    return ExitOrderDecision(
        action=ExitOrderAction.FAIL_CLOSED,
        order_status=order_status,
        local_transition_allowed=False,
        position_may_remain_open=(
            position_may_remain_open
        ),
        requires_immediate_reconciliation=True,
        reason=reason,
    )


def decide_exit_order_lifecycle(
    *,
    trade: TradeRecord,
    order: EquityOrder,
    average_fill_price: Any = None,
    close_reason: Any = None,
) -> ExitOrderDecision:
    """
    Convert a broker profit-target or time-exit order
    observation into a safe local-ledger instruction.

    This function performs no broker or database writes.
    """

    order_status = interpret_equity_order(order)

    if trade.status != TradeStatus.EXIT_PENDING:
        return fail_closed_decision(
            order_status,
            (
                "The local trade is not in the "
                "exit-pending state."
            ),
        )

    if not trade.exit_order_id:
        return fail_closed_decision(
            order_status,
            (
                "The exit-pending local trade has no "
                "broker exit-order ID."
            ),
        )

    if order.order_id != trade.exit_order_id:
        return fail_closed_decision(
            order_status,
            (
                "The observed broker order ID does not "
                "match the local exit-order ID."
            ),
        )

    if order.symbol.upper() != trade.symbol.upper():
        return fail_closed_decision(
            order_status,
            (
                "The broker exit-order symbol does not "
                "match the local trade symbol."
            ),
        )

    if order.side.lower() != "sell":
        return fail_closed_decision(
            order_status,
            (
                "The observed exit order is not a "
                "sell order."
            ),
        )

    if order.trigger != "immediate":
        return fail_closed_decision(
            order_status,
            (
                "The observed exit order has an "
                "unexpected trigger type."
            ),
        )

    if order.order_type != "limit":
        return fail_closed_decision(
            order_status,
            (
                "The observed exit order is not a "
                "limit order."
            ),
        )

    if trade.filled_quantity <= 0:
        return fail_closed_decision(
            order_status,
            (
                "The local trade has no confirmed "
                "position quantity to exit."
            ),
        )

    if trade.average_entry_price is None:
        return fail_closed_decision(
            order_status,
            (
                "The local trade has no confirmed "
                "average entry price."
            ),
        )

    if trade.protective_stop_order_id:
        return fail_closed_decision(
            order_status,
            (
                "A protective-stop order is still "
                "linked to the trade while a separate "
                "exit order is pending. This could "
                "create conflicting sell orders."
            ),
        )

    expected_quantity = Decimal(
        trade.filled_quantity
    )

    if (
        order_status.requested_quantity
        != expected_quantity
    ):
        return fail_closed_decision(
            order_status,
            (
                "The exit-order quantity does not match "
                "the confirmed local position quantity."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.WAIT
    ):
        return ExitOrderDecision(
            action=ExitOrderAction.WAIT,
            order_status=order_status,
            local_transition_allowed=False,
            position_may_remain_open=True,
            requires_immediate_reconciliation=False,
            reason=(
                "The exit order remains active without "
                "a reported fill."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    ):
        return ExitOrderDecision(
            action=(
                ExitOrderAction
                .RECONCILE_PARTIAL_FILL
            ),
            order_status=order_status,
            local_transition_allowed=False,
            position_may_remain_open=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The exit order has partially filled. "
                "The remaining broker position must be "
                "reconciled and protected immediately."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.CONFIRM_FULL_FILL
    ):
        parsed_average_price = decimal_price(
            average_fill_price
        )

        if parsed_average_price is None:
            return fail_closed_decision(
                order_status,
                (
                    "The exit is fully filled, but a "
                    "valid broker-confirmed average fill "
                    "price was not supplied."
                ),
                position_may_remain_open=False,
            )

        parsed_close_reason = valid_close_reason(
            close_reason
        )

        if parsed_close_reason is None:
            return fail_closed_decision(
                order_status,
                (
                    "The exit is fully filled, but a "
                    "valid close reason was not supplied."
                ),
                position_may_remain_open=False,
            )

        if (
            order_status.filled_quantity
            != expected_quantity
        ):
            return fail_closed_decision(
                order_status,
                (
                    "The exit filled quantity does not "
                    "match the local position quantity."
                ),
                position_may_remain_open=False,
            )

        return ExitOrderDecision(
            action=ExitOrderAction.CLOSE_FULL_EXIT,
            order_status=order_status,
            local_transition_allowed=True,
            position_may_remain_open=False,
            requires_immediate_reconciliation=False,
            reason=(
                "The exit order fully closed the "
                "confirmed local position."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction
        .RELEASE_UNFILLED_RESERVATION
    ):
        if order_status.filled_quantity != 0:
            return fail_closed_decision(
                order_status,
                (
                    "The terminal exit order cannot be "
                    "treated as unfilled because a fill "
                    "was reported."
                ),
            )

        return ExitOrderDecision(
            action=(
                ExitOrderAction
                .RECONCILE_UNFILLED_TERMINAL
            ),
            order_status=order_status,
            local_transition_allowed=False,
            position_may_remain_open=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The exit order became inactive without "
                "a fill. The broker position and its "
                "protective coverage must be reconciled "
                "before another exit is attempted."
            ),
        )

    return fail_closed_decision(
        order_status,
        (
            "The broker exit-order state cannot be "
            "safely converted into a local trade "
            "transition."
        ),
    )


class ExitOrderLifecycleCoordinator:
    """
    Applies confirmed full exit fills only to the local
    trade-state ledger.

    Partial, cancelled, rejected, inconsistent, and
    unfamiliar states remain unchanged and require
    reconciliation.

    This class contains no Robinhood client and cannot
    place, replace, or cancel a broker order.
    """

    def __init__(
        self,
        trade_store: TradeStateStore,
    ):
        self.trade_store = trade_store

    def apply(
        self,
        *,
        trade_id: str,
        order: EquityOrder,
        average_fill_price: Any = None,
        close_reason: Any = None,
    ) -> ExitOrderResult:
        before = self.trade_store.get_trade(
            trade_id
        )

        decision = decide_exit_order_lifecycle(
            trade=before,
            order=order,
            average_fill_price=(
                average_fill_price
            ),
            close_reason=close_reason,
        )

        after = before
        changed = False

        if (
            decision.action
            == ExitOrderAction.CLOSE_FULL_EXIT
        ):
            parsed_average_price = decimal_price(
                average_fill_price
            )

            parsed_close_reason = valid_close_reason(
                close_reason
            )

            if (
                parsed_average_price is None
                or parsed_close_reason is None
            ):
                raise RuntimeError(
                    "Lifecycle decision allowed a full "
                    "exit without valid close details."
                )

            after = self.trade_store.close_trade(
                trade_id,
                average_exit_price=(
                    parsed_average_price
                ),
                reason=parsed_close_reason,
            )

            changed = True

        return ExitOrderResult(
            decision=decision,
            trade_before=before,
            trade_after=after,
            ledger_changed=changed,
        )


def main() -> None:
    print("\nEXIT-ORDER LIFECYCLE COORDINATOR\n")
    print(
        "Pending exit: wait without changing "
        "the ledger."
    )
    print(
        "Full exit fill: close the local trade "
        "using the broker-confirmed fill price."
    )
    print(
        "Partial exit fill: fail closed and "
        "reconcile the remaining position."
    )
    print(
        "Cancelled or rejected exit: keep the "
        "ledger unchanged and reconcile protection."
    )
    print(
        "Conflicting protective stop: fail closed "
        "to prevent duplicate sell exposure."
    )
    print("\nBroker write capability: False")


if __name__ == "__main__":
    main()