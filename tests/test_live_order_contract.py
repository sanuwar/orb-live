"""
Offline tests for the live order-contract boundary.

Covers decimal-safe remaining-quantity derivation and the strict
place/cancel response parsers grounded in the official output schemas.

No broker access occurs. Every fixture value is INVENTED: no historical
scalar value from any real order, account, or symbol appears here. The
placeholder symbol is deliberately not a tradable ticker.
"""
from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from decimal import Decimal

import pytest

from src.live_order_contract import (
    ABSENT_ORDER_FIELDS,
    ADVERTISED_ORDER_INPUT_FILTER_STATES,
    ADVERTISED_ORDER_OUTPUT_STATES,
    CHECKPOINT_7B_OBSERVED_STATES,
    INPUT_FILTER_ONLY_ORDER_STATES,
    OBSERVED_ORDER_FIELDS,
    ORDER_ALLOWED_FIELDS,
    ORDER_OPTIONAL_FIELDS,
    ORDER_REQUIRED_FIELDS,
    OUTPUT_ONLY_ORDER_STATES,
    REF_ID_FINDINGS,
    SCHEMA_SNAPSHOT_DATE,
    UNESTABLISHED_RESPONSE_CONTRACTS,
    BrokerCallOutcome,
    BrokerCallResult,
    CancelAcceptance,
    ObservedOrderField,
    ParsedCancelResponse,
    ParsedPlaceResponse,
    QuantityDerivationError,
    ResponseContractUnavailableError,
    ResponseContractViolationError,
    derive_quantities,
    parse_cancel_response,
    parse_order_status,
    parse_place_response,
    remaining_quantity,
    require_response_contract,
)


# --------------------------------------------------------------------------
# Invented fixtures. Nothing here reproduces a real order.
# --------------------------------------------------------------------------

FIXTURE_SYMBOL = "ZZTEST"
FIXTURE_ORDER_ID = "fixture-order-0000-0000"
FIXTURE_INSTRUMENT_ID = "fixture-instrument-0000-0000"
FIXTURE_CREATED_AT = "2020-01-02T15:30:00Z"
FIXTURE_TRANSACTION_AT = "2020-01-02T15:31:00Z"


def make_order(**overrides) -> dict:
    """A schema-complete equity order object with invented values."""
    order = {
        "id": FIXTURE_ORDER_ID,
        "instrument_id": FIXTURE_INSTRUMENT_ID,
        "symbol": FIXTURE_SYMBOL,
        "side": "buy",
        "type": "limit",
        "state": "queued",
        "quantity": "4",
        "cumulative_quantity": "0",
        "price": "11.50",
        "stop_price": None,
        "average_price": None,
        "fees": "0.00",
        "dollar_based_amount": None,
        "time_in_force": "gfd",
        "market_hours": "regular_hours",
        "trigger": "immediate",
        "placed_agent": "agentic",
        "created_at": FIXTURE_CREATED_AT,
        "last_transaction_at": None,
        "executions": None,
    }
    order.update(overrides)
    return order


def make_place_response(**overrides) -> dict:
    return {
        "data": {"order": make_order(**overrides)},
        "guide": "Invented placeholder guide text.",
    }


def make_cancel_response(accepted) -> dict:
    return {
        "data": {"accepted": accepted},
        "guide": "Invented placeholder guide text.",
    }


def test_order_status_parser_is_independent_of_place_entry_point(monkeypatch):
    def prohibited_place_parser(_response):
        raise AssertionError("place response parser was invoked")

    monkeypatch.setattr(
        "src.live_order_contract.parse_place_response",
        prohibited_place_parser,
    )
    parsed = parse_order_status(make_order())
    assert parsed.state == "queued"
    assert parsed.remaining_quantity == Decimal("4")
    assert not hasattr(parsed, "establishes_submission_only")

# ---------- observed field names vs. advertised field names ----------

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


def test_observed_fields_are_a_subset_of_the_advertised_fields():
    """
    7B probed a fixed candidate list; the schema advertises more.

    A larger advertised set is not a contradiction of the 7B observation,
    and neither record is discarded in favour of the other.
    """
    assert OBSERVED_ORDER_FIELDS < ORDER_REQUIRED_FIELDS


