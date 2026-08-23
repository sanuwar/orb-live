"""Tests for the M54 (Issue #360) pure capital-scaling arithmetic:
percent-return implied by a fixed dollar target, its simple/compounded
annualizations, the linear-scaling capital estimate, and the scaled
dollar drawdown. No strategy results, no market data -- pure math
only, committed before the evidence-synthesis conclusions."""
from __future__ import annotations

from decimal import Decimal

import pytest

from src.capital_target_evidence_synthesis import (
    STARTING_CAPITAL_30K,
    TARGET_MONTHLY_1000,
    TARGET_MONTHLY_500,
    CapitalTargetEvidenceError,
    compounded_annual_pct,
    monthly_return_pct_for_target,
    required_capital_for_target,
    scaled_drawdown_dollars,
    simple_annual_pct,
)


def test_frozen_targets_match_issue_360():
    assert STARTING_CAPITAL_30K == Decimal("30000")
    assert TARGET_MONTHLY_500 == Decimal("500")
    assert TARGET_MONTHLY_1000 == Decimal("1000")


def test_500_per_month_on_30k_is_1_6667_pct_monthly():
    pct = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_500)
    assert pct == Decimal("500") / Decimal("30000")
    assert round(pct * 100, 4) == Decimal("1.6667")


def test_500_per_month_simple_annualizes_to_20_pct():
    pct = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_500)
    annual = simple_annual_pct(pct)
    assert annual == Decimal("0.2")


def test_1000_per_month_on_30k_is_3_3333_pct_monthly():
    pct = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_1000)
    assert round(pct * 100, 4) == Decimal("3.3333")


def test_1000_per_month_simple_annualizes_to_40_pct():
    pct = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_1000)
    annual = simple_annual_pct(pct)
    assert annual == Decimal("0.4")


def test_monthly_return_pct_rejects_nonpositive_capital():
    with pytest.raises(CapitalTargetEvidenceError):
        monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_500, capital=Decimal("0"))


def test_compounded_annual_pct_exceeds_simple_for_a_positive_monthly_return():
    # Compounding a positive monthly return always yields a strictly
    # larger annual figure than simple multiplication by 12.
    monthly = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_500)
    assert compounded_annual_pct(monthly) > simple_annual_pct(monthly)


def test_compounded_annual_pct_known_value():
    # 1%/month compounded for 12 months: 1.01^12 - 1 ~= 12.6825%.
    result = compounded_annual_pct(Decimal("0.01"))
    assert abs(result - Decimal("0.126825")) < Decimal("0.0001")


def test_required_capital_for_target_matches_frozen_formula():
    # A strategy observed at $456.87/month mean on the standard $30,000
    # base, scaled to the $500 target.
    observed_mean = Decimal("456.87")
    required = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    assert required == STARTING_CAPITAL_30K * TARGET_MONTHLY_500 / observed_mean


def test_required_capital_for_target_scales_linearly_with_target():
    observed_mean = Decimal("456.87")
    required_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    required_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    # Decimal division context precision can differ in the last digit
    # between computing 2x directly vs. doubling a separately-rounded
    # result -- a Decimal-precision artifact, not a linearity bug.
    assert abs(required_1000 - required_500 * 2) < Decimal("0.0000001")


def test_required_capital_for_target_rejects_zero_observed_mean():
    with pytest.raises(CapitalTargetEvidenceError):
        required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=Decimal("0"))


def test_required_capital_for_target_rejects_negative_observed_mean():
    with pytest.raises(CapitalTargetEvidenceError):
        required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=Decimal("-10"))


def test_required_capital_for_target_rejects_nonpositive_starting_capital():
    with pytest.raises(CapitalTargetEvidenceError):
        required_capital_for_target(
            target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=Decimal("100"),
            observed_starting_capital=Decimal("0"),
        )


def test_scaled_drawdown_dollars_matches_frozen_formula():
    result = scaled_drawdown_dollars(required_capital=Decimal("32831.20"), observed_max_drawdown_pct=Decimal("0.3414"))
    assert result == Decimal("32831.20") * Decimal("0.3414")


def test_scaled_drawdown_dollars_scales_linearly_with_capital():
    dd_pct = Decimal("0.25")
    small = scaled_drawdown_dollars(required_capital=Decimal("30000"), observed_max_drawdown_pct=dd_pct)
    large = scaled_drawdown_dollars(required_capital=Decimal("60000"), observed_max_drawdown_pct=dd_pct)
    assert large == small * 2


def test_scaled_drawdown_dollars_rejects_negative_required_capital():
    with pytest.raises(CapitalTargetEvidenceError):
        scaled_drawdown_dollars(required_capital=Decimal("-100"), observed_max_drawdown_pct=Decimal("0.1"))


def test_scaled_drawdown_dollars_rejects_negative_drawdown_pct():
    with pytest.raises(CapitalTargetEvidenceError):
        scaled_drawdown_dollars(required_capital=Decimal("30000"), observed_max_drawdown_pct=Decimal("-0.1"))


