"""Research tooling for M41: candidate-selection timing/ranking research
(Issue #330).

M40 found that `GAP_050_RVOL_175` (Issue #325's CHALLENGER) shows a
materially stronger ALL_CANDIDATES result than its own FIRST_VALID
one-trade-per-day selection on P_MINUS_1 -- suggesting the FIRST_VALID
chronological-selection policy may be discarding information, not that
the strategy family lacks edge. This module decomposes *why* into three
separable questions via four (five, counting both rankers) execution/
selection arms, all on the single frozen CHALLENGER config only -- no new
gap/RVOL threshold, no PRIMARY, no exit/stop/target/SPY/breakout rule
invented anywhere in this module.

Arms
----
CURRENT_FIRST_VALID: today's existing baseline -- full 09:40-11:00 entry
window, deterministic first-valid candidate
(`orb_out_of_sample_validation_research.select_first_candidate_per_day`,
reused unmodified), immediate execution via the unmodified
`build_stability_rows` (Issue #319).

EARLY_FIRST_VALID: candidates restricted to those that qualified at or
before 09:50 only, still first-valid chronologically, same immediate
execution. Isolates whether early entries themselves carry edge.
`early_pool` filters the SAME full-window `candidate_signals`
`evaluate_combo` already produces by `LiveData.timestamp` rather than
re-scanning a truncated window -- provably equivalent, because
`resolve_session_signal`'s per-(symbol, session_date) bar-by-bar walk
never diverges before an approval is found: if a symbol's full-window
scan approved at or before 09:50, a truncated 09:40-09:50 scan would
have found the identical bar (nothing about the walk up to that point
depends on how much window remains after it); if the full-window
approval came after 09:50 (or never), the symbol correctly did not
qualify by 09:50 either way.

WAITED_FIRST_VALID_0950: the SAME candidate identity as EARLY_FIRST_VALID
(the first symbol to qualify by 09:50), but the decision/execution is
delayed to 09:50: the ORIGINAL breakout bar's price is never reused with
a later timestamp (that would be an impossible historical fill -- price
information the original bar did not have at 09:50 could leak in). The
real, unmodified `SignalEngine.evaluate` is re-run at the bar at/after
09:50 in that same session's already-precomputed
`SessionSignalInputs.window`, using the SAME `OpeningData` (so
`stop_price = opening.opening_range_low` is identical either way -- it is
a session-level constant, not bar-dependent) but a genuinely later
`LiveData` (so `entry_price = live.completed_bar_close` reflects the real
price at that later moment). If the setup is no longer valid by then
(price pulled back through the opening-range high, or SPY dropped below
its VWAP), the re-evaluation correctly rejects and no trade is recorded
for that occurrence -- a real, reportable cost of waiting, not an
error. Isolates the cost of waiting, holding candidate identity fixed.

RANKED_POOL_0950_MAX_RVOL / RANKED_POOL_0950_EQUAL_RANK_STRENGTH: the
full EARLY_FIRST_VALID pool for each day (every symbol that qualified by
09:50, not just the first), ranked by one of two independently
predeclared rankers (`rank_max_rvol`, `rank_equal_rank_strength`), one
selected winner per day, executed with the identical WAIT-to-09:50
mechanics as WAITED_FIRST_VALID_0950. Isolates the value of decision-time
ranking versus chronological-first selection, holding execution timing
fixed.

Frozen rankers (definitions fixed before any empirical execution;
`Decimal` throughout, no ML, no fitted coefficients, no future-return/
MFE/outcome field is ever read by either ranker):

RANKER A MAX_RVOL -- highest `decision.relative_volume` wins; ties broken
by earlier qualifying signal, then alphabetical symbol.

RANKER B EQUAL_RANK_STRENGTH -- equal-weight ordinal ranking (rank 1 =
best) across four factors, all evaluated at the SAME 09:50 decision
snapshot for every candidate in that day's pool (an apples-to-apples
comparison at one clock instant, matching "all candidates known by
09:50" -> "decision-time ranking"): gap magnitude (`decision.gap_percent`
-- session-constant, unaffected by which bar is used), opening RVOL
(`decision.relative_volume` -- also session-constant), breakout strength
(`breakout_strength`: (price at the 09:50 decision bar - opening-range
high) / opening-range width -- a dimensionless ratio already, no further
normalization needed), and relative strength versus SPY
(`relative_strength_vs_spy`: the traded symbol's own return from session
open through the 09:50 decision timestamp, minus SPY's return over the
identical interval). Each factor's ordinal rank across that day's pool is
assigned by sorting on (factor value descending, earlier qualifying
signal, alphabetical symbol) -- the same minimal tie-break convention
RANKER A uses, applied per-factor so every rank is a unique integer and
no fractional tie-averaging is needed. The four per-factor ranks are then
averaged with equal weight (1/4 each); lowest average rank wins, ties
broken the same way. If no candidate in a day's pool has data available
at/after 09:50, RANKER B falls back to the same (timestamp, symbol)
tie-break RANKER A uses, so both ranked arms report exactly one
occurrence per qualifying day and the resulting execution attempt
correctly surfaces as `NO_DECISION_TIME_DATA` rather than silently
vanishing that day.

Secondary recency-weighted deployment view (per-trade exponential time
decay, half-life 504 trading sessions, anchor date 2026-08-13, frozen
before any empirical execution): `weight_i = 2 ** (-age_i / 504)`, where
`age_i` is the number of research-calendar trading sessions from
`trade_date_i` through the anchor date (a trade ON the anchor date has
age 0, weight 1.0; 504 sessions old, weight 0.5; and so on). Applied at
the trade level, never by assigning arbitrary weights to whole periods.
`recency_weighted_trade_quality` reports ONLY `weighted_expectancy_r`,
`weighted_win_rate`, and `weighted_profit_factor_r` (plus the raw
weighted gross figures) -- it deliberately never computes a weighted
cumulative-R figure, which would misrepresent an actual chronological
account path; chronological cumulative R and drawdown remain exclusively
in the unweighted, primary, per-period view
(`max_drawdown_and_streak_from_rows`, reused unchanged). P_MINUS_1/2022
evidence is never discarded by this view, only naturally down-weighted
by age alongside every other period.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.exchange_calendar import EASTERN
from src.market_data import MinuteBar
from src.market_data_canonical import MarketBar, compute_market_data_batch_digest, compute_market_data_digest
from src.orb_baseline_stability_research import TradeDiagnosticRow, build_stability_rows
from src.orb_existing_configs_reevaluation_research import FROZEN_CONFIG_SET
from src.orb_gap_rvol_sensitivity_research import (
    CandidateSignal,
    ComboEvaluation,
    InputsTable,
    NORMALIZATION_VERSION,
    build_candidate_shadow_decision,
    sensitivity_config,
)
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_research_historical_calendar import research_regular_session_bounds
from src.shadow_execution_simulator import ShadowExecutionResult, simulate_shadow_execution
from src.signal_engine import LiveData, OpeningData, SignalDecision, SignalEngine

__all__ = [
    "CandidateSelectionTimingError",
    "CONFIG_LABEL",
    "CONFIG_ENTRY",
    "DECISION_CUTOFF_TIME",
    "ANCHOR_DATE",
    "HALF_LIFE_SESSIONS",
    "ARM_CURRENT_FIRST_VALID",
    "ARM_EARLY_FIRST_VALID",
    "ARM_WAITED_FIRST_VALID_0950",
    "ARM_RANKED_POOL_0950_MAX_RVOL",
    "ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH",
    "ARMS",
    "DecisionTimeCandidate",
    "early_pool",
    "group_by_day",
    "live_data_at_or_after",
    "enrich_with_decision_time",
    "session_open_price",
    "breakout_strength",
    "relative_strength_vs_spy",
    "rank_max_rvol",
    "rank_equal_rank_strength",
    "build_wait_row",
    "build_all_arms_rows",
    "build_session_index",
    "trade_age_in_sessions",
    "recency_weight",
    "recency_weighted_trade_quality",
]


class CandidateSelectionTimingError(RuntimeError):
    """Refused: M41 candidate-selection timing/ranking tooling could not
    safely proceed."""


# ---------------------------------------------------------------------------
# Frozen configuration: the single Issue #325 CHALLENGER, no new threshold.

CONFIG_LABEL = "GAP_050_RVOL_175"
CONFIG_ENTRY = FROZEN_CONFIG_SET[CONFIG_LABEL]
if CONFIG_ENTRY.combo.gap_percent != Decimal("0.50") or CONFIG_ENTRY.combo.rvol_multiple != Decimal("1.75"):
    raise CandidateSelectionTimingError(f"{CONFIG_LABEL} does not resolve to gap=0.50%% rvol=1.75x")

DECISION_CUTOFF_TIME = time(9, 50)

ANCHOR_DATE = date(2026, 8, 13)
HALF_LIFE_SESSIONS = 504

ARM_CURRENT_FIRST_VALID = "CURRENT_FIRST_VALID"
ARM_EARLY_FIRST_VALID = "EARLY_FIRST_VALID"
ARM_WAITED_FIRST_VALID_0950 = "WAITED_FIRST_VALID_0950"
ARM_RANKED_POOL_0950_MAX_RVOL = "RANKED_POOL_0950_MAX_RVOL"
ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH = "RANKED_POOL_0950_EQUAL_RANK_STRENGTH"
ARMS: tuple[str, ...] = (
    ARM_CURRENT_FIRST_VALID, ARM_EARLY_FIRST_VALID, ARM_WAITED_FIRST_VALID_0950,
    ARM_RANKED_POOL_0950_MAX_RVOL, ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH,
)


# ---------------------------------------------------------------------------
# Candidate-pool construction: pure filtering of already-computed
# candidate_signals, no re-scan of bars, no new breakout logic.

def early_pool(candidate_signals: Sequence[CandidateSignal], *, cutoff: time = DECISION_CUTOFF_TIME) -> tuple[CandidateSignal, ...]:
    """Every candidate signal whose own `LiveData.timestamp` (ET) is at or
    before `cutoff` -- see module docstring for why this is exactly
    equivalent to re-scanning a truncated 09:40-09:50 window."""
    return tuple(c for c in candidate_signals if c[3].timestamp.astimezone(EASTERN).time() <= cutoff)


def group_by_day(candidate_signals: Sequence[CandidateSignal]) -> dict[date, list[CandidateSignal]]:
    by_day: dict[date, list[CandidateSignal]] = {}
    for c in candidate_signals:
        by_day.setdefault(c[1], []).append(c)
    return by_day


def live_data_at_or_after(window: Sequence[tuple[Any, LiveData]], *, cutoff: time = DECISION_CUTOFF_TIME) -> tuple[Any, LiveData] | None:
    """The first `(bar, live_data)` pair in a `SessionSignalInputs.window`
    (already precomputed by `precompute_inputs_table`, covering the FULL
    09:40-11:00 entry window regardless of which arm is asking) whose
    `LiveData.timestamp` (ET) is at or after `cutoff`. `None` if no bar in
    the window reaches `cutoff` -- a real data limitation, never guessed
    around."""
    for bar, live_data in window:
        if live_data.timestamp.astimezone(EASTERN).time() >= cutoff:
            return bar, live_data
    return None


@dataclass(frozen=True)
class DecisionTimeCandidate:
    """One candidate, enriched with what is knowable at the 09:50 decision
    point. `decision_bar`/`decision_live_data` are `None` when the
    session's own window has no bar at/after `cutoff` (an honest data gap,
    never fabricated)."""

    symbol: str
    session_date: date
    opening_data: OpeningData
    original_live_data: LiveData
    original_decision: SignalDecision
    decision_bar: Any | None
    decision_live_data: LiveData | None


def enrich_with_decision_time(
    candidate: CandidateSignal, *, inputs_table: InputsTable, cutoff: time = DECISION_CUTOFF_TIME,
) -> DecisionTimeCandidate:
    symbol, session_date, opening_data, live_data, decision = candidate
    window = inputs_table[(symbol, session_date)].window
    found = live_data_at_or_after(window, cutoff=cutoff)
    decision_bar, decision_live_data = found if found is not None else (None, None)
    return DecisionTimeCandidate(
        symbol=symbol, session_date=session_date, opening_data=opening_data,
        original_live_data=live_data, original_decision=decision,
        decision_bar=decision_bar, decision_live_data=decision_live_data,
    )


# ---------------------------------------------------------------------------
# Ranking factors -- pure functions over already-available data, no new
# price/return semantics invented beyond the operator's own frozen
# formulas.

def session_open_price(bars: Sequence[MinuteBar], *, session_date: date) -> Decimal:
    """The first non-interpolated bar's own open price for `session_date`
    -- the identical technique `precompute_session_signal_inputs` already
    uses for the traded symbol's own `today_open`, applied here to SPY
    (which is not itself part of the traded universe, so it has no
    `OpeningData` of its own to read this from)."""
    today_bars = sorted(
        (b for b in bars if b.timestamp_et.date() == session_date and not b.interpolated),
        key=lambda b: b.begins_at,
    )
    if not today_bars:
        raise CandidateSelectionTimingError(f"no bars for session_date={session_date}")
    return today_bars[0].open_price


def breakout_strength(*, opening_data: OpeningData, price_at_decision: Decimal) -> Decimal:
    """(decision-time price - opening-range high) / opening-range width.
    Already dimensionless (a price difference over a price difference);
    positive means still trading above the opening-range high at decision
    time, more positive means further above it."""
    width = opening_data.opening_range_high - opening_data.opening_range_low
    return (price_at_decision - opening_data.opening_range_high) / width


def relative_strength_vs_spy(
    *, opening_data: OpeningData, price_at_decision: Decimal, spy_price_at_decision: Decimal, spy_open: Decimal,
) -> Decimal:
    """Stock return from session open through the decision timestamp,
    minus SPY's return over the identical interval."""
    stock_return = price_at_decision / opening_data.today_open - 1
    spy_return = spy_price_at_decision / spy_open - 1
    return stock_return - spy_return