def test_ref_id_and_remaining_quantity_are_absent_on_both_evidence_lines():
    assert ABSENT_ORDER_FIELDS == {"ref_id", "remaining_quantity"}
    # Observed absent during 7B ...
    assert not (ABSENT_ORDER_FIELDS & OBSERVED_ORDER_FIELDS)
    # ... and independently not advertised by the output schema.
    assert not (ABSENT_ORDER_FIELDS & ORDER_ALLOWED_FIELDS)


def test_observed_field_enum_is_consistent():
    assert {f.value for f in ObservedOrderField} == OBSERVED_ORDER_FIELDS


def test_advertised_order_fields_match_the_schema():
    assert len(ORDER_REQUIRED_FIELDS) == 20
    assert ORDER_OPTIONAL_FIELDS == {"reject_reason"}
    assert ORDER_ALLOWED_FIELDS == ORDER_REQUIRED_FIELDS | {"reject_reason"}


def test_schema_snapshot_provenance_is_recorded():
    assert SCHEMA_SNAPSHOT_DATE == "2026-07-17"


# ---------- lifecycle-state record correction ----------

def test_output_lifecycle_states_include_the_cancellation_states():
    """
    The earlier record wrongly concluded these were not equity states.

    That conclusion came from the INPUT state filter. The OUTPUT state
    description advertises both.
    """
    assert "pending_cancelled" in ADVERTISED_ORDER_OUTPUT_STATES
    assert "partially_filled_rest_cancelled" in ADVERTISED_ORDER_OUTPUT_STATES


def test_output_state_set_is_exactly_the_advertised_set():
    assert ADVERTISED_ORDER_OUTPUT_STATES == {
        "new",
        "queued",
        "unconfirmed",
        "partially_filled",
        "filled",
        "cancelled",
        "rejected",
        "failed",
        "voided",
        "pending_cancelled",
        "partially_filled_rest_cancelled",
        "locating",
        "locate_failed",
    }


def test_input_filter_states_are_recorded_separately():
    assert "pending_cancelled" not in ADVERTISED_ORDER_INPUT_FILTER_STATES
    assert (
        "partially_filled_rest_cancelled"
        not in ADVERTISED_ORDER_INPUT_FILTER_STATES
    )


def test_the_two_state_lists_differ_in_both_directions():
    """Neither list is silently preferred over the other."""
    assert OUTPUT_ONLY_ORDER_STATES == {
        "pending_cancelled",
        "partially_filled_rest_cancelled",
        "locating",
        "locate_failed",
    }
    # `confirmed` is filterable on input but is not an advertised output
    # state. The asymmetry runs both ways.
    assert INPUT_FILTER_ONLY_ORDER_STATES == {"confirmed"}


def test_checkpoint_7b_observed_only_two_states():
    assert CHECKPOINT_7B_OBSERVED_STATES == {"filled", "cancelled"}
    assert CHECKPOINT_7B_OBSERVED_STATES < ADVERTISED_ORDER_OUTPUT_STATES


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


# ---------- the fail-closed chokepoint ----------

def test_no_contract_remains_unestablished():
    """Both write-tool contracts are now grounded in the official schema."""
    assert UNESTABLISHED_RESPONSE_CONTRACTS == frozenset()


@pytest.mark.parametrize(
    "tool",
    [
        "place_equity_order",
        "cancel_equity_order",
        "get_equity_orders",
        "get_equity_positions",
    ],
)
def test_grounded_contracts_are_allowed(tool):
    require_response_contract(tool)


def test_chokepoint_still_fails_closed_for_an_unknown_contract():
    """The mechanism survives so a future unknown tool still fails closed."""
    import src.live_order_contract as module

    original = module.UNESTABLISHED_RESPONSE_CONTRACTS
    module.UNESTABLISHED_RESPONSE_CONTRACTS = frozenset({"future_tool"})
    try:
        with pytest.raises(ResponseContractUnavailableError):
            module.require_response_contract("future_tool")
    finally:
        module.UNESTABLISHED_RESPONSE_CONTRACTS = original


# ---------- place: the exact advertised envelope ----------

def test_exact_place_envelope_is_accepted():
    parsed = parse_place_response(make_place_response())

    assert isinstance(parsed, ParsedPlaceResponse)
    assert parsed.server_order_id == FIXTURE_ORDER_ID
    assert parsed.state == "queued"
    assert parsed.symbol == FIXTURE_SYMBOL
    assert parsed.side == "buy"
    assert parsed.order_type == "limit"
    assert parsed.trigger == "immediate"
    assert parsed.time_in_force == "gfd"
    assert parsed.market_hours == "regular_hours"


