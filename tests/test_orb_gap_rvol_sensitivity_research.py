from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_gap_rvol_sensitivity_research import (
    FREQUENCY_TARGETS,
    GAP_THRESHOLDS,
    RVOL_THRESHOLDS,
    ParameterCombo,
    aggregate_trade_quality,
    all_incremental_effects,
    baseline_combo,
    build_recommendation,
    evaluate_combo,
    frequency_target_hits,
    gap_relaxation_steps,
    incremental_effect,
    matrix_row,
    parameter_matrix,
    precompute_inputs_table,
    relaxation_distance,
    rvol_relaxation_steps,
    sensitivity_config,
    simulate_combo_trade_quality,
)
from src.orb_universe_expansion_research import (
    CANDIDATE,
    DATA_INCOMPLETE,
    evaluate_symbol_session_with_signal,
    index_minute_bars_by_date,
    precompute_session_signal_inputs,
)

REPO_ROOT_CONFIG_PATH = "config/strategy_v0.yaml"


def _evaluate(combo, universe, session_dates, minute_bars, base_config=None):
    """Test convenience: precompute the inputs table and evaluate one combo
    against it in a single call, matching what `evaluate_combo` alone did
    before `precompute_inputs_table` was split out for reuse across the
    whole matrix (see `src/orb_gap_rvol_sensitivity_research.py`)."""
    config = base_config if base_config is not None else CONFIG
    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=config,
    )
    return evaluate_combo(
        combo=combo, universe=universe, session_dates=session_dates,
        inputs_table=inputs_table, base_config=config,
    )


# ---------------------------------------------------------------------------
# Synthetic fixtures. Built as canonical MarketBar objects (bridged to
# MinuteBar via `.to_minute_bar()`) so the same fixtures drive both the
# funnel-evaluation tests and the trade-quality/shadow-execution tests --
# one source of truth, matching this repository's injected-effects
# convention (no network access).
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


def synthetic_symbol_bars(
    symbol: str, session_date: date, *, gap_percent: Decimal, rvol_multiple: Decimal,
    lookback_days: int, breakout: bool = True, historical_dates: list[date] | None = None,
) -> list[MarketBar]:
    """20 (or `lookback_days`) prior 09:30-09:34 sessions with a fixed
    100,000-share-per-bar (500,000/session) opening volume and a Decimal
    100 close, then a study-day opening range gapped up by exactly
    `gap_percent` with total opening volume scaled to exactly
    `rvol_multiple` x the historical median, then (if `breakout`) an entry
    window bar closing above the opening-range high."""
    bars: list[MarketBar] = []
    for prior_date in (historical_dates or _prior_sessions(session_date, count=lookback_days)):
        for minute in range(5):
            bars.append(_mbar(symbol, prior_date, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))

    previous_close = Decimal("100")
    today_open = previous_close * (Decimal("1") + gap_percent / Decimal("100"))
    opening_bar_volume = int((rvol_multiple * Decimal("500000")) / 5)
    opening_high = today_open + Decimal("0.5")
    opening_low = today_open - Decimal("0.3")
    for minute in range(5):
        bars.append(
            _mbar(
                symbol, session_date, 9, 30 + minute,
                open_=str(today_open), high=str(opening_high), low=str(opening_low), close=str(today_open),
                volume=opening_bar_volume,
            )
        )
    breakout_close = opening_high + Decimal("1") if breakout else opening_low
    bars.append(
        _mbar(
            symbol, session_date, 9, 40,
            open_=str(today_open), high=str(breakout_close + Decimal("0.5")), low=str(opening_low),
            close=str(breakout_close), volume=50_000,
        )
    )
    # A couple more bars for a trade-quality simulation to have something to work with.
    bars.append(_mbar(symbol, session_date, 9, 41, open_=str(breakout_close), high=str(breakout_close + Decimal("0.3")), low=str(breakout_close - Decimal("0.2")), close=str(breakout_close + Decimal("0.1")), volume=10_000))
    bars.append(_mbar(symbol, session_date, 9, 42, open_=str(breakout_close), high=str(breakout_close + Decimal("2")), low=str(breakout_close - Decimal("0.1")), close=str(breakout_close + Decimal("1.9")), volume=10_000))
    return bars


def synthetic_spy_bars(session_dates: list[date]) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for session_date in session_dates:
        for minute in range(20):
            bars.append(_mbar("SPY", session_date, 9, 30 + minute, open_="500", high="501", low="499.5", close="500.5", volume=100_000))
    return bars


