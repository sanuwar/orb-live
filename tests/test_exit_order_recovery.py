from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from tests.snapshot_helpers import apply_broker_snapshot
from tests.test_entry_recovery import create_legacy_database

from src.account_state import (
    AccountInfo,
    AccountState,
    EquityOrder,
    EquityPosition,
    PortfolioState,
)
from src.exit_order_lifecycle import (
    ExitOrderAction,
    ExitOrderLifecycleCoordinator,
    exit_observation_key,
)
from src.trade_state import (
    ExitRecoveryAction,
    ExitRecoveryStep,
    StopRecoveryAction,
    StopRecoveryStep,
    TradeStateError,
    TradeStateStore,
    TradeStatus,
)


STRATEGY = "orb_long_v0"
SESSION_DATE = date(2026, 7, 17)


def make_trade(
    store: TradeStateStore,
    *,
    strategy: str = STRATEGY,
    quantity: int = 10,
    order_id: str = "exit-1",
    recovery_ref_id: str = "exit-ref-1",
):
    trade = store.reserve_entry(
        strategy=strategy,
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=quantity,
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
        filled_quantity=quantity,
        average_entry_price=Decimal("100"),
    )
    return store.begin_exit(
        trade.trade_id,
        broker_order_id=order_id,
        requested_quantity=quantity,
        close_reason="profit_target",
        intent_type="profit_target",
        intended_price=Decimal("102"),
        recovery_ref_id=recovery_ref_id,
    )


def order(
    *,
    order_id: str = "exit-1",
    symbol: str = "AAPL",
    side: str = "sell",
    order_type: str = "limit",
    state: str = "confirmed",
    quantity: Decimal = Decimal("10"),
    cumulative: Decimal = Decimal("0"),
    limit_price: Decimal | None = Decimal("102"),
    stop_price: Decimal | None = None,
    trigger: str = "immediate",
    placed_agent: str = "agentic",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=quantity,
        cumulative_quantity=cumulative,
        limit_price=limit_price,
        stop_price=stop_price,
        trigger=trigger,
        placed_agent=placed_agent,
    )


def stop_order(
    *,
    order_id: str = "replacement-stop",
    symbol: str = "AAPL",
    side: str = "sell",
    order_type: str = "stop_market",
    state: str = "confirmed",
    quantity: Decimal = Decimal("6"),
    cumulative: Decimal = Decimal("0"),
    stop_price: Decimal | None = Decimal("99"),
    trigger: str = "stop",
    placed_agent: str = "agentic",
) -> EquityOrder:
    return order(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=quantity,
        cumulative=cumulative,
        limit_price=None,
        stop_price=stop_price,
        trigger=trigger,
        placed_agent=placed_agent,
    )


