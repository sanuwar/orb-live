from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_fine_rvol_validation_research import (
    GAP_THRESHOLDS,
    RVOL_THRESHOLDS,
    ParameterCombo,
    all_incremental_effects,
    assess_rvol_1_25_signal,
    assess_rvol_1_50_robustness,
    build_recommendation,
    evaluate_combo,
    gap_relaxation_steps,
    matrix_row,
    parameter_matrix,
    precompute_inputs_table,
    production_baseline_combo,
    relaxation_distance,
    rvol_relaxation_steps,
    sensitivity_config,
    split_sessions_chronologically,
)

REPO_ROOT_CONFIG_PATH = "config/strategy_v0.yaml"
CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])
SESSION_DATE = date(2026, 8, 14)


def _prior_sessions(first_session: date, *, count: int) -> list[date]:
    import src.exchange_calendar as cal
    dates: list[date] = []
    cursor = first_session - timedelta(days=1)
    while len(dates) < count:
        if cal.regular_session_bounds(cursor) is not None:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


HISTORICAL_DATES = _prior_sessions(SESSION_DATE, count=LOOKBACK_DAYS)


def _mbar(symbol, session_date, hh, mm, *, open_, high, low, close, volume):
    ts = datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=session_date, event_timestamp=ts,
        end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


def synthetic_symbol_bars(symbol: str, session_date: date, *, gap_percent: Decimal, rvol_multiple: Decimal, breakout: bool = True) -> list[MarketBar]:
    """Minimal fixture (same construction as
    `tests/test_orb_gap_rvol_sensitivity_research.py`'s own helper): fixed
    historical opening volume, a study-day opening range gapped up by
    exactly `gap_percent` with volume scaled to exactly `rvol_multiple` x
    the historical median, then an entry-window bar closing above the
    opening-range high."""
    bars: list[MarketBar] = []
    for prior_date in HISTORICAL_DATES:
        for minute in range(5):
            bars.append(_mbar(symbol, prior_date, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))
    previous_close = Decimal("100")
    today_open = previous_close * (Decimal("1") + gap_percent / Decimal("100"))
    opening_bar_volume = int((rvol_multiple * Decimal("500000")) / 5)
    opening_high = today_open + Decimal("0.5")
    opening_low = today_open - Decimal("0.3")
    for minute in range(5):
        bars.append(_mbar(symbol, session_date, 9, 30 + minute, open_=str(today_open), high=str(opening_high), low=str(opening_low), close=str(today_open), volume=opening_bar_volume))
    breakout_close = opening_high + Decimal("1") if breakout else opening_low
    bars.append(_mbar(symbol, session_date, 9, 40, open_=str(today_open), high=str(breakout_close + Decimal("0.5")), low=str(opening_low), close=str(breakout_close), volume=50_000))
    return bars


def synthetic_spy_bars(session_dates: list[date]) -> list[MarketBar]:
    bars: list[MarketBar] = []
    for session_date in session_dates:
        for minute in range(20):
            bars.append(_mbar("SPY", session_date, 9, 30 + minute, open_="500", high="501", low="499.5", close="500.5", volume=100_000))
    return bars


def _minute_bars(gap_percent: Decimal, rvol_multiple: Decimal, session_date: date = SESSION_DATE, *, symbol: str = "AAPL") -> dict[str, list]:
    candidate = synthetic_symbol_bars(symbol, session_date, gap_percent=gap_percent, rvol_multiple=rvol_multiple)
    spy = synthetic_spy_bars(HISTORICAL_DATES + [session_date])
    return {
        symbol: [b.to_minute_bar() for b in candidate],
        "SPY": [b.to_minute_bar() for b in spy],
    }


# ---------------------------------------------------------------------------
# Parameter matrix: exact 12-combination fine-RVOL grid, deterministic.
# ---------------------------------------------------------------------------


def test_parameter_matrix_is_deterministic_12_combinations():
    matrix = parameter_matrix()
    assert len(matrix) == 12
    assert len(set(matrix)) == 12
    assert matrix == parameter_matrix()
    assert set(c.gap_percent for c in matrix) == set(GAP_THRESHOLDS)
    assert set(c.rvol_multiple for c in matrix) == set(RVOL_THRESHOLDS)


