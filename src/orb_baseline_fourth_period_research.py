"""Research tooling for the ORB C_BASE fourth-period baseline validation
study (Issue #321).

Issue #319/PR #320 classified C_BASE (gap>=0.50%/RVOL>=1.50x) as
REGIME_DEPENDENT_EDGE across three periods (P1 third, P2 OOS, P3
discovery). This module adds exactly ONE further independent period (P0,
the ~252 sessions immediately preceding P1) and asks whether C_BASE
retains enough underlying edge across a longer history to justify the
queued profit-protection/adaptive-stop experiment.

CRITICAL EFFICIENCY REQUIREMENT (per this study's own goal document): P1,
P2, and P3 are NOT recomputed from minute bars here. Their trade-quality
aggregates (native/cohort/one-trade-per-day, per-symbol contribution,
quarterly buckets) are restated as fixed constants in
`PRIOR_PERIODS_REFERENCE`/`PRIOR_QUARTERS_REFERENCE` below, sourced
verbatim from the merged #320 artifact
(`artifacts/research-baseline-stability/results/orb_baseline_stability_decomposition_report.json`)
-- the identical discipline `orb_out_of_sample_validation_research.DISCOVERY_PERIOD_REFERENCE`
and `orb_rvol_band_third_period_research.PRIOR_PERIOD_BAND_REFERENCE`
already established. Only P0 is newly fetched/precomputed/simulated.

Because no per-trade P1-P3 rows are available (only their aggregate
statistics), this module reconstructs any four-period COMBINED aggregate
(all-candidates, one-trade-per-day, excluding one symbol) via exact
algebraic recombination of `trade_quality_from_rows`-shaped dictionaries
-- `combine_trade_quality_aggregates` and
`trade_quality_excluding_symbol_from_aggregate` below. This is exact
arithmetic (gross profit/loss and cumulative R are both linearly
additive across trades, so summing per-period aggregates reconstructs the
true combined total precisely), NOT an approximation, and requires no
bar-level resimulation of P1-P3. The one genuine limitation: maximum
drawdown requires chronologically-ordered per-trade P&L, which is not
recoverable from aggregate statistics alone -- combined/period max
drawdown for P1-P3 is reported as unavailable rather than estimated, and
is computed only for P0 (whose real per-trade rows exist here).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.market_data_canonical import MarketDataFreshnessPolicy
from src.orb_baseline_stability_research import (
    C_BASE,
    MIN_QUARTER_CLOSED_TRADES,
    TradeDiagnosticRow,
)
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, regime_summary

__all__ = [
    "BaselineFourthPeriodError",
    "P0_CONFIG",
    "ANCHORED_COHORT",
    "PRIOR_PERIODS_REFERENCE",
    "PRIOR_QUARTERS_REFERENCE",
    "derive_freshness_policy_for_window",
    "combine_trade_quality_aggregates",
    "trade_quality_excluding_symbol_from_aggregate",
    "max_drawdown_and_streak_from_rows",
    "symbol_total_pnl",
    "symbol_total_r",
    "merge_quarterly_reference",
    "BaselineConclusionCriteria",
    "DEFAULT_BASELINE_CONCLUSION_CRITERIA",
    "CATASTROPHIC_EXPECTANCY_THRESHOLD",
    "CATASTROPHIC_PROFIT_FACTOR_THRESHOLD",
    "is_p0_catastrophically_unstable",
    "FOUR_PERIOD_STRONG_EVIDENCE_MIN_EXPECTANCY",
    "FOUR_PERIOD_STRONG_EVIDENCE_MIN_PROFIT_FACTOR",
    "four_period_evidence_is_strong_enough",
    "classify_baseline_conclusion",
    "recommend_next_milestone",
]


class BaselineFourthPeriodError(RuntimeError):
    """Refused: fourth-period baseline validation tooling could not safely
    proceed."""


#: P0 tests C_BASE only -- the identical `ParameterCombo` #317/#319 already
#: define, not redefined here.
P0_CONFIG = C_BASE

#: The #320 common cohort, reused unchanged ("anchored") -- never
#: recomputed and never adjusted using P0's own outcomes.
ANCHORED_COHORT: tuple[str, ...] = ("AAPL", "AMD", "AMZN", "BAC", "GOOGL", "INTC", "MSFT", "NVDA", "TSLA")


# ---------------------------------------------------------------------------
# Freshness policy: derived from the actual requested window, not another
# arbitrary magic number. Issue #318 found an 800-day policy silently
# dropped bars older than ~2024-06-07 as STALE for a study whose own
# lookback reached into 2024-03 -- the fix there was a wider hardcoded
# constant (1100 days) picked by hand. This time the policy is computed
# directly from the caller's own `earliest_required_date` and `now`, so it
# can never again be narrower than the window it is meant to cover.
# ---------------------------------------------------------------------------


def derive_freshness_policy_for_window(
    *, earliest_required_date: date, now: datetime, safety_margin_days: int = 30, version: str,
) -> MarketDataFreshnessPolicy:
    """A `MarketDataFreshnessPolicy` whose `max_age_seconds` exactly covers
    the age of `earliest_required_date` relative to `now`, plus
    `safety_margin_days` of headroom (covers e.g. `now` advancing between
    policy construction and the actual fetch). Raises
    `BaselineFourthPeriodError` if `earliest_required_date` is not
    strictly before `now`'s own date -- a caller passing a future date is
    a bug, not something to silently tolerate."""
    now_date = now.astimezone(timezone.utc).date() if now.tzinfo else now.date()
    if earliest_required_date >= now_date:
        raise BaselineFourthPeriodError(
            f"earliest_required_date {earliest_required_date.isoformat()} is not before now {now_date.isoformat()}"
        )
    age_days = (now_date - earliest_required_date).days
    max_age_seconds = float((age_days + safety_margin_days) * 86400)
    return MarketDataFreshnessPolicy(version=version, max_age_seconds=max_age_seconds)


# ---------------------------------------------------------------------------
# Exact algebraic recombination of trade-quality aggregates. Every field
# `trade_quality_from_rows` (`src.orb_regime_diagnostic_research`) computes
# is either a sum (closed_trades, winners, losers, cumulative_r,
# gross_profit_r, gross_loss_r) or is fully reconstructible from a sum
# (gross_profit = average_winner*winners, gross_loss = -average_loser*losers,
# and their R-unit equivalents gross_profit_r = average_winner_r*winners_r,
# gross_loss_r = -average_loser_r*losers_r) -- so summing per-period
# aggregates reconstructs the true combined total exactly, in BOTH units,
# without needing the underlying per-trade rows. Price/per-share and R
# quantities are accumulated in fully independent fields throughout --
# Issue #322's own correction requires they never be conflated.
# ---------------------------------------------------------------------------


@dataclass
class _RawAccumulator:
    closed: float = 0.0
    winners: float = 0.0
    losers: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    cumulative_r: float = 0.0
    stopped: float = 0.0
    # R-unit accumulators. `winners_r`/`losers_r` count trades with a
    # positive/non-positive `r_multiple` -- these coincide with `winners`/
    # `losers` (realized_pnl sign) whenever risk is positive, which it
    # always is for a long position with a protective stop below entry,
    # but are tracked independently rather than assumed identical.
    winners_r: float = 0.0
    losers_r: float = 0.0
    gross_profit_r: float = 0.0
    gross_loss_r: float = 0.0


def _accumulate(tq: Mapping[str, Any] | None, *, sign: float, into: _RawAccumulator) -> None:
    if tq is None:
        return
    closed = tq.get("closed_trades") or 0
    winners = tq.get("winners") or 0
    losers = tq.get("losers") or 0
    avg_winner = tq.get("average_winner")
    avg_loser = tq.get("average_loser")
    cumulative_r = tq.get("cumulative_r") or 0.0
    stop_out_rate = tq.get("stop_out_rate") or 0.0

    into.closed += sign * closed
    into.winners += sign * winners
    into.losers += sign * losers
    if avg_winner is not None and winners:
        into.gross_profit += sign * (avg_winner * winners)
    if avg_loser is not None and losers:
        into.gross_loss += sign * (-avg_loser * losers)
    into.cumulative_r += sign * cumulative_r
    into.stopped += sign * (stop_out_rate * closed)

    # R-unit accumulation. `winners`/`losers` are reused as the R-side trade
    # counts (see the dataclass note above) since `trade_quality_from_rows`
    # does not persist separate R-side winner/loser counts; the R-side
    # gross totals themselves are read directly from `gross_profit_r`/
    # `gross_loss_r` when present (exact), falling back to
    # `average_*_r * winners/losers` only if a caller supplies a
    # differently-shaped aggregate that lacks the gross fields.
    gross_profit_r = tq.get("gross_profit_r")
    gross_loss_r = tq.get("gross_loss_r")
    if gross_profit_r is None:
        avg_winner_r = tq.get("average_winner_r")
        gross_profit_r = (avg_winner_r * winners) if (avg_winner_r is not None and winners) else 0.0
    if gross_loss_r is None:
        avg_loser_r = tq.get("average_loser_r")
        gross_loss_r = (-avg_loser_r * losers) if (avg_loser_r is not None and losers) else 0.0
    into.gross_profit_r += sign * gross_profit_r
    into.gross_loss_r += sign * gross_loss_r
    into.winners_r += sign * winners
    into.losers_r += sign * losers


def _finalize(acc: _RawAccumulator) -> dict[str, Any]:
    closed = acc.closed
    winners = acc.winners
    losers = acc.losers
    gross_profit = acc.gross_profit
    gross_loss = acc.gross_loss

    win_rate = (winners / closed) if closed else None
    average_winner = (gross_profit / winners) if winners else None
    average_loser = (-(gross_loss / losers)) if losers else None
    expectancy_per_trade = ((gross_profit - gross_loss) / closed) if closed else None
    if gross_loss > 0:
        profit_factor: float | None = gross_profit / gross_loss
        profit_factor_infinite_no_losses = False
    elif gross_profit > 0:
        profit_factor = None
        profit_factor_infinite_no_losses = True
    else:
        profit_factor = None
        profit_factor_infinite_no_losses = False
    stop_out_rate = (acc.stopped / closed) if closed else None

    average_winner_r = (acc.gross_profit_r / acc.winners_r) if acc.winners_r else None
    average_loser_r = (-(acc.gross_loss_r / acc.losers_r)) if acc.losers_r else None
    expectancy_r_per_trade = (acc.cumulative_r / closed) if closed else None
    if acc.gross_loss_r > 0:
        profit_factor_r: float | None = acc.gross_profit_r / acc.gross_loss_r
        profit_factor_r_infinite_no_losses = False
    elif acc.gross_profit_r > 0:
        profit_factor_r = None
        profit_factor_r_infinite_no_losses = True
    else:
        profit_factor_r = None
        profit_factor_r_infinite_no_losses = False

    return {
        "closed_trades": int(round(closed)), "winners": int(round(winners)), "losers": int(round(losers)),
        "win_rate": win_rate,
        # Legacy/price-per-share fields (unchanged meaning: raw dollars per share).
        "average_winner": average_winner, "average_loser": average_loser,
        "expectancy_per_trade": expectancy_per_trade, "profit_factor": profit_factor,
        "profit_factor_infinite_no_losses": profit_factor_infinite_no_losses,
        "average_winner_price_per_share": average_winner, "average_loser_price_per_share": average_loser,
        "expectancy_price_per_share": expectancy_per_trade, "profit_factor_price_per_share": profit_factor,
        # R (risk-normalized) fields -- PRIMARY for fixed-risk sizing decisions.
        "average_winner_r": average_winner_r, "average_loser_r": average_loser_r,
        "expectancy_r_per_trade": expectancy_r_per_trade,
        "gross_profit_r": acc.gross_profit_r, "gross_loss_r": acc.gross_loss_r,
        "profit_factor_r": profit_factor_r, "profit_factor_r_infinite_no_losses": profit_factor_r_infinite_no_losses,
        "stop_out_rate": stop_out_rate, "cumulative_r": acc.cumulative_r,
        "meets_minimum_sample": closed >= MIN_SAMPLE_CLOSED_TRADES,
        "reconstructed_from_aggregates": True,
    }


def combine_trade_quality_aggregates(aggregates: Sequence[Mapping[str, Any] | None]) -> dict[str, Any]:
    """Exact combination of any number of `trade_quality_from_rows`-shaped
    aggregates (e.g. one per period) into a single equivalent aggregate,
    as if every underlying trade had been in one combined set. `None`
    entries (a period contributing nothing) are skipped."""
    acc = _RawAccumulator()
    for tq in aggregates:
        _accumulate(tq, sign=1.0, into=acc)
    return _finalize(acc)


def trade_quality_excluding_symbol_from_aggregate(
    period_total: Mapping[str, Any], symbol_stats: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """`period_total` with `symbol_stats`'s own trades algebraically
    subtracted -- the reconstruction-from-aggregates equivalent of
    `orb_baseline_stability_research.excluding_symbol`, used for periods
    (P1-P3) whose per-trade rows are not available here. If `symbol_stats`
    is `None` (the symbol had no trades in this period), returns
    `period_total` unchanged."""
    acc = _RawAccumulator()
    _accumulate(period_total, sign=1.0, into=acc)
    _accumulate(symbol_stats, sign=-1.0, into=acc)
    return _finalize(acc)


def max_drawdown_and_streak_from_rows(rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Maximum drawdown and longest losing streak for P0's own real,
    closed trades -- computable here (unlike for P1-P3, which have no
    surviving per-trade rows) because P0's rows are freshly built from
    real simulator output. Ordered by (`session_date`, `symbol`) as a
    deterministic proxy for exit chronology, not exact microsecond
    exit-timestamp order -- ORB V0 never holds overnight, so every trade
    both enters and exits the same session day, and same-day multi-
    candidate ordering ties are immaterial to an aggregate drawdown
    figure at this sample size. Documented simplification, not a claim of
    exact intraday sequencing."""
    closed = [r for r in rows if r.outcome["closed"]]
    ordered = sorted(closed, key=lambda r: (r.decision["session_date"], r.decision["symbol"]))
    running = Decimal("0")
    peak = Decimal("0")
    max_drawdown = Decimal("0")
    longest_losing_streak = 0
    current_streak = 0
    for row in ordered:
        pnl = Decimal(row.outcome["realized_pnl"])
        running += pnl
        peak = max(peak, running)
        max_drawdown = max(max_drawdown, peak - running)
        if pnl <= 0:
            current_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_streak)
        else:
            current_streak = 0
    return {"maximum_drawdown": str(max_drawdown), "longest_losing_streak": longest_losing_streak}


