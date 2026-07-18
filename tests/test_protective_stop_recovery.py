from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.account_state import EquityOrder
from src.protective_stop_lifecycle import (
    ProtectiveStopAction,
    ProtectiveStopLifecycleCoordinator,
    protective_stop_observation_key,
)
from src.trade_state import (
    StopRecoveryAction,
    StopRecoveryStep,
    TradeStateError,
    TradeStateStore,
    TradeStatus,
)
from tests.snapshot_helpers import (
    apply_broker_snapshot,
    make_broker_snapshot,
    protected_account_state,
)
from tests.test_entry_recovery import (
    create_legacy_database,
)


STRATEGY = "orb_long_v0"
OTHER_STRATEGY = "other_strategy"


def table_count(store, table: str) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        )


def make_protected_trade(
    tmp_path,
    *,
    strategy: str = STRATEGY,
    quantity: int = 10,
):
    store = TradeStateStore(
        tmp_path / f"{strategy}.sqlite"
    )
    trade = store.reserve_entry(
        strategy=strategy,
        symbol="AAPL",
        session_date=date(2026, 7, 18),
        quantity=quantity,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"entry-{strategy}",
    )
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=quantity,
        average_entry_price=Decimal("100"),
    )
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=f"stop-{strategy}",
    )
    apply_broker_snapshot(
        store,
        strategy=strategy,
        account_state=protected_account_state(
            trade
        ),
    )
    return store, store.get_trade(trade.trade_id)


def stop_order(
    trade,
    *,
    state: str = "confirmed",
    quantity: str | None = None,
    cumulative_quantity: str = "0",
    average_price: str | None = None,
    order_id: str | None = None,
    symbol: str | None = None,
    side: str = "sell",
    trigger: str = "stop",
    order_type: str = "stop_market",
    placed_agent: str = "agentic",
):
    order = EquityOrder(
        order_id=(
            order_id
            if order_id is not None
            else trade.protective_stop_order_id
        ),
        symbol=symbol or trade.symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=Decimal(
            quantity
            if quantity is not None
            else trade.filled_quantity
        ),
        cumulative_quantity=Decimal(
            cumulative_quantity
        ),
        limit_price=None,
        stop_price=Decimal("99"),
        trigger=trigger,
        placed_agent=placed_agent,
    )
    return order, average_price


def apply_stop(
    store,
    trade,
    *,
    observation_key: str | None = None,
    **changes,
):
    order, average_price = stop_order(
        trade,
        **changes,
    )
    return ProtectiveStopLifecycleCoordinator(
        store
    ).apply(
        trade_id=trade.trade_id,
        order=order,
        average_fill_price=average_price,
        observation_key=observation_key,
        observation_at=datetime(
            2026,
            7,
            18,
            14,
            30,
            tzinfo=timezone.utc,
        ),
    )


def account_state_for(
    trade,
    *,
    position_quantity: int | None,
    stop_quantity: int | None = None,
    stop_state: str = "confirmed",
    stop_cumulative: int = 0,
    stop_order_id: str | None = None,
    stop_symbol: str | None = None,
    stop_side: str = "sell",
    stop_type: str = "stop_market",
    stop_trigger: str = "stop",
    stop_agent: str = "agentic",
):
    base = protected_account_state(trade)
    positions = ()

    if position_quantity is not None:
        position = base.positions[0]
        quantity = Decimal(position_quantity)
        positions = (
            replace(
                position,
                quantity=quantity,
                intraday_quantity=quantity,
                shares_available_for_sells=quantity,
            ),
        )

    orders = ()
    if stop_quantity is not None:
        stop = base.open_orders[0]
        orders = (
            replace(
                stop,
                order_id=(
                    stop_order_id
                    if stop_order_id is not None
                    else trade.protective_stop_order_id
                ),
                symbol=stop_symbol or trade.symbol,
                side=stop_side,
                order_type=stop_type,
                trigger=stop_trigger,
                state=stop_state,
                quantity=Decimal(stop_quantity),
                cumulative_quantity=Decimal(
                    stop_cumulative
                ),
                placed_agent=stop_agent,
            ),
        )

    return replace(
        base,
        positions=positions,
        open_orders=orders,
    )


