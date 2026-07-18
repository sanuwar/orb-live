from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import sqlite3
from decimal import Decimal

import pytest

from tests.snapshot_helpers import make_broker_snapshot

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.commissioning_engine import CommissioningEngine
from src.config import load_config
from src.entry_lifecycle import (
    EntryLifecycleAction,
    EntryLifecycleCoordinator,
    entry_observation_key,
)
from src.order_monitor import (
    OrderMonitorAction,
    ReadOnlyOrderMonitor,
)
from src.order_observer import OrderNotFoundError
from src.shadow_runtime import ShadowRuntime
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
    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state(
            positions=(
                make_position("MSFT", "10"),
            ),
            orders=(
                make_stop("MSFT", "10"),
            ),
        )),
    )
    assert not store.is_entry_halted(STRATEGY)

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
        snapshot=make_broker_snapshot(state),
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
        snapshot=make_broker_snapshot(make_account_state()),
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

    linked_stop = make_stop("AAPL", "4")
    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=linked_stop.order_id,
    )
    assert store.is_entry_halted(STRATEGY)

    state = make_account_state(
        positions=(
            make_position("AAPL", "4"),
        ),
        orders=(linked_stop,),
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(state),
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
        snapshot=make_broker_snapshot(make_account_state()),
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

def create_legacy_database(database_path) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE trades (
                trade_id TEXT PRIMARY KEY,
                strategy TEXT NOT NULL,
                symbol TEXT NOT NULL,
                session_date TEXT NOT NULL,
                status TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                filled_quantity INTEGER NOT NULL
                    DEFAULT 0,
                planned_entry TEXT NOT NULL,
                initial_stop TEXT NOT NULL,
                target_price TEXT NOT NULL,
                average_entry_price TEXT,
                average_exit_price TEXT,
                realized_pnl TEXT NOT NULL
                    DEFAULT '0',
                entry_order_id TEXT,
                protective_stop_order_id TEXT,
                exit_order_id TEXT,
                close_reason TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT,
                closed_at TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                UNIQUE (
                    strategy,
                    symbol,
                    session_date
                )
            );

            CREATE INDEX idx_trades_status
            ON trades(status);

            CREATE INDEX idx_trades_session_date
            ON trades(session_date);

            CREATE TABLE trade_events (
                event_id INTEGER PRIMARY KEY
                    AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                old_status TEXT,
                new_status TEXT,
                occurred_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(trade_id)
                    REFERENCES trades(trade_id)
            );

            CREATE TABLE order_events (
                event_id INTEGER PRIMARY KEY
                    AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                broker_order_id TEXT NOT NULL,
                role TEXT NOT NULL,
                order_state TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT,
                filled_quantity TEXT,
                limit_price TEXT,
                stop_price TEXT,
                occurred_at TEXT NOT NULL,
                details_json TEXT NOT NULL,
                FOREIGN KEY(trade_id)
                    REFERENCES trades(trade_id)
            );

            CREATE TRIGGER prevent_trade_event_update
            BEFORE UPDATE ON trade_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'trade_events are append-only'
                );
            END;

            CREATE TRIGGER prevent_trade_event_delete
            BEFORE DELETE ON trade_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'trade_events are append-only'
                );
            END;

            CREATE TRIGGER prevent_order_event_update
            BEFORE UPDATE ON order_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'order_events are append-only'
                );
            END;

            CREATE TRIGGER prevent_order_event_delete
            BEFORE DELETE ON order_events
            BEGIN
                SELECT RAISE(
                    ABORT,
                    'order_events are append-only'
                );
            END;
            """
        )

        connection.execute(
            """
            INSERT INTO trades (
                trade_id,
                strategy,
                symbol,
                session_date,
                status,
                quantity,
                filled_quantity,
                planned_entry,
                initial_stop,
                target_price,
                average_entry_price,
                average_exit_price,
                realized_pnl,
                entry_order_id,
                protective_stop_order_id,
                exit_order_id,
                close_reason,
                created_at,
                updated_at,
                opened_at,
                closed_at,
                version
            )
            VALUES (
                'legacy-trade',
                'orb_long_v0',
                'AAPL',
                '2026-07-16',
                'closed',
                2,
                2,
                '100',
                '99',
                '102',
                '100',
                '101',
                '2',
                'legacy-entry',
                'legacy-stop',
                'legacy-exit',
                'profit_target',
                '2026-07-16T13:30:00+00:00',
                '2026-07-16T14:30:00+00:00',
                '2026-07-16T13:31:00+00:00',
                '2026-07-16T14:30:00+00:00',
                4
            )
            """
        )
        connection.execute(
            """
            INSERT INTO trade_events (
                event_id,
                trade_id,
                event_type,
                old_status,
                new_status,
                occurred_at,
                details_json
            )
            VALUES (
                7,
                'legacy-trade',
                'trade_closed',
                'exit_pending',
                'closed',
                '2026-07-16T14:30:00+00:00',
                '{"source":"legacy"}'
            )
            """
        )
        connection.execute(
            """
            INSERT INTO order_events (
                event_id,
                trade_id,
                broker_order_id,
                role,
                order_state,
                side,
                quantity,
                filled_quantity,
                limit_price,
                stop_price,
                occurred_at,
                details_json
            )
            VALUES (
                9,
                'legacy-trade',
                'legacy-entry',
                'entry',
                'filled',
                'buy',
                '2',
                '2',
                '100',
                NULL,
                '2026-07-16T13:31:00+00:00',
                '{"source":"legacy"}'
            )
            """
        )


def migration_snapshot(database_path):
    with sqlite3.connect(database_path) as connection:
        trade_columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(trades)"
            )
        )
        order_columns = tuple(
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(order_events)"
            )
        )
        schema_objects = tuple(
            connection.execute(
                """
                SELECT type, name, sql
                FROM sqlite_master
                WHERE name IN (
                    'entry_halts',
                    'entry_recovery_events',
                    'idx_order_events_observation_key',
                    'prevent_entry_recovery_event_update',
                    'prevent_entry_recovery_event_delete'
                )
                ORDER BY type, name
                """
            )
        )
        trade_events = tuple(
            connection.execute(
                """
                SELECT event_id, trade_id, event_type,
                       old_status, new_status,
                       occurred_at, details_json
                FROM trade_events
                ORDER BY event_id
                """
            )
        )
        order_events = tuple(
            connection.execute(
                """
                SELECT event_id, trade_id,
                       broker_order_id, role,
                       order_state, side, quantity,
                       filled_quantity, limit_price,
                       stop_price, occurred_at,
                       details_json, observation_key
                FROM order_events
                ORDER BY event_id
                """
            )
        )

    return (
        trade_columns,
        order_columns,
        schema_objects,
        trade_events,
        order_events,
    )


def test_legacy_schema_migration_is_idempotent_and_preserves_data(
    tmp_path,
):
    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    first_store = TradeStateStore(database_path)
    first_trade = first_store.get_trade(
        "legacy-trade"
    )
    first_snapshot = migration_snapshot(
        database_path
    )

    assert first_trade.status == TradeStatus.CLOSED
    assert first_trade.realized_pnl == Decimal("2")
    assert first_trade.version == 4
    assert first_trade.protected_quantity == 0
    assert first_trade.required_recovery_action == (
        EntryRecoveryAction.NONE
    )
    assert first_trade.recovery_attempt_count == 0
    assert (
        first_trade.last_protection_reconciliation_id
        is None
    )
    assert first_trade.protection_confirmed_at is None

    with sqlite3.connect(database_path) as connection:
        proof_objects = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE name IN (
                    'broker_reconciliation_runs',
                    'protection_reconciliation_events',
                    'prevent_broker_reconciliation_run_update',
                    'prevent_broker_reconciliation_run_delete',
                    'prevent_protection_reconciliation_event_update',
                    'prevent_protection_reconciliation_event_delete'
                )
                """
            )
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM broker_reconciliation_runs"
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT COUNT(*) FROM protection_reconciliation_events"
        ).fetchone()[0] == 0

    assert len(proof_objects) == 6
    assert first_store.count_trade_events() == 1
    assert first_store.count_order_events() == 1
    assert (
        first_store.count_entry_recovery_events()
        == 0
    )

    required_trade_columns = {
        "protected_quantity",
        "last_broker_observation_at",
        "required_recovery_action",
        "last_completed_recovery_step",
        "recovery_ref_id",
        "recovery_attempt_count",
        "recovery_failure_reason",
        "operator_ack_required",
        "last_entry_observation_key",
        "last_protection_reconciliation_id",
        "protection_confirmed_at",
    }
    assert required_trade_columns.issubset(
        set(first_snapshot[0])
    )
    assert "observation_key" in first_snapshot[1]
    assert len(first_snapshot[2]) == 5
    assert first_snapshot[3][0][0] == 7
    assert first_snapshot[4][0][0] == 9
    assert first_snapshot[4][0][-1] is None

    restarted = TradeStateStore(database_path)
    second_snapshot = migration_snapshot(
        database_path
    )

    assert restarted.get_trade(
        "legacy-trade"
    ) == first_trade
    assert second_snapshot == first_snapshot
    assert restarted.count_trade_events() == 1
    assert restarted.count_order_events() == 1
    assert (
        restarted.count_entry_recovery_events()
        == 0
    )


