from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import sqlite3

import pytest

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.broker_action_saga import (
    ACTIVE_SAGA_STATUSES,
    BROKER_WRITE_ACTION_TYPES,
    BrokerActionSagaError,
    BrokerActionSagaStore,
    SagaActionStatus,
    SagaActionType,
    SagaHaltedError,
    TERMINAL_SAGA_STATUSES,
    action_payload_digest,
    canonical_action_payload,
    condition_satisfied,
)
from src.broker_snapshot import account_fingerprint
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_entry_recovery import create_legacy_database


ACCOUNT_NUMBER = "TEST-SAGA-1234"
STRATEGY = "orb_long_v0"
OTHER_STRATEGY = "orb_short_v0"
NOW = datetime(2026, 7, 18, 14, 0, tzinfo=timezone.utc)
FINGERPRINT = account_fingerprint(ACCOUNT_NUMBER)
SNAPSHOT_DIGEST = "d" * 64
PLAN_DIGEST = "c" * 64
RECONCILIATION_ID = "recon-baseline-1"


def make_filled_trade(store, *, symbol="AAPL", strategy=STRATEGY):
    trade = store.reserve_entry(
        strategy=strategy,
        symbol=symbol,
        session_date=date(2026, 7, 18),
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=NOW - timedelta(minutes=3),
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id=f"fake-entry-{symbol}",
        occurred_at=NOW - timedelta(minutes=2),
    )
    return store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
        occurred_at=NOW - timedelta(minutes=1),
    )


def make_stores(tmp_path, *, name="state.sqlite"):
    store = TradeStateStore(tmp_path / name)
    trade = make_filled_trade(store)
    saga = BrokerActionSagaStore(store)
    return store, saga, trade


def make_authorization(store, trade):
    authorization, _ = store.authorize_broker_action_plan(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        purpose="protect_filled_entry",
        plan_digest=PLAN_DIGEST,
        source_trade_version=trade.version,
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        authorized_at=NOW,
        expires_at=NOW + timedelta(seconds=15),
    )
    return authorization


def stop_payload(*, quantity="10", stop_price="99"):
    return {
        "symbol": "AAPL",
        "side": "sell",
        "type": "stop_market",
        "quantity": quantity,
        "stop_price": stop_price,
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }


def expected_stop_postcondition():
    return {
        "active_stop_present": True,
        "stop_quantity": "10",
        "no_conflicting_sell_order": True,
    }


def plan_stop_action(
    saga,
    trade,
    authorization,
    **overrides,
):
    arguments = dict(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=(
            SagaActionType.PLACE_INITIAL_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"initial-stop:{trade.trade_id}:10"
        ),
        payload=stop_payload(),
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition=(
            expected_stop_postcondition()
        ),
        authorization_id=(
            authorization.authorization_id
        ),
        side="sell",
        order_type="stop_market",
        trigger_type="stop",
        requested_quantity=10,
        stop_price=Decimal("99"),
        time_in_force="gfd",
        market_hours="regular_hours",
        occurred_at=NOW,
    )
    arguments.update(overrides)
    return saga.plan_action(**arguments)


def ready_stop_action(saga, trade, authorization):
    action, _ = plan_stop_action(
        saga, trade, authorization
    )
    return saga.evaluate_readiness(
        action.action_id, occurred_at=NOW
    )


def proof_account_state(
    *,
    account_number=ACCOUNT_NUMBER,
    stop_order_id="fake-stop-1",
):
    quantity = Decimal("10")
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
                quantity=quantity,
                intraday_quantity=quantity,
                average_buy_price=Decimal("100"),
                shares_available_for_sells=quantity,
                shares_held_for_sells=Decimal("0"),
                position_type="long",
            ),
        ),
        open_orders=(
            EquityOrder(
                order_id=stop_order_id,
                symbol="AAPL",
                side="sell",
                order_type="stop_market",
                state="confirmed",
                quantity=quantity,
                cumulative_quantity=Decimal("0"),
                limit_price=None,
                stop_price=Decimal("99"),
                trigger="stop",
                placed_agent="agentic",
            ),
        ),
    )


def proof_snapshot(**snapshot_overrides):
    account_state = snapshot_overrides.pop(
        "account_state", None
    ) or proof_account_state()
    return make_broker_snapshot(
        account_state,
        started_at=NOW,
        **snapshot_overrides,
    )


def good_observation():
    return {
        "active_stop_present": True,
        "stop_quantity": "10",
        "no_conflicting_sell_order": True,
        "resulting_broker_order_id": "fake-stop-1",
    }


def submitted_action(saga, trade, authorization, *, owner="executor-1"):
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner=owner,
        occurred_at=NOW,
    )
    action, attempt = saga.record_submission_intent(
        action.action_id,
        owner=owner,
        occurred_at=NOW + timedelta(seconds=1),
    )
    return action, attempt


def unknown_action(saga, trade, authorization, *, owner="executor-1"):
    action, _ = submitted_action(
        saga, trade, authorization, owner=owner
    )
    return saga.finalize_attempt(
        action.action_id,
        owner=owner,
        classification="timeout",
        occurred_at=NOW + timedelta(seconds=2),
    )


