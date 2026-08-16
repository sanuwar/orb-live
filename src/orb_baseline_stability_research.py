"""Research tooling for the ORB C_BASE baseline stability decomposition
study (Issue #319).

Issue #317/PR #318 found the proposed RVOL 1.50x-1.99x band did not
generalize to a third, independent historical period. Before considering
any further strategy-rule change, this module answers a prior question:
does the underlying C_BASE configuration (gap>=0.50%/RVOL>=1.50x,
otherwise canonical `orb_long_v0`) show a reasonably persistent edge across
the three already-established periods, and is period-to-period
deterioration primarily a time/market-regime effect or is it materially
explained by different liquidity-qualified universes across periods?

Reuse, not reinterpretation: candidate selection and trade-quality
simulation reuse the identical unmodified pipeline every prior study in
this line used -- `evaluate_combo`/`precompute_inputs_table`
(`src.orb_gap_rvol_sensitivity_research`), `build_candidate_shadow_decision`/
`simulate_shadow_execution` for outcomes, and `select_first_candidate_per_day`
(`src.orb_out_of_sample_validation_research`) for the deterministic
one-trade-per-day view. `C_BASE`/`B_BASE` are the identical `ParameterCombo`
objects Issue #317's own module already defined (`CONFIG_C`/`CONFIG_B`
there), not redefined here.

Rows are built as `src.orb_regime_diagnostic_research.TradeDiagnosticRow`
instances (decision-time features / outcome fields, disjoint by
construction) so this module can reuse `regime_summary`/`symbol_summary`/
`trade_quality_from_rows`/`walk_forward_quarters`/`month_summary`
unchanged for quarterly and monthly aggregation -- the only addition here
is `maximum_favorable_excursion`/`maximum_adverse_excursion` (already
computed, unmodified, by the pinned `simulate_shadow_execution` and simply
read here, never recomputed) in the outcome dict, needed for the
entry-vs-exit-edge (MFE) diagnostic.

Anti-cherry-picking: the common-cohort universe (`common_cohort`) is
formed strictly from each period's own `SymbolLiquidityProfile.eligible`
flag -- a data-quality/liquidity determination made without reference to
any trade outcome -- intersected across all three periods. It is never
filtered, reordered, or adjusted by realized P&L.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import mean, median
from typing import Any, Mapping, Sequence

from src.orb_gap_rvol_sensitivity_research import (
    CandidateSignal,
    ParameterCombo,
    build_candidate_shadow_decision,
    sensitivity_config,
)
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import (
    TradeDiagnosticRow,
    month_summary,
    regime_summary,
    symbol_summary,
    trade_quality_from_rows,
    walk_forward_quarters,
)
from src.orb_rvol_band_third_period_research import CONFIG_B, CONFIG_C
from src.orb_universe_expansion_research import SymbolLiquidityProfile
from src.shadow_execution_simulator import ShadowExecutionResult, simulate_shadow_execution

__all__ = [
    "BaselineStabilityError",
    "C_BASE",
    "B_BASE",
    "STUDY_CONFIGS",
    "PERIODS",
    "MIN_COHORT_SIZE",
    "MIN_QUARTER_CLOSED_TRADES",
    "common_cohort",
    "build_stability_rows",
    "cell_summary",
    "quarterly_stability",
    "combined_view",
    "mfe_analysis",
    "MFE_THRESHOLDS_R",
    "MEANINGFUL_MFE_THRESHOLD_R",
    "MEANINGFUL_MFE_FRACTION",
    "meaningful_mfe_fraction",
    "excluding_symbol",
    "best_contributing_symbol",
    "StabilityCriteria",
    "DEFAULT_STABILITY_CRITERIA",
    "classify_stability",
    "recommend_next_experiment",
]


class BaselineStabilityError(RuntimeError):
    """Refused: baseline stability decomposition tooling could not safely
    proceed."""


# ---------------------------------------------------------------------------
# The primary and comparator configurations. Reused unchanged from Issue
# #317's own module -- CONFIG_C (0.50%/1.50x) is this study's C_BASE,
# CONFIG_B (0.75%/1.50x) is the optional B_BASE comparator.
# ---------------------------------------------------------------------------

C_BASE: ParameterCombo = CONFIG_C
B_BASE: ParameterCombo = CONFIG_B
STUDY_CONFIGS: Mapping[str, ParameterCombo] = {"C_BASE": C_BASE, "B_BASE": B_BASE}

#: The three already-established periods, fixed, no boundary changes.
PERIODS: tuple[str, ...] = ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")

#: Below this many symbols, the common-cohort view is classified
#: INSUFFICIENT_SAMPLE rather than interpreted -- predeclared before
#: computing the actual intersection.
MIN_COHORT_SIZE = 3

#: Predeclared before computing any quarter's result. Quarters are much
#: shorter (~63 sessions) than the whole-period 15-closed-trade bar used
#: elsewhere in this research line (Issue #310/#315), so a
#: proportionally smaller floor is used here; quarters below it are
#: reported but marked inadequate, never dropped silently.
MIN_QUARTER_CLOSED_TRADES = 8


def common_cohort(profiles_by_period: Mapping[str, Mapping[str, SymbolLiquidityProfile]]) -> tuple[str, ...]:
    """The fixed diagnostic cohort: every symbol whose
    `SymbolLiquidityProfile.eligible` is True in EVERY period in
    `profiles_by_period` -- eligibility/data-quality only, never trade
    outcome. Sorted alphabetically (not by any period's own volume
    ranking, which would privilege one period's ordering over the
    others) for a single deterministic, period-agnostic order."""
    period_labels = tuple(profiles_by_period)
    if not period_labels:
        return ()
    eligible_sets = [
        {symbol for symbol, profile in profiles.items() if profile.eligible}
        for profiles in profiles_by_period.values()
    ]
    intersection = set.intersection(*eligible_sets) if eligible_sets else set()
    return tuple(sorted(intersection))


# ---------------------------------------------------------------------------
# Row construction. Mirrors `orb_regime_diagnostic_research.build_diagnostic_rows`'s
# per-candidate replay exactly (identical decision/simulation construction),
# but keeps `maximum_favorable_excursion`/`maximum_adverse_excursion` in the
# outcome dict (needed here, not carried by that module's own row shape) and
# omits the regime-bucket decision-time features this study does not use.
# ---------------------------------------------------------------------------


def _session_bars(bars: Sequence, *, session_date: date) -> list:
    return sorted((b for b in bars if b.trading_date == session_date), key=lambda b: b.event_timestamp)


def _outcome_fields(result: ShadowExecutionResult) -> dict[str, Any]:
    entered = result.entry_price is not None
    closed = result.realized_pnl is not None
    risk = (result.entry_price - result.initial_stop_price) if (entered and result.initial_stop_price is not None) else None
    r_multiple = float(result.realized_pnl / risk) if (closed and risk and risk > 0) else None
    mfe_r = float(result.maximum_favorable_excursion / risk) if (
        entered and risk and risk > 0 and result.maximum_favorable_excursion is not None
    ) else None
    mae_r = float(result.maximum_adverse_excursion / risk) if (
        entered and risk and risk > 0 and result.maximum_adverse_excursion is not None
    ) else None
    return {
        "state": result.state.value, "entered": entered, "closed": closed,
        "entry_price": str(result.entry_price) if result.entry_price is not None else None,
        "initial_stop_price": str(result.initial_stop_price) if result.initial_stop_price is not None else None,
        "exit_price": str(result.exit_price) if result.exit_price is not None else None,
        "exit_reason": result.exit_reason,
        "realized_pnl": str(result.realized_pnl) if result.realized_pnl is not None else None,
        "r_multiple": r_multiple,
        "is_winner": (result.realized_pnl > 0) if closed else None,
        "is_stopped": result.state.value == "STOPPED",
        "initial_risk_r": str(risk) if risk is not None else None,
        "maximum_favorable_excursion": (
            str(result.maximum_favorable_excursion) if result.maximum_favorable_excursion is not None else None
        ),
        "maximum_adverse_excursion": (
            str(result.maximum_adverse_excursion) if result.maximum_adverse_excursion is not None else None
        ),
        "mfe_r": mfe_r, "mae_r": mae_r,
    }


def build_stability_rows(
    *,
    period: str,
    view: str,
    config_label: str,
    combo: ParameterCombo,
    candidate_signals: Sequence[CandidateSignal],
    market_bars_by_symbol: Mapping[str, Sequence],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
    session_bounds_provider=None,
) -> tuple[TradeDiagnosticRow, ...]:
    """One `TradeDiagnosticRow` per candidate signal, replayed through the
    unmodified `simulate_shadow_execution`. `period`/`view`/`config` are
    carried as decision-time fields (all knowable before any outcome) so
    rows from every period/view/config can be freely concatenated and
    grouped by `regime_summary`/`month_summary`/`walk_forward_quarters`
    (imported unchanged from `orb_regime_diagnostic_research`)."""
    combo_config = sensitivity_config(base_config, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    shadow_run_id = f"baseline-stability-{period}-{view}-{config_label}"
    spy_bars = market_bars_by_symbol["SPY"]

    rows: list[TradeDiagnosticRow] = []
    for symbol, session_date, opening_data, live_data, decision in candidate_signals:
        candidate_bars = market_bars_by_symbol[symbol]
        shadow_decision = build_candidate_shadow_decision(
            symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
            decision=decision, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
        )
        session_bars = _session_bars(candidate_bars, session_date=session_date)
        subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
        kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
        result = simulate_shadow_execution(shadow_decision, subsequent_bars, **kwargs)

        decision_features = {
            "period": period, "view": view, "config": config_label, "symbol": symbol,
            "session_date": session_date.isoformat(), "month": session_date.strftime("%Y-%m"),
            "quarter": f"{session_date.year}-Q{(session_date.month - 1) // 3 + 1}",
        }
        rows.append(TradeDiagnosticRow(decision=decision_features, outcome=_outcome_fields(result)))
    return tuple(rows)


# ---------------------------------------------------------------------------
# Per-cell summary (period x config x view).
# ---------------------------------------------------------------------------


def cell_summary(
    *,
    rows: Sequence[TradeDiagnosticRow],
    universe: Sequence[str],
    total_sessions: int,
    coverage_pct: float,
    coverage_status: str,
) -> dict[str, Any]:
    """Full per-cell metric set. `rows` are ALL-CANDIDATES rows for this
    (period, config, view) cell; the caller computes a separate
    ONE_TRADE_PER_DAY cell by building rows from
    `select_first_candidate_per_day(candidate_signals)` instead and calling
    this same function again -- `select_first_candidate_per_day` needs the
    original `CandidateSignal` tuples (with `LiveData.timestamp`), which a
    `TradeDiagnosticRow` does not retain, so that selection always happens
    before row-building, never after."""
    overview = trade_quality_from_rows(rows)
    candidate_days = len({r.decision["session_date"] for r in rows})
    return {
        "universe": list(universe), "universe_size": len(universe),
        "sessions": total_sessions, "coverage_pct": coverage_pct, "coverage_status": coverage_status,
        "candidate_days": candidate_days,
        "candidate_day_rate": (candidate_days / total_sessions) if total_sessions else 0.0,
        "candidate_occurrences": len(rows),
        "trade_quality": overview,
        "monthly": month_summary(rows),
        "symbol_contribution": symbol_summary(rows),
    }


# ---------------------------------------------------------------------------
# Quarterly rolling stability.
# ---------------------------------------------------------------------------


def quarterly_stability(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Goal item "ROLLING STABILITY": fixed calendar-quarter buckets (no
    optimized boundaries -- the `quarter` field is a plain `YYYY-Qn` of the
    trade's own `session_date`), each marked adequate/inadequate against
    `MIN_QUARTER_CLOSED_TRADES`, plus the requested summary fractions."""
    by_quarter = walk_forward_quarters(rows)
    quarters: dict[str, dict[str, Any]] = {}
    for quarter, stats in sorted(by_quarter.items()):
        adequate = stats["closed_trades"] >= MIN_QUARTER_CLOSED_TRADES
        quarters[quarter] = {**stats, "adequate_sample": adequate}

    adequate_quarters = [q for q, s in quarters.items() if s["adequate_sample"]]
    positive_expectancy = [q for q in adequate_quarters if (quarters[q]["expectancy_per_trade"] or 0) > 0]
    pf_above_1 = [q for q in adequate_quarters if (quarters[q]["profit_factor"] or 0) > 1]

    best_quarter = max(adequate_quarters, key=lambda q: quarters[q]["expectancy_per_trade"] or float("-inf"), default=None)
    worst_quarter = min(adequate_quarters, key=lambda q: quarters[q]["expectancy_per_trade"] or float("inf"), default=None)

    longest_negative_streak = 0
    current_streak = 0
    for quarter in sorted(adequate_quarters):
        if (quarters[quarter]["expectancy_per_trade"] or 0) <= 0:
            current_streak += 1
            longest_negative_streak = max(longest_negative_streak, current_streak)
        else:
            current_streak = 0

    return {
        "quarters": quarters,
        "adequate_quarter_count": len(adequate_quarters),
        "inadequate_quarter_count": len(quarters) - len(adequate_quarters),
        "fraction_adequate_quarters_positive_expectancy": (
            len(positive_expectancy) / len(adequate_quarters) if adequate_quarters else None
        ),
        "fraction_adequate_quarters_pf_above_1": (
            len(pf_above_1) / len(adequate_quarters) if adequate_quarters else None
        ),
        "best_quarter": best_quarter,
        "worst_quarter": worst_quarter,
        "longest_negative_expectancy_streak_quarters": longest_negative_streak,
        "minimum_closed_trades_for_adequate_sample": MIN_QUARTER_CLOSED_TRADES,
    }


