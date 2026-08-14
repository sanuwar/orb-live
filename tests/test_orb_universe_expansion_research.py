from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.market_data import MinuteBar
from src.market_data_canonical import session_open_for
from src.orb_universe_expansion_research import (
    BREAKOUT_ELIMINATED,
    CANDIDATE,
    DATA_INCOMPLETE,
    GAP_ELIMINATED,
    RVOL_ELIMINATED,
    SPY_VWAP_ELIMINATED,
    UNIVERSE_POOL,
    UniverseResearchError,
    build_study_report,
    cache_key,
    csv_summary_rows,
    evaluate_symbol_session,
    incremental_symbols,
    liquid_universe,
    production_universe,
    trading_sessions_before,
)

ET = ZoneInfo("America/New_York")


# ---------------------------------------------------------------------------
# Universe construction.
# ---------------------------------------------------------------------------


def test_liquid_universe_is_deterministic_and_ordered_prefix():
    small = liquid_universe(15)
    large = liquid_universe(50)
    assert small == UNIVERSE_POOL[:15]
    assert large[:15] == small
    assert len(large) == 50
    assert len(set(large)) == 50  # no duplicates


def test_liquid_universe_tier_one_matches_production():
    assert liquid_universe(15) == production_universe()


def test_liquid_universe_rejects_out_of_range_size():
    with pytest.raises(UniverseResearchError):
        liquid_universe(0)
    with pytest.raises(UniverseResearchError):
        liquid_universe(len(UNIVERSE_POOL) + 1)


def test_incremental_symbols_is_the_added_tail():
    added = incremental_symbols(smaller_size=15, larger_size=25)
    assert added == UNIVERSE_POOL[15:25]
    assert len(added) == 10


def test_incremental_symbols_rejects_non_increasing_sizes():
    with pytest.raises(UniverseResearchError):
        incremental_symbols(smaller_size=25, larger_size=25)


def test_universe_pool_excludes_spy_and_duplicate_share_classes():
    assert "SPY" not in UNIVERSE_POOL
    assert "GOOG" not in UNIVERSE_POOL  # GOOGL already represents Alphabet


# ---------------------------------------------------------------------------
# Trading calendar.
# ---------------------------------------------------------------------------


def test_trading_sessions_before_skips_weekend_and_orders_oldest_first():
    # 2026-08-17 is a Monday; the 3 sessions before it must be Wed/Thu/Fri
    # of the prior week, in ascending order.
    sessions = trading_sessions_before(date(2026, 8, 17), count=3)
    assert sessions == (date(2026, 8, 12), date(2026, 8, 13), date(2026, 8, 14))


def test_trading_sessions_before_rejects_non_positive_count():
    with pytest.raises(UniverseResearchError):
        trading_sessions_before(date(2026, 8, 17), count=0)


# ---------------------------------------------------------------------------
# Canonical per-session evaluation -- built from synthetic bars only, no
# network access, matching this repository's injected-effects convention.
# ---------------------------------------------------------------------------


def _bar(symbol, moment, *, open_, high, low, close, volume):
    return MinuteBar(
        symbol=symbol, begins_at=moment, open_price=Decimal(open_), high_price=Decimal(high),
        low_price=Decimal(low), close_price=Decimal(close), volume=volume, session="REGULAR",
        interpolated=False,
    )