def test_place_result_is_immutable():
    parsed = parse_place_response(make_place_response())
    with pytest.raises(FrozenInstanceError):
        parsed.state = "filled"  # type: ignore[misc]


def test_place_result_carries_no_raw_payload_or_prose():
    names = {f.name for f in fields(ParsedPlaceResponse)}
    for forbidden in (
        "guide",
        "raw",
        "payload",
        "response",
        "reject_reason",
        "executions",
        "instrument_id",
        "placed_agent",
        "dollar_based_amount",
        "fees",
    ):
        assert forbidden not in names


@pytest.mark.parametrize(
    "response",
    [
        {"data": {"order": None}, "guide": "g"},
        {"data": {}, "guide": "g"},
        {"data": {"order": {}}, "guide": "g"},
        {"guide": "g"},
        {"data": {"order": {}}},
        {"data": {"order": {}}, "guide": "g", "extra": 1},
        {"data": {"order": {}, "extra": 1}, "guide": "g"},
        {"order": {}},
        {"accepted": True},
        {},
    ],
)
def test_malformed_place_envelopes_are_rejected(response):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(response)


@pytest.mark.parametrize(
    "response", [None, "text", 7, [], (), True, object()]
)
def test_place_requires_a_mapping(response):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(response)


@pytest.mark.parametrize("guide", [None, 7, "", "   ", True, []])
def test_place_guide_must_be_a_real_string(guide):
    response = make_place_response()
    response["guide"] = guide
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(response)


# ---------- place: every required field ----------

@pytest.mark.parametrize("field_name", sorted(ORDER_REQUIRED_FIELDS))
def test_each_missing_required_place_field_is_rejected(field_name):
    order = make_order()
    del order[field_name]

    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response({"data": {"order": order}, "guide": "g"})

    assert field_name in str(excinfo.value)


@pytest.mark.parametrize(
    "field_name",
    sorted(
        {
            "id",
            "instrument_id",
            "symbol",
            "side",
            "type",
            "state",
            "cumulative_quantity",
            "fees",
            "time_in_force",
            "market_hours",
            "trigger",
            "placed_agent",
            "created_at",
        }
    ),
)
def test_null_non_nullable_place_fields_are_rejected(field_name):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(**{field_name: None}))


@pytest.mark.parametrize(
    "field_name",
    sorted(
        {
            "id",
            "instrument_id",
            "symbol",
            "side",
            "type",
            "state",
            "time_in_force",
            "market_hours",
            "trigger",
            "placed_agent",
            "created_at",
        }
    ),
)
@pytest.mark.parametrize("bad", [7, 7.5, True, [], {}, ""])
def test_wrong_typed_place_text_fields_are_rejected(field_name, bad):
    if field_name == "symbol" and bad == "":
        # A CREATION response may carry the symbol blank; the broker
        # populates it by the time the order is read back. Proven live on
        # 2026-07-31. Presence, non-nullness, and the string type are still
        # enforced, and parse_order_status still rejects a blank symbol --
        # both asserted in tests/test_place_response_blank_symbol.py.
        assert parse_place_response(make_place_response(symbol="")).symbol == ""
        return
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(**{field_name: bad}))


@pytest.mark.parametrize(
    "field_name",
    ["quantity", "cumulative_quantity", "price", "stop_price",
     "average_price", "fees"],
)
@pytest.mark.parametrize("bad", ["abc", "", "   ", "1.2.3", "NaN", "1,5"])
def test_malformed_place_numeric_strings_are_rejected(field_name, bad):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(**{field_name: bad}))


def test_unexpected_place_field_is_rejected():
    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response(make_place_response(surprise="value"))

    assert "surprise" in str(excinfo.value)


def test_reject_reason_is_the_only_permitted_optional_field():
    parsed = parse_place_response(
        make_place_response(
            state="rejected", reject_reason="invented placeholder reason"
        )
    )
    assert parsed.state == "rejected"


# ---------- place: numbers are Decimal, never float ----------

@pytest.mark.parametrize(
    "field_name",
    ["quantity", "cumulative_quantity", "price", "stop_price",
     "average_price", "fees"],
)
def test_python_floats_are_rejected_in_place_numeric_fields(field_name):
    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response(make_place_response(**{field_name: 1.5}))

    assert "float" in str(excinfo.value).lower()


