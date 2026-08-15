"""Tests for the injectable `session_bounds_provider` seam added to
`src/market_data_canonical.py` (Issue #305, enabling-milestone for the
out-of-sample validation study). Proves: (1) every existing caller's
default behavior is byte-for-byte unchanged, (2) an alternate provider is
scoped to exactly the call it is passed to, (3) fail-closed behavior holds
for both the default and an injected provider, and (4) no production/live
launcher path ever supplies a non-default provider.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.exchange_calendar import EASTERN, regular_session_bounds
from src.market_data_canonical import (
    MarketBar,
    MarketDataError,
    MarketDataFreshnessPolicy,
    MarketDataQuality,
    MarketDataSourceIdentity,
    session_open_for,
    validate_market_bar,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TRADING_DATE = date(2026, 8, 11)  # Tuesday, a regular trading day
HOLIDAY_DATE = date(2026, 1, 1)  # New Year's Day
SOURCE = MarketDataSourceIdentity(provider="alpaca", feed="iex", api_version="v2")


def _bar(trading_date: date, **overrides) -> MarketBar:
    open_time = session_open_for(trading_date)
    event = overrides.pop("event_timestamp", datetime.combine(trading_date, open_time, tzinfo=EASTERN))
    base = dict(
        symbol="AAPL", exchange_session="REGULAR", trading_date=trading_date, event_timestamp=event,
        end_timestamp=event + timedelta(minutes=1), receive_timestamp=event + timedelta(seconds=1),
        open=Decimal("200.00"), high=Decimal("201.00"), low=Decimal("199.50"), close=Decimal("200.50"),
        volume=10_000, source=SOURCE, interval_seconds=60, provider_sequence_id="seq-1",
        quality=MarketDataQuality.VALID,
    )
    base.update(overrides)
    return MarketBar(**base)


def _fake_provider(day: date) -> tuple[time, time] | None:
    """A trivially different, obviously-not-production provider: every
    weekday is open 10:00-15:00, every weekend closed. Used only to prove
    injection actually changes the answer and stays scoped."""
    if day.weekday() >= 5:
        return None
    return (time(10, 0), time(15, 0))


# ---------------------------------------------------------------------------
# 1. Default behavior matches current production behavior.
# ---------------------------------------------------------------------------


def test_session_open_for_default_matches_regular_session_bounds():
    assert session_open_for(TRADING_DATE) == regular_session_bounds(TRADING_DATE)[0]


def test_session_open_for_default_raises_on_holiday():
    with pytest.raises(MarketDataError):
        session_open_for(HOLIDAY_DATE)


def test_validate_market_bar_default_behavior_unchanged():
    b = _bar(TRADING_DATE)
    outcome = validate_market_bar(
        b, expected_symbol="AAPL", expected_trading_date=TRADING_DATE, expected_interval_seconds=60,
        freshness_policy=MarketDataFreshnessPolicy(version="test-v1", max_age_seconds=120), now=b.event_timestamp + timedelta(seconds=30),
    )
    assert outcome is MarketDataQuality.VALID


# ---------------------------------------------------------------------------
# 2. Explicit regular_session_bounds provider identical to omitted provider.
# ---------------------------------------------------------------------------


def test_session_open_for_explicit_default_provider_identical_to_omitted():
    assert session_open_for(TRADING_DATE) == session_open_for(
        TRADING_DATE, session_bounds_provider=regular_session_bounds,
    )


def test_validate_market_bar_explicit_default_provider_identical_to_omitted():
    b = _bar(TRADING_DATE)
    kwargs = dict(
        expected_symbol="AAPL", expected_trading_date=TRADING_DATE, expected_interval_seconds=60,
        freshness_policy=MarketDataFreshnessPolicy(version="test-v1", max_age_seconds=120), now=b.event_timestamp + timedelta(seconds=30),
    )
    omitted = validate_market_bar(b, **kwargs)
    explicit = validate_market_bar(b, session_bounds_provider=regular_session_bounds, **kwargs)
    assert omitted == explicit


# ---------------------------------------------------------------------------
# 3 & 7. Injected provider changes only that call; fails closed for bad input.
# ---------------------------------------------------------------------------


def test_injected_provider_changes_session_open_for_this_call_only():
    injected_open = session_open_for(TRADING_DATE, session_bounds_provider=_fake_provider)
    assert injected_open == time(10, 0)
    # A subsequent call with no provider is completely unaffected -- no
    # leaked global/module state from the previous injected call.
    assert session_open_for(TRADING_DATE) == time(9, 30)


def test_injected_provider_fails_closed_on_a_day_it_reports_closed():
    saturday = date(2026, 8, 15)
    assert _fake_provider(saturday) is None
    with pytest.raises(MarketDataError):
        session_open_for(saturday, session_bounds_provider=_fake_provider)


def test_validate_market_bar_with_injected_provider_reclassifies_market_holiday():
    b = _bar(TRADING_DATE)
    kwargs = dict(
        expected_symbol="AAPL", expected_trading_date=TRADING_DATE, expected_interval_seconds=60,
        freshness_policy=MarketDataFreshnessPolicy(version="test-v1", max_age_seconds=120), now=b.event_timestamp + timedelta(seconds=30),
    )

    def _always_closed(_day: date) -> tuple[time, time] | None:
        return None

    outcome = validate_market_bar(b, session_bounds_provider=_always_closed, **kwargs)
    assert outcome is MarketDataQuality.MARKET_HOLIDAY
    # And a call with no provider on the same bar is unaffected.
    assert validate_market_bar(b, **kwargs) is MarketDataQuality.VALID


def test_provider_that_raises_propagates_rather_than_silently_accepting():
    def _broken(_day: date) -> tuple[time, time] | None:
        raise RuntimeError("provider is broken")

    with pytest.raises(RuntimeError):
        session_open_for(TRADING_DATE, session_bounds_provider=_broken)


# ---------------------------------------------------------------------------
# 5. Research provider (2025, via the isolated research-only calendar
#    module) can resolve a known valid session through this exact seam.
# ---------------------------------------------------------------------------


def test_research_calendar_provider_resolves_a_known_2025_session_through_the_seam():
    from src.orb_research_historical_calendar import research_regular_session_bounds

    resolved = session_open_for(date(2025, 4, 3), session_bounds_provider=research_regular_session_bounds)
    assert resolved == time(9, 30)


# ---------------------------------------------------------------------------
# 8. No production/live launcher path ever supplies a non-default provider.
# ---------------------------------------------------------------------------


PRODUCTION_ENTRY_POINTS = (
    "read_only_observation_checkpoint_launcher.py",
    "src/live_shadow_session.py",
    "src/bounded_unattended_orchestrator.py",
    "src/shadow_execution_simulator.py",
    "src/shadow_trading_decision.py",
)


@pytest.mark.parametrize("relative_path", PRODUCTION_ENTRY_POINTS)
def test_no_production_file_passes_session_bounds_provider(relative_path):
    full_path = REPOSITORY_ROOT / relative_path
    if not full_path.exists():
        pytest.skip(f"{relative_path} not present")
    source = full_path.read_text(encoding="utf-8")
    assert "session_bounds_provider=" not in source
    assert "session_open_resolver=" not in source


def test_market_data_canonical_default_parameter_is_the_real_production_function():
    import inspect

    signature = inspect.signature(session_open_for)
    assert signature.parameters["session_bounds_provider"].default is regular_session_bounds
