from __future__ import annotations

from contextlib import asynccontextmanager
from decimal import Decimal

import pytest

from src.robinhood_client import (
    RobinhoodClient,
    RobinhoodClientError,
    _unwrap_tool_result,
)


class FakeResult:
    def __init__(
        self,
        *,
        data: dict | None = None,
        is_error: bool = False,
        error_text: str = "",
    ):
        self.isError = is_error
        self.structuredContent = (
            {"data": data}
            if data is not None
            else None
        )
        self.content = []

        if error_text:
            self.content = [
                type(
                    "Content",
                    (),
                    {"text": error_text},
                )()
            ]


class FakeSession:
    def __init__(self, result: FakeResult):
        self.result = result
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict,
    ):
        self.calls.append(
            (tool_name, arguments)
        )
        return self.result


class FakeRobinhoodClient(RobinhoodClient):
    def __init__(self, result: FakeResult):
        self.fake_session = FakeSession(result)

    @asynccontextmanager
    async def session(self):
        yield self.fake_session


def valid_quote_result() -> FakeResult:
    return FakeResult(
        data={
            "results": [
                {
                    "quote": {
                        "symbol": "AAPL",
                        "bid_price": "330.71",
                        "ask_price": "330.73",
                        "last_trade_price": "330.715",
                    }
                }
            ]
        }
    )


@pytest.mark.anyio
async def test_get_equity_quote_parses_nested_quote():
    client = FakeRobinhoodClient(
        valid_quote_result()
    )

    quote = await client.get_equity_quote(
        " aapl "
    )

    assert quote.symbol == "AAPL"
    assert quote.bid_price == Decimal("330.71")
    assert quote.ask_price == Decimal("330.73")
    assert quote.last_trade_price == Decimal(
        "330.715"
    )

    assert client.fake_session.calls == [
        (
            "get_equity_quotes",
            {"symbols": ["AAPL"]},
        )
    ]


@pytest.mark.anyio
async def test_missing_symbol_is_rejected():
    client = FakeRobinhoodClient(
        valid_quote_result()
    )

    with pytest.raises(
        ValueError,
        match="symbol is required",
    ):
        await client.get_equity_quote("   ")


@pytest.mark.anyio
async def test_missing_quote_is_rejected():
    result = FakeResult(
        data={
            "results": [
                {
                    "quote": {
                        "symbol": "MSFT",
                        "bid_price": "500",
                        "ask_price": "501",
                    }
                }
            ]
        }
    )

    client = FakeRobinhoodClient(result)

    with pytest.raises(
        RobinhoodClientError,
        match="No quote was returned",
    ):
        await client.get_equity_quote("AAPL")


@pytest.mark.anyio
async def test_bid_above_ask_is_rejected():
    result = FakeResult(
        data={
            "results": [
                {
                    "quote": {
                        "symbol": "AAPL",
                        "bid_price": "331",
                        "ask_price": "330",
                        "last_trade_price": "330.50",
                    }
                }
            ]
        }
    )

    client = FakeRobinhoodClient(result)

    with pytest.raises(
        RobinhoodClientError,
        match="bid above the ask",
    ):
        await client.get_equity_quote("AAPL")


def test_tool_error_is_unwrapped_as_exception():
    result = FakeResult(
        is_error=True,
        error_text="Permission denied",
    )

    with pytest.raises(
        RobinhoodClientError,
        match="Permission denied",
    ):
        _unwrap_tool_result(
            result,
            "get_equity_quotes",
        )


def test_missing_structured_content_is_rejected():
    result = FakeResult()

    with pytest.raises(
        RobinhoodClientError,
        match="no structured content",
    ):
        _unwrap_tool_result(
            result,
            "get_equity_quotes",
        )