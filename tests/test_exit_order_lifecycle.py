from __future__ import annotations

from datetime import date
from decimal import Decimal

from tests.snapshot_helpers import confirm_linked_stop

from src.account_state import EquityOrder
from src.exit_order_lifecycle import (
    ExitOrderAction,
    ExitOrderLifecycleCoordinator,
    decide_exit_order_lifecycle,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)


def make_exit_pending_trade(
    store: TradeStateStore,
    *,
    with_protective_stop: bool = False,
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

    if with_protective_stop:
        trade = store.attach_protective_stop(
            trade.trade_id,
            broker_order_id="stop-1",
        )
        trade = confirm_linked_stop(store, trade)

    return store.begin_exit(
        trade.trade_id,
        broker_order_id="exit-1",
    )


def make_exit_order(
    *,
    state: str = "confirmed",
    cumulative_quantity: str = "0",
    quantity: str = "10",
    order_id: str = "exit-1",
    symbol: str = "AAPL",
    side: str = "sell",
    trigger: str = "immediate",
    order_type: str = "limit",
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
        limit_price=Decimal("102"),
        stop_price=None,
        trigger=trigger,
        placed_agent="agentic",
    )


def test_pending_exit_waits_without_ledger_change(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(),
        close_reason="profit_target",
    )

    assert (
        result.decision.action
        == ExitOrderAction.WAIT
    )
    assert result.ledger_changed is False
    assert result.trade_before == result.trade_after
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )
    assert (
        result.decision.position_may_remain_open
        is True
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is False
    )
    assert store.count_trade_events() == 4


def test_full_exit_requires_average_fill_price(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        close_reason="profit_target",
    )

    assert (
        result.decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert (
        result.decision.position_may_remain_open
        is False
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )


def test_full_exit_requires_close_reason(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="102",
        close_reason="",
    )

    assert (
        result.decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "close reason" in (
        result.decision.reason.lower()
    )
    assert result.ledger_changed is False


def test_full_exit_closes_local_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="102",
        close_reason="profit_target",
    )

    assert (
        result.decision.action
        == ExitOrderAction.CLOSE_FULL_EXIT
    )
    assert result.ledger_changed is True
    assert (
        result.trade_after.status
        == TradeStatus.CLOSED
    )
    assert (
        result.trade_after.average_exit_price
        == Decimal("102")
    )
    assert (
        result.trade_after.realized_pnl
        == Decimal("20")
    )
    assert (
        result.trade_after.close_reason
        == "profit_target"
    )
    assert (
        result.decision.position_may_remain_open
        is False
    )
    assert store.count_trade_events() == 5


def test_partial_exit_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="partially_filled",
            cumulative_quantity="4",
        ),
        close_reason="time_exit",
    )

    assert (
        result.decision.action
        == ExitOrderAction
        .RECONCILE_PARTIAL_FILL
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )
    assert (
        result.decision.position_may_remain_open
        is True
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )
    assert (
        result.decision.order_status
        .filled_quantity
        == Decimal("4")
    )


def test_cancelled_unfilled_exit_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="cancelled",
        ),
        close_reason="time_exit",
    )

    assert (
        result.decision.action
        == ExitOrderAction
        .RECONCILE_UNFILLED_TERMINAL
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )
    assert (
        result.decision.position_may_remain_open
        is True
    )
    assert (
        result.decision
        .requires_immediate_reconciliation
        is True
    )


def test_rejected_unfilled_exit_requires_reconciliation(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="rejected",
        ),
        close_reason="time_exit",
    )

    assert (
        result.decision.action
        == ExitOrderAction
        .RECONCILE_UNFILLED_TERMINAL
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )


def test_exit_order_id_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            order_id="different-exit"
        ),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "order ID" in decision.reason


def test_exit_symbol_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            symbol="MSFT"
        ),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "symbol" in decision.reason.lower()


def test_non_sell_exit_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            side="buy"
        ),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "sell order" in decision.reason


def test_unexpected_exit_trigger_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            trigger="stop"
        ),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "trigger" in decision.reason.lower()


def test_non_limit_exit_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            order_type="market"
        ),
        close_reason="time_exit",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "limit order" in decision.reason


def test_exit_quantity_mismatch_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(
            quantity="9"
        ),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "quantity" in decision.reason.lower()


def test_linked_protective_stop_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(
        store,
        with_protective_stop=True,
    )

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert "protective-stop" in (
        decision.reason.lower()
    )


def test_non_exit_pending_trade_fails_closed(
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

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(),
        close_reason="profit_target",
    )

    assert (
        decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert (
        "not in the exit-pending state"
        in decision.reason
    )


def test_unknown_broker_state_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="unexpected_state"
        ),
        close_reason="profit_target",
    )

    assert (
        result.decision.action
        == ExitOrderAction.FAIL_CLOSED
    )
    assert result.ledger_changed is False
    assert (
        result.trade_after.status
        == TradeStatus.EXIT_PENDING
    )