def account_state(
    *,
    position_quantity: Decimal | None,
    orders: tuple[EquityOrder, ...] = (),
) -> AccountState:
    positions = ()
    if position_quantity is not None:
        positions = (
            EquityPosition(
                symbol="AAPL",
                quantity=position_quantity,
                intraday_quantity=position_quantity,
                average_buy_price=Decimal("100"),
                shares_available_for_sells=position_quantity,
                shares_held_for_sells=Decimal("0"),
                position_type="long",
            ),
        )

    return AccountState(
        account=AccountInfo(
            account_number="TEST12345678",
            masked_account_number="test-account",
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
        positions=positions,
        open_orders=orders,
    )


ORDER_STATE_CASES = [
    ("new", "0", None, ExitOrderAction.WAIT),
    ("queued", "0", None, ExitOrderAction.WAIT),
    ("confirmed", "0", None, ExitOrderAction.WAIT),
    ("unconfirmed", "0", None, ExitOrderAction.WAIT),
    ("open", "0", None, ExitOrderAction.WAIT),
    ("submitted", "0", None, ExitOrderAction.WAIT),
    ("accepted", "0", None, ExitOrderAction.WAIT),
    ("partially_filled", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("partial_fill", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("partially_executed", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("confirmed", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("cancelled", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("rejected", "4", "101", ExitOrderAction.RECORD_PARTIAL_FILL),
    ("filled", "10", "102", ExitOrderAction.RECORD_FULL_FILL),
    ("executed", "10", "102", ExitOrderAction.RECORD_FULL_FILL),
    ("completed", "10", "102", ExitOrderAction.RECORD_FULL_FILL),
    ("cancelled", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("canceled", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("voided", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("expired", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("rejected", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("failed", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("denied", "0", None, ExitOrderAction.RECORD_TERMINAL),
    ("unknown_state", "0", None, ExitOrderAction.FAIL_CLOSED),
]


@pytest.mark.parametrize(
    ("state", "cumulative", "average", "expected_action"),
    ORDER_STATE_CASES,
)
def test_exit_order_state_matrix_is_durable(
    tmp_path,
    state,
    cumulative,
    average,
    expected_action,
):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            state=state,
            cumulative=Decimal(cumulative),
        ),
        average_fill_price=average,
        fees="0",
    )

    assert result.decision.action == expected_action
    assert result.ledger_changed is True
    assert store.count_exit_recovery_events() == 1
    assert store.is_entry_halted(STRATEGY)
    assert result.trade_after.status != TradeStatus.CLOSED


MISMATCH_CASES = [
    ({"order_id": "other"}, "exit_order_id_mismatch"),
    ({"symbol": "MSFT"}, "exit_symbol_mismatch"),
    ({"side": "buy"}, "exit_side_mismatch"),
    ({"order_type": "market"}, "exit_type_mismatch"),
    ({"trigger": "stop"}, "exit_trigger_mismatch"),
    ({"placed_agent": "manual"}, "exit_origin_mismatch"),
    ({"quantity": Decimal("9")}, "exit_requested_quantity_mismatch"),
    ({"quantity": Decimal("10.5")}, "invalid_exit_quantity"),
    ({"cumulative": Decimal("10.5")}, "invalid_exit_quantity"),
    ({"limit_price": Decimal("103")}, "exit_price_mismatch"),
    ({"state": "filled", "cumulative": Decimal("10")}, "exit_average_price_missing"),
    ({"state": "partially_filled", "cumulative": Decimal("4")}, "exit_average_price_missing"),
]


@pytest.mark.parametrize(("changes", "failure_code"), MISMATCH_CASES)
def test_mismatched_exit_evidence_persists_without_advancing_accounting(
    tmp_path,
    changes,
    failure_code,
):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(**changes),
    )

    assert result.decision.action == ExitOrderAction.FAIL_CLOSED
    assert result.decision.failure_code == failure_code
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_executed_quantity == 0
    assert result.trade_after.cumulative_exited_quantity == 0
    assert result.trade_after.cumulative_exit_proceeds == 0
    assert result.trade_after.exit_operator_ack_required is True


def prepare_partial_exit(store: TradeStateStore):
    trade = make_trade(store)
    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            state="partially_filled",
            cumulative=Decimal("4"),
        ),
        average_fill_price="101",
        fees="0.40",
    )
    return result.trade_after


STOP_PROOF_CASES = [
    ({"state": "cancelled"}, Decimal("6"), False),
    ({"state": "rejected"}, Decimal("6"), False),
    ({"state": "pending_cancel"}, Decimal("6"), False),
    ({"state": "partially_filled", "cumulative": Decimal("1")}, Decimal("6"), False),
    ({"quantity": Decimal("5")}, Decimal("6"), False),
    ({"quantity": Decimal("7")}, Decimal("6"), False),
    ({"symbol": "MSFT"}, Decimal("6"), False),
    ({"side": "buy"}, Decimal("6"), False),
    ({"order_type": "limit"}, Decimal("6"), False),
    ({"trigger": "immediate"}, Decimal("6"), False),
    ({"placed_agent": "manual"}, Decimal("6"), False),
    ({"stop_price": None}, Decimal("6"), False),
    ({"stop_price": Decimal("0")}, Decimal("6"), False),
    ({}, Decimal("5"), False),
    ({}, Decimal("7"), False),
    ({}, Decimal("6.5"), False),
    ({"order_id": "unrelated-stop"}, Decimal("6"), False),
    ({}, Decimal("6"), True),
]


@pytest.mark.parametrize(
    ("stop_changes", "position_quantity", "add_conflict"),
    STOP_PROOF_CASES,
)
def test_only_exact_active_agentic_stop_can_clear_exit_halt(
    tmp_path,
    stop_changes,
    position_quantity,
    add_conflict,
):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="replacement-stop",
    )

    observed_stop = stop_order(**stop_changes)
    orders = (observed_stop,)
    if add_conflict:
        orders += (
            order(
                order_id="other-sell",
                quantity=Decimal("6"),
                state="confirmed",
            ),
        )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(
            position_quantity=position_quantity,
            orders=orders,
        ),
    )

    persisted = store.get_trade(trade.trade_id)
    assert persisted.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert store.is_entry_halted(STRATEGY)
    assert persisted.protected_quantity == 0


def test_full_exit_closes_only_after_fresh_flat_snapshot(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)
    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            state="filled",
            cumulative=Decimal("10"),
        ),
        average_fill_price="102",
        fees="1.25",
    )

    with pytest.raises(TradeStateError, match="flat-snapshot proof"):
        store.close_trade(
            trade.trade_id,
            average_exit_price=Decimal("102"),
            reason="profit_target",
        )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(position_quantity=None),
        reconciliation_id="flat-proof-1",
    )
    closed = store.get_trade(trade.trade_id)

    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert closed.status == TradeStatus.CLOSED
    assert closed.close_reason == "profit_target"
    assert closed.realized_pnl == Decimal("20")
    assert closed.cumulative_exit_fees == Decimal("1.25")
    assert not store.is_entry_halted(STRATEGY)


def test_flat_snapshot_cannot_close_incomplete_exit_accounting(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(position_quantity=None),
    )
    persisted = store.get_trade(trade.trade_id)

    assert persisted.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert persisted.exit_operator_ack_required is True
    assert store.is_entry_halted(STRATEGY)


def test_exact_remaining_protection_clears_halt(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="replacement-stop",
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(
            position_quantity=Decimal("6"),
            orders=(stop_order(),),
        ),
        reconciliation_id="protection-proof-1",
    )
    persisted = store.get_trade(trade.trade_id)

    assert persisted.status == TradeStatus.OPEN_PROTECTED
    assert persisted.protected_quantity == 6
    assert persisted.exit_remaining_position_quantity == 6
    assert (
        persisted.last_completed_exit_recovery_step
        == ExitRecoveryStep.PROTECTION_CONFIRMED.value
    )
    assert not store.is_entry_halted(STRATEGY)


def test_active_exit_conflicts_with_remaining_stop_proof(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="replacement-stop",
    )

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(
            position_quantity=Decimal("6"),
            orders=(
                stop_order(),
                order(
                    state="confirmed",
                    quantity=Decimal("10"),
                    cumulative=Decimal("4"),
                ),
            ),
        ),
    )

    persisted = store.get_trade(trade.trade_id)
    assert persisted.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert store.is_entry_halted(STRATEGY)


def test_other_strategy_reconciliation_cannot_clear_orb_halt(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)

    apply_broker_snapshot(
        store,
        strategy="other_strategy",
        account_state=account_state(position_quantity=None),
    )

    assert store.get_trade(trade.trade_id).status == TradeStatus.EXIT_PARTIALLY_FILLED
    assert store.is_entry_halted(STRATEGY)


def test_identical_observation_is_restart_stable_and_idempotent(tmp_path):
    database = tmp_path / "state.sqlite"
    store = TradeStateStore(database)
    trade = make_trade(store)
    observed = order(
        state="partially_filled",
        cumulative=Decimal("4"),
    )
    key = exit_observation_key(
        observed,
        "101",
        "0.40",
        trade.exit_recovery_ref_id,
    )
    first = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=observed,
        average_fill_price="101",
        fees="0.40",
        observation_key=key,
    )
    event_count = store.count_trade_events()

    restarted = TradeStateStore(database)
    same_key = exit_observation_key(
        observed,
        "101",
        "0.40",
        trade.exit_recovery_ref_id,
    )
    second = ExitOrderLifecycleCoordinator(restarted).apply(
        trade_id=trade.trade_id,
        order=observed,
        average_fill_price="101",
        fees="0.40",
        observation_key=same_key,
    )

    assert key == same_key
    assert first.ledger_changed is True
    assert second.ledger_changed is False
    assert restarted.count_exit_recovery_events() == 1
    assert restarted.count_trade_events() == event_count
    persisted = restarted.get_trade(trade.trade_id)
    assert persisted.exit_executed_quantity == 4
    assert persisted.cumulative_exited_quantity == 4
    assert persisted.cumulative_exit_fees == Decimal("0.40")


