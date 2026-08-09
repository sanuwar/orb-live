"""Broker-free coverage for the creation-time blank symbol repair.

Proven live on 2026-07-31 (run …-06): entry, stop, and exit each reported
place_order_object / symbol / empty_text while the same orders read back with
the symbol populated. Everything here is a local fake; nothing reaches a
broker, an MCP server, or the network.

The controlling distinction under test is three-way:
  blank symbol  -> identifies nothing, reconcilable
  wrong symbol  -> identifies another instrument, terminal
  right symbol  -> direct accepted path
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest

from src.live_order_contract import (
    ResponseContractViolationError,
    classify_place_order_object_violation,
    parse_order_status,
    parse_place_response,
)
from src.one_share_commissioning import (
    ALLOWED_FAILURE_CATEGORIES,
    CommissioningError,
    State,
    validate_sanitized_evidence,
)
from src.robinhood_commissioning_adapter import (
    AmbiguousWriteError,
    PlacementSymbolMismatchError,
    RobinhoodCommissioningAdapter,
)
from tests.execution_safety_test_support import allow_decision
from tests.test_one_share_commissioning_orchestrator import (
    FakeAdapter,
    make_orchestrator,
    order,
)

ROLES = ("entry", "stop", "exit")
ROLE_SHAPES = {
    "entry": ("buy", "market", "immediate", "gfd", None),
    "stop": ("sell", "market", "stop", "gtc", "185.00"),
    "exit": ("sell", "market", "immediate", "gfd", None),
}
BLANKS = ("", "   ", "\t", "\n")


def order_object(role="entry", symbol="AAPL", **overrides):
    side, order_type, trigger, duration, stop_price = ROLE_SHAPES[role]
    payload = {
        "id": "order-1", "instrument_id": "instrument-1", "symbol": symbol,
        "side": side, "type": order_type, "state": "filled", "quantity": "1",
        "cumulative_quantity": "1", "price": None, "stop_price": stop_price,
        "average_price": "200.00", "fees": "0.00", "dollar_based_amount": None,
        "time_in_force": duration, "market_hours": "regular_hours",
        "trigger": trigger, "placed_agent": "agent",
        "created_at": "2026-07-31T15:00:00Z",
        "last_transaction_at": "2026-07-31T15:00:01Z", "executions": [],
    }
    payload.update(overrides)
    return payload


def place_envelope(role="entry", symbol="AAPL", **overrides):
    return {"data": {"order": order_object(role, symbol, **overrides)},
            "guide": "g"}


# --- creation parser --------------------------------------------------


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("blank", BLANKS)
def test_creation_parser_accepts_a_blank_symbol(role, blank):
    parsed = parse_place_response(place_envelope(role, blank))
    # Carried through exactly as sent. Never replaced with the request.
    assert parsed.symbol == blank
    assert parsed.symbol.upper() != "AAPL"


@pytest.mark.parametrize("role", ROLES)
def test_creation_parser_accepts_a_matching_symbol(role):
    assert parse_place_response(place_envelope(role, "AAPL")).symbol == "AAPL"


@pytest.mark.parametrize(
    "symbol,kind", [(None, "null"), (123, "wrong_type"), ([], "wrong_type"),
                    (True, "null")],
)
def test_creation_parser_still_rejects_missing_null_and_non_string(symbol, kind):
    with pytest.raises(ResponseContractViolationError) as caught:
        parse_place_response(place_envelope("entry", symbol))
    assert caught.value.safe_field_alias == "symbol"
    assert caught.value.safe_violation_kind == kind


def test_creation_parser_still_rejects_an_absent_symbol():
    order_without = {k: v for k, v in order_object().items() if k != "symbol"}
    with pytest.raises(ResponseContractViolationError):
        parse_place_response({"data": {"order": order_without}, "guide": "g"})


@pytest.mark.parametrize("field", ["fees", "state", "placed_agent", "id"])
def test_no_other_field_became_lenient(field):
    """Only symbol was relaxed; every other text field stays strict."""
    for bad in ("", "   "):
        with pytest.raises(ResponseContractViolationError):
            parse_place_response(place_envelope("entry", "AAPL", **{field: bad}))


# --- status parser stays strict ---------------------------------------


@pytest.mark.parametrize("blank", BLANKS)
def test_status_parser_still_rejects_a_blank_symbol(blank):
    with pytest.raises(ResponseContractViolationError):
        parse_order_status(order_object("entry", blank))


def test_status_parser_still_accepts_a_populated_symbol():
    assert parse_order_status(order_object("entry", "AAPL")).state == "filled"


# --- field-violation inspector no longer accuses symbol ---------------


@pytest.mark.parametrize("blank", BLANKS)
def test_inspector_no_longer_reports_symbol_empty_text(blank):
    assert classify_place_order_object_violation(
        {"order": order_object("entry", blank)}
    ) is None


def test_inspector_still_reports_a_genuine_symbol_violation():
    result = classify_place_order_object_violation(
        {"order": order_object("entry", None)}
    )
    assert result["safe_field_alias"] == "symbol"
    assert result["safe_violation_kind"] == "null"


# --- adapter: the three-way distinction -------------------------------


class Session:
    def __init__(self, structured):
        self.structured = structured
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        return SimpleNamespace(structuredContent=self.structured, isError=False)


def request_payload(role):
    side, order_type, _, duration, stop_price = ROLE_SHAPES[role]
    payload = {
        "account_number": "transient", "symbol": "AAPL", "side": side,
        "type": "stop_market" if role == "stop" else "market", "quantity": "1",
        "time_in_force": duration, "market_hours": "regular_hours",
    }
    if role == "stop":
        payload["stop_price"] = stop_price
    return payload


def place(session, role):
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    payload = request_payload(role)
    return asyncio.run(adapter.place_equity_order(
        payload, role=role,
        safety_decision=allow_decision(role=role, arguments=payload),
    ))


@pytest.mark.parametrize("role", ROLES)
def test_matching_symbol_takes_the_direct_accepted_path(role):
    session = Session(place_envelope(role, "AAPL"))
    parsed = place(session, role)

    assert parsed.symbol == "AAPL"
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("blank", BLANKS)
def test_blank_symbol_is_pending_and_reconcilable(role, blank):
    session = Session(place_envelope(role, blank))

    with pytest.raises(AmbiguousWriteError) as caught:
        place(session, role)

    error = caught.value
    assert error.category == f"{role.upper()}_PLACE_SYMBOL_PENDING"
    assert error.category in ALLOWED_FAILURE_CATEGORIES
    # Neither a rejection nor a transport failure.
    assert error.call_returned is True
    assert error.response_shape == "documented_envelope"
    assert error.transport_classification == "NONE"
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ROLES)
def test_wrong_symbol_is_a_terminal_mismatch_not_ambiguity(role):
    session = Session(place_envelope(role, "MSFT"))

    with pytest.raises(PlacementSymbolMismatchError) as caught:
        place(session, role)

    assert caught.value.category == f"{role.upper()}_PLACE_SYMBOL_MISMATCH"
    assert caught.value.category in ALLOWED_FAILURE_CATEGORIES
    # Deliberately NOT an AmbiguousWriteError: that type is reconciled.
    assert not isinstance(caught.value, AmbiguousWriteError)
    assert "MSFT" not in str(caught.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize("role", ROLES)
def test_no_placement_is_ever_repeated_for_any_symbol_state(role):
    for symbol, expected in (("AAPL", None), ("", AmbiguousWriteError),
                             ("MSFT", PlacementSymbolMismatchError)):
        session = Session(place_envelope(role, symbol))
        adapter = RobinhoodCommissioningAdapter(session, "transient")
        payload = request_payload(role)
        decision = allow_decision(role=role, arguments=payload)
        try:
            asyncio.run(adapter.place_equity_order(
                payload, role=role, safety_decision=decision
            ))
        except Exception as error:
            assert expected is not None and isinstance(error, expected)
        with pytest.raises(CommissioningError, match="only once"):
            asyncio.run(adapter.place_equity_order(
                payload, role=role, safety_decision=decision
            ))
        assert len(session.calls) == 1


# --- end-to-end through the orchestrator ------------------------------


class BlankSymbolCreationAdapter(FakeAdapter):
    """Placements return a blank creation symbol; reads return the real one.

    This is the exact live shape from run …-06.
    """

    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        self._record("place_equity_order")
        self.write_roles.append(role)
        # The write DID land at the broker; only the creation response could
        # not identify it. Reads therefore advance exactly as on success.
        self.phase = role
        raise AmbiguousWriteError(
            f"{role.upper()}_PLACE_SYMBOL_PENDING",
            transport_classification="NONE",
            response_shape="documented_envelope",
            call_returned=True,
        )


def evidence_of(result):
    return json.loads(result.evidence_path.read_text(encoding="utf-8"))


def test_live_shape_reconciles_to_complete_without_malformed_classification(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=BlankSymbolCreationAdapter(),
        run_id="blank-symbol-live-shape",
    )
    result = asyncio.run(instance.run())
    rendered = result.evidence_path.read_text(encoding="utf-8")
    evidence = json.loads(rendered)
    classifications = evidence["conservative_classifications"]

    assert result.state is State.COMPLETE
    assert evidence["boolean_validations"]["verified_flat"] is True
    # The whole point: no longer malformed.
    for role in ("ENTRY", "STOP", "EXIT"):
        assert f"{role}_PLACE_RESPONSE_MALFORMED" not in classifications
        assert f"{role}_PLACE_SYMBOL_PENDING" in classifications
    # Identity was proven by the read-only reconciliation, not by the response.
    assert "ENTRY_RECONCILED_BY_ORDER" in classifications
    assert "STOP_RECONCILED_BY_ORDER" in classifications
    assert "EXIT_RECONCILED_BY_ORDER" in classifications
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1, "stop_place": 1, "stop_cancel": 1, "exit_place": 1,
    }
    assert evidence["record_counts"]["stop_place_retries"] == 0
    assert evidence["record_counts"]["stop_cancel_retries"] == 0
    assert adapter.write_roles == ["entry", "stop", "cancel", "exit"]
    assert handoffs == []
    # The field-diagnostic accusation is gone.
    assert evidence["place_diagnostics"] == {}
    validate_sanitized_evidence(evidence)
    for leaked in ("AAPL", "MSFT", "order-1", "instrument-1", "transient"):
        assert leaked not in rendered


class WrongSymbolCreationAdapter(FakeAdapter):
    async def place_equity_order(self, arguments, *, role, safety_decision=None):
        self._record("place_equity_order")
        self.write_roles.append(role)
        raise PlacementSymbolMismatchError(f"{role.upper()}_PLACE_SYMBOL_MISMATCH")


def test_wrong_symbol_fails_closed_without_reconciliation(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=WrongSymbolCreationAdapter(),
        run_id="blank-symbol-mismatch",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.OPERATOR_REQUIRED
    assert evidence["failure_category"] == "ENTRY_PLACE_SYMBOL_MISMATCH"
    # Exactly one write, and reconciliation never started.
    assert adapter.write_roles == ["entry"]
    assert evidence["write_attempt_counters"] == {
        "entry_place": 1, "stop_place": 0, "stop_cancel": 0, "exit_place": 0,
    }
    assert "entry_reconciliation_polls" not in evidence["record_counts"]
    assert evidence["boolean_validations"].get("verified_flat") is not True
    assert len(handoffs) == 1


class BlankThenWrongSymbolAdapter(BlankSymbolCreationAdapter):
    """Creation symbol blank, but the reconciled order names another symbol."""

    async def get_equity_orders(self):
        rows = await super().get_equity_orders()
        return tuple(
            order(row.server_order_id, side=row.side, order_type=row.order_type,
                  state=row.state, cumulative=row.cumulative_quantity,
                  remaining=row.remaining_quantity, stop_price=row.stop_price,
                  average=row.average_fill_price, symbol="MSFT")
            if row.side == "buy" else row
            for row in rows
        )


def test_blank_then_wrong_reconciled_symbol_fails_closed(tmp_path):
    instance, adapter, _, handoffs = make_orchestrator(
        tmp_path, adapter=BlankThenWrongSymbolAdapter(),
        run_id="blank-symbol-reconciled-wrong",
    )
    result = asyncio.run(instance.run())
    evidence = evidence_of(result)

    assert result.state is State.OPERATOR_REQUIRED
    assert adapter.write_roles.count("entry") == 1
    assert evidence["write_attempt_counters"]["stop_place"] == 0
    assert evidence["boolean_validations"].get("verified_flat") is not True
    assert len(handoffs) == 1
