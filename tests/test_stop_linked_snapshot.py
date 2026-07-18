from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.broker_snapshot import validate_snapshot
from src.trade_state import (
    TradeStateError,
    TradeStateStore,
    TradeStatus,
)
from tests.snapshot_helpers import (
    apply_broker_snapshot,
    make_broker_snapshot,
    protected_account_state,
)


STRATEGY = "orb_long_v0"
OTHER_STRATEGY = "other_strategy"


def make_linked_trade(
    tmp_path,
    *,
    strategy: str = STRATEGY,
    symbol: str = "AAPL",
):
    store = TradeStateStore(
        tmp_path / f"{strategy}-{symbol}.sqlite"
    )
    trade = store.reserve_entry(
        strategy=strategy,
        symbol=symbol,
        session_date=datetime.now(
            timezone.utc
        ).date(),
        quantity=4,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"entry-{symbol}",
    )
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=4,
        average_entry_price=Decimal("100"),
    )
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=f"stop-{symbol}",
    )
    return store, trade


def table_count(store, table: str) -> int:
    with sqlite3.connect(store.db_path) as connection:
        return int(
            connection.execute(
                f"SELECT COUNT(*) FROM {table}"
            ).fetchone()[0]
        )


def test_linking_stop_does_not_claim_protection(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)

    assert trade.status == TradeStatus.STOP_LINKED
    assert trade.protected_quantity == 0
    assert trade.last_protection_reconciliation_id is None
    assert trade.protection_confirmed_at is None
    assert store.is_entry_halted(STRATEGY)


def test_complete_snapshot_is_only_path_to_open_protected(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    account_state = protected_account_state(trade)

    snapshot, issues = apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state,
        reconciliation_id="proof-cycle-1",
    )
    confirmed = store.get_trade(trade.trade_id)

    assert issues == ()
    assert confirmed.status == TradeStatus.OPEN_PROTECTED
    assert confirmed.protected_quantity == 4
    assert (
        confirmed.last_protection_reconciliation_id
        == "proof-cycle-1"
    )
    assert confirmed.protection_confirmed_at is not None
    assert not store.is_entry_halted(STRATEGY)
    assert table_count(
        store,
        "broker_reconciliation_runs",
    ) == 1
    assert table_count(
        store,
        "protection_reconciliation_events",
    ) == 1

    with sqlite3.connect(store.db_path) as connection:
        run = connection.execute(
            """
            SELECT account_fingerprint,
                   snapshot_valid,
                   safety_confirmed,
                   result_code
            FROM broker_reconciliation_runs
            WHERE reconciliation_id = ?
            """,
            (snapshot.metadata.reconciliation_id,),
        ).fetchone()

    assert run[0] != account_state.account.account_number
    assert len(run[0]) == 64
    assert run[1:] == (1, 1, "safety_confirmed")


def test_identical_snapshot_is_idempotent_across_restart(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    snapshot = make_broker_snapshot(
        protected_account_state(trade),
        reconciliation_id="stable-cycle",
    )
    evaluated_at = (
        snapshot.metadata.completed_at
        + timedelta(milliseconds=1)
    )

    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )
    first = store.get_trade(trade.trade_id)
    counts = (
        store.count_trade_events(),
        store.count_order_events(),
        store.count_entry_recovery_events(),
        table_count(store, "broker_reconciliation_runs"),
        table_count(
            store,
            "protection_reconciliation_events",
        ),
    )

    restarted = TradeStateStore(store.db_path)
    restarted.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )
    second = restarted.get_trade(trade.trade_id)

    assert second == first
    assert second.recovery_attempt_count == 0
    assert second.filled_quantity == 4
    assert second.protected_quantity == 4
    assert counts == (
        restarted.count_trade_events(),
        restarted.count_order_events(),
        restarted.count_entry_recovery_events(),
        table_count(
            restarted,
            "broker_reconciliation_runs",
        ),
        table_count(
            restarted,
            "protection_reconciliation_events",
        ),
    )


def test_changed_snapshot_creates_new_durable_failure(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    state = protected_account_state(trade)
    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=state,
        reconciliation_id="cycle-1",
    )
    invalid_stop = replace(
        state.open_orders[0],
        state="cancelled",
    )
    changed_state = replace(
        state,
        open_orders=(invalid_stop,),
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=changed_state,
        reconciliation_id="cycle-2",
    )
    persisted = store.get_trade(trade.trade_id)

    assert persisted.status == TradeStatus.STOP_LINKED
    assert persisted.protected_quantity == 0
    assert store.is_entry_halted(STRATEGY)
    assert table_count(
        store,
        "broker_reconciliation_runs",
    ) == 2
    assert table_count(
        store,
        "protection_reconciliation_events",
    ) == 2

    with sqlite3.connect(store.db_path) as connection:
        result = connection.execute(
            """
            SELECT result_code, confirmed
            FROM protection_reconciliation_events
            WHERE reconciliation_id = 'cycle-2'
            """
        ).fetchone()

    assert result == ("stop_state_invalid", 0)


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
        {"stop_price": None},
    ],
)
def test_invalid_stop_evidence_cannot_clear_halt(
    tmp_path,
    stop_changes,
):
    store, trade = make_linked_trade(tmp_path)
    state = protected_account_state(trade)
    invalid_state = replace(
        state,
        open_orders=(
            replace(
                state.open_orders[0],
                **stop_changes,
            ),
        ),
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=invalid_state,
    )

    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED
    assert store.is_entry_halted(STRATEGY)


