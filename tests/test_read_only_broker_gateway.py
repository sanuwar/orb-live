from __future__ import annotations

from contextlib import asynccontextmanager

import asyncio

import pytest

from src.read_only_broker_gateway import (
    FORBIDDEN_TOOL_MARKERS,
    READ_ONLY_TOOL_ALLOWLIST,
    ReadOnlyBrokerGateway,
    ReadOnlySession,
    ReadOnlyToolViolation,
    assert_tool_allowed,
)


WRITE_TOOL_NAMES = (
    "place_equity_order",
    "cancel_equity_order",
    "replace_equity_order",
    "modify_equity_order",
    "review_equity_order",
    "place_option_order",
)


class RecordingSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, tool_name, arguments):
        self.calls.append((tool_name, arguments))
        return {"tool": tool_name}


class RecordingClient:
    def __init__(self):
        self.session_object = RecordingSession()
        self.sessions_opened = 0

    @asynccontextmanager
    async def session(self):
        self.sessions_opened += 1
        yield self.session_object


def test_allowlist_contains_only_read_tools():
    for tool_name in READ_ONLY_TOOL_ALLOWLIST:
        assert tool_name.startswith("get_")

        for marker in FORBIDDEN_TOOL_MARKERS:
            assert marker not in tool_name


def test_allowlist_is_immutable():
    assert isinstance(
        READ_ONLY_TOOL_ALLOWLIST, frozenset
    )

    with pytest.raises(AttributeError):
        READ_ONLY_TOOL_ALLOWLIST.add(
            "place_equity_order"
        )


@pytest.mark.parametrize(
    "tool_name", WRITE_TOOL_NAMES
)
def test_write_tools_are_rejected(tool_name):
    with pytest.raises(ReadOnlyToolViolation):
        assert_tool_allowed(tool_name)

    assert not ReadOnlyBrokerGateway.is_allowed(
        tool_name
    )


@pytest.mark.parametrize(
    "tool_name", sorted(READ_ONLY_TOOL_ALLOWLIST)
)
def test_read_tools_are_allowed(tool_name):
    assert assert_tool_allowed(tool_name) == tool_name
    assert ReadOnlyBrokerGateway.is_allowed(tool_name)


@pytest.mark.parametrize(
    "tool_name", ["", "   ", None, 17, object()]
)
def test_invalid_tool_names_fail_closed(tool_name):
    with pytest.raises(ReadOnlyToolViolation):
        assert_tool_allowed(tool_name)


def test_unknown_tool_rejected_before_session_opens():
    client = RecordingClient()
    gateway = ReadOnlyBrokerGateway(client)

    with pytest.raises(ReadOnlyToolViolation):
        asyncio.run(
            gateway.read("place_equity_order", {})
        )

    assert client.sessions_opened == 0
    assert client.session_object.calls == []


def test_allowed_read_reaches_the_transport():
    client = RecordingClient()
    gateway = ReadOnlyBrokerGateway(client)

    result = asyncio.run(
        gateway.read("get_accounts", {})
    )

    assert result == {"tool": "get_accounts"}
    assert client.sessions_opened == 1
    assert client.session_object.calls == [
        ("get_accounts", {})
    ]


def test_wrapped_session_rejects_write_tools():
    inner = RecordingSession()
    session = ReadOnlySession(inner)

    with pytest.raises(ReadOnlyToolViolation):
        asyncio.run(
            session.call_tool(
                "cancel_equity_order",
                {"order_id": "x"},
            )
        )

    assert inner.calls == []


def test_gateway_exposes_no_write_method():
    methods = {
        name
        for name in dir(ReadOnlyBrokerGateway)
        if not name.startswith("_")
    }

    for marker in FORBIDDEN_TOOL_MARKERS:
        assert not any(
            marker in name for name in methods
        )


def test_gateway_requires_a_client():
    with pytest.raises(ReadOnlyToolViolation):
        ReadOnlyBrokerGateway(None)


def test_session_requires_a_session():
    with pytest.raises(ReadOnlyToolViolation):
        ReadOnlySession(None)
