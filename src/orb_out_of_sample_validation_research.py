"""Research tooling for the ORB out-of-sample historical validation study
(Issue #304).

Issues #300 and #302 (both merged) discovered/selected promising gap/RVOL
thresholds -- notably gap=0.50%/rvol=1.25x -- entirely from one 90-session
window, 2026-04-07 through 2026-08-13 (the "discovery period"). A threshold
selected and evaluated on the same sample it was chosen from tells you
nothing about generalization. This module answers exactly that: do five
specific, pre-registered gap/RVOL configurations -- not a new sweep --
continue to show acceptable frequency, expectancy, and profit factor on an
earlier, previously unoptimized historical period that ends strictly before
the discovery window begins?

Reuse, not reinterpretation: every configuration is evaluated through the
identical unmodified pipeline #300/#302 already validated --
`src.orb_universe_expansion_research.resolve_session_signal` (fed by
`precompute_session_signal_inputs`), `build_study_report`, and the merged
M15 shadow-execution simulator for trade quality. This module imports the
shared building blocks directly from `src.orb_gap_rvol_sensitivity_research`
(config override, per-combo evaluation, matrix-row aggregation,
concentration, trade-quality aggregation) unchanged, and adds only: (1) the
five pre-registered configurations (not a grid), (2) the discovery-period
reference figures reported by Issue #302's own merged artifact (hardcoded
from that report, never re-derived, so this module cannot quietly redefine
what "the discovery result" was), (3) candidate-multiplicity /
one-trade-per-day descriptive views, and (4) a deterministic robustness
classifier whose thresholds are fixed in this file before any out-of-sample
number is computed.

Calendar: the out-of-sample period needed here reaches into 2025, outside
`src.exchange_calendar`'s own 2026-2027 coverage. Session-date resolution
for this study therefore uses
`src.orb_research_historical_calendar.research_trading_sessions_before`
(RESEARCH-ONLY, isolated from production and the pinned checkpoint
closure -- see that module's own docstring), never
`src.orb_universe_expansion_research.trading_sessions_before` for the
primary out-of-sample window.

Safety: `sensitivity_config` (imported unmodified from
`orb_gap_rvol_sensitivity_research`) always deep-copies the loaded config
before overriding the two thresholds under study; `config/strategy_v0.yaml`
is never read back or written by this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any, Mapping, Sequence

from src.orb_gap_rvol_sensitivity_research import (
    ComboEvaluation,
    InputsTable,
    NORMALIZATION_VERSION,
    ParameterCombo,
    REPOSITORY_ROOT,
    RESEARCH_UNIVERSE_LABEL,
    CandidateSignal,
    aggregate_trade_quality,
    build_candidate_shadow_decision,
    evaluate_combo,
    matrix_row,
    precompute_inputs_table,
    sensitivity_config,
    simulate_combo_trade_quality,
    symbol_concentration,
)
from src.shadow_execution_simulator import ShadowExecutionResult, simulate_shadow_execution

__all__ = [
    "REPOSITORY_ROOT",
    "NORMALIZATION_VERSION",
    "RESEARCH_UNIVERSE_LABEL",
    "ParameterCombo",
    "InputsTable",
    "ComboEvaluation",
    "precompute_inputs_table",
    "evaluate_combo",
    "matrix_row",
    "symbol_concentration",
    "sensitivity_config",
    "build_candidate_shadow_decision",
    "simulate_combo_trade_quality",
    "aggregate_trade_quality",
    "OutOfSampleValidationError",
    "CONFIGS",
    "CONFIG_LABELS",
    "config_matrix",
    "config_for_letter",
    "DISCOVERY_PERIOD",
    "DISCOVERY_PERIOD_REFERENCE",
    "candidate_multiplicity",
    "same_day_symbol_combinations",
    "discovery_vs_out_of_sample",
    "RobustnessCriteria",
    "DEFAULT_ROBUSTNESS_CRITERIA",
    "classify_robustness",
    "final_decision",
    "select_first_candidate_per_day",
    "simulate_candidate_signals_detailed",
    "extended_trade_stats",
    "one_trade_per_day_vs_all_candidates",
]


class OutOfSampleValidationError(RuntimeError):
    """Refused: out-of-sample validation tooling could not safely proceed."""


# ---------------------------------------------------------------------------
# The five pre-registered configurations. Fixed by the study's own goal
# document, not derived from a sweep -- adding, removing, or re-ordering an
# entry here is a scope change, not a tuning step.
# ---------------------------------------------------------------------------

CONFIG_A_BASELINE = ParameterCombo(gap_percent=Decimal("1.00"), rvol_multiple=Decimal("3.00"))
CONFIG_B_CONSERVATIVE_RELAXED = ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("1.50"))
CONFIG_C_FINE_STUDY_RUNNER_UP = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.50"))
CONFIG_D_HIGHER_GAP_LOWER_RVOL = ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("1.25"))
CONFIG_E_RESEARCH_LEADER = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.25"))

CONFIGS: tuple[ParameterCombo, ...] = (
    CONFIG_A_BASELINE,
    CONFIG_B_CONSERVATIVE_RELAXED,
    CONFIG_C_FINE_STUDY_RUNNER_UP,
    CONFIG_D_HIGHER_GAP_LOWER_RVOL,
    CONFIG_E_RESEARCH_LEADER,
)

CONFIG_LABELS: Mapping[ParameterCombo, str] = {
    CONFIG_A_BASELINE: "A",
    CONFIG_B_CONSERVATIVE_RELAXED: "B",
    CONFIG_C_FINE_STUDY_RUNNER_UP: "C",
    CONFIG_D_HIGHER_GAP_LOWER_RVOL: "D",
    CONFIG_E_RESEARCH_LEADER: "E",
}


def config_matrix() -> tuple[ParameterCombo, ...]:
    return CONFIGS


def config_for_letter(letter: str) -> ParameterCombo:
    for combo, label in CONFIG_LABELS.items():
        if label == letter:
            return combo
    raise OutOfSampleValidationError(f"no configuration labelled {letter!r}")


# ---------------------------------------------------------------------------
# Discovery-period reference figures. Sourced from the merged Issue #302
# artifact (`artifacts/research-fine-rvol-validation/results/
# orb_fine_rvol_validation_study.json`, 90 sessions, 2026-04-07 through
# 2026-08-13). Hardcoded on purpose: this study measures the out-of-sample
# period honestly and compares against what was actually reported, not
# against a number this module could quietly redefine after the fact.
# ---------------------------------------------------------------------------

DISCOVERY_PERIOD = {"first_session": "2026-04-07", "last_session": "2026-08-13", "sessions": 90}

DISCOVERY_PERIOD_REFERENCE: Mapping[str, Mapping[str, Any]] = {
    "A": {
        "sessions_with_candidate": 14, "candidate_day_rate": 0.1556, "top_3_share": 0.5556,
        "trades_closed": None, "win_rate": None, "expectancy_per_trade": None,
        "profit_factor": None, "maximum_drawdown": None,
        "note": "coverage_status=INVALID_INSUFFICIENT_COVERAGE at 87.56% in the discovery-period "
                "report; trade quality was not evaluated for this row there.",
    },
    "B": {
        "sessions_with_candidate": 35, "candidate_day_rate": 0.3889, "top_3_share": 0.4630,
        "trades_closed": 50, "win_rate": 0.56, "expectancy_per_trade": 0.6852,
        "profit_factor": 1.471868328627504992769093038, "maximum_drawdown": 36.895,
    },
    "C": {
        "sessions_with_candidate": 40, "candidate_day_rate": 0.4444, "top_3_share": None,
        "trades_closed": 57, "win_rate": 0.5789473684210527, "expectancy_per_trade": 1.042105263157894736842105263,
        "profit_factor": 1.788635156664896441848114711, "maximum_drawdown": 36.895,
    },
    "D": {
        "sessions_with_candidate": 40, "candidate_day_rate": 0.4444, "top_3_share": 0.4492753623188406,
        "trades_closed": 65, "win_rate": 0.5230769230769231, "expectancy_per_trade": 1.312230769230769230769230769,
        "profit_factor": 1.973853970428726380087914597, "maximum_drawdown": 36.895,
    },
    "E": {
        "sessions_with_candidate": 47, "candidate_day_rate": 0.5222, "top_3_share": None,
        "trades_closed": 75, "win_rate": 0.5466666666666666, "expectancy_per_trade": 1.528733333333333333333333333,
        "profit_factor": 2.269712070874861572535991141, "maximum_drawdown": 36.895,
    },
}


# ---------------------------------------------------------------------------
# One-trade-per-day / candidate-multiplicity view.
# ---------------------------------------------------------------------------


def candidate_multiplicity(evaluation: ComboEvaluation) -> dict[str, Any]:
    """Goal item "one-trade-per-day view": for this combo's candidate days,
    how many carried exactly 1/2/3+ candidates, and how many "excess"
    candidates (beyond the first of each day) exist in total. Descriptive
    only -- no ranking or multi-trade logic."""
    counts = evaluation.candidates_by_session
    exactly_1 = sum(1 for c in counts.values() if c == 1)
    exactly_2 = sum(1 for c in counts.values() if c == 2)
    three_plus = sum(1 for c in counts.values() if c >= 3)
    excess = sum(c - 1 for c in counts.values())
    return {
        "candidate_days": len(counts),
        "days_with_exactly_1": exactly_1,
        "days_with_exactly_2": exactly_2,
        "days_with_3_or_more": three_plus,
        "total_excess_candidates_beyond_first_per_day": excess,
    }


def same_day_symbol_combinations(evaluation: ComboEvaluation, *, top_n: int = 5) -> list[dict[str, Any]]:
    """Goal item "optional multi-candidate research view": among days with
    2+ candidates, the most common same-day symbol combinations. Research
    only -- never used to imply all candidates should be traded."""
    by_day: dict[date, list[str]] = {}
    for symbol, session_date, *_rest in evaluation.candidate_signals:
        by_day.setdefault(session_date, []).append(symbol)
    from collections import Counter

    combo_counts: Counter[tuple[str, ...]] = Counter()
    for symbols in by_day.values():
        if len(symbols) >= 2:
            combo_counts[tuple(sorted(symbols))] += 1
    ranked = sorted(combo_counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"symbols": list(combo), "occurrences": count} for combo, count in ranked[:top_n]]


def multiplicity_distribution(evaluation: ComboEvaluation, *, session_dates: Sequence[date]) -> dict[str, int]:
    """0/1/2/3+ candidate-day distribution across every studied session,
    including zero-candidate days -- goal item "optional multi-candidate
    research view" example format."""
    counts = evaluation.candidates_by_session
    zero = len(session_dates) - len(counts)
    return {
        "0_candidates": zero,
        "1_candidate": sum(1 for c in counts.values() if c == 1),
        "2_candidates": sum(1 for c in counts.values() if c == 2),
        "3_plus_candidates": sum(1 for c in counts.values() if c >= 3),
    }


