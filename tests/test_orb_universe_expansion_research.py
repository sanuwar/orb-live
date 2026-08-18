import json
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.config import load_config
from src.market_data import MinuteBar
from src.market_data_canonical import session_open_for
from src.market_data_provider import ALPACA_IEX_SOURCE, AlpacaCredentials
from src.orb_universe_expansion_research import (
    BELOW_MINIMUM_DOLLAR_VOLUME,
    BELOW_MINIMUM_PRICE,
    BELOW_MINIMUM_SHARE_VOLUME,
    BREAKOUT_ELIMINATED,
    CANDIDATE,
    DATA_INCOMPLETE,
    DEFAULT_LIQUIDITY_THRESHOLDS,
    DEFAULT_RVOL_WARMUP_MULTIPLIER,
    GAP_ELIMINATED,
    INSUFFICIENT_OPENING_RANGE_COVERAGE,
    INSUFFICIENT_RVOL_HISTORY_COVERAGE,
    INSUFFICIENT_RVOL_LOOKBACK_HISTORY,
    INVALID_INSUFFICIENT_COVERAGE,
    MINIMUM_COVERAGE,
    MISSING_HISTORICAL_PRIOR_CLOSE,
    MISSING_OPENING_RANGE_BARS,
    MISSING_SPY_BARS_FOR_SESSION,
    MISSING_TODAY_BARS,
    NO_HISTORICAL_DATA,
    RVOL_ELIMINATED,
    SPY_VWAP_ELIMINATED,
    UNIVERSE_POOL,
    VALID_COVERAGE,
    IndexedMinuteBars,
    LiquidityThresholds,
    LiquidityTier,
    SessionEvaluation,
    SymbolLiquidityProfile,
    UniverseResearchError,
    build_liquidity_tiers,
    build_study_report,
    cache_key,
    compute_symbol_liquidity_profile,
    csv_summary_rows,
    eligible_minute_bars,
    evaluate_symbol_session,
    fetch_full_minute_history,
    historical_fetch_start,
    incremental_symbols,
    index_minute_bars_by_date,
    liquid_universe,
    precompute_session_signal_inputs,
    production_universe,
    rank_liquidity_qualified_symbols,
    trading_sessions_before,
    validate_cache_integrity,
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
# M43.5 (Issue #338): date-indexed bar retrieval. `index_minute_bars_by_date`
# and `precompute_session_signal_inputs`'s optional `candidate_bars_index`/
# `spy_bars_index` parameters are a faster retrieval path only -- these
# tests prove the indexed path returns exactly what the original
# full-history-rescan path returns, on the same inputs, not an approximation.
# ---------------------------------------------------------------------------


def test_index_minute_bars_by_date_buckets_exactly_by_date_and_preserves_order():
    d1, d2 = date(2026, 8, 10), date(2026, 8, 11)
    start1 = datetime.combine(d1, time(9, 30), tzinfo=ET)
    start2 = datetime.combine(d2, time(9, 30), tzinfo=ET)
    b1a = _bar("AAPL", start1, open_="100", high="100.5", low="99.5", close="100.2", volume=1000)
    b1b = _bar("AAPL", start1 + timedelta(minutes=1), open_="100.2", high="100.6", low="99.8", close="100.3", volume=1000)
    b2a = _bar("AAPL", start2, open_="101", high="101.5", low="100.5", close="101.2", volume=1000)
    bars = [b1a, b1b, b2a]

    index = index_minute_bars_by_date(bars)

    assert index.by_date[d1] == (b1a, b1b)  # exactly the requested date's bars, original relative order
    assert index.by_date[d2] == (b2a,)
    assert index.sorted_dates == (d1, d2)
    assert index.by_date.get(date(2026, 8, 12), ()) == ()  # missing date: same empty-result semantics as a full scan


def test_precompute_session_signal_inputs_index_parity_matches_unindexed_default():
    """The most important parity property: passing a pre-built index must
    never change `precompute_session_signal_inputs`'s result versus the
    original (index=None) full-history scan, on identical bars."""
    candidate_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    reference = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
    )
    indexed = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(candidate_bars),
        spy_bars_index=index_minute_bars_by_date(spy_bars),
    )

    assert indexed == reference
    assert reference.incomplete_reason is None  # sanity: the fixture actually produced a complete evaluation
    assert reference.opening_data is not None


def test_precompute_session_signal_inputs_index_parity_missing_today_bars():
    """A session date with historical bars but nothing on the session date
    itself: MISSING_TODAY_BARS in both paths (missing-date semantics must
    match, not just the populated-date case)."""
    candidate_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    reference = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
    )
    indexed = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(candidate_bars),
        spy_bars_index=index_minute_bars_by_date(spy_bars),
    )

    assert indexed == reference
    assert reference.incomplete_reason == MISSING_OPENING_RANGE_BARS  # no opening-range bars at all on SESSION_DATE