# ---------------------------------------------------------------------------
# Combined full-history descriptive view and symbol-exclusion sensitivity.
# ---------------------------------------------------------------------------


def combined_view(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Goal item "COMBINED FULL-HISTORY VIEW": ALL_CANDIDATES vs
    ONE_TRADE_PER_DAY over `rows` (already chronologically concatenated
    across periods by the caller). ONE_TRADE_PER_DAY here is computed
    directly from rows (first row per `session_date` in file/insertion
    order, which the caller guarantees is chronological by construction --
    the original `select_first_candidate_per_day` decision-timestamp rule
    cannot be replayed from rows alone since they do not carry `LiveData`,
    so the caller passes already-ordered, already-first-per-day rows when
    it wants this view)."""
    return {"ALL_CANDIDATES": trade_quality_from_rows(rows)}


def excluding_symbol(rows: Sequence[TradeDiagnosticRow], *, symbol: str) -> dict[str, Any]:
    """Sensitivity view only -- never used to select or filter a
    deployable universe. Reports trade quality with all trades for
    `symbol` removed."""
    remaining = [r for r in rows if r.decision["symbol"] != symbol]
    return trade_quality_from_rows(remaining)


def best_contributing_symbol(rows: Sequence[TradeDiagnosticRow]) -> str | None:
    """`symbol_summary` already ranks contributors internally (P&L
    descending); reuse its own `top_profitable_contributors` list rather
    than re-deriving the ranking here."""
    top = symbol_summary(rows).get("top_profitable_contributors") or []
    return top[0]["symbol"] if top else None


# ---------------------------------------------------------------------------
# Entry-vs-exit-edge (MFE) diagnostic. Descriptive only -- never changes the
# simulator or implements a trailing stop.
# ---------------------------------------------------------------------------

#: Fixed R thresholds this diagnostic checks losing trades against --
#: predeclared, not derived from the data.
MFE_THRESHOLDS_R: tuple[Decimal, ...] = (Decimal("0.25"), Decimal("0.50"), Decimal("0.75"), Decimal("1.00"))

#: The single threshold/fraction pair `recommend_next_experiment` checks
#: against, predeclared before any MFE number was computed: "a meaningful
#: fraction of eventual losses show substantial positive MFE before
#: reversing" (goal document, PROFIT_PROTECTION_EXIT_EXPERIMENT rule) means
#: at least 30% of losers reached +0.50R at some point before closing as a
#: loss.
MEANINGFUL_MFE_THRESHOLD_R = Decimal("0.50")
MEANINGFUL_MFE_FRACTION = 0.30


def meaningful_mfe_fraction(mfe_result: Mapping[str, Any]) -> bool:
    entry = mfe_result["losers_reaching_threshold_before_reversing"].get(f"+{MEANINGFUL_MFE_THRESHOLD_R}R")
    fraction = entry.get("fraction") if entry else None
    return fraction is not None and fraction >= MEANINGFUL_MFE_FRACTION


def mfe_analysis(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """For CLOSED trades only: MFE/realized-R distributions for winners,
    and -- for eventual losers -- the fraction whose `mfe_r` (maximum
    favorable excursion this trade reached at any point while open,
    normalized to its own initial risk R, already computed by the
    unmodified simulator) reached each of `MFE_THRESHOLDS_R` before the
    trade ultimately closed as a loss."""
    closed = [r for r in rows if r.outcome["closed"]]
    winners = [r for r in closed if r.outcome["is_winner"]]
    losers = [r for r in closed if not r.outcome["is_winner"]]

    def _mfe_r_values(subset: Sequence[TradeDiagnosticRow]) -> list[float]:
        return [r.outcome["mfe_r"] for r in subset if r.outcome["mfe_r"] is not None]

    def _r_multiples(subset: Sequence[TradeDiagnosticRow]) -> list[float]:
        return [r.outcome["r_multiple"] for r in subset if r.outcome["r_multiple"] is not None]

    winner_mfe = _mfe_r_values(winners)
    loser_mfe = _mfe_r_values(losers)
    winner_r = _r_multiples(winners)

    loser_reached: dict[str, dict[str, Any]] = {}
    for threshold in MFE_THRESHOLDS_R:
        threshold_f = float(threshold)
        reached = [r for r in losers if r.outcome["mfe_r"] is not None and r.outcome["mfe_r"] >= threshold_f]
        loser_reached[f"+{threshold}R"] = {
            "losers_with_mfe_data": len(loser_mfe),
            "losers_reaching_threshold": len(reached),
            "fraction": (len(reached) / len(loser_mfe)) if loser_mfe else None,
        }

    def _distribution(values: Sequence[float]) -> dict[str, Any]:
        if not values:
            return {"count": 0, "mean": None, "median": None, "min": None, "max": None}
        return {
            "count": len(values), "mean": mean(values), "median": median(values),
            "min": min(values), "max": max(values),
        }

    return {
        "closed_trades": len(closed), "winners": len(winners), "losers": len(losers),
        "winner_mfe_r_distribution": _distribution(winner_mfe),
        "winner_realized_r_distribution": _distribution(winner_r),
        "loser_mfe_r_distribution": _distribution(loser_mfe),
        "losers_reaching_threshold_before_reversing": loser_reached,
    }


# ---------------------------------------------------------------------------
# Predeclared stability classification (goal item "PREDECLARED
# INTERPRETATION"). Fixed here before any period's result is interpreted.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StabilityCriteria:
    minimum_closed_trades_per_period: int = 15
    material_expectancy_threshold: float = 0.1


DEFAULT_STABILITY_CRITERIA = StabilityCriteria()


def classify_stability(
    *,
    native_trade_quality_by_period: Mapping[str, Mapping[str, Any]],
    cohort_trade_quality_by_period: Mapping[str, Mapping[str, Any]],
    single_symbol_dependence_by_period: Mapping[str, bool],
    criteria: StabilityCriteria = DEFAULT_STABILITY_CRITERIA,
) -> dict[str, Any]:
    """STABLE_EDGE / REGIME_DEPENDENT_EDGE / WEAK_OR_NO_EDGE /
    INSUFFICIENT_EVIDENCE for C_BASE, over the three native-view period
    results, per the goal document's own guidance:

    INSUFFICIENT_EVIDENCE if any period has fewer than
    `minimum_closed_trades_per_period` closed trades.

    STABLE_EDGE if every period has positive expectancy and profit_factor>1,
    with no period single-symbol dependent.

    Otherwise REGIME_DEPENDENT_EDGE if at least one period is meaningfully
    positive (expectancy exceeds `material_expectancy_threshold`) and at
    least one is weak/negative, AND that spread survives common-cohort
    normalization (the same qualitative pattern holds in the cohort view --
    not merely explained by different universes).

    Otherwise WEAK_OR_NO_EDGE."""

    def _pf(raw: Any) -> float | None:
        if raw in (None, "INFINITE_NO_LOSSES"):
            return None
        return float(raw)

    insufficient = [
        p for p in native_trade_quality_by_period
        if (native_trade_quality_by_period[p].get("closed_trades") or 0) < criteria.minimum_closed_trades_per_period
    ]
    if insufficient:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": f"periods below {criteria.minimum_closed_trades_per_period} closed trades: {insufficient}",
        }

    expectancy_by_period = {
        p: tq.get("expectancy_per_trade") for p, tq in native_trade_quality_by_period.items()
    }
    pf_by_period = {p: _pf(tq.get("profit_factor")) for p, tq in native_trade_quality_by_period.items()}

    all_positive = all((e or 0) > 0 for e in expectancy_by_period.values())
    all_pf_above_1 = all((pf or 0) > 1 for pf in pf_by_period.values())
    no_single_symbol_dependence = not any(single_symbol_dependence_by_period.values())

    if all_positive and all_pf_above_1 and no_single_symbol_dependence:
        return {
            "verdict": "STABLE_EDGE",
            "expectancy_by_period": expectancy_by_period, "profit_factor_by_period": pf_by_period,
            "single_symbol_dependence_by_period": dict(single_symbol_dependence_by_period),
        }

    material_positive = [p for p, e in expectancy_by_period.items() if (e or 0) > criteria.material_expectancy_threshold]
    weak_or_negative = [p for p, e in expectancy_by_period.items() if (e or 0) <= criteria.material_expectancy_threshold]

    if material_positive and weak_or_negative:
        cohort_expectancy_by_period = {
            p: tq.get("expectancy_per_trade") for p, tq in cohort_trade_quality_by_period.items()
        }
        cohort_material_positive = [
            p for p in material_positive
            if p in cohort_expectancy_by_period and (cohort_expectancy_by_period[p] or 0) > criteria.material_expectancy_threshold
        ]
        cohort_weak_or_negative = [
            p for p in weak_or_negative
            if p in cohort_expectancy_by_period and (cohort_expectancy_by_period[p] or 0) <= criteria.material_expectancy_threshold
        ]
        spread_survives_cohort = bool(cohort_material_positive) and bool(cohort_weak_or_negative)
        return {
            "verdict": "REGIME_DEPENDENT_EDGE" if spread_survives_cohort else "WEAK_OR_NO_EDGE",
            "expectancy_by_period": expectancy_by_period, "profit_factor_by_period": pf_by_period,
            "material_positive_periods": material_positive, "weak_or_negative_periods": weak_or_negative,
            "cohort_expectancy_by_period": cohort_expectancy_by_period,
            "spread_survives_cohort_normalization": spread_survives_cohort,
            "single_symbol_dependence_by_period": dict(single_symbol_dependence_by_period),
        }

    return {
        "verdict": "WEAK_OR_NO_EDGE",
        "expectancy_by_period": expectancy_by_period, "profit_factor_by_period": pf_by_period,
        "single_symbol_dependence_by_period": dict(single_symbol_dependence_by_period),
    }


def recommend_next_experiment(
    *, stability_verdict: str, meaningful_mfe_fraction: bool,
) -> dict[str, Any]:
    """Goal item "NEXT-EXPERIMENT DECISION": exactly one of
    PROFIT_PROTECTION_EXIT_EXPERIMENT / CAPITAL_PATH_EXPERIMENT /
    MORE_BASELINE_VALIDATION / PAUSE_STRATEGY_REFINEMENT, per the fixed
    rules the goal document states."""
    if stability_verdict == "STABLE_EDGE" and meaningful_mfe_fraction:
        recommendation = "PROFIT_PROTECTION_EXIT_EXPERIMENT"
        rationale = (
            "C_BASE retains a stable edge across all three periods and a meaningful fraction of "
            "eventual losses showed substantial positive MFE before reversing -- exit refinement "
            "is justified as the next experiment."
        )
    elif stability_verdict == "STABLE_EDGE":
        recommendation = "CAPITAL_PATH_EXPERIMENT"
        rationale = "C_BASE appears sufficiently stable for account-path modeling; MFE evidence did not clear the bar for prioritizing exit refinement first."
    elif stability_verdict == "REGIME_DEPENDENT_EDGE":
        recommendation = "MORE_BASELINE_VALIDATION"
        rationale = "Evidence is promising in some periods but historical stability is not yet resolved -- validate further before committing to either exit refinement or capital-path modeling."
    elif stability_verdict == "INSUFFICIENT_EVIDENCE":
        recommendation = "MORE_BASELINE_VALIDATION"
        rationale = "Sample size in at least one period is too small for a defensible stability conclusion."
    else:
        recommendation = "PAUSE_STRATEGY_REFINEMENT"
        rationale = "The underlying edge itself is too weak or inconsistent across periods to justify exit tuning or capital-path modeling."

    return {"recommendation": recommendation, "rationale": rationale, "authorizes_production_change": False}