# ---------------------------------------------------------------------------
# Discovery vs out-of-sample comparison.
# ---------------------------------------------------------------------------


def discovery_vs_out_of_sample(
    *, label: str, oos_row: Mapping[str, Any], oos_trade_quality: Mapping[str, Any],
) -> dict[str, Any]:
    """Goal item "Critical comparison": pair the out-of-sample row/trade-
    quality figures against the hardcoded discovery-period reference for
    the same configuration letter, and report signed deltas where both
    sides have a value. Never treats the discovery figure as a target."""
    reference = DISCOVERY_PERIOD_REFERENCE[label]

    def _delta(oos_value: Any, ref_value: Any) -> float | None:
        if oos_value is None or ref_value is None:
            return None
        return float(oos_value) - float(ref_value)

    oos_pf_raw = oos_trade_quality.get("profit_factor")
    oos_pf = float(oos_pf_raw) if oos_pf_raw not in (None, "INFINITE_NO_LOSSES") else None
    ref_pf = reference.get("profit_factor")

    return {
        "config": label,
        "discovery_period": reference,
        "out_of_sample": {
            "sessions_with_candidate": oos_row["sessions_with_candidate"],
            "candidate_day_rate": oos_row["candidate_day_rate"],
            "top_3_share": oos_row["concentration"]["top_3_share"],
            "trades_closed": oos_trade_quality.get("trades_closed"),
            "win_rate": oos_trade_quality.get("win_rate"),
            "expectancy_per_trade": oos_trade_quality.get("expectancy_per_trade"),
            "profit_factor": oos_pf_raw,
            "maximum_drawdown": oos_trade_quality.get("maximum_drawdown"),
        },
        "delta": {
            "candidate_day_rate": _delta(oos_row["candidate_day_rate"], reference.get("candidate_day_rate")),
            "top_3_share": _delta(oos_row["concentration"]["top_3_share"], reference.get("top_3_share")),
            "win_rate": _delta(oos_trade_quality.get("win_rate"), reference.get("win_rate")),
            "expectancy_per_trade": _delta(
                oos_trade_quality.get("expectancy_per_trade"), reference.get("expectancy_per_trade"),
            ),
            "profit_factor": _delta(oos_pf, ref_pf),
            "maximum_drawdown": _delta(oos_trade_quality.get("maximum_drawdown"), reference.get("maximum_drawdown")),
        },
    }


