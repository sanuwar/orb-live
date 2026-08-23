"""M52 (Issue #356): concentrated point-in-time cross-sectional equity
momentum feasibility -- the exact single strategy M51 selected
(`SELECTED_FOR_M52`, `CONCENTRATED_CROSS_SECTIONAL_MOMENTUM`), tested
once, frozen, on a $30,000 account.

This module is the frozen protocol and pure research logic: universe
provenance dataclasses, the point-in-time snapshot-selection rule,
N-PORT holdings parsing, liquidity eligibility, the 12-1 momentum
signal, the SPY 200-session trend filter, the monthly-rebalance
portfolio simulator, metrics, and the frozen classification function --
committed before any empirical result is read. All network I/O (SEC
EDGAR N-PORT fetch, OpenFIGI identifier resolution, Alpaca daily-bar
fetch) lives in `scripts/research_cross_sectional_momentum.py`; this
module only consumes already-fetched data.

Universe: iShares Russell 1000 ETF (IWB) official point-in-time
holdings from SEC EDGAR N-PORT-P filings (CIK 0001100663, series
S000004347, class C000012077 -- independently verified against SEC's
own series/class index, not assumed). At every monthly rebalance, only
the most recently PUBLISHED N-PORT-P snapshot (filing date <= signal
date) is used -- Form N-PORT is filed quarterly with a ~60-day public-
disclosure lag (only the third month of each fiscal quarter is
publicly disclosed), so a signal date's eligible universe is normally
2-5 months stale relative to that date, by design, matching what was
genuinely knowable at the time.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping, Sequence

from src.orb_capital_target_strategy_family_research import (
    DailySeries,
    annualized_downside_deviation_pct,
    cagr,
    fixed_notional_monthly_dollar_pnl,
    max_drawdown,
    monthly_return_pct,
    pearson_correlation,
    sharpe_ratio,
    sortino_ratio,
)

__all__ = [
    "STARTING_CAPITAL",
    "TOP_N",
    "MOMENTUM_LOOKBACK_SESSIONS",
    "MOMENTUM_SKIP_SESSIONS",
    "TREND_FILTER_SMA_SESSIONS",
    "LIQUIDITY_LOOKBACK_SESSIONS",
    "MIN_MEDIAN_CLOSE",
    "MIN_MEDIAN_DOLLAR_VOLUME",
    "MIN_MEDIAN_SHARE_VOLUME",
    "MIN_SESSION_COVERAGE_PCT",
    "BASE_COST_BPS",
    "STRESS_COST_BPS",
    "EVALUATION_START",
    "EVALUATION_END",
    "WARMUP_TARGET_START",
    "CALENDAR_SEGMENTS",
    "IWB_CIK",
    "IWB_SERIES_ID",
    "IWB_CLASS_ID",
    "IWB_TICKER",
    "IWB_FUND_NAME",
    "PRACTICAL_TARGET_MONTHLY_DOLLARS",
    "STRETCH_TARGET_MONTHLY_DOLLARS",
    "CLASSIFICATION_TARGET_FEASIBLE",
    "CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT",
    "CLASSIFICATION_NOT_PROMISING",
    "CLASSIFICATION_INSUFFICIENT_EVIDENCE",
    "TARGET_MIN_BASE_MEAN_MONTHLY_DOLLARS",
    "TARGET_MAX_BASE_DRAWDOWN_PCT",
    "TARGET_MIN_POSITIVE_SEGMENTS",
    "TOTAL_SEGMENTS",
    "TARGET_MIN_PROFITABLE_MONTH_PCT",
    "TARGET_WORST_MONTH_FLOOR_PCT",
    "TARGET_MAX_SYMBOL_CONTRIBUTION_SHARE",
    "POSITIVE_INSUFFICIENT_MAX_DRAWDOWN_PCT",
    "NOT_PROMISING_MAX_DRAWDOWN_PCT",
    "NOT_PROMISING_MIN_POSITIVE_SEGMENTS",
    "M49_FROZEN_REFERENCE",
    "NportFiling",
    "select_filing_for_signal_date",
    "month_end_signal_dates",
    "RawHolding",
    "ASSET_CATEGORY_COMMON_EQUITY",
    "ISSUER_CATEGORY_CORPORATE",
    "parse_nport_holdings_xml",
    "ResolutionCandidate",
    "ResolvedHolding",
    "resolve_holding",
    "UniverseSnapshot",
    "LiquidityCheck",
    "evaluate_liquidity_eligibility",
    "compute_12_1_momentum",
    "MomentumRanking",
    "rank_and_select_top_n",
    "trend_filter_active",
    "MomentumPosition",
    "MomentumTransaction",
    "BlockedIntent",
    "PortfolioSnapshot",
    "PortfolioSimulationResult",
    "MonthlySignal",
    "simulate_cross_sectional_momentum_portfolio",
    "segment_label",
    "compute_portfolio_metrics",
    "classify_cross_sectional_momentum",
    "CrossSectionalMomentumError",
]


class CrossSectionalMomentumError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Frozen configuration (Issue #356). Every value here is fixed for the
# single M52 experiment -- never swept.
# ---------------------------------------------------------------------------

STARTING_CAPITAL = Decimal("30000")
TOP_N = 15
MOMENTUM_LOOKBACK_SESSIONS = 252
MOMENTUM_SKIP_SESSIONS = 21
TREND_FILTER_SMA_SESSIONS = 200

LIQUIDITY_LOOKBACK_SESSIONS = 60
MIN_MEDIAN_CLOSE = Decimal("10")
MIN_MEDIAN_DOLLAR_VOLUME = Decimal("50000000")
MIN_MEDIAN_SHARE_VOLUME = 500_000
MIN_SESSION_COVERAGE_PCT = Decimal("0.95")

BASE_COST_BPS = Decimal("0.0005")
STRESS_COST_BPS = Decimal("0.0010")

EVALUATION_START = date(2021, 1, 4)
EVALUATION_END = date(2026, 8, 13)
WARMUP_TARGET_START = date(2020, 1, 2)

CALENDAR_SEGMENTS: tuple[str, ...] = ("2021", "2022", "2023", "2024", "2025", "2026_YTD")

#: Independently verified against SEC's own series/class index (a listing
#: of every iShares Trust series, each with its own S000.../C000... ID) --
#: not assumed from the ticker alone. "iShares Russell 1000 Growth ETF"
#: (S000004346) and "iShares Russell 1000 Value ETF" (S000004345) are
#: adjacent, easily-confused series; this is deliberately the plain
#: "iShares Russell 1000 ETF" entry only.
IWB_CIK = "0001100663"
IWB_SERIES_ID = "S000004347"
IWB_CLASS_ID = "C000012077"
IWB_TICKER = "IWB"
IWB_FUND_NAME = "iShares Russell 1000 ETF"

PRACTICAL_TARGET_MONTHLY_DOLLARS = Decimal("500")
STRETCH_TARGET_MONTHLY_DOLLARS = Decimal("1000")

CLASSIFICATION_TARGET_FEASIBLE = "TARGET_FEASIBLE"
CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT = "POSITIVE_BUT_INSUFFICIENT"
CLASSIFICATION_NOT_PROMISING = "NOT_PROMISING"
CLASSIFICATION_INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"

TARGET_MIN_BASE_MEAN_MONTHLY_DOLLARS = Decimal("500")
TARGET_MAX_BASE_DRAWDOWN_PCT = Decimal("0.25")
TARGET_MIN_POSITIVE_SEGMENTS = 5
TOTAL_SEGMENTS = 6
TARGET_MIN_PROFITABLE_MONTH_PCT = Decimal("0.55")
TARGET_WORST_MONTH_FLOOR_PCT = Decimal("-0.10")
TARGET_MAX_SYMBOL_CONTRIBUTION_SHARE = Decimal("0.35")
POSITIVE_INSUFFICIENT_MAX_DRAWDOWN_PCT = Decimal("0.35")
NOT_PROMISING_MAX_DRAWDOWN_PCT = Decimal("0.35")
NOT_PROMISING_MIN_POSITIVE_SEGMENTS = 3

#: M49's own 2022-2026 headline result, carried forward only as a
#: reference point (Issue #356: "comparison with frozen M49 results").
#: Never reopened, retuned, or combined with M52's own evaluation.
M49_FROZEN_REFERENCE: dict[str, object] = {
    "base_mean_monthly_dollars": "184.75",
    "stress_mean_monthly_dollars": "122.02",
    "base_max_drawdown_pct": "0.1460",
    "classification": "SAFE_BUT_BELOW_TARGET",
    "note": "Frozen reference only. M52 does not reopen, retune, or combine with this result.",
}


# ---------------------------------------------------------------------------
# Universe provenance: point-in-time N-PORT filing selection.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NportFiling:
    series_id: str
    cik: str
    accession: str
    period_ending: date
    file_date: date
    document_url: str

    def __post_init__(self) -> None:
        if self.file_date < self.period_ending:
            raise CrossSectionalMomentumError(
                f"{self.accession}: file_date {self.file_date} precedes its own period_ending {self.period_ending}"
            )


def select_filing_for_signal_date(filings: Sequence[NportFiling], *, signal_date: date) -> NportFiling | None:
    """The single most-recently-PUBLISHED filing whose `file_date` is on
    or before `signal_date` -- never a future snapshot, never the
    filing with the latest `period_ending` (that would be lookahead: a
    filing only becomes usable once it is actually public). Returns
    `None` (fail closed) if no qualifying filing exists yet."""
    eligible = [f for f in filings if f.file_date <= signal_date]
    if not eligible:
        return None
    return max(eligible, key=lambda f: (f.file_date, f.period_ending))


# ---------------------------------------------------------------------------
# N-PORT holdings parsing and filtering.
# ---------------------------------------------------------------------------

#: N-PORT `assetCat` for a common-equity position -- excludes cash (STIV),
#: preferred equity (EP), debt, derivatives, and other fund shares.
ASSET_CATEGORY_COMMON_EQUITY = "EC"
#: N-PORT `issuerCat` for a corporate issuer -- excludes government/
#: municipal/other non-corporate issuers.
ISSUER_CATEGORY_CORPORATE = "CORP"


@dataclass(frozen=True)
class RawHolding:
    name: str
    cusip: str
    isin: str | None
    asset_category: str
    issuer_category: str
    invest_country: str
    balance: Decimal | None = None
    val_usd: Decimal | None = None
    pct_val: Decimal | None = None


def parse_nport_holdings_xml(xml_text: str) -> tuple[RawHolding, ...]:
    """Every `<invstOrSec>` record in a Form N-PORT-P primary document,
    filtered to US common-equity corporate issuers only (`assetCat=="EC"`,
    `issuerCat=="CORP"`, `invCountry=="US"`) -- excludes cash, derivatives,
    other ETF shares, preferred stock, and non-US issuers, matching Issue
    #356's 'US common stocks only' requirement structurally (via the
    filing's own categorization), not by name-matching heuristics."""
    import xml.etree.ElementTree as ET

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    root = ET.fromstring(xml_text)
    holdings: list[RawHolding] = []
    for elem in root.iter():
        if _local(elem.tag) != "invstOrSec":
            continue

        # N-PORT-P documents declare a default XML namespace
        # (xmlns="http://www.sec.gov/edgar/nport"), which makes every
        # child element's own `.tag` namespaced too
        # (e.g. "{http://www.sec.gov/edgar/nport}assetCat") --
        # `Element.find("assetCat")` never matches that, so this looks
        # up direct children by their LOCAL (namespace-stripped) tag
        # name instead, exactly like the `invstOrSec` match above.
        direct_children = {_local(child.tag): child for child in elem}

        def _text(tag: str) -> str | None:
            child = direct_children.get(tag)
            return child.text.strip() if child is not None and child.text else None

        asset_cat = _text("assetCat")
        issuer_cat = _text("issuerCat")
        invest_country = _text("invCountry")
        if asset_cat != ASSET_CATEGORY_COMMON_EQUITY:
            continue
        if issuer_cat != ISSUER_CATEGORY_CORPORATE:
            continue
        if invest_country != "US":
            continue
        name = _text("name")
        cusip = _text("cusip")
        if not name or not cusip:
            continue
        isin = None
        identifiers_elem = direct_children.get("identifiers")
        if identifiers_elem is not None:
            for child in identifiers_elem:
                if _local(child.tag) == "isin":
                    isin = child.get("value")
                    break
        balance_text = _text("balance")
        val_text = _text("valUSD")
        pct_text = _text("pctVal")
        holdings.append(RawHolding(
            name=name, cusip=cusip, isin=isin, asset_category=asset_cat, issuer_category=issuer_cat,
            invest_country=invest_country,
            balance=Decimal(balance_text) if balance_text else None,
            val_usd=Decimal(val_text) if val_text else None,
            pct_val=Decimal(pct_text) if pct_text else None,
        ))
    return tuple(holdings)