SESSION_DATE = date(2026, 8, 14)
CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])
HISTORICAL_DATES = _prior_sessions(SESSION_DATE, count=LOOKBACK_DAYS)


def _market_bars(gap_percent: Decimal, rvol_multiple: Decimal, *, symbol: str = "AAPL", breakout: bool = True) -> dict[str, list[MarketBar]]:
    candidate = synthetic_symbol_bars(
        symbol, SESSION_DATE, gap_percent=gap_percent, rvol_multiple=rvol_multiple,
        lookback_days=LOOKBACK_DAYS, breakout=breakout, historical_dates=HISTORICAL_DATES,
    )
    spy = synthetic_spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    return {symbol: candidate, "SPY": spy}


def _minute_bars(market_bars: dict[str, list[MarketBar]]) -> dict[str, list]:
    return {symbol: [bar.to_minute_bar() for bar in bars] for symbol, bars in market_bars.items()}


# ---------------------------------------------------------------------------
# Parameter matrix and in-memory-only config override.
# ---------------------------------------------------------------------------


def test_parameter_matrix_is_deterministic_12_combinations():
    matrix = parameter_matrix()
    assert len(matrix) == 12
    assert len(set(matrix)) == 12
    assert matrix == parameter_matrix()  # deterministic across calls
    assert matrix[0] == baseline_combo() == ParameterCombo(Decimal("1.00"), Decimal("3.0"))
    assert set(c.gap_percent for c in matrix) == set(GAP_THRESHOLDS)
    assert set(c.rvol_multiple for c in matrix) == set(RVOL_THRESHOLDS)