def test_precompute_session_signal_inputs_index_parity_missing_spy_bars():
    candidate_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars: list = []

    reference = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
    )
    indexed = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(candidate_bars),
        spy_bars_index=index_minute_bars_by_date(spy_bars),
    )

    assert indexed == reference
    assert reference.incomplete_reason == MISSING_SPY_BARS_FOR_SESSION


def test_precompute_session_signal_inputs_index_multi_symbol_isolation():
    """Two symbols' indexes, built independently, must never leak bars into
    each other's lookups -- each index is scoped to the bars it was built
    from."""
    aapl_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    aapl_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    msft_bars = _historical_bars("MSFT", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=2_000_000)
    msft_bars += _entry_window_bars("MSFT", SESSION_DATE, opening_close="101.5", breakout_at_minute=None)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)
    spy_index = index_minute_bars_by_date(spy_bars)

    aapl_result = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=aapl_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(aapl_bars), spy_bars_index=spy_index,
    )
    msft_result = precompute_session_signal_inputs(
        symbol="MSFT", session_date=SESSION_DATE, candidate_bars=msft_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(msft_bars), spy_bars_index=spy_index,
    )

    assert aapl_result.opening_data.symbol == "AAPL"
    assert msft_result.opening_data.symbol == "MSFT"
    assert aapl_result.opening_data.median_opening_volume != msft_result.opening_data.median_opening_volume
    # Every window bar traces back to its own symbol only -- no cross-symbol leakage.
    aapl_window_symbols = {bar.symbol for bar, _live in aapl_result.window}
    msft_window_symbols = {bar.symbol for bar, _live in msft_result.window}
    assert aapl_window_symbols == {"AAPL"}
    assert msft_window_symbols == {"MSFT"}


def test_precompute_session_signal_inputs_index_skips_all_interpolated_prior_date():
    """`historical`'s `previous_close` must be the most recent NON-
    interpolated bar strictly before the session date, even when the
    single closest prior date is entirely interpolated -- the indexed
    backward walk must fall through to the next-most-recent date exactly
    as a full scan across every prior bar would."""
    older_date = SESSION_DATE - timedelta(days=3)
    closer_date = SESSION_DATE - timedelta(days=1)
    older_start = datetime.combine(older_date, time(9, 30), tzinfo=ET)
    closer_start = datetime.combine(closer_date, time(9, 30), tzinfo=ET)

    real_prior_bar = _bar("AAPL", older_start, open_="100", high="100.5", low="99.5", close="97.0", volume=200_000)
    interpolated_bar = MinuteBar(
        symbol="AAPL", begins_at=closer_start, open_price=Decimal("100"), high_price=Decimal("100.5"),
        low_price=Decimal("99.5"), close_price=Decimal("999.0"), volume=200_000, session="REGULAR", interpolated=True,
    )
    candidate_bars = [real_prior_bar, interpolated_bar]
    candidate_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)

    reference = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
    )
    indexed = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=index_minute_bars_by_date(candidate_bars),
        spy_bars_index=index_minute_bars_by_date(spy_bars),
    )

    assert indexed == reference
    # RVOL lookback fails first (only one real historical session, far short
    # of LOOKBACK_DAYS) -- this fixture's job is only to prove the indexed
    # and unindexed paths make the identical decision, not to reach a full
    # CANDIDATE evaluation.
    assert reference.incomplete_reason == INSUFFICIENT_RVOL_LOOKBACK_HISTORY


def test_precompute_session_signal_inputs_index_parity_deterministic_repeated_execution():
    candidate_bars = _historical_bars("AAPL", SESSION_DATE, lookback_days=LOOKBACK_DAYS, volume=1_000_000)
    candidate_bars += _entry_window_bars("AAPL", SESSION_DATE, opening_close="101.5", breakout_at_minute=5)
    spy_bars = _spy_bars(SESSION_DATE, above_vwap=True)
    candidate_bars_index = index_minute_bars_by_date(candidate_bars)
    spy_bars_index = index_minute_bars_by_date(spy_bars)

    first = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=candidate_bars_index, spy_bars_index=spy_bars_index,
    )
    second = precompute_session_signal_inputs(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=candidate_bars, spy_bars=spy_bars, config=CONFIG,
        candidate_bars_index=candidate_bars_index, spy_bars_index=spy_bars_index,
    )

    assert first == second


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


def test_build_study_report_accepts_named_universes():
    day = date(2026, 8, 10)
    report = build_study_report(
        session_dates=(day,), evaluations={}, universe_sizes=(), include_production=False,
        named_universes={"liquidity_top_23": ("AAPL", "MSFT")},
    )
    labels = {item["label"] for item in report["universe_reports"]}
    assert labels == {"liquidity_top_23"}
    tier = next(item for item in report["universe_reports"] if item["label"] == "liquidity_top_23")
    assert tier["universe"] == ["AAPL", "MSFT"]


