from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any


class OrderPhase(str, Enum):
    PENDING = "pending"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


class OrderInstruction(str, Enum):
    WAIT = "wait"
    PROTECT_PARTIAL_FILL = "protect_partial_fill"
    CONFIRM_FULL_FILL = "confirm_full_fill"
    RELEASE_UNFILLED_RESERVATION = (
        "release_unfilled_reservation"
    )
    FAIL_CLOSED = "fail_closed"


PENDING_STATES = {
    "new",
    "queued",
    "unconfirmed",
    "confirmed",
    "pending",
    "open",
    "submitted",
    "accepted",
    "locating",
    "pending_cancel",
    "pending_cancelled",
    "pending_canceled",
}

PARTIAL_STATES = {
    "partially_filled",
    "partial_fill",
    "partially_executed",
}

FILLED_STATES = {
    "filled",
    "executed",
    "completed",
}

CANCELLED_STATES = {
    "cancelled",
    "canceled",
    "voided",
    "expired",
}

REJECTED_STATES = {
    "rejected",
    "failed",
    "denied",
}


@dataclass(frozen=True)
class OrderStatusDecision:
    broker_state: str
    normalized_state: str
    phase: OrderPhase
    instruction: OrderInstruction
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    terminal: bool
    requires_reconciliation: bool
    reason: str

    @property
    def has_any_fill(self) -> bool:
        return self.filled_quantity > 0

    @property
    def fully_filled(self) -> bool:
        return (
            self.phase == OrderPhase.FILLED
            and self.filled_quantity
            == self.requested_quantity
        )

    @property
    def safe_to_release_reservation(self) -> bool:
        return (
            self.terminal
            and self.filled_quantity == 0
            and self.phase
            in {
                OrderPhase.CANCELLED,
                OrderPhase.REJECTED,
            }
        )


def normalize_order_state(
    value: Any,
) -> str:
    if value is None:
        return ""

    normalized = str(value).strip().lower()

    normalized = normalized.replace("-", "_")
    normalized = normalized.replace(" ", "_")

    while "__" in normalized:
        normalized = normalized.replace(
            "__",
            "_",
        )

    if normalized == "canceled":
        return "cancelled"

    if normalized == "pending_canceled":
        return "pending_cancelled"

    return normalized


def decimal_quantity(
    value: Any,
) -> Decimal | None:
    if value in {None, ""}:
        return None

    try:
        return Decimal(str(value))
    except (
        InvalidOperation,
        TypeError,
        ValueError,
    ):
        return None


def unknown_decision(
    *,
    broker_state: str,
    normalized_state: str,
    requested_quantity: Decimal,
    filled_quantity: Decimal,
    remaining_quantity: Decimal,
    reason: str,
) -> OrderStatusDecision:
    return OrderStatusDecision(
        broker_state=broker_state,
        normalized_state=normalized_state,
        phase=OrderPhase.UNKNOWN,
        instruction=OrderInstruction.FAIL_CLOSED,
        requested_quantity=requested_quantity,
        filled_quantity=filled_quantity,
        remaining_quantity=remaining_quantity,
        terminal=False,
        requires_reconciliation=True,
        reason=reason,
    )