def record_entry_order_event(
    store: TradeStateStore,
    trade_id: str,
    order: EquityOrder,
    observation_key: str,
) -> bool:
    return store.record_order_event(
        trade_id=trade_id,
        broker_order_id=order.order_id,
        role="entry",
        order_state=order.state,
        side=order.side,
        quantity=order.quantity,
        filled_quantity=(
            order.cumulative_quantity
        ),
        limit_price=order.limit_price,
        stop_price=order.stop_price,
        observation_key=observation_key,
    )


def test_observation_idempotency_survives_restart_and_changed_data_adds_event(
    tmp_path,
):
    database_path = (
        tmp_path / "observation_restart.sqlite"
    )
    store = TradeStateStore(database_path)
    trade = make_pending_trade(store)
    first_order = make_order(
        state="partially_filled",
        cumulative_quantity="4",
    )
    first_key = entry_observation_key(
        first_order,
        "100.20",
    )

    first = EntryLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=first_order,
        average_fill_price="100.20",
        observation_at=OBSERVED_AT,
    )
    assert record_entry_order_event(
        store,
        trade.trade_id,
        first_order,
        first_key,
    )

    first_version = first.trade_after.version
    first_recovery_events = (
        store.count_entry_recovery_events()
    )
    first_order_events = (
        store.count_order_events()
    )

    restarted = TradeStateStore(database_path)
    restart_key = entry_observation_key(
        first_order,
        "100.20",
    )

    assert restart_key == first_key

    repeated = EntryLifecycleCoordinator(
        restarted
    ).apply(
        trade_id=trade.trade_id,
        order=first_order,
        average_fill_price="100.20",
        observation_at=(
            OBSERVED_AT + timedelta(minutes=5)
        ),
    )
    assert not record_entry_order_event(
        restarted,
        trade.trade_id,
        first_order,
        restart_key,
    )

    assert repeated.ledger_changed is False
    assert repeated.trade_after.version == first_version
    assert repeated.trade_after.filled_quantity == 4
    assert repeated.trade_after.recovery_attempt_count == 0
    assert (
        restarted.count_entry_recovery_events()
        == first_recovery_events
    )
    assert (
        restarted.count_order_events()
        == first_order_events
    )

    changed_order = make_order(
        state="partially_filled",
        cumulative_quantity="5",
    )
    changed_key = entry_observation_key(
        changed_order,
        "100.21",
    )

    assert changed_key != first_key
    assert record_entry_order_event(
        restarted,
        trade.trade_id,
        changed_order,
        changed_key,
    )

    changed = EntryLifecycleCoordinator(
        restarted
    ).apply(
        trade_id=trade.trade_id,
        order=changed_order,
        average_fill_price="100.21",
        observation_at=(
            OBSERVED_AT + timedelta(minutes=6)
        ),
    )

    assert changed.ledger_changed is True
    assert changed.trade_after.version == (
        first_version + 1
    )
    assert changed.trade_after.filled_quantity == 5
    assert changed.trade_after.remaining_entry_quantity == 5
    assert changed.trade_after.recovery_attempt_count == 0
    assert (
        restarted.count_entry_recovery_events()
        == first_recovery_events + 1
    )
    assert (
        restarted.count_order_events()
        == first_order_events + 1
    )

    changed_version = changed.trade_after.version
    replayed_old_observation = (
        EntryLifecycleCoordinator(
            restarted
        ).apply(
            trade_id=trade.trade_id,
            order=first_order,
            average_fill_price="100.20",
            observation_at=(
                OBSERVED_AT
                + timedelta(minutes=7)
            ),
        )
    )
    assert not record_entry_order_event(
        restarted,
        trade.trade_id,
        first_order,
        first_key,
    )

    assert (
        replayed_old_observation.ledger_changed
        is False
    )
    assert (
        replayed_old_observation
        .trade_after.version
        == changed_version
    )
    assert (
        replayed_old_observation
        .trade_after.filled_quantity
        == 5
    )
    assert (
        restarted.count_entry_recovery_events()
        == first_recovery_events + 1
    )
    assert (
        restarted.count_order_events()
        == first_order_events + 1
    )


