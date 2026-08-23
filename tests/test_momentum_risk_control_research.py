"""Tests for the M53 (Issue #358) frozen risk-control decision logic:
the deterministic arm-selection rule and the opportunity-cost
comparison against the exact M52 baseline. Committed before any
empirical arm result is read, per Issue #358's sequencing requirement.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.cross_sectional_momentum_research import (
    CLASSIFICATION_NOT_PROMISING,
    CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT,
    CLASSIFICATION_TARGET_FEASIBLE,
)
from src.momentum_risk_control_research import (
    ARM_A_TOP_N,
    ARM_B_TOP_N,
    ARM_BREADTH_30,
    ARM_MARKET_VOL_15,
    M53_DECISION_PARKED,
    M53_DECISION_SUPPORTED,
    MARKET_VOL_LOOKBACK_SESSIONS,
    MARKET_VOL_TARGET_ANNUALIZED,
    MomentumRiskControlError,
    compute_opportunity_cost,
    select_m53_design,
)


def test_frozen_arm_config_matches_issue_358():
    assert ARM_BREADTH_30 == "BREADTH_30"
    assert ARM_MARKET_VOL_15 == "MARKET_VOL_15"
    assert ARM_A_TOP_N == 30
    assert ARM_B_TOP_N == 15
    assert MARKET_VOL_LOOKBACK_SESSIONS == 63
    assert MARKET_VOL_TARGET_ANNUALIZED == Decimal("0.15")


def _metrics(*, mean_monthly=Decimal("500"), endpoint_pnl=Decimal("30000"), max_dd=Decimal("0.20"), turnover=Decimal("1000000"), cagr=Decimal("0.12")):
    return {
        "mean_monthly_dollars": mean_monthly, "endpoint_marked_net_pnl": endpoint_pnl,
        "max_drawdown_pct": max_dd, "turnover": turnover, "cagr": cagr,
    }


# ---------------------------------------------------------------------------
# select_m53_design: the frozen selection rule.
# ---------------------------------------------------------------------------


def test_select_m53_design_selects_the_only_feasible_arm_a():
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_a_stress_metrics=_metrics(), arm_a_base_metrics=_metrics(),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT,
        arm_b_stress_metrics=_metrics(), arm_b_base_metrics=_metrics(),
    )
    assert result == {"decision": M53_DECISION_SUPPORTED, "selected_arm": ARM_BREADTH_30, "reason": "only_arm_a_target_feasible"}


def test_select_m53_design_selects_the_only_feasible_arm_b():
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_NOT_PROMISING,
        arm_a_stress_metrics=_metrics(), arm_a_base_metrics=_metrics(),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_b_stress_metrics=_metrics(), arm_b_base_metrics=_metrics(),
    )
    assert result == {"decision": M53_DECISION_SUPPORTED, "selected_arm": ARM_MARKET_VOL_15, "reason": "only_arm_b_target_feasible"}


def test_select_m53_design_parks_when_neither_arm_is_feasible():
    # "Best of two" is not sufficient for promotion -- even if arm A's
    # numbers look better, neither TARGET_FEASIBLE means no selection.
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT,
        arm_a_stress_metrics=_metrics(mean_monthly=Decimal("900")), arm_a_base_metrics=_metrics(max_dd=Decimal("0.10")),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_NOT_PROMISING,
        arm_b_stress_metrics=_metrics(mean_monthly=Decimal("10")), arm_b_base_metrics=_metrics(max_dd=Decimal("0.40")),
    )
    assert result == {"decision": M53_DECISION_PARKED, "selected_arm": None, "reason": "neither_arm_target_feasible"}


def test_select_m53_design_both_feasible_picks_higher_stress_mean_monthly_dollars():
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_a_stress_metrics=_metrics(mean_monthly=Decimal("600")), arm_a_base_metrics=_metrics(),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_b_stress_metrics=_metrics(mean_monthly=Decimal("700")), arm_b_base_metrics=_metrics(),
    )
    assert result["decision"] == M53_DECISION_SUPPORTED
    assert result["selected_arm"] == ARM_MARKET_VOL_15
    assert result["reason"] == "higher_stress_mean_monthly_dollars"


def test_select_m53_design_both_feasible_tiebreaks_on_lower_base_max_drawdown():
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_a_stress_metrics=_metrics(mean_monthly=Decimal("600")), arm_a_base_metrics=_metrics(max_dd=Decimal("0.22")),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_b_stress_metrics=_metrics(mean_monthly=Decimal("600")), arm_b_base_metrics=_metrics(max_dd=Decimal("0.18")),
    )
    assert result["decision"] == M53_DECISION_SUPPORTED
    assert result["selected_arm"] == ARM_MARKET_VOL_15
    assert result["reason"] == "tiebreak_lower_base_max_drawdown"


def test_select_m53_design_both_feasible_tiebreaks_on_lower_turnover_when_all_else_tied():
    result = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_a_stress_metrics=_metrics(mean_monthly=Decimal("600")), arm_a_base_metrics=_metrics(max_dd=Decimal("0.20"), turnover=Decimal("2000000")),
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_TARGET_FEASIBLE,
        arm_b_stress_metrics=_metrics(mean_monthly=Decimal("600")), arm_b_base_metrics=_metrics(max_dd=Decimal("0.20"), turnover=Decimal("1500000")),
    )
    assert result["decision"] == M53_DECISION_SUPPORTED
    assert result["selected_arm"] == ARM_MARKET_VOL_15
    assert result["reason"] == "tiebreak_lower_turnover"


def test_select_m53_design_raises_when_both_feasible_and_fully_tied():
    with pytest.raises(MomentumRiskControlError):
        select_m53_design(
            arm_a_label=ARM_BREADTH_30, arm_a_classification=CLASSIFICATION_TARGET_FEASIBLE,
            arm_a_stress_metrics=_metrics(), arm_a_base_metrics=_metrics(),
            arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=CLASSIFICATION_TARGET_FEASIBLE,
            arm_b_stress_metrics=_metrics(), arm_b_base_metrics=_metrics(),
        )


# ---------------------------------------------------------------------------
# compute_opportunity_cost.
# ---------------------------------------------------------------------------


def test_compute_opportunity_cost_reports_deltas_relative_to_baseline():
    arm = _metrics(mean_monthly=Decimal("450"), endpoint_pnl=Decimal("35000"), max_dd=Decimal("0.30"), turnover=Decimal("1800000"), cagr=Decimal("0.14"))
    baseline = _metrics(mean_monthly=Decimal("456.87"), endpoint_pnl=Decimal("35315.86"), max_dd=Decimal("0.3414"), turnover=Decimal("1509712.39"), cagr=Decimal("0.1515"))
    delta = compute_opportunity_cost(arm, baseline_metrics=baseline)
    assert delta["mean_monthly_dollars_delta"] == arm["mean_monthly_dollars"] - baseline["mean_monthly_dollars"]
    assert delta["endpoint_marked_net_pnl_delta"] == arm["endpoint_marked_net_pnl"] - baseline["endpoint_marked_net_pnl"]
    assert delta["max_drawdown_pct_delta"] == arm["max_drawdown_pct"] - baseline["max_drawdown_pct"]
    assert delta["turnover_delta"] == arm["turnover"] - baseline["turnover"]
    assert delta["cagr_delta"] == arm["cagr"] - baseline["cagr"]


def test_compute_opportunity_cost_cagr_delta_none_when_either_side_missing():
    arm = _metrics(cagr=None)
    baseline = _metrics(cagr=Decimal("0.10"))
    delta = compute_opportunity_cost(arm, baseline_metrics=baseline)
    assert delta["cagr_delta"] is None
