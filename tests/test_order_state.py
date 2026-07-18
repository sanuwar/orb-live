from __future__ import annotations

from decimal import Decimal

from src.account_state import EquityOrder
from src.order_state import (
    OrderInstruction,
    OrderPhase,
    interpret_equity_order,
    interpret_order_status,
    normalize_order_state,
)


def test_normalize_order_state():
    assert (
        normalize_order_state(" Pending-Cancel ")
        == "pending_cancel"
    )
    assert (
        normalize_order_state("pending canceled")
        == "pending_cancelled"
    )
    assert (
        normalize_order_state("canceled")
        == "cancelled"
    )


def test_pending_order_without_fill_waits():
    decision = interpret_order_status(
        broker_state="confirmed",
        requested_quantity="10",
        cumulative_quantity="0",
    )

    assert decision.phase == OrderPhase.PENDING
    assert (
        decision.instruction
        == OrderInstruction.WAIT
    )
    assert decision.remaining_quantity == Decimal(
        "10"
    )
    assert decision.terminal is False
    assert (
        decision.requires_reconciliation
        is False
    )


def test_pending_order_with_fill_protects_partial():
    decision = interpret_order_status(
        broker_state="confirmed",
        requested_quantity="10",
        cumulative_quantity="4",
    )

    assert (
        decision.phase
        == OrderPhase.PARTIALLY_FILLED
    )
    assert (
        decision.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    )
    assert decision.filled_quantity == Decimal(
        "4"
    )
    assert decision.remaining_quantity == Decimal(
        "6"
    )
    assert decision.has_any_fill is True
    assert (
        decision.requires_reconciliation
        is True
    )


def test_partial_state_requires_partial_quantity():
    decision = interpret_order_status(
        broker_state="partially_filled",
        requested_quantity="10",
        cumulative_quantity="0",
    )

    assert decision.phase == OrderPhase.UNKNOWN
    assert (
        decision.instruction
        == OrderInstruction.FAIL_CLOSED
    )
    assert (
        decision.requires_reconciliation
        is True
    )


def test_filled_order_confirms_full_fill():
    decision = interpret_order_status(
        broker_state="filled",
        requested_quantity="10",
        cumulative_quantity="10",
    )

    assert decision.phase == OrderPhase.FILLED
    assert (
        decision.instruction
        == OrderInstruction.CONFIRM_FULL_FILL
    )
    assert decision.fully_filled is True
    assert decision.terminal is True
    assert decision.remaining_quantity == Decimal(
        "0"
    )


def test_filled_state_with_incomplete_quantity_fails_closed():
    decision = interpret_order_status(
        broker_state="filled",
        requested_quantity="10",
        cumulative_quantity="9",
    )

    assert decision.phase == OrderPhase.UNKNOWN
    assert (
        decision.instruction
        == OrderInstruction.FAIL_CLOSED
    )
    assert (
        decision.requires_reconciliation
        is True
    )


def test_cancelled_unfilled_order_releases_reservation():
    decision = interpret_order_status(
        broker_state="cancelled",
        requested_quantity="10",
        cumulative_quantity="0",
    )

    assert decision.phase == OrderPhase.CANCELLED
    assert (
        decision.instruction
        == OrderInstruction
        .RELEASE_UNFILLED_RESERVATION
    )
    assert (
        decision.safe_to_release_reservation
        is True
    )
    assert decision.terminal is True


def test_cancelled_partial_fill_still_requires_protection():
    decision = interpret_order_status(
        broker_state="cancelled",
        requested_quantity="10",
        cumulative_quantity="3",
    )

    assert (
        decision.phase
        == OrderPhase.PARTIALLY_FILLED
    )
    assert (
        decision.instruction
        == OrderInstruction.PROTECT_PARTIAL_FILL
    )
    assert decision.terminal is True
    assert (
        decision.safe_to_release_reservation
        is False
    )
    assert (
        decision.requires_reconciliation
        is True
    )


def test_rejected_unfilled_order_releases_reservation():
    decision = interpret_order_status(
        broker_state="rejected",
        requested_quantity="10",
        cumulative_quantity="0",
    )

    assert decision.phase == OrderPhase.REJECTED
    assert (
        decision.instruction
        == OrderInstruction
        .RELEASE_UNFILLED_RESERVATION
    )
    assert (
        decision.safe_to_release_reservation
        is True
    )


def test_unknown_state_fails_closed():
    decision = interpret_order_status(
        broker_state="unexpected_broker_state",
        requested_quantity="10",
        cumulative_quantity="0",
    )

    assert decision.phase == OrderPhase.UNKNOWN
    assert (
        decision.instruction
        == OrderInstruction.FAIL_CLOSED
    )
    assert (
        decision.requires_reconciliation
        is True
    )
    assert "unfamiliar" in decision.reason.lower()


def test_invalid_quantities_fail_closed():
    invalid_requested = interpret_order_status(
        broker_state="confirmed",
        requested_quantity="0",
        cumulative_quantity="0",
    )

    excessive_fill = interpret_order_status(
        broker_state="confirmed",
        requested_quantity="10",
        cumulative_quantity="11",
    )

    assert (
        invalid_requested.phase
        == OrderPhase.UNKNOWN
    )
    assert (
        excessive_fill.phase
        == OrderPhase.UNKNOWN
    )
    assert (
        invalid_requested.instruction
        == OrderInstruction.FAIL_CLOSED
    )
    assert (
        excessive_fill.instruction
        == OrderInstruction.FAIL_CLOSED
    )


def test_interpret_equity_order_wrapper():
    order = EquityOrder(
        order_id="order-1",
        symbol="AAPL",
        side="buy",
        order_type="limit",
        state="partially_filled",
        quantity=Decimal("10"),
        cumulative_quantity=Decimal("4"),
        limit_price=Decimal("100"),
        stop_price=None,
        trigger="immediate",
        placed_agent="agentic",
    )

    decision = interpret_equity_order(order)

    assert (
        decision.phase
        == OrderPhase.PARTIALLY_FILLED
    )
    assert decision.requested_quantity == Decimal(
        "10"
    )
    assert decision.filled_quantity == Decimal(
        "4"
    )
    assert decision.remaining_quantity == Decimal(
        "6"
    )