# ---------------------------------------------------------------
# Canonical payload and digest behavior
# ---------------------------------------------------------------


def test_equivalent_decimal_forms_share_one_digest():
    digests = {
        action_payload_digest(
            stop_payload(quantity=quantity)
        )
        for quantity in (
            "10",
            "10.0",
            "10.000000",
            10,
            Decimal("10.00"),
        )
    }
    assert len(digests) == 1


def test_meaningful_payload_changes_alter_digest():
    base = action_payload_digest(stop_payload())
    changed = {
        "quantity": "11",
        "stop_price": "98",
    }
    for field, value in changed.items():
        payload = stop_payload()
        payload[field] = value
        assert (
            action_payload_digest(payload) != base
        )
    for field, value in (
        ("symbol", "MSFT"),
        ("side", "buy"),
        ("type", "limit"),
        ("time_in_force", "gtc"),
        ("market_hours", "extended_hours"),
    ):
        payload = stop_payload()
        payload[field] = value
        assert (
            action_payload_digest(payload) != base
        )


def test_canonical_payload_rejects_sensitive_values():
    for forbidden in (
        {"account_number": "12345678"},
        {"ref_id": "some-ref"},
        {"token": "secret"},
        {"observed_at": "2026-07-18T14:00:00"},
        {"local_timestamp": "now"},
    ):
        payload = stop_payload()
        payload.update(forbidden)
        with pytest.raises(BrokerActionSagaError):
            canonical_action_payload(payload)


def test_canonical_payload_orders_keys_deterministically():
    payload = stop_payload()
    reversed_payload = dict(
        reversed(list(payload.items()))
    )
    assert action_payload_digest(
        payload
    ) == action_payload_digest(reversed_payload)


def test_condition_matching_normalizes_numeric_facts():
    assert condition_satisfied(
        {"stop_quantity": "10"},
        {"stop_quantity": Decimal("10.0")},
    )
    assert not condition_satisfied(
        {"stop_quantity": "10"},
        {"stop_quantity": "11"},
    )
    assert not condition_satisfied(
        {"stop_quantity": "10"},
        {},
    )


# ---------------------------------------------------------------
# Planning, deduplication, and ref_id stability
# ---------------------------------------------------------------


def test_identical_logical_planning_is_idempotent(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)

    first, created_first = plan_stop_action(
        saga, trade, authorization
    )
    second, created_second = plan_stop_action(
        saga, trade, authorization
    )

    assert created_first is True
    assert created_second is False
    assert first.action_id == second.action_id
    assert first.ref_id == second.ref_id
    assert (
        first.payload_digest
        == second.payload_digest
    )
    assert saga.count_saga_actions() == 1


def test_changed_payload_cannot_reuse_logical_action(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)

    plan_stop_action(saga, trade, authorization)
    with pytest.raises(
        BrokerActionSagaError,
        match="different payload",
    ):
        plan_stop_action(
            saga,
            trade,
            authorization,
            payload=stop_payload(stop_price="98"),
        )
    assert saga.count_saga_actions() == 1


def test_completed_action_does_not_silently_reopen(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.cancel_action(
        action.action_id,
        reason="Test cancellation.",
        occurred_at=NOW,
    )

    replanned, created = plan_stop_action(
        saga, trade, authorization
    )
    assert created is False
    assert replanned.action_id == action.action_id
    assert (
        replanned.status
        == SagaActionStatus.CANCELLED
    )
    assert saga.count_saga_actions() == 1


def test_duplicate_exit_action_is_prevented(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    exit_payload = {
        "symbol": "AAPL",
        "side": "sell",
        "type": "limit",
        "quantity": "10",
        "limit_price": "102",
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
    }
    first, created_first = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{trade.trade_id}:profit-target"
        ),
        payload=exit_payload,
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "exit_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        close_reason="profit_target",
        occurred_at=NOW,
    )
    second, created_second = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{trade.trade_id}:profit-target"
        ),
        payload=dict(exit_payload),
        reconciliation_id="recon-later",
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "exit_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        close_reason="profit_target",
        occurred_at=NOW + timedelta(seconds=5),
    )

    assert created_first is True
    assert created_second is False
    assert first.action_id == second.action_id
    assert first.close_reason == "profit_target"


def test_exit_action_preserves_durable_close_reason(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{trade.trade_id}:time-exit"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "101",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "exit_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        close_reason="time_exit",
        occurred_at=NOW,
    )

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    restored = reopened.get_action(
        action.action_id
    )
    assert restored.close_reason == "time_exit"


def test_broker_write_actions_require_authorization(tmp_path):
    store, saga, trade = make_stores(tmp_path)

    for action_type in BROKER_WRITE_ACTION_TYPES:
        with pytest.raises(
            BrokerActionSagaError,
            match="authorization",
        ):
            saga.plan_action(
                strategy=trade.strategy,
                trade_id=trade.trade_id,
                action_type=action_type,
                logical_action_key=(
                    f"unauthorized:{action_type.value}"
                ),
                payload=stop_payload(),
                reconciliation_id=RECONCILIATION_ID,
                snapshot_digest=SNAPSHOT_DIGEST,
                account_fingerprint=FINGERPRINT,
                expected_precondition={},
                expected_postcondition={},
                occurred_at=NOW,
            )
    assert saga.count_saga_actions() == 0