def test_exact_active_zero_fill_requires_no_recovery(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    before_events = table_count(
        store,
        "protective_stop_recovery_events",
    )

    result = apply_stop(store, trade)

    assert result.decision.action == (
        ProtectiveStopAction.WAIT
    )
    assert not result.ledger_changed
    assert result.trade_after.status == (
        TradeStatus.OPEN_PROTECTED
    )
    assert table_count(
        store,
        "protective_stop_recovery_events",
    ) == before_events
    assert not store.is_entry_halted(STRATEGY)


def test_observation_key_is_stable_across_restart_and_idempotent(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    order, average = stop_order(
        trade,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="99",
    )
    first_key = protective_stop_observation_key(
        order,
        average,
    )
    first = apply_stop(
        store,
        trade,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="99",
    )
    counts = (
        table_count(store, "trade_events"),
        table_count(
            store,
            "protective_stop_recovery_events",
        ),
    )
    version = first.trade_after.version

    restarted = TradeStateStore(store.db_path)
    persisted = restarted.get_trade(trade.trade_id)
    second_key = protective_stop_observation_key(
        order,
        average,
    )
    second = apply_stop(
        restarted,
        persisted,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="99",
    )

    assert first_key == second_key
    assert not second.ledger_changed
    assert second.trade_after.version == version
    assert (
        table_count(restarted, "trade_events"),
        table_count(
            restarted,
            "protective_stop_recovery_events",
        ),
    ) == counts
    assert second.trade_after.stop_executed_quantity == 4
    assert second.trade_after.stop_recovery_attempt_count == 0


def test_changed_broker_observation_creates_one_new_event(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    first = apply_stop(
        store,
        trade,
        state="partially_filled",
        cumulative_quantity="3",
        average_price="99",
    )
    before = table_count(
        store,
        "protective_stop_recovery_events",
    )

    second = apply_stop(
        store,
        first.trade_after,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="99",
    )

    assert second.ledger_changed
    assert table_count(
        store,
        "protective_stop_recovery_events",
    ) == before + 1
    assert second.trade_after.stop_executed_quantity == 4
    assert second.trade_after.stop_recovery_attempt_count == 0


def test_positive_execution_without_average_price_fails_closed_durably(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        state="partially_filled",
        cumulative_quantity="2",
    )

    assert result.decision.fail_closed
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert result.trade_after.average_stop_fill_price is None
    assert result.trade_after.stop_executed_quantity == 2
    assert result.trade_after.stop_operator_ack_required
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    "state",
    [
        "cancelled",
        "rejected",
        "pending_cancel",
    ],
)
def test_inactive_or_pending_cancel_stop_requires_durable_recovery(
    tmp_path,
    state,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        state=state,
    )

    assert result.decision.action == (
        ProtectiveStopAction.RECOVER_INACTIVE_STOP
    )
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert result.trade_after.protective_stop_order_id
    assert result.trade_after.required_stop_recovery_action == (
        StopRecoveryAction
        .REPLACE_PROTECTION_OR_FLATTEN
    )
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"order_id": "unrelated"}, "order ID"),
        ({"symbol": "MSFT"}, "symbol"),
        ({"side": "buy"}, "sell"),
        ({"trigger": "immediate"}, "stop trigger"),
        ({"order_type": "stop_limit"}, "stop-market"),
        ({"placed_agent": "manual"}, "agentic"),
    ],
)
def test_wrong_stop_shape_or_origin_fails_closed_durably(
    tmp_path,
    changes,
    message,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        **changes,
    )

    assert result.decision.fail_closed
    assert message.lower() in result.decision.reason.lower()
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert result.trade_after.stop_operator_ack_required
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    ("state", "quantity", "filled"),
    [
        ("unexpected", "10", "0"),
        ("", "10", "0"),
        ("partially_filled", "10", "0"),
        ("filled", "10", "9"),
    ],
)
def test_unknown_or_inconsistent_stop_state_fails_closed(
    tmp_path,
    state,
    quantity,
    filled,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        state=state,
        quantity=quantity,
        cumulative_quantity=filled,
        average_price=("99" if filled != "0" else None),
    )

    assert result.decision.fail_closed
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    ("quantity", "expected_text"),
    [
        ("9", "undersized"),
        ("11", "oversized"),
    ],
)
def test_under_or_oversized_stop_fails_closed(
    tmp_path,
    quantity,
    expected_text,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        quantity=quantity,
    )

    assert result.decision.fail_closed
    assert expected_text in result.decision.reason
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert result.trade_after.protected_quantity == 0
    assert store.is_entry_halted(STRATEGY)


