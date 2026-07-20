from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
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
from src.broker_identity import CommissionedAccount
from src.broker_snapshot import account_fingerprint
from src.live_reconciliation import (
    DivergenceCategory,
    LiveReconciliationError,
    LiveReconciliationStore,
    LiveReconciler,
)
from src.trade_state import TradeStateStore, TradeStatus


ACCOUNT_NUMBER = "TEST12345678"
COMMISSIONED = CommissionedAccount(
    expected_fingerprint=account_fingerprint(
        ACCOUNT_NUMBER
    )
)
OTHER_ACCOUNT = "OTHER87654321"

SESSION_DATE = date(2026, 7, 17)
TIMESTAMP = datetime(
    2026, 7, 17, 14, 0, tzinfo=timezone.utc
)
IN_GRACE = TIMESTAMP + timedelta(seconds=10)
OUT_OF_GRACE = TIMESTAMP + timedelta(seconds=600)


def make_account_state(
    *,
    positions=(),
    orders=(),
    account_number: str = ACCOUNT_NUMBER,
) -> AccountState:
    return AccountState(
        account=AccountInfo(
            account_number=account_number,
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
            unleveraged_buying_power=Decimal("3000"),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=tuple(positions),
        open_orders=tuple(orders),
    )


def make_position(
    symbol: str = "AAPL",
    quantity: str = "10",
) -> EquityPosition:
    return EquityPosition(
        symbol=symbol,
        quantity=Decimal(quantity),
        intraday_quantity=Decimal(quantity),
        average_buy_price=Decimal("100"),
        shares_available_for_sells=Decimal(quantity),
        shares_held_for_sells=Decimal("0"),
        position_type="long",
    )


def make_stop_order(
    *,
    order_id: str = "stop-AAPL",
    symbol: str = "AAPL",
    quantity: str = "10",
    state: str = "confirmed",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side="sell",
        order_type="stop_market",
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal("0"),
        limit_price=None,
        stop_price=Decimal("99"),
        trigger="stop",
        placed_agent="agentic",
    )


def make_entry_order(
    *,
    order_id: str = "entry-AAPL",
    symbol: str = "AAPL",
    quantity: str = "10",
    cumulative: str = "10",
    state: str = "confirmed",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side="buy",
        order_type="limit",
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(cumulative),
        limit_price=Decimal("100"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )


def snapshot_at(
    account_state,
    *,
    evaluated_at: datetime,
    reconciliation_id: str = "cycle-1",
    **changes,
):
    completed = evaluated_at - timedelta(
        milliseconds=1
    )
    started = completed - timedelta(milliseconds=3)

    return make_broker_snapshot(
        account_state,
        reconciliation_id=reconciliation_id,
        started_at=started,
        positions_read_at=(
            started + timedelta(milliseconds=1)
        ),
        orders_read_at=(
            started + timedelta(milliseconds=2)
        ),
        completed_at=completed,
        **changes,
    )


def make_store(tmp_path) -> TradeStateStore:
    return TradeStateStore(
        tmp_path / "state.sqlite"
    )


def filled_trade(
    store: TradeStateStore,
    *,
    symbol: str = "AAPL",
    quantity: int = 10,
):
    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol=symbol,
        session_date=SESSION_DATE,
        quantity=quantity,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=TIMESTAMP,
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


def protected_trade(
    store: TradeStateStore,
    *,
    symbol: str = "AAPL",
    quantity: int = 10,
):
    trade = filled_trade(
        store,
        symbol=symbol,
        quantity=quantity,
    )
    return store.attach_protective_stop(
        trade.trade_id,
        broker_order_id=f"stop-{symbol}",
        occurred_at=TIMESTAMP,
    )


def reconcile(
    trades,
    account_state,
    *,
    evaluated_at: datetime = OUT_OF_GRACE,
    reconciliation_id: str = "cycle-1",
    account: CommissionedAccount = COMMISSIONED,
):
    reconciler = LiveReconciler(account)
    return reconciler.reconcile(
        snapshot=snapshot_at(
            account_state,
            evaluated_at=evaluated_at,
            reconciliation_id=reconciliation_id,
        ),
        trades=trades,
        evaluated_at=evaluated_at,
    )


def categories(outcome):
    return {
        finding.category
        for finding in outcome.findings
    }


def test_matched_state_reconciles_as_confirmed(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(
                make_entry_order(),
                make_stop_order(),
            ),
        ),
    )

    assert outcome.accepted
    assert categories(outcome) == {
        DivergenceCategory.CONFIRMED
    }


def test_stale_snapshot_fails_closed(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    reconciler = LiveReconciler(COMMISSIONED)
    snapshot = snapshot_at(
        make_account_state(
            positions=(make_position(),),
            orders=(make_stop_order(),),
        ),
        evaluated_at=OUT_OF_GRACE,
    )

    outcome = reconciler.reconcile(
        snapshot=snapshot,
        trades=[trade],
        evaluated_at=OUT_OF_GRACE
        + timedelta(minutes=5),
    )

    assert not outcome.accepted
    assert outcome.result_code == "stale_snapshot"
    assert outcome.findings == ()


def test_commissioned_mismatch_fails_closed(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_stop_order(),),
            account_number=OTHER_ACCOUNT,
        ),
    )

    assert not outcome.accepted
    assert (
        outcome.result_code
        == "commissioned_account_mismatch"
    )
    assert outcome.findings == ()
    assert OTHER_ACCOUNT not in str(
        outcome.rejection.reason
    )