@pytest.mark.parametrize(
    "stop_changes",
    [
        {"state": "cancelled"},
        {"state": "rejected"},
        {
            "state": "partially_filled",
            "cumulative_quantity": Decimal("1"),
        },
        {"quantity": Decimal("3")},
        {"order_id": "unrelated-stop"},
        {"symbol": "MSFT"},
        {"placed_agent": "manual"},
        {"order_type": "limit"},
        {"trigger": "immediate"},
    ],
)
def test_invalid_or_unrelated_stop_cannot_clear_entry_halt(
    tmp_path,
    stop_changes,
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

    linked_stop = replace(
        make_stop("AAPL", "4"),
        order_id="linked-stop",
    )
    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=linked_stop.order_id,
    )

    invalid_stop = replace(
        linked_stop,
        **stop_changes,
    )
    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state(
            positions=(
                make_position("AAPL", "4"),
            ),
            orders=(invalid_stop,),
        )),
    )

    assert store.is_entry_halted(STRATEGY)


def test_missing_stale_linked_stop_cannot_clear_entry_halt(
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
    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="linked-stop",
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state(
            positions=(
                make_position("AAPL", "4"),
            ),
            orders=(),
        )),
    )

    assert store.is_entry_halted(STRATEGY)


def test_other_strategy_or_unlinked_stop_cannot_satisfy_orb_halt(
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
    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="orb-linked-stop",
    )

    other_strategy_stop = replace(
        make_stop("AAPL", "4"),
        order_id="other-strategy-stop",
    )
    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state(
            positions=(
                make_position("AAPL", "4"),
            ),
            orders=(other_strategy_stop,),
        )),
    )

    assert store.is_entry_halted(STRATEGY)