def _tie_break_key(c: DecisionTimeCandidate) -> tuple[datetime, str]:
    return (c.original_live_data.timestamp, c.symbol)


def rank_max_rvol(pool: Sequence[DecisionTimeCandidate]) -> DecisionTimeCandidate:
    """RANKER A: highest opening RVOL wins; ties broken by earlier
    qualifying signal, then alphabetical symbol."""
    return min(pool, key=lambda c: (-c.original_decision.relative_volume, *_tie_break_key(c)))


def _integer_ranks(pool: Sequence[DecisionTimeCandidate], *, value) -> dict[str, int]:
    ordered = sorted(pool, key=lambda c: (-value(c), *_tie_break_key(c)))
    return {c.symbol: rank for rank, c in enumerate(ordered, start=1)}


def rank_equal_rank_strength(pool: Sequence[DecisionTimeCandidate], *, spy_open: Decimal) -> DecisionTimeCandidate:
    """RANKER B: equal-weight ordinal ranking across gap magnitude,
    opening RVOL, breakout strength, and relative strength versus SPY, all
    at the 09:50 decision snapshot. Falls back to the (timestamp, symbol)
    tie-break alone if no pool member has decision-time data, so this
    function always returns exactly one winner."""
    usable = [c for c in pool if c.decision_live_data is not None]
    if not usable:
        return min(pool, key=_tie_break_key)

    prices = {c.symbol: c.decision_live_data.completed_bar_close for c in usable}
    bstrength = {c.symbol: breakout_strength(opening_data=c.opening_data, price_at_decision=prices[c.symbol]) for c in usable}
    rstrength = {
        c.symbol: relative_strength_vs_spy(
            opening_data=c.opening_data, price_at_decision=prices[c.symbol],
            spy_price_at_decision=c.decision_live_data.spy_price, spy_open=spy_open,
        )
        for c in usable
    }
    gap_ranks = _integer_ranks(usable, value=lambda c: c.original_decision.gap_percent)
    rvol_ranks = _integer_ranks(usable, value=lambda c: c.original_decision.relative_volume)
    bstrength_ranks = _integer_ranks(usable, value=lambda c: bstrength[c.symbol])
    rstrength_ranks = _integer_ranks(usable, value=lambda c: rstrength[c.symbol])

    def average_rank(c: DecisionTimeCandidate) -> float:
        return (gap_ranks[c.symbol] + rvol_ranks[c.symbol] + bstrength_ranks[c.symbol] + rstrength_ranks[c.symbol]) / 4.0

    return min(usable, key=lambda c: (average_rank(c), *_tie_break_key(c)))


