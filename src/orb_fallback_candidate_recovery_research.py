"""Research tooling for M44: fallback-candidate trade-frequency recovery
(Issue #340).

One material question only: when the primary pre-09:50 candidate
(`WAITED_FIRST_VALID_0950`) fails the frozen 09:50 revalidation, can a
profitable trade day be recovered by taking the earliest OTHER candidate
that was already present in the pre-09:50 pool and is independently valid
at 09:50? This is EXIT-unchanged, entry-frequency research only: no
gap/RVOL/universe/exit-rule change, no ranking, no second trade, no new
checkpoint. `strategy.enabled` remains `false` throughout.

Reuse, not reinterpretation:
  * The pre-09:50 candidate pool comes from M41's own `early_pool`
    (`src.orb_candidate_selection_timing_research`), unmodified.
  * Each candidate's 09:50 decision-time re-evaluation
    (`SignalEngine.evaluate` re-run at the bar at/after 09:50, never the
    original breakout price/timestamp) is M43's own
    `_waited_shadow_decision` (`src.orb_profit_protection_exit_research`),
    imported and called unmodified once per attempted candidate -- the
    identical mechanism M42/M43's own `WAITED_FIRST_VALID_0950` primary
    already uses, so BASELINE here reproduces M43's own primary trade
    identities exactly (see `test_baseline_matches_m43_waited_primary_
    exactly` in the test suite).
  * The one frozen exit arm is M43's own `simulate_exit_arm(..., arm=
    ARM_BE_AFTER_050R)`, unmodified -- no exit-rule variant is compared
    here; M43 already answered the exit question.
  * `index_market_bars_by_date` (M43.5, Issue #338) is reused for the
    date-indexed bar-retrieval path throughout.

BASELINE vs FALLBACK share one evaluation, not two
----------------------------------------------------
For each session day, the ordered early-pool candidates (original
qualifying signal timestamp ascending, symbol ascending tie-break --
`select_first_candidate_per_day`'s own tie-break, reused identically) are
re-evaluated ONE AT A TIME via `_waited_shadow_decision`, STOPPING at the
first candidate found valid at its own 09:50 decision time (`attempts`
below). BASELINE's outcome is always derivable from `attempts[0]` alone
(the primary is always attempted first, regardless of its own outcome);
FALLBACK's outcome is always `attempts[-1]` (either the candidate that
broke the loop by being found valid, or -- if none were valid -- the last
candidate in the full ordered pool, since the loop then runs to
completion). No SignalEngine re-evaluation is ever duplicated between the
two policies, and evaluating BASELINE's own primary candidate is never
redone for FALLBACK.

No ranking, no "best" candidate
--------------------------------
FALLBACK never compares candidates against each other -- RVOL, gap,
breakout strength, and sector are never read for selection purposes here
(they may still appear as fields on the underlying `SignalDecision`/
`OpeningData` objects because those types are shared with every other
research module, but no downstream code in this module ever branches on
them). The rule is purely "first remaining candidate independently valid
at 09:50," in the SAME chronological order the pool was already built in.
A still-valid primary is never abandoned for a "better" alternative --
FALLBACK only ever advances past the primary when the primary itself is
invalid or has no 09:50 decision-time data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping, Sequence

from src.market_data_canonical import MarketBar
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    HALF_LIFE_SESSIONS,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    DecisionTimeCandidate,
    early_pool,
    enrich_with_decision_time,
    group_by_day,
    recency_weighted_trade_quality,
)
from src.orb_existing_configs_reevaluation_research import best_contributing_symbol_by_r
from src.orb_gap_rvol_sensitivity_research import CandidateSignal, ComboEvaluation, InputsTable, sensitivity_config
from src.orb_profit_protection_exit_research import (
    ARM_BE_AFTER_050R,
    ENTRY_WAITED_FIRST_VALID_0950,
    IndexedMarketBars,
    ProfitProtectionExitError,
    _decision_features,
    _no_decision_time_data_outcome,
    _outcome_dict,
    _waited_shadow_decision,
    index_market_bars_by_date,
    simulate_exit_arm,
)
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, TradeDiagnosticRow, symbol_summary, trade_quality_from_rows
from src.orb_profit_protection_exit_research import full_stop_out_rate, max_drawdown_r_and_streak_from_rows
from src.shadow_trading_decision import ShadowTradingDecision


class FallbackCandidateRecoveryError(RuntimeError):
    """Refused: fallback-candidate recovery research could not safely proceed."""


ENTRY_BASELINE = ENTRY_WAITED_FIRST_VALID_0950  # "WAITED_FIRST_VALID_0950" -- reused literally, not reproduced
ENTRY_FALLBACK = "FALLBACK_FIRST_VALID_0950"
ENTRY_POLICIES: tuple[str, ...] = (ENTRY_BASELINE, ENTRY_FALLBACK)

EXIT_ARM = ARM_BE_AFTER_050R


@dataclass(frozen=True)
class CandidateAttempt:
    """One candidate's independent 09:50 re-evaluation. `position` is
    1-based (1 = primary, the same candidate `WAITED_FIRST_VALID_0950`
    already selects). `approved` is `True` (valid at 09:50), `False`
    (SignalEngine rejected the fresh re-evaluation), or `None` (the
    session had no bar at/after 09:50 for this candidate -- an honest data
    gap, never conflated with a strategy rejection)."""
    candidate: DecisionTimeCandidate
    position: int
    approved: bool | None
    shadow_decision: ShadowTradingDecision | None
    subsequent_bars: tuple[MarketBar, ...]


@dataclass(frozen=True)
class DayOutcome:
    session_date: date
    pool_size: int
    attempts: tuple[CandidateAttempt, ...]

    @property
    def primary(self) -> CandidateAttempt:
        return self.attempts[0]

    @property
    def fallback_selected(self) -> CandidateAttempt:
        """The candidate FALLBACK's own stopping rule lands on: the one
        that broke the attempt loop by being valid, or the last candidate
        tried if none were valid (which is only reached once every early
        candidate for the day has been attempted)."""
        return self.attempts[-1]

    @property
    def fallback_used(self) -> bool:
        """True exactly when FALLBACK entered a DIFFERENT candidate than
        BASELINE would have -- i.e. the primary itself was not the one
        found valid."""
        return self.fallback_selected.position != 1 and bool(self.fallback_selected.approved)


def ordered_early_pool_per_day(candidate_signals: Sequence[CandidateSignal]) -> dict[date, tuple[CandidateSignal, ...]]:
    """Every pre-09:50 candidate (M41's own `early_pool`, unmodified),
    grouped by day (M41's own `group_by_day`, unmodified) and ordered
    within each day by `(original qualifying LiveData.timestamp, symbol)`
    ascending -- `select_first_candidate_per_day`'s own deterministic
    tie-break, reused identically, so this ordering's own first element is
    always exactly M41/M42/M43's own `WAITED_FIRST_VALID_0950` primary
    candidate for that day."""
    early = early_pool(candidate_signals)
    by_day = group_by_day(early)
    return {
        day: tuple(sorted(day_candidates, key=lambda c: (c[3].timestamp, c[0])))
        for day, day_candidates in by_day.items()
    }


def _attempt_candidates(
    ordered_enriched: Sequence[DecisionTimeCandidate], *, combo_config: Mapping[str, Any], strategy_version: str,
    implementation_digest: str, shadow_run_id_prefix: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    market_bars_index_by_symbol: Mapping[str, IndexedMarketBars] | None,
    session_bounds_provider=None,
) -> tuple[CandidateAttempt, ...]:
    """Re-evaluate `ordered_enriched` (primary first) one candidate at a
    time via M43's own unmodified `_waited_shadow_decision`, stopping as
    soon as one is found valid ('approved') at its own 09:50 decision
    time -- FALLBACK's own predeclared stopping rule. Fresh 09:50 data is
    used for every re-evaluation (each candidate's own `decision_live_data`
    comes from that candidate's own session window, never a stale/earlier
    value); no bar after 09:50 ever influences which candidate is
    attempted or in what order (the order was already fixed by
    `ordered_early_pool_per_day`, itself built only from pre-09:50
    qualifying timestamps)."""
    attempts: list[CandidateAttempt] = []
    for position, candidate in enumerate(ordered_enriched, start=1):
        shadow_decision, subsequent_bars, approved = _waited_shadow_decision(
            candidate, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest,
            shadow_run_id=f"{shadow_run_id_prefix}-pos{position}-{candidate.symbol}", created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, market_bars_index_by_symbol=market_bars_index_by_symbol,
        )
        attempts.append(CandidateAttempt(
            candidate=candidate, position=position, approved=approved,
            shadow_decision=shadow_decision, subsequent_bars=tuple(subsequent_bars),
        ))
        if approved:
            break
    return tuple(attempts)


def build_day_outcomes(
    *, period: str, evaluation: ComboEvaluation, inputs_table: InputsTable,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], base_config: Mapping[str, Any],
    implementation_digest: str, created_at: datetime, session_bounds_provider=None,
) -> tuple[DayOutcome, ...]:
    """The one shared per-day computation both BASELINE and FALLBACK
    derive their rows from -- see module docstring. No new candidate
    generation beyond what `evaluate_combo` (unmodified) already
    produced."""
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise FallbackCandidateRecoveryError("build_day_outcomes requires the frozen GAP_050_RVOL_175 combo")

    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"

    pool_by_day = ordered_early_pool_per_day(evaluation.candidate_signals)
    market_bars_index_by_symbol = {
        symbol: index_market_bars_by_date(bars) for symbol, bars in market_bars_by_symbol.items()
    }

    outcomes: list[DayOutcome] = []
    for session_date in sorted(pool_by_day):
        day_pool = pool_by_day[session_date]
        enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in day_pool)
        shadow_run_id_prefix = f"m44-{period}-{session_date.isoformat()}"
        attempts = _attempt_candidates(
            enriched, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id_prefix=shadow_run_id_prefix, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, market_bars_index_by_symbol=market_bars_index_by_symbol,
            session_bounds_provider=session_bounds_provider,
        )
        outcomes.append(DayOutcome(session_date=session_date, pool_size=len(day_pool), attempts=attempts))
    return tuple(outcomes)


def _row_for_attempt(attempt: CandidateAttempt, *, period: str, entry_policy: str, session_bounds_provider=None) -> TradeDiagnosticRow:
    features = _decision_features(
        period=period, entry_policy=entry_policy, symbol=attempt.candidate.symbol,
        session_date=attempt.candidate.session_date, original_timestamp=attempt.candidate.original_live_data.timestamp,
    )
    features["wait_reevaluation_approved"] = attempt.approved
    features["pool_position"] = attempt.position
    if attempt.shadow_decision is None:
        return TradeDiagnosticRow(decision=features, outcome=_no_decision_time_data_outcome())
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    outcome = simulate_exit_arm(attempt.shadow_decision, attempt.subsequent_bars, arm=EXIT_ARM, **kwargs)
    return TradeDiagnosticRow(decision=features, outcome=_outcome_dict(outcome))


def build_policy_rows(
    day_outcomes: Sequence[DayOutcome], *, period: str, session_bounds_provider=None,
) -> dict[str, tuple[TradeDiagnosticRow, ...]]:
    """`{ENTRY_BASELINE: rows, ENTRY_FALLBACK: rows}` -- one row per session
    day that had at least one pre-09:50 candidate, for each policy."""
    baseline_rows = tuple(
        _row_for_attempt(day.primary, period=period, entry_policy=ENTRY_BASELINE, session_bounds_provider=session_bounds_provider)
        for day in day_outcomes
    )
    fallback_rows = tuple(
        _row_for_attempt(day.fallback_selected, period=period, entry_policy=ENTRY_FALLBACK, session_bounds_provider=session_bounds_provider)
        for day in day_outcomes
    )
    return {ENTRY_BASELINE: baseline_rows, ENTRY_FALLBACK: fallback_rows}


def fallback_only_rows(day_outcomes: Sequence[DayOutcome], *, period: str, session_bounds_provider=None) -> tuple[TradeDiagnosticRow, ...]:
    """Rows for days FALLBACK actually recovered -- i.e. entered a
    candidate the primary-only BASELINE policy would not have (the primary
    failed, and a later candidate in the SAME pre-09:50 pool was
    independently valid at 09:50)."""
    return tuple(
        _row_for_attempt(day.fallback_selected, period=period, entry_policy=ENTRY_FALLBACK, session_bounds_provider=session_bounds_provider)
        for day in day_outcomes if day.fallback_used
    )


# ---------------------------------------------------------------------------
# Fallback-mechanics diagnostics -- reported separately from trade-quality
# metrics per Issue #340's own requirement ("no missing-data cases hidden
# as strategy rejection").

def fallback_mechanics_diagnostics(day_outcomes: Sequence[DayOutcome]) -> dict[str, Any]:
    days_with_pool = len(day_outcomes)
    primary_survives = sum(1 for d in day_outcomes if d.primary.approved is True)
    primary_fails = sum(1 for d in day_outcomes if d.primary.approved is not True)
    primary_fails_no_data = sum(1 for d in day_outcomes if d.primary.approved is None)
    primary_fails_invalid = sum(1 for d in day_outcomes if d.primary.approved is False)

    failure_days = [d for d in day_outcomes if d.primary.approved is not True]
    failure_days_with_alternatives = [d for d in failure_days if d.pool_size >= 2]
    recovered_days = [d for d in day_outcomes if d.fallback_used]
    all_alternatives_failed_days = [
        d for d in failure_days_with_alternatives if not d.fallback_used
    ]

    position_counts: dict[str, int] = {}
    fallback_symbols: dict[str, int] = {}
    for d in recovered_days:
        position = d.fallback_selected.position
        bucket = "second" if position == 2 else ("third" if position == 3 else "fourth_plus")
        position_counts[bucket] = position_counts.get(bucket, 0) + 1
        symbol = d.fallback_selected.candidate.symbol
        fallback_symbols[symbol] = fallback_symbols.get(symbol, 0) + 1

    recovery_rate_among_failures = (len(recovered_days) / len(failure_days)) if failure_days else None
    recovery_rate_among_failures_with_alternatives = (
        len(recovered_days) / len(failure_days_with_alternatives) if failure_days_with_alternatives else None
    )

    return {
        "days_with_any_pre_0950_candidate": days_with_pool,
        "days_primary_survives_0950": primary_survives,
        "days_primary_fails_0950": primary_fails,
        "days_primary_fails_no_decision_time_data": primary_fails_no_data,
        "days_primary_fails_signal_invalid": primary_fails_invalid,
        "primary_failure_days_with_additional_early_candidate": len(failure_days_with_alternatives),
        "days_recovered_by_fallback": len(recovered_days),
        "recovery_rate_among_primary_failure_days": recovery_rate_among_failures,
        "recovery_rate_among_primary_failure_days_with_alternatives": recovery_rate_among_failures_with_alternatives,
        "fallback_candidate_position_counts": position_counts,
        "fallback_symbols": dict(sorted(fallback_symbols.items())),
        "days_with_alternatives_where_all_failed": len(all_alternatives_failed_days),
    }


# ---------------------------------------------------------------------------
# Predeclared classification (frozen before any empirical M44 result is
# read -- see docs/research/ORB_FALLBACK_CANDIDATE_RECOVERY_EXPERIMENT.md).

MIN_FALLBACK_ONLY_SUPPORTED_N = 15
MIN_FALLBACK_ONLY_INSUFFICIENT_N = 10
MIN_FREQUENCY_GAIN_PCT = 0.10
MAX_DRAWDOWN_INCREASE_PCT = 0.20


def classify_fallback_recovery(
    *, fallback_only_quality: Mapping[str, Any], fallback_only_expectancy_excl_best_symbol: float | None,
    combined_expectancy_r: float | None, combined_pf_r: float | None,
    combined_r_per_month: float, baseline_r_per_month: float,
    combined_trades_per_month: float, baseline_trades_per_month: float,
    combined_drawdown_r: float, baseline_drawdown_r: float,
) -> dict[str, Any]:
    """SUPPORTED / MIXED / NOT_SUPPORTED / INSUFFICIENT_EVIDENCE, per this
    study's own predeclared rule (frozen before any P_MINUS_2..P3 result
    was read; see `docs/research/ORB_FALLBACK_CANDIDATE_RECOVERY_
    EXPERIMENT.md`)."""
    n = fallback_only_quality["closed_trades"]
    if n < MIN_FALLBACK_ONLY_INSUFFICIENT_N:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"fallback-only closed trades={n} below {MIN_FALLBACK_ONLY_INSUFFICIENT_N}", "fallback_only_n": n}

    fallback_only_exp_r = fallback_only_quality["expectancy_r_per_trade"]
    fallback_only_pf_r = fallback_only_quality["profit_factor_r"]

    frequency_gain_pct = (
        (combined_trades_per_month - baseline_trades_per_month) / baseline_trades_per_month
        if baseline_trades_per_month else None
    )
    drawdown_increase_pct = (
        (combined_drawdown_r - baseline_drawdown_r) / baseline_drawdown_r if baseline_drawdown_r else None
    )

    checks = {
        "fallback_only_n_at_least_15": n >= MIN_FALLBACK_ONLY_SUPPORTED_N,
        "fallback_only_expectancy_positive": (fallback_only_exp_r or 0) > 0,
        "fallback_only_pf_above_1": (fallback_only_pf_r or 0) > 1,
        "combined_expectancy_positive": (combined_expectancy_r or 0) > 0,
        "combined_pf_above_1": (combined_pf_r or 0) > 1,
        "combined_r_per_month_beats_baseline": combined_r_per_month > baseline_r_per_month,
        "frequency_gain_at_least_10pct": (frequency_gain_pct is not None and frequency_gain_pct >= MIN_FREQUENCY_GAIN_PCT),
        "drawdown_not_worse_than_20pct": (drawdown_increase_pct is not None and drawdown_increase_pct <= MAX_DRAWDOWN_INCREASE_PCT),
        "fallback_only_expectancy_excl_best_symbol_positive": (fallback_only_expectancy_excl_best_symbol or 0) > 0,
    }

    if all(checks.values()):
        verdict = "SUPPORTED"
    elif checks["fallback_only_expectancy_positive"] and checks["fallback_only_pf_above_1"] and checks["combined_expectancy_positive"] and checks["combined_pf_above_1"]:
        verdict = "MIXED"
    else:
        verdict = "NOT_SUPPORTED"

    return {
        "verdict": verdict, "fallback_only_n": n, "checks": checks,
        "fallback_only_expectancy_r": fallback_only_exp_r, "fallback_only_pf_r": fallback_only_pf_r,
        "combined_expectancy_r": combined_expectancy_r, "combined_pf_r": combined_pf_r,
        "frequency_gain_pct": frequency_gain_pct, "drawdown_increase_pct": drawdown_increase_pct,
    }
