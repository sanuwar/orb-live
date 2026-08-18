"""Research tooling for M42: independent validation of the 09:50
first-candidate confirmation policy (Issue #332).

M41 found that `WAITED_FIRST_VALID_0950` -- identify the first candidate
that qualifies by 09:50 ET, wait until 09:50, re-run the real
`SignalEngine` on that SAME symbol using the realistic decision-time
price, enter only if it still qualifies -- was the strongest and most
robust of five candidate-selection/timing arms across P_MINUS_1/P0-P3.
Because those five periods were themselves used to *discover* that
finding, they cannot independently validate it. This module answers
exactly one question on one completely untouched period ("P_MINUS_2"):
does `WAITED_FIRST_VALID_0950` retain a defensible advantage over
`CURRENT_FIRST_VALID`?

Reuse, not reinterpretation: `build_both_policies_rows` below calls
`early_pool`, `enrich_with_decision_time`, and `build_wait_row` directly
from `src.orb_candidate_selection_timing_research` (M41, merged,
unmodified) -- the WAITED policy is never reimplemented here. A dedicated
test (`test_m42_waited_policy_matches_m41_implementation_on_identical_fixture`)
proves the two modules produce byte-identical decision/outcome fields
(modulo the `period`/`shadow_run_id` labels, which are expected to
differ) on the same synthetic candidates.

No new gap/RVOL threshold (`GAP_050_RVOL_175` only, the exact Issue #325
CHALLENGER), no candidate ranking, no fallback candidate, no second
trade, no new timing variant (only 09:50, nothing else), no
exit/stop/target change.

P_MINUS_2 is 252 research-calendar trading sessions strictly before
P_MINUS_1's own start date (2022-03-30), reaching into 2021 -- the reason
`src.orb_research_historical_calendar`'s floor was extended from
2022-01-01 to 2021-01-01 (verified NOT in `PINNED_IMPLEMENTATION_CLOSURE`
before touching it). Universe is the same fixed #320 `ANCHORED_COHORT`
used for P_MINUS_1, reused unchanged -- not re-derived, not chosen based
on P_MINUS_2's own P&L. Documented limitation: this cohort was
established from later periods, so its backward application into
P_MINUS_2 may carry survivorship/universe-selection bias; that limitation
is disclosed, not "fixed" by substituting symbols now.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.exchange_calendar import EASTERN
from src.orb_baseline_fourth_period_research import ANCHORED_COHORT, max_drawdown_and_streak_from_rows
from src.orb_baseline_stability_research import TradeDiagnosticRow, build_stability_rows, excluding_symbol
from src.orb_candidate_selection_timing_research import (
    CONFIG_ENTRY,
    CONFIG_LABEL,
    DECISION_CUTOFF_TIME,
    DecisionTimeCandidate,
    build_wait_row,
    early_pool,
    enrich_with_decision_time,
)
from src.orb_existing_configs_reevaluation_research import best_contributing_symbol_by_r
from src.orb_gap_rvol_sensitivity_research import ComboEvaluation, InputsTable, sensitivity_config
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows
from src.orb_research_historical_calendar import research_trading_sessions_before

__all__ = [
    "Independent0950ConfirmationError",
    "P_MINUS_2_REFERENCE_DAY",
    "P_MINUS_2_SESSION_COUNT",
    "VALIDATION_UNIVERSE",
    "POLICY_CURRENT_FIRST_VALID",
    "POLICY_WAITED_FIRST_VALID_0950",
    "POLICIES",
    "derive_p_minus_2_sessions",
    "build_both_policies_rows",
    "wait_diagnostics",
    "waited_symbol_robustness",
    "Independent0950ValidationCriteria",
    "DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA",
    "classify_0950_confirmation",
    "recommend_next_milestone",
]


class Independent0950ConfirmationError(RuntimeError):
    """Refused: M42 independent 09:50-confirmation validation tooling
    could not safely proceed."""


# ---------------------------------------------------------------------------
# Frozen period: P_MINUS_2, 252 sessions strictly before P_MINUS_1's start.

P_MINUS_2_REFERENCE_DAY = date(2022, 3, 30)
P_MINUS_2_SESSION_COUNT = 252


def derive_p_minus_2_sessions(
    *, reference_day: date = P_MINUS_2_REFERENCE_DAY, count: int = P_MINUS_2_SESSION_COUNT,
) -> tuple[date, ...]:
    """The `count` most recent research-calendar-supported sessions
    strictly before `reference_day`. Raises `ResearchCalendarError`
    (propagated, not caught) if the research calendar's own coverage
    floor is reached first."""
    return research_trading_sessions_before(reference_day, count=count)


# ---------------------------------------------------------------------------
# Frozen universe: the same fixed #320 ANCHORED_COHORT used for P_MINUS_1.

VALIDATION_UNIVERSE: tuple[str, ...] = ANCHORED_COHORT


# ---------------------------------------------------------------------------
# Two policies only.

POLICY_CURRENT_FIRST_VALID = "CURRENT_FIRST_VALID"
POLICY_WAITED_FIRST_VALID_0950 = "WAITED_FIRST_VALID_0950"
POLICIES: tuple[str, str] = (POLICY_CURRENT_FIRST_VALID, POLICY_WAITED_FIRST_VALID_0950)


def build_both_policies_rows(
    *, period: str, evaluation: ComboEvaluation, inputs_table: InputsTable,
    market_bars_by_symbol: Mapping[str, Sequence], base_config: Mapping[str, Any],
    implementation_digest: str, created_at, session_bounds_provider=None,
) -> tuple[dict[str, tuple[TradeDiagnosticRow, ...]], tuple, tuple[DecisionTimeCandidate, ...]]:
    """Builds CURRENT_FIRST_VALID (`build_stability_rows`, unmodified) and
    WAITED_FIRST_VALID_0950 (`early_pool` + `enrich_with_decision_time` +
    `build_wait_row`, all imported unmodified from M41) rows. Returns
    `(rows_by_policy, early_winners, waited_enriched)` -- the latter two
    are also needed by `wait_diagnostics` below."""
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise Independent0950ConfirmationError("build_both_policies_rows requires the frozen GAP_050_RVOL_175 combo")

    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"

    full_signals = evaluation.candidate_signals
    early_signals = early_pool(full_signals)

    current_winners = select_first_candidate_per_day(full_signals)
    early_winners = select_first_candidate_per_day(early_signals)

    common_kwargs = dict(
        config_label=CONFIG_LABEL, combo=CONFIG_ENTRY.combo, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=session_bounds_provider,
    )
    rows_current = build_stability_rows(period=period, view=POLICY_CURRENT_FIRST_VALID, candidate_signals=current_winners, **common_kwargs)

    waited_enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)
    shadow_run_id = f"m42-{period}-{POLICY_WAITED_FIRST_VALID_0950}-{CONFIG_LABEL}"
    rows_waited = tuple(
        build_wait_row(
            c, period=period, view=POLICY_WAITED_FIRST_VALID_0950, combo_config=combo_config,
            strategy_version=strategy_version, implementation_digest=implementation_digest,
            shadow_run_id=shadow_run_id, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, session_bounds_provider=session_bounds_provider,
        )
        for c in waited_enriched
    )

    return {POLICY_CURRENT_FIRST_VALID: rows_current, POLICY_WAITED_FIRST_VALID_0950: rows_waited}, current_winners, waited_enriched


# ---------------------------------------------------------------------------
# WAIT-specific diagnostics. Never uses outcome/future information to
# change which symbol is selected -- purely descriptive, computed after
# the (already-decided) rows exist.

def wait_diagnostics(
    *, waited_enriched: Sequence[DecisionTimeCandidate], rows_current: Sequence[TradeDiagnosticRow],
    rows_waited: Sequence[TradeDiagnosticRow],
) -> dict[str, Any]:
    total = len(waited_enriched)
    survived = sum(1 for r in rows_waited if r.decision.get("wait_reevaluation_approved") is True)
    rejected = sum(1 for r in rows_waited if r.decision.get("wait_reevaluation_approved") is False)
    no_data = sum(1 for r in rows_waited if r.decision.get("wait_reevaluation_approved") is None)
    survival_rate = (survived / total) if total else None

    wait_minutes = [
        (c.decision_live_data.timestamp - c.original_live_data.timestamp).total_seconds() / 60.0
        for c in waited_enriched if c.decision_live_data is not None
    ]
    average_wait_minutes = (sum(wait_minutes) / len(wait_minutes)) if wait_minutes else None

    signal_time_histogram: dict[str, int] = {}
    for c in waited_enriched:
        key = c.original_live_data.timestamp.astimezone(EASTERN).strftime("%H:%M")
        signal_time_histogram[key] = signal_time_histogram.get(key, 0) + 1

    current_entered_by_day = {r.decision["session_date"]: r for r in rows_current if r.outcome["entered"]}
    waited_entered_by_day = {r.decision["session_date"]: r for r in rows_waited if r.outcome["entered"]}

    skip_days = sum(1 for day in current_entered_by_day if day not in waited_entered_by_day)
    both_trade_days = [day for day in current_entered_by_day if day in waited_entered_by_day]
    same_symbol_overlap = sum(
        1 for day in both_trade_days
        if current_entered_by_day[day].decision["symbol"] == waited_entered_by_day[day].decision["symbol"]
    )

    paired_r_deltas: list[float] = []
    transitions: dict[str, int] = {}
    for day in both_trade_days:
        c_row, w_row = current_entered_by_day[day], waited_entered_by_day[day]
        if c_row.outcome["closed"] and w_row.outcome["closed"]:
            paired_r_deltas.append(w_row.outcome["r_multiple"] - c_row.outcome["r_multiple"])
            key = f"current_{'win' if c_row.outcome['is_winner'] else 'loss'}_waited_{'win' if w_row.outcome['is_winner'] else 'loss'}"
            transitions[key] = transitions.get(key, 0) + 1

    return {
        "days_with_candidate_by_0950": total,
        "days_surviving_0950_revalidation": survived,
        "survival_rate": survival_rate,
        "days_rejected_at_0950": rejected,
        "days_no_decision_time_data": no_data,
        "average_wait_minutes": average_wait_minutes,
        "first_signal_time_histogram": signal_time_histogram,
        "days_waited_skips_but_current_trades": skip_days,
        "both_policies_trade_days": len(both_trade_days),
        "same_symbol_overlap_count": same_symbol_overlap,
        "paired_r_delta_mean": (sum(paired_r_deltas) / len(paired_r_deltas)) if paired_r_deltas else None,
        "paired_comparisons_count": len(paired_r_deltas),
        "win_loss_transitions": transitions,
    }


# ---------------------------------------------------------------------------
# Symbol robustness (WAITED only). Reuses existing R-based ranking and
# exclusion helpers unchanged; introduces no symbol whitelist.

def waited_symbol_robustness(rows_waited: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    combined = trade_quality_from_rows(rows_waited)
    per_symbol = symbol_summary(rows_waited)["per_symbol"]
    best_symbol = best_contributing_symbol_by_r(rows_waited)

    contribution_pct = None
    if best_symbol and combined.get("cumulative_r"):
        best_cum_r = per_symbol[best_symbol].get("cumulative_r") or 0
        contribution_pct = best_cum_r / combined["cumulative_r"]

    top3 = sorted(per_symbol, key=lambda s: per_symbol[s].get("cumulative_r") or 0, reverse=True)[:3]
    excl_best = excluding_symbol(rows_waited, symbol=best_symbol) if best_symbol else combined
    excl_aapl = excluding_symbol(rows_waited, symbol="AAPL")
    excl_tsla = excluding_symbol(rows_waited, symbol="TSLA")

    return {
        "best_r_contributor": best_symbol,
        "best_r_contribution_pct": contribution_pct,
        "top3_r_contributors": top3,
        "expectancy_r_excluding_best_contributor": excl_best.get("expectancy_r_per_trade"),
        "expectancy_r_excluding_aapl": excl_aapl.get("expectancy_r_per_trade"),
        "expectancy_r_excluding_tsla": excl_tsla.get("expectancy_r_per_trade"),
    }


# ---------------------------------------------------------------------------
# Predeclared classification -- frozen before P_MINUS_2 results are read.

@dataclass(frozen=True)
class Independent0950ValidationCriteria:
    minimum_waited_closed_trades: int = MIN_SAMPLE_CLOSED_TRADES  # 15
    maximum_acceptable_drawdown_r: float = 15.0


DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA = Independent0950ValidationCriteria()


def _pf_r(tq: Mapping[str, Any] | None) -> float | None:
    tq = tq or {}
    raw = tq.get("profit_factor_r")
    if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
        return float("inf")
    return raw


def classify_0950_confirmation(
    *, waited_trade_quality: Mapping[str, Any], current_trade_quality: Mapping[str, Any],
    waited_max_drawdown_r: float | None, waited_excluding_best_symbol_expectancy_r: float | None,
    criteria: Independent0950ValidationCriteria = DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA,
) -> dict[str, Any]:
    """VALIDATED / MIXED / FAILED / INSUFFICIENT_SAMPLE, per this study's
    own predeclared rule -- fixed before any P_MINUS_2 result was read.
    R-normalized metrics only."""
    closed = waited_trade_quality.get("closed_trades") or 0
    if closed < criteria.minimum_waited_closed_trades:
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": f"WAITED closed trades={closed} below minimum {criteria.minimum_waited_closed_trades}",
        }

    w_exp = waited_trade_quality.get("expectancy_r_per_trade")
    w_pf = _pf_r(waited_trade_quality)
    c_exp = current_trade_quality.get("expectancy_r_per_trade")
    c_pf = _pf_r(current_trade_quality)

    checks = {
        "expectancy_r_positive": (w_exp or 0) > 0,
        "profit_factor_r_above_1": (w_pf or 0) > 1,
        "adequate_sample": True,
        "expectancy_r_beats_current": w_exp is not None and c_exp is not None and w_exp > c_exp,
        "profit_factor_r_beats_current": w_pf is not None and c_pf is not None and w_pf > c_pf,
        "acceptable_drawdown": waited_max_drawdown_r is not None and waited_max_drawdown_r <= criteria.maximum_acceptable_drawdown_r,
        "not_single_symbol_dependent": (waited_excluding_best_symbol_expectancy_r or 0) > 0,
    }

    if all(checks.values()):
        verdict = "VALIDATED"
    elif (w_exp or 0) <= 0 or (w_pf or 0) <= 1:
        verdict = "FAILED"
    else:
        verdict = "MIXED"

    return {
        "verdict": verdict, "checks": checks,
        "waited_expectancy_r": w_exp, "waited_profit_factor_r": w_pf,
        "current_expectancy_r": c_exp, "current_profit_factor_r": c_pf,
        "waited_excluding_best_symbol_expectancy_r": waited_excluding_best_symbol_expectancy_r,
        "waited_max_drawdown_r": waited_max_drawdown_r,
    }


def recommend_next_milestone(*, verdict: str) -> dict[str, Any]:
    """Exactly one of PROFIT_PROTECTION_EXIT_EXPERIMENT / PAUSE_0950_CORE
    / (operator decision required), per the study's own fixed rule. By
    `classify_0950_confirmation`'s own construction, MIXED always implies
    positive expectancy_r and PF_R>1 (FAILED already captures every case
    where either is not true), so MIXED unconditionally routes to
    PROFIT_PROTECTION_EXIT_EXPERIMENT with the core labeled PROVISIONAL,
    matching the frozen decision structure exactly."""
    if verdict == "VALIDATED":
        return {
            "next_milestone": "PROFIT_PROTECTION_EXIT_EXPERIMENT",
            "rationale": "WAITED_FIRST_VALID_0950 independently validated on P_MINUS_2.",
            "core_status": "VALIDATED", "requires_operator_decision": False,
        }
    if verdict == "MIXED":
        return {
            "next_milestone": "PROFIT_PROTECTION_EXIT_EXPERIMENT",
            "rationale": (
                "MIXED, but WAITED retains positive expectancy_r and profit_factor_r>1 by construction -- "
                "the 09:50 core is labeled PROVISIONAL rather than fully validated. No further timing sweep."
            ),
            "core_status": "PROVISIONAL", "requires_operator_decision": False,
        }
    if verdict == "FAILED":
        return {
            "next_milestone": "PAUSE_0950_CORE",
            "rationale": "WAITED_FIRST_VALID_0950 did not independently validate on P_MINUS_2.",
            "core_status": "FAILED", "requires_operator_decision": False,
        }
    return {
        "next_milestone": None,
        "rationale": "Insufficient sample on P_MINUS_2 -- report and stop for operator decision.",
        "core_status": "INSUFFICIENT_SAMPLE", "requires_operator_decision": True,
    }