def test_close_cannot_clear_halt_without_flat_reconciliation(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state="filled",
        cumulative_quantity="10",
        average_price="100.25",
    )
    closed = store.close_trade(
        trade.trade_id,
        average_exit_price=Decimal("101"),
        reason="test_close",
    )

    assert closed.status == TradeStatus.CLOSED
    assert store.is_entry_halted(STRATEGY)

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state()),
    )

    assert not store.is_entry_halted(STRATEGY)


class StaticAccountReader:
    def __init__(self, state):
        self.state = state
        self.calls = 0

    async def read(self, **kwargs):
        self.calls += 1
        return self.state

    async def read_snapshot(self, **kwargs):
        return make_broker_snapshot(
            await self.read(**kwargs)
        )


@pytest.mark.anyio
async def test_commissioning_cannot_bypass_durable_strategy_halt(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    reader = StaticAccountReader(
        make_account_state(
            positions=(
                make_position("AAPL", "4"),
            ),
            orders=(),
        )
    )
    engine = CommissioningEngine(
        load_config(),
        trade_store=store,
        client=object(),
    )
    engine.account_reader = reader

    report = await engine.run()

    assert reader.calls == 1
    assert report.durable_entry_halt is True
    assert report.dry_run_plan.approved is False
    assert "reconciliation" in (
        report.dry_run_plan.reason.lower()
    )

    with pytest.raises(EntryHaltedError):
        store.reserve_entry(
            strategy=STRATEGY,
            symbol="MSFT",
            session_date=SESSION_DATE,
            quantity=1,
            planned_entry=Decimal("100"),
            initial_stop=Decimal("99"),
            target_price=Decimal("102"),
        )


def test_full_suite_repository_data_database_guard_is_active(
    repository_data_database_guard,
):
    assert getattr(
        sqlite3.connect,
        "orb_repository_data_guard",
        False,
    )
    assert getattr(
        TradeStateStore.__init__,
        "orb_repository_data_guard",
        False,
    )
    assert (
        repository_data_database_guard.attempts
        == []
    )


def test_persistence_paths_are_explicit_at_composition_boundaries():
    with pytest.raises(TypeError):
        TradeStateStore()

    with pytest.raises(
        TypeError,
        match="trade_store",
    ):
        CommissioningEngine(
            load_config(),
            client=object(),
        )

    with pytest.raises(
        TypeError,
        match="trade_store",
    ):
        ShadowRuntime(
            load_config(),
            client=object(),
        )

def test_linked_open_entry_order_blocks_protection_clearance(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = make_pending_trade(store)

    apply_entry(
        store,
        trade.trade_id,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="100.20",
    )

    linked_stop = replace(
        make_stop("AAPL", "4"),
        order_id="linked-stop",
    )
    store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=linked_stop.order_id,
    )
    still_open_entry = make_order(
        state="partially_filled",
        cumulative_quantity="4",
    )

    store.begin_startup_reconciliation(
        STRATEGY
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=make_broker_snapshot(make_account_state(
            positions=(
                make_position("AAPL", "4"),
            ),
            orders=(
                still_open_entry,
                linked_stop,
            ),
        )),
    )

    assert store.is_entry_halted(STRATEGY)

def test_legacy_open_protected_requires_fresh_snapshot_proof(
    tmp_path,
):
    database_path = (
        tmp_path / "legacy-open-protected.sqlite"
    )
    create_legacy_database(database_path)

    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            UPDATE trades
            SET status = 'open_protected',
                protective_stop_order_id = 'legacy-stop',
                average_exit_price = NULL,
                close_reason = NULL,
                closed_at = NULL
            WHERE trade_id = 'legacy-trade'
            """
        )

    store = TradeStateStore(database_path)
    legacy = store.get_trade("legacy-trade")

    assert legacy.status == TradeStatus.OPEN_PROTECTED
    assert legacy.last_protection_reconciliation_id is None
    assert legacy.protection_confirmed_at is None

    store.begin_startup_reconciliation(
        STRATEGY,
        occurred_at=OBSERVED_AT,
    )
    restarted = TradeStateStore(database_path)
    pending = restarted.get_trade("legacy-trade")

    assert pending.status == TradeStatus.STOP_LINKED
    assert pending.protected_quantity == 0
    assert restarted.is_entry_halted(STRATEGY)
    assert restarted.count_order_events() == 1
    assert restarted.count_trade_events() == 2