def test_changed_broker_observation_creates_one_new_durable_event(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)
    coordinator = ExitOrderLifecycleCoordinator(store)
    first_order = order(
        state="partially_filled",
        cumulative=Decimal("4"),
    )
    second_order = order(
        state="partially_filled",
        cumulative=Decimal("5"),
    )

    coordinator.apply(
        trade_id=trade.trade_id,
        order=first_order,
        average_fill_price="101",
        fees="0.40",
    )
    coordinator.apply(
        trade_id=trade.trade_id,
        order=second_order,
        average_fill_price="101.20",
        fees="0.55",
    )
    coordinator.apply(
        trade_id=trade.trade_id,
        order=second_order,
        average_fill_price="101.20",
        fees="0.55",
    )

    persisted = store.get_trade(trade.trade_id)
    assert store.count_exit_recovery_events() == 2
    assert persisted.exit_executed_quantity == 5
    assert persisted.cumulative_exited_quantity == 5
    assert persisted.cumulative_exit_proceeds == Decimal("506.00")
    assert persisted.cumulative_exit_fees == Decimal("0.55")
    assert persisted.cumulative_realized_pnl == Decimal("6.00")


def test_decreasing_observation_records_raw_evidence_without_regression(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            state="partially_filled",
            cumulative=Decimal("3"),
        ),
        average_fill_price="100.50",
        fees="0.30",
    )

    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_executed_quantity == 4
    assert result.trade_after.cumulative_exited_quantity == 4
    assert result.trade_after.cumulative_exit_proceeds == Decimal("404")
    assert result.trade_after.cumulative_exit_fees == Decimal("0.40")