@pytest.mark.parametrize(
    "field_name", ["quantity", "cumulative_quantity", "price", "fees"]
)
@pytest.mark.parametrize("bad", [4, Decimal("4")])
def test_non_string_numerics_are_rejected(field_name, bad):
    """The schema advertises decimal STRINGS; a bare number is a violation."""
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(**{field_name: bad}))


def test_parsed_numbers_are_exact_decimals():
    parsed = parse_place_response(
        make_place_response(
            quantity="0.3",
            cumulative_quantity="0.1",
            price="11.10",
        )
    )
    assert isinstance(parsed.submitted_quantity, Decimal)
    assert isinstance(parsed.cumulative_quantity, Decimal)
    assert isinstance(parsed.limit_price, Decimal)
    assert parsed.remaining_quantity == Decimal("0.2")
    assert str(parsed.remaining_quantity) == "0.2"


def test_impossible_place_fill_state_is_rejected():
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(
            make_place_response(quantity="1", cumulative_quantity="2")
        )


@pytest.mark.parametrize("field_name", ["price", "stop_price",
                                        "average_price"])
@pytest.mark.parametrize("bad", ["0", "-1.00"])
def test_non_positive_place_prices_are_rejected(field_name, bad):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(**{field_name: bad}))


# ---------- place: lifecycle fixtures ----------

def test_submitted_but_unfilled_response():
    parsed = parse_place_response(
        make_place_response(
            state="queued", quantity="4", cumulative_quantity="0"
        )
    )
    assert parsed.state == "queued"
    assert parsed.cumulative_quantity == Decimal("0")
    assert parsed.remaining_quantity == Decimal("4")
    assert parsed.average_fill_price is None
    assert parsed.has_any_reported_fill is False
    assert parsed.establishes_submission_only is True


def test_partially_filled_response():
    parsed = parse_place_response(
        make_place_response(
            state="partially_filled",
            quantity="4",
            cumulative_quantity="1",
            average_price="11.25",
            last_transaction_at=FIXTURE_TRANSACTION_AT,
        )
    )
    assert parsed.state == "partially_filled"
    assert parsed.remaining_quantity == Decimal("3")
    assert parsed.average_fill_price == Decimal("11.25")
    assert parsed.last_transaction_at == FIXTURE_TRANSACTION_AT
    assert parsed.last_transaction_at_utc is not None


def test_filled_response_still_only_establishes_submission():
    """A filled state in a SUBMISSION response is not a verified fill."""
    parsed = parse_place_response(
        make_place_response(
            state="filled",
            quantity="4",
            cumulative_quantity="4",
            average_price="11.40",
        )
    )
    assert parsed.state == "filled"
    assert parsed.remaining_quantity == Decimal("0")
    assert parsed.establishes_submission_only is True


def test_rejected_response():
    parsed = parse_place_response(
        make_place_response(
            state="rejected",
            cumulative_quantity="0",
            reject_reason="invented placeholder reason",
        )
    )
    assert parsed.state == "rejected"
    assert parsed.establishes_submission_only is True


def test_pending_cancel_response():
    parsed = parse_place_response(make_place_response(state="pending_cancelled"))
    assert parsed.state == "pending_cancelled"


def test_partial_fill_rest_cancelled_response():
    parsed = parse_place_response(
        make_place_response(
            state="partially_filled_rest_cancelled",
            quantity="4",
            cumulative_quantity="1",
            average_price="11.30",
        )
    )
    assert parsed.state == "partially_filled_rest_cancelled"
    assert parsed.cumulative_quantity == Decimal("1")
    assert parsed.remaining_quantity == Decimal("3")


@pytest.mark.parametrize("state", sorted(ADVERTISED_ORDER_OUTPUT_STATES))
def test_every_advertised_state_parses_and_is_preserved_verbatim(state):
    parsed = parse_place_response(make_place_response(state=state))
    assert parsed.state == state


def test_place_response_never_reports_a_verified_fill_attribute():
    names = {f.name for f in fields(ParsedPlaceResponse)}
    for forbidden in ("is_filled", "filled", "succeeded", "cancelled",
                      "protected", "terminal"):
        assert forbidden not in names


