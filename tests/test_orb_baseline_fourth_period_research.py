"""Tests for the ORB C_BASE fourth-period baseline validation study
(Issue #321, `src/orb_baseline_fourth_period_research.py`).

Covers: the freshness policy is derived from the actual requested window
(not a hardcoded magic number), algebraic recombination of trade-quality
aggregates is exact (cross-checked against #320's real-row ground truth
elsewhere in this session), the predeclared BASELINE_SUPPORTED_FOR_EXIT_RESEARCH/
REGIME_DEPENDENCE_CONFIRMED/BASELINE_NOT_SUPPORTED/INSUFFICIENT_EVIDENCE
classification and its seven criteria, the next-milestone rule (including
that REGIME_DEPENDENCE_CONFIRMED does not automatically request a fifth
period), and that this module never touches production configuration.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from src.orb_baseline_fourth_period_research import (
    ANCHORED_COHORT,
    CATASTROPHIC_EXPECTANCY_THRESHOLD,
    CATASTROPHIC_PROFIT_FACTOR_THRESHOLD,
    DEFAULT_BASELINE_CONCLUSION_CRITERIA,
    FOUR_PERIOD_STRONG_EVIDENCE_MIN_EXPECTANCY,
    FOUR_PERIOD_STRONG_EVIDENCE_MIN_PROFIT_FACTOR,
    P0_CONFIG,
    PRIOR_PERIODS_REFERENCE,
    PRIOR_QUARTERS_REFERENCE,
    BaselineConclusionCriteria,
    BaselineFourthPeriodError,
    classify_baseline_conclusion,
    combine_trade_quality_aggregates,
    derive_freshness_policy_for_window,
    four_period_evidence_is_strong_enough,
    is_p0_catastrophically_unstable,
    max_drawdown_and_streak_from_rows,
    recommend_next_milestone,
    symbol_total_pnl,
    trade_quality_excluding_symbol_from_aggregate,
)
from src.orb_baseline_stability_research import TradeDiagnosticRow
from src.orb_gap_rvol_sensitivity_research import ParameterCombo

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Config / cohort identity.
# ---------------------------------------------------------------------------


def test_p0_config_is_c_base_gap_050_rvol_150():
    from decimal import Decimal

    assert P0_CONFIG == ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.50"))


def test_anchored_cohort_matches_320_exactly():
    assert ANCHORED_COHORT == ("AAPL", "AMD", "AMZN", "BAC", "GOOGL", "INTC", "MSFT", "NVDA", "TSLA")


def test_prior_periods_reference_covers_all_three_periods():
    assert set(PRIOR_PERIODS_REFERENCE) == {"P1_THIRD", "P2_OOS", "P3_DISCOVERY"}


def test_prior_periods_p1_cohort_equals_native():
    p1 = PRIOR_PERIODS_REFERENCE["P1_THIRD"]
    assert p1["cohort"] == p1["native"]
    assert p1["cohort_one_trade_per_day"] == p1["native_one_trade_per_day"]


def test_prior_quarters_reference_has_ten_quarters():
    assert len(PRIOR_QUARTERS_REFERENCE) == 10
    assert "2024-Q2" in PRIOR_QUARTERS_REFERENCE
    assert "2026-Q3" in PRIOR_QUARTERS_REFERENCE


# ---------------------------------------------------------------------------
# Freshness policy: derived from the actual window, not a magic number.
# ---------------------------------------------------------------------------


def test_freshness_policy_covers_exact_window_plus_margin():
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    earliest = date(2023, 3, 3)
    policy = derive_freshness_policy_for_window(earliest_required_date=earliest, now=now, safety_margin_days=30, version="test-v1")
    expected_days = (now.date() - earliest).days + 30
    assert policy.max_age_seconds == pytest.approx(expected_days * 86400.0)


def test_freshness_policy_scales_with_window_not_fixed():
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    near = derive_freshness_policy_for_window(earliest_required_date=date(2026, 1, 1), now=now, version="v")
    far = derive_freshness_policy_for_window(earliest_required_date=date(2020, 1, 1), now=now, version="v")
    assert far.max_age_seconds > near.max_age_seconds


def test_freshness_policy_rejects_non_past_earliest_date():
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    with pytest.raises(BaselineFourthPeriodError):
        derive_freshness_policy_for_window(earliest_required_date=date(2026, 8, 16), now=now, version="v")
    with pytest.raises(BaselineFourthPeriodError):
        derive_freshness_policy_for_window(earliest_required_date=date(2026, 9, 1), now=now, version="v")


def test_freshness_policy_covers_actual_p0_lookback_window():
    """P0's own lookback (20 sessions before 2023-03-31) reaches to
    2023-03-03 -- the policy derived for that exact date must cover it
    with margin, proven here rather than discovered hours into a run
    (Issue #318's own failure mode)."""
    from src.orb_research_historical_calendar import research_trading_sessions_before

    p0_dates = research_trading_sessions_before(date(2024, 4, 3), count=252)
    lookback = research_trading_sessions_before(p0_dates[0], count=20)
    now = datetime(2026, 8, 16, tzinfo=timezone.utc)
    policy = derive_freshness_policy_for_window(earliest_required_date=lookback[0], now=now, safety_margin_days=30, version="p0-v1")
    age_of_oldest_bar_seconds = (now.date() - lookback[0]).days * 86400.0
    assert policy.max_age_seconds > age_of_oldest_bar_seconds


# ---------------------------------------------------------------------------
# Exact algebraic recombination.
# ---------------------------------------------------------------------------


def _tq(**overrides):
    base = {
        "closed_trades": 10, "winners": 4, "losers": 6, "win_rate": 0.4,
        "average_winner": 5.0, "average_loser": -2.0,
        "expectancy_per_trade": (5.0 * 4 - 2.0 * 6) / 10, "profit_factor": (5.0 * 4) / (2.0 * 6),
        "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.3, "cumulative_r": 3.0,
        "meets_minimum_sample": False,
    }
    base.update(overrides)
    return base


def test_combine_two_equal_aggregates_doubles_counts_preserves_ratios():
    tq = _tq()
    combined = combine_trade_quality_aggregates([tq, tq])
    assert combined["closed_trades"] == 20
    assert combined["winners"] == 8
    assert combined["losers"] == 12
    assert combined["win_rate"] == pytest.approx(0.4)
    assert combined["expectancy_per_trade"] == pytest.approx(tq["expectancy_per_trade"])
    assert combined["cumulative_r"] == pytest.approx(6.0)


def test_combine_skips_none_entries():
    tq = _tq()
    combined = combine_trade_quality_aggregates([tq, None])
    assert combined["closed_trades"] == 10


def test_combine_empty_list_returns_empty_aggregate():
    combined = combine_trade_quality_aggregates([])
    assert combined["closed_trades"] == 0
    assert combined["expectancy_per_trade"] is None
    assert combined["win_rate"] is None


def test_combine_infinite_profit_factor_when_no_losses():
    tq_no_losses = _tq(losers=0, average_loser=None, closed_trades=4, winners=4, stop_out_rate=0.0)
    combined = combine_trade_quality_aggregates([tq_no_losses])
    assert combined["profit_factor_infinite_no_losses"] is True
    assert combined["profit_factor"] is None


def test_excluding_symbol_from_aggregate_reduces_totals():
    period_total = _tq(closed_trades=20, winners=8, losers=12, average_winner=5.0, average_loser=-2.0, cumulative_r=6.0)
    symbol_stats = _tq(closed_trades=10, winners=4, losers=6, average_winner=5.0, average_loser=-2.0, cumulative_r=3.0)
    reduced = trade_quality_excluding_symbol_from_aggregate(period_total, symbol_stats)
    assert reduced["closed_trades"] == 10
    assert reduced["cumulative_r"] == pytest.approx(3.0)


def test_excluding_symbol_none_stats_returns_period_unchanged():
    period_total = _tq()
    reduced = trade_quality_excluding_symbol_from_aggregate(period_total, None)
    assert reduced["closed_trades"] == period_total["closed_trades"]
    assert reduced["cumulative_r"] == pytest.approx(period_total["cumulative_r"])


def test_reconstruction_matches_320_real_row_ground_truth_all_candidates():
    """Cross-check against #320's own real-concatenated-row result
    (independently verified during that study's development): combining
    P1/P2/P3's native aggregates must reproduce 291 closed trades,
    expectancy 0.3436..., PF 1.2272..., cumulative R 11.9035..."""
    natives = [PRIOR_PERIODS_REFERENCE[p]["native"] for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")]
    combined = combine_trade_quality_aggregates(natives)
    assert combined["closed_trades"] == 291
    assert combined["expectancy_per_trade"] == pytest.approx(0.3436254295532646, abs=1e-6)
    assert combined["profit_factor"] == pytest.approx(1.2271839145745769, abs=1e-6)
    assert combined["cumulative_r"] == pytest.approx(11.903512237486298, abs=1e-4)


def test_reconstruction_matches_320_real_row_ground_truth_excluding_tsla():
    excl = [
        trade_quality_excluding_symbol_from_aggregate(
            PRIOR_PERIODS_REFERENCE[p]["native"], PRIOR_PERIODS_REFERENCE[p]["symbol_contribution"].get("TSLA")
        )
        for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")
    ]
    combined = combine_trade_quality_aggregates(excl)
    assert combined["closed_trades"] == 244
    assert combined["expectancy_per_trade"] == pytest.approx(0.12434426229508197, abs=1e-6)
    assert combined["profit_factor"] == pytest.approx(1.0901165812727407, abs=1e-6)


def _row(*, session_date, symbol, realized_pnl, closed=True):
    return TradeDiagnosticRow(
        decision={"session_date": session_date.isoformat(), "symbol": symbol},
        outcome={"closed": closed, "realized_pnl": realized_pnl if closed else None},
    )


def test_max_drawdown_computed_from_running_pnl_peak_to_trough():
    rows = [
        _row(session_date=date(2023, 4, 3), symbol="AAA", realized_pnl="10.00"),
        _row(session_date=date(2023, 4, 4), symbol="AAA", realized_pnl="-15.00"),
        _row(session_date=date(2023, 4, 5), symbol="AAA", realized_pnl="-5.00"),
        _row(session_date=date(2023, 4, 6), symbol="AAA", realized_pnl="8.00"),
    ]
    result = max_drawdown_and_streak_from_rows(rows)
    # peak after trade 1 = 10; trough after trade 3 = 10-15-5 = -10; drawdown = 10-(-10)=20
    assert result["maximum_drawdown"] == "20.00"
    assert result["longest_losing_streak"] == 2


def test_max_drawdown_ignores_non_closed_trades():
    rows = [
        _row(session_date=date(2023, 4, 3), symbol="AAA", realized_pnl=None, closed=False),
        _row(session_date=date(2023, 4, 4), symbol="AAA", realized_pnl="5.00"),
    ]
    result = max_drawdown_and_streak_from_rows(rows)
    assert result["maximum_drawdown"] == "0"
    assert result["longest_losing_streak"] == 0


def test_max_drawdown_empty_rows_no_crash():
    result = max_drawdown_and_streak_from_rows([])
    assert result["maximum_drawdown"] == "0"
    assert result["longest_losing_streak"] == 0


def test_symbol_total_pnl_matches_avg_times_count():
    stats = {"winners": 4, "losers": 6, "average_winner": 5.0, "average_loser": -2.0}
    assert symbol_total_pnl(stats) == pytest.approx(4 * 5.0 + 6 * -2.0)


def test_symbol_total_pnl_none_is_zero():
    assert symbol_total_pnl(None) == 0.0


# ---------------------------------------------------------------------------
# Predeclared baseline classification.
# ---------------------------------------------------------------------------


def _combined(**overrides):
    base = {"closed_trades": 300, "expectancy_per_trade": 0.5, "profit_factor": 1.5, "profit_factor_infinite_no_losses": False}
    base.update(overrides)
    return base


def test_classify_supported_when_all_seven_criteria_pass():
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(expectancy_per_trade=0.5, profit_factor=1.5),
        combined_one_trade_per_day=_combined(expectancy_per_trade=0.6, profit_factor=1.6),
        combined_excluding_best_symbol=_combined(expectancy_per_trade=0.2, profit_factor=1.1),
        period_native_expectancy={"P0": 0.1, "P1": 0.02, "P2": 0.3, "P3": 1.0},
        p0_catastrophic_instability=False, p0_meaningful_mfe_fraction=True,
    )
    assert result["verdict"] == "BASELINE_SUPPORTED_FOR_EXIT_RESEARCH"
    assert all(result["checks"].values())


def test_classify_insufficient_evidence_below_minimum_sample():
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(closed_trades=5),
        combined_one_trade_per_day=_combined(),
        combined_excluding_best_symbol=_combined(),
        period_native_expectancy={"P0": 0.1, "P1": 0.02, "P2": 0.3, "P3": 1.0},
        p0_catastrophic_instability=False, p0_meaningful_mfe_fraction=True,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_regime_dependence_confirmed_when_combined_positive_but_a_criterion_fails():
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(expectancy_per_trade=0.5, profit_factor=1.5),
        combined_one_trade_per_day=_combined(expectancy_per_trade=0.6, profit_factor=1.6),
        combined_excluding_best_symbol=_combined(expectancy_per_trade=0.2, profit_factor=1.1),
        period_native_expectancy={"P0": -0.5, "P1": -0.5, "P2": 0.3, "P3": 1.0},  # only 2 of 4 non-negative
        p0_catastrophic_instability=False, p0_meaningful_mfe_fraction=True,
    )
    assert result["verdict"] == "REGIME_DEPENDENCE_CONFIRMED"
    assert result["checks"]["5_at_least_3_of_4_periods_non_negative"] is False