# ---------------------------------------------------------------------------
# Robustness classification. Criteria are fixed here, before any
# out-of-sample number exists -- goal item "Robustness classification"
# explicitly requires this ordering.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RobustnessCriteria:
    minimum_trades_closed: int = 20
    minimum_candidate_day_rate: float = 0.25
    maximum_top_3_concentration: float = 0.75
    minimum_frequency_retention_vs_discovery: float = 0.5


DEFAULT_ROBUSTNESS_CRITERIA = RobustnessCriteria()


def classify_robustness(
    *, row: Mapping[str, Any], trade_quality: Mapping[str, Any], discovery_candidate_day_rate: float | None,
    criteria: RobustnessCriteria = DEFAULT_ROBUSTNESS_CRITERIA,
) -> dict[str, Any]:
    """ROBUST / MIXED / NOT_ROBUST / INSUFFICIENT_EVIDENCE, from a fixed,
    documented rule:

    INSUFFICIENT_EVIDENCE: coverage is not VALID, or fewer than
    `minimum_trades_closed` simulated closed trades exist -- there is not
    enough resolved evidence to judge quality either way.

    Otherwise, five boolean checks are evaluated: positive expectancy,
    profit_factor > 1, candidate_day_rate >= minimum_candidate_day_rate,
    top_3_share <= maximum_top_3_concentration, and out-of-sample
    candidate_day_rate retaining at least
    `minimum_frequency_retention_vs_discovery` of the discovery-period rate
    (skipped, counted as passing, if no discovery figure exists for this
    row). ROBUST requires all five. NOT_ROBUST is either failed quality gate
    (expectancy <= 0 or profit_factor <= 1) alone -- quality breaking is
    disqualifying on its own, frequency/concentration cannot offset it.
    Otherwise MIXED: quality holds but at least one other check fails."""
    coverage_ok = row["coverage_status"] == "VALID"
    trades_closed = trade_quality.get("trades_closed") or 0
    if not coverage_ok or trades_closed < criteria.minimum_trades_closed:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": (
                f"coverage_status={row['coverage_status']!r}, trades_closed={trades_closed} "
                f"(minimum {criteria.minimum_trades_closed})"
            ),
            "checks": {},
        }

    expectancy_raw = trade_quality.get("expectancy_per_trade")
    expectancy = float(expectancy_raw) if expectancy_raw is not None else None
    pf_raw = trade_quality.get("profit_factor")
    if pf_raw == "INFINITE_NO_LOSSES":
        profit_factor: float | None = float("inf")
    elif pf_raw is not None:
        profit_factor = float(pf_raw)
    else:
        profit_factor = None

    positive_expectancy = expectancy is not None and expectancy > 0
    profit_factor_above_1 = profit_factor is not None and profit_factor > 1
    candidate_day_rate = row["candidate_day_rate"]
    frequency_ok = candidate_day_rate >= criteria.minimum_candidate_day_rate
    top_3_share = row["concentration"]["top_3_share"]
    concentration_ok = top_3_share <= criteria.maximum_top_3_concentration
    if discovery_candidate_day_rate and discovery_candidate_day_rate > 0:
        retention = candidate_day_rate / discovery_candidate_day_rate
        retention_ok = retention >= criteria.minimum_frequency_retention_vs_discovery
    else:
        retention = None
        retention_ok = True

    checks = {
        "positive_expectancy": positive_expectancy,
        "profit_factor_above_1": profit_factor_above_1,
        "candidate_day_rate_at_least_minimum": frequency_ok,
        "top_3_concentration_within_limit": concentration_ok,
        "frequency_retention_vs_discovery_ok": retention_ok,
    }

    if not positive_expectancy or not profit_factor_above_1:
        verdict = "NOT_ROBUST"
    elif all(checks.values()):
        verdict = "ROBUST"
    else:
        verdict = "MIXED"

    return {
        "verdict": verdict,
        "checks": checks,
        "values": {
            "expectancy_per_trade": expectancy_raw,
            "profit_factor": pf_raw,
            "candidate_day_rate": candidate_day_rate,
            "top_3_share": top_3_share,
            "frequency_retention_vs_discovery": retention,
        },
        "criteria": {
            "minimum_trades_closed": criteria.minimum_trades_closed,
            "minimum_candidate_day_rate": criteria.minimum_candidate_day_rate,
            "maximum_top_3_concentration": criteria.maximum_top_3_concentration,
            "minimum_frequency_retention_vs_discovery": criteria.minimum_frequency_retention_vs_discovery,
        },
    }