def interpret_order_status(
    *,
    broker_state: Any,
    requested_quantity: Any,
    cumulative_quantity: Any,
) -> OrderStatusDecision:
    """
    Interpret broker order state using both the textual
    state and reported cumulative fill quantity.

    Any inconsistent or unfamiliar combination fails
    closed and requires broker reconciliation.
    """

    raw_state = (
        ""
        if broker_state is None
        else str(broker_state)
    )

    normalized_state = normalize_order_state(
        broker_state
    )

    requested = decimal_quantity(
        requested_quantity
    )

    filled = decimal_quantity(
        cumulative_quantity
    )

    if requested is None or requested <= 0:
        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=(
                requested or Decimal("0")
            ),
            filled_quantity=(
                filled or Decimal("0")
            ),
            remaining_quantity=Decimal("0"),
            reason=(
                "Requested order quantity is missing, "
                "invalid, or not positive."
            ),
        )

    if filled is None or filled < 0:
        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=(
                filled or Decimal("0")
            ),
            remaining_quantity=requested,
            reason=(
                "Cumulative filled quantity is missing, "
                "invalid, or negative."
            ),
        )

    remaining = max(
        Decimal("0"),
        requested - filled,
    )

    if filled > requested:
        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=Decimal("0"),
            reason=(
                "Broker cumulative fill exceeds the "
                "requested order quantity."
            ),
        )

    if not normalized_state:
        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            reason=(
                "Broker order state is missing."
            ),
        )

    if normalized_state in FILLED_STATES:
        if filled != requested:
            return unknown_decision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=remaining,
                reason=(
                    "Broker reports a filled order, but "
                    "the cumulative quantity does not "
                    "equal the requested quantity."
                ),
            )

        return OrderStatusDecision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            phase=OrderPhase.FILLED,
            instruction=(
                OrderInstruction.CONFIRM_FULL_FILL
            ),
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=Decimal("0"),
            terminal=True,
            requires_reconciliation=False,
            reason=(
                "Broker reports the full requested "
                "quantity as filled."
            ),
        )

    if normalized_state in PARTIAL_STATES:
        if filled <= 0 or filled >= requested:
            return unknown_decision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=remaining,
                reason=(
                    "Broker partial-fill state is "
                    "inconsistent with the reported "
                    "filled quantity."
                ),
            )

        return OrderStatusDecision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            phase=OrderPhase.PARTIALLY_FILLED,
            instruction=(
                OrderInstruction
                .PROTECT_PARTIAL_FILL
            ),
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            terminal=False,
            requires_reconciliation=True,
            reason=(
                "The order is partially filled. The "
                "actual filled quantity must be "
                "reconciled and protected immediately."
            ),
        )

    if normalized_state in CANCELLED_STATES:
        if filled == 0:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=OrderPhase.CANCELLED,
                instruction=(
                    OrderInstruction
                    .RELEASE_UNFILLED_RESERVATION
                ),
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=requested,
                terminal=True,
                requires_reconciliation=False,
                reason=(
                    "The order was cancelled without "
                    "receiving a fill."
                ),
            )

        if filled < requested:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=(
                    OrderPhase.PARTIALLY_FILLED
                ),
                instruction=(
                    OrderInstruction
                    .PROTECT_PARTIAL_FILL
                ),
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=Decimal("0"),
                terminal=True,
                requires_reconciliation=True,
                reason=(
                    "The order was cancelled after a "
                    "partial fill. The filled shares "
                    "still require reconciliation and "
                    "protection."
                ),
            )

        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            reason=(
                "The broker reports a cancelled order "
                "with the entire quantity filled."
            ),
        )

    if normalized_state in REJECTED_STATES:
        if filled == 0:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=OrderPhase.REJECTED,
                instruction=(
                    OrderInstruction
                    .RELEASE_UNFILLED_RESERVATION
                ),
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=requested,
                terminal=True,
                requires_reconciliation=False,
                reason=(
                    "The order was rejected without "
                    "receiving a fill."
                ),
            )

        if filled < requested:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=(
                    OrderPhase.PARTIALLY_FILLED
                ),
                instruction=(
                    OrderInstruction
                    .PROTECT_PARTIAL_FILL
                ),
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=Decimal("0"),
                terminal=True,
                requires_reconciliation=True,
                reason=(
                    "The broker reports a rejected "
                    "order with a partial fill. The "
                    "filled shares require immediate "
                    "reconciliation and protection."
                ),
            )

        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            reason=(
                "The broker reports a rejected order "
                "with the entire quantity filled."
            ),
        )

    if normalized_state in PENDING_STATES:
        if filled == 0:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=OrderPhase.PENDING,
                instruction=OrderInstruction.WAIT,
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=requested,
                terminal=False,
                requires_reconciliation=False,
                reason=(
                    "The order is active and has not "
                    "received a fill."
                ),
            )

        if filled < requested:
            return OrderStatusDecision(
                broker_state=raw_state,
                normalized_state=normalized_state,
                phase=(
                    OrderPhase.PARTIALLY_FILLED
                ),
                instruction=(
                    OrderInstruction
                    .PROTECT_PARTIAL_FILL
                ),
                requested_quantity=requested,
                filled_quantity=filled,
                remaining_quantity=remaining,
                terminal=False,
                requires_reconciliation=True,
                reason=(
                    "The active order has received a "
                    "partial fill. The actual position "
                    "must be reconciled and protected."
                ),
            )

        return unknown_decision(
            broker_state=raw_state,
            normalized_state=normalized_state,
            requested_quantity=requested,
            filled_quantity=filled,
            remaining_quantity=remaining,
            reason=(
                "The broker reports the full quantity "
                "filled while the textual order state "
                "is still pending."
            ),
        )

    return unknown_decision(
        broker_state=raw_state,
        normalized_state=normalized_state,
        requested_quantity=requested,
        filled_quantity=filled,
        remaining_quantity=remaining,
        reason=(
            "The broker returned an unfamiliar order "
            f"state: {normalized_state!r}."
        ),
    )


def interpret_equity_order(
    order: Any,
) -> OrderStatusDecision:
    """
    Interpret an object such as account_state.EquityOrder.
    """

    return interpret_order_status(
        broker_state=getattr(
            order,
            "state",
            None,
        ),
        requested_quantity=getattr(
            order,
            "quantity",
            None,
        ),
        cumulative_quantity=getattr(
            order,
            "cumulative_quantity",
            None,
        ),
    )


def main() -> None:
    examples = (
        (
            "confirmed",
            "10",
            "0",
        ),
        (
            "partially_filled",
            "10",
            "4",
        ),
        (
            "filled",
            "10",
            "10",
        ),
        (
            "cancelled",
            "10",
            "0",
        ),
        (
            "rejected",
            "10",
            "0",
        ),
        (
            "unexpected_state",
            "10",
            "0",
        ),
    )

    print("\nORDER-STATE INTERPRETER\n")

    for state, quantity, filled in examples:
        decision = interpret_order_status(
            broker_state=state,
            requested_quantity=quantity,
            cumulative_quantity=filled,
        )

        print(
            f"{state:20} -> "
            f"{decision.phase.value:18} "
            f"{decision.instruction.value}"
        )

    print("\nBroker write capability: False")


if __name__ == "__main__":
    main()