def test_classify_not_supported_when_combined_expectancy_negative():
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(expectancy_per_trade=-0.2, profit_factor=0.8),
        combined_one_trade_per_day=_combined(expectancy_per_trade=-0.1, profit_factor=0.9),
        combined_excluding_best_symbol=_combined(expectancy_per_trade=-0.3, profit_factor=0.7),
        period_native_expectancy={"P0": -0.5, "P1": -0.5, "P2": -0.3, "P3": -0.1},
        p0_catastrophic_instability=False, p0_meaningful_mfe_fraction=True,
    )
    assert result["verdict"] == "BASELINE_NOT_SUPPORTED"


def test_classify_not_supported_when_catastrophic_p0_instability():
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(expectancy_per_trade=0.5, profit_factor=1.5),
        combined_one_trade_per_day=_combined(expectancy_per_trade=0.6, profit_factor=1.6),
        combined_excluding_best_symbol=_combined(expectancy_per_trade=0.2, profit_factor=1.1),
        period_native_expectancy={"P0": 0.1, "P1": 0.02, "P2": 0.3, "P3": 1.0},
        p0_catastrophic_instability=True, p0_meaningful_mfe_fraction=True,
    )
    assert result["verdict"] != "BASELINE_SUPPORTED_FOR_EXIT_RESEARCH"
    assert result["checks"]["6_no_catastrophic_p0_instability"] is False


