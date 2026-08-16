"""Tests for the ORB C_BASE baseline stability decomposition study
(Issue #319, `src/orb_baseline_stability_research.py`).

Covers: the common-cohort intersection is formed strictly from eligibility
(never P&L), quarterly bucketing/adequate-sample marking, MFE-before-
reversal fraction computation, the predeclared STABLE_EDGE/
REGIME_DEPENDENT_EDGE/WEAK_OR_NO_EDGE/INSUFFICIENT_EVIDENCE classification,
the next-experiment recommendation rule, and that this module never
touches production configuration.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.orb_baseline_stability_research import (
    B_BASE,
    C_BASE,
    DEFAULT_STABILITY_CRITERIA,
    MEANINGFUL_MFE_FRACTION,
    MEANINGFUL_MFE_THRESHOLD_R,
    MIN_COHORT_SIZE,
    MIN_QUARTER_CLOSED_TRADES,
    StabilityCriteria,
    TradeDiagnosticRow,
    best_contributing_symbol,
    classify_stability,
    combined_view,
    common_cohort,
    excluding_symbol,
    meaningful_mfe_fraction,
    mfe_analysis,
    quarterly_stability,
    recommend_next_experiment,
)
from src.orb_gap_rvol_sensitivity_research import ParameterCombo
from src.orb_universe_expansion_research import SymbolLiquidityProfile

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _profile(symbol: str, *, eligible: bool) -> SymbolLiquidityProfile:
    return SymbolLiquidityProfile(
        symbol=symbol, trading_days_observed=200, avg_daily_dollar_volume=Decimal("10000000"),
        avg_daily_share_volume=Decimal("100000"), median_price=Decimal("100"),
        opening_window_minute_coverage_pct=1.0, session_opening_range_complete_pct=1.0,
        rvol_history_satisfiable_pct=1.0, eligible=eligible,
        exclusion_reasons=() if eligible else ("INSUFFICIENT_LIQUIDITY",),
    )


# ---------------------------------------------------------------------------
# Config identity.
# ---------------------------------------------------------------------------


def test_c_base_is_gap_050_rvol_150():
    assert C_BASE == ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.50"))


def test_b_base_is_gap_075_rvol_150():
    assert B_BASE == ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("1.50"))


# ---------------------------------------------------------------------------
# Common cohort: eligibility-only intersection, never P&L.
# ---------------------------------------------------------------------------


def test_common_cohort_is_intersection_of_eligible_symbols():
    profiles_by_period = {
        "P1": {"AAA": _profile("AAA", eligible=True), "BBB": _profile("BBB", eligible=True), "CCC": _profile("CCC", eligible=False)},
        "P2": {"AAA": _profile("AAA", eligible=True), "BBB": _profile("BBB", eligible=False), "CCC": _profile("CCC", eligible=True)},
        "P3": {"AAA": _profile("AAA", eligible=True), "BBB": _profile("BBB", eligible=True), "CCC": _profile("CCC", eligible=True)},
    }
    cohort = common_cohort(profiles_by_period)
    assert cohort == ("AAA",)


def test_common_cohort_sorted_alphabetically_not_by_volume():
    profiles_by_period = {
        "P1": {"ZZZ": _profile("ZZZ", eligible=True), "AAA": _profile("AAA", eligible=True)},
        "P2": {"ZZZ": _profile("ZZZ", eligible=True), "AAA": _profile("AAA", eligible=True)},
    }
    cohort = common_cohort(profiles_by_period)
    assert cohort == ("AAA", "ZZZ")


def test_common_cohort_empty_when_no_period_shares_a_symbol():
    profiles_by_period = {
        "P1": {"AAA": _profile("AAA", eligible=True)},
        "P2": {"BBB": _profile("BBB", eligible=True)},
    }
    assert common_cohort(profiles_by_period) == ()


def test_common_cohort_empty_input_returns_empty():
    assert common_cohort({}) == ()


def test_min_cohort_size_is_3():
    assert MIN_COHORT_SIZE == 3


# ---------------------------------------------------------------------------
# Row builder helpers for the aggregation tests below.
# ---------------------------------------------------------------------------


def _row(
    *, period: str, symbol: str, session_date: date, closed: bool = True, is_winner: bool = True,
    realized_pnl: str = "5.00", r_multiple: float = 1.0, mfe_r: float | None = 1.5, mae_r: float | None = -0.5,
    is_stopped: bool = False,
) -> TradeDiagnosticRow:
    quarter = f"{session_date.year}-Q{(session_date.month - 1) // 3 + 1}"
    return TradeDiagnosticRow(
        decision={
            "period": period, "view": "NATIVE", "config": "C_BASE", "symbol": symbol,
            "session_date": session_date.isoformat(), "month": session_date.strftime("%Y-%m"), "quarter": quarter,
        },
        outcome={
            "state": "STOPPED" if is_stopped else "TIME_EXIT", "entered": True, "closed": closed,
            "entry_price": "100.00", "initial_stop_price": "98.00", "exit_price": "102.00",
            "exit_reason": "time_exit", "realized_pnl": realized_pnl if closed else None,
            "r_multiple": r_multiple if closed else None, "is_winner": is_winner if closed else None,
            "is_stopped": is_stopped, "initial_risk_r": "2.00",
            "maximum_favorable_excursion": "3.00", "maximum_adverse_excursion": "-1.00",
            "mfe_r": mfe_r, "mae_r": mae_r,
        },
    )


# ---------------------------------------------------------------------------
# Quarterly stability.
# ---------------------------------------------------------------------------


def test_min_quarter_closed_trades_is_8():
    assert MIN_QUARTER_CLOSED_TRADES == 8


def test_quarterly_stability_marks_adequate_and_inadequate_quarters():
    rows = [
        _row(period="P1", symbol="AAA", session_date=date(2024, 5, i + 1), realized_pnl="5.00", r_multiple=1.0)
        for i in range(10)
    ] + [
        _row(period="P1", symbol="AAA", session_date=date(2024, 8, i + 1), realized_pnl="5.00", r_multiple=1.0)
        for i in range(3)
    ]
    result = quarterly_stability(rows)
    assert result["quarters"]["2024-Q2"]["adequate_sample"] is True
    assert result["quarters"]["2024-Q2"]["closed_trades"] == 10
    assert result["quarters"]["2024-Q3"]["adequate_sample"] is False
    assert result["quarters"]["2024-Q3"]["closed_trades"] == 3
    assert result["adequate_quarter_count"] == 1
    assert result["inadequate_quarter_count"] == 1


def test_quarterly_stability_fraction_positive_expectancy_only_over_adequate_quarters():
    positive_rows = [
        _row(period="P1", symbol="AAA", session_date=date(2024, 5, i + 1), realized_pnl="5.00", r_multiple=1.0)
        for i in range(10)
    ]
    negative_rows = [
        _row(period="P1", symbol="AAA", session_date=date(2024, 8, i + 1), realized_pnl="-5.00", r_multiple=-1.0, is_winner=False)
        for i in range(10)
    ]
    result = quarterly_stability(positive_rows + negative_rows)
    assert result["fraction_adequate_quarters_positive_expectancy"] == pytest.approx(0.5)


def test_quarterly_stability_longest_negative_streak():
    q1 = [_row(period="P1", symbol="A", session_date=date(2024, 2, i + 1), realized_pnl="-5.00", r_multiple=-1.0, is_winner=False) for i in range(8)]
    q2 = [_row(period="P1", symbol="A", session_date=date(2024, 5, i + 1), realized_pnl="-5.00", r_multiple=-1.0, is_winner=False) for i in range(8)]
    q3 = [_row(period="P1", symbol="A", session_date=date(2024, 8, i + 1), realized_pnl="5.00", r_multiple=1.0) for i in range(8)]
    result = quarterly_stability(q1 + q2 + q3)
    assert result["longest_negative_expectancy_streak_quarters"] == 2


def test_quarterly_stability_empty_rows_no_crash():
    result = quarterly_stability([])
    assert result["adequate_quarter_count"] == 0
    assert result["fraction_adequate_quarters_positive_expectancy"] is None


# ---------------------------------------------------------------------------
# MFE analysis: descriptive only, thresholds fixed.
# ---------------------------------------------------------------------------


def test_mfe_thresholds_are_fixed_quarter_steps():
    from src.orb_baseline_stability_research import MFE_THRESHOLDS_R

    assert MFE_THRESHOLDS_R == (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))


def test_mfe_analysis_computes_loser_reach_fractions():
    losers = [
        _row(period="P1", symbol="A", session_date=date(2024, 5, 1), is_winner=False, realized_pnl="-4.00", r_multiple=-1.0, mfe_r=0.80),
        _row(period="P1", symbol="A", session_date=date(2024, 5, 2), is_winner=False, realized_pnl="-4.00", r_multiple=-1.0, mfe_r=0.10),
    ]
    result = mfe_analysis(losers)
    assert result["losers"] == 2
    assert result["losers_reaching_threshold_before_reversing"]["+0.25R"]["losers_reaching_threshold"] == 1
    assert result["losers_reaching_threshold_before_reversing"]["+0.25R"]["fraction"] == pytest.approx(0.5)
    assert result["losers_reaching_threshold_before_reversing"]["+1.00R"]["losers_reaching_threshold"] == 0


def test_mfe_analysis_excludes_non_closed_trades():
    rows = [_row(period="P1", symbol="A", session_date=date(2024, 5, 1), closed=False)]
    result = mfe_analysis(rows)
    assert result["closed_trades"] == 0
    assert result["winners"] == 0 and result["losers"] == 0


def test_mfe_analysis_winner_distribution_present():
    winners = [
        _row(period="P1", symbol="A", session_date=date(2024, 5, i + 1), is_winner=True, realized_pnl="5.00", r_multiple=1.5, mfe_r=2.0)
        for i in range(3)
    ]
    result = mfe_analysis(winners)
    assert result["winner_mfe_r_distribution"]["count"] == 3
    assert result["winner_mfe_r_distribution"]["mean"] == pytest.approx(2.0)
    assert result["winner_realized_r_distribution"]["mean"] == pytest.approx(1.5)


def test_meaningful_mfe_fraction_true_above_threshold():
    mfe_result = {"losers_reaching_threshold_before_reversing": {f"+{MEANINGFUL_MFE_THRESHOLD_R}R": {"fraction": 0.5}}}
    assert meaningful_mfe_fraction(mfe_result) is True


def test_meaningful_mfe_fraction_false_below_threshold():
    mfe_result = {"losers_reaching_threshold_before_reversing": {f"+{MEANINGFUL_MFE_THRESHOLD_R}R": {"fraction": 0.1}}}
    assert meaningful_mfe_fraction(mfe_result) is False


def test_meaningful_mfe_fraction_false_when_no_data():
    mfe_result = {"losers_reaching_threshold_before_reversing": {f"+{MEANINGFUL_MFE_THRESHOLD_R}R": {"fraction": None}}}
    assert meaningful_mfe_fraction(mfe_result) is False


def test_meaningful_mfe_fraction_constant_is_30_percent():
    assert MEANINGFUL_MFE_FRACTION == 0.30


# ---------------------------------------------------------------------------
# Symbol-exclusion sensitivity: diagnostic only.
# ---------------------------------------------------------------------------


def test_excluding_symbol_removes_only_that_symbol():
    rows = [
        _row(period="P1", symbol="AAA", session_date=date(2024, 5, 1), realized_pnl="10.00", r_multiple=2.0),
        _row(period="P1", symbol="BBB", session_date=date(2024, 5, 2), realized_pnl="5.00", r_multiple=1.0),
    ]
    result = excluding_symbol(rows, symbol="AAA")
    assert result["closed_trades"] == 1
    assert result["expectancy_per_trade"] == pytest.approx(5.0)


def test_best_contributing_symbol_returns_top_pnl_symbol():
    rows = [
        _row(period="P1", symbol="AAA", session_date=date(2024, 5, 1), realized_pnl="20.00", r_multiple=2.0),
        _row(period="P1", symbol="BBB", session_date=date(2024, 5, 2), realized_pnl="5.00", r_multiple=1.0),
    ]
    assert best_contributing_symbol(rows) == "AAA"


def test_best_contributing_symbol_none_when_no_rows():
    assert best_contributing_symbol([]) is None


# ---------------------------------------------------------------------------
# Predeclared stability classification.
# ---------------------------------------------------------------------------


def _tq(*, expectancy, pf, closed=30):
    return {"expectancy_per_trade": expectancy, "profit_factor": pf, "closed_trades": closed}


def test_classify_stability_stable_edge_when_all_periods_positive():
    native = {"P1": _tq(expectancy=1.0, pf=1.5), "P2": _tq(expectancy=0.8, pf=1.4), "P3": _tq(expectancy=1.2, pf=1.6)}
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=native,
        single_symbol_dependence_by_period={"P1": False, "P2": False, "P3": False},
    )
    assert result["verdict"] == "STABLE_EDGE"


def test_classify_stability_insufficient_evidence_below_minimum_trades():
    native = {"P1": _tq(expectancy=1.0, pf=1.5, closed=5), "P2": _tq(expectancy=0.8, pf=1.4), "P3": _tq(expectancy=1.2, pf=1.6)}
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=native,
        single_symbol_dependence_by_period={"P1": False, "P2": False, "P3": False},
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_stability_regime_dependent_when_spread_survives_cohort():
    native = {"P1": _tq(expectancy=-0.5, pf=0.8), "P2": _tq(expectancy=1.0, pf=1.5), "P3": _tq(expectancy=1.2, pf=1.6)}
    cohort = {"P1": _tq(expectancy=-0.4, pf=0.85), "P2": _tq(expectancy=0.9, pf=1.4), "P3": _tq(expectancy=1.1, pf=1.5)}
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=cohort,
        single_symbol_dependence_by_period={"P1": False, "P2": False, "P3": False},
    )
    assert result["verdict"] == "REGIME_DEPENDENT_EDGE"


def test_classify_stability_weak_when_spread_does_not_survive_cohort_normalization():
    native = {"P1": _tq(expectancy=-0.5, pf=0.8), "P2": _tq(expectancy=1.0, pf=1.5), "P3": _tq(expectancy=1.2, pf=1.6)}
    # Cohort view: the weak period is no longer weak, and the strong period is no longer strong --
    # the native-view spread does not survive universe normalization.
    cohort = {"P1": _tq(expectancy=0.5, pf=1.2), "P2": _tq(expectancy=0.05, pf=1.01), "P3": _tq(expectancy=0.05, pf=1.01)}
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=cohort,
        single_symbol_dependence_by_period={"P1": False, "P2": False, "P3": False},
    )
    assert result["verdict"] == "WEAK_OR_NO_EDGE"


def test_classify_stability_not_stable_when_single_symbol_dependent():
    native = {"P1": _tq(expectancy=1.0, pf=1.5), "P2": _tq(expectancy=0.8, pf=1.4), "P3": _tq(expectancy=1.2, pf=1.6)}
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=native,
        single_symbol_dependence_by_period={"P1": True, "P2": False, "P3": False},
    )
    assert result["verdict"] != "STABLE_EDGE"


def test_default_stability_criteria_minimum_is_15():
    assert DEFAULT_STABILITY_CRITERIA.minimum_closed_trades_per_period == 15


def test_custom_stability_criteria_are_honoured():
    native = {"P1": _tq(expectancy=1.0, pf=1.5, closed=20), "P2": _tq(expectancy=0.8, pf=1.4, closed=20), "P3": _tq(expectancy=1.2, pf=1.6, closed=20)}
    strict = StabilityCriteria(minimum_closed_trades_per_period=1000)
    result = classify_stability(
        native_trade_quality_by_period=native, cohort_trade_quality_by_period=native,
        single_symbol_dependence_by_period={"P1": False, "P2": False, "P3": False}, criteria=strict,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# Next-experiment recommendation.
# ---------------------------------------------------------------------------


def test_recommend_profit_protection_when_stable_and_meaningful_mfe():
    result = recommend_next_experiment(stability_verdict="STABLE_EDGE", meaningful_mfe_fraction=True)
    assert result["recommendation"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"
    assert result["authorizes_production_change"] is False


def test_recommend_capital_path_when_stable_but_not_meaningful_mfe():
    result = recommend_next_experiment(stability_verdict="STABLE_EDGE", meaningful_mfe_fraction=False)
    assert result["recommendation"] == "CAPITAL_PATH_EXPERIMENT"


def test_recommend_more_baseline_validation_when_regime_dependent():
    result = recommend_next_experiment(stability_verdict="REGIME_DEPENDENT_EDGE", meaningful_mfe_fraction=True)
    assert result["recommendation"] == "MORE_BASELINE_VALIDATION"


def test_recommend_more_baseline_validation_when_insufficient_evidence():
    result = recommend_next_experiment(stability_verdict="INSUFFICIENT_EVIDENCE", meaningful_mfe_fraction=True)
    assert result["recommendation"] == "MORE_BASELINE_VALIDATION"


def test_recommend_pause_when_weak_or_no_edge():
    result = recommend_next_experiment(stability_verdict="WEAK_OR_NO_EDGE", meaningful_mfe_fraction=True)
    assert result["recommendation"] == "PAUSE_STRATEGY_REFINEMENT"


def test_recommendation_never_authorizes_production_change():
    for verdict in ("STABLE_EDGE", "REGIME_DEPENDENT_EDGE", "WEAK_OR_NO_EDGE", "INSUFFICIENT_EVIDENCE"):
        result = recommend_next_experiment(stability_verdict=verdict, meaningful_mfe_fraction=True)
        assert result["authorizes_production_change"] is False


# ---------------------------------------------------------------------------
# Combined view.
# ---------------------------------------------------------------------------


def test_combined_view_reports_all_candidates_trade_quality():
    rows = [_row(period="P1", symbol="A", session_date=date(2024, 5, 1))]
    result = combined_view(rows)
    assert result["ALL_CANDIDATES"]["closed_trades"] == 1


# ---------------------------------------------------------------------------
# No production config changes.
# ---------------------------------------------------------------------------


def test_module_never_touches_strategy_yaml_or_enabled_flag():
    source = (REPOSITORY_ROOT / "src" / "orb_baseline_stability_research.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
    assert "yaml.safe_dump" not in source


def test_script_never_writes_strategy_yaml_or_sets_enabled_flag():
    source = (REPOSITORY_ROOT / "scripts" / "research_orb_baseline_stability_decomposition.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
    assert "yaml.safe_dump" not in source


def test_module_never_implements_trailing_stop_or_changes_simulator():
    source = (REPOSITORY_ROOT / "src" / "orb_baseline_stability_research.py").read_text(encoding="utf-8")
    assert "def trailing_stop" not in source
    assert "class TrailingStop" not in source
    assert "def simulate_shadow_execution" not in source