# ---------------------------------------------------------------------------
# Final decision (goal item "Decision section"). A fixed rule over the two
# configurations of primary interest (C: 0.50%/1.50x, E: 0.50%/1.25x),
# never a production authorization.
# ---------------------------------------------------------------------------


def final_decision(*, classification_by_label: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    primary_labels = ("C", "E")
    verdicts = {label: classification_by_label[label]["verdict"] for label in primary_labels}

    if any(v == "ROBUST" for v in verdicts.values()):
        robust_labels = [label for label in primary_labels if verdicts[label] == "ROBUST"]
        decision = "READY_FOR_PRODUCTION_PARAMETER_REVIEW"
        rationale = (
            f"{'/'.join(robust_labels)} classified ROBUST on out-of-sample data: positive "
            "expectancy, profit_factor>1, adequate closed-trade count, acceptable candidate-day "
            "frequency and concentration, and frequency retained relative to the discovery "
            "period. This authorizes only a SEPARATE production-change review, not a production "
            "change here."
        )
    elif all(v == "NOT_ROBUST" for v in verdicts.values()):
        decision = "RESEARCH_CONFIGURATION_NOT_ROBUST"
        rationale = "Both C (0.50%/1.50x) and E (0.50%/1.25x) classified NOT_ROBUST out-of-sample: expectancy or profit_factor broke down outside the discovery period."
    else:
        decision = "NEEDS_MORE_HISTORICAL_VALIDATION"
        rationale = (
            f"C={verdicts['C']}, E={verdicts['E']}: neither reaches ROBUST and at least one is "
            "not a clean NOT_ROBUST failure either (MIXED or INSUFFICIENT_EVIDENCE) -- the "
            "out-of-sample evidence is not yet conclusive enough either to advance or to "
            "discard these thresholds."
        )

    return {
        "decision": decision,
        "rationale": rationale,
        "primary_configuration_verdicts": verdicts,
        "authorizes_production_change": False,
    }


# ---------------------------------------------------------------------------
# One-trade-per-day view. Current ORB-LIVE v1 is bounded around one entry
# attempt; this is a separate, explicitly RESEARCH-ONLY simulation view
# comparing that bound against an unbounded ALL-CANDIDATES view, never a
# claim that the all-candidate model is current production behavior.
# ---------------------------------------------------------------------------


def select_first_candidate_per_day(candidate_signals: Sequence[CandidateSignal]) -> tuple[CandidateSignal, ...]:
    """Deterministic one-trade-per-day selection: the first candidate
    chronologically each session day, by the decision's own `LiveData.
    timestamp` (the instant the completed-bar decision was made -- known at
    decision time, not derived from any later bar), ties broken
    alphabetically by symbol for full determinism. Never uses realized P&L
    to choose -- the exact rule the study goal requires ("first valid
    qualifying breakout chronologically... do not use future P&L")."""
    best_by_day: dict[date, CandidateSignal] = {}
    for candidate in candidate_signals:
        _symbol, session_date, _opening, live_data, _decision = candidate
        current = best_by_day.get(session_date)
        if current is None:
            best_by_day[session_date] = candidate
            continue
        current_symbol, _cd, _co, current_live, _cdec = current
        if (live_data.timestamp, candidate[0]) < (current_live.timestamp, current_symbol):
            best_by_day[session_date] = candidate
    return tuple(best_by_day[day] for day in sorted(best_by_day))


def _session_bars_for(bars: Sequence[Any], *, session_date: date) -> list[Any]:
    return sorted((bar for bar in bars if bar.trading_date == session_date), key=lambda bar: bar.event_timestamp)


def simulate_candidate_signals_detailed(
    *,
    candidate_signals: Sequence[CandidateSignal],
    combo: ParameterCombo,
    market_bars_by_symbol: Mapping[str, Sequence],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
    run_label: str,
) -> tuple[dict[str, Any], list[ShadowExecutionResult]]:
    """Replays an arbitrary candidate-signal subset through the unmodified
    M15 shadow-execution simulator -- the same building blocks
    `orb_gap_rvol_sensitivity_research.simulate_combo_trade_quality` uses,
    generalized to any subset (all candidates, or the one-per-day
    selection) so both views share identical entry/stop/target/exit
    semantics. No stop/target/exit rule is invented here; entry/stop/
    target/exit decisions remain entirely `src.exit_engine.ExitEngine`'s
    and `simulate_shadow_execution`'s. Returns both the aggregate summary
    (`aggregate_trade_quality`, unmodified) and the raw per-trade results,
    which the aggregate alone cannot support (streaks, monthly/day/symbol
    breakdowns)."""
    combo_config = sensitivity_config(base_config, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    shadow_run_id = f"oos-validation-{run_label}-{combo.label}"
    spy_bars = market_bars_by_symbol["SPY"]

    results: list[ShadowExecutionResult] = []
    for symbol, session_date, opening_data, live_data, decision in candidate_signals:
        candidate_bars = market_bars_by_symbol[symbol]
        shadow_decision = build_candidate_shadow_decision(
            symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
            decision=decision, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
        )
        session_bars = _session_bars_for(candidate_bars, session_date=session_date)
        subsequent_bars = [bar for bar in session_bars if bar.event_timestamp >= shadow_decision.signal_timestamp]
        results.append(simulate_shadow_execution(shadow_decision, subsequent_bars))
    return aggregate_trade_quality(results), results


def extended_trade_stats(results: Sequence[ShadowExecutionResult]) -> dict[str, Any]:
    """Metrics `aggregate_trade_quality` does not already carry, computed
    only from CLOSED trades (`realized_pnl is not None`), ordered by exit
    time: longest losing streak (consecutive closed trades with
    `realized_pnl <= 0`), best/worst single trading day by summed realized
    P&L, monthly (calendar `YYYY-MM`) win/loss/P&L breakdown, and
    per-symbol trade count/P&L/share of total realized P&L. Every figure
    here is arithmetic over data the simulator already produced -- no new
    semantics are invented."""
    closed = [r for r in results if r.realized_pnl is not None]
    if not closed:
        return {
            "longest_losing_streak": 0, "best_day": None, "worst_day": None,
            "monthly": {}, "by_symbol": {},
        }
    ordered = sorted(closed, key=lambda r: r.exit_timestamp)

    longest_losing_streak = 0
    current_streak = 0
    for r in ordered:
        if r.realized_pnl <= 0:
            current_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_streak)
        else:
            current_streak = 0

    daily_pnl: dict[date, Decimal] = {}
    monthly: dict[str, dict[str, Any]] = {}
    by_symbol: dict[str, dict[str, Any]] = {}
    for r in ordered:
        daily_pnl[r.trading_date] = daily_pnl.get(r.trading_date, Decimal("0")) + r.realized_pnl
        month_key = r.trading_date.strftime("%Y-%m")
        month = monthly.setdefault(month_key, {"trades_closed": 0, "wins": 0, "losses": 0, "realized_pnl": Decimal("0")})
        month["trades_closed"] += 1
        month["wins" if r.realized_pnl > 0 else "losses"] += 1
        month["realized_pnl"] += r.realized_pnl
        sym = by_symbol.setdefault(r.symbol, {"trades_closed": 0, "wins": 0, "losses": 0, "realized_pnl": Decimal("0")})
        sym["trades_closed"] += 1
        sym["wins" if r.realized_pnl > 0 else "losses"] += 1
        sym["realized_pnl"] += r.realized_pnl

    best_day = max(daily_pnl.items(), key=lambda kv: kv[1])
    worst_day = min(daily_pnl.items(), key=lambda kv: kv[1])
    total_pnl = sum(daily_pnl.values(), Decimal("0"))

    return {
        "longest_losing_streak": longest_losing_streak,
        "best_day": {"date": best_day[0].isoformat(), "realized_pnl": str(best_day[1])},
        "worst_day": {"date": worst_day[0].isoformat(), "realized_pnl": str(worst_day[1])},
        "monthly": {
            k: {**v, "realized_pnl": str(v["realized_pnl"])}
            for k, v in sorted(monthly.items())
        },
        "by_symbol": {
            k: {**v, "realized_pnl": str(v["realized_pnl"]),
                "share_of_total_realized_pnl": (float(v["realized_pnl"] / total_pnl) if total_pnl != 0 else None)}
            for k, v in sorted(by_symbol.items(), key=lambda kv: kv[1]["realized_pnl"], reverse=True)
        },
    }


def one_trade_per_day_vs_all_candidates(
    *,
    combo: ParameterCombo,
    evaluation: ComboEvaluation,
    market_bars_by_symbol: Mapping[str, Sequence],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
) -> dict[str, Any]:
    """Goal item "V1 one-trade-per-day view": simulates the same
    configuration's candidates two ways -- every candidate (ALL_CANDIDATES,
    research-only, not current production behavior) and exactly one per day
    via `select_first_candidate_per_day` (ONE_TRADE_PER_DAY, matching
    current ORB-LIVE v1's own bound) -- and reports both aggregates plus
    each view's extended stats side by side."""
    all_signals = evaluation.candidate_signals
    one_per_day_signals = select_first_candidate_per_day(all_signals)

    all_summary, all_results = simulate_candidate_signals_detailed(
        candidate_signals=all_signals, combo=combo, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, implementation_digest=implementation_digest, created_at=created_at,
        run_label="all-candidates",
    )
    one_summary, one_results = simulate_candidate_signals_detailed(
        candidate_signals=one_per_day_signals, combo=combo, market_bars_by_symbol=market_bars_by_symbol,
        base_config=base_config, implementation_digest=implementation_digest, created_at=created_at,
        run_label="one-trade-per-day",
    )

    return {
        "note": (
            "ONE_TRADE_PER_DAY matches current ORB-LIVE v1's own one-entry-attempt "
            "bound. ALL_CANDIDATES is research-only and is NOT current production "
            "behavior -- it exists to show how much additional (unrealized, in "
            "production) opportunity or risk coexists on multi-candidate days."
        ),
        "ALL_CANDIDATES": {
            "candidate_count": len(all_signals),
            "trade_quality": all_summary,
            "extended_stats": extended_trade_stats(all_results),
        },
        "ONE_TRADE_PER_DAY": {
            "candidate_count": len(one_per_day_signals),
            "selection_rule": "first_valid_qualifying_breakout_chronologically_by_decision_timestamp",
            "trade_quality": one_summary,
            "extended_stats": extended_trade_stats(one_results),
        },
    }
