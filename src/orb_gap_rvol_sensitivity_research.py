"""Research tooling for the ORB gap x RVOL sensitivity study (Issue #300).

This module answers one question: holding every other `orb_long_v0` rule
fixed, how does relaxing the two most restrictive entry thresholds --
`minimum_gap_percent` and `minimum_relative_volume` -- change candidate
frequency, zero-candidate days, candidate concentration, and (where the
repository can truthfully support it) trade quality? It is research-only
and never proposes a production threshold change.

Reuse, not reinterpretation:
  * The 4x3=12 gap/RVOL combination is evaluated entirely through
    `src.orb_universe_expansion_research.resolve_session_signal` (fed by
    `precompute_session_signal_inputs`, computed once and reused across the
    whole matrix -- see `precompute_inputs_table` below)
    and `build_study_report` (Issue #298, merged) -- the same canonical
    `SignalEngine` gate order, the same fetch/coverage/liquidity-qualified
    universe infrastructure, unmodified. This module adds only: (1) an
    in-memory config override for the two thresholds under study, (2) extra
    aggregation (max candidates/day, full per-symbol counts, incremental
    diffs, frequency-target detection, ranking) `build_study_report` does
    not already compute, and (3) a bridge from an approved `CANDIDATE`
    evaluation to `src.shadow_execution_simulator.simulate_shadow_execution`
    (Milestone 15, merged) for trade-quality metrics, reusing the identical
    `src.shadow_trading_decision.build_shadow_trading_decision` construction
    `src/live_shadow_session.py`'s own live evaluation path already uses.
  * No stop, target, or exit rule is invented here: `simulate_shadow_execution`
    delegates entry/stop/target/exit decisions entirely to the unmodified
    `src.exit_engine.ExitEngine`.

Safety: `sensitivity_config` always deep-copies the loaded config before
overriding `minimum_gap_percent`/`minimum_relative_volume` -- the tracked
`config/strategy_v0.yaml` is never read back or written by this module, and
`strategy.enabled` is flipped to `True` only inside that same in-memory copy
(the identical discipline `evaluate_symbol_session` already uses), never
persisted.
"""
from __future__ import annotations

import copy
import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, Sequence

from src.market_data_canonical import (
    MarketBar, MarketDataQuality, compute_market_data_batch_digest, compute_market_data_digest,
)
from src.orb_universe_expansion_research import (
    CANDIDATE,
    SessionEvaluation,
    SessionSignalInputs,
    build_study_report,
    precompute_session_signal_inputs,
    resolve_session_signal,
)
from src.shadow_execution_simulator import ShadowExecutionResult, simulate_shadow_execution
from src.shadow_trading_decision import ShadowTradingDecision, build_shadow_trading_decision
from src.signal_engine import LiveData, OpeningData, SignalDecision

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
NORMALIZATION_VERSION = "alpaca-market-data-v1"
RESEARCH_UNIVERSE_LABEL = "research_universe"

#: The full, fixed 3x4=12-combination gap/RVOL matrix requested for this
#: study. Ordered gap outer (most to least restrictive) / RVOL inner (most
#: to least restrictive) so index 0 is always the current production
#: baseline and adjacent entries share exactly one threshold, matching the
#: order `rvol_relaxation_steps`/`gap_relaxation_steps` walk below.
GAP_THRESHOLDS: tuple[Decimal, ...] = (Decimal("1.00"), Decimal("0.75"), Decimal("0.50"))
RVOL_THRESHOLDS: tuple[Decimal, ...] = (Decimal("3.0"), Decimal("2.5"), Decimal("2.0"), Decimal("1.5"))

#: Candidate-day-rate targets this study explicitly checks each combination
#: against (goal item 7).
FREQUENCY_TARGETS: tuple[float, ...] = (0.25, 0.40, 0.50, 0.60, 0.70)

TRADE_QUALITY_NOT_YET_EVALUATED = "TRADE_QUALITY_NOT_YET_EVALUATED"


class SensitivityResearchError(RuntimeError):
    """Refused: sensitivity research tooling could not safely proceed."""


