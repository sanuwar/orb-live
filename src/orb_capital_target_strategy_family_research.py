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
#   1. SCHEDULED EXIT at today's open -- for every position opened on an
#      earlier day and flagged at yesterday's close processing (step 4):
#      exit at today's open. A position can never scheduled-exit on its
#      own entry day (nothing is flagged for a position that does not
#      exist yet).
#   2. ENTRY at today's open -- yesterday's ranked candidate queue is
#      filled in rank order, subject to the position-count/duplicate-
#      symbol/per-position-risk/aggregate-risk/cash caps; the stop is
#      `entry_open - 2*ATR(14)` computed from bars through YESTERDAY's
#      close only (never today's not-yet-known high/low).
#   3. STOP CHECK at today's (open, low) -- applied to EVERY position
#      still open after steps 1-2, including one entered THIS SAME
#      session in step 2 (a position must be able to stop out on its own
#      entry day if today's low breaches its stop -- an entry-day gap-
#      through is structurally impossible since the stop is computed
#      strictly below the entry price, but an intraday touch after entry
#      is not, and omitting this check understates both realized losses
#      and drawdown). Uses `stop_fill_price` for the fill exactly as any
#      other stop.
#   4. SIGNAL SCAN + SCHEDULED-EXIT FLAGGING at today's close -- ETFs not
#      currently held with close > SMA(200) and RSI(2) < 10 are ranked
#      (ascending RSI(2), ticker ascending) and queued for tomorrow's
#      entry step; every position still open (including one entered or
#      stopped-and-still-open... i.e. any position that survived step 3)
#      is evaluated for close > SMA(5) or five completed holding sessions
#      (`i - entry_index + 1 >= 5`) and flagged for a scheduled exit at
#      tomorrow's open if either holds.
#   5. Mark today's closing equity (cash + open positions marked at
#      today's close) for the daily equity curve.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Transaction:
    """One row of the explicit transaction ledger -- every buy and sell
    either simulation engine ever executes, real or (for
    `EVALUATION_END_MARK` rows, added separately by
    `evaluation_end_mark_transactions`) hypothetical. `reason` values:
    `ENTRY` (ARM_A open), `STOP`/`SMA5`/`MAX_HOLD` (ARM_A close),
    `REBALANCE_BUY` (ARM_B/C new position), `REBALANCE_TOPUP` (ARM_B/C
    adds to a continuing holding), `REBALANCE_SELL_REMOVED` (ARM_B/C
    full exit, left the target basket), `REBALANCE_TRIM` (ARM_B/C
    partial sell, still in the basket), `EVALUATION_END_MARK`
    (hypothetical liquidation at the fixed 2026-08-13 evaluation
    endpoint, for net-P&L/concentration/turnover accounting only --
    never a strategy-generated exit and never fed back into the
    simulation)."""

    date: date
    arm: str
    symbol: str
    side: str  # "BUY" | "SELL"
    quantity: int
    price: Decimal
    notional: Decimal
    cost: Decimal
    reason: str


@dataclass(frozen=True)
class PositionSnapshot:
    """One row of the daily position ledger: the state of ONE open
    position on ONE session, recorded once per open position per
    session (never merely an aggregate count). `planned_risk` is
    populated for ARM_A positions and `None` for ARM_B/ARM_C holdings
    (which are not risk-sized)."""

    date: date
    symbol: str
    quantity: int
    mark_price: Decimal
    market_value: Decimal
    planned_risk: Decimal | None


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
    transactions: tuple[Transaction, ...] = ()
    daily_position_count: tuple[tuple[date, int], ...] = ()
    daily_position_snapshots: tuple[PositionSnapshot, ...] = ()


