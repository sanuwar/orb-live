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


class ProtectiveStopAction(str, Enum):
    WAIT = "wait"
    CLOSE_FULL_STOP_FILL = "close_full_stop_fill"
    MARK_STOP_INACTIVE = "mark_stop_inactive"
    RECONCILE_PARTIAL_FILL = (
        "reconcile_partial_fill"
    )
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class ProtectiveStopDecision:
    action: ProtectiveStopAction
    order_status: OrderStatusDecision
    local_transition_allowed: bool
    position_may_be_unprotected: bool
    requires_immediate_reconciliation: bool
    reason: str

    @property
    def fail_closed(self) -> bool:
        return (
            self.action
            == ProtectiveStopAction.FAIL_CLOSED
        )


@dataclass(frozen=True)
class ProtectiveStopResult:
    decision: ProtectiveStopDecision
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


def fail_closed_decision(
    order_status: OrderStatusDecision,
    reason: str,
    *,
    position_may_be_unprotected: bool = True,
) -> ProtectiveStopDecision:
    return ProtectiveStopDecision(
        action=ProtectiveStopAction.FAIL_CLOSED,
        order_status=order_status,
        local_transition_allowed=False,
        position_may_be_unprotected=(
            position_may_be_unprotected
        ),
        requires_immediate_reconciliation=True,
        reason=reason,
    )


def decide_protective_stop_lifecycle(
    *,
    trade: TradeRecord,
    order: EquityOrder,
    average_fill_price: Any = None,
) -> ProtectiveStopDecision:
    """
    Convert a broker protective-stop observation into
    a safe local-ledger instruction.

    This function performs no database or broker writes.
    """

    order_status = interpret_equity_order(order)

    if trade.status != TradeStatus.OPEN_PROTECTED:
        return fail_closed_decision(
            order_status,
            (
                "The local trade is not in the "
                "open-protected state."
            ),
        )

    if not trade.protective_stop_order_id:
        return fail_closed_decision(
            order_status,
            (
                "The protected local trade has no "
                "protective-stop order ID."
            ),
        )

    if (
        order.order_id
        != trade.protective_stop_order_id
    ):
        return fail_closed_decision(
            order_status,
            (
                "The observed broker order ID does not "
                "match the local protective-stop ID."
            ),
        )

    if (
        order.symbol.upper()
        != trade.symbol.upper()
    ):
        return fail_closed_decision(
            order_status,
            (
                "The protective-stop symbol does not "
                "match the local trade symbol."
            ),
        )

    if order.side.lower() != "sell":
        return fail_closed_decision(
            order_status,
            (
                "The observed protective order is not "
                "a sell order."
            ),
        )

    if order.trigger != "stop":
        return fail_closed_decision(
            order_status,
            (
                "The observed protective order does "
                "not have a stop trigger."
            ),
        )

    if order.order_type != "stop_market":
        return fail_closed_decision(
            order_status,
            (
                "The observed protective order is not "
                "a stop-market order."
            ),
        )

    if trade.filled_quantity <= 0:
        return fail_closed_decision(
            order_status,
            (
                "The local trade has no confirmed "
                "filled position quantity."
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
                "The protective-stop quantity does not "
                "match the confirmed local position "
                "quantity."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.WAIT
    ):
        return ProtectiveStopDecision(
            action=ProtectiveStopAction.WAIT,
            order_status=order_status,
            local_transition_allowed=False,
            position_may_be_unprotected=False,
            requires_immediate_reconciliation=False,
            reason=(
                "The protective stop remains active "
                "without a reported fill."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    ):
        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .RECONCILE_PARTIAL_FILL
            ),
            order_status=order_status,
            local_transition_allowed=False,
            position_may_be_unprotected=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The protective stop has partially "
                "filled. The remaining broker position "
                "and stop coverage must be reconciled "
                "immediately."
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
                    "The protective stop is fully "
                    "filled, but a valid broker-confirmed "
                    "average fill price was not supplied."
                ),
            )

        if (
            order_status.filled_quantity
            != expected_quantity
        ):
            return fail_closed_decision(
                order_status,
                (
                    "The protective-stop filled quantity "
                    "does not match the local position "
                    "quantity."
                ),
            )

        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .CLOSE_FULL_STOP_FILL
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=False,
            requires_immediate_reconciliation=False,
            reason=(
                "The protective stop fully closed the "
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
                    "The protective stop cannot be "
                    "marked inactive because a fill was "
                    "reported."
                ),
            )

        return ProtectiveStopDecision(
            action=(
                ProtectiveStopAction
                .MARK_STOP_INACTIVE
            ),
            order_status=order_status,
            local_transition_allowed=True,
            position_may_be_unprotected=True,
            requires_immediate_reconciliation=True,
            reason=(
                "The protective stop became inactive "
                "without a fill. The local trade must "
                "be marked unprotected."
            ),
        )

    return fail_closed_decision(
        order_status,
        (
            "The broker protective-stop state cannot "
            "be safely converted into a local trade "
            "transition."
        ),
    )


class ProtectiveStopLifecycleCoordinator:
    """
    Applies approved protective-stop transitions only
    to the local trade-state ledger.

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
    ) -> ProtectiveStopResult:
        before = self.trade_store.get_trade(
            trade_id
        )

        decision = (
            decide_protective_stop_lifecycle(
                trade=before,
                order=order,
                average_fill_price=(
                    average_fill_price
                ),
            )
        )

        after = before
        changed = False

        if (
            decision.action
            == ProtectiveStopAction
            .CLOSE_FULL_STOP_FILL
        ):
            parsed_average_price = decimal_price(
                average_fill_price
            )

            if parsed_average_price is None:
                raise RuntimeError(
                    "Lifecycle decision allowed a stop "
                    "fill without a valid average price."
                )

            after = self.trade_store.close_trade(
                trade_id,
                average_exit_price=(
                    parsed_average_price
                ),
                reason="protective_stop",
            )

            changed = True

        elif (
            decision.action
            == ProtectiveStopAction
            .MARK_STOP_INACTIVE
        ):
            after = (
                self.trade_store
                .mark_protective_stop_inactive(
                    trade_id,
                    reason=(
                        "broker_stop_"
                        f"{decision.order_status.phase.value}"
                    ),
                )
            )

            changed = True

        return ProtectiveStopResult(
            decision=decision,
            trade_before=before,
            trade_after=after,
            ledger_changed=changed,
        )


def main() -> None:
    print(
        "\nPROTECTIVE-STOP LIFECYCLE "
        "COORDINATOR\n"
    )
    print(
        "Active stop: wait without changing "
        "the ledger."
    )
    print(
        "Full stop fill: close the local trade "
        "using the broker-confirmed fill price."
    )
    print(
        "Cancelled or rejected stop: mark the "
        "local position unprotected."
    )
    print(
        "Partial stop fill: fail closed and "
        "require immediate reconciliation."
    )
    print("\nBroker write capability: False")


if __name__ == "__main__":
    main()