"""Canonical, provider-neutral market-data model and fail-closed quality
validation for production market-data ingestion (M15 Issue 1).

Provider payloads never reach the strategy directly. The pipeline is:

    provider payload
          |
    provider adapter (src/market_data_provider.py)
          |
    MarketBar (this module) -- provider identity + quality carried explicitly
          |
    validate_market_bar / validate_market_data_batch (this module)
          |
    MinuteBar (src/market_data.py, via MarketBar.to_minute_bar()) -- the
    existing, already-tested opening-range/VWAP/median-volume calculations
    consume this unchanged, so this module deliberately does not duplicate
    them.

Every quality check here fails closed: an unrecognized, malformed, or
ambiguous input becomes a specific `MarketDataQuality` rejection reason,
never a silently-repaired bar. No I/O, no provider knowledge, no network
call -- this module only classifies facts a caller already has.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Sequence

from src.exchange_calendar import EASTERN, regular_session_bounds
from src.market_data import MinuteBar
from src.protection_observation_model import canonical_bytes


class MarketDataError(RuntimeError):
    """This module's own construction contract was violated (e.g. a naive
    datetime, a non-Decimal price). Distinct from a quality rejection: a
    structurally malformed input is a programming error in the caller, not
    a fact about the market this module's quality vocabulary can express."""


_DIGEST_DOMAIN = b"orb-live/market-data/v1"


class MarketDataQuality(str, Enum):
    """No transformation ever turns a rejected bar into a valid one. VALID
    is the only value that permits a bar to be used for a shadow decision."""

    VALID = "VALID"
    STALE = "STALE"
    FUTURE_TIMESTAMP = "FUTURE_TIMESTAMP"
    MALFORMED_TIMESTAMP = "MALFORMED_TIMESTAMP"
    DUPLICATE_CONFLICTING = "DUPLICATE_CONFLICTING"
    OUT_OF_ORDER = "OUT_OF_ORDER"
    OVERLAPPING_PROHIBITED = "OVERLAPPING_PROHIBITED"
    INVALID_OHLC = "INVALID_OHLC"
    NEGATIVE_PRICE = "NEGATIVE_PRICE"
    INVALID_VOLUME = "INVALID_VOLUME"
    WRONG_SYMBOL = "WRONG_SYMBOL"
    WRONG_TRADING_DATE = "WRONG_TRADING_DATE"
    WRONG_INTERVAL = "WRONG_INTERVAL"
    UNSUPPORTED_PROVIDER_STATE = "UNSUPPORTED_PROVIDER_STATE"
    MISSING_OPENING_RANGE_BARS = "MISSING_OPENING_RANGE_BARS"
    MARKET_HOLIDAY = "MARKET_HOLIDAY"
    OUTSIDE_SESSION = "OUTSIDE_SESSION"
    PROVIDER_ERROR = "PROVIDER_ERROR"
    INCOMPLETE_RESPONSE = "INCOMPLETE_RESPONSE"
    MIXED_PROVIDER_IDENTITY = "MIXED_PROVIDER_IDENTITY"


#: The only value that permits a bar/batch to be used for a shadow decision.
ACCEPTABLE_QUALITY: MarketDataQuality = MarketDataQuality.VALID