def run_arm_a(
    series_by_symbol: Mapping[str, DailySeries], master_dates: Sequence[date], *,
    cost_bps: Decimal = BASE_COST_BPS, starting_capital: Decimal = STARTING_CAPITAL,
    max_risk_per_position: Decimal = MAX_RISK_PER_POSITION, max_aggregate_risk: Decimal = MAX_AGGREGATE_RISK,
    max_positions: int = MAX_CONCURRENT_POSITIONS_A, evaluation_start: date | None = None,
) -> ArmASimulationResult:
    """`evaluation_start`, if given, gates all EXECUTION (steps 1-3: any
    scheduled-exit fill, entry fill, or stop fill -- and therefore every
    cash change, cost, transaction-ledger row, position, and realized
    P&L) to sessions on or after it. Signal scanning and scheduled-exit
    flagging (steps 4a/4b) always run unconditionally, on every session
    including warm-up ones -- indicators and pending signals must still
    be computed from pre-evaluation data so a genuine 2021-12-31 signal
    can fill on the first evaluation session's open. `pending_entries`
    is naturally never "carried forward" across a blocked day: it is
    fully recomputed by step 4a every single session (including
    warm-up ones) and only ever consumed by the VERY NEXT session's
    step 2, so a warm-up-day signal that could not execute (because the
    next session was still before `evaluation_start`) is simply
    discarded when that next session's own step 4a overwrites it --
    never accumulated or replayed later."""
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
    daily_position_count: list[tuple[date, int]] = []
    daily_position_snapshots: list[PositionSnapshot] = []
    pending_entries: list[str] = []
    transactions: list[Transaction] = []

    for i in range(n):
        day = master_dates[i]
        execution_allowed = evaluation_start is None or day >= evaluation_start

        # --- 1. Scheduled-exit processing at today's open ---
        if execution_allowed:
            for symbol in list(open_positions):
                pos = open_positions[symbol]
                reason = scheduled_exit_reason.get(symbol)
                if reason is None:
                    continue
                s = series_by_symbol[symbol]
                exit_price = s.opens[i]
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
                transactions.append(Transaction(
                    date=day, arm=ARM_A, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason=reason,
                ))
                del open_positions[symbol]
                scheduled_exit_reason.pop(symbol, None)

        # --- 2. Entry processing at today's open ---
        if execution_allowed:
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
                transactions.append(Transaction(
                    date=day, arm=ARM_A, symbol=symbol, side="BUY", quantity=quantity,
                    price=entry_open, notional=notional, cost=entry_cost, reason="ENTRY",
                ))

        # --- 3. Stop check at today's (open, low) -- every position still
        # open after steps 1-2, INCLUDING one just entered in step 2. ---
        if execution_allowed:
            for symbol in list(open_positions):
                pos = open_positions[symbol]
                s = series_by_symbol[symbol]
                open_i, low_i = s.opens[i], s.lows[i]
                if low_i > pos.stop_price:
                    continue
                exit_price = stop_fill_price(session_open=open_i, stop_price=pos.stop_price)
                exit_notional = exit_price * pos.quantity
                exit_cost = transaction_cost(exit_notional, cost_bps)
                gross_pnl = (exit_price - pos.entry_price) * pos.quantity
                net_pnl = gross_pnl - pos.entry_cost - exit_cost
                cash += exit_notional - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(ClosedTrade(
                    symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=exit_price, quantity=pos.quantity,
                    exit_reason="STOP", holding_sessions=i - pos.entry_index + 1, planned_risk=pos.planned_risk,
                    gross_pnl=gross_pnl, entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl,
                ))
                transactions.append(Transaction(
                    date=day, arm=ARM_A, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason="STOP",
                ))
                del open_positions[symbol]
                scheduled_exit_reason.pop(symbol, None)

        # --- 4a. Signal scan at today's close ---
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

        # --- 4b. Scheduled-exit flagging at today's close ---
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
        daily_position_count.append((day, len(open_positions)))
        for symbol in sorted(open_positions):
            pos = open_positions[symbol]
            mark_price = series_by_symbol[symbol].closes[i]
            daily_position_snapshots.append(PositionSnapshot(
                date=day, symbol=symbol, quantity=pos.quantity, mark_price=mark_price,
                market_value=mark_price * pos.quantity, planned_risk=pos.planned_risk,
            ))

    return ArmASimulationResult(
        arm=ARM_A, closed_trades=tuple(closed_trades), daily_equity=tuple(daily_equity),
        starting_capital=starting_capital, ending_cash=cash,
        open_positions_at_end=tuple(open_positions.values()), total_cost_paid=total_cost_paid,
        transactions=tuple(transactions), daily_position_count=tuple(daily_position_count),
        daily_position_snapshots=tuple(daily_position_snapshots),
    )


# ---------------------------------------------------------------------------
# ARM_B / ARM_C: shared monthly rebalance engine.
#
# At each calendar month-end session i (the last session of its calendar
# month), an arm-specific `selector(i) -> tuple[str, ...]` computes the
# eligible/ranked target basket using only bars through session i's close
# (never a later bar). The basket is executed at session i+1's open
# (skipped entirely if i is the study's last session -- there is no next
# open to execute at), as a genuine equal-capital rebalance:
#   1. SELL every currently-held symbol no longer in the target (full
#      exit, reason `REBALANCE_SELL_REMOVED`).
#   2. Compute current total equity at today's open (cash + remaining
#      continuing holdings marked at today's open) and divide by the
#      target basket size for the equal per-symbol capital target.
#   3. Compute each target symbol's whole-share target quantity at
#      today's open price.
#   4. TRIM every continuing holding whose current quantity exceeds its
#      target quantity (partial sell, reason `REBALANCE_TRIM`) --
#      processed in ticker-ascending order.
#   5. BUY new positions and TOP UP continuing holdings below their
#      target quantity (reason `REBALANCE_BUY` / `REBALANCE_TOPUP`) --
#      processed in ticker-ascending order, capped by available cash
#      (a cash shortfall silently buys as many whole shares as afford,
#      never partial shares, never borrows).
# A top-up combines with the existing lot into ONE weighted-average-cost
# holding (keeping the ORIGINAL entry_date/entry_index for holding-
# session bookkeeping -- a top-up does not reset how long a position has
# been continuously held). Every step 1/4/5 transaction is recorded in
# the explicit `transactions` ledger. Continuing holdings finish within
# one-share rounding of the equal-capital target (whole-share sizing is
# the only source of residual drift); costs are charged on every actual
# trade (steps 1, 4, 5) -- never on a holding that already sits exactly
# at target.
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
    reason: str = "REMOVED"  # "REMOVED" (full exit, left the target basket) | "TRIMMED" (partial sell, still in the basket)


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
    transactions: tuple[Transaction, ...] = ()
    daily_position_count: tuple[tuple[date, int], ...] = ()
    daily_position_snapshots: tuple[PositionSnapshot, ...] = ()


def month_end_indices(master_dates: Sequence[date]) -> list[int]:
    """Index of the last session of every calendar month in
    `master_dates`, in ascending order."""
    n = len(master_dates)
    return [i for i in range(n) if i == n - 1 or (master_dates[i + 1].year, master_dates[i + 1].month) != (master_dates[i].year, master_dates[i].month)]