def test_fine_rvol_grid_matches_the_requested_thresholds():
    assert GAP_THRESHOLDS == (Decimal("1.00"), Decimal("0.75"), Decimal("0.50"))
    assert RVOL_THRESHOLDS == (Decimal("2.00"), Decimal("1.75"), Decimal("1.50"), Decimal("1.25"))


def test_gap_and_rvol_relaxation_steps_are_adjacent_pairs_in_order():
    assert gap_relaxation_steps() == ((Decimal("1.00"), Decimal("0.75")), (Decimal("0.75"), Decimal("0.50")))
    assert rvol_relaxation_steps() == (
        (Decimal("2.00"), Decimal("1.75")), (Decimal("1.75"), Decimal("1.50")), (Decimal("1.50"), Decimal("1.25")),
    )


def test_relaxation_distance_zero_at_strictest_corner():
    assert relaxation_distance(ParameterCombo(Decimal("1.00"), Decimal("2.00"))) == 0
    assert relaxation_distance(ParameterCombo(Decimal("0.50"), Decimal("1.25"))) == 2 + 3


def test_production_baseline_combo_is_the_real_production_thresholds_not_in_this_grid():
    baseline = production_baseline_combo()
    assert baseline == ParameterCombo(Decimal("1.00"), Decimal("3.0"))
    assert baseline not in parameter_matrix()


# ---------------------------------------------------------------------------
# Production config unchanged.
# ---------------------------------------------------------------------------


