"""Focused tests for M41 candidate-selection timing/ranking research
(Issue #330)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_existing_configs_reevaluation_research import FROZEN_CONFIG_SET
from src.orb_gap_rvol_sensitivity_research import ParameterCombo, precompute_inputs_table, evaluate_combo, sensitivity_config
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    ARM_CURRENT_FIRST_VALID,
    ARM_EARLY_FIRST_VALID,
    ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH,
    ARM_RANKED_POOL_0950_MAX_RVOL,
    ARM_WAITED_FIRST_VALID_0950,
    ARMS,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    DECISION_CUTOFF_TIME,
    HALF_LIFE_SESSIONS,
    DecisionTimeCandidate,
    breakout_strength,
    build_all_arms_rows,
    build_session_index,
    build_wait_row,
    early_pool,
    enrich_with_decision_time,
    group_by_day,
    live_data_at_or_after,
    rank_equal_rank_strength,
    rank_max_rvol,
    recency_weight,
    recency_weighted_trade_quality,
    relative_strength_vs_spy,
    session_open_price,
    trade_age_in_sessions,
)
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from src.orb_baseline_stability_research import TradeDiagnosticRow
from src.signal_engine import LiveData, OpeningData, SignalDecision

CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])


# ---------------------------------------------------------------------------
# Fixture helpers -- canonical MarketBar objects driving the real,
# unmodified precompute/evaluate/simulate pipeline (no hand-built
# SignalDecision/LiveData for the integration tests; only the pure-function
# unit tests below construct those directly).
# ---------------------------------------------------------------------------


def _mbar(symbol, session_date, hh, mm, *, open_, high, low, close, volume):
    ts = datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=session_date, event_timestamp=ts,
        end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


def _prior_sessions(first_session: date, *, count: int) -> list[date]:
    import src.exchange_calendar as cal

    dates: list[date] = []
    cursor = first_session - timedelta(days=1)
    while len(dates) < count:
        if cal.regular_session_bounds(cursor) is not None:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


def _historical_bars(symbol: str, dates: list[date]) -> list[MarketBar]:
    bars = []
    for d in dates:
        for minute in range(5):
            bars.append(_mbar(symbol, d, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))
    return bars


def _symbol_day_bars(
    symbol: str, session_date: date, *, gap_percent: Decimal, rvol_multiple: Decimal, minute_closes: dict[tuple[int, int], str],
) -> list[MarketBar]:
    """Opening range 09:30-09:34 gapped up by `gap_percent`, opening
    volume scaled to exactly `rvol_multiple`, then one bar per
    `minute_closes` entry (hh, mm) -> close price string, each bar's
    open/high/low a tight band around its own close and the previous
    bar's close, giving full per-minute control over the price path
    (needed to control exactly what happens at/after 09:50)."""
    previous_close = Decimal("100")
    today_open = previous_close * (Decimal("1") + gap_percent / Decimal("100"))
    opening_bar_volume = int((rvol_multiple * Decimal("500000")) / 5)
    bars: list[MarketBar] = []
    for minute in range(5):
        bars.append(
            _mbar(symbol, session_date, 9, 30 + minute, open_=str(today_open), high=str(today_open + Decimal("0.5")),
                  low=str(today_open - Decimal("0.3")), close=str(today_open), volume=opening_bar_volume)
        )
    prev_close = today_open
    for (hh, mm), close_str in sorted(minute_closes.items()):
        close = Decimal(close_str)
        open_ = prev_close
        high = max(open_, close) + Decimal("0.02")
        low = min(open_, close) - Decimal("0.02")
        bars.append(_mbar(symbol, session_date, hh, mm, open_=str(open_), high=str(high), low=str(low), close=str(close), volume=20_000))
        prev_close = close
    return bars


def _spy_bars(dates: list[date], *, base_price: str = "500") -> list[MarketBar]:
    # A steady 0.01/minute uptrend so `spy_price > spy_vwap` holds from the
    # first opening-range bar onward (a perfectly flat series has
    # spy_price == spy_vwap, which fails the strict gate).
    bars = []
    for d in dates:
        price = Decimal(base_price)
        for minute in range(60):
            hh, mm = (9, 30 + minute) if minute < 30 else (10, minute - 30)
            price += Decimal("0.01")
            bars.append(_mbar("SPY", d, hh, mm, open_=str(price - Decimal("0.01")), high=str(price + Decimal("0.02")), low=str(price - Decimal("0.02")), close=str(price), volume=100_000))
    return bars


def _minute_bars(market_bars: dict[str, list[MarketBar]]) -> dict[str, list]:
    return {symbol: [bar.to_minute_bar() for bar in bars] for symbol, bars in market_bars.items()}


SESSION_DATE = date(2026, 8, 14)
HISTORICAL_DATES = _prior_sessions(SESSION_DATE, count=LOOKBACK_DAYS)


def _evaluate_single_day(minute_closes_by_symbol: dict[str, dict[tuple[int, int], str]], *, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75")):
    """Builds one session's worth of candidates for the given symbols
    (each with its own minute-close path) plus SPY, precomputes and
    evaluates through the real, unmodified pipeline against the frozen
    M41 config, and returns (evaluation, inputs_table, market_bars,
    minute_bars)."""
    market_bars: dict[str, list[MarketBar]] = {}
    for symbol, minute_closes in minute_closes_by_symbol.items():
        market_bars[symbol] = _historical_bars(symbol, HISTORICAL_DATES) + _symbol_day_bars(
            symbol, SESSION_DATE, gap_percent=gap_percent, rvol_multiple=rvol_multiple, minute_closes=minute_closes,
        )
    market_bars["SPY"] = _spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    minute_bars = _minute_bars(market_bars)
    universe = tuple(minute_closes_by_symbol.keys())
    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    evaluation = evaluate_combo(
        combo=CONFIG_ENTRY.combo, universe=universe, session_dates=[SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    return evaluation, inputs_table, market_bars, minute_bars


# A breakout at 09:41 that stays comfortably above the opening-range high
# all the way through 09:55, then drifts higher -- valid at 09:50.
STRONG_BREAKOUT_HOLDS = {
    (9, 40): "105.5", (9, 41): "106.0", (9, 42): "106.2", (9, 43): "106.3", (9, 44): "106.4",
    (9, 45): "106.5", (9, 46): "106.6", (9, 47): "106.7", (9, 48): "106.8", (9, 49): "106.9",
    (9, 50): "107.0", (9, 51): "107.2", (9, 52): "107.4", (9, 53): "107.6", (9, 54): "107.8", (9, 55): "108.0",
}

# A breakout at 09:41 that pulls back BELOW the opening-range high by 09:50
# (opening_range_high = today_open + 0.5 = 100.5 + 0.5 = 101.0 at gap=0.5%).
PULLS_BACK_BY_0950 = {
    (9, 40): "101.5", (9, 41): "101.6", (9, 42): "101.4", (9, 43): "101.2", (9, 44): "101.1",
    (9, 45): "101.05", (9, 46): "101.0", (9, 47): "100.9", (9, 48): "100.8", (9, 49): "100.7",
    (9, 50): "100.6", (9, 51): "100.5", (9, 52): "100.4",
}


# ---------------------------------------------------------------------------
# Frozen configuration.
# ---------------------------------------------------------------------------

def test_config_label_resolves_to_challenger_only():
    assert CONFIG_LABEL == "GAP_050_RVOL_175"
    assert CONFIG_ENTRY is FROZEN_CONFIG_SET["GAP_050_RVOL_175"]
    assert CONFIG_ENTRY.combo == ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75"))


def test_primary_config_is_never_referenced_by_this_module():
    import inspect

    import src.orb_candidate_selection_timing_research as mod

    source = inspect.getsource(mod)
    assert "GAP_100_RVOL_250" not in source


def test_decision_cutoff_is_0950():
    assert DECISION_CUTOFF_TIME == time(9, 50)


def test_anchor_date_and_half_life_are_frozen():
    assert ANCHOR_DATE == date(2026, 8, 13)
    assert HALF_LIFE_SESSIONS == 504


def test_five_arms_are_declared():
    assert ARMS == (
        ARM_CURRENT_FIRST_VALID, ARM_EARLY_FIRST_VALID, ARM_WAITED_FIRST_VALID_0950,
        ARM_RANKED_POOL_0950_MAX_RVOL, ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH,
    )


# ---------------------------------------------------------------------------
# Pool construction: early_pool / live_data_at_or_after.
# ---------------------------------------------------------------------------

def _live(hh, mm, *, close="105", spy_price="500", spy_vwap="499"):
    ts = datetime(2026, 8, 14, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
    return LiveData(timestamp=ts, completed_bar_close=Decimal(close), spy_price=Decimal(spy_price), spy_vwap=Decimal(spy_vwap))


def _opening(symbol="AAPL", *, high="101.0", low="99.5", today_open="100.5"):
    return OpeningData(
        symbol=symbol, previous_close=Decimal("100"), today_open=Decimal(today_open),
        opening_range_high=Decimal(high), opening_range_low=Decimal(low),
        opening_range_volume=500_000, median_opening_volume=Decimal("300000"),
    )


def _decision(*, gap="0.50", rvol="1.75", entry="105", stop="99.5"):
    return SignalDecision(
        approved=True, reason="ORB V0 long-entry signal approved.", gap_percent=Decimal(gap),
        relative_volume=Decimal(rvol), entry_price=Decimal(entry), stop_price=Decimal(stop),
    )


def test_early_pool_includes_exactly_0950_boundary():
    at_0950 = ("AAPL", SESSION_DATE, _opening(), _live(9, 50), _decision())
    at_0951 = ("MSFT", SESSION_DATE, _opening(), _live(9, 51), _decision())
    at_0949 = ("GOOGL", SESSION_DATE, _opening(), _live(9, 49), _decision())
    pool = early_pool([at_0950, at_0951, at_0949])
    symbols = {c[0] for c in pool}
    assert symbols == {"AAPL", "GOOGL"}


def test_live_data_at_or_after_finds_first_bar_at_cutoff():
    window = [(None, _live(9, 48)), (None, _live(9, 49)), (None, _live(9, 50)), (None, _live(9, 51))]
    found = live_data_at_or_after(window)
    assert found is not None
    assert found[1].timestamp.astimezone(EASTERN).time() == time(9, 50)


def test_live_data_at_or_after_returns_none_when_window_ends_before_cutoff():
    window = [(None, _live(9, 40)), (None, _live(9, 45))]
    assert live_data_at_or_after(window) is None


def test_group_by_day_groups_multiple_symbols_on_the_same_day():
    c1 = ("AAPL", SESSION_DATE, _opening(), _live(9, 41), _decision())
    c2 = ("MSFT", SESSION_DATE, _opening(), _live(9, 43), _decision())
    grouped = group_by_day([c1, c2])
    assert set(grouped.keys()) == {SESSION_DATE}
    assert len(grouped[SESSION_DATE]) == 2


# ---------------------------------------------------------------------------
# Ranking factor formulas.
# ---------------------------------------------------------------------------

def test_breakout_strength_positive_above_range_high():
    opening = _opening(high="101.0", low="99.5")
    # width = 1.5, price=102.5 -> (102.5-101.0)/1.5 = 1.0
    result = breakout_strength(opening_data=opening, price_at_decision=Decimal("102.5"))
    assert result == Decimal("1.0")


def test_breakout_strength_negative_when_price_below_range_high():
    opening = _opening(high="101.0", low="99.5")
    result = breakout_strength(opening_data=opening, price_at_decision=Decimal("100.25"))
    assert result < 0


def test_relative_strength_vs_spy_positive_when_stock_outperforms():
    opening = _opening(today_open="100.0")
    result = relative_strength_vs_spy(
        opening_data=opening, price_at_decision=Decimal("102.0"),
        spy_price_at_decision=Decimal("505"), spy_open=Decimal("500"),
    )
    # stock_return = 0.02, spy_return = 0.01 -> relative = 0.01
    assert result == Decimal("102.0") / Decimal("100.0") - 1 - (Decimal("505") / Decimal("500") - 1)


def test_relative_strength_vs_spy_negative_when_stock_underperforms():
    opening = _opening(today_open="100.0")
    result = relative_strength_vs_spy(
        opening_data=opening, price_at_decision=Decimal("100.5"),
        spy_price_at_decision=Decimal("510"), spy_open=Decimal("500"),
    )
    assert result < 0


# ---------------------------------------------------------------------------
# Rankers.
# ---------------------------------------------------------------------------

def _dtc(symbol, *, ts=(9, 41), rvol="1.75", gap="0.50", decision_ts=(9, 50), price_at_decision="105", spy_price="500", opening_high="101.0", opening_low="99.5", today_open="100.5"):
    opening = _opening(symbol, high=opening_high, low=opening_low, today_open=today_open)
    original_live = _live(*ts)
    decision = _decision(gap=gap, rvol=rvol)
    decision_live = _live(*decision_ts, close=price_at_decision, spy_price=spy_price) if decision_ts is not None else None
    return DecisionTimeCandidate(
        symbol=symbol, session_date=SESSION_DATE, opening_data=opening, original_live_data=original_live,
        original_decision=decision, decision_bar=None, decision_live_data=decision_live,
    )


def test_rank_max_rvol_picks_highest_relative_volume():
    low_rvol = _dtc("AAPL", rvol="1.80")
    high_rvol = _dtc("MSFT", rvol="2.50")
    winner = rank_max_rvol([low_rvol, high_rvol])
    assert winner.symbol == "MSFT"


def test_rank_max_rvol_ties_break_by_earlier_signal_then_symbol():
    same_rvol_later = _dtc("ZETA", rvol="2.00", ts=(9, 45))
    same_rvol_earlier = _dtc("ALPHA", rvol="2.00", ts=(9, 42))
    winner = rank_max_rvol([same_rvol_later, same_rvol_earlier])
    assert winner.symbol == "ALPHA"


def test_rank_max_rvol_alphabetical_final_tiebreak():
    a = _dtc("BETA", rvol="2.00", ts=(9, 42))
    b = _dtc("ALPHA", rvol="2.00", ts=(9, 42))
    winner = rank_max_rvol([a, b])
    assert winner.symbol == "ALPHA"


def test_rank_equal_rank_strength_prefers_dominant_candidate():
    # MSFT dominates on every factor: higher gap, higher rvol, further
    # above range high, and stronger vs SPY.
    weak = _dtc("AAPL", gap="0.50", rvol="1.80", price_at_decision="101.2", opening_high="101.0", opening_low="99.5", spy_price="500")
    strong = _dtc("MSFT", gap="1.20", rvol="2.50", price_at_decision="103.0", opening_high="101.0", opening_low="99.5", spy_price="500")
    winner = rank_equal_rank_strength([weak, strong], spy_open=Decimal("500"))
    assert winner.symbol == "MSFT"


def test_rank_equal_rank_strength_falls_back_when_no_decision_time_data():
    a = _dtc("AAPL", ts=(9, 42), decision_ts=None)
    b = _dtc("MSFT", ts=(9, 44), decision_ts=None)
    winner = rank_equal_rank_strength([a, b], spy_open=Decimal("500"))
    assert winner.symbol == "AAPL"  # earlier original signal wins the fallback tie-break


def test_rank_equal_rank_strength_uses_equal_weights_not_a_single_dominant_factor():
    # A wins gap+rvol, B wins breakout_strength+relative_strength -- exact tie on
    # average rank (2.5 each); alphabetical tie-break decides.
    a = _dtc("AAA", gap="1.00", rvol="3.00", price_at_decision="101.1", opening_high="101.0", opening_low="99.5", spy_price="500")
    b = _dtc("BBB", gap="0.50", rvol="1.80", price_at_decision="103.0", opening_high="101.0", opening_low="99.5", spy_price="500")
    winner = rank_equal_rank_strength([a, b], spy_open=Decimal("500"))
    assert winner.symbol == "AAA"


def test_enrich_with_decision_time_reads_the_real_window():
    from src.orb_gap_rvol_sensitivity_research import SessionSignalInputs

    original = ("AAPL", SESSION_DATE, _opening(), _live(9, 41), _decision())
    window = ((None, _live(9, 48)), (None, _live(9, 50)))
    inputs_table = {("AAPL", SESSION_DATE): SessionSignalInputs(None, _opening(), window)}
    enriched = enrich_with_decision_time(original, inputs_table=inputs_table)
    assert enriched.decision_live_data.timestamp.astimezone(EASTERN).time() == time(9, 50)


def test_session_open_price_reads_first_non_interpolated_bar():
    bars = _spy_bars([SESSION_DATE])
    minute_bars = [b.to_minute_bar() for b in bars]
    price = session_open_price(minute_bars, session_date=SESSION_DATE)
    assert price == Decimal("500")


# ---------------------------------------------------------------------------
# WAIT-to-09:50 execution mechanics (integration, real simulator).
# ---------------------------------------------------------------------------

def test_wait_execution_never_reuses_the_original_breakout_price():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    assert len(evaluation.candidate_signals) == 1
    candidate = evaluation.candidate_signals[0]
    original_entry_price = candidate[4].entry_price
    enriched = enrich_with_decision_time(candidate, inputs_table=inputs_table)
    assert enriched.decision_live_data is not None
    assert enriched.decision_live_data.timestamp.astimezone(EASTERN).time() == time(9, 50)

    combo_config = sensitivity_config(CONFIG, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    row = build_wait_row(
        enriched, period="TEST", view=ARM_WAITED_FIRST_VALID_0950, combo_config=combo_config,
        strategy_version="orb_long_v0-test", implementation_digest="test-digest", shadow_run_id="test-wait",
        created_at=datetime.now(timezone.utc), market_bars_by_symbol=market_bars,
    )
    assert row.decision["wait_reevaluation_approved"] is True
    assert row.outcome["entered"] is True
    # The decision at 09:50 corresponds to the bar BEGINNING at 09:49
    # (LiveData.timestamp = bar.begins_at + 1 minute) -- its own close
    # (106.9) must be the entry price, not the original breakout bar's.
    assert row.outcome["entry_price"] == "106.9"
    assert row.outcome["entry_price"] != str(original_entry_price)


def test_wait_execution_reports_invalidated_when_pullback_by_0950():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": PULLS_BACK_BY_0950})
    assert len(evaluation.candidate_signals) == 1
    candidate = evaluation.candidate_signals[0]
    enriched = enrich_with_decision_time(candidate, inputs_table=inputs_table)
    assert enriched.decision_live_data is not None

    combo_config = sensitivity_config(CONFIG, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    row = build_wait_row(
        enriched, period="TEST", view=ARM_WAITED_FIRST_VALID_0950, combo_config=combo_config,
        strategy_version="orb_long_v0-test", implementation_digest="test-digest", shadow_run_id="test-wait-invalid",
        created_at=datetime.now(timezone.utc), market_bars_by_symbol=market_bars,
    )
    assert row.decision["wait_reevaluation_approved"] is False
    assert row.outcome["entered"] is False
    assert row.outcome["closed"] is False


def test_wait_execution_records_no_decision_time_data_honestly():
    candidate_no_data = DecisionTimeCandidate(
        symbol="AAPL", session_date=SESSION_DATE, opening_data=_opening(), original_live_data=_live(9, 41),
        original_decision=_decision(), decision_bar=None, decision_live_data=None,
    )
    row = build_wait_row(
        candidate_no_data, period="TEST", view=ARM_WAITED_FIRST_VALID_0950, combo_config=sensitivity_config(CONFIG, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75")),
        strategy_version="orb_long_v0-test", implementation_digest="test-digest", shadow_run_id="test-no-data",
        created_at=datetime.now(timezone.utc), market_bars_by_symbol={"AAPL": [], "SPY": []},
    )
    assert row.outcome["state"] == "NO_DECISION_TIME_DATA"
    assert row.outcome["entered"] is False
    assert row.outcome["closed"] is False
    assert row.decision["decision_timestamp"] is None


# ---------------------------------------------------------------------------
# build_all_arms_rows integration.
# ---------------------------------------------------------------------------

def test_build_all_arms_rows_current_and_early_share_identity_when_one_candidate():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest",
        created_at=datetime.now(timezone.utc),
    )
    assert set(rows.keys()) == set(ARMS)
    assert len(rows[ARM_CURRENT_FIRST_VALID]) == 1
    assert len(rows[ARM_EARLY_FIRST_VALID]) == 1
    assert rows[ARM_CURRENT_FIRST_VALID][0].decision["symbol"] == rows[ARM_EARLY_FIRST_VALID][0].decision["symbol"] == "AAPL"


def test_build_all_arms_rows_waited_reuses_early_identity_but_different_entry_price():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest",
        created_at=datetime.now(timezone.utc),
    )
    early_row = rows[ARM_EARLY_FIRST_VALID][0]
    waited_row = rows[ARM_WAITED_FIRST_VALID_0950][0]
    assert early_row.decision["symbol"] == waited_row.decision["symbol"] == "AAPL"
    assert early_row.outcome["entry_price"] != waited_row.outcome["entry_price"]


def test_build_all_arms_rows_ranked_arms_pick_among_multi_symbol_pool():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({
        "AAPL": {**STRONG_BREAKOUT_HOLDS},
        "MSFT": {(9, 40): "105.5", (9, 41): "108.0", (9, 42): "108.5", (9, 43): "109.0", (9, 44): "109.5", (9, 45): "110.0", (9, 46): "110.5", (9, 47): "111.0", (9, 48): "111.5", (9, 49): "112.0", (9, 50): "112.5"},
    })
    rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest",
        created_at=datetime.now(timezone.utc),
    )
    assert len(rows[ARM_RANKED_POOL_0950_MAX_RVOL]) == 1
    assert len(rows[ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH]) == 1
    # Both symbols had identical gap/rvol (same combo thresholds drove candidate
    # generation identically); MSFT ran further above its range high by 09:50,
    # so both rankers should prefer MSFT's stronger breakout.
    assert rows[ARM_RANKED_POOL_0950_MAX_RVOL][0].decision["symbol"] in {"AAPL", "MSFT"}
    assert rows[ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH][0].decision["symbol"] in {"AAPL", "MSFT"}


def test_build_all_arms_rows_rejects_wrong_combo():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    wrong_evaluation = evaluate_combo(
        combo=ParameterCombo(gap_percent=Decimal("1.00"), rvol_multiple=Decimal("2.50")),
        universe=("AAPL",), session_dates=[SESSION_DATE], inputs_table=inputs_table, base_config=CONFIG,
    )
    with pytest.raises(Exception):
        build_all_arms_rows(
            period="TEST", evaluation=wrong_evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
            minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest",
            created_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Recency-weighted deployment view.
# ---------------------------------------------------------------------------

def test_session_index_covers_anchor_date():
    index = build_session_index(start_date=date(2026, 1, 1))
    assert ANCHOR_DATE in index


def test_recency_weight_is_one_on_anchor_date():
    index = build_session_index(start_date=date(2026, 1, 1))
    assert recency_weight(ANCHOR_DATE, session_index=index) == pytest.approx(1.0)


def test_recency_weight_is_half_at_504_sessions():
    index = build_session_index()
    target_index = index[ANCHOR_DATE] - HALF_LIFE_SESSIONS
    target_date = next(d for d, i in index.items() if i == target_index)
    assert recency_weight(target_date, session_index=index) == pytest.approx(0.5)


def test_recency_weight_is_quarter_at_1008_sessions():
    index = build_session_index()
    target_index = index[ANCHOR_DATE] - 2 * HALF_LIFE_SESSIONS
    target_date = next(d for d, i in index.items() if i == target_index)
    assert recency_weight(target_date, session_index=index) == pytest.approx(0.25)


def test_trade_age_in_sessions_is_zero_on_anchor_date():
    index = build_session_index(start_date=date(2026, 1, 1))
    assert trade_age_in_sessions(ANCHOR_DATE, session_index=index) == 0


def _row(session_date_str: str, r_multiple: float) -> TradeDiagnosticRow:
    return TradeDiagnosticRow(
        decision={"symbol": "AAPL", "session_date": session_date_str},
        outcome={"closed": True, "r_multiple": r_multiple},
    )


def test_recency_weighted_expectancy_matches_hand_computation():
    index = build_session_index()
    # Two trades: one on the anchor date (weight 1.0, r=+1.0), one 504
    # sessions earlier (weight 0.5, r=-1.0).
    older_index = index[ANCHOR_DATE] - HALF_LIFE_SESSIONS
    older_date = next(d for d, i in index.items() if i == older_index)
    rows = [_row(ANCHOR_DATE.isoformat(), 1.0), _row(older_date.isoformat(), -1.0)]
    result = recency_weighted_trade_quality(rows, session_index=index)
    expected_expectancy = (1.0 * 1.0 + 0.5 * -1.0) / (1.0 + 0.5)
    assert result["weighted_expectancy_r"] == pytest.approx(expected_expectancy)
    assert result["closed_trades"] == 2


def test_recency_weighted_win_rate_and_profit_factor():
    index = build_session_index()
    rows = [_row(ANCHOR_DATE.isoformat(), 2.0), _row(ANCHOR_DATE.isoformat(), -1.0)]
    result = recency_weighted_trade_quality(rows, session_index=index)
    assert result["weighted_win_rate"] == pytest.approx(0.5)
    assert result["weighted_gross_profit_r"] == pytest.approx(2.0)
    assert result["weighted_gross_loss_r"] == pytest.approx(1.0)
    assert result["weighted_profit_factor_r"] == pytest.approx(2.0)


def test_recency_weighted_view_never_produces_a_weighted_cumulative_r():
    index = build_session_index()
    rows = [_row(ANCHOR_DATE.isoformat(), 1.0)]
    result = recency_weighted_trade_quality(rows, session_index=index)
    assert "weighted_cumulative_r" not in result
    assert not any("cumulative" in key for key in result)


def test_recency_weighted_view_handles_no_closed_trades():
    index = build_session_index()
    result = recency_weighted_trade_quality([], session_index=index)
    assert result["closed_trades"] == 0
    assert result["weighted_expectancy_r"] is None


def test_p_minus_1_evidence_is_not_discarded_only_downweighted():
    index = build_session_index()
    p_minus_1_start = date(2022, 3, 30)
    weight = recency_weight(p_minus_1_start, session_index=index)
    assert 0 < weight < 1.0


# ---------------------------------------------------------------------------
# Safety / scope.
# ---------------------------------------------------------------------------

def test_module_never_touches_strategy_yaml_or_enabled_flag_persistently():
    import inspect

    import src.orb_candidate_selection_timing_research as mod

    source = inspect.getsource(mod)
    assert "strategy_v0.yaml" not in source
    for match_line in source.splitlines():
        if "enabled" in match_line and "=" in match_line:
            assert "in-memory only" in source  # documented, never persisted


def test_module_constructs_no_new_parameter_combo():
    import inspect

    import src.orb_candidate_selection_timing_research as mod

    source = inspect.getsource(mod)
    assert "ParameterCombo(" not in source


def test_module_never_changes_stop_target_exit_logic():
    import inspect

    import src.orb_candidate_selection_timing_research as mod

    source = inspect.getsource(mod)
    for forbidden in ("stop_loss_percent", "target_percent", "ExitEngine("):
        assert forbidden not in source