@dataclass(frozen=True)
class ParameterCombo:
    gap_percent: Decimal
    rvol_multiple: Decimal

    @property
    def label(self) -> str:
        return f"gap_{self.gap_percent}pct_rvol_{self.rvol_multiple}x"


def parameter_matrix() -> tuple[ParameterCombo, ...]:
    """The full, deterministic 12-combination gap x RVOL matrix."""
    return tuple(
        ParameterCombo(gap_percent=gap, rvol_multiple=rvol)
        for gap in GAP_THRESHOLDS
        for rvol in RVOL_THRESHOLDS
    )


def baseline_combo() -> ParameterCombo:
    return ParameterCombo(gap_percent=GAP_THRESHOLDS[0], rvol_multiple=RVOL_THRESHOLDS[0])


def gap_relaxation_steps() -> tuple[tuple[Decimal, Decimal], ...]:
    """Adjacent (from, to) gap-threshold pairs in relaxation order."""
    return tuple(zip(GAP_THRESHOLDS, GAP_THRESHOLDS[1:]))


def rvol_relaxation_steps() -> tuple[tuple[Decimal, Decimal], ...]:
    """Adjacent (from, to) RVOL-threshold pairs in relaxation order."""
    return tuple(zip(RVOL_THRESHOLDS, RVOL_THRESHOLDS[1:]))


def sensitivity_config(
    base_config: Mapping[str, Any], *, gap_percent: Decimal, rvol_multiple: Decimal,
) -> dict[str, Any]:
    """An in-memory-only copy of `base_config` with `minimum_gap_percent`
    and `minimum_relative_volume` overridden to the given research values.
    `base_config` is deep-copied first and is never mutated by this
    function; nothing here ever reads back or writes
    `config/strategy_v0.yaml`. Every other key (universe, entry window,
    opening-range length, SPY/VWAP gate, exit rules) is carried through
    unchanged."""
    overridden = copy.deepcopy(dict(base_config))
    overridden["signal"]["minimum_gap_percent"] = float(gap_percent)
    overridden["signal"]["minimum_relative_volume"] = float(rvol_multiple)
    return overridden


CandidateSignal = tuple[str, date, OpeningData, LiveData, SignalDecision]


@dataclass(frozen=True)
class ComboEvaluation:
    combo: ParameterCombo
    report: Mapping[str, Any]
    candidate_signals: tuple[CandidateSignal, ...]

    @property
    def universe_report(self) -> Mapping[str, Any]:
        for entry in self.report["universe_reports"]:
            if entry["label"] == RESEARCH_UNIVERSE_LABEL:
                return entry
        raise SensitivityResearchError(f"missing universe report for {RESEARCH_UNIVERSE_LABEL!r}")

    @property
    def candidate_days_by_symbol(self) -> Counter[str]:
        return Counter(symbol for symbol, _date, *_rest in self.candidate_signals)

    @property
    def candidates_by_session(self) -> Counter[date]:
        return Counter(session_date for _symbol, session_date, *_rest in self.candidate_signals)

    @property
    def max_candidates_one_day(self) -> int:
        counts = self.candidates_by_session
        return max(counts.values()) if counts else 0

    @property
    def candidate_keys(self) -> frozenset[tuple[str, date]]:
        return frozenset((symbol, session_date) for symbol, session_date, *_rest in self.candidate_signals)


InputsTable = dict[tuple[str, date], SessionSignalInputs]


def precompute_inputs_table(
    *,
    universe: Sequence[str],
    session_dates: Sequence[date],
    minute_bars_by_symbol: Mapping[str, Sequence],
    spy_minute_bars: Sequence,
    base_config: Mapping[str, Any],
) -> InputsTable:
    """Precompute `SessionSignalInputs` once for every (symbol, session_date)
    pair, using `base_config` (whose opening-range length, RVOL lookback,
    and entry window -- everything except `minimum_gap_percent`/
    `minimum_relative_volume` -- are held fixed across the whole gap/RVOL
    matrix per this study's own scope, so precomputing against `base_config`
    is exactly equivalent to precomputing against any one combo's config).
    `evaluate_combo` below reuses this table across all 12 combinations, so
    the expensive full-history bar scan runs once per symbol/session, not
    once per symbol/session/combination -- a 12x reduction for this study's
    matrix."""
    table: InputsTable = {}
    for symbol in universe:
        candidate_bars = minute_bars_by_symbol[symbol]
        for session_date in session_dates:
            table[(symbol, session_date)] = precompute_session_signal_inputs(
                symbol=symbol, session_date=session_date, candidate_bars=candidate_bars,
                spy_bars=spy_minute_bars, config=base_config,
            )
    return table