def symbol_total_pnl(symbol_stats: Mapping[str, Any] | None) -> float:
    """Net realized price-per-share P&L implied by one symbol's own
    aggregate -- gross profit plus gross loss (gross_loss's sign
    convention inside `average_loser` is already negative, so simple
    addition gives the net figure), used only to rank contributors, never
    to filter trades. Dollar-based -- for the R-normalized equivalent used
    by fixed-risk decisions, see `symbol_total_r`."""
    if symbol_stats is None:
        return 0.0
    winners = symbol_stats.get("winners") or 0
    losers = symbol_stats.get("losers") or 0
    avg_winner = symbol_stats.get("average_winner") or 0.0
    avg_loser = symbol_stats.get("average_loser") or 0.0
    return (avg_winner * winners) + (avg_loser * losers)


def symbol_total_r(symbol_stats: Mapping[str, Any] | None) -> float:
    """Net R contributed by one symbol -- `cumulative_r` if the aggregate
    carries it directly (exact), otherwise reconstructed from
    `average_winner_r`/`average_loser_r` and win/loss counts. This is the
    PRIMARY ranking basis for "best contributing symbol" once decisions
    are R-based (Issue #322's own correction) -- it can name a different
    symbol than `symbol_total_pnl` when a large-risk trade dominates
    dollar P&L without dominating R."""
    if symbol_stats is None:
        return 0.0
    cumulative_r = symbol_stats.get("cumulative_r")
    if cumulative_r is not None:
        return float(cumulative_r)
    winners = symbol_stats.get("winners") or 0
    losers = symbol_stats.get("losers") or 0
    avg_winner_r = symbol_stats.get("average_winner_r") or 0.0
    avg_loser_r = symbol_stats.get("average_loser_r") or 0.0
    return (avg_winner_r * winners) + (avg_loser_r * losers)