# ---------------------------------------------------------------------------
# WAIT-to-09:50 execution: reuses the unmodified SignalEngine/shadow
# decision/execution simulator, never holds the original breakout price
# with a later timestamp.

def _session_bars(bars: Sequence[MarketBar], *, session_date: date) -> list[MarketBar]:
    return sorted((bar for bar in bars if bar.trading_date == session_date), key=lambda bar: bar.event_timestamp)


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


def _no_decision_time_data_outcome() -> dict[str, Any]:
    """Same key shape as `_outcome_fields`, for the honest case where a
    candidate's session has no bar at/after the decision cutoff -- never
    fabricated as a fill."""
    return {
        "state": "NO_DECISION_TIME_DATA", "entered": False, "closed": False,
        "entry_price": None, "initial_stop_price": None, "exit_price": None, "exit_reason": None,
        "realized_pnl": None, "r_multiple": None, "is_winner": None, "is_stopped": False,
        "initial_risk_r": None, "maximum_favorable_excursion": None, "maximum_adverse_excursion": None,
        "mfe_r": None, "mae_r": None,
    }


def _decision_features(*, period: str, view: str, candidate: DecisionTimeCandidate) -> dict[str, Any]:
    session_date = candidate.session_date
    return {
        "period": period, "view": view, "config": CONFIG_LABEL, "symbol": candidate.symbol,
        "session_date": session_date.isoformat(), "month": session_date.strftime("%Y-%m"),
        "quarter": f"{session_date.year}-Q{(session_date.month - 1) // 3 + 1}",
        "original_qualifying_timestamp": candidate.original_live_data.timestamp.isoformat(),
        "decision_timestamp": (
            candidate.decision_live_data.timestamp.isoformat() if candidate.decision_live_data is not None else None
        ),
    }


