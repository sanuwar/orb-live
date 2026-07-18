from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.account_state import EquityOrder
from src.broker_action_plan import (
    BrokerActionKind,
    BrokerActionPlanner,
    BrokerPlanPurpose,
)
from src.dry_run_engine import DryRunPlan
from src.live_safety_gate import (
    GateFailure,
    SafetyGateDecision,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)


SESSION_DATE = date(2026, 7, 17)


def approved_safety() -> SafetyGateDecision:
    return SafetyGateDecision(
        allowed=True,
        failures=(),
    )


def blocked_safety() -> SafetyGateDecision:
    return SafetyGateDecision(
        allowed=False,
        failures=(
            GateFailure(
                code="watchdog_blocked",
                message="Watchdog blocked entry.",
            ),
        ),
    )


def approved_entry_plan() -> DryRunPlan:
    return DryRunPlan(
        symbol="AAPL",
        approved=True,
        reason="Qualified.",
        shares=10,
        planned_risk=Decimal("10"),
        position_value=Decimal("1000"),
        entry_review_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "10",
            "limit_price": "100.10",
        },
        entry_placement_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "10",
            "limit_price": "100.10",
        },
        protective_stop_payload={
            "account_number": "TEST12345678",
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop_market",
            "quantity": "10",
            "stop_price": "99.00",
        },
    )


def stop_payload(
    *,
    symbol: str = "AAPL",
    quantity: str = "10",
    stop_price: str = "99",
) -> dict:
    return {
        "account_number": "TEST12345678",
        "symbol": symbol,
        "side": "sell",
        "type": "stop_market",
        "quantity": quantity,
        "stop_price": stop_price,
    }


def exit_payload(
    *,
    symbol: str = "AAPL",
    quantity: str = "10",
) -> dict:
    return {
        "account_number": "TEST12345678",
        "symbol": symbol,
        "side": "sell",
        "type": "limit",
        "quantity": quantity,
        "limit_price": "102",
    }


def make_unprotected_trade(
    store: TradeStateStore,
):
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


def make_protected_trade(
    store: TradeStateStore,
):
    trade = make_unprotected_trade(store)

    return store.attach_protective_stop(
        trade.trade_id,
        broker_order_id="stop-1",
    )


def make_sell_order(
    *,
    order_id: str = "stop-1",
    symbol: str = "AAPL",
    order_type: str = "stop_market",
    trigger: str = "stop",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side="sell",
        order_type=order_type,
        state="confirmed",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("0"),
        limit_price=(
            Decimal("102")
            if order_type == "limit"
            else None
        ),
        stop_price=(
            Decimal("99")
            if trigger == "stop"
            else None
        ),
        trigger=trigger,
        placed_agent="agentic",
    )


def test_valid_entry_plan_requires_review_then_place():
    result = BrokerActionPlanner.plan_entry(
        plan=approved_entry_plan(),
        safety_decision=approved_safety(),
    )

    assert result.allowed is True
    assert result.blockers == ()
    assert result.purpose == BrokerPlanPurpose.ENTRY

    assert result.action_kinds() == (
        BrokerActionKind.REVIEW_ENTRY,
        BrokerActionKind.PLACE_ENTRY,
    )

    review, placement = result.actions

    assert review.sequence == 1
    assert placement.sequence == 2
    assert placement.depends_on_sequence == 1
    assert placement.requires_review_ref_id is True
    assert review.requires_review_ref_id is False


def test_entry_is_blocked_when_safety_gate_fails():
    result = BrokerActionPlanner.plan_entry(
        plan=approved_entry_plan(),
        safety_decision=blocked_safety(),
    )

    assert result.allowed is False
    assert result.actions == ()
    assert result.has_blocker(
        "safety_gate_blocked"
    )
    assert "safety gate" in result.reason.lower()


def test_entry_payload_mismatch_is_blocked():
    plan = approved_entry_plan()

    bad_plan = DryRunPlan(
        symbol=plan.symbol,
        approved=plan.approved,
        reason=plan.reason,
        shares=plan.shares,
        planned_risk=plan.planned_risk,
        position_value=plan.position_value,
        entry_review_payload={
            **plan.entry_review_payload,
            "quantity": "9",
        },
        entry_placement_payload={
            **plan.entry_placement_payload,
            "side": "sell",
        },
        protective_stop_payload=(
            plan.protective_stop_payload
        ),
    )

    result = BrokerActionPlanner.plan_entry(
        plan=bad_plan,
        safety_decision=approved_safety(),
    )

    assert result.allowed is False
    assert result.has_blocker(
        "entry_review_quantity_mismatch"
    )
    assert result.has_blocker(
        "invalid_entry_placement_side"
    )


def test_initial_protection_reviews_then_places_stop(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_initial_protection(
            trade=trade,
            stop_payload=stop_payload(),
            open_orders=(),
        )
    )

    assert result.allowed is True
    assert (
        result.purpose
        == BrokerPlanPurpose
        .PROTECT_FILLED_ENTRY
    )

    assert result.action_kinds() == (
        BrokerActionKind
        .REVIEW_PROTECTIVE_STOP,
        BrokerActionKind
        .PLACE_PROTECTIVE_STOP,
    )

    assert (
        result.actions[1].depends_on_sequence
        == 1
    )
    assert (
        result.actions[1]
        .requires_review_ref_id
        is True
    )