def run_monthly_rebalance_arm(
    series_by_symbol: Mapping[str, DailySeries], master_dates: Sequence[date], *,
    arm: str, selector: "Any",  # Callable[[int], tuple[str, ...]]
    cost_bps: Decimal = BASE_COST_BPS, starting_capital: Decimal = STARTING_CAPITAL,
    evaluation_start: date | None = None,
) -> MonthlyRebalanceSimulationResult:
    """`evaluation_start`, if given, gates rebalance EXECUTION (every
    sell/trim/buy/top-up, and therefore every cash change, cost,
    transaction-ledger row, and position) to sessions on or after it.
    Month-end SIGNAL computation (`selector`) always runs unconditionally
    on every session including warm-up ones -- a genuine December 2021
    month-end selection still schedules execution at the next session's
    open exactly as usual, and if that next session is already on or
    after `evaluation_start` (the normal case for the December-to-
    January transition), it executes there. A month-end signal whose
    scheduled execution session is STILL before `evaluation_start` (an
    earlier warm-up month) is silently superseded, never queued or
    replayed: the next month-end's own signal unconditionally overwrites
    `pending_target`/`pending_execution_index` regardless of whether the
    previous one ever executed, so no pre-evaluation selection can ever
    leak into the evaluation period."""
    require_full_alignment(series_by_symbol, master_dates=master_dates)
    n = len(master_dates)
    month_ends = set(month_end_indices(master_dates))

    cash = starting_capital
    total_cost_paid = Decimal(0)
    open_positions: dict[str, RebalanceHolding] = {}
    closed_trades: list[RebalanceTrade] = []
    daily_equity: list[tuple[date, Decimal]] = []
    daily_position_count: list[tuple[date, int]] = []
    daily_position_snapshots: list[PositionSnapshot] = []
    rebalance_events: list[RebalanceEvent] = []
    transactions: list[Transaction] = []

    pending_target: tuple[str, ...] | None = None
    pending_execution_index: int | None = None

    for i in range(n):
        day = master_dates[i]
        execution_allowed = evaluation_start is None or day >= evaluation_start

        if pending_execution_index == i and pending_target is not None and execution_allowed:
            target = set(pending_target)

            # 1. SELL every currently-held symbol no longer in the target.
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
                    entry_cost=pos.entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason="REMOVED",
                ))
                transactions.append(Transaction(
                    date=day, arm=arm, symbol=symbol, side="SELL", quantity=pos.quantity,
                    price=exit_price, notional=exit_notional, cost=exit_cost, reason="REBALANCE_SELL_REMOVED",
                ))
                del open_positions[symbol]

            # 2-3. Equal-capital target quantity for every target symbol,
            # from equity marked at today's open (continuing holdings only
            # -- removed ones are already sold above).
            target_capital_per_symbol = Decimal(0)
            target_qty: dict[str, int] = {}
            if target:
                equity_now = cash + sum(
                    (open_positions[s].quantity * series_by_symbol[s].opens[i] for s in open_positions), Decimal(0),
                )
                target_capital_per_symbol = equity_now / Decimal(len(target))
                for symbol in target:
                    price = series_by_symbol[symbol].opens[i]
                    target_qty[symbol] = equal_capital_whole_shares(capital=target_capital_per_symbol, price=price)

            # 4. TRIM continuing holdings above their target quantity
            # (ticker-ascending order; frees cash before any buy/top-up).
            for symbol in sorted(s for s in open_positions if s in target):
                pos = open_positions[symbol]
                desired = target_qty.get(symbol, 0)
                if pos.quantity <= desired:
                    continue
                trim_qty = pos.quantity - desired
                price = series_by_symbol[symbol].opens[i]
                proceeds = price * trim_qty
                exit_cost = transaction_cost(proceeds, cost_bps)
                frac = Decimal(trim_qty) / Decimal(pos.quantity)
                allocated_entry_cost = pos.entry_cost * frac
                gross_pnl = (price - pos.entry_price) * trim_qty
                net_pnl = gross_pnl - allocated_entry_cost - exit_cost
                cash += proceeds - exit_cost
                total_cost_paid += exit_cost
                closed_trades.append(RebalanceTrade(
                    symbol=symbol, entry_date=pos.entry_date, exit_date=day,
                    entry_price=pos.entry_price, exit_price=price, quantity=trim_qty,
                    holding_sessions=i - pos.entry_index + 1, gross_pnl=gross_pnl,
                    entry_cost=allocated_entry_cost, exit_cost=exit_cost, net_pnl=net_pnl, reason="TRIMMED",
                ))
                transactions.append(Transaction(
                    date=day, arm=arm, symbol=symbol, side="SELL", quantity=trim_qty,
                    price=price, notional=proceeds, cost=exit_cost, reason="REBALANCE_TRIM",
                ))
                if desired <= 0:
                    del open_positions[symbol]
                else:
                    open_positions[symbol] = RebalanceHolding(
                        symbol=symbol, entry_date=pos.entry_date, entry_index=pos.entry_index,
                        entry_price=pos.entry_price, quantity=desired, entry_cost=pos.entry_cost - allocated_entry_cost,
                    )

            # 5. BUY new positions and TOP UP continuing holdings below
            # target (ticker-ascending order; cash-shortfall buys as many
            # whole shares as afford, never partial, never borrows).
            for symbol in sorted(target):
                current = open_positions.get(symbol)
                current_qty = current.quantity if current is not None else 0
                needed = target_qty.get(symbol, 0) - current_qty
                if needed <= 0:
                    continue
                price = series_by_symbol[symbol].opens[i]
                if price <= 0:
                    continue
                affordable = int(cash // (price * (Decimal(1) + cost_bps / Decimal(10000))))
                buy_qty = min(needed, affordable)
                while buy_qty > 0 and price * buy_qty + transaction_cost(price * buy_qty, cost_bps) > cash:
                    buy_qty -= 1
                if buy_qty <= 0:
                    continue
                notional = price * buy_qty
                entry_cost = transaction_cost(notional, cost_bps)
                cash -= notional + entry_cost
                total_cost_paid += entry_cost
                reason = "REBALANCE_TOPUP" if current is not None else "REBALANCE_BUY"
                if current is None:
                    open_positions[symbol] = RebalanceHolding(
                        symbol=symbol, entry_date=day, entry_index=i, entry_price=price,
                        quantity=buy_qty, entry_cost=entry_cost,
                    )
                else:
                    combined_qty = current.quantity + buy_qty
                    weighted_entry_price = (current.quantity * current.entry_price + buy_qty * price) / Decimal(combined_qty)
                    open_positions[symbol] = RebalanceHolding(
                        symbol=symbol, entry_date=current.entry_date, entry_index=current.entry_index,
                        entry_price=weighted_entry_price, quantity=combined_qty,
                        entry_cost=current.entry_cost + entry_cost,
                    )
                transactions.append(Transaction(
                    date=day, arm=arm, symbol=symbol, side="BUY", quantity=buy_qty,
                    price=price, notional=notional, cost=entry_cost, reason=reason,
                ))

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
        daily_position_count.append((day, len(open_positions)))
        for symbol in sorted(open_positions):
            pos = open_positions[symbol]
            mark_price = series_by_symbol[symbol].closes[i]
            daily_position_snapshots.append(PositionSnapshot(
                date=day, symbol=symbol, quantity=pos.quantity, mark_price=mark_price,
                market_value=mark_price * pos.quantity, planned_risk=None,
            ))

    return MonthlyRebalanceSimulationResult(
        arm=arm, closed_trades=tuple(closed_trades), daily_equity=tuple(daily_equity),
        starting_capital=starting_capital, ending_cash=cash,
        open_positions_at_end=tuple(open_positions.values()), total_cost_paid=total_cost_paid,
        rebalance_events=tuple(rebalance_events), transactions=tuple(transactions),
        daily_position_count=tuple(daily_position_count),
        daily_position_snapshots=tuple(daily_position_snapshots),
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
    """`[(month_end_date, dollar_pnl_for_that_month), ...]`, computed
    against the account's own actual, naturally-COMPOUNDING equity base
    at the start of each month. **SECONDARY / diagnostic only** -- later
    months operate on a changing (compounded) equity base, so this is
    NOT the frozen protocol's "no compounding" primary dollar-
    withdrawal view; see `fixed_notional_monthly_dollar_pnl` for that.
    The first calendar month's P&L is measured against
    `daily_equity[0][1]` (typically the starting capital, since the
    caller's simulation engines append one equity mark per session
    starting on day 0)."""
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
    month. This is the basis both `monthly_dollar_pnl` (compounded,
    secondary) and `fixed_notional_monthly_dollar_pnl` (fixed-notional,
    PRIMARY) are derived from."""
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


def fixed_notional_monthly_dollar_pnl(
    monthly_pct_series: Sequence[tuple[date, Decimal]], *, notional: Decimal = STARTING_CAPITAL,
) -> list[tuple[date, Decimal]]:
    """The frozen protocol's PRIMARY dollar-withdrawal view: each
    month's own percentage return applied to a FIXED notional (the
    original $30,000 starting capital), never to the account's own
    (compounding) equity at that month's start. Two consecutive +10%
    months therefore both contribute exactly `0.10 * notional` here --
    e.g. $3,000 and $3,000 on a $30,000 notional -- even though the same
    two months would compound the account's own equity to $33,000 then
    $36,300 (a $3,300 second-month dollar change) in the naturally-
    compounding `monthly_dollar_pnl` series. Averaging THIS series is
    what "average monthly dollars on $30,000, without compounding"
    means; `monthly_dollar_pnl`/`cagr` are the secondary, genuinely-
    compounding diagnostics."""
    return [(d, pct * notional) for d, pct in monthly_pct_series]


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


def annualized_volatility_pct(monthly_returns: Sequence[Decimal]) -> Decimal | None:
    """Annualized volatility of monthly returns: `stdev(monthly) *
    sqrt(12)` -- the same population-stdev/sqrt(12) annualization
    convention `sharpe_ratio` uses. `None` if fewer than 2 months."""
    values = _to_float_list(monthly_returns)
    if len(values) < 2:
        return None
    return Decimal(str(statistics.pstdev(values) * (12 ** 0.5)))


def annualized_downside_deviation_pct(monthly_returns: Sequence[Decimal]) -> Decimal | None:
    """Annualized downside deviation: `stdev(negative monthly) *
    sqrt(12)` -- the same convention `sortino_ratio` uses for its
    denominator. `None` if fewer than 2 months or no negative months
    (undefined downside deviation)."""
    values = _to_float_list(monthly_returns)
    if len(values) < 2:
        return None
    downside = [v for v in values if v < 0]
    if not downside:
        return None
    downside_dev = statistics.pstdev(downside) if len(downside) > 1 else abs(downside[0])
    return Decimal(str(downside_dev * (12 ** 0.5)))


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


def fill_missing_months_with_zero(
    monthly_series: Sequence[tuple[date, Decimal]], *, start: date, end: date,
) -> list[tuple[date, Decimal]]:
    """Builds the complete calendar-month grid from `start` through `end`
    (inclusive, by (year, month)) and fills every month absent from
    `monthly_series` with exactly `Decimal(0)`, rather than letting it
    silently disappear from a later `align_monthly_series` inner join.
    Existing months keep their own value unchanged. Correct for a sparse
    series like ORB's own monthly R-sum (most months have zero closed
    trades) being correlated against a densely-populated series (an
    ETF arm's own monthly return) -- dropping no-trade months would
    otherwise only ever compare ORB's OWN trading months against the
    ETF arm, silently discarding the (materially informative) fact that
    ORB was flat everywhere else. Constant risk-normalization (R,
    scaled by a fixed dollar-per-R) does not change the resulting
    Pearson correlation, so leaving the series in R (not converting to
    dollars) is an accepted, documented convention here."""
    by_month = {(d.year, d.month): v for d, v in monthly_series}
    out: list[tuple[date, Decimal]] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append((date(year, month, 1), by_month.get((year, month), Decimal(0))))
        month += 1
        if month == 13:
            month = 1
            year += 1
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
# Finding 15 (independent review): the frozen decision tree's required
# answer when every M48 arm classifies POSITIVE_BUT_INSUFFICIENT -- a
# deliberately generous mathematical combination-plausibility upper
# bound, computed WITHOUT running any M49 portfolio-combination
# simulation.
# ---------------------------------------------------------------------------

#: 0.363 R/month at $300 risk-per-trade (1% of the $30,000 target
#: capital) -- the established ORB monthly-dollar reference figure this
#: milestone was handed for the combination check, independent of and
#: not recomputed from M48's own `load_orb_monthly_r_series()` (a
#: different ORB confirmation-policy pickle used only for the
#: correlation-to-ORB metric elsewhere in this module).
ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK = Decimal("108.90")
COMBINATION_TARGET_MONTHLY_DOLLARS = Decimal("1000")
COMBINATION_MATHEMATICALLY_PLAUSIBLE = "COMBINATION_MATHEMATICALLY_PLAUSIBLE"
COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE = "COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE"


def compute_combination_plausibility_upper_bound(arm_mean_monthly_dollars: Mapping[str, Decimal]) -> dict[str, Any]:
    """Only reached when every M48 arm classifies
    POSITIVE_BUT_INSUFFICIENT on its own. Sums `ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK`
    with the TWO highest of `arm_mean_monthly_dollars` (each arm's own
    fixed-$30,000 primary monthly-dollar figure). This is deliberately
    GENEROUS, not a claim of real combined achievability: it treats ORB
    and each of the two chosen arms as though each could independently
    draw on its own full capital, when in reality a single real $30,000
    account would have to be SPLIT between whichever of these strategies
    are actually run together, diluting each one's dollar contribution
    below its solo-$30k figure. Because this upper bound can only
    OVERSTATE true combined achievability, never understate it: if it
    still falls short of `COMBINATION_TARGET_MONTHLY_DOLLARS`, no
    less-generous (capital-split) combination could reach the target
    either, and no M49 portfolio-combination simulation is warranted."""
    ranked = sorted(arm_mean_monthly_dollars.items(), key=lambda kv: kv[1], reverse=True)
    top_two = ranked[:2]
    top_two_sum = sum((v for _, v in top_two), Decimal(0))
    upper_bound = ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK + top_two_sum
    verdict = COMBINATION_MATHEMATICALLY_PLAUSIBLE if upper_bound >= COMBINATION_TARGET_MONTHLY_DOLLARS else COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE
    return {
        "orb_monthly_dollars_at_one_pct_risk": ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK,
        "top_two_arms": [{"arm": arm, "mean_monthly_dollar_pnl_fixed_30k": value} for arm, value in top_two],
        "generous_upper_bound_monthly_dollars": upper_bound,
        "target_monthly_dollars": COMBINATION_TARGET_MONTHLY_DOLLARS,
        "verdict": verdict,
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


def evaluation_end_mark_transactions(
    *, open_positions_at_end: Sequence[Any], end_date: date, end_price_by_symbol: Mapping[str, Decimal],
    cost_bps: Decimal, arm: str,
) -> tuple[Transaction, ...]:
    """A hypothetical full liquidation of every still-open position at
    `end_date`'s adjusted close, with a hypothetical liquidation cost
    applied -- for net-P&L/concentration/turnover accounting ONLY. Never
    a real strategy-generated exit, never fed back into either
    simulation engine, and always labeled `reason="EVALUATION_END_MARK"`
    so it is never confused with one. `end_date` must be the study's
    fixed overall evaluation endpoint, not an interim segment boundary
    -- an open position that merely straddles a calendar-segment cut is
    NOT liquidated for that segment's own reporting; only the true final
    date gets this treatment."""
    out = []
    for pos in open_positions_at_end:
        price = end_price_by_symbol.get(pos.symbol)
        if price is None:
            continue
        notional = price * pos.quantity
        cost = transaction_cost(notional, cost_bps)
        out.append(Transaction(
            date=end_date, arm=arm, symbol=pos.symbol, side="SELL", quantity=pos.quantity,
            price=price, notional=notional, cost=cost, reason="EVALUATION_END_MARK",
        ))
    return tuple(out)


def exposure_and_concurrency(
    *, entry_exit_pairs: Sequence[tuple[date, date | None]], window_dates: Sequence[date],
) -> dict[str, Any]:
    """`entry_exit_pairs` is `[(entry_date, exit_date_or_None_if_still_open), ...]`
    for every position/holding (closed or still open) touching this arm.
    A position is considered "open" on a session if
    `entry_date <= session <= exit_date` (inclusive both ends -- the
    entry session itself and the exit session itself both count as
    exposed), or `entry_date <= session` with no exit yet. Returns the
    percentage of `window_dates` with at least one position open and the
    average number of concurrent positions across `window_dates`."""
    if not window_dates:
        return {"exposure_pct": None, "average_concurrent_positions": None}
    counts: list[int] = []
    for day in window_dates:
        n = 0
        for entry_date, exit_date in entry_exit_pairs:
            if entry_date <= day and (exit_date is None or day <= exit_date):
                n += 1
        counts.append(n)
    days_with_position = sum(1 for c in counts if c > 0)
    return {
        "exposure_pct": Decimal(days_with_position) / Decimal(len(window_dates)),
        "average_concurrent_positions": Decimal(str(statistics.fmean(counts))) if counts else Decimal(0),
    }


def exposure_and_concurrency_from_daily_counts(
    daily_position_count: Sequence[tuple[date, int]], *, window_dates: Sequence[date],
) -> dict[str, Any]:
    """The preferred exposure/concurrency computation: reads each
    session's ACTUAL open-position count directly off the simulation's
    own `daily_position_count` series (recorded once per session inside
    either engine's own loop), rather than reconstructing overlapping
    intervals from closed-trade records after the fact.
    `exposure_and_concurrency`'s `entry_exit_pairs` reconstruction is
    correct for ARM_A (each closed trade IS one genuine, non-overlapping
    position lifetime) but silently overcounts for ARM_B/ARM_C: a
    continuing holding that gets TRIMMED several times across several
    rebalances produces several `RebalanceTrade` records that all share
    the SAME original `entry_date` with different `exit_date`s, which
    the interval-reconstruction approach double(triple, ...)-counts as
    separate simultaneous positions -- observed producing an average
    concurrency figure larger than the arm's own universe size. This
    function has no such failure mode since it never reconstructs
    anything; it only reads what the engine itself already tracked."""
    if not window_dates:
        return {"exposure_pct": None, "average_concurrent_positions": None}
    counts_by_date = dict(daily_position_count)
    counts = [counts_by_date.get(d, 0) for d in window_dates]
    days_with_position = sum(1 for c in counts if c > 0)
    return {
        "exposure_pct": Decimal(days_with_position) / Decimal(len(window_dates)),
        "average_concurrent_positions": Decimal(str(statistics.fmean(counts))) if counts else Decimal(0),
    }


def compute_arm_metrics(
    *, arm: str, closed_trades: Sequence[Any], daily_equity: Sequence[tuple[date, Decimal]],
    starting_capital: Decimal, total_cost_paid: Decimal, evaluation_start: date, evaluation_end: date,
    open_positions_at_end: Sequence[Any] = (), transactions: Sequence[Transaction] = (),
    end_mark_transactions: Sequence[Transaction] = (), fixed_notional: Decimal = STARTING_CAPITAL,
    daily_position_count: Sequence[tuple[date, int]] = (),
    rebalance_events: Sequence[Any] = (),
) -> dict[str, Any]:
    """The full required-output set for one arm/cost-case, computed over
    `[evaluation_start, evaluation_end]` only (warm-up-period equity
    marks and any pre-evaluation trades are excluded). `closed_trades`
    is the arm's FULL (unfiltered) trade list -- this function does its
    own window filtering for P&L purposes, but needs the full list (plus
    `open_positions_at_end`, for a position still open past the window)
    to correctly compute exposure/concurrency for positions that were
    open DURING the window even if their own entry or exit falls
    outside it. `transactions` is the arm's FULL transaction ledger
    (also unfiltered) -- turnover and this window's own cost total are
    computed from it directly, filtered to this window's own dates, so a
    per-segment call never reports the whole study's cost total.
    `end_mark_transactions` (from `evaluation_end_mark_transactions`) is
    non-empty ONLY when this window's own end is the study's true fixed
    evaluation endpoint AND positions are still open then -- it feeds a
    hypothetical-liquidation-adjusted net P&L and folds each such
    position's own unrealized (marked, cost-adjusted) contribution into
    symbol concentration; it must never be passed for an interim segment
    boundary that merely happens to have open positions mid-flight.
    `rebalance_events` (ARM_B/ARM_C only, from `MonthlyRebalanceSimulationResult.rebalance_events`;
    empty for ARM_A) is the arm's FULL (unfiltered) month-end signal
    log, used only to report `scheduled_rebalance_count` /
    `executed_rebalance_count` / `signal_count` -- it is never used for
    P&L. These counts are kept strictly separate from `closed_lot_count`
    (`len(window_trades)`): a single scheduled rebalance can close
    several lots (one TRIM/REMOVE per continuing or removed symbol), so
    closed-lot count must never be reported or read as a rebalance-event
    count."""
    window_equity = slice_to_window(daily_equity, start=evaluation_start, end=evaluation_end)
    window_trades = [t for t in closed_trades if evaluation_start <= t.exit_date <= evaluation_end]
    window_transactions = [t for t in transactions if evaluation_start <= t.date <= evaluation_end]

    if not window_equity:
        raise CapitalTargetResearchError("no equity marks in the evaluation window")

    baseline_equity = starting_capital
    prior_before_window = [e for d, e in daily_equity if d < evaluation_start]
    if prior_before_window:
        baseline_equity = prior_before_window[-1]
    anchored_equity = [(evaluation_start, baseline_equity)] + window_equity if window_equity[0][0] != evaluation_start else window_equity

    monthly_pct_pairs = monthly_return_pct(anchored_equity)
    monthly_pct = [v for _, v in monthly_pct_pairs]
    fixed_pnl_pairs = fixed_notional_monthly_dollar_pnl(monthly_pct_pairs, notional=fixed_notional)  # PRIMARY
    compounded_pnl_pairs = monthly_dollar_pnl(anchored_equity)  # SECONDARY
    fixed_pnl = [v for _, v in fixed_pnl_pairs]
    compounded_pnl = [v for _, v in compounded_pnl_pairs]

    gross_pnl = sum((t.gross_pnl for t in window_trades), Decimal(0))
    net_pnl_realized = sum((t.net_pnl for t in window_trades), Decimal(0))
    net_pnl_equity = window_equity[-1][1] - baseline_equity

    end_mark_cost = sum((t.cost for t in end_mark_transactions), Decimal(0))
    net_pnl_marked = net_pnl_equity - end_mark_cost  # equity view, less a hypothetical liquidation cost on anything still open at the true evaluation end

    dd = max_drawdown(anchored_equity)
    prof = profitable_month_stats(fixed_pnl)  # sign-equivalent to the compounded series; fixed is the primary basis
    worst_pct = worst_and_percentile_month(monthly_pct)

    pnl_by_symbol: dict[str, Decimal] = {}
    for t in window_trades:
        pnl_by_symbol[t.symbol] = pnl_by_symbol.get(t.symbol, Decimal(0)) + t.net_pnl
    end_mark_by_symbol = {t.symbol: t for t in end_mark_transactions}
    for pos in open_positions_at_end:
        mark = end_mark_by_symbol.get(pos.symbol)
        if mark is None:
            continue
        unrealized_gross = (mark.price - pos.entry_price) * pos.quantity
        unrealized_net = unrealized_gross - pos.entry_cost - mark.cost
        pnl_by_symbol[pos.symbol] = pnl_by_symbol.get(pos.symbol, Decimal(0)) + unrealized_net
    concentration = positive_contribution_concentration(pnl_by_symbol)

    trade_notionals = [t.notional for t in window_transactions] + [t.notional for t in end_mark_transactions]
    window_cost_paid = sum((t.cost for t in window_transactions), Decimal(0)) + end_mark_cost
    average_equity = (sum((e for _, e in window_equity), Decimal(0)) / Decimal(len(window_equity))) if window_equity else starting_capital
    turnover = turnover_and_costs(trade_notionals, window_cost_paid, average_equity)

    segment_net: dict[str, Decimal] = {}
    for seg in CALENDAR_SEGMENTS:
        seg_marks = [(d, e) for d, e in anchored_equity if segment_label(d) == seg]
        if not seg_marks:
            continue
        seg_start_candidates = [e for d, e in anchored_equity if d < seg_marks[0][0]]
        seg_baseline = seg_start_candidates[-1] if seg_start_candidates else baseline_equity
        segment_net[seg] = seg_marks[-1][1] - seg_baseline

    exposure = exposure_and_concurrency_from_daily_counts(daily_position_count, window_dates=[d for d, _ in window_equity])

    # Count separation (Finding 14, independent review): closed-lot
    # count is NEVER a rebalance-event count -- a single scheduled
    # rebalance can produce several closed lots (one TRIM/REMOVE per
    # affected symbol). All counts are derived strictly from the
    # explicit ledgers already computed above, windowed the same way
    # (`window_trades`/`window_transactions` filter by exit/transaction
    # date; rebalance events below filter by EXECUTION date, matching
    # that same convention, since a signal generated just before
    # `evaluation_start` that legitimately executes on the first
    # in-window session -- see `run_monthly_rebalance_arm`'s Finding 10
    # gating -- must still be counted in this window).
    closed_lot_count = len(window_trades)
    buy_transaction_count = sum(1 for t in window_transactions if t.side == "BUY")
    sell_transaction_count = sum(1 for t in window_transactions if t.side == "SELL")
    window_rebalance_events = [
        e for e in rebalance_events
        if e.execution_date is not None and evaluation_start <= e.execution_date <= evaluation_end
    ]
    scheduled_rebalance_count = len(window_rebalance_events)
    executed_transaction_dates = {t.date for t in window_transactions}
    executed_rebalance_count = sum(1 for e in window_rebalance_events if e.execution_date in executed_transaction_dates)
    # For ARM_A (no rebalance_events passed) a "signal" is every entry
    # actually filled; for ARM_B/ARM_C a "signal" is every month-end
    # selector call that got scheduled to execute in this window.
    signal_count = scheduled_rebalance_count if rebalance_events else sum(1 for t in window_transactions if t.reason == "ENTRY")

    return {
        "arm": arm,
        "closed_lot_count": closed_lot_count,
        "signal_count": signal_count,
        "scheduled_rebalance_count": scheduled_rebalance_count,
        "executed_rebalance_count": executed_rebalance_count,
        "buy_transaction_count": buy_transaction_count,
        "sell_transaction_count": sell_transaction_count,
        "exposure_pct": exposure["exposure_pct"],
        "average_concurrent_positions": exposure["average_concurrent_positions"],
        "gross_pnl": gross_pnl,
        "net_pnl_realized": net_pnl_realized,
        "net_pnl": net_pnl_equity,
        "net_pnl_marked": net_pnl_marked,
        "end_mark_liquidation_cost": end_mark_cost,
        "mean_monthly_dollar_pnl_fixed_30k": (statistics.fmean(_to_float_list(fixed_pnl)) if fixed_pnl else None),
        "median_monthly_dollar_pnl_fixed_30k": (statistics.median(_to_float_list(fixed_pnl)) if fixed_pnl else None),
        "mean_monthly_dollar_pnl_compounded": (statistics.fmean(_to_float_list(compounded_pnl)) if compounded_pnl else None),
        "median_monthly_dollar_pnl_compounded": (statistics.median(_to_float_list(compounded_pnl)) if compounded_pnl else None),
        "mean_monthly_return_pct": (Decimal(str(statistics.fmean(_to_float_list(monthly_pct)))) if monthly_pct else Decimal(0)),
        "median_monthly_return_pct": (statistics.median(_to_float_list(monthly_pct)) if monthly_pct else None),
        "cagr": cagr(anchored_equity),
        "sharpe": sharpe_ratio(monthly_pct),
        "sortino": sortino_ratio(monthly_pct),
        "annualized_volatility_pct": annualized_volatility_pct(monthly_pct),
        "annualized_downside_deviation_pct": annualized_downside_deviation_pct(monthly_pct),
        "max_drawdown_dollars": dd["max_drawdown_dollars"],
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "recovery_days": dd["recovery_days"],
        "profitable_month_pct": prof["profitable_month_pct"],
        "longest_losing_streak": prof["longest_losing_streak"],
        "worst_month_dollars_fixed_30k": worst_and_percentile_month(fixed_pnl)["worst_month"],
        "fifth_percentile_month_pct": worst_pct["percentile_month"],
        "concentration": concentration,
        "turnover": turnover,
        "monthly_dollar_pnl_fixed_30k_series": fixed_pnl_pairs,
        "monthly_dollar_pnl_compounded_series": compounded_pnl_pairs,
        "monthly_return_pct_series": monthly_pct_pairs,
        "segment_net_pnl": segment_net,
        "starting_capital": baseline_equity,
        "ending_equity": window_equity[-1][1],
        "total_cost_paid": window_cost_paid,
        "reconciliation": {
            "symbol_contribution_sum": sum(pnl_by_symbol.values(), Decimal(0)),
            "net_pnl_marked": net_pnl_marked,
            "residual": net_pnl_marked - sum(pnl_by_symbol.values(), Decimal(0)),
        },
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
    "OpenPosition", "ClosedTrade", "PositionSnapshot", "ArmASimulationResult", "run_arm_a",
    "RebalanceHolding", "RebalanceTrade", "RebalanceEvent", "MonthlyRebalanceSimulationResult",
    "month_end_indices", "run_monthly_rebalance_arm", "arm_b_selector", "arm_c_selector",
    "monthly_equity_marks", "monthly_dollar_pnl", "monthly_return_pct", "cagr", "max_drawdown",
    "sharpe_ratio", "sortino_ratio", "annualized_volatility_pct", "annualized_downside_deviation_pct",
    "profitable_month_stats", "worst_and_percentile_month",
    "pearson_correlation", "align_monthly_series", "positive_contribution_concentration", "turnover_and_costs",
    "CLASSIFICATION_DIRECT_TARGET_FEASIBLE", "CLASSIFICATION_PROMISING_PORTFOLIO_CONTRIBUTOR",
    "CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT", "CLASSIFICATION_NOT_PROMISING",
    "DIRECT_TARGET_MONTHLY_DOLLARS", "DIRECT_TARGET_MAX_DRAWDOWN_PCT", "CONTRIBUTOR_MONTHLY_DOLLARS",
    "CONTRIBUTOR_MAX_DRAWDOWN_PCT", "CONTRIBUTOR_MIN_PROFITABLE_MONTH_PCT", "CONTRIBUTOR_MAX_ORB_CORRELATION",
    "MAX_SINGLE_SYMBOL_POSITIVE_SHARE", "REQUIRED_POSITIVE_SEGMENTS", "ALL_FIVE_CALENDAR_SEGMENTS",
    "CONTRIBUTOR_MIN_NONNEGATIVE_SEGMENTS", "CONTRIBUTOR_MIN_POSITIVE_SEGMENTS",
    "classify_capital_target_arm",
    "ORB_MONTHLY_DOLLARS_AT_ONE_PCT_RISK", "COMBINATION_TARGET_MONTHLY_DOLLARS",
    "COMBINATION_MATHEMATICALLY_PLAUSIBLE", "COMBINATION_NOT_MATHEMATICALLY_PLAUSIBLE",
    "compute_combination_plausibility_upper_bound",
    "CALENDAR_SEGMENTS", "segment_label", "slice_to_window", "scale_trade_to_risk_cap", "compute_arm_metrics",
    "exposure_and_concurrency", "exposure_and_concurrency_from_daily_counts",
    "Transaction", "evaluation_end_mark_transactions", "fixed_notional_monthly_dollar_pnl",
    "fill_missing_months_with_zero",
]
