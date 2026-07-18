from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable

try:
    from .account_state import EquityOrder
    from .dry_run_engine import DryRunPlan
    from .live_safety_gate import SafetyGateDecision
    from .trade_state import (
        TradeRecord,
        TradeStatus,
    )
except ImportError:
    from account_state import EquityOrder
    from dry_run_engine import DryRunPlan
    from live_safety_gate import SafetyGateDecision
    from trade_state import (
        TradeRecord,
        TradeStatus,
    )


class BrokerPlanPurpose(str, Enum):
    ENTRY = "entry"
    PROTECT_FILLED_ENTRY = "protect_filled_entry"
    REPLACE_PROTECTIVE_STOP = (
        "replace_protective_stop"
    )
    EXIT_POSITION = "exit_position"


class BrokerActionKind(str, Enum):
    REVIEW_ENTRY = "review_entry"
    PLACE_ENTRY = "place_entry"

    REVIEW_PROTECTIVE_STOP = (
        "review_protective_stop"
    )
    PLACE_PROTECTIVE_STOP = (
        "place_protective_stop"
    )

    CANCEL_PROTECTIVE_STOP = (
        "cancel_protective_stop"
    )
    CONFIRM_STOP_CANCELLED = (
        "confirm_stop_cancelled"
    )

    REVIEW_EXIT = "review_exit"
    PLACE_EXIT = "place_exit"


@dataclass(frozen=True)
class PlanBlocker:
    code: str
    message: str


@dataclass(frozen=True)
class PlannedBrokerAction:
    sequence: int
    kind: BrokerActionKind
    description: str
    payload: dict[str, Any] | None = None
    requires_broker_confirmation: bool = True
    depends_on_sequence: int | None = None
    requires_review_ref_id: bool = False


@dataclass(frozen=True)
class BrokerActionPlan:
    purpose: BrokerPlanPurpose
    allowed: bool
    actions: tuple[PlannedBrokerAction, ...]
    blockers: tuple[PlanBlocker, ...]

    @property
    def reason(self) -> str:
        if self.allowed:
            return (
                "The broker action sequence is valid."
            )

        return "; ".join(
            blocker.message
            for blocker in self.blockers
        )

    def has_blocker(
        self,
        code: str,
    ) -> bool:
        return any(
            blocker.code == code
            for blocker in self.blockers
        )

    def action_kinds(
        self,
    ) -> tuple[BrokerActionKind, ...]:
        return tuple(
            action.kind
            for action in self.actions
        )