# ---------------------------------------------------------------------------
# M54 (Issue #360) results reconciliation: the Section 4 capital-scaling
# table in docs/research/CAPITAL_TARGET_EVIDENCE_SYNTHESIS_M54.md is
# computed from these exact figures, pulled directly from each
# milestone's final committed result (M48's docs/research/
# ORB_CAPITAL_TARGET_STRATEGY_FAMILY_FEASIBILITY.md Round 4, M49's
# docs/research/ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_FEASIBILITY.md,
# M52's artifacts/research-cross-sectional-momentum/results/
# m52_result.json, M53's artifacts/research-momentum-risk-control/
# results/m53_result.json). These tests pin the report's own numbers to
# this module's actual output so a future change to the module cannot
# silently drift the published report without a test failure.
# ---------------------------------------------------------------------------


def _assert_currency_close(actual: Decimal, expected_str: str) -> None:
    assert abs(actual - Decimal(expected_str)) < Decimal("0.01")


def test_reconciled_m52_baseline_capital_scaling_matches_report():
    observed_mean = Decimal("456.87")
    observed_dd = Decimal("0.3414")
    req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    _assert_currency_close(req_500, "32832.10")
    _assert_currency_close(req_1000, "65664.19")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_500, observed_max_drawdown_pct=observed_dd), "11208.88")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_1000, observed_max_drawdown_pct=observed_dd), "22417.76")


def test_reconciled_m49_core_satellite_capital_scaling_matches_report():
    observed_mean = Decimal("184.75")
    observed_dd = Decimal("0.1460")
    req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    _assert_currency_close(req_500, "81190.80")
    _assert_currency_close(req_1000, "162381.60")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_500, observed_max_drawdown_pct=observed_dd), "11853.86")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_1000, observed_max_drawdown_pct=observed_dd), "23707.71")


def test_reconciled_m53_breadth_30_capital_scaling_matches_report():
    observed_mean = Decimal("328.24")
    observed_dd = Decimal("0.2725")
    req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    _assert_currency_close(req_500, "45698.27")
    _assert_currency_close(req_1000, "91396.54")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_500, observed_max_drawdown_pct=observed_dd), "12452.78")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_1000, observed_max_drawdown_pct=observed_dd), "24905.56")


def test_reconciled_m53_market_vol_15_capital_scaling_matches_report():
    observed_mean = Decimal("456.72")
    observed_dd = Decimal("0.3429")
    req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    _assert_currency_close(req_500, "32842.88")
    _assert_currency_close(req_1000, "65685.76")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_500, observed_max_drawdown_pct=observed_dd), "11261.82")
    _assert_currency_close(scaled_drawdown_dollars(required_capital=req_1000, observed_max_drawdown_pct=observed_dd), "22523.65")


def test_reconciled_m48_arm_capital_scaling_matches_report():
    arms = {
        "ARM_A": (Decimal("78.53"), Decimal("0.0904"), "191009.81", "382019.61", "17267.29", "34534.57"),
        "ARM_B": (Decimal("196.72"), Decimal("0.2356"), "76250.51", "152501.02", "17964.62", "35929.24"),
        "ARM_C": (Decimal("177.26"), Decimal("0.2679"), "84621.46", "169242.92", "22670.09", "45340.18"),
    }
    for label, (mean, dd, req500_expected, req1000_expected, sdd500_expected, sdd1000_expected) in arms.items():
        req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=mean)
        req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=mean)
        _assert_currency_close(req_500, req500_expected)
        _assert_currency_close(req_1000, req1000_expected)
        _assert_currency_close(scaled_drawdown_dollars(required_capital=req_500, observed_max_drawdown_pct=dd), sdd500_expected)
        _assert_currency_close(scaled_drawdown_dollars(required_capital=req_1000, observed_max_drawdown_pct=dd), sdd1000_expected)


def test_reconciled_orb_standalone_capital_scaling_matches_report():
    # The corrected, more-rigorous ORB standalone figure ($59.00/month,
    # from M49's actual chronological simulation), not the superseded
    # $108.82-$108.90/month non-simulated R x $300 approximation cited
    # in M44/M45/M48/M51 -- see report Section 3a.
    observed_mean = Decimal("59.00")
    req_500 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_500, observed_mean_monthly_dollars=observed_mean)
    req_1000 = required_capital_for_target(target_monthly_dollars=TARGET_MONTHLY_1000, observed_mean_monthly_dollars=observed_mean)
    _assert_currency_close(req_500, "254237.29")
    _assert_currency_close(req_1000, "508474.58")


def test_500_and_1000_per_month_compounded_annual_pct_match_report():
    m500 = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_500)
    m1000 = monthly_return_pct_for_target(target_monthly_dollars=TARGET_MONTHLY_1000)
    assert round(compounded_annual_pct(m500) * 100, 2) == Decimal("21.94")
    assert round(compounded_annual_pct(m1000) * 100, 2) == Decimal("48.21")
