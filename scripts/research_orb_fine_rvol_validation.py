"""CLI entry point for the fine-RVOL validation study (Issue #302).

Research-only. Reuses the merged liquidity-qualified universe methodology
and fetch/coverage infrastructure from Issue #298
(`src/orb_universe_expansion_research.py`), the merged gap x RVOL
sensitivity study's shared building blocks (Issue #300,
`src/orb_gap_rvol_sensitivity_research.py`), and the merged M15
shadow-decision/execution-simulator path
(`src/shadow_trading_decision.py`, `src/shadow_execution_simulator.py`) --
see `src/orb_fine_rvol_validation_research.py` for this study's own logic
(the finer RVOL grid, chronological split-half robustness checks). Never
writes `config/strategy_v0.yaml`, never sets `strategy.enabled` in tracked
config, never calls a broker.

Two data-source modes, identical posture to
`scripts/research_orb_gap_rvol_sensitivity.py`:

* Credentialed (default): requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
  in the environment (never `config/local.yaml`) and refuses to run without
  them rather than fabricate a result.
* `--cache-only`: reuses the existing shared on-disk bar cache
  (`artifacts/research-universe-expansion/bar-cache/`, gitignored) with an
  inner HTTP client that raises loudly on any cache miss instead of making a
  network call.

Session-count ladder: per this study's own goal ("preferably 180 sessions,
otherwise 120, minimum 90"), when `--sessions` is not explicitly given and
`--cache-only` is active, this script tries 180, then 120, then 90 trailing
sessions in that order and reports the first one the cache can fully
satisfy -- it never silently truncates or fabricates data for a gap the
cache does not cover. Pass `--sessions` explicitly to skip the ladder and
require exactly that count (still subject to the 90-session floor).
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
from src.orb_fine_rvol_validation_research import (
    NORMALIZATION_VERSION,
    ParameterCombo,
    REPOSITORY_ROOT,
    RESEARCH_UNIVERSE_LABEL,
    all_incremental_effects,
    assess_rvol_1_25_signal,
    assess_rvol_1_50_robustness,
    build_recommendation,
    evaluate_combo,
    frequency_target_hits,
    matrix_row,
    parameter_matrix,
    precompute_inputs_table,
    production_baseline_combo,
    simulate_combo_trade_quality,
    split_sessions_chronologically,
)
from src.orb_gap_rvol_sensitivity_research import aggregate_trade_quality
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    DEFAULT_LIQUIDITY_THRESHOLDS,
    SUPPORTED_FEEDS,
    UniverseResearchError,
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

DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "artifacts" / "research-fine-rvol-validation" / "results"
#: Matches Issue #300's own cache-only default (1x RVOL-lookback warmup) --
#: this study reuses the identical shared cache.
DEFAULT_WARMUP_MULTIPLIER = 1
#: "preferably 180 trading sessions, otherwise 120, minimum 90" -- tried in
#: this order when `--sessions` is not explicitly given.
SESSION_LADDER: tuple[int, ...] = (180, 120, 90)
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"


class _RaiseOnCacheMiss:
    """Cache-only mode's inner HTTP client. Reaching this method means the
    requested historical range is not fully covered by the existing cache,
    so this study fails closed (loudly) rather than fabricate or truncate
    data."""

    async def get(self, url: str, *, headers, params):  # noqa: ANN001
        raise SystemExit(
            f"{CACHE_MISS_MARKER}: no cached response for this request "
            f"(url={url}, params={params}). Re-run without --cache-only with real "
            f"Alpaca credentials to fetch it, or pass --sessions to a value the "
            f"cache already covers."
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

    print(f"Phase 1/3: liquidity-qualifying {len(pool_symbols)} pool symbols")
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

    print("Phase 2/3: evaluating 12-combination fine gap x RVOL matrix")
    evaluation_universe = sorted(set(research_universe) | set(production))
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
    production_baseline_evaluation = evaluate_combo(
        combo=production_baseline_combo(), universe=production, session_dates=session_dates,
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

    print("Phase 3/3: chronological split-half robustness check")
    first_half_dates, second_half_dates = split_sessions_chronologically(session_dates)
    print(
        f"  first half: {len(first_half_dates)} sessions "
        f"({first_half_dates[0].isoformat()} to {first_half_dates[-1].isoformat()})"
    )
    print(
        f"  second half: {len(second_half_dates)} sessions "
        f"({second_half_dates[0].isoformat()} to {second_half_dates[-1].isoformat()})"
    )
    first_half_rows: dict[ParameterCombo, dict] = {}
    second_half_rows: dict[ParameterCombo, dict] = {}
    for combo in parameter_matrix():
        first_half_eval = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=first_half_dates,
            inputs_table=inputs_table, base_config=config,
        )
        second_half_eval = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=second_half_dates,
            inputs_table=inputs_table, base_config=config,
        )
        first_half_rows[combo] = matrix_row(first_half_eval)
        second_half_rows[combo] = matrix_row(second_half_eval)

    robustness = assess_rvol_1_50_robustness(first_half_rows=first_half_rows, second_half_rows=second_half_rows)
    rvol_1_25_signal = assess_rvol_1_25_signal(
        rows_by_combo=rows_by_combo, trade_quality_by_combo=trade_quality_by_combo,
    )
    recommendation = build_recommendation(
        rows_by_combo, production_baseline_row=production_baseline_row,
        robustness=robustness, rvol_1_25_signal=rvol_1_25_signal,
        trade_quality_by_combo=trade_quality_by_combo,
    )
    print(f"  1.50x robustness: {robustness['verdict']}")
    print(f"  1.25x signal assessment: {rvol_1_25_signal['verdict']}")
    print(f"  recommended for further validation: {recommendation['recommended_for_further_validation']['combo']}")

    report = {
        "issue": 302,
        "study_dates": {"first_session": session_dates[0].isoformat(), "last_session": session_dates[-1].isoformat(), "sessions": len(session_dates)},
        "chronological_split": {
            "first_half": {"first_session": first_half_dates[0].isoformat(), "last_session": first_half_dates[-1].isoformat(), "sessions": len(first_half_dates)},
            "second_half": {"first_session": second_half_dates[0].isoformat(), "last_session": second_half_dates[-1].isoformat(), "sessions": len(second_half_dates)},
        },
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
        "chronological_split_matrix": {
            "first_half": {f"gap_{c.gap_percent}pct_rvol_{c.rvol_multiple}x": first_half_rows[c] for c in parameter_matrix()},
            "second_half": {f"gap_{c.gap_percent}pct_rvol_{c.rvol_multiple}x": second_half_rows[c] for c in parameter_matrix()},
        },
        "rvol_1_50x_robustness": robustness,
        "rvol_1_25x_signal_assessment": rvol_1_25_signal,
        "recommendation": recommendation,
        "production_baseline_combo": {"gap_percent": str(production_baseline_combo().gap_percent), "rvol_multiple": str(production_baseline_combo().rvol_multiple)},
        "implementation_digest": implementation_digest,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "orb_fine_rvol_validation_study.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    csv_path = output_dir / "orb_fine_rvol_validation_matrix.csv"
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
    parser.add_argument(
        "--sessions", type=int, default=None,
        help="trailing trading sessions to study; omit to try the 180/120/90 ladder (cache-only) or default to 180 (credentialed)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="shared with the Issue #298/#300 bar cache")
    parser.add_argument("--cache-only", action="store_true", help="reuse the existing bar cache with no network call; fails closed on any cache miss")
    parser.add_argument("--feed", choices=sorted(SUPPORTED_FEEDS), default=DEFAULT_FEED)
    parser.add_argument("--warmup-multiplier", type=int, default=DEFAULT_WARMUP_MULTIPLIER)
    args = parser.parse_args(argv)

    if args.sessions is not None and args.sessions < 90:
        print("refusing to run below the 90-session minimum sample size", file=sys.stderr)
        return 2

    if args.sessions is not None:
        session_ladder = [args.sessions]
    elif args.cache_only:
        session_ladder = list(SESSION_LADDER)
    else:
        session_ladder = [SESSION_LADDER[0]]

    for attempt_sessions in session_ladder:
        is_last_rung = attempt_sessions == session_ladder[-1]
        try:
            asyncio.run(_run(
                session_count=attempt_sessions, output_dir=args.output_dir, cache_dir=args.cache_dir,
                cache_only=args.cache_only, feed=args.feed, warmup_multiplier=args.warmup_multiplier,
            ))
            return 0
        except SystemExit as exc:
            if not is_last_rung and CACHE_MISS_MARKER in str(exc):
                print(
                    f"{attempt_sessions}-session request exceeds cached range; "
                    f"falling back to the next rung of the session ladder",
                    file=sys.stderr,
                )
                continue
            raise
        except UniverseResearchError as exc:
            if not is_last_rung:
                print(
                    f"{attempt_sessions}-session request exceeds supported exchange-calendar "
                    f"coverage ({exc}); falling back to the next rung of the session ladder",
                    file=sys.stderr,
                )
                continue
            raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