def decimal_value(
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


def normalized_symbol(
    payload: dict[str, Any],
) -> str:
    return str(
        payload.get("symbol", "")
    ).strip().upper()


def payload_quantity(
    payload: dict[str, Any],
) -> Decimal | None:
    return decimal_value(
        payload.get("quantity")
    )


def open_sell_orders_for_symbol(
    orders: Iterable[EquityOrder],
    symbol: str,
) -> tuple[EquityOrder, ...]:
    normalized = symbol.strip().upper()

    return tuple(
        order
        for order in orders
        if order.side.lower() == "sell"
        and order.symbol.upper() == normalized
    )


def validate_order_payload(
    payload: Any,
    *,
    expected_symbol: str,
    expected_side: str,
    expected_type: str,
    expected_quantity: int,
    label: str,
) -> tuple[PlanBlocker, ...]:
    blockers: list[PlanBlocker] = []

    if not isinstance(payload, dict):
        return (
            PlanBlocker(
                code=f"missing_{label}_payload",
                message=(
                    f"The {label} payload is missing."
                ),
            ),
        )

    if (
        normalized_symbol(payload)
        != expected_symbol.upper()
    ):
        blockers.append(
            PlanBlocker(
                code=f"{label}_symbol_mismatch",
                message=(
                    f"The {label} payload symbol does "
                    "not match the local trade."
                ),
            )
        )

    if (
        str(payload.get("side", "")).lower()
        != expected_side
    ):
        blockers.append(
            PlanBlocker(
                code=f"invalid_{label}_side",
                message=(
                    f"The {label} payload has an "
                    "unexpected order side."
                ),
            )
        )

    if (
        str(payload.get("type", "")).lower()
        != expected_type
    ):
        blockers.append(
            PlanBlocker(
                code=f"invalid_{label}_type",
                message=(
                    f"The {label} payload has an "
                    "unexpected order type."
                ),
            )
        )

    quantity = payload_quantity(payload)

    if quantity != Decimal(
        expected_quantity
    ):
        blockers.append(
            PlanBlocker(
                code=f"{label}_quantity_mismatch",
                message=(
                    f"The {label} payload quantity "
                    "does not match the expected "
                    "position quantity."
                ),
            )
        )

    return tuple(blockers)


class BrokerActionPlanner:
    """
    Produces ordered broker-action instructions.

    The planner contains no Robinhood client and cannot
    review, place, replace, or cancel an order.
    """

    @staticmethod
    def plan_entry(
        *,
        plan: DryRunPlan,
        safety_decision: SafetyGateDecision,
    ) -> BrokerActionPlan:
        blockers: list[PlanBlocker] = []

        if not safety_decision.allowed:
            blockers.append(
                PlanBlocker(
                    code="safety_gate_blocked",
                    message=(
                        "The live safety gate did not "
                        "approve the entry."
                    ),
                )
            )

        if not plan.approved:
            blockers.append(
                PlanBlocker(
                    code="entry_plan_not_approved",
                    message=(
                        "The dry-run entry plan is not "
                        "approved."
                    ),
                )
            )

        if not plan.symbol:
            blockers.append(
                PlanBlocker(
                    code="missing_entry_symbol",
                    message=(
                        "The entry plan has no symbol."
                    ),
                )
            )

        if plan.shares <= 0:
            blockers.append(
                PlanBlocker(
                    code="invalid_entry_quantity",
                    message=(
                        "The planned entry quantity is "
                        "not positive."
                    ),
                )
            )

        blockers.extend(
            validate_order_payload(
                plan.entry_review_payload,
                expected_symbol=plan.symbol,
                expected_side="buy",
                expected_type="limit",
                expected_quantity=plan.shares,
                label="entry_review",
            )
        )

        blockers.extend(
            validate_order_payload(
                plan.entry_placement_payload,
                expected_symbol=plan.symbol,
                expected_side="buy",
                expected_type="limit",
                expected_quantity=plan.shares,
                label="entry_placement",
            )
        )

        if blockers:
            return BrokerActionPlan(
                purpose=BrokerPlanPurpose.ENTRY,
                allowed=False,
                actions=(),
                blockers=tuple(blockers),
            )

        actions = (
            PlannedBrokerAction(
                sequence=1,
                kind=(
                    BrokerActionKind.REVIEW_ENTRY
                ),
                description=(
                    "Submit the entry payload for "
                    "broker review."
                ),
                payload=dict(
                    plan.entry_review_payload
                ),
                requires_broker_confirmation=True,
            ),
            PlannedBrokerAction(
                sequence=2,
                kind=(
                    BrokerActionKind.PLACE_ENTRY
                ),
                description=(
                    "Place the entry only with the "
                    "ref_id returned by the successful "
                    "entry review."
                ),
                payload=dict(
                    plan.entry_placement_payload
                ),
                requires_broker_confirmation=True,
                depends_on_sequence=1,
                requires_review_ref_id=True,
            ),
        )

        return BrokerActionPlan(
            purpose=BrokerPlanPurpose.ENTRY,
            allowed=True,
            actions=actions,
            blockers=(),
        )

    @staticmethod
    def plan_initial_protection(
        *,
        trade: TradeRecord,
        stop_payload: dict[str, Any] | None,
        open_orders: Iterable[
            EquityOrder
        ] = (),
    ) -> BrokerActionPlan:
        blockers: list[PlanBlocker] = []

        if (
            trade.status
            != TradeStatus.OPEN_UNPROTECTED
        ):
            blockers.append(
                PlanBlocker(
                    code="trade_not_unprotected",
                    message=(
                        "Initial protection can only be "
                        "planned for an open-unprotected "
                        "trade."
                    ),
                )
            )

        if trade.filled_quantity <= 0:
            blockers.append(
                PlanBlocker(
                    code="missing_filled_quantity",
                    message=(
                        "The trade has no confirmed "
                        "filled position quantity."
                    ),
                )
            )

        if trade.protective_stop_order_id:
            blockers.append(
                PlanBlocker(
                    code="stop_already_linked",
                    message=(
                        "The trade already has a linked "
                        "protective-stop order."
                    ),
                )
            )

        existing_sells = (
            open_sell_orders_for_symbol(
                open_orders,
                trade.symbol,
            )
        )

        if existing_sells:
            blockers.append(
                PlanBlocker(
                    code="existing_sell_order",
                    message=(
                        "An open sell order already "
                        "exists for the symbol."
                    ),
                )
            )

        blockers.extend(
            validate_order_payload(
                stop_payload,
                expected_symbol=trade.symbol,
                expected_side="sell",
                expected_type="stop_market",
                expected_quantity=(
                    trade.filled_quantity
                ),
                label="protective_stop",
            )
        )

        if blockers:
            return BrokerActionPlan(
                purpose=(
                    BrokerPlanPurpose
                    .PROTECT_FILLED_ENTRY
                ),
                allowed=False,
                actions=(),
                blockers=tuple(blockers),
            )

        actions = (
            PlannedBrokerAction(
                sequence=1,
                kind=(
                    BrokerActionKind
                    .REVIEW_PROTECTIVE_STOP
                ),
                description=(
                    "Review the protective stop "
                    "immediately after the entry fill."
                ),
                payload=dict(stop_payload),
                requires_broker_confirmation=True,
            ),
            PlannedBrokerAction(
                sequence=2,
                kind=(
                    BrokerActionKind
                    .PLACE_PROTECTIVE_STOP
                ),
                description=(
                    "Place the protective stop using "
                    "the ref_id returned by the "
                    "successful stop review."
                ),
                payload=dict(stop_payload),
                requires_broker_confirmation=True,
                depends_on_sequence=1,
                requires_review_ref_id=True,
            ),
        )

        return BrokerActionPlan(
            purpose=(
                BrokerPlanPurpose
                .PROTECT_FILLED_ENTRY
            ),
            allowed=True,
            actions=actions,
            blockers=(),
        )

    @staticmethod
    def plan_stop_replacement(
        *,
        trade: TradeRecord,
        replacement_stop_payload: (
            dict[str, Any] | None
        ),
        open_orders: Iterable[
            EquityOrder
        ],
    ) -> BrokerActionPlan:
        blockers: list[PlanBlocker] = []

        if (
            trade.status
            != TradeStatus.OPEN_PROTECTED
        ):
            blockers.append(
                PlanBlocker(
                    code="trade_not_protected",
                    message=(
                        "A protective stop can only be "
                        "replaced for an open-protected "
                        "trade."
                    ),
                )
            )

        if not trade.protective_stop_order_id:
            blockers.append(
                PlanBlocker(
                    code="missing_current_stop_id",
                    message=(
                        "The local trade has no current "
                        "protective-stop order ID."
                    ),
                )
            )

        symbol_sells = (
            open_sell_orders_for_symbol(
                open_orders,
                trade.symbol,
            )
        )

        matching_current = tuple(
            order
            for order in symbol_sells
            if (
                order.order_id
                == trade.protective_stop_order_id
            )
        )

        if len(matching_current) != 1:
            blockers.append(
                PlanBlocker(
                    code="current_stop_not_unique",
                    message=(
                        "Exactly one matching current "
                        "protective stop must be open at "
                        "the broker."
                    ),
                )
            )

        if len(symbol_sells) != 1:
            blockers.append(
                PlanBlocker(
                    code="conflicting_sell_orders",
                    message=(
                        "Additional open sell orders "
                        "exist for the symbol."
                    ),
                )
            )

        blockers.extend(
            validate_order_payload(
                replacement_stop_payload,
                expected_symbol=trade.symbol,
                expected_side="sell",
                expected_type="stop_market",
                expected_quantity=(
                    trade.filled_quantity
                ),
                label="replacement_stop",
            )
        )

        if blockers:
            return BrokerActionPlan(
                purpose=(
                    BrokerPlanPurpose
                    .REPLACE_PROTECTIVE_STOP
                ),
                allowed=False,
                actions=(),
                blockers=tuple(blockers),
            )

        actions = (
            PlannedBrokerAction(
                sequence=1,
                kind=(
                    BrokerActionKind
                    .CANCEL_PROTECTIVE_STOP
                ),
                description=(
                    "Request cancellation of the "
                    "current protective stop."
                ),
                payload={
                    "order_id": (
                        trade
                        .protective_stop_order_id
                    )
                },
                requires_broker_confirmation=True,
            ),
            PlannedBrokerAction(
                sequence=2,
                kind=(
                    BrokerActionKind
                    .CONFIRM_STOP_CANCELLED
                ),
                description=(
                    "Confirm from fresh broker state "
                    "that the previous stop is fully "
                    "cancelled and received no fill."
                ),
                payload={
                    "order_id": (
                        trade
                        .protective_stop_order_id
                    )
                },
                requires_broker_confirmation=True,
                depends_on_sequence=1,
            ),
            PlannedBrokerAction(
                sequence=3,
                kind=(
                    BrokerActionKind
                    .REVIEW_PROTECTIVE_STOP
                ),
                description=(
                    "Review the replacement stop only "
                    "after cancellation is confirmed."
                ),
                payload=dict(
                    replacement_stop_payload
                ),
                requires_broker_confirmation=True,
                depends_on_sequence=2,
            ),
            PlannedBrokerAction(
                sequence=4,
                kind=(
                    BrokerActionKind
                    .PLACE_PROTECTIVE_STOP
                ),
                description=(
                    "Place the replacement stop using "
                    "the ref_id returned by its review."
                ),
                payload=dict(
                    replacement_stop_payload
                ),
                requires_broker_confirmation=True,
                depends_on_sequence=3,
                requires_review_ref_id=True,
            ),
        )

        return BrokerActionPlan(
            purpose=(
                BrokerPlanPurpose
                .REPLACE_PROTECTIVE_STOP
            ),
            allowed=True,
            actions=actions,
            blockers=(),
        )

    @staticmethod
    def plan_exit(
        *,
        trade: TradeRecord,
        exit_payload: dict[str, Any] | None,
        open_orders: Iterable[
            EquityOrder
        ],
    ) -> BrokerActionPlan:
        blockers: list[PlanBlocker] = []

        if trade.status not in {
            TradeStatus.OPEN_UNPROTECTED,
            TradeStatus.OPEN_PROTECTED,
        }:
            blockers.append(
                PlanBlocker(
                    code="trade_not_exit_eligible",
                    message=(
                        "A separate exit can only be "
                        "planned for an open trade."
                    ),
                )
            )

        if trade.filled_quantity <= 0:
            blockers.append(
                PlanBlocker(
                    code="missing_exit_quantity",
                    message=(
                        "The trade has no confirmed "
                        "position quantity to exit."
                    ),
                )
            )

        symbol_sells = (
            open_sell_orders_for_symbol(
                open_orders,
                trade.symbol,
            )
        )

        current_stop = tuple(
            order
            for order in symbol_sells
            if (
                trade.protective_stop_order_id
                and order.order_id
                == trade.protective_stop_order_id
            )
        )

        if (
            trade.status
            == TradeStatus.OPEN_PROTECTED
        ):
            if not trade.protective_stop_order_id:
                blockers.append(
                    PlanBlocker(
                        code="missing_linked_stop",
                        message=(
                            "The protected trade has no "
                            "linked protective-stop ID."
                        ),
                    )
                )

            if len(current_stop) != 1:
                blockers.append(
                    PlanBlocker(
                        code="broker_stop_not_unique",
                        message=(
                            "Exactly one matching broker "
                            "protective stop must exist "
                            "before planning a separate "
                            "exit."
                        ),
                    )
                )

            if len(symbol_sells) != 1:
                blockers.append(
                    PlanBlocker(
                        code="conflicting_sell_orders",
                        message=(
                            "Additional open sell orders "
                            "exist for the symbol."
                        ),
                    )
                )

        elif symbol_sells:
            blockers.append(
                PlanBlocker(
                    code="untracked_sell_order",
                    message=(
                        "The unprotected trade already "
                        "has an open sell order."
                    ),
                )
            )

        blockers.extend(
            validate_order_payload(
                exit_payload,
                expected_symbol=trade.symbol,
                expected_side="sell",
                expected_type="limit",
                expected_quantity=(
                    trade.filled_quantity
                ),
                label="exit",
            )
        )

        if blockers:
            return BrokerActionPlan(
                purpose=(
                    BrokerPlanPurpose.EXIT_POSITION
                ),
                allowed=False,
                actions=(),
                blockers=tuple(blockers),
            )

        actions: list[
            PlannedBrokerAction
        ] = []

        if (
            trade.status
            == TradeStatus.OPEN_PROTECTED
        ):
            actions.extend(
                [
                    PlannedBrokerAction(
                        sequence=1,
                        kind=(
                            BrokerActionKind
                            .CANCEL_PROTECTIVE_STOP
                        ),
                        description=(
                            "Request cancellation of "
                            "the current protective stop "
                            "before submitting a separate "
                            "exit."
                        ),
                        payload={
                            "order_id": (
                                trade
                                .protective_stop_order_id
                            )
                        },
                        requires_broker_confirmation=True,
                    ),
                    PlannedBrokerAction(
                        sequence=2,
                        kind=(
                            BrokerActionKind
                            .CONFIRM_STOP_CANCELLED
                        ),
                        description=(
                            "Confirm from fresh broker "
                            "state that the protective "
                            "stop is cancelled and did "
                            "not fill."
                        ),
                        payload={
                            "order_id": (
                                trade
                                .protective_stop_order_id
                            )
                        },
                        requires_broker_confirmation=True,
                        depends_on_sequence=1,
                    ),
                    PlannedBrokerAction(
                        sequence=3,
                        kind=(
                            BrokerActionKind.REVIEW_EXIT
                        ),
                        description=(
                            "Review the separate exit "
                            "only after stop cancellation "
                            "is confirmed."
                        ),
                        payload=dict(exit_payload),
                        requires_broker_confirmation=True,
                        depends_on_sequence=2,
                    ),
                    PlannedBrokerAction(
                        sequence=4,
                        kind=(
                            BrokerActionKind.PLACE_EXIT
                        ),
                        description=(
                            "Place the exit using the "
                            "ref_id returned by the "
                            "successful exit review."
                        ),
                        payload=dict(exit_payload),
                        requires_broker_confirmation=True,
                        depends_on_sequence=3,
                        requires_review_ref_id=True,
                    ),
                ]
            )

        else:
            actions.extend(
                [
                    PlannedBrokerAction(
                        sequence=1,
                        kind=(
                            BrokerActionKind.REVIEW_EXIT
                        ),
                        description=(
                            "Review the exit for the "
                            "confirmed unprotected "
                            "position."
                        ),
                        payload=dict(exit_payload),
                        requires_broker_confirmation=True,
                    ),
                    PlannedBrokerAction(
                        sequence=2,
                        kind=(
                            BrokerActionKind.PLACE_EXIT
                        ),
                        description=(
                            "Place the exit using the "
                            "ref_id returned by the "
                            "successful exit review."
                        ),
                        payload=dict(exit_payload),
                        requires_broker_confirmation=True,
                        depends_on_sequence=1,
                        requires_review_ref_id=True,
                    ),
                ]
            )

        return BrokerActionPlan(
            purpose=(
                BrokerPlanPurpose.EXIT_POSITION
            ),
            allowed=True,
            actions=tuple(actions),
            blockers=(),
        )


def main() -> None:
    print("\nBROKER ACTION PLANNER\n")
    print(
        "Entry sequence: review, then place using "
        "the returned ref_id."
    )
    print(
        "Protection sequence: review, then place "
        "the protective stop."
    )
    print(
        "Stop replacement: cancel, confirm, review, "
        "then place."
    )
    print(
        "Separate exit: cancel and confirm any "
        "protective stop before review and placement."
    )
    print(
        "Conflicting or duplicate sell orders: blocked."
    )
    print("\nBroker write capability: False")


if __name__ == "__main__":
    main()