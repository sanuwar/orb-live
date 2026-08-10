"""Provider-neutral market-data fetch boundary, plus one concrete adapter
(Alpaca Market Data API v2, IEX feed) for M15 Issue 1.

`MarketDataProvider` is the one interface strategy/shadow code depends on.
Nothing above this boundary needs to know which provider produced a
`MarketBar` -- provider-specific payload shapes never leak past this
module. Only ONE concrete adapter is implemented here (`AlpacaMarketDataProvider`),
matching the instruction to build the boundary first and recommend one
provider, not three competing adapters.

This module never touches a brokerage credential: `AlpacaCredentials` is a
market-data-only API key/secret pair, structurally distinct from
`src.robinhood_client`'s OAuth token storage, and this file does not import
`src.robinhood_client`, `src.robinhood_commissioning_adapter`, or any other
broker-write-capable module (see `tests/test_market_data_shadow_firewall.py`
for the structural proof).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol, Sequence

from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataSourceIdentity


class MarketDataProviderError(RuntimeError):
    """Any refusal at the provider boundary. Never carries an API key,
    account identifier, or other secret -- only a fixed, sanitized
    category."""


class MarketDataProvider(Protocol):
    """The one fetch boundary strategy/shadow code depends on."""

    async def fetch_minute_bars(
        self, *, symbol: str, start: datetime, end: datetime,
    ) -> Sequence[MarketBar]:
        ...


@dataclass(frozen=True)
class AlpacaCredentials:
    """A market-data-only API key/secret pair. Never a brokerage
    credential: Alpaca issues separate keys for market-data-only access
    that carry no trading or account-read authority, and this repository
    only ever uses that scope here."""

    api_key_id: str
    api_secret_key: str

    def __post_init__(self) -> None:
        if not isinstance(self.api_key_id, str) or not self.api_key_id.strip():
            raise MarketDataProviderError("an Alpaca API key ID is required")
        if not isinstance(self.api_secret_key, str) or not self.api_secret_key.strip():
            raise MarketDataProviderError("an Alpaca API secret key is required")

    def headers(self) -> dict[str, str]:
        return {
            "APCA-API-KEY-ID": self.api_key_id,
            "APCA-API-SECRET-KEY": self.api_secret_key,
        }


#: The Alpaca IEX-feed source identity every bar this adapter produces
#: carries. A fixed constant, not derived from the response, so a
#: provider response can never claim to be a different source than the
#: one this adapter actually called.
ALPACA_IEX_SOURCE = MarketDataSourceIdentity(
    provider="alpaca", feed="iex", api_version="v2",
)

ALPACA_DEFAULT_BASE_URL = "https://data.alpaca.markets/v2"


def _parse_alpaca_timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise MarketDataProviderError("bar timestamp is missing or malformed")
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise MarketDataProviderError("bar timestamp could not be parsed") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_alpaca_decimal(value: Any, *, field: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise MarketDataProviderError(f"bar field {field} is not numeric")
    try:
        return Decimal(str(value))
    except InvalidOperation as exc:
        raise MarketDataProviderError(f"bar field {field} could not be parsed") from exc


def normalize_alpaca_bar(
    raw: Mapping[str, Any],
    *,
    symbol: str,
    receive_timestamp: datetime,
    interval_seconds: int = 60,
) -> MarketBar:
    """Turn one raw Alpaca bar object (`{"t","o","h","l","c","v",...}`) into
    a canonical `MarketBar`. Raises `MarketDataProviderError` for anything
    structurally incomplete -- this function never guesses a missing field.
    `quality` is intentionally NOT computed here: normalization and quality
    classification are separate concerns (see `src.market_data_canonical.
    validate_market_bar`), so every bar this function returns carries
    `MarketDataQuality.VALID` as a placeholder that the caller is REQUIRED
    to immediately overwrite via `validate_market_bar` before trusting it."""
    from src.market_data_canonical import MarketDataQuality

    if not isinstance(raw, Mapping):
        raise MarketDataProviderError("bar payload is not an object")
    missing = {"t", "o", "h", "l", "c", "v"} - set(raw)
    if missing:
        raise MarketDataProviderError(f"bar payload is missing fields: {sorted(missing)}")

    event_timestamp = _parse_alpaca_timestamp(raw["t"])
    end_timestamp = event_timestamp + timedelta(seconds=interval_seconds)
    open_price = _parse_alpaca_decimal(raw["o"], field="o")
    high_price = _parse_alpaca_decimal(raw["h"], field="h")
    low_price = _parse_alpaca_decimal(raw["l"], field="l")
    close_price = _parse_alpaca_decimal(raw["c"], field="c")
    raw_volume = raw["v"]
    if isinstance(raw_volume, bool) or not isinstance(raw_volume, (int, float)):
        raise MarketDataProviderError("bar field v is not numeric")
    volume = int(raw_volume)

    return MarketBar(
        symbol=symbol.upper().strip(),
        exchange_session="REGULAR",
        # Trading dates are exchange-local dates, not UTC calendar dates.
        trading_date=event_timestamp.astimezone(EASTERN).date(),
        event_timestamp=event_timestamp,
        end_timestamp=end_timestamp,
        receive_timestamp=receive_timestamp,
        open=open_price,
        high=high_price,
        low=low_price,
        close=close_price,
        volume=volume,
        source=ALPACA_IEX_SOURCE,
        interval_seconds=interval_seconds,
        provider_sequence_id=None,
        quality=MarketDataQuality.VALID,
    )


class _HttpResponseLike(Protocol):
    status_code: int

    def json(self) -> Any: ...


class _HttpClientLike(Protocol):
    async def get(
        self, url: str, *, headers: Mapping[str, str], params: Mapping[str, Any],
    ) -> _HttpResponseLike:
        ...


class AlpacaMarketDataProvider:
    """Reads minute bars from Alpaca's public Market Data API v2 (IEX
    feed). Never imports or references anything Robinhood/broker-write
    capable -- see `tests/test_market_data_shadow_firewall.py`. The HTTP
    client and clock are both injected (matching this repository's
    injected-effects convention elsewhere), so tests never make a real
    network call."""

    def __init__(
        self,
        *,
        credentials: AlpacaCredentials,
        http_client: _HttpClientLike,
        now: Callable[[], datetime],
        base_url: str = ALPACA_DEFAULT_BASE_URL,
    ) -> None:
        if not isinstance(credentials, AlpacaCredentials):
            raise MarketDataProviderError("Alpaca credentials are required")
        self._credentials = credentials
        self._http_client = http_client
        self._now = now
        self._base_url = base_url
        self._last_response_diagnostics: dict[str, object] = {}

    @property
    def last_response_diagnostics(self) -> dict[str, object]:
        """Sanitized facts about the most recent provider response."""
        return dict(self._last_response_diagnostics)

    async def fetch_minute_bars(
        self, *, symbol: str, start: datetime, end: datetime,
    ) -> list[MarketBar]:
        normalized_symbol = symbol.upper().strip()
        if not normalized_symbol:
            raise MarketDataProviderError("a symbol is required")
        if start.tzinfo is None or end.tzinfo is None:
            raise MarketDataProviderError("start and end must be timezone-aware")
        if start >= end:
            raise MarketDataProviderError("start must be earlier than end")

        params = {
            "start": start.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "end": end.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
            "timeframe": "1Min",
            "feed": "iex",
            "limit": 10000,
            "adjustment": "split",
        }
        try:
            response = await self._http_client.get(
                f"{self._base_url}/stocks/{normalized_symbol}/bars",
                headers=self._credentials.headers(),
                params=params,
            )
        except Exception as exc:
            raise MarketDataProviderError("provider request failed") from exc

        self._last_response_diagnostics = {"http_status": response.status_code}
        if response.status_code != 200:
            raise MarketDataProviderError(
                f"provider returned a non-success status ({response.status_code})"
            )

        try:
            payload = response.json()
        except Exception as exc:
            raise MarketDataProviderError("provider response was not valid JSON") from exc

        if isinstance(payload, Mapping):
            self._last_response_diagnostics["top_level_keys"] = sorted(
                str(key) for key in payload
            )
        if not isinstance(payload, Mapping) or "bars" not in payload:
            raise MarketDataProviderError("provider response is missing 'bars'")

        raw_bars = payload["bars"]
        if raw_bars is None:
            self._last_response_diagnostics["returned_bar_count"] = 0
            return []
        if not isinstance(raw_bars, list):
            raise MarketDataProviderError("provider 'bars' field is not a list")

        self._last_response_diagnostics["returned_bar_count"] = len(raw_bars)
        receive_timestamp = self._now()
        bars = [
            normalize_alpaca_bar(
                raw_bar, symbol=normalized_symbol, receive_timestamp=receive_timestamp,
            )
            for raw_bar in raw_bars
            if raw_bar is not None
        ]
        # Preserve provider arrival order until the canonical batch-quality
        # gate has had a chance to reject an out-of-order response. A caller
        # may sort only after that validation.
        self._last_response_diagnostics["returned_timestamps"] = [
            bar.event_timestamp.isoformat() for bar in bars
        ]
        return bars


__all__ = [
    "MarketDataProviderError",
    "MarketDataProvider",
    "AlpacaCredentials",
    "ALPACA_IEX_SOURCE",
    "ALPACA_DEFAULT_BASE_URL",
    "normalize_alpaca_bar",
    "AlpacaMarketDataProvider",
]