# ---------------------------------------------------------------------------
# Hardcoded P1-P3 reference. Sourced verbatim from the merged Issue #319/
# PR #320 artifact (C_BASE only, matching this study's own scope) --
# restated here so this study never needs to recompute P1-P3 from bars.
# ---------------------------------------------------------------------------

PRIOR_PERIODS_REFERENCE: Mapping[str, Mapping[str, Any]] = {
    "P1_THIRD": {
        "first_session": "2024-04-03", "last_session": "2025-04-02", "sessions": 252,
        "native_universe": ("NVDA", "AAPL", "TSLA", "AMZN", "MSFT", "GOOGL", "AMD", "BAC", "INTC"),
        "coverage_pct": 0.9788, "coverage_status": "VALID", "candidate_days": 67,
        "native": {
            "closed_trades": 84, "winners": 33, "losers": 51, "win_rate": 0.39285714285714285,
            "average_winner": 3.596363636363636, "average_loser": -2.2894117647058825,
            "expectancy_per_trade": 0.022857142857142857, "profit_factor": 1.0164439876670093,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.47619047619047616,
            "cumulative_r": -1.8504041098656112, "meets_minimum_sample": True,
            "average_winner_r": 1.0495314611811077, "average_loser_r": -0.7153910260557287,
            "expectancy_r_per_trade": -0.02202862035554299,
            "gross_profit_r": 34.63453821897655, "gross_loss_r": 36.484942328842166,
            "profit_factor_r": 0.9492830742834194, "profit_factor_r_infinite_no_losses": False,
        },
        "native_one_trade_per_day": {
            "closed_trades": 64, "winners": 26, "losers": 38, "win_rate": 0.40625,
            "average_winner": 3.7096153846153848, "average_loser": -2.483421052631579,
            "expectancy_per_trade": 0.0325, "profit_factor": 1.022040902829289,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.46875,
            "cumulative_r": -3.990129892468079, "meets_minimum_sample": True,
            "average_winner_r": 0.9477756840526904, "average_loser_r": -0.7534815178378429,
            "expectancy_r_per_trade": -0.062345779569813735,
            "gross_profit_r": 24.64216778536995, "gross_loss_r": 28.63229767783803,
            "profit_factor_r": 0.8606423439235015, "profit_factor_r_infinite_no_losses": False,
        },
        # Cohort == this period's own native universe (see #320); identical figures.
        "cohort": None,  # filled below from native
        "cohort_one_trade_per_day": None,  # filled below from native_one_trade_per_day
        "symbol_contribution": {
            "AAPL": {"closed_trades": 7, "winners": 1, "losers": 6, "average_winner": 3.405, "average_loser": -1.0583333333333333, "cumulative_r": -2.4147391494777732, "expectancy_per_trade": -0.4207142857142857, "profit_factor": 0.5362204724409448, "win_rate": 0.14285714285714285, "stop_out_rate": 0.2857142857142857, "average_winner_r": 1.0460829493087558, "average_loser_r": -0.5768036831310882, "gross_profit_r": 1.0460829493087558, "gross_loss_r": 3.460822098786529},
            "AMD": {"closed_trades": 12, "winners": 3, "losers": 9, "average_winner": 0.8266666666666667, "average_loser": -1.806111111111111, "cumulative_r": -5.240242188455167, "expectancy_per_trade": -1.1479166666666667, "profit_factor": 0.15256844047985235, "win_rate": 0.25, "stop_out_rate": 0.5833333333333334, "average_winner_r": 0.3227999289511581, "average_loser_r": -0.6898491083676269, "gross_profit_r": 0.9683997868534743, "gross_loss_r": 6.208641975308642},
            "AMZN": {"closed_trades": 14, "winners": 2, "losers": 12, "average_winner": 2.245, "average_loser": -1.5391666666666666, "cumulative_r": -4.9436366261831415, "expectancy_per_trade": -0.9985714285714286, "profit_factor": 0.24309691391445587, "win_rate": 0.14285714285714285, "stop_out_rate": 0.6428571428571429, "average_winner_r": 1.6233333333333333, "average_loser_r": -0.6825252744041507, "gross_profit_r": 3.2466666666666666, "gross_loss_r": 8.190303292849809},
            "GOOGL": {"closed_trades": 8, "winners": 3, "losers": 5, "average_winner": 1.3833333333333333, "average_loser": -1.792, "cumulative_r": -1.9741485367770202, "expectancy_per_trade": -0.60125, "profit_factor": 0.46316964285714285, "win_rate": 0.375, "stop_out_rate": 0.625, "average_winner_r": 1.0086171544076599, "average_loser_r": -1.0, "gross_profit_r": 3.02585146322298, "gross_loss_r": 5.0},
            "MSFT": {"closed_trades": 9, "winners": 6, "losers": 3, "average_winner": 3.6275, "average_loser": -1.8466666666666667, "cumulative_r": 4.567390387977363, "expectancy_per_trade": 1.8027777777777778, "profit_factor": 3.92870036101083, "win_rate": 0.6666666666666666, "stop_out_rate": 0.3333333333333333, "average_winner_r": 1.2612317313295605, "average_loser_r": -1.0, "gross_profit_r": 7.567390387977363, "gross_loss_r": 3.0},
            "NVDA": {"closed_trades": 12, "winners": 7, "losers": 5, "average_winner": 1.4328571428571428, "average_loser": -0.425, "cumulative_r": 3.8941258830408607, "expectancy_per_trade": 0.65875, "profit_factor": 4.72, "win_rate": 0.5833333333333334, "stop_out_rate": 0.3333333333333333, "average_winner_r": 0.7317867511090999, "average_loser_r": -0.24567627494456762, "gross_profit_r": 5.122507257763699, "gross_loss_r": 1.2283813747228383},
            "TSLA": {"closed_trades": 22, "winners": 11, "losers": 11, "average_winner": 6.578181818181818, "average_loser": -5.369090909090909, "cumulative_r": 4.260846120009268, "expectancy_per_trade": 0.6045454545454545, "profit_factor": 1.2251947172367084, "win_rate": 0.5, "stop_out_rate": 0.45454545454545453, "average_winner_r": 1.241603609743965, "average_loser_r": -0.8542539624703953, "gross_profit_r": 13.657639707183616, "gross_loss_r": 9.396793587174349},
        },
    },
    "P2_OOS": {
        "first_session": "2025-04-03", "last_session": "2026-04-06", "sessions": 252,
        "native_universe": ("NVDA", "TSLA", "AAPL", "AMZN", "MSFT", "GOOGL", "META", "AVGO", "AMD", "INTC", "BAC", "WMT", "CSCO", "KO", "NKE", "VZ", "T"),
        "coverage_pct": 0.9818, "coverage_status": "VALID", "candidate_days": 94,
        "native": {
            "closed_trades": 150, "winners": 61, "losers": 89, "win_rate": 0.4066666666666667,
            "average_winner": 4.7007377049180326, "average_loser": -2.7873033707865167,
            "expectancy_per_trade": 0.25783333333333336, "profit_factor": 1.1559035756036602,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.41333333333333333,
            "cumulative_r": 0.8218248797360456, "meets_minimum_sample": True,
            "average_winner_r": 0.9989387904723392, "average_loser_r": -0.6754319251581645,
            "expectancy_r_per_trade": 0.005478832531573637,
            "gross_profit_r": 60.93526621881269, "gross_loss_r": 60.113441339076644,
            "profit_factor_r": 1.0136712332787678, "profit_factor_r_infinite_no_losses": False,
        },
        "native_one_trade_per_day": {
            "closed_trades": 93, "winners": 44, "losers": 49, "win_rate": 0.4731182795698925,
            "average_winner": 4.898977272727273, "average_loser": -2.8496938775510205,
            "expectancy_per_trade": 0.8163440860215053, "profit_factor": 1.5437032262684858,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.3333333333333333,
            "cumulative_r": 13.159882533447849, "meets_minimum_sample": True,
            "average_winner_r": 0.9993311724465483, "average_loser_r": -0.6287895725346996,
            "expectancy_r_per_trade": 0.1415041132628801,
            "gross_profit_r": 43.97057158764813, "gross_loss_r": 30.810689054200278,
            "profit_factor_r": 1.4271206823806435, "profit_factor_r_infinite_no_losses": False,
        },
        "cohort": {
            "closed_trades": 112, "winners": 42, "losers": 70, "win_rate": 0.375,
            "average_winner": 4.0753571428571425, "average_loser": -2.398142857142857,
            "expectancy_per_trade": 0.029419642857142856, "profit_factor": 1.0196282837910289,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.42857142857142855,
            "cumulative_r": -6.162757742029823, "meets_minimum_sample": True,
            "average_winner_r": 0.9485009578365263, "average_loser_r": -0.6571399710166276,
            "expectancy_r_per_trade": -0.05502462269669485,
            "gross_profit_r": 39.837040229134104, "gross_loss_r": 45.99979797116393,
            "profit_factor_r": 0.8660264172052865, "profit_factor_r_infinite_no_losses": False,
        },
        "cohort_one_trade_per_day": {
            "closed_trades": 81, "winners": 35, "losers": 46, "win_rate": 0.43209876543209874,
            "average_winner": 4.1894285714285715, "average_loser": -2.259021739130435,
            "expectancy_per_trade": 0.5273456790123456, "profit_factor": 1.4110571139873935,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.345679012345679,
            "cumulative_r": 5.12646374656416, "meets_minimum_sample": True,
            "average_winner_r": 0.9471550753265343, "average_loser_r": -0.5752672988169844,
            "expectancy_r_per_trade": 0.13233746663000323,
            "gross_profit_r": 31.256117485775633, "gross_loss_r": 21.860157355045406,
            "profit_factor_r": 1.429821248681982, "profit_factor_r_infinite_no_losses": False,
        },
        "symbol_contribution": {
            "AAPL": {"closed_trades": 10, "winners": 5, "losers": 5, "average_winner": 3.539, "average_loser": -1.114, "cumulative_r": 3.1756029758681446, "expectancy_per_trade": 1.2125, "profit_factor": 3.1768402154398565, "win_rate": 0.5, "stop_out_rate": 0.2, "average_winner_r": 1.0503004698806697, "average_loser_r": -0.4151798747070408, "gross_profit_r": 5.251502349403348, "gross_loss_r": 2.0758993735352043},
            "AMD": {"closed_trades": 27, "winners": 9, "losers": 18, "average_winner": 4.116666666666666, "average_loser": -2.3455555555555554, "cumulative_r": -4.481342910577494, "expectancy_per_trade": -0.19148148148148147, "profit_factor": 0.8775461866414022, "win_rate": 0.3333333333333333, "stop_out_rate": 0.4074074074074074, "average_winner_r": 0.9122016224590317, "average_loser_r": -0.7050643062615989, "gross_profit_r": 8.209814602131285, "gross_loss_r": 12.69115751270878},
            "AMZN": {"closed_trades": 12, "winners": 5, "losers": 7, "average_winner": 1.17, "average_loser": -1.87, "cumulative_r": -3.8225892156944212, "expectancy_per_trade": -0.6033333333333334, "profit_factor": 0.44690603514132926, "win_rate": 0.4166666666666667, "stop_out_rate": 0.5833333333333334, "average_winner_r": 0.43548215686111574, "average_loser_r": -0.8571428571428571, "gross_profit_r": 2.1774107843055788, "gross_loss_r": 6.0},
            "AVGO": {"closed_trades": 17, "winners": 9, "losers": 8, "average_winner": 5.157777777777778, "average_loser": -4.6375, "cumulative_r": -0.06706701227926135, "expectancy_per_trade": 0.548235294117647, "profit_factor": 1.251212938005391, "win_rate": 0.5294117647058824, "stop_out_rate": 0.29411764705882354, "average_winner_r": 0.6714408261690261, "average_loser_r": -0.7637543059750621, "gross_profit_r": 6.042967435521235, "gross_loss_r": 6.110034447800497},
            "GOOGL": {"closed_trades": 17, "winners": 7, "losers": 10, "average_winner": 2.0414285714285714, "average_loser": -3.575, "cumulative_r": -2.771520253286629, "expectancy_per_trade": -1.2623529411764707, "profit_factor": 0.39972027972027974, "win_rate": 0.4117647058823529, "stop_out_rate": 0.47058823529411764, "average_winner_r": 0.7406319922780229, "average_loser_r": -0.795594419923279, "gross_profit_r": 5.18442394594616, "gross_loss_r": 7.95594419923279},
            "META": {"closed_trades": 13, "winners": 6, "losers": 7, "average_winner": 10.175, "average_loser": -5.674285714285714, "cumulative_r": 4.521283636406405, "expectancy_per_trade": 1.6407692307692308, "profit_factor": 1.5370090634441087, "win_rate": 0.46153846153846156, "stop_out_rate": 0.38461538461538464, "average_winner_r": 1.5874820927531041, "average_loser_r": -0.7148012743017457, "gross_profit_r": 9.524892556518624, "gross_loss_r": 5.00360892011222},
            "MSFT": {"closed_trades": 12, "winners": 5, "losers": 7, "average_winner": 3.232, "average_loser": -2.644285714285714, "cumulative_r": 0.2800527170061716, "expectancy_per_trade": -0.19583333333333333, "profit_factor": 0.8730415991356024, "win_rate": 0.4166666666666667, "stop_out_rate": 0.5833333333333334, "average_winner_r": 1.0560105434012343, "average_loser_r": -0.7142857142857143, "gross_profit_r": 5.280052717006171, "gross_loss_r": 5.0},
            "NVDA": {"closed_trades": 14, "winners": 2, "losers": 12, "average_winner": 2.3925, "average_loser": -1.1891666666666667, "cumulative_r": -2.6990759709955983, "expectancy_per_trade": -0.6775, "profit_factor": 0.3353188507358094, "win_rate": 0.14285714285714285, "stop_out_rate": 0.35714285714285715, "average_winner_r": 1.6259124087591241, "average_loser_r": -0.4959083990428205, "gross_profit_r": 3.2518248175182483, "gross_loss_r": 5.950900788513846},
            "TSLA": {"closed_trades": 20, "winners": 9, "losers": 11, "average_winner": 8.370555555555555, "average_loser": -3.4963636363636366, "cumulative_r": 4.1561149156500035, "expectancy_per_trade": 1.84375, "profit_factor": 1.9587883515340614, "win_rate": 0.45, "stop_out_rate": 0.4, "average_winner_r": 1.1646678903137013, "average_loser_r": -0.5750814633793917, "gross_profit_r": 10.482011012823312, "gross_loss_r": 6.32589609717331},
            "WMT": {"closed_trades": 8, "winners": 4, "losers": 4, "average_winner": 2.0275, "average_loser": -0.845, "cumulative_r": 2.5303659976387247, "expectancy_per_trade": 0.59125, "profit_factor": 2.399408284023669, "win_rate": 0.5, "stop_out_rate": 0.5, "average_winner_r": 1.3825914994096813, "average_loser_r": -0.75, "gross_profit_r": 5.530365997638725, "gross_loss_r": 3.0},
        },
    },
    "P3_DISCOVERY": {
        "first_session": "2026-04-07", "last_session": "2026-08-13", "sessions": 90,
        "native_universe": ("NVDA", "AMZN", "AAPL", "MSFT", "GOOGL", "META", "TSLA", "INTC", "AMD", "AVGO", "NFLX", "BAC", "V", "CSCO", "NOW", "WMT", "CRM", "KO", "JPM", "VZ", "BSX", "NKE", "SLB"),
        "coverage_pct": 0.985, "coverage_status": "VALID", "candidate_days": 40,
        "native": {
            "closed_trades": 57, "winners": 33, "losers": 24, "win_rate": 0.5789473684210527,
            "average_winner": 4.082424242424242, "average_loser": -3.138333333333333,
            "expectancy_per_trade": 1.0421052631578946, "profit_factor": 1.7886351566648964,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.22807017543859648,
            "cumulative_r": 12.932091467615864, "meets_minimum_sample": True,
            "average_winner_r": 0.8596978804063485, "average_loser_r": -0.643247441074735,
            "expectancy_r_per_trade": 0.2268787976774713,
            "gross_profit_r": 28.370030053409504, "gross_loss_r": 15.437938585793638,
            "profit_factor_r": 1.8376825309770493, "profit_factor_r_infinite_no_losses": False,
        },
        "native_one_trade_per_day": {
            "closed_trades": 39, "winners": 22, "losers": 17, "win_rate": 0.5641025641025641,
            "average_winner": 3.753409090909091, "average_loser": -3.2244117647058825,
            "expectancy_per_trade": 0.7117948717948718, "profit_factor": 1.5064307215178327,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.28205128205128205,
            "cumulative_r": 8.40815406498133, "meets_minimum_sample": True,
            "average_winner_r": 0.9079488214575338, "average_loser_r": -0.6803952945343772,
            "expectancy_r_per_trade": 0.21559369397388026,
            "gross_profit_r": 19.97487407206574, "gross_loss_r": 11.566720007084411,
            "profit_factor_r": 1.726926393984767, "profit_factor_r_infinite_no_losses": False,
        },
        "cohort": {
            "closed_trades": 40, "winners": 25, "losers": 15, "win_rate": 0.625,
            "average_winner": 4.2568, "average_loser": -3.4743333333333335,
            "expectancy_per_trade": 1.357625, "profit_factor": 2.042022450350187,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.15,
            "cumulative_r": 15.359737814429272, "meets_minimum_sample": True,
            "average_winner_r": 0.9213270324725822, "average_loser_r": -0.5115625331590189,
            "expectancy_r_per_trade": 0.38399344536073177,
            "gross_profit_r": 23.033175811814555, "gross_loss_r": 7.6734379973852835,
            "profit_factor_r": 3.0016761482484235, "profit_factor_r_infinite_no_losses": False,
        },
        "cohort_one_trade_per_day": {
            "closed_trades": 30, "winners": 17, "losers": 13, "win_rate": 0.5666666666666667,
            "average_winner": 3.7932352941176473, "average_loser": -3.9061538461538463,
            "expectancy_per_trade": 0.4568333333333333, "profit_factor": 1.2698897203623474,
            "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.2,
            "cumulative_r": 8.947631431793168, "meets_minimum_sample": True,
            "average_winner_r": 0.950097162784198, "average_loser_r": -0.5700806067046436,
            "expectancy_r_per_trade": 0.3528844676278674,
            "gross_profit_r": 16.151651767331366, "gross_loss_r": 6.270886673751079,
            "profit_factor_r": 2.5756567783212505, "profit_factor_r_infinite_no_losses": False,
        },
        "symbol_contribution": {
            "AAPL": {"closed_trades": 5, "winners": 3, "losers": 2, "average_winner": 1.9366666666666668, "average_loser": -3.0725, "cumulative_r": 2.323341490869484, "expectancy_per_trade": -0.067, "profit_factor": 0.9454841334418226, "win_rate": 0.6, "stop_out_rate": 0.2, "average_winner_r": 1.3342478280749885, "average_loser_r": -0.8397009966777409, "gross_profit_r": 4.002743484224966, "gross_loss_r": 1.6794019933554818},
            "AMD": {"closed_trades": 7, "winners": 4, "losers": 3, "average_winner": 6.8725, "average_loser": -10.136666666666667, "cumulative_r": 0.5927259240724836, "expectancy_per_trade": -0.41714285714285715, "profit_factor": 0.9039789542913516, "win_rate": 0.5714285714285714, "stop_out_rate": 0.2857142857142857, "average_winner_r": 0.6782813758656498, "average_loser_r": -0.7067998597967052, "gross_profit_r": 2.7131255034625994, "gross_loss_r": 2.120399579390116},
            "AMZN": {"closed_trades": 10, "winners": 6, "losers": 4, "average_winner": 2.415, "average_loser": -0.7975, "cumulative_r": 4.186357010478194, "expectancy_per_trade": 1.13, "profit_factor": 4.5423197492163006, "win_rate": 0.6, "stop_out_rate": 0.0, "average_winner_r": 0.8456166552270922, "average_loser_r": -0.22183573022108963, "gross_profit_r": 5.073699931362554, "gross_loss_r": 0.8873429208843585},
            "AVGO": {"closed_trades": 5, "winners": 3, "losers": 2, "average_winner": 6.01, "average_loser": -5.045, "cumulative_r": 1.075886212112908, "expectancy_per_trade": 1.588, "profit_factor": 1.7869177403369674, "win_rate": 0.6, "stop_out_rate": 0.2, "average_winner_r": 0.8481844890626433, "average_loser_r": -0.7343336275375111, "gross_profit_r": 2.5445534671879297, "gross_loss_r": 1.4686672550750222},
            "GOOGL": {"closed_trades": 1, "winners": 1, "losers": 0, "average_winner": 2.99, "average_loser": None, "cumulative_r": 0.38831168831168833, "expectancy_per_trade": 2.99, "profit_factor": None, "win_rate": 1.0, "stop_out_rate": 0.0, "average_winner_r": 0.38831168831168833, "average_loser_r": None, "gross_profit_r": 0.38831168831168833, "gross_loss_r": 0.0},
            "JPM": {"closed_trades": 1, "winners": 0, "losers": 1, "average_winner": None, "average_loser": -1.35, "cumulative_r": -1.0, "expectancy_per_trade": -1.35, "profit_factor": 0.0, "win_rate": 0.0, "stop_out_rate": 1.0, "average_winner_r": None, "average_loser_r": -1.0, "gross_profit_r": 0.0, "gross_loss_r": 1.0},
            "META": {"closed_trades": 3, "winners": 2, "losers": 1, "average_winner": 3.9275, "average_loser": -7.47, "cumulative_r": -0.36293315574951235, "expectancy_per_trade": 0.12833333333333333, "profit_factor": 1.0515394912985274, "win_rate": 0.6666666666666666, "stop_out_rate": 0.3333333333333333, "average_winner_r": 0.3185334221252438, "average_loser_r": -1.0, "gross_profit_r": 0.6370668442504877, "gross_loss_r": 1.0},
            "MSFT": {"closed_trades": 6, "winners": 4, "losers": 2, "average_winner": 5.90125, "average_loser": -1.6375, "cumulative_r": 4.172657515198702, "expectancy_per_trade": 3.388333333333333, "profit_factor": 7.207633587786259, "win_rate": 0.6666666666666666, "stop_out_rate": 0.0, "average_winner_r": 1.2467094322984205, "average_loser_r": -0.40709010699748954, "gross_profit_r": 4.986837729193682, "gross_loss_r": 0.8141802139949791},
            "NFLX": {"closed_trades": 6, "winners": 3, "losers": 3, "average_winner": 0.805, "average_loser": -0.89, "cumulative_r": -0.8447660698434691, "expectancy_per_trade": -0.0425, "profit_factor": 0.9044943820224719, "win_rate": 0.5, "stop_out_rate": 0.5, "average_winner_r": 0.718411310052177, "average_loser_r": -1.0, "gross_profit_r": 2.155233930156531, "gross_loss_r": 3.0},
            "NVDA": {"closed_trades": 6, "winners": 3, "losers": 3, "average_winner": 2.2, "average_loser": -1.0466666666666666, "cumulative_r": 0.857471667709322, "expectancy_per_trade": 0.5766666666666667, "profit_factor": 2.1019108280254777, "win_rate": 0.5, "stop_out_rate": 0.3333333333333333, "average_winner_r": 0.6765283191565569, "average_loser_r": -0.3907044299201162, "gross_profit_r": 2.0295849574696705, "gross_loss_r": 1.1721132897603486},
            "TSLA": {"closed_trades": 5, "winners": 4, "losers": 1, "average_winner": 6.35875, "average_loser": -5.955, "cumulative_r": 2.838872517789396, "expectancy_per_trade": 3.896, "profit_factor": 4.27120067170445, "win_rate": 0.8, "stop_out_rate": 0.2, "average_winner_r": 0.9597181294473489, "average_loser_r": -1.0, "gross_profit_r": 3.8388725177893956, "gross_loss_r": 1.0},
            "WMT": {"closed_trades": 2, "winners": 0, "losers": 2, "average_winner": None, "average_loser": -0.8125, "cumulative_r": -1.2958333333333334, "expectancy_per_trade": -0.8125, "profit_factor": 0.0, "win_rate": 0.0, "stop_out_rate": 0.5, "average_winner_r": None, "average_loser_r": -0.6479166666666667, "gross_profit_r": 0.0, "gross_loss_r": 1.2958333333333334},
        },
    },
}
# P1's cohort/cohort_one_trade_per_day equal its own native figures (cohort == P1's own universe, per #320).
PRIOR_PERIODS_REFERENCE["P1_THIRD"]["cohort"] = PRIOR_PERIODS_REFERENCE["P1_THIRD"]["native"]
PRIOR_PERIODS_REFERENCE["P1_THIRD"]["cohort_one_trade_per_day"] = PRIOR_PERIODS_REFERENCE["P1_THIRD"]["native_one_trade_per_day"]

