"""Broker-free coverage naming the exact order-object contract violation.

Every fixture is local. Nothing reaches a broker, an MCP server, or the
network. The controlling invariant is that this diagnostic is read-only: it
must agree with the parser on every input and must never change what the
parser accepts.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.live_order_contract import (
    CONTRACT_VIOLATION_KINDS,
    ORDER_REQUIRED_FIELDS,
    PLACE_ORDER_FIELD_ALIASES,
    PLACE_ORDER_FIELD_ALIAS_VALUES,
    PLACE_ORDER_OBJECT_SCOPE,
    ResponseContractViolationError,
    classify_place_order_object_violation,
    parse_order_status,
    parse_place_response,
)
from src.one_share_commissioning import (
    ALLOWED_PLACE_DIAGNOSTICS,
    PROHIBITED_EVIDENCE_TERMS,
    SAFE_EXCEPTION_TYPES,
    CommissioningError,
    State,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    BrokerRejectedError,
    RobinhoodCommissioningAdapter,
)
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
)

ROLE_SHAPES = {
    "entry": ("buy", "market", "immediate", "gfd", None),
    "stop": ("sell", "market", "stop", "gtc", "185.00"),
    "exit": ("sell", "market", "immediate", "gfd", None),
}

# A private sentinel that must never survive into evidence.
PRIVATE_VALUE = "PRIVATE-SENTINEL-VALUE"


def order_object(role="entry", **overrides):
    side, order_type, trigger, duration, stop_price = ROLE_SHAPES[role]
    order = {
        "id": "order-1", "instrument_id": "instrument-1", "symbol": "AAPL",
        "side": side, "type": order_type, "state": "filled", "quantity": "1",
        "cumulative_quantity": "1", "price": None, "stop_price": stop_price,
        "average_price": "200.00", "fees": "0.00", "dollar_based_amount": None,
        "time_in_force": duration, "market_hours": "regular_hours",
        "trigger": trigger, "placed_agent": "agent",
        "created_at": "2026-07-31T15:00:00Z",
        "last_transaction_at": "2026-07-31T15:00:01Z", "executions": [],
    }
    order.update(overrides)
    return order


def violation_of(order):
    return classify_place_order_object_violation({"order": order})


def parser_rejects(order):
    try:
        parse_place_response({"data": {"order": order}, "guide": "g"})
        return False
    except ResponseContractViolationError:
        return True


# --- vocabulary -------------------------------------------------------


def test_alias_vocabulary_covers_every_canonical_field():
    for name in ORDER_REQUIRED_FIELDS:
        assert name in PLACE_ORDER_FIELD_ALIASES
        assert PLACE_ORDER_FIELD_ALIASES[name] in PLACE_ORDER_FIELD_ALIAS_VALUES


def test_no_alias_collides_with_a_prohibited_evidence_term():
    """An alias may be used as a key one day; none may name a private thing."""
    for alias in PLACE_ORDER_FIELD_ALIAS_VALUES:
        for term in PROHIBITED_EVIDENCE_TERMS:
            assert term not in alias.lower(), (alias, term)


def test_stop_price_is_aliased_because_the_canonical_name_is_prohibited():
    assert PLACE_ORDER_FIELD_ALIASES["stop_price"] == "stop_trigger_price"
    assert "stop_price" not in PLACE_ORDER_FIELD_ALIAS_VALUES


def test_contract_violation_error_is_allowlisted_as_an_exception_type():
    assert "ResponseContractViolationError" in SAFE_EXCEPTION_TYPES


def test_violation_kinds_are_the_fixed_documented_set():
    assert CONTRACT_VIOLATION_KINDS == {
        "absent", "null", "wrong_type", "empty_text", "invalid_decimal",
        "invalid_enum", "invalid_format", "unexpected_field",
        "contradictory_fields", "unknown",
    }


# --- every required field, every applicable failure -------------------


@pytest.mark.parametrize("field", sorted(ORDER_REQUIRED_FIELDS))
def test_every_required_field_absence_is_named(field):
    order = {k: v for k, v in order_object().items() if k != field}
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_contract_scope"] == PLACE_ORDER_OBJECT_SCOPE
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == "absent"


NON_NULLABLE = sorted({
    "created_at", "cumulative_quantity", "fees", "id", "instrument_id",
    "market_hours", "placed_agent", "side", "state", "symbol",
    "time_in_force", "trigger", "type",
})


@pytest.mark.parametrize("field", NON_NULLABLE)
def test_every_non_nullable_field_null_is_named(field):
    order = order_object(**{field: None})
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == "null"


TEXT_FIELDS = sorted({
    "created_at", "id", "instrument_id", "market_hours", "placed_agent",
    "side", "state", "symbol", "time_in_force", "trigger", "type",
})


@pytest.mark.parametrize("field", TEXT_FIELDS)
@pytest.mark.parametrize(
    "value,kind", [(123, "wrong_type"), ("", "empty_text"), ("   ", "empty_text")]
)
def test_text_field_type_and_emptiness_are_named(field, value, kind):
    order = order_object(**{field: value})
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == kind


@pytest.mark.parametrize("field", ["cumulative_quantity", "fees"])
@pytest.mark.parametrize(
    "value,kind",
    [(0.5, "wrong_type"), (5, "wrong_type"), ("abc", "invalid_decimal"),
     ("", "empty_text"), (None, "null")],
)
def test_required_decimal_failures_are_named(field, value, kind):
    order = order_object(**{field: value})
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == kind


@pytest.mark.parametrize(
    "field,value,kind",
    [
        ("price", "0.00", "invalid_format"),
        ("price", "abc", "invalid_decimal"),
        ("stop_price", "0.00", "invalid_format"),
        ("average_price", "-1.00", "invalid_format"),
        ("quantity", "abc", "invalid_decimal"),
    ],
)
def test_optional_decimal_failures_are_named(field, value, kind):
    order = order_object(**{field: value})
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == kind


def test_unexpected_field_is_named_without_revealing_its_name():
    order = {**order_object(), PRIVATE_VALUE: 1}
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == "unexpected_field"
    assert result["safe_violation_kind"] == "unexpected_field"
    assert PRIVATE_VALUE not in json.dumps(result)


def test_contradictory_quantities_are_named():
    order = order_object(quantity="1", cumulative_quantity="2")
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_violation_kind"] == "contradictory_fields"


@pytest.mark.parametrize("field", ["dollar_based_amount", "executions"])
def test_nested_object_failures_are_named(field):
    order = order_object(**{field: "not-a-container"})
    result = violation_of(order)

    assert parser_rejects(order)
    assert result["safe_field_alias"] == PLACE_ORDER_FIELD_ALIASES[field]
    assert result["safe_violation_kind"] == "wrong_type"


def test_order_that_is_not_an_object_is_named_structurally():
    result = classify_place_order_object_violation({"order": ["x"]})
    assert result["safe_field_alias"] == "order_object"
    assert result["safe_violation_kind"] == "wrong_type"


# --- read-only guarantee ----------------------------------------------


def test_a_valid_order_object_yields_no_violation_for_every_role():
    for role in ("entry", "stop", "exit"):
        order = order_object(role)
        assert violation_of(order) is None
        assert not parser_rejects(order)
        assert parse_order_status(order)


@pytest.mark.parametrize("envelope", [
    {}, {"order": None}, {"data": 1}, None, [],
])
def test_envelope_level_failures_are_out_of_this_scope(envelope):
    """The envelope layer has its own classifier; this one stays silent."""
    assert classify_place_order_object_violation(envelope) is None


def test_inspector_agrees_with_the_parser_on_every_single_field_mutation():
    """The diagnostic must never claim a violation the parser would accept."""
    base = order_object()
    for field in sorted(ORDER_REQUIRED_FIELDS):
        for value in (None, "", "   ", 7, 0.5, [], {}):
            order = {**base, field: value}
            claimed = violation_of(order) is not None
            assert claimed == parser_rejects(order), (field, value)


def test_inspector_never_raises_on_hostile_input():
    for value in (None, 0, "", b"x", [1], {"order": {"id": object()}},
                  {"order": {}}):
        classify_place_order_object_violation(value)


# --- adapter and evidence integration ---------------------------------


class Session:
    def __init__(self, structured=None, error=None, is_error=False):
        self.structured, self.error, self.is_error = structured, error, is_error
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
    return asyncio.run(adapter.place_equity_order(place_payload(role), role=role))


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_adapter_carries_the_named_violation_for_every_role(role):
    order = order_object(role, fees=None)
    session = Session({"data": {"order": order}, "guide": "g"})

    with pytest.raises(AmbiguousWriteError) as caught:
        place(session, role)

    error = caught.value
    assert error.safe_contract_scope == PLACE_ORDER_OBJECT_SCOPE
    assert error.safe_field_alias == "fees"
    assert error.safe_violation_kind == "null"
    assert error.place_shape["contract_field_alias"] == "fees"
    assert error.place_shape["contract_violation_kind"] == "null"
    assert error.place_shape["envelope_classification"] == "order_object_returned"
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_valid_response_carries_no_violation_and_writes_once(role):
    session = Session({"data": {"order": order_object(role)}, "guide": "g"})
    parsed = place(session, role)

    assert parsed.server_order_id == "order-1"
    assert len(session.calls) == 1


def test_explicit_rejection_and_transport_carry_no_contract_violation():
    with pytest.raises(BrokerRejectedError):
        place(Session(is_error=True), "entry")
    for error in (TimeoutError(), RuntimeError("private text")):
        with pytest.raises(AmbiguousWriteError) as caught:
            place(Session(error=error), "entry")
        assert caught.value.safe_field_alias is None
        assert caught.value.place_shape == {}


class NamedViolationAdapter(FakeAdapter):
    """A placement whose order object is missing one required field."""

    def __init__(self, failing_role, **kwargs):
        super().__init__(**kwargs)
        self.failing_role = failing_role

    async def place_equity_order(self, arguments, *, role):
        if role != self.failing_role:
            return await super().place_equity_order(arguments, role=role)
        self._record("place_equity_order")
        self.write_roles.append(role)
        order = order_object(role, fees=None)
        from src.live_order_contract import classify_place_response_shape
        shape = dict(classify_place_response_shape({"order": order}))
        violation = classify_place_order_object_violation({"order": order})
        shape["contract_scope"] = violation["safe_contract_scope"]
        shape["contract_field_alias"] = violation["safe_field_alias"]
        shape["contract_violation_kind"] = violation["safe_violation_kind"]
        raise AmbiguousWriteError(
            f"{role.upper()}_PLACE_RESPONSE_MALFORMED",
            transport_classification="ResponseContractViolationError",
            response_shape="malformed_envelope",
            call_returned=True,
            place_shape=shape,
        )


@pytest.mark.parametrize("role", ["entry", "stop"])
def test_named_violation_reaches_evidence_without_any_private_value(tmp_path, role):
    instance, adapter, _, _ = make_orchestrator(
        tmp_path, adapter=NamedViolationAdapter(role),
        run_id=f"contract-violation-{role}",
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)
    diagnostics = evidence["place_diagnostics"]

    validate_sanitized_evidence(evidence)
    assert diagnostics[f"{role}_contract_scope"] == PLACE_ORDER_OBJECT_SCOPE
    assert diagnostics[f"{role}_contract_field_alias"] == "fees"
    assert diagnostics[f"{role}_contract_violation_kind"] == "null"
    assert evidence["write_attempt_counters"][f"{role}_place"] == 1
    assert adapter.write_roles.count(role) == 1
    for key, value in diagnostics.items():
        assert value in ALLOWED_PLACE_DIAGNOSTICS[key]
    for leaked in ("order-1", "instrument-1", "transient", "200.00",
                   "PRIVATE", "agent", "raw_response"):
        assert leaked not in rendered


def test_valid_run_records_no_contract_diagnostic_and_completes(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=FakeAdapter(), run_id="contract-violation-clean",
    )
    result = asyncio.run(instance.run())
    evidence = json.loads(result.evidence_path.read_text(encoding="utf-8"))

    assert result.state is State.COMPLETE
    assert evidence["place_diagnostics"] == {}
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert handoffs == []


def test_unknown_or_unavailable_facts_are_dropped_not_invented(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="contract-drop")
    instance.machine.evidence_store.start()
    instance._record_place_shape(
        "entry",
        {"contract_field_alias": "fees", "contract_violation_kind": "not_a_kind",
         "contract_scope": "not_a_scope", "invented_fact": "true"},
    )
    assert instance.machine.evidence.place_diagnostics == {
        "entry_contract_field_alias": "fees"
    }


def test_diagnostic_values_are_rejected_outside_the_allowlist(tmp_path):
    instance, _, _, _ = make_orchestrator(tmp_path, run_id="contract-allowlist")
    machine = instance.machine
    machine.evidence_store.start()
    for value in ("stop_price", PRIVATE_VALUE, "", None, 1):
        with pytest.raises(CommissioningError, match="value is invalid"):
            machine.record_place_diagnostic("entry_contract_field_alias", value)
    for key, allowed in ALLOWED_PLACE_DIAGNOSTICS.items():
        for value in allowed:
            machine.record_place_diagnostic(key, value)
