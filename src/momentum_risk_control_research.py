"""M53 (Issue #358): bounded cross-sectional momentum risk-control
feasibility -- two predeclared, economically interpretable risk
controls layered on M52's exact frozen momentum strategy
(`src/cross_sectional_momentum_research.py`), each tested once, to
determine whether either preserves M52's positive economics while
bringing drawdown/tail risk inside M52's own `TARGET_FEASIBLE` limits.

This module is the frozen protocol and pure decision logic only:
M53-specific arm constants, the opportunity-cost comparison against
the exact M52 baseline, and the deterministic arm-selection rule --
committed before any empirical result is read. Universe, liquidity,
momentum, trend filter, portfolio mechanics, costs, metrics, and
classification are ALL reused unchanged from
`src.cross_sectional_momentum_research`, not reimplemented: both arms
are produced by calling `simulate_cross_sectional_momentum_portfolio`
with different optional arguments (`top_n`, `vol_target_annualized`)
-- the exact same code path M52's own baseline uses with no new
arguments, so baseline reproduction is bit-for-bit by construction,
not a separately maintained copy. All network/cache I/O and
orchestration lives in `scripts/research_momentum_risk_control.py`;
this module only consumes already-simulated results.

Arm A -- BREADTH_30: portfolio breadth top 15 -> top 30
(`simulate_cross_sectional_momentum_portfolio(..., top_n=ARM_A_TOP_N)`),
equal-capital weighting, every other M52 rule identical. Tests whether
reducing single-name concentration improves drawdown/tail behavior
without destroying the momentum return.

Arm B -- MARKET_VOL_15: M52's own top-15 selection unchanged
(`top_n=ARM_B_TOP_N`), with one lagged, monthly market-volatility
exposure cap layered on top
(`vol_target_annualized=MARKET_VOL_TARGET_ANNUALIZED`,
`vol_lookback_sessions=MARKET_VOL_LOOKBACK_SESSIONS`) --
`compute_market_vol_exposure_multiplier` in the M52 module. The
existing SPY 200-session trend filter remains unchanged and takes
precedence: when it is off, `selected` is already empty and the
multiplier has no effect, so exposure is zero either way. Tests an
explicit, lagged market-risk throttle without changing stock
selection.

No combined arm. No other breadth, volatility target, lookback,
stop, hedge, sector cap, or alternate configuration is in scope.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.cross_sectional_momentum_research import CLASSIFICATION_TARGET_FEASIBLE, TOP_N

__all__ = [
    "ARM_BREADTH_30",
    "ARM_MARKET_VOL_15",
    "ARM_A_TOP_N",
    "ARM_B_TOP_N",
    "MARKET_VOL_LOOKBACK_SESSIONS",
    "MARKET_VOL_TARGET_ANNUALIZED",
    "M53_DECISION_SUPPORTED",
    "M53_DECISION_PARKED",
    "MomentumRiskControlError",
    "compute_opportunity_cost",
    "select_m53_design",
]


class MomentumRiskControlError(RuntimeError):
    """Refused: an M53 (Issue #358) protocol precondition failed -- e.g.
    baseline parity did not hold, or the frozen tiebreak was exhausted
    without a winner. Never silently substituted or guessed."""


# ---------------------------------------------------------------------------
# Frozen arm configuration (Issue #358). Exactly two new arms, no others.
# ---------------------------------------------------------------------------

ARM_BREADTH_30 = "BREADTH_30"
ARM_MARKET_VOL_15 = "MARKET_VOL_15"

#: Arm A: portfolio breadth only, every other M52 rule identical.
ARM_A_TOP_N = 30

#: Arm B: M52's own top-15 selection, unchanged.
ARM_B_TOP_N = TOP_N

#: Arm B: SPY trailing daily-return lookback for the exposure multiplier.
MARKET_VOL_LOOKBACK_SESSIONS = 63

#: Arm B: annualized volatility target the exposure multiplier scales to.
MARKET_VOL_TARGET_ANNUALIZED = Decimal("0.15")


M53_DECISION_SUPPORTED = "SUPPORTED_FOR_M54"
M53_DECISION_PARKED = "PARKED_NO_CREDIBLE_RISK_CONTROL"


def compute_opportunity_cost(arm_metrics: dict[str, Any], *, baseline_metrics: dict[str, Any]) -> dict[str, Any]:
    """Delta of an arm's key metrics relative to the exact M52 baseline
    (same cost scenario). Reported for transparency only -- never used
    to justify weakening a classification gate."""
    arm_cagr = arm_metrics.get("cagr")
    baseline_cagr = baseline_metrics.get("cagr")
    return {
        "mean_monthly_dollars_delta": arm_metrics["mean_monthly_dollars"] - baseline_metrics["mean_monthly_dollars"],
        "endpoint_marked_net_pnl_delta": arm_metrics["endpoint_marked_net_pnl"] - baseline_metrics["endpoint_marked_net_pnl"],
        "max_drawdown_pct_delta": arm_metrics["max_drawdown_pct"] - baseline_metrics["max_drawdown_pct"],
        "cagr_delta": (arm_cagr - baseline_cagr) if arm_cagr is not None and baseline_cagr is not None else None,
        "turnover_delta": arm_metrics["turnover"] - baseline_metrics["turnover"],
    }


def select_m53_design(
    *,
    arm_a_label: str,
    arm_a_classification: str,
    arm_a_stress_metrics: dict[str, Any],
    arm_a_base_metrics: dict[str, Any],
    arm_b_label: str,
    arm_b_classification: str,
    arm_b_stress_metrics: dict[str, Any],
    arm_b_base_metrics: dict[str, Any],
) -> dict[str, Any]:
    """The frozen M53 selection rule (Issue #358), applied once, without
    tuning:
    - exactly one arm `TARGET_FEASIBLE` -> select it as the supported
      M53 design;
    - both `TARGET_FEASIBLE` -> select deterministically by higher
      stress-cost mean monthly dollars; tie -> lower base max drawdown;
      still tied -> lower turnover;
    - neither `TARGET_FEASIBLE` -> report both honestly, park this
      branch -- being "best of two" is not sufficient for promotion,
      so no arm is selected and no automatic M54 follows."""
    a_feasible = arm_a_classification == CLASSIFICATION_TARGET_FEASIBLE
    b_feasible = arm_b_classification == CLASSIFICATION_TARGET_FEASIBLE

    if a_feasible and not b_feasible:
        return {"decision": M53_DECISION_SUPPORTED, "selected_arm": arm_a_label, "reason": "only_arm_a_target_feasible"}
    if b_feasible and not a_feasible:
        return {"decision": M53_DECISION_SUPPORTED, "selected_arm": arm_b_label, "reason": "only_arm_b_target_feasible"}
    if not a_feasible and not b_feasible:
        return {"decision": M53_DECISION_PARKED, "selected_arm": None, "reason": "neither_arm_target_feasible"}

    # Both TARGET_FEASIBLE -- deterministic tiebreak, in the frozen order.
    a_stress = arm_a_stress_metrics["mean_monthly_dollars"]
    b_stress = arm_b_stress_metrics["mean_monthly_dollars"]
    if a_stress != b_stress:
        winner = arm_a_label if a_stress > b_stress else arm_b_label
        return {"decision": M53_DECISION_SUPPORTED, "selected_arm": winner, "reason": "higher_stress_mean_monthly_dollars"}

    a_dd = arm_a_base_metrics["max_drawdown_pct"]
    b_dd = arm_b_base_metrics["max_drawdown_pct"]
    if a_dd != b_dd:
        winner = arm_a_label if a_dd < b_dd else arm_b_label
        return {"decision": M53_DECISION_SUPPORTED, "selected_arm": winner, "reason": "tiebreak_lower_base_max_drawdown"}

    a_turnover = arm_a_base_metrics["turnover"]
    b_turnover = arm_b_base_metrics["turnover"]
    if a_turnover != b_turnover:
        winner = arm_a_label if a_turnover < b_turnover else arm_b_label
        return {"decision": M53_DECISION_SUPPORTED, "selected_arm": winner, "reason": "tiebreak_lower_turnover"}

    raise MomentumRiskControlError(
        "both arms TARGET_FEASIBLE and tied on stress mean monthly dollars, base max drawdown, and turnover -- "
        "the frozen tiebreak sequence is exhausted; refusing to guess a winner"
    )
