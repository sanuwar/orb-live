from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.entry_lifecycle import (
    EntryLifecycleAction,
    EntryLifecycleCoordinator,
)
from src.order_monitor import (
    OrderMonitorAction,
    ReadOnlyOrderMonitor,
)
from src.order_observer import OrderNotFoundError
from src.trade_state import (
    EntryHaltedError,
    EntryRecoveryAction,
    TradeStateStore,
    TradeStatus,
)


STRATEGY = "orb_long_v0"
OTHER_STRATEGY = "other_strategy"
SESSION_DATE = date(2026, 7, 17)
OBSERVED_AT = datetime(
    2026,
    7,
    17,
    14,
    0,
    tzinfo=timezone.utc,
)


def make_store(tmp_path) -> TradeStateStore:
    path = tmp_path / "entry_recovery.sqlite"
    store = TradeStateStore(path)
    assert store.db_path == path
    return store


def make_pending_trade(
    store: TradeStateStore,
    *,
    strategy: str = STRATEGY,
    symbol: str = "AAPL",
):
    trade = store.reserve_entry(
        strategy=strategy,
        symbol=symbol,
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=OBSERVED_AT,
    )

    return store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"fake-entry-{symbol}",
        occurred_at=OBSERVED_AT,
    )


def make_order(
    *,
    state: str,
    cumulative_quantity: str,
    symbol: str = "AAPL",
    quantity: str = "10",
) -> EquityOrder:
    return EquityOrder(
        order_id=f"fake-entry-{symbol}",
        symbol=symbol,
        side="buy",
        order_type="limit",
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(
            cumulative_quantity
        ),
        limit_price=Decimal("100.10"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )


def apply_entry(
    store: TradeStateStore,
    trade_id: str,
    *,
    state: str,
    cumulative_quantity: str,
    average_price: str | None,
    observed_at: datetime = OBSERVED_AT,
):
    return EntryLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade_id,
        order=make_order(
            state=state,
            cumulative_quantity=(
                cumulative_quantity
            ),
            symbol=(
                store.get_trade(
                    trade_id
                ).symbol
            ),
        ),
        average_fill_price=average_price,
        observation_at=observed_at,
    )


def make_position(
    symbol: str,
    quantity: str,
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


def make_stop(
    symbol: str,
    quantity: str,
) -> EquityOrder:
    return EquityOrder(
        order_id=f"fake-stop-{symbol}",
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


def make_account_state(
    *,
    positions=(),
    orders=(),
) -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number="FAKE",
            masked_account_number="FAKE",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("0"),
            equity_value=Decimal("0"),
            cash=Decimal("0"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("0"),
            unleveraged_buying_power=Decimal("0"),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=tuple(positions),
        open_orders=tuple(orders),
    )


def test_partial_entry_fill_persists_quantities_and_action(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    persisted = result.trade_after

    assert result.ledger_changed is True
    assert persisted.status == (
        TradeStatus.ENTRY_PARTIALLY_FILLED
    )
    assert persisted.filled_quantity == 4
    assert persisted.remaining_entry_quantity == 6
    assert persisted.protected_quantity == 0
    assert persisted.unprotected_quantity == 4
    assert persisted.average_entry_price == Decimal(
        "100.20"
    )
    assert persisted.initial_stop == Decimal("99")
    assert persisted.entry_order_id is not None
    assert persisted.required_recovery_action == (
        EntryRecoveryAction
        .CANCEL_REMAINDER_THEN_PROTECT
    )
    assert persisted.recovery_ref_id
    assert persisted.recovery_attempt_count == 0
    assert store.is_entry_halted(STRATEGY)


def test_full_entry_fill_is_protection_pending(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state="filled",
        cumulative_quantity="10",
        average_price="100.25",
    )

    assert result.trade_after.status == (
        TradeStatus.PROTECTION_PENDING
    )
    assert result.trade_after.unprotected_quantity == 10
    assert result.trade_after.required_recovery_action == (
        EntryRecoveryAction.PROTECT_FILLED_QUANTITY
    )
    assert store.is_entry_halted(STRATEGY)


def test_cancelled_unfilled_entry_releases_reservation(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state="cancelled",
        cumulative_quantity="0",
        average_price=None,
    )

    assert result.decision.action == (
        EntryLifecycleAction.CANCEL_UNFILLED
    )
    assert result.trade_after.status == (
        TradeStatus.CANCELLED
    )
    assert not store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    "terminal_state",
    ["cancelled", "rejected"],
)
def test_terminal_partial_entry_requires_protection(
    tmp_path,
    terminal_state,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state=terminal_state,
        cumulative_quantity="4",
        average_price="100.20",
    )

    assert result.trade_after.status == (
        TradeStatus.RECOVERY_REQUIRED
    )
    assert result.trade_after.filled_quantity == 4
    assert result.trade_after.remaining_entry_quantity == 6
    assert result.trade_after.required_recovery_action == (
        EntryRecoveryAction.PROTECT_FILLED_QUANTITY
    )
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    ("state", "filled", "average_price"),
    [
        ("unexpected", "0", None),
        ("filled", "11", "100.20"),
        ("filled", "10", None),
    ],
)
def test_ambiguous_entry_observation_durably_halts(
    tmp_path,
    state,
    filled,
    average_price,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state=state,
        cumulative_quantity=filled,
        average_price=average_price,
    )

    assert result.trade_after.status == (
        TradeStatus.RECOVERY_REQUIRED
    )
    assert result.trade_after.required_recovery_action == (
        EntryRecoveryAction.RECONCILE_BROKER_STATE
    )
    assert (
        result.trade_after.operator_ack_required
        is True
    )
    assert result.trade_after.recovery_failure_reason
    assert store.is_entry_halted(STRATEGY)


def test_stale_observation_durably_halts(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    first = apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
        observed_at=OBSERVED_AT,
    )

    stale = apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="5",
        average_price="100.21",
        observed_at=(
            OBSERVED_AT - timedelta(seconds=1)
        ),
    )

    assert first.trade_after.filled_quantity == 4
    assert stale.trade_after.status == (
        TradeStatus.RECOVERY_REQUIRED
    )
    assert "stale" in (
        stale.trade_after
        .recovery_failure_reason.lower()
    )
    assert store.is_entry_halted(STRATEGY)


