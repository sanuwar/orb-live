from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any

try:
    from .account_state import EquityOrder
    from .order_state import (
        OrderInstruction,
        OrderPhase,
        OrderStatusDecision,
        interpret_equity_order,
    )
    from .trade_state import (
        EntryRecoveryAction,
        EntryRecoveryStep,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )
except ImportError:
    from account_state import EquityOrder
    from order_state import (
        OrderInstruction,
        OrderPhase,
        OrderStatusDecision,
        interpret_equity_order,
    )
    from trade_state import (
        EntryRecoveryAction,
        EntryRecoveryStep,
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )


class EntryLifecycleAction(str, Enum):
    WAIT = "wait"
    MARK_FULL_FILL = "mark_full_fill"
    CANCEL_UNFILLED = "cancel_unfilled"
    PROTECT_PARTIAL_FILL = "protect_partial_fill"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class EntryLifecycleDecision:
    action: EntryLifecycleAction
    order_status: OrderStatusDecision
    local_transition_allowed: bool
    requires_immediate_protection: bool
    required_recovery_action: EntryRecoveryAction
    reason: str

    @property
    def fail_closed(self) -> bool:
        return (
            self.action
            == EntryLifecycleAction.FAIL_CLOSED
        )


@dataclass(frozen=True)
class EntryLifecycleResult:
    decision: EntryLifecycleDecision
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
) -> EntryLifecycleDecision:
    return EntryLifecycleDecision(
        action=EntryLifecycleAction.FAIL_CLOSED,
        order_status=order_status,
        local_transition_allowed=False,
        requires_immediate_protection=(
            order_status.has_any_fill
        ),
        required_recovery_action=(
            EntryRecoveryAction
            .RECONCILE_BROKER_STATE
        ),
        reason=reason,
    )


