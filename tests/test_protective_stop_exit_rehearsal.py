from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.account_state import EquityOrder
from src.execution_safety_governance import (
    CommissioningGovernanceEvidence,
    CommissioningMode,
)
from src.exit_engine import ET, PositionState
from src.order_observer import ObservedEquityOrder, OrderExecution
from src.protective_stop_exit_rehearsal import (
    StopExitRehearsalState,
    build_protective_stop_exit_rehearsal,
    compute_protective_stop_exit_rehearsal_digest,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_attended_safety_evaluation_bridge import (
    NOW,
    TRUSTED,
    allowed_acknowledgements,
)
from tests.test_live_reconciliation import make_account_state, make_position
from tests.test_post_entry_protection_rehearsal import rehearse


def order(
    *,
    state="confirmed",
    symbol="AAPL",
    side="sell",
    quantity="1",
    cumulative="0",
    stop_price="99",
    order_id="stop-1",
):
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type="stop_market",
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(cumulative),
        limit_price=None,
        stop_price=Decimal(stop_price),
        trigger="stop",
        placed_agent="agentic",
    )


def observed(value=None, *, average=None, fees="0", when=NOW):
    value = value or order()
    executions = ()
    if value.cumulative_quantity > 0 and average is not None:
        executions = (
            OrderExecution(
                execution_id="execution-1",
                price=Decimal(average),
                quantity=value.cumulative_quantity,
                fees=Decimal(fees),
                timestamp=when,
            ),
        )
    return ObservedEquityOrder(
        order=value,
        average_price=None if average is None else Decimal(average),
        fees=Decimal(fees),
        created_at=when - timedelta(minutes=1),
        last_transaction_at=when,
        market_hours="regular_hours",
        time_in_force="gtc",
        executions=executions,
    )


def entry_observation(*, average="100", fees="0.01"):
    value = EquityOrder(
        order_id="entry-1",
        symbol="AAPL",
        side="buy",
        order_type="market",
        state="filled",
        quantity=Decimal("1"),
        cumulative_quantity=Decimal("1"),
        limit_price=None,
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )
    return observed(value, average=average, fees=fees)


def snapshot(*, quantity="1", account_number="TEST12345678"):
    positions = () if Decimal(quantity) == 0 else (make_position(quantity=quantity),)
    state = make_account_state(positions=positions, account_number=account_number)
    return make_broker_snapshot(
        state,
        reconciliation_id="stop-cycle",
        started_at=NOW - timedelta(seconds=4),
        positions_read_at=NOW - timedelta(seconds=3),
        orders_read_at=NOW - timedelta(seconds=2),
        completed_at=NOW - timedelta(seconds=1),
    )


def governance(*, operator_attended=True, acknowledgements=True):
    return CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=operator_attended,
        operator_acknowledgements=(
            allowed_acknowledgements() if acknowledgements else None
        ),
        source_hash_verified=True,
        release_sha="a" * 40,
        implementation_digest=TRUSTED,
        account_fingerprint_matched=True,
        account_fingerprint_reference=snapshot().metadata.account_fingerprint,
        suspension_state="CLEAR",
    )


def build(**changes):
    return build_protective_stop_exit_rehearsal(
        changes.pop("source", rehearse()),
        snapshot=changes.pop("snapshot", snapshot()),
        protective_stop_order_id=changes.pop("protective_stop_order_id", "stop-1"),
        stop_observation=changes.pop("stop_observation", observed()),
        stop_observed_at=changes.pop("stop_observed_at", NOW),
        entry_observation=changes.pop("entry_observation", entry_observation()),
        evaluated_at=changes.pop("evaluated_at", NOW),
        release_sha=changes.pop("release_sha", "a" * 40),
        trusted_implementation_digest=TRUSTED,
        **changes,
    )


def position(*, current="101", high="101", hour=11, minute=0):
    return PositionState(
        symbol="AAPL",
        entry_price=Decimal("100"),
        initial_stop_price=Decimal("99"),
        current_stop_price=Decimal("99"),
        current_price=Decimal(current),
        highest_price_seen=Decimal(high),
        timestamp=datetime(2026, 7, 20, hour, minute, tzinfo=ET),
    )


def test_fixture_stop_reconciles_but_snapshot_without_stop_does_not_ground():
    result = build()
    assert result.rehearsal_state is StopExitRehearsalState.OPEN_PROTECTED
    assert result.stop_reconciliation_state == "STOP_WORKING"
    assert result.m13_protection_reason == "STOP_NOT_FOUND"
    assert result.protection_grounded is False
    assert result.broker_write_count == 0 and result.execution_authorized is False


def test_stop_identity_quantity_price_and_snapshot_fail_closed():
    assert build(stop_observation=observed(order(symbol="MSFT"))).rehearsal_state is StopExitRehearsalState.STOP_IDENTITY_MISMATCH
    assert build(stop_observation=observed(order(quantity="2"))).rehearsal_state is StopExitRehearsalState.STOP_QUANTITY_MISMATCH
    assert build(stop_observation=observed(order(stop_price="98"))).rehearsal_state is StopExitRehearsalState.STOP_PRICE_MISMATCH
    assert build(evaluated_at=NOW + timedelta(seconds=31)).rehearsal_state is StopExitRehearsalState.SNAPSHOT_STALE
    assert build(snapshot=snapshot(account_number="OTHER87654321")).rehearsal_state is StopExitRehearsalState.ACCOUNT_MISMATCH