def test_conflicting_sell_cannot_clear_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    state = protected_account_state(trade)
    conflict = replace(
        state.open_orders[0],
        order_id="other-active-sell",
    )
    state = replace(
        state,
        open_orders=state.open_orders + (conflict,),
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=state,
    )

    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED
    assert store.is_entry_halted(STRATEGY)


@pytest.mark.parametrize(
    "metadata_changes,expected_code",
    [
        (
            {"positions_complete": False},
            "positions_incomplete",
        ),
        (
            {"orders_complete": False},
            "orders_incomplete",
        ),
        (
            {"positions_cycle_id": "other-cycle"},
            "mixed_snapshot_cycles",
        ),
        (
            {"orders_cycle_id": "other-cycle"},
            "mixed_snapshot_cycles",
        ),
        (
            {"account_fingerprint_value": "wrong"},
            "account_fingerprint_mismatch",
        ),
    ],
)
def test_invalid_snapshot_metadata_cannot_clear_halt(
    tmp_path,
    metadata_changes,
    expected_code,
):
    store, trade = make_linked_trade(tmp_path)
    snapshot = make_broker_snapshot(
        protected_account_state(trade),
        **metadata_changes,
    )
    evaluated_at = (
        snapshot.metadata.completed_at
        + timedelta(milliseconds=1)
    )

    issues = store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )

    assert expected_code in {
        issue.code for issue in issues
    }
    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED
    assert store.is_entry_halted(STRATEGY)


def test_stale_snapshot_cannot_clear_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    now = datetime.now(timezone.utc)
    snapshot = make_broker_snapshot(
        protected_account_state(trade),
        started_at=now - timedelta(minutes=2),
        positions_read_at=(
            now - timedelta(minutes=2, seconds=-1)
        ),
        orders_read_at=(
            now - timedelta(minutes=2, seconds=-2)
        ),
        completed_at=(now - timedelta(minutes=1)),
    )

    validation = validate_snapshot(
        snapshot,
        evaluated_at=now,
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=now,
    )

    assert validation.result_code == "stale_snapshot"
    assert store.is_entry_halted(STRATEGY)


def test_naive_snapshot_timestamp_cannot_clear_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    snapshot = make_broker_snapshot(
        protected_account_state(trade)
    )
    snapshot = replace(
        snapshot,
        metadata=replace(
            snapshot.metadata,
            started_at=(
                snapshot.metadata.started_at
                .replace(tzinfo=None)
            ),
        ),
    )
    evaluated_at = (
        snapshot.metadata.completed_at
        + timedelta(milliseconds=1)
    )

    issues = store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )

    assert "naive_snapshot_timestamp" in {
        issue.code for issue in issues
    }
    assert store.is_entry_halted(STRATEGY)


def test_valid_flat_snapshot_clears_trade_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    flat_state = replace(
        protected_account_state(trade),
        positions=(),
        open_orders=(),
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=flat_state,
    )

    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.ERROR
    assert not store.is_entry_halted(STRATEGY)


def test_other_strategy_snapshot_cannot_clear_orb_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    flat_state = replace(
        protected_account_state(trade),
        positions=(),
        open_orders=(),
    )

    apply_broker_snapshot(
        store,
        strategy=OTHER_STRATEGY,
        account_state=flat_state,
    )

    assert store.is_entry_halted(STRATEGY)
    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED


def test_reconciliation_id_reuse_with_changed_evidence_fails_closed(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)
    state = protected_account_state(trade)
    snapshot = make_broker_snapshot(
        state,
        reconciliation_id="one-cycle",
    )
    evaluated_at = (
        snapshot.metadata.completed_at
        + timedelta(milliseconds=1)
    )
    store.apply_startup_reconciliation(
        strategy=STRATEGY,
        snapshot=snapshot,
        occurred_at=evaluated_at,
    )
    changed = replace(
        snapshot,
        account_state=replace(
            state,
            open_orders=(),
        ),
    )

    with pytest.raises(
        TradeStateError,
        match="reused with different broker evidence",
    ):
        store.apply_startup_reconciliation(
            strategy=STRATEGY,
            snapshot=changed,
            occurred_at=evaluated_at,
        )

    assert store.is_entry_halted(STRATEGY)
    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED
    assert table_count(
        store,
        "broker_reconciliation_runs",
    ) == 1


def test_plain_account_state_cannot_clear_halt(
    tmp_path,
):
    store, trade = make_linked_trade(tmp_path)

    with pytest.raises(
        TradeStateError,
        match="BrokerSnapshot",
    ):
        store.apply_startup_reconciliation(
            strategy=STRATEGY,
            snapshot=protected_account_state(trade),
        )

    assert store.is_entry_halted(STRATEGY)
    assert store.get_trade(
        trade.trade_id
    ).status == TradeStatus.STOP_LINKED