#: Quarterly buckets from the merged #320 artifact (C_BASE, NATIVE, P1+P2+P3
#: combined chronology). Restated verbatim; `merge_quarterly_reference`
#: below adds P0's own quarters (and re-merges the one shared boundary
#: quarter, 2024-Q2, which both P0's tail end and P1's start contribute
#: to) without recomputing any of these. `cumulative_r` (unaffected by
#: Issue #322's price/R conflation bug -- it was always R-based) is present
#: for every quarter, so `expectancy_r_per_trade` is exact for all of them
#: via `cumulative_r / closed_trades`. `gross_profit_r`/`gross_loss_r` (and
#: therefore `profit_factor_r`) are populated only for the six quarters
#: reconstructible from the retained per-trade #316 trades CSV
#: (2025-Q2..2026-Q3, P2/P3's own quarters); the four quarters wholly
#: inside P1 (2024-Q2..2025-Q1, no surviving per-trade rows -- see this
#: module's own docstring) report `profit_factor_r=None` rather than an
#: approximated figure. P1's own period-level `profit_factor_r` (in
#: `PRIOR_PERIODS_REFERENCE["P1_THIRD"]`) IS exact; only the *per-quarter*
#: split of it is unavailable without recomputation.
PRIOR_QUARTERS_REFERENCE: Mapping[str, Mapping[str, Any]] = {
    "2024-Q2": {"closed_trades": 18, "winners": 8, "losers": 10, "win_rate": 0.4444444444444444, "average_winner": 2.76125, "average_loser": -2.248, "expectancy_per_trade": -0.021666666666666667, "profit_factor": 0.9826512455516014, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.4444444444444444, "cumulative_r": 0.647175999899919, "meets_minimum_sample": True},
    "2024-Q3": {"closed_trades": 28, "winners": 8, "losers": 20, "win_rate": 0.2857142857142857, "average_winner": 3.258125, "average_loser": -1.78875, "expectancy_per_trade": -0.3467857142857143, "profit_factor": 0.7285814116002796, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.5, "cumulative_r": -6.988473641702725, "meets_minimum_sample": True},
    "2024-Q4": {"closed_trades": 20, "winners": 8, "losers": 12, "win_rate": 0.4, "average_winner": 4.571875, "average_loser": -4.2820833333333335, "expectancy_per_trade": -0.7405, "profit_factor": 0.7117835944341734, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.5, "cumulative_r": -2.7880292749701514, "meets_minimum_sample": True},
    "2025-Q1": {"closed_trades": 18, "winners": 9, "losers": 9, "win_rate": 0.5, "average_winner": 3.772222222222222, "average_loser": -0.7911111111111111, "expectancy_per_trade": 1.4905555555555556, "profit_factor": 4.768258426966292, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.4444444444444444, "cumulative_r": 7.278922806907346, "meets_minimum_sample": True},
    "2025-Q2": {"closed_trades": 36, "winners": 10, "losers": 26, "win_rate": 0.2777777777777778, "average_winner": 6.0835, "average_loser": -1.7465384615384616, "expectancy_per_trade": 0.4284722222222222, "profit_factor": 1.3396828892314467, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.6111111111111112, "cumulative_r": -1.1435718238800248, "meets_minimum_sample": True, "gross_profit_r": 16.48731771844915, "gross_loss_r": 17.630889542329175},
    "2025-Q3": {"closed_trades": 40, "winners": 16, "losers": 24, "win_rate": 0.4, "average_winner": 4.31, "average_loser": -3.0397916666666664, "expectancy_per_trade": -0.099875, "profit_factor": 0.9452402165718594, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.325, "cumulative_r": 0.3095176626078948, "meets_minimum_sample": True, "gross_profit_r": 15.901885699501193, "gross_loss_r": 15.592368036893298},
    "2025-Q4": {"closed_trades": 36, "winners": 18, "losers": 18, "win_rate": 0.5, "average_winner": 4.745, "average_loser": -3.2041666666666666, "expectancy_per_trade": 0.7704166666666666, "profit_factor": 1.480884265279584, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.4166666666666667, "cumulative_r": 3.382588890344555, "meets_minimum_sample": True, "gross_profit_r": 16.495811215504915, "gross_loss_r": 13.11322232516036},
    "2026-Q1": {"closed_trades": 35, "winners": 16, "losers": 19, "win_rate": 0.45714285714285713, "average_winner": 4.350625, "average_loser": -3.443157894736842, "expectancy_per_trade": 0.11971428571428572, "profit_factor": 1.0640476918373587, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.3142857142857143, "cumulative_r": -0.8410256721176135, "meets_minimum_sample": True, "gross_profit_r": 11.67292998222938, "gross_loss_r": 12.513955654346994},
    "2026-Q2": {"closed_trades": 41, "winners": 24, "losers": 17, "win_rate": 0.5853658536585366, "average_winner": 3.88875, "average_loser": -3.8517647058823528, "expectancy_per_trade": 0.6792682926829269, "profit_factor": 1.425320708613317, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.24390243902439024, "cumulative_r": 9.708048987632491, "meets_minimum_sample": True, "gross_profit_r": 20.97823467588827, "gross_loss_r": 11.27018568825578},
    "2026-Q3": {"closed_trades": 19, "winners": 10, "losers": 9, "win_rate": 0.5263157894736842, "average_winner": 4.332, "average_loser": -1.8277777777777777, "expectancy_per_trade": 1.4142105263157896, "profit_factor": 2.633434650455927, "profit_factor_infinite_no_losses": False, "stop_out_rate": 0.21052631578947367, "cumulative_r": 2.338358302764607, "meets_minimum_sample": True, "gross_profit_r": 7.769116980649288, "gross_loss_r": 5.430758677884681},
}