def _historical_bars(symbol, session_date, *, lookback_days, volume):
    """20 prior sessions' opening 5-minute bars, giving a stable median
    opening volume and a fixed previous close."""
    bars = []
    cursor = session_date - timedelta(days=1)
    produced = 0
    while produced < lookback_days:
        if cursor.weekday() < 5:
            opening = session_open_for(cursor) if cursor.weekday() < 5 else time(9, 30)
            start = datetime.combine(cursor, time(9, 30), tzinfo=ET)
            for offset in range(5):
                bars.append(_bar(symbol, start + timedelta(minutes=offset), open_="100", high="100.5", low="99.5", close="100", volume=volume // 5))
            produced += 1
        cursor -= timedelta(days=1)
    return bars


def _entry_window_bars(symbol, session_date, *, opening_close, breakout_at_minute, opening_volume=600_000):
    """Opening range (09:30-09:35) plus one bar per minute through 11:00.
    `breakout_at_minute` is the offset (minutes after 09:40) at which close
    first exceeds the opening-range high of 102; None means it never does.
    `opening_volume` is per opening-range bar; the default (600k x 5 =
    3,000,000) is exactly 3x the 1,000,000-share historical median produced
    by `_historical_bars(..., volume=1_000_000)`, so RVOL passes unless a
    test deliberately overrides it."""
    start = datetime.combine(session_date, time(9, 30), tzinfo=ET)
    bars = [
        # Gap up ~1.7% from the historical previous close of 100.
        _bar(symbol, start, open_="101.7", high="101.9", low="101.2", close="101.5", volume=opening_volume),
        _bar(symbol, start + timedelta(minutes=1), open_="101.5", high="101.9", low="101.2", close="101.6", volume=opening_volume),
        _bar(symbol, start + timedelta(minutes=2), open_="101.6", high="101.9", low="101.3", close="101.7", volume=opening_volume),
        _bar(symbol, start + timedelta(minutes=3), open_="101.7", high="101.95", low="101.4", close="101.8", volume=opening_volume),
        _bar(symbol, start + timedelta(minutes=4), open_="101.8", high="102", low="101.5", close=str(opening_close), volume=opening_volume),
    ]
    entry_window_start = start + timedelta(minutes=10)  # 09:40
    for minute in range(0, 81):  # 09:40 through 11:00 inclusive
        close = "101.5"
        if breakout_at_minute is not None and minute >= breakout_at_minute:
            close = "102.5"
        bars.append(
            _bar(
                symbol, entry_window_start + timedelta(minutes=minute),
                open_="101.5", high="102.6", low="101.2", close=close, volume=50_000,
            )
        )
    return bars


def _spy_bars(session_date, *, above_vwap: bool):
    start = datetime.combine(session_date, time(9, 30), tzinfo=ET)
    bars = []
    for minute in range(95):  # 09:30 through 11:04
        close = "691" if above_vwap else "688"
        bars.append(_bar("SPY", start + timedelta(minutes=minute), open_="690", high="691.5", low="688.5", close=close, volume=100_000))
    return bars


SESSION_DATE = date(2026, 8, 14)  # a real supported Friday trading day
CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])


def test_evaluate_symbol_session_approves_a_true_orb_candidate():
    candidate_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    result = evaluate_symbol_session(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars,
        spy_bars=spy_bars, config=CONFIG,
    )

    assert result.state == CANDIDATE
    assert result.approved_at is not None
    assert result.gap_percent >= Decimal(str(CONFIG["signal"]["minimum_gap_percent"]))
    assert result.relative_volume >= Decimal(str(CONFIG["signal"]["minimum_relative_volume"]))


