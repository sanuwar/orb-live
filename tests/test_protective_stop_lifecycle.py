from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.snapshot_helpers import confirm_linked_stop

from src.account_state import EquityOrder
from src.protective_stop_lifecycle import (
    ProtectiveStopAction,
    ProtectiveStopLifecycleCoordinator,
    decide_protective_stop_lifecycle,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)


def make_protected_trade(
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

    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
    )

    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )

    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )
    return confirm_linked_stop(store, trade)


def make_stop_order(
    *,
    state: str = "confirmed",
    cumulative_quantity: str = "0",
    quantity: str = "10",
    order_id: str = "stop-1",
    symbol: str = "AAPL",
    side: str = "sell",
    trigger: str = "stop",
    order_type: str = "stop_market",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(
            cumulative_quantity
        ),
        limit_price=None,
        stop_price=Decimal("99"),
        trigger=trigger,
        placed_agent="agentic",
    )


def test_active_stop_waits_without_ledger_change(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction.WAIT
    )
    assert result.ledger_changed is False
    assert result.trade_before == result.trade_after
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_PROTECTED
    )
    assert (
        result.decision.position_may_be_unprotected
        is False
    )
    assert store.count_trade_events() == 5


def test_full_stop_fill_requires_average_price(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="filled",
            cumulative_quantity="10",
        ),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_PROTECTED
    )


def test_full_stop_fill_closes_local_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="99",
    )

    assert (
        result.decision.action
        == ProtectiveStopAction
        .CLOSE_FULL_STOP_FILL
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.CLOSED
    )
    assert (
        result.trade_after.average_exit_price
        == Decimal("99")
    )
    assert (
        result.trade_after.realized_pnl
        == Decimal("-10")
    )
    assert (
        result.trade_after.close_reason
        == "protective_stop"
    )
    assert (
        result.trade_after
        .protective_stop_order_id
        is None
    )
    assert store.count_trade_events() == 6


def test_cancelled_stop_marks_trade_unprotected(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="cancelled",
        ),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction
        .MARK_STOP_INACTIVE
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_UNPROTECTED
    )
    assert (
        result.trade_after
        .protective_stop_order_id
        is None
    )
    assert (
        result.decision.position_may_be_unprotected
        is True
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )


def test_rejected_stop_marks_trade_unprotected(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="rejected",
        ),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction
        .MARK_STOP_INACTIVE
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_UNPROTECTED
    )


def test_partial_stop_fill_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="partially_filled",
            cumulative_quantity="4",
        ),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction
        .RECONCILE_PARTIAL_FILL
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )
    assert (
        result.decision.position_may_be_unprotected
        is True
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_PROTECTED
    )
    assert (
        result.decision.order_status
        .filled_quantity
        == Decimal("4")
    )


def test_stop_order_id_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            order_id="different-stop"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "order ID" in decision.reason


def test_stop_symbol_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            symbol="MSFT"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "symbol" in decision.reason.lower()


def test_non_sell_stop_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            side="buy"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "sell order" in decision.reason


def test_non_stop_trigger_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            trigger="immediate"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "stop trigger" in decision.reason


def test_non_stop_market_order_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            order_type="stop_limit"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "stop-market" in decision.reason


def test_stop_quantity_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(
            quantity="9"
        ),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert "quantity" in decision.reason.lower()


def test_non_protected_local_trade_fails_closed(
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

    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
    )

    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )

    decision = decide_protective_stop_lifecycle(
        trade=trade,
        order=make_stop_order(),
    )

    assert (
        decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert (
        "not in the open-protected state"
        in decision.reason
    )


def test_unknown_broker_state_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_stop_order(
            state="unexpected_state"
        ),
    )

    assert (
        result.decision.action
        == ProtectiveStopAction.FAIL_CLOSED
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.OPEN_PROTECTED
    )