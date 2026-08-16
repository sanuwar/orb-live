"""CLI entry point for the ORB regime diagnostic study (Issue #310).

Research-only, diagnostic. Explains, using only decision-time-knowable
information, why gap=0.50%/rvol=1.25x and gap=0.50%/rvol=1.50x (Issue
#304, merged) classified NOT_ROBUST on the 252-session out-of-sample
period despite strong results on the 90-session discovery period. Reuses
the merged liquidity-qualified universe methodology, gap/RVOL evaluator,
injectable calendar seam (Issue #305), and shadow-execution simulator
unchanged -- see `src/orb_regime_diagnostic_research.py` for this study's
own logic (decision-time feature extraction, regime bucketing,
anti-data-mining classification). Never writes `config/strategy_v0.yaml`,
never sets `strategy.enabled`, never calls a broker.

Two periods, two calendars:
* Discovery (2026-04-07..2026-08-13, 90 sessions): within
  `src.exchange_calendar`'s own 2026-2027 coverage -- no seam needed.
* Out-of-sample (2025-04-03..2026-04-06, 252 sessions): outside that
  coverage -- uses the RESEARCH-ONLY calendar seam exactly as
  `scripts/research_orb_out_of_sample_validation.py` does.

`--cache-only` (default here): reuses the existing shared on-disk bar
cache; fails closed on any cache miss rather than fabricate data. Pass
`--fetch-missing` to allow a network fetch for cache gaps (requires
`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import MarketDataFreshnessPolicy, session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_gap_rvol_sensitivity_research import ParameterCombo, evaluate_combo, precompute_inputs_table
from src.orb_regime_diagnostic_research import (
    BASELINE_CONFIG,
    MIN_SAMPLE_CLOSED_TRADES,
    PRIMARY_CONFIGS,
    PRIMARY_CONFIG_LABELS,
    build_diagnostic_rows,
    candidate_multiplicity,
    classify_regime_signal,
    month_summary,
    regime_summary,
    same_day_symbol_combinations,
    select_first_candidate_per_day,
    symbol_summary,
    trade_quality_from_rows,
    walk_forward_quarters,
)
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    DEFAULT_LIQUIDITY_THRESHOLDS,
    UniverseResearchError,
    all_pool_symbols_with_production,
    compute_symbol_liquidity_profile,
    eligible_market_bars,
    fetch_full_minute_history,
    rank_liquidity_qualified_symbols,
    trading_sessions_before,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-regime-diagnostic" / "results"
DISCOVERY_REFERENCE_DAY = date(2026, 8, 14)
DISCOVERY_SESSIONS = 90
OOS_REFERENCE_DAY = date(2026, 4, 7)
OOS_SESSIONS = 252
#: Reaches into 2025; the shared RESEARCH_FRESHNESS_POLICY default (400
#: days) undercounts bars that old relative to today -- same fix Issue
#: #304 applied. Harmless to reuse for the (much more recent) discovery
#: period too, since it only widens acceptance.
DIAGNOSTIC_FRESHNESS_POLICY = MarketDataFreshnessPolicy(version="orb-regime-diagnostic-v1", max_age_seconds=800 * 86400.0)
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"

ALL_CONFIGS: tuple[ParameterCombo, ...] = (*PRIMARY_CONFIGS, BASELINE_CONFIG)
REGIME_DIMENSIONS: tuple[str, ...] = (
    "gap_bucket", "rvol_bucket", "breakout_time_bucket", "spy_vwap_premium_bucket",
    "spy_gap_direction", "or_width_bucket", "volatility_bucket", "day_of_week", "month",
)


class _RaiseOnCacheMiss:
    async def get(self, url: str, *, headers, params):  # noqa: ANN001
        raise SystemExit(
            f"{CACHE_MISS_MARKER}: no cached response for this request "
            f"(url={url}, params={params}). Re-run with --fetch-missing and Alpaca credentials."
        )


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        print(
            "ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY, "
            "or omit --fetch-missing to stay cache-only. Refusing to fabricate results.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


async def _fetch_period_bars(
    *, session_dates: tuple[date, ...], session_bounds_provider, cache_dir: Path, fetch_missing: bool, feed: str,
) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    if session_bounds_provider is None:
        fetch_from = trading_sessions_before(session_dates[0], count=lookback_days)[0]
        resolver = session_open_for
    else:
        fetch_from = research_trading_sessions_before(session_dates[0], count=lookback_days)[0]
        resolver = partial(session_open_for, session_bounds_provider=session_bounds_provider)

    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")
        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(session_dates[-1], resolver(session_dates[-1]), tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

    symbols = list(all_pool_symbols_with_production()) + ["SPY"]
    market_bars_by_symbol: dict[str, list] = {}
    for symbol in symbols:
        raw_bars = await fetch_full_minute_history(transport, credentials=credentials, symbol=symbol, start=start, end=end, now=now, feed=feed)
        kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
        market_bars_by_symbol[symbol] = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), freshness_policy=DIAGNOSTIC_FRESHNESS_POLICY, **kwargs,
        )
    return market_bars_by_symbol


def _liquidity_qualify(
    *, market_bars_by_symbol: dict[str, list], session_dates: tuple[date, ...], session_bounds_provider,
) -> tuple[str, ...]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    pool_symbols = list(all_pool_symbols_with_production())
    resolver_kwargs = {} if session_bounds_provider is None else {
        "session_open_resolver": partial(session_open_for, session_bounds_provider=session_bounds_provider)
    }
    profiles = {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=[b.to_minute_bar() for b in market_bars_by_symbol[symbol]],
            session_dates=session_dates, lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
            **resolver_kwargs,
        )
        for symbol in pool_symbols
    }
    return rank_liquidity_qualified_symbols(profiles)


async def _evaluate_period(
    *, label: str, session_dates: tuple[date, ...], session_bounds_provider, cache_dir: Path,
    fetch_missing: bool, feed: str, implementation_digest: str, created_at: datetime,
) -> dict:
    print(f"[{label}] fetching/loading {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]})")
    market_bars_by_symbol = await _fetch_period_bars(
        session_dates=session_dates, session_bounds_provider=session_bounds_provider,
        cache_dir=cache_dir, fetch_missing=fetch_missing, feed=feed,
    )
    research_universe = _liquidity_qualify(
        market_bars_by_symbol=market_bars_by_symbol, session_dates=session_dates,
        session_bounds_provider=session_bounds_provider,
    )
    print(f"[{label}] liquidity-qualified universe: {len(research_universe)} symbols -> {research_universe}")

    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver_kwargs = {} if session_bounds_provider is None else {
        "session_open_resolver": partial(session_open_for, session_bounds_provider=session_bounds_provider)
    }
    inputs_table = precompute_inputs_table(
        universe=research_universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(), **resolver_kwargs,
    )

    rows_by_config = {}
    evaluations_by_config = {}
    for combo in ALL_CONFIGS:
        config_label = PRIMARY_CONFIG_LABELS[combo]
        evaluation = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=session_dates,
            inputs_table=inputs_table, base_config=load_config(),
        )
        evaluations_by_config[combo] = evaluation
        rows = build_diagnostic_rows(
            period=label, combo=combo, config_label=config_label, evaluation=evaluation,
            market_bars_by_symbol=market_bars_by_symbol, base_config=load_config(),
            implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=session_bounds_provider,
        )
        rows_by_config[combo] = rows
        closed = sum(1 for r in rows if r.outcome["closed"])
        print(f"[{label}] {config_label}: {len(evaluation.candidate_signals)} candidates, {closed} closed trades")

    return {
        "label": label, "session_dates": session_dates, "universe": research_universe,
        "evaluations_by_config": evaluations_by_config, "rows_by_config": rows_by_config,
    }


def _config_overview(rows) -> dict:
    return trade_quality_from_rows(rows)


def _build_report(discovery: dict, oos: dict, *, implementation_digest: str) -> dict:
    report: dict = {
        "issue": 310,
        "discovery_period": {"first_session": discovery["session_dates"][0].isoformat(), "last_session": discovery["session_dates"][-1].isoformat(), "sessions": len(discovery["session_dates"]), "universe": list(discovery["universe"])},
        "out_of_sample_period": {"first_session": oos["session_dates"][0].isoformat(), "last_session": oos["session_dates"][-1].isoformat(), "sessions": len(oos["session_dates"]), "universe": list(oos["universe"])},
        "min_sample_closed_trades": MIN_SAMPLE_CLOSED_TRADES,
        "configurations": {},
        "regime_signal_classification": {},
        "implementation_digest": implementation_digest,
    }

    all_trade_rows: list[dict] = []
    for combo in ALL_CONFIGS:
        label = PRIMARY_CONFIG_LABELS[combo]
        disc_rows = discovery["rows_by_config"][combo]
        oos_rows = oos["rows_by_config"][combo]
        all_trade_rows.extend(r.flat() for r in disc_rows)
        all_trade_rows.extend(r.flat() for r in oos_rows)

        disc_overview = _config_overview(disc_rows)
        oos_overview = _config_overview(oos_rows)

        entry = {
            "gap_percent": str(combo.gap_percent), "rvol_multiple": str(combo.rvol_multiple),
            "discovery": {
                "overview": disc_overview,
                "regime_summary": {dim: regime_summary(disc_rows, key_field=dim) for dim in REGIME_DIMENSIONS},
                "symbol_summary": symbol_summary(disc_rows),
                "walk_forward_quarters": walk_forward_quarters(disc_rows),
                "candidate_multiplicity": candidate_multiplicity(discovery["evaluations_by_config"][combo]),
                "same_day_symbol_combinations": same_day_symbol_combinations(discovery["evaluations_by_config"][combo]),
            },
            "out_of_sample": {
                "overview": oos_overview,
                "regime_summary": {dim: regime_summary(oos_rows, key_field=dim) for dim in REGIME_DIMENSIONS},
                "symbol_summary": symbol_summary(oos_rows),
                "walk_forward_quarters": walk_forward_quarters(oos_rows),
                "candidate_multiplicity": candidate_multiplicity(oos["evaluations_by_config"][combo]),
                "same_day_symbol_combinations": same_day_symbol_combinations(oos["evaluations_by_config"][combo]),
            },
        }

        # single-vs-multi-candidate day split
        for period_name, rows in (("discovery", disc_rows), ("out_of_sample", oos_rows)):
            single = [r for r in rows if r.decision["candidates_same_day"] == 1]
            multi = [r for r in rows if r.decision["candidates_same_day"] > 1]
            entry[period_name]["single_vs_multi_candidate_day"] = {
                "single_candidate_day": _config_overview(single),
                "multi_candidate_day": _config_overview(multi),
            }
            # one-trade-per-day vs all-candidates (decision-time-only selection)
            first_of_day_rows = [r for r in rows if r.decision["is_first_candidate_of_day"]]
            entry[period_name]["one_trade_per_day_vs_all_candidates"] = {
                "ALL_CANDIDATES": _config_overview(rows),
                "ONE_TRADE_PER_DAY": _config_overview(first_of_day_rows),
            }

        report["configurations"][label] = entry

        if combo in PRIMARY_CONFIGS:
            baseline_disc_exp = disc_overview.get("expectancy_per_trade") or 0.0
            baseline_oos_exp = oos_overview.get("expectancy_per_trade") or 0.0
            classification_by_dim = {}
            for dim in REGIME_DIMENSIONS:
                disc_dim = entry["discovery"]["regime_summary"][dim]
                oos_dim = entry["out_of_sample"]["regime_summary"][dim]
                per_bucket = {}
                for bucket in sorted(set(disc_dim) | set(oos_dim)):
                    symbol_ctx = None  # per-bucket symbol breakdown not computed (would require re-slicing rows); documented limitation
                    per_bucket[bucket] = classify_regime_signal(
                        oos_bucket_stats=oos_dim.get(bucket), discovery_bucket_stats=disc_dim.get(bucket),
                        baseline_expectancy=(baseline_oos_exp + baseline_disc_exp) / 2,
                        symbol_summary_for_bucket=symbol_ctx,
                    )
                classification_by_dim[dim] = per_bucket
            report["regime_signal_classification"][label] = classification_by_dim

    report["_all_trade_rows"] = all_trade_rows
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true", help="allow a network fetch for cache gaps (requires Alpaca credentials); default is cache-only, fails closed on any miss")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    discovery_dates = trading_sessions_before(DISCOVERY_REFERENCE_DAY, count=DISCOVERY_SESSIONS)
    oos_dates = research_trading_sessions_before(OOS_REFERENCE_DAY, count=OOS_SESSIONS)
    if set(discovery_dates) & set(oos_dates):
        raise SystemExit("discovery and out-of-sample periods must not overlap")

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    discovery = asyncio.run(_evaluate_period(
        label="DISCOVERY", session_dates=discovery_dates, session_bounds_provider=None,
        cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
        implementation_digest=implementation_digest, created_at=created_at,
    ))
    oos = asyncio.run(_evaluate_period(
        label="OUT_OF_SAMPLE", session_dates=oos_dates, session_bounds_provider=research_regular_session_bounds,
        cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
        implementation_digest=implementation_digest, created_at=created_at,
    ))

    print("Building regime diagnostic report...")
    report = _build_report(discovery, oos, implementation_digest=implementation_digest)
    all_trade_rows = report.pop("_all_trade_rows")

    for label, entry in report["regime_signal_classification"].items():
        for dim, buckets in entry.items():
            promising = [b for b, v in buckets.items() if v["verdict"] == "PROMISING"]
            if promising:
                print(f"  [{label}] {dim}: PROMISING buckets -> {promising}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_regime_diagnostic_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    trade_fieldnames = sorted({key for row in all_trade_rows for key in row})
    with (args.output_dir / "orb_regime_diagnostic_trades.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=trade_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_trade_rows)

    regime_rows = []
    for config_label, entry in report["configurations"].items():
        for period_name in ("discovery", "out_of_sample"):
            for dim, buckets in entry[period_name]["regime_summary"].items():
                for bucket, stats in buckets.items():
                    regime_rows.append({"config": config_label, "period": period_name, "dimension": dim, "bucket": bucket, **stats})
    with (args.output_dir / "orb_regime_diagnostic_regime_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in regime_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(regime_rows)

    symbol_rows = []
    for config_label, entry in report["configurations"].items():
        for period_name in ("discovery", "out_of_sample"):
            for symbol, stats in entry[period_name]["symbol_summary"]["per_symbol"].items():
                symbol_rows.append({"config": config_label, "period": period_name, "symbol": symbol, **stats})
    with (args.output_dir / "orb_regime_diagnostic_symbol_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in symbol_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(symbol_rows)

    time_rows = []
    for config_label, entry in report["configurations"].items():
        for period_name in ("discovery", "out_of_sample"):
            for bucket, stats in entry[period_name]["regime_summary"]["breakout_time_bucket"].items():
                time_rows.append({"config": config_label, "period": period_name, "breakout_time_bucket": bucket, **stats})
    with (args.output_dir / "orb_regime_diagnostic_time_bucket_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in time_rows for key in row})
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(time_rows)

    print(f"JSON report: {args.output_dir / 'orb_regime_diagnostic_report.json'}")
    print(f"Trade-level CSV: {args.output_dir / 'orb_regime_diagnostic_trades.csv'} ({len(all_trade_rows)} rows)")
    print(f"Regime summary CSV: {args.output_dir / 'orb_regime_diagnostic_regime_summary.csv'} ({len(regime_rows)} rows)")
    print(f"Symbol summary CSV: {args.output_dir / 'orb_regime_diagnostic_symbol_summary.csv'} ({len(symbol_rows)} rows)")
    print(f"Time-bucket summary CSV: {args.output_dir / 'orb_regime_diagnostic_time_bucket_summary.csv'} ({len(time_rows)} rows)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except UniverseResearchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