def build_wait_row(
    candidate: DecisionTimeCandidate, *, period: str, view: str, combo_config: Mapping[str, Any],
    strategy_version: str, implementation_digest: str, shadow_run_id: str, created_at: datetime,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], session_bounds_provider=None,
) -> TradeDiagnosticRow:
    """One `TradeDiagnosticRow` for a WAIT-to-09:50 execution attempt.
    Never reuses `candidate.original_decision`/`original_live_data` as the
    fill -- re-runs the real, unmodified `SignalEngine` at
    `candidate.decision_live_data` (the bar at/after the cutoff) for a
    fresh entry price and eligibility check, exactly as a trader who
    waited until then would see. If the setup no longer approves (or no
    decision-time bar exists at all), records that honestly rather than
    forcing a trade."""
    if candidate.decision_live_data is None:
        return TradeDiagnosticRow(
            decision=_decision_features(period=period, view=view, candidate=candidate),
            outcome=_no_decision_time_data_outcome(),
        )

    evaluation_config = copy.deepcopy(dict(combo_config))
    evaluation_config["strategy"]["enabled"] = True  # in-memory only; never persisted
    engine = SignalEngine(evaluation_config)
    new_decision = engine.evaluate(candidate.opening_data, candidate.decision_live_data)

    candidate_bars = market_bars_by_symbol[candidate.symbol]
    spy_bars = market_bars_by_symbol["SPY"]
    shadow_decision = build_candidate_shadow_decision(
        symbol=candidate.symbol, session_date=candidate.session_date, opening_data=candidate.opening_data,
        live_data=candidate.decision_live_data, decision=new_decision, combo_config=combo_config,
        strategy_version=strategy_version, implementation_digest=implementation_digest,
        shadow_run_id=shadow_run_id, created_at=created_at,
        candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
    )
    session_bars = _session_bars(candidate_bars, session_date=candidate.session_date)
    subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    result = simulate_shadow_execution(shadow_decision, subsequent_bars, **kwargs)

    decision_features = _decision_features(period=period, view=view, candidate=candidate)
    decision_features["wait_reevaluation_approved"] = new_decision.approved
    return TradeDiagnosticRow(decision=decision_features, outcome=_outcome_fields(result))