def test_identical_observation_is_idempotent(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)
    coordinator = EntryLifecycleCoordinator(store)
    order = make_order(
        state="partially_filled",
        cumulative_quantity="4",
    )

    first = coordinator.apply(
        trade_id=trade.trade_id,
        order=order,
        average_fill_price="100.20",
        observation_at=OBSERVED_AT,
    )
    event_count = (
        store.count_entry_recovery_events()
    )
    version = first.trade_after.version

    for _ in range(2):
        store.record_order_event(
            trade_id=trade.trade_id,
            broker_order_id="fake-entry-AAPL",
            role="entry",
            order_state="partially_filled",
            side="buy",
            quantity=Decimal("10"),
            filled_quantity=Decimal("4"),
            observation_key=(
                first.trade_after
                .last_entry_observation_key
            ),
        )

    second = coordinator.apply(
        trade_id=trade.trade_id,
        order=order,
        average_fill_price="100.20",
        observation_at=(
            OBSERVED_AT + timedelta(seconds=10)
        ),
    )

    assert second.ledger_changed is False
    assert second.trade_after.version == version
    assert (
        store.count_entry_recovery_events()
        == event_count
    )
    assert store.count_order_events() == 1
    assert store.is_entry_halted(STRATEGY)


def test_restart_preserves_recovery_state_and_halt(
    tmp_path,
):
    database_path = tmp_path / "restart.sqlite"
    store = TradeStateStore(database_path)
    trade = make_pending_trade(store)

    result = apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    restarted = TradeStateStore(database_path)
    recovered = restarted.get_trade(
        trade.trade_id
    )

    assert recovered == result.trade_after
    assert restarted.is_entry_halted(STRATEGY)
    assert recovered.required_recovery_action == (
        EntryRecoveryAction
        .CANCEL_REMAINDER_THEN_PROTECT
    )


@pytest.mark.parametrize(
    ("state", "filled", "average_price"),
    [
        ("partially_filled", "4", "100.20"),
        ("filled", "10", "100.25"),
    ],
)
def test_new_entries_blocked_while_recovery_or_protection_pending(
    tmp_path,
    state,
    filled,
    average_price,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state=state,
        cumulative_quantity=filled,
        average_price=average_price,
    )

    with pytest.raises(
        EntryHaltedError,
        match="durable",
    ):
        store.reserve_entry(
            strategy=STRATEGY,
            symbol="MSFT",
            session_date=SESSION_DATE,
            quantity=1,
            planned_entry=Decimal("100"),
            initial_stop=Decimal("99"),
            target_price=Decimal("102"),
        )


