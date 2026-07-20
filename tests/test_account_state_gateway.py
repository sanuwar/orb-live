from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from src.account_state import AccountStateReader
from src.read_only_broker_gateway import (
    READ_ONLY_TOOL_ALLOWLIST,
    ReadOnlyBrokerGateway,
    ReadOnlySession,
    ReadOnlyToolViolation,
)
from src.robinhood_client import RobinhoodClientError


ACCOUNT_NUMBER = "TEST12345678"


class ToolResult:
    def __init__(self, data):
        self.isError = False
        self.structuredContent = {"data": data}
        self.content = []


class FakeSession:
    """Deterministic replay of the four read payloads."""

    def __init__(self, payloads):
        self.payloads = payloads
        self.calls: list[str] = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append(tool_name)

        if tool_name not in self.payloads:
            raise AssertionError(
                f"unexpected tool: {tool_name}"
            )

        return ToolResult(self.payloads[tool_name])


class FakeClient:
    def __init__(self, payloads):
        self.session_object = FakeSession(payloads)
        self.sessions_opened = 0

    @asynccontextmanager
    async def session(self):
        self.sessions_opened += 1
        yield self.session_object


class TripwireClient:
    """Opening a raw session at all is a failure."""

    def __init__(self):
        self.sessions_opened = 0

    @asynccontextmanager
    async def session(self):
        self.sessions_opened += 1
        raise AssertionError(
            "The raw client session must not be opened."
        )
        yield None  # pragma: no cover


def payloads(**changes):
    base = {
        "get_accounts": {
            "accounts": [
                {
                    "account_number": ACCOUNT_NUMBER,
                    "agentic_allowed": True,
                    "state": "active",
                    "brokerage_account_type": "individual",
                    "type": "cash",
                    "nickname": "Test",
                }
            ]
        },
        "get_portfolio": {
            "total_value": "3000",
            "equity_value": "3000",
            "cash": "3000",
            "pending_deposits": "0",
            "buying_power": {
                "buying_power": "3000",
                "unleveraged_buying_power": "3000",
                "display_currency": "USD",
            },
        },
        "get_equity_positions": {"positions": []},
        "get_equity_orders": {"orders": []},
    }
    base.update(changes)
    return base


def read_snapshot(client=None, payload=None):
    client = client or FakeClient(payload or payloads())
    reader = AccountStateReader(client)
    return client, asyncio.run(reader.read_snapshot())


# ---------- gateway routing ----------

def test_reader_builds_a_gateway_by_default():
    reader = AccountStateReader(FakeClient(payloads()))

    assert isinstance(reader.gateway, ReadOnlyBrokerGateway)


def test_reader_accepts_an_injected_gateway():
    client = FakeClient(payloads())
    gateway = ReadOnlyBrokerGateway(client)
    reader = AccountStateReader(client, gateway=gateway)

    assert reader.gateway is gateway


def test_reader_uses_the_gateway_session_not_the_raw_client():
    """The yielded session must be the allowlist-enforcing wrapper."""

    client = FakeClient(payloads())
    reader = AccountStateReader(client)
    seen: list[object] = []

    original = ReadOnlySession.call_tool

    async def spy(self, tool_name, arguments=None):
        seen.append(self)
        return await original(self, tool_name, arguments)

    ReadOnlySession.call_tool = spy
    try:
        asyncio.run(reader.read_snapshot())
    finally:
        ReadOnlySession.call_tool = original

    assert len(seen) == 4
    assert all(
        isinstance(item, ReadOnlySession) for item in seen
    )


def test_read_snapshot_does_not_call_client_session_directly():
    source = (
        __import__("pathlib")
        .Path(__import__("src.account_state", fromlist=["x"]).__file__)
        .read_text(encoding="utf-8")
    )

    assert "self.client.session()" not in source
    assert "self.gateway.session()" in source


def test_rejected_tool_never_opens_the_raw_session():
    client = TripwireClient()
    gateway = ReadOnlyBrokerGateway(client)

    with pytest.raises(ReadOnlyToolViolation):
        asyncio.run(
            gateway.read("place_equity_order", {})
        )

    assert client.sessions_opened == 0