# ---------------------------------------------------------------------------
# Per-period orchestration: builds all five arms' rows from one already-
# computed full-window `ComboEvaluation` + `InputsTable`. No new candidate
# generation beyond what `evaluate_combo` (unmodified) already produced.

def build_all_arms_rows(
    *, period: str, evaluation: ComboEvaluation, inputs_table: InputsTable,
    market_bars_by_symbol: Mapping[str, Sequence[MarketBar]], minute_bars_by_symbol: Mapping[str, Sequence[MinuteBar]],
    base_config: Mapping[str, Any], implementation_digest: str, created_at: datetime, session_bounds_provider=None,
) -> dict[str, tuple[TradeDiagnosticRow, ...]]:
    if evaluation.combo != CONFIG_ENTRY.combo:
        raise CandidateSelectionTimingError("build_all_arms_rows requires the frozen GAP_050_RVOL_175 combo")

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
    rows_current = build_stability_rows(period=period, view=ARM_CURRENT_FIRST_VALID, candidate_signals=current_winners, **common_kwargs)
    rows_early = build_stability_rows(period=period, view=ARM_EARLY_FIRST_VALID, candidate_signals=early_winners, **common_kwargs)

    waited_enriched = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)
    shadow_run_id_waited = f"m41-{period}-{ARM_WAITED_FIRST_VALID_0950}-{CONFIG_LABEL}"
    rows_waited = tuple(
        build_wait_row(
            c, period=period, view=ARM_WAITED_FIRST_VALID_0950, combo_config=combo_config,
            strategy_version=strategy_version, implementation_digest=implementation_digest,
            shadow_run_id=shadow_run_id_waited, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, session_bounds_provider=session_bounds_provider,
        )
        for c in waited_enriched
    )

    early_enriched_pool = [enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_signals]
    pool_by_day = group_by_day(early_signals)
    enriched_by_day: dict[date, list[DecisionTimeCandidate]] = {}
    for c in early_enriched_pool:
        enriched_by_day.setdefault(c.session_date, []).append(c)
    spy_open_by_day = {day: session_open_price(minute_bars_by_symbol["SPY"], session_date=day) for day in enriched_by_day}

    ranked_max_rvol_winners = [rank_max_rvol(day_pool) for day_pool in enriched_by_day.values()]
    shadow_run_id_max = f"m41-{period}-{ARM_RANKED_POOL_0950_MAX_RVOL}-{CONFIG_LABEL}"
    rows_ranked_max_rvol = tuple(
        build_wait_row(
            c, period=period, view=ARM_RANKED_POOL_0950_MAX_RVOL, combo_config=combo_config,
            strategy_version=strategy_version, implementation_digest=implementation_digest,
            shadow_run_id=shadow_run_id_max, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, session_bounds_provider=session_bounds_provider,
        )
        for c in ranked_max_rvol_winners
    )

    ranked_equal_rank_winners = [
        rank_equal_rank_strength(day_pool, spy_open=spy_open_by_day[day]) for day, day_pool in enriched_by_day.items()
    ]
    shadow_run_id_equal = f"m41-{period}-{ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH}-{CONFIG_LABEL}"
    rows_ranked_equal_rank = tuple(
        build_wait_row(
            c, period=period, view=ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH, combo_config=combo_config,
            strategy_version=strategy_version, implementation_digest=implementation_digest,
            shadow_run_id=shadow_run_id_equal, created_at=created_at,
            market_bars_by_symbol=market_bars_by_symbol, session_bounds_provider=session_bounds_provider,
        )
        for c in ranked_equal_rank_winners
    )

    return {
        ARM_CURRENT_FIRST_VALID: rows_current,
        ARM_EARLY_FIRST_VALID: rows_early,
        ARM_WAITED_FIRST_VALID_0950: rows_waited,
        ARM_RANKED_POOL_0950_MAX_RVOL: rows_ranked_max_rvol,
        ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH: rows_ranked_equal_rank,
    }


