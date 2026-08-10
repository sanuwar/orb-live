"""M15 Issue 1: Alpaca market-data provider adapter.

Every test is offline: a fake HTTP client stands in for `httpx`, and no
network call, credential file, or broker session is ever touched.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.market_data_canonical import MarketDataQuality
from src.market_data_provider import (
    ALPACA_IEX_SOURCE,
    AlpacaCredentials,
    AlpacaMarketDataProvider,
    MarketDataProviderError,
    normalize_alpaca_bar,
)

RAW_BAR = {
    "t": "2026-08-11T13:30:00Z", "o": "200.00", "h": "201.00",
    "l": "199.50", "c": "200.50", "v": 10_000,
}


class FakeResponse:
    def __init__(self, payload, *, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


class FakeHttpClient:
    def __init__(self, response: FakeResponse, *, raise_on_get: Exception | None = None):
        self._response = response
        self._raise_on_get = raise_on_get
        self.calls: list[dict] = []

    async def get(self, url, *, headers, params):
        self.calls.append({"url": url, "headers": dict(headers), "params": dict(params)})
        if self._raise_on_get is not None:
            raise self._raise_on_get
        return self._response


def _provider(response: FakeResponse, **kwargs) -> tuple[AlpacaMarketDataProvider, FakeHttpClient]:
    client = FakeHttpClient(response, **kwargs)
    provider = AlpacaMarketDataProvider(
        credentials=AlpacaCredentials(api_key_id="key", api_secret_key="secret"),
        http_client=client,
        now=lambda: datetime(2026, 8, 11, 13, 31, tzinfo=timezone.utc),
    )
    return provider, client


# ---------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------


def test_normalize_valid_alpaca_bar():
    receive = datetime.now(timezone.utc)
    bar = normalize_alpaca_bar(RAW_BAR, symbol="aapl", receive_timestamp=receive)
    assert bar.symbol == "AAPL"
    assert bar.close == Decimal("200.50")
    assert bar.source == ALPACA_IEX_SOURCE
    assert bar.receive_timestamp == receive
    assert bar.quality is MarketDataQuality.VALID  # placeholder, caller must re-validate


def test_incomplete_provider_response_rejected():
    incomplete = {"t": "2026-08-11T13:30:00Z", "o": "200.00"}  # missing h/l/c/v
    with pytest.raises(MarketDataProviderError):
        normalize_alpaca_bar(incomplete, symbol="AAPL", receive_timestamp=datetime.now(timezone.utc))


def test_unsupported_payload_shape_rejected():
    with pytest.raises(MarketDataProviderError):
        normalize_alpaca_bar("not-a-mapping", symbol="AAPL", receive_timestamp=datetime.now(timezone.utc))


def test_malformed_timestamp_rejected():
    bad = dict(RAW_BAR, t="not-a-timestamp")
    with pytest.raises(MarketDataProviderError):
        normalize_alpaca_bar(bad, symbol="AAPL", receive_timestamp=datetime.now(timezone.utc))


def test_malformed_price_rejected():
    bad = dict(RAW_BAR, o="not-a-number")
    with pytest.raises(MarketDataProviderError):
        normalize_alpaca_bar(bad, symbol="AAPL", receive_timestamp=datetime.now(timezone.utc))


# ---------------------------------------------------------------------
# Provider fetch: valid, incomplete, error, timeout, unsupported
# ---------------------------------------------------------------------


def test_valid_provider_response_preserves_order_for_batch_validation():
    second_bar = dict(RAW_BAR, t="2026-08-11T13:31:00Z")
    response = FakeResponse({"bars": [second_bar, RAW_BAR], "symbol": "AAPL", "next_page_token": None})
    provider, client = _provider(response)
    bars = asyncio.run(provider.fetch_minute_bars(
        symbol="AAPL",
        start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
    ))
    assert len(bars) == 2
    assert bars[0].event_timestamp > bars[1].event_timestamp
    assert client.calls[0]["headers"]["APCA-API-KEY-ID"] == "key"
    assert "secret" == client.calls[0]["headers"]["APCA-API-SECRET-KEY"]


def test_empty_bars_list_returns_empty():
    response = FakeResponse({"bars": [], "symbol": "AAPL", "next_page_token": None})
    provider, _ = _provider(response)
    bars = asyncio.run(provider.fetch_minute_bars(
        symbol="AAPL",
        start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
    ))
    assert bars == []


def test_null_bars_field_returns_empty():
    response = FakeResponse({"bars": None, "symbol": "AAPL", "next_page_token": None})
    provider, _ = _provider(response)
    bars = asyncio.run(provider.fetch_minute_bars(
        symbol="AAPL",
        start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
    ))
    assert bars == []


def test_incomplete_provider_response_missing_bars_key_rejected():
    response = FakeResponse({"symbol": "AAPL"})
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_provider_error_status_rejected():
    response = FakeResponse({"message": "forbidden"}, status_code=403)
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_provider_transport_failure_classified_as_provider_error():
    response = FakeResponse({"bars": []})
    provider, _ = _provider(response, raise_on_get=TimeoutError("connect timed out"))
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_unsupported_json_payload_rejected():
    response = FakeResponse(["not", "a", "mapping"])
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_bars_field_not_a_list_rejected():
    response = FakeResponse({"bars": "not-a-list"})
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_naive_start_or_end_rejected():
    response = FakeResponse({"bars": []})
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30),  # naive
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))


def test_start_after_end_rejected():
    response = FakeResponse({"bars": []})
    provider, _ = _provider(response)
    with pytest.raises(MarketDataProviderError):
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
        ))


def test_credentials_require_both_key_and_secret():
    with pytest.raises(MarketDataProviderError):
        AlpacaCredentials(api_key_id="", api_secret_key="secret")
    with pytest.raises(MarketDataProviderError):
        AlpacaCredentials(api_key_id="key", api_secret_key="")


def test_credentials_never_appear_in_provider_error_text():
    response = FakeResponse({"message": "forbidden"}, status_code=403)
    provider, _ = _provider(response)
    try:
        asyncio.run(provider.fetch_minute_bars(
            symbol="AAPL",
            start=datetime(2026, 8, 11, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 11, 13, 32, tzinfo=timezone.utc),
        ))
        raise AssertionError("expected MarketDataProviderError")
    except MarketDataProviderError as error:
        assert "secret" not in str(error)
        assert "key" not in str(error).lower()