def evaluate_combo(
    *,
    combo: ParameterCombo,
    universe: Sequence[str],
    session_dates: Sequence[date],
    inputs_table: InputsTable,
    base_config: Mapping[str, Any],
) -> ComboEvaluation:
    """Evaluate every symbol/session in `universe` against one gap/RVOL
    combination, using the unmodified canonical evaluator's cheap,
    threshold-dependent half (`resolve_session_signal`) against a table
    already precomputed by `precompute_inputs_table`. Reuses
    `build_study_report` for coverage/funnel aggregation exactly as Issue
    #298's study does; the only new aggregation here is what that report
    does not already carry (max candidates/day, full per-symbol counts,
    candidate keys for incremental diffing) plus the `SignalDecision`
    context each `CANDIDATE` needs for the trade-quality path below."""
    combo_config = sensitivity_config(
        base_config, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple,
    )
    evaluations: dict[tuple[str, date], SessionEvaluation] = {}
    candidate_signals: list[CandidateSignal] = []
    for symbol in universe:
        for session_date in session_dates:
            inputs = inputs_table[(symbol, session_date)]
            evaluation, opening_data, live_data, decision = resolve_session_signal(
                symbol=symbol, session_date=session_date, inputs=inputs, config=combo_config,
            )
            evaluations[(symbol, session_date)] = evaluation
            if evaluation.state == CANDIDATE:
                assert opening_data is not None and live_data is not None and decision is not None
                candidate_signals.append((symbol, session_date, opening_data, live_data, decision))
    report = build_study_report(
        session_dates=session_dates, evaluations=evaluations, universe_sizes=(),
        include_production=False, named_universes={RESEARCH_UNIVERSE_LABEL: tuple(universe)},
    )
    return ComboEvaluation(combo=combo, report=report, candidate_signals=tuple(candidate_signals))


def matrix_row(evaluation: ComboEvaluation) -> dict[str, Any]:
    """One row of the required Gap x RVOL matrix (goal item 5)."""
    universe = evaluation.universe_report
    coverage = universe["coverage"]
    by_symbol = evaluation.candidate_days_by_symbol
    candidate_day_counts = [count for count in evaluation.candidates_by_session.values()]
    return {
        "gap_percent": str(evaluation.combo.gap_percent),
        "rvol_multiple": str(evaluation.combo.rvol_multiple),
        "sessions_studied": universe["sessions"],
        "coverage_pct": coverage["completeness"],
        "coverage_status": coverage["status"],
        "sessions_with_candidate": universe["sessions_with_candidate"],
        "candidate_day_rate": universe["candidate_day_rate"],
        "zero_candidate_sessions": universe["zero_candidate_sessions"],
        "total_candidates": universe["total_candidates"],
        "mean_candidates_per_session": universe["mean_candidates_per_session"],
        "median_candidates_on_candidate_days": (
            float(median(candidate_day_counts)) if candidate_day_counts else 0.0
        ),
        "max_candidates_one_day": evaluation.max_candidates_one_day,
        "funnel": dict(universe["funnel"]),
        "candidates_by_symbol": dict(sorted(by_symbol.items(), key=lambda item: (-item[1], item[0]))),
        "concentration": symbol_concentration(by_symbol),
    }