def test_catastrophic_instability_thresholds_fixed():
    assert CATASTROPHIC_EXPECTANCY_THRESHOLD == -1.0
    assert CATASTROPHIC_PROFIT_FACTOR_THRESHOLD == 0.5


def test_is_p0_catastrophically_unstable_false_for_mild_result():
    assert is_p0_catastrophically_unstable({"expectancy_per_trade": 0.02, "profit_factor": 1.0}) is False


def test_is_p0_catastrophically_unstable_true_for_deeply_negative_expectancy():
    assert is_p0_catastrophically_unstable({"expectancy_per_trade": -1.5, "profit_factor": 0.9}) is True


def test_is_p0_catastrophically_unstable_true_for_collapsed_profit_factor():
    assert is_p0_catastrophically_unstable({"expectancy_per_trade": 0.1, "profit_factor": 0.2}) is True


def test_four_period_strong_evidence_thresholds_fixed():
    assert FOUR_PERIOD_STRONG_EVIDENCE_MIN_EXPECTANCY == 0.1
    assert FOUR_PERIOD_STRONG_EVIDENCE_MIN_PROFIT_FACTOR == 1.2


def test_four_period_evidence_is_strong_enough_true_above_bar():
    assert four_period_evidence_is_strong_enough({"expectancy_per_trade": 0.3, "profit_factor": 1.3}) is True


