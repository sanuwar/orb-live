from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
import json

import pytest

from src.one_share_commissioning import (
    AtomicEvidenceStore, CommissioningError, CommissioningMachine,
    EASTERN, ENTRY_AUTHORIZATION_STAGE, FLATTEN_AUTHORIZATION_STAGE,
    EntryFillObservation, OrderObservation, RuntimeAuthorization, State, WriteDisposition,
    assess_provisional_protection, normalize_stop_price, validate_sanitized_evidence,
)


NOW = datetime(2026, 7, 27, 11, 5, tzinfo=EASTERN)


def auth(run_id, stage, **changes):
    values = dict(
        run_id=run_id, eastern_date=date(2026, 7, 27),
        window_start=NOW - timedelta(minutes=5),
        window_end=NOW + timedelta(minutes=5), stage=stage,
        symbol="AAPL", quantity=Decimal("1"), planned_risk_usd=Decimal("15"),
        acknowledges_loss_may_exceed_plan=True, operator_present=True,
        robinhood_ui_available=True, no_second_trade=True,
    )
    values.update(changes)
    return RuntimeAuthorization(**values)


def machine(tmp_path, run_id="run-1"):
    result = CommissioningMachine(run_id, AtomicEvidenceStore(tmp_path, run_id))
    result.start()
    return result


def to_entry_reconciling(value):
    value.preflight_passed(strategy_enabled=False, commissioned_accounts=1,
        aapl_position_quantity=0, related_open_orders=0, regular_market_hours=True)
    value.entry_reviewed(accepted=True)
    value.authorize_entry(auth(value.run_id, ENTRY_AUTHORIZATION_STAGE), now=NOW)
    value.entry_submitted(WriteDisposition.ACCEPTED)


def to_stop_reconciling(value):
    to_entry_reconciling(value)
    value.entry_observed(order=entry_order(), position_quantity=1)
    value.stop_reviewed(accepted=True, construction_seconds=1)
    value.stop_submitted(WriteDisposition.ACCEPTED)


def good_order():
    return OrderObservation(True, "AAPL", "sell", "stop_market", Decimal("1"),
        Decimal("0"), Decimal("1"), Decimal("185.00"), "confirmed")


def entry_order(*, filled=Decimal("1"), remaining=Decimal("0"),
                state="filled", price=Decimal("200"), **changes):
    values = dict(
        commissioned_account=True, symbol="AAPL", side="buy", order_type="market",
        requested_quantity=Decimal("1"), cumulative_quantity=filled,
        remaining_quantity=remaining, state=state, average_fill_price=price,
    )
    values.update(changes)
    return EntryFillObservation(**values)


def test_clean_one_share_entry_flow(tmp_path):
    value = machine(tmp_path)
    to_stop_reconciling(value)
    result = value.protection_observed(order=good_order(), position_quantity=1,
        competing_sell_orders=0, observation_seconds=2, unprotected_seconds=3)
    assert result.provisional
    assert value.state is State.EXIT_AUTHORIZATION_REQUIRED


def test_entry_review_rejection(tmp_path):
    value = machine(tmp_path)
    value.preflight_passed(strategy_enabled=False, commissioned_accounts=1,
        aapl_position_quantity=0, related_open_orders=0, regular_market_hours=True)
    value.entry_reviewed(accepted=False)
    assert value.state is State.FAILED_CLOSED


@pytest.mark.parametrize("disposition,state", [
    (WriteDisposition.REJECTED, State.FAILED_CLOSED),
    (WriteDisposition.AMBIGUOUS, State.OPERATOR_REQUIRED),
])
def test_entry_placement_rejection_or_ambiguity_has_no_retry(tmp_path, disposition, state):
    value = machine(tmp_path)
    value.preflight_passed(strategy_enabled=False, commissioned_accounts=1,
        aapl_position_quantity=0, related_open_orders=0, regular_market_hours=True)
    value.entry_reviewed(accepted=True)
    value.authorize_entry(auth(value.run_id, ENTRY_AUTHORIZATION_STAGE), now=NOW)
    value.entry_submitted(disposition)
    assert value.state is state
    assert value.entry_place_attempted


def test_zero_fill_through_deadline(tmp_path):
    value = machine(tmp_path); to_entry_reconciling(value)
    value.entry_observed(order=entry_order(filled=Decimal("0"), remaining=Decimal("1"),
                         state="confirmed", price=None), position_quantity=0,
                         deadline_exceeded=True)
    assert value.state is State.OPERATOR_REQUIRED


def test_exact_one_share_fill_and_missing_price(tmp_path):
    value = machine(tmp_path); to_entry_reconciling(value)
    value.entry_observed(order=entry_order(), position_quantity=1)
    assert value.state is State.ENTRY_FILLED
    missing = machine(tmp_path, "run-2"); to_entry_reconciling(missing)
    missing.entry_observed(order=entry_order(price=None), position_quantity=1)
    assert missing.state is State.OPERATOR_REQUIRED