def decide_entry_lifecycle(
    *,
    trade: TradeRecord,
    order: EquityOrder,
    average_fill_price: Any = None,
) -> EntryLifecycleDecision:
    """
    Convert one broker entry-order observation into a
    safe local-ledger instruction.

    This function is deterministic and performs no
    broker or database writes.
    """

    order_status = interpret_equity_order(order)

    if trade.status not in {
        TradeStatus.ENTRY_PENDING,
        TradeStatus.ENTRY_PARTIALLY_FILLED,
        TradeStatus.PROTECTION_PENDING,
        TradeStatus.RECOVERY_REQUIRED,
    }:
        return fail_closed_decision(
            order_status,
            (
                "The local trade is not in the "
                "entry-pending state."
            ),
        )

    if not trade.entry_order_id:
        return fail_closed_decision(
            order_status,
            (
                "The local entry-pending trade has no "
                "broker entry-order ID."
            ),
        )

    if order.order_id != trade.entry_order_id:
        return fail_closed_decision(
            order_status,
            (
                "The observed broker order ID does not "
                "match the local entry-order ID."
            ),
        )

    if order.symbol.upper() != trade.symbol.upper():
        return fail_closed_decision(
            order_status,
            (
                "The broker order symbol does not match "
                "the local trade symbol."
            ),
        )

    if order.side.lower() != "buy":
        return fail_closed_decision(
            order_status,
            (
                "The observed entry order is not a "
                "buy order."
            ),
        )

    if order.trigger != "immediate":
        return fail_closed_decision(
            order_status,
            (
                "The observed entry order has an "
                "unexpected trigger type."
            ),
        )

    expected_quantity = Decimal(
        trade.quantity
    )

    if (
        order_status.requested_quantity
        != expected_quantity
    ):
        return fail_closed_decision(
            order_status,
            (
                "The broker order quantity does not "
                "match the local reserved quantity."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.WAIT
    ):
        return EntryLifecycleDecision(
            action=EntryLifecycleAction.WAIT,
            order_status=order_status,
            local_transition_allowed=False,
            requires_immediate_protection=False,
            required_recovery_action=(
                EntryRecoveryAction.WAIT
            ),
            reason=(
                "The entry order remains pending "
                "without a fill."
            ),
        )

    if (
        order_status.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    ):
        return EntryLifecycleDecision(
            action=(
                EntryLifecycleAction
                .PROTECT_PARTIAL_FILL
            ),
            order_status=order_status,
            local_transition_allowed=True,
            requires_immediate_protection=True,
            required_recovery_action=(
                EntryRecoveryAction
                .PROTECT_FILLED_QUANTITY
                if order_status.normalized_state
                in {
                    "cancelled",
                    "rejected",
                }
                else EntryRecoveryAction
                .CANCEL_REMAINDER_THEN_PROTECT
            ),
            reason=(
                "A partial entry fill exists. The "
                "actual broker position must be "
                "reconciled and protected before any "
                "normal lifecycle transition continues."
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
                    "The entry is fully filled, but a "
                    "valid broker-confirmed average fill "
                    "price was not supplied."
                ),
            )

        if (
            order_status.filled_quantity
            != expected_quantity
        ):
            return fail_closed_decision(
                order_status,
                (
                    "The confirmed filled quantity does "
                    "not match the local trade quantity."
                ),
            )

        return EntryLifecycleDecision(
            action=(
                EntryLifecycleAction.MARK_FULL_FILL
            ),
            order_status=order_status,
            local_transition_allowed=True,
            requires_immediate_protection=True,
            required_recovery_action=(
                EntryRecoveryAction
                .PROTECT_FILLED_QUANTITY
            ),
            reason=(
                "The full entry quantity and average "
                "fill price are confirmed. The local "
                "trade may transition to open and "
                "unprotected."
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
                    "The order cannot release its "
                    "reservation because a fill was "
                    "reported."
                ),
            )

        return EntryLifecycleDecision(
            action=(
                EntryLifecycleAction
                .CANCEL_UNFILLED
            ),
            order_status=order_status,
            local_transition_allowed=True,
            requires_immediate_protection=False,
            required_recovery_action=(
                EntryRecoveryAction.NONE
            ),
            reason=(
                "The entry order reached a terminal "
                "state without a fill. The local "
                "reservation may be cancelled."
            ),
        )

    return fail_closed_decision(
        order_status,
        (
            "The broker order state cannot be safely "
            "converted into a local trade transition."
        ),
    )


def entry_observation_key(
    order: EquityOrder,
    average_fill_price: Any = None,
) -> str:
    payload = {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "state": order.state,
        "side": order.side,
        "order_type": order.order_type,
        "trigger": order.trigger,
        "quantity": str(order.quantity),
        "cumulative_quantity": str(
            order.cumulative_quantity
        ),
        "average_fill_price": str(
            average_fill_price
        ),
    }

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    return hashlib.sha256(encoded).hexdigest()

class EntryLifecycleCoordinator:
    """
    Applies approved entry lifecycle transitions only
    to the local trade-state ledger.

    This class contains no Robinhood client and cannot
    review, place, replace, or cancel a broker order.
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
        observation_at: datetime | None = None,
        observation_key: str | None = None,
    ) -> EntryLifecycleResult:
        before = self.trade_store.get_trade(
            trade_id
        )

        decision = decide_entry_lifecycle(
            trade=before,
            order=order,
            average_fill_price=(
                average_fill_price
            ),
        )

        after = before
        changed = False
        timestamp = (
            observation_at
            or datetime.now(timezone.utc)
        )
        stable_key = (
            observation_key
            or entry_observation_key(
                order,
                average_fill_price,
            )
        )

        filled_decimal = (
            decision.order_status
            .filled_quantity
        )

        if (
            filled_decimal
            == filled_decimal.to_integral_value()
        ):
            observed_filled = int(
                filled_decimal
            )
        else:
            observed_filled = (
                before.filled_quantity
            )

        parsed_average_price = decimal_price(
            average_fill_price
        )

        if (
            decision.action
            == EntryLifecycleAction.MARK_FULL_FILL
        ):
            after, changed = (
                self.trade_store
                .persist_entry_observation(
                    trade_id,
                    status=(
                        TradeStatus
                        .PROTECTION_PENDING
                    ),
                    filled_quantity=(
                        observed_filled
                    ),
                    average_entry_price=(
                        parsed_average_price
                    ),
                    required_action=(
                        decision
                        .required_recovery_action
                    ),
                    last_completed_step=(
                        EntryRecoveryStep
                        .FULL_FILL_CLASSIFIED
                    ),
                    observation_key=stable_key,
                    observed_at=timestamp,
                )
            )

        elif (
            decision.action
            == EntryLifecycleAction
            .PROTECT_PARTIAL_FILL
        ):
            terminal_partial = (
                decision.order_status
                .normalized_state
                in {
                    "cancelled",
                    "rejected",
                }
            )

            after, changed = (
                self.trade_store
                .persist_entry_observation(
                    trade_id,
                    status=(
                        TradeStatus
                        .RECOVERY_REQUIRED
                        if terminal_partial
                        else TradeStatus
                        .ENTRY_PARTIALLY_FILLED
                    ),
                    filled_quantity=(
                        observed_filled
                    ),
                    average_entry_price=(
                        parsed_average_price
                    ),
                    required_action=(
                        decision
                        .required_recovery_action
                    ),
                    last_completed_step=(
                        EntryRecoveryStep
                        .PARTIAL_FILL_CLASSIFIED
                    ),
                    observation_key=stable_key,
                    observed_at=timestamp,
                )
            )

        elif (
            decision.action
            == EntryLifecycleAction.FAIL_CLOSED
        ):
            after, changed = (
                self.trade_store
                .persist_entry_observation(
                    trade_id,
                    status=(
                        TradeStatus
                        .RECOVERY_REQUIRED
                    ),
                    filled_quantity=(
                        observed_filled
                    ),
                    average_entry_price=(
                        parsed_average_price
                    ),
                    required_action=(
                        EntryRecoveryAction
                        .RECONCILE_BROKER_STATE
                    ),
                    last_completed_step=(
                        EntryRecoveryStep
                        .AMBIGUOUS_STATE_RECORDED
                    ),
                    observation_key=stable_key,
                    observed_at=timestamp,
                    failure_reason=(
                        decision.reason
                    ),
                    operator_ack_required=True,
                )
            )

        elif (
            decision.action
            == EntryLifecycleAction
            .CANCEL_UNFILLED
        ):
            after = (
                self.trade_store
                .cancel_unfilled_trade(
                    trade_id,
                    reason=(
                        "broker_entry_"
                        f"{decision.order_status.phase.value}"
                    ),
                    occurred_at=timestamp,
                )
            )

            changed = True

        return EntryLifecycleResult(
            decision=decision,
            trade_before=before,
            trade_after=after,
            ledger_changed=changed,
        )


def main() -> None:
    print("\nENTRY LIFECYCLE COORDINATOR\n")
    print(
        "Pending order: wait without changing "
        "the ledger."
    )
    print(
        "Full fill: require a broker-confirmed "
        "average fill price."
    )
    print(
        "Partial fill: fail closed and require "
        "immediate reconciliation and protection."
    )
    print(
        "Cancelled or rejected without a fill: "
        "release the local reservation."
    )
    print("\nBroker write capability: False")


if __name__ == "__main__":
    main()