def merge_quarterly_reference(p0_rows: Sequence[TradeDiagnosticRow]) -> dict[str, Any]:
    """Merges P0's own real per-quarter aggregates (computed from real P0
    rows via the unmodified `regime_summary`) with `PRIOR_QUARTERS_REFERENCE`.
    Any quarter key present in both (only "2024-Q2" is possible here -- P0
    ends 2024-04-02, P1 starts 2024-04-03, both inside calendar Q2 2024) is
    combined via `combine_trade_quality_aggregates`; every other quarter is
    taken from whichever side has it, unchanged."""
    p0_quarters = regime_summary(p0_rows, key_field="quarter")
    merged: dict[str, dict[str, Any]] = {}
    for quarter in set(p0_quarters) | set(PRIOR_QUARTERS_REFERENCE):
        sources = [d.get(quarter) for d in (p0_quarters, PRIOR_QUARTERS_REFERENCE) if quarter in d]
        merged[quarter] = combine_trade_quality_aggregates(sources)
        merged[quarter]["adequate_sample"] = merged[quarter]["closed_trades"] >= MIN_QUARTER_CLOSED_TRADES

    adequate = [q for q, s in merged.items() if s["adequate_sample"]]
    # R-based (Issue #322's own correction) -- `expectancy_r_per_trade` is
    # exact for every quarter (derived from `cumulative_r`, unaffected by
    # the original bug); `profit_factor_r` is `None` for the four quarters
    # wholly inside P1 (see `PRIOR_QUARTERS_REFERENCE`'s own docstring) and
    # is simply excluded from the PF-based fraction below, not treated as 0.
    positive_expectancy_r = [q for q in adequate if (merged[q]["expectancy_r_per_trade"] or 0) > 0]
    pf_r_known = [q for q in adequate if merged[q]["profit_factor_r"] is not None]
    pf_r_above_1 = [q for q in pf_r_known if merged[q]["profit_factor_r"] > 1]
    best_quarter = max(adequate, key=lambda q: merged[q]["expectancy_r_per_trade"] or float("-inf"), default=None)
    worst_quarter = min(adequate, key=lambda q: merged[q]["expectancy_r_per_trade"] or float("inf"), default=None)

    longest_negative_streak = 0
    current_streak = 0
    for quarter in sorted(adequate):
        if (merged[quarter]["expectancy_r_per_trade"] or 0) <= 0:
            current_streak += 1
            longest_negative_streak = max(longest_negative_streak, current_streak)
        else:
            current_streak = 0

    return {
        "quarters": merged,
        "adequate_quarter_count": len(adequate), "inadequate_quarter_count": len(merged) - len(adequate),
        "fraction_adequate_quarters_positive_expectancy_r": (len(positive_expectancy_r) / len(adequate)) if adequate else None,
        "fraction_adequate_quarters_pf_r_above_1_of_known": (len(pf_r_above_1) / len(pf_r_known)) if pf_r_known else None,
        "quarters_with_pf_r_known": len(pf_r_known), "quarters_with_pf_r_unavailable": len(adequate) - len(pf_r_known),
        "best_quarter": best_quarter, "worst_quarter": worst_quarter,
        "longest_negative_expectancy_r_streak_quarters": longest_negative_streak,
        "minimum_closed_trades_for_adequate_sample": MIN_QUARTER_CLOSED_TRADES,
    }


