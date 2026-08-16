"""Research tooling for the RVOL 1.50x-1.99x band third-period validation
study (Issue #317).

Issue #310/#315 (regime diagnostic, merged PR #316) found that restricting
RVOL to the 1.50x-1.99x range specifically -- not merely "RVOL >= 1.50x" --
improved expectancy for two independent configurations (gap>=0.75%/RVOL
base 1.50x, and gap>=0.50%/RVOL base 1.50x) in both the 90-session
discovery period and the 252-session out-of-sample period, and survived
removing each config's best-contributing symbol. This module validates
that same band on a THIRD independent historical period -- the ~252
sessions immediately preceding the out-of-sample period's own first
session (2025-04-03) -- reusing the identical configurations and band
definition, not a new sweep.

Reuse, not reinterpretation: candidate selection uses the unmodified
`evaluate_combo`/`precompute_inputs_table`
(`src.orb_gap_rvol_sensitivity_research`) with each config's own base RVOL
threshold (1.50x) already gating admission -- that IS the BASE view.
BAND is a post-hoc filter of those same candidates by the canonical
engine's own already-computed `SignalDecision.relative_volume`, never a
second engine run with a different threshold, so BAND is always a strict
subset of BASE. Trade-quality simulation reuses
`orb_out_of_sample_validation_research.simulate_candidate_signals_detailed`/
`extended_trade_stats`/`select_first_candidate_per_day` unchanged.

No new filters, no new stop/target/exit rule, no gap/timing/SPY-rule
variation, and no neighboring-band search are introduced here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.orb_gap_rvol_sensitivity_research import (
    CandidateSignal,
    ComboEvaluation,
    ParameterCombo,
    matrix_row,
)
from src.orb_out_of_sample_validation_research import (
    CONFIG_B_CONSERVATIVE_RELAXED,
    CONFIG_C_FINE_STUDY_RUNNER_UP,
    extended_trade_stats,
    select_first_candidate_per_day,
    simulate_candidate_signals_detailed,
)

__all__ = [
    "RvolBandThirdPeriodError",
    "CONFIG_B",
    "CONFIG_C",
    "STUDY_CONFIGS",
    "RVOL_BAND_LOWER",
    "RVOL_BAND_UPPER",
    "in_band",
    "band_candidate_signals",
    "cell_metrics",
    "band_effect",
    "PRIOR_PERIOD_BAND_REFERENCE",
    "three_period_comparison",
    "ValidationCriteria",
    "DEFAULT_VALIDATION_CRITERIA",
    "classify_band_validation",
    "final_decision",
]


class RvolBandThirdPeriodError(RuntimeError):
    """Refused: third-period RVOL-band validation tooling could not safely
    proceed."""


# ---------------------------------------------------------------------------
# The two configurations under study. Reused unchanged from the merged
# out-of-sample validation module (Issue #304/#307/#314) -- these labels
# (B=0.75%/1.50x, C=0.50%/1.50x) match that study's own A-E lettering, not
# the regime diagnostic's (#310/#315) differently-lettered A/B/C.
# ---------------------------------------------------------------------------

CONFIG_B: ParameterCombo = CONFIG_B_CONSERVATIVE_RELAXED  # gap >=0.75%, RVOL base >=1.50x
CONFIG_C: ParameterCombo = CONFIG_C_FINE_STUDY_RUNNER_UP   # gap >=0.50%, RVOL base >=1.50x

STUDY_CONFIGS: Mapping[str, ParameterCombo] = {"B": CONFIG_B, "C": CONFIG_C}

#: The band under validation: RVOL >= 1.50x AND < 2.00x, exactly the range
#: identified as PROMISING in the merged regime diagnostic (Issue #310/#315,
#: PR #316) -- not adjustable by this study.
RVOL_BAND_LOWER = Decimal("1.50")
RVOL_BAND_UPPER = Decimal("2.00")


def in_band(relative_volume: Decimal) -> bool:
    return RVOL_BAND_LOWER <= relative_volume < RVOL_BAND_UPPER


def band_candidate_signals(candidate_signals: Sequence[CandidateSignal]) -> tuple[CandidateSignal, ...]:
    """The strict subset of `candidate_signals` (already gated at each
    config's own base RVOL threshold, e.g. >=1.50x, by `evaluate_combo`)
    whose canonical `SignalDecision.relative_volume` also falls below
    `RVOL_BAND_UPPER` -- i.e. exactly the BAND view. Never re-runs the
    signal engine and never touches gap, timing, or any other threshold."""
    return tuple(c for c in candidate_signals if in_band(c[4].relative_volume))


# ---------------------------------------------------------------------------
# Per-cell metrics (BASE or BAND). Reuses
# `simulate_candidate_signals_detailed`/`extended_trade_stats` unchanged for
# trade-quality/monthly/symbol figures; adds only the candidate-day-rate/
# concentration/one-trade-per-day bookkeeping those helpers do not already
# carry in the shape this study's goal document asks for.
# ---------------------------------------------------------------------------


def _symbol_concentration_and_exclusions(by_symbol: Mapping[str, Any]) -> dict[str, Any]:
    """PnL-based concentration (top-3 share of total realized P&L) and the
    two required exclusion views (best-contributing symbol, TSLA), computed
    from `extended_trade_stats(...)["by_symbol"]` -- already ranked
    descending by realized P&L -- without re-deriving anything."""
    ranked = list(by_symbol.items())  # already sorted descending by realized_pnl (extended_trade_stats)
    total = sum((Decimal(v["realized_pnl"]) for _s, v in ranked), Decimal("0"))
    top3 = sum((Decimal(v["realized_pnl"]) for _s, v in ranked[:3]), Decimal("0"))
    top3_share = float(top3 / total) if total != 0 else None
    if ranked:
        best_symbol, best_stats = ranked[0]
        best_pnl = Decimal(best_stats["realized_pnl"])
    else:
        best_symbol, best_pnl = None, Decimal("0")
    excluding_best_symbol = total - best_pnl
    tsla_pnl = Decimal(by_symbol.get("TSLA", {}).get("realized_pnl", "0"))
    excluding_tsla = total - tsla_pnl
    return {
        "total_realized_pnl": str(total),
        "top_3_share_of_total_pnl": top3_share,
        "best_contributing_symbol": best_symbol,
        "best_contributing_symbol_pnl": str(best_pnl),
        "profitable_excluding_best_symbol": bool(excluding_best_symbol > 0) if ranked else None,
        "result_excluding_best_symbol": str(excluding_best_symbol),
        "tsla_contribution_pnl": str(tsla_pnl),
        "profitable_excluding_tsla": bool(excluding_tsla > 0) if ranked else None,
        "result_excluding_tsla": str(excluding_tsla),
    }


def cell_metrics(
    *,
    cell_label: str,
    combo: ParameterCombo,
    candidate_signals: Sequence[CandidateSignal],
    total_sessions: int,
    market_bars_by_symbol: Mapping[str, Sequence],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
    session_bounds_provider=None,
) -> dict[str, Any]:
    """Full metric set (goal item "METRICS") for one study cell -- one of
    B_BASE/B_BAND/C_BASE/C_BAND -- built entirely from
    `candidate_signals` (already the correct BASE or BAND subset by the
    time this is called) replayed through the unmodified simulator."""
    candidate_days = len({session_date for _symbol, session_date, *_rest in candidate_signals})
    all_summary, all_results = simulate_candidate_signals_detailed(
        candidate_signals=candidate_signals, combo=combo, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, implementation_digest=implementation_digest, created_at=created_at,
        run_label=f"third-period-{cell_label}", session_bounds_provider=session_bounds_provider,
    )
    extended = extended_trade_stats(all_results)
    concentration = _symbol_concentration_and_exclusions(extended["by_symbol"])

    one_per_day_signals = select_first_candidate_per_day(candidate_signals)
    one_summary, one_results = simulate_candidate_signals_detailed(
        candidate_signals=one_per_day_signals, combo=combo, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, implementation_digest=implementation_digest, created_at=created_at,
        run_label=f"third-period-{cell_label}-one-per-day", session_bounds_provider=session_bounds_provider,
    )

    return {
        "cell": cell_label,
        "gap_percent": str(combo.gap_percent),
        "rvol_multiple": str(combo.rvol_multiple),
        "sessions": total_sessions,
        "candidate_days": candidate_days,
        "candidate_day_rate": (candidate_days / total_sessions) if total_sessions else 0.0,
        "candidate_occurrences": len(candidate_signals),
        "trade_quality": all_summary,
        "longest_losing_streak": extended["longest_losing_streak"],
        "best_day": extended["best_day"],
        "worst_day": extended["worst_day"],
        "monthly": extended["monthly"],
        "symbol_contribution": extended["by_symbol"],
        "concentration": concentration,
        "one_trade_per_day": {
            "candidate_occurrences": len(one_per_day_signals),
            "trade_quality": one_summary,
        },
    }


def band_effect(*, base: Mapping[str, Any], band: Mapping[str, Any]) -> dict[str, Any]:
    """Goal item "BAND EFFECT": Metric | BASE | BAND | Delta, for the
    fields the goal document names explicitly. `None` deltas are reported
    rather than coerced to 0 when either side lacks a value (e.g. no closed
    trades)."""

    def _f(value: Any) -> float | None:
        if value in (None, "INFINITE_NO_LOSSES"):
            return None
        return float(value)

    base_tq, band_tq = base["trade_quality"], band["trade_quality"]
    rows: dict[str, dict[str, Any]] = {}

    def _row(name: str, base_value: Any, band_value: Any) -> None:
        b, d = _f(base_value), _f(band_value)
        rows[name] = {
            "BASE": base_value, "BAND": band_value,
            "delta": (d - b) if (b is not None and d is not None) else None,
        }

    _row("trade_count", base_tq["trades_closed"], band_tq["trades_closed"])
    _row("candidate_day_rate", base["candidate_day_rate"], band["candidate_day_rate"])
    _row("win_rate", base_tq["win_rate"], band_tq["win_rate"])
    _row("expectancy_per_trade", base_tq["expectancy_per_trade"], band_tq["expectancy_per_trade"])
    _row("profit_factor", base_tq["profit_factor"], band_tq["profit_factor"])
    _row("cumulative_r_multiple", base_tq["cumulative_r_multiple"], band_tq["cumulative_r_multiple"])
    _row("maximum_drawdown", base_tq["maximum_drawdown"], band_tq["maximum_drawdown"])
    _row("stop_out_rate", base_tq["stop_out_rate"], band_tq["stop_out_rate"])
    return rows


# ---------------------------------------------------------------------------
# Prior-period BAND reference figures. Sourced from the merged Issue
# #310/#315 regime-diagnostic artifact (PR #316,
# `artifacts/research-regime-diagnostic/results/orb_regime_diagnostic_report.json`
# and `orb_regime_diagnostic_trades.csv`, RVOL bucket "1.50-1.99"),
# restated here as fixed constants -- exactly the discipline
# `orb_out_of_sample_validation_research.DISCOVERY_PERIOD_REFERENCE` already
# established. Hardcoded on purpose: this study measures the third period
# honestly and compares against what #316 actually reported, never a figure
# this module could quietly redefine after the fact. Note #316's own
# A/B/C lettering differs from this study's: #316's B (0.50%/1.50x) is this
# study's C; #316's C (0.75%/1.50x) is this study's B.
# ---------------------------------------------------------------------------

PRIOR_PERIOD_BAND_REFERENCE: Mapping[str, Mapping[str, Mapping[str, Any]]] = {
    "B": {  # this study's B = 0.75%/1.50x = #316's C_0.75gap_1.50rvol
        "discovery": {
            "period_label": "2026-04-07..2026-08-13 (90 sessions)",
            "closed_trades": 22, "win_rate": 0.6818181818181818, "expectancy_per_trade": 1.360681818181818,
            "profit_factor": 3.0023411371237456, "cumulative_r_multiple": 6.324134956384941,
            "top_3_share_of_total_pnl": 0.705, "best_contributing_symbol": "MSFT",
            "profitable_excluding_best_symbol": True, "profitable_excluding_tsla": True,
        },
        "out_of_sample": {
            "period_label": "2025-04-03..2026-04-06 (252 sessions)",
            "closed_trades": 56, "win_rate": 0.42857142857142855, "expectancy_per_trade": 1.0273214285714285,
            "profit_factor": 1.8700189035916823, "cumulative_r_multiple": 4.655625314793841,
            "top_3_share_of_total_pnl": 1.3189, "best_contributing_symbol": "META",
            "profitable_excluding_best_symbol": True, "profitable_excluding_tsla": True,
        },
    },
    "C": {  # this study's C = 0.50%/1.50x = #316's B_0.50gap_1.50rvol
        "discovery": {
            "period_label": "2026-04-07..2026-08-13 (90 sessions)",
            "closed_trades": 26, "win_rate": 0.6538461538461539, "expectancy_per_trade": 1.7846153846153847,
            "profit_factor": 3.626662892725729, "cumulative_r_multiple": 8.324134956384942,
            "top_3_share_of_total_pnl": 0.8148, "best_contributing_symbol": "TSLA",
            "profitable_excluding_best_symbol": True, "profitable_excluding_tsla": True,
        },
        "out_of_sample": {
            "period_label": "2025-04-03..2026-04-06 (252 sessions)",
            "closed_trades": 70, "win_rate": 0.4142857142857143, "expectancy_per_trade": 0.7843571428571429,
            "profit_factor": 1.619345741680767, "cumulative_r_multiple": 3.9897328588012333,
            "top_3_share_of_total_pnl": 1.1485, "best_contributing_symbol": "TSLA",
            "profitable_excluding_best_symbol": True, "profitable_excluding_tsla": True,
        },
    },
}


def three_period_comparison(*, label: str, third_period_band_metrics: Mapping[str, Any]) -> dict[str, Any]:
    """Goal item "THREE-PERIOD COMPARISON": third period (measured here) +
    the two prior periods (hardcoded reference above), for one config's
    BAND cell."""
    reference = PRIOR_PERIOD_BAND_REFERENCE[label]
    tq = third_period_band_metrics["trade_quality"]
    third = {
        "period_label": "third period (this study)",
        "closed_trades": tq["trades_closed"], "win_rate": tq["win_rate"],
        "expectancy_per_trade": tq["expectancy_per_trade"], "profit_factor": tq["profit_factor"],
        "cumulative_r_multiple": tq["cumulative_r_multiple"],
        "top_3_share_of_total_pnl": third_period_band_metrics["concentration"]["top_3_share_of_total_pnl"],
        "best_contributing_symbol": third_period_band_metrics["concentration"]["best_contributing_symbol"],
        "profitable_excluding_best_symbol": third_period_band_metrics["concentration"]["profitable_excluding_best_symbol"],
        "profitable_excluding_tsla": third_period_band_metrics["concentration"]["profitable_excluding_tsla"],
    }
    return {
        "config": label,
        "third_period": third,
        "out_of_sample": reference["out_of_sample"],
        "discovery": reference["discovery"],
    }


# ---------------------------------------------------------------------------
# Predeclared VALIDATED/MIXED/FAILED/INSUFFICIENT_EVIDENCE classification.
# Fixed here before any third-period number is interpreted (goal item
# "SUCCESS CRITERIA").
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationCriteria:
    minimum_closed_trades: int = 15
    band_expectancy_tolerance: float = 0.0  # BAND must not be worse than BASE by more than this
    band_profit_factor_tolerance: float = 0.0


DEFAULT_VALIDATION_CRITERIA = ValidationCriteria()


def _pf_value(raw: Any) -> float | None:
    if raw is None:
        return None
    if raw == "INFINITE_NO_LOSSES":
        return float("inf")
    return float(raw)


def classify_band_validation(
    *,
    label: str,
    band_metrics: Mapping[str, Any],
    base_metrics: Mapping[str, Any],
    reference: Mapping[str, Mapping[str, Any]],
    criteria: ValidationCriteria = DEFAULT_VALIDATION_CRITERIA,
) -> dict[str, Any]:
    """VALIDATED / MIXED / FAILED / INSUFFICIENT_EVIDENCE for one config's
    BAND cell on the third period, against the six predeclared criteria
    (goal item "SUCCESS CRITERIA"):

    1. sufficient sample (>= `criteria.minimum_closed_trades` closed trades);
    2. expectancy > 0 in the third period;
    3. profit_factor > 1 in the third period;
    4. BAND is not materially worse than BASE on both expectancy and PF
       (comparison, not a frequency requirement -- BAND may trade less);
    5. not dependent on one symbol (`profitable_excluding_best_symbol` is
       True, or there are too few distinct symbols to judge, which is
       reported but does not by itself cause FAILED);
    6. direction is consistent with the prior two periods (this same BAND
       cell's reference expectancy is positive in both, per
       `PRIOR_PERIOD_BAND_REFERENCE`).

    Criteria are exactly as declared before any third-period number was
    computed; nothing here is loosened after seeing results."""
    tq = band_metrics["trade_quality"]
    closed = tq["trades_closed"] or 0
    if closed < criteria.minimum_closed_trades:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": f"third-period closed trades={closed} (minimum {criteria.minimum_closed_trades})",
            "checks": {},
        }

    expectancy = float(tq["expectancy_per_trade"]) if tq["expectancy_per_trade"] is not None else None
    profit_factor = _pf_value(tq["profit_factor"])
    positive_expectancy = expectancy is not None and expectancy > 0
    pf_above_1 = profit_factor is not None and profit_factor > 1

    base_tq = base_metrics["trade_quality"]
    base_expectancy = float(base_tq["expectancy_per_trade"]) if base_tq["expectancy_per_trade"] is not None else None
    base_pf = _pf_value(base_tq["profit_factor"])
    band_not_worse_expectancy = (
        expectancy is not None and base_expectancy is not None
        and expectancy >= base_expectancy - criteria.band_expectancy_tolerance
    )
    band_not_worse_pf = (
        profit_factor is not None and base_pf is not None
        and profit_factor >= base_pf - criteria.band_profit_factor_tolerance
    )
    band_not_worse_than_base = band_not_worse_expectancy and band_not_worse_pf

    not_single_symbol_dependent = band_metrics["concentration"].get("profitable_excluding_best_symbol")
    single_symbol_ok = not_single_symbol_dependent is not False

    disc_exp = reference["discovery"]["expectancy_per_trade"]
    oos_exp = reference["out_of_sample"]["expectancy_per_trade"]
    consistent_with_prior_periods = disc_exp > 0 and oos_exp > 0 and positive_expectancy

    checks = {
        "sufficient_sample": True,
        "positive_expectancy_third_period": positive_expectancy,
        "profit_factor_above_1_third_period": pf_above_1,
        "band_not_materially_worse_than_base": band_not_worse_than_base,
        "not_dependent_on_one_symbol": single_symbol_ok,
        "direction_consistent_with_prior_periods": consistent_with_prior_periods,
    }

    if not positive_expectancy or not pf_above_1:
        verdict = "FAILED"
    elif all(checks.values()):
        verdict = "VALIDATED"
    else:
        verdict = "MIXED"

    return {
        "verdict": verdict,
        "checks": checks,
        "values": {
            "expectancy_per_trade": tq["expectancy_per_trade"], "profit_factor": tq["profit_factor"],
            "base_expectancy_per_trade": base_tq["expectancy_per_trade"], "base_profit_factor": base_tq["profit_factor"],
            "closed_trades": closed,
        },
        "criteria": {
            "minimum_closed_trades": criteria.minimum_closed_trades,
            "band_expectancy_tolerance": criteria.band_expectancy_tolerance,
            "band_profit_factor_tolerance": criteria.band_profit_factor_tolerance,
        },
    }


def final_decision(*, classification_by_label: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Goal item "FINAL DECISION": exactly one of
    BAND_VALIDATED_FOR_PARAMETER_REVIEW / BAND_NEEDS_MORE_VALIDATION /
    BAND_NOT_VALIDATED, over both configs (B, C). Never authorizes a
    production change."""
    verdicts = {label: classification_by_label[label]["verdict"] for label in ("B", "C")}

    if all(v == "VALIDATED" for v in verdicts.values()):
        decision = "BAND_VALIDATED_FOR_PARAMETER_REVIEW"
        rationale = (
            "Both B and C classified VALIDATED on the third, independent period: positive "
            "expectancy and profit_factor>1 with sufficient sample, BAND not materially worse "
            "than BASE, not single-symbol dependent, and direction consistent with both prior "
            "periods. This authorizes only a SEPARATE production-change review, not a production "
            "change here."
        )
        recommend_capital_path_experiment = True
    elif any(v == "FAILED" for v in verdicts.values()) and not any(v == "VALIDATED" for v in verdicts.values()):
        decision = "BAND_NOT_VALIDATED"
        rationale = f"B={verdicts['B']}, C={verdicts['C']}: at least one config FAILED and neither reached VALIDATED."
        recommend_capital_path_experiment = False
    else:
        decision = "BAND_NEEDS_MORE_VALIDATION"
        rationale = f"B={verdicts['B']}, C={verdicts['C']}: evidence is mixed -- neither a clean pass nor a clean failure for both configs."
        recommend_capital_path_experiment = False

    return {
        "decision": decision,
        "rationale": rationale,
        "configuration_verdicts": verdicts,
        "authorizes_production_change": False,
        "recommend_30k_capital_path_experiment_next": recommend_capital_path_experiment,
    }