# ---------------------------------------------------------------------------
# Identifier resolution (CUSIP-first, ISIN fallback) -- the actual
# lookup is an external call (OpenFIGI, in the CLI layer); this module
# only decides, from already-fetched candidate results, whether a
# holding resolves uniquely or must be preserved as unresolved.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolutionCandidate:
    ticker: str
    exch_code: str
    name: str


#: Bloomberg exchange codes for the major US common-stock listing venues
#: this universe can plausibly resolve to. Anything outside this set
#: (foreign cross-listings, OTC, ADR variants) is not treated as a match.
US_PRIMARY_EXCHANGE_CODES = frozenset({"UN", "UW", "UA", "UR", "UQ", "UD", "UP", "UF"})


@dataclass(frozen=True)
class ResolvedHolding:
    raw: RawHolding
    ticker: str | None
    resolution_status: str  # "RESOLVED" | "UNRESOLVED" | "AMBIGUOUS"
    candidates: tuple[ResolutionCandidate, ...] = field(default_factory=tuple)


#: A plausible US common-stock ticker: 1-5 uppercase letters, optionally
#: a share-class suffix. OpenFIGI's own tickers use "/" for share
#: classes (e.g. "BRK/B" for Berkshire Hathaway Class B); confirmed live
#: against Alpaca that its API instead expects "." (e.g. "BRK.B" --
#: "BRK-B" 400s, "BRKB" silently returns no bars). Both real, valid
#: share-class notations are accepted here; `_normalize_ticker` below
#: converts to the "." form Alpaca actually expects. OpenFIGI's search
#: was ALSO found, via a live run, to sometimes return an internal
#: placeholder "ticker" (e.g. "0673085D" for Pluralsight LLC, acquired/
#: delisted in 2021, or "9990302D" for Apache Corp) for securities with
#: no currently tradeable listing -- exactly the "delisted/renamed/
#: acquired... never silently replace" case the frozen protocol
#: anticipates. Such values match neither notation and are filtered out
#: here, before the uniqueness check, rather than accepted as a
#: resolution.
_PLAUSIBLE_TICKER_PATTERN = re.compile(r"^[A-Z]{1,5}([./][A-Z]{1,2})?$")


