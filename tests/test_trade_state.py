from __future__ import annotations

import sqlite3
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from tests.snapshot_helpers import (
    apply_broker_snapshot,
    make_broker_snapshot,
)

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.trade_state import (
    DuplicateEntryError,
    ExitRecoveryAction,
    ExitRecoveryStep,
    InvalidTransitionError,
    TradeStateError,
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)
TIMESTAMP = datetime(
    2026,
    7,
    17,
    14,
    0,
    tzinfo=timezone.utc,
)


def reserve_trade(
    store: TradeStateStore,
    *,
    symbol: str = "AAPL",
    session_date: date = SESSION_DATE,
    quantity: int = 10,
    entry: str = "100",
    stop: str = "99",
    target: str = "102",
):
    return store.reserve_entry(
        strategy="orb_v0",
        symbol=symbol,
        session_date=session_date,
        quantity=quantity,
        planned_entry=Decimal(entry),
        initial_stop=Decimal(stop),
        target_price=Decimal(target),
        occurred_at=TIMESTAMP,
    )


def make_open_trade(
    store: TradeStateStore,
    *,
    symbol: str = "AAPL",
    session_date: date = SESSION_DATE,
    quantity: int = 10,
):
    trade = reserve_trade(
        store,
        symbol=symbol,
        session_date=session_date,
        quantity=quantity,
    )

    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"entry-{symbol}",
        occurred_at=TIMESTAMP,
    )

    return store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=quantity,
        average_entry_price=Decimal("100"),
        occurred_at=TIMESTAMP,
    )


def make_account_state(
    *,
    positions: tuple[
        EquityPosition, ...
    ] = (),
    orders: tuple[
        EquityOrder, ...
    ] = (),
) -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number="TEST12345678",
            masked_account_number="••••5678",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("3000"),
            equity_value=Decimal("3000"),
            cash=Decimal("3000"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("3000"),
            unleveraged_buying_power=Decimal(
                "3000"
            ),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=positions,
        open_orders=orders,
    )


def make_position(
    symbol: str,
    quantity: str = "10",
) -> EquityPosition:
    return EquityPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        intraday_quantity=Decimal(quantity),
        average_buy_price=Decimal("100"),
        shares_available_for_sells=Decimal(
            quantity
        ),
        shares_held_for_sells=Decimal("0"),
        position_type="long",
    )


def make_stop_order(
    *,
    order_id: str,
    symbol: str,
    quantity: str = "10",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side="sell",
        order_type="stop_market",
        state="confirmed",
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal("0"),
        limit_price=None,
        stop_price=Decimal("99"),
        trigger="stop",
        placed_agent="agentic",
    )


