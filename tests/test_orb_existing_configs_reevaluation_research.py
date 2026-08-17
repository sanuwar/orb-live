"""Tests for the existing-configuration R-normalized re-evaluation study
(Issue #325, `src/orb_existing_configs_reevaluation_research.py`).

Covers: the frozen configuration set is exactly the union of Issue #300's
and Issue #302's grids (no threshold can enter without being traceable to
a prior study), R-based classification never reads price-per-share
fields, the deterministic one-trade-per-day selection is unchanged
(first-valid-candidate, no ranking introduced), R-based symbol-removal
sensitivity, and that this module never touches production configuration.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.orb_existing_configs_reevaluation_research import (
    DEFAULT_SELECTION_CRITERIA,
    FROZEN_CONFIG_SET,
    PERIOD_NATIVE_UNIVERSES,
    PERIOD_SESSION_SPEC,
    PERIODS,
    SelectionCriteria,
    TradeDiagnosticRow,
    best_contributing_symbol_by_r,
    classify_configuration,
    combined_trade_quality,
    recommend_next_milestone,
)
from src.orb_gap_rvol_sensitivity_research import GAP_THRESHOLDS as SENSITIVITY_GAP_THRESHOLDS
from src.orb_gap_rvol_sensitivity_research import RVOL_THRESHOLDS as SENSITIVITY_RVOL_THRESHOLDS
from src.orb_fine_rvol_validation_research import GAP_THRESHOLDS as FINE_GAP_THRESHOLDS
from src.orb_fine_rvol_validation_research import RVOL_THRESHOLDS as FINE_RVOL_THRESHOLDS

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Frozen configuration set: exactly the union of #300's and #302's grids.
# ---------------------------------------------------------------------------


def test_frozen_config_set_has_exactly_18_entries():
    assert len(FROZEN_CONFIG_SET) == 18


def test_frozen_config_set_is_exact_union_of_300_and_302_grids():
    expected = {
        (gap, rvol)
        for gap in SENSITIVITY_GAP_THRESHOLDS for rvol in SENSITIVITY_RVOL_THRESHOLDS
    } | {
        (gap, rvol)
        for gap in FINE_GAP_THRESHOLDS for rvol in FINE_RVOL_THRESHOLDS
    }
    actual = {(entry.combo.gap_percent, entry.combo.rvol_multiple) for entry in FROZEN_CONFIG_SET.values()}
    assert actual == expected


def test_every_config_traceable_to_issue_300_or_302():
    for label, entry in FROZEN_CONFIG_SET.items():
        assert entry.source_studies, f"{label} has no source study"
        assert all(s in ("Issue #300", "Issue #302") for s in entry.source_studies)


def test_no_config_label_uses_bare_letter_naming():
    # The exact naming-ambiguity failure mode this study's own goal
    # document flags: historical "B"/"C" meant different configs in
    # different studies.
    for label in FROZEN_CONFIG_SET:
        assert label.startswith("GAP_")
        assert "_RVOL_" in label
        assert label not in ("A", "B", "C", "D", "E")


def test_config_labels_match_their_own_gap_rvol_values():
    for label, entry in FROZEN_CONFIG_SET.items():
        gap_part, rvol_part = label.split("_RVOL_")
        gap_digits = gap_part.replace("GAP_", "")
        assert Decimal(gap_digits) / 100 == entry.combo.gap_percent
        assert Decimal(rvol_part) / 100 == entry.combo.rvol_multiple


def test_no_threshold_outside_the_frozen_set_can_be_constructed_silently():
    # Sanity: every combo is one of the 18 -- attempting to build an
    # arbitrary new combo and checking it is NOT in the set proves the set
    # is closed, not just non-empty.
    from src.orb_gap_rvol_sensitivity_research import ParameterCombo

    invented = ParameterCombo(gap_percent=Decimal("0.60"), rvol_multiple=Decimal("1.40"))
    assert invented not in {e.combo for e in FROZEN_CONFIG_SET.values()}


# ---------------------------------------------------------------------------
# Periods and universes: fixed, reused, not recomputed.
# ---------------------------------------------------------------------------


def test_four_periods_fixed():
    assert PERIODS == ("P0", "P1", "P2", "P3")


def test_period_session_spec_matches_established_boundaries():
    assert PERIOD_SESSION_SPEC["P0"] == (date(2024, 4, 3), 252, True)
    assert PERIOD_SESSION_SPEC["P1"] == (date(2025, 4, 3), 252, True)
    assert PERIOD_SESSION_SPEC["P2"] == (date(2026, 4, 7), 252, True)
    assert PERIOD_SESSION_SPEC["P3"] == (date(2026, 8, 14), 90, False)


def test_period_native_universes_match_established_sizes():
    assert len(PERIOD_NATIVE_UNIVERSES["P0"]) == 7
    assert len(PERIOD_NATIVE_UNIVERSES["P1"]) == 9
    assert len(PERIOD_NATIVE_UNIVERSES["P2"]) == 17
    assert len(PERIOD_NATIVE_UNIVERSES["P3"]) == 23


# ---------------------------------------------------------------------------
# R-based aggregation and combination.
# ---------------------------------------------------------------------------


def _row(*, period_tag, session_date, symbol, realized_pnl, r_multiple, is_winner):
    return TradeDiagnosticRow(
        decision={"session_date": session_date.isoformat(), "symbol": symbol, "period": period_tag},
        outcome={
            "closed": True, "is_winner": is_winner, "is_stopped": False,
            "realized_pnl": realized_pnl, "r_multiple": r_multiple,
        },
    )


def test_combined_trade_quality_uses_r_multiple_not_realized_pnl():
    # Every row has realized_pnl=100 (would produce a huge price-based
    # expectancy) but r_multiple alternates sign -- combined expectancy_r
    # must track r_multiple, proving no price-per-share field leaks in.
    rows_by_period = {
        "P0": [_row(period_tag="P0", session_date=date(2023, 4, 1), symbol="AAA", realized_pnl="100.00", r_multiple=1.0, is_winner=True)],
        "P1": [_row(period_tag="P1", session_date=date(2024, 4, 1), symbol="AAA", realized_pnl="100.00", r_multiple=-1.0, is_winner=False)],
    }
    result = combined_trade_quality(rows_by_period)
    assert result["expectancy_r_per_trade"] == pytest.approx(0.0)
    assert result["expectancy_price_per_share"] == pytest.approx(100.0)


def test_combined_trade_quality_pf_r_uses_positive_negative_r_totals():
    rows_by_period = {
        "P0": [
            _row(period_tag="P0", session_date=date(2023, 4, 1), symbol="AAA", realized_pnl="1", r_multiple=3.0, is_winner=True),
            _row(period_tag="P0", session_date=date(2023, 4, 2), symbol="AAA", realized_pnl="1", r_multiple=-1.5, is_winner=False),
        ],
    }
    result = combined_trade_quality(rows_by_period)
    assert result["gross_profit_r"] == pytest.approx(3.0)
    assert result["gross_loss_r"] == pytest.approx(1.5)
    assert result["profit_factor_r"] == pytest.approx(2.0)


def test_best_contributing_symbol_by_r_ranks_by_cumulative_r_not_dollars():
    rows = [
        _row(period_tag="P0", session_date=date(2023, 4, 1), symbol="BIGDOLLAR", realized_pnl="1000.00", r_multiple=0.1, is_winner=True),
        _row(period_tag="P0", session_date=date(2023, 4, 2), symbol="BIGR", realized_pnl="5.00", r_multiple=2.0, is_winner=True),
    ]
    assert best_contributing_symbol_by_r(rows) == "BIGR"


def test_best_contributing_symbol_by_r_none_for_empty_rows():
    assert best_contributing_symbol_by_r([]) is None


# ---------------------------------------------------------------------------
# One-trade-per-day: deterministic first-valid-candidate, not ranking.
# ---------------------------------------------------------------------------


def test_no_ranking_language_or_implementation_in_module():
    source = (REPOSITORY_ROOT / "src" / "orb_existing_configs_reevaluation_research.py").read_text(encoding="utf-8")
    assert "def rank_candidate" not in source
    assert "candidate_ranking" not in source
    assert "decision_time_ranked" not in source


def test_script_reuses_select_first_candidate_per_day_not_a_new_selector():
    source = (REPOSITORY_ROOT / "scripts" / "research_orb_existing_configs_reevaluation.py").read_text(encoding="utf-8")
    assert "select_first_candidate_per_day" in source
    assert "def select_first_candidate_per_day" not in source  # imported, not redefined


# ---------------------------------------------------------------------------
# Predeclared classification: R metrics only, never price-per-share.
# ---------------------------------------------------------------------------


def _one_day(**overrides):
    base = {
        "closed_trades": 30, "expectancy_r_per_trade": 0.2, "profit_factor_r": 1.3,
        "profit_factor_r_infinite_no_losses": False,
    }
    base.update(overrides)
    return base


def _all_cand(**overrides):
    base = {"expectancy_r_per_trade": 0.05}
    base.update(overrides)
    return base


GOOD_PERIODS = {"P0": 0.1, "P1": 0.05, "P2": 0.2, "P3": 0.3}


def test_classify_core_candidate_when_all_checks_pass():
    result = classify_configuration(
        combined_one_per_day=_one_day(), combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r=GOOD_PERIODS,
        one_per_day_excluding_best_symbol=_one_day(expectancy_r_per_trade=0.1),
    )
    assert result["verdict"] == "CORE_CANDIDATE"
    assert all(result["checks"].values())


def test_classify_ignores_price_per_share_fields_entirely():
    # Strongly positive price-per-share figures alongside a negative R
    # expectancy must NOT classify as CORE_CANDIDATE or even promising.
    contaminated = {
        "closed_trades": 30, "expectancy_per_trade": 50.0, "profit_factor": 10.0,  # price-based, must be ignored
        "expectancy_r_per_trade": -0.3, "profit_factor_r": 0.5, "profit_factor_r_infinite_no_losses": False,
    }
    result = classify_configuration(
        combined_one_per_day=contaminated, combined_all_candidates=contaminated,
        period_one_per_day_expectancy_r={"P0": -0.3, "P1": -0.3, "P2": -0.3, "P3": -0.3},
        one_per_day_excluding_best_symbol=contaminated,
    )
    assert result["verdict"] == "WEAK_OR_NO_EDGE"
    assert result["checks"]["one_per_day_expectancy_r_positive"] is False


def test_classify_insufficient_evidence_below_minimum_sample():
    result = classify_configuration(
        combined_one_per_day=_one_day(closed_trades=5), combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r=GOOD_PERIODS,
        one_per_day_excluding_best_symbol=_one_day(),
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_promising_but_unstable_when_positive_but_period_robustness_fails():
    result = classify_configuration(
        combined_one_per_day=_one_day(), combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r={"P0": -0.5, "P1": -0.5, "P2": 0.3, "P3": 0.3},  # only 2 of 4 non-negative
        one_per_day_excluding_best_symbol=_one_day(expectancy_r_per_trade=0.1),
    )
    assert result["verdict"] == "PROMISING_BUT_UNSTABLE"
    assert result["checks"]["period_robustness_at_least_3_of_4"] is False


def test_classify_promising_but_unstable_when_single_symbol_dependent():
    result = classify_configuration(
        combined_one_per_day=_one_day(), combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r=GOOD_PERIODS,
        one_per_day_excluding_best_symbol=_one_day(expectancy_r_per_trade=-0.1),  # flips negative
    )
    assert result["verdict"] == "PROMISING_BUT_UNSTABLE"
    assert result["checks"]["not_single_symbol_dependent"] is False


def test_classify_weak_when_all_candidates_materially_negative():
    result = classify_configuration(
        combined_one_per_day=_one_day(), combined_all_candidates=_all_cand(expectancy_r_per_trade=-0.5),
        period_one_per_day_expectancy_r=GOOD_PERIODS,
        one_per_day_excluding_best_symbol=_one_day(expectancy_r_per_trade=0.1),
    )
    assert result["verdict"] == "PROMISING_BUT_UNSTABLE"
    assert result["checks"]["all_candidates_not_materially_negative"] is False


def test_classify_weak_or_no_edge_when_one_per_day_negative():
    result = classify_configuration(
        combined_one_per_day=_one_day(expectancy_r_per_trade=-0.1, profit_factor_r=0.8),
        combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r={"P0": -0.1, "P1": -0.1, "P2": -0.1, "P3": -0.1},
        one_per_day_excluding_best_symbol=_one_day(),
    )
    assert result["verdict"] == "WEAK_OR_NO_EDGE"


def test_default_selection_criteria_minimum_is_15():
    assert DEFAULT_SELECTION_CRITERIA.minimum_combined_one_per_day_closed_trades == 15


def test_custom_selection_criteria_are_honoured():
    strict = SelectionCriteria(minimum_combined_one_per_day_closed_trades=1000)
    result = classify_configuration(
        combined_one_per_day=_one_day(closed_trades=100), combined_all_candidates=_all_cand(),
        period_one_per_day_expectancy_r=GOOD_PERIODS, one_per_day_excluding_best_symbol=_one_day(),
        criteria=strict,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# Next-milestone recommendation.
# ---------------------------------------------------------------------------


def test_recommend_validate_single_trade_core_when_core_candidate_exists():
    result = recommend_next_milestone(any_core_candidate=True, any_promising_but_unstable=False)
    assert result["next_milestone"] == "VALIDATE_SINGLE_TRADE_CORE"


def test_recommend_candidate_selection_research_when_only_promising():
    result = recommend_next_milestone(any_core_candidate=False, any_promising_but_unstable=True)
    assert result["next_milestone"] == "CANDIDATE_SELECTION_RESEARCH"


def test_recommend_pause_entry_research_when_neither():
    result = recommend_next_milestone(any_core_candidate=False, any_promising_but_unstable=False)
    assert result["next_milestone"] == "PAUSE_ENTRY_RESEARCH"


def test_recommendation_never_authorizes_production_change_or_new_threshold_search():
    for core, promising in ((True, False), (False, True), (False, False)):
        result = recommend_next_milestone(any_core_candidate=core, any_promising_but_unstable=promising)
        assert result["authorizes_production_change"] is False
        assert result["authorizes_new_threshold_search"] is False


# ---------------------------------------------------------------------------
# No production config changes.
# ---------------------------------------------------------------------------


def test_module_never_touches_strategy_yaml_or_enabled_flag():
    source = (REPOSITORY_ROOT / "src" / "orb_existing_configs_reevaluation_research.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
    assert "yaml.safe_dump" not in source


def test_module_never_changes_exit_stop_target_or_spy_logic():
    source = (REPOSITORY_ROOT / "src" / "orb_existing_configs_reevaluation_research.py").read_text(encoding="utf-8")
    assert "def simulate_shadow_execution" not in source
    assert "class ExitEngine" not in source
    assert "spy_vwap" not in source.lower().replace(" ", "_")


def test_script_never_writes_strategy_yaml_or_sets_enabled_flag():
    source = (REPOSITORY_ROOT / "scripts" / "research_orb_existing_configs_reevaluation.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
