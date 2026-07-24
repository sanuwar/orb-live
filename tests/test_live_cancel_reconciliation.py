"""
Offline tests for the asynchronous cancellation reconciliation model.

No broker access occurs. Every fixture value is INVENTED.

The central property under test: ACCEPTANCE IS NOT COMPLETION. An accepted
cancel request never becomes a verified cancellation, and never authorizes
anything.
"""
from __future__ import annotations

import ast
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.live_cancel_reconciliation import (
    OPERATOR_RESOLUTION_STATES,
    UNRESOLVED_STATES,
    CancelReconciliationOutcome,
    CancelReconciliationState,
    reconcile_cancellation,
)
from src.live_order_contract import CancelAcceptance


REPOSITORY_ROOT = Path(__file__).parent.parent

ACCEPTED = CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC
NOT_ACCEPTED = CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED


def observed(**overrides):
    """A cancel-requested reconciliation over an invented order."""
    arguments = {
        "cancel_requested": True,
        "acceptance": ACCEPTED,
        "observed_state": "pending_cancelled",
        "observed_submitted_quantity": "4",
        "observed_cumulative_quantity": "0",
    }
    arguments.update(overrides)
    return reconcile_cancellation(**arguments)


# ---------- every required state is representable ----------

def test_every_required_state_exists():
    assert {member.value for member in CancelReconciliationState} == {
        "CANCEL_NOT_REQUESTED",
        "CANCEL_REQUEST_ACCEPTED",
        "CANCELLATION_PENDING",
        "CANCELLATION_VERIFIED",
        "PARTIAL_FILL_WITH_REMAINDER_CANCELLED",
        "FILL_RACED_CANCELLATION",
        "CANCELLATION_REJECTED",
        "INDETERMINATE",
    }


# ---------- no cancel requested ----------

def test_no_cancel_requested():
    outcome = reconcile_cancellation(cancel_requested=False)
    assert outcome.state is CancelReconciliationState.CANCEL_NOT_REQUESTED
    assert outcome.cancellation_verified is False


