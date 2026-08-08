import asyncio
from decimal import Decimal
from types import SimpleNamespace
import pytest

from src.one_share_commissioning import CommissioningError
from src.robinhood_commissioning_adapter import (
    ALLOWED_COMMISSIONING_TOOLS, AmbiguousWriteError, BrokerRejectedError,
    RobinhoodCommissioningAdapter, assert_tool_allowed,
)
from tests.execution_safety_test_support import allow_decision


class Session:
    def __init__(self, responses=None, error=None):
        self.responses = list(responses or [])
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return self.responses.pop(0)


def adapter(session):
    return RobinhoodCommissioningAdapter(session, "transient")


def payload(role):
    side, order_type, duration = {
        "entry": ("buy", "market", "gfd"),
        "stop": ("sell", "stop_market", "gtc"),
        "exit": ("sell", "market", "gfd"),
    }[role]
    result = {
        "account_number": "transient", "symbol": "AAPL", "side": side,
        "type": order_type, "quantity": "1", "time_in_force": duration,
        "market_hours": "regular_hours",
    }
    if role == "stop":
        result["stop_price"] = "185.00"
    return result


def place_result(role, order_id="order-1"):
    side, order_type, trigger, duration = {
        "entry": ("buy", "market", "immediate", "gfd"),
        "stop": ("sell", "market", "stop", "gtc"),
        "exit": ("sell", "market", "immediate", "gfd"),
    }[role]
    return SimpleNamespace(
        server_order_id=order_id, symbol="AAPL", side=side,
        order_type=order_type, trigger=trigger,
        submitted_quantity=Decimal("1"), time_in_force=duration,
        market_hours="regular_hours",
        stop_price=Decimal("185") if role == "stop" else None,
    )


def test_exact_allowed_surface_and_prohibited_tool_rejection():
    assert ALLOWED_COMMISSIONING_TOOLS == {
        "get_accounts", "get_equity_positions", "get_equity_orders",
        "review_equity_order", "place_equity_order", "cancel_equity_order",
    }
    for name in ALLOWED_COMMISSIONING_TOOLS:
        assert assert_tool_allowed(name) == name
    for name in ("list_tools", "get_market_data", "replace_order", "place_option_order"):
        with pytest.raises(CommissioningError):
            assert_tool_allowed(name)


def test_reads_and_review_are_sequential_and_unpaginated():
    session = Session([{"accounts": [{"account_number": "transient",
        "agentic_allowed": True, "state": "active", "deactivated": False,
        "permanently_deactivated": False}]}, {"positions": []}, {
            "symbol": "AAPL", "side": "buy", "type": "market", "quantity": "1",
            "order_checks": {}, "quote_data": {},
        }])
    instance = adapter(session)
    asyncio.run(instance.get_accounts())
    asyncio.run(instance.get_equity_positions())
    asyncio.run(instance.review_equity_order(payload("entry"), role="entry"))
    assert [call[0] for call in session.calls] == [
        "get_accounts", "get_equity_positions", "review_equity_order"
    ]
    assert instance.call_counts == {
        "get_accounts": 1, "get_equity_positions": 1, "review_equity_order": 1
    }


def test_pagination_is_rejected():
    instance = adapter(Session([{"positions": [], "next": "cursor"}]))
    with pytest.raises(CommissioningError):
        asyncio.run(instance.get_equity_positions())


@pytest.mark.parametrize("role", ["entry", "stop", "exit"])
def test_each_place_role_is_one_shot(monkeypatch, role):
    monkeypatch.setattr("src.robinhood_commissioning_adapter.parse_place_response",
                        lambda value: place_result(role))
    session = Session([{"accepted": True}, {"accepted": True}])
    instance = adapter(session)
    place_args = payload(role)
    decision = allow_decision(role=role, arguments=place_args)
    asyncio.run(instance.place_equity_order(
        place_args, role=role, safety_decision=decision
    ))
    with pytest.raises(CommissioningError):
        asyncio.run(instance.place_equity_order(
            place_args, role=role, safety_decision=decision
        ))
    assert len(session.calls) == 1


def test_ambiguous_place_is_not_retried():
    session = Session(error=TimeoutError())
    instance = adapter(session)
    entry_args = payload("entry")
    decision = allow_decision(role="entry", arguments=entry_args)
    with pytest.raises(AmbiguousWriteError):
        asyncio.run(instance.place_equity_order(
            entry_args, role="entry", safety_decision=decision
        ))
    with pytest.raises(CommissioningError):
        asyncio.run(instance.place_equity_order(
            entry_args, role="entry", safety_decision=decision
        ))
    assert len(session.calls) == 1