def test_partial_realized_pnl_flows_to_daily_weekly_and_drawdown(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    prepare_partial_exit(store)
    other = make_trade(
        store,
        strategy="other_strategy",
        order_id="other-exit",
        recovery_ref_id="other-ref",
    )
    ExitOrderLifecycleCoordinator(store).apply(
        trade_id=other.trade_id,
        order=order(
            order_id="other-exit",
            state="partially_filled",
            cumulative=Decimal("2"),
        ),
        average_fill_price="90",
    )

    assert store.daily_pnl(SESSION_DATE, strategy=STRATEGY) == Decimal("4")
    assert store.weekly_pnl(SESSION_DATE, strategy=STRATEGY) == Decimal("4")
    assert store.current_drawdown(strategy=STRATEGY) == Decimal("0")
    assert store.daily_pnl(SESSION_DATE) == Decimal("-16")
    assert store.current_drawdown() == Decimal("20")


def test_cross_source_stop_and_exit_accounting_is_weighted_once(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = store.reserve_entry(
        strategy=STRATEGY,
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )
    trade = store.attach_entry_order(trade.trade_id, broker_order_id="entry-1")
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )
    trade, _ = store.persist_stop_recovery_observation(
        trade.trade_id,
        observation_key="stop-partial-1",
        broker_order_id="stop-1",
        broker_state="cancelled",
        requested_quantity=10,
        executed_quantity=3,
        remaining_order_quantity=0,
        average_fill_price=Decimal("99"),
        required_action=StopRecoveryAction.PROTECT_REMAINING_POSITION,
        last_completed_step=StopRecoveryStep.PARTIAL_FILL_RECORDED,
        failure_reason="Stop cancelled after partial fill.",
        operator_ack_required=True,
    )
    trade = store.mark_protective_stop_inactive(
        trade.trade_id,
        reason="Snapshot confirms the partially filled stop is inactive.",
    )
    trade = store.begin_exit(
        trade.trade_id,
        broker_order_id="exit-2",
        requested_quantity=7,
        close_reason="stop_recovery",
        intent_type="stop_recovery",
        intended_price=Decimal("102"),
        recovery_ref_id="cross-source-ref",
    )
    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            order_id="exit-2",
            quantity=Decimal("7"),
            state="filled",
            cumulative=Decimal("7"),
        ),
        average_fill_price="102",
    )

    persisted = result.trade_after
    assert persisted.exit_attempt_base_quantity == 3
    assert persisted.cumulative_exited_quantity == 10
    assert persisted.cumulative_exit_proceeds == Decimal("1011")
    assert persisted.cumulative_average_exit_price == Decimal("101.1")
    assert persisted.cumulative_realized_pnl == Decimal("11")