# ---------------------------------------------------------------------------
# Predeclared final decision. Fixed here before P0 is computed.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BaselineConclusionCriteria:
    minimum_periods_non_negative_of_four: int = 3
    meaningful_mfe_threshold_fraction: float = 0.30


DEFAULT_BASELINE_CONCLUSION_CRITERIA = BaselineConclusionCriteria()

#: Predeclared BEFORE computing P0 (goal document criterion 6, "P0 does
#: not reveal catastrophic instability"). P0 is judged catastrophically
#: unstable only if its own native-view expectancy is far worse than
#: anything observed in P1-P3 (worst prior native expectancy was P1's
#: -- actually P1 was mildly positive at 0.023; the true floor concern is
#: a genuinely broken period) or its profit factor collapses well below
#: breakeven -- both thresholds fixed here, not tuned to whatever P0
#: turns out to show.
CATASTROPHIC_EXPECTANCY_THRESHOLD = -1.0
CATASTROPHIC_PROFIT_FACTOR_THRESHOLD = 0.5


def is_p0_catastrophically_unstable(p0_native_all_candidates: Mapping[str, Any]) -> bool:
    """R-based (Issue #322's own correction): reads `expectancy_r_per_trade`/
    `profit_factor_r`, not the price-per-share fields. The threshold
    values themselves are unchanged (-1.0R average loss per trade, or a
    profit factor below 0.5, are each independently a reasonable
    catastrophic bar in R terms too)."""
    expectancy_r = p0_native_all_candidates.get("expectancy_r_per_trade")
    pf_r = p0_native_all_candidates.get("profit_factor_r")
    if pf_r is None and p0_native_all_candidates.get("profit_factor_r_infinite_no_losses"):
        pf_r = float("inf")
    return (expectancy_r is not None and expectancy_r < CATASTROPHIC_EXPECTANCY_THRESHOLD) or (
        pf_r is not None and pf_r < CATASTROPHIC_PROFIT_FACTOR_THRESHOLD
    )