@pytest.mark.parametrize(
    "state",
    ["filled", "cancelled", "rejected", "queued",
     "partially_filled_rest_cancelled"],
)
def test_submission_only_flag_is_true_for_every_state(state):
    quantities = (
        {"quantity": "4", "cumulative_quantity": "4", "average_price": "11.40"}
        if state == "filled"
        else {"quantity": "4", "cumulative_quantity": "0"}
    )
    if state == "partially_filled_rest_cancelled":
        quantities = {"quantity": "4", "cumulative_quantity": "1",
                      "average_price": "11.30"}

    parsed = parse_place_response(
        make_place_response(state=state, **quantities)
    )
    assert parsed.establishes_submission_only is True


# ---------- place: dollar-based and nested objects ----------

def test_dollar_based_order_without_quantity_has_no_derivable_remainder():
    parsed = parse_place_response(
        make_place_response(
            type="market",
            price=None,
            quantity=None,
            cumulative_quantity="0",
            dollar_based_amount={"amount": "100.00", "currency_code": "USD"},
        )
    )
    assert parsed.submitted_quantity is None
    # Never guessed, never defaulted to zero.
    assert parsed.remaining_quantity is None


@pytest.mark.parametrize(
    "amount",
    [
        {"amount": "100.00"},
        {"currency_code": "USD"},
        {"amount": "100.00", "currency_code": "USD", "extra": 1},
        {"amount": 100.0, "currency_code": "USD"},
        {"amount": "abc", "currency_code": "USD"},
        "100.00",
        7,
    ],
)
def test_malformed_dollar_based_amount_is_rejected(amount):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(dollar_based_amount=amount))


def test_valid_executions_are_accepted_and_counted():
    parsed = parse_place_response(
        make_place_response(
            state="filled",
            quantity="4",
            cumulative_quantity="4",
            average_price="11.40",
            executions=[
                {
                    "id": "fixture-execution-0001",
                    "price": "11.40",
                    "quantity": "4",
                    "timestamp": FIXTURE_TRANSACTION_AT,
                    "fees": "0.00",
                }
            ],
        )
    )
    assert parsed.execution_count == 1


@pytest.mark.parametrize(
    "executions",
    [
        "not-an-array",
        7,
        [{"id": "x", "price": "1.00", "quantity": "1"}],
        [{"id": "x", "price": "1.00", "quantity": "1",
          "timestamp": "t", "fees": "0.00", "extra": 1}],
        [{"id": "x", "price": 1.0, "quantity": "1",
          "timestamp": "t", "fees": "0.00"}],
        [None],
    ],
)
def test_malformed_executions_are_rejected(executions):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(make_place_response(executions=executions))


# ---------- place: ref_id ----------

def test_place_response_without_ref_id_is_accepted():
    """The advertised order object has no ref_id; its absence is correct."""
    response = make_place_response()
    assert "ref_id" not in response["data"]["order"]
    parse_place_response(response)


def test_returned_ref_id_is_rejected_because_the_schema_forbids_it():
    """additionalProperties=false makes a returned ref_id a violation."""
    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response(
            make_place_response(ref_id="fixture-ref-0000-0000")
        )

    assert "ref_id" in str(excinfo.value)


def test_parsed_place_result_exposes_no_ref_id():
    names = {f.name for f in fields(ParsedPlaceResponse)}
    assert "ref_id" not in names


def test_ref_id_findings_are_recorded():
    assert REF_ID_FINDINGS.place_input_accepts_ref_id is True
    assert REF_ID_FINDINGS.place_output_advertises_ref_id is False
    assert REF_ID_FINDINGS.order_status_output_advertises_ref_id is False
    assert REF_ID_FINDINGS.checkpoint_7b_records_sampled == 13
    assert REF_ID_FINDINGS.checkpoint_7b_ref_id_observed is False
    assert REF_ID_FINDINGS.lookup_by_ref_supported is False
    assert REF_ID_FINDINGS.same_ref_is_local_payload_integrity_only is True
    assert REF_ID_FINDINGS.automatic_retry_authorized is False


def test_no_ref_lookup_helper_exists():
    """An order can never be rediscovered by ref_id."""
    import src.live_order_contract as module

    for name in dir(module):
        lowered = name.lower()
        assert not (lowered.startswith("lookup") and "ref" in lowered)


# ---------- cancel ----------

def test_exact_cancel_accepted_true():
    parsed = parse_cancel_response(make_cancel_response(True))

    assert isinstance(parsed, ParsedCancelResponse)
    assert parsed.acceptance is CancelAcceptance.CANCEL_REQUEST_ACCEPTED_ASYNC
    assert parsed.request_was_accepted is True
    assert parsed.order_status_reconciliation_required is True