def test_four_period_evidence_is_strong_enough_false_below_bar():
    assert four_period_evidence_is_strong_enough({"expectancy_per_trade": 0.05, "profit_factor": 1.05}) is False


def test_default_criteria_minimum_periods_is_3_of_4():
    assert DEFAULT_BASELINE_CONCLUSION_CRITERIA.minimum_periods_non_negative_of_four == 3


def test_custom_criteria_are_honoured():
    strict = BaselineConclusionCriteria(minimum_periods_non_negative_of_four=4)
    result = classify_baseline_conclusion(
        combined_all_candidates=_combined(expectancy_per_trade=0.5, profit_factor=1.5),
        combined_one_trade_per_day=_combined(expectancy_per_trade=0.6, profit_factor=1.6),
        combined_excluding_best_symbol=_combined(expectancy_per_trade=0.2, profit_factor=1.1),
        period_native_expectancy={"P0": 0.1, "P1": -0.02, "P2": 0.3, "P3": 1.0},  # only 3 of 4 non-negative
        p0_catastrophic_instability=False, p0_meaningful_mfe_fraction=True, criteria=strict,
    )
    assert result["checks"]["5_at_least_3_of_4_periods_non_negative"] is False


# ---------------------------------------------------------------------------
# Next-milestone recommendation.
# ---------------------------------------------------------------------------