def test_execution_over_requested_and_position_fails_closed(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)

    result = apply_stop(
        store,
        trade,
        state="partially_filled",
        quantity="10",
        cumulative_quantity="11",
        average_price="99",
    )

    assert result.decision.fail_closed
    assert result.trade_after.stop_executed_quantity == 11
    assert result.trade_after.stop_operator_ack_required
    assert store.is_entry_halted(STRATEGY)


def test_full_stop_fill_closes_only_after_fresh_flat_snapshot(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    result = apply_stop(
        store,
        trade,
        state="filled",
        cumulative_quantity="10",
        average_price="99",
    )

    assert result.decision.action == (
        ProtectiveStopAction
        .RECONCILE_FULL_STOP_FILL
    )
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert result.trade_after.realized_pnl == 0
    assert store.is_entry_halted(STRATEGY)

    snapshot, issues = apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state_for(
            result.trade_after,
            position_quantity=None,
        ),
        reconciliation_id="full-stop-flat",
    )
    closed = store.get_trade(trade.trade_id)
    event_counts = (
        table_count(store, "trade_events"),
        table_count(
            store,
            "protection_reconciliation_events",
        ),
    )

    assert issues == ()
    assert closed.status == TradeStatus.CLOSED
    assert closed.average_exit_price == Decimal("99")
    assert closed.realized_pnl == Decimal("-10")
    assert not store.is_entry_halted(STRATEGY)

    repeat = store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=(
            snapshot.metadata.completed_at
            + timedelta(milliseconds=2)
        ),
    )

    assert repeat == ()
    assert (
        table_count(store, "trade_events"),
        table_count(
            store,
            "protection_reconciliation_events",
        ),
    ) == event_counts
    assert store.get_trade(trade.trade_id).realized_pnl == (
        Decimal("-10")
    )


def test_full_stop_fill_with_remaining_position_stays_halted(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    recovery = apply_stop(
        store,
        trade,
        state="filled",
        cumulative_quantity="10",
        average_price="99",
    ).trade_after

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state_for(
            recovery,
            position_quantity=1,
            stop_quantity=None,
        ),
        reconciliation_id="not-flat",
    )

    persisted = store.get_trade(trade.trade_id)
    assert persisted.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert persisted.remaining_broker_position_quantity == 1
    assert persisted.realized_pnl == 0
    assert store.is_entry_halted(STRATEGY)


