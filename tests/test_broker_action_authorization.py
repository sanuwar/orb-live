from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import sqlite3

import pytest

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.broker_action_authorization import (
    BrokerActionAuthorizationError,
    BrokerActionAuthorizer,
    broker_action_plan_digest,
)
from src.broker_action_plan import (
    BrokerActionPlanner,
    BrokerPlanPurpose,
)
from src.broker_snapshot import account_fingerprint
from src.trade_state import (
    BrokerActionAuthorizationStatus,
    TradeStateError,
    TradeStateStore,
)
from tests.snapshot_helpers import (
    confirm_linked_stop,
    make_broker_snapshot,
)
from tests.test_entry_recovery import create_legacy_database


ACCOUNT_NUMBER = "TEST-AUTH-1234"
STRATEGY = "orb_long_v0"
NOW = datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc)


def make_store_and_trade(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = store.reserve_entry(
        strategy=STRATEGY,
        symbol="AAPL",
        session_date=date(2026, 7, 18),
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=NOW - timedelta(minutes=3),
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="fake-entry",
        occurred_at=NOW - timedelta(minutes=2),
    )
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
        occurred_at=NOW - timedelta(minutes=1),
    )
    return store, trade


def account_state(*, open_orders=(), account_number=ACCOUNT_NUMBER):
    return AccountState(
        account=AccountInfo(
            account_number=account_number,
            masked_account_number="test-account",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("10000"),
            equity_value=Decimal("1000"),
            cash=Decimal("9000"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("9000"),
            unleveraged_buying_power=Decimal("9000"),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=(
            EquityPosition(
                symbol="AAPL",
                quantity=Decimal("10"),
                intraday_quantity=Decimal("10"),
                average_buy_price=Decimal("100"),
                shares_available_for_sells=Decimal("10"),
                shares_held_for_sells=Decimal("0"),
                position_type="long",
            ),
        ),
        open_orders=tuple(open_orders),
    )


def stop_payload():
    return {
        "account_number": ACCOUNT_NUMBER,
        "symbol": "AAPL",
        "side": "sell",
        "type": "stop_market",
        "quantity": "10",
        "stop_price": "99",
    }


def exit_payload():
    return {
        "account_number": ACCOUNT_NUMBER,
        "symbol": "AAPL",
        "side": "sell",
        "type": "limit",
        "quantity": "10",
        "limit_price": "102",
    }


def open_stop_order():
    return EquityOrder(
        order_id="fake-stop",
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        state="confirmed",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("0"),
        limit_price=None,
        stop_price=Decimal("99"),
        trigger="stop",
        placed_agent="agentic",
    )


def make_plan(trade, *, payload=None, open_orders=()):
    return BrokerActionPlanner.plan_initial_protection(
        trade=trade,
        stop_payload=payload or stop_payload(),
        open_orders=open_orders,
    )


def make_snapshot(*, state=None, **changes):
    metadata = {
        "reconciliation_id": "recon-auth-1",
        "started_at": NOW - timedelta(seconds=1),
        "positions_read_at": NOW - timedelta(milliseconds=800),
        "orders_read_at": NOW - timedelta(milliseconds=600),
        "completed_at": NOW - timedelta(milliseconds=400),
    }
    metadata.update(changes)
    return make_broker_snapshot(
        state or account_state(),
        **metadata,
    )


def authorize(store, trade, *, plan=None, snapshot=None):
    return BrokerActionAuthorizer(store).authorize(
        plan=plan or make_plan(trade),
        strategy=STRATEGY,
        trade_id=trade.trade_id,
        snapshot=snapshot or make_snapshot(),
        expected_account_fingerprint=account_fingerprint(ACCOUNT_NUMBER),
        occurred_at=NOW,
    )


def test_authorization_binds_plan_to_fresh_durable_evidence(tmp_path):
    store, trade = make_store_and_trade(tmp_path)

    result = authorize(store, trade)
    record = result.authorization

    assert result.created is True
    assert record.status == BrokerActionAuthorizationStatus.AUTHORIZED
    assert record.trade_id == trade.trade_id
    assert record.strategy == STRATEGY
    assert record.purpose == BrokerPlanPurpose.PROTECT_FILLED_ENTRY.value
    assert record.source_trade_version == trade.version
    assert record.reconciliation_id == "recon-auth-1"
    assert record.plan_digest == result.plan_digest
    assert len(record.snapshot_digest) == 64
    assert record.account_fingerprint == account_fingerprint(ACCOUNT_NUMBER)
    assert NOW < record.expires_at <= NOW + timedelta(seconds=15)
    assert store.count_broker_action_authorizations() == 1
    assert store.count_broker_action_authorization_events() == 1
    assert ACCOUNT_NUMBER.encode() not in (tmp_path / "state.sqlite").read_bytes()


@pytest.mark.parametrize("purpose", ("replace", "exit"))
def test_protected_trade_replacement_and_exit_plans_are_rebuilt(
    tmp_path,
    purpose,
):
    store, trade = make_store_and_trade(tmp_path)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="fake-stop",
        occurred_at=NOW - timedelta(seconds=20),
    )
    trade = confirm_linked_stop(
        store,
        trade,
        account_number=ACCOUNT_NUMBER,
    )
    orders = (open_stop_order(),)
    if purpose == "replace":
        plan = BrokerActionPlanner.plan_stop_replacement(
            trade=trade,
            replacement_stop_payload={
                **stop_payload(),
                "stop_price": "99.50",
            },
            open_orders=orders,
        )
        expected = (
            BrokerPlanPurpose.REPLACE_PROTECTIVE_STOP.value
        )
    else:
        plan = BrokerActionPlanner.plan_exit(
            trade=trade,
            exit_payload=exit_payload(),
            open_orders=orders,
        )
        expected = BrokerPlanPurpose.EXIT_POSITION.value

    result = authorize(
        store,
        trade,
        plan=plan,
        snapshot=make_snapshot(
            state=account_state(open_orders=orders)
        ),
    )

    assert result.authorization.purpose == expected
    assert (
        result.authorization.source_trade_version
        == trade.version
    )


def test_identical_authorization_is_idempotent_across_restart(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    first = authorize(store, trade)

    restarted = TradeStateStore(tmp_path / "state.sqlite")
    second = authorize(restarted, restarted.get_trade(trade.trade_id))

    assert second.created is False
    assert second.authorization.authorization_id == first.authorization.authorization_id
    assert restarted.count_broker_action_authorizations() == 1
    assert restarted.count_broker_action_authorization_events() == 1


def test_authorization_retry_rejects_changed_snapshot_evidence(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    authorize(store, trade)
    changed = make_snapshot(
        positions_read_at=NOW - timedelta(milliseconds=700),
    )

    with pytest.raises(TradeStateError, match="persisted evidence"):
        authorize(store, trade, snapshot=changed)

def test_canonical_digest_is_stable_for_payload_key_order(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    payload = stop_payload()
    reversed_payload = dict(reversed(tuple(payload.items())))

    assert broker_action_plan_digest(make_plan(trade, payload=payload)) == (
        broker_action_plan_digest(make_plan(trade, payload=reversed_payload))
    )


@pytest.mark.parametrize(
    "snapshot",
    (
        make_snapshot(positions_complete=False),
        make_snapshot(orders_cycle_id="other-cycle"),
        make_snapshot(completed_at=NOW - timedelta(minutes=2)),
    ),
)
def test_incomplete_mixed_or_stale_snapshot_fails_closed(tmp_path, snapshot):
    store, trade = make_store_and_trade(tmp_path)

    with pytest.raises(BrokerActionAuthorizationError):
        authorize(store, trade, snapshot=snapshot)

    assert store.count_broker_action_authorizations() == 0


def test_snapshot_and_payload_must_match_expected_account(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    wrong_payload = {**stop_payload(), "account_number": "OTHER-ACCOUNT"}

    with pytest.raises(BrokerActionAuthorizationError, match="payload account"):
        authorize(store, trade, plan=make_plan(trade, payload=wrong_payload))

    with pytest.raises(BrokerActionAuthorizationError, match="expected account"):
        BrokerActionAuthorizer(store).authorize(
            plan=make_plan(trade),
            strategy=STRATEGY,
            trade_id=trade.trade_id,
            snapshot=make_snapshot(),
            expected_account_fingerprint=account_fingerprint("OTHER-ACCOUNT"),
            occurred_at=NOW,
        )


def test_changed_snapshot_or_plan_cannot_be_authorized(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    open_sell = EquityOrder(
        order_id="fake-existing-sell",
        symbol="AAPL",
        side="sell",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("101"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )

    with pytest.raises(BrokerActionAuthorizationError, match="rebuilt"):
        authorize(
            store,
            trade,
            snapshot=make_snapshot(state=account_state(open_orders=(open_sell,))),
        )

    changed_payload = {**stop_payload(), "stop_price": "98.50"}
    changed_plan = make_plan(trade, payload=changed_payload)
    tampered = replace(
        changed_plan,
        actions=(
            changed_plan.actions[0],
            replace(
                changed_plan.actions[1],
                payload=stop_payload(),
            ),
        ),
    )
    with pytest.raises(BrokerActionAuthorizationError, match="rebuilt"):
        authorize(store, trade, plan=tampered)


def test_malformed_blocked_and_entry_plans_fail_closed(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    valid = make_plan(trade)
    malformed = replace(
        valid,
        actions=(replace(valid.actions[0], sequence=2), valid.actions[1]),
    )

    with pytest.raises(BrokerActionAuthorizationError, match="contiguous"):
        authorize(store, trade, plan=malformed)

    blocked = make_plan(trade, open_orders=(EquityOrder(
        order_id="fake-sell",
        symbol="AAPL",
        side="sell",
        order_type="limit",
        state="confirmed",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("0"),
        limit_price=Decimal("101"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    ),))
    with pytest.raises(BrokerActionAuthorizationError, match="blocker-free"):
        authorize(store, trade, plan=blocked)

    with pytest.raises(BrokerActionAuthorizationError, match="reservation binding"):
        authorize(store, trade, plan=replace(valid, purpose=BrokerPlanPurpose.ENTRY))


def test_strategy_mismatch_and_nonpositive_ttl_fail_closed(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    authorizer = BrokerActionAuthorizer(store)

    with pytest.raises(BrokerActionAuthorizationError, match="strategy"):
        authorizer.authorize(
            plan=make_plan(trade),
            strategy="another_strategy",
            trade_id=trade.trade_id,
            snapshot=make_snapshot(),
            expected_account_fingerprint=account_fingerprint(ACCOUNT_NUMBER),
            occurred_at=NOW,
        )

    with pytest.raises(BrokerActionAuthorizationError, match="TTL"):
        authorizer.authorize(
            plan=make_plan(trade),
            strategy=STRATEGY,
            trade_id=trade.trade_id,
            snapshot=make_snapshot(),
            expected_account_fingerprint=account_fingerprint(ACCOUNT_NUMBER),
            occurred_at=NOW,
            authorization_ttl=timedelta(0),
        )


def test_claim_is_one_time_and_same_claim_replay_is_idempotent(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    result = authorize(store, trade)

    claimed, created = store.claim_broker_action_authorization(
        result.authorization.authorization_id,
        plan_digest=result.plan_digest,
        claim_id="attempt-1",
        occurred_at=NOW + timedelta(seconds=1),
    )
    replayed, replay_created = store.claim_broker_action_authorization(
        result.authorization.authorization_id,
        plan_digest=result.plan_digest,
        claim_id="attempt-1",
        occurred_at=NOW + timedelta(seconds=2),
    )

    assert created is True
    assert replay_created is False
    assert claimed.status == BrokerActionAuthorizationStatus.CLAIMED
    assert replayed == claimed
    assert store.count_broker_action_authorization_events() == 2

    with pytest.raises(TradeStateError, match="different attempt"):
        store.claim_broker_action_authorization(
            result.authorization.authorization_id,
            plan_digest=result.plan_digest,
            claim_id="attempt-2",
            occurred_at=NOW + timedelta(seconds=3),
        )

    with pytest.raises(TradeStateError, match="different evidence"):
        store.claim_broker_action_authorization(
            result.authorization.authorization_id,
            plan_digest="0" * 64,
            claim_id="attempt-1",
            occurred_at=NOW + timedelta(seconds=3),
        )


@pytest.mark.parametrize(
    "failure",
    ("expired", "predates", "digest", "trade_version"),
)
def test_failed_claim_is_durably_invalidated(tmp_path, failure):
    store, trade = make_store_and_trade(tmp_path)
    result = authorize(store, trade)
    digest = result.plan_digest
    claim_at = NOW + timedelta(seconds=1)

    if failure == "expired":
        claim_at = result.authorization.expires_at
    elif failure == "predates":
        claim_at = (
            result.authorization.authorized_at
            - timedelta(microseconds=1)
        )
    elif failure == "digest":
        digest = "0" * 64
    else:
        store.attach_protective_stop(
            trade.trade_id,
            broker_order_id="fake-stop",
            occurred_at=NOW + timedelta(milliseconds=500),
        )

    with pytest.raises(TradeStateError):
        store.claim_broker_action_authorization(
            result.authorization.authorization_id,
            plan_digest=digest,
            claim_id="attempt-1",
            occurred_at=claim_at,
        )

    persisted = store.get_broker_action_authorization(
        result.authorization.authorization_id
    )
    assert persisted.status == BrokerActionAuthorizationStatus.INVALIDATED
    assert persisted.invalidation_reason
    assert store.count_broker_action_authorization_events() == 2


def test_authorization_events_are_append_only(tmp_path):
    store, trade = make_store_and_trade(tmp_path)
    authorize(store, trade)

    with sqlite3.connect(tmp_path / "state.sqlite") as connection:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE broker_action_authorization_events SET event_type = 'changed'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM broker_action_authorization_events")


def test_legacy_migration_is_additive_idempotent_and_preserves_data(tmp_path):
    database = tmp_path / "legacy.sqlite"
    create_legacy_database(database)

    first = TradeStateStore(database)
    second = TradeStateStore(database)

    assert first.get_trade("legacy-trade").strategy == "orb_long_v0"
    assert second.count_broker_action_authorizations() == 0
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM trade_events").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM order_events").fetchone()[0] == 1
        tables = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        active_index = next(
            row
            for row in connection.execute(
                "PRAGMA index_list(broker_action_authorizations)"
            )
            if row[1] == "idx_one_active_broker_action_authorization"
        )
    assert "broker_action_authorizations" in tables
    assert "broker_action_authorization_events" in tables
    assert active_index[2] == 1
    assert active_index[4] == 1


def test_authorizer_has_no_broker_client_or_write_surface(tmp_path):
    store, _ = make_store_and_trade(tmp_path)
    authorizer = BrokerActionAuthorizer(store)

    assert vars(authorizer) == {"trade_store": store}
    for method in ("review", "place", "replace", "cancel", "submit"):
        assert not hasattr(authorizer, method)
