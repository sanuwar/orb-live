"""Research tooling for the existing-configuration R-normalized
re-evaluation study (Issue #325).

PR #322 (Issue #321) found that legacy "expectancy"/"profit factor" in
this research line were price-per-share, not risk-normalized, and
introduced `expectancy_r_per_trade`/`profit_factor_r` as the correct
basis for any fixed-risk decision. This module re-evaluates every
gap/RVOL configuration ALREADY tested historically (Issue #300's gap x
RVOL sensitivity grid and Issue #302's finer RVOL grid -- the union is
`FROZEN_CONFIG_SET` below) across the four already-established
independent periods, using R-normalized metrics as primary. This is
re-ranking of existing configurations, not a new parameter search: no
threshold in `FROZEN_CONFIG_SET` was invented for this study, and no
entry/exit/stop/target/SPY/timing rule is changed.

Reuse, not reinterpretation: candidate generation and simulation reuse
the identical unmodified pipeline every prior study in this line used
(`evaluate_combo`/`precompute_inputs_table` from
`orb_gap_rvol_sensitivity_research`, `build_stability_rows` from
`orb_baseline_stability_research`, `select_first_candidate_per_day` from
`orb_out_of_sample_validation_research`). Each period's own
already-established liquidity-qualified universe (from Issues
#304/#310/#317/#319/#321) is reused unchanged here -- never recomputed --
so this study only needs to fetch/precompute bars for that period's own
(already-known) native-universe symbols, not the full ~100-symbol pool,
and needs exactly one precompute pass per period regardless of how many
of the 18 configurations are evaluated against it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.orb_baseline_fourth_period_research import max_drawdown_and_streak_from_rows, symbol_total_r
from src.orb_baseline_stability_research import TradeDiagnosticRow, excluding_symbol
from src.orb_gap_rvol_sensitivity_research import ParameterCombo
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows

__all__ = [
    "ExistingConfigsReevaluationError",
    "FROZEN_CONFIG_SET",
    "PERIODS",
    "PERIOD_NATIVE_UNIVERSES",
    "PERIOD_SESSION_SPEC",
    "best_contributing_symbol_by_r",
    "combined_trade_quality",
    "SelectionCriteria",
    "DEFAULT_SELECTION_CRITERIA",
    "classify_configuration",
    "recommend_next_milestone",
]


class ExistingConfigsReevaluationError(RuntimeError):
    """Refused: existing-configuration R-normalized re-evaluation tooling
    could not safely proceed."""


# ---------------------------------------------------------------------------
# Frozen configuration set: the exact union of Issue #300's 12-combination
# grid (gap in {1.00%,0.75%,0.50%} x rvol in {3.00x,2.50x,2.00x,1.50x}) and
# Issue #302's finer 12-combination grid (same gaps x rvol in
# {2.00x,1.75x,1.50x,1.25x}) -- 18 unique combinations after removing the
# six shared points (each gap's own rvol=2.00x/1.50x appear in both grids).
# Every entry is traceable to a specific prior study; nothing here is
# interpolated or newly introduced. Labels use explicit GAP_xxx_RVOL_yyy
# naming (never bare "B"/"C", which meant different things in different
# studies) with an explicit mapping back to every prior name.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConfigEntry:
    combo: ParameterCombo
    source_studies: tuple[str, ...]
    prior_names: tuple[str, ...]


def _combo(gap: str, rvol: str) -> ParameterCombo:
    return ParameterCombo(gap_percent=Decimal(gap), rvol_multiple=Decimal(rvol))


FROZEN_CONFIG_SET: Mapping[str, ConfigEntry] = {
    "GAP_100_RVOL_300": ConfigEntry(_combo("1.00", "3.0"), ("Issue #300",), ("#300 baseline/A", "#304 CONFIG_A_BASELINE", "#310 BASELINE_CONFIG/CONTROL")),
    "GAP_100_RVOL_250": ConfigEntry(_combo("1.00", "2.5"), ("Issue #300",), ("#300 grid point",)),
    "GAP_100_RVOL_200": ConfigEntry(_combo("1.00", "2.00"), ("Issue #300", "Issue #302"), ("#300 grid point", "#302 grid point")),
    "GAP_100_RVOL_175": ConfigEntry(_combo("1.00", "1.75"), ("Issue #302",), ("#302 grid point",)),
    "GAP_100_RVOL_150": ConfigEntry(_combo("1.00", "1.5"), ("Issue #300", "Issue #302"), ("#300 grid point", "#302 grid point")),
    "GAP_100_RVOL_125": ConfigEntry(_combo("1.00", "1.25"), ("Issue #302",), ("#302 grid point",)),
    "GAP_075_RVOL_300": ConfigEntry(_combo("0.75", "3.0"), ("Issue #300",), ("#300 grid point",)),
    "GAP_075_RVOL_250": ConfigEntry(_combo("0.75", "2.5"), ("Issue #300",), ("#300 grid point",)),
    "GAP_075_RVOL_200": ConfigEntry(_combo("0.75", "2.00"), ("Issue #300", "Issue #302"), ("#300 grid point", "#302 grid point")),
    "GAP_075_RVOL_175": ConfigEntry(_combo("0.75", "1.75"), ("Issue #302",), ("#302 grid point",)),
    "GAP_075_RVOL_150": ConfigEntry(
        _combo("0.75", "1.5"), ("Issue #300", "Issue #302"),
        ("#300 grid point", "#302 grid point", "#304 CONFIG_B_CONSERVATIVE_RELAXED", "#310 PRIMARY_CONFIG_C",
         "#317 CONFIG_B", "#319 B_BASE", "#321 comparator B_BASE"),
    ),
    "GAP_075_RVOL_125": ConfigEntry(_combo("0.75", "1.25"), ("Issue #302",), ("#302 grid point", "#304 CONFIG_D_HIGHER_GAP_LOWER_RVOL")),
    "GAP_050_RVOL_300": ConfigEntry(_combo("0.50", "3.0"), ("Issue #300",), ("#300 grid point",)),
    "GAP_050_RVOL_250": ConfigEntry(_combo("0.50", "2.5"), ("Issue #300",), ("#300 grid point",)),
    "GAP_050_RVOL_200": ConfigEntry(_combo("0.50", "2.00"), ("Issue #300", "Issue #302"), ("#300 grid point", "#302 grid point")),
    "GAP_050_RVOL_175": ConfigEntry(_combo("0.50", "1.75"), ("Issue #302",), ("#302 grid point",)),
    "GAP_050_RVOL_150": ConfigEntry(
        _combo("0.50", "1.5"), ("Issue #300", "Issue #302"),
        ("#300 grid point", "#302 grid point", "#304 CONFIG_C_FINE_STUDY_RUNNER_UP", "#310 PRIMARY_CONFIG_B",
         "#317 CONFIG_C", "#319 C_BASE", "#321 P0_CONFIG"),
    ),
    "GAP_050_RVOL_125": ConfigEntry(
        _combo("0.50", "1.25"), ("Issue #302",),
        ("#302 grid point", "#304 CONFIG_E_RESEARCH_LEADER", "#310 PRIMARY_CONFIG_A"),
    ),
}

assert len(FROZEN_CONFIG_SET) == 18

#: The four already-established independent periods, fixed, no boundary
#: changes -- (reference_day, session_count, uses_research_calendar).
PERIOD_SESSION_SPEC: Mapping[str, tuple[date, int, bool]] = {
    "P0": (date(2024, 4, 3), 252, True),
    "P1": (date(2025, 4, 3), 252, True),
    "P2": (date(2026, 4, 7), 252, True),
    "P3": (date(2026, 8, 14), 90, False),
}
PERIODS: tuple[str, ...] = ("P0", "P1", "P2", "P3")

#: Each period's own already-established liquidity-qualified native
#: universe (Issues #321/#319/#317/#304-#310 respectively), reused
#: unchanged -- never recomputed by this study.
PERIOD_NATIVE_UNIVERSES: Mapping[str, tuple[str, ...]] = {
    "P0": ("NVDA", "AAPL", "MSFT", "TSLA", "AMZN", "GOOGL", "AMD"),
    "P1": ("NVDA", "AAPL", "TSLA", "AMZN", "MSFT", "GOOGL", "AMD", "BAC", "INTC"),
    "P2": ("NVDA", "TSLA", "AAPL", "AMZN", "MSFT", "GOOGL", "META", "AVGO", "AMD", "INTC", "BAC", "WMT", "CSCO", "KO", "NKE", "VZ", "T"),
    "P3": ("NVDA", "AMZN", "AAPL", "MSFT", "GOOGL", "META", "TSLA", "INTC", "AMD", "AVGO", "NFLX", "BAC", "V", "CSCO", "NOW", "WMT", "CRM", "KO", "JPM", "VZ", "BSX", "NKE", "SLB"),
}


def best_contributing_symbol_by_r(rows: Sequence[TradeDiagnosticRow]) -> str | None:
    """The largest R contributor among `rows`' own symbols -- R-based
    (Issue #322's own correction), NOT `orb_regime_diagnostic_research.
    symbol_summary`'s own dollar-P&L-ranked `top_profitable_contributors`
    (which would silently reintroduce a price-based selection basis).
    Ranks by each symbol's own `cumulative_r` via `symbol_total_r`."""
    per_symbol = symbol_summary(rows)["per_symbol"]
    if not per_symbol:
        return None
    return max(per_symbol, key=lambda symbol: symbol_total_r(per_symbol[symbol]))


def combined_trade_quality(rows_by_period: Mapping[str, Sequence[TradeDiagnosticRow]]) -> dict[str, Any]:
    """Trade quality over ALL periods' real rows concatenated -- since
    this study computes genuine per-trade rows for every period (unlike
    Issue #321's reuse-from-aggregates design), the combined figure is
    exact `trade_quality_from_rows` over the concatenation, not an
    algebraic reconstruction."""
    combined: list[TradeDiagnosticRow] = []
    for rows in rows_by_period.values():
        combined.extend(rows)
    return trade_quality_from_rows(combined)


# ---------------------------------------------------------------------------
# Predeclared selection criteria, fixed before any period's result is read.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionCriteria:
    minimum_combined_one_per_day_closed_trades: int = MIN_SAMPLE_CLOSED_TRADES
    all_candidates_not_materially_negative_threshold: float = -0.05
    minimum_periods_non_negative_of_four: int = 3


DEFAULT_SELECTION_CRITERIA = SelectionCriteria()


def _expectancy_r(tq: Mapping[str, Any]) -> float | None:
    return tq.get("expectancy_r_per_trade")


def _pf_r(tq: Mapping[str, Any]) -> float | None:
    raw = tq.get("profit_factor_r")
    if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
        return float("inf")
    return raw


def classify_configuration(
    *,
    combined_one_per_day: Mapping[str, Any],
    combined_all_candidates: Mapping[str, Any],
    period_one_per_day_expectancy_r: Mapping[str, float | None],
    one_per_day_excluding_best_symbol: Mapping[str, Any],
    criteria: SelectionCriteria = DEFAULT_SELECTION_CRITERIA,
) -> dict[str, Any]:
    """CORE_CANDIDATE / PROMISING_BUT_UNSTABLE / WEAK_OR_NO_EDGE /
    INSUFFICIENT_EVIDENCE, per the study's own predeclared rule -- fixed
    here before any configuration's results were read. **R-normalized
    metrics only**; price-per-share fields are never read by this
    function. The PRIMARY product view is ONE-TRADE-PER-DAY (ORB-LIVE V1's
    own bounded-daily-attempt product); ALL-CANDIDATES is a robustness
    diagnostic only and cannot promote a configuration ALL-CANDIDATES
    itself does not support with an adequate combined sample."""
    one_day_closed = combined_one_per_day.get("closed_trades") or 0
    if one_day_closed < criteria.minimum_combined_one_per_day_closed_trades:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"combined one-trade-per-day closed trades={one_day_closed} below minimum {criteria.minimum_combined_one_per_day_closed_trades}"}

    one_day_exp_r = _expectancy_r(combined_one_per_day)
    one_day_pf_r = _pf_r(combined_one_per_day)
    all_cand_exp_r = _expectancy_r(combined_all_candidates)
    excl_best_exp_r = _expectancy_r(one_per_day_excluding_best_symbol)

    non_negative_periods = sum(1 for e in period_one_per_day_expectancy_r.values() if (e or 0) >= 0)

    positive_edge = (one_day_exp_r or 0) > 0 and (one_day_pf_r or 0) > 1

    checks = {
        "one_per_day_expectancy_r_positive": (one_day_exp_r or 0) > 0,
        "one_per_day_pf_r_above_1": (one_day_pf_r or 0) > 1,
        "all_candidates_not_materially_negative": (
            all_cand_exp_r is not None and all_cand_exp_r >= criteria.all_candidates_not_materially_negative_threshold
        ),
        "not_single_symbol_dependent": (excl_best_exp_r or 0) > 0,
        "period_robustness_at_least_3_of_4": non_negative_periods >= criteria.minimum_periods_non_negative_of_four,
        "adequate_combined_sample": True,
    }

    if all(checks.values()):
        verdict = "CORE_CANDIDATE"
    elif positive_edge:
        verdict = "PROMISING_BUT_UNSTABLE"
    else:
        verdict = "WEAK_OR_NO_EDGE"

    return {
        "verdict": verdict, "checks": checks,
        "one_per_day_expectancy_r": one_day_exp_r, "one_per_day_profit_factor_r": one_day_pf_r,
        "all_candidates_expectancy_r": all_cand_exp_r,
        "one_per_day_excluding_best_symbol_expectancy_r": excl_best_exp_r,
        "non_negative_periods_of_4": non_negative_periods,
    }


def recommend_next_milestone(*, any_core_candidate: bool, any_promising_but_unstable: bool) -> dict[str, Any]:
    """Exactly one of VALIDATE_SINGLE_TRADE_CORE / CANDIDATE_SELECTION_RESEARCH /
    PAUSE_ENTRY_RESEARCH, per the study's own fixed rule."""
    if any_core_candidate:
        milestone = "VALIDATE_SINGLE_TRADE_CORE"
        rationale = "At least one existing configuration classified CORE_CANDIDATE under the predeclared robustness rule."
    elif any_promising_but_unstable:
        milestone = "CANDIDATE_SELECTION_RESEARCH"
        rationale = "No configuration is robust enough to be CORE_CANDIDATE, but at least one shows positive combined R edge with instability -- candidate quality/selection is a more credible next hypothesis than another threshold search."
    else:
        milestone = "PAUSE_ENTRY_RESEARCH"
        rationale = "No existing configuration shows a defensible R-normalized edge, robust or otherwise."
    return {
        "next_milestone": milestone, "rationale": rationale,
        "authorizes_production_change": False, "authorizes_new_threshold_search": False,
    }
