"""Tests for the injectable `session_bounds_provider` seam added to
`src/shadow_execution_simulator.py` (Issue #311, enabling-milestone
following the identical Issue #305/PR #306 pattern for
`src/market_data_canonical.py`).

Root cause this proves fixed: `_validated_bars` called `validate_market_bar`
without forwarding a calendar provider, so any subsequent bar on a date
outside `src.exchange_calendar`'s own 2026-2027 coverage (e.g. any 2025
date) was classified MARKET_HOLIDAY -> the whole simulation returned
DATA_INVALID and was silently dropped from "closed trades." This already
corrupted the merged Issue #304/PR #307 out-of-sample study's trade-quality
numbers (see Issue #311) -- this test file proves the fix directly against
that exact failure mode, not just the seam's shape in the abstract.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.exchange_calendar import EASTERN, regular_session_bounds
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_research_historical_calendar import research_regular_session_bounds
from src.shadow_execution_simulator import ShadowExecutionState, simulate_shadow_execution
from src.shadow_trading_decision import ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

# A date within src.exchange_calendar's own 2026-2027 coverage, for the
# "default behavior unchanged" checks.
SUPPORTED_DAY = date(2026, 8, 11)
SUPPORTED_SIGNAL = datetime(2026, 8, 11, 14, 40, tzinfo=timezone.utc)

# A real 2025 trading day outside that coverage -- exactly the class of
# date that previously always resolved to DATA_INVALID.
HISTORICAL_DAY = date(2025, 4, 3)
HISTORICAL_SIGNAL = datetime(2025, 4, 3, 14, 40, tzinfo=timezone.utc)


def _decision(*, trading_date: date, signal: datetime, entry=Decimal("100"), stop=Decimal("99")) -> ShadowTradingDecision:
    digest = compute_shadow_decision_digest(
        shadow_run_id="calendar-seam-fixture", symbol="AAPL", trading_date=trading_date,
        strategy_version="orb_long_v0-0.1.0", implementation_digest="a" * 64,
        market_data_digest="b" * 64, opening_range_identity="c" * 64,
        signal_timestamp=signal, decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=entry, proposed_stop=stop, proposed_target=None, reason="fixture",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    return ShadowTradingDecision(
        shadow_run_id="calendar-seam-fixture", decision_id=digest, symbol="AAPL", trading_date=trading_date,
        strategy_version="orb_long_v0-0.1.0", implementation_digest="a" * 64,
        market_data_digest="b" * 64, opening_range_identity="c" * 64,
        signal_timestamp=signal, decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=entry, proposed_stop=stop, proposed_target=None,
        reason="fixture", market_data_quality=MarketDataQuality.VALID,
        exchange_session="REGULAR", created_at=signal,
    )


def _bar(*, trading_date: date, signal: datetime, minute: int, open_="100", high="100.5", low="99.5", close="100") -> MarketBar:
    timestamp = signal + timedelta(minutes=minute)
    return MarketBar(
        symbol="AAPL", exchange_session="REGULAR", trading_date=trading_date,
        event_timestamp=timestamp, end_timestamp=timestamp + timedelta(minutes=1),
        receive_timestamp=timestamp + timedelta(seconds=1), open=Decimal(open_),
        high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=100,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None,
        quality=MarketDataQuality.VALID,
    )


# ---------------------------------------------------------------------------
# The actual bug, proven fixed.
# ---------------------------------------------------------------------------


def test_2025_trade_was_previously_data_invalid_with_default_provider():
    """Documents the exact failure mode found in Issue #311: with the
    default (production-only) provider, a real 2025 trade never resolves
    to a real outcome -- it is always DATA_INVALID, regardless of the bars
    given."""
    decision = _decision(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL)
    bars = [
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=1),
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=2, low="98.9", high="100.2"),
    ]
    result = simulate_shadow_execution(decision, bars)
    assert result.state is ShadowExecutionState.DATA_INVALID
    assert result.reason_code == MarketDataQuality.MARKET_HOLIDAY.value


def test_2025_trade_simulates_for_real_when_research_provider_supplied():
    """The fix: with the research-only historical provider, the same 2025
    candidate and bars now proceed through real simulation (STOPPED here,
    given the bar shape), not DATA_INVALID."""
    decision = _decision(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL)
    bars = [
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=1),
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=2, low="98.9", high="100.2"),
    ]
    result = simulate_shadow_execution(decision, bars, session_bounds_provider=research_regular_session_bounds)
    assert result.state is ShadowExecutionState.STOPPED
    assert result.realized_pnl == Decimal("-1")


# ---------------------------------------------------------------------------
# 1. Omitted provider == current production behavior.
# 2. Explicit regular_session_bounds == omitted provider.
# ---------------------------------------------------------------------------


def _supported_day_bars():
    return [
        _bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=1),
        _bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=2, low="98.9", high="100.2"),
    ]


def test_omitted_provider_matches_current_production_behavior():
    decision = _decision(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL)
    result = simulate_shadow_execution(decision, _supported_day_bars())
    assert result.state is ShadowExecutionState.STOPPED
    assert result.realized_pnl == Decimal("-1")


def test_explicit_regular_session_bounds_identical_to_omitted():
    decision = _decision(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL)
    omitted = simulate_shadow_execution(decision, _supported_day_bars())
    explicit = simulate_shadow_execution(decision, _supported_day_bars(), session_bounds_provider=regular_session_bounds)
    assert omitted.state == explicit.state
    assert omitted.realized_pnl == explicit.realized_pnl
    assert omitted.simulation_digest == explicit.simulation_digest


# ---------------------------------------------------------------------------
# 3. Injected provider affects only that specific call.
# 4. No process/global state leaks.
# ---------------------------------------------------------------------------


def test_injected_provider_does_not_leak_to_a_later_call_without_one():
    historical_decision = _decision(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL)
    historical_bars = [
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=1),
        _bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=2, low="98.9", high="100.2"),
    ]
    with_provider = simulate_shadow_execution(historical_decision, historical_bars, session_bounds_provider=research_regular_session_bounds)
    assert with_provider.state is ShadowExecutionState.STOPPED

    # A subsequent, unrelated 2025 call with NO provider must still fail
    # closed exactly as before -- the prior call's provider must not have
    # leaked into any shared/global state.
    without_provider = simulate_shadow_execution(historical_decision, historical_bars)
    assert without_provider.state is ShadowExecutionState.DATA_INVALID

    # And a same-process supported-day call is unaffected either way.
    supported_decision = _decision(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL)
    supported_result = simulate_shadow_execution(supported_decision, _supported_day_bars())
    assert supported_result.state is ShadowExecutionState.STOPPED


# ---------------------------------------------------------------------------
# 5. Provider reporting a closed/non-session day still fails closed.
# 6. Invalid historical bars are not silently accepted.
# ---------------------------------------------------------------------------


def test_provider_reporting_closed_day_still_fails_closed():
    def _always_closed(_day: date) -> tuple[time, time] | None:
        return None

    decision = _decision(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL)
    bars = [_bar(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL, minute=1)]
    result = simulate_shadow_execution(decision, bars, session_bounds_provider=_always_closed)
    assert result.state is ShadowExecutionState.DATA_INVALID
    assert result.reason_code == MarketDataQuality.MARKET_HOLIDAY.value


def test_provider_still_rejects_structurally_invalid_bars():
    """A provider that reports the day open does not bypass ordinary bar
    validity checks (e.g. a bar dated for a different symbol/date)."""
    decision = _decision(trading_date=HISTORICAL_DAY, signal=HISTORICAL_SIGNAL)
    wrong_date_bar = _bar(trading_date=date(2025, 4, 4), signal=HISTORICAL_SIGNAL, minute=1)
    result = simulate_shadow_execution(decision, [wrong_date_bar], session_bounds_provider=research_regular_session_bounds)
    assert result.state is ShadowExecutionState.DATA_INVALID
    assert result.reason_code == MarketDataQuality.WRONG_TRADING_DATE.value


# ---------------------------------------------------------------------------
# 7. Production/live files never inject session_bounds_provider.
# ---------------------------------------------------------------------------


PRODUCTION_FILES = (
    "read_only_observation_checkpoint_launcher.py",
    "src/live_shadow_session.py",
    "src/bounded_unattended_orchestrator.py",
    "src/shadow_candidate_commissioning_review.py",
)


@pytest.mark.parametrize("relative_path", PRODUCTION_FILES)
def test_no_production_file_passes_session_bounds_provider(relative_path):
    full_path = REPOSITORY_ROOT / relative_path
    if not full_path.exists():
        pytest.skip(f"{relative_path} not present")
    source = full_path.read_text(encoding="utf-8")
    assert "session_bounds_provider=" not in source


def test_default_parameter_is_the_real_production_function():
    import inspect

    from src.shadow_execution_simulator import simulate_shadow_execution as fn

    signature = inspect.signature(fn)
    assert signature.parameters["session_bounds_provider"].default is regular_session_bounds


# ---------------------------------------------------------------------------
# 8. Existing simulator digest/result behavior unchanged for existing
#    supported-calendar fixtures when provider is omitted.
# ---------------------------------------------------------------------------


def test_existing_supported_calendar_fixtures_unchanged_when_provider_omitted():
    """Every state transition already covered by
    tests/test_shadow_execution_simulator.py -- re-asserted here narrowly
    to prove the new parameter changed nothing about those outcomes."""
    stopped = simulate_shadow_execution(
        _decision(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL),
        [_bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=1),
         _bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=2, low="98.9", high="100.2")],
    )
    assert stopped.state is ShadowExecutionState.STOPPED
    assert stopped.exit_price == Decimal("99") and stopped.realized_pnl == Decimal("-1")

    targeted = simulate_shadow_execution(
        _decision(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL),
        [_bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=1),
         _bar(trading_date=SUPPORTED_DAY, signal=SUPPORTED_SIGNAL, minute=2, open_="100.2", low="100.1", high="102.1", close="100.2")],
    )
    assert targeted.state is ShadowExecutionState.TARGET_HIT
    assert targeted.exit_price == Decimal("102") and targeted.realized_pnl == Decimal("2")