def test_reconcile_only_action_requires_no_authorization(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    action, created = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.RECONCILE_ONLY,
        logical_action_key=(
            f"reconcile:{trade.trade_id}"
        ),
        payload={"symbol": "AAPL"},
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={},
        occurred_at=NOW,
    )
    assert created is True
    assert action.authorization_id is None


def test_planning_rolls_back_on_invalid_dependency(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)

    with pytest.raises(
        BrokerActionSagaError,
        match="dependency was not found",
    ):
        plan_stop_action(
            saga,
            trade,
            authorization,
            depends_on=("missing-action",),
        )
    assert saga.count_saga_actions() == 0
    assert saga.count_saga_action_events() == 0


def test_ref_id_is_stable_unique_and_safe(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = plan_stop_action(
        saga, trade, authorization
    )

    assert action.ref_id.startswith("orb-saga-")
    assert ACCOUNT_NUMBER not in action.ref_id
    assert FINGERPRINT not in action.ref_id

    other_action, _ = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{trade.trade_id}:profit-target"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "102",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "exit_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        occurred_at=NOW,
    )
    assert other_action.ref_id != action.ref_id


def test_same_ref_id_with_different_payload_fails_closed(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )

    tampered_digest = "e" * 64
    with sqlite3.connect(
        tmp_path / "state.sqlite"
    ) as connection:
        connection.execute(
            """
            UPDATE broker_saga_actions
            SET payload_digest = ?
            WHERE action_id = ?
            """,
            (tampered_digest, action.action_id),
        )

    with pytest.raises(
        BrokerActionSagaError,
        match="does not match its digest",
    ):
        saga.verify_action_integrity(
            action.action_id, occurred_at=NOW
        )
    assert saga.is_broker_action_halted(STRATEGY)


# ---------------------------------------------------------------
# Restart durability
# ---------------------------------------------------------------