def symbol_concentration(candidate_days_by_symbol: Mapping[str, int]) -> dict[str, Any]:
    """Share of all candidates contributed by the top-N symbols -- goal item
    5's "concentration of candidates among top symbols"."""
    total = sum(candidate_days_by_symbol.values())
    ranked = sorted(candidate_days_by_symbol.items(), key=lambda item: (-item[1], item[0]))
    result: dict[str, Any] = {"total_candidates": total, "distinct_symbols": len(ranked)}
    for top_n in (1, 3, 5):
        top_sum = sum(count for _symbol, count in ranked[:top_n])
        result[f"top_{top_n}_share"] = (top_sum / total) if total else 0.0
    return result


@dataclass(frozen=True)
class IncrementalEffect:
    axis: str
    held_fixed: str
    from_threshold: str
    to_threshold: str
    additional_candidate_days: int
    additional_candidate_occurrences: int
    newly_admitted: tuple[dict[str, str], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "axis": self.axis,
            "held_fixed": self.held_fixed,
            "from_threshold": self.from_threshold,
            "to_threshold": self.to_threshold,
            "additional_candidate_days": self.additional_candidate_days,
            "additional_candidate_occurrences": self.additional_candidate_occurrences,
            "newly_admitted": list(self.newly_admitted),
        }


def incremental_effect(from_eval: ComboEvaluation, to_eval: ComboEvaluation, *, axis: str, held_fixed: str) -> IncrementalEffect:
    from_keys = from_eval.candidate_keys
    to_keys = to_eval.candidate_keys
    newly_admitted = tuple(
        {"symbol": symbol, "date": session_date.isoformat()}
        for symbol, session_date in sorted(to_keys - from_keys, key=lambda item: (item[1], item[0]))
    )
    from_sessions = {session_date for _symbol, session_date in from_keys}
    to_sessions = {session_date for _symbol, session_date in to_keys}
    threshold_label = (
        f"{from_eval.combo.rvol_multiple}x -> {to_eval.combo.rvol_multiple}x" if axis == "rvol"
        else f"{from_eval.combo.gap_percent}% -> {to_eval.combo.gap_percent}%"
    )
    from_value = str(from_eval.combo.rvol_multiple) if axis == "rvol" else str(from_eval.combo.gap_percent)
    to_value = str(to_eval.combo.rvol_multiple) if axis == "rvol" else str(to_eval.combo.gap_percent)
    return IncrementalEffect(
        axis=axis, held_fixed=held_fixed, from_threshold=from_value, to_threshold=to_value,
        additional_candidate_days=len(to_sessions) - len(from_sessions),
        additional_candidate_occurrences=len(to_keys) - len(from_keys),
        newly_admitted=newly_admitted,
    )


def all_incremental_effects(evaluations_by_combo: Mapping[ParameterCombo, ComboEvaluation]) -> list[dict[str, Any]]:
    """Goal item 6: for every RVOL relaxation step at each fixed gap, and
    every gap relaxation step at each fixed RVOL, how many additional
    candidate days/occurrences appear and which symbol/date pairs are newly
    admitted."""
    effects: list[dict[str, Any]] = []
    for gap in GAP_THRESHOLDS:
        for from_rvol, to_rvol in rvol_relaxation_steps():
            from_eval = evaluations_by_combo[ParameterCombo(gap, from_rvol)]
            to_eval = evaluations_by_combo[ParameterCombo(gap, to_rvol)]
            effects.append(incremental_effect(from_eval, to_eval, axis="rvol", held_fixed=f"gap={gap}%").as_dict())
    for rvol in RVOL_THRESHOLDS:
        for from_gap, to_gap in gap_relaxation_steps():
            from_eval = evaluations_by_combo[ParameterCombo(from_gap, rvol)]
            to_eval = evaluations_by_combo[ParameterCombo(to_gap, rvol)]
            effects.append(incremental_effect(from_eval, to_eval, axis="gap", held_fixed=f"rvol={rvol}x").as_dict())
    return effects