@pytest.mark.parametrize("changes", [
    {"commissioned_account": False}, {"symbol": "MSFT"}, {"side": "sell"},
    {"requested_quantity": Decimal("2")}, {"state": "confirmed"},
])
def test_entry_requires_grounded_exact_fill(tmp_path, changes):
    value = machine(tmp_path); to_entry_reconciling(value)
    value.entry_observed(order=entry_order(**changes), position_quantity=1)
    assert value.state is State.OPERATOR_REQUIRED


@pytest.mark.parametrize("filled,position", [(Decimal("0.5"), 1), (1, 2)])
def test_invalid_quantity_requires_operator(tmp_path, filled, position):
    value = machine(tmp_path); to_entry_reconciling(value)
    value.entry_observed(order=entry_order(filled=filled, remaining=Decimal("0")),
                         position_quantity=position)
    assert value.state is State.OPERATOR_REQUIRED


def test_stop_calculation_and_normalization():
    assert normalize_stop_price("200.004") == Decimal("185.00")
    with pytest.raises(CommissioningError):
        normalize_stop_price("15")


@pytest.mark.parametrize("accepted,disposition", [
    (False, None), (True, WriteDisposition.REJECTED),
    (True, WriteDisposition.AMBIGUOUS),
])
def test_stop_failures_require_operator_without_retry(tmp_path, accepted, disposition):
    value = machine(tmp_path); to_entry_reconciling(value)
    value.entry_observed(order=entry_order(), position_quantity=1)
    value.stop_reviewed(accepted=accepted, construction_seconds=1)
    if disposition is not None:
        value.stop_submitted(disposition)
        assert value.stop_place_attempted
    assert value.state is State.OPERATOR_REQUIRED


@pytest.mark.parametrize("change", [
    {"symbol": "MSFT"}, {"side": "buy"},
    {"requested_quantity": Decimal("2")}, {"stop_price": Decimal("184")},
    {"state": "mystery"}, {"state": "cancelled"},
])
def test_provisional_protection_rejects_field_mismatch(change):
    result = assess_provisional_protection(order=replace(good_order(), **change),
        position_quantity=1, expected_stop_price=Decimal("185"), competing_sell_orders=0)
    assert not result.provisional and result.classification == "INDETERMINATE"


@pytest.mark.parametrize("position,competitors", [(0, 0), (1, 1)])
def test_provisional_protection_rejects_position_or_competitor(position, competitors):
    result = assess_provisional_protection(order=good_order(), position_quantity=position,
        expected_stop_price=185, competing_sell_orders=competitors)
    assert not result.provisional


def to_exit_authorization(value):
    to_stop_reconciling(value)
    value.protection_observed(order=good_order(), position_quantity=1,
        competing_sell_orders=0, observation_seconds=2, unprotected_seconds=3)
    value.authorize_flatten(auth(value.run_id, FLATTEN_AUTHORIZATION_STAGE), now=NOW)


