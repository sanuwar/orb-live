"""CLI entry point for the ORB C_BASE baseline stability decomposition
study (Issue #319).

Research-only, diagnostic. Issue #317/PR #318 found the proposed RVOL
1.50x-1.99x band did not generalize to a third, independent historical
period. This script determines whether the underlying C_BASE configuration
(gap>=0.50%/RVOL>=1.50x, otherwise canonical `orb_long_v0`) has a
reasonably persistent edge across the three already-established periods,
and whether period-to-period deterioration is primarily a time/market-
regime effect or is materially explained by different liquidity-qualified
universes -- see `src/orb_baseline_stability_research.py` for this study's
own logic. Never writes `config/strategy_v0.yaml`, never sets
`strategy.enabled`, never calls a broker, never implements a trailing
stop or changes the simulator.

Three fixed periods, three calendars:
* P1_THIRD (2024-04-03..2025-04-02, 252 sessions): RESEARCH-ONLY calendar
  (Issue #317's 2024 extension).
* P2_OOS (2025-04-03..2026-04-06, 252 sessions): RESEARCH-ONLY calendar
  (2025 portion).
* P3_DISCOVERY (2026-04-07..2026-08-13, 90 sessions): production calendar
  (within `src.exchange_calendar`'s own 2026-2027 coverage).

`--cache-only` (default here): reuses the existing shared on-disk bar
cache -- all three periods' bars were already fetched by prior merged
studies (#304/#310/#317), so this should normally need no network access
at all. Pass `--fetch-missing` to allow a network fetch for any gap
(requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`, read from the
environment only, never printed).
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
from src.orb_baseline_stability_research import (
    B_BASE,
    C_BASE,
    MEANINGFUL_MFE_THRESHOLD_R,
    MIN_COHORT_SIZE,
    STUDY_CONFIGS,
    best_contributing_symbol,
    build_stability_rows,
    cell_summary,
    classify_stability,
    combined_view,
    common_cohort,
    excluding_symbol,
    meaningful_mfe_fraction,
    mfe_analysis,
    quarterly_stability,
    recommend_next_experiment,
)
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, matrix_row, precompute_inputs_table
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import trade_quality_from_rows
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

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-baseline-stability" / "results"

#: Fixed, no boundary changes.
PERIOD_DEFINITIONS: dict[str, dict] = {
    "P1_THIRD": {"reference_day": date(2025, 4, 3), "sessions": 252, "research_calendar": True},
    "P2_OOS": {"reference_day": date(2026, 4, 7), "sessions": 252, "research_calendar": True},
    "P3_DISCOVERY": {"reference_day": date(2026, 8, 14), "sessions": 90, "research_calendar": False},
}

#: Reaches back into 2024 (P1's own lookback floor) -- same value Issue
#: #317's own script uses (proven correct there, see that script's own
#: comment on why 800 days was insufficient).
STABILITY_FRESHNESS_POLICY = MarketDataFreshnessPolicy(
    version="orb-baseline-stability-v1", max_age_seconds=1100 * 86400.0,
)
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"


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


def _session_dates_for(period: str) -> tuple[date, ...]:
    spec = PERIOD_DEFINITIONS[period]
    if spec["research_calendar"]:
        return research_trading_sessions_before(spec["reference_day"], count=spec["sessions"])
    return trading_sessions_before(spec["reference_day"], count=spec["sessions"])


def _session_bounds_provider_for(period: str):
    return research_regular_session_bounds if PERIOD_DEFINITIONS[period]["research_calendar"] else None


async def _fetch_period_bars(
    *, period: str, session_dates: tuple[date, ...], cache_dir: Path, fetch_missing: bool, feed: str,
) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    session_bounds_provider = _session_bounds_provider_for(period)
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
            raw_bars, symbol=symbol, now=now(), freshness_policy=STABILITY_FRESHNESS_POLICY, **kwargs,
        )
    return market_bars_by_symbol


def _liquidity_profiles(*, period: str, market_bars_by_symbol: dict[str, list], session_dates: tuple[date, ...]) -> dict[str, object]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    pool_symbols = list(all_pool_symbols_with_production())
    session_bounds_provider = _session_bounds_provider_for(period)
    resolver_kwargs = {} if session_bounds_provider is None else {
        "session_open_resolver": partial(session_open_for, session_bounds_provider=session_bounds_provider)
    }
    return {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=[b.to_minute_bar() for b in market_bars_by_symbol[symbol]],
            session_dates=session_dates, lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
            **resolver_kwargs,
        )
        for symbol in pool_symbols
    }


async def _load_profiles(*, period: str, cache_dir: Path, fetch_missing: bool, feed: str) -> dict:
    """Phase 1 (per period): fetch bars just long enough to compute
    liquidity profiles and the native universe, then let `market_bars_by_symbol`
    go out of scope when this coroutine returns. Holding all three periods'
    full ~100-symbol bar sets in memory simultaneously (the original,
    single-pass design) pushed resident memory into double-digit GB and
    caused severe thrashing; this two-pass split keeps only one period's
    raw bars in memory at a time."""
    session_dates = _session_dates_for(period)
    print(f"[{period}] loading {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]}) for profiling")
    market_bars_by_symbol = await _fetch_period_bars(
        period=period, session_dates=session_dates, cache_dir=cache_dir, fetch_missing=fetch_missing, feed=feed,
    )
    profiles = _liquidity_profiles(period=period, market_bars_by_symbol=market_bars_by_symbol, session_dates=session_dates)
    native_universe = rank_liquidity_qualified_symbols(profiles)
    print(f"[{period}] native universe: {len(native_universe)} symbols -> {native_universe}")
    return {"period": period, "session_dates": session_dates, "profiles": profiles, "native_universe": native_universe}


async def _evaluate_period(
    *, period: str, session_dates: tuple[date, ...], native_universe: tuple[str, ...], cohort: tuple[str, ...],
    cache_dir: Path, fetch_missing: bool, feed: str, implementation_digest: str, created_at: datetime,
) -> dict:
    """Phase 2 (per period): reload this period's bars (cache-only, so a
    fast disk re-read given phase 1 already populated the on-disk HTTP
    cache) to build the precomputed inputs table and evaluate both views.
    `market_bars_by_symbol`/`inputs_table` go out of scope when this
    coroutine returns; only the much smaller per-cell summaries/rows are
    kept by the caller."""
    print(f"[{period}] reloading {len(session_dates)} sessions for evaluation")
    market_bars_by_symbol = await _fetch_period_bars(
        period=period, session_dates=session_dates, cache_dir=cache_dir, fetch_missing=fetch_missing, feed=feed,
    )
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    session_bounds_provider = _session_bounds_provider_for(period)
    resolver_kwargs = {} if session_bounds_provider is None else {
        "session_open_resolver": partial(session_open_for, session_bounds_provider=session_bounds_provider)
    }
    inputs_table = precompute_inputs_table(
        universe=native_universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(), **resolver_kwargs,
    )

    cells: dict[str, dict] = {}
    for config_label, combo in STUDY_CONFIGS.items():
        for view, universe in (("NATIVE", native_universe), ("COHORT", cohort)):
            if view == "COHORT" and len(universe) < MIN_COHORT_SIZE:
                cells[f"{config_label}_{view}"] = {"insufficient_sample": True, "universe_size": len(universe)}
                continue
            evaluation = evaluate_combo(
                combo=combo, universe=universe, session_dates=session_dates,
                inputs_table=inputs_table, base_config=load_config(),
            )
            row = matrix_row(evaluation)
            all_rows = build_stability_rows(
                period=period, view=view, config_label=config_label, combo=combo,
                candidate_signals=evaluation.candidate_signals, market_bars_by_symbol=market_bars_by_symbol,
                base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
                session_bounds_provider=session_bounds_provider,
            )
            one_per_day_signals = select_first_candidate_per_day(evaluation.candidate_signals)
            one_per_day_rows = build_stability_rows(
                period=period, view=view, config_label=config_label, combo=combo,
                candidate_signals=one_per_day_signals, market_bars_by_symbol=market_bars_by_symbol,
                base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
                session_bounds_provider=session_bounds_provider,
            )
            summary = cell_summary(
                rows=all_rows, universe=universe, total_sessions=len(session_dates),
                coverage_pct=row["coverage_pct"], coverage_status=row["coverage_status"],
            )
            summary["one_trade_per_day"] = cell_summary(
                rows=one_per_day_rows, universe=universe, total_sessions=len(session_dates),
                coverage_pct=row["coverage_pct"], coverage_status=row["coverage_status"],
            )
            summary["one_trade_per_day"]["rows"] = one_per_day_rows
            summary["rows"] = all_rows
            cells[f"{config_label}_{view}"] = summary
            print(
                f"[{period}] {config_label}/{view}: candidates={len(evaluation.candidate_signals)} "
                f"closed={summary['trade_quality']['closed_trades']}"
            )
    return cells


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true", help="allow a network fetch for cache gaps (requires Alpaca credentials); default is cache-only, fails closed on any miss")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    # Phase 1: profiles/native-universe only, one period's bars in memory at a time.
    profiles_by_period: dict[str, dict] = {}
    for period in PERIOD_DEFINITIONS:
        profiles_by_period[period] = asyncio.run(_load_profiles(
            period=period, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
        ))

    session_dates_by_period = {p: profiles_by_period[p]["session_dates"] for p in PERIOD_DEFINITIONS}
    seen: set = set()
    for dates in session_dates_by_period.values():
        overlap = seen & set(dates)
        if overlap:
            raise SystemExit(f"periods overlap: {sorted(overlap)[:5]}")
        seen |= set(dates)
    print("period non-overlap confirmed")

    cohort = common_cohort({p: profiles_by_period[p]["profiles"] for p in PERIOD_DEFINITIONS})
    print(f"common cohort ({len(cohort)} symbols): {cohort}")

    # Phase 2: reload each period's bars in turn to evaluate; only one
    # period's raw bars/inputs table are held in memory at any point.
    cells_by_period: dict[str, dict] = {}
    for period in PERIOD_DEFINITIONS:
        cells_by_period[period] = asyncio.run(_evaluate_period(
            period=period, session_dates=session_dates_by_period[period],
            native_universe=profiles_by_period[period]["native_universe"], cohort=cohort,
            cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
            implementation_digest=implementation_digest, created_at=created_at,
        ))

    # ---- Quarterly stability (C_BASE, NATIVE, ALL_CANDIDATES, combined chronologically) ----
    combined_native_c_base_rows: list = []
    for period in PERIOD_DEFINITIONS:
        cell = cells_by_period[period].get("C_BASE_NATIVE", {})
        combined_native_c_base_rows.extend(cell.get("rows", []))
    quarterly = quarterly_stability(combined_native_c_base_rows)

    # ---- Universe effect decomposition (C_BASE, per period) ----
    decomposition = {}
    for period in PERIOD_DEFINITIONS:
        native_cell = cells_by_period[period].get("C_BASE_NATIVE", {})
        cohort_cell = cells_by_period[period].get("C_BASE_COHORT", {})
        native_rows = native_cell.get("rows", [])
        native_tq = native_cell.get("trade_quality", {})
        cohort_tq = cohort_cell.get("trade_quality") if not cohort_cell.get("insufficient_sample") else None
        best_symbol = best_contributing_symbol(native_rows) if native_rows else None
        excl_tsla = excluding_symbol(native_rows, symbol="TSLA") if native_rows else None
        excl_best = excluding_symbol(native_rows, symbol=best_symbol) if best_symbol else None
        decomposition[period] = {
            "native": {"universe_size": native_cell.get("universe_size"), "trade_quality": native_tq},
            "cohort": {
                "universe_size": cohort_cell.get("universe_size"),
                "trade_quality": cohort_tq, "insufficient_sample": bool(cohort_cell.get("insufficient_sample")),
            },
            "expectancy_delta_native_minus_cohort": (
                (native_tq.get("expectancy_per_trade") or 0) - (cohort_tq.get("expectancy_per_trade") or 0)
                if cohort_tq else None
            ),
            "best_contributing_symbol": best_symbol,
            "excluding_tsla": excl_tsla,
            "excluding_best_symbol": excl_best,
        }

    single_symbol_dependence_by_period = {
        period: (decomposition[period]["excluding_best_symbol"] or {}).get("expectancy_per_trade") is not None
        and (decomposition[period]["excluding_best_symbol"]["expectancy_per_trade"] or 0) <= 0
        and (decomposition[period]["native"]["trade_quality"].get("expectancy_per_trade") or 0) > 0
        for period in PERIOD_DEFINITIONS
    }

    # ---- MFE analysis (C_BASE, NATIVE, ALL_CANDIDATES, combined) ----
    mfe = mfe_analysis(combined_native_c_base_rows)
    mfe_meaningful = meaningful_mfe_fraction(mfe)

    # ---- Combined full-history view (C_BASE, NATIVE) ----
    combined_one_per_day_rows: list = []
    for period in PERIOD_DEFINITIONS:
        cell = cells_by_period[period].get("C_BASE_NATIVE", {})
        combined_one_per_day_rows.extend(cell.get("one_trade_per_day", {}).get("rows") or [])
    combined_full_history = {
        "ALL_CANDIDATES": trade_quality_from_rows(combined_native_c_base_rows),
        "ONE_TRADE_PER_DAY": trade_quality_from_rows(combined_one_per_day_rows),
        "excluding_TSLA": excluding_symbol(combined_native_c_base_rows, symbol="TSLA"),
        "excluding_best_symbol": (
            excluding_symbol(combined_native_c_base_rows, symbol=best_contributing_symbol(combined_native_c_base_rows))
            if combined_native_c_base_rows else None
        ),
    }

    # ---- Stability classification ----
    native_tq_by_period = {p: cells_by_period[p].get("C_BASE_NATIVE", {}).get("trade_quality", {}) for p in PERIOD_DEFINITIONS}
    cohort_tq_by_period = {
        p: cells_by_period[p].get("C_BASE_COHORT", {}).get("trade_quality", {})
        for p in PERIOD_DEFINITIONS if not cells_by_period[p].get("C_BASE_COHORT", {}).get("insufficient_sample")
    }
    classification = classify_stability(
        native_trade_quality_by_period=native_tq_by_period, cohort_trade_quality_by_period=cohort_tq_by_period,
        single_symbol_dependence_by_period=single_symbol_dependence_by_period,
    )
    decision = recommend_next_experiment(
        stability_verdict=classification["verdict"], meaningful_mfe_fraction=mfe_meaningful,
    )

    print(f"STABILITY CLASSIFICATION: {classification['verdict']}")
    print(f"NEXT EXPERIMENT: {decision['recommendation']}")

    # ---- Assemble report ----
    def _strip_rows(cells: dict) -> dict:
        out = {}
        for key, cell in cells.items():
            cell = {k: v for k, v in cell.items() if k != "rows"}
            if "one_trade_per_day" in cell:
                cell["one_trade_per_day"] = {
                    k: v for k, v in cell["one_trade_per_day"].items() if k != "rows"
                }
            out[key] = cell
        return out

    report = {
        "issue": 319,
        "periods": {
            p: {"first_session": session_dates_by_period[p][0].isoformat(),
                "last_session": session_dates_by_period[p][-1].isoformat(),
                "sessions": len(session_dates_by_period[p]),
                "native_universe": list(profiles_by_period[p]["native_universe"])}
            for p in PERIOD_DEFINITIONS
        },
        "common_cohort": list(cohort),
        "cells_by_period": {p: _strip_rows(cells_by_period[p]) for p in PERIOD_DEFINITIONS},
        "quarterly_stability": quarterly,
        "universe_effect_decomposition": decomposition,
        "single_symbol_dependence_by_period": single_symbol_dependence_by_period,
        "mfe_analysis": mfe, "mfe_meaningful_fraction": mfe_meaningful,
        "meaningful_mfe_threshold_r": str(MEANINGFUL_MFE_THRESHOLD_R),
        "combined_full_history": combined_full_history,
        "stability_classification": classification,
        "next_experiment_decision": decision,
        "implementation_digest": implementation_digest,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_baseline_stability_decomposition_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    period_rows = []
    for period in PERIOD_DEFINITIONS:
        for cell_key, cell in cells_by_period[period].items():
            if cell.get("insufficient_sample"):
                continue
            tq = cell["trade_quality"]
            period_rows.append({
                "period": period, "cell": cell_key, "universe_size": cell["universe_size"],
                "candidate_days": cell["candidate_days"], "candidate_day_rate": cell["candidate_day_rate"],
                "candidate_occurrences": cell["candidate_occurrences"], "closed_trades": tq["closed_trades"],
                "win_rate": tq["win_rate"], "expectancy_per_trade": tq["expectancy_per_trade"],
                "profit_factor": tq["profit_factor"], "cumulative_r": tq["cumulative_r"],
                "stop_out_rate": tq["stop_out_rate"],
            })
    with (args.output_dir / "orb_baseline_stability_period_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(period_rows[0].keys()) if period_rows else ["period", "cell"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(period_rows)

    cohort_rows = []
    for period in PERIOD_DEFINITIONS:
        entry = decomposition[period]
        cohort_rows.append({
            "period": period, "native_universe_size": entry["native"]["universe_size"],
            "native_expectancy": entry["native"]["trade_quality"].get("expectancy_per_trade"),
            "native_pf": entry["native"]["trade_quality"].get("profit_factor"),
            "cohort_universe_size": entry["cohort"]["universe_size"],
            "cohort_expectancy": (entry["cohort"]["trade_quality"] or {}).get("expectancy_per_trade"),
            "cohort_pf": (entry["cohort"]["trade_quality"] or {}).get("profit_factor"),
            "cohort_insufficient_sample": entry["cohort"]["insufficient_sample"],
            "expectancy_delta_native_minus_cohort": entry["expectancy_delta_native_minus_cohort"],
        })
    with (args.output_dir / "orb_baseline_stability_common_cohort_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(cohort_rows[0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(cohort_rows)

    quarterly_rows = []
    for quarter, stats in quarterly["quarters"].items():
        quarterly_rows.append({"quarter": quarter, **{k: v for k, v in stats.items()}})
    with (args.output_dir / "orb_baseline_stability_quarterly.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in quarterly_rows for key in row}) if quarterly_rows else ["quarter"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(quarterly_rows)

    symbol_rows = []
    for period in PERIOD_DEFINITIONS:
        native_cell = cells_by_period[period].get("C_BASE_NATIVE", {})
        for symbol, stats in native_cell.get("symbol_contribution", {}).get("per_symbol", {}).items():
            symbol_rows.append({"period": period, "symbol": symbol, **stats})
    with (args.output_dir / "orb_baseline_stability_symbol_contribution.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in symbol_rows for key in row}) if symbol_rows else ["period", "symbol"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(symbol_rows)

    mfe_summary_rows = [{
        "metric": "closed_trades", "value": mfe["closed_trades"],
    }, {
        "metric": "winners", "value": mfe["winners"],
    }, {
        "metric": "losers", "value": mfe["losers"],
    }]
    for label, entry in mfe["losers_reaching_threshold_before_reversing"].items():
        mfe_summary_rows.append({"metric": f"losers_reaching_{label}_fraction", "value": entry["fraction"]})
    with (args.output_dir / "orb_baseline_stability_mfe_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(mfe_summary_rows)

    print(f"JSON report: {args.output_dir / 'orb_baseline_stability_decomposition_report.json'}")
    print(f"Period comparison CSV: {args.output_dir / 'orb_baseline_stability_period_comparison.csv'} ({len(period_rows)} rows)")
    print(f"Cohort comparison CSV: {args.output_dir / 'orb_baseline_stability_common_cohort_comparison.csv'} ({len(cohort_rows)} rows)")
    print(f"Quarterly CSV: {args.output_dir / 'orb_baseline_stability_quarterly.csv'} ({len(quarterly_rows)} rows)")
    print(f"Symbol contribution CSV: {args.output_dir / 'orb_baseline_stability_symbol_contribution.csv'} ({len(symbol_rows)} rows)")
    print(f"MFE summary CSV: {args.output_dir / 'orb_baseline_stability_mfe_summary.csv'} ({len(mfe_summary_rows)} rows)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except UniverseResearchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