# ---------------------------------------------------------------------------
# Secondary recency-weighted deployment view: per-trade exponential decay,
# half-life 504 trading sessions, anchor date 2026-08-13. Never produces a
# weighted cumulative-R figure -- chronological cumulative R/drawdown stay
# exclusively in the unweighted, primary view
# (`orb_baseline_fourth_period_research.max_drawdown_and_streak_from_rows`).

def build_session_index(*, start_date: date = date(2022, 1, 1), end_date: date = ANCHOR_DATE) -> dict[date, int]:
    """Every research-calendar trading session in `[start_date, end_date]`
    mapped to its 0-based ascending ordinal index -- built once, reused to
    compute every trade's exact session-count age from the anchor date."""
    sessions: list[date] = []
    cursor = start_date
    while cursor <= end_date:
        if research_regular_session_bounds(cursor) is not None:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return {day: index for index, day in enumerate(sessions)}


def trade_age_in_sessions(trade_date: date, *, session_index: Mapping[date, int], anchor_date: date = ANCHOR_DATE) -> int:
    return session_index[anchor_date] - session_index[trade_date]


def recency_weight(
    trade_date: date, *, session_index: Mapping[date, int], anchor_date: date = ANCHOR_DATE,
    half_life_sessions: int = HALF_LIFE_SESSIONS,
) -> float:
    age = trade_age_in_sessions(trade_date, session_index=session_index, anchor_date=anchor_date)
    return 2.0 ** (-age / half_life_sessions)