def test_exact_cancel_accepted_false():
    parsed = parse_cancel_response(make_cancel_response(False))

    assert parsed.acceptance is CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED
    assert parsed.request_was_accepted is False
    assert parsed.order_status_reconciliation_required is True


@pytest.mark.parametrize(
    "accepted", [1, 0, "true", "false", "True", "yes", None, [], {}, 1.0]
)
def test_non_boolean_accepted_values_are_rejected(accepted):
    """No truthiness coercion may manufacture a cancellation conclusion."""
    with pytest.raises(ResponseContractViolationError):
        parse_cancel_response(make_cancel_response(accepted))


@pytest.mark.parametrize(
    "response",
    [
        {"data": {}, "guide": "g"},
        {"data": {"accepted": True}},
        {"guide": "g"},
        {"data": {"accepted": True}, "guide": "g", "extra": 1},
        {"data": {"accepted": True, "extra": 1}, "guide": "g"},
        {"data": {"order": {}}, "guide": "g"},
        {"accepted": True},
        {},
        None,
        "text",
        7,
        [],
    ],
)
def test_malformed_cancel_envelopes_are_rejected(response):
    with pytest.raises(ResponseContractViolationError):
        parse_cancel_response(response)


@pytest.mark.parametrize("guide", [None, 7, "", "   ", True])
def test_cancel_guide_must_be_a_real_string(guide):
    with pytest.raises(ResponseContractViolationError):
        parse_cancel_response({"data": {"accepted": True}, "guide": guide})


def test_accepted_true_never_maps_to_a_completed_cancellation():
    """
    The result type must expose no attribute that reads as CANCELLED.

    Acceptance is asynchronous; completion requires an order-status read.
    """
    parsed = parse_cancel_response(make_cancel_response(True))
    names = {f.name for f in fields(ParsedCancelResponse)}

    for forbidden in (
        "cancelled",
        "canceled",
        "is_cancelled",
        "completed",
        "cancellation_complete",
        "terminal",
        "succeeded",
    ):
        assert forbidden not in names
        assert not hasattr(parsed, forbidden)


def test_neither_cancel_result_is_labelled_cancelled():
    """Both outcomes describe the REQUEST; neither names the order state."""
    for member in CancelAcceptance:
        assert "CANCELLED" not in member.value
        assert member.value.startswith("CANCEL_REQUEST_")


def test_cancel_acceptance_values_are_exactly_the_two_permitted():
    assert {member.value for member in CancelAcceptance} == {
        "CANCEL_REQUEST_ACCEPTED_ASYNC",
        "CANCEL_REQUEST_NOT_ACCEPTED",
    }


def test_cancel_result_is_immutable():
    parsed = parse_cancel_response(make_cancel_response(True))
    with pytest.raises(FrozenInstanceError):
        parsed.acceptance = (  # type: ignore[misc]
            CancelAcceptance.CANCEL_REQUEST_NOT_ACCEPTED
        )


def test_cancel_result_carries_no_guide_text():
    names = {f.name for f in fields(ParsedCancelResponse)}
    assert names == {"acceptance", "order_status_reconciliation_required"}


def test_reconciliation_is_documented_as_mandatory():
    import src.live_order_contract as module

    doc = (module.parse_cancel_response.__doc__ or "").upper()
    assert "MANDATORY" in doc
    assert "GET_EQUITY_ORDERS" in doc


# ---------- errors never leak values ----------

def test_place_errors_never_echo_guide_text_or_reject_reasons():
    secret_guide = "SENTINELGUIDETEXT"
    secret_reason = "SENTINELREJECTREASON"

    response = make_place_response(
        reject_reason=secret_reason, quantity="not-a-number"
    )
    response["guide"] = secret_guide

    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response(response)

    message = str(excinfo.value)
    assert secret_guide not in message
    assert secret_reason not in message


def test_place_errors_never_echo_the_offending_value():
    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_place_response(make_place_response(quantity="SENTINELVALUE"))

    assert "SENTINELVALUE" not in str(excinfo.value)


def test_cancel_errors_never_echo_guide_text():
    with pytest.raises(ResponseContractViolationError) as excinfo:
        parse_cancel_response(
            {"data": {"accepted": "yes"}, "guide": "SENTINELGUIDETEXT"}
        )

    assert "SENTINELGUIDETEXT" not in str(excinfo.value)


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
    names = {f.name for f in fields(BrokerCallResult)}
    assert names == {"outcome", "detail"}