def _require_aware(value: datetime, *, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise MarketDataError(f"{label} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise MarketDataError(f"{label} must be timezone-aware")
    return value


def _require_positive_decimal(value: Decimal, *, label: str) -> Decimal:
    if not isinstance(value, Decimal):
        raise MarketDataError(f"{label} must be a Decimal")
    return value


@dataclass(frozen=True)
class MarketDataSourceIdentity:
    """Which provider, which feed, which contract version produced a bar.
    Reference-only: never carries an API key, account identifier, or any
    other secret."""

    provider: str
    feed: str
    api_version: str

    def __post_init__(self) -> None:
        for field_name in ("provider", "feed", "api_version"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise MarketDataError(f"source {field_name} is required")


@dataclass(frozen=True)
class MarketBar:
    """One canonical, immutable OHLCV bar. `quality` is set once, by
    `validate_market_bar`/`validate_market_data_batch` -- this dataclass
    itself never computes it, matching every other immutable evidence
    record in this repository (facts are supplied, not derived on read)."""

    symbol: str
    exchange_session: str
    trading_date: date
    event_timestamp: datetime
    end_timestamp: datetime
    receive_timestamp: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int
    source: MarketDataSourceIdentity
    interval_seconds: int
    provider_sequence_id: str | None
    quality: MarketDataQuality

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise MarketDataError("symbol is required")
        if self.symbol != self.symbol.upper().strip():
            raise MarketDataError("symbol must be normalized upper-case")
        if not isinstance(self.exchange_session, str) or not self.exchange_session:
            raise MarketDataError("exchange_session is required")
        if not isinstance(self.trading_date, date) or isinstance(self.trading_date, datetime):
            raise MarketDataError("trading_date must be a plain date")
        _require_aware(self.event_timestamp, label="event_timestamp")
        _require_aware(self.end_timestamp, label="end_timestamp")
        _require_aware(self.receive_timestamp, label="receive_timestamp")
        for label in ("open", "high", "low", "close"):
            _require_positive_decimal(getattr(self, label), label=label)
        if not isinstance(self.volume, int) or isinstance(self.volume, bool):
            raise MarketDataError("volume must be an int")
        if not isinstance(self.source, MarketDataSourceIdentity):
            raise MarketDataError("source must be a MarketDataSourceIdentity")
        if not isinstance(self.interval_seconds, int) or self.interval_seconds <= 0:
            raise MarketDataError("interval_seconds must be a positive int")
        if not isinstance(self.quality, MarketDataQuality):
            raise MarketDataError("quality must be a MarketDataQuality")

    def to_minute_bar(self) -> MinuteBar:
        """Bridges to the existing, already-tested `src.market_data.
        MinuteBar` so `calculate_opening_range`/`calculate_estimated_vwap`/
        `calculate_median_opening_volume` are reused unchanged rather than
        reimplemented. Only called for bars that already carry quality
        VALID -- callers are expected to filter first."""
        return MinuteBar(
            symbol=self.symbol,
            begins_at=self.event_timestamp,
            open_price=self.open,
            high_price=self.high,
            low_price=self.low,
            close_price=self.close,
            volume=self.volume,
            session=self.exchange_session,
            interpolated=False,
        )


@dataclass(frozen=True)
class MarketDataFreshnessPolicy:
    """Versioned so a future change to the freshness bound is an explicit,
    reviewable, digest-visible decision rather than a magic number edited
    in place. `max_age_seconds` bounds how old `event_timestamp` may be
    relative to an injected `now` before a bar is STALE; there is no
    implicit wall-clock read anywhere in this module."""

    version: str
    max_age_seconds: float

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version.strip():
            raise MarketDataError("freshness policy version is required")
        if not isinstance(self.max_age_seconds, (int, float)) or isinstance(
            self.max_age_seconds, bool
        ) or self.max_age_seconds <= 0:
            raise MarketDataError("max_age_seconds must be a positive number")


#: The default policy for M15 Issue 2's first bounded shadow session: a bar
#: older than 5 minutes is too old to drive an opening-range decision meant
#: to be evaluated within the entry window.
DEFAULT_FRESHNESS_POLICY = MarketDataFreshnessPolicy(
    version="market-data-freshness-v1", max_age_seconds=300.0,
)


def compute_market_data_digest(
    bar: MarketBar, *, normalization_version: str,
) -> str:
    """Deterministic digest over every field that determines what this bar
    asserts about the market. Binds source/symbol/interval/trading_date/
    timestamps/OHLCV/normalization version, using the repository's one
    canonical hashing convention (`canonical_bytes`, already used by
    `src/protection_observation_model.py` and, transitively, `src/
    execution_safety_governance.py`) rather than a second hand-rolled
    encoder. Deliberately excludes `receive_timestamp` (when this process
    happened to observe the bar, not a fact about the bar itself) and
    `quality` (the digest identifies the bar's content; quality is this
    module's own judgment ABOUT that content, computed separately and
    recorded alongside, not folded into the content identity)."""
    payload = {
        "provider": bar.source.provider,
        "feed": bar.source.feed,
        "api_version": bar.source.api_version,
        "symbol": bar.symbol,
        "exchange_session": bar.exchange_session,
        "trading_date": bar.trading_date.isoformat(),
        "event_timestamp": bar.event_timestamp.isoformat(),
        "end_timestamp": bar.end_timestamp.isoformat(),
        "open": format(bar.open, "f"),
        "high": format(bar.high, "f"),
        "low": format(bar.low, "f"),
        "close": format(bar.close, "f"),
        "volume": bar.volume,
        "interval_seconds": bar.interval_seconds,
        "provider_sequence_id": bar.provider_sequence_id or "",
        "normalization_version": normalization_version,
    }
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00bar\x00" + canonical_bytes(payload)
    ).hexdigest()


def compute_market_data_batch_digest(digests: Sequence[str]) -> str:
    """Deterministic digest over an ordered sequence of already-computed
    per-bar digests (not the bars themselves again -- each bar's own digest
    already binds its full content, so this only needs to bind the set and
    order of bars that make up the batch)."""
    payload = {"bar_digests": list(digests)}
    return hashlib.sha256(
        _DIGEST_DOMAIN + b"\x00batch\x00" + canonical_bytes(payload)
    ).hexdigest()


def _classify_structural_defects(bar: MarketBar) -> MarketDataQuality | None:
    """Defects intrinsic to one bar, independent of any other bar or of
    time. Returns None if none apply."""
    if bar.high < bar.low:
        return MarketDataQuality.INVALID_OHLC
    if not (bar.low <= bar.open <= bar.high):
        return MarketDataQuality.INVALID_OHLC
    if not (bar.low <= bar.close <= bar.high):
        return MarketDataQuality.INVALID_OHLC
    if bar.open <= 0 or bar.high <= 0 or bar.low <= 0 or bar.close <= 0:
        return MarketDataQuality.NEGATIVE_PRICE
    if bar.volume < 0:
        return MarketDataQuality.INVALID_VOLUME
    if bar.end_timestamp <= bar.event_timestamp:
        return MarketDataQuality.MALFORMED_TIMESTAMP
    return None


def validate_market_bar(
    bar: MarketBar,
    *,
    expected_symbol: str,
    expected_trading_date: date,
    expected_interval_seconds: int,
    freshness_policy: MarketDataFreshnessPolicy,
    now: datetime,
) -> MarketDataQuality:
    """The fail-closed per-bar quality gate. Every branch below returns a
    specific rejection reason; nothing here ever repairs or substitutes a
    value. Order is cheapest-and-most-specific first, but no check is
    skipped on the strength of another passing."""
    _require_aware(now, label="now")

    structural = _classify_structural_defects(bar)
    if structural is not None:
        return structural

    if bar.symbol != expected_symbol.upper().strip():
        return MarketDataQuality.WRONG_SYMBOL
    if bar.trading_date != expected_trading_date:
        return MarketDataQuality.WRONG_TRADING_DATE
    if bar.interval_seconds != expected_interval_seconds:
        return MarketDataQuality.WRONG_INTERVAL

    if bar.event_timestamp > now:
        return MarketDataQuality.FUTURE_TIMESTAMP
    age_seconds = (now - bar.event_timestamp).total_seconds()
    if age_seconds > freshness_policy.max_age_seconds:
        return MarketDataQuality.STALE

    bounds = regular_session_bounds(bar.trading_date)
    if bounds is None:
        return MarketDataQuality.MARKET_HOLIDAY
    session_open, session_close = bounds
    # Compare in the same wall-clock frame regular_session_bounds uses
    # (America/New_York); event_timestamp is required tz-aware but may be
    # in any zone, so convert explicitly rather than trusting the caller's
    # zone choice.
    bar_time_local = bar.event_timestamp.astimezone(EASTERN).time()
    if not (session_open <= bar_time_local < session_close):
        return MarketDataQuality.OUTSIDE_SESSION

    return MarketDataQuality.VALID


def validate_market_data_batch(
    bars: Sequence[MarketBar],
) -> dict[int, MarketDataQuality]:
    """Cross-bar checks that no single bar can express on its own:
    duplicate/conflicting timestamps, out-of-order arrival, prohibited
    overlap, and mixed provider identity within what is supposed to be one
    batch. Returns `{index in bars: override quality}` for every bar this
    check demotes, keyed by position (not by the bar object itself, so two
    bars that happen to be field-for-field equal are never conflated); an
    index absent from the mapping is unaffected by this pass. The caller is
    expected to have already run `validate_market_bar` per bar and to fold
    both results (e.g. worst-of) before treating a bar as usable."""
    overrides: dict[int, MarketDataQuality] = {}
    if not bars:
        return overrides

    sources = {(bar.source.provider, bar.source.feed, bar.source.api_version) for bar in bars}
    if len(sources) > 1:
        return {index: MarketDataQuality.MIXED_PROVIDER_IDENTITY for index in range(len(bars))}

    seen_timestamps: dict[datetime, int] = {}
    previous_timestamp: datetime | None = None
    for index, bar in enumerate(bars):
        if previous_timestamp is not None and bar.event_timestamp < previous_timestamp:
            overrides.setdefault(index, MarketDataQuality.OUT_OF_ORDER)
        previous_timestamp = bar.event_timestamp
        existing_index = seen_timestamps.get(bar.event_timestamp)
        if existing_index is None:
            seen_timestamps[bar.event_timestamp] = index
            continue
        existing = bars[existing_index]
        if (
            existing.open == bar.open and existing.high == bar.high
            and existing.low == bar.low and existing.close == bar.close
            and existing.volume == bar.volume
        ):
            # Exact duplicate: deterministic, not an error -- both copies
            # keep whatever quality validate_market_bar already gave them.
            continue
        overrides[index] = MarketDataQuality.DUPLICATE_CONFLICTING
        overrides[existing_index] = MarketDataQuality.DUPLICATE_CONFLICTING

    order_by_time = sorted(range(len(bars)), key=lambda index: bars[index].event_timestamp)
    previous_index: int | None = None
    for index in order_by_time:
        if previous_index is not None:
            same_start_as_previous = (
                bars[index].event_timestamp == bars[previous_index].event_timestamp
            )
            # A shared start time is already fully adjudicated by the
            # duplicate-detection pass above (either DUPLICATE_CONFLICTING
            # or an accepted exact duplicate) -- that is not the kind of
            # overlap this check exists to catch, which is two bars with
            # genuinely DIFFERENT start times whose [event_timestamp,
            # end_timestamp) windows still overlap.
            if (
                not same_start_as_previous
                and bars[index].event_timestamp < bars[previous_index].end_timestamp
            ):
                overrides.setdefault(index, MarketDataQuality.OVERLAPPING_PROHIBITED)
        previous_index = index

    return overrides


def session_open_for(trading_date: date) -> time:
    """The authoritative session open for `trading_date`, derived from
    `src.exchange_calendar.regular_session_bounds` rather than a hardcoded
    constant. Raises if the date is not a trading day -- callers computing
    an opening range for a non-trading day have already made a mistake
    upstream (the quality gate above independently catches this too, via
    MARKET_HOLIDAY, for data that arrives despite that mistake)."""
    bounds = regular_session_bounds(trading_date)
    if bounds is None:
        raise MarketDataError(f"{trading_date.isoformat()} is not a trading day")
    return bounds[0]


__all__ = [
    "MarketDataError",
    "MarketDataQuality",
    "ACCEPTABLE_QUALITY",
    "MarketDataSourceIdentity",
    "MarketBar",
    "MarketDataFreshnessPolicy",
    "DEFAULT_FRESHNESS_POLICY",
    "compute_market_data_digest",
    "compute_market_data_batch_digest",
    "validate_market_bar",
    "validate_market_data_batch",
    "session_open_for",
]