def _is_plausible_ticker(ticker: str) -> bool:
    return bool(_PLAUSIBLE_TICKER_PATTERN.match(ticker))


def _normalize_ticker(ticker: str) -> str:
    """OpenFIGI's "/" share-class notation (e.g. "BRK/B") normalized to
    the "." notation Alpaca's API actually expects ("BRK.B"), confirmed
    live. A ticker with no "/" is returned unchanged."""
    return ticker.replace("/", ".")


def resolve_holding(raw: RawHolding, candidates: Sequence[ResolutionCandidate]) -> ResolvedHolding:
    """Deterministic, fail-closed resolution from already-fetched
    candidate search results: resolves only if filtering to recognized
    US primary-exchange codes AND plausible ticker formatting yields
    EXACTLY ONE distinct NORMALIZED ticker. Zero matches -> `UNRESOLVED`;
    more than one distinct ticker -> `AMBIGUOUS`. Never guesses, never
    substitutes a current successor for an unresolved historical
    security."""
    us_candidates = tuple(
        c for c in candidates if c.exch_code in US_PRIMARY_EXCHANGE_CODES and _is_plausible_ticker(c.ticker)
    )
    distinct_tickers = sorted({_normalize_ticker(c.ticker) for c in us_candidates})
    if len(distinct_tickers) == 1:
        return ResolvedHolding(raw=raw, ticker=distinct_tickers[0], resolution_status="RESOLVED", candidates=tuple(us_candidates))
    if len(distinct_tickers) == 0:
        return ResolvedHolding(raw=raw, ticker=None, resolution_status="UNRESOLVED", candidates=tuple(us_candidates))
    return ResolvedHolding(raw=raw, ticker=None, resolution_status="AMBIGUOUS", candidates=tuple(us_candidates))


@dataclass(frozen=True)
class UniverseSnapshot:
    filing: NportFiling
    holdings: tuple[ResolvedHolding, ...]
    raw_holdings_sha256: str
    normalized_holdings_sha256: str

    @property
    def resolved_tickers(self) -> tuple[str, ...]:
        return tuple(sorted({h.ticker for h in self.holdings if h.resolution_status == "RESOLVED" and h.ticker}))

    @property
    def unresolved_count(self) -> int:
        return sum(1 for h in self.holdings if h.resolution_status != "RESOLVED")


# ---------------------------------------------------------------------------
# Liquidity eligibility.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LiquidityCheck:
    symbol: str
    as_of: date
    eligible: bool
    median_close: Decimal | None
    median_dollar_volume: Decimal | None
    median_share_volume: int | None
    session_coverage_pct: Decimal | None
    reason: str