def test_recommend_profit_protection_when_supported():
    result = recommend_next_milestone(baseline_verdict="BASELINE_SUPPORTED_FOR_EXIT_RESEARCH", four_period_evidence_strong_enough=True)
    assert result["next_milestone"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"


def test_regime_dependence_confirmed_does_not_automatically_request_fifth_period():
    strong = recommend_next_milestone(baseline_verdict="REGIME_DEPENDENCE_CONFIRMED", four_period_evidence_strong_enough=True)
    weak = recommend_next_milestone(baseline_verdict="REGIME_DEPENDENCE_CONFIRMED", four_period_evidence_strong_enough=False)
    assert strong["next_milestone"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"
    assert weak["next_milestone"] == "PAUSE_STRATEGY_REFINEMENT"
    valid_milestones = {"PROFIT_PROTECTION_EXIT_EXPERIMENT", "MORE_BASELINE_VALIDATION", "PAUSE_STRATEGY_REFINEMENT"}
    for result in (strong, weak):
        assert result["next_milestone"] in valid_milestones


def test_recommend_pause_when_not_supported():
    result = recommend_next_milestone(baseline_verdict="BASELINE_NOT_SUPPORTED", four_period_evidence_strong_enough=True)
    assert result["next_milestone"] == "PAUSE_STRATEGY_REFINEMENT"


def test_recommend_more_validation_when_insufficient_evidence():
    result = recommend_next_milestone(baseline_verdict="INSUFFICIENT_EVIDENCE", four_period_evidence_strong_enough=True)
    assert result["next_milestone"] == "MORE_BASELINE_VALIDATION"


def test_recommendation_never_authorizes_production_or_capital_path():
    for verdict in ("BASELINE_SUPPORTED_FOR_EXIT_RESEARCH", "REGIME_DEPENDENCE_CONFIRMED", "BASELINE_NOT_SUPPORTED", "INSUFFICIENT_EVIDENCE"):
        result = recommend_next_milestone(baseline_verdict=verdict, four_period_evidence_strong_enough=True)
        assert result["authorizes_production_change"] is False
        assert result["authorizes_capital_path_experiment"] is False


# ---------------------------------------------------------------------------
# No production config changes.
# ---------------------------------------------------------------------------


def test_module_never_touches_strategy_yaml_or_enabled_flag():
    source = (REPOSITORY_ROOT / "src" / "orb_baseline_fourth_period_research.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
    assert "yaml.safe_dump" not in source


def test_module_never_implements_trailing_stop_or_changes_simulator():
    source = (REPOSITORY_ROOT / "src" / "orb_baseline_fourth_period_research.py").read_text(encoding="utf-8")
    assert "def trailing_stop" not in source
    assert "class TrailingStop" not in source
    assert "def simulate_shadow_execution" not in source
