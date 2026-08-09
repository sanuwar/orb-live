"""Broker-free coverage for the place-response contract across all three roles.

Everything here is a local fake. Nothing reaches a broker, an MCP server, or
the network. These tests pin what the place parser accepts and rejects today,
and prove the structural classifier describes a rejection without ever carrying
part of the response body.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.live_order_contract import (
    ResponseContractViolationError,
    classify_place_response_shape,
    parse_order_status,
    parse_place_response,
)
from src.one_share_commissioning import (
    ALLOWED_PLACE_DIAGNOSTICS,
    CommissioningError,
    State,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    BrokerRejectedError,
    RobinhoodCommissioningAdapter,
)
from tests.execution_safety_test_support import allow_decision
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
)

ROLE_SHAPES = {
    "entry": ("buy", "market", "immediate", "gfd", None),
    "stop": ("sell", "market", "stop", "gtc", "185.00"),
    "exit": ("sell", "market", "immediate", "gfd", None),
}


def order_object(role, *, state="filled", order_id="order-1"):
    side, order_type, trigger, duration, stop_price = ROLE_SHAPES[role]
    return {
        "id": order_id, "instrument_id": "instrument-1", "symbol": "AAPL",
        "side": side, "type": order_type, "state": state, "quantity": "1",
        "cumulative_quantity": "1" if state == "filled" else "0",
        "price": None, "stop_price": stop_price,
        "average_price": "200.00" if state == "filled" else None,
        "fees": "0.00", "dollar_based_amount": None,
        "time_in_force": duration, "market_hours": "regular_hours",
        "trigger": trigger, "placed_agent": "agent",
        "created_at": "2026-07-31T15:00:00Z",
        "last_transaction_at": "2026-07-31T15:00:01Z", "executions": [],
    }


def envelope(role, **kwargs):
    return {"data": {"order": order_object(role, **kwargs)}, "guide": "g"}


# --- the documented envelope is accepted for every write role ---------


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_documented_envelope_is_accepted_for_every_role(role):
    parsed = parse_place_response(envelope(role))
    assert parsed.server_order_id == "order-1"
    assert parsed.symbol.upper() == "AAPL"


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_place_and_status_parsers_agree_on_the_same_order_object(role):
    """The advertised order object is the same shape in both directions.

    If these ever diverge, a placement can be rejected while a read of the
    identical order succeeds -- which is exactly the live symptom.
    """
    order = order_object(role)
    assert parse_order_status(order).state == "filled"
    assert parse_place_response({"data": {"order": order}, "guide": "g"})


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
@pytest.mark.parametrize("state", ["filled", "confirmed", "queued"])
def test_pending_and_filled_states_are_both_accepted(role, state):
    assert parse_place_response(envelope(role, state=state))


# --- rejected envelopes -----------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"data": {"order": None}, "guide": "g"},
        {"data": {}, "guide": "g"},
        {"data": {"order": order_object("entry"), "extra": 1}, "guide": "g"},
        {"data": {"order": order_object("entry")}},
        {"data": {"order": order_object("entry")}, "guide": "g", "x": 1},
        {"data": None, "guide": "g"},
        {"data": [], "guide": "g"},
        {"data": {"order": []}, "guide": "g"},
        {"data": {"accepted": True}, "guide": "g"},
    ],
)
def test_unrecognized_envelopes_are_rejected(payload):
    with pytest.raises(ResponseContractViolationError):
        parse_place_response(payload)


def test_missing_order_identifier_is_rejected():
    order = order_object("entry")
    del order["id"]
    with pytest.raises(ResponseContractViolationError):
        parse_place_response({"data": {"order": order}, "guide": "g"})


def test_transport_success_alone_never_implies_acceptance():
    """An empty-but-well-formed envelope must not read as a placed order."""
    for payload in ({"data": {"order": {}}, "guide": "g"},
                    {"data": {"order": None}, "guide": "g"}):
        with pytest.raises(ResponseContractViolationError):
            parse_place_response(payload)


# --- structural classifier --------------------------------------------


@pytest.mark.parametrize(
    "data,classification",
    [
        ({"order": order_object("entry")}, "order_object_returned"),
        ({"order": None}, "order_null_schema_legal"),
        ({"order": order_object("entry"), "warnings": []}, "unexpected_data_keys"),
        ({"accepted": True}, "order_key_absent"),
        ({}, "order_key_absent"),
        ([], "data_not_an_object"),
        ("text", "data_not_an_object"),
        (None, "envelope_absent"),
    ],
)
def test_classifier_names_the_envelope_layer(data, classification):
    assert classify_place_response_shape(data)["envelope_classification"] == (
        classification
    )


def test_classifier_reports_only_allowlisted_booleans_and_no_body():
    order = order_object("entry", order_id="PRIVATE-ORDER-ID")
    shape = classify_place_response_shape({"order": order, "secret_key": 1})
    rendered = json.dumps(shape)

    assert shape["identifier_field_present"] == "true"
    assert shape["unexpected_data_keys_present"] == "true"
    assert shape["order_object_present"] == "true"
    assert shape["order_value_null"] == "false"
    for leaked in ("PRIVATE-ORDER-ID", "secret_key", "AAPL", "200.00",
                   "instrument-1"):
        assert leaked not in rendered
    for name, value in shape.items():
        assert value in ALLOWED_PLACE_DIAGNOSTICS[f"entry_{name}"]


def test_classifier_never_raises_on_hostile_input():
    for value in (None, 0, "", b"x", [1, 2], {"order": {"id": object()}}):
        assert "envelope_classification" in classify_place_response_shape(value)


# --- adapter carries the shape without carrying the body --------------


class Session:
    def __init__(self, structured=None, error=None, is_error=False):
        self.structured = structured
        self.error = error
        self.is_error = is_error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            structuredContent=self.structured, isError=self.is_error
        )


def place_payload(role):
    side, order_type, _, duration, stop_price = ROLE_SHAPES[role]
    payload = {
        "account_number": "transient", "symbol": "AAPL", "side": side,
        "type": "stop_market" if role == "stop" else "market",
        "quantity": "1", "time_in_force": duration,
        "market_hours": "regular_hours",
    }
    if role == "stop":
        payload["stop_price"] = stop_price
    return payload


def place(session, role):
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    payload = place_payload(role)
    return asyncio.run(adapter.place_equity_order(
        payload, role=role,
        safety_decision=allow_decision(role=role, arguments=payload),
    ))


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_valid_response_returns_a_validated_result_with_one_write(role):
    session = Session({"data": {"order": order_object(role)}, "guide": "g"})
    parsed = place(session, role)

    assert parsed.server_order_id == "order-1"
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_null_order_is_ambiguous_and_carries_the_schema_legal_classification(role):
    session = Session({"data": {"order": None}, "guide": "g"})
    with pytest.raises(AmbiguousWriteError) as caught:
        place(session, role)

    assert caught.value.category.endswith("_PLACE_RESPONSE_MALFORMED")
    assert caught.value.call_returned is True
    shape = caught.value.place_shape
    assert shape["envelope_classification"] == "order_null_schema_legal"
    assert shape["order_value_null"] == "true"
    assert shape["order_object_present"] == "false"
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_explicit_rejection_stays_distinct_from_ambiguity(role):
    session = Session(is_error=True)
    with pytest.raises(BrokerRejectedError):
        place(session, role)


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_transport_failure_records_not_reached_and_never_repeats(role):
    session = Session(error=TimeoutError())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    payload = place_payload(role)
    decision = allow_decision(role=role, arguments=payload)
    with pytest.raises(AmbiguousWriteError) as caught:
        asyncio.run(adapter.place_equity_order(
            payload, role=role, safety_decision=decision
        ))

    assert caught.value.transport_classification == "TimeoutError"
    assert caught.value.response_shape == "not_reached"
    assert caught.value.place_shape == {}
    with pytest.raises(CommissioningError, match="only once"):
        asyncio.run(adapter.place_equity_order(
            payload, role=role, safety_decision=decision
        ))
    assert len(session.calls) == 1


# --- evidence integration ---------------------------------------------


class NullOrderAdapter(FakeAdapter):
    """Placements return a schema-legal null order, as the live runs may have."""

    def __init__(self, failing_role, **kwargs):
        super().__init__(**kwargs)
        self.failing_role = failing_role

    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        if role != self.failing_role:
            return await super().place_equity_order(
                arguments, role=role, safety_decision=safety_decision
            )
        self._record("place_equity_order")
        self.write_roles.append(role)
        raise AmbiguousWriteError(
            f"{role.upper()}_PLACE_RESPONSE_MALFORMED",
            transport_classification="ValueError",
            response_shape="malformed_envelope",
            call_returned=True,
            place_shape=classify_place_response_shape({"order": None}),
        )


@pytest.mark.parametrize("role", ["entry", "stop"])
def test_place_diagnostics_reach_evidence_for_each_role(tmp_path, role):
    instance, adapter, _, _ = make_orchestrator(
        tmp_path, adapter=NullOrderAdapter(role), run_id=f"place-shape-{role}",
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)
    diagnostics = evidence["place_diagnostics"]

    validate_sanitized_evidence(evidence)
    assert diagnostics[f"{role}_envelope_classification"] == (
        "order_null_schema_legal"
    )
    assert diagnostics[f"{role}_order_value_null"] == "true"
    assert adapter.write_roles.count(role) == 1
    assert evidence["write_attempt_counters"][f"{role}_place"] == 1
    for key, value in diagnostics.items():
        assert value in ALLOWED_PLACE_DIAGNOSTICS[key]
    for leaked in ("order-1", "instrument-1", "transient", "raw_response"):
        assert leaked not in rendered


def test_place_diagnostic_keys_and_values_are_strictly_allowlisted(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="place-allowlist")
    machine = instance.machine
    machine.evidence_store.start()
    for key in ("order_id", "account_number", "entry_raw_response", "nope"):
        with pytest.raises(CommissioningError, match="key is invalid"):
            machine.record_place_diagnostic(key, "true")
    for value in ("order-1", "maybe", "", None, 1):
        with pytest.raises(CommissioningError, match="value is invalid"):
            machine.record_place_diagnostic("entry_order_key_present", value)
    for key, allowed in ALLOWED_PLACE_DIAGNOSTICS.items():
        for value in allowed:
            machine.record_place_diagnostic(key, value)


def test_unknown_diagnostic_facts_are_dropped_not_raised(tmp_path):
    """A diagnostic must never fail a run that is already handling ambiguity."""
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="place-drop-unknown")
    instance.machine.evidence_store.start()
    instance._record_place_shape(
        "entry", {"order_key_present": "true", "not_a_fact": "true",
                  "order_value_null": "not_a_value"},
    )
    diagnostics = instance.machine.evidence.place_diagnostics
    assert diagnostics == {"entry_order_key_present": "true"}


def test_valid_placement_records_no_place_diagnostics_and_completes(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(), run_id="place-shape-clean",
    )
    result = asyncio.run(instance.run())
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert result.state is State.COMPLETE
    assert evidence["place_diagnostics"] == {}
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert handoffs == []