def test_partial_fill_reprotects_exact_remaining_position_only(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    recovery = apply_stop(
        store,
        trade,
        state="partially_filled",
        cumulative_quantity="4",
        average_price="99",
    ).trade_after

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state_for(
            recovery,
            position_quantity=6,
            stop_quantity=None,
        ),
        reconciliation_id="partial-unresolved",
    )
    unresolved = store.get_trade(trade.trade_id)

    assert unresolved.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert unresolved.remaining_broker_position_quantity == 6
    assert unresolved.unprotected_quantity == 6
    assert store.is_entry_halted(STRATEGY)

    linked = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="replacement-stop",
    )
    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state_for(
            linked,
            position_quantity=6,
            stop_quantity=6,
            stop_order_id="replacement-stop",
        ),
        reconciliation_id="partial-protected",
    )
    protected = store.get_trade(trade.trade_id)

    assert protected.status == TradeStatus.OPEN_PROTECTED
    assert protected.protected_quantity == 6
    assert protected.remaining_broker_position_quantity == 6
    assert protected.unprotected_quantity == 0
    assert protected.stop_executed_quantity == 4
    assert protected.required_stop_recovery_action == (
        StopRecoveryAction.NONE
    )
    assert not store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    "variant",
    [
        "cancelled",
        "rejected",
        "partial",
        "pending_cancel",
        "undersized",
        "oversized",
        "unrelated",
        "manual",
    ],
)
def test_invalid_or_unrelated_snapshot_stop_cannot_clear_halt(
    tmp_path,
    variant,
):
    store, trade = make_protected_trade(tmp_path)
    recovery = apply_stop(
        store,
        trade,
        state="cancelled",
    ).trade_after
    kwargs = {
        "position_quantity": 10,
        "stop_quantity": 10,
    }

    if variant in {"cancelled", "rejected", "pending_cancel"}:
        kwargs["stop_state"] = variant
    elif variant == "partial":
        kwargs["stop_state"] = "partially_filled"
        kwargs["stop_cumulative"] = 1
    elif variant == "undersized":
        kwargs["stop_quantity"] = 9
    elif variant == "oversized":
        kwargs["stop_quantity"] = 11
    elif variant == "unrelated":
        kwargs["stop_order_id"] = "unrelated-stop"
    elif variant == "manual":
        kwargs["stop_agent"] = "manual"

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state_for(
            recovery,
            **kwargs,
        ),
        reconciliation_id=f"invalid-{variant}",
    )

    persisted = store.get_trade(trade.trade_id)
    assert persisted.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    "variant",
    [
        "wrong_account",
        "cycle_mismatch",
        "positions_incomplete",
        "stale",
    ],
)
def test_invalid_snapshot_metadata_cannot_clear_halt(
    tmp_path,
    variant,
):
    store, trade = make_protected_trade(tmp_path)
    recovery = apply_stop(
        store,
        trade,
        state="cancelled",
    ).trade_after
    account_state = account_state_for(
        recovery,
        position_quantity=None,
    )
    now = datetime.now(timezone.utc)
    kwargs = {"reconciliation_id": f"bad-{variant}"}

    if variant == "wrong_account":
        kwargs["account_fingerprint_value"] = "f" * 64
    elif variant == "cycle_mismatch":
        kwargs["orders_cycle_id"] = "other-cycle"
    elif variant == "positions_incomplete":
        kwargs["positions_complete"] = False
    elif variant == "stale":
        old = now - timedelta(hours=1)
        kwargs.update(
            {
                "started_at": old,
                "positions_read_at": (
                    old + timedelta(seconds=1)
                ),
                "orders_read_at": (
                    old + timedelta(seconds=2)
                ),
                "completed_at": (
                    old + timedelta(seconds=3)
                ),
            }
        )

    snapshot = make_broker_snapshot(
        account_state,
        **kwargs,
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=now,
    )

    assert store.get_trade(trade.trade_id).status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert store.is_entry_halted(STRATEGY)


def test_other_strategy_reconciliation_cannot_clear_orb_halt(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    apply_stop(
        store,
        trade,
        state="cancelled",
    )
    flat = account_state_for(
        trade,
        position_quantity=None,
    )

    apply_broker_snapshot(
        store,
        strategy=OTHER_STRATEGY,
        account_state=flat,
        reconciliation_id="other-strategy-cycle",
    )

    assert store.is_entry_halted(STRATEGY)
    assert store.get_trade(trade.trade_id).status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )


def test_reused_reconciliation_id_with_changed_evidence_fails_closed(
    tmp_path,
):
    store, trade = make_protected_trade(tmp_path)
    recovery = apply_stop(
        store,
        trade,
        state="cancelled",
    ).trade_after
    first = make_broker_snapshot(
        account_state_for(
            recovery,
            position_quantity=10,
            stop_quantity=None,
        ),
        reconciliation_id="reused-cycle",
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=first,
        occurred_at=(
            first.metadata.completed_at
            + timedelta(milliseconds=1)
        ),
    )
    changed = make_broker_snapshot(
        account_state_for(
            recovery,
            position_quantity=None,
        ),
        reconciliation_id="reused-cycle",
    )

    with pytest.raises(
        TradeStateError,
        match="reused",
    ):
        store.apply_startup_reconciliation(
            strategy=STRATEGY,
            snapshot=changed,
            occurred_at=(
                changed.metadata.completed_at
                + timedelta(milliseconds=1)
            ),
        )

    assert store.is_entry_halted(STRATEGY)