#: Predeclared BEFORE computing P0 (goal document: "determine whether the
#: four-period combined evidence is still strong enough to justify ONE
#: controlled exit experiment" under REGIME_DEPENDENCE_CONFIRMED). Fixed
#: bar, not adjusted after seeing the combined result.
FOUR_PERIOD_STRONG_EVIDENCE_MIN_EXPECTANCY = 0.1
FOUR_PERIOD_STRONG_EVIDENCE_MIN_PROFIT_FACTOR = 1.2


def four_period_evidence_is_strong_enough(combined_all_candidates: Mapping[str, Any]) -> bool:
    """R-based (Issue #322's own correction): reads `expectancy_r_per_trade`/
    `profit_factor_r`. 0.1R average expectancy per trade and PF_R>1.2 are
    each meaningful, risk-normalized bars -- not adjusted from the
    original threshold values, which were never price-scale-specific to
    begin with."""
    expectancy_r = combined_all_candidates.get("expectancy_r_per_trade")
    pf_r = combined_all_candidates.get("profit_factor_r")
    if pf_r is None and combined_all_candidates.get("profit_factor_r_infinite_no_losses"):
        pf_r = float("inf")
    return (
        expectancy_r is not None and expectancy_r > FOUR_PERIOD_STRONG_EVIDENCE_MIN_EXPECTANCY
        and pf_r is not None and pf_r > FOUR_PERIOD_STRONG_EVIDENCE_MIN_PROFIT_FACTOR
    )


