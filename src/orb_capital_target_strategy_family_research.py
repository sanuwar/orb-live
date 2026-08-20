"""M48: $30K capital-target ETF strategy-family feasibility research
(Issue #348).

Three distinct, evidence-backed, long-only ETF strategy families, tested
against the frozen question: can any of them meet a $1,000/month capital
target on $30,000, or provide a robust, low-correlation portfolio
contribution worth combining with the supported ORB lane later? Research
only -- no activation, no broker writes, no leverage/short/options.

- ARM_A ETF_PULLBACK_RSI2: daily event-driven mean-reversion (Wilder
  RSI(2) < 10 inside a 200-session uptrend, next-open entry, 2xATR(14)
  stop, exit on close > SMA(5) / 5 sessions held / stop).
- ARM_B SECTOR_MOMENTUM_12_1: monthly cross-sectional momentum rotation
  across 11 sector ETFs, top 3 eligible, equal capital.
- ARM_C DIVERSIFIED_TREND_12M: monthly time-series trend across 8 asset-
  class ETFs, hold every eligible one, equal capital, long/cash only.

This module holds every deterministic, independently-testable piece:
indicators, order-fill mechanics, sizing, ranking, cost application,
the two simulation engines, and the portfolio-metrics/classification
layer. `scripts/research_capital_target_strategy_family.py` owns all
I/O (data fetch, caching, provenance, report generation) and orchestrates
this module; nothing here performs any network call or reads a clock.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.market_data_canonical import MarketBar

STARTING_CAPITAL = Decimal("30000")
MAX_RISK_PER_POSITION = Decimal("300")  # 1.00% of starting capital
MAX_AGGREGATE_RISK = Decimal("600")  # 2.00% of starting capital
SECONDARY_RISK_PER_POSITION = Decimal("150")  # 0.50% secondary view
MAX_CONCURRENT_POSITIONS_A = 3
BASE_COST_BPS = Decimal("5")
STRESS_COST_BPS = Decimal("10")

ARM_A = "ETF_PULLBACK_RSI2"
ARM_B = "SECTOR_MOMENTUM_12_1"
ARM_C = "DIVERSIFIED_TREND_12M"
ARMS = (ARM_A, ARM_B, ARM_C)

UNIVERSE_A: tuple[str, ...] = (
    "SPY", "QQQ", "IWM", "DIA", "XLB", "XLC", "XLE", "XLF", "XLI",
    "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
UNIVERSE_B: tuple[str, ...] = (
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
UNIVERSE_C: tuple[str, ...] = ("SPY", "QQQ", "IWM", "TLT", "IEF", "GLD", "DBC", "VNQ")

UNIVERSE_BY_ARM: Mapping[str, tuple[str, ...]] = {ARM_A: UNIVERSE_A, ARM_B: UNIVERSE_B, ARM_C: UNIVERSE_C}

EVALUATION_START = date(2022, 1, 3)
EVALUATION_END = date(2026, 8, 13)

RSI_PERIOD_A = 2
RSI_ENTRY_THRESHOLD_A = Decimal("10")
TREND_SMA_PERIOD = 200
EXIT_SMA_PERIOD_A = 5
ATR_PERIOD_A = 14
ATR_STOP_MULTIPLE_A = Decimal("2")
MAX_HOLDING_SESSIONS_A = 5

MOMENTUM_LOOKBACK_SESSIONS = 252
MOMENTUM_SKIP_SESSIONS = 21
TREND_LOOKBACK_SESSIONS_C = 252
TOP_N_B = 3


class CapitalTargetResearchError(RuntimeError):
    """Any refusal in the M48 capital-target research pipeline."""


# ---------------------------------------------------------------------------
# Indicators -- pure, list-aligned to the input series, warm-up-safe (None
# until enough completed history exists). Every value at index i is
# computable from bars[0..i] only -- never a later bar.
# ---------------------------------------------------------------------------


def simple_moving_average(values: Sequence[Decimal], window: int) -> list[Decimal | None]:
    if window <= 0:
        raise CapitalTargetResearchError("window must be positive")
    out: list[Decimal | None] = []
    for i in range(len(values)):
        if i + 1 < window:
            out.append(None)
            continue
        out.append(sum(values[i + 1 - window : i + 1]) / Decimal(window))
    return out


def _rsi_from_averages(avg_gain: Decimal, avg_loss: Decimal) -> Decimal:
    if avg_loss == 0:
        return Decimal(100)
    rs = avg_gain / avg_loss
    return Decimal(100) - (Decimal(100) / (Decimal(1) + rs))


def wilder_rsi(closes: Sequence[Decimal], period: int = RSI_PERIOD_A) -> list[Decimal | None]:
    """Wilder's original RSI smoothing (RMA of gains/losses): the first
    `period`-session average gain/loss is a plain mean of the first
    `period` day-over-day deltas; every subsequent average is Wilder-
    smoothed (`avg = (prior_avg * (period - 1) + current) / period`).
    `out[i]` is defined starting at `i == period` (the first index with
    `period` completed deltas behind it)."""
    if period <= 0:
        raise CapitalTargetResearchError("period must be positive")
    n = len(closes)
    out: list[Decimal | None] = [None] * n
    if n < period + 1:
        return out
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [d if d > 0 else Decimal(0) for d in deltas]
    losses = [-d if d < 0 else Decimal(0) for d in deltas]
    avg_gain = sum(gains[:period]) / Decimal(period)
    avg_loss = sum(losses[:period]) / Decimal(period)
    out[period] = _rsi_from_averages(avg_gain, avg_loss)
    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / Decimal(period)
        avg_loss = (avg_loss * (period - 1) + losses[i]) / Decimal(period)
        out[i + 1] = _rsi_from_averages(avg_gain, avg_loss)
    return out


def average_true_range(
    highs: Sequence[Decimal], lows: Sequence[Decimal], closes: Sequence[Decimal], period: int = ATR_PERIOD_A,
) -> list[Decimal | None]:
    """Wilder-smoothed ATR (RMA of true range), the same smoothing
    convention as `wilder_rsi`. `out[i]` is defined starting at
    `i == period`."""
    if period <= 0:
        raise CapitalTargetResearchError("period must be positive")
    n = len(closes)
    if not (len(highs) == len(lows) == n):
        raise CapitalTargetResearchError("highs/lows/closes must be the same length")
    out: list[Decimal | None] = [None] * n
    if n < period + 1:
        return out
    trs = [
        max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        for i in range(1, n)
    ]
    avg = sum(trs[:period]) / Decimal(period)
    out[period] = avg
    for i in range(period, len(trs)):
        avg = (avg * (period - 1) + trs[i]) / Decimal(period)
        out[i + 1] = avg
    return out


def trailing_return(closes: Sequence[Decimal], *, as_of_index: int, lookback: int) -> Decimal | None:
    """Total return from `lookback` sessions before `as_of_index` through
    `as_of_index`, inclusive of both endpoints. `None` if there is not
    enough history."""
    base_index = as_of_index - lookback
    if base_index < 0:
        return None
    base = closes[base_index]
    if base == 0:
        return None
    return (closes[as_of_index] - base) / base


def momentum_12_1(closes: Sequence[Decimal], *, as_of_index: int) -> Decimal | None:
    """12-minus-1 momentum: total return from `MOMENTUM_LOOKBACK_SESSIONS`
    sessions ago through `MOMENTUM_SKIP_SESSIONS` sessions ago (skips the
    most recent ~1 month of the lookback window)."""
    base_index = as_of_index - MOMENTUM_LOOKBACK_SESSIONS
    recent_index = as_of_index - MOMENTUM_SKIP_SESSIONS
    if base_index < 0 or recent_index < 0:
        return None
    base = closes[base_index]
    if base == 0:
        return None
    return (closes[recent_index] - base) / base


# ---------------------------------------------------------------------------
# Order-fill mechanics, sizing, ranking, costs.
# ---------------------------------------------------------------------------


def stop_fill_price(*, session_open: Decimal, stop_price: Decimal) -> Decimal:
    """Long-position protective-stop fill: a gap-through (session opens
    at or below the stop) fills at the open; otherwise the stop fills at
    the stop price itself. Whether the stop was touched at all is the
    caller's own responsibility (`session_low <= stop_price`) -- this
    function only resolves the FILL PRICE once triggered, exactly as the
    frozen protocol specifies ("assumed filled at the stop price unless
    the session opens below it, in which case fill at the open")."""
    if session_open <= stop_price:
        return session_open
    return stop_price


def whole_share_quantity(*, entry_price: Decimal, stop_price: Decimal, max_risk_dollars: Decimal, available_cash: Decimal) -> int:
    """Largest whole-share quantity whose planned risk
    (`quantity * (entry_price - stop_price)`) does not exceed
    `max_risk_dollars` and whose notional does not exceed
    `available_cash`. Zero if the stop is not below the entry price or
    either constraint allows zero shares."""
    if entry_price <= 0:
        return 0
    per_share_risk = entry_price - stop_price
    if per_share_risk <= 0:
        return 0
    max_shares_by_risk = int(max_risk_dollars // per_share_risk)
    max_shares_by_cash = int(available_cash // entry_price)
    return max(0, min(max_shares_by_risk, max_shares_by_cash))


def equal_capital_whole_shares(*, capital: Decimal, price: Decimal) -> int:
    """Whole shares affordable with `capital` at `price` -- the sizing
    rule for ARM_B/ARM_C's equal-capital-allocation buys (no risk-based
    cap; those arms are not stop-based)."""
    if price <= 0:
        return 0
    return max(0, int(capital // price))


@dataclass(frozen=True)
class PullbackCandidate:
    symbol: str
    rsi2: Decimal


def rank_pullback_candidates(candidates: Sequence[PullbackCandidate]) -> tuple[PullbackCandidate, ...]:
    """Ascending RSI(2), ticker-ascending tie-break -- never ranked by
    gap size, ATR, or any other field."""
    return tuple(sorted(candidates, key=lambda c: (c.rsi2, c.symbol)))


@dataclass(frozen=True)
class MomentumCandidate:
    symbol: str
    momentum: Decimal


def rank_momentum_candidates(candidates: Sequence[MomentumCandidate]) -> tuple[MomentumCandidate, ...]:
    """Descending momentum, ticker-ascending tie-break."""
    return tuple(sorted(candidates, key=lambda c: (-c.momentum, c.symbol)))


def transaction_cost(notional: Decimal, bps: Decimal) -> Decimal:
    """Cost in dollars for one side (buy or sell) of a trade of the given
    notional, at `bps` basis points."""
    if notional < 0:
        raise CapitalTargetResearchError("notional must not be negative")
    return notional * bps / Decimal(10000)


# ---------------------------------------------------------------------------
# Daily-bar series construction and cross-symbol alignment.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailySeries:
    """One symbol's ordered, deduplicated daily OHLCV series."""

    symbol: str
    dates: tuple[date, ...]
    opens: tuple[Decimal, ...]
    highs: tuple[Decimal, ...]
    lows: tuple[Decimal, ...]
    closes: tuple[Decimal, ...]
    volumes: tuple[int, ...]

    def __post_init__(self) -> None:
        lengths = {len(self.dates), len(self.opens), len(self.highs), len(self.lows), len(self.closes), len(self.volumes)}
        if len(lengths) != 1:
            raise CapitalTargetResearchError(f"{self.symbol}: all series must share one length")
        if list(self.dates) != sorted(self.dates):
            raise CapitalTargetResearchError(f"{self.symbol}: dates must be strictly ascending")
        if len(set(self.dates)) != len(self.dates):
            raise CapitalTargetResearchError(f"{self.symbol}: duplicate trading_date")

    def index_of(self, day: date) -> int | None:
        try:
            return self.dates.index(day)
        except ValueError:
            return None


def daily_series_from_bars(bars: Sequence[MarketBar]) -> DailySeries:
    """Builds one `DailySeries` from a raw (possibly unsorted) `MarketBar`
    sequence for a single symbol. Fails closed on a duplicate
    `trading_date` (never silently keeps one and drops the other) and on
    a mixed-symbol input."""
    if not bars:
        raise CapitalTargetResearchError("at least one bar is required")
    symbols = {b.symbol for b in bars}
    if len(symbols) != 1:
        raise CapitalTargetResearchError(f"mixed symbols in one series: {sorted(symbols)}")
    ordered = sorted(bars, key=lambda b: b.trading_date)
    dates = [b.trading_date for b in ordered]
    if len(set(dates)) != len(dates):
        seen: set[date] = set()
        dupes = sorted({d for d in dates if d in seen or seen.add(d)})  # type: ignore[func-returns-value]
        raise CapitalTargetResearchError(f"{ordered[0].symbol}: duplicate trading_date(s): {dupes}")
    return DailySeries(
        symbol=ordered[0].symbol,
        dates=tuple(dates),
        opens=tuple(b.open for b in ordered),
        highs=tuple(b.high for b in ordered),
        lows=tuple(b.low for b in ordered),
        closes=tuple(b.close for b in ordered),
        volumes=tuple(b.volume for b in ordered),
    )


def session_alignment_report(series_by_symbol: Mapping[str, DailySeries], *, master_dates: Sequence[date]) -> dict[str, Any]:
    """Diagnostic (never raises): for each symbol, which `master_dates`
    entries it is missing, and whether it carries any date outside
    `master_dates` (an extra/unexpected session). Feeds the provenance
    manifest; `require_full_alignment` below is the fail-closed gate that
    actually blocks a misaligned run."""
    master_set = set(master_dates)
    report: dict[str, Any] = {}
    for symbol, series in series_by_symbol.items():
        symbol_dates = set(series.dates)
        report[symbol] = {
            "missing_master_dates": sorted(d.isoformat() for d in (master_set - symbol_dates)),
            "extra_dates_outside_master": sorted(d.isoformat() for d in (symbol_dates - master_set)),
            "session_count": len(series.dates),
        }
    return report


def require_full_alignment(series_by_symbol: Mapping[str, DailySeries], *, master_dates: Sequence[date]) -> None:
    """Fail-closed: every symbol must carry a bar for every date in
    `master_dates` (the study's master trading calendar). Raises on the
    first missing session found, naming the symbol and the exact missing
    dates, rather than silently proceeding with a gap or forward/back-
    filling a substitute price."""
    report = session_alignment_report(series_by_symbol, master_dates=master_dates)
    broken = {symbol: info["missing_master_dates"] for symbol, info in report.items() if info["missing_master_dates"]}
    if broken:
        raise CapitalTargetResearchError(f"missing sessions detected (fail-closed): {broken}")


def ohlc_integrity_report(series_by_symbol: Mapping[str, DailySeries]) -> dict[str, Any]:
    """Structural OHLC sanity checks (never raises): high >= max(open,
    close, low), low <= min(open, close, high), all prices positive,
    volume non-negative. Used as part of the adjustment/coverage
    verification recorded in the provenance manifest -- this cannot
    independently re-derive a correct split/dividend adjustment from a
    single vendor's own data, but it does catch a structurally broken or
    corrupted response."""
    report: dict[str, Any] = {}
    for symbol, series in series_by_symbol.items():
        violations: list[str] = []
        for i, day in enumerate(series.dates):
            o, h, l, c = series.opens[i], series.highs[i], series.lows[i], series.closes[i]
            v = series.volumes[i]
            if h < max(o, c, l):
                violations.append(f"{day.isoformat()}: high < max(open,close,low)")
            if l > min(o, c, h):
                violations.append(f"{day.isoformat()}: low > min(open,close,high)")
            if min(o, h, l, c) <= 0:
                violations.append(f"{day.isoformat()}: non-positive price")
            if v < 0:
                violations.append(f"{day.isoformat()}: negative volume")
        report[symbol] = {"session_count": len(series.dates), "violations": violations}
    return report


# ---------------------------------------------------------------------------
# ARM_A (ETF_PULLBACK_RSI2): daily event-driven simulation.
#
# Per session i (chronological order, one full pass = one trading day):
#   1. EXIT at today's open -- for every position opened on an earlier
#      day: a scheduled exit (flagged at yesterday's close processing,
#      step 4) fires first; otherwise the protective stop is checked
#      against today's (open, low) and fires if touched, using
#      `stop_fill_price` for the fill; otherwise the position stays open.
#      A position can never exit on its own entry day.
#   2. ENTRY at today's open -- yesterday's ranked candidate queue is
#      filled in rank order, subject to the position-count/duplicate-
#      symbol/per-position-risk/aggregate-risk/cash caps; the stop is
#      `entry_open - 2*ATR(14)` computed from bars through YESTERDAY's
#      close only (never today's not-yet-known high/low).
#   3. SIGNAL SCAN at today's close -- ETFs not currently held with
#      close > SMA(200) and RSI(2) < 10 are ranked (ascending RSI(2),
#      ticker ascending) and queued for tomorrow's entry step.
#   4. SCHEDULED-EXIT FLAGGING at today's close -- for every position
#      still open (including one entered earlier today), evaluate
#      close > SMA(5) or five completed holding sessions
#      (`i - entry_index + 1 >= 5`); if either holds, flag it for a
#      scheduled exit at tomorrow's open (step 1 next iteration).
#   5. Mark today's closing equity (cash + open positions marked at
#      today's close) for the daily equity curve.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OpenPosition:
    symbol: str
    entry_date: date
    entry_index: int
    entry_price: Decimal
    quantity: int
    stop_price: Decimal
    planned_risk: Decimal
    entry_cost: Decimal


@dataclass(frozen=True)
class ClosedTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    exit_reason: str  # "SMA5" | "MAX_HOLD" | "STOP"
    holding_sessions: int
    planned_risk: Decimal
    gross_pnl: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class ArmASimulationResult:
    arm: str
    closed_trades: tuple[ClosedTrade, ...]
    daily_equity: tuple[tuple[date, Decimal], ...]
    starting_capital: Decimal
    ending_cash: Decimal
    open_positions_at_end: tuple[OpenPosition, ...]
    total_cost_paid: Decimal


def run_arm_a(
    series_by_symbol: Mapping[str, DailySeries], master_dates: Sequence[date], *,
    cost_bps: Decimal = BASE_COST_BPS, starting_capital: Decimal = STARTING_CAPITAL,
    max_risk_per_position: Decimal = MAX_RISK_PER_POSITION, max_aggregate_risk: Decimal = MAX_AGGREGATE_RISK,
    max_positions: int = MAX_CONCURRENT_POSITIONS_A,
) -> ArmASimulationResult:
    require_full_alignment(series_by_symbol, master_dates=master_dates)
    symbols = sorted(series_by_symbol)
    n = len(master_dates)

    indicators: dict[str, dict[str, list[Decimal | None]]] = {}
    for symbol in symbols:
        s = series_by_symbol[symbol]
        indicators[symbol] = {
            "sma200": simple_moving_average(s.closes, TREND_SMA_PERIOD),
            "rsi2": wilder_rsi(s.closes, RSI_PERIOD_A),
            "sma5": simple_moving_average(s.closes, EXIT_SMA_PERIOD_A),
            "atr14": average_true_range(s.highs, s.lows, s.closes, ATR_PERIOD_A),
        }

    cash = starting_capital
    total_cost_paid = Decimal(0)
    open_positions: dict[str, OpenPosition] = {}
    scheduled_exit_reason: dict[str, str] = {}
    closed_trades: list[ClosedTrade] = []
    daily_equity: list[tuple[date, Decimal]] = []
    pending_entries: list[str] = []

    for i in range(n):
        day = master_dates[i]

        # --- 1. Exit processing at today's open ---
        for symbol in list(open_positions):
            pos = open_positions[symbol]
            s = series_by_symbol[symbol]
            open_i, low_i = s.opens[i], s.lows[i]
            reason = scheduled_exit_reason.get(symbol)
            if reason is not None:
                exit_price = open_i
            elif low_i <= pos.stop_price:
                exit_price = stop_fill_price(session_open=open_i, stop_price=pos.stop_price)
                reason = "STOP"
            else:
                continue
            exit_notional = exit_price * pos.quantity
            exit_cost = transaction_cost(exit_notional, cost_bps)
            gross_pnl = (exit_price - pos.entry_price) * pos.quantity
            net_pnl = gross_pnl - pos.entry_cost - exit_cost
            cash += exit_notional - exit_cost
            total_cost_paid += exit_cost
            closed_trades.append(ClosedTrade(
                symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                exit_reason=reason, holding_sessions=i - pos.entry_index + 1, planned_risk=pos.planned_risk,
                gross_pnl=gross_pnl, entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl,
            ))
            del open_positions[symbol]
            scheduled_exit_reason.pop(symbol, None)

        # --- 2. Entry processing at today's open ---
        for symbol in pending_entries:
            if len(open_positions) >= max_positions:
                break
            if symbol in open_positions:
                continue
            s = series_by_symbol[symbol]
            atr = indicators[symbol]["atr14"][i - 1] if i > 0 else None
            if atr is None:
                continue
            entry_open = s.opens[i]
            stop_price = entry_open - ATR_STOP_MULTIPLE_A * atr
            remaining_aggregate = max_aggregate_risk - sum((p.planned_risk for p in open_positions.values()), Decimal(0))
            if remaining_aggregate <= 0:
                continue
            risk_cap = min(max_risk_per_position, remaining_aggregate)
            quantity = whole_share_quantity(entry_price=entry_open, stop_price=stop_price, max_risk_dollars=risk_cap, available_cash=cash)
            if quantity <= 0:
                continue
            notional = entry_open * quantity
            entry_cost = transaction_cost(notional, cost_bps)
            if notional + entry_cost > cash:
                continue
            cash -= notional + entry_cost
            total_cost_paid += entry_cost
            planned_risk = (entry_open - stop_price) * quantity
            open_positions[symbol] = OpenPosition(
                symbol=symbol, entry_date=day, entry_index=i, entry_price=entry_open, quantity=quantity,
                stop_price=stop_price, planned_risk=planned_risk, entry_cost=entry_cost,
            )

        # --- 3. Signal scan at today's close ---
        candidates: list[PullbackCandidate] = []
        for symbol in symbols:
            if symbol in open_positions:
                continue
            sma200_i = indicators[symbol]["sma200"][i]
            rsi2_i = indicators[symbol]["rsi2"][i]
            if sma200_i is None or rsi2_i is None:
                continue
            close_i = series_by_symbol[symbol].closes[i]
            if close_i > sma200_i and rsi2_i < RSI_ENTRY_THRESHOLD_A:
                candidates.append(PullbackCandidate(symbol=symbol, rsi2=rsi2_i))
        pending_entries = [c.symbol for c in rank_pullback_candidates(candidates)]

        # --- 4. Scheduled-exit flagging at today's close ---
        scheduled_exit_reason = {}
        for symbol, pos in open_positions.items():
            s = series_by_symbol[symbol]
            close_i = s.closes[i]
            sma5_i = indicators[symbol]["sma5"][i]
            sessions_held = i - pos.entry_index + 1
            if sma5_i is not None and close_i > sma5_i:
                scheduled_exit_reason[symbol] = "SMA5"
            elif sessions_held >= MAX_HOLDING_SESSIONS_A:
                scheduled_exit_reason[symbol] = "MAX_HOLD"

        # --- 5. Mark today's closing equity ---
        equity = cash + sum((open_positions[s].quantity * series_by_symbol[s].closes[i] for s in open_positions), Decimal(0))
        daily_equity.append((day, equity))

    return ArmASimulationResult(
        arm=ARM_A, closed_trades=tuple(closed_trades), daily_equity=tuple(daily_equity),
        starting_capital=starting_capital, ending_cash=cash,
        open_positions_at_end=tuple(open_positions.values()), total_cost_paid=total_cost_paid,
    )


# ---------------------------------------------------------------------------
# ARM_B / ARM_C: shared monthly rebalance engine.
#
# At each calendar month-end session i (the last session of its calendar
# month), an arm-specific `selector(i) -> tuple[str, ...]` computes the
# eligible/ranked target basket using only bars through session i's close
# (never a later bar). The basket is executed at session i+1's open
# (skipped entirely if i is the study's last session -- there is no next
# open to execute at). Execution is INCREMENTAL, not a full liquidate-
# and-rebuild: any currently-held symbol not in the new target is sold;
# any target symbol not currently held is bought with an equal-capital
# share of current equity (`current_equity / len(target)`); a symbol that
# remains in the target across rebalances is left untouched (no forced
# re-equalization trade) -- this is the interpretation of "equal-capital
# allocation" + "costs apply to every REQUIRED buy/sell" that minimizes
# turnover to only what basket-membership changes actually require,
# rather than a full rebuild every month. Documented explicitly here and
# in the milestone report as a deliberate, single, frozen convention.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RebalanceHolding:
    symbol: str
    entry_date: date
    entry_index: int
    entry_price: Decimal
    quantity: int
    entry_cost: Decimal


@dataclass(frozen=True)
class RebalanceTrade:
    symbol: str
    entry_date: date
    exit_date: date
    entry_price: Decimal
    exit_price: Decimal
    quantity: int
    holding_sessions: int
    gross_pnl: Decimal
    entry_cost: Decimal
    exit_cost: Decimal
    net_pnl: Decimal


@dataclass(frozen=True)
class RebalanceEvent:
    signal_date: date
    signal_index: int
    execution_date: date | None
    execution_index: int | None
    target_symbols: tuple[str, ...]


@dataclass(frozen=True)
class MonthlyRebalanceSimulationResult:
    arm: str
    closed_trades: tuple[RebalanceTrade, ...]
    daily_equity: tuple[tuple[date, Decimal], ...]
    starting_capital: Decimal
    ending_cash: Decimal
    open_positions_at_end: tuple[RebalanceHolding, ...]
    total_cost_paid: Decimal
    rebalance_events: tuple[RebalanceEvent, ...]


def month_end_indices(master_dates: Sequence[date]) -> list[int]:
    """Index of the last session of every calendar month in
    `master_dates`, in ascending order."""
    n = len(master_dates)
    return [i for i in range(n) if i == n - 1 or (master_dates[i + 1].year, master_dates[i + 1].month) != (master_dates[i].year, master_dates[i].month)]


def run_monthly_rebalance_arm(
    series_by_symbol: Mapping[str, DailySeries], master_dates: Sequence[date], *,
    arm: str, selector: "Any",  # Callable[[int], tuple[str, ...]]
    cost_bps: Decimal = BASE_COST_BPS, starting_capital: Decimal = STARTING_CAPITAL,
) -> MonthlyRebalanceSimulationResult:
    require_full_alignment(series_by_symbol, master_dates=master_dates)
    n = len(master_dates)
    month_ends = set(month_end_indices(master_dates))

    cash = starting_capital
    total_cost_paid = Decimal(0)
    open_positions: dict[str, RebalanceHolding] = {}
    closed_trades: list[RebalanceTrade] = []
    daily_equity: list[tuple[date, Decimal]] = []
    rebalance_events: list[RebalanceEvent] = []

    pending_target: tuple[str, ...] | None = None
    pending_execution_index: int | None = None

    for i in range(n):
        day = master_dates[i]

        if pending_execution_index == i and pending_target is not None:
            target = set(pending_target)
            for symbol in sorted(open_positions):
                if symbol in target:
                    continue
                pos = open_positions[symbol]
                exit_price = series_by_symbol[symbol].opens[i]
                exit_notional = exit_price * pos.quantity
                exit_cost = transaction_cost(exit_notional, cost_bps)
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                net_pnl = gross_pnl - pos.entry_cost - exit_cost
                cash += exit_notional - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(RebalanceTrade(
                    symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl,
                ))
                del open_positions[symbol]

            new_buys = sorted(s for s in target if s not in open_positions)
            if new_buys:
                equity_now = cash + sum(
                    (open_positions[s].quantity * series_by_symbol[s].opens[i] for s in open_positions), Decimal(0),
                )
                target_capital_per_symbol = equity_now / Decimal(len(target)) if target else Decimal(0)
                for symbol in new_buys:
                    entry_price = series_by_symbol[symbol].opens[i]
                    quantity = equal_capital_whole_shares(capital=min(target_capital_per_symbol, cash), price=entry_price)
                    if quantity <= 0:
                        continue
                    notional = entry_price * quantity
                    entry_cost = transaction_cost(notional, cost_bps)
                    if notional + entry_cost > cash:
                        continue
                    cash -= notional + entry_cost
                    total_cost_paid += entry_cost
                    open_positions[symbol] = RebalanceHolding(
                        symbol=symbol, entry_date=day, entry_index=i, entry_price=entry_price,
                        quantity=quantity, entry_cost=entry_cost,
                    )
            pending_target = None
            pending_execution_index = None

        if i in month_ends:
            selected = tuple(selector(i))
            if i + 1 < n:
                rebalance_events.append(RebalanceEvent(
                    signal_date=day, signal_index=i, execution_date=master_dates[i + 1],
                    execution_index=i + 1, target_symbols=selected,
                ))
                pending_target = selected
                pending_execution_index = i + 1
            else:
                rebalance_events.append(RebalanceEvent(
                    signal_date=day, signal_index=i, execution_date=None, execution_index=None,
                    target_symbols=selected,
                ))

        equity = cash + sum(
            (open_positions[s].quantity * series_by_symbol[s].closes[i] for s in open_positions), Decimal(0),
        )
        daily_equity.append((day, equity))

    return MonthlyRebalanceSimulationResult(
        arm=arm, closed_trades=tuple(closed_trades), daily_equity=tuple(daily_equity),
        starting_capital=starting_capital, ending_cash=cash,
        open_positions_at_end=tuple(open_positions.values()), total_cost_paid=total_cost_paid,
        rebalance_events=tuple(rebalance_events),
    )


def arm_b_selector(series_by_symbol: Mapping[str, DailySeries], sma200_by_symbol: Mapping[str, list[Decimal | None]]) -> "Any":
    symbols = sorted(series_by_symbol)

    def selector(i: int) -> tuple[str, ...]:
        candidates: list[MomentumCandidate] = []
        for symbol in symbols:
            s = series_by_symbol[symbol]
            sma200_i = sma200_by_symbol[symbol][i]
            momentum_i = momentum_12_1(s.closes, as_of_index=i)
            if sma200_i is None or momentum_i is None:
                continue
            if momentum_i > 0 and s.closes[i] > sma200_i:
                candidates.append(MomentumCandidate(symbol=symbol, momentum=momentum_i))
        ranked = rank_momentum_candidates(candidates)
        return tuple(c.symbol for c in ranked[:TOP_N_B])

    return selector


def arm_c_selector(series_by_symbol: Mapping[str, DailySeries], sma200_by_symbol: Mapping[str, list[Decimal | None]]) -> "Any":
    symbols = sorted(series_by_symbol)

    def selector(i: int) -> tuple[str, ...]:
        eligible: list[str] = []
        for symbol in symbols:
            s = series_by_symbol[symbol]
            sma200_i = sma200_by_symbol[symbol][i]
            trailing_i = trailing_return(s.closes, as_of_index=i, lookback=TREND_LOOKBACK_SESSIONS_C)
            if sma200_i is None or trailing_i is None:
                continue
            if trailing_i > 0 and s.closes[i] > sma200_i:
                eligible.append(symbol)
        return tuple(sorted(eligible))

    return selector


# ---------------------------------------------------------------------------
# Portfolio metrics.
#
# `daily_equity` (from either simulation engine) is the account's own
# actual, naturally-compounding dollar equity curve -- profits stay in
# the account and can fund larger future positions, exactly like a real
# account. Two distinct views are derived from it, per the frozen
# protocol's "no compounding for the primary dollar-withdrawal view;
# also report compounded equity as a secondary diagnostic":
#   - PRIMARY: `monthly_dollar_pnl` is the raw dollar P&L for each
#     calendar month (`equity[month_end] - equity[prior_month_end]`),
#     and "average monthly dollars on $30,000" is the plain arithmetic
#     mean of those dollar figures -- never compounded forward into a
#     growing base for the purpose of that one summary number.
#   - SECONDARY: CAGR/total return computed directly from the equity
#     curve's own start/end values, which DOES reflect the account's
#     real compounding growth.
# ---------------------------------------------------------------------------


def monthly_equity_marks(daily_equity: Sequence[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """The last daily-equity mark of each calendar month, in order,
    including the very first mark as an implicit month-zero baseline is
    NOT included here -- callers needing a baseline use
    `daily_equity[0]` directly (see `monthly_dollar_pnl`)."""
    marks: list[tuple[date, Decimal]] = []
    for i, (day, equity) in enumerate(daily_equity):
        is_month_end = i == len(daily_equity) - 1 or daily_equity[i + 1][0].month != day.month or daily_equity[i + 1][0].year != day.year
        if is_month_end:
            marks.append((day, equity))
    return marks


def monthly_dollar_pnl(daily_equity: Sequence[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """`[(month_end_date, dollar_pnl_for_that_month), ...]`. The first
    calendar month's P&L is measured against `daily_equity[0][1]`
    (typically the starting capital, since the caller's simulation
    engines append one equity mark per session starting on day 0)."""
    if not daily_equity:
        return []
    marks = monthly_equity_marks(daily_equity)
    out: list[tuple[date, Decimal]] = []
    prior = daily_equity[0][1]
    for month_end_date, equity in marks:
        out.append((month_end_date, equity - prior))
        prior = equity
    return out


def monthly_return_pct(daily_equity: Sequence[tuple[date, Decimal]]) -> list[tuple[date, Decimal]]:
    """`[(month_end_date, pct_return_for_that_month), ...]` computed
    against the account's own compounding base at the start of that
    month (secondary/diagnostic view, not the primary dollar view)."""
    if not daily_equity:
        return []
    marks = monthly_equity_marks(daily_equity)
    out: list[tuple[date, Decimal]] = []
    prior = daily_equity[0][1]
    for month_end_date, equity in marks:
        pct = (equity - prior) / prior if prior != 0 else Decimal(0)
        out.append((month_end_date, pct))
        prior = equity
    return out


def cagr(daily_equity: Sequence[tuple[date, Decimal]]) -> Decimal | None:
    if len(daily_equity) < 2:
        return None
    start_date, start_equity = daily_equity[0]
    end_date, end_equity = daily_equity[-1]
    if start_equity <= 0:
        return None
    years = Decimal((end_date - start_date).days) / Decimal(365)
    if years <= 0:
        return None
    ratio = float(end_equity / start_equity)
    return Decimal(str(ratio ** (1.0 / float(years)))) - Decimal(1)


def max_drawdown(daily_equity: Sequence[tuple[date, Decimal]]) -> dict[str, Any]:
    """Peak-to-trough drawdown in both dollars and percent, plus the
    number of calendar days from the peak to the first subsequent mark
    that recovers to a new equity high (`None` if never recovered by the
    end of the series)."""
    if not daily_equity:
        return {"max_drawdown_dollars": Decimal(0), "max_drawdown_pct": Decimal(0), "recovery_days": None}
    peak = daily_equity[0][1]
    peak_date = daily_equity[0][0]
    worst_dollars = Decimal(0)
    worst_pct = Decimal(0)
    worst_trough_date: date | None = None
    worst_peak_date: date | None = None
    for day, equity in daily_equity:
        if equity > peak:
            peak = equity
            peak_date = day
        drawdown_dollars = peak - equity
        drawdown_pct = (drawdown_dollars / peak) if peak != 0 else Decimal(0)
        if drawdown_dollars > worst_dollars:
            worst_dollars = drawdown_dollars
            worst_pct = drawdown_pct
            worst_trough_date = day
            worst_peak_date = peak_date
    recovery_days = None
    if worst_trough_date is not None and worst_peak_date is not None:
        worst_peak_equity = next(e for d, e in daily_equity if d == worst_peak_date)
        for day, equity in daily_equity:
            if day > worst_trough_date and equity >= worst_peak_equity:
                recovery_days = (day - worst_trough_date).days
                break
    return {"max_drawdown_dollars": worst_dollars, "max_drawdown_pct": worst_pct, "recovery_days": recovery_days}


def _to_float_list(values: Sequence[Decimal]) -> list[float]:
    return [float(v) for v in values]


def sharpe_ratio(monthly_returns: Sequence[Decimal]) -> Decimal | None:
    """Annualized Sharpe (0% risk-free), from monthly returns:
    `mean(monthly) / stdev(monthly) * sqrt(12)`."""
    values = _to_float_list(monthly_returns)
    if len(values) < 2:
        return None
    stdev = statistics.pstdev(values)
    if stdev == 0:
        return None
    return Decimal(str(statistics.fmean(values) / stdev * (12 ** 0.5)))


def sortino_ratio(monthly_returns: Sequence[Decimal]) -> Decimal | None:
    """Annualized Sortino (0% risk-free / 0% MAR): mean monthly return
    divided by the downside deviation (stdev of only the negative
    monthly returns), annualized by sqrt(12). `None` if there are fewer
    than 2 months or no negative months (undefined downside deviation)."""
    values = _to_float_list(monthly_returns)
    if len(values) < 2:
        return None
    downside = [v for v in values if v < 0]
    if not downside:
        return None
    downside_dev = statistics.pstdev(downside) if len(downside) > 1 else abs(downside[0])
    if downside_dev == 0:
        return None
    return Decimal(str(statistics.fmean(values) / downside_dev * (12 ** 0.5)))


def profitable_month_stats(monthly_pnl: Sequence[Decimal]) -> dict[str, Any]:
    if not monthly_pnl:
        return {"profitable_month_pct": None, "longest_losing_streak": 0, "profitable_months": 0, "total_months": 0}
    profitable = sum(1 for v in monthly_pnl if v > 0)
    longest = 0
    current = 0
    for v in monthly_pnl:
        if v <= 0:
            current += 1
            longest = max(longest, current)
        else:
            current = 0
    return {
        "profitable_month_pct": Decimal(profitable) / Decimal(len(monthly_pnl)),
        "longest_losing_streak": longest, "profitable_months": profitable, "total_months": len(monthly_pnl),
    }


def worst_and_percentile_month(monthly_pnl: Sequence[Decimal], *, percentile: Decimal = Decimal("5")) -> dict[str, Any]:
    if not monthly_pnl:
        return {"worst_month": None, "percentile_month": None}
    ordered = sorted(monthly_pnl)
    worst = ordered[0]
    rank = (percentile / Decimal(100)) * Decimal(len(ordered) - 1)
    lower_index = int(rank)
    upper_index = min(lower_index + 1, len(ordered) - 1)
    frac = rank - Decimal(lower_index)
    percentile_value = ordered[lower_index] + (ordered[upper_index] - ordered[lower_index]) * frac
    return {"worst_month": worst, "percentile_month": percentile_value}


def pearson_correlation(series_a: Sequence[Decimal], series_b: Sequence[Decimal]) -> Decimal | None:
    """Pearson correlation over the two series' own values at matching
    positions (the caller is responsible for aligning them to the same
    calendar months before calling). `None` if fewer than 2 points or
    either series has zero variance."""
    if len(series_a) != len(series_b) or len(series_a) < 2:
        return None
    a = _to_float_list(series_a)
    b = _to_float_list(series_b)
    if statistics.pstdev(a) == 0 or statistics.pstdev(b) == 0:
        return None
    return Decimal(str(statistics.correlation(a, b)))


def align_monthly_series(*series: Sequence[tuple[date, Decimal]]) -> list[tuple[date, ...]]:
    """Inner-joins any number of `(month_end_date, value)` series on
    month (year, month) -- returns `[(date, value_a, value_b, ...), ...]`
    for months present in EVERY input series, in chronological order.
    Used to align correlation inputs (e.g. an arm's monthly returns to
    ORB's or SPY's) that may not share the exact same calendar coverage."""
    keyed = [{(d.year, d.month): v for d, v in s} for s in series]
    common_keys = set(keyed[0])
    for k in keyed[1:]:
        common_keys &= set(k)
    out = []
    for key in sorted(common_keys):
        out.append((date(key[0], key[1], 1),) + tuple(k[key] for k in keyed))
    return out


def positive_contribution_concentration(pnl_by_symbol: Mapping[str, Decimal]) -> dict[str, Any]:
    """Best-symbol concentration using each symbol's own NET P&L: keep
    only symbols with a positive net contribution, denominator = sum of
    those positive contributions, numerator = the largest one. Matches
    the M46/M47-established convention (never per-trade positive sums)."""
    positive = {s: v for s, v in pnl_by_symbol.items() if v > 0}
    if not positive:
        return {"best_contributor": None, "best_contribution_share": None, "positive_contributors": {}}
    total_positive = sum(positive.values())
    best_symbol = max(positive, key=lambda s: positive[s])
    share = (positive[best_symbol] / total_positive) if total_positive else None
    return {"best_contributor": best_symbol, "best_contribution_share": share, "positive_contributors": dict(positive)}


def turnover_and_costs(closed_trade_notionals: Sequence[Decimal], total_cost_paid: Decimal, average_equity: Decimal) -> dict[str, Any]:
    total_traded_notional = sum(closed_trade_notionals, Decimal(0))
    turnover_ratio = (total_traded_notional / average_equity) if average_equity else None
    return {"total_traded_notional": total_traded_notional, "turnover_ratio": turnover_ratio, "total_cost_paid": total_cost_paid}


# ---------------------------------------------------------------------------
# Predeclared classification (frozen in Issue #348 before any empirical
# result was read).
# ---------------------------------------------------------------------------

CLASSIFICATION_DIRECT_TARGET_FEASIBLE = "DIRECT_TARGET_FEASIBLE"
CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR = "PROMISING_PORTFOLIO_CONTRIBUTOR"
CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT = "POSITIVE_BUT_INSUFFICIENT"
CLASSIFICATION_NOT_PROMISING = "NOT_PROMISING"

DIRECT_TARGET_MONTHLY_DOLLARS = Decimal("1000")
DIRECT_TARGET_MAX_DRAWDOWN_PCT = Decimal("0.20")
CONTRIBUTOR_MONTHLY_DOLLARS = Decimal("250")
CONTRIBUTOR_MAX_DRAWDOWN_PCT = Decimal("0.15")
CONTRIBUTOR_MIN_PROFITABLE_MONTH_PCT = Decimal("0.55")
CONTRIBUTOR_MAX_ORB_CORRELATION = Decimal("0.60")
MAX_SINGLE_SYMBOL_POSITIVE_SHARE = Decimal("0.50")
REQUIRED_POSITIVE_SEGMENTS = ("2023", "2024", "2025", "2026_YTD")
#: All five reported calendar segments, including 2022 -- the CONTRIBUTOR
#: gate's "at least four of five ... at least three ..." counts are over
#: ALL FIVE of these, unlike the DIRECT gate's four-segment
#: `REQUIRED_POSITIVE_SEGMENTS` (2022 is reported but not one of DIRECT's
#: four required-positive segments).
ALL_FIVE_CALENDAR_SEGMENTS = ("2022", "2023", "2024", "2025", "2026_YTD")
CONTRIBUTOR_MIN_NONNEGATIVE_SEGMENTS = 4
CONTRIBUTOR_MIN_POSITIVE_SEGMENTS = 3


def classify_capital_target_arm(
    *, average_monthly_dollars_base: Decimal, segment_net_returns: Mapping[str, Decimal],
    max_drawdown_pct: Decimal, pooled_net_base: Decimal, pooled_net_stress: Decimal,
    mean_monthly_return_pct: Decimal, profitable_month_pct: Decimal | None,
    correlation_to_orb: Decimal | None, best_single_symbol_positive_share: Decimal | None,
    integrity_ok: bool,
) -> dict[str, Any]:
    """Applies the frozen four-label M48 gate exactly. `segment_net_returns`
    must carry every key in `ALL_FIVE_CALENDAR_SEGMENTS` (2022 is reported
    and counts toward the CONTRIBUTOR gate's 4-of-5/3-of-5 segment counts,
    but is not one of DIRECT's four required-positive segments per the
    frozen protocol). No arm is promoted merely for being the best of the
    three -- this function only ever looks at one arm's own numbers."""
    if not integrity_ok:
        return {"verdict": CLASSIFICATION_NOT_PROMISING, "reason": "data-integrity failure", "checks": {}}
    if pooled_net_base <= 0 or pooled_net_stress <= 0:
        return {
            "verdict": CLASSIFICATION_NOT_PROMISING,
            "reason": f"pooled_net_base={pooled_net_base} pooled_net_stress={pooled_net_stress} -- one or both non-positive",
            "checks": {},
        }

    concentration_ok = best_single_symbol_positive_share is None or best_single_symbol_positive_share <= MAX_SINGLE_SYMBOL_POSITIVE_SHARE

    direct_checks = {
        "average_monthly_dollars_at_least_1000": average_monthly_dollars_base >= DIRECT_TARGET_MONTHLY_DOLLARS,
        "required_segments_all_positive": all(segment_net_returns.get(seg, Decimal("-1")) > 0 for seg in REQUIRED_POSITIVE_SEGMENTS),
        "max_drawdown_at_most_20pct": max_drawdown_pct <= DIRECT_TARGET_MAX_DRAWDOWN_PCT,
        "stress_cost_pooled_positive": pooled_net_stress > 0,
        "no_symbol_over_50pct_of_positive_pnl": concentration_ok,
    }
    if all(direct_checks.values()):
        return {"verdict": CLASSIFICATION_DIRECT_TARGET_FEASIBLE, "checks": direct_checks}

    nonneg_segments = sum(1 for seg in ALL_FIVE_CALENDAR_SEGMENTS if segment_net_returns.get(seg, Decimal("-1")) >= 0)
    pos_segments = sum(1 for seg in ALL_FIVE_CALENDAR_SEGMENTS if segment_net_returns.get(seg, Decimal("-1")) > 0)
    contributor_checks = {
        "average_monthly_dollars_at_least_250": average_monthly_dollars_base >= CONTRIBUTOR_MONTHLY_DOLLARS,
        "pooled_net_positive": pooled_net_base > 0,
        "mean_monthly_return_positive": mean_monthly_return_pct > 0,
        "at_least_4_of_5_segments_nonnegative": nonneg_segments >= CONTRIBUTOR_MIN_NONNEGATIVE_SEGMENTS,
        "at_least_3_of_5_segments_positive": pos_segments >= CONTRIBUTOR_MIN_POSITIVE_SEGMENTS,
        "max_drawdown_at_most_15pct": max_drawdown_pct <= CONTRIBUTOR_MAX_DRAWDOWN_PCT,
        "stress_cost_pooled_positive": pooled_net_stress > 0,
        "profitable_months_at_least_55pct": profitable_month_pct is not None and profitable_month_pct >= CONTRIBUTOR_MIN_PROFITABLE_MONTH_PCT,
        "orb_correlation_at_most_060": correlation_to_orb is not None and correlation_to_orb <= CONTRIBUTOR_MAX_ORB_CORRELATION,
        "no_symbol_over_50pct_of_positive_pnl": concentration_ok,
    }
    if all(contributor_checks.values()):
        return {"verdict": CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR, "checks": contributor_checks, "direct_checks": direct_checks}

    # Positive pooled net return after BOTH cost cases (already established
    # above by the early NOT_PROMISING return) but one or more contributor
    # gates failed here and the DIRECT gate failed above.
    return {
        "verdict": CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT,
        "checks": contributor_checks, "direct_checks": direct_checks,
    }


# ---------------------------------------------------------------------------
# Calendar segmentation and the top-level per-arm metrics aggregator.
# ---------------------------------------------------------------------------

CALENDAR_SEGMENTS: tuple[str, ...] = ("2022", "2023", "2024", "2025", "2026_YTD")


def segment_label(day: date) -> str | None:
    if day.year in (2022, 2023, 2024, 2025):
        return str(day.year)
    if day.year == 2026:
        return "2026_YTD"
    return None


def slice_to_window(daily_equity: Sequence[tuple[date, Decimal]], *, start: date, end: date) -> list[tuple[date, Decimal]]:
    return [(d, e) for d, e in daily_equity if start <= d <= end]


def scale_trade_to_risk_cap(trade: ClosedTrade, *, risk_cap: Decimal) -> ClosedTrade:
    """Post-hoc `$risk_cap`-view of one ARM_A closed trade, WITHOUT
    rerunning the simulation or re-selecting trades: the same entry/exit
    dates and prices, with quantity scaled down to whatever `risk_cap`
    would have allowed (never scaled UP past the quantity actually
    taken, since the study never re-optimizes size upward after the
    fact). Costs and P&L are recomputed proportionally to the new
    quantity at the SAME per-share cost rate implied by the original
    trade."""
    if trade.quantity <= 0 or trade.planned_risk <= 0:
        return trade
    per_share_risk = trade.planned_risk / Decimal(trade.quantity)
    if per_share_risk <= 0:
        return trade
    capped_quantity = min(trade.quantity, int(risk_cap // per_share_risk))
    if capped_quantity == trade.quantity:
        return trade
    if capped_quantity <= 0:
        gross_pnl = Decimal(0)
        entry_cost = Decimal(0)
        exit_cost = Decimal(0)
    else:
        scale = Decimal(capped_quantity) / Decimal(trade.quantity)
        gross_pnl = trade.gross_pnl * scale
        entry_cost = trade.entry_cost * scale
        exit_cost = trade.exit_cost * scale
    net_pnl = gross_pnl - entry_cost - exit_cost
    return ClosedTrade(
        symbol=trade.symbol, entry_date=trade.entry_date, exit_date=trade.exit_date,
        entry_price=trade.entry_price, exit_price=trade.exit_price, quantity=capped_quantity,
        exit_reason=trade.exit_reason, holding_sessions=trade.holding_sessions,
        planned_risk=(per_share_risk * capped_quantity), gross_pnl=gross_pnl,
        entry_cost=entry_cost, exit_cost=exit_cost, net_pnl=net_pnl,
    )


def compute_arm_metrics(
    *, arm: str, closed_trades: Sequence[Any], daily_equity: Sequence[tuple[date, Decimal]],
    starting_capital: Decimal, total_cost_paid: Decimal, evaluation_start: date, evaluation_end: date,
) -> dict[str, Any]:
    """The full required-output set for one arm/cost-case, computed over
    `[evaluation_start, evaluation_end]` only (warm-up-period equity
    marks and any pre-evaluation trades are excluded)."""
    window_equity = slice_to_window(daily_equity, start=evaluation_start, end=evaluation_end)
    window_trades = [t for t in closed_trades if evaluation_start <= t.exit_date <= evaluation_end]

    if not window_equity:
        raise CapitalTargetResearchError("no equity marks in the evaluation window")

    baseline_equity = starting_capital
    prior_before_window = [e for d, e in daily_equity if d < evaluation_start]
    if prior_before_window:
        baseline_equity = prior_before_window[-1]
    anchored_equity = [(evaluation_start, baseline_equity)] + window_equity if window_equity[0][0] != evaluation_start else window_equity

    monthly_pnl_pairs = monthly_dollar_pnl(anchored_equity)
    monthly_pct_pairs = monthly_return_pct(anchored_equity)
    monthly_pnl = [v for _, v in monthly_pnl_pairs]
    monthly_pct = [v for _, v in monthly_pct_pairs]

    gross_pnl = sum((t.gross_pnl for t in window_trades), Decimal(0))
    net_pnl_realized = sum((t.net_pnl for t in window_trades), Decimal(0))
    net_pnl_equity = window_equity[-1][1] - baseline_equity

    dd = max_drawdown(anchored_equity)
    prof = profitable_month_stats(monthly_pnl)
    worst_pct = worst_and_percentile_month(monthly_pct)

    pnl_by_symbol: dict[str, Decimal] = {}
    for t in window_trades:
        pnl_by_symbol[t.symbol] = pnl_by_symbol.get(t.symbol, Decimal(0)) + t.net_pnl
    concentration = positive_contribution_concentration(pnl_by_symbol)

    trade_notionals = [t.entry_price * t.quantity for t in window_trades] + [t.exit_price * t.quantity for t in window_trades]
    average_equity = (sum((e for _, e in window_equity), Decimal(0)) / Decimal(len(window_equity))) if window_equity else starting_capital
    turnover = turnover_and_costs(trade_notionals, total_cost_paid, average_equity)

    segment_net: dict[str, Decimal] = {}
    for seg in CALENDAR_SEGMENTS:
        seg_marks = [(d, e) for d, e in anchored_equity if segment_label(d) == seg]
        if not seg_marks:
            continue
        seg_start_candidates = [e for d, e in anchored_equity if d < seg_marks[0][0]]
        seg_baseline = seg_start_candidates[-1] if seg_start_candidates else baseline_equity
        segment_net[seg] = seg_marks[-1][1] - seg_baseline

    return {
        "arm": arm,
        "trade_or_rebalance_count": len(window_trades),
        "gross_pnl": gross_pnl,
        "net_pnl_realized": net_pnl_realized,
        "net_pnl": net_pnl_equity,
        "mean_monthly_dollar_pnl": (statistics.fmean(_to_float_list(monthly_pnl)) if monthly_pnl else None),
        "median_monthly_dollar_pnl": (statistics.median(_to_float_list(monthly_pnl)) if monthly_pnl else None),
        "mean_monthly_return_pct": (Decimal(str(statistics.fmean(_to_float_list(monthly_pct)))) if monthly_pct else Decimal(0)),
        "median_monthly_return_pct": (statistics.median(_to_float_list(monthly_pct)) if monthly_pct else None),
        "cagr": cagr(anchored_equity),
        "sharpe": sharpe_ratio(monthly_pct),
        "sortino": sortino_ratio(monthly_pct),
        "max_drawdown_dollars": dd["max_drawdown_dollars"],
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "recovery_days": dd["recovery_days"],
        "profitable_month_pct": prof["profitable_month_pct"],
        "longest_losing_streak": prof["longest_losing_streak"],
        "worst_month_dollars": worst_and_percentile_month(monthly_pnl)["worst_month"],
        "fifth_percentile_month_pct": worst_pct["percentile_month"],
        "concentration": concentration,
        "turnover": turnover,
        "monthly_dollar_pnl_series": monthly_pnl_pairs,
        "monthly_return_pct_series": monthly_pct_pairs,
        "segment_net_pnl": segment_net,
        "starting_capital": baseline_equity,
        "ending_equity": window_equity[-1][1],
        "total_cost_paid": total_cost_paid,
    }


__all__ = [
    "CapitalTargetResearchError",
    "STARTING_CAPITAL", "MAX_RISK_PER_POSITION", "MAX_AGGREGATE_RISK", "SECONDARY_RISK_PER_POSITION",
    "MAX_CONCURRENT_POSITIONS_A", "BASE_COST_BPS", "STRESS_COST_BPS",
    "ARM_A", "ARM_B", "ARM_C", "ARMS",
    "UNIVERSE_A", "UNIVERSE_B", "UNIVERSE_C", "UNIVERSE_BY_ARM",
    "EVALUATION_START", "EVALUATION_END",
    "RSI_PERIOD_A", "RSI_ENTRY_THRESHOLD_A", "TREND_SMA_PERIOD", "EXIT_SMA_PERIOD_A",
    "ATR_PERIOD_A", "ATR_STOP_MULTIPLE_A", "MAX_HOLDING_SESSIONS_A",
    "MOMENTUM_LOOKBACK_SESSIONS", "MOMENTUM_SKIP_SESSIONS", "TREND_LOOKBACK_SESSIONS_C", "TOP_N_B",
    "simple_moving_average", "wilder_rsi", "average_true_range", "trailing_return", "momentum_12_1",
    "stop_fill_price", "whole_share_quantity", "equal_capital_whole_shares",
    "PullbackCandidate", "rank_pullback_candidates",
    "MomentumCandidate", "rank_momentum_candidates",
    "transaction_cost",
    "DailySeries", "daily_series_from_bars", "session_alignment_report", "require_full_alignment",
    "ohlc_integrity_report",
    "OpenPosition", "ClosedTrade", "ArmASimulationResult", "run_arm_a",
    "RebalanceHolding", "RebalanceTrade", "RebalanceEvent", "MonthlyRebalanceSimulationResult",
    "month_end_indices", "run_monthly_rebalance_arm", "arm_b_selector", "arm_c_selector",
    "monthly_equity_marks", "monthly_dollar_pnl", "monthly_return_pct", "cagr", "max_drawdown",
    "sharpe_ratio", "sortino_ratio", "profitable_month_stats", "worst_and_percentile_month",
    "pearson_correlation", "align_monthly_series", "positive_contribution_concentration", "turnover_and_costs",
    "CLASSIFICATION_DIRECT_TARGET_FEASIBLE", "CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR",
    "CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT", "CLASSIFICATION_NOT_PROMISING",
    "DIRECT_TARGET_MONTHLY_DOLLARS", "DIRECT_TARGET_MAX_DRAWDOWN_PCT", "CONTRIBUTOR_MONTHLY_DOLLARS",
    "CONTRIBUTOR_MAX_DRAWDOWN_PCT", "CONTRIBUTOR_MIN_PROFITABLE_MONTH_PCT", "CONTRIBUTOR_MAX_ORB_CORRELATION",
    "MAX_SINGLE_SYMBOL_POSITIVE_SHARE", "REQUIRED_POSITIVE_SEGMENTS", "ALL_FIVE_CALENDAR_SEGMENTS",
    "CONTRIBUTOR_MIN_NONNEGATIVE_SEGMENTS", "CONTRIBUTOR_MIN_POSITIVE_SEGMENTS",
    "classify_capital_target_arm",
    "CALENDAR_SEGMENTS", "segment_label", "slice_to_window", "scale_trade_to_risk_cap", "compute_arm_metrics",
]
