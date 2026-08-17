"""Focused tests for the independent single-trade-core validation study
(local milestone M40)."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

from src.orb_baseline_fourth_period_research import ANCHORED_COHORT
from src.orb_existing_configs_reevaluation_research import FROZEN_CONFIG_SET
from src.orb_independent_single_trade_core_validation_research import (
    CHALLENGER_CONFIG,
    CHALLENGER_CONFIG_LABEL,
    DEFAULT_VALIDATION_CRITERIA,
    P0_START,
    P_MINUS_1_SESSION_COUNT,
    PRIMARY_CONFIG,
    PRIMARY_CONFIG_LABEL,
    VALIDATION_CONFIG_LABELS,
    VALIDATION_UNIVERSE,
    ValidationCriteria,
    classify_validation,
    derive_p_minus_1_sessions,
    earliest_cached_bar_utc,
    p_minus_1_coverage_gap,
    recommend_next_milestone,
)


# ---------------------------------------------------------------------------
# Frozen configuration identification: no new threshold, exact reuse of
# Issue #325's FROZEN_CONFIG_SET.

def test_primary_and_challenger_resolve_to_existing_frozen_configs():
    assert PRIMARY_CONFIG_LABEL in FROZEN_CONFIG_SET
    assert CHALLENGER_CONFIG_LABEL in FROZEN_CONFIG_SET
    assert PRIMARY_CONFIG is FROZEN_CONFIG_SET[PRIMARY_CONFIG_LABEL]
    assert CHALLENGER_CONFIG is FROZEN_CONFIG_SET[CHALLENGER_CONFIG_LABEL]


def test_primary_satisfies_operator_boundary_gap_100_rvol_250():
    assert PRIMARY_CONFIG.combo.gap_percent >= Decimal("1.00")
    assert PRIMARY_CONFIG.combo.rvol_multiple >= Decimal("2.50")


def test_challenger_satisfies_operator_boundary_gap_050_rvol_175():
    assert CHALLENGER_CONFIG.combo.gap_percent >= Decimal("0.50")
    assert CHALLENGER_CONFIG.combo.rvol_multiple >= Decimal("1.75")


def test_primary_and_challenger_are_the_two_issue_325_core_candidates():
    # These are exactly the two CORE_CANDIDATE labels reported by Issue
    # #325 -- pinned here as a literal so this test fails loudly if either
    # module drifts, rather than silently re-deriving a "new" pair.
    assert VALIDATION_CONFIG_LABELS == ("GAP_100_RVOL_250", "GAP_050_RVOL_175")


def test_validation_config_labels_are_exactly_two():
    assert len(VALIDATION_CONFIG_LABELS) == 2
    assert len(set(VALIDATION_CONFIG_LABELS)) == 2


# ---------------------------------------------------------------------------
# Universe: fixed #320 cohort, reused unchanged, not profitability-derived.

def test_validation_universe_is_the_anchored_cohort_unchanged():
    assert VALIDATION_UNIVERSE == ANCHORED_COHORT


def test_validation_universe_is_a_tuple_of_nine_symbols():
    assert len(VALIDATION_UNIVERSE) == 9
    assert len(set(VALIDATION_UNIVERSE)) == 9


# ---------------------------------------------------------------------------
# Period derivation: programmatic, frozen, no hand-picked dates.

def test_p_minus_1_reference_day_is_p0_start():
    assert P0_START == date(2023, 3, 31)


def test_p_minus_1_session_count_is_252():
    assert P_MINUS_1_SESSION_COUNT == 252


def test_derive_p_minus_1_sessions_succeeds_now_that_calendar_covers_2022():
    # The research-only calendar was extended to 2022-01-01 (local
    # milestone M40, this module's own follow-up) specifically so this
    # window -- 252 sessions strictly before P0's own start date -- is
    # fully computable. Exact dates pinned here so a future calendar
    # regression fails loudly.
    sessions = derive_p_minus_1_sessions()
    assert len(sessions) == 252
    assert sessions[0] == date(2022, 3, 30)
    assert sessions[-1] == date(2023, 3, 30)
    assert sessions[-1] < P0_START


# ---------------------------------------------------------------------------
# Coverage-gap detector: documents the exact shortfall, never estimates.
# (The real P_MINUS_1 window is fully covered as of the calendar
# extension above; a synthetic, deliberately-oversized window is used
# below to keep this code path under test rather than deleting it.)

def test_coverage_gap_is_none_when_window_is_fully_computable():
    # A window entirely inside covered research-calendar territory (e.g.
    # a handful of sessions before 2023-03-31) needs no data outside the
    # calendar's own coverage.
    gap = p_minus_1_coverage_gap(reference_day=date(2023, 3, 31), count=10)
    assert gap is None


def test_coverage_gap_is_none_for_the_actual_p_minus_1_window():
    gap = p_minus_1_coverage_gap()
    assert gap is None


def test_coverage_gap_reports_a_real_shortfall_for_an_oversized_window():
    # A window that reaches past the (now 2022-01-01) calendar floor still
    # produces a structured gap report -- the detector itself is not
    # hardcoded to any one year boundary.
    gap = p_minus_1_coverage_gap(reference_day=date(2023, 3, 31), count=2000)
    assert gap is not None
    assert gap.requested_session_count == 2000
    assert gap.reference_day == date(2023, 3, 31)
    assert gap.calendar_lower_bound == date(2022, 1, 1)
    assert gap.sessions_available_within_calendar_coverage < 2000
    assert gap.sessions_short == 2000 - gap.sessions_available_within_calendar_coverage
    assert gap.sessions_short > 0


def test_coverage_gap_detail_names_both_missing_prerequisites():
    gap = p_minus_1_coverage_gap(reference_day=date(2023, 3, 31), count=2000)
    assert gap is not None
    assert "calendar" in gap.detail.lower()
    assert "alpaca" in gap.detail.lower() or "network fetch" in gap.detail.lower()


def test_earliest_cached_bar_utc_reads_existing_cache_without_fetching(tmp_path: Path):
    # Empty/nonexistent cache dir -> None, never a guess.
    assert earliest_cached_bar_utc(tmp_path / "does-not-exist") is None


def test_coverage_gap_includes_earliest_cached_bar_when_cache_dir_given():
    from src.orb_universe_expansion_research import DEFAULT_CACHE_DIR

    gap = p_minus_1_coverage_gap(reference_day=date(2023, 3, 31), count=2000, cache_dir=DEFAULT_CACHE_DIR)
    assert gap is not None
    assert gap.earliest_cached_bar_utc is not None
    assert gap.earliest_cached_bar_utc >= "2022-01-01"


# ---------------------------------------------------------------------------
# Classification: predeclared VALIDATED / MIXED / FAILED / INSUFFICIENT_SAMPLE.

def _tq(*, closed, exp_r, pf_r, pf_r_inf=False):
    return {"closed_trades": closed, "expectancy_r_per_trade": exp_r, "profit_factor_r": pf_r, "profit_factor_r_infinite_no_losses": pf_r_inf}


def test_classify_insufficient_sample_when_coverage_gap_present():
    gap = p_minus_1_coverage_gap(reference_day=date(2023, 3, 31), count=2000)
    result = classify_validation(
        one_per_day=None, all_candidates=None, one_per_day_excluding_best_symbol=None,
        drawdown_r=None, coverage_gap=gap,
    )
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"
    assert "coverage gap" in result["reason"]


def test_classify_insufficient_sample_below_minimum_closed_trades():
    result = classify_validation(
        one_per_day=_tq(closed=5, exp_r=0.5, pf_r=2.0), all_candidates=_tq(closed=5, exp_r=0.5, pf_r=2.0),
        one_per_day_excluding_best_symbol=_tq(closed=4, exp_r=0.4, pf_r=1.8), drawdown_r=2.0, coverage_gap=None,
    )
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"


def test_classify_validated_when_all_checks_pass():
    result = classify_validation(
        one_per_day=_tq(closed=40, exp_r=0.08, pf_r=1.2),
        all_candidates=_tq(closed=60, exp_r=0.02, pf_r=1.05),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=0.03, pf_r=1.1),
        drawdown_r=6.0, coverage_gap=None,
    )
    assert result["verdict"] == "VALIDATED"
    assert all(result["checks"].values())


def test_classify_mixed_when_positive_edge_but_drawdown_too_large():
    result = classify_validation(
        one_per_day=_tq(closed=40, exp_r=0.08, pf_r=1.2),
        all_candidates=_tq(closed=60, exp_r=0.02, pf_r=1.05),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=0.03, pf_r=1.1),
        drawdown_r=25.0, coverage_gap=None,  # exceeds DEFAULT_VALIDATION_CRITERIA.maximum_acceptable_drawdown_r
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["acceptable_drawdown"] is False


def test_classify_mixed_when_positive_edge_but_single_symbol_dependent():
    result = classify_validation(
        one_per_day=_tq(closed=40, exp_r=0.08, pf_r=1.2),
        all_candidates=_tq(closed=60, exp_r=0.02, pf_r=1.05),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=-0.01, pf_r=0.9),
        drawdown_r=6.0, coverage_gap=None,
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["not_single_symbol_dependent"] is False


def test_classify_failed_when_one_per_day_expectancy_negative():
    result = classify_validation(
        one_per_day=_tq(closed=40, exp_r=-0.05, pf_r=0.9),
        all_candidates=_tq(closed=60, exp_r=-0.02, pf_r=0.95),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=-0.03, pf_r=0.9),
        drawdown_r=6.0, coverage_gap=None,
    )
    assert result["verdict"] == "FAILED"


def test_classify_ignores_price_per_share_fields_entirely():
    # A "contaminated" fixture: strongly positive price fields, but the R
    # fields alone determine the verdict.
    contaminated_one_per_day = {
        "closed_trades": 40, "expectancy_r_per_trade": -0.05, "profit_factor_r": 0.9,
        "expectancy_per_trade": 50.0, "profit_factor": 5.0,
        "expectancy_price_per_share": 50.0, "profit_factor_price_per_share": 5.0,
    }
    result = classify_validation(
        one_per_day=contaminated_one_per_day, all_candidates=_tq(closed=60, exp_r=-0.02, pf_r=0.95),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=-0.03, pf_r=0.9),
        drawdown_r=6.0, coverage_gap=None,
    )
    assert result["verdict"] == "FAILED"


def test_infinite_profit_factor_r_with_no_losses_passes_pf_check():
    result = classify_validation(
        one_per_day=_tq(closed=20, exp_r=0.3, pf_r=None, pf_r_inf=True),
        all_candidates=_tq(closed=25, exp_r=0.1, pf_r=1.5),
        one_per_day_excluding_best_symbol=_tq(closed=15, exp_r=0.2, pf_r=1.4),
        drawdown_r=1.0, coverage_gap=None,
    )
    assert result["checks"]["one_per_day_pf_r_above_1"] is True
    assert result["verdict"] == "VALIDATED"


def test_default_validation_criteria_minimum_is_15():
    assert DEFAULT_VALIDATION_CRITERIA.minimum_one_per_day_closed_trades == 15


def test_custom_validation_criteria_are_honoured():
    strict = ValidationCriteria(maximum_acceptable_drawdown_r=1.0)
    result = classify_validation(
        one_per_day=_tq(closed=40, exp_r=0.08, pf_r=1.2),
        all_candidates=_tq(closed=60, exp_r=0.02, pf_r=1.05),
        one_per_day_excluding_best_symbol=_tq(closed=30, exp_r=0.03, pf_r=1.1),
        drawdown_r=2.0, coverage_gap=None, criteria=strict,
    )
    assert result["checks"]["acceptable_drawdown"] is False
    assert result["verdict"] == "MIXED"


# ---------------------------------------------------------------------------
# Next-milestone recommendation.

def test_recommend_profit_protection_when_either_validated():
    decision = recommend_next_milestone(primary_verdict="VALIDATED", challenger_verdict="FAILED")
    assert decision["next_milestone"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"


def test_recommend_candidate_selection_when_mixed_and_no_validated():
    decision = recommend_next_milestone(primary_verdict="MIXED", challenger_verdict="FAILED")
    assert decision["next_milestone"] == "CANDIDATE_SELECTION_RESEARCH"


def test_recommend_pause_when_neither_validated_nor_mixed():
    decision = recommend_next_milestone(primary_verdict="FAILED", challenger_verdict="INSUFFICIENT_SAMPLE")
    assert decision["next_milestone"] == "PAUSE_SINGLE_TRADE_CORE"


def test_recommend_pause_when_both_insufficient_sample():
    decision = recommend_next_milestone(primary_verdict="INSUFFICIENT_SAMPLE", challenger_verdict="INSUFFICIENT_SAMPLE")
    assert decision["next_milestone"] == "PAUSE_SINGLE_TRADE_CORE"


def test_recommendation_never_authorizes_production_change():
    for pv in ("VALIDATED", "MIXED", "FAILED", "INSUFFICIENT_SAMPLE"):
        for cv in ("VALIDATED", "MIXED", "FAILED", "INSUFFICIENT_SAMPLE"):
            decision = recommend_next_milestone(primary_verdict=pv, challenger_verdict=cv)
            assert decision["authorizes_production_change"] is False


# ---------------------------------------------------------------------------
# Safety / scope: no production/strategy files touched by this module.

def test_module_never_touches_strategy_yaml_or_enabled_flag():
    import inspect

    import src.orb_independent_single_trade_core_validation_research as mod

    source = inspect.getsource(mod)
    assert "strategy.enabled" not in source
    assert "strategy_v0.yaml" not in source


def test_module_never_changes_exit_stop_target_or_spy_logic():
    import inspect

    import src.orb_independent_single_trade_core_validation_research as mod

    source = inspect.getsource(mod)
    for forbidden in ("stop_loss_percent", "target_percent", "vwap_gate", "breakout_confirmation"):
        assert forbidden not in source


def test_module_constructs_no_new_parameter_combo():
    import inspect

    import src.orb_independent_single_trade_core_validation_research as mod

    source = inspect.getsource(mod)
    assert "ParameterCombo(" not in source