def frequency_target_hits(rows: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    """Goal item 7: which combinations clear each candidate-day-rate
    target, in matrix order (least to most relaxed)."""
    hits: dict[str, list[str]] = {f">={int(target * 100)}%": [] for target in FREQUENCY_TARGETS}
    for row in rows:
        for target in FREQUENCY_TARGETS:
            if row["candidate_day_rate"] >= target:
                hits[f">={int(target * 100)}%"].append(f"gap={row['gap_percent']}% rvol={row['rvol_multiple']}x")
    return hits


# ---------------------------------------------------------------------------
# Trade quality: reuses the merged M15 shadow-decision/execution-simulator
# path (src/shadow_trading_decision.py, src/shadow_execution_simulator.py)
# unchanged. No stop/target/exit rule is invented here.
# ---------------------------------------------------------------------------


def _session_bars(bars: Sequence[MarketBar], *, session_date: date) -> list[MarketBar]:
    return sorted((bar for bar in bars if bar.trading_date == session_date), key=lambda bar: bar.event_timestamp)


def build_candidate_shadow_decision(
    *,
    symbol: str,
    session_date: date,
    opening_data: OpeningData,
    live_data: LiveData,
    decision: SignalDecision,
    combo_config: Mapping[str, Any],
    strategy_version: str,
    implementation_digest: str,
    shadow_run_id: str,
    created_at: datetime,
    candidate_market_bars: Sequence[MarketBar],
    spy_market_bars: Sequence[MarketBar],
) -> ShadowTradingDecision:
    """Wrap one already-approved `CANDIDATE` signal in a `ShadowTradingDecision`,
    the identical construction `src/live_shadow_session.py`'s live evaluation
    path uses -- `evaluated_signal_decision=decision` means the canonical
    `SignalEngine` is never re-run, only packaged."""
    session_candidate_bars = _session_bars(candidate_market_bars, session_date=session_date)
    session_spy_bars = _session_bars(spy_market_bars, session_date=session_date)
    digests = [
        compute_market_data_digest(bar, normalization_version=NORMALIZATION_VERSION)
        for bar in session_candidate_bars + session_spy_bars
    ]
    market_data_digest = compute_market_data_batch_digest(digests) if digests else ""
    opening_range_identity = hashlib.sha256(
        (
            symbol + "|" + session_date.isoformat() + "|" + str(opening_data.opening_range_high)
            + "|" + str(opening_data.opening_range_low)
        ).encode("utf-8")
    ).hexdigest()
    return build_shadow_trading_decision(
        shadow_run_id=shadow_run_id,
        strategy_version=strategy_version,
        implementation_digest=implementation_digest,
        market_data_digest=market_data_digest,
        opening_range_identity=opening_range_identity,
        exchange_session="REGULAR",
        market_data_quality=MarketDataQuality.VALID,
        signal_config=combo_config,
        opening=opening_data,
        live=live_data,
        created_at=created_at,
        evaluated_signal_decision=decision,
    )


def simulate_combo_trade_quality(
    *,
    evaluation: ComboEvaluation,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
) -> dict[str, Any]:
    """For every `CANDIDATE` this combination produced, replay it through
    `simulate_shadow_execution` (ORB V0's own exit rules, unmodified) using
    the real subsequent same-day bars, then aggregate to the metrics goal
    item 8 requests. ORB V0 never holds overnight (`ExitEngine`'s own
    `exit.time_exit` forces a same-day exit) so subsequent bars are bounded
    to the candidate's own session date."""
    combo_config = sensitivity_config(
        base_config, gap_percent=evaluation.combo.gap_percent, rvol_multiple=evaluation.combo.rvol_multiple,
    )
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    shadow_run_id = f"gap-rvol-sensitivity-{evaluation.combo.label}"
    spy_bars = market_bars_by_symbol["SPY"]

    results: list[ShadowExecutionResult] = []
    for symbol, session_date, opening_data, live_data, decision in evaluation.candidate_signals:
        candidate_bars = market_bars_by_symbol[symbol]
        shadow_decision = build_candidate_shadow_decision(
            symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
            decision=decision, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
        )
        session_bars = _session_bars(candidate_bars, session_date=session_date)
        subsequent_bars = [bar for bar in session_bars if bar.event_timestamp >= shadow_decision.signal_timestamp]
        results.append(simulate_shadow_execution(shadow_decision, subsequent_bars))
    return aggregate_trade_quality(results)


def aggregate_trade_quality(results: Sequence[ShadowExecutionResult]) -> dict[str, Any]:
    """Goal item 8's metric set, computed only from trades the simulator
    could actually resolve. `winners`/`losers` are split on realized P&L
    sign among CLOSED trades only (`STOPPED`, `TARGET_HIT`, `TIME_EXIT`);
    positions still open at the data boundary and ambiguous outcomes are
    reported separately, never folded into win/loss so they cannot silently
    inflate either count."""
    entered = [r for r in results if r.entry_price is not None]
    closed = [r for r in entered if r.realized_pnl is not None]
    winners = [r for r in closed if r.realized_pnl > 0]
    losers = [r for r in closed if r.realized_pnl <= 0]
    stopped = [r for r in closed if r.state.value == "STOPPED"]
    ambiguous = [r for r in results if r.state.value == "AMBIGUOUS"]
    still_open = [r for r in entered if r.realized_pnl is None]
    pending = [r for r in results if r.state.value == "PENDING_NOT_FILLED"]

    gross_profit = sum((r.realized_pnl for r in winners), Decimal("0"))
    gross_loss = sum((-r.realized_pnl for r in losers), Decimal("0"))

    r_multiples: list[Decimal] = []
    for r in closed:
        risk = r.entry_price - r.initial_stop_price
        if risk > 0:
            r_multiples.append(r.realized_pnl / risk)

    ordered_closed = sorted(closed, key=lambda r: r.exit_timestamp)
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    for r in ordered_closed:
        running += r.realized_pnl
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)

    if gross_loss > 0:
        profit_factor: str | None = str(gross_profit / gross_loss)
    elif gross_profit > 0:
        profit_factor = "INFINITE_NO_LOSSES"
    else:
        profit_factor = None

    return {
        "status": "EVALUATED" if results else "NO_CANDIDATES_TO_SIMULATE",
        "candidates_evaluated": len(results),
        "trades_entered": len(entered),
        "trades_closed": len(closed),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": (len(winners) / len(closed)) if closed else None,
        "average_winner": str(mean([r.realized_pnl for r in winners])) if winners else None,
        "average_loser": str(mean([r.realized_pnl for r in losers])) if losers else None,
        "expectancy_per_trade": str((gross_profit - gross_loss) / len(closed)) if closed else None,
        "profit_factor": profit_factor,
        "stop_out_rate": (len(stopped) / len(closed)) if closed else None,
        "maximum_drawdown": str(max_drawdown),
        "cumulative_r_multiple": str(sum(r_multiples, Decimal("0"))) if r_multiples else None,
        "still_open_at_data_boundary": len(still_open),
        "pending_not_filled_never_touched_entry": len(pending),
        "ambiguous_outcomes": len(ambiguous),
    }