def test_sensitivity_config_overrides_only_gap_and_rvol_in_memory():
    base = load_config()
    overridden = sensitivity_config(base, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.5"))

    assert overridden["signal"]["minimum_gap_percent"] == 0.5
    assert overridden["signal"]["minimum_relative_volume"] == 1.5
    # Every other key is carried through unchanged.
    assert overridden["signal"]["opening_range_minutes"] == base["signal"]["opening_range_minutes"]
    assert overridden["signal"]["entry_start"] == base["signal"]["entry_start"]
    assert overridden["signal"]["entry_end"] == base["signal"]["entry_end"]
    assert overridden["signal"]["require_spy_above_vwap"] == base["signal"]["require_spy_above_vwap"]
    assert overridden["universe"]["symbols"] == base["universe"]["symbols"]
    assert overridden["exit"] == base["exit"]

    # The base mapping passed in is never mutated.
    assert base["signal"]["minimum_gap_percent"] == 1.0
    assert base["signal"]["minimum_relative_volume"] == 3.0
    assert base["strategy"]["enabled"] is False


def test_sensitivity_config_never_touches_tracked_config_file():
    from pathlib import Path

    path = Path(REPO_ROOT_CONFIG_PATH)
    before = path.read_bytes()
    base = load_config()
    for combo in parameter_matrix():
        sensitivity_config(base, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    after = path.read_bytes()
    assert before == after
    # A fresh load still reflects the real, untouched production values.
    reloaded = load_config()
    assert reloaded["signal"]["minimum_gap_percent"] == 1.0
    assert reloaded["signal"]["minimum_relative_volume"] == 3.0
    assert reloaded["strategy"]["enabled"] is False


# ---------------------------------------------------------------------------
# evaluate_combo reuses the canonical evaluator, does not reimplement it.
# ---------------------------------------------------------------------------


def test_evaluate_combo_matches_direct_canonical_evaluation():
    market_bars = _market_bars(Decimal("0.75"), Decimal("2.0"))
    minute_bars = _minute_bars(market_bars)
    combo = ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("2.0"))

    combo_evaluation = _evaluate(combo, ["AAPL"], [SESSION_DATE], minute_bars)

    direct_config = sensitivity_config(CONFIG, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    direct_evaluation, direct_opening, direct_live, direct_decision = evaluate_symbol_session_with_signal(
        symbol="AAPL", session_date=SESSION_DATE, candidate_bars=minute_bars["AAPL"],
        spy_bars=minute_bars["SPY"], config=direct_config,
    )

    assert direct_evaluation.state == CANDIDATE
    assert len(combo_evaluation.candidate_signals) == 1
    symbol, session_date, opening_data, live_data, decision = combo_evaluation.candidate_signals[0]
    assert (symbol, session_date) == ("AAPL", SESSION_DATE)
    assert opening_data == direct_opening
    assert live_data == direct_live
    assert decision == direct_decision


def test_evaluate_combo_relaxed_threshold_admits_previously_eliminated_day():
    # gap=0.75% clears a 0.80% real gap; gap=1.00% (baseline) does not.
    market_bars = _market_bars(Decimal("0.80"), Decimal("3.0"))
    minute_bars = _minute_bars(market_bars)

    baseline_eval = _evaluate(baseline_combo(), ["AAPL"], [SESSION_DATE], minute_bars)
    relaxed_eval = _evaluate(ParameterCombo(Decimal("0.75"), Decimal("3.0")), ["AAPL"], [SESSION_DATE], minute_bars)

    assert len(baseline_eval.candidate_signals) == 0
    assert len(relaxed_eval.candidate_signals) == 1


# ---------------------------------------------------------------------------
# Matrix aggregation.
# ---------------------------------------------------------------------------


def test_matrix_row_aggregates_max_per_day_and_concentration():
    combo = ParameterCombo(Decimal("0.50"), Decimal("1.5"))
    session_dates = [SESSION_DATE, SESSION_DATE + timedelta(days=1)]
    while session_dates[1].weekday() >= 5:
        session_dates[1] += timedelta(days=1)

    aapl_bars = synthetic_symbol_bars("AAPL", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("2.0"), lookback_days=LOOKBACK_DAYS, historical_dates=HISTORICAL_DATES)
    msft_bars = synthetic_symbol_bars("MSFT", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("2.0"), lookback_days=LOOKBACK_DAYS, historical_dates=HISTORICAL_DATES)
    spy_bars = synthetic_spy_bars(HISTORICAL_DATES + session_dates)

    minute_bars = {
        "AAPL": [b.to_minute_bar() for b in aapl_bars],
        "MSFT": [b.to_minute_bar() for b in msft_bars],
        "SPY": [b.to_minute_bar() for b in spy_bars],
    }

    evaluation = _evaluate(combo, ["AAPL", "MSFT"], session_dates, minute_bars)
    row = matrix_row(evaluation)

    assert row["sessions_studied"] == 2
    assert row["sessions_with_candidate"] == 1  # both candidates land on SESSION_DATE only
    assert row["total_candidates"] == 2
    assert row["max_candidates_one_day"] == 2
    assert row["zero_candidate_sessions"] == 1
    assert row["candidates_by_symbol"] == {"AAPL": 1, "MSFT": 1}
    assert row["concentration"]["top_1_share"] == pytest.approx(0.5)
    assert row["coverage_status"] in ("VALID", "INVALID_INSUFFICIENT_COVERAGE")


# ---------------------------------------------------------------------------
# M43.5 (Issue #338): `precompute_inputs_table` now always date-indexes each
# symbol's (and SPY's) bars once and forwards that index to
# `precompute_session_signal_inputs` -- the one caller for which the
# one-time index-build cost is worth paying, since it evaluates every
# session for a symbol. This is the "most important integration test":
# proving the indexed production path is byte-identical, across multiple
# symbols and multiple session dates in the same table, to a test-only
# reference loop that calls `precompute_session_signal_inputs` the original
# way (no index -- the literal pre-M43.5 implementation, still the default
# when the two new parameters are omitted).
# ---------------------------------------------------------------------------


def _reference_precompute_inputs_table(*, universe, session_dates, minute_bars_by_symbol, spy_minute_bars, base_config):
    """Test-only reference: the exact pre-M43.5 `precompute_inputs_table`
    body, calling `precompute_session_signal_inputs` without an index for
    every (symbol, session_date) pair -- i.e. always the full-history-rescan
    path. Not used at runtime; exists solely so the tests below can assert
    the indexed production implementation never diverges from it."""
    table = {}
    for symbol in universe:
        candidate_bars = minute_bars_by_symbol[symbol]
        for session_date in session_dates:
            table[(symbol, session_date)] = precompute_session_signal_inputs(
                symbol=symbol, session_date=session_date, candidate_bars=candidate_bars,
                spy_bars=spy_minute_bars, config=base_config,
            )
    return table


def test_precompute_inputs_table_index_parity_multi_symbol_multi_session():
    session_dates = [SESSION_DATE, SESSION_DATE + timedelta(days=1)]
    while session_dates[1].weekday() >= 5:
        session_dates[1] += timedelta(days=1)

    aapl_bars = synthetic_symbol_bars("AAPL", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("2.0"), lookback_days=LOOKBACK_DAYS, historical_dates=HISTORICAL_DATES)
    msft_bars = synthetic_symbol_bars("MSFT", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("2.0"), lookback_days=LOOKBACK_DAYS, historical_dates=HISTORICAL_DATES, breakout=False)
    spy_bars = synthetic_spy_bars(HISTORICAL_DATES + session_dates)

    minute_bars = {
        "AAPL": [b.to_minute_bar() for b in aapl_bars],
        "MSFT": [b.to_minute_bar() for b in msft_bars],
        "SPY": [b.to_minute_bar() for b in spy_bars],
    }

    reference = _reference_precompute_inputs_table(
        universe=["AAPL", "MSFT"], session_dates=session_dates, minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    indexed = precompute_inputs_table(
        universe=["AAPL", "MSFT"], session_dates=session_dates, minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )

    assert indexed.keys() == reference.keys()
    for key in reference:
        assert indexed[key] == reference[key], f"parity mismatch at {key}"
    # Sanity: the fixture actually reaches a mix of outcomes, not every key
    # trivially DATA_INCOMPLETE (a table where every entry is identically
    # 'missing everything' could pass an equality check without exercising
    # the indexed lookups at all).
    assert any(v.incomplete_reason is None for v in reference.values())
    assert any(v.incomplete_reason is not None for v in reference.values())


def test_precompute_inputs_table_index_parity_deterministic_repeated_execution():
    aapl_bars = synthetic_symbol_bars("AAPL", SESSION_DATE, gap_percent=Decimal("0.60"), rvol_multiple=Decimal("2.0"), lookback_days=LOOKBACK_DAYS, historical_dates=HISTORICAL_DATES)
    spy_bars = synthetic_spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    minute_bars = {"AAPL": [b.to_minute_bar() for b in aapl_bars], "SPY": [b.to_minute_bar() for b in spy_bars]}

    first = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    second = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )

    assert first == second


# ---------------------------------------------------------------------------
# Coverage remains fail-closed -- reused unmodified from build_study_report.
# ---------------------------------------------------------------------------


def test_matrix_row_coverage_status_reflects_incomplete_data():
    combo = baseline_combo()
    minute_bars = {"AAPL": [], "SPY": []}  # no bars at all -> DATA_INCOMPLETE every session
    evaluation = _evaluate(combo, ["AAPL"], [SESSION_DATE], minute_bars)
    row = matrix_row(evaluation)
    assert row["coverage_status"] == "INVALID_INSUFFICIENT_COVERAGE"
    assert row["coverage_pct"] == 0.0
    assert row["total_candidates"] == 0


# ---------------------------------------------------------------------------
# Incremental candidate accounting.
# ---------------------------------------------------------------------------


def test_incremental_effect_reports_newly_admitted_symbol_days():
    market_bars = _market_bars(Decimal("0.80"), Decimal("3.0"))
    minute_bars = _minute_bars(market_bars)

    from_eval = _evaluate(baseline_combo(), ["AAPL"], [SESSION_DATE], minute_bars)
    to_eval = _evaluate(ParameterCombo(Decimal("0.75"), Decimal("3.0")), ["AAPL"], [SESSION_DATE], minute_bars)

    effect = incremental_effect(from_eval, to_eval, axis="gap", held_fixed="rvol=3.0x")
    assert effect.additional_candidate_days == 1
    assert effect.additional_candidate_occurrences == 1
    assert effect.newly_admitted == ({"symbol": "AAPL", "date": SESSION_DATE.isoformat()},)


def test_all_incremental_effects_covers_every_adjacent_step_and_is_monotonic():
    market_bars = _market_bars(Decimal("0.60"), Decimal("2.2"))
    minute_bars = _minute_bars(market_bars)

    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    evaluations_by_combo = {
        combo: evaluate_combo(
            combo=combo, universe=["AAPL"], session_dates=[SESSION_DATE],
            inputs_table=inputs_table, base_config=CONFIG,
        )
        for combo in parameter_matrix()
    }
    effects = all_incremental_effects(evaluations_by_combo)
    expected_count = len(GAP_THRESHOLDS) * (len(RVOL_THRESHOLDS) - 1) + len(RVOL_THRESHOLDS) * (len(GAP_THRESHOLDS) - 1)
    assert len(effects) == expected_count
    # Relaxing a threshold can only ever add candidates, never remove one --
    # every step's delta must be non-negative for this single-symbol fixture.
    assert all(effect["additional_candidate_days"] >= 0 for effect in effects)
    assert all(effect["additional_candidate_occurrences"] >= 0 for effect in effects)


def test_gap_and_rvol_relaxation_steps_are_adjacent_pairs_in_order():
    assert gap_relaxation_steps() == ((Decimal("1.00"), Decimal("0.75")), (Decimal("0.75"), Decimal("0.50")))
    assert rvol_relaxation_steps() == (
        (Decimal("3.0"), Decimal("2.5")), (Decimal("2.5"), Decimal("2.0")), (Decimal("2.0"), Decimal("1.5")),
    )


# ---------------------------------------------------------------------------
# Frequency targets.
# ---------------------------------------------------------------------------


def test_frequency_target_hits_only_lists_combinations_that_clear_each_bar():
    rows = [
        {"gap_percent": "1.00", "rvol_multiple": "3.0", "candidate_day_rate": 0.10},
        {"gap_percent": "0.75", "rvol_multiple": "2.0", "candidate_day_rate": 0.45},
        {"gap_percent": "0.50", "rvol_multiple": "1.5", "candidate_day_rate": 0.72},
    ]
    hits = frequency_target_hits(rows)
    assert set(hits) == {f">={int(t * 100)}%" for t in FREQUENCY_TARGETS}
    assert hits[">=25%"] == ["gap=0.75% rvol=2.0x", "gap=0.50% rvol=1.5x"]
    assert hits[">=40%"] == ["gap=0.75% rvol=2.0x", "gap=0.50% rvol=1.5x"]
    assert hits[">=60%"] == ["gap=0.50% rvol=1.5x"]
    assert hits[">=70%"] == ["gap=0.50% rvol=1.5x"]


# ---------------------------------------------------------------------------
# Ranking / recommendation.
# ---------------------------------------------------------------------------


def _fake_row(gap: str, rvol: str, *, rate: float, coverage: str = "VALID", top3: float = 0.5, days: int | None = None):
    return {
        "gap_percent": gap, "rvol_multiple": rvol, "candidate_day_rate": rate, "coverage_status": coverage,
        "sessions_with_candidate": days if days is not None else int(rate * 90),
        "concentration": {"top_3_share": top3},
    }


def test_build_recommendation_identifies_baseline_and_frequency_extremes():
    rows_by_combo = {}
    for combo in parameter_matrix():
        rate = {
            (Decimal("1.00"), Decimal("3.0")): 0.10,
            (Decimal("1.00"), Decimal("2.5")): 0.15,
            (Decimal("0.75"), Decimal("3.0")): 0.18,
            (Decimal("0.50"), Decimal("1.5")): 0.65,
        }.get((combo.gap_percent, combo.rvol_multiple), 0.20)
        rows_by_combo[combo] = _fake_row(str(combo.gap_percent), str(combo.rvol_multiple), rate=rate)

    recommendation = build_recommendation(rows_by_combo)

    assert recommendation["baseline"] == {"combo": baseline_combo().label, "gap_percent": "1.00", "rvol_multiple": "3.0"}
    assert recommendation["highest_frequency"]["gap_percent"] == "0.50"
    assert recommendation["highest_frequency"]["rvol_multiple"] == "1.5"
    assert recommendation["first_exceeding_50pct_candidate_days"] is not None
    assert recommendation["smallest_useful_relaxation"] is not None
    assert recommendation["recommended_for_further_validation"] is not None


def test_build_recommendation_no_combo_exceeding_50pct_reports_none():
    rows_by_combo = {combo: _fake_row(str(combo.gap_percent), str(combo.rvol_multiple), rate=0.05) for combo in parameter_matrix()}
    recommendation = build_recommendation(rows_by_combo)
    assert recommendation["first_exceeding_50pct_candidate_days"] is None


def test_relaxation_distance_zero_at_baseline_and_positive_away_from_it():
    assert relaxation_distance(baseline_combo()) == 0
    assert relaxation_distance(ParameterCombo(Decimal("0.50"), Decimal("1.5"))) == 2 + 3


# ---------------------------------------------------------------------------
# Trade quality: reuses the merged shadow-decision / execution-simulator
# path unchanged.
# ---------------------------------------------------------------------------


def test_simulate_combo_trade_quality_produces_a_resolved_trade():
    combo = ParameterCombo(Decimal("0.60"), Decimal("2.0"))
    market_bars = _market_bars(Decimal("0.60"), Decimal("2.2"))
    minute_bars = _minute_bars(market_bars)

    evaluation = _evaluate(combo, ["AAPL"], [SESSION_DATE], minute_bars)
    assert len(evaluation.candidate_signals) == 1

    trade_quality = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol=market_bars, base_config=CONFIG,
        implementation_digest="a" * 64, created_at=datetime.now(timezone.utc),
    )

    assert trade_quality["status"] == "EVALUATED"
    assert trade_quality["candidates_evaluated"] == 1
    assert trade_quality["trades_entered"] == 1
    # The fixture's breakout bar rallies hard afterward -- entered, not eliminated as ineligible/ambiguous.
    assert trade_quality["ambiguous_outcomes"] == 0


def test_simulate_combo_trade_quality_no_candidates_reports_status():
    combo = baseline_combo()
    minute_bars = {"AAPL": [], "SPY": []}
    evaluation = _evaluate(combo, ["AAPL"], [SESSION_DATE], minute_bars)
    trade_quality = simulate_combo_trade_quality(
        evaluation=evaluation, market_bars_by_symbol={"AAPL": [], "SPY": []}, base_config=CONFIG,
        implementation_digest="a" * 64, created_at=datetime.now(timezone.utc),
    )
    assert trade_quality == aggregate_trade_quality([])
    assert trade_quality["status"] == "NO_CANDIDATES_TO_SIMULATE"


def test_aggregate_trade_quality_splits_winners_losers_and_computes_expectancy():
    from src.shadow_execution_simulator import ShadowExecutionState, simulate_shadow_execution
    from src.shadow_trading_decision import ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest

    day = date(2026, 8, 11)
    signal_ts = datetime(2026, 8, 11, 14, 40, tzinfo=timezone.utc)

    def candidate(entry="100", stop="99"):
        digest = compute_shadow_decision_digest(
            shadow_run_id="fixture", symbol="AAPL", trading_date=day, strategy_version="orb_long_v0-test",
            implementation_digest="a" * 64, market_data_digest="b" * 64, opening_range_identity="c" * 64,
            signal_timestamp=signal_ts, decision_type=ShadowDecisionType.LONG_CANDIDATE,
            proposed_entry=Decimal(entry), proposed_stop=Decimal(stop), proposed_target=None,
            reason="fixture", market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
        )
        return ShadowTradingDecision(
            shadow_run_id="fixture", decision_id=digest, symbol="AAPL", trading_date=day,
            strategy_version="orb_long_v0-test", implementation_digest="a" * 64, market_data_digest="b" * 64,
            opening_range_identity="c" * 64, signal_timestamp=signal_ts, decision_type=ShadowDecisionType.LONG_CANDIDATE,
            proposed_entry=Decimal(entry), proposed_stop=Decimal(stop), proposed_target=None, reason="fixture",
            market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR", created_at=signal_ts,
        )

    def bar(minute, *, open="100", high="100.5", low="99.5", close="100"):
        ts = signal_ts + timedelta(minutes=minute)
        return MarketBar(
            symbol="AAPL", exchange_session="REGULAR", trading_date=day, event_timestamp=ts,
            end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
            open=Decimal(open), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=100,
            source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
        )

    winner = simulate_shadow_execution(candidate(), [bar(1), bar(2, open="100.2", low="100.1", high="102.1", close="100.2")])
    assert winner.state == ShadowExecutionState.TARGET_HIT
    loser = simulate_shadow_execution(candidate(), [bar(1), bar(2, low="98.9", high="100.2")])
    assert loser.state == ShadowExecutionState.STOPPED

    aggregate = aggregate_trade_quality([winner, loser])
    assert aggregate["status"] == "EVALUATED"
    assert aggregate["winners"] == 1
    assert aggregate["losers"] == 1
    assert aggregate["trades_closed"] == 2
    assert aggregate["win_rate"] == pytest.approx(0.5)
    assert Decimal(aggregate["average_winner"]) == Decimal("2")
    assert Decimal(aggregate["average_loser"]) == Decimal("-1")
    assert Decimal(aggregate["expectancy_per_trade"]) == Decimal("0.5")
    assert Decimal(aggregate["profit_factor"]) == Decimal("2")
    assert aggregate["stop_out_rate"] == pytest.approx(0.5)
    assert aggregate["ambiguous_outcomes"] == 0
