"""M47: bounded sequential second-trade large-cap ORB feasibility research.

Research question: on a day where the supported `WAITED_FIRST_VALID_0950`
policy enters and fully closes its first trade early enough, can one
additional, independently valid, non-overlapping large-cap ORB trade
materially improve frequency and R/month without destroying the
baseline's positive trade quality or materially worsening drawdown?

`SEQUENTIAL_SECOND_VALID_1020`:
  1. The BASELINE first-entry/exit lifecycle (`GAP_050_RVOL_175` +
     `WAITED_FIRST_VALID_0950` + `BE_AFTER_050R`) runs completely
     unmodified -- this module reuses
     `src.orb_profit_protection_exit_research._waited_shadow_decision`
     and `simulate_exit_arm` directly rather than re-deriving baseline
     behavior.
  2. A second trade is eligible only when the first trade actually
     entered, actually closed, and closed strictly before 10:20 ET.
  3. The scan window is (first-trade exit timestamp, 10:20 ET] --
     strictly after exit, inclusive of 10:20.
  4. Every OTHER universe symbol (the first-trade symbol excluded) is
     evaluated FRESH via the real, unmodified `SignalEngine` at every
     completed minute in the scan window, reusing each symbol's already
     -precomputed `SessionSignalInputs.window`
     (`src.orb_universe_expansion_research.precompute_session_signal_inputs`,
     driven once per (symbol, session_date) by
     `orb_gap_rvol_sensitivity_research.precompute_inputs_table` for the
     whole universe) -- no candidate's gap/RVOL/breakout/approval is ever
     carried forward from an earlier minute. `SignalEngine.evaluate`
     rejects any symbol outside `config["universe"]["symbols"]`
     regardless of gap/RVOL qualification, and several periods' frozen
     native universes (e.g. BAC/INTC for P_MINUS_2/P_MINUS_1/P1, plus
     CSCO/KO/NKE/VZ/T for P2 and more for P3) contain symbols outside the
     tracked *production* 15-symbol universe -- so the second-trade scan's
     own `SignalEngine` is built from `research_config_for_universe`
     (M46's helper, reused unmodified) against the exact `universe`
     parameter already passed to `scan_for_second_candidate`, never the
     production default. This is scoped to the second-trade scan only:
     the BASELINE trade's own 09:50 `_waited_shadow_decision` re-check is
     untouched, reusing the exact same frozen behavior M43/M45 already
     validated (changing it would not be an additive M47 change but a
     retroactive edit to already-supported/frozen baseline behavior).
  5. The earliest minute with at least one independently valid symbol
     wins; same-minute ties break by ticker ascending. No ranking by
     RVOL, gap, strength, sector, or expected return.
  6. The winning candidate is wrapped into a `ShadowTradingDecision` via
     the unmodified `build_candidate_shadow_decision` and simulated
     through the unmodified `simulate_exit_arm(..., arm=ARM_BE_AFTER_050R)`
     -- identical entry-fill and exit mechanics as the first trade.

Maximum two entries/day, maximum one open position at any instant (the
second trade cannot begin scanning until the first has fully closed), no
third trade, no same-symbol re-entry, no simultaneous positions, no
portfolio/capital modeling. Not M45's later-first-entry recovery (which
replaces the FIRST trade's timing), not M46's small/mid-cap lane, not
same-symbol re-entry research.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Mapping, Sequence

from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar
from src.orb_candidate_selection_timing_research import DecisionTimeCandidate
from src.orb_existing_configs_reevaluation_research import best_contributing_symbol_by_r
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_gap_rvol_sensitivity_research import CandidateSignal, InputsTable, build_candidate_shadow_decision
from src.orb_small_midcap_momentum_research import research_config_for_universe
from src.orb_profit_protection_exit_research import (
    ARM_BE_AFTER_050R,
    IndexedMarketBars,
    _session_bars,
    _waited_shadow_decision,
    _outcome_dict,
    max_drawdown_r_and_streak_from_rows,
    simulate_exit_arm,
)
from src.orb_regime_diagnostic_research import TradeDiagnosticRow, trade_quality_from_rows
from src.signal_engine import SignalEngine

POLICY_SEQUENTIAL_SECOND_VALID_1020 = "SEQUENTIAL_SECOND_VALID_1020"
SECOND_SCAN_END_ET = time(10, 20)


class SequentialSecondTradeError(RuntimeError):
    """Refused: M47 sequential second-trade research tooling could not
    safely proceed."""


# --------------------------------------------------------------------------
# Second-trade candidate scan (the genuinely new mechanism -- everything it
# calls is unmodified, reused machinery).
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class SecondScanResult:
    """`status` is one of: `NOT_ELIGIBLE` (first trade did not close before
    10:20, or never closed), `NO_VALID_CANDIDATE` (scan ran, nothing
    qualified), `SELECTED` (a winning candidate was found).
    `evaluation_universe` is an audit field: the exact `universe.symbols`
    the scan's own `SignalEngine` was built with, always equal to the
    `universe` argument this scan was called with -- lets a caller/test
    assert requested-universe == evaluation-config-universe directly off
    the result, per period, without re-deriving it."""

    status: str
    scan_start: datetime | None = None
    scan_end_et: time = SECOND_SCAN_END_ET
    selected_symbol: str | None = None
    selected_timestamp: datetime | None = None
    candidate_signal: CandidateSignal | None = None
    tied_symbols: tuple[str, ...] = ()
    evaluation_universe: tuple[str, ...] = ()


def second_trade_eligibility(*, first_entered: bool, first_closed: bool, first_exit_timestamp: datetime | None) -> tuple[bool, str | None]:
    """Pure eligibility check, isolated for direct testing. Returns
    `(eligible, reason_if_not)`."""
    if not first_entered:
        return False, "FIRST_TRADE_DID_NOT_ENTER"
    if not first_closed or first_exit_timestamp is None:
        return False, "FIRST_TRADE_NOT_CLOSED_OR_UNTRUSTWORTHY_TIMESTAMP"
    if first_exit_timestamp.astimezone(EASTERN).time() >= SECOND_SCAN_END_ET:
        return False, "FIRST_TRADE_CLOSED_AT_OR_AFTER_SCAN_END"
    return True, None


def scan_for_second_candidate(
    *, session_date: date, first_trade_symbol: str, universe: Sequence[str],
    scan_start_exclusive: datetime, inputs_table: InputsTable, combo_config: Mapping[str, Any],
) -> SecondScanResult:
    """Walk every completed minute strictly after `scan_start_exclusive`
    through `SECOND_SCAN_END_ET` (ET, inclusive), evaluating every OTHER
    universe symbol fresh via `SignalEngine` at that exact minute using
    that symbol's own already-precomputed window entry for that minute
    (never an earlier minute's decision). The earliest minute with >=1
    approved symbol wins; ties at that minute break by ticker ascending.
    Deterministic and side-effect-free -- suitable for repeated calls."""
    evaluation_universe = tuple(universe)
    other_symbols = sorted(s for s in universe if s != first_trade_symbol)
    if not other_symbols:
        return SecondScanResult(status="NO_VALID_CANDIDATE", scan_start=scan_start_exclusive, evaluation_universe=evaluation_universe)

    # The exact frozen native universe for this period, not the tracked
    # production default -- several periods' native universes contain
    # symbols (BAC/INTC/CSCO/KO/NKE/VZ/T/... per period) outside the
    # production 15-symbol list, which `SignalEngine.evaluate` would
    # otherwise silently reject before gap/RVOL is ever checked.
    evaluation_config = research_config_for_universe(combo_config, universe)
    evaluation_config["strategy"]["enabled"] = True  # in-memory only; never persisted
    assert evaluation_config["universe"]["symbols"] == list(universe), "second-trade scan universe mismatch"
    engine = SignalEngine(evaluation_config)

    # Collect every (timestamp, symbol) pair in the scan window across all
    # other symbols, each paired with its own bar/live_data/opening_data.
    by_timestamp: dict[datetime, list[tuple[str, Any, Any]]] = {}
    for symbol in other_symbols:
        inputs = inputs_table.get((symbol, session_date))
        if inputs is None or inputs.incomplete_reason is not None:
            continue
        for _bar, live_data in inputs.window:
            ts = live_data.timestamp
            if ts <= scan_start_exclusive:
                continue
            if ts.astimezone(EASTERN).time() > SECOND_SCAN_END_ET:
                continue
            by_timestamp.setdefault(ts, []).append((symbol, inputs.opening_data, live_data))

    for ts in sorted(by_timestamp):
        approved: list[tuple[str, Any, Any]] = []
        for symbol, opening_data, live_data in by_timestamp[ts]:
            decision = engine.evaluate(opening_data, live_data)
            if decision.approved:
                approved.append((symbol, opening_data, live_data, decision))
        if not approved:
            continue
        approved.sort(key=lambda t: t[0])
        winner_symbol, winner_opening, winner_live, winner_decision = approved[0]
        candidate_signal: CandidateSignal = (winner_symbol, session_date, winner_opening, winner_live, winner_decision)
        return SecondScanResult(
            status="SELECTED", scan_start=scan_start_exclusive, selected_symbol=winner_symbol,
            selected_timestamp=ts, candidate_signal=candidate_signal,
            tied_symbols=tuple(a[0] for a in approved), evaluation_universe=evaluation_universe,
        )

    return SecondScanResult(status="NO_VALID_CANDIDATE", scan_start=scan_start_exclusive, evaluation_universe=evaluation_universe)


def build_second_shadow_outcome(
    *, candidate_signal: CandidateSignal, combo_config: Mapping[str, Any], strategy_version: str,
    implementation_digest: str, shadow_run_id: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    market_bars_index_by_symbol: Mapping[str, IndexedMarketBars] | None = None,
    session_bounds_provider=None,
):
    """Wrap the winning second-trade candidate in a `ShadowTradingDecision`
    (unmodified `build_candidate_shadow_decision`) and simulate it through
    the unmodified `BE_AFTER_050R` exit arm -- identical mechanics to the
    first trade. Returns the `ExitArmOutcome`."""
    symbol, session_date, opening_data, live_data, decision = candidate_signal
    candidate_bars = market_bars_by_symbol[symbol]
    spy_bars = market_bars_by_symbol["SPY"]
    shadow_decision = build_candidate_shadow_decision(
        symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
        decision=decision, combo_config=combo_config, strategy_version=strategy_version,
        implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
        candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
    )
    index = None if market_bars_index_by_symbol is None else market_bars_index_by_symbol.get(symbol)
    session_bars = _session_bars(candidate_bars, session_date=session_date, index=index)
    subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    return simulate_exit_arm(shadow_decision, subsequent_bars, arm=ARM_BE_AFTER_050R, **kwargs)


# --------------------------------------------------------------------------
# Day orchestration.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DayResult:
    session_date: date
    period: str
    baseline_symbol: str | None
    baseline_row: TradeDiagnosticRow | None
    second_eligible: bool
    second_ineligibility_reason: str | None
    second_scan: SecondScanResult | None
    second_row: TradeDiagnosticRow | None
    minutes_between_first_exit_and_second_decision: float | None


def build_day_results(
    *, period: str, waited_baseline_candidates: Sequence[DecisionTimeCandidate], universe: Sequence[str],
    inputs_table: InputsTable, combo_config: Mapping[str, Any], strategy_version: str,
    implementation_digest: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    market_bars_index_by_symbol: Mapping[str, IndexedMarketBars] | None = None,
    session_bounds_provider=None,
) -> tuple[DayResult, ...]:
    """`waited_baseline_candidates` is exactly M43/M45's own
    `enrich_with_decision_time`-enriched, `select_first_candidate_per_day`
    -selected WAITED_FIRST_VALID_0950 candidate list -- one entry per
    baseline trade day, unmodified. For each day: replay the baseline
    trade exactly (`_waited_shadow_decision` + `simulate_exit_arm`), check
    second-trade eligibility from its own real entry/exit timestamps, and
    -- only if eligible -- scan for and simulate a second trade."""
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    results: list[DayResult] = []

    for candidate in waited_baseline_candidates:
        shadow_run_id = f"m47-{period}-{POLICY_SEQUENTIAL_SECOND_VALID_1020}-baseline"
        shadow_decision, subsequent_bars, wait_approved = _waited_shadow_decision(
            candidate, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, market_bars_index_by_symbol=market_bars_index_by_symbol,
        )
        if shadow_decision is None:
            results.append(DayResult(
                session_date=candidate.session_date, period=period, baseline_symbol=candidate.symbol,
                baseline_row=None, second_eligible=False,
                second_ineligibility_reason="FIRST_TRADE_NO_DECISION_TIME_DATA",
                second_scan=None, second_row=None, minutes_between_first_exit_and_second_decision=None,
            ))
            continue

        baseline_outcome = simulate_exit_arm(shadow_decision, subsequent_bars, arm=ARM_BE_AFTER_050R, **kwargs)
        baseline_outcome_dict = _outcome_dict(baseline_outcome)
        baseline_decision_features = {
            "period": period, "symbol": candidate.symbol, "session_date": candidate.session_date.isoformat(),
            "policy": POLICY_SEQUENTIAL_SECOND_VALID_1020, "leg": "BASELINE",
            "wait_reevaluation_approved": wait_approved,
        }
        baseline_row = TradeDiagnosticRow(decision=baseline_decision_features, outcome=baseline_outcome_dict)

        first_exit_ts = baseline_outcome.exit_timestamp
        eligible, reason = second_trade_eligibility(
            first_entered=baseline_outcome_dict["entered"], first_closed=baseline_outcome_dict["closed"],
            first_exit_timestamp=first_exit_ts,
        )

        if not eligible:
            results.append(DayResult(
                session_date=candidate.session_date, period=period, baseline_symbol=candidate.symbol,
                baseline_row=baseline_row, second_eligible=False, second_ineligibility_reason=reason,
                second_scan=None, second_row=None, minutes_between_first_exit_and_second_decision=None,
            ))
            continue

        scan = scan_for_second_candidate(
            session_date=candidate.session_date, first_trade_symbol=candidate.symbol, universe=universe,
            scan_start_exclusive=first_exit_ts, inputs_table=inputs_table, combo_config=combo_config,
        )

        second_row = None
        minutes_between = None
        if scan.status == "SELECTED" and scan.candidate_signal is not None:
            second_shadow_run_id = f"m47-{period}-{POLICY_SEQUENTIAL_SECOND_VALID_1020}-second"
            second_outcome = build_second_shadow_outcome(
                candidate_signal=scan.candidate_signal, combo_config=combo_config, strategy_version=strategy_version,
                implementation_digest=implementation_digest, shadow_run_id=second_shadow_run_id, created_at=created_at,
                market_bars_by_symbol=market_bars_by_symbol, market_bars_index_by_symbol=market_bars_index_by_symbol,
                session_bounds_provider=session_bounds_provider,
            )
            second_outcome_dict = _outcome_dict(second_outcome)
            second_decision_features = {
                "period": period, "symbol": scan.selected_symbol, "session_date": candidate.session_date.isoformat(),
                "policy": POLICY_SEQUENTIAL_SECOND_VALID_1020, "leg": "SECOND",
                "first_trade_symbol": candidate.symbol, "tied_symbols_at_decision": list(scan.tied_symbols),
            }
            second_row = TradeDiagnosticRow(decision=second_decision_features, outcome=second_outcome_dict)
            minutes_between = (scan.selected_timestamp - first_exit_ts).total_seconds() / 60.0

        results.append(DayResult(
            session_date=candidate.session_date, period=period, baseline_symbol=candidate.symbol,
            baseline_row=baseline_row, second_eligible=True, second_ineligibility_reason=None,
            second_scan=scan, second_row=second_row,
            minutes_between_first_exit_and_second_decision=minutes_between,
        ))

    return tuple(results)


def baseline_rows(day_results: Sequence[DayResult]) -> tuple[TradeDiagnosticRow, ...]:
    return tuple(d.baseline_row for d in day_results if d.baseline_row is not None)


def second_only_rows(day_results: Sequence[DayResult]) -> tuple[TradeDiagnosticRow, ...]:
    return tuple(d.second_row for d in day_results if d.second_row is not None)


def combined_rows(day_results: Sequence[DayResult]) -> tuple[TradeDiagnosticRow, ...]:
    """BASELINE first trades plus eligible SECOND trades, pooled into one
    view -- never combined with M45 later-first trades or M46 small/mid
    trades."""
    out: list[TradeDiagnosticRow] = []
    for d in day_results:
        if d.baseline_row is not None:
            out.append(d.baseline_row)
        if d.second_row is not None:
            out.append(d.second_row)
    return tuple(out)


# --------------------------------------------------------------------------
# Predeclared classification (frozen before any empirical M47 result is
# read -- see docs/research/ORB_SEQUENTIAL_SECOND_TRADE_FEASIBILITY.md and
# Issue #346). Numeric thresholds match M45's adapted ten-condition gate;
# the sample-size gate is a single threshold (not M45's two-tier 15/30),
# per M47's own frozen protocol: fewer than 30 genuinely closed SECOND_ONLY
# trades is INSUFFICIENT_EVIDENCE outright.

MIN_SECOND_ONLY_SUPPORTED_N = 30
MIN_FREQUENCY_GAIN_PCT = 0.25
MIN_COMBINED_EXPECTANCY_RATIO = 0.80
MAX_DRAWDOWN_RATIO = 1.25


def second_only_symbol_robustness(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Best-symbol concentration using each symbol's own NET cumulative R
    (not individual-trade positive R) as the basis: sum each symbol's
    closed-trade `r_multiple` values into one net figure per symbol, keep
    only symbols whose net is positive, and divide the largest positive
    net symbol contribution by the sum of all positive net symbol
    contributions. A symbol with, say, three winners and two losers that
    nets negative contributes nothing to either the numerator or the
    denominator -- summing each trade's own `max(r, 0)` instead (the
    prior, incorrect implementation) would count that symbol's winning
    trades toward the denominator even though the symbol's own net
    contribution is negative, understating concentration. Matches M46's
    own convention. Also returns expectancy excluding the best
    R-contributing SECOND_ONLY symbol."""
    if not rows:
        return {
            "best_r_contributor": None, "best_r_contribution_share": None,
            "expectancy_r_excluding_best_contributor": None,
        }
    best_symbol = best_contributing_symbol_by_r(rows)
    tq = trade_quality_from_rows(rows)
    per_symbol_net_r: dict[str, float] = {}
    for r in rows:
        if r.outcome["closed"] and r.outcome["r_multiple"] is not None:
            per_symbol_net_r[r.decision["symbol"]] = per_symbol_net_r.get(r.decision["symbol"], 0.0) + r.outcome["r_multiple"]
    positive_net_total_r = sum(v for v in per_symbol_net_r.values() if v > 0)
    best_contribution_share = None
    if best_symbol and positive_net_total_r and per_symbol_net_r.get(best_symbol, 0.0) > 0:
        best_contribution_share = per_symbol_net_r[best_symbol] / positive_net_total_r
    excl_best = excluding_symbol(rows, symbol=best_symbol) if best_symbol else tq
    return {
        "best_r_contributor": best_symbol, "best_r_contribution_share": best_contribution_share,
        "expectancy_r_excluding_best_contributor": excl_best.get("expectancy_r_per_trade"),
    }


def classify_sequential_second_trade(
    *, second_only_rows: Sequence[TradeDiagnosticRow], combined_rows: Sequence[TradeDiagnosticRow],
    baseline_rows: Sequence[TradeDiagnosticRow], total_sessions: int,
) -> dict[str, Any]:
    """Applies the frozen M47 ten-condition gate. `total_sessions` is the
    pooled session count used for the trades/month and R/month
    denominators (`sessions / 21.0`), matching the established
    M43/M45/M46 convention."""
    n = sum(1 for r in second_only_rows if r.outcome["closed"])
    if n < MIN_SECOND_ONLY_SUPPORTED_N:
        return {
            "verdict": "INSUFFICIENT_EVIDENCE",
            "reason": f"SECOND_ONLY closed trades={n} below {MIN_SECOND_ONLY_SUPPORTED_N}",
            "second_only_n": n,
        }

    months = total_sessions / 21.0
    tq_second = trade_quality_from_rows(second_only_rows)
    tq_combined = trade_quality_from_rows(combined_rows)
    tq_baseline = trade_quality_from_rows(baseline_rows)
    dd_combined = max_drawdown_r_and_streak_from_rows(combined_rows)["maximum_drawdown_r"]
    dd_baseline = max_drawdown_r_and_streak_from_rows(baseline_rows)["maximum_drawdown_r"]
    robustness = second_only_symbol_robustness(second_only_rows)

    baseline_trades_per_month = tq_baseline["closed_trades"] / months if months else None
    combined_trades_per_month = tq_combined["closed_trades"] / months if months else None
    baseline_r_per_month = tq_baseline["cumulative_r"] / months if months else None
    combined_r_per_month = tq_combined["cumulative_r"] / months if months else None

    frequency_gain_pct = (
        (combined_trades_per_month - baseline_trades_per_month) / baseline_trades_per_month
        if baseline_trades_per_month else None
    )
    expectancy_ratio = (
        (tq_combined["expectancy_r_per_trade"] / tq_baseline["expectancy_r_per_trade"])
        if tq_baseline["expectancy_r_per_trade"] else None
    )
    drawdown_ratio = (dd_combined / dd_baseline) if dd_baseline else None

    checks = {
        "second_only_n_at_least_30": n >= MIN_SECOND_ONLY_SUPPORTED_N,
        "second_only_expectancy_positive": (tq_second["expectancy_r_per_trade"] or 0) > 0,
        "second_only_pf_above_1": (tq_second["profit_factor_r"] or 0) > 1,
        "combined_expectancy_positive": (tq_combined["expectancy_r_per_trade"] or 0) > 0,
        "combined_pf_above_1": (tq_combined["profit_factor_r"] or 0) > 1,
        "frequency_gain_at_least_25pct": (frequency_gain_pct is not None and frequency_gain_pct >= MIN_FREQUENCY_GAIN_PCT),
        "combined_r_per_month_beats_baseline": (
            combined_r_per_month is not None and baseline_r_per_month is not None and combined_r_per_month > baseline_r_per_month
        ),
        "combined_expectancy_at_least_80pct_baseline": (expectancy_ratio is not None and expectancy_ratio >= MIN_COMBINED_EXPECTANCY_RATIO),
        "drawdown_not_worse_than_125pct": (drawdown_ratio is not None and drawdown_ratio <= MAX_DRAWDOWN_RATIO),
        "second_only_expectancy_excl_best_symbol_positive": (robustness["expectancy_r_excluding_best_contributor"] or 0) > 0,
    }

    if all(checks.values()):
        verdict = "SUPPORTED"
    elif checks["second_only_expectancy_positive"] and checks["second_only_pf_above_1"] and checks["combined_expectancy_positive"] and checks["combined_pf_above_1"]:
        verdict = "MIXED"
    else:
        verdict = "NOT_SUPPORTED"

    return {
        "verdict": verdict, "second_only_n": n, "checks": checks,
        "second_only_expectancy_r": tq_second["expectancy_r_per_trade"], "second_only_pf_r": tq_second["profit_factor_r"],
        "combined_expectancy_r": tq_combined["expectancy_r_per_trade"], "combined_pf_r": tq_combined["profit_factor_r"],
        "frequency_gain_pct": frequency_gain_pct, "expectancy_ratio": expectancy_ratio, "drawdown_ratio": drawdown_ratio,
        "baseline_trades_per_month": baseline_trades_per_month, "combined_trades_per_month": combined_trades_per_month,
        "baseline_r_per_month": baseline_r_per_month, "combined_r_per_month": combined_r_per_month,
        "combined_max_drawdown_r": dd_combined, "baseline_max_drawdown_r": dd_baseline,
        "symbol_robustness": robustness,
    }


__all__ = [
    "SequentialSecondTradeError",
    "POLICY_SEQUENTIAL_SECOND_VALID_1020",
    "SECOND_SCAN_END_ET",
    "SecondScanResult",
    "second_trade_eligibility",
    "scan_for_second_candidate",
    "build_second_shadow_outcome",
    "DayResult",
    "build_day_results",
    "baseline_rows",
    "second_only_rows",
    "combined_rows",
    "MIN_SECOND_ONLY_SUPPORTED_N",
    "MIN_FREQUENCY_GAIN_PCT",
    "MIN_COMBINED_EXPECTANCY_RATIO",
    "MAX_DRAWDOWN_RATIO",
    "second_only_symbol_robustness",
    "classify_sequential_second_trade",
]
