"""
Offline tests for the live order-contract boundary.

Covers decimal-safe remaining-quantity derivation and the fail-closed
response-contract boundary. No broker access occurs. No historical scalar
value from any real order is reproduced here.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.live_order_contract import (
    ABSENT_ORDER_FIELDS,
    OBSERVED_ORDER_FIELDS,
    UNESTABLISHED_RESPONSE_CONTRACTS,
    BrokerCallOutcome,
    BrokerCallResult,
    ObservedOrderField,
    QuantityDerivationError,
    ResponseContractUnavailableError,
    derive_quantities,
    parse_cancel_response,
    parse_place_response,
    remaining_quantity,
    require_response_contract,
)


# ---------- observed field names ----------

def test_observed_fields_match_checkpoint_7b():
    assert OBSERVED_ORDER_FIELDS == {
        "id",
        "state",
        "quantity",
        "cumulative_quantity",
        "average_price",
        "side",
        "symbol",
        "type",
        "price",
        "stop_price",
        "created_at",
        "last_transaction_at",
    }


def test_ref_id_and_remaining_quantity_are_recorded_absent():
    assert ABSENT_ORDER_FIELDS == {"ref_id", "remaining_quantity"}
    assert not (ABSENT_ORDER_FIELDS & OBSERVED_ORDER_FIELDS)


def test_observed_field_enum_is_consistent():
    assert {f.value for f in ObservedOrderField} == OBSERVED_ORDER_FIELDS


# ---------- decimal-safe remaining quantity ----------

@pytest.mark.parametrize(
    "quantity, cumulative, expected",
    [
        ("10", "0", "10"),
        ("10", "4", "6"),
        ("10", "10", "0"),
        ("1", "1", "0"),
        ("0", "0", "0"),
        ("2.5", "0.5", "2"),
        ("0.000001", "0", "0.000001"),
        (Decimal("7"), Decimal("3"), "4"),
        (5, 2, "3"),
    ],
)
def test_remaining_quantity_is_derived_exactly(
    quantity, cumulative, expected
):
    assert remaining_quantity(quantity, cumulative) == Decimal(expected)


def test_derivation_avoids_binary_floating_point():
    """0.1 + 0.2 style error must not appear."""
    derived = derive_quantities("0.3", "0.1")
    assert derived.remaining == Decimal("0.2")
    assert str(derived.remaining) == "0.2"


@pytest.mark.parametrize("bad", [1.5, 0.1, -2.0])
def test_binary_floats_are_rejected(bad):
    with pytest.raises(QuantityDerivationError):
        remaining_quantity(bad, "0")
    with pytest.raises(QuantityDerivationError):
        remaining_quantity("10", bad)


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "abc", "1.2.3", "1e", "NaN", "Infinity", "-Infinity", "1,5"],
)
def test_malformed_numeric_strings_are_rejected(bad):
    with pytest.raises(QuantityDerivationError):
        remaining_quantity(bad, "0")


@pytest.mark.parametrize("bad", [None, True, False, object(), [], {}])
def test_non_numeric_types_are_rejected(bad):
    with pytest.raises(QuantityDerivationError):
        remaining_quantity(bad, "0")


def test_negative_submitted_quantity_is_rejected():
    with pytest.raises(QuantityDerivationError):
        remaining_quantity("-1", "0")


def test_negative_cumulative_quantity_is_rejected():
    with pytest.raises(QuantityDerivationError):
        remaining_quantity("10", "-1")


def test_cumulative_greater_than_submitted_is_rejected():
    with pytest.raises(QuantityDerivationError):
        remaining_quantity("10", "11")


def test_impossible_fill_is_never_clamped():
    """An over-fill must raise, never silently become zero."""
    with pytest.raises(QuantityDerivationError) as excinfo:
        derive_quantities("5", "9")
    assert "refusing to clamp" in str(excinfo.value).lower()


def test_fill_state_flags():
    unfilled = derive_quantities("10", "0")
    assert unfilled.is_fully_filled is False
    assert unfilled.is_partially_filled is False

    partial = derive_quantities("10", "4")
    assert partial.is_partially_filled is True
    assert partial.is_fully_filled is False

    full = derive_quantities("10", "10")
    assert full.is_fully_filled is True
    assert full.is_partially_filled is False


def test_normalized_text_uses_project_decimal_rules():
    text = derive_quantities("10.500", "0.500").as_text()
    assert text == {
        "submitted_quantity": "10.5",
        "cumulative_quantity": "0.5",
        "remaining_quantity": "10",
    }


def test_zero_normalizes_to_plain_zero():
    assert derive_quantities("1.000", "1.000").as_text()[
        "remaining_quantity"
    ] == "0"


# ---------- response-contract boundary ----------

def test_unestablished_contracts_are_recorded():
    assert UNESTABLISHED_RESPONSE_CONTRACTS == {
        "place_equity_order",
        "cancel_equity_order",
    }


@pytest.mark.parametrize(
    "tool", ["place_equity_order", "cancel_equity_order"]
)
def test_require_response_contract_fails_closed(tool):
    with pytest.raises(ResponseContractUnavailableError):
        require_response_contract(tool)


def test_established_read_contract_is_allowed():
    # get_equity_orders WAS observed; it is not on the unavailable list.
    require_response_contract("get_equity_orders")


def test_place_parser_always_fails_closed():
    with pytest.raises(ResponseContractUnavailableError):
        parse_place_response({"anything": "at all"})


def test_cancel_parser_always_fails_closed():
    with pytest.raises(ResponseContractUnavailableError):
        parse_cancel_response({"anything": "at all"})


@pytest.mark.parametrize(
    "invented",
    [
        {"data": {"order": {"id": "x", "state": "queued"}}},
        {"data": {"accepted": True}},
        {"order": {"id": "x"}},
        {"accepted": True},
    ],
)
def test_parsers_reject_even_plausible_shapes(invented):
    """No assumed envelope is ever accepted."""
    with pytest.raises(ResponseContractUnavailableError):
        parse_place_response(invented)
    with pytest.raises(ResponseContractUnavailableError):
        parse_cancel_response(invented)


def test_no_invented_response_fields_in_source():
    from pathlib import Path

    source = (
        Path(__file__).parent.parent / "src" / "live_order_contract.py"
    ).read_text(encoding="utf-8")
    # These must appear only as prose describing what is NOT assumed.
    for invented in ('"accepted"', "'accepted'", '"order"]', "['order']"):
        assert invented not in source


# ---------- protocol-neutral result types ----------

def test_broker_call_outcomes_are_protocol_neutral():
    assert {o.value for o in BrokerCallOutcome} == {
        "confirmed_succeeded",
        "confirmed_failed",
        "outcome_unknown",
    }


def test_ambiguous_result_is_identified():
    ambiguous = BrokerCallResult(
        outcome=BrokerCallOutcome.OUTCOME_UNKNOWN, detail="lost response"
    )
    assert ambiguous.is_ambiguous is True

    confirmed = BrokerCallResult(
        outcome=BrokerCallOutcome.CONFIRMED_SUCCEEDED, detail="ok"
    )
    assert confirmed.is_ambiguous is False


def test_result_carries_no_raw_payload():
    from dataclasses import fields

    names = {f.name for f in fields(BrokerCallResult)}
    assert names == {"outcome", "detail"}
