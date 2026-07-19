from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from tests.snapshot_helpers import confirm_linked_stop

from src.account_state import EquityOrder
from src.exit_order_lifecycle import (
    ExitOrderAction,
    ExitOrderLifecycleCoordinator,
    decide_exit_order_lifecycle,
)
from src.trade_state import (
    ExitRecoveryAction,
    ExitRecoveryStep,
    TradeStateError,
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)


def make_open_trade(store: TradeStateStore):
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
    return store.mark_entry_filled(
        trade.trade_id,
        filled_quantity=10,
        average_entry_price=Decimal("100"),
    )


def make_exit_pending_trade(store: TradeStateStore):
    trade = make_open_trade(store)
    return store.begin_exit(
        trade.trade_id,
        broker_order_id="exit-1",
        requested_quantity=10,
        close_reason="profit_target",
        intent_type="profit_target",
        intended_price=Decimal("102"),
        recovery_ref_id="exit-intent-1",
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
    placed_agent: str = "agentic",
    limit_price: str = "102",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(cumulative_quantity),
        limit_price=Decimal(limit_price),
        stop_price=None,
        trigger=trigger,
        placed_agent=placed_agent,
    )


def test_pending_exit_persists_observation_and_waits(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(),
        close_reason="profit_target",
    )

    assert result.decision.action == ExitOrderAction.WAIT
    assert result.ledger_changed is True
    assert result.recovery_event_recorded is True
    assert result.trade_after.status == TradeStatus.EXIT_PENDING
    assert (
        result.trade_after.last_completed_exit_recovery_step
        == ExitRecoveryStep.OBSERVATION_RECORDED.value
    )
    assert result.decision.requires_immediate_reconciliation is False
    assert store.count_exit_recovery_events() == 1


def test_full_exit_without_average_price_fails_closed_durably(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        close_reason="profit_target",
    )

    assert result.decision.action == ExitOrderAction.FAIL_CLOSED
    assert result.ledger_changed is True
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_executed_quantity == 0
    assert result.trade_after.cumulative_exited_quantity == 0
    assert result.trade_after.exit_operator_ack_required is True


def test_supplied_close_reason_must_match_durable_intent(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="102",
        close_reason="",
    )

    assert result.decision.action == ExitOrderAction.FAIL_CLOSED
    assert "durable intent" in result.decision.reason
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED


def test_full_exit_fill_waits_for_flat_snapshot(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="filled",
            cumulative_quantity="10",
        ),
        average_fill_price="102",
        close_reason="profit_target",
        fees="1.25",
    )

    assert result.decision.action == ExitOrderAction.RECORD_FULL_FILL
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_executed_quantity == 10
    assert result.trade_after.cumulative_exited_quantity == 10
    assert result.trade_after.exit_remaining_position_quantity == 0
    assert result.trade_after.cumulative_average_exit_price == Decimal("102")
    assert result.trade_after.cumulative_realized_pnl == Decimal("20")
    assert result.trade_after.cumulative_exit_fees == Decimal("1.25")
    assert result.trade_after.close_reason is None
    assert result.trade_after.closed_at is None
    assert (
        result.trade_after.required_exit_recovery_action
        == ExitRecoveryAction.RECONCILE_BROKER_STATE
    )


def test_partial_exit_persists_quantity_pnl_and_recovery(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(
            state="partially_filled",
            cumulative_quantity="4",
        ),
        average_fill_price="101",
        close_reason="profit_target",
        fees="0.50",
    )

    assert result.decision.action == ExitOrderAction.RECORD_PARTIAL_FILL
    assert result.trade_after.status == TradeStatus.EXIT_PARTIALLY_FILLED
    assert result.trade_after.exit_executed_quantity == 4
    assert result.trade_after.exit_remaining_position_quantity == 6
    assert result.trade_after.cumulative_realized_pnl == Decimal("4")
    assert result.trade_after.realized_pnl == Decimal("4")
    assert result.trade_after.cumulative_exit_fees == Decimal("0.50")
    assert result.decision.requires_immediate_reconciliation is True


@pytest.mark.parametrize("state", ["cancelled", "rejected"])
def test_unfilled_terminal_exit_requires_durable_recovery(tmp_path, state):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(state=state),
        close_reason="profit_target",
    )

    assert result.decision.action == ExitOrderAction.RECORD_TERMINAL
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_remaining_position_quantity == 10
    assert result.trade_after.exit_operator_ack_required is True


@pytest.mark.parametrize(
    ("override", "reason_text"),
    [
        ({"order_id": "different-exit"}, "order id"),
        ({"symbol": "MSFT"}, "symbol"),
        ({"side": "buy"}, "sell"),
        ({"trigger": "stop"}, "trigger"),
        ({"order_type": "market"}, "limit"),
        ({"quantity": "9"}, "quantity"),
        ({"placed_agent": "manual"}, "agentic"),
        ({"limit_price": "103"}, "price"),
    ],
)
def test_mismatched_exit_evidence_fails_closed(tmp_path, override, reason_text):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    decision = decide_exit_order_lifecycle(
        trade=trade,
        order=make_exit_order(**override),
        close_reason="profit_target",
    )

    assert decision.action == ExitOrderAction.FAIL_CLOSED
    assert reason_text in decision.reason.lower()


def test_linked_protective_stop_blocks_separate_exit_intent(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_open_trade(store)
    trade = store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )
    trade = confirm_linked_stop(store, trade)

    with pytest.raises(TradeStateError, match="stop remains linked"):
        store.begin_exit(
            trade.trade_id,
            broker_order_id="exit-1",
            requested_quantity=10,
            close_reason="profit_target",
            intent_type="profit_target",
        )


def test_non_exit_lifecycle_trade_fails_closed(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
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

    assert decision.action == ExitOrderAction.FAIL_CLOSED
    assert "durable exit lifecycle" in decision.reason


def test_unknown_broker_state_is_persisted_fail_closed(tmp_path):
    store = TradeStateStore(tmp_path / "state.sqlite")
    trade = make_exit_pending_trade(store)

    result = ExitOrderLifecycleCoordinator(store).apply(
        trade_id=trade.trade_id,
        order=make_exit_order(state="unexpected_state"),
        close_reason="profit_target",
    )

    assert result.decision.action == ExitOrderAction.FAIL_CLOSED
    assert result.ledger_changed is True
    assert result.trade_after.status == TradeStatus.EXIT_RECOVERY_REQUIRED
    assert result.trade_after.exit_operator_ack_required is True