def test_startup_pending_halt_survives_restart(
    tmp_path,
):
    database_path = tmp_path / "startup.sqlite"
    store = TradeStateStore(database_path)
    store.begin_startup_reconciliation(
        STRATEGY
    )

    restarted = TradeStateStore(database_path)

    assert restarted.is_entry_halted(STRATEGY)

    with pytest.raises(EntryHaltedError):
        restarted.reserve_entry(
            strategy=STRATEGY,
            symbol="AAPL",
            session_date=SESSION_DATE,
            quantity=1,
            planned_entry=Decimal("100"),
            initial_stop=Decimal("99"),
            target_price=Decimal("102"),
        )


def test_clean_unrelated_trade_does_not_clear_halt(
    tmp_path,
):
    store = make_store(tmp_path)

    clean = make_pending_trade(
        store,
        symbol="MSFT",
    )
    clean = apply_entry(
        store,
        clean.trade_id,
        state="filled",
        cumulative_quantity="10",
        average_price="100.10",
    ).trade_after
    store.attach_protective_stop(
        clean.trade_id,
        broker_order_id="fake-stop-MSFT",
    )

    recovery = make_pending_trade(
        store,
        symbol="AAPL",
    )
    apply_entry(
        store,
        recovery.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    state = make_account_state(
        positions=(
            make_position("MSFT", "10"),
            make_position("AAPL", "4"),
        ),
        orders=(
            make_stop("MSFT", "10"),
        ),
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        account_state=state,
    )

    assert store.is_entry_halted(STRATEGY)


def test_flat_reconciliation_clears_recovery_condition(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state="cancelled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        account_state=make_account_state(),
    )

    recovered = store.get_trade(
        trade.trade_id
    )

    assert recovered.status == TradeStatus.ERROR
    assert recovered.required_recovery_action == (
        EntryRecoveryAction.NONE
    )
    assert not store.is_entry_halted(STRATEGY)


def test_fully_protected_reconciliation_clears_recovery_condition(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state="cancelled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    state = make_account_state(
        positions=(
            make_position("AAPL", "4"),
        ),
        orders=(
            make_stop("AAPL", "4"),
        ),
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        account_state=state,
    )

    recovered = store.get_trade(
        trade.trade_id
    )

    assert recovered.status == (
        TradeStatus.OPEN_PROTECTED
    )
    assert recovered.protected_quantity == 4
    assert recovered.unprotected_quantity == 0
    assert recovered.protective_stop_order_id
    assert not store.is_entry_halted(STRATEGY)


def test_other_strategy_cannot_clear_orb_halt(
    tmp_path,
):
    store = make_store(tmp_path)
    orb_trade = make_pending_trade(store)

    apply_entry(
        store,
        orb_trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    other_trade = make_pending_trade(
        store,
        strategy=OTHER_STRATEGY,
        symbol="MSFT",
    )
    apply_entry(
        store,
        other_trade.trade_id,
        state="partially_filled",
        cumulative_quantity="2",
        average_price="100.10",
    )

    store.begin_startup_reconciliation(
        OTHER_STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=OTHER_STRATEGY,
        account_state=make_account_state(),
    )

    assert store.is_entry_halted(STRATEGY)
    assert not store.is_entry_halted(
        OTHER_STRATEGY
    )


class MissingOrderObserver:
    async def observe(self, **kwargs):
        raise OrderNotFoundError(
            "Fake order is missing."
        )


@pytest.mark.anyio
async def test_missing_order_persists_halt_without_broker_write(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    monitor = ReadOnlyOrderMonitor(
        store,
        observer=MissingOrderObserver(),
    )

    result = await monitor.monitor_trade(
        account_number="FAKE",
        trade_id=trade.trade_id,
    )

    persisted = store.get_trade(
        trade.trade_id
    )

    assert result.action == (
        OrderMonitorAction.FAIL_CLOSED
    )
    assert persisted.status == (
        TradeStatus.RECOVERY_REQUIRED
    )
    assert store.is_entry_halted(STRATEGY)
    assert not hasattr(monitor, "broker_executor")