def test_cache_key_is_stable_and_distinguishes_requests():
    start = datetime(2026, 8, 1, tzinfo=ET)
    end = datetime(2026, 8, 2, tzinfo=ET)
    assert cache_key("AAPL", start, end) == cache_key("AAPL", start, end)
    assert cache_key("AAPL", start, end) != cache_key("MSFT", start, end)


# ---------------------------------------------------------------------------
# Regression: the funnel-survivor-counting bug found in the first real run
# (54 orb_breakout_survivors but total_candidates=0 for the 100-universe).
# Root cause: each `*_survivors` counter was incremented BEFORE checking
# whether that same gate had eliminated the symbol-day, so every counter
# reported entrants to the next gate rather than survivors of its own gate.
# ---------------------------------------------------------------------------


def test_funnel_survivor_counts_match_actual_gate_outcomes_not_entrants():
    day = date(2026, 8, 10)
    universe = liquid_universe(6)
    states = [
        GAP_ELIMINATED, RVOL_ELIMINATED, SPY_VWAP_ELIMINATED,
        BREAKOUT_ELIMINATED, BREAKOUT_ELIMINATED, CANDIDATE,
    ]
    evaluations = {}
    for symbol, state in zip(universe, states):
        approved_at = datetime.combine(day, time(9, 45), tzinfo=ET) if state == CANDIDATE else None
        evaluations[(symbol, day)] = _evaluation(symbol, day, state, approved_at=approved_at)

    report = build_study_report(session_dates=(day,), evaluations=evaluations, universe_sizes=(6,), include_production=False)
    universe_report = next(item for item in report["universe_reports"] if item["label"] == "top_6")
    funnel = universe_report["funnel"]

    assert funnel["symbols_evaluated"] == 6
    assert funnel["gap_survivors"] == 5           # everyone except the 1 gap-eliminated symbol-day
    assert funnel["opening_rvol_survivors"] == 4  # everyone except gap+rvol eliminated
    assert funnel["spy_vwap_survivors"] == 3      # everyone except gap+rvol+spy eliminated
    # This is the exact invariant the original bug violated: breakout
    # survivors must equal the actual candidate count, not include the 2
    # BREAKOUT_ELIMINATED symbol-days that reached but failed this gate.
    assert funnel["orb_breakout_survivors"] == 1
    assert funnel["orb_breakout_survivors"] == universe_report["total_candidates"]


def test_report_flags_insufficient_coverage_below_threshold():
    day = date(2026, 8, 10)
    universe = liquid_universe(10)
    # Only 1 of 10 possible symbol-days is evaluable (10% complete).
    evaluations = {(universe[0], day): _evaluation(universe[0], day, GAP_ELIMINATED)}

    report = build_study_report(session_dates=(day,), evaluations=evaluations, universe_sizes=(10,), include_production=False)
    coverage = report["universe_reports"][0]["coverage"]

    assert coverage["possible_symbol_days"] == 10
    assert coverage["complete_symbol_days"] == 1
    assert coverage["completeness"] == 0.1
    assert coverage["status"] == INVALID_INSUFFICIENT_COVERAGE


def test_report_flags_valid_coverage_at_or_above_threshold():
    day1, day2 = date(2026, 8, 10), date(2026, 8, 11)
    universe = liquid_universe(10)
    evaluations = {}
    for session_date in (day1, day2):
        for symbol in universe:
            evaluations[(symbol, session_date)] = _evaluation(symbol, session_date, GAP_ELIMINATED)
    # Drop exactly 1 of 20 possible symbol-days -> 95% complete, at the floor.
    del evaluations[(universe[0], day1)]

    report = build_study_report(session_dates=(day1, day2), evaluations=evaluations, universe_sizes=(10,), include_production=False)
    coverage = report["universe_reports"][0]["coverage"]

    assert coverage["completeness"] == MINIMUM_COVERAGE
    assert coverage["status"] == VALID_COVERAGE


def test_incomplete_reason_breakdown_is_recorded_by_reason_symbol_and_session():
    from src.orb_universe_expansion_research import MISSING_TODAY_BARS

    day = date(2026, 8, 10)
    universe = liquid_universe(3)
    evaluations = {
        (universe[0], day): SessionEvaluation(
            symbol=universe[0], session_date=day, state=DATA_INCOMPLETE,
            gap_percent=None, relative_volume=None, approved_at=None,
            incomplete_reason=MISSING_TODAY_BARS,
        ),
        (universe[1], day): _evaluation(universe[1], day, GAP_ELIMINATED),
        # universe[2] left unevaluated entirely.
    }

    report = build_study_report(session_dates=(day,), evaluations=evaluations, universe_sizes=(3,), include_production=False)
    coverage = report["universe_reports"][0]["coverage"]

    assert coverage["data_incomplete_by_reason"] == {"MISSING_TODAY_BARS": 1, "MISSING_EVALUATION": 1}
    assert coverage["data_incomplete_by_symbol"] == {universe[0]: 1, universe[2]: 1}
    assert coverage["data_incomplete_by_session"] == {day.isoformat(): 2}