def classify_baseline_conclusion(
    *,
    combined_all_candidates: Mapping[str, Any],
    combined_one_trade_per_day: Mapping[str, Any],
    combined_excluding_best_symbol: Mapping[str, Any],
    period_native_expectancy_r: Mapping[str, float | None],
    p0_catastrophic_instability: bool,
    p0_meaningful_mfe_fraction: bool,
    criteria: BaselineConclusionCriteria = DEFAULT_BASELINE_CONCLUSION_CRITERIA,
) -> dict[str, Any]:
    """BASELINE_SUPPORTED_FOR_EXIT_RESEARCH / REGIME_DEPENDENCE_CONFIRMED /
    BASELINE_NOT_SUPPORTED / INSUFFICIENT_EVIDENCE, per the seven fixed
    criteria this study's own goal document lists for
    BASELINE_SUPPORTED_FOR_EXIT_RESEARCH. Criteria are not weakened after
    seeing P0.

    **R-normalized metrics are PRIMARY here (Issue #322's own correction).**
    All four criteria that read `expectancy`/`profit_factor` use
    `expectancy_r_per_trade`/`profit_factor_r` -- risk-normalized, the
    correct basis for a fixed-risk account-sizing decision -- never the
    price-per-share fields (`expectancy_per_trade`/`profit_factor`), which
    are not comparable across trades with different stop widths and were
    previously (incorrectly) treated as if they were R multiples.
    `period_native_expectancy_r` must already be R-based (its own name
    says so); passing price-based expectancy here silently reintroduces
    the exact mistake this correction fixes."""
    if any(tq.get("closed_trades", 0) < MIN_SAMPLE_CLOSED_TRADES for tq in (combined_all_candidates,)):
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": "combined sample below minimum closed-trade floor"}

    def _pf_r(tq: Mapping[str, Any]) -> float | None:
        raw = tq.get("profit_factor_r")
        if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
            return float("inf")
        return raw

    combined_exp_r = combined_all_candidates.get("expectancy_r_per_trade")
    combined_pf_r = _pf_r(combined_all_candidates)
    one_per_day_exp_r = combined_one_trade_per_day.get("expectancy_r_per_trade")
    one_per_day_pf_r = _pf_r(combined_one_trade_per_day)
    excl_best_exp_r = combined_excluding_best_symbol.get("expectancy_r_per_trade")
    excl_best_pf_r = _pf_r(combined_excluding_best_symbol)

    non_negative_periods = sum(1 for e in period_native_expectancy_r.values() if (e or 0) >= 0)

    checks = {
        "1_combined_expectancy_r_positive": (combined_exp_r or 0) > 0,
        "2_combined_pf_r_above_1": (combined_pf_r or 0) > 1,
        "3_one_trade_per_day_expectancy_r_positive_and_pf_r_above_1": (
            (one_per_day_exp_r or 0) > 0 and (one_per_day_pf_r or 0) > 1
        ),
        "4_excluding_best_symbol_still_expectancy_r_positive_and_pf_r_above_1": (
            (excl_best_exp_r or 0) > 0 and (excl_best_pf_r or 0) > 1
        ),
        "5_at_least_3_of_4_periods_r_non_negative": non_negative_periods >= criteria.minimum_periods_non_negative_of_four,
        "6_no_catastrophic_p0_instability": not p0_catastrophic_instability,
        "7_mfe_behavior_meaningfully_present": p0_meaningful_mfe_fraction,
    }

    if all(checks.values()):
        verdict = "BASELINE_SUPPORTED_FOR_EXIT_RESEARCH"
    elif (combined_exp_r or 0) > 0 and (combined_pf_r or 0) > 1:
        verdict = "REGIME_DEPENDENCE_CONFIRMED"
    else:
        verdict = "BASELINE_NOT_SUPPORTED"

    return {
        "verdict": verdict, "checks": checks,
        "combined_expectancy_r": combined_exp_r, "combined_profit_factor_r": combined_pf_r,
        "non_negative_periods_of_4": non_negative_periods,
    }


def recommend_next_milestone(*, baseline_verdict: str, four_period_evidence_strong_enough: bool) -> dict[str, Any]:
    """Exactly one of PROFIT_PROTECTION_EXIT_EXPERIMENT / MORE_BASELINE_VALIDATION /
    PAUSE_STRATEGY_REFINEMENT, per the fixed rules -- REGIME_DEPENDENCE_CONFIRMED
    does NOT automatically request a fifth period; it asks instead whether
    the four-period combined evidence is strong enough for one controlled
    exit experiment."""
    if baseline_verdict == "BASELINE_SUPPORTED_FOR_EXIT_RESEARCH":
        milestone = "PROFIT_PROTECTION_EXIT_EXPERIMENT"
        rationale = "Baseline conclusion cleared all seven predeclared criteria for exit-management research."
    elif baseline_verdict == "REGIME_DEPENDENCE_CONFIRMED":
        if four_period_evidence_strong_enough:
            milestone = "PROFIT_PROTECTION_EXIT_EXPERIMENT"
            rationale = (
                "Combined edge remains positive and, despite period-to-period instability, the four-period "
                "combined evidence is judged strong enough to justify one controlled exit experiment -- not "
                "another historical-period expansion."
            )
        else:
            milestone = "PAUSE_STRATEGY_REFINEMENT"
            rationale = "Combined edge remains positive but is not judged strong enough yet to justify a controlled exit experiment; a fifth period is not requested either."
    elif baseline_verdict == "BASELINE_NOT_SUPPORTED":
        milestone = "PAUSE_STRATEGY_REFINEMENT"
        rationale = "Combined/sensitivity evidence no longer shows a defensible positive edge."
    else:
        milestone = "MORE_BASELINE_VALIDATION"
        rationale = "P0 sample or data quality was inadequate for a defensible conclusion."

    return {
        "next_milestone": milestone, "rationale": rationale,
        "authorizes_production_change": False, "authorizes_capital_path_experiment": False,
    }