def test_missing_cancelled_rejected_and_partial_stops_are_conservative():
    assert build(stop_observation=None).rehearsal_state is StopExitRehearsalState.UNPROTECTED_POSITION
    cancelled = observed(order(state="cancelled"))
    assert build(stop_observation=cancelled).rehearsal_state is StopExitRehearsalState.STOP_CANCELLED
    rejected = observed(order(state="rejected"))
    assert build(stop_observation=rejected).rehearsal_state is StopExitRehearsalState.STOP_REJECTED
    partial = observed(order(state="partially_filled", cumulative="0.5"), average="99")
    result = build(stop_observation=partial, snapshot=snapshot(quantity="0.5"))
    assert result.rehearsal_state is StopExitRehearsalState.STOP_PARTIALLY_FILLED
    assert result.remaining_position_quantity == "0.5"
    assert result.protection_grounded is False


def test_full_stop_fill_reconciles_authoritative_exit_and_pnl():
    filled = observed(order(state="filled", cumulative="1"), average="99", fees="0.01")
    result = build(stop_observation=filled, snapshot=snapshot(quantity="0"))
    assert result.rehearsal_state is StopExitRehearsalState.STOP_FILLED_EXIT_RECONCILED
    assert result.entry_fill_price == "100" and result.exit_fill_price == "99"
    assert result.realized_pnl == "-1.02"
    assert result.realized_pnl_source == "BROKER_AUTHORITATIVE_FILLS_LESS_REPORTED_FEES"
    assert result.remaining_position_quantity == "0"


def test_full_stop_fill_requires_flat_position_and_authoritative_fill_facts():
    filled = observed(order(state="filled", cumulative="1"), average="99")
    assert build(stop_observation=filled).rehearsal_state is StopExitRehearsalState.POSITION_MISMATCH
    assert build(stop_observation=filled, snapshot=snapshot(quantity="0"), entry_observation=None).rehearsal_state is StopExitRehearsalState.UNKNOWN_EXIT_STATE


def test_target_and_time_exit_preserve_working_stop_and_do_not_assume_oco():
    target = build(exit_position=position(current="102", high="102"))
    assert target.rehearsal_state is StopExitRehearsalState.OPEN_PROTECTED
    assert target.exit_intent == "exit_profit_target"
    assert "EXIT_WITH_STOP_DOUBLE_SELL_RISK" in target.blocking_reasons
    timed = build(exit_position=position(current="101", high="101", hour=15, minute=45))
    assert timed.exit_intent == "exit_time"
    assert timed.exit_safety_result == "NOT_EVALUATED"


def test_confirmed_cancel_then_exit_uses_bridge_and_allow_is_non_authorizing():
    clear = SimpleNamespace(state=SimpleNamespace(value="CLEAR"))
    result = build(
        stop_observation=observed(order(state="cancelled")),
        exit_position=position(current="102", high="102"),
        exit_stop_cancellation_confirmed=True,
        suspension_decision=clear,
        governance_evidence=governance(),
    )
    assert result.rehearsal_state is StopExitRehearsalState.EXIT_REHEARSAL_READY
    assert result.exit_safety_result == "ALLOW"
    assert result.exit_action_identity not in {None, result.protective_action_identity}
    assert result.execution_authorized is False and result.broker_write_count == 0


def test_exit_safety_deny_incomplete_and_stale_block():
    common = dict(
        stop_observation=observed(order(state="cancelled")),
        exit_position=position(current="102", high="102"),
        exit_stop_cancellation_confirmed=True,
        suspension_decision=SimpleNamespace(state=SimpleNamespace(value="CLEAR")),
    )
    assert build(**common, governance_evidence=governance(operator_attended=False)).rehearsal_state is StopExitRehearsalState.SAFETY_DENY
    assert build(**common, governance_evidence=None).rehearsal_state is StopExitRehearsalState.SAFETY_INCOMPLETE
    assert build(**common, governance_evidence=governance(), safety_decision_time=NOW - timedelta(seconds=31)).rehearsal_state is StopExitRehearsalState.SAFETY_STALE


def test_source_digest_and_account_binding_fail_closed():
    source = rehearse()
    assert build(source=replace(source, release_sha="b" * 40)).rehearsal_state is StopExitRehearsalState.SOURCE_MISMATCH
    changed = replace(snapshot().metadata, account_fingerprint="b" * 64)
    result = build(snapshot=replace(snapshot(), metadata=changed))
    assert result.rehearsal_state in {StopExitRehearsalState.SOURCE_MISMATCH, StopExitRehearsalState.ACCOUNT_MISMATCH}


def test_digest_is_deterministic_and_write_boundary_is_unreachable():
    first, second = build(), build()
    assert first == second
    assert compute_protective_stop_exit_rehearsal_digest(first) == first.rehearsal_digest
    path = Path(__file__).resolve().parents[1] / "src/protective_stop_exit_rehearsal.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "evaluate_execution_safety" not in names
    assert not (names & {"RobinhoodCommissioningAdapter", "place_equity_order", "cancel_equity_order", "call_tool", "_call", "flatten"})
