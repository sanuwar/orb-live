"""Research tooling for the ORB regime diagnostic study (Issue #310).

Issue #304 (merged) found gap=0.50%/rvol=1.25x and gap=0.50%/rvol=1.50x
both classified NOT_ROBUST on a 252-session out-of-sample period, after
showing strong expectancy/profit factor on the 90-session discovery
period. This module does not tune a new threshold; it explains, using
only decision-time-knowable information, whether the discovery-period
edge was broadly present or concentrated in identifiable regimes, and
whether any such regime signal survives in *both* periods.

Reuse, not reinterpretation: every candidate/trade in this module comes
from the identical unmodified pipeline #298/#300/#302/#304/#305 already
validated -- `precompute_inputs_table`/`evaluate_combo`
(`src.orb_gap_rvol_sensitivity_research`) for candidate selection, and
`simulate_shadow_execution` (`src.shadow_execution_simulator`) for trade
outcomes. `SignalDecision.gap_percent`/`relative_volume` -- the canonical
engine's own computed values -- are used directly for the gap/RVOL
features rather than recomputed here, so there is no risk of divergence
from what actually gated the candidate.

No look-ahead: every decision-time feature in a `TradeDiagnosticRow` is
computable from data at or before `live_data.timestamp` (the completed-
bar decision instant). Outcome fields are kept in a disjoint set of keys
(`OUTCOME_FIELDS`) and a dedicated test
(`tests/test_orb_regime_diagnostic_research.py`) asserts no outcome field
name ever appears among the decision-time feature keys.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from statistics import mean, pstdev
from typing import Any, Mapping, Sequence

from src.exchange_calendar import EASTERN as _ET
from src.market_data import MinuteBar
from src.orb_gap_rvol_sensitivity_research import (
    ComboEvaluation,
    ParameterCombo,
    build_candidate_shadow_decision,
    sensitivity_config,
)
from src.orb_out_of_sample_validation_research import (
    candidate_multiplicity,
    same_day_symbol_combinations,
    select_first_candidate_per_day,
)
from src.shadow_execution_simulator import ShadowExecutionResult, simulate_shadow_execution

__all__ = [
    "RegimeDiagnosticError",
    "PRIMARY_CONFIGS",
    "PRIMARY_CONFIG_LABELS",
    "BASELINE_CONFIG",
    "MIN_SAMPLE_CLOSED_TRADES",
    "MIN_SAMPLE_CANDIDATE_DAYS",
    "GAP_BUCKETS",
    "RVOL_BUCKETS",
    "BREAKOUT_TIME_BUCKETS",
    "gap_bucket",
    "rvol_bucket",
    "breakout_time_bucket",
    "spy_vwap_premium_bucket",
    "spy_gap_direction",
    "or_width_bucket",
    "volatility_bucket",
    "trailing_realized_volatility",
    "DECISION_TIME_FIELDS",
    "OUTCOME_FIELDS",
    "TradeDiagnosticRow",
    "spy_daily_context",
    "build_diagnostic_rows",
    "regime_summary",
    "symbol_summary",
    "month_summary",
    "walk_forward_quarters",
    "classify_regime_signal",
    "trade_quality_from_rows",
    "candidate_multiplicity",
    "same_day_symbol_combinations",
    "select_first_candidate_per_day",
]


class RegimeDiagnosticError(RuntimeError):
    """Refused: regime diagnostic tooling could not safely proceed."""


# ---------------------------------------------------------------------------
# Primary configurations. Fixed by the study's own goal document -- this is
# diagnosis of the existing #304 result, not a new sweep.
# ---------------------------------------------------------------------------

PRIMARY_CONFIG_A = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.25"))
PRIMARY_CONFIG_B = ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.50"))
PRIMARY_CONFIG_C = ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("1.50"))
BASELINE_CONFIG = ParameterCombo(gap_percent=Decimal("1.00"), rvol_multiple=Decimal("3.00"))

PRIMARY_CONFIGS: tuple[ParameterCombo, ...] = (PRIMARY_CONFIG_A, PRIMARY_CONFIG_B, PRIMARY_CONFIG_C)
PRIMARY_CONFIG_LABELS: Mapping[ParameterCombo, str] = {
    PRIMARY_CONFIG_A: "A_0.50gap_1.25rvol",
    PRIMARY_CONFIG_B: "B_0.50gap_1.50rvol",
    PRIMARY_CONFIG_C: "C_0.75gap_1.50rvol",
    BASELINE_CONFIG: "CONTROL_1.00gap_3.00rvol",
}

#: Predeclared BEFORE interpreting any subgroup result (goal item
#: "Performance by regime" / "Robustness"). A regime bucket with fewer
#: closed trades than this is reported but never called meaningful.
MIN_SAMPLE_CLOSED_TRADES = 15

#: Same discipline for candidate-day-level groupings (e.g. multiplicity).
MIN_SAMPLE_CANDIDATE_DAYS = 15


# ---------------------------------------------------------------------------
# Descriptive bucket definitions. Exact, non-overlapping, established
# before looking at any P&L (goal items D/E/C).
# ---------------------------------------------------------------------------

GAP_BUCKETS: tuple[tuple[str, Decimal, Decimal | None], ...] = (
    ("0.50-0.74", Decimal("0.50"), Decimal("0.75")),
    ("0.75-0.99", Decimal("0.75"), Decimal("1.00")),
    ("1.00-1.49", Decimal("1.00"), Decimal("1.50")),
    (">=1.50", Decimal("1.50"), None),
)

RVOL_BUCKETS: tuple[tuple[str, Decimal, Decimal | None], ...] = (
    ("1.25-1.49", Decimal("1.25"), Decimal("1.50")),
    ("1.50-1.99", Decimal("1.50"), Decimal("2.00")),
    ("2.00-2.99", Decimal("2.00"), Decimal("3.00")),
    (">=3.00", Decimal("3.00"), None),
)

#: Canonical entry window is 09:40-11:00 (config/strategy_v0.yaml); these
#: three buckets partition it exactly.
BREAKOUT_TIME_BUCKETS: tuple[tuple[str, time, time], ...] = (
    ("09:40-09:59", time(9, 40), time(10, 0)),
    ("10:00-10:29", time(10, 0), time(10, 30)),
    ("10:30-11:00", time(10, 30), time(11, 1)),  # upper bound exclusive, covers 11:00 exactly
)


def _bucket_by_ranges(value: Decimal, ranges: Sequence[tuple[str, Decimal, Decimal | None]]) -> str:
    for label, lower, upper in ranges:
        if value >= lower and (upper is None or value < upper):
            return label
    return "OUT_OF_RANGE"


def gap_bucket(gap_percent: Decimal) -> str:
    return _bucket_by_ranges(gap_percent, GAP_BUCKETS)


def rvol_bucket(relative_volume: Decimal) -> str:
    return _bucket_by_ranges(relative_volume, RVOL_BUCKETS)


def breakout_time_bucket(decision_timestamp_et: time) -> str:
    for label, lower, upper in BREAKOUT_TIME_BUCKETS:
        if lower <= decision_timestamp_et < upper:
            return label
    return "OUT_OF_WINDOW"


def spy_vwap_premium_bucket(premium_pct: float) -> str:
    """Bucketed by SPY price's magnitude above/below its own VWAP at
    decision time -- not just the boolean gate `require_spy_above_vwap`
    already enforces, but how far above."""
    if premium_pct < 0:
        return "BELOW_VWAP"
    if premium_pct < 0.05:
        return "0.00-0.05_ABOVE"
    if premium_pct < 0.15:
        return "0.05-0.15_ABOVE"
    return ">=0.15_ABOVE"


def spy_gap_direction(spy_gap_pct: float) -> str:
    if spy_gap_pct > 0.05:
        return "UP"
    if spy_gap_pct < -0.05:
        return "DOWN"
    return "FLAT"


def or_width_bucket(width_pct: float) -> str:
    if width_pct < 0.5:
        return "<0.50"
    if width_pct < 1.0:
        return "0.50-0.99"
    if width_pct < 2.0:
        return "1.00-1.99"
    return ">=2.00"


def volatility_bucket(trailing_vol_pct: float | None) -> str:
    if trailing_vol_pct is None:
        return "UNKNOWN"
    if trailing_vol_pct < 0.5:
        return "LOW_<0.50"
    if trailing_vol_pct < 1.0:
        return "MODERATE_0.50-0.99"
    return "HIGH_>=1.00"


# ---------------------------------------------------------------------------
# SPY daily context, computed once per period from SPY's own minute bars.
# Every value here uses only bars up to and including the given date's
# own opening bars (today_open) or strictly before it (prior_close,
# trailing volatility) -- never a later high/low/close.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpyDayContext:
    session_date: date
    prior_close: Decimal | None
    today_open: Decimal | None
    daily_close: Decimal  # the day's own close -- outcome-only, never used to select a trade


def spy_daily_context(spy_bars: Sequence[MinuteBar]) -> dict[date, SpyDayContext]:
    """One `SpyDayContext` per trading date present in `spy_bars`, sorted
    chronologically. `prior_close`/`today_open` are the only fields
    decision-time code may read; `daily_close` is retained purely for
    later realized-volatility bucketing of *other* days (never the day's
    own trade decision) and is documented as outcome-only."""
    by_date: dict[date, list[MinuteBar]] = defaultdict(list)
    for bar in spy_bars:
        if bar.interpolated:
            continue
        by_date[bar.timestamp_et.date()].append(bar)
    ordered_dates = sorted(by_date)
    contexts: dict[date, SpyDayContext] = {}
    previous_close: Decimal | None = None
    for session_date in ordered_dates:
        day_bars = sorted(by_date[session_date], key=lambda b: b.begins_at)
        today_open = day_bars[0].open_price
        daily_close = day_bars[-1].close_price
        contexts[session_date] = SpyDayContext(
            session_date=session_date, prior_close=previous_close, today_open=today_open, daily_close=daily_close,
        )
        previous_close = daily_close
    return contexts


def trailing_realized_volatility(
    contexts_by_date: Mapping[date, SpyDayContext], *, before: date, lookback_days: int = 20,
) -> float | None:
    """Population stdev of SPY's trailing `lookback_days` daily returns,
    using only days strictly before `before` -- a decision-time-safe
    volatility proxy requiring no external/paid data source. Returns
    `None` if fewer than `lookback_days + 1` prior closes exist."""
    prior_dates = sorted(d for d in contexts_by_date if d < before)
    if len(prior_dates) < lookback_days + 1:
        return None
    window = prior_dates[-(lookback_days + 1):]
    closes = [contexts_by_date[d].daily_close for d in window]
    returns = [float(closes[i] / closes[i - 1] - 1) * 100 for i in range(1, len(closes))]
    return pstdev(returns) if len(returns) > 1 else None


# ---------------------------------------------------------------------------
# Trade-level diagnostic rows: decision-time features + outcome, disjoint
# key sets by construction (asserted by test).
# ---------------------------------------------------------------------------

DECISION_TIME_FIELDS: tuple[str, ...] = (
    "period", "config", "symbol", "session_date", "month", "quarter", "day_of_week",
    "gap_percent", "gap_bucket", "relative_volume", "rvol_bucket",
    "breakout_time_et", "breakout_time_bucket",
    "spy_price", "spy_vwap", "spy_vwap_premium_pct", "spy_vwap_premium_bucket",
    "spy_prior_close", "spy_today_open", "spy_gap_pct", "spy_gap_direction",
    "opening_range_high", "opening_range_low", "opening_range_width_pct", "or_width_bucket",
    "trailing_volatility_pct", "volatility_bucket",
    "candidates_same_day", "is_first_candidate_of_day",
)

OUTCOME_FIELDS: tuple[str, ...] = (
    "state", "entered", "closed", "entry_price", "initial_stop_price", "exit_price", "exit_reason",
    "realized_pnl", "r_multiple", "is_winner", "is_stopped",
)


@dataclass(frozen=True)
class TradeDiagnosticRow:
    decision: dict[str, Any]
    outcome: dict[str, Any]

    def flat(self) -> dict[str, Any]:
        return {**self.decision, **self.outcome}


def _decision_time_features(
    *, period: str, config_label: str, symbol: str, session_date: date, opening_data, live_data, decision,
    spy_contexts: Mapping[date, SpyDayContext], candidates_same_day: int, is_first_candidate_of_day: bool,
) -> dict[str, Any]:
    gap_percent = decision.gap_percent
    relative_volume = decision.relative_volume
    spy_price = live_data.spy_price
    spy_vwap = live_data.spy_vwap
    spy_vwap_premium_pct = float((spy_price / spy_vwap - 1) * 100) if spy_vwap else None
    spy_context = spy_contexts.get(session_date)
    spy_prior_close = spy_context.prior_close if spy_context else None
    spy_today_open = spy_context.today_open if spy_context else None
    spy_gap_pct = (
        float((spy_today_open / spy_prior_close - 1) * 100)
        if spy_prior_close and spy_today_open else None
    )
    or_high, or_low = opening_data.opening_range_high, opening_data.opening_range_low
    or_width_pct = float((or_high - or_low) / or_low * 100) if or_low else None
    trailing_vol = trailing_realized_volatility(spy_contexts, before=session_date)
    breakout_time_et = live_data.timestamp.astimezone(_ET).time() if hasattr(live_data.timestamp, "astimezone") else None

    return {
        "period": period, "config": config_label, "symbol": symbol, "session_date": session_date.isoformat(),
        "month": session_date.strftime("%Y-%m"), "quarter": f"{session_date.year}-Q{(session_date.month - 1) // 3 + 1}",
        "day_of_week": session_date.strftime("%A"),
        "gap_percent": str(gap_percent), "gap_bucket": gap_bucket(gap_percent),
        "relative_volume": str(relative_volume), "rvol_bucket": rvol_bucket(relative_volume),
        "breakout_time_et": breakout_time_et.isoformat() if breakout_time_et else None,
        "breakout_time_bucket": breakout_time_bucket(breakout_time_et) if breakout_time_et else "UNKNOWN",
        "spy_price": str(spy_price), "spy_vwap": str(spy_vwap),
        "spy_vwap_premium_pct": spy_vwap_premium_pct,
        "spy_vwap_premium_bucket": spy_vwap_premium_bucket(spy_vwap_premium_pct) if spy_vwap_premium_pct is not None else "UNKNOWN",
        "spy_prior_close": str(spy_prior_close) if spy_prior_close else None,
        "spy_today_open": str(spy_today_open) if spy_today_open else None,
        "spy_gap_pct": spy_gap_pct,
        "spy_gap_direction": spy_gap_direction(spy_gap_pct) if spy_gap_pct is not None else "UNKNOWN",
        "opening_range_high": str(or_high), "opening_range_low": str(or_low),
        "opening_range_width_pct": or_width_pct,
        "or_width_bucket": or_width_bucket(or_width_pct) if or_width_pct is not None else "UNKNOWN",
        "trailing_volatility_pct": trailing_vol, "volatility_bucket": volatility_bucket(trailing_vol),
        "candidates_same_day": candidates_same_day, "is_first_candidate_of_day": is_first_candidate_of_day,
    }


def _outcome_fields(result: ShadowExecutionResult) -> dict[str, Any]:
    entered = result.entry_price is not None
    closed = result.realized_pnl is not None
    risk = (result.entry_price - result.initial_stop_price) if (entered and result.initial_stop_price is not None) else None
    r_multiple = float(result.realized_pnl / risk) if (closed and risk and risk > 0) else None
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
    }


def build_diagnostic_rows(
    *,
    period: str,
    combo: ParameterCombo,
    config_label: str,
    evaluation: ComboEvaluation,
    market_bars_by_symbol: Mapping[str, Sequence],
    base_config: Mapping[str, Any],
    implementation_digest: str,
    created_at: datetime,
    session_bounds_provider=None,
) -> list[TradeDiagnosticRow]:
    """Builds one `TradeDiagnosticRow` per candidate signal in `evaluation`,
    replaying each through the unmodified shadow-execution simulator for
    outcome fields (identical to
    `orb_out_of_sample_validation_research.simulate_candidate_signals_detailed`,
    inlined here only because this module additionally needs the
    decision-time feature set attached per-row, not just the aggregate).

    `session_bounds_provider`, if given, is forwarded to
    `simulate_shadow_execution`'s own identically-named parameter (Issue
    #311/PR #312); every existing caller omits this and is unaffected. The
    out-of-sample period must supply the research-only provider or every
    2025 outcome silently reverts to DATA_INVALID (the exact bug Issue
    #313 corrected in the #304/#307 study)."""
    spy_bars = market_bars_by_symbol["SPY"]
    spy_contexts = spy_daily_context([bar.to_minute_bar() for bar in spy_bars])
    candidates_per_day = evaluation.candidates_by_session
    first_of_day = {c[:2] for c in select_first_candidate_per_day(evaluation.candidate_signals)}

    combo_config = sensitivity_config(base_config, gap_percent=combo.gap_percent, rvol_multiple=combo.rvol_multiple)
    strategy_version = f"{base_config['strategy']['name']}-{base_config['strategy']['version']}"
    shadow_run_id = f"regime-diagnostic-{period}-{config_label}"

    rows: list[TradeDiagnosticRow] = []
    for symbol, session_date, opening_data, live_data, decision in evaluation.candidate_signals:
        candidate_bars = market_bars_by_symbol[symbol]
        shadow_decision = build_candidate_shadow_decision(
            symbol=symbol, session_date=session_date, opening_data=opening_data, live_data=live_data,
            decision=decision, combo_config=combo_config, strategy_version=strategy_version,
            implementation_digest=implementation_digest, shadow_run_id=shadow_run_id, created_at=created_at,
            candidate_market_bars=candidate_bars, spy_market_bars=spy_bars,
        )
        session_bars = sorted((b for b in candidate_bars if b.trading_date == session_date), key=lambda b: b.event_timestamp)
        subsequent_bars = [b for b in session_bars if b.event_timestamp >= shadow_decision.signal_timestamp]
        sim_kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
        result = simulate_shadow_execution(shadow_decision, subsequent_bars, **sim_kwargs)

        decision_features = _decision_time_features(
            period=period, config_label=config_label, symbol=symbol, session_date=session_date,
            opening_data=opening_data, live_data=live_data, decision=decision, spy_contexts=spy_contexts,
            candidates_same_day=candidates_per_day.get(session_date, 0),
            is_first_candidate_of_day=(symbol, session_date) in first_of_day,
        )
        rows.append(TradeDiagnosticRow(decision=decision_features, outcome=_outcome_fields(result)))
    return rows


# ---------------------------------------------------------------------------
# Aggregation. Every summary below reports sample size alongside any
# ratio and is never interpreted below MIN_SAMPLE_CLOSED_TRADES.
# ---------------------------------------------------------------------------


def trade_quality_from_rows(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Trade-quality aggregate over CLOSED rows, in TWO explicit, distinct
    units -- never conflate them (Issue #322's own correction):

    PRICE/PER-SHARE metrics (`*_price_per_share`, and the identical-valued
    legacy `expectancy_per_trade`/`profit_factor`/`average_winner`/
    `average_loser` kept for backward compatibility with every merged
    caller that already reads them -- #304/#310/#317/#319 and their tests)
    are derived from `realized_pnl`: raw dollars per share, NOT
    risk-normalized. Two trades with identical `realized_pnl` can carry
    very different risk (`entry_price - initial_stop_price`), so these
    figures are not comparable across trades with different stop widths
    and are NOT the right basis for fixed-risk position-sizing decisions.

    R metrics (`*_r`) are derived from `r_multiple` (`realized_pnl` divided
    by that same trade's own initial risk, already computed by the
    unmodified simulator in `_outcome_fields` and never recomputed here).
    `expectancy_r_per_trade` and `profit_factor_r` are risk-normalized and
    are the PRIMARY metrics for any conclusion about fixed-risk account
    sizing. The two units can and do disagree in sign -- a period can show
    positive price-per-share expectancy while its R-expectancy is negative
    (or vice versa) if larger-risk trades happen to land favorably (or
    unfavorably) in raw dollar terms; neither figure should be read as a
    proxy for the other."""
    closed = [r for r in rows if r.outcome["closed"]]
    winners = [r for r in closed if r.outcome["is_winner"]]
    losers = [r for r in closed if not r.outcome["is_winner"]]
    stopped = [r for r in closed if r.outcome["is_stopped"]]
    pnls = [Decimal(r.outcome["realized_pnl"]) for r in closed]
    gross_profit = sum((p for p in pnls if p > 0), Decimal("0"))
    gross_loss = sum((-p for p in pnls if p <= 0), Decimal("0"))
    r_multiples = [r.outcome["r_multiple"] for r in closed if r.outcome["r_multiple"] is not None]
    positive_r = [x for x in r_multiples if x > 0]
    negative_r = [x for x in r_multiples if x <= 0]
    gross_profit_r = sum(positive_r) if positive_r else 0.0
    gross_loss_r = -sum(negative_r) if negative_r else 0.0

    if gross_loss > 0:
        profit_factor: float | None = float(gross_profit / gross_loss)
    elif gross_profit > 0:
        profit_factor = None  # infinite, no losses -- reported as None rather than a magic sentinel here
    else:
        profit_factor = None

    if gross_loss_r > 0:
        profit_factor_r: float | None = gross_profit_r / gross_loss_r
        profit_factor_r_infinite_no_losses = False
    elif gross_profit_r > 0:
        profit_factor_r = None
        profit_factor_r_infinite_no_losses = True
    else:
        profit_factor_r = None
        profit_factor_r_infinite_no_losses = False

    average_winner_price_per_share = float(mean([Decimal(r.outcome["realized_pnl"]) for r in winners])) if winners else None
    average_loser_price_per_share = float(mean([Decimal(r.outcome["realized_pnl"]) for r in losers])) if losers else None
    expectancy_price_per_share = float((gross_profit - gross_loss) / len(closed)) if closed else None

    return {
        "candidate_occurrences": len(rows),
        "closed_trades": len(closed),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": (len(winners) / len(closed)) if closed else None,
        # Legacy raw-price fields, unchanged in value -- kept for every existing caller.
        "average_winner": average_winner_price_per_share,
        "average_loser": average_loser_price_per_share,
        "expectancy_per_trade": expectancy_price_per_share,
        "profit_factor": profit_factor,
        "profit_factor_infinite_no_losses": bool(gross_profit > 0 and gross_loss == 0),
        # Explicit-unit aliases of the same raw-price figures above.
        "average_winner_price_per_share": average_winner_price_per_share,
        "average_loser_price_per_share": average_loser_price_per_share,
        "expectancy_price_per_share": expectancy_price_per_share,
        "profit_factor_price_per_share": profit_factor,
        # R (risk-normalized) fields -- PRIMARY for fixed-risk sizing decisions.
        "average_winner_r": float(mean(positive_r)) if positive_r else None,
        "average_loser_r": float(mean(negative_r)) if negative_r else None,
        "expectancy_r_per_trade": float(mean(r_multiples)) if r_multiples else None,
        "gross_profit_r": float(gross_profit_r),
        "gross_loss_r": float(gross_loss_r),
        "profit_factor_r": profit_factor_r,
        "profit_factor_r_infinite_no_losses": profit_factor_r_infinite_no_losses,
        "stop_out_rate": (len(stopped) / len(closed)) if closed else None,
        "cumulative_r": sum(r_multiples) if r_multiples else None,
        "meets_minimum_sample": len(closed) >= MIN_SAMPLE_CLOSED_TRADES,
    }


def regime_summary(
    rows: Sequence[TradeDiagnosticRow], *, key_field: str,
) -> dict[str, dict[str, Any]]:
    """Groups rows by `decision[key_field]` (e.g. `"gap_bucket"`) and
    reports trade quality per bucket, always alongside sample size."""
    by_bucket: dict[str, list[TradeDiagnosticRow]] = defaultdict(list)
    for row in rows:
        by_bucket[row.decision[key_field]].append(row)
    return {bucket: trade_quality_from_rows(bucket_rows) for bucket, bucket_rows in sorted(by_bucket.items())}


def symbol_summary(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    by_symbol: dict[str, list[TradeDiagnosticRow]] = defaultdict(list)
    for row in rows:
        by_symbol[row.decision["symbol"]].append(row)
    per_symbol = {symbol: trade_quality_from_rows(sym_rows) for symbol, sym_rows in by_symbol.items()}

    closed_rows = [r for r in rows if r.outcome["closed"]]
    total_pnl = sum((Decimal(r.outcome["realized_pnl"]) for r in closed_rows), Decimal("0"))
    pnl_by_symbol = {
        symbol: sum((Decimal(r.outcome["realized_pnl"]) for r in sym_rows if r.outcome["closed"]), Decimal("0"))
        for symbol, sym_rows in by_symbol.items()
    }
    ranked = sorted(pnl_by_symbol.items(), key=lambda kv: kv[1], reverse=True)
    top_contributors = [{"symbol": s, "realized_pnl": str(p)} for s, p in ranked[:3] if p > 0]
    loss_contributors = [{"symbol": s, "realized_pnl": str(p)} for s, p in ranked[-3:] if p < 0]
    top3_share = (
        float(sum(p for _s, p in ranked[:3]) / total_pnl) if total_pnl != 0 else None
    )
    best_symbol_pnl = ranked[0][1] if ranked else Decimal("0")
    profitability_without_best_symbol = total_pnl - best_symbol_pnl
    return {
        "per_symbol": per_symbol,
        "total_realized_pnl": str(total_pnl),
        "top_profitable_contributors": top_contributors,
        "top_loss_contributors": loss_contributors,
        "top_3_share_of_total_pnl": top3_share,
        "profitable_without_best_symbol": bool(profitability_without_best_symbol > 0) if closed_rows else None,
        "total_pnl_excluding_best_symbol": str(profitability_without_best_symbol),
    }


def month_summary(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    return regime_summary(rows, key_field="month")


def walk_forward_quarters(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Descriptive, non-overlapping calendar-quarter split -- no per-block
    tuning, just the same fixed configuration replayed across fixed
    chronological blocks (goal item "Optional walk-forward view")."""
    return regime_summary(rows, key_field="quarter")


# ---------------------------------------------------------------------------
# Anti-data-mining classification (goal item "Robustness"). Criteria fixed
# here, before interpreting any subgroup.
# ---------------------------------------------------------------------------


def classify_regime_signal(
    *, oos_bucket_stats: Mapping[str, Any] | None, discovery_bucket_stats: Mapping[str, Any] | None,
    baseline_expectancy: float | None, symbol_summary_for_bucket: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """PROMISING / MIXED / NO_EVIDENCE / INSUFFICIENT_SAMPLE for one
    regime bucket, against the six criteria goal item "Robustness /
    Anti-data-mining rules" lists: decision-time-knowable (guaranteed by
    construction -- every field driving bucket assignment is in
    `DECISION_TIME_FIELDS`), economically plausible (asserted by the
    caller's choice of which buckets to even test, not re-derived here),
    minimum sample size, material expectancy/PF improvement over the
    unconditional baseline, same-direction improvement in BOTH periods,
    and not dependent on a single symbol."""
    if oos_bucket_stats is None or discovery_bucket_stats is None:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reason": "bucket absent in one period"}
    if not oos_bucket_stats.get("meets_minimum_sample") or not discovery_bucket_stats.get("meets_minimum_sample"):
        return {
            "verdict": "INSUFFICIENT_SAMPLE",
            "reason": (
                f"closed trades oos={oos_bucket_stats.get('closed_trades')} "
                f"discovery={discovery_bucket_stats.get('closed_trades')} (minimum {MIN_SAMPLE_CLOSED_TRADES})"
            ),
        }

    oos_exp = oos_bucket_stats.get("expectancy_per_trade")
    disc_exp = discovery_bucket_stats.get("expectancy_per_trade")
    if oos_exp is None or disc_exp is None or baseline_expectancy is None:
        return {"verdict": "INSUFFICIENT_SAMPLE", "reason": "expectancy not computable in one period"}

    oos_improves = oos_exp > baseline_expectancy
    discovery_improves = disc_exp > baseline_expectancy
    same_direction = oos_improves and discovery_improves
    material = abs(oos_exp - baseline_expectancy) > 0.1 and abs(disc_exp - baseline_expectancy) > 0.1

    single_symbol_dependent = None
    if symbol_summary_for_bucket is not None:
        single_symbol_dependent = symbol_summary_for_bucket.get("profitable_without_best_symbol") is False

    if same_direction and material and single_symbol_dependent is not True:
        verdict = "PROMISING"
    elif oos_improves or discovery_improves:
        verdict = "MIXED"
    else:
        verdict = "NO_EVIDENCE"

    return {
        "verdict": verdict,
        "oos_expectancy": oos_exp, "discovery_expectancy": disc_exp, "baseline_expectancy": baseline_expectancy,
        "oos_improves": oos_improves, "discovery_improves": discovery_improves,
        "same_direction_both_periods": same_direction, "material": material,
        "single_symbol_dependent": single_symbol_dependent,
    }