def test_incomplete_pages_fail_closed(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    reconciler = LiveReconciler(COMMISSIONED)
    snapshot = snapshot_at(
        make_account_state(
            positions=(make_position(),),
            orders=(make_stop_order(),),
        ),
        evaluated_at=OUT_OF_GRACE,
        orders_complete=False,
    )

    outcome = reconciler.reconcile(
        snapshot=snapshot,
        trades=[trade],
        evaluated_at=OUT_OF_GRACE,
    )

    assert not outcome.accepted
    assert outcome.result_code == "orders_incomplete"
    assert outcome.findings == ()


def test_partial_fill_uses_exact_decimals(tmp_path):
    store = make_store(tmp_path)
    trade = filled_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(
                make_entry_order(cumulative="4"),
                make_stop_order(),
            ),
        ),
    )

    partial = outcome.by_category(
        DivergenceCategory.PARTIAL_FILL
    )

    assert len(partial) == 1
    assert partial[0].observed_quantity == Decimal(
        "4"
    )
    assert partial[0].escalates


def test_missing_protective_stop_halts(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    missing = outcome.by_category(
        DivergenceCategory.PROTECTION_MISSING
    )

    assert len(missing) == 1
    assert missing[0].halts
    assert missing[0].escalates


def test_indeterminate_stop_is_never_optimistic(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(
                make_entry_order(),
                make_stop_order(state="unknown"),
            ),
        ),
    )

    unknown = outcome.by_category(
        DivergenceCategory.PROTECTION_UNKNOWN
    )

    assert len(unknown) == 1
    assert unknown[0].halts
    assert (
        DivergenceCategory.CONFIRMED
        not in categories(outcome)
    )


def test_undersized_stop_is_protection_unknown(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(
                make_entry_order(),
                make_stop_order(quantity="4"),
            ),
        ),
    )

    assert (
        DivergenceCategory.PROTECTION_UNKNOWN
        in categories(outcome)
    )


def test_delayed_visibility_is_insufficient_evidence(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
        evaluated_at=IN_GRACE,
    )

    delayed = outcome.by_category(
        DivergenceCategory.DELAYED_VISIBILITY
    )

    assert len(delayed) == 1
    assert delayed[0].is_insufficient_evidence
    assert not delayed[0].halts
    assert (
        DivergenceCategory.PROTECTION_MISSING
        not in categories(outcome)
    )


def test_delayed_entry_is_not_no_effect(tmp_path):
    store = make_store(tmp_path)
    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=TIMESTAMP,
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-AAPL",
        occurred_at=TIMESTAMP,
    )

    outcome = reconcile(
        [trade],
        make_account_state(),
        evaluated_at=IN_GRACE,
    )

    assert categories(outcome) == {
        DivergenceCategory.DELAYED_VISIBILITY
    }
    assert (
        DivergenceCategory.INTERNAL_AHEAD
        not in categories(outcome)
    )


def test_absent_entry_escalates_after_grace(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
        occurred_at=TIMESTAMP,
    )
    trade = store.attach_entry_order(
        trade.trade_id,
        broker_order_id="entry-AAPL",
        occurred_at=TIMESTAMP,
    )

    outcome = reconcile(
        [trade],
        make_account_state(),
        evaluated_at=OUT_OF_GRACE,
    )

    internal = outcome.by_category(
        DivergenceCategory.INTERNAL_AHEAD
    )

    assert len(internal) == 1
    assert internal[0].escalates


def test_broker_ahead_is_detected(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(
                make_position(quantity="25"),
            ),
            orders=(
                make_entry_order(),
                make_stop_order(quantity="25"),
            ),
        ),
    )

    ahead = outcome.by_category(
        DivergenceCategory.BROKER_AHEAD
    )

    assert len(ahead) == 1
    assert ahead[0].observed_quantity == Decimal(
        "25"
    )
    assert ahead[0].internal_quantity == Decimal(
        "10"
    )


def test_duplicate_protective_stops_conflict(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(
                make_stop_order(order_id="stop-AAPL"),
                make_stop_order(order_id="stop-dup"),
            ),
        ),
    )

    assert categories(outcome) == {
        DivergenceCategory.CONFLICTING
    }
    assert outcome.findings[0].halts