# ---------- preserved behavior ----------

def test_the_four_reads_are_unchanged_and_allowlisted():
    client, _ = read_snapshot()

    assert client.session_object.calls == [
        "get_accounts",
        "get_portfolio",
        "get_equity_positions",
        "get_equity_orders",
    ]

    for tool in client.session_object.calls:
        assert tool in READ_ONLY_TOOL_ALLOWLIST


def test_exactly_one_bounded_session_is_opened():
    client, _ = read_snapshot()

    assert client.sessions_opened == 1


def test_no_additional_broker_calls_or_retries():
    client, _ = read_snapshot()

    assert len(client.session_object.calls) == 4
    assert len(set(client.session_object.calls)) == 4


def test_successful_snapshot_behavior_is_unchanged():
    _, snapshot = read_snapshot()
    metadata = snapshot.metadata

    assert (
        snapshot.account_state.account.account_number
        == ACCOUNT_NUMBER
    )
    assert (
        snapshot.account_state.account.masked_account_number
        == "••••5678"
    )
    assert metadata.positions_complete is True
    assert metadata.orders_complete is True
    assert (
        metadata.positions_cycle_id
        == metadata.reconciliation_id
    )
    assert (
        metadata.orders_cycle_id
        == metadata.reconciliation_id
    )
    assert (
        metadata.started_at
        <= metadata.positions_read_at
        <= metadata.orders_read_at
        <= metadata.completed_at
    )
    assert len(metadata.account_fingerprint) == 64
    assert ACCOUNT_NUMBER not in metadata.account_fingerprint


def test_read_returns_account_state():
    client = FakeClient(payloads())
    reader = AccountStateReader(client)
    state = asyncio.run(reader.read())

    assert state.account.account_number == ACCOUNT_NUMBER


# ---------- preserved fail-closed semantics ----------

def test_paginated_positions_still_fail_closed():
    payload = payloads(
        get_equity_positions={
            "positions": [],
            "next": "page-2",
        }
    )

    with pytest.raises(RobinhoodClientError):
        read_snapshot(payload=payload)


def test_paginated_orders_still_fail_closed():
    payload = payloads(
        get_equity_orders={
            "orders": [],
            "next": "page-2",
        }
    )

    with pytest.raises(RobinhoodClientError):
        read_snapshot(payload=payload)


def test_no_eligible_account_still_fails_closed():
    payload = payloads(
        get_accounts={
            "accounts": [
                {
                    "account_number": ACCOUNT_NUMBER,
                    "agentic_allowed": False,
                    "state": "active",
                }
            ]
        }
    )

    with pytest.raises(RobinhoodClientError):
        read_snapshot(payload=payload)


def test_conflicting_multiple_accounts_still_fail_closed():
    payload = payloads(
        get_accounts={
            "accounts": [
                {
                    "account_number": ACCOUNT_NUMBER,
                    "agentic_allowed": True,
                    "state": "active",
                },
                {
                    "account_number": "OTHER87654321",
                    "agentic_allowed": True,
                    "state": "active",
                },
            ]
        }
    )

    with pytest.raises(RobinhoodClientError) as error:
        read_snapshot(payload=payload)

    assert ACCOUNT_NUMBER not in str(error.value)


def test_malformed_response_still_fails_closed():
    client = FakeClient(payloads())

    async def malformed(tool_name, arguments):
        client.session_object.calls.append(tool_name)
        result = ToolResult(None)
        result.structuredContent = {"data": "not-a-dict"}
        return result

    client.session_object.call_tool = malformed
    reader = AccountStateReader(client)

    with pytest.raises(RobinhoodClientError):
        asyncio.run(reader.read_snapshot())


# ---------- hygiene ----------

def test_no_credential_or_production_path_is_touched():
    import src.account_state as module

    source = (
        __import__("pathlib")
        .Path(module.__file__)
        .read_text(encoding="utf-8")
    )

    for marker in (
        ".rh_tokens.json",
        "tokens.json",
        "credentials.json",
        "data/trade_state.sqlite",
        "DEFAULT_DB_PATH",
    ):
        assert marker not in source
