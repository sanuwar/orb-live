"""CLI entry point for the ORB gap x RVOL sensitivity study (Issue #300).

Research-only. Reuses the merged liquidity-qualified universe methodology
and fetch/coverage infrastructure from Issue #298
(`src/orb_universe_expansion_research.py`) and the merged M15
shadow-decision/execution-simulator path
(`src/shadow_trading_decision.py`, `src/shadow_execution_simulator.py`) --
see `src/orb_gap_rvol_sensitivity_research.py` for the actual sensitivity
logic this script drives. Never writes `config/strategy_v0.yaml`, never
sets `strategy.enabled` in tracked config, never calls a broker.

Two data-source modes:

* Credentialed (default): identical posture to
  `scripts/research_orb_universe_expansion.py` -- requires
  `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY` in the environment (never
  `config/local.yaml`) and refuses to run without them rather than
  fabricate a result.
* `--cache-only`: reuses Issue #298's existing on-disk bar cache
  (`artifacts/research-universe-expansion/bar-cache/`, gitignored, shared
  with that study so overlapping symbol/date ranges are never re-fetched)
  with an inner HTTP client that raises loudly on any cache miss instead of
  making a network call. Safe by construction: a cache hit is unmodified
  historical data already fetched by a credentialed run, and any gap in the
  cache fails the run closed rather than silently fabricating or truncating
  data. No credentials are read or required in this mode.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_gap_rvol_sensitivity_research import (
    NORMALIZATION_VERSION,
    ParameterCombo,
    REPOSITORY_ROOT,
    RESEARCH_UNIVERSE_LABEL,
    aggregate_trade_quality,
    all_incremental_effects,
    baseline_combo,
    build_recommendation,
    evaluate_combo,
    frequency_target_hits,
    matrix_row,
    parameter_matrix,
    precompute_inputs_table,
    simulate_combo_trade_quality,
)
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    DEFAULT_LIQUIDITY_THRESHOLDS,
    SUPPORTED_FEEDS,
    all_pool_symbols_with_production,
    eligible_market_bars,
    fetch_full_minute_history,
    compute_symbol_liquidity_profile,
    historical_fetch_start,
    production_universe,
    rank_liquidity_qualified_symbols,
    trading_sessions_before,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "artifacts" / "research-gap-rvol-sensitivity" / "results"
#: Unlike the Issue #298 CLI's default of 3, this study defaults to a 1x
#: RVOL-lookback warmup buffer because that is what the shared bar cache
#: actually contains (see docs/research/ORB_GAP_RVOL_SENSITIVITY_STUDY.md,
#: "Data source" -- the merged study's own liquidity-qualification pass
#: documented staying on this same narrower, already-cached buffer for the
#: identical reason). `--warmup-multiplier 3` is available for a future
#: credentialed run that fetches the wider buffer.
DEFAULT_WARMUP_MULTIPLIER = 1


class _RaiseOnCacheMiss:
    """A cache-only mode's inner HTTP client. `CachingHttpClient` only ever
    calls this when the requested (url, params) pair is not already cached
    -- so reaching this method means the requested historical range is not
    fully covered by the existing cache, and this study must fail closed
    (loudly, not by fabricating or truncating data) rather than pretend a
    real network call happened."""

    async def get(self, url: str, *, headers, params):  # noqa: ANN001 -- matches _HttpClientLike
        raise SystemExit(
            f"CACHE_MISS_IN_CACHE_ONLY_MODE: no cached response for this request "
            f"(url={url}, params={params}). Re-run without --cache-only with real "
            f"Alpaca credentials to fetch it, or narrow --sessions/--warmup-multiplier "
            f"to a range already cached."
        )


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        print(
            "ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY (market-data-only Alpaca keys), or pass "
            "--cache-only to reuse the existing bar cache with no network call. "
            "Refusing to fabricate results.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


async def _run(
    *, session_count: int, output_dir: Path, cache_dir: Path, cache_only: bool,
    feed: str, warmup_multiplier: int,
) -> dict:
    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    today = datetime.now(EASTERN).date()
    session_dates = trading_sessions_before(today, count=session_count)
    fetch_from = historical_fetch_start(
        session_dates[0], lookback_days=lookback_days, warmup_multiplier=warmup_multiplier,
    )

    integrity = validate_cache_integrity(cache_dir)
    if integrity["removed"]:
        print(f"cache integrity: removed {len(integrity['removed'])} corrupt entr(y/ies)")
    print(f"cache integrity: {len(integrity['kept'])} usable cached response(s) reused")

    if cache_only:
        print("data source: --cache-only (no network call permitted; fails closed on any cache miss)")
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")
        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)
    else:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(
        session_dates[-1], session_open_for(session_dates[-1]), tzinfo=EASTERN,
    ).astimezone(timezone.utc) + timedelta(hours=8)

    pool_symbols = list(all_pool_symbols_with_production())
    fetch_symbols = pool_symbols + ["SPY"]
    print(
        f"studying {len(session_dates)} sessions "
        f"({session_dates[0].isoformat()} to {session_dates[-1].isoformat()}), "
        f"{len(fetch_symbols)} candidate/context symbols, feed={feed}, "
        f"RVOL warmup buffer back to {fetch_from.isoformat()} "
        f"({lookback_days}x{warmup_multiplier}={lookback_days * warmup_multiplier} sessions)"
    )

    market_bars_by_symbol: dict[str, list] = {}
    minute_bars_by_symbol: dict[str, list] = {}
    for index, symbol in enumerate(fetch_symbols, start=1):
        def _progress(page_number: int, bars_so_far: int, *, _symbol: str = symbol) -> None:
            print(f"  fetching {_symbol} {index}/{len(fetch_symbols)}: page {page_number}, {bars_so_far} bars so far")

        raw_bars = await fetch_full_minute_history(
            transport, credentials=credentials, symbol=symbol, start=start, end=end,
            now=now, feed=feed, on_page=_progress,
        )
        market_bars = eligible_market_bars(raw_bars, symbol=symbol, now=now())
        market_bars_by_symbol[symbol] = market_bars
        minute_bars_by_symbol[symbol] = [bar.to_minute_bar() for bar in market_bars]
        print(f"fetched {symbol} {index}/{len(fetch_symbols)}: {len(market_bars)} valid minute bars")

    print(f"Phase 1/2: liquidity-qualifying {len(pool_symbols)} pool symbols")
    profiles = {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=minute_bars_by_symbol[symbol], session_dates=session_dates,
            lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
        )
        for symbol in pool_symbols
    }
    research_universe = rank_liquidity_qualified_symbols(profiles)
    production = production_universe()
    print(
        f"liquidity-qualified research universe: {len(research_universe)} of "
        f"{len(pool_symbols)} pool symbols -> {research_universe}"
    )
    print(f"current production universe (context only): {len(production)} symbols -> {production}")

    print("Phase 2/2: evaluating 12-combination gap x RVOL matrix")
    evaluation_universe = sorted(set(research_universe) | set(production))
    # Precompute the threshold-independent per-(symbol, session) inputs ONCE
    # for every symbol either universe needs, then reuse that table for all
    # 12 combinations (`evaluate_combo` only re-runs the cheap threshold
    # comparison per combination) -- see `precompute_inputs_table`'s
    # docstring for why this matters: without it, this matrix would re-scan
    # each symbol's full multi-month bar history 12 times instead of once.
    inputs_table = precompute_inputs_table(
        universe=evaluation_universe, session_dates=session_dates,
        minute_bars_by_symbol=minute_bars_by_symbol, spy_minute_bars=minute_bars_by_symbol["SPY"],
        base_config=config,
    )
    evaluations_by_combo: dict[ParameterCombo, object] = {}
    for combo in parameter_matrix():
        evaluations_by_combo[combo] = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=session_dates,
            inputs_table=inputs_table, base_config=config,
        )
        row = evaluations_by_combo[combo].universe_report
        print(
            f"  gap={combo.gap_percent}% rvol={combo.rvol_multiple}x: "
            f"coverage={row['coverage']['completeness']:.2%} ({row['coverage']['status']}), "
            f"candidate_day_rate={row['candidate_day_rate']:.2%}, "
            f"total_candidates={row['total_candidates']}"
        )
    # Current-production context (goal item 9.A's baseline) is only needed
    # at the baseline combo, not the full matrix.
    production_baseline_evaluation = evaluate_combo(
        combo=baseline_combo(), universe=production, session_dates=session_dates,
        inputs_table=inputs_table, base_config=config,
    )

    rows_by_combo = {combo: matrix_row(evaluations_by_combo[combo]) for combo in parameter_matrix()}
    production_baseline_row = matrix_row(production_baseline_evaluation)
    incremental_effects = all_incremental_effects(evaluations_by_combo)
    freq_hits = frequency_target_hits([rows_by_combo[c] for c in parameter_matrix()])

    print("Trade quality: replaying every CANDIDATE through the merged shadow-execution simulator")
    implementation_digest = checkpoint_implementation_digest(REPOSITORY_ROOT)
    created_at = now()
    trade_quality_by_combo = {}
    for combo in parameter_matrix():
        trade_quality_by_combo[combo] = simulate_combo_trade_quality(
            evaluation=evaluations_by_combo[combo], market_bars_by_symbol=market_bars_by_symbol,
            base_config=config, implementation_digest=implementation_digest, created_at=created_at,
        )
        tq = trade_quality_by_combo[combo]
        print(
            f"  gap={combo.gap_percent}% rvol={combo.rvol_multiple}x: {tq['status']}, "
            f"trades_closed={tq['trades_closed']}, win_rate={tq['win_rate']}"
        )

    recommendation = build_recommendation(rows_by_combo, trade_quality_by_combo)

    report = {
        "issue": 300,
        "study_dates": {"first_session": session_dates[0].isoformat(), "last_session": session_dates[-1].isoformat(), "sessions": len(session_dates)},
        "data_source": {
            "mode": "cache-only" if cache_only else "credentialed",
            "cache_dir": str(cache_dir),
            "feed": feed,
            "warmup_multiplier": warmup_multiplier,
            "fetch_from": fetch_from.isoformat(),
        },
        "research_universe": {"label": RESEARCH_UNIVERSE_LABEL, "symbols": list(research_universe), "size": len(research_universe)},
        "current_production_universe": {"symbols": list(production), "size": len(production)},
        "liquidity_thresholds": {
            "min_median_price": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_median_price),
            "min_avg_daily_dollar_volume": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_avg_daily_dollar_volume),
            "min_avg_daily_share_volume": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_avg_daily_share_volume),
            "min_session_opening_range_complete_pct": DEFAULT_LIQUIDITY_THRESHOLDS.min_session_opening_range_complete_pct,
            "min_rvol_history_satisfiable_pct": DEFAULT_LIQUIDITY_THRESHOLDS.min_rvol_history_satisfiable_pct,
        },
        "matrix": [rows_by_combo[c] for c in parameter_matrix()],
        "current_production_baseline_context": production_baseline_row,
        "incremental_effects": incremental_effects,
        "frequency_target_hits": freq_hits,
        "trade_quality": {
            f"gap_{c.gap_percent}pct_rvol_{c.rvol_multiple}x": trade_quality_by_combo[c]
            for c in parameter_matrix()
        },
        "recommendation": recommendation,
        "baseline_combo": {"gap_percent": str(baseline_combo().gap_percent), "rvol_multiple": str(baseline_combo().rvol_multiple)},
        "implementation_digest": implementation_digest,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "orb_gap_rvol_sensitivity_study.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    csv_path = output_dir / "orb_gap_rvol_sensitivity_matrix.csv"
    fieldnames = [
        "gap_percent", "rvol_multiple", "sessions_studied", "coverage_pct", "coverage_status",
        "sessions_with_candidate", "candidate_day_rate", "zero_candidate_sessions", "total_candidates",
        "mean_candidates_per_session", "median_candidates_on_candidate_days", "max_candidates_one_day",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for combo in parameter_matrix():
            writer.writerow(rows_by_combo[combo])

    print(f"JSON report: {json_path}")
    print(f"CSV matrix: {csv_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sessions", type=int, default=90, help="trailing trading sessions to study (default 90; minimum 60)")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="shared with scripts/research_orb_universe_expansion.py's bar cache")
    parser.add_argument("--cache-only", action="store_true", help="reuse the existing bar cache with no network call; fails closed on any cache miss")
    parser.add_argument("--feed", choices=sorted(SUPPORTED_FEEDS), default=DEFAULT_FEED)
    parser.add_argument("--warmup-multiplier", type=int, default=DEFAULT_WARMUP_MULTIPLIER)
    args = parser.parse_args(argv)

    if args.sessions < 60:
        print("refusing to run below the 60-session minimum sample size", file=sys.stderr)
        return 2

    asyncio.run(_run(
        session_count=args.sessions, output_dir=args.output_dir, cache_dir=args.cache_dir,
        cache_only=args.cache_only, feed=args.feed, warmup_multiplier=args.warmup_multiplier,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