class MissingStopObserver:
    def __init__(self):
        self.read_calls = 0
        self.write_calls = 0

    async def observe(self, **kwargs):
        from src.order_observer import (
            OrderObservationError,
        )

        self.read_calls += 1
        raise OrderObservationError(
            "linked stop not returned"
        )

    async def review_order(self, **kwargs):
        self.write_calls += 1
        raise AssertionError("broker write called")

    async def place_order(self, **kwargs):
        self.write_calls += 1
        raise AssertionError("broker write called")

    async def cancel_order(self, **kwargs):
        self.write_calls += 1
        raise AssertionError("broker write called")

    async def replace_order(self, **kwargs):
        self.write_calls += 1
        raise AssertionError("broker write called")


@pytest.mark.anyio
async def test_missing_stop_observation_persists_halt_without_broker_write(
    tmp_path,
):
    from src.order_monitor import ReadOnlyOrderMonitor

    store, trade = make_protected_trade(tmp_path)
    observer = MissingStopObserver()
    result = await ReadOnlyOrderMonitor(
        store,
        observer=observer,
    ).monitor_trade(
        account_number="TEST12345678",
        trade_id=trade.trade_id,
    )

    assert result.fail_closed
    assert result.trade_after.status == (
        TradeStatus.STOP_RECOVERY_REQUIRED
    )
    assert observer.read_calls == 1
    assert observer.write_calls == 0
    assert table_count(
        store,
        "protective_stop_recovery_events",
    ) == 1
    assert store.is_entry_halted(STRATEGY)


def test_legacy_migration_adds_stop_recovery_schema_idempotently(
    tmp_path,
):
    database_path = tmp_path / "legacy-stop.sqlite"
    create_legacy_database(database_path)

    with sqlite3.connect(database_path) as connection:
        before_trade = connection.execute(
            """
            SELECT trade_id, strategy, symbol,
                   session_date, entry_order_id,
                   protective_stop_order_id, exit_order_id
            FROM trades WHERE trade_id = ?
            """,
            ("legacy-trade",),
        ).fetchone()
        before_events = connection.execute(
            "SELECT COUNT(*) FROM trade_events"
        ).fetchone()[0]

    first = TradeStateStore(database_path)
    migrated = first.get_trade("legacy-trade")

    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(trades)"
            )
        }
        recovery_table = connection.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name = 'protective_stop_recovery_events'
            """
        ).fetchone()
        after_events = connection.execute(
            "SELECT COUNT(*) FROM trade_events"
        ).fetchone()[0]

    required_columns = {
        "stop_original_position_quantity",
        "stop_requested_quantity",
        "stop_executed_quantity",
        "remaining_broker_position_quantity",
        "last_stop_state",
        "average_stop_fill_price",
        "required_stop_recovery_action",
        "last_completed_stop_recovery_step",
        "stop_recovery_ref_id",
        "stop_recovery_attempt_count",
        "stop_recovery_failure_reason",
        "stop_operator_ack_required",
        "last_stop_observation_key",
        "last_stop_reconciliation_id",
    }

    assert required_columns <= columns
    assert recovery_table is not None
    assert migrated.stop_executed_quantity == 0
    assert migrated.required_stop_recovery_action == (
        StopRecoveryAction.NONE
    )
    assert after_events == before_events

    second = TradeStateStore(database_path)
    assert second.get_trade("legacy-trade") == migrated

    with sqlite3.connect(database_path) as connection:
        after_trade = connection.execute(
            """
            SELECT trade_id, strategy, symbol,
                   session_date, entry_order_id,
                   protective_stop_order_id, exit_order_id
            FROM trades WHERE trade_id = ?
            """,
            ("legacy-trade",),
        ).fetchone()
        final_events = connection.execute(
            "SELECT COUNT(*) FROM trade_events"
        ).fetchone()[0]

    assert after_trade == before_trade
    assert final_events == before_events