def test_sensitivity_config_never_touches_tracked_config_file():
    path = Path(REPO_ROOT_CONFIG_PATH)
    before = path.read_bytes()
    base = load_config()
    for combo in parameter_matrix():
        sensitivity_config(base, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    after = path.read_bytes()
    assert before == after
    reloaded = load_config()
    assert reloaded["signal"]["minimum_gap_percent"] == 1.0
    assert reloaded["signal"]["minimum_relative_volume"] == 3.0
    assert reloaded["strategy"]["enabled"] is False


# ---------------------------------------------------------------------------
# Incremental RVOL/gap comparisons across the fine grid.
# ---------------------------------------------------------------------------


def test_all_incremental_effects_covers_every_adjacent_step_in_the_fine_grid():
    minute_bars = _minute_bars(Decimal("0.60"), Decimal("1.60"))
    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    evaluations_by_combo = {
        combo: evaluate_combo(combo=combo, universe=["AAPL"], session_dates=[SESSION_DATE], inputs_table=inputs_table, base_config=CONFIG)
        for combo in parameter_matrix()
    }
    effects = all_incremental_effects(evaluations_by_combo)
    expected_count = len(GAP_THRESHOLDS) * (len(RVOL_THRESHOLDS) - 1) + len(RVOL_THRESHOLDS) * (len(GAP_THRESHOLDS) - 1)
    assert expected_count == 17
    assert len(effects) == expected_count
    assert all(effect["additional_candidate_days"] >= 0 for effect in effects)
    assert all(effect["additional_candidate_occurrences"] >= 0 for effect in effects)


def test_rvol_relaxation_admits_a_previously_eliminated_day():
    # rvol=1.75x clears a real 1.8x RVOL day; rvol=2.00x (this grid's strictest) does not.
    minute_bars = _minute_bars(Decimal("0.60"), Decimal("1.80"))
    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    strict_eval = evaluate_combo(
        combo=ParameterCombo(Decimal("0.60"), Decimal("2.00")), universe=["AAPL"], session_dates=[SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    relaxed_eval = evaluate_combo(
        combo=ParameterCombo(Decimal("0.60"), Decimal("1.75")), universe=["AAPL"], session_dates=[SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    assert len(strict_eval.candidate_signals) == 0
    assert len(relaxed_eval.candidate_signals) == 1


# ---------------------------------------------------------------------------
# Coverage fail-closed behavior (reused unmodified from build_study_report).
# ---------------------------------------------------------------------------


def test_matrix_row_coverage_status_reflects_incomplete_data():
    minute_bars = {"AAPL": [], "SPY": []}
    inputs_table = precompute_inputs_table(
        universe=["AAPL"], session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    evaluation = evaluate_combo(
        combo=ParameterCombo(Decimal("1.00"), Decimal("2.00")), universe=["AAPL"], session_dates=[SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    row = matrix_row(evaluation)
    assert row["coverage_status"] == "INVALID_INSUFFICIENT_COVERAGE"
    assert row["coverage_pct"] == 0.0
    assert row["total_candidates"] == 0


# ---------------------------------------------------------------------------
# Chronological split calculations.
# ---------------------------------------------------------------------------


def test_split_sessions_chronologically_is_deterministic_and_order_independent():
    dates = [date(2026, 1, d) for d in (5, 1, 3, 2, 4)]
    first_half, second_half = split_sessions_chronologically(dates)
    assert first_half == (date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3))
    assert second_half == (date(2026, 1, 4), date(2026, 1, 5))
    assert first_half + second_half == tuple(sorted(dates))
    # Order-independent: shuffling the input yields the identical split.
    reversed_split = split_sessions_chronologically(list(reversed(dates)))
    assert reversed_split == (first_half, second_half)


def test_split_sessions_chronologically_even_count_splits_evenly():
    dates = [date(2026, 1, d) for d in (1, 2, 3, 4)]
    first_half, second_half = split_sessions_chronologically(dates)
    assert len(first_half) == 2
    assert len(second_half) == 2


def test_assess_rvol_1_50_robustness_true_when_both_halves_improve():
    gap = Decimal("0.50")
    strict = ParameterCombo(gap, Decimal("2.00"))
    target = ParameterCombo(gap, Decimal("1.50"))
    first_half_rows = {strict: {"candidate_day_rate": 0.10}, target: {"candidate_day_rate": 0.30}}
    second_half_rows = {strict: {"candidate_day_rate": 0.12}, target: {"candidate_day_rate": 0.28}}
    result = assess_rvol_1_50_robustness(first_half_rows=first_half_rows, second_half_rows=second_half_rows, gap=gap)
    assert result["verdict"] == "ROBUST_ACROSS_BOTH_HALVES"
    assert result["first_half_delta"] == pytest.approx(0.20)
    assert result["second_half_delta"] == pytest.approx(0.16)


def test_assess_rvol_1_50_robustness_false_when_one_half_does_not_improve():
    gap = Decimal("0.50")
    strict = ParameterCombo(gap, Decimal("2.00"))
    target = ParameterCombo(gap, Decimal("1.50"))
    first_half_rows = {strict: {"candidate_day_rate": 0.10}, target: {"candidate_day_rate": 0.30}}
    second_half_rows = {strict: {"candidate_day_rate": 0.20}, target: {"candidate_day_rate": 0.18}}
    result = assess_rvol_1_50_robustness(first_half_rows=first_half_rows, second_half_rows=second_half_rows, gap=gap)
    assert result["verdict"] == "NOT_ROBUST_SAMPLE_ARTIFACT_SUSPECTED"
    assert result["second_half_delta"] < 0


# ---------------------------------------------------------------------------
# Recommendation calculations, including the 1.25x signal assessment.
# ---------------------------------------------------------------------------


def _fake_row(gap: str, rvol: str, *, rate: float, coverage: str = "VALID", top3: float = 0.5, days: int | None = None, total_candidates: int | None = None):
    return {
        "gap_percent": gap, "rvol_multiple": rvol, "candidate_day_rate": rate, "coverage_status": coverage,
        "sessions_with_candidate": days if days is not None else int(rate * 90),
        "total_candidates": total_candidates if total_candidates is not None else int(rate * 90),
        "concentration": {"top_3_share": top3},
    }


def _fake_rows_by_combo(overrides: dict) -> dict:
    rows = {}
    for combo in parameter_matrix():
        rate = overrides.get((combo.gap_percent, combo.rvol_multiple), 0.15)
        rows[combo] = _fake_row(str(combo.gap_percent), str(combo.rvol_multiple), rate=rate)
    return rows


def test_build_recommendation_identifies_baseline_and_frequency_extremes():
    rows_by_combo = _fake_rows_by_combo({
        (Decimal("1.00"), Decimal("2.00")): 0.15,
        (Decimal("0.50"), Decimal("1.25")): 0.60,
    })
    baseline_row = _fake_row("1.00", "3.0", rate=0.1333, days=12)
    robustness = {"verdict": "ROBUST_ACROSS_BOTH_HALVES"}
    signal = {"verdict": "USEFUL_SIGNAL"}

    recommendation = build_recommendation(
        rows_by_combo, production_baseline_row=baseline_row, robustness=robustness, rvol_1_25_signal=signal,
    )

    assert recommendation["production_baseline"]["gap_percent"] == "1.00"
    assert recommendation["production_baseline"]["rvol_multiple"] == "3.0"
    assert recommendation["highest_frequency"]["gap_percent"] == "0.50"
    assert recommendation["highest_frequency"]["rvol_multiple"] == "1.25"
    assert recommendation["first_exceeding_50pct_candidate_days"] is not None
    assert recommendation["smallest_useful_relaxation"] is not None
    assert recommendation["recommended_for_further_validation"] is not None
    assert recommendation["rvol_1_50x_robustness"] == robustness
    assert recommendation["rvol_1_25x_signal_assessment"] == signal


def test_build_recommendation_no_combo_exceeding_50pct_reports_none():
    rows_by_combo = _fake_rows_by_combo({})
    baseline_row = _fake_row("1.00", "3.0", rate=0.1333, days=12)
    recommendation = build_recommendation(
        rows_by_combo, production_baseline_row=baseline_row,
        robustness={"verdict": "NOT_ROBUST_SAMPLE_ARTIFACT_SUSPECTED"}, rvol_1_25_signal={"verdict": "NO_ADDITIONAL_FREQUENCY"},
    )
    assert recommendation["first_exceeding_50pct_candidate_days"] is None


def test_assess_rvol_1_25_signal_useful_when_frequency_grows_without_quality_loss():
    gap = Decimal("0.50")
    rows_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): _fake_row("0.50", "1.50", rate=0.44, days=40, total_candidates=61, top3=0.41),
        ParameterCombo(gap, Decimal("1.25")): _fake_row("0.50", "1.25", rate=0.55, days=50, total_candidates=75, top3=0.35),
    }
    trade_quality_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): {"expectancy_per_trade": "1.0"},
        ParameterCombo(gap, Decimal("1.25")): {"expectancy_per_trade": "1.1"},
    }
    result = assess_rvol_1_25_signal(rows_by_combo=rows_by_combo, trade_quality_by_combo=trade_quality_by_combo, gap=gap)
    assert result["verdict"] == "USEFUL_SIGNAL"
    assert result["additional_candidate_days_vs_1.50x"] == 10
    assert result["expectancy_degrades"] is False


def test_assess_rvol_1_25_signal_mostly_noise_when_quality_degrades_on_both_axes():
    gap = Decimal("0.50")
    rows_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): _fake_row("0.50", "1.50", rate=0.44, days=40, total_candidates=61, top3=0.41),
        ParameterCombo(gap, Decimal("1.25")): _fake_row("0.50", "1.25", rate=0.48, days=43, total_candidates=68, top3=0.55),
    }
    trade_quality_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): {"expectancy_per_trade": "1.0"},
        ParameterCombo(gap, Decimal("1.25")): {"expectancy_per_trade": "0.3"},
    }
    result = assess_rvol_1_25_signal(rows_by_combo=rows_by_combo, trade_quality_by_combo=trade_quality_by_combo, gap=gap)
    assert result["verdict"] == "MOSTLY_NOISE"
    assert result["expectancy_degrades"] is True
    assert result["concentration_worsens"] is True


def test_assess_rvol_1_25_signal_no_additional_frequency_when_days_do_not_grow():
    gap = Decimal("0.50")
    rows_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): _fake_row("0.50", "1.50", rate=0.44, days=40, total_candidates=61, top3=0.41),
        ParameterCombo(gap, Decimal("1.25")): _fake_row("0.50", "1.25", rate=0.44, days=40, total_candidates=61, top3=0.41),
    }
    trade_quality_by_combo = {
        ParameterCombo(gap, Decimal("1.50")): {"expectancy_per_trade": "1.0"},
        ParameterCombo(gap, Decimal("1.25")): {"expectancy_per_trade": "1.0"},
    }
    result = assess_rvol_1_25_signal(rows_by_combo=rows_by_combo, trade_quality_by_combo=trade_quality_by_combo, gap=gap)
    assert result["verdict"] == "NO_ADDITIONAL_FREQUENCY"
