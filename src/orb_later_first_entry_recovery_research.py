"""Research tooling for M45: later-first-entry trade-frequency recovery
(Issue #342).

One material question only: on a day where the frozen
`WAITED_FIRST_VALID_0950` primary produces NO TRADE, does allowing the
first genuinely valid ORB setup that appears strictly after 09:50 through
10:20 ET inclusive recover meaningful trade frequency while preserving
positive trade quality? This is entry-frequency research only -- no
gap/RVOL/universe/exit-rule change, no ranking, no second trade, no
cutoff sweep. `strategy.enabled` remains `false` throughout.

M44's fallback-candidate policy (Issue #340) classified
INSUFFICIENT_EVIDENCE and is NOT reused here -- this module's BASELINE is
the plain, unmodified `WAITED_FIRST_VALID_0950` primary (M43's own
`_waited_shadow_decision`, called exactly once per day, exactly as M43/
M44's BASELINE already does).

Reuse, not reinterpretation:
  * The pre-09:50 candidate pool and primary-selection tie-break come
    from M41's own `early_pool`/`select_first_candidate_per_day`
    (`src.orb_candidate_selection_timing_research`,
    `src.orb_out_of_sample_validation_research`), unmodified.
  * The primary's 09:50 decision-time re-evaluation is M43's own
    `_waited_shadow_decision` (`src.orb_profit_protection_exit_research`),
    called exactly once per day for the primary candidate -- never
    duplicated for the later scan.
  * The one frozen exit arm is M43's own `simulate_exit_arm(..., arm=
    ARM_BE_AFTER_050R)`, unmodified.
  * `index_market_bars_by_date` (M43.5, Issue #338) is reused for the
    date-indexed bar-retrieval path.

Later-window scan: cheap search, expensive resolution only once
------------------------------------------------------------------
M44's own runtime bottleneck came from calling the expensive shadow-
decision-construction path (`build_candidate_shadow_decision`, an
unindexed full-history rescan plus per-bar digest hashing) once per
*attempted* candidate, not just the one ultimately selected. M45
deliberately avoids repeating that: the later-window SEARCH (steps 2-4
below) evaluates every universe symbol at every completed decision
minute strictly after 09:50 through 10:20 ET inclusive using only the
real, unmodified `SignalEngine.evaluate` against already-precomputed
`SessionSignalInputs.window` entries (`precompute_inputs_table` already
computed this for the FULL 09:40-11:00 entry window, for every
(symbol, session_date) pair, whether or not that symbol was ever a
pre-09:50 candidate) -- a pure, cheap function call, no bar rescan, no
shadow-decision construction. Only the ONE candidate the search settles
on (if any) is ever passed to `_waited_shadow_decision` for the
expensive resolution -- bounded at one extra expensive call per
BASELINE-no-trade day, not one per attempted symbol-minute.

No ranking, no "best" candidate
--------------------------------
The later candidate is the first (earliest decision minute, symbol
ascending same-minute tie-break) that independently passes the complete,
fresh `SignalEngine` re-evaluation at its own decision minute -- RVOL,
gap magnitude, and breakout strength are never compared across
candidates for selection purposes. A later candidate may be a stock that
never appeared before 09:50, a stock that failed 09:50 revalidation, or
any other frozen-universe symbol; only that it independently satisfies
the complete, fresh setup at the later minute -- never a revival of
stale pre-09:50 evidence.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Mapping, Sequence

from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    HALF_LIFE_SESSIONS,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    DecisionTimeCandidate,
    early_pool,
    recency_weighted_trade_quality,
)
from src.orb_existing_configs_reevaluation_research import best_contributing_symbol_by_r
from src.orb_gap_rvol_sensitivity_research import CandidateSignal, ComboEvaluation, InputsTable, sensitivity_config
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
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
from src.signal_engine import LiveData, OpeningData, SignalDecision, SignalEngine


class LaterFirstEntryRecoveryError(RuntimeError):
    """Refused: later-first-entry recovery research could not safely proceed."""


ENTRY_BASELINE = ENTRY_WAITED_FIRST_VALID_0950  # "WAITED_FIRST_VALID_0950" -- reused literally, not reproduced
ENTRY_LATER = "LATER_FIRST_VALID_1020"
ENTRY_POLICIES: tuple[str, ...] = (ENTRY_BASELINE, ENTRY_LATER)

EXIT_ARM = ARM_BE_AFTER_050R

#: Frozen before any empirical result was read (Issue #342). Strictly
#: after the established 09:50 primary decision point, through 10:20 ET
#: inclusive -- no sweep, no later cutoff experiment.
LATER_WINDOW_START = time(9, 51)
LATER_WINDOW_END = time(10, 20)


@dataclass(frozen=True)
class LaterCandidateAttempt:
    """The one later-window candidate selected for a day (or `None` if
    none qualified). `decision_timestamp` is both the qualifying and the
    decision instant -- a later candidate is a NEW, freshly-evaluated
    setup, not a stale pre-09:50 signal replayed with a later clock."""
    symbol: str
    decision_timestamp: datetime
    opening_data: OpeningData
    live_data: LiveData
    decision: SignalDecision


@dataclass(frozen=True)
class DayOutcome:
    session_date: date
    baseline_attempted: bool  # True iff a pre-09:50 candidate existed to evaluate at all
    baseline_shadow_decision: Any
    baseline_subsequent_bars: tuple[MarketBar, ...]
    baseline_approved: bool | None
    baseline_candidate_symbol: str | None
    later_scanned: bool  # True iff the later window was ever opened (baseline had no trade)
    later: LaterCandidateAttempt | None
    later_shadow_decision: Any
    later_subsequent_bars: tuple[MarketBar, ...]

    @property
    def baseline_traded(self) -> bool:
        return bool(self.baseline_approved)

    @property
    def later_traded(self) -> bool:
        return self.later is not None


def _later_window_candidates(
    *, universe: Sequence[str], session_date: date, inputs_table: InputsTable,
) -> list[tuple[datetime, str, OpeningData, LiveData]]:
    """Every (timestamp, symbol, opening_data, live_data) tuple across the
    whole frozen universe whose decision timestamp (ET) falls strictly
    after 09:50 through 10:20 inclusive for `session_date`, sorted by
    (timestamp, symbol) ascending -- the exact two-level order ("earliest
    valid decision timestamp, symbol ascending tie-break") the frozen
    selection rule requires. Drawn entirely from `precompute_inputs_table`'s
    already-computed `SessionSignalInputs.window` (the full 09:40-11:00
    entry window, computed once per (symbol, session_date) regardless of
    whether that symbol was ever a pre-09:50 candidate) -- no new bar
    scan, no re-interpretation of bar timestamps."""
    out: list[tuple[datetime, str, OpeningData, LiveData]] = []
    for symbol in universe:
        inputs = inputs_table.get((symbol, session_date))
        if inputs is None or inputs.opening_data is None:
            continue
        for _bar, live_data in inputs.window:
            t = live_data.timestamp.astimezone(EASTERN).time()
            if LATER_WINDOW_START <= t <= LATER_WINDOW_END:
                out.append((live_data.timestamp, symbol, inputs.opening_data, live_data))
    out.sort(key=lambda row: (row[0], row[1]))
    return out


def _scan_later_window(
    *, universe: Sequence[str], session_date: date, inputs_table: InputsTable, engine: SignalEngine,
) -> LaterCandidateAttempt | None:
    """Walk the later-window candidates in frozen (timestamp, symbol)
    order, re-evaluating each independently with the real, unmodified
    `SignalEngine.evaluate` against its own fresh decision-time data
    (never a stale/earlier value), returning the first approved -- or
    `None` if none qualify by 10:20. Pure search only: never constructs a
    shadow decision or touches market-data bars directly."""
    for timestamp, symbol, opening_data, live_data in _later_window_candidates(
        universe=universe, session_date=session_date, inputs_table=inputs_table,
    ):
        decision = engine.evaluate(opening_data, live_data)
        if decision.approved:
            return LaterCandidateAttempt(
                symbol=symbol, decision_timestamp=timestamp, opening_data=opening_data,
                live_data=live_data, decision=decision,
            )
    return None


def build_day_outcomes(
    *, period: str, evaluation: ComboEvaluation, inputs_table: InputsTable, session_dates: Sequence[date],
    universe: Sequence[str], market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], base_config: Mapping[str, Any],
    implementation_digest: str, created_at: datetime, session_bounds_provider=None,
) -> tuple[DayOutcome, ...]:
    """One `DayOutcome` per `session_dates` entry (every session in the
    period, not just days with a pre-09:50 candidate -- BASELINE-no-trade
    includes both "primary failed 09:50" and "no pre-09:50 candidate at
    all"). BASELINE's own primary is evaluated exactly once via M43's own
    `_waited_shadow_decision`; the later window is only ever opened, and
    only ever resolved to one expensive shadow-decision call, on a day
    BASELINE did not trade."""
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise LaterFirstEntryRecoveryError("build_day_outcomes requires the frozen GAP_050_RVOL_175 combo")

    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    evaluation_config = copy.deepcopy(dict(combo_config))
    evaluation_config["strategy"]["enabled"] = True  # in-memory only; never persisted
    engine = SignalEngine(evaluation_config)

    market_bars_index_by_symbol = {
        symbol: index_market_bars_by_date(bars) for symbol, bars in market_bars_by_symbol.items()
    }

    from src.orb_candidate_selection_timing_research import enrich_with_decision_time
    early_winners = select_first_candidate_per_day(early_pool(evaluation.candidate_signals))
    baseline_candidate_by_day: dict[date, DecisionTimeCandidate] = {
        c.session_date: c for c in (enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)
    }

    outcomes: list[DayOutcome] = []
    for session_date in session_dates:
        baseline_candidate = baseline_candidate_by_day.get(session_date)
        if baseline_candidate is not None:
            b_shadow, b_bars, b_approved = _waited_shadow_decision(
                baseline_candidate, combo_config=combo_config, strategy_version=strategy_version,
                implementation_digest=implementation_digest, shadow_run_id=f"m45-{period}-{session_date.isoformat()}-baseline",
                created_at=created_at, market_bars_by_symbol=market_bars_by_symbol,
                market_bars_index_by_symbol=market_bars_index_by_symbol,
            )
            baseline_symbol = baseline_candidate.symbol
            baseline_attempted = True
        else:
            b_shadow, b_bars, b_approved = None, [], None
            baseline_symbol = None
            baseline_attempted = False

        later_attempt: LaterCandidateAttempt | None = None
        l_shadow, l_bars = None, []
        later_scanned = not b_approved
        if later_scanned:
            later_attempt = _scan_later_window(universe=universe, session_date=session_date, inputs_table=inputs_table, engine=engine)
            if later_attempt is not None:
                later_wrapped = DecisionTimeCandidate(
                    symbol=later_attempt.symbol, session_date=session_date, opening_data=later_attempt.opening_data,
                    original_live_data=later_attempt.live_data, original_decision=later_attempt.decision,
                    decision_bar=None, decision_live_data=later_attempt.live_data,
                )
                l_shadow, l_bars, _l_approved = _waited_shadow_decision(
                    later_wrapped, combo_config=combo_config, strategy_version=strategy_version,
                    implementation_digest=implementation_digest, shadow_run_id=f"m45-{period}-{session_date.isoformat()}-later-{later_attempt.symbol}",
                    created_at=created_at, market_bars_by_symbol=market_bars_by_symbol,
                    market_bars_index_by_symbol=market_bars_index_by_symbol,
                )

        outcomes.append(DayOutcome(
            session_date=session_date, baseline_attempted=baseline_attempted, baseline_shadow_decision=b_shadow,
            baseline_subsequent_bars=tuple(b_bars), baseline_approved=b_approved, baseline_candidate_symbol=baseline_symbol,
            later_scanned=later_scanned, later=later_attempt, later_shadow_decision=l_shadow, later_subsequent_bars=tuple(l_bars),
        ))
    return tuple(outcomes)


def _row_for_baseline(day: DayOutcome, *, period: str, session_bounds_provider=None) -> TradeDiagnosticRow | None:
    if not day.baseline_attempted:
        return None
    features = _decision_features(
        period=period, entry_policy=ENTRY_BASELINE, symbol=day.baseline_candidate_symbol,
        session_date=day.session_date, original_timestamp=day.baseline_shadow_decision.signal_timestamp if day.baseline_shadow_decision else day.session_date,
    )
    features["wait_reevaluation_approved"] = day.baseline_approved
    if day.baseline_shadow_decision is None:
        return TradeDiagnosticRow(decision=features, outcome=_no_decision_time_data_outcome())
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    outcome = simulate_exit_arm(day.baseline_shadow_decision, day.baseline_subsequent_bars, arm=EXIT_ARM, **kwargs)
    return TradeDiagnosticRow(decision=features, outcome=_outcome_dict(outcome))


def _row_for_later_or_baseline(day: DayOutcome, *, period: str, session_bounds_provider=None) -> TradeDiagnosticRow | None:
    """LATER policy's own row for the day: the baseline row if baseline
    traded (LATER never scans), the later-candidate row if a later
    candidate was found, or `None` if the day had neither (no pre-09:50
    candidate AND nothing valid through 10:20 -- genuinely no trade, not
    fabricated as one)."""
    if day.baseline_approved:
        return _row_for_baseline(day, period=period, session_bounds_provider=session_bounds_provider)
    if day.later is None:
        return None
    features = _decision_features(
        period=period, entry_policy=ENTRY_LATER, symbol=day.later.symbol,
        session_date=day.session_date, original_timestamp=day.later.decision_timestamp,
    )
    features["wait_reevaluation_approved"] = True
    features["later_decision_timestamp"] = day.later.decision_timestamp.isoformat()
    if day.later_shadow_decision is None:
        return TradeDiagnosticRow(decision=features, outcome=_no_decision_time_data_outcome())
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    outcome = simulate_exit_arm(day.later_shadow_decision, day.later_subsequent_bars, arm=EXIT_ARM, **kwargs)
    return TradeDiagnosticRow(decision=features, outcome=_outcome_dict(outcome))


def build_policy_rows(day_outcomes: Sequence[DayOutcome], *, period: str, session_bounds_provider=None) -> dict[str, tuple[TradeDiagnosticRow, ...]]:
    baseline_rows = tuple(
        row for row in (
            _row_for_baseline(d, period=period, session_bounds_provider=session_bounds_provider) for d in day_outcomes
        ) if row is not None
    )
    later_rows = tuple(
        row for row in (
            _row_for_later_or_baseline(d, period=period, session_bounds_provider=session_bounds_provider) for d in day_outcomes
        ) if row is not None
    )
    return {ENTRY_BASELINE: baseline_rows, ENTRY_LATER: later_rows}


def later_only_rows(day_outcomes: Sequence[DayOutcome], *, period: str, session_bounds_provider=None) -> tuple[TradeDiagnosticRow, ...]:
    """Rows added ONLY by the later-entry mechanism -- baseline had no
    trade, and the later-window scan found a qualifying candidate."""
    return tuple(
        row for row in (
            _row_for_later_or_baseline(d, period=period, session_bounds_provider=session_bounds_provider)
            for d in day_outcomes if (not d.baseline_approved) and d.later is not None
        ) if row is not None
    )


# ---------------------------------------------------------------------------
# Recovery / timing diagnostics.

def _bucket_for(t: time) -> str:
    if t <= time(10, 0):
        return "09:51-10:00"
    if t <= time(10, 10):
        return "10:01-10:10"
    return "10:11-10:20"


def recovery_diagnostics(day_outcomes: Sequence[DayOutcome], *, session_bounds_provider=None) -> dict[str, Any]:
    """`session_bounds_provider` must match whatever provider the period's
    own `day_outcomes` were built with (research-calendar periods need
    their own non-default provider) -- passed through unchanged to
    `simulate_exit_arm`, exactly as `build_policy_rows`/`later_only_rows`
    already require. Pooling `day_outcomes` across periods that need
    different providers is only safe when this function -- like those --
    is called once per period with that period's own provider, and the
    resulting counts summed by the caller."""
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    total_sessions = len(day_outcomes)
    baseline_trade_days = sum(1 for d in day_outcomes if d.baseline_approved)
    baseline_no_trade_days = total_sessions - baseline_trade_days
    no_trade_with_later_valid = sum(1 for d in day_outcomes if (not d.baseline_approved) and d.later is not None)
    later_selections = no_trade_with_later_valid
    later_closed = 0
    for d in day_outcomes:
        if (not d.baseline_approved) and d.later is not None and d.later_shadow_decision is not None:
            outcome = simulate_exit_arm(d.later_shadow_decision, d.later_subsequent_bars, arm=EXIT_ARM, **kwargs)
            if outcome.realized_pnl is not None:
                later_closed += 1
    later_data_invalid = later_selections - later_closed
    recovery_rate = (no_trade_with_later_valid / baseline_no_trade_days) if baseline_no_trade_days else None

    buckets = {"09:51-10:00": 0, "10:01-10:10": 0, "10:11-10:20": 0}
    times: list[time] = []
    for d in day_outcomes:
        if d.later is not None:
            t = d.later.decision_timestamp.astimezone(EASTERN).time()
            buckets[_bucket_for(t)] += 1
            times.append(t)
    median_time = sorted(times)[len(times) // 2] if times else None
    earliest_time = min(times) if times else None
    latest_time = max(times) if times else None

    baseline_trades_per_month = baseline_trade_days / (total_sessions / 21.0) if total_sessions else None
    combined_trade_days = sum(1 for d in day_outcomes if d.baseline_approved or d.later is not None)
    combined_trades_per_month = combined_trade_days / (total_sessions / 21.0) if total_sessions else None
    pct_increase_trades_per_month = (
        (combined_trades_per_month - baseline_trades_per_month) / baseline_trades_per_month
        if baseline_trades_per_month else None
    )
    pct_increase_trade_days = (
        (combined_trade_days - baseline_trade_days) / baseline_trade_days if baseline_trade_days else None
    )

    return {
        "total_trading_sessions": total_sessions,
        "baseline_trade_days": baseline_trade_days,
        "baseline_no_trade_days": baseline_no_trade_days,
        "no_trade_days_with_later_valid_setup": no_trade_with_later_valid,
        "later_selections": later_selections,
        "later_closed_trades": later_closed,
        "later_data_invalid_outcomes": later_data_invalid,
        "recovery_rate_among_baseline_no_trade_days": recovery_rate,
        "timing_buckets": buckets,
        "median_later_decision_time": median_time.isoformat() if median_time else None,
        "earliest_later_decision_time": earliest_time.isoformat() if earliest_time else None,
        "latest_later_decision_time": latest_time.isoformat() if latest_time else None,
        "percentage_increase_trades_per_month": pct_increase_trades_per_month,
        "percentage_increase_trade_days": pct_increase_trade_days,
    }


# ---------------------------------------------------------------------------
# Predeclared classification (frozen before any empirical M45 result is
# read -- see docs/research/ORB_LATER_FIRST_ENTRY_RECOVERY_EXPERIMENT.md).

MIN_LATER_ONLY_SUPPORTED_N = 30
MIN_LATER_ONLY_INSUFFICIENT_N = 15
MIN_FREQUENCY_GAIN_PCT = 0.25
MIN_COMBINED_EXPECTANCY_RATIO = 0.80
MAX_DRAWDOWN_RATIO = 1.25


def classify_later_first_entry_recovery(
    *, later_only_quality: Mapping[str, Any], later_only_expectancy_excl_best_symbol: float | None,
    combined_expectancy_r: float | None, combined_pf_r: float | None,
    combined_r_per_month: float, baseline_r_per_month: float,
    combined_trades_per_month: float, baseline_trades_per_month: float,
    baseline_expectancy_r: float, combined_drawdown_r: float, baseline_drawdown_r: float,
) -> dict[str, Any]:
    n = later_only_quality["closed_trades"]
    if n < MIN_LATER_ONLY_INSUFFICIENT_N:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"later-only closed trades={n} below {MIN_LATER_ONLY_INSUFFICIENT_N}", "later_only_n": n}

    later_only_exp_r = later_only_quality["expectancy_r_per_trade"]
    later_only_pf_r = later_only_quality["profit_factor_r"]

    frequency_gain_pct = (
        (combined_trades_per_month - baseline_trades_per_month) / baseline_trades_per_month
        if baseline_trades_per_month else None
    )
    expectancy_ratio = (combined_expectancy_r / baseline_expectancy_r) if baseline_expectancy_r else None
    drawdown_ratio = (combined_drawdown_r / baseline_drawdown_r) if baseline_drawdown_r else None

    checks = {
        "later_only_n_at_least_30": n >= MIN_LATER_ONLY_SUPPORTED_N,
        "later_only_expectancy_positive": (later_only_exp_r or 0) > 0,
        "later_only_pf_above_1": (later_only_pf_r or 0) > 1,
        "combined_expectancy_positive": (combined_expectancy_r or 0) > 0,
        "combined_pf_above_1": (combined_pf_r or 0) > 1,
        "frequency_gain_at_least_25pct": (frequency_gain_pct is not None and frequency_gain_pct >= MIN_FREQUENCY_GAIN_PCT),
        "combined_r_per_month_beats_baseline": combined_r_per_month > baseline_r_per_month,
        "combined_expectancy_at_least_80pct_baseline": (expectancy_ratio is not None and expectancy_ratio >= MIN_COMBINED_EXPECTANCY_RATIO),
        "drawdown_not_worse_than_125pct": (drawdown_ratio is not None and drawdown_ratio <= MAX_DRAWDOWN_RATIO),
        "later_only_expectancy_excl_best_symbol_positive": (later_only_expectancy_excl_best_symbol or 0) > 0,
    }

    if all(checks.values()):
        verdict = "SUPPORTED"
    elif checks["later_only_expectancy_positive"] and checks["later_only_pf_above_1"] and checks["combined_expectancy_positive"] and checks["combined_pf_above_1"]:
        verdict = "MIXED"
    else:
        verdict = "NOT_SUPPORTED"

    return {
        "verdict": verdict, "later_only_n": n, "checks": checks,
        "later_only_expectancy_r": later_only_exp_r, "later_only_pf_r": later_only_pf_r,
        "combined_expectancy_r": combined_expectancy_r, "combined_pf_r": combined_pf_r,
        "frequency_gain_pct": frequency_gain_pct, "expectancy_ratio": expectancy_ratio, "drawdown_ratio": drawdown_ratio,
    }


def symbol_robustness(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    best_symbol = best_contributing_symbol_by_r(rows)
    per_symbol = symbol_summary(rows)["per_symbol"]
    excl_best = excluding_symbol(rows, symbol=best_symbol) if best_symbol else trade_quality_from_rows(rows)
    excl_aapl = excluding_symbol(rows, symbol="AAPL")
    excl_tsla = excluding_symbol(rows, symbol="TSLA")
    top3 = sorted(per_symbol, key=lambda s: per_symbol[s].get("cumulative_r") or 0, reverse=True)[:3]
    total_r = trade_quality_from_rows(rows)["cumulative_r"]
    top3_share = None
    if total_r:
        top3_r = sum((per_symbol[s].get("cumulative_r") or 0) for s in top3)
        top3_share = top3_r / total_r
    best_contribution_share = None
    if best_symbol and total_r:
        best_contribution_share = (per_symbol[best_symbol].get("cumulative_r") or 0) / total_r
    return {
        "symbol_counts": {s: v["closed_trades"] for s, v in per_symbol.items()},
        "cumulative_r_by_symbol": {s: v.get("cumulative_r") for s, v in per_symbol.items()},
        "best_r_contributor": best_symbol, "best_r_contribution_share": best_contribution_share,
        "expectancy_r_excluding_best_contributor": excl_best.get("expectancy_r_per_trade"),
        "expectancy_r_excluding_aapl": excl_aapl.get("expectancy_r_per_trade"),
        "expectancy_r_excluding_tsla": excl_tsla.get("expectancy_r_per_trade"),
        "top3_r_contributors": top3, "top3_share_of_cumulative_r": top3_share,
    }