def test_stop_cancel_pending_then_stop_fills(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="pending_cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    assert value.state is State.STOP_CANCEL_RECONCILING
    value.stop_cancel_observed(stop_state="filled", cumulative_quantity=1,
                               remaining_quantity=0, position_quantity=0)
    assert value.state is State.VERIFIED_FLAT
    assert not value.exit_place_attempted


def test_cancelled_stop_followed_by_exit_and_complete(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    value.exit_submitted(WriteDisposition.ACCEPTED, latest_position_quantity=1)
    value.exit_observed(position_quantity=0, related_nonterminal_orders=0)
    evidence_path = value.complete(related_nonterminal_orders=0)
    assert value.state is State.COMPLETE
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert payload["planned_risk_usd"] == 15
    assert "fill_price" not in evidence_path.read_text(encoding="utf-8")


def test_flat_account_prevents_duplicate_exit(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=0)
    assert value.state is State.VERIFIED_FLAT and not value.exit_place_attempted


def test_unresolved_stop_prevents_exit(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="pending_cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    value.exit_submitted(WriteDisposition.ACCEPTED, latest_position_quantity=1)
    assert value.state is State.OPERATOR_REQUIRED and not value.exit_place_attempted


def test_ambiguous_exit_reconciles_and_never_writes_again(tmp_path):
    """An ambiguous exit is read back, not concluded, and never rewritten.

    The write is spent the moment it is attempted, so the state machine moves
    to reconciliation rather than to a verdict: the sell may already be filled.
    """
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    value.exit_submitted(WriteDisposition.AMBIGUOUS, latest_position_quantity=1)
    assert value.state is State.EXIT_RECONCILING
    assert value.exit_place_attempted
    with pytest.raises(CommissioningError):
        value.exit_submitted(WriteDisposition.ACCEPTED, latest_position_quantity=1)


def test_rejected_exit_is_terminal_and_never_retried(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    value.exit_submitted(WriteDisposition.REJECTED, latest_position_quantity=1)
    assert value.state is State.OPERATOR_REQUIRED and value.exit_place_attempted


@pytest.mark.parametrize("position,orders,reason", [
    (1, 0, "EXIT_CONFIRMATION_DEADLINE_EXCEEDED"),
    (0, 1, "RELATED_ORDER_REMAINS"),
])
def test_final_position_or_order_failure(tmp_path, position, orders, reason):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="cancelled", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    value.exit_submitted(WriteDisposition.ACCEPTED, latest_position_quantity=1)
    value.exit_observed(position_quantity=position, related_nonterminal_orders=orders,
                        deadline_exceeded=True)
    assert value.state is State.OPERATOR_REQUIRED
    assert value.evidence.completion_or_failure_reason == reason


@pytest.mark.parametrize("changes", [
    {"window_end": NOW - timedelta(seconds=1)},
    {"operator_present": False}, {"no_second_trade": False},
])
def test_authorization_rejections(tmp_path, changes):
    value = machine(tmp_path)
    value.preflight_passed(strategy_enabled=False, commissioned_accounts=1,
        aapl_position_quantity=0, related_open_orders=0, regular_market_hours=True)
    value.entry_reviewed(accepted=True)
    authorization = auth(value.run_id, ENTRY_AUTHORIZATION_STAGE, **changes)
    with pytest.raises(CommissioningError):
        value.authorize_entry(authorization, now=NOW)
    assert value.state is State.FAILED_CLOSED


def test_authorization_and_run_id_are_not_reusable(tmp_path):
    authorization = auth("run-1", ENTRY_AUTHORIZATION_STAGE)
    authorization.consume(now=NOW, expected_stage=ENTRY_AUTHORIZATION_STAGE, run_id="run-1")
    with pytest.raises(CommissioningError):
        authorization.consume(now=NOW, expected_stage=ENTRY_AUTHORIZATION_STAGE, run_id="run-1")
    value = machine(tmp_path)
    with pytest.raises(CommissioningError):
        machine(tmp_path)


def test_authorization_requires_eastern_narrow_window(tmp_path):
    naive = auth("run-1", ENTRY_AUTHORIZATION_STAGE)
    naive.window_start = naive.window_start.replace(tzinfo=None)
    with pytest.raises(CommissioningError):
        naive.consume(now=NOW, expected_stage=ENTRY_AUTHORIZATION_STAGE, run_id="run-1")
    wide = auth("run-2", ENTRY_AUTHORIZATION_STAGE,
                window_start=NOW - timedelta(minutes=8),
                window_end=NOW + timedelta(minutes=8))
    with pytest.raises(CommissioningError):
        wide.consume(now=NOW, expected_stage=ENTRY_AUTHORIZATION_STAGE, run_id="run-2")


@pytest.mark.parametrize("run_id", ["", ".", "..", "../escape", "bad/name", "bad\\name"])
def test_run_id_must_be_path_safe(tmp_path, run_id):
    with pytest.raises(CommissioningError):
        machine(tmp_path, run_id)


def test_strategy_enabled_and_second_trade_policy_rejected(tmp_path):
    value = machine(tmp_path)
    with pytest.raises(CommissioningError):
        value.preflight_passed(strategy_enabled=True, commissioned_accounts=1,
            aapl_position_quantity=0, related_open_orders=0, regular_market_hours=True)
    assert value.state is State.FAILED_CLOSED


def test_prohibited_tool_call_rejected(tmp_path):
    value = machine(tmp_path)
    with pytest.raises(CommissioningError):
        value.record_tool_call("get_market_data")
    assert value.state is State.OPERATOR_REQUIRED


def test_flatten_requires_second_authorization(tmp_path):
    value = machine(tmp_path); to_stop_reconciling(value)
    value.protection_observed(order=good_order(), position_quantity=1,
        competing_sell_orders=0, observation_seconds=2, unprotected_seconds=3)
    with pytest.raises(CommissioningError):
        value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    assert value.state is State.OPERATOR_REQUIRED


def test_partial_directory_recovery_fails_closed(tmp_path):
    partial = tmp_path / "used.partial"
    partial.mkdir()
    with pytest.raises(CommissioningError):
        machine(tmp_path, "used")


def test_unknown_cancel_state_requires_operator(tmp_path):
    value = machine(tmp_path); to_exit_authorization(value)
    value.stop_cancel_requested(WriteDisposition.ACCEPTED)
    value.stop_cancel_observed(stop_state="future_state", cumulative_quantity=0,
                               remaining_quantity=1, position_quantity=1)
    assert value.state is State.OPERATOR_REQUIRED


@pytest.mark.parametrize("field", [
    "account_number", "fingerprint", "order_id", "ref_id", "fill_price",
    "stop_price", "token", "cookie", "oauth_url", "raw_response",
])
def test_evidence_redaction_rejects_private_fields(field):
    with pytest.raises(CommissioningError):
        validate_sanitized_evidence({field: "private"})