def recency_weighted_trade_quality(
    rows: Sequence[TradeDiagnosticRow], *, session_index: Mapping[date, int], anchor_date: date = ANCHOR_DATE,
    half_life_sessions: int = HALF_LIFE_SESSIONS,
) -> dict[str, Any]:
    closed = [r for r in rows if r.outcome["closed"]]
    weighted: list[tuple[float, float]] = []
    for row in closed:
        trade_date = date.fromisoformat(row.decision["session_date"])
        w = recency_weight(trade_date, session_index=session_index, anchor_date=anchor_date, half_life_sessions=half_life_sessions)
        weighted.append((w, row.outcome["r_multiple"]))

    total_weight = sum(w for w, _ in weighted)
    if not weighted or total_weight <= 0:
        return {
            "closed_trades": len(closed), "sum_weights": total_weight,
            "weighted_expectancy_r": None, "weighted_win_rate": None,
            "weighted_gross_profit_r": None, "weighted_gross_loss_r": None, "weighted_profit_factor_r": None,
        }

    weighted_expectancy_r = sum(w * r for w, r in weighted) / total_weight
    weighted_win_rate = sum(w for w, r in weighted if r > 0) / total_weight
    weighted_gross_profit_r = sum(w * r for w, r in weighted if r > 0)
    weighted_gross_loss_r = abs(sum(w * r for w, r in weighted if r < 0))
    if weighted_gross_loss_r > 0:
        weighted_profit_factor_r = weighted_gross_profit_r / weighted_gross_loss_r
    else:
        weighted_profit_factor_r = float("inf") if weighted_gross_profit_r > 0 else None

    return {
        "closed_trades": len(closed), "sum_weights": total_weight,
        "weighted_expectancy_r": weighted_expectancy_r, "weighted_win_rate": weighted_win_rate,
        "weighted_gross_profit_r": weighted_gross_profit_r, "weighted_gross_loss_r": weighted_gross_loss_r,
        "weighted_profit_factor_r": weighted_profit_factor_r,
    }
