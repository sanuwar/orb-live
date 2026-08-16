"""Tests for the Issue #313 correction of Issue #304/PR #307's out-of-sample
trade-quality results.

Proves, at the level of the actual functions the CLI script calls (not just
the lower-level `shadow_execution_simulator` seam already covered by
`tests/test_shadow_execution_simulator_calendar_seam.py`):

1. Without the research calendar provider, a real 2025 candidate's trade
   quality is silently empty (the exact #307 bug, reproduced one layer up).
2. With `session_bounds_provider=research_regular_session_bounds`, the same
   candidate now produces a real closed trade.
3. `research_orb_out_of_sample_validation.py`'s own source actually wires
   the provider into every place that needed it -- so this cannot silently
   regress back to the #307 bug.
4. Output is deterministic.
5. Production config is untouched.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_out_of_sample_validation_research import (
    ParameterCombo,
    evaluate_combo,
    one_trade_per_day_vs_all_candidates,
    precompute_inputs_table,
    simulate_combo_trade_quality,
)
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "research_orb_out_of_sample_validation.py"

CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])
HISTORICAL_SESSION_DATE = date(2025, 6, 2)  # a real 2025 trading day, outside production calendar coverage
HISTORICAL_LOOKBACK_DATES = research_trading_sessions_before(HISTORICAL_SESSION_DATE, count=LOOKBACK_DAYS)
COMBO = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.25"))


def _mbar(symbol, session_date, hh, mm, *, open_, high, low, close, volume):
    ts = datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=session_date, event_timestamp=ts,
        end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


def _historical_candidate_bars(symbol: str) -> list[MarketBar]:
    """A 2025 candidate that clears gap=0.50%/rvol=1.25x and breaks out,
    then gets stopped -- same fixture shape as
    tests/test_orb_gap_rvol_sensitivity_research.py's synthetic_symbol_bars,
    rebuilt here against 2025 historical lookback dates (research calendar)
    instead of production-calendar-only dates."""
    bars: list[MarketBar] = []
    for prior_date in HISTORICAL_LOOKBACK_DATES:
        for minute in range(5):
            bars.append(_mbar(symbol, prior_date, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))
    today_open = Decimal("100.60")  # 0.60% gap
    opening_bar_volume = int((Decimal("1.30") * Decimal("500000")) / 5)  # 1.30x RVOL
    opening_high = today_open + Decimal("0.5")
    opening_low = today_open - Decimal("0.3")
    for minute in range(5):
        bars.append(_mbar(symbol, HISTORICAL_SESSION_DATE, 9, 30 + minute, open_=str(today_open), high=str(opening_high), low=str(opening_low), close=str(today_open), volume=opening_bar_volume))
    breakout_close = opening_high + Decimal("1")
    bars.append(_mbar(symbol, HISTORICAL_SESSION_DATE, 9, 40, open_=str(today_open), high=str(breakout_close + Decimal("0.5")), low=str(opening_low), close=str(breakout_close), volume=50_000))
    # Entry fills on this bar (low touches the breakout entry level); stop
    # triggers cleanly on the following bar -- matching the same two-bar
    # shape tests/test_orb_gap_rvol_sensitivity_research.py's own
    # synthetic_symbol_bars uses for its trade-quality fixtures.
    bars.append(_mbar(symbol, HISTORICAL_SESSION_DATE, 9, 41, open_=str(breakout_close), high=str(breakout_close + Decimal("0.2")), low=str(breakout_close - Decimal("0.1")), close=str(breakout_close), volume=10_000))
    bars.append(_mbar(symbol, HISTORICAL_SESSION_DATE, 9, 42, open_=str(breakout_close), high=str(breakout_close), low=str(opening_low - Decimal("1")), close=str(opening_low - Decimal("1")), volume=10_000))
    return bars


def _historical_spy_bars() -> list[MarketBar]:
    bars: list[MarketBar] = []
    for session_date in HISTORICAL_LOOKBACK_DATES + (HISTORICAL_SESSION_DATE,):
        for minute in range(20):
            bars.append(_mbar("SPY", session_date, 9, 30 + minute, open_="500", high="501", low="499.5", close="500.5", volume=100_000))
    return bars


def _evaluation():
    candidate_bars = _historical_candidate_bars("AAPL")
    spy_bars = _historical_spy_bars()
    market_bars_by_symbol = {
        "AAPL": candidate_bars,
        "SPY": spy_bars,
    }
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver = lambda day: __import__("src.market_data_canonical", fromlist=["session_open_for"]).session_open_for(
        day, session_bounds_provider=research_regular_session_bounds,
    )
    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[HISTORICAL_SESSION_DATE], minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=CONFIG, session_open_resolver=resolver,
    )
    evaluation = evaluate_combo(
        combo=COMBO, universe=["AAPL"], session_dates=[HISTORICAL_SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    assert len(evaluation.candidate_signals) == 1  # fixture must actually qualify
    return evaluation, market_bars_by_symbol


# ---------------------------------------------------------------------------
# 1 & 2. The bug reproduced and fixed at simulate_combo_trade_quality.
# ---------------------------------------------------------------------------


def test_2025_trade_quality_empty_without_research_provider():
    evaluation, market_bars_by_symbol = _evaluation()
    tq = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol=market_bars_by_symbol, base_config=CONFIG,
        implementation_digest="d" * 64, created_at=datetime.now(timezone.utc),
    )
    assert tq["trades_closed"] == 0
    assert tq["status"] == "NO_CANDIDATES_TO_SIMULATE" or tq["candidates_evaluated"] == 1


def test_2025_trade_quality_real_with_research_provider():
    evaluation, market_bars_by_symbol = _evaluation()
    tq = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol=market_bars_by_symbol, base_config=CONFIG,
        implementation_digest="d" * 64, created_at=datetime.now(timezone.utc),
        session_bounds_provider=research_regular_session_bounds,
    )
    assert tq["trades_closed"] == 1
    assert tq["losers"] == 1  # fixture is built to stop out
    assert tq["status"] == "EVALUATED"


def test_one_trade_per_day_vs_all_candidates_also_fixed():
    evaluation, market_bars_by_symbol = _evaluation()
    result = one_trade_per_day_vs_all_candidates(
        combo=COMBO, evaluation=evaluation, market_bars_by_symbol=market_bars_by_symbol, base_config=CONFIG,
        implementation_digest="d" * 64, created_at=datetime.now(timezone.utc),
        session_bounds_provider=research_regular_session_bounds,
    )
    assert result["ALL_CANDIDATES"]["trade_quality"]["trades_closed"] == 1
    assert result["ONE_TRADE_PER_DAY"]["trade_quality"]["trades_closed"] == 1


# ---------------------------------------------------------------------------
# 3. The script's own source wires the provider into every needed call.
# ---------------------------------------------------------------------------


def test_script_passes_research_provider_into_trade_quality_calls():
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    assert "session_bounds_provider=research_regular_session_bounds" in source
    # Must appear at both call sites, not just one -- a naive fix could
    # patch simulate_combo_trade_quality and silently miss the
    # one-trade-per-day view (or vice versa).
    assert source.count("session_bounds_provider=research_regular_session_bounds") >= 2


# ---------------------------------------------------------------------------
# 4. Deterministic output.
# ---------------------------------------------------------------------------


def test_corrected_trade_quality_is_deterministic():
    evaluation, market_bars_by_symbol = _evaluation()
    created_at = datetime.now(timezone.utc)
    first = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol=market_bars_by_symbol, base_config=CONFIG,
        implementation_digest="d" * 64, created_at=created_at, session_bounds_provider=research_regular_session_bounds,
    )
    second = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol=market_bars_by_symbol, base_config=CONFIG,
        implementation_digest="d" * 64, created_at=created_at, session_bounds_provider=research_regular_session_bounds,
    )
    assert first == second


# ---------------------------------------------------------------------------
# 5. Production config untouched.
# ---------------------------------------------------------------------------


def test_production_config_unchanged():
    assert CONFIG["strategy"]["enabled"] is False
    assert float(CONFIG["signal"]["minimum_gap_percent"]) == 1.0
    assert float(CONFIG["signal"]["minimum_relative_volume"]) == 3.0
