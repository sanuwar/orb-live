"""CLI entry point for M48: $30K capital-target ETF strategy-family
feasibility (Issue #348).

Fetches split/dividend-adjusted DAILY OHLCV from Alpaca (`timeframe=1Day`,
`adjustment=all`), caches raw responses locally under a new, gitignored
M48-only directory (never the shared minute-bar cache), records a full
provenance manifest, runs the three frozen arms via
`src.orb_capital_target_strategy_family_research`, and writes the
required per-segment/pooled metrics and classification to
`artifacts/research-capital-target-strategy-family/results/`.

Market-data calls only -- no brokerage-account, position, order, or
Robinhood call anywhere in this module.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import MarketBar
from src.market_data_provider import ALPACA_DEFAULT_BASE_URL, AlpacaCredentials, MarketDataProviderError, normalize_alpaca_bar
from src.orb_universe_expansion_research import CachingHttpClient
from src.orb_capital_target_strategy_family_research import (
    ARM_A, ARM_B, ARM_C, ARMS,
    BASE_COST_BPS, STRESS_COST_BPS, STARTING_CAPITAL, SECONDARY_RISK_PER_POSITION, MAX_RISK_PER_POSITION,
    EVALUATION_START, EVALUATION_END, CALENDAR_SEGMENTS, REQUIRED_POSITIVE_SEGMENTS,
    UNIVERSE_A, UNIVERSE_B, UNIVERSE_C, UNIVERSE_BY_ARM,
    DailySeries, daily_series_from_bars, require_full_alignment, session_alignment_report, ohlc_integrity_report,
    run_arm_a, run_monthly_rebalance_arm, arm_b_selector, arm_c_selector,
    simple_moving_average, compute_arm_metrics, scale_trade_to_risk_cap, segment_label,
    monthly_return_pct, pearson_correlation, align_monthly_series, classify_capital_target_arm,
    max_drawdown, evaluation_end_mark_transactions, fill_missing_months_with_zero,
    CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT, compute_combination_plausibility_upper_bound,
    Transaction, PositionSnapshot,
)

WARMUP_START = date(2021, 1, 1)
#: The `end` boundary passed to Alpaca's bars request. In practice the
#: fetched daily series DOES include a bar dated 2026-08-14 (one session
#: past EVALUATION_END) -- Alpaca's daily-bar timestamps evidently fall
#: before this boundary's midnight-ET cutoff for that session, so this
#: name is descriptive of the REQUEST parameter only, not a guarantee
#: that the received data stops at EVALUATION_END. Every place that
#: needs a specific evaluation-window close MUST resolve it by exact
#: date (`close_on_exact_date`), never a positional index, precisely
#: because of this one-session overshoot.
FETCH_END_EXCLUSIVE = date(2026, 8, 14)
CACHE_ROOT = ROOT / "artifacts" / "research-capital-target-strategy-family" / "daily-bar-cache"
RESULTS_DIR = ROOT / "artifacts" / "research-capital-target-strategy-family" / "results"
PROVENANCE_PATH = RESULTS_DIR / "provenance_manifest.json"
#: SIP (the consolidated tape across all US exchanges) is the frozen M48
#: empirical feed -- IEX is a single individual exchange whose OHLC can
#: differ materially from the consolidated market, which matters
#: directly here since ARM_A's entries/stops/exits are all driven by
#: daily open/high/low/close. M46 hit the same IEX-completeness problem
#: and switched to SIP; M48 starts there. Feed-specific cache
#: subdirectories (`daily-bar-cache/sip/`, `daily-bar-cache/iex/`) keep
#: the two feeds' raw responses permanently separate -- an IEX cache
#: entry is NEVER read as if it were SIP data, and the original IEX
#: cache from the first (pre-review) run is left untouched as historical
#: evidence, not deleted.
DEFAULT_FEED = "sip"
MAX_PAGES_PER_SYMBOL = 20


def cache_dir_for_feed(feed: str) -> Path:
    return CACHE_ROOT / feed

ALL_SYMBOLS: tuple[str, ...] = tuple(sorted(set(UNIVERSE_A) | set(UNIVERSE_B) | set(UNIVERSE_C)))


class CapitalTargetCLIError(RuntimeError):
    pass


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        print("ALPACA_MARKET_DATA_CREDENTIALS_MISSING", file=sys.stderr)
        raise SystemExit(2)
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


async def fetch_daily_bars(
    http_client: Any, *, credentials: AlpacaCredentials, symbol: str, start: date, end_exclusive: date,
    now: Any, feed: str = DEFAULT_FEED,
) -> tuple[list[MarketBar], list[dict[str, Any]]]:
    """Fetches every daily bar in `[start, end_exclusive)` for `symbol`,
    split/dividend-adjusted (`adjustment=all`), following
    `next_page_token` to completion. Returns `(bars, raw_pages)` -- the
    raw pages are kept for the provenance hash, separate from the
    normalized `MarketBar` objects."""
    start_dt = datetime.combine(start, datetime.min.time(), tzinfo=EASTERN).astimezone(timezone.utc)
    end_dt = datetime.combine(end_exclusive, datetime.min.time(), tzinfo=EASTERN).astimezone(timezone.utc)
    all_bars: list[MarketBar] = []
    raw_pages: list[dict[str, Any]] = []
    page_token: str | None = None
    receive_timestamp = now()
    for _page in range(1, MAX_PAGES_PER_SYMBOL + 1):
        params: dict[str, Any] = {
            "start": start_dt.isoformat().replace("+00:00", "Z"),
            "end": end_dt.isoformat().replace("+00:00", "Z"),
            "timeframe": "1Day",
            "feed": feed,
            "limit": 10000,
            "adjustment": "all",
        }
        if page_token is not None:
            params["page_token"] = page_token
        response = await http_client.get(
            f"{ALPACA_DEFAULT_BASE_URL}/stocks/{symbol}/bars", headers=credentials.headers(), params=params,
        )
        if response.status_code != 200:
            raise MarketDataProviderError(f"{symbol}: provider returned status {response.status_code}")
        payload = response.json()
        if not isinstance(payload, Mapping) or "bars" not in payload:
            raise MarketDataProviderError(f"{symbol}: provider response is missing 'bars'")
        raw_bars = payload["bars"] or []
        raw_pages.append({"params": params, "bar_count": len(raw_bars)})
        for raw in raw_bars:
            all_bars.append(normalize_alpaca_bar(raw, symbol=symbol, receive_timestamp=receive_timestamp, interval_seconds=86400))
        page_token = payload.get("next_page_token")
        if not page_token:
            return all_bars, raw_pages
    raise CapitalTargetCLIError(f"{symbol}: exceeded {MAX_PAGES_PER_SYMBOL}-page pagination guard")


async def fetch_all_symbols(*, cache_dir: Path, fetch_missing: bool, feed: str) -> dict[str, DailySeries]:
    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")

        class _RaiseOnCacheMiss:
            async def get(self, url: str, *, headers: Any, params: Any) -> Any:  # noqa: ANN001
                raise SystemExit(
                    f"CACHE_MISS_IN_CACHE_ONLY_MODE: no cached response (url={url}, params={params}). "
                    "Rerun with --fetch-missing to populate the cache."
                )

        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    series_by_symbol: dict[str, DailySeries] = {}
    for symbol in ALL_SYMBOLS:
        bars, _raw_pages = await fetch_daily_bars(
            transport, credentials=credentials, symbol=symbol, start=WARMUP_START, end_exclusive=FETCH_END_EXCLUSIVE, now=now, feed=feed,
        )
        series_by_symbol[symbol] = daily_series_from_bars(bars)
        print(f"[{symbol}] {len(bars)} daily bars ({series_by_symbol[symbol].dates[0]}..{series_by_symbol[symbol].dates[-1]})")
    return series_by_symbol


def _sha256_of_series(series: DailySeries) -> str:
    material = json.dumps(
        {
            "symbol": series.symbol,
            "dates": [d.isoformat() for d in series.dates],
            "opens": [str(v) for v in series.opens],
            "highs": [str(v) for v in series.highs],
            "lows": [str(v) for v in series.lows],
            "closes": [str(v) for v in series.closes],
            "volumes": list(series.volumes),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def build_provenance_manifest(series_by_symbol: Mapping[str, DailySeries], *, master_dates: Sequence[date], feed: str) -> dict[str, Any]:
    alignment = session_alignment_report(series_by_symbol, master_dates=master_dates)
    integrity = ohlc_integrity_report(series_by_symbol)
    per_symbol: dict[str, Any] = {}
    for symbol, series in series_by_symbol.items():
        per_symbol[symbol] = {
            "provider": "alpaca", "feed": feed, "timeframe": "1Day", "adjustment": "all",
            "requested_start": WARMUP_START.isoformat(), "requested_end_exclusive": FETCH_END_EXCLUSIVE.isoformat(),
            "received_start": series.dates[0].isoformat(), "received_end": series.dates[-1].isoformat(),
            "session_count": len(series.dates),
            "sha256": _sha256_of_series(series),
            "missing_master_dates": alignment[symbol]["missing_master_dates"],
            "extra_dates_outside_master": alignment[symbol]["extra_dates_outside_master"],
            "ohlc_integrity_violations": integrity[symbol]["violations"],
        }
    return {
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "universe_a": list(UNIVERSE_A), "universe_b": list(UNIVERSE_B), "universe_c": list(UNIVERSE_C),
        "warmup_start": WARMUP_START.isoformat(), "evaluation_start": EVALUATION_START.isoformat(),
        "evaluation_end": EVALUATION_END.isoformat(), "fetch_end_exclusive": FETCH_END_EXCLUSIVE.isoformat(),
        "master_calendar_symbol": "SPY", "master_calendar_session_count": len(master_dates),
        "per_symbol": per_symbol,
    }


def build_master_calendar(spy_series: DailySeries) -> tuple[date, ...]:
    return tuple(d for d in spy_series.dates if WARMUP_START <= d <= EVALUATION_END)


def close_on_exact_date(series: DailySeries, day: date) -> Decimal:
    """The exact close for `day`, by date lookup -- never a positional
    index. The cached daily-bar series may (and, for the 2026-08-14
    request boundary, does) extend past `day`; a positional `[-1]` would
    silently return a LATER session's close, a one-session lookahead.
    Fails closed if `day` is missing or duplicated in the series."""
    matches = [i for i, d in enumerate(series.dates) if d == day]
    if not matches:
        raise CapitalTargetCLIError(f"{series.symbol}: no bar for exact date {day.isoformat()} (fail-closed, no lookahead substitute)")
    if len(matches) > 1:
        raise CapitalTargetCLIError(f"{series.symbol}: duplicate bars for exact date {day.isoformat()}")
    return series.closes[matches[0]]


def end_price_by_symbol_for(series_by_symbol: Mapping[str, DailySeries], symbols: Sequence[str], *, end_date: date) -> dict[str, Decimal]:
    return {s: close_on_exact_date(series_by_symbol[s], end_date) for s in symbols}


def segment_carry_unrealized_by_symbol(
    *, segment_end_positions: Sequence[Any], transactions: Sequence[Transaction],
    daily_position_snapshots: Sequence[PositionSnapshot], evaluation_start: date, evaluation_end: date,
    series_by_symbol: Mapping[str, DailySeries],
) -> dict[str, Decimal]:
    """Finding 19 (independent review): for a SEGMENT window ONLY (never
    the pooled/true-final window, which already uses
    `evaluation_end_mark_transactions`/`open_positions_at_end`), computes
    each still-open-at-segment-end position's own unrealized net
    contribution for THIS segment specifically -- so segment-level
    concentration/contribution accounting stays consistent with the
    segment's own equity-based net P&L (`compute_arm_metrics`'s
    `net_pnl`/`net_pnl_marked`), which already includes this movement.
    Uses ONLY the existing position/transaction ledgers -- never reruns
    or re-derives anything about the simulation itself.

    Correctly (and unambiguously) resolved for exactly two cases:
      (a) a position open BEFORE this segment and untouched (no BUY/SELL
          transaction for that symbol) DURING it -- pure mark-to-market
          since the last snapshot before the segment began.
      (b) a position that entered FRESH during this segment (its only
          transaction here is a single ENTRY/REBALANCE_BUY) and was
          never topped up or trimmed afterward -- mark-to-market since
          that one entry.
    A position topped up or trimmed while STILL remaining open at the
    segment's own end is intentionally left OUT here: the shared,
    running weighted-average cost basis both simulation engines
    maintain cannot be unambiguously split into "gained before this
    segment" vs. "gained during this segment" without per-lot cost
    history the current ledger does not track. Any such gap is never
    silently hidden -- it remains fully visible via
    `compute_arm_metrics`'s own `reconciliation.residual` for that
    segment."""
    window_transactions_by_symbol: dict[str, list[Transaction]] = {}
    for t in transactions:
        if evaluation_start <= t.date <= evaluation_end:
            window_transactions_by_symbol.setdefault(t.symbol, []).append(t)

    snapshot_value_by_symbol_and_date: dict[tuple[str, date], Decimal] = {
        (s.symbol, s.date): s.market_value for s in daily_position_snapshots
    }
    pre_window_dates_by_symbol: dict[str, list[date]] = {}
    for s in daily_position_snapshots:
        if s.date < evaluation_start:
            pre_window_dates_by_symbol.setdefault(s.symbol, []).append(s.date)

    out: dict[str, Decimal] = {}
    for pos in segment_end_positions:
        symbol = pos.symbol
        end_price = close_on_exact_date(series_by_symbol[symbol], evaluation_end)
        market_value_end = end_price * pos.quantity
        txns = window_transactions_by_symbol.get(symbol, [])
        if not txns:
            pre_dates = pre_window_dates_by_symbol.get(symbol)
            if not pre_dates:
                continue  # no pre-window snapshot -- ambiguous; left to the residual
            reference_value = snapshot_value_by_symbol_and_date[(symbol, max(pre_dates))]
            out[symbol] = market_value_end - reference_value
        elif len(txns) == 1 and txns[0].side == "BUY" and txns[0].reason in ("ENTRY", "REBALANCE_BUY"):
            entry_txn = txns[0]
            out[symbol] = market_value_end - entry_txn.notional - entry_txn.cost
        # else: topped up/trimmed while remaining open this segment --
        # deliberately left out; the residual absorbs it, never hidden.
    return out


def _sha256_of_json(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, default=_json_default)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _transaction_rows(transactions: Sequence[Any]) -> list[dict[str, Any]]:
    return [
        {
            "date": t.date.isoformat(), "arm": t.arm, "symbol": t.symbol, "side": t.side,
            "quantity": t.quantity, "price": str(t.price), "notional": str(t.notional),
            "cost": str(t.cost), "reason": t.reason,
        }
        for t in transactions
    ]


def _strategy_transaction_ledger_hash(transactions: Sequence[Any]) -> str:
    """Real, strategy-generated transactions only -- never includes an
    `EVALUATION_END_MARK` row (hashed separately, see
    `_endpoint_mark_ledger_hash`), so this hash alone reconstructs
    exactly what the simulation actually traded."""
    return _sha256_of_json(_transaction_rows(transactions))


def _endpoint_mark_ledger_hash(end_mark_transactions: Sequence[Any]) -> str:
    return _sha256_of_json(_transaction_rows(end_mark_transactions))


def _combined_accounting_ledger_hash(transactions: Sequence[Any], end_mark_transactions: Sequence[Any]) -> str:
    """Every row that ever entered net-P&L/concentration/turnover
    accounting -- real transactions plus the hypothetical endpoint mark,
    in that order -- so this single hash reconstructs the exact
    accounting basis the reported metrics were computed from."""
    return _sha256_of_json(_transaction_rows(transactions) + _transaction_rows(end_mark_transactions))


def _position_equity_ledger_hash(
    daily_equity: Sequence[tuple[date, Decimal]], daily_position_count: Sequence[tuple[date, int]],
    daily_position_snapshots: Sequence[Any],
) -> str:
    """The COMPLETE daily position/equity ledger: not merely aggregate
    equity and position counts, but one row per open position per
    session -- date, symbol, quantity, mark price, market value, and
    (ARM_A only) planned risk -- so two portfolios with identical equity
    and position counts but different underlying symbols/quantities
    hash differently."""
    rows = {
        "daily_equity": [[d.isoformat(), str(e)] for d, e in daily_equity],
        "daily_position_count": [[d.isoformat(), c] for d, c in daily_position_count],
        "daily_position_snapshots": [
            {
                "date": s.date.isoformat(), "symbol": s.symbol, "quantity": s.quantity,
                "mark_price": str(s.mark_price), "market_value": str(s.market_value),
                "planned_risk": (str(s.planned_risk) if s.planned_risk is not None else None),
            }
            for s in daily_position_snapshots
        ],
    }
    return _sha256_of_json(rows)


def _master_calendar_hash(master_dates: Sequence[date]) -> str:
    return _sha256_of_json([d.isoformat() for d in master_dates])


# ---------------------------------------------------------------------------
# Empirical execution: run all three arms across both cost cases, load the
# ORB-baseline monthly R-series for correlation, and assemble the full
# required-output set. This function performs no network I/O of its own --
# `series_by_symbol` must already be fetched/aligned.
# ---------------------------------------------------------------------------

ORB_CHECKPOINT_DIR = ROOT / "artifacts" / "research-profit-protection-exit" / "period-checkpoint"
ORB_PERIODS = ("P_MINUS_2", "P_MINUS_1", "P0", "P1", "P2", "P3")


def load_orb_monthly_r_series() -> list[tuple[date, Decimal]]:
    """Pooled `WAITED_FIRST_VALID_0950`/`BE_AFTER_050R` closed-trade R
    multiples from the existing, already-merged M43 checkpoints
    (untouched, read-only), bucketed by calendar month and then
    zero-filled across the COMPLETE `[EVALUATION_START, EVALUATION_END]`
    month grid via `fill_missing_months_with_zero` -- the ORB lane is
    sparse (most months have zero closed trades), and leaving those
    months absent would silently drop them from any later
    `align_monthly_series` inner join against a densely-populated ETF
    arm series, distorting the resulting correlation. R is a fixed
    per-trade risk normalization, not a dollar figure; a constant
    scaling factor does not change Pearson correlation, so this series
    is correlated in R terms directly, not converted to dollars. Never
    re-runs or modifies the ORB baseline -- purely a read of its own
    already-validated results, for correlation purposes only."""
    import pickle

    monthly: dict[tuple[int, int], Decimal] = {}
    for period in ORB_PERIODS:
        path = ORB_CHECKPOINT_DIR / f"{period}.pkl"
        if not path.exists():
            continue
        with path.open("rb") as fh:
            result = pickle.load(fh)
        rows = result["rows"]["WAITED_FIRST_VALID_0950"]["BE_AFTER_050R"]
        for row in rows:
            if not row.outcome["closed"] or row.outcome["r_multiple"] is None:
                continue
            session_date = date.fromisoformat(row.decision["session_date"])
            key = (session_date.year, session_date.month)
            monthly[key] = monthly.get(key, Decimal(0)) + Decimal(str(row.outcome["r_multiple"]))
    sparse = [(date(y, m, 1), r) for (y, m), r in sorted(monthly.items())]
    return fill_missing_months_with_zero(sparse, start=EVALUATION_START, end=EVALUATION_END)


def run_full_study(series_by_symbol: Mapping[str, DailySeries], master_dates: Sequence[date]) -> dict[str, Any]:
    results: dict[str, Any] = {"arms": {}}

    universe_series = {arm: {s: series_by_symbol[s] for s in UNIVERSE_BY_ARM[arm]} for arm in ARMS}
    sma200_by_arm_symbol = {
        arm: {s: simple_moving_average(series_by_symbol[s].closes, 200) for s in UNIVERSE_BY_ARM[arm]}
        for arm in (ARM_B, ARM_C)
    }

    raw_sim: dict[str, dict[Decimal, Any]] = {}
    for arm in ARMS:
        raw_sim[arm] = {}
        for cost_bps in (BASE_COST_BPS, STRESS_COST_BPS):
            if arm == ARM_A:
                sim = run_arm_a(universe_series[ARM_A], master_dates, cost_bps=cost_bps, evaluation_start=EVALUATION_START)
            elif arm == ARM_B:
                selector = arm_b_selector(universe_series[ARM_B], sma200_by_arm_symbol[ARM_B])
                sim = run_monthly_rebalance_arm(universe_series[ARM_B], master_dates, arm=ARM_B, selector=selector, cost_bps=cost_bps, evaluation_start=EVALUATION_START)
            else:
                selector = arm_c_selector(universe_series[ARM_C], sma200_by_arm_symbol[ARM_C])
                sim = run_monthly_rebalance_arm(universe_series[ARM_C], master_dates, arm=ARM_C, selector=selector, cost_bps=cost_bps, evaluation_start=EVALUATION_START)
            raw_sim[arm][cost_bps] = sim

    orb_monthly = load_orb_monthly_r_series()
    spy_monthly_pct_pairs = monthly_return_pct([(d, series_by_symbol["SPY"].closes[i]) for i, d in enumerate(master_dates)])
    spy_monthly_pct_pairs = [(d, v) for d, v in spy_monthly_pct_pairs if EVALUATION_START <= d <= EVALUATION_END]

    per_arm_output: dict[str, Any] = {}
    ledger_hashes: dict[str, Any] = {}
    for arm in ARMS:
        base_sim = raw_sim[arm][BASE_COST_BPS]
        stress_sim = raw_sim[arm][STRESS_COST_BPS]

        # Hypothetical-liquidation marks at the TRUE fixed evaluation
        # endpoint only -- never for an interim segment boundary. Resolved
        # by EXACT date (never a positional [-1]): the cached series runs
        # through 2026-08-14, one session past EVALUATION_END, so a
        # positional lookup would silently use a later close.
        end_price_by_symbol = end_price_by_symbol_for(series_by_symbol, UNIVERSE_BY_ARM[arm], end_date=EVALUATION_END)
        base_end_marks = evaluation_end_mark_transactions(
            open_positions_at_end=base_sim.open_positions_at_end, end_date=EVALUATION_END,
            end_price_by_symbol=end_price_by_symbol, cost_bps=BASE_COST_BPS, arm=arm,
        )
        stress_end_marks = evaluation_end_mark_transactions(
            open_positions_at_end=stress_sim.open_positions_at_end, end_date=EVALUATION_END,
            end_price_by_symbol=end_price_by_symbol, cost_bps=STRESS_COST_BPS, arm=arm,
        )

        for cost_label, sim, end_marks in (("base_5bps", base_sim, base_end_marks), ("stress_10bps", stress_sim, stress_end_marks)):
            ledger_hashes[f"{arm}__{cost_label}"] = {
                "strategy_transaction_ledger_sha256": _strategy_transaction_ledger_hash(sim.transactions),
                "endpoint_mark_ledger_sha256": _endpoint_mark_ledger_hash(end_marks),
                "combined_accounting_ledger_sha256": _combined_accounting_ledger_hash(sim.transactions, end_marks),
                "position_equity_ledger_sha256": _position_equity_ledger_hash(sim.daily_equity, sim.daily_position_count, sim.daily_position_snapshots),
                "strategy_transaction_count": len(sim.transactions),
                "endpoint_mark_transaction_count": len(end_marks),
                "combined_transaction_count": len(sim.transactions) + len(end_marks),
            }

        base_metrics = compute_arm_metrics(
            arm=arm, closed_trades=base_sim.closed_trades, daily_equity=base_sim.daily_equity,
            starting_capital=base_sim.starting_capital, total_cost_paid=base_sim.total_cost_paid,
            evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
            open_positions_at_end=base_sim.open_positions_at_end, transactions=base_sim.transactions,
            end_mark_transactions=base_end_marks, daily_position_count=base_sim.daily_position_count,
            rebalance_events=getattr(base_sim, "rebalance_events", ()),
        )
        stress_metrics = compute_arm_metrics(
            arm=arm, closed_trades=stress_sim.closed_trades, daily_equity=stress_sim.daily_equity,
            starting_capital=stress_sim.starting_capital, total_cost_paid=stress_sim.total_cost_paid,
            evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
            open_positions_at_end=stress_sim.open_positions_at_end, transactions=stress_sim.transactions,
            end_mark_transactions=stress_end_marks, daily_position_count=stress_sim.daily_position_count,
            rebalance_events=getattr(stress_sim, "rebalance_events", ()),
        )

        # Finding 19 (independent review): required outputs are per arm,
        # cost case, calendar segment, AND pooled -- segment metrics were
        # previously computed only for the base-cost simulation. Both
        # base and stress segment metrics are built here, each from its
        # OWN simulation (base/stress cost can diverge in exactly which
        # trades execute, not merely their cost, so each needs its own
        # ledgers/snapshots/segment-end positions).
        base_segment_end_positions = dict(getattr(base_sim, "segment_end_open_positions", ()))
        stress_segment_end_positions = dict(getattr(stress_sim, "segment_end_open_positions", ()))

        def _build_segment_metrics(sim: Any, end_marks: Any, segment_end_positions: dict[str, Any]) -> dict[str, Any]:
            out: dict[str, Any] = {}
            for seg in CALENDAR_SEGMENTS:
                seg_dates = [d for d in master_dates if segment_label(d) == seg and EVALUATION_START <= d <= EVALUATION_END]
                if not seg_dates:
                    continue
                # Only the segment whose own end IS the true evaluation
                # end (2026_YTD) gets the hypothetical endpoint mark;
                # earlier segment boundaries are interim reporting cuts,
                # not real or hypothetical liquidation events -- those
                # instead get the Finding 19 carry-unrealized attribution
                # so their own concentration/contribution accounting
                # stays consistent with their own equity-based net P&L.
                is_true_end_segment = seg_dates[-1] == EVALUATION_END
                seg_end_marks = end_marks if is_true_end_segment else ()
                seg_carry_unrealized = (
                    {} if is_true_end_segment else
                    segment_carry_unrealized_by_symbol(
                        segment_end_positions=segment_end_positions.get(seg, ()), transactions=sim.transactions,
                        daily_position_snapshots=sim.daily_position_snapshots,
                        evaluation_start=seg_dates[0], evaluation_end=seg_dates[-1],
                        series_by_symbol=series_by_symbol,
                    )
                )
                out[seg] = compute_arm_metrics(
                    arm=arm, closed_trades=sim.closed_trades, daily_equity=sim.daily_equity,
                    starting_capital=sim.starting_capital, total_cost_paid=sim.total_cost_paid,
                    evaluation_start=seg_dates[0], evaluation_end=seg_dates[-1],
                    open_positions_at_end=sim.open_positions_at_end, transactions=sim.transactions,
                    end_mark_transactions=seg_end_marks, daily_position_count=sim.daily_position_count,
                    rebalance_events=getattr(sim, "rebalance_events", ()),
                    segment_carry_unrealized_net=seg_carry_unrealized,
                )
            return out

        segment_metrics = _build_segment_metrics(base_sim, base_end_marks, base_segment_end_positions)
        stress_segment_metrics = _build_segment_metrics(stress_sim, stress_end_marks, stress_segment_end_positions)

        arm_monthly_pct_pairs = base_metrics["monthly_return_pct_series"]
        aligned_orb = align_monthly_series(arm_monthly_pct_pairs, orb_monthly)
        correlation_to_orb = pearson_correlation([r[1] for r in aligned_orb], [r[2] for r in aligned_orb]) if len(aligned_orb) >= 2 else None
        aligned_spy = align_monthly_series(arm_monthly_pct_pairs, spy_monthly_pct_pairs)
        correlation_to_spy = pearson_correlation([r[1] for r in aligned_spy], [r[2] for r in aligned_spy]) if len(aligned_spy) >= 2 else None

        risk_views = {}
        if arm == ARM_A:
            for label, cap in (("300", MAX_RISK_PER_POSITION), ("150", SECONDARY_RISK_PER_POSITION)):
                scaled_trades = tuple(scale_trade_to_risk_cap(t, risk_cap=cap) for t in base_sim.closed_trades)
                risk_views[label] = {
                    "gross_pnl": sum((t.gross_pnl for t in scaled_trades if EVALUATION_START <= t.exit_date <= EVALUATION_END), Decimal(0)),
                    "net_pnl": sum((t.net_pnl for t in scaled_trades if EVALUATION_START <= t.exit_date <= EVALUATION_END), Decimal(0)),
                    "fixed_notional_30k_monthly_dollar_pnl_available": False,
                    "note": (
                        "Only realized gross/net P&L can be validly derived post-hoc "
                        "by linearly rescaling each closed trade's quantity to this "
                        "risk cap. A fixed-$30,000-notional monthly-dollar figure at "
                        "this risk cap is NOT reported here: position sizing, cash "
                        "constraints, and the concurrent-position limit are all "
                        "risk-cap-dependent, so a valid fixed-notional monthly series "
                        "at either $150 or $300 risk-per-position requires rerunning "
                        "the full "
                        "simulation at that risk cap, not a post-hoc rescale of the "
                        "$300-cap trade list. The primary $300-cap fixed-$30K monthly "
                        "figure is reported in base_cost/stress_cost above."
                    ),
                }

        best_share = base_metrics["concentration"]["best_contribution_share"]
        classification = classify_capital_target_arm(
            average_monthly_dollars_base=Decimal(str(base_metrics["mean_monthly_dollar_pnl_fixed_30k"] or 0)),
            segment_net_returns={seg: v["net_pnl_marked"] for seg, v in segment_metrics.items()},
            max_drawdown_pct=base_metrics["max_drawdown_pct"],
            pooled_net_base=base_metrics["net_pnl_marked"], pooled_net_stress=stress_metrics["net_pnl_marked"],
            mean_monthly_return_pct=base_metrics["mean_monthly_return_pct"],
            profitable_month_pct=base_metrics["profitable_month_pct"],
            correlation_to_orb=correlation_to_orb, best_single_symbol_positive_share=best_share,
            integrity_ok=True,
        )

        per_arm_output[arm] = {
            "base_cost": base_metrics, "stress_cost": stress_metrics,
            "segments": segment_metrics, "stress_segments": stress_segment_metrics,
            "risk_views": risk_views, "correlation_to_orb": correlation_to_orb, "correlation_to_spy": correlation_to_spy,
            "classification": classification,
        }

    # Cross-arm correlations.
    cross_arm_correlation: dict[str, Decimal | None] = {}
    for a in ARMS:
        for b in ARMS:
            if a >= b:
                continue
            series_a = per_arm_output[a]["base_cost"]["monthly_return_pct_series"]
            series_b = per_arm_output[b]["base_cost"]["monthly_return_pct_series"]
            aligned = align_monthly_series(series_a, series_b)
            corr = pearson_correlation([r[1] for r in aligned], [r[2] for r in aligned]) if len(aligned) >= 2 else None
            cross_arm_correlation[f"{a}__{b}"] = corr

    spy_buy_and_hold = compute_spy_buy_and_hold(series_by_symbol["SPY"], master_dates)

    # Finding 15 (independent review): the frozen decision tree's
    # required next step when every arm classifies
    # POSITIVE_BUT_INSUFFICIENT on its own -- a deliberately generous
    # mathematical combination-plausibility upper bound, computed here
    # directly rather than by running any M49 portfolio-combination
    # simulation.
    all_insufficient = all(
        per_arm_output[arm]["classification"]["verdict"] == CLASSIFICATION_POSITIVE_BUT_INSUFFICIENT
        for arm in ARMS
    )
    combination_plausibility = None
    if all_insufficient:
        arm_means = {
            arm: Decimal(str(per_arm_output[arm]["base_cost"]["mean_monthly_dollar_pnl_fixed_30k"] or 0))
            for arm in ARMS
        }
        combination_plausibility = compute_combination_plausibility_upper_bound(arm_means)

    results["arms"] = per_arm_output
    results["cross_arm_correlation"] = cross_arm_correlation
    results["spy_buy_and_hold_benchmark"] = spy_buy_and_hold
    results["orb_monthly_r_series"] = [(d.isoformat(), str(r)) for d, r in orb_monthly]
    results["ledger_hashes"] = ledger_hashes
    results["master_calendar_sha256"] = _master_calendar_hash(master_dates)
    results["combination_plausibility"] = combination_plausibility
    return results


def compute_spy_buy_and_hold(spy_series: DailySeries, master_dates: Sequence[date]) -> dict[str, Any]:
    eval_indices = [i for i, d in enumerate(master_dates) if EVALUATION_START <= d <= EVALUATION_END]
    if not eval_indices:
        return {}
    start_close = spy_series.closes[eval_indices[0]]
    end_close = spy_series.closes[eval_indices[-1]]
    total_return = (end_close - start_close) / start_close
    equity_curve = [(master_dates[i], STARTING_CAPITAL * (spy_series.closes[i] / start_close)) for i in eval_indices]
    dd = max_drawdown(equity_curve)
    return {
        "total_return_pct": total_return, "start_close": start_close, "end_close": end_close,
        "max_drawdown_pct": dd["max_drawdown_pct"], "note": "benchmark only -- never a promotion gate for any arm",
    }


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    return str(value)


COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json"


def _git_head_sha() -> str | None:
    import subprocess

    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except Exception:
        return None


SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "orb_capital_target_strategy_family_research.py",
    ROOT / "scripts" / "research_capital_target_strategy_family.py",
)


def _git_working_tree_clean() -> bool | None:
    """`True` only if the exact SOURCE FILES being hashed
    (`SOURCE_FILES_FOR_HASHING`) are byte-identical to `HEAD` -- i.e.
    `generation_git_head_sha` genuinely identifies the implementation
    that generated this run, with no uncommitted source edit sitting on
    top of it. Deliberately scoped to only those two files, NOT the
    whole working tree: `study_results.json` and this very hashes
    document are always rewritten by the current run before this is
    computed, so they always differ from whatever was last committed --
    that is the expected, intended effect of a rerun, not evidence of an
    unclean implementation. `None` if git itself could not be queried
    (never silently reported as clean in that case)."""
    import subprocess

    try:
        rel_paths = [str(p.relative_to(ROOT).as_posix()) for p in SOURCE_FILES_FOR_HASHING]
        out = subprocess.run(
            ["git", "status", "--porcelain", "--"] + rel_paths,
            cwd=ROOT, capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip() == ""
    except Exception:
        return None


def _source_file_hashes() -> dict[str, str]:
    return {
        str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in SOURCE_FILES_FOR_HASHING
    }


def build_committed_hashes_document(*, manifest: Mapping[str, Any], results: Mapping[str, Any], results_path: Path, feed: str) -> dict[str, Any]:
    """The small, COMMITTED (never-gitignored), non-vendor-data record
    Issue #348 requires: exact feed/timeframe/adjustment parameters, the
    per-symbol normalized-series hashes, the master-calendar hash, every
    arm/cost-case transaction- and position/equity-ledger hash, the
    `study_results.json` file hash, the exact evaluation window, the
    generating implementation's git HEAD SHA, and the exact SHA-256 of
    the two source files that generated it. `generation_git_head_sha`
    only identifies the actual generating implementation when
    `generation_working_tree_clean` is `True` -- callers must generate
    from a clean checkout of an already-committed implementation-only
    commit (never a dirty working tree) for that SHA to be meaningful.
    Contains no raw OHLCV, no full vendor response, and no secret --
    only SHA-256 digests and already-public parameters/counts."""
    return {
        "feed": feed, "timeframe": "1Day", "adjustment": "all",
        "evaluation_start": EVALUATION_START.isoformat(), "evaluation_end": EVALUATION_END.isoformat(),
        "warmup_start": WARMUP_START.isoformat(),
        "master_calendar_session_count": manifest["master_calendar_session_count"],
        "master_calendar_sha256": results["master_calendar_sha256"],
        "per_symbol_series_sha256": {symbol: info["sha256"] for symbol, info in manifest["per_symbol"].items()},
        "ledger_hashes": results["ledger_hashes"],
        "study_results_json_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        "generation_git_head_sha": _git_head_sha(),
        "generation_working_tree_clean": _git_working_tree_clean(),
        "generation_source_file_sha256": _source_file_hashes(),
        "retrieval_timestamp_utc": manifest["retrieval_timestamp_utc"],
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-missing", action="store_true", default=False)
    parser.add_argument("--feed", default=DEFAULT_FEED)
    parser.add_argument("--data-only", action="store_true", default=False, help="Fetch/verify data and write the provenance manifest, but do not run the study.")
    args = parser.parse_args(argv)

    cache_dir = cache_dir_for_feed(args.feed)
    series_by_symbol = await fetch_all_symbols(cache_dir=cache_dir, fetch_missing=args.fetch_missing, feed=args.feed)
    master_dates = build_master_calendar(series_by_symbol["SPY"])

    require_full_alignment({s: series_by_symbol[s] for s in ALL_SYMBOLS}, master_dates=master_dates)
    integrity = ohlc_integrity_report(series_by_symbol)
    violations = {s: v["violations"] for s, v in integrity.items() if v["violations"]}
    if violations:
        raise CapitalTargetCLIError(f"OHLC integrity violations detected (fail-closed): {violations}")

    manifest = build_provenance_manifest(series_by_symbol, master_dates=master_dates, feed=args.feed)
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    PROVENANCE_PATH.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Provenance manifest written to {PROVENANCE_PATH}")
    print(f"Master calendar: {len(master_dates)} sessions, {master_dates[0]}..{master_dates[-1]}")
    print("Data-fetch and alignment stage complete.")

    if args.data_only:
        return 0

    results = run_full_study(series_by_symbol, master_dates)
    results_path = RESULTS_DIR / "study_results.json"
    results_path.write_text(json.dumps(results, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Study results written to {results_path}")
    for arm in ARMS:
        verdict = results["arms"][arm]["classification"]["verdict"]
        base_mean = results["arms"][arm]["base_cost"]["mean_monthly_dollar_pnl_fixed_30k"]
        print(f"[{arm}] verdict={verdict} mean_monthly_dollar_pnl_base={base_mean}")

    committed_hashes = build_committed_hashes_document(manifest=manifest, results=results, results_path=results_path, feed=args.feed)
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")
    print("Empirical study stage complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