def test_stop_without_position_conflicts(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            orders=(make_stop_order(),),
        ),
    )

    assert categories(outcome) == {
        DivergenceCategory.CONFLICTING
    }


def test_missing_evidence_when_no_stop_recorded(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = filled_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    missing = outcome.by_category(
        DivergenceCategory.MISSING_EVIDENCE
    )

    assert len(missing) == 1
    assert missing[0].halts


def test_reconciliation_does_not_mutate_trade_state(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    before = store.get_trade(trade.trade_id)

    reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    after = store.get_trade(trade.trade_id)

    assert after == before
    assert after.version == before.version
    assert (
        after.cumulative_realized_pnl
        == before.cumulative_realized_pnl
    )
    assert after.status == before.status


def test_evidence_recording_is_idempotent(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    evidence = LiveReconciliationStore(
        tmp_path / "evidence.sqlite"
    )

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    first = evidence.record(outcome.findings)
    second = evidence.record(outcome.findings)

    assert first == len(outcome.findings)
    assert second == 0
    assert evidence.count_evidence() == len(
        outcome.findings
    )


def test_evidence_survives_restart_without_duplication(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    path = tmp_path / "evidence.sqlite"

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    LiveReconciliationStore(path).record(
        outcome.findings
    )

    reopened = LiveReconciliationStore(path)
    reopened.record(outcome.findings)

    assert reopened.count_evidence() == len(
        outcome.findings
    )


def test_evidence_rows_hold_no_account_number(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    evidence = LiveReconciliationStore(
        tmp_path / "evidence.sqlite"
    )

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )
    evidence.record(outcome.findings)

    for row in evidence.list_evidence():
        rendered = str(row)
        assert ACCOUNT_NUMBER not in rendered
        assert "stop-AAPL" not in rendered


def test_findings_never_expose_the_account_number(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)

    outcome = reconcile(
        [trade],
        make_account_state(
            positions=(make_position(),),
            orders=(make_entry_order(),),
        ),
    )

    for finding in outcome.findings:
        assert ACCOUNT_NUMBER not in repr(finding)
        assert finding.account_fingerprint == (
            COMMISSIONED.expected_fingerprint
        )


def test_reconciliation_is_deterministic(tmp_path):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    account_state = make_account_state(
        positions=(make_position(),),
        orders=(make_entry_order(),),
    )

    first = reconcile([trade], account_state)
    second = reconcile([trade], account_state)

    assert first.findings == second.findings
    assert (
        first.snapshot_digest
        == second.snapshot_digest
    )


def test_closed_trade_with_position_conflicts(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    closed = replace(
        trade,
        status=TradeStatus.CLOSED,
    )

    outcome = reconcile(
        [closed],
        make_account_state(
            positions=(make_position(),),
        ),
    )

    assert categories(outcome) == {
        DivergenceCategory.CONFLICTING
    }
    assert outcome.findings[0].halts


def test_closed_trade_without_position_is_quiet(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    closed = replace(
        trade,
        status=TradeStatus.CLOSED,
    )

    outcome = reconcile(
        [closed],
        make_account_state(),
    )

    assert outcome.accepted
    assert outcome.findings == ()


def test_partial_exit_is_not_a_false_divergence(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    partially_exited = replace(
        trade,
        exit_remaining_position_quantity=4,
    )

    outcome = reconcile(
        [partially_exited],
        make_account_state(
            positions=(
                make_position(quantity="4"),
            ),
            orders=(
                make_entry_order(),
                make_stop_order(quantity="4"),
            ),
        ),
    )

    assert categories(outcome) == {
        DivergenceCategory.CONFIRMED
    }


def test_partial_exit_shortfall_still_escalates(
    tmp_path,
):
    store = make_store(tmp_path)
    trade = protected_trade(store)
    partially_exited = replace(
        trade,
        exit_remaining_position_quantity=4,
    )

    outcome = reconcile(
        [partially_exited],
        make_account_state(
            positions=(
                make_position(quantity="1"),
            ),
            orders=(
                make_entry_order(),
                make_stop_order(quantity="1"),
            ),
        ),
    )

    assert (
        DivergenceCategory.INTERNAL_AHEAD
        in categories(outcome)
    )


def test_reconciler_requires_commissioned_account():
    with pytest.raises(LiveReconciliationError):
        LiveReconciler(
            "not-a-commissioned-account"
        )


def test_reconciler_rejects_non_positive_age():
    with pytest.raises(LiveReconciliationError):
        LiveReconciler(
            COMMISSIONED,
            maximum_snapshot_age=timedelta(0),
        )


def test_reconciler_rejects_negative_grace():
    with pytest.raises(LiveReconciliationError):
        LiveReconciler(
            COMMISSIONED,
            visibility_grace=timedelta(seconds=-1),
        )


def test_store_requires_explicit_path():
    with pytest.raises(LiveReconciliationError):
        LiveReconciliationStore(None)