def test_acceptance_without_a_request_is_indeterminate():
    outcome = reconcile_cancellation(
        cancel_requested=False, acceptance=ACCEPTED
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


# ---------- acceptance is not completion ----------

def test_accepted_alone_is_not_a_completed_cancellation():
    outcome = reconcile_cancellation(
        cancel_requested=True, acceptance=ACCEPTED
    )
    assert outcome.state is CancelReconciliationState.CANCEL_REQUEST_ACCEPTED
    assert outcome.cancellation_verified is False
    assert outcome.is_unresolved is True


def test_accepted_while_the_order_is_still_working_stays_unverified():
    outcome = observed(observed_state="queued")
    assert outcome.state is CancelReconciliationState.CANCEL_REQUEST_ACCEPTED
    assert outcome.cancellation_verified is False


@pytest.mark.parametrize("state", list(CancelReconciliationState))
def test_no_state_ever_authorizes_an_exit(state):
    outcome = CancelReconciliationOutcome(state=state, reason="fixture")
    assert outcome.authorizes_exit is False


def test_accepted_never_authorizes_an_exit():
    outcome = reconcile_cancellation(
        cancel_requested=True, acceptance=ACCEPTED
    )
    assert outcome.authorizes_exit is False


def test_not_accepted_is_never_a_successful_cancellation():
    outcome = reconcile_cancellation(
        cancel_requested=True, acceptance=NOT_ACCEPTED
    )
    assert outcome.state is CancelReconciliationState.CANCELLATION_REJECTED
    assert outcome.cancellation_verified is False
    assert outcome.requires_operator_resolution is True


def test_a_request_with_no_response_and_no_read_is_indeterminate():
    outcome = reconcile_cancellation(cancel_requested=True)
    assert outcome.state is CancelReconciliationState.INDETERMINATE


# ---------- pending cancellation stays unresolved ----------

def test_pending_cancelled_remains_unresolved():
    outcome = observed(observed_state="pending_cancelled")
    assert outcome.state is CancelReconciliationState.CANCELLATION_PENDING
    assert outcome.cancellation_verified is False
    assert outcome.is_unresolved is True
    assert outcome.requires_operator_resolution is False


def test_unresolved_states_are_exactly_the_three_pollable_ones():
    assert UNRESOLVED_STATES == {
        CancelReconciliationState.CANCEL_REQUEST_ACCEPTED,
        CancelReconciliationState.CANCELLATION_PENDING,
        CancelReconciliationState.INDETERMINATE,
    }


# ---------- cancellation is verified only after reconciliation ----------

def test_cancelled_with_no_fill_is_verified():
    outcome = observed(
        observed_state="cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="0",
    )
    assert outcome.state is CancelReconciliationState.CANCELLATION_VERIFIED
    assert outcome.cancellation_verified is True
    assert outcome.is_unresolved is False


def test_cancelled_with_a_reported_fill_is_a_race_not_a_cancellation():
    """A cancelled state carrying filled shares must never verify."""
    outcome = observed(
        observed_state="cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
    )
    assert outcome.state is CancelReconciliationState.FILL_RACED_CANCELLATION
    assert outcome.cancellation_verified is False


@pytest.mark.parametrize(
    "submitted, cumulative",
    [("4", "5"), ("-1", "0"), ("abc", "0"), ("4", "abc"), (None, "0"),
     ("4", None), (1.5, "0"), ("4", 1.5)],
)
def test_cancelled_with_irreconcilable_quantities_is_indeterminate(
    submitted, cumulative
):
    outcome = observed(
        observed_state="cancelled",
        observed_submitted_quantity=submitted,
        observed_cumulative_quantity=cumulative,
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE
    assert outcome.cancellation_verified is False


# ---------- partial fill with a cancelled remainder ----------

def test_partial_fill_rest_cancelled_reconciles_with_a_verified_position():
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
        verified_position_quantity="1",
    )
    assert outcome.state is (
        CancelReconciliationState.PARTIAL_FILL_WITH_REMAINDER_CANCELLED
    )
    assert outcome.requires_operator_resolution is True
    assert outcome.cancellation_verified is False


def test_partial_fill_rest_cancelled_requires_a_position_reading():
    """Without independent position evidence it must not be confirmed."""
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


def test_partial_fill_rest_cancelled_rejects_a_disagreeing_position():
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
        verified_position_quantity="2",
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


@pytest.mark.parametrize("cumulative", ["0", "4"])
def test_partial_fill_rest_cancelled_requires_a_genuine_partial(cumulative):
    """Zero filled or fully filled contradicts the reported state."""
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity=cumulative,
        verified_position_quantity=cumulative,
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


def test_partial_fill_rest_cancelled_rejects_a_malformed_position():
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
        verified_position_quantity="abc",
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


# ---------- fill races ----------

def test_a_filled_order_after_a_cancel_request_is_a_race():
    outcome = observed(
        observed_state="filled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="4",
    )
    assert outcome.state is CancelReconciliationState.FILL_RACED_CANCELLATION
    assert outcome.requires_operator_resolution is True


def test_any_fill_beyond_the_request_baseline_is_a_race():
    """Even a still-working order that filled further is surfaced."""
    outcome = observed(
        observed_state="partially_filled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="2",
        cumulative_quantity_at_request="1",
    )
    assert outcome.state is CancelReconciliationState.FILL_RACED_CANCELLATION


def test_no_new_fill_against_the_baseline_is_not_a_race():
    outcome = observed(
        observed_state="cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="0",
        cumulative_quantity_at_request="0",
    )
    assert outcome.state is CancelReconciliationState.CANCELLATION_VERIFIED


def test_a_race_beats_a_terminal_cancelled_state():
    """A fill after the request is surfaced even alongside a cancellation."""
    outcome = observed(
        observed_state="partially_filled_rest_cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="1",
        cumulative_quantity_at_request="0",
        verified_position_quantity="1",
    )
    assert outcome.state is CancelReconciliationState.FILL_RACED_CANCELLATION


def test_a_malformed_baseline_is_indeterminate():
    outcome = observed(
        observed_state="cancelled",
        observed_cumulative_quantity="0",
        cumulative_quantity_at_request="abc",
    )
    assert outcome.state is CancelReconciliationState.INDETERMINATE


# ---------- unknown and non-cancellation terminal states ----------

@pytest.mark.parametrize(
    "state",
    ["unknown_state", "", "   ", "canceled", "pending_canceled", "open",
     "accepted", 7, True, []],
)
def test_unknown_states_fail_closed(state):
    outcome = observed(observed_state=state)
    assert outcome.state is CancelReconciliationState.INDETERMINATE
    assert outcome.cancellation_verified is False


@pytest.mark.parametrize(
    "state", ["rejected", "failed", "voided", "locate_failed"]
)
def test_rejected_or_failed_is_not_a_cancellation_outcome(state):
    outcome = observed(observed_state=state)
    assert outcome.state is CancelReconciliationState.INDETERMINATE
    assert outcome.cancellation_verified is False


def test_a_working_order_without_acceptance_is_indeterminate():
    outcome = observed(observed_state="queued", acceptance=None)
    assert outcome.state is CancelReconciliationState.INDETERMINATE


def test_a_working_order_with_a_refused_cancel_is_rejected():
    outcome = observed(observed_state="queued", acceptance=NOT_ACCEPTED)
    assert outcome.state is CancelReconciliationState.CANCELLATION_REJECTED


def test_a_read_back_cancelled_state_wins_over_a_refused_request():
    """The order-status read is the authority on the final state."""
    outcome = observed(
        observed_state="cancelled",
        acceptance=NOT_ACCEPTED,
        observed_cumulative_quantity="0",
    )
    assert outcome.state is CancelReconciliationState.CANCELLATION_VERIFIED


# ---------- outcome shape ----------

def test_operator_resolution_states_are_the_four_unsafe_ones():
    assert OPERATOR_RESOLUTION_STATES == {
        CancelReconciliationState.PARTIAL_FILL_WITH_REMAINDER_CANCELLED,
        CancelReconciliationState.FILL_RACED_CANCELLATION,
        CancelReconciliationState.CANCELLATION_REJECTED,
        CancelReconciliationState.INDETERMINATE,
    }


def test_only_verified_cancellation_reports_verified():
    for state in CancelReconciliationState:
        outcome = CancelReconciliationOutcome(state=state, reason="fixture")
        assert outcome.cancellation_verified is (
            state is CancelReconciliationState.CANCELLATION_VERIFIED
        )


def test_the_outcome_is_immutable():
    outcome = reconcile_cancellation(cancel_requested=False)
    with pytest.raises(FrozenInstanceError):
        outcome.state = (  # type: ignore[misc]
            CancelReconciliationState.CANCELLATION_VERIFIED
        )


def test_reasons_never_carry_quantities_or_identifiers():
    outcome = observed(
        observed_state="cancelled",
        observed_submitted_quantity="4",
        observed_cumulative_quantity="0",
    )
    for digit in "0123456789":
        assert digit not in outcome.reason


# ---------- static safety ----------

def test_module_submits_nothing_and_retries_nothing():
    source = (
        REPOSITORY_ROOT / "src" / "live_cancel_reconciliation.py"
    ).read_text(encoding="utf-8").lower()

    for forbidden in (
        "call_tool",
        "def place",
        "def submit",
        "def cancel_order",
        "def exit",
        "def retry",
        "max_retries",
        "time.sleep",
        "asyncio.sleep",
        "robinhood",
        "httpx",
    ):
        assert forbidden not in source


def test_module_has_no_async_entry_point():
    tree = ast.parse(
        (REPOSITORY_ROOT / "src" / "live_cancel_reconciliation.py").read_text(
            encoding="utf-8"
        )
    )
    for node in ast.walk(tree):
        assert not isinstance(node, ast.AsyncFunctionDef)
        assert not isinstance(node, ast.Await)
