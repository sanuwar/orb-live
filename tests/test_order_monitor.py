from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from src.account_state import EquityOrder
from src.order_monitor import (
    MonitoredOrderRole,
    OrderMonitorAction,
    ReadOnlyOrderMonitor,
)
from src.order_observer import (
    ObservedEquityOrder,
    OrderObservationError,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)
ACCOUNT_NUMBER = "TEST12345678"


class FakeObserver:
    def __init__(
        self,
        observed: ObservedEquityOrder | None = None,
        error: Exception | None = None,
    ):
        self.observed = observed
        self.error = error
        self.calls: list[dict[str, str]] = []

    async def observe(
        self,
        *,
        account_number: str,
        order_id: str,
    ) -> ObservedEquityOrder:
        self.calls.append(
            {
                "account_number": account_number,
                "order_id": order_id,
            }
        )

        if self.error is not None:
            raise self.error

        if self.observed is None:
            raise AssertionError(
                "Fake observer has no result."
            )

        return self.observed


def observed_order(
    *,
    order_id: str,
    symbol: str = "AAPL",
    side: str,
    order_type: str,
    state: str,
    quantity: str = "10",
    cumulative_quantity: str = "0",
    average_price: str | None = None,
    trigger: str = "immediate",
    placed_agent: str = "agentic",
    limit_price: str | None = None,
    stop_price: str | None = None,
) -> ObservedEquityOrder:
    return ObservedEquityOrder(
        order=EquityOrder(
            order_id=order_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            state=state,
            quantity=Decimal(quantity),
            cumulative_quantity=Decimal(
                cumulative_quantity
            ),
            limit_price=(
                Decimal(limit_price)
                if limit_price is not None
                else None
            ),
            stop_price=(
                Decimal(stop_price)
                if stop_price is not None
                else None
            ),
            trigger=trigger,
            placed_agent=placed_agent,
        ),
        average_price=(
            Decimal(average_price)
            if average_price is not None
            else None
        ),
        fees=Decimal("0"),
        created_at=None,
        last_transaction_at=None,
        market_hours="regular_hours",
        time_in_force="gfd",
        executions=(),
    )


def reserve_trade(
    store: TradeStateStore,
):
    return store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )


def make_entry_pending_trade(
    store: TradeStateStore,
):
    trade = reserve_trade(store)

    return store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
    )


def make_open_unprotected_trade(
    store: TradeStateStore,
):
    trade = make_entry_pending_trade(store)

    return store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )


def make_open_protected_trade(
    store: TradeStateStore,
):
    trade = make_open_unprotected_trade(store)

    return store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )


def make_exit_pending_trade(
    store: TradeStateStore,
):
    trade = make_open_unprotected_trade(store)

    return store.begin_exit(
        trade.trade_id,
        broker_order_id="exit-1",
    )


@pytest.mark.anyio
async def test_reserved_trade_has_no_linked_order(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(store)
    observer = FakeObserver()

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.NO_LINKED_ORDER
    )
    assert result.role == MonitoredOrderRole.NONE
    assert result.ledger_changed is False
    assert result.requires_reconciliation is False
    assert observer.calls == []


@pytest.mark.anyio
async def test_open_unprotected_trade_requires_protection(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_unprotected_trade(store)
    observer = FakeObserver()

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.NO_LINKED_ORDER
    )
    assert (
        result.requires_immediate_protection
        is True
    )
    assert result.requires_reconciliation is True
    assert result.trade_after.status == (
        TradeStatus.PROTECTION_PENDING
    )
    assert observer.calls == []


@pytest.mark.anyio
async def test_pending_entry_waits(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="entry-1",
            side="buy",
            order_type="limit",
            state="confirmed",
            limit_price="100.10",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert result.role == MonitoredOrderRole.ENTRY
    assert result.action == OrderMonitorAction.WAIT
    assert result.ledger_changed is False
    assert result.order_event_recorded is True
    assert result.requires_reconciliation is False
    assert result.trade_after.status == (
        TradeStatus.ENTRY_PENDING
    )
    assert store.count_order_events() == 1


@pytest.mark.anyio
async def test_full_entry_fill_updates_ledger_and_requires_stop(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="entry-1",
            side="buy",
            order_type="limit",
            state="filled",
            cumulative_quantity="10",
            average_price="100.25",
            limit_price="100.10",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .ENTRY_FILLED_REQUIRES_PROTECTION
    )
    assert result.ledger_changed is True
    assert (
        result.requires_immediate_protection
        is True
    )
    assert result.requires_reconciliation is False
    assert result.trade_after.status == (
        TradeStatus.PROTECTION_PENDING
    )
    assert result.trade_after.average_entry_price == (
        Decimal("100.25")
    )
    assert store.count_order_events() == 1


@pytest.mark.anyio
async def test_cancelled_entry_releases_reservation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="entry-1",
            side="buy",
            order_type="limit",
            state="cancelled",
            limit_price="100.10",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .ENTRY_RESERVATION_RELEASED
    )
    assert result.ledger_changed is True
    assert result.trade_after.status == (
        TradeStatus.CANCELLED
    )
    assert result.requires_reconciliation is False


@pytest.mark.anyio
async def test_partial_entry_fill_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="entry-1",
            side="buy",
            order_type="limit",
            state="partially_filled",
            cumulative_quantity="4",
            average_price="100.20",
            limit_price="100.10",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .RECONCILE_IMMEDIATELY
    )
    assert result.ledger_changed is True
    assert (
        result.requires_immediate_protection
        is True
    )
    assert result.requires_reconciliation is True
    assert result.trade_after.status == (
        TradeStatus.ENTRY_PARTIALLY_FILLED
    )
    assert result.trade_after.filled_quantity == 4