# ---------------------------------------------------------------------------
# Regression: DATA_INCOMPLETE dominance in the first real run. Root cause:
# `AlpacaMarketDataProvider.fetch_minute_bars` returns only the first
# 10,000-bar page and discards `next_page_token`, so a 90+ session request
# silently returned only its oldest ~26 trading days. These tests exercise
# `fetch_full_minute_history`'s pagination loop with a fake HTTP client --
# no live network call.
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _FakePaginatingHttpClient:
    def __init__(self, pages_by_token):
        self._pages_by_token = pages_by_token
        self.requested_tokens: list = []

    async def get(self, url, *, headers, params):
        token = params.get("page_token")
        self.requested_tokens.append(token)
        return _FakeResponse(200, self._pages_by_token[token])


def test_fetch_full_minute_history_follows_pagination_cursor_across_pages():
    import asyncio
    from datetime import timezone as tz

    page_one = {
        "bars": [{"t": "2026-06-01T13:30:00Z", "o": "100", "h": "101", "l": "99", "c": "100.5", "v": 1000}],
        "next_page_token": "tok1", "symbol": "AAPL",
    }
    page_two = {
        "bars": [{"t": "2026-06-01T13:31:00Z", "o": "100.5", "h": "101", "l": "99", "c": "100.6", "v": 900}],
        "next_page_token": None, "symbol": "AAPL",
    }
    client = _FakePaginatingHttpClient({None: page_one, "tok1": page_two})
    credentials = AlpacaCredentials(api_key_id="key", api_secret_key="secret")

    bars = asyncio.run(fetch_full_minute_history(
        client, credentials=credentials, symbol="AAPL",
        start=datetime(2026, 6, 1, tzinfo=tz.utc), end=datetime(2026, 6, 2, tzinfo=tz.utc),
        now=lambda: datetime(2026, 6, 2, tzinfo=tz.utc),
    ))

    assert client.requested_tokens == [None, "tok1"]
    assert [bar.close for bar in bars] == [Decimal("100.5"), Decimal("100.6")]


def test_fetch_full_minute_history_guards_against_runaway_pagination():
    import asyncio
    from datetime import timezone as tz

    class _InfiniteClient:
        async def get(self, url, *, headers, params):
            return _FakeResponse(200, {"bars": [], "next_page_token": "always-more", "symbol": "AAPL"})

    credentials = AlpacaCredentials(api_key_id="key", api_secret_key="secret")

    with pytest.raises(UniverseResearchError):
        asyncio.run(fetch_full_minute_history(
            _InfiniteClient(), credentials=credentials, symbol="AAPL",
            start=datetime(2026, 6, 1, tzinfo=tz.utc), end=datetime(2026, 6, 2, tzinfo=tz.utc),
            now=lambda: datetime(2026, 6, 2, tzinfo=tz.utc),
        ))


def test_eligible_minute_bars_drops_invalid_quality_bars():
    from datetime import timezone as tz
    from src.market_data_canonical import MarketBar, MarketDataQuality

    trading_date = date(2026, 8, 14)
    now = datetime(2026, 8, 14, 20, tzinfo=tz.utc)

    def _market_bar(minute_offset, *, volume):
        moment = datetime(2026, 8, 14, 13, 30, tzinfo=tz.utc) + timedelta(minutes=minute_offset)
        return MarketBar(
            symbol="AAPL", exchange_session="REGULAR", trading_date=trading_date,
            event_timestamp=moment, end_timestamp=moment + timedelta(minutes=1),
            receive_timestamp=now, open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
            close=Decimal("100.5"), volume=volume, source=ALPACA_IEX_SOURCE, interval_seconds=60,
            provider_sequence_id=None, quality=MarketDataQuality.VALID,
        )

    valid_bar = _market_bar(0, volume=1000)
    invalid_bar = _market_bar(1, volume=-5)  # structurally invalid: negative volume

    eligible = eligible_minute_bars([valid_bar, invalid_bar], symbol="AAPL", now=now)

    assert len(eligible) == 1
    assert eligible[0].volume == 1000