def test_restart_preserves_planned_action_state(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    restored = reopened.get_action(
        action.action_id
    )

    assert (
        restored.status
        == SagaActionStatus.CLAIMED
    )
    assert restored.ref_id == action.ref_id
    assert (
        restored.payload_digest
        == action.payload_digest
    )
    assert restored.claim_owner == "executor-1"
    assert restored.claimed_at is not None
    assert restored.claim_expires_at is not None


def test_restart_preserves_dependencies(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    upstream, _ = plan_stop_action(
        saga, trade, authorization
    )
    downstream, _ = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{trade.trade_id}:protected"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "102",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "exit_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        depends_on=(upstream.action_id,),
        occurred_at=NOW,
    )

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    assert reopened.get_dependencies(
        downstream.action_id
    ) == (upstream.action_id,)


def test_restart_preserves_unknown_outcome(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )
    assert (
        action.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    restored = reopened.get_action(
        action.action_id
    )
    assert (
        restored.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert reopened.is_broker_action_halted(
        STRATEGY
    )


def test_saga_initialization_is_idempotent(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    plan_stop_action(saga, trade, authorization)
    actions_before = saga.count_saga_actions()
    events_before = saga.count_saga_action_events()

    for _ in range(3):
        reopened = BrokerActionSagaStore(
            TradeStateStore(
                tmp_path / "state.sqlite"
            )
        )
        assert (
            reopened.count_saga_actions()
            == actions_before
        )
        assert (
            reopened.count_saga_action_events()
            == events_before
        )


# ---------------------------------------------------------------
# Claiming and concurrency
# ---------------------------------------------------------------


def test_only_ready_actions_can_be_claimed(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = plan_stop_action(
        saga, trade, authorization
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="Only READY",
    ):
        saga.claim_action(
            action.action_id,
            owner="executor-1",
            occurred_at=NOW,
        )


def test_simultaneous_claims_produce_one_winner(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    second_view = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )

    _, claimed = saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    assert claimed is True
    with pytest.raises(
        BrokerActionSagaError,
        match="unexpired claim",
    ):
        second_view.claim_action(
            action.action_id,
            owner="executor-2",
            occurred_at=NOW + timedelta(seconds=1),
        )


def test_unexpired_claim_blocks_other_owner(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        ttl=timedelta(seconds=30),
        occurred_at=NOW,
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="unexpired claim",
    ):
        saga.claim_action(
            action.action_id,
            owner="executor-2",
            occurred_at=NOW + timedelta(seconds=10),
        )


def test_same_owner_repeated_claim_is_idempotent(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    first, created = saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    second, reclaimed = saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW + timedelta(seconds=5),
    )

    assert created is True
    assert reclaimed is False
    assert first.version == second.version
    assert (
        second.claim_owner == "executor-1"
    )


def test_expired_claim_requires_explicit_recovery(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        ttl=timedelta(seconds=5),
        occurred_at=NOW,
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="explicit recovery",
    ):
        saga.claim_action(
            action.action_id,
            owner="executor-2",
            occurred_at=NOW + timedelta(minutes=1),
        )

    recovered = saga.recover_expired_claim(
        action.action_id,
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert (
        recovered.status == SagaActionStatus.READY
    )
    assert recovered.claim_owner is None

    _, claimed = saga.claim_action(
        action.action_id,
        owner="executor-2",
        occurred_at=NOW + timedelta(minutes=2),
    )
    assert claimed is True


def test_expired_claim_after_intent_becomes_unknown(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        ttl=timedelta(seconds=5),
        occurred_at=NOW,
    )
    saga.record_submission_intent(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW + timedelta(seconds=1),
    )

    recovered = saga.recover_expired_claim(
        action.action_id,
        occurred_at=NOW + timedelta(minutes=1),
    )
    assert (
        recovered.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert saga.is_broker_action_halted(STRATEGY)
    attempts = saga.get_attempts(action.action_id)
    assert attempts[-1].finalized is True
    assert attempts[-1].result == "unknown"


def test_claim_release_is_audited(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    released = saga.release_claim(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert (
        released.status == SagaActionStatus.READY
    )
    assert released.claim_owner is None

    with sqlite3.connect(
        tmp_path / "state.sqlite"
    ) as connection:
        connection.row_factory = sqlite3.Row
        events = connection.execute(
            """
            SELECT event_type
            FROM broker_saga_action_events
            WHERE action_id = ?
            ORDER BY event_id
            """,
            (action.action_id,),
        ).fetchall()
    assert "saga_claim_released" in {
        row["event_type"] for row in events
    }


# ---------------------------------------------------------------
# Attempts and submission intent
# ---------------------------------------------------------------


def test_submission_intent_is_persisted_before_any_submission(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, attempt = submitted_action(
        saga, trade, authorization
    )

    assert (
        action.status
        == SagaActionStatus
        .SUBMISSION_INTENT_RECORDED
    )
    assert action.submission_intent_at is not None
    assert (
        attempt.submission_intent_at is not None
    )
    assert attempt.finalized is False
    assert (
        attempt.response_classification is None
    )
    assert attempt.ref_id == action.ref_id
    assert (
        attempt.payload_digest
        == action.payload_digest
    )


def test_attempt_numbers_are_monotonic_and_unique(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    saga.resolve_unknown_outcome(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={"effect_present": False},
    )
    saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=NOW + timedelta(seconds=30),
    )
    saga.approve_retry(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=31),
    )
    saga.claim_action(
        action.action_id,
        owner="executor-2",
        occurred_at=NOW + timedelta(seconds=32),
    )
    saga.record_submission_intent(
        action.action_id,
        owner="executor-2",
        occurred_at=NOW + timedelta(seconds=33),
    )

    attempts = saga.get_attempts(action.action_id)
    assert [
        attempt.attempt_number
        for attempt in attempts
    ] == [1, 2]
    assert attempts[0].finalized is True
    assert attempts[1].finalized is False
    refreshed = saga.get_action(action.action_id)
    assert refreshed.attempt_count == 2
    assert refreshed.ref_id == action.ref_id


def test_finalized_attempts_are_append_only(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )
    attempts = saga.get_attempts(action.action_id)
    attempt_id = attempts[-1].attempt_id

    with sqlite3.connect(
        tmp_path / "state.sqlite"
    ) as connection:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="cannot change",
        ):
            connection.execute(
                """
                UPDATE broker_saga_action_attempts
                SET result = 'confirmed_succeeded'
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            connection.execute(
                """
                DELETE FROM
                    broker_saga_action_attempts
                WHERE attempt_id = ?
                """,
                (attempt_id,),
            )


def test_attempt_count_changes_only_with_persisted_attempts(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    assert action.attempt_count == 0
    assert saga.count_saga_attempts() == 0

    saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    assert (
        saga.get_action(
            action.action_id
        ).attempt_count
        == 0
    )

    saga.record_submission_intent(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW + timedelta(seconds=1),
    )
    refreshed = saga.get_action(action.action_id)
    assert refreshed.attempt_count == 1
    assert saga.count_saga_attempts() == 1


def test_submission_intent_requires_active_claim_owner(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="active claim",
    ):
        saga.record_submission_intent(
            action.action_id,
            owner="executor-1",
            occurred_at=NOW,
        )

    saga.claim_action(
        action.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="active claim",
    ):
        saga.record_submission_intent(
            action.action_id,
            owner="executor-2",
            occurred_at=NOW + timedelta(seconds=1),
        )


def test_intent_on_expired_claim_is_rejected(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.claim_action(
        action.action_id,
        owner="executor-1",
        ttl=timedelta(seconds=5),
        occurred_at=NOW,
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="expired claim",
    ):
        saga.record_submission_intent(
            action.action_id,
            owner="executor-1",
            occurred_at=NOW + timedelta(minutes=1),
        )


# ---------------------------------------------------------------
# Unknown outcome protocol
# ---------------------------------------------------------------


def test_crash_after_submission_intent_becomes_unknown(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = submitted_action(
        saga, trade, authorization
    )

    marked = saga.mark_outcome_unknown(
        action.action_id,
        reason="Simulated crash after intent.",
        occurred_at=NOW + timedelta(seconds=2),
    )
    assert (
        marked.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert saga.is_broker_action_halted(STRATEGY)
    assert store.is_entry_halted(STRATEGY)


def test_unknown_outcome_cannot_be_resubmitted(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    with pytest.raises(BrokerActionSagaError):
        saga.claim_action(
            action.action_id,
            owner="executor-2",
            occurred_at=NOW + timedelta(seconds=5),
        )
    with pytest.raises(
        BrokerActionSagaError,
        match="proof of no effect",
    ):
        saga.approve_retry(
            action.action_id,
            occurred_at=NOW + timedelta(seconds=5),
        )
    with pytest.raises(BrokerActionSagaError):
        saga.record_submission_intent(
            action.action_id,
            owner="executor-1",
            occurred_at=NOW + timedelta(seconds=5),
        )


def test_fresh_proof_resolves_unknown_as_performed(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    resolved = saga.resolve_unknown_outcome(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={
            "effect_present": True,
            "resulting_broker_order_id": (
                "fake-stop-1"
            ),
        },
    )
    assert (
        resolved.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert (
        resolved.resulting_broker_order_id
        == "fake-stop-1"
    )


def test_fresh_proof_resolves_unknown_as_not_performed(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    resolved = saga.resolve_unknown_outcome(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={"effect_present": False},
    )
    assert (
        resolved.status
        == SagaActionStatus.CONFIRMED_NOT_PERFORMED
    )


def test_ambiguous_proof_preserves_unknown_and_halt(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    resolved = saga.resolve_unknown_outcome(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={"effect_present": None},
    )
    assert (
        resolved.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert saga.is_broker_action_halted(STRATEGY)


def test_unknown_resolution_requires_fresh_matching_snapshot(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    stale = proof_snapshot()
    with pytest.raises(
        BrokerActionSagaError,
        match="older than the permitted",
    ):
        saga.resolve_unknown_outcome(
            action.action_id,
            snapshot=stale,
            evaluated_at=(
                stale.metadata.completed_at
                + timedelta(minutes=5)
            ),
            observation={"effect_present": False},
        )

    mismatched = proof_snapshot(
        account_state=proof_account_state(
            account_number="OTHER-ACCOUNT"
        )
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="commissioned account fingerprint",
    ):
        saga.resolve_unknown_outcome(
            action.action_id,
            snapshot=mismatched,
            evaluated_at=(
                mismatched.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation={"effect_present": False},
        )
    assert (
        saga.get_action(action.action_id).status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )


def test_controlled_retry_only_after_proof_of_no_effect(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    saga.resolve_unknown_outcome(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={"effect_present": False},
    )
    with pytest.raises(
        SagaHaltedError,
        match="cleared before a controlled retry",
    ):
        saga.approve_retry(
            action.action_id,
            occurred_at=NOW + timedelta(seconds=30),
        )
    saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=NOW + timedelta(seconds=30),
    )
    retried = saga.approve_retry(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=31),
    )
    assert (
        retried.status == SagaActionStatus.READY
    )
    assert retried.ref_id == action.ref_id


# ---------------------------------------------------------------
# Broker confirmation proof
# ---------------------------------------------------------------


def confirmable_action(saga, trade, authorization):
    action, _ = submitted_action(
        saga, trade, authorization
    )
    return saga.finalize_attempt(
        action.action_id,
        owner="executor-1",
        classification="acknowledged",
        resulting_broker_order_id="fake-stop-1",
        occurred_at=NOW + timedelta(seconds=2),
    )


def test_local_acknowledgment_cannot_confirm_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    assert (
        action.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert action.confirmed_at is None
    attempts = saga.get_attempts(action.action_id)
    assert (
        attempts[-1].response_classification
        == "acknowledged"
    )


def test_fresh_snapshot_proof_confirms_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    confirmed = saga.confirm_action_succeeded(
        action.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation=good_observation(),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )
    assert confirmed.confirmed_at is not None
    assert (
        confirmed.confirmation_reconciliation_id
        == snapshot.metadata.reconciliation_id
    )
    assert (
        confirmed.confirmation_snapshot_digest
        is not None
    )


def test_stale_snapshot_cannot_confirm_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    with pytest.raises(
        BrokerActionSagaError,
        match="older than the permitted",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=snapshot,
            evaluated_at=(
                snapshot.metadata.completed_at
                + timedelta(minutes=10)
            ),
            observation=good_observation(),
        )
    assert (
        saga.get_action(action.action_id).status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )


def test_incomplete_snapshot_cannot_confirm_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot(
        orders_complete=False
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="incomplete",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=snapshot,
            evaluated_at=(
                snapshot.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation=good_observation(),
        )


def test_mixed_account_snapshot_cannot_confirm_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    inconsistent = proof_snapshot(
        account_fingerprint_value=(
            account_fingerprint("OTHER-ACCOUNT")
        )
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="does not match its evidence",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=inconsistent,
            evaluated_at=(
                inconsistent.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation=good_observation(),
        )

    other_account = proof_snapshot(
        account_state=proof_account_state(
            account_number="OTHER-ACCOUNT"
        )
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="commissioned account fingerprint",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=other_account,
            evaluated_at=(
                other_account.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation=good_observation(),
        )


def test_conflicting_evidence_cannot_confirm_success(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = confirmable_action(
        saga, trade, authorization
    )

    snapshot = proof_snapshot()
    conflicting = good_observation()
    conflicting["no_conflicting_sell_order"] = False
    with pytest.raises(
        BrokerActionSagaError,
        match="does not prove",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=snapshot,
            evaluated_at=(
                snapshot.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation=conflicting,
        )

    wrong_quantity = good_observation()
    wrong_quantity["stop_quantity"] = "7"
    with pytest.raises(
        BrokerActionSagaError,
        match="does not prove",
    ):
        saga.confirm_action_succeeded(
            action.action_id,
            snapshot=snapshot,
            evaluated_at=(
                snapshot.metadata.completed_at
                + timedelta(seconds=1)
            ),
            observation=wrong_quantity,
        )
    assert (
        saga.get_action(action.action_id).status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )


# ---------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------


def make_dependent_pair(store, saga, trade, authorization):
    upstream, _ = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=(
            SagaActionType.CANCEL_ENTRY_REMAINDER
        ),
        logical_action_key=(
            f"cancel-remainder:{trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "4",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "entry_remainder_inactive": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        source_broker_order_id="fake-entry-AAPL",
        occurred_at=NOW,
    )
    downstream, _ = plan_stop_action(
        saga,
        trade,
        authorization,
        depends_on=(upstream.action_id,),
    )
    return upstream, downstream


def test_dependency_blocks_downstream_action(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    upstream, downstream = make_dependent_pair(
        store, saga, trade, authorization
    )

    evaluated = saga.evaluate_readiness(
        downstream.action_id, occurred_at=NOW
    )
    assert (
        evaluated.status
        == SagaActionStatus.BLOCKED
    )
    assert (
        "not confirmed"
        in evaluated.blocked_reason
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="Only READY",
    ):
        saga.claim_action(
            downstream.action_id,
            owner="executor-1",
            occurred_at=NOW,
        )


def test_completed_dependency_unlocks_downstream(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    upstream, downstream = make_dependent_pair(
        store, saga, trade, authorization
    )

    saga.evaluate_readiness(
        upstream.action_id, occurred_at=NOW
    )
    saga.claim_action(
        upstream.action_id,
        owner="executor-1",
        occurred_at=NOW,
    )
    saga.record_submission_intent(
        upstream.action_id,
        owner="executor-1",
        occurred_at=NOW + timedelta(seconds=1),
    )
    saga.finalize_attempt(
        upstream.action_id,
        owner="executor-1",
        classification="acknowledged",
        occurred_at=NOW + timedelta(seconds=2),
    )
    snapshot = proof_snapshot()
    saga.confirm_action_succeeded(
        upstream.action_id,
        snapshot=snapshot,
        evaluated_at=(
            snapshot.metadata.completed_at
            + timedelta(seconds=1)
        ),
        observation={
            "entry_remainder_inactive": True,
        },
    )

    evaluated = saga.evaluate_readiness(
        downstream.action_id,
        occurred_at=NOW + timedelta(seconds=3),
    )
    assert (
        evaluated.status == SagaActionStatus.READY
    )


def test_failed_dependency_blocks_downstream(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    upstream, downstream = make_dependent_pair(
        store, saga, trade, authorization
    )

    saga.cancel_action(
        upstream.action_id,
        reason="Upstream abandoned in test.",
        occurred_at=NOW,
    )
    evaluated = saga.evaluate_readiness(
        downstream.action_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert (
        evaluated.status
        == SagaActionStatus.BLOCKED
    )
    assert "failed" in evaluated.blocked_reason


def test_partial_entry_recovery_sequence_is_deterministic(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    upstream, downstream = make_dependent_pair(
        store, saga, trade, authorization
    )

    assert saga.get_dependencies(
        downstream.action_id
    ) == (upstream.action_id,)
    assert (
        upstream.action_type
        == SagaActionType.CANCEL_ENTRY_REMAINDER
    )
    assert (
        downstream.action_type
        == SagaActionType
        .PLACE_INITIAL_PROTECTIVE_STOP
    )

    replayed_upstream, created = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=(
            SagaActionType.CANCEL_ENTRY_REMAINDER
        ),
        logical_action_key=(
            f"cancel-remainder:{trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "4",
        },
        reconciliation_id="recon-after-restart",
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "entry_remainder_inactive": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        source_broker_order_id="fake-entry-AAPL",
        occurred_at=NOW + timedelta(seconds=10),
    )
    assert created is False
    assert (
        replayed_upstream.action_id
        == upstream.action_id
    )


# ---------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------


def test_initial_stop_requires_exact_unprotected_quantity(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = plan_stop_action(
        saga,
        trade,
        authorization,
        expected_precondition={
            "unprotected_quantity": "10",
        },
    )

    blocked = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW,
        precondition_observation={
            "unprotected_quantity": "7",
        },
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )

    ready = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=1),
        precondition_observation={
            "unprotected_quantity": "10.0",
        },
    )
    assert ready.status == SagaActionStatus.READY


def test_replacement_stop_requires_exact_remaining_quantity(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action, _ = saga.plan_action(
        strategy=trade.strategy,
        trade_id=trade.trade_id,
        action_type=(
            SagaActionType
            .PLACE_REPLACEMENT_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"replacement-stop:{trade.trade_id}:6"
        ),
        payload=stop_payload(
            quantity="6", stop_price="100"
        ),
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={
            "remaining_position_quantity": "6",
            "ambiguous_sell_exposure": False,
        },
        expected_postcondition={
            "active_stop_present": True,
            "stop_quantity": "6",
        },
        authorization_id=(
            authorization.authorization_id
        ),
        requested_quantity=6,
        occurred_at=NOW,
    )

    blocked = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW,
        precondition_observation={
            "remaining_position_quantity": "6",
            "ambiguous_sell_exposure": True,
        },
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )

    ready = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=1),
        precondition_observation={
            "remaining_position_quantity": "6",
            "ambiguous_sell_exposure": False,
        },
    )
    assert ready.status == SagaActionStatus.READY


# ---------------------------------------------------------------
# Halts
# ---------------------------------------------------------------


def test_global_action_halt_survives_restart(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    saga.activate_broker_action_halt(
        strategy=STRATEGY,
        reason="Durable halt for testing.",
        halt_key="manual-test-halt",
        occurred_at=NOW,
    )

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    halt = reopened.get_broker_action_halt(
        STRATEGY
    )
    assert halt.active is True
    assert (
        "Durable halt for testing."
        in halt.reasons
    )


def test_halt_blocks_claims_and_readiness(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = ready_stop_action(
        saga, trade, authorization
    )
    saga.activate_broker_action_halt(
        strategy=STRATEGY,
        reason="Halted for testing.",
        halt_key="manual-test-halt",
        occurred_at=NOW,
    )

    with pytest.raises(SagaHaltedError):
        saga.claim_action(
            action.action_id,
            owner="executor-1",
            occurred_at=NOW + timedelta(seconds=1),
        )
    blocked = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )


def test_operator_ack_alone_cannot_clear_unknown_outcome(
    tmp_path,
):
    store, saga, trade = make_stores(tmp_path)
    authorization = make_authorization(store, trade)
    action = unknown_action(
        saga, trade, authorization
    )

    acknowledged = saga.acknowledge_operator(
        action.action_id,
        note="Operator saw the alert.",
        occurred_at=NOW + timedelta(seconds=5),
    )
    assert (
        acknowledged.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert saga.is_broker_action_halted(STRATEGY)

    with pytest.raises(
        BrokerActionSagaError,
        match="remains unknown",
    ):
        saga.clear_broker_action_halt(
            strategy=STRATEGY,
            halt_key=(
                f"saga-action:{action.action_id}"
            ),
            occurred_at=NOW + timedelta(seconds=6),
        )


def test_another_strategy_cannot_clear_the_halt(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    saga.activate_broker_action_halt(
        strategy=STRATEGY,
        reason="Strategy-owned halt.",
        halt_key="strategy-owned-halt",
        occurred_at=NOW,
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="own strategy",
    ):
        saga.clear_broker_action_halt(
            strategy=OTHER_STRATEGY,
            halt_key="strategy-owned-halt",
            occurred_at=NOW + timedelta(seconds=1),
        )
    assert saga.is_broker_action_halted(STRATEGY)


def test_halts_are_strategy_isolated(tmp_path):
    store, saga, trade = make_stores(tmp_path)
    other_trade = store.reserve_entry(
        strategy=OTHER_STRATEGY,
        symbol="MSFT",
        session_date=date(2026, 7, 18),
        quantity=5,
        planned_entry=Decimal("50"),
        initial_stop=Decimal("49"),
        target_price=Decimal("52"),
        occurred_at=NOW - timedelta(minutes=3),
    )
    saga.activate_broker_action_halt(
        strategy=STRATEGY,
        reason="Only the first strategy halts.",
        halt_key="first-strategy-halt",
        occurred_at=NOW,
    )

    assert saga.is_broker_action_halted(STRATEGY)
    assert not saga.is_broker_action_halted(
        OTHER_STRATEGY
    )

    action, _ = saga.plan_action(
        strategy=OTHER_STRATEGY,
        trade_id=other_trade.trade_id,
        action_type=SagaActionType.RECONCILE_ONLY,
        logical_action_key=(
            f"reconcile:{other_trade.trade_id}"
        ),
        payload={"symbol": "MSFT"},
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={},
        occurred_at=NOW,
    )
    evaluated = saga.evaluate_readiness(
        action.action_id, occurred_at=NOW
    )
    assert (
        evaluated.status == SagaActionStatus.READY
    )


def test_entry_actions_respect_durable_strategy_halt(tmp_path):
    store = TradeStateStore(
        tmp_path / "entry-state.sqlite"
    )
    saga = BrokerActionSagaStore(store)
    entry_trade = store.reserve_entry(
        strategy=STRATEGY,
        symbol="NVDA",
        session_date=date(2026, 7, 18),
        quantity=5,
        planned_entry=Decimal("500"),
        initial_stop=Decimal("495"),
        target_price=Decimal("510"),
        occurred_at=NOW - timedelta(minutes=3),
    )
    authorization, _ = (
        store.authorize_broker_action_plan(
            strategy=STRATEGY,
            trade_id=entry_trade.trade_id,
            purpose="entry",
            plan_digest=PLAN_DIGEST,
            source_trade_version=(
                entry_trade.version
            ),
            reconciliation_id=RECONCILIATION_ID,
            snapshot_digest=SNAPSHOT_DIGEST,
            account_fingerprint=FINGERPRINT,
            authorized_at=NOW,
            expires_at=NOW + timedelta(seconds=15),
        )
    )
    action, _ = saga.plan_action(
        strategy=STRATEGY,
        trade_id=entry_trade.trade_id,
        action_type=SagaActionType.PLACE_ENTRY,
        logical_action_key=(
            f"entry:{entry_trade.trade_id}"
        ),
        payload={
            "symbol": "NVDA",
            "side": "buy",
            "type": "limit",
            "quantity": "5",
            "limit_price": "500",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "entry_order_present": True,
        },
        authorization_id=(
            authorization.authorization_id
        ),
        occurred_at=NOW,
    )

    assert (
        saga.evaluate_readiness(
            action.action_id, occurred_at=NOW
        ).status
        == SagaActionStatus.READY
    )

    with sqlite3.connect(
        tmp_path / "entry-state.sqlite"
    ) as connection:
        connection.execute(
            """
            INSERT INTO entry_halts (
                halt_key, strategy, trade_id,
                active, reason,
                operator_ack_required,
                created_at, updated_at, cleared_at
            )
            VALUES (
                'test-entry-halt', ?, NULL, 1,
                'Durable strategy halt.', 0, ?, ?,
                NULL
            )
            """,
            (
                STRATEGY,
                NOW.isoformat(),
                NOW.isoformat(),
            ),
        )

    blocked = saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )
    assert (
        "strategy halt" in blocked.blocked_reason
    )


# ---------------------------------------------------------------
# Migration safety
# ---------------------------------------------------------------


def test_migration_is_additive_and_preserves_history(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    with sqlite3.connect(
        database_path
    ) as connection:
        trade_events_before = connection.execute(
            "SELECT COUNT(*) FROM trade_events"
        ).fetchone()[0]
        order_events_before = connection.execute(
            "SELECT COUNT(*) FROM order_events"
        ).fetchone()[0]

    store = TradeStateStore(database_path)
    saga = BrokerActionSagaStore(store)

    legacy_trade = store.get_trade("legacy-trade")
    assert legacy_trade.status == TradeStatus.CLOSED
    assert legacy_trade.version == 4

    with sqlite3.connect(
        database_path
    ) as connection:
        trade_events_after = connection.execute(
            "SELECT COUNT(*) FROM trade_events"
        ).fetchone()[0]
        order_events_after = connection.execute(
            "SELECT COUNT(*) FROM order_events"
        ).fetchone()[0]
    assert (
        trade_events_after == trade_events_before
    )
    assert (
        order_events_after == order_events_before
    )


def test_migration_is_idempotent(tmp_path):
    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    for _ in range(3):
        store = TradeStateStore(database_path)
        saga = BrokerActionSagaStore(store)
        assert saga.count_saga_actions() == 0

    with sqlite3.connect(
        database_path
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
    assert "broker_saga_actions" in tables
    assert (
        "broker_saga_action_attempts" in tables
    )
    assert "broker_action_halts" in tables


def test_ambiguous_legacy_state_gets_no_fabricated_action(
    tmp_path,
):
    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    store = TradeStateStore(database_path)
    saga = BrokerActionSagaStore(store)

    assert saga.count_saga_actions() == 0
    assert saga.count_saga_attempts() == 0
    assert saga.count_saga_action_events() == 0


# ---------------------------------------------------------------
# Static live-broker guards
# ---------------------------------------------------------------


def saga_module_source() -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / "broker_action_saga.py"
    ).read_text(encoding="utf-8")


def test_saga_module_contains_no_live_broker_capability():
    lowered = saga_module_source().lower()
    for forbidden in (
        "robinhood",
        "call_tool",
        "review_equity_order",
        "place_equity_order",
        "cancel_equity_order",
        "clientsession",
        "mcp",
        "http",
        "requests",
        "urllib",
        "socket",
        "websocket",
        "aiohttp",
    ):
        assert forbidden not in lowered, forbidden


def test_saga_module_has_no_default_production_path():
    import src.broker_action_saga as saga_module

    assert not hasattr(
        saga_module, "DEFAULT_DB_PATH"
    )
    assert "data/" not in saga_module_source()
    with pytest.raises(
        BrokerActionSagaError,
        match="TradeStateStore is required",
    ):
        BrokerActionSagaStore("data")


def test_repository_data_guard_attempts_remain_zero(
    repository_data_database_guard,
):
    assert (
        repository_data_database_guard.attempts
        == []
    )


def test_status_model_is_complete():
    statuses = set(SagaActionStatus)
    assert (
        ACTIVE_SAGA_STATUSES
        | TERMINAL_SAGA_STATUSES
        == statuses
    )
    assert (
        ACTIVE_SAGA_STATUSES
        & TERMINAL_SAGA_STATUSES
        == set()
    )
    assert len(SagaActionType) == 10