@pytest.mark.anyio
async def test_active_protective_stop_waits(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_protected_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="stop-1",
            side="sell",
            order_type="stop_market",
            state="confirmed",
            trigger="stop",
            stop_price="99",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.role
        == MonitoredOrderRole.PROTECTIVE_STOP
    )
    assert result.action == OrderMonitorAction.WAIT
    assert result.ledger_changed is False
    assert result.trade_after.status == (
        TradeStatus.OPEN_PROTECTED
    )
    assert result.requires_reconciliation is False


@pytest.mark.anyio
async def test_full_stop_fill_closes_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_protected_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="stop-1",
            side="sell",
            order_type="stop_market",
            state="filled",
            cumulative_quantity="10",
            average_price="99",
            trigger="stop",
            stop_price="99",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .STOP_FILLED_TRADE_CLOSED
    )
    assert result.ledger_changed is True
    assert result.trade_after.status == (
        TradeStatus.CLOSED
    )
    assert result.trade_after.realized_pnl == (
        Decimal("-10")
    )
    assert result.requires_reconciliation is False


@pytest.mark.anyio
async def test_cancelled_stop_marks_trade_unprotected(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_protected_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="stop-1",
            side="sell",
            order_type="stop_market",
            state="cancelled",
            trigger="stop",
            stop_price="99",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .STOP_INACTIVE_REQUIRES_PROTECTION
    )
    assert result.ledger_changed is True
    assert result.trade_after.status == (
        TradeStatus.OPEN_UNPROTECTED
    )
    assert (
        result.requires_immediate_protection
        is True
    )
    assert result.requires_reconciliation is True


@pytest.mark.anyio
async def test_partial_stop_fill_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_protected_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="stop-1",
            side="sell",
            order_type="stop_market",
            state="partially_filled",
            cumulative_quantity="4",
            average_price="99",
            trigger="stop",
            stop_price="99",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .RECONCILE_IMMEDIATELY
    )
    assert result.ledger_changed is False
    assert result.requires_reconciliation is True
    assert (
        result.requires_immediate_protection
        is True
    )


@pytest.mark.anyio
async def test_pending_exit_waits(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="exit-1",
            side="sell",
            order_type="limit",
            state="confirmed",
            limit_price="102",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
        close_reason="profit_target",
    )

    assert result.role == MonitoredOrderRole.EXIT
    assert result.action == OrderMonitorAction.WAIT
    assert result.ledger_changed is False
    assert result.trade_after.status == (
        TradeStatus.EXIT_PENDING
    )


@pytest.mark.anyio
async def test_full_exit_fill_closes_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="exit-1",
            side="sell",
            order_type="limit",
            state="filled",
            cumulative_quantity="10",
            average_price="102",
            limit_price="102",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
        close_reason="profit_target",
    )

    assert (
        result.action
        == OrderMonitorAction
        .EXIT_FILLED_TRADE_CLOSED
    )
    assert result.ledger_changed is True
    assert result.trade_after.status == (
        TradeStatus.CLOSED
    )
    assert result.trade_after.realized_pnl == (
        Decimal("20")
    )
    assert result.trade_after.close_reason == (
        "profit_target"
    )


@pytest.mark.anyio
async def test_full_exit_without_close_reason_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="exit-1",
            side="sell",
            order_type="limit",
            state="filled",
            cumulative_quantity="10",
            average_price="102",
            limit_price="102",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.FAIL_CLOSED
    )
    assert result.ledger_changed is False
    assert result.order_event_recorded is True
    assert result.requires_reconciliation is True
    assert result.trade_after.status == (
        TradeStatus.EXIT_PENDING
    )


@pytest.mark.anyio
async def test_non_agentic_order_fails_closed_after_recording(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        observed_order(
            order_id="entry-1",
            side="buy",
            order_type="limit",
            state="confirmed",
            limit_price="100.10",
            placed_agent="user",
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.FAIL_CLOSED
    )
    assert result.order_event_recorded is True
    assert result.ledger_changed is False
    assert result.requires_reconciliation is True
    assert store.count_order_events() == 1


@pytest.mark.anyio
async def test_observation_failure_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)

    observer = FakeObserver(
        error=OrderObservationError(
            "temporary read failure"
        )
    )

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.FAIL_CLOSED
    )
    assert result.observed_order is None
    assert result.order_event_recorded is False
    assert result.ledger_changed is False
    assert result.requires_reconciliation is True
    assert store.count_order_events() == 0


@pytest.mark.anyio
async def test_empty_account_number_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_entry_pending_trade(store)
    observer = FakeObserver()

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number="",
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction.FAIL_CLOSED
    )
    assert result.role == MonitoredOrderRole.NONE
    assert result.requires_reconciliation is True
    assert observer.calls == []


@pytest.mark.anyio
async def test_closed_trade_requires_no_monitoring(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_unprotected_trade(store)

    trade = store.close_trade(
        trade.trade_id,
        average_exit_price=Decimal("101"),
        reason="test_close",
    )

    observer = FakeObserver()

    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number=ACCOUNT_NUMBER,
        trade_id=trade.trade_id,
    )

    assert (
        result.action
        == OrderMonitorAction
        .TERMINAL_NO_ACTION
    )
    assert result.requires_reconciliation is False
    assert result.ledger_changed is False
    assert observer.calls == []