def evaluate_liquidity_eligibility(
    *, symbol: str, as_of: date, closes: Sequence[Decimal], volumes: Sequence[int],
    expected_session_count: int,
) -> LiquidityCheck:
    """`closes`/`volumes` must already be exactly the trailing
    `LIQUIDITY_LOOKBACK_SESSIONS` sessions strictly before/through
    `as_of` (the caller's responsibility -- this function does no
    calendar lookup, only the pass/fail arithmetic, so it stays free of
    lookahead by construction: the caller can never accidentally hand
    it a future bar)."""
    if len(closes) != len(volumes):
        raise CrossSectionalMomentumError(f"{symbol}: closes/volumes length mismatch ({len(closes)} vs {len(volumes)})")
    n = len(closes)
    coverage = Decimal(n) / Decimal(expected_session_count) if expected_session_count > 0 else Decimal(0)
    if n == 0 or coverage < MIN_SESSION_COVERAGE_PCT:
        return LiquidityCheck(
            symbol=symbol, as_of=as_of, eligible=False, median_close=None, median_dollar_volume=None,
            median_share_volume=None, session_coverage_pct=coverage,
            reason=f"insufficient session coverage ({n}/{expected_session_count} = {coverage:.4f} < {MIN_SESSION_COVERAGE_PCT})",
        )
    sorted_closes = sorted(closes)
    median_close = sorted_closes[n // 2] if n % 2 == 1 else (sorted_closes[n // 2 - 1] + sorted_closes[n // 2]) / 2
    dollar_volumes = sorted(c * Decimal(v) for c, v in zip(closes, volumes))
    median_dollar_volume = dollar_volumes[n // 2] if n % 2 == 1 else (dollar_volumes[n // 2 - 1] + dollar_volumes[n // 2]) / 2
    sorted_volumes = sorted(volumes)
    median_share_volume = sorted_volumes[n // 2] if n % 2 == 1 else (sorted_volumes[n // 2 - 1] + sorted_volumes[n // 2]) // 2

    failures = []
    if median_close < MIN_MEDIAN_CLOSE:
        failures.append(f"median close {median_close} < {MIN_MEDIAN_CLOSE}")
    if median_dollar_volume < MIN_MEDIAN_DOLLAR_VOLUME:
        failures.append(f"median dollar volume {median_dollar_volume} < {MIN_MEDIAN_DOLLAR_VOLUME}")
    if median_share_volume < MIN_MEDIAN_SHARE_VOLUME:
        failures.append(f"median share volume {median_share_volume} < {MIN_MEDIAN_SHARE_VOLUME}")

    eligible = len(failures) == 0
    return LiquidityCheck(
        symbol=symbol, as_of=as_of, eligible=eligible, median_close=median_close,
        median_dollar_volume=median_dollar_volume, median_share_volume=median_share_volume,
        session_coverage_pct=coverage, reason="ok" if eligible else "; ".join(failures),
    )


# ---------------------------------------------------------------------------
# 12-1 momentum signal and ranking.
# ---------------------------------------------------------------------------


def compute_12_1_momentum(closes: Sequence[Decimal]) -> Decimal | None:
    """`closes` must be exactly the trailing `MOMENTUM_LOOKBACK_SESSIONS +
    1` adjusted closes ending at (and including) the signal session, so
    `closes[-1]` is `adjusted_close[t]`, `closes[-1 - MOMENTUM_SKIP_
    SESSIONS]` is `adjusted_close[t-21]`, and `closes[0]` is
    `adjusted_close[t-252]`. Returns `None` (never a guess) if there is
    not enough history."""
    required = MOMENTUM_LOOKBACK_SESSIONS + 1
    if len(closes) < required:
        return None
    trailing = closes[-required:]
    close_t_minus_252 = trailing[0]
    close_t_minus_21 = trailing[-1 - MOMENTUM_SKIP_SESSIONS]
    if close_t_minus_252 == 0:
        return None
    return close_t_minus_21 / close_t_minus_252 - 1


@dataclass(frozen=True)
class MomentumRanking:
    signal_date: date
    selected: tuple[str, ...]
    momentum_by_symbol: dict[str, Decimal]
    eligible_count: int
    shortfall: int


def rank_and_select_top_n(momentum_by_symbol: Mapping[str, Decimal], *, signal_date: date, top_n: int = TOP_N) -> MomentumRanking:
    """Rank descending by momentum, ties broken by ticker ascending
    (deterministic, per Issue #356); select exactly the top `top_n`, or
    fewer if fewer are eligible -- never padded."""
    ranked = sorted(momentum_by_symbol.items(), key=lambda kv: (-kv[1], kv[0]))
    selected = tuple(symbol for symbol, _ in ranked[:top_n])
    shortfall = max(0, top_n - len(momentum_by_symbol))
    return MomentumRanking(
        signal_date=signal_date, selected=selected, momentum_by_symbol=dict(momentum_by_symbol),
        eligible_count=len(momentum_by_symbol), shortfall=shortfall,
    )


# ---------------------------------------------------------------------------
# Market/trend filter.
# ---------------------------------------------------------------------------


def trend_filter_active(spy_closes: Sequence[Decimal]) -> bool | None:
    """`spy_closes` must be exactly the trailing `TREND_FILTER_SMA_
    SESSIONS + 1` SPY adjusted closes ending at the signal session, so
    `spy_closes[-1]` is the signal-date close. Returns `None` (never a
    guess) if there is not enough history. The filter is active (long
    permitted) only if the signal-date close is STRICTLY greater than
    the trailing 200-session SMA computed from the 200 sessions
    immediately preceding it (not including the signal-date bar
    itself, matching a standard trend-following convention)."""
    required = TREND_FILTER_SMA_SESSIONS + 1
    if len(spy_closes) < required:
        return None
    trailing = spy_closes[-required:]
    signal_close = trailing[-1]
    sma_window = trailing[:-1]
    sma = sum(sma_window) / Decimal(len(sma_window))
    return signal_close > sma


# ---------------------------------------------------------------------------
# Portfolio mechanics: monthly rebalance, equal-capital weighting, whole
# shares, deterministic ticker-ascending event ordering.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MomentumPosition:
    symbol: str
    entry_date: date
    quantity: int
    #: Weighted-average cost basis PER SHARE, including this position's
    #: own proportional buy-side costs -- updated on every additional
    #: buy, unchanged by a partial sell (standard weighted-average-cost
    #: accounting), so `(sell_price - avg_cost_basis) * qty - sell_cost`
    #: is a correct realized-P&L figure at every sale.
    avg_cost_basis: Decimal


@dataclass(frozen=True)
class MomentumTransaction:
    date: date
    symbol: str
    side: str
    quantity: int
    price: Decimal
    notional: Decimal
    cost: Decimal
    reason: str
    #: Set only for SELL transactions: `(price - avg_cost_basis) * quantity
    #: - cost`, the realized gain/loss on the shares sold, net of both
    #: this sale's own cost and the proportional buy-side cost already
    #: folded into `avg_cost_basis`. `None` for BUY transactions.
    realized_pnl: Decimal | None = None


@dataclass(frozen=True)
class BlockedIntent:
    date: date
    symbol: str
    side: str
    desired_quantity: int
    filled_quantity: int
    reason: str


@dataclass(frozen=True)
class PortfolioSnapshot:
    date: date
    cash: Decimal
    positions: dict[str, int] = field(default_factory=dict)
    equity: Decimal = Decimal(0)


@dataclass(frozen=True)
class MonthlySignal:
    signal_date: date
    execution_date: date | None
    trend_active: bool | None
    filing_accession: str | None
    eligible_count: int
    selected: tuple[str, ...]
    shortfall: int


@dataclass(frozen=True)
class PortfolioSimulationResult:
    cost_label: str
    transactions: tuple[MomentumTransaction, ...]
    blocked_intents: tuple[BlockedIntent, ...]
    daily_snapshots: tuple[PortfolioSnapshot, ...]
    monthly_signals: tuple[MonthlySignal, ...]
    open_positions_at_end: dict[str, MomentumPosition]
    final_cash: Decimal


def month_end_signal_dates(master_dates: Sequence[date], *, start: date, end: date) -> tuple[date, ...]:
    """The last session of every calendar month with at least one
    session in `[start, end]`, PLUS the month immediately before `start`
    (its own month-end is the signal whose execution lands on `start`
    itself, per Issue #356's frozen mechanics)."""
    by_month: dict[tuple[int, int], date] = {}
    for d in master_dates:
        key = (d.year, d.month)
        if key not in by_month or d > by_month[key]:
            by_month[key] = d
    relevant = sorted(d for d in by_month.values() if start <= d <= end)
    prior_candidates = sorted((d for d in by_month.values() if d < start), reverse=True)
    if prior_candidates:
        relevant = [prior_candidates[0]] + relevant
    return tuple(sorted(relevant))


def _next_session_after(master_dates: Sequence[date], day: date) -> date | None:
    for d in master_dates:
        if d > day:
            return d
    return None


def _closes_ending_at(series: DailySeries, *, as_of: date, window: int) -> tuple[Decimal, ...] | None:
    date_index = {d: i for i, d in enumerate(series.dates)}
    if as_of not in date_index:
        return None
    idx = date_index[as_of]
    if idx + 1 < window:
        return None
    return series.closes[idx + 1 - window: idx + 1]


def _trailing_window_ending_before(series: DailySeries, *, as_of: date, window: int) -> tuple[tuple[Decimal, ...], tuple[int, ...]] | None:
    """The `window` sessions STRICTLY BEFORE `as_of` (never including
    `as_of` itself) -- used for liquidity eligibility, which Issue #356
    scopes to data available "by that signal date" without specifying
    same-day inclusion; excluding the signal-date bar itself is the more
    conservative, unambiguously-no-lookahead reading."""
    dates = series.dates
    idx = None
    for i, d in enumerate(dates):
        if d >= as_of:
            idx = i
            break
    if idx is None or idx < window:
        return None
    closes = series.closes[idx - window: idx]
    volumes = series.volumes[idx - window: idx]
    return closes, volumes


def simulate_cross_sectional_momentum_portfolio(
    *,
    monthly_universes: Mapping[date, tuple[str, ...]],
    series_by_symbol: Mapping[str, DailySeries],
    spy_series: DailySeries,
    master_dates: Sequence[date],
    evaluation_start: date = EVALUATION_START,
    evaluation_end: date = EVALUATION_END,
    cost_bps: Decimal,
    cost_label: str,
    starting_capital: Decimal = STARTING_CAPITAL,
    filing_accession_for_signal: Mapping[date, str] | None = None,
) -> PortfolioSimulationResult:
    """The single frozen monthly-rebalance event loop (Issue #356): at
    each month-end signal date, evaluate the SPY trend filter and (if
    active) rank the point-in-time-eligible, liquidity-filtered universe
    by 12-1 momentum, selecting the top `TOP_N`; execute at the next
    session's open with equal-capital weighting, whole shares, sells
    before buys, deterministic ticker-ascending tie-breaks, and cash
    that never goes negative. Marks the endpoint at `evaluation_end`'s
    exact close, never a positional lookup."""
    signal_dates = month_end_signal_dates(master_dates, start=evaluation_start, end=evaluation_end)
    transactions: list[MomentumTransaction] = []
    blocked: list[BlockedIntent] = []
    monthly_signals: list[MonthlySignal] = []
    positions: dict[str, MomentumPosition] = {}
    cash = starting_capital

    def _price_on(symbol: str, day: date) -> Decimal | None:
        series = series_by_symbol.get(symbol)
        if series is None:
            return None
        try:
            idx = series.dates.index(day)
        except ValueError:
            return None
        return series.opens[idx]

    def _close_on(symbol: str, day: date) -> Decimal | None:
        series = series_by_symbol.get(symbol)
        if series is None:
            return None
        try:
            idx = series.dates.index(day)
        except ValueError:
            return None
        return series.closes[idx]

    for signal_date in signal_dates:
        execution_date = _next_session_after(master_dates, signal_date)
        spy_window = _closes_ending_at(spy_series, as_of=signal_date, window=TREND_FILTER_SMA_SESSIONS + 1)
        trend_active = trend_filter_active(spy_window) if spy_window is not None else None

        selected: tuple[str, ...] = ()
        eligible_count = 0
        shortfall = TOP_N
        if trend_active:
            universe = monthly_universes.get(signal_date, ())
            momentum_by_symbol: dict[str, Decimal] = {}
            for symbol in universe:
                series = series_by_symbol.get(symbol)
                if series is None:
                    continue
                liq_window = _trailing_window_ending_before(series, as_of=signal_date, window=LIQUIDITY_LOOKBACK_SESSIONS)
                if liq_window is None:
                    continue
                liq_closes, liq_volumes = liq_window
                liq = evaluate_liquidity_eligibility(
                    symbol=symbol, as_of=signal_date, closes=liq_closes, volumes=liq_volumes,
                    expected_session_count=LIQUIDITY_LOOKBACK_SESSIONS,
                )
                if not liq.eligible:
                    continue
                mom_window = _closes_ending_at(series, as_of=signal_date, window=MOMENTUM_LOOKBACK_SESSIONS + 1)
                if mom_window is None:
                    continue
                momentum = compute_12_1_momentum(mom_window)
                if momentum is None:
                    continue
                momentum_by_symbol[symbol] = momentum
            ranking = rank_and_select_top_n(momentum_by_symbol, signal_date=signal_date, top_n=TOP_N)
            selected = ranking.selected
            eligible_count = ranking.eligible_count
            shortfall = ranking.shortfall

        accession = filing_accession_for_signal.get(signal_date) if filing_accession_for_signal else None
        monthly_signals.append(MonthlySignal(
            signal_date=signal_date, execution_date=execution_date, trend_active=trend_active,
            filing_accession=accession, eligible_count=eligible_count, selected=selected, shortfall=shortfall,
        ))

        if execution_date is None or execution_date > evaluation_end or execution_date < evaluation_start:
            continue

        current_symbols = sorted(positions.keys())
        removals = sorted(s for s in current_symbols if s not in selected)
        continuing = sorted(s for s in current_symbols if s in selected)
        additions = sorted(s for s in selected if s not in positions)

        # Step 1: sell removals in full.
        for symbol in removals:
            price = _price_on(symbol, execution_date)
            pos = positions[symbol]
            if price is None:
                blocked.append(BlockedIntent(date=execution_date, symbol=symbol, side="SELL", desired_quantity=pos.quantity, filled_quantity=0, reason="no execution price available"))
                continue
            notional = price * pos.quantity
            cost = notional * cost_bps
            realized = (price - pos.avg_cost_basis) * pos.quantity - cost
            cash += notional - cost
            transactions.append(MomentumTransaction(date=execution_date, symbol=symbol, side="SELL", quantity=pos.quantity, price=price, notional=notional, cost=cost, reason="removal", realized_pnl=realized))
            del positions[symbol]

        target_count = len(selected) if selected else 0
        if target_count > 0:
            portfolio_value = cash + sum((_price_on(s, execution_date) or Decimal(0)) * positions[s].quantity for s in continuing)
            target_dollars_per_position = portfolio_value / target_count

            # Step 2: trim overweight continuing holdings.
            for symbol in continuing:
                price = _price_on(symbol, execution_date)
                pos = positions[symbol]
                if price is None or price <= 0:
                    continue
                target_qty = int(target_dollars_per_position // price)
                if pos.quantity > target_qty:
                    sell_qty = pos.quantity - target_qty
                    notional = price * sell_qty
                    cost = notional * cost_bps
                    realized = (price - pos.avg_cost_basis) * sell_qty - cost
                    cash += notional - cost
                    transactions.append(MomentumTransaction(date=execution_date, symbol=symbol, side="SELL", quantity=sell_qty, price=price, notional=notional, cost=cost, reason="trim", realized_pnl=realized))
                    remaining_qty = pos.quantity - sell_qty
                    if remaining_qty > 0:
                        positions[symbol] = MomentumPosition(symbol=symbol, entry_date=pos.entry_date, quantity=remaining_qty, avg_cost_basis=pos.avg_cost_basis)
                    else:
                        del positions[symbol]

            # Step 3: top up underweight continuing holdings.
            for symbol in continuing:
                if symbol not in positions:
                    continue
                price = _price_on(symbol, execution_date)
                pos = positions[symbol]
                if price is None or price <= 0:
                    continue
                target_qty = int(target_dollars_per_position // price)
                if target_qty > pos.quantity:
                    desired_buy = target_qty - pos.quantity
                    affordable = int(cash // (price * (1 + cost_bps))) if price > 0 else 0
                    buy_qty = min(desired_buy, affordable)
                    if buy_qty > 0:
                        notional = price * buy_qty
                        cost = notional * cost_bps
                        cash -= notional + cost
                        transactions.append(MomentumTransaction(date=execution_date, symbol=symbol, side="BUY", quantity=buy_qty, price=price, notional=notional, cost=cost, reason="top_up"))
                        new_qty = pos.quantity + buy_qty
                        new_avg_cost = (pos.avg_cost_basis * pos.quantity + notional + cost) / new_qty
                        positions[symbol] = MomentumPosition(symbol=symbol, entry_date=pos.entry_date, quantity=new_qty, avg_cost_basis=new_avg_cost)
                    if buy_qty < desired_buy:
                        blocked.append(BlockedIntent(date=execution_date, symbol=symbol, side="BUY", desired_quantity=desired_buy, filled_quantity=buy_qty, reason="insufficient cash for full top-up"))

            # Step 4: buy new additions.
            for symbol in additions:
                price = _price_on(symbol, execution_date)
                if price is None or price <= 0:
                    blocked.append(BlockedIntent(date=execution_date, symbol=symbol, side="BUY", desired_quantity=0, filled_quantity=0, reason="no execution price available"))
                    continue
                target_qty = int(target_dollars_per_position // price)
                affordable = int(cash // (price * (1 + cost_bps)))
                buy_qty = min(target_qty, affordable)
                if buy_qty > 0:
                    notional = price * buy_qty
                    cost = notional * cost_bps
                    cash -= notional + cost
                    transactions.append(MomentumTransaction(date=execution_date, symbol=symbol, side="BUY", quantity=buy_qty, price=price, notional=notional, cost=cost, reason="addition"))
                    positions[symbol] = MomentumPosition(symbol=symbol, entry_date=execution_date, quantity=buy_qty, avg_cost_basis=(notional + cost) / buy_qty)
                if buy_qty < target_qty:
                    blocked.append(BlockedIntent(date=execution_date, symbol=symbol, side="BUY", desired_quantity=target_qty - buy_qty, filled_quantity=buy_qty, reason="insufficient cash for full addition"))

        if cash < 0:
            raise CrossSectionalMomentumError(f"{execution_date}: cash went negative ({cash}) -- fail closed")

    daily_snapshots: list[PortfolioSnapshot] = []
    eval_dates = [d for d in master_dates if evaluation_start <= d <= evaluation_end]
    running_cash = starting_capital
    txn_by_date: dict[date, list[MomentumTransaction]] = {}
    for t in transactions:
        txn_by_date.setdefault(t.date, []).append(t)
    running_positions: dict[str, int] = {}
    for d in eval_dates:
        for t in txn_by_date.get(d, ()):
            if t.side == "BUY":
                running_cash -= t.notional + t.cost
                running_positions[t.symbol] = running_positions.get(t.symbol, 0) + t.quantity
            else:
                running_cash += t.notional - t.cost
                running_positions[t.symbol] = running_positions.get(t.symbol, 0) - t.quantity
                if running_positions[t.symbol] <= 0:
                    del running_positions[t.symbol]
        equity = running_cash + sum((_close_on(s, d) or Decimal(0)) * qty for s, qty in running_positions.items())
        daily_snapshots.append(PortfolioSnapshot(date=d, cash=running_cash, positions=dict(running_positions), equity=equity))

    return PortfolioSimulationResult(
        cost_label=cost_label, transactions=tuple(transactions), blocked_intents=tuple(blocked),
        daily_snapshots=tuple(daily_snapshots), monthly_signals=tuple(monthly_signals),
        open_positions_at_end=dict(positions), final_cash=cash,
    )


# ---------------------------------------------------------------------------
# Metrics and frozen classification.
# ---------------------------------------------------------------------------


def segment_label(day: date) -> str | None:
    if day.year in (2021, 2022, 2023, 2024, 2025):
        return str(day.year)
    if day.year == 2026:
        return "2026_YTD"
    return None


def _spy_buy_and_hold_equity(spy_series: DailySeries, *, eval_dates: Sequence[date], starting_capital: Decimal) -> list[tuple[date, Decimal]]:
    date_index = {d: i for i, d in enumerate(spy_series.dates)}
    if not eval_dates or eval_dates[0] not in date_index:
        return []
    entry_close = spy_series.closes[date_index[eval_dates[0]]]
    if entry_close <= 0:
        return []
    shares = int(starting_capital // entry_close)
    leftover_cash = starting_capital - shares * entry_close
    out: list[tuple[date, Decimal]] = []
    for d in eval_dates:
        idx = date_index.get(d)
        if idx is None:
            continue
        out.append((d, leftover_cash + shares * spy_series.closes[idx]))
    return out


def compute_portfolio_metrics(
    result: PortfolioSimulationResult,
    *, starting_capital: Decimal = STARTING_CAPITAL, evaluation_end: date = EVALUATION_END,
    spy_series: DailySeries | None = None,
) -> dict[str, Any]:
    daily_equity = [(s.date, s.equity) for s in result.daily_snapshots]
    monthly_pct = monthly_return_pct(daily_equity)
    fixed_dollar = fixed_notional_monthly_dollar_pnl(monthly_pct, notional=starting_capital)
    dd = max_drawdown(daily_equity)
    pct_values = [p for _, p in monthly_pct]
    dollar_values = [d for _, d in fixed_dollar]

    def _median(values: list[Decimal]) -> Decimal:
        if not values:
            return Decimal(0)
        s = sorted(values)
        n = len(s)
        return s[n // 2] if n % 2 == 1 else (s[n // 2 - 1] + s[n // 2]) / 2

    mean_monthly_dollars = (sum(dollar_values) / len(dollar_values)) if dollar_values else Decimal(0)
    median_monthly_dollars = _median(dollar_values)
    profitable_months = sum(1 for v in dollar_values if v > 0)
    profitable_month_pct = Decimal(profitable_months) / Decimal(len(dollar_values)) if dollar_values else Decimal(0)
    worst_month = min(dollar_values) if dollar_values else Decimal(0)
    worst_month_pct = min(pct_values) if pct_values else Decimal(0)
    sorted_pct = sorted(pct_values)
    p5_index = max(0, int(len(sorted_pct) * Decimal("0.05")) - 1) if sorted_pct else 0
    fifth_percentile_month_pct = sorted_pct[p5_index] if sorted_pct else Decimal(0)

    longest_losing_streak = 0
    current_streak = 0
    for v in dollar_values:
        if v < 0:
            current_streak += 1
            longest_losing_streak = max(longest_losing_streak, current_streak)
        else:
            current_streak = 0

    total_costs = sum(t.cost for t in result.transactions)
    total_buy_notional = sum(t.notional for t in result.transactions if t.side == "BUY")
    total_sell_notional = sum(t.notional for t in result.transactions if t.side == "SELL")
    realized_net_pnl = sum((t.realized_pnl or Decimal(0)) for t in result.transactions if t.side == "SELL")
    final_equity = daily_equity[-1][1] if daily_equity else starting_capital
    equity_net_pnl = final_equity - starting_capital
    gross_pnl = equity_net_pnl + total_costs
    endpoint_marked_net_pnl = equity_net_pnl  # daily_snapshots already marks every date, including evaluation_end, via the exact close lookup (_close_on) -- no separate positional endpoint exists to diverge from this.

    segments: dict[str, dict[str, Any]] = {}
    for seg in CALENDAR_SEGMENTS:
        seg_dollars = [d for day, d in fixed_dollar if segment_label(day) == seg]
        segments[seg] = {
            "net_pnl": sum(seg_dollars) if seg_dollars else Decimal(0),
            "month_count": len(seg_dollars),
            "positive": (sum(seg_dollars) if seg_dollars else Decimal(0)) > 0,
        }

    invested_position_counts = [len(s.positions) for s in result.daily_snapshots]
    avg_invested_positions = (sum(invested_position_counts) / len(invested_position_counts)) if invested_position_counts else 0
    max_invested_positions = max(invested_position_counts) if invested_position_counts else 0
    avg_cash_utilization_pct = (
        Decimal(1) - (sum(s.cash for s in result.daily_snapshots) / len(result.daily_snapshots)) / starting_capital
    ) if result.daily_snapshots else Decimal(0)

    symbol_realized: dict[str, Decimal] = {}
    for t in result.transactions:
        if t.side == "SELL" and t.realized_pnl is not None:
            symbol_realized[t.symbol] = symbol_realized.get(t.symbol, Decimal(0)) + t.realized_pnl
    total_positive_realized = sum(v for v in symbol_realized.values() if v > 0)
    best_symbol_positive_share = Decimal(0)
    if total_positive_realized > 0:
        best_symbol_positive_share = max((v for v in symbol_realized.values() if v > 0), default=Decimal(0)) / total_positive_realized

    signal_count = len(result.monthly_signals)
    intended_order_count = sum(len(s.selected) for s in result.monthly_signals)
    fill_count = len(result.transactions)
    blocked_count = len(result.blocked_intents)

    spy_comparison: dict[str, Any] | None = None
    if spy_series is not None:
        eval_dates_list = [d for d, _ in daily_equity]
        spy_equity = _spy_buy_and_hold_equity(spy_series, eval_dates=eval_dates_list, starting_capital=starting_capital)
        if spy_equity:
            spy_dd = max_drawdown(spy_equity)
            spy_comparison = {
                "final_equity": spy_equity[-1][1],
                "net_pnl": spy_equity[-1][1] - starting_capital,
                "max_drawdown_pct": spy_dd["max_drawdown_pct"],
            }

    return {
        "cost_label": result.cost_label,
        "mean_monthly_dollars": mean_monthly_dollars,
        "median_monthly_dollars": median_monthly_dollars,
        "monthly_pct_series": monthly_pct,
        "monthly_dollar_series": fixed_dollar,
        "gross_pnl": gross_pnl,
        "realized_net_pnl": realized_net_pnl,
        "equity_net_pnl": equity_net_pnl,
        "endpoint_marked_net_pnl": endpoint_marked_net_pnl,
        "cagr": cagr(daily_equity),
        "sharpe_ratio": sharpe_ratio(pct_values),
        "sortino_ratio": sortino_ratio(pct_values),
        "annualized_downside_deviation_pct": annualized_downside_deviation_pct(pct_values),
        "max_drawdown_dollars": dd["max_drawdown_dollars"],
        "max_drawdown_pct": dd["max_drawdown_pct"],
        "recovery_days": dd["recovery_days"],
        "worst_month_dollars": worst_month,
        "worst_month_pct": worst_month_pct,
        "fifth_percentile_month_pct": fifth_percentile_month_pct,
        "profitable_month_pct": profitable_month_pct,
        "longest_losing_streak_months": longest_losing_streak,
        "total_costs": total_costs,
        "total_buy_notional": total_buy_notional,
        "total_sell_notional": total_sell_notional,
        "turnover": total_buy_notional + total_sell_notional,
        "avg_invested_positions": avg_invested_positions,
        "max_invested_positions": max_invested_positions,
        "avg_cash_utilization_pct": avg_cash_utilization_pct,
        "symbol_realized_pnl": symbol_realized,
        "best_symbol_positive_contribution_share": best_symbol_positive_share,
        "segments": segments,
        "signal_count": signal_count,
        "intended_order_count": intended_order_count,
        "fill_count": fill_count,
        "blocked_intent_count": blocked_count,
        "spy_buy_and_hold_comparison": spy_comparison,
        "final_equity": final_equity,
        "final_cash": result.final_cash,
    }


def classify_cross_sectional_momentum(*, base_metrics: dict[str, Any], stress_metrics: dict[str, Any], integrity_failure: bool = False) -> dict[str, Any]:
    """The frozen classification rule (Issue #356), applied once,
    without tuning. `base_metrics`/`stress_metrics` are the return
    values of `compute_portfolio_metrics` for the base (5bps) and
    stress (10bps) cost cases respectively."""
    if integrity_failure:
        return {"verdict": CLASSIFICATION_INSUFFICIENT_EVIDENCE, "reasons": ["integrity failure reported by caller"]}

    base_segments_positive = sum(1 for seg in base_metrics["segments"].values() if seg["positive"])

    target_checks = {
        "base_mean_monthly_dollars_ge_500": base_metrics["mean_monthly_dollars"] >= TARGET_MIN_BASE_MEAN_MONTHLY_DOLLARS,
        "stress_mean_monthly_dollars_gt_0": stress_metrics["mean_monthly_dollars"] > 0,
        "pooled_base_endpoint_marked_net_pnl_gt_0": base_metrics["endpoint_marked_net_pnl"] > 0,
        "pooled_stress_endpoint_marked_net_pnl_gt_0": stress_metrics["endpoint_marked_net_pnl"] > 0,
        "base_max_drawdown_le_25pct": base_metrics["max_drawdown_pct"] <= TARGET_MAX_BASE_DRAWDOWN_PCT,
        "at_least_5_of_6_segments_positive": base_segments_positive >= TARGET_MIN_POSITIVE_SEGMENTS,
        "profitable_months_ge_55pct": base_metrics["profitable_month_pct"] >= TARGET_MIN_PROFITABLE_MONTH_PCT,
        "worst_month_ge_neg_10pct": base_metrics["worst_month_pct"] >= TARGET_WORST_MONTH_FLOOR_PCT,
        "best_symbol_share_le_35pct": base_metrics["best_symbol_positive_contribution_share"] <= TARGET_MAX_SYMBOL_CONTRIBUTION_SHARE,
    }
    if all(target_checks.values()):
        return {"verdict": CLASSIFICATION_TARGET_FEASIBLE, "gate_checks": target_checks}

    # NOT_PROMISING is an explicit OR of four independent triggers and is
    # checked BEFORE POSITIVE_BUT_INSUFFICIENT, not as a residual "else"
    # branch: its own "fewer than 3 segments positive" trigger is a
    # strictly wider condition than POSITIVE_BUT_INSUFFICIENT's "at least
    # 1 segment positive" requirement (1 or 2 positive segments satisfies
    # the latter while still tripping the former), so checking
    # POSITIVE_BUT_INSUFFICIENT first could silently skip a NOT_PROMISING
    # verdict the frozen rule actually requires.
    not_promising_triggers = {
        "base_endpoint_marked_net_pnl_le_0": base_metrics["endpoint_marked_net_pnl"] <= 0,
        "stress_endpoint_marked_net_pnl_le_0": stress_metrics["endpoint_marked_net_pnl"] <= 0,
        "base_max_drawdown_gt_35pct": base_metrics["max_drawdown_pct"] > NOT_PROMISING_MAX_DRAWDOWN_PCT,
        "fewer_than_3_segments_positive": base_segments_positive < NOT_PROMISING_MIN_POSITIVE_SEGMENTS,
    }
    if any(not_promising_triggers.values()):
        return {"verdict": CLASSIFICATION_NOT_PROMISING, "gate_checks": {**target_checks, "not_promising_triggers": not_promising_triggers}}

    # NOT_PROMISING being false here guarantees (by construction: base/
    # stress P&L > 0, drawdown <= 35%, and >= 3 positive segments implies
    # >= 1 positive segment) that POSITIVE_BUT_INSUFFICIENT's own gates
    # all pass -- computed and asserted, not merely assumed.
    positive_insufficient_checks = {
        "pooled_base_endpoint_marked_net_pnl_gt_0": base_metrics["endpoint_marked_net_pnl"] > 0,
        "pooled_stress_endpoint_marked_net_pnl_gt_0": stress_metrics["endpoint_marked_net_pnl"] > 0,
        "base_max_drawdown_le_35pct": base_metrics["max_drawdown_pct"] <= POSITIVE_INSUFFICIENT_MAX_DRAWDOWN_PCT,
        "at_least_1_segment_positive": base_segments_positive >= 1,
    }
    assert all(positive_insufficient_checks.values()), "unreachable: NOT_PROMISING triggers were all false"
    return {"verdict": CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT, "gate_checks": {**target_checks, **positive_insufficient_checks}}
