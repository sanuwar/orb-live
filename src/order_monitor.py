from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from enum import Enum
from typing import Any

try:
    from .entry_lifecycle import (
        EntryLifecycleAction,
        EntryLifecycleCoordinator,
        entry_observation_key,
    )
    from .exit_order_lifecycle import (
        ExitOrderAction,
        ExitOrderLifecycleCoordinator,
    )
    from .order_observer import (
        EquityOrderObserver,
        ObservedEquityOrder,
        OrderObservationError,
    )
    from .protective_stop_lifecycle import (
        ProtectiveStopAction,
        ProtectiveStopLifecycleCoordinator,
    )
    from .trade_state import (
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )
except ImportError:
    from entry_lifecycle import (
        EntryLifecycleAction,
        EntryLifecycleCoordinator,
        entry_observation_key,
    )
    from exit_order_lifecycle import (
        ExitOrderAction,
        ExitOrderLifecycleCoordinator,
    )
    from order_observer import (
        EquityOrderObserver,
        ObservedEquityOrder,
        OrderObservationError,
    )
    from protective_stop_lifecycle import (
        ProtectiveStopAction,
        ProtectiveStopLifecycleCoordinator,
    )
    from trade_state import (
        TradeRecord,
        TradeStateStore,
        TradeStatus,
    )


class MonitoredOrderRole(str, Enum):
    ENTRY = "entry"
    PROTECTIVE_STOP = "protective_stop"
    EXIT = "exit"
    NONE = "none"


class OrderMonitorAction(str, Enum):
    WAIT = "wait"

    ENTRY_FILLED_REQUIRES_PROTECTION = (
        "entry_filled_requires_protection"
    )
    ENTRY_RESERVATION_RELEASED = (
        "entry_reservation_released"
    )

    STOP_FILLED_TRADE_CLOSED = (
        "stop_filled_trade_closed"
    )
    STOP_INACTIVE_REQUIRES_PROTECTION = (
        "stop_inactive_requires_protection"
    )

    EXIT_FILLED_TRADE_CLOSED = (
        "exit_filled_trade_closed"
    )

    RECONCILE_IMMEDIATELY = (
        "reconcile_immediately"
    )
    NO_LINKED_ORDER = "no_linked_order"
    TERMINAL_NO_ACTION = "terminal_no_action"
    FAIL_CLOSED = "fail_closed"


@dataclass(frozen=True)
class OrderMonitorResult:
    role: MonitoredOrderRole
    action: OrderMonitorAction
    trade_before: TradeRecord
    trade_after: TradeRecord
    observed_order: ObservedEquityOrder | None
    ledger_changed: bool
    order_event_recorded: bool
    requires_immediate_protection: bool
    requires_reconciliation: bool
    reason: str

    @property
    def fail_closed(self) -> bool:
        return (
            self.action
            == OrderMonitorAction.FAIL_CLOSED
        )

    @property
    def broker_order_id(self) -> str | None:
        if self.observed_order is None:
            return None

        return self.observed_order.order_id


def fail_closed_result(
    *,
    role: MonitoredOrderRole,
    trade: TradeRecord,
    reason: str,
    observed_order: ObservedEquityOrder | None = None,
    order_event_recorded: bool = False,
    requires_immediate_protection: bool = False,
) -> OrderMonitorResult:
    return OrderMonitorResult(
        role=role,
        action=OrderMonitorAction.FAIL_CLOSED,
        trade_before=trade,
        trade_after=trade,
        observed_order=observed_order,
        ledger_changed=False,
        order_event_recorded=(
            order_event_recorded
        ),
        requires_immediate_protection=(
            requires_immediate_protection
        ),
        requires_reconciliation=True,
        reason=reason,
    )