def test_initial_protection_blocks_existing_sell_order(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_initial_protection(
            trade=trade,
            stop_payload=stop_payload(),
            open_orders=(
                make_sell_order(
                    order_id="unknown-sell"
                ),
            ),
        )
    )

    assert result.allowed is False
    assert result.has_blocker(
        "existing_sell_order"
    )
    assert result.actions == ()


def test_initial_protection_blocks_quantity_mismatch(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_initial_protection(
            trade=trade,
            stop_payload=stop_payload(
                quantity="9"
            ),
        )
    )

    assert result.allowed is False
    assert result.has_blocker(
        "protective_stop_quantity_mismatch"
    )


def test_valid_stop_replacement_sequence(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_stop_replacement(
            trade=trade,
            replacement_stop_payload=(
                stop_payload(
                    stop_price="100"
                )
            ),
            open_orders=(
                make_sell_order(),
            ),
        )
    )

    assert result.allowed is True
    assert (
        result.purpose
        == BrokerPlanPurpose
        .REPLACE_PROTECTIVE_STOP
    )

    assert result.action_kinds() == (
        BrokerActionKind
        .CANCEL_PROTECTIVE_STOP,
        BrokerActionKind
        .CONFIRM_STOP_CANCELLED,
        BrokerActionKind
        .REVIEW_PROTECTIVE_STOP,
        BrokerActionKind
        .PLACE_PROTECTIVE_STOP,
    )

    assert [
        action.sequence
        for action in result.actions
    ] == [1, 2, 3, 4]

    assert [
        action.depends_on_sequence
        for action in result.actions
    ] == [None, 1, 2, 3]

    assert (
        result.actions[-1]
        .requires_review_ref_id
        is True
    )


def test_stop_replacement_blocks_conflicting_sells(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_stop_replacement(
            trade=trade,
            replacement_stop_payload=(
                stop_payload()
            ),
            open_orders=(
                make_sell_order(),
                make_sell_order(
                    order_id="other-sell",
                    order_type="limit",
                    trigger="immediate",
                ),
            ),
        )
    )

    assert result.allowed is False
    assert result.has_blocker(
        "conflicting_sell_orders"
    )


def test_stop_replacement_requires_current_broker_stop(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = (
        BrokerActionPlanner
        .plan_stop_replacement(
            trade=trade,
            replacement_stop_payload=(
                stop_payload()
            ),
            open_orders=(),
        )
    )

    assert result.allowed is False
    assert result.has_blocker(
        "current_stop_not_unique"
    )


def test_protected_exit_cancels_stop_before_review(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(),
        open_orders=(
            make_sell_order(),
        ),
    )

    assert result.allowed is True
    assert (
        result.purpose
        == BrokerPlanPurpose.EXIT_POSITION
    )

    assert result.action_kinds() == (
        BrokerActionKind
        .CANCEL_PROTECTIVE_STOP,
        BrokerActionKind
        .CONFIRM_STOP_CANCELLED,
        BrokerActionKind.REVIEW_EXIT,
        BrokerActionKind.PLACE_EXIT,
    )

    assert (
        result.actions[2].depends_on_sequence
        == 2
    )
    assert (
        result.actions[3].depends_on_sequence
        == 3
    )
    assert (
        result.actions[3]
        .requires_review_ref_id
        is True
    )


def test_unprotected_exit_reviews_then_places(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(),
        open_orders=(),
    )

    assert result.allowed is True

    assert result.action_kinds() == (
        BrokerActionKind.REVIEW_EXIT,
        BrokerActionKind.PLACE_EXIT,
    )

    assert (
        result.actions[1].depends_on_sequence
        == 1
    )
    assert (
        result.actions[1]
        .requires_review_ref_id
        is True
    )


def test_unprotected_exit_blocks_untracked_sell(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(),
        open_orders=(
            make_sell_order(
                order_id="unknown-sell"
            ),
        ),
    )

    assert result.allowed is False
    assert result.has_blocker(
        "untracked_sell_order"
    )


def test_protected_exit_blocks_additional_sell_order(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_protected_trade(store)

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(),
        open_orders=(
            make_sell_order(),
            make_sell_order(
                order_id="extra-sell",
                order_type="limit",
                trigger="immediate",
            ),
        ),
    )

    assert result.allowed is False
    assert result.has_blocker(
        "conflicting_sell_orders"
    )


def test_exit_payload_mismatch_is_blocked(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = make_unprotected_trade(store)

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(
            symbol="MSFT",
            quantity="9",
        ),
        open_orders=(),
    )

    assert result.allowed is False
    assert result.has_blocker(
        "exit_symbol_mismatch"
    )
    assert result.has_blocker(
        "exit_quantity_mismatch"
    )


def test_exit_is_blocked_for_non_open_trade(
    tmp_path,
):
    store = TradeStateStore(
        tmp_path / "state.sqlite"
    )

    trade = store.reserve_entry(
        strategy="orb_v0",
        symbol="AAPL",
        session_date=SESSION_DATE,
        quantity=10,
        planned_entry=Decimal("100"),
        initial_stop=Decimal("99"),
        target_price=Decimal("102"),
    )

    assert trade.status == TradeStatus.RESERVED

    result = BrokerActionPlanner.plan_exit(
        trade=trade,
        exit_payload=exit_payload(),
        open_orders=(),
    )

    assert result.allowed is False
    assert result.has_blocker(
        "trade_not_exit_eligible"
    )
    assert result.has_blocker(
        "missing_exit_quantity"
    )