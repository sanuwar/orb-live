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