# ---------------------------------------------------------------------------
# Ranking / recommendation (goal item 9). A small, documented heuristic, not
# an optimizer -- it never recommends purely on candidate count.
# ---------------------------------------------------------------------------


def relaxation_distance(combo: ParameterCombo) -> int:
    """How many single-threshold relaxation steps separate `combo` from the
    baseline -- used only to break ties toward the smaller relaxation, never
    to pick a "best" combination on its own."""
    gap_index = GAP_THRESHOLDS.index(combo.gap_percent)
    rvol_index = RVOL_THRESHOLDS.index(combo.rvol_multiple)
    return gap_index + rvol_index


def build_recommendation(
    rows_by_combo: Mapping[ParameterCombo, Mapping[str, Any]],
    trade_quality_by_combo: Mapping[ParameterCombo, Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    matrix = parameter_matrix()
    baseline = baseline_combo()

    highest_frequency = max(
        matrix, key=lambda combo: (rows_by_combo[combo]["candidate_day_rate"], -relaxation_distance(combo)),
    )

    first_50pct = next(
        (combo for combo in matrix if rows_by_combo[combo]["candidate_day_rate"] >= 0.50), None,
    )

    baseline_days = rows_by_combo[baseline]["sessions_with_candidate"]
    smallest_relaxation = None
    for distance in range(1, len(GAP_THRESHOLDS) + len(RVOL_THRESHOLDS) - 1):
        candidates_at_distance = [c for c in matrix if relaxation_distance(c) == distance]
        improved = [
            c for c in candidates_at_distance
            if rows_by_combo[c]["sessions_with_candidate"] > baseline_days
            and rows_by_combo[c]["coverage_status"] == "VALID"
        ]
        if improved:
            smallest_relaxation = max(improved, key=lambda c: rows_by_combo[c]["sessions_with_candidate"])
            break

    valid_combos = [c for c in matrix if rows_by_combo[c]["coverage_status"] == "VALID"]
    pool = valid_combos or list(matrix)
    if trade_quality_by_combo:
        def score(combo: ParameterCombo) -> tuple[float, float, int]:
            row = rows_by_combo[combo]
            concentration = row["concentration"]["top_3_share"]
            expectancy_raw = trade_quality_by_combo.get(combo, {}).get("expectancy_per_trade")
            expectancy = float(expectancy_raw) if expectancy_raw not in (None,) else 0.0
            return (row["candidate_day_rate"] * (1.0 - concentration), expectancy, -relaxation_distance(combo))
    else:
        def score(combo: ParameterCombo) -> tuple[float, float, int]:
            row = rows_by_combo[combo]
            concentration = row["concentration"]["top_3_share"]
            return (row["candidate_day_rate"] * (1.0 - concentration), 0.0, -relaxation_distance(combo))
    recommended = max(pool, key=score)

    return {
        "baseline": {"combo": baseline.label, "gap_percent": str(baseline.gap_percent), "rvol_multiple": str(baseline.rvol_multiple)},
        "smallest_useful_relaxation": (
            {"combo": smallest_relaxation.label, "gap_percent": str(smallest_relaxation.gap_percent), "rvol_multiple": str(smallest_relaxation.rvol_multiple)}
            if smallest_relaxation is not None else None
        ),
        "first_exceeding_50pct_candidate_days": (
            {"combo": first_50pct.label, "gap_percent": str(first_50pct.gap_percent), "rvol_multiple": str(first_50pct.rvol_multiple)}
            if first_50pct is not None else None
        ),
        "highest_frequency": {"combo": highest_frequency.label, "gap_percent": str(highest_frequency.gap_percent), "rvol_multiple": str(highest_frequency.rvol_multiple)},
        "recommended_for_further_validation": {"combo": recommended.label, "gap_percent": str(recommended.gap_percent), "rvol_multiple": str(recommended.rvol_multiple)},
        "rationale": (
            "recommended_for_further_validation maximizes candidate_day_rate * "
            "(1 - top_3_symbol_concentration) among VALID-coverage combinations "
            "(falling back to all 12 if none reach VALID coverage), using "
            "expectancy_per_trade as a tiebreaker when trade-quality data exists "
            "and the smallest relaxation distance from baseline as the final "
            "tiebreaker -- this is a research heuristic for choosing what to "
            "validate further, not an optimizer and not a production change."
        ),
    }


__all__ = [
    "SensitivityResearchError",
    "GAP_THRESHOLDS",
    "RVOL_THRESHOLDS",
    "FREQUENCY_TARGETS",
    "TRADE_QUALITY_NOT_YET_EVALUATED",
    "ParameterCombo",
    "parameter_matrix",
    "baseline_combo",
    "gap_relaxation_steps",
    "rvol_relaxation_steps",
    "sensitivity_config",
    "ComboEvaluation",
    "InputsTable",
    "precompute_inputs_table",
    "evaluate_combo",
    "matrix_row",
    "symbol_concentration",
    "IncrementalEffect",
    "incremental_effect",
    "all_incremental_effects",
    "frequency_target_hits",
    "build_candidate_shadow_decision",
    "simulate_combo_trade_quality",
    "aggregate_trade_quality",
    "relaxation_distance",
    "build_recommendation",
    "RESEARCH_UNIVERSE_LABEL",
    "REPOSITORY_ROOT",
    "NORMALIZATION_VERSION",
]