def test_ambiguous_cancel_is_not_retried(monkeypatch):
    monkeypatch.setattr("src.robinhood_commissioning_adapter.parse_cancel_response",
                        lambda value: (_ for _ in ()).throw(ValueError()))
    session = Session([{"accepted": True}, {"accepted": True}])
    instance = adapter(session)
    cancel_args = {"order_id": "transient"}
    decision = allow_decision(role="cancel", arguments=cancel_args)
    with pytest.raises(AmbiguousWriteError):
        asyncio.run(instance.cancel_equity_order(
            cancel_args, safety_decision=decision
        ))
    with pytest.raises(CommissioningError):
        asyncio.run(instance.cancel_equity_order(
            cancel_args, safety_decision=decision
        ))
    assert len(session.calls) == 1


def test_unknown_place_role_rejected_before_call():
    session = Session()
    instance = adapter(session)
    with pytest.raises(CommissioningError):
        asyncio.run(instance.place_equity_order({}, role="second-entry"))
    assert session.calls == []


@pytest.mark.parametrize("field,value", [
    ("account_number", "wrong"), ("symbol", "MSFT"), ("quantity", "2"),
    ("side", "sell"), ("market_hours", "extended_hours"),
])
def test_policy_mismatch_is_rejected_before_delegation(field, value):
    session = Session()
    instance = adapter(session)
    arguments = payload("entry")
    arguments[field] = value
    with pytest.raises(CommissioningError):
        asyncio.run(instance.place_equity_order(arguments, role="entry"))
    assert session.calls == []


def test_stop_price_must_be_contract_normalized():
    session = Session()
    instance = adapter(session)
    arguments = payload("stop")
    arguments["stop_price"] = "185.001"
    with pytest.raises(CommissioningError):
        asyncio.run(instance.place_equity_order(arguments, role="stop"))
    assert session.calls == []


def test_reads_always_use_bound_account():
    session = Session([{"positions": []}, {"orders": []}])
    instance = adapter(session)
    asyncio.run(instance.get_equity_positions())
    asyncio.run(instance.get_equity_orders())
    assert all(arguments == {"account_number": "transient"}
               for _, arguments in session.calls)


def test_rich_order_read_preserves_transient_identifier_only_in_memory(monkeypatch):
    parsed = SimpleNamespace(
        state="confirmed", submitted_quantity=Decimal("1"),
        cumulative_quantity=Decimal("0"), remaining_quantity=Decimal("1"),
    )
    monkeypatch.setattr("src.robinhood_commissioning_adapter.parse_order_status",
                        lambda row: parsed)
    row = {
        "id": "transient-id", "symbol": "AAPL", "side": "sell",
        "type": "market", "trigger": "stop", "stop_price": "185.00",
        "average_price": None, "time_in_force": "gtc",
        "market_hours": "regular_hours",
    }
    instance = adapter(Session([{"orders": [row]}]))
    records = asyncio.run(instance.get_equity_orders())
    assert len(records) == 1
    assert records[0].server_order_id == "transient-id"
    assert records[0].order_type == "stop_market"
    assert records[0].stop_price == Decimal("185.00")


def test_position_read_rejects_missing_quantity():
    instance = adapter(Session([{"positions": [{"symbol": "AAPL"}]}]))
    with pytest.raises(CommissioningError):
        asyncio.run(instance.get_equity_positions())


def test_explicit_write_rejection_is_not_ambiguous():
    class Rejected:
        is_error = True
    instance = adapter(Session([Rejected()]))
    entry_args = payload("entry")
    with pytest.raises(BrokerRejectedError):
        asyncio.run(instance.place_equity_order(
            entry_args, role="entry",
            safety_decision=allow_decision(role="entry", arguments=entry_args),
        ))


def test_distinct_writes_are_serialized(monkeypatch):
    responses = iter((place_result("entry", "entry-1"),
                      place_result("stop", "stop-1")))
    monkeypatch.setattr("src.robinhood_commissioning_adapter.parse_place_response",
                        lambda value: next(responses))

    class ConcurrentSession:
        def __init__(self):
            self.active = 0
            self.maximum = 0

        async def call_tool(self, name, arguments):
            self.active += 1
            self.maximum = max(self.maximum, self.active)
            await asyncio.sleep(0)
            self.active -= 1
            return {"accepted": True}

    async def invoke_both(adapter):
        entry_args = payload("entry")
        stop_args = payload("stop")
        await asyncio.gather(
            adapter.place_equity_order(
                entry_args, role="entry",
                safety_decision=allow_decision(role="entry", arguments=entry_args),
            ),
            adapter.place_equity_order(
                stop_args, role="stop",
                safety_decision=allow_decision(role="stop", arguments=stop_args),
            ),
        )

    session = ConcurrentSession()
    asyncio.run(invoke_both(adapter(session)))
    assert session.maximum == 1
