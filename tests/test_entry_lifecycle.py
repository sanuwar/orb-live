from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.account_state import EquityOrder
from src.entry_lifecycle import (
    EntryLifecycleAction,
    EntryLifecycleCoordinator,
    decide_entry_lifecycle,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)


def make_pending_trade(
    store: TradeStateStore,
):
    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )

    return store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
    )


def make_order(
    *,
    state: str = "confirmed",
    cumulative_quantity: str = "0",
    quantity: str = "10",
    order_id: str = "entry-1",
    symbol: str = "AAPL",
    side: str = "buy",
    trigger: str = "immediate",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type="limit",
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(
            cumulative_quantity
        ),
        limit_price=Decimal("100"),
        stop_price=None,
        trigger=trigger,
        placed_agent="agentic",
    )


def test_pending_order_waits_without_ledger_change(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    coordinator = EntryLifecycleCoordinator(
        store
    )

    result = coordinator.apply(
        trade_id=trade.trade_id,
        order=make_order(),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.WAIT
    )
    assert result.ledger_changed is False
    assert result.trade_before == result.trade_after
    assert (
        result.trade_after.status
        == TradeStatus.ENTRY_PENDING
    )
    assert store.count_trade_events() == 2


def test_full_fill_requires_average_fill_price(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="filled",
            cumulative_quantity="10",
        ),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert (
        result.decision
        .requires_immediate_protection
        is True
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.RECOVERY_REQUIRED
    )
    assert result.trade_after.filled_quantity == 10
    assert (
        result.trade_after.operator_ack_required
        is True
    )


def test_full_fill_updates_local_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="100.25",
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.MARK_FULL_FILL
    )
    assert (
        result.decision
        .requires_immediate_protection
        is True
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.PROTECTION_PENDING
    )
    assert (
        result.trade_after.filled_quantity
        == 10
    )
    assert (
        result.trade_after.average_entry_price
        == Decimal("100.25")
    )
    assert store.count_trade_events() == 3


def test_cancelled_unfilled_order_releases_reservation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="cancelled",
            cumulative_quantity="0",
        ),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.CANCEL_UNFILLED
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.CANCELLED
    )
    assert (
        result.trade_after.close_reason
        == "broker_entry_cancelled"
    )
    assert store.list_active_trades() == ()


def test_rejected_unfilled_order_releases_reservation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="rejected",
            cumulative_quantity="0",
        ),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.CANCEL_UNFILLED
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.CANCELLED
    )
    assert (
        result.trade_after.close_reason
        == "broker_entry_rejected"
    )


def test_partial_fill_requires_protection_without_transition(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="partially_filled",
            cumulative_quantity="4",
        ),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction
        .PROTECT_PARTIAL_FILL
    )
    assert (
        result.decision
        .requires_immediate_protection
        is True
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.RECOVERY_REQUIRED
    )
    assert (
        result.trade_after.filled_quantity
        == 4
    )
    assert (
        result.decision.order_status
        .filled_quantity
        == Decimal("4")
    )


def test_order_id_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(
            order_id="different-order"
        ),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert "order ID" in decision.reason


def test_symbol_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(
            symbol="MSFT"
        ),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert "symbol" in decision.reason.lower()


def test_non_buy_entry_order_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(
            side="sell"
        ),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert "buy order" in decision.reason


def test_quantity_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(
            quantity="9"
        ),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert "quantity" in decision.reason.lower()


def test_unexpected_trigger_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(
            trigger="stop"
        ),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert "trigger" in decision.reason.lower()


def test_non_pending_local_trade_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )

    decision = decide_entry_lifecycle(
        trade=trade,
        order=make_order(),
    )

    assert (
        decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert (
        "not in the entry-pending state"
        in decision.reason
    )


def test_unknown_broker_state_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_pending_trade(store)

    result = EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_order(
            state="unexpected_state"
        ),
    )

    assert (
        result.decision.action
        == EntryLifecycleAction.FAIL_CLOSED
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.RECOVERY_REQUIRED
    )