def test_multiple_exit_attempts_do_not_double_count_prior_fill(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)
    terminal = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(
            state="cancelled",
            cumulative=Decimal("4"),
        ),
        average_fill_price="101",
        fees="0.40",
    ).trade_after
    terminal = store.attach_protective_stop(
        terminal.trade_id,
        broker_order_id="replacement-stop",
    )
    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(
            position_quantity=Decimal("6"),
            orders=(stop_order(),),
        ),
    )
    protected = store.get_trade(terminal.trade_id)
    unprotected = store.mark_protective_stop_inactive(
        protected.trade_id,
        reason="Stop is inactive before second exit attempt.",
    )
    second = store.begin_exit(
        unprotected.trade_id,
        broker_order_id="exit-2",
        requested_quantity=6,
        close_reason="time_exit",
        intent_type="time_exit",
        intended_price=Decimal("102"),
        recovery_ref_id="exit-ref-2",
    )
    final = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=second.trade_id,
        order=order(
            order_id="exit-2",
            quantity=Decimal("6"),
            state="filled",
            cumulative=Decimal("6"),
        ),
        average_fill_price="102",
        fees="0.60",
    ).trade_after

    assert second.exit_recovery_attempt_count == 2
    assert final.exit_attempt_base_quantity == 4
    assert final.exit_executed_quantity == 6
    assert final.cumulative_exited_quantity == 10
    assert final.cumulative_exit_proceeds == Decimal("1016")
    assert final.cumulative_exit_fees == Decimal("1.00")
    assert final.cumulative_realized_pnl == Decimal("16")


def test_legacy_exit_pending_migration_is_idempotent_and_preserves_data(tmp_path):
    database = tmp_path / "legacy.sqlite"
    create_legacy_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            UPDATE trades
            SET status = 'exit_pending',
                close_reason = NULL,
                closed_at = NULL
            WHERE trade_id = 'legacy-trade'
            """
        )

    first = TradeStateStore(database)
    migrated = first.get_trade("legacy-trade")
    first_counts = (
        first.count_trade_events(),
        first.count_order_events(),
        first.count_exit_recovery_events(),
    )
    second = TradeStateStore(database)
    reopened = second.get_trade("legacy-trade")
    second_counts = (
        second.count_trade_events(),
        second.count_order_events(),
        second.count_exit_recovery_events(),
    )

    assert migrated.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert migrated.exit_operator_ack_required is True
    assert migrated.required_exit_recovery_action == (
        ExitRecoveryAction.OPERATOR_INTERVENTION
    )
    assert second.is_entry_halted("orb_long_v0")
    assert reopened == migrated
    assert second_counts == first_counts
    assert first_counts[:2] == (1, 1)


def test_missing_or_blank_exit_intent_fields_fail_closed(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = store.reserve_entry(
        strategy=STRATEGY,
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )
    trade = store.attach_entry_order(trade.trade_id, broker_order_id="entry-1")
    trade = store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )

    for kwargs in (
        {"close_reason": ""},
        {"intent_type": ""},
        {"recovery_ref_id": "   "},
    ):
        arguments = {
            "broker_order_id": "exit-1",
            "requested_quantity": 10,
            "close_reason": "profit_target",
            "intent_type": "profit_target",
            "recovery_ref_id": "exit-ref",
        }
        arguments.update(kwargs)
        with pytest.raises(TradeStateError):
            store.begin_exit(trade.trade_id, **arguments)

    assert store.get_trade(trade.trade_id).status == TradeStatus.PROTECTION_PENDING
    assert store.count_exit_recovery_events() == 0

def test_operator_required_recovery_is_sticky_until_snapshot(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_trade(store)
    failed = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(state="unknown_state"),
    ).trade_after
    observed = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=order(state="confirmed"),
    ).trade_after

    assert failed.exit_operator_ack_required is True
    assert observed.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert observed.exit_operator_ack_required is True
    assert observed.required_exit_recovery_action == (
        ExitRecoveryAction.OPERATOR_INTERVENTION
    )
    assert store.is_entry_halted(STRATEGY)


def test_restart_flatness_after_partial_protection_cannot_clear_halt(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = prepare_partial_exit(store)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="replacement-stop",
    )
    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(
            position_quantity=Decimal("6"),
            orders=(stop_order(),),
        ),
    )
    assert store.get_trade(trade.trade_id).status == TradeStatus.OPEN_PROTECTED

    apply_broker_snapshot(
        store,
        strategy=STRATEGY,
        account_state=account_state(position_quantity=None),
        reconciliation_id="restart-flat-incomplete",
    )
    persisted = store.get_trade(trade.trade_id)

    assert persisted.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert persisted.cumulative_exited_quantity == 4
    assert persisted.exit_operator_ack_required is True
    assert store.is_entry_halted(STRATEGY)