class ReadOnlyOrderMonitor:
    """
    Retrieves fresh broker status for the order linked
    to one local trade and applies the matching local
    lifecycle coordinator.

    Broker access is read-only. This class cannot review,
    place, replace, or cancel an order.

    Local SQLite state may be updated after confirmed
    broker observations.
    """

    def __init__(
        self,
        trade_store: TradeStateStore,
        *,
        observer: EquityOrderObserver | None = None,
    ):
        self.trade_store = trade_store
        self.observer = (
            observer or EquityOrderObserver()
        )

        self.entry_coordinator = (
            EntryLifecycleCoordinator(
                trade_store
            )
        )

        self.stop_coordinator = (
            ProtectiveStopLifecycleCoordinator(
                trade_store
            )
        )

        self.exit_coordinator = (
            ExitOrderLifecycleCoordinator(
                trade_store
            )
        )

    def _record_observation(
        self,
        *,
        trade_id: str,
        role: MonitoredOrderRole,
        observed: ObservedEquityOrder,
    ) -> bool:
        order = observed.order
        observation_key = entry_observation_key(
            order,
            observed.average_price,
        )

        return self.trade_store.record_order_event(
            trade_id=trade_id,
            broker_order_id=order.order_id,
            role=role.value,
            order_state=order.state,
            side=order.side,
            quantity=order.quantity,
            filled_quantity=(
                order.cumulative_quantity
            ),
            limit_price=order.limit_price,
            stop_price=order.stop_price,
            observation_key=observation_key,
            details={
                "average_price":
                    observed.average_price,
                "fees": observed.fees,
                "execution_count":
                    len(observed.executions),
                "execution_quantity":
                    observed.execution_quantity,
                "execution_fees":
                    observed.execution_fees,
                "market_hours":
                    observed.market_hours,
                "time_in_force":
                    observed.time_in_force,
                "created_at":
                    observed.created_at,
                "last_transaction_at":
                    observed.last_transaction_at,
                "placed_agent":
                    order.placed_agent,
            },
        )

    async def _observe_linked_order(
        self,
        *,
        account_number: str,
        trade: TradeRecord,
        role: MonitoredOrderRole,
        order_id: str,
    ) -> tuple[
        ObservedEquityOrder | None,
        OrderMonitorResult | None,
    ]:
        try:
            observed = await self.observer.observe(
                account_number=account_number,
                order_id=order_id,
            )
        except OrderObservationError as error:
            failed_trade = trade

            if role == MonitoredOrderRole.ENTRY:
                failure_key = hashlib.sha256(
                    (
                        f"{trade.trade_id}:"
                        f"{type(error).__name__}:"
                        f"{error}"
                    ).encode("utf-8")
                ).hexdigest()

                failed_trade, _ = (
                    self.trade_store
                    .halt_entry_recovery(
                        trade.trade_id,
                        reason=(
                            "Fresh broker order status "
                            f"could not be retrieved: "
                            f"{error}"
                        ),
                        observation_key=failure_key,
                        observed_at=datetime.now(
                            timezone.utc
                        ),
                    )
                )

            return (
                None,
                fail_closed_result(
                    role=role,
                    trade=failed_trade,
                    reason=(
                        "Fresh broker order status "
                        f"could not be retrieved: {error}"
                    ),
                    requires_immediate_protection=(
                        trade.status
                        in {
                            TradeStatus
                            .OPEN_UNPROTECTED,
                            TradeStatus
                            .OPEN_PROTECTED,
                            TradeStatus
                            .EXIT_PENDING,
                        }
                    ),
                ),
            )

        self._record_observation(
            trade_id=trade.trade_id,
            role=role,
            observed=observed,
        )

        if not observed.is_agentic_order:
            return (
                observed,
                fail_closed_result(
                    role=role,
                    trade=trade,
                    observed_order=observed,
                    order_event_recorded=True,
                    reason=(
                        "The linked broker order was "
                        "not placed through the agentic "
                        "MCP channel."
                    ),
                    requires_immediate_protection=(
                        observed.has_any_fill
                    ),
                ),
            )

        return observed, None

    async def monitor_trade(
        self,
        *,
        account_number: str,
        trade_id: str,
        close_reason: str | None = None,
    ) -> OrderMonitorResult:
        account_number = account_number.strip()

        if not account_number:
            trade = self.trade_store.get_trade(
                trade_id
            )

            return fail_closed_result(
                role=MonitoredOrderRole.NONE,
                trade=trade,
                reason=(
                    "Account number is required for "
                    "fresh broker order observation."
                ),
            )

        trade = self.trade_store.get_trade(
            trade_id
        )

        if trade.status == TradeStatus.RESERVED:
            return OrderMonitorResult(
                role=MonitoredOrderRole.NONE,
                action=(
                    OrderMonitorAction
                    .NO_LINKED_ORDER
                ),
                trade_before=trade,
                trade_after=trade,
                observed_order=None,
                ledger_changed=False,
                order_event_recorded=False,
                requires_immediate_protection=False,
                requires_reconciliation=False,
                reason=(
                    "The entry is reserved locally, but "
                    "no broker entry order has been "
                    "attached yet."
                ),
            )

        if trade.status in {
            TradeStatus.PROTECTION_PENDING,
            TradeStatus.OPEN_UNPROTECTED,
        }:
            return OrderMonitorResult(
                role=MonitoredOrderRole.NONE,
                action=(
                    OrderMonitorAction
                    .NO_LINKED_ORDER
                ),
                trade_before=trade,
                trade_after=trade,
                observed_order=None,
                ledger_changed=False,
                order_event_recorded=False,
                requires_immediate_protection=True,
                requires_reconciliation=True,
                reason=(
                    "The trade is open without a linked "
                    "protective-stop order."
                ),
            )

        if trade.status in {
            TradeStatus.CLOSED,
            TradeStatus.CANCELLED,
        }:
            return OrderMonitorResult(
                role=MonitoredOrderRole.NONE,
                action=(
                    OrderMonitorAction
                    .TERMINAL_NO_ACTION
                ),
                trade_before=trade,
                trade_after=trade,
                observed_order=None,
                ledger_changed=False,
                order_event_recorded=False,
                requires_immediate_protection=False,
                requires_reconciliation=False,
                reason=(
                    "The local trade is terminal and "
                    "requires no order monitoring."
                ),
            )

        if trade.status == TradeStatus.ERROR:
            return fail_closed_result(
                role=MonitoredOrderRole.NONE,
                trade=trade,
                reason=(
                    "The local trade is in an error "
                    "state and requires reconciliation."
                ),
                requires_immediate_protection=(
                    trade.filled_quantity > 0
                ),
            )

        if trade.status in {
            TradeStatus.ENTRY_PENDING,
            TradeStatus.ENTRY_PARTIALLY_FILLED,
            TradeStatus.RECOVERY_REQUIRED,
        }:
            role = MonitoredOrderRole.ENTRY
            order_id = trade.entry_order_id

            if not order_id:
                return fail_closed_result(
                    role=role,
                    trade=trade,
                    reason=(
                        "The entry-pending trade has no "
                        "linked broker entry-order ID."
                    ),
                )

            observed, failure = (
                await self._observe_linked_order(
                    account_number=account_number,
                    trade=trade,
                    role=role,
                    order_id=order_id,
                )
            )

            if failure is not None:
                return failure

            assert observed is not None

            lifecycle = (
                self.entry_coordinator.apply(
                    trade_id=trade_id,
                    order=observed.order,
                    average_fill_price=(
                        observed.average_price
                    ),
                    observation_at=(
                        observed
                        .last_transaction_at
                        or observed.created_at
                        or datetime.now(
                            timezone.utc
                        )
                    ),
                    observation_key=(
                        entry_observation_key(
                            observed.order,
                            observed.average_price,
                        )
                    ),
                )
            )

            if (
                lifecycle.decision.action
                == EntryLifecycleAction.WAIT
            ):
                action = OrderMonitorAction.WAIT
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                == EntryLifecycleAction
                .MARK_FULL_FILL
            ):
                action = (
                    OrderMonitorAction
                    .ENTRY_FILLED_REQUIRES_PROTECTION
                )
                protection = True
                reconciliation = False

            elif (
                lifecycle.decision.action
                == EntryLifecycleAction
                .CANCEL_UNFILLED
            ):
                action = (
                    OrderMonitorAction
                    .ENTRY_RESERVATION_RELEASED
                )
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                == EntryLifecycleAction
                .PROTECT_PARTIAL_FILL
            ):
                action = (
                    OrderMonitorAction
                    .RECONCILE_IMMEDIATELY
                )
                protection = True
                reconciliation = True

            else:
                action = (
                    OrderMonitorAction.FAIL_CLOSED
                )
                protection = (
                    lifecycle.decision
                    .requires_immediate_protection
                )
                reconciliation = True

            return OrderMonitorResult(
                role=role,
                action=action,
                trade_before=(
                    lifecycle.trade_before
                ),
                trade_after=(
                    lifecycle.trade_after
                ),
                observed_order=observed,
                ledger_changed=(
                    lifecycle.ledger_changed
                ),
                order_event_recorded=True,
                requires_immediate_protection=(
                    protection
                ),
                requires_reconciliation=(
                    reconciliation
                ),
                reason=(
                    lifecycle.decision.reason
                ),
            )

        if (
            trade.status
            == TradeStatus.OPEN_PROTECTED
        ):
            role = (
                MonitoredOrderRole
                .PROTECTIVE_STOP
            )

            order_id = (
                trade.protective_stop_order_id
            )

            if not order_id:
                return fail_closed_result(
                    role=role,
                    trade=trade,
                    reason=(
                        "The open-protected trade has no "
                        "linked protective-stop ID."
                    ),
                    requires_immediate_protection=True,
                )

            observed, failure = (
                await self._observe_linked_order(
                    account_number=account_number,
                    trade=trade,
                    role=role,
                    order_id=order_id,
                )
            )

            if failure is not None:
                return failure

            assert observed is not None

            lifecycle = (
                self.stop_coordinator.apply(
                    trade_id=trade_id,
                    order=observed.order,
                    average_fill_price=(
                        observed.average_price
                    ),
                )
            )

            if (
                lifecycle.decision.action
                == ProtectiveStopAction.WAIT
            ):
                action = OrderMonitorAction.WAIT
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                == ProtectiveStopAction
                .CLOSE_FULL_STOP_FILL
            ):
                action = (
                    OrderMonitorAction
                    .STOP_FILLED_TRADE_CLOSED
                )
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                == ProtectiveStopAction
                .MARK_STOP_INACTIVE
            ):
                action = (
                    OrderMonitorAction
                    .STOP_INACTIVE_REQUIRES_PROTECTION
                )
                protection = True
                reconciliation = True

            elif (
                lifecycle.decision.action
                == ProtectiveStopAction
                .RECONCILE_PARTIAL_FILL
            ):
                action = (
                    OrderMonitorAction
                    .RECONCILE_IMMEDIATELY
                )
                protection = True
                reconciliation = True

            else:
                action = (
                    OrderMonitorAction.FAIL_CLOSED
                )
                protection = (
                    lifecycle.decision
                    .position_may_be_unprotected
                )
                reconciliation = True

            return OrderMonitorResult(
                role=role,
                action=action,
                trade_before=(
                    lifecycle.trade_before
                ),
                trade_after=(
                    lifecycle.trade_after
                ),
                observed_order=observed,
                ledger_changed=(
                    lifecycle.ledger_changed
                ),
                order_event_recorded=True,
                requires_immediate_protection=(
                    protection
                ),
                requires_reconciliation=(
                    reconciliation
                ),
                reason=(
                    lifecycle.decision.reason
                ),
            )

        if (
            trade.status
            == TradeStatus.EXIT_PENDING
        ):
            role = MonitoredOrderRole.EXIT
            order_id = trade.exit_order_id

            if not order_id:
                return fail_closed_result(
                    role=role,
                    trade=trade,
                    reason=(
                        "The exit-pending trade has no "
                        "linked broker exit-order ID."
                    ),
                    requires_immediate_protection=True,
                )

            observed, failure = (
                await self._observe_linked_order(
                    account_number=account_number,
                    trade=trade,
                    role=role,
                    order_id=order_id,
                )
            )

            if failure is not None:
                return failure

            assert observed is not None

            lifecycle = (
                self.exit_coordinator.apply(
                    trade_id=trade_id,
                    order=observed.order,
                    average_fill_price=(
                        observed.average_price
                    ),
                    close_reason=close_reason,
                )
            )

            if (
                lifecycle.decision.action
                == ExitOrderAction.WAIT
            ):
                action = OrderMonitorAction.WAIT
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                == ExitOrderAction.CLOSE_FULL_EXIT
            ):
                action = (
                    OrderMonitorAction
                    .EXIT_FILLED_TRADE_CLOSED
                )
                protection = False
                reconciliation = False

            elif (
                lifecycle.decision.action
                in {
                    ExitOrderAction
                    .RECONCILE_PARTIAL_FILL,
                    ExitOrderAction
                    .RECONCILE_UNFILLED_TERMINAL,
                }
            ):
                action = (
                    OrderMonitorAction
                    .RECONCILE_IMMEDIATELY
                )
                protection = (
                    lifecycle.decision
                    .position_may_remain_open
                )
                reconciliation = True

            else:
                action = (
                    OrderMonitorAction.FAIL_CLOSED
                )
                protection = (
                    lifecycle.decision
                    .position_may_remain_open
                )
                reconciliation = True

            return OrderMonitorResult(
                role=role,
                action=action,
                trade_before=(
                    lifecycle.trade_before
                ),
                trade_after=(
                    lifecycle.trade_after
                ),
                observed_order=observed,
                ledger_changed=(
                    lifecycle.ledger_changed
                ),
                order_event_recorded=True,
                requires_immediate_protection=(
                    protection
                ),
                requires_reconciliation=(
                    reconciliation
                ),
                reason=(
                    lifecycle.decision.reason
                ),
            )

        return fail_closed_result(
            role=MonitoredOrderRole.NONE,
            trade=trade,
            reason=(
                "The local trade status is not "
                "supported by the order monitor."
            ),
            requires_immediate_protection=(
                trade.filled_quantity > 0
            ),
        )


def main() -> None:
    print("\nREAD-ONLY ORDER MONITOR\n")
    print(
        "Entry pending: observe the linked entry "
        "order and apply the entry lifecycle."
    )
    print(
        "Open protected: observe the linked stop "
        "and apply the stop lifecycle."
    )
    print(
        "Exit pending: observe the linked exit and "
        "apply the exit lifecycle."
    )
    print(
        "Partial fills, missing links, unknown states, "
        "and observation failures: fail closed."
    )
    print(
        "Broker write capability: False"
    )


if __name__ == "__main__":
    main()