def test_validate_cache_integrity_removes_corrupt_entries_but_keeps_truncated_pages(tmp_path):
    cache_dir = tmp_path / "bar-cache"
    cache_dir.mkdir()
    (cache_dir / "valid_truncated.json").write_text(
        json.dumps({
            "bars": [{"t": "2026-06-01T13:30:00Z", "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}],
            "next_page_token": "tok1", "symbol": "AAPL",
        }),
        encoding="utf-8",
    )
    (cache_dir / "missing_bars_key.json").write_text(json.dumps({"symbol": "AAPL"}), encoding="utf-8")
    (cache_dir / "corrupt.json").write_text("{not valid json", encoding="utf-8")

    result = validate_cache_integrity(cache_dir)

    assert result["kept"] == ["valid_truncated.json"]
    assert set(result["removed"]) == {"missing_bars_key.json", "corrupt.json"}
    assert (cache_dir / "valid_truncated.json").exists()
    assert not (cache_dir / "missing_bars_key.json").exists()
    assert not (cache_dir / "corrupt.json").exists()


# ---------------------------------------------------------------------------
# Diagnosis of the operator's second real run (87.56% -> 51.24% coverage by
# universe size, still below the 95% floor): raw-vs-eligible comparison
# against the operator's real post-pagination-fix cache showed the missing
# opening-range minutes are ABSENT IN ALPACA'S RAW IEX RESPONSE ITSELF (not
# filtered out by `eligible_minute_bars`, not a timezone bug, not a
# pagination gap) -- i.e. real IEX single-venue trading sparsity, matching
# the operator's hypothesis. These tests cover the two capabilities added in
# response: a research-only `feed` parameter to let an operator compare IEX
# vs SIP coverage, and a larger RVOL historical warmup buffer to compensate
# for sparse opening ranges among the *lookback* candidates.
# ---------------------------------------------------------------------------


def test_fetch_full_minute_history_defaults_to_iex_feed():
    import asyncio
    from datetime import timezone as tz

    captured_params = []

    class _CapturingClient:
        async def get(self, url, *, headers, params):
            captured_params.append(dict(params))
            return _FakeResponse(200, {"bars": [], "next_page_token": None, "symbol": "AAPL"})

    credentials = AlpacaCredentials(api_key_id="key", api_secret_key="secret")
    asyncio.run(fetch_full_minute_history(
        _CapturingClient(), credentials=credentials, symbol="AAPL",
        start=datetime(2026, 6, 1, tzinfo=tz.utc), end=datetime(2026, 6, 2, tzinfo=tz.utc),
        now=lambda: datetime(2026, 6, 2, tzinfo=tz.utc),
    ))

    assert captured_params[0]["feed"] == "iex"


def test_fetch_full_minute_history_honors_explicit_sip_feed_and_retags_source():
    import asyncio
    from datetime import timezone as tz

    captured_params = []

    class _CapturingClient:
        async def get(self, url, *, headers, params):
            captured_params.append(dict(params))
            return _FakeResponse(200, {
                "bars": [{"t": "2026-06-01T13:30:00Z", "o": "100", "h": "101", "l": "99", "c": "100.5", "v": 1000}],
                "next_page_token": None, "symbol": "AAPL",
            })

    credentials = AlpacaCredentials(api_key_id="key", api_secret_key="secret")
    bars = asyncio.run(fetch_full_minute_history(
        _CapturingClient(), credentials=credentials, symbol="AAPL",
        start=datetime(2026, 6, 1, tzinfo=tz.utc), end=datetime(2026, 6, 2, tzinfo=tz.utc),
        now=lambda: datetime(2026, 6, 2, tzinfo=tz.utc), feed="sip",
    ))

    assert captured_params[0]["feed"] == "sip"
    # Retagged so a SIP comparison's provenance is honest -- `normalize_
    # alpaca_bar` (production, unmodified) always stamps ALPACA_IEX_SOURCE.
    assert bars[0].source.feed == "sip"
    assert bars[0].source.provider == "alpaca"


def test_fetch_full_minute_history_rejects_unsupported_feed():
    import asyncio
    from datetime import timezone as tz

    credentials = AlpacaCredentials(api_key_id="key", api_secret_key="secret")
    with pytest.raises(UniverseResearchError):
        asyncio.run(fetch_full_minute_history(
            _FakePaginatingHttpClient({}), credentials=credentials, symbol="AAPL",
            start=datetime(2026, 6, 1, tzinfo=tz.utc), end=datetime(2026, 6, 2, tzinfo=tz.utc),
            now=lambda: datetime(2026, 6, 2, tzinfo=tz.utc), feed="nasdaq-totalview",
        ))


def test_historical_fetch_start_extends_buffer_by_warmup_multiplier():
    first_session = date(2026, 6, 10)
    plain = trading_sessions_before(first_session, count=20)[0]
    warmed = historical_fetch_start(first_session, lookback_days=20)

    assert DEFAULT_RVOL_WARMUP_MULTIPLIER == 3
    assert warmed == trading_sessions_before(first_session, count=60)[0]
    # The warmed-up start must reach further back in time than a plain
    # exactly-lookback_days buffer would.
    assert warmed < plain


def test_historical_fetch_start_respects_explicit_multiplier():
    first_session = date(2026, 6, 10)
    assert historical_fetch_start(
        first_session, lookback_days=20, warmup_multiplier=1,
    ) == trading_sessions_before(first_session, count=20)[0]
    assert historical_fetch_start(
        first_session, lookback_days=20, warmup_multiplier=5,
    ) == trading_sessions_before(first_session, count=100)[0]


def test_historical_fetch_start_rejects_non_positive_inputs():
    with pytest.raises(UniverseResearchError):
        historical_fetch_start(date(2026, 6, 10), lookback_days=0)
    with pytest.raises(UniverseResearchError):
        historical_fetch_start(date(2026, 6, 10), lookback_days=20, warmup_multiplier=0)


def test_larger_warmup_buffer_lets_median_volume_find_enough_complete_sessions():
    """Reproduces the RVOL-lookback failure mode found in the real cache:
    a symbol whose opening range is complete on only every-other session.
    A plain 20-session buffer supplies just 10 complete candidates (fewer
    than the 20 `relative_volume_lookback_days` requires); the warmed-up
    buffer supplies enough candidates for 20 complete ones to exist."""
    from src.market_data import calculate_median_opening_volume

    symbol = "TESTX"
    study_date = date(2026, 6, 10)
    lookback_days = 20

    def _build_bars(*, back_to: date) -> list[MinuteBar]:
        bars = []
        cursor = study_date - timedelta(days=1)
        while cursor >= back_to:
            if cursor.weekday() < 5:
                # Every other session is missing its 09:32 bar -- an
                # incomplete opening range, exactly like the real IEX gaps.
                complete = (cursor.toordinal() % 2 == 0)
                start = datetime.combine(cursor, time(9, 30), tzinfo=ET)
                for offset in range(5):
                    if complete or offset != 2:
                        bars.append(MinuteBar(
                            symbol=symbol, begins_at=start + timedelta(minutes=offset),
                            open_price=Decimal("100"), high_price=Decimal("100.5"),
                            low_price=Decimal("99.5"), close_price=Decimal("100"),
                            volume=1000, session="REGULAR", interpolated=False,
                        ))
            cursor -= timedelta(days=1)
        return bars

    plain_start = trading_sessions_before(study_date, count=lookback_days)[0]
    warmed_start = historical_fetch_start(study_date, lookback_days=lookback_days)

    with pytest.raises(ValueError):
        calculate_median_opening_volume(
            _build_bars(back_to=plain_start), before_date=study_date, lookback_days=lookback_days,
        )

    # No exception: the larger buffer supplies enough complete candidates.
    result = calculate_median_opening_volume(
        _build_bars(back_to=warmed_start), before_date=study_date, lookback_days=lookback_days,
    )
    assert result == Decimal("5000")


# ---------------------------------------------------------------------------
# Liquidity-qualified universe construction (pivot from fixed-size tiers,
# per the operator's IEX-sparsity finding). All offline, synthetic fixtures.
# ---------------------------------------------------------------------------


def _liquidity_day_bars(symbol, day, *, minutes_present=(0, 1, 2, 3, 4), close="500", volume=10000):
    start = datetime.combine(day, time(9, 30), tzinfo=ET)
    return [
        _bar(symbol, start + timedelta(minutes=offset), open_=close, high=close, low=close, close=close, volume=volume)
        for offset in minutes_present
    ]


def _liquidity_bars_over_days(symbol, days, *, incomplete_days=(), close="500", volume=10000):
    incomplete = set(incomplete_days)
    bars = []
    for day in days:
        minutes = (0, 1, 2) if day in incomplete else (0, 1, 2, 3, 4)
        bars.extend(_liquidity_day_bars(symbol, day, minutes_present=minutes, close=close, volume=volume))
    return bars


LIQ_STUDY_DATE = date(2026, 6, 10)


def test_compute_symbol_liquidity_profile_fully_eligible_symbol():
    days = trading_sessions_before(LIQ_STUDY_DATE, count=60) + (LIQ_STUDY_DATE,)
    session_dates = days[-10:]
    bars = _liquidity_bars_over_days("AAPL", days, close="500", volume=10000)

    profile = compute_symbol_liquidity_profile(
        symbol="AAPL", bars=bars, session_dates=session_dates, lookback_days=20,
    )

    assert profile.eligible is True
    assert profile.exclusion_reasons == ()
    assert profile.session_opening_range_complete_pct == 1.0
    assert profile.rvol_history_satisfiable_pct == 1.0
    assert profile.avg_daily_dollar_volume == Decimal("500") * 10000 * 5
    assert profile.avg_daily_share_volume == Decimal(10000 * 5)
    assert profile.median_price == Decimal("500")
    assert profile.trading_days_observed == len(days)


def test_compute_symbol_liquidity_profile_excludes_below_minimum_price():
    days = trading_sessions_before(LIQ_STUDY_DATE, count=30) + (LIQ_STUDY_DATE,)
    bars = _liquidity_bars_over_days("PENNY", days, close="1", volume=1_000_000)

    profile = compute_symbol_liquidity_profile(
        symbol="PENNY", bars=bars, session_dates=days[-10:], lookback_days=20,
    )

    assert profile.eligible is False
    assert BELOW_MINIMUM_PRICE in profile.exclusion_reasons


def test_compute_symbol_liquidity_profile_excludes_below_minimum_dollar_volume():
    days = trading_sessions_before(LIQ_STUDY_DATE, count=30) + (LIQ_STUDY_DATE,)
    # price=$50 (passes price floor), volume=3000/bar -> share vol 15,000/day
    # (passes share floor) but dollar vol $750,000/day (fails the $1M floor).
    bars = _liquidity_bars_over_days("THIN", days, close="50", volume=3000)

    profile = compute_symbol_liquidity_profile(
        symbol="THIN", bars=bars, session_dates=days[-10:], lookback_days=20,
    )

    assert profile.eligible is False
    assert profile.exclusion_reasons == (BELOW_MINIMUM_DOLLAR_VOLUME,)


def test_compute_symbol_liquidity_profile_excludes_below_minimum_share_volume():
    # price=$250 (passes price floor), volume=1000/bar -> share vol 5,000/day
    # (fails the 10,000 floor) but dollar vol $1,250,000/day (passes).
    days = trading_sessions_before(LIQ_STUDY_DATE, count=30) + (LIQ_STUDY_DATE,)
    bars = _liquidity_bars_over_days("LOWSHARE", days, close="250", volume=1000)

    profile = compute_symbol_liquidity_profile(
        symbol="LOWSHARE", bars=bars, session_dates=days[-10:], lookback_days=20,
    )

    assert profile.eligible is False
    assert profile.exclusion_reasons == (BELOW_MINIMUM_SHARE_VOLUME,)


def test_compute_symbol_liquidity_profile_excludes_insufficient_opening_range_coverage():
    # 80 days of ample, fully-complete lookback history, then the 20 STUDY
    # session dates themselves have 2 incomplete opening ranges (18/20 =
    # 90% < 95%) while the deep lookback history stays fully complete, so
    # RVOL satisfiability is unaffected -- isolates opening-range coverage.
    days = trading_sessions_before(LIQ_STUDY_DATE, count=80) + (LIQ_STUDY_DATE,)
    session_dates = days[-20:]
    incomplete = session_dates[:2]
    bars = _liquidity_bars_over_days("GAPPY", days, incomplete_days=incomplete, close="500", volume=10000)

    profile = compute_symbol_liquidity_profile(
        symbol="GAPPY", bars=bars, session_dates=session_dates, lookback_days=20,
    )

    assert profile.eligible is False
    assert INSUFFICIENT_OPENING_RANGE_COVERAGE in profile.exclusion_reasons
    assert profile.session_opening_range_complete_pct == pytest.approx(18 / 20)


def test_compute_symbol_liquidity_profile_excludes_insufficient_rvol_history_coverage():
    # Only 10 days of history exist before the (fully complete) session
    # dates -- never enough to satisfy a 20-session RVOL lookback, even
    # though every one of those 10 days, and every session date itself, has
    # a complete opening range. Isolates the lookback-depth failure mode
    # from the opening-range-coverage failure mode.
    lookback_history = trading_sessions_before(LIQ_STUDY_DATE, count=10)
    session_dates = (LIQ_STUDY_DATE,)
    bars = _liquidity_bars_over_days("SHALLOW", lookback_history + session_dates, close="500", volume=10000)

    profile = compute_symbol_liquidity_profile(
        symbol="SHALLOW", bars=bars, session_dates=session_dates, lookback_days=20,
    )

    assert profile.eligible is False
    assert profile.exclusion_reasons == (INSUFFICIENT_RVOL_HISTORY_COVERAGE,)
    assert profile.session_opening_range_complete_pct == 1.0
    assert profile.rvol_history_satisfiable_pct == 0.0


def test_compute_symbol_liquidity_profile_reports_no_historical_data():
    profile = compute_symbol_liquidity_profile(
        symbol="EMPTY", bars=[], session_dates=(LIQ_STUDY_DATE,), lookback_days=20,
    )

    assert profile.eligible is False
    assert profile.exclusion_reasons == (NO_HISTORICAL_DATA,)
    assert profile.trading_days_observed == 0


def test_symbol_liquidity_profile_rejects_inconsistent_eligibility_and_reasons():
    with pytest.raises(UniverseResearchError):
        SymbolLiquidityProfile(
            symbol="X", trading_days_observed=1, avg_daily_dollar_volume=Decimal("1"),
            avg_daily_share_volume=Decimal("1"), median_price=Decimal("1"),
            opening_window_minute_coverage_pct=1.0, session_opening_range_complete_pct=1.0,
            rvol_history_satisfiable_pct=1.0, eligible=True, exclusion_reasons=(BELOW_MINIMUM_PRICE,),
        )
    with pytest.raises(UniverseResearchError):
        SymbolLiquidityProfile(
            symbol="X", trading_days_observed=1, avg_daily_dollar_volume=Decimal("1"),
            avg_daily_share_volume=Decimal("1"), median_price=Decimal("1"),
            opening_window_minute_coverage_pct=1.0, session_opening_range_complete_pct=1.0,
            rvol_history_satisfiable_pct=1.0, eligible=False, exclusion_reasons=(),
        )


def _profile(symbol, *, dollar_volume, eligible=True, reasons=()):
    return SymbolLiquidityProfile(
        symbol=symbol, trading_days_observed=90, avg_daily_dollar_volume=Decimal(str(dollar_volume)),
        avg_daily_share_volume=Decimal("100000"), median_price=Decimal("100"),
        opening_window_minute_coverage_pct=1.0, session_opening_range_complete_pct=1.0,
        rvol_history_satisfiable_pct=1.0, eligible=eligible, exclusion_reasons=reasons,
    )


def test_rank_liquidity_qualified_symbols_orders_by_dollar_volume_and_excludes_ineligible():
    profiles = {
        "LOW": _profile("LOW", dollar_volume=1_000_000),
        "HIGH": _profile("HIGH", dollar_volume=5_000_000),
        "MID": _profile("MID", dollar_volume=3_000_000),
        "OUT": _profile("OUT", dollar_volume=9_000_000, eligible=False, reasons=(BELOW_MINIMUM_PRICE,)),
    }

    ranked = rank_liquidity_qualified_symbols(profiles)

    assert ranked == ("HIGH", "MID", "LOW")


def test_rank_liquidity_qualified_symbols_breaks_ties_alphabetically():
    profiles = {
        "ZEBRA": _profile("ZEBRA", dollar_volume=5_000_000),
        "ALPHA": _profile("ALPHA", dollar_volume=5_000_000),
        "MIKE": _profile("MIKE", dollar_volume=5_000_000),
    }

    ranked = rank_liquidity_qualified_symbols(profiles)

    assert ranked == ("ALPHA", "MIKE", "ZEBRA")


def test_rank_liquidity_qualified_symbols_is_deterministic_across_dict_orderings():
    a = {"A": _profile("A", dollar_volume=2), "B": _profile("B", dollar_volume=1), "C": _profile("C", dollar_volume=3)}
    b = {"C": _profile("C", dollar_volume=3), "A": _profile("A", dollar_volume=2), "B": _profile("B", dollar_volume=1)}

    assert rank_liquidity_qualified_symbols(a) == rank_liquidity_qualified_symbols(b) == ("C", "A", "B")


def test_build_liquidity_tiers_is_nested_and_strictly_superset():
    ranked = ("A", "B", "C", "D", "E")
    tiers = build_liquidity_tiers(ranked, (2, 4))

    assert tiers[0].symbols == ("A", "B")
    assert tiers[1].symbols == ("A", "B", "C", "D")
    assert set(tiers[0].symbols) <= set(tiers[1].symbols)
    assert tiers[0].size_shortfall is False
    assert tiers[1].size_shortfall is False


def test_build_liquidity_tiers_does_not_pad_a_short_tier():
    ranked = ("A", "B", "C")
    tiers = build_liquidity_tiers(ranked, (2, 10))

    assert tiers[1].requested_size == 10
    assert tiers[1].achieved_size == 3
    assert tiers[1].symbols == ("A", "B", "C")
    assert tiers[1].size_shortfall is True


def test_build_liquidity_tiers_rejects_non_ascending_or_duplicate_sizes():
    with pytest.raises(UniverseResearchError):
        build_liquidity_tiers(("A", "B"), (10, 5))
    with pytest.raises(UniverseResearchError):
        build_liquidity_tiers(("A", "B"), (5, 5))
    with pytest.raises(UniverseResearchError):
        build_liquidity_tiers(("A", "B"), (0,))


def test_liquidity_thresholds_rejects_invalid_values():
    with pytest.raises(UniverseResearchError):
        LiquidityThresholds(min_median_price=Decimal("0"))
    with pytest.raises(UniverseResearchError):
        LiquidityThresholds(min_session_opening_range_complete_pct=1.5)
    with pytest.raises(UniverseResearchError):
        LiquidityThresholds(min_rvol_history_satisfiable_pct=0.0)
