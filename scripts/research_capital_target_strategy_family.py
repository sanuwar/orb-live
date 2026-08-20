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
    max_drawdown,
)

WARMUP_START = date(2021, 1, 1)
FETCH_END_EXCLUSIVE = date(2026, 8, 14)  # one day past EVALUATION_END; Alpaca's `end` is exclusive
DEFAULT_CACHE_DIR = ROOT / "artifacts" / "research-capital-target-strategy-family" / "daily-bar-cache"
RESULTS_DIR = ROOT / "artifacts" / "research-capital-target-strategy-family" / "results"
PROVENANCE_PATH = RESULTS_DIR / "provenance_manifest.json"
DEFAULT_FEED = "iex"
MAX_PAGES_PER_SYMBOL = 20

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
    (untouched, read-only), bucketed by calendar month. Never re-runs or
    modifies the ORB baseline -- purely a read of its own already-
    validated results, for correlation purposes only."""
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
    return [(date(y, m, 1), r) for (y, m), r in sorted(monthly.items())]


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
                sim = run_arm_a(universe_series[ARM_A], master_dates, cost_bps=cost_bps)
            elif arm == ARM_B:
                selector = arm_b_selector(universe_series[ARM_B], sma200_by_arm_symbol[ARM_B])
                sim = run_monthly_rebalance_arm(universe_series[ARM_B], master_dates, arm=ARM_B, selector=selector, cost_bps=cost_bps)
            else:
                selector = arm_c_selector(universe_series[ARM_C], sma200_by_arm_symbol[ARM_C])
                sim = run_monthly_rebalance_arm(universe_series[ARM_C], master_dates, arm=ARM_C, selector=selector, cost_bps=cost_bps)
            raw_sim[arm][cost_bps] = sim

    orb_monthly = load_orb_monthly_r_series()
    spy_monthly_pct_pairs = monthly_return_pct([(d, series_by_symbol["SPY"].closes[i]) for i, d in enumerate(master_dates)])
    spy_monthly_pct_pairs = [(d, v) for d, v in spy_monthly_pct_pairs if EVALUATION_START <= d <= EVALUATION_END]

    per_arm_output: dict[str, Any] = {}
    for arm in ARMS:
        base_sim = raw_sim[arm][BASE_COST_BPS]
        stress_sim = raw_sim[arm][STRESS_COST_BPS]

        base_metrics = compute_arm_metrics(
            arm=arm, closed_trades=base_sim.closed_trades, daily_equity=base_sim.daily_equity,
            starting_capital=base_sim.starting_capital, total_cost_paid=base_sim.total_cost_paid,
            evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        )
        stress_metrics = compute_arm_metrics(
            arm=arm, closed_trades=stress_sim.closed_trades, daily_equity=stress_sim.daily_equity,
            starting_capital=stress_sim.starting_capital, total_cost_paid=stress_sim.total_cost_paid,
            evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        )

        segment_metrics = {}
        for seg in CALENDAR_SEGMENTS:
            seg_dates = [d for d in master_dates if segment_label(d) == seg and EVALUATION_START <= d <= EVALUATION_END]
            if not seg_dates:
                continue
            segment_metrics[seg] = compute_arm_metrics(
                arm=arm, closed_trades=base_sim.closed_trades, daily_equity=base_sim.daily_equity,
                starting_capital=base_sim.starting_capital, total_cost_paid=base_sim.total_cost_paid,
                evaluation_start=seg_dates[0], evaluation_end=seg_dates[-1],
            )

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
                }

        best_share = base_metrics["concentration"]["best_contribution_share"]
        classification = classify_capital_target_arm(
            average_monthly_dollars_base=Decimal(str(base_metrics["mean_monthly_dollar_pnl"] or 0)),
            segment_net_returns={seg: v["net_pnl"] for seg, v in segment_metrics.items()},
            max_drawdown_pct=base_metrics["max_drawdown_pct"],
            pooled_net_base=base_metrics["net_pnl"], pooled_net_stress=stress_metrics["net_pnl"],
            mean_monthly_return_pct=base_metrics["mean_monthly_return_pct"],
            profitable_month_pct=base_metrics["profitable_month_pct"],
            correlation_to_orb=correlation_to_orb, best_single_symbol_positive_share=best_share,
            integrity_ok=True,
        )

        per_arm_output[arm] = {
            "base_cost": base_metrics, "stress_cost": stress_metrics, "segments": segment_metrics,
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

    results["arms"] = per_arm_output
    results["cross_arm_correlation"] = cross_arm_correlation
    results["spy_buy_and_hold_benchmark"] = spy_buy_and_hold
    results["orb_monthly_r_series"] = [(d.isoformat(), str(r)) for d, r in orb_monthly]
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


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-missing", action="store_true", default=False)
    parser.add_argument("--feed", default=DEFAULT_FEED)
    parser.add_argument("--data-only", action="store_true", default=False, help="Fetch/verify data and write the provenance manifest, but do not run the study.")
    args = parser.parse_args(argv)

    series_by_symbol = await fetch_all_symbols(cache_dir=DEFAULT_CACHE_DIR, fetch_missing=args.fetch_missing, feed=args.feed)
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
        base_mean = results["arms"][arm]["base_cost"]["mean_monthly_dollar_pnl"]
        print(f"[{arm}] verdict={verdict} mean_monthly_dollar_pnl_base={base_mean}")
    print("Empirical study stage complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
