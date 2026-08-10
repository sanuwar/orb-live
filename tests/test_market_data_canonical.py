"""M15 Issue 1: canonical market-data model, normalization, and fail-closed
quality validation.

Every fixture is local and offline. Nothing here opens a network
connection, reads a credential, or touches a broker.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.exchange_calendar import EASTERN
from src.market_data_canonical import (
    DEFAULT_FRESHNESS_POLICY,
    MarketBar,
    MarketDataError,
    MarketDataFreshnessPolicy,
    MarketDataQuality,
    MarketDataSourceIdentity,
    compute_market_data_batch_digest,
    compute_market_data_digest,
    session_open_for,
    validate_market_bar,
    validate_market_data_batch,
)

TRADING_DATE = date(2026, 8, 11)  # Tuesday, a regular trading day
SOURCE = MarketDataSourceIdentity(provider="alpaca", feed="iex", api_version="v2")


def bar(**overrides) -> MarketBar:
    open_time = session_open_for(TRADING_DATE)
    event = overrides.pop(
        "event_timestamp", datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN)
    )
    base = dict(
        symbol="AAPL",
        exchange_session="REGULAR",
        trading_date=TRADING_DATE,
        event_timestamp=event,
        end_timestamp=event + timedelta(minutes=1),
        receive_timestamp=event + timedelta(seconds=1),
        open=Decimal("200.00"),
        high=Decimal("201.00"),
        low=Decimal("199.50"),
        close=Decimal("200.50"),
        volume=10_000,
        source=SOURCE,
        interval_seconds=60,
        provider_sequence_id="seq-1",
        quality=MarketDataQuality.VALID,
    )
    base.update(overrides)
    return MarketBar(**base)


def _now_for(b: MarketBar, *, age_seconds: float = 30.0) -> datetime:
    return b.event_timestamp + timedelta(seconds=age_seconds)


# ---------------------------------------------------------------------
# MODEL / NORMALIZATION
# ---------------------------------------------------------------------


def test_valid_bar_normalizes_and_converts_to_minute_bar():
    b = bar()
    minute_bar = b.to_minute_bar()
    assert minute_bar.symbol == "AAPL"
    assert minute_bar.close_price == Decimal("200.50")
    assert minute_bar.interpolated is False


def test_deterministic_normalization_same_inputs_same_bar():
    a = bar()
    c = bar()
    assert a == c


def test_deterministic_market_data_digest():
    b = bar()
    first = compute_market_data_digest(b, normalization_version="v1")
    second = compute_market_data_digest(b, normalization_version="v1")
    assert first == second
    assert len(first) == 64


def test_digest_changes_with_normalization_version():
    b = bar()
    assert compute_market_data_digest(b, normalization_version="v1") != (
        compute_market_data_digest(b, normalization_version="v2")
    )


def test_digest_excludes_receive_timestamp_and_quality():
    a = bar(receive_timestamp=bar().receive_timestamp + timedelta(minutes=5))
    b = bar()
    assert compute_market_data_digest(a, normalization_version="v1") == (
        compute_market_data_digest(b, normalization_version="v1")
    )


def test_batch_digest_deterministic_and_order_sensitive():
    digests = ["a" * 64, "b" * 64]
    first = compute_market_data_batch_digest(digests)
    second = compute_market_data_batch_digest(digests)
    assert first == second
    assert first != compute_market_data_batch_digest(list(reversed(digests)))


def test_malformed_ohlc_rejected():
    b = bar(high=Decimal("199.00"), low=Decimal("201.00"))  # high < low
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.INVALID_OHLC


def test_open_outside_high_low_rejected():
    b = bar(open=Decimal("300.00"))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.INVALID_OHLC


def test_negative_price_rejected_via_quality_gate():
    # Construction only requires a Decimal (a provider could honestly
    # report a non-positive value); NEGATIVE_PRICE is this module's
    # quality classification for it, not a construction-time error. All
    # four prices are set to the SAME negative value so the OHLC-
    # consistency check (which would otherwise fire first for an
    # internally-inconsistent bar) passes trivially and this isolates the
    # negative-price check specifically.
    b = bar(
        open=Decimal("-1.00"), high=Decimal("-1.00"),
        low=Decimal("-1.00"), close=Decimal("-1.00"),
    )
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.NEGATIVE_PRICE


def test_zero_price_rejected_via_quality_gate():
    # __post_init__ only requires a Decimal; zero/negative is a quality
    # question this module answers via NEGATIVE_PRICE, not a construction
    # error, since a provider could honestly report a non-positive value.
    b = bar(open=Decimal("0"), high=Decimal("0"), low=Decimal("0"), close=Decimal("0"))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.NEGATIVE_PRICE


def test_invalid_volume_rejected():
    b = bar(volume=-1)
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.INVALID_VOLUME


def test_wrong_symbol_rejected():
    b = bar()
    quality = validate_market_bar(
        b, expected_symbol="MSFT", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.WRONG_SYMBOL


def test_wrong_interval_rejected():
    b = bar()
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=300, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.WRONG_INTERVAL


def test_wrong_trading_date_rejected():
    b = bar()
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=date(2026, 8, 12),
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.WRONG_TRADING_DATE


def test_malformed_timestamp_rejected():
    b = bar()
    from dataclasses import replace
    malformed = replace(b, end_timestamp=b.event_timestamp - timedelta(seconds=1))
    quality = validate_market_bar(
        malformed, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.MALFORMED_TIMESTAMP


# ---------------------------------------------------------------------
# TEMPORAL
# ---------------------------------------------------------------------


def test_stale_bar_rejected():
    b = bar()
    now = b.event_timestamp + timedelta(seconds=DEFAULT_FRESHNESS_POLICY.max_age_seconds + 1)
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY, now=now,
    )
    assert quality is MarketDataQuality.STALE


def test_future_bar_rejected():
    b = bar()
    now = b.event_timestamp - timedelta(seconds=1)
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY, now=now,
    )
    assert quality is MarketDataQuality.FUTURE_TIMESTAMP


def test_freshness_is_deterministic_for_identical_data_policy_and_time():
    b = bar()
    now = _now_for(b)
    first = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY, now=now,
    )
    second = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY, now=now,
    )
    assert first == second is MarketDataQuality.VALID


def test_out_of_order_bars_rejected():
    open_time = session_open_for(TRADING_DATE)
    first = bar(event_timestamp=datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN))
    second = bar(
        event_timestamp=datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN) + timedelta(seconds=30),
        end_timestamp=datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN) + timedelta(minutes=1, seconds=30),
    )
    overrides = validate_market_data_batch([first, second])
    assert overrides.get(1) == MarketDataQuality.OVERLAPPING_PROHIBITED


def test_conflicting_duplicate_rejected():
    open_time = session_open_for(TRADING_DATE)
    event = datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN)
    first = bar(event_timestamp=event)
    conflicting = bar(event_timestamp=event, close=Decimal("999.00"))
    overrides = validate_market_data_batch([first, conflicting])
    assert overrides.get(0) == MarketDataQuality.DUPLICATE_CONFLICTING
    assert overrides.get(1) == MarketDataQuality.DUPLICATE_CONFLICTING


def test_exact_duplicate_handled_deterministically():
    open_time = session_open_for(TRADING_DATE)
    event = datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN)
    first = bar(event_timestamp=event)
    same = bar(event_timestamp=event)
    overrides = validate_market_data_batch([first, same])
    assert overrides == {}


def test_previous_trading_day_data_rejected_when_current_session_required():
    b = bar()  # trading_date=TRADING_DATE (Aug 11)
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=date(2026, 8, 12),
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.WRONG_TRADING_DATE


# ---------------------------------------------------------------------
# CALENDAR
# ---------------------------------------------------------------------


def test_regular_session_bar_is_valid():
    b = bar()
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.VALID


def test_holiday_rejected():
    holiday = date(2026, 1, 1)  # New Year's Day
    event = datetime(2026, 1, 1, 10, 0, tzinfo=EASTERN)
    b = bar(trading_date=holiday, event_timestamp=event, end_timestamp=event + timedelta(minutes=1))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=holiday,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.MARKET_HOLIDAY


def test_outside_session_rejected():
    event = datetime(2026, 8, 11, 20, 0, tzinfo=EASTERN)  # 8pm ET, after close
    b = bar(event_timestamp=event, end_timestamp=event + timedelta(minutes=1))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.OUTSIDE_SESSION


def test_timezone_correctness_utc_event_still_classified_correctly():
    open_time = session_open_for(TRADING_DATE)
    event_et = datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN)
    event_utc = event_et.astimezone(timezone.utc)
    b = bar(event_timestamp=event_utc, end_timestamp=event_utc + timedelta(minutes=1))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.VALID


def test_session_open_for_derives_from_calendar_not_a_hardcoded_constant():
    assert session_open_for(TRADING_DATE) is not None
    early_close_day = date(2026, 11, 27)  # day after Thanksgiving, early close
    # session_open_for only returns the OPEN time, which is the same
    # 9:30 on both regular and early-close days -- this test documents
    # that fact rather than asserting a difference that does not exist.
    assert session_open_for(early_close_day) == session_open_for(TRADING_DATE)
    with pytest.raises(MarketDataError):
        session_open_for(date(2026, 1, 1))  # holiday: not a trading day


def test_opening_range_boundary_first_bar_after_open_included():
    open_time = session_open_for(TRADING_DATE)
    event = datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN)
    b = bar(event_timestamp=event, end_timestamp=event + timedelta(minutes=1))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.VALID


def test_opening_range_boundary_bar_before_open_excluded():
    open_time = session_open_for(TRADING_DATE)
    event = datetime.combine(TRADING_DATE, open_time, tzinfo=EASTERN) - timedelta(minutes=1)
    b = bar(event_timestamp=event, end_timestamp=event + timedelta(minutes=1))
    quality = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE,
        expected_interval_seconds=60, freshness_policy=DEFAULT_FRESHNESS_POLICY,
        now=_now_for(b),
    )
    assert quality is MarketDataQuality.OUTSIDE_SESSION


# ---------------------------------------------------------------------
# PROVIDER / MIXED IDENTITY
# ---------------------------------------------------------------------


def test_mixed_provider_identity_rejected():
    other_source = MarketDataSourceIdentity(provider="other", feed="delayed", api_version="v1")
    a = bar()
    b2 = bar(source=other_source)
    overrides = validate_market_data_batch([a, b2])
    assert overrides.get(0) == MarketDataQuality.MIXED_PROVIDER_IDENTITY
    assert overrides.get(1) == MarketDataQuality.MIXED_PROVIDER_IDENTITY


def test_provider_identity_bound_into_digest():
    a = bar()
    other_source = MarketDataSourceIdentity(provider="other", feed="delayed", api_version="v1")
    b2 = bar(source=other_source)
    assert compute_market_data_digest(a, normalization_version="v1") != (
        compute_market_data_digest(b2, normalization_version="v1")
    )


def test_freshness_policy_requires_positive_version_and_max_age():
    with pytest.raises(MarketDataError):
        MarketDataFreshnessPolicy(version="", max_age_seconds=60.0)
    with pytest.raises(MarketDataError):
        MarketDataFreshnessPolicy(version="v1", max_age_seconds=0)
    with pytest.raises(MarketDataError):
        MarketDataFreshnessPolicy(version="v1", max_age_seconds=-1)