def test_evaluate_symbol_session_eliminates_on_gap():
    # Opening range built off a previous close of 100 with today's open
    # essentially flat (~100.2) -- well under the 1.0% gap floor.
    candidate_bars = _historical_bars("MSFT", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("MSFT", SESSION_DATE, opening_close="101.5", breakout_at_minute=None)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    # Force a below-threshold gap by using a flat opening bar directly.
    start = datetime.combine(SESSION_DATE, time(9, 30), tzinfo=ET)
    candidate_bars = [bar for bar in candidate_bars if not (bar.begins_at.date() == SESSION_DATE and bar.begins_at < start + timedelta(minutes=5))]
    candidate_bars += [
        _bar("MSFT", start, open_="100.1", high="100.6", low="99.9", close="100.2", volume=200_000),
        _bar("MSFT", start + timedelta(minutes=1), open_="100.2", high="100.6", low="100", close="100.3", volume=200_000),
        _bar("MSFT", start + timedelta(minutes=2), open_="100.3", high="100.6", low="100.1", close="100.3", volume=200_000),
        _bar("MSFT", start + timedelta(minutes=3), open_="100.3", high="100.6", low="100.2", close="100.4", volume=200_000),
        _bar("MSFT", start + timedelta(minutes=4), open_="100.4", high="100.6", low="100.3", close="100.4", volume=200_000),
    ]

    result = evaluate_symbol_session(
        symbol="MSFT", session_date=SESSION_DATE, candidate_bars=candidate_bars,
        spy_bars=spy_bars, config=CONFIG,
    )

    assert result.state == GAP_ELIMINATED
    assert result.relative_volume is None


def test_evaluate_symbol_session_eliminates_on_rvol():
    # Real gap, but opening-range volume far below the 20-session median.
    candidate_bars = _historical_bars("NVDA", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=5_000_000)
    candidate_bars += _entry_window_bars("NVDA", SESSION_DATE, opening_close="101.5", breakout_at_minute=None)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    result = evaluate_symbol_session(
        symbol="NVDA", session_date=SESSION_DATE, candidate_bars=candidate_bars,
        spy_bars=spy_bars, config=CONFIG,
    )

    assert result.state == RVOL_ELIMINATED
    assert result.gap_percent is not None


def test_evaluate_symbol_session_eliminates_on_spy_vwap():
    candidate_bars = _historical_bars("AMZN", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("AMZN", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=False)

    result = evaluate_symbol_session(
        symbol="AMZN", session_date=SESSION_DATE, candidate_bars=candidate_bars,
        spy_bars=spy_bars, config=CONFIG,
    )

    assert result.state == SPY_VWAP_ELIMINATED


def test_evaluate_symbol_session_eliminates_on_missing_breakout():
    candidate_bars = _historical_bars("META", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("META", SESSION_DATE, opening_close="101.5", breakout_at_minute=None)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    result = evaluate_symbol_session(
        symbol="META", session_date=SESSION_DATE, candidate_bars=candidate_bars,
        spy_bars=spy_bars, config=CONFIG,
    )

    assert result.state == BREAKOUT_ELIMINATED


def test_evaluate_symbol_session_reports_data_incomplete_when_bars_are_missing():
    result = evaluate_symbol_session(
        symbol="TSLA", session_date=SESSION_DATE, candidate_bars=[], spy_bars=[], config=CONFIG,
    )
    assert result.state == DATA_INCOMPLETE
    assert result.approved_at is None


# ---------------------------------------------------------------------------
# Report construction -- pure, deterministic, no I/O.
# ---------------------------------------------------------------------------


def _evaluation(symbol, session_date, state, *, approved_at=None):
    from src.orb_universe_expansion_research import SessionEvaluation
    return SessionEvaluation(
        symbol=symbol, session_date=session_date, state=state,
        gap_percent=Decimal("1.5") if state != DATA_INCOMPLETE else None,
        relative_volume=Decimal("3.2") if state in (CANDIDATE, SPY_VWAP_ELIMINATED, BREAKOUT_ELIMINATED) else None,
        approved_at=approved_at,
    )


def test_build_study_report_counts_candidate_days_and_zero_days():
    day1, day2 = date(2026, 8, 10), date(2026, 8, 11)
    universe = liquid_universe(15)
    evaluations = {(symbol, day1): _evaluation(symbol, day1, GAP_ELIMINATED) for symbol in universe}
    evaluations[(universe[0], day1)] = _evaluation(universe[0], day1, CANDIDATE, approved_at=datetime.combine(day1, time(9, 45), tzinfo=ET))
    evaluations.update({(symbol, day2): _evaluation(symbol, day2, GAP_ELIMINATED) for symbol in universe})

    report = build_study_report(session_dates=(day1, day2), evaluations=evaluations, universe_sizes=(15,), include_production=False)

    top15 = next(item for item in report["universe_reports"] if item["label"] == "top_15")
    assert top15["sessions"] == 2
    assert top15["sessions_with_candidate"] == 1
    assert top15["zero_candidate_sessions"] == 1
    assert top15["total_candidates"] == 1
    assert top15["candidate_day_rate"] == 0.5
    assert top15["funnel"]["symbols_evaluated"] == 30
    assert top15["top_candidate_symbols"][0]["symbol"] == universe[0]


def test_larger_universe_never_reports_fewer_candidates_than_smaller_prefix():
    day = date(2026, 8, 10)
    universe = liquid_universe(25)
    evaluations = {(symbol, day): _evaluation(symbol, day, GAP_ELIMINATED) for symbol in universe}
    # Only a symbol added in the 16-25 tail is a candidate.
    extra_candidate = universe[20]
    evaluations[(extra_candidate, day)] = _evaluation(extra_candidate, day, CANDIDATE, approved_at=datetime.combine(day, time(9, 45), tzinfo=ET))

    report = build_study_report(session_dates=(day,), evaluations=evaluations, universe_sizes=(15, 25), include_production=False)
    counts = {item["label"]: item["total_candidates"] for item in report["universe_reports"]}

    assert counts["top_15"] == 0
    assert counts["top_25"] == 1


def test_csv_summary_rows_matches_report_labels():
    day = date(2026, 8, 10)
    report = build_study_report(session_dates=(day,), evaluations={}, universe_sizes=(15,), include_production=True)
    rows = csv_summary_rows(report)
    labels = {row["label"] for row in rows}
    assert labels == {"current_production", "top_15"}
    assert all(row["sessions"] == 1 for row in rows)


def test_cache_key_is_stable_and_distinguishes_requests():
    start = datetime(2026, 8, 1, tzinfo=ET)
    end = datetime(2026, 8, 2, tzinfo=ET)
    assert cache_key("AAPL", start, end) == cache_key("AAPL", start, end)
    assert cache_key("AAPL", start, end) != cache_key("MSFT", start, end)