def make_entry_order(
    *,
    order_id: str,
    symbol: str,
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side="buy",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("1"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("100"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )


def test_reserve_entry_creates_persistent_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(
        store,
        symbol="aapl",
    )

    assert trade.symbol == "AAPL"
    assert trade.strategy == "orb_v0"
    assert trade.status == TradeStatus.RESERVED
    assert trade.quantity == 10
    assert trade.filled_quantity == 0
    assert trade.version == 1

    assert store.get_trade(
        trade.trade_id
    ) == trade

    assert store.list_active_trades() == (
        trade,
    )

    assert store.count_trade_events() == 1


def test_invalid_long_trade_plan_is_rejected(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    with pytest.raises(
        TradeStateError,
        match="stop must be below",
    ):
        store.reserve_entry(
            strategy="orb_v0",
            symbol="AAPL",
            session_date=SESSION_DATE,
            quantity=10,
            planned_entry=Decimal("100"),
            initial_stop=Decimal("101"),
            target_price=Decimal("102"),
        )

    assert store.count_trade_events() == 0


def test_duplicate_symbol_entry_is_blocked(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    reserve_trade(store)

    with pytest.raises(
        DuplicateEntryError,
        match="already reserved",
    ):
        reserve_trade(store)

    assert store.count_trade_events() == 1


def test_complete_trade_lifecycle_and_pnl(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(store)

    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
        occurred_at=TIMESTAMP,
    )

    assert (
        trade.status
        == TradeStatus.ENTRY_PENDING
    )
    assert trade.entry_order_id == "entry-1"

    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal(
            "100.10"
        ),
        occurred_at=TIMESTAMP,
    )

    assert (
        trade.status
        == TradeStatus.PROTECTION_PENDING
    )

    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
        occurred_at=TIMESTAMP,
    )

    assert trade.status == TradeStatus.STOP_LINKED
    assert trade.protected_quantity == 0
    assert store.is_entry_halted("orb_v0")

    apply_broker_snapshot(
        store,
        strategy="orb_v0",
        account_state=make_account_state(
            positions=(make_position("AAPL"),),
            orders=(
                make_stop_order(
                    order_id="stop-1",
                    symbol="AAPL",
                ),
            ),
        ),
    )
    trade = store.get_trade(trade.trade_id)

    assert (
        trade.status
        == TradeStatus.OPEN_PROTECTED
    )
    assert (
        trade.protective_stop_order_id
        == "stop-1"
    )

    trade = store.mark_protective_stop_inactive(
        trade.trade_id,
        reason="Broker confirms stop inactive before separate exit.",
        occurred_at=TIMESTAMP,
    )
    trade = store.begin_exit(
        trade.trade_id,
        broker_order_id="exit-1",
        requested_quantity=10,
        close_reason="profit_target",
        intent_type="profit_target",
        intended_price=Decimal("102"),
        recovery_ref_id="exit-intent-1",
        occurred_at=TIMESTAMP,
    )

    assert (
        trade.status
        == TradeStatus.EXIT_PENDING
    )

    trade, recorded = store.persist_exit_observation(
        trade.trade_id,
        observation_key="exit-full-1",
        broker_order_id="exit-1",
        broker_state="filled",
        requested_quantity=10,
        executed_quantity=10,
        average_fill_price=Decimal("102.10"),
        order_fees=Decimal("0"),
        new_status=TradeStatus.EXIT_RECOVERY_REQUIRED,
        required_action=(
            ExitRecoveryAction.RECONCILE_BROKER_STATE
        ),
        last_completed_step=(
            ExitRecoveryStep.FULL_FILL_RECORDED
        ),
        failure_code=None,
        failure_reason=None,
        operator_ack_required=False,
        observed_at=TIMESTAMP,
    )
    assert recorded is True
    assert trade.status == TradeStatus.EXIT_RECOVERY_REQUIRED

    apply_broker_snapshot(
        store,
        strategy="orb_v0",
        account_state=make_account_state(),
    )
    trade = store.get_trade(trade.trade_id)

    assert trade.status == TradeStatus.CLOSED
    assert trade.realized_pnl == Decimal(
        "20.00"
    )
    assert (
        trade.average_exit_price
        == Decimal("102.10")
    )
    assert trade.close_reason == "profit_target"
    assert (
        trade.protective_stop_order_id
        is None
    )

    assert store.list_active_trades() == ()
    assert store.count_trade_events() == 10


def test_partial_entry_fill_fails_closed(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(store)

    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-1",
    )

    with pytest.raises(
        TradeStateError,
        match="Partial entry fills",
    ):
        store.mark_entry_filled(
            trade.trade_id,
            filled_quantity=5,
            average_entry_price=Decimal(
                "100"
            ),
        )

    unchanged = store.get_trade(
        trade.trade_id
    )

    assert (
        unchanged.status
        == TradeStatus.ENTRY_PENDING
    )
    assert unchanged.filled_quantity == 0


def test_closed_trade_cannot_transition_again(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_trade(store)

    trade = store.close_trade(
        trade.trade_id,
        average_exit_price=Decimal("101"),
        reason="manual_test",
    )

    with pytest.raises(
        InvalidTransitionError,
        match="Cannot transition",
    ):
        store.attach_entry_order(
            trade.trade_id,
            broker_order_id="another-entry",
        )


def test_cancel_unfilled_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(store)

    trade = store.cancel_unfilled_trade(
        trade.trade_id,
        reason="entry_window_closed",
    )

    assert (
        trade.status
        == TradeStatus.CANCELLED
    )
    assert (
        trade.close_reason
        == "entry_window_closed"
    )
    assert store.list_active_trades() == ()

    assert (
        store.entry_count_for_day(
            strategy="orb_v0",
            session_date=SESSION_DATE,
        )
        == 0
    )


def test_filled_trade_cannot_be_cancelled_as_unfilled(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_trade(store)

    with pytest.raises(
        TradeStateError,
        match="filled trade cannot",
    ):
        store.cancel_unfilled_trade(
            trade.trade_id,
            reason="invalid_cancel",
        )


def test_order_events_are_append_only(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = reserve_trade(store)

    store.record_order_event(
        trade_id=trade.trade_id,
        broker_order_id="entry-1",
        role="entry",
        order_state="confirmed",
        side="buy",
        quantity=Decimal("10"),
        filled_quantity=Decimal("0"),
        limit_price=Decimal("100"),
    )

    assert store.count_order_events() == 1

    with pytest.raises(
        sqlite3.IntegrityError,
        match="append-only",
    ):
        with store._connect() as connection:
            connection.execute(
                """
                UPDATE order_events
                SET order_state = 'filled'
                """
            )


def close_test_trade(
    store: TradeStateStore,
    *,
    symbol: str,
    session_date: date,
    exit_price: str,
):
    trade = make_open_trade(
        store,
        symbol=symbol,
        session_date=session_date,
        quantity=1,
    )

    closed = store.close_trade(
        trade.trade_id,
        average_exit_price=Decimal(
            exit_price
        ),
        reason="test_close",
    )
    store.begin_startup_reconciliation(
        "orb_v0"
    )
    store.apply_startup_reconciliation(
        strategy="orb_v0",
        snapshot=make_broker_snapshot(make_account_state()),
    )
    return closed


def test_daily_weekly_pnl_and_current_drawdown(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    monday = date(2026, 7, 13)
    tuesday = date(2026, 7, 14)
    wednesday = date(2026, 7, 15)

    close_test_trade(
        store,
        symbol="AAPL",
        session_date=monday,
        exit_price="110",
    )

    close_test_trade(
        store,
        symbol="MSFT",
        session_date=tuesday,
        exit_price="96",
    )

    close_test_trade(
        store,
        symbol="NVDA",
        session_date=wednesday,
        exit_price="92",
    )

    assert store.daily_pnl(
        tuesday,
        strategy="orb_v0",
    ) == Decimal("-4")

    assert store.weekly_pnl(
        wednesday,
        strategy="orb_v0",
    ) == Decimal("-2")

    assert store.current_drawdown(
        strategy="orb_v0",
    ) == Decimal("12")


def test_reconciliation_accepts_matching_protected_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_trade(store)

    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )

    account_state = make_account_state(
        positions=(
            make_position("AAPL"),
        ),
        orders=(
            make_stop_order(
                order_id="stop-1",
                symbol="AAPL",
            ),
        ),
    )

    apply_broker_snapshot(
        store,
        strategy="orb_v0",
        account_state=account_state,
    )

    assert store.reconcile(
        account_state
    ) == ()


def test_reconciliation_detects_mismatches(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_open_trade(store)

    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="missing-stop",
    )

    account_state = make_account_state(
        positions=(
            make_position(
                "TSLA",
                quantity="1",
            ),
        ),
        orders=(
            make_entry_order(
                order_id="unknown-entry",
                symbol="MSFT",
            ),
        ),
    )

    issues = store.reconcile(account_state)

    codes = {
        issue.code
        for issue in issues
    }

    assert "linked_stop_unconfirmed" in codes
    assert "local_position_missing" in codes
    assert "untracked_position" in codes
    assert "untracked_entry_order" in codes

    critical_codes = {
        issue.code
        for issue in issues
        if issue.severity == "critical"
    }

    assert "linked_stop_unconfirmed" in (
        critical_codes
    )
    assert "local_position_missing" in (
        critical_codes
    )
    assert "untracked_position" in (
        critical_codes
    )