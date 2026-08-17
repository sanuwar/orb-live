"""CLI entry point for the ORB C_BASE fourth-period baseline validation
study (Issue #321).

Research-only, validation (not optimization). Issue #319/PR #320
classified C_BASE (gap>=0.50%/RVOL>=1.50x) as REGIME_DEPENDENT_EDGE
across three periods. This script adds exactly ONE further independent
period, P0 -- the ~252 sessions immediately preceding P1 -- and combines
it with P1/P2/P3's already-computed, merged #320 results (never
recomputed from bars here; see `src/orb_baseline_fourth_period_research.py`
for the exact algebraic recombination method and the hardcoded P1-P3
reference data) to ask whether C_BASE retains enough underlying edge
across a longer history to justify the queued profit-protection/
adaptive-stop experiment.

Only P0 is fetched/precomputed/simulated here. Never writes
`config/strategy_v0.yaml`, never sets `strategy.enabled`, never calls a
broker, never implements a trailing stop or changes the simulator.

`--cache-only` (default here): reuses the existing shared on-disk bar
cache; fails closed on any cache miss rather than fabricate data. Pass
`--fetch-missing` to allow a network fetch for cache gaps (requires
`ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`, read from the environment
only, never printed).
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
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_baseline_fourth_period_research import (
    ANCHORED_COHORT,
    P0_CONFIG,
    PRIOR_PERIODS_REFERENCE,
    BaselineFourthPeriodError,
    classify_baseline_conclusion,
    combine_trade_quality_aggregates,
    derive_freshness_policy_for_window,
    four_period_evidence_is_strong_enough,
    is_p0_catastrophically_unstable,
    max_drawdown_and_streak_from_rows,
    merge_quarterly_reference,
    recommend_next_milestone,
    symbol_total_pnl,
    trade_quality_excluding_symbol_from_aggregate,
)
from src.orb_baseline_stability_research import (
    best_contributing_symbol,
    build_stability_rows,
    cell_summary,
    excluding_symbol,
    meaningful_mfe_fraction,
    mfe_analysis,
)
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, matrix_row, precompute_inputs_table
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import symbol_summary
from src.orb_research_historical_calendar import (
    ResearchCalendarError,
    research_regular_session_bounds,
    research_trading_sessions_before,
)
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

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-baseline-fourth-period" / "results"

#: P0 ends exactly where P1 (Issue #317) begins -- zero gap, zero overlap.
P0_REFERENCE_DAY = date(2024, 4, 3)
P0_SESSIONS_PREFERRED = 252
P0_SESSIONS_FALLBACK = 180
P0_SESSIONS_MINIMUM = 120
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


def _select_p0_dates() -> tuple[date, ...]:
    for count in (P0_SESSIONS_PREFERRED, P0_SESSIONS_FALLBACK, P0_SESSIONS_MINIMUM):
        try:
            dates = research_trading_sessions_before(P0_REFERENCE_DAY, count=count)
        except ResearchCalendarError:
            continue
        print(f"P0: {count} sessions requested, calendar coverage sufficient -> using {count}")
        return dates
    raise SystemExit(
        f"REFUSED: research calendar cannot support even the {P0_SESSIONS_MINIMUM}-session "
        "absolute minimum ending before " + P0_REFERENCE_DAY.isoformat()
    )


async def _fetch_p0_bars(*, session_dates: tuple[date, ...], cache_dir: Path, fetch_missing: bool, feed: str, freshness_policy) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    fetch_from = research_trading_sessions_before(session_dates[0], count=lookback_days)[0]
    resolver = partial(session_open_for, session_bounds_provider=research_regular_session_bounds)

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
        market_bars_by_symbol[symbol] = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy,
            session_bounds_provider=research_regular_session_bounds,
        )
    return market_bars_by_symbol


def _liquidity_profiles(*, market_bars_by_symbol: dict[str, list], session_dates: tuple[date, ...]) -> dict[str, object]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    pool_symbols = list(all_pool_symbols_with_production())
    resolver_kwargs = {"session_open_resolver": partial(session_open_for, session_bounds_provider=research_regular_session_bounds)}
    return {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=[b.to_minute_bar() for b in market_bars_by_symbol[symbol]],
            session_dates=session_dates, lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
            **resolver_kwargs,
        )
        for symbol in pool_symbols
    }


def _build_view_cell(
    *, view: str, universe: tuple[str, ...], session_dates: tuple[date, ...], inputs_table, market_bars_by_symbol,
    implementation_digest: str, created_at: datetime,
) -> dict:
    evaluation = evaluate_combo(
        combo=P0_CONFIG, universe=universe, session_dates=session_dates,
        inputs_table=inputs_table, base_config=load_config(),
    )
    row = matrix_row(evaluation)
    all_rows = build_stability_rows(
        period="P0", view=view, config_label="C_BASE", combo=P0_CONFIG,
        candidate_signals=evaluation.candidate_signals, market_bars_by_symbol=market_bars_by_symbol,
        base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=research_regular_session_bounds,
    )
    one_per_day_signals = select_first_candidate_per_day(evaluation.candidate_signals)
    one_per_day_rows = build_stability_rows(
        period="P0", view=view, config_label="C_BASE", combo=P0_CONFIG,
        candidate_signals=one_per_day_signals, market_bars_by_symbol=market_bars_by_symbol,
        base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=research_regular_session_bounds,
    )
    summary = cell_summary(
        rows=all_rows, universe=universe, total_sessions=len(session_dates),
        coverage_pct=row["coverage_pct"], coverage_status=row["coverage_status"],
    )
    summary["one_trade_per_day"] = cell_summary(
        rows=one_per_day_rows, universe=universe, total_sessions=len(session_dates),
        coverage_pct=row["coverage_pct"], coverage_status=row["coverage_status"],
    )
    drawdown = max_drawdown_and_streak_from_rows(all_rows)
    summary["maximum_drawdown"] = drawdown["maximum_drawdown"]
    summary["longest_losing_streak"] = drawdown["longest_losing_streak"]
    summary["one_trade_per_day"]["maximum_drawdown"] = max_drawdown_and_streak_from_rows(one_per_day_rows)["maximum_drawdown"]
    summary["rows"] = all_rows
    summary["one_trade_per_day_rows"] = one_per_day_rows
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true", help="allow a network fetch for cache gaps (requires Alpaca credentials); default is cache-only, fails closed on any miss")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    empirical_start = datetime.now(timezone.utc)

    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    p0_dates = _select_p0_dates()

    # Non-overlap check: only calendar dates are generated for P1-P3 here (no bars touched).
    p1_dates = set(research_trading_sessions_before(date(2025, 4, 3), count=252))
    p2_dates = set(research_trading_sessions_before(date(2026, 4, 7), count=252))
    p3_dates = set(trading_sessions_before(date(2026, 8, 14), count=90))
    for label, other in (("P1", p1_dates), ("P2", p2_dates), ("P3", p3_dates)):
        overlap = set(p0_dates) & other
        if overlap:
            raise SystemExit(f"P0 overlaps {label}: {sorted(overlap)[:5]}")

    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    lookback_start = research_trading_sessions_before(p0_dates[0], count=lookback_days)[0]
    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=lookback_start, now=empirical_start, safety_margin_days=30,
        version="orb-baseline-fourth-period-v1",
    )

    pool_size = len(list(all_pool_symbols_with_production()))
    print(f"P0 dates: {p0_dates[0]}..{p0_dates[-1]} ({len(p0_dates)} sessions), zero overlap with P1/P2/P3 confirmed")
    print(f"P0 lookback start (RVOL {lookback_days}d): {lookback_start}")
    print(f"universe candidate pool size: {pool_size} symbols (+ SPY)")
    print(f"expected data-fetch range: {lookback_start} .. {p0_dates[-1]}")
    print(
        f"freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} "
        f"({freshness_policy.max_age_seconds / 86400.0:.1f} days) -- covers lookback_start age "
        f"({(empirical_start.date() - lookback_start).days} days) with margin"
    )

    print("Fetching/loading P0 bars...")
    market_bars_by_symbol = asyncio.run(_fetch_p0_bars(
        session_dates=p0_dates, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing,
        feed=args.feed, freshness_policy=freshness_policy,
    ))
    profiles = _liquidity_profiles(market_bars_by_symbol=market_bars_by_symbol, session_dates=p0_dates)
    native_universe = rank_liquidity_qualified_symbols(profiles)
    print(f"P0 native universe: {len(native_universe)} symbols -> {native_universe}")

    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver_kwargs = {"session_open_resolver": partial(session_open_for, session_bounds_provider=research_regular_session_bounds)}
    universe_for_precompute = tuple(sorted(set(native_universe) | set(ANCHORED_COHORT)))
    inputs_table = precompute_inputs_table(
        universe=universe_for_precompute, session_dates=p0_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(), **resolver_kwargs,
    )

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    cells = {}
    for view, universe in (("NATIVE", native_universe), ("ANCHORED_COHORT", ANCHORED_COHORT)):
        cells[view] = _build_view_cell(
            view=view, universe=universe, session_dates=p0_dates, inputs_table=inputs_table,
            market_bars_by_symbol=market_bars_by_symbol, implementation_digest=implementation_digest,
            created_at=created_at,
        )
        tq = cells[view]["trade_quality"]
        print(f"P0 {view}: candidates={cells[view]['candidate_occurrences']} closed={tq['closed_trades']} expectancy={tq['expectancy_per_trade']}")

    empirical_elapsed_seconds = (datetime.now(timezone.utc) - empirical_start).total_seconds()

    p0_native_rows = cells["NATIVE"]["rows"]
    p0_native_tq = cells["NATIVE"]["trade_quality"]
    p0_mfe = mfe_analysis(p0_native_rows)
    p0_mfe_meaningful = meaningful_mfe_fraction(p0_mfe)
    p0_catastrophic = is_p0_catastrophically_unstable(p0_native_tq)

    # ---- Four-period table (native + cohort + one-trade-per-day) ----
    four_period_rows = []
    for label, ref in (("P0", None), *((p, PRIOR_PERIODS_REFERENCE[p]) for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY"))):
        if label == "P0":
            for view_key, view_label in (("NATIVE", "native"), ("ANCHORED_COHORT", "anchored_cohort")):
                for selection, tq, dd in (
                    ("all_candidates", cells[view_key]["trade_quality"], cells[view_key]["maximum_drawdown"]),
                    ("one_trade_per_day", cells[view_key]["one_trade_per_day"]["trade_quality"], cells[view_key]["one_trade_per_day"]["maximum_drawdown"]),
                ):
                    four_period_rows.append({
                        "period": "P0", "view": view_label, "selection": selection,
                        "closed_trades": tq["closed_trades"], "expectancy_per_trade": tq["expectancy_per_trade"],
                        "profit_factor": tq["profit_factor"], "win_rate": tq["win_rate"],
                        "cumulative_r": tq["cumulative_r"], "maximum_drawdown": dd,
                    })
        else:
            for view_key, tq_key, otpd_key in (("native", "native", "native_one_trade_per_day"), ("cohort", "cohort", "cohort_one_trade_per_day")):
                for selection, key in (("all_candidates", tq_key), ("one_trade_per_day", otpd_key)):
                    tq = ref[key]
                    four_period_rows.append({
                        "period": label, "view": view_key, "selection": selection,
                        "closed_trades": tq["closed_trades"], "expectancy_per_trade": tq["expectancy_per_trade"],
                        "profit_factor": tq["profit_factor"], "win_rate": tq["win_rate"],
                        "cumulative_r": tq["cumulative_r"], "maximum_drawdown": "unavailable (reused from #320 aggregates)",
                    })

    # ---- Combined full-history view (native, all 4 periods) ----
    period_native_all = [p0_native_tq] + [PRIOR_PERIODS_REFERENCE[p]["native"] for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")]
    combined_all_candidates = combine_trade_quality_aggregates(period_native_all)

    period_one_per_day = [cells["NATIVE"]["one_trade_per_day"]["trade_quality"]] + [
        PRIOR_PERIODS_REFERENCE[p]["native_one_trade_per_day"] for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")
    ]
    combined_one_trade_per_day = combine_trade_quality_aggregates(period_one_per_day)

    # excluding TSLA
    p0_excl_tsla = excluding_symbol(p0_native_rows, symbol="TSLA")
    prior_excl_tsla = [
        trade_quality_excluding_symbol_from_aggregate(
            PRIOR_PERIODS_REFERENCE[p]["native"], PRIOR_PERIODS_REFERENCE[p]["symbol_contribution"].get("TSLA"),
        )
        for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")
    ]
    combined_excluding_tsla = combine_trade_quality_aggregates([p0_excl_tsla] + prior_excl_tsla)

    # excluding the single best contributor across all four periods combined
    p0_symbol_totals = {
        symbol: symbol_total_pnl(stats)
        for symbol, stats in symbol_summary(p0_native_rows)["per_symbol"].items()
    }
    combined_symbol_totals: dict[str, float] = dict(p0_symbol_totals)
    for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY"):
        for symbol, stats in PRIOR_PERIODS_REFERENCE[p]["symbol_contribution"].items():
            combined_symbol_totals[symbol] = combined_symbol_totals.get(symbol, 0.0) + symbol_total_pnl(stats)
    overall_best_symbol = max(combined_symbol_totals, key=lambda s: combined_symbol_totals[s]) if combined_symbol_totals else None

    p0_excl_best = excluding_symbol(p0_native_rows, symbol=overall_best_symbol) if overall_best_symbol else p0_native_tq
    prior_excl_best = [
        trade_quality_excluding_symbol_from_aggregate(
            PRIOR_PERIODS_REFERENCE[p]["native"], PRIOR_PERIODS_REFERENCE[p]["symbol_contribution"].get(overall_best_symbol),
        )
        for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")
    ]
    combined_excluding_best_symbol = combine_trade_quality_aggregates([p0_excl_best] + prior_excl_best)

    # ---- Quarterly (merged) ----
    quarterly = merge_quarterly_reference(p0_native_rows)

    # ---- Fraction of adequately sampled PERIODS (not quarters) ----
    period_native_expectancy = {
        "P0": p0_native_tq.get("expectancy_per_trade"),
        "P1_THIRD": PRIOR_PERIODS_REFERENCE["P1_THIRD"]["native"]["expectancy_per_trade"],
        "P2_OOS": PRIOR_PERIODS_REFERENCE["P2_OOS"]["native"]["expectancy_per_trade"],
        "P3_DISCOVERY": PRIOR_PERIODS_REFERENCE["P3_DISCOVERY"]["native"]["expectancy_per_trade"],
    }
    period_native_pf = {
        "P0": p0_native_tq.get("profit_factor"),
        "P1_THIRD": PRIOR_PERIODS_REFERENCE["P1_THIRD"]["native"]["profit_factor"],
        "P2_OOS": PRIOR_PERIODS_REFERENCE["P2_OOS"]["native"]["profit_factor"],
        "P3_DISCOVERY": PRIOR_PERIODS_REFERENCE["P3_DISCOVERY"]["native"]["profit_factor"],
    }
    fraction_periods_positive_expectancy = sum(1 for e in period_native_expectancy.values() if (e or 0) > 0) / 4
    fraction_periods_pf_above_1 = sum(1 for pf in period_native_pf.values() if (pf or 0) > 1) / 4

    # ---- Predeclared decision ----
    classification = classify_baseline_conclusion(
        combined_all_candidates=combined_all_candidates, combined_one_trade_per_day=combined_one_trade_per_day,
        combined_excluding_best_symbol=combined_excluding_best_symbol, period_native_expectancy=period_native_expectancy,
        p0_catastrophic_instability=p0_catastrophic, p0_meaningful_mfe_fraction=p0_mfe_meaningful,
    )
    strong_enough = four_period_evidence_is_strong_enough(combined_all_candidates)
    milestone = recommend_next_milestone(baseline_verdict=classification["verdict"], four_period_evidence_strong_enough=strong_enough)

    print(f"P0 catastrophic instability: {p0_catastrophic}")
    print(f"P0 meaningful MFE fraction (>=30% at +0.50R): {p0_mfe_meaningful}")
    print(f"BASELINE CONCLUSION: {classification['verdict']}")
    print(f"NEXT MILESTONE: {milestone['next_milestone']}")
    print(f"Empirical P0 step runtime: {empirical_elapsed_seconds:.1f}s")

    def _strip(cell: dict) -> dict:
        out = {k: v for k, v in cell.items() if k not in ("rows", "one_trade_per_day_rows")}
        if "one_trade_per_day" in out:
            out["one_trade_per_day"] = {k: v for k, v in out["one_trade_per_day"].items() if k not in ("rows",)}
        return out

    report = {
        "issue": 321,
        "p0": {
            "first_session": p0_dates[0].isoformat(), "last_session": p0_dates[-1].isoformat(),
            "sessions": len(p0_dates), "native_universe": list(native_universe),
            "anchored_cohort": list(ANCHORED_COHORT), "cells": {v: _strip(c) for v, c in cells.items()},
            "mfe_analysis": p0_mfe, "meaningful_mfe_fraction": p0_mfe_meaningful,
            "catastrophic_instability": p0_catastrophic,
        },
        "prior_periods_reused": {p: PRIOR_PERIODS_REFERENCE[p]["native"] for p in ("P1_THIRD", "P2_OOS", "P3_DISCOVERY")},
        "four_period_table": four_period_rows,
        "combined_full_history": {
            "ALL_CANDIDATES": combined_all_candidates, "ONE_TRADE_PER_DAY": combined_one_trade_per_day,
            "excluding_TSLA": combined_excluding_tsla, "excluding_best_symbol": combined_excluding_best_symbol,
            "overall_best_contributing_symbol": overall_best_symbol,
        },
        "fraction_periods_positive_expectancy": fraction_periods_positive_expectancy,
        "fraction_periods_pf_above_1": fraction_periods_pf_above_1,
        "quarterly_stability": quarterly,
        "baseline_classification": classification,
        "next_milestone": milestone,
        "four_period_evidence_strong_enough": strong_enough,
        "empirical_p0_runtime_seconds": empirical_elapsed_seconds,
        "implementation_digest": implementation_digest,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_baseline_fourth_period_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    (args.output_dir / "orb_baseline_fourth_period_combined_summary.json").write_text(
        json.dumps(report["combined_full_history"], indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    with (args.output_dir / "orb_baseline_fourth_period_p0_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        p0_rows_only = [r for r in four_period_rows if r["period"] == "P0"]
        fieldnames = list(p0_rows_only[0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(p0_rows_only)

    symbol_rows = []
    for symbol, stats in symbol_summary(p0_native_rows)["per_symbol"].items():
        symbol_rows.append({"period": "P0", "symbol": symbol, **stats})
    with (args.output_dir / "orb_baseline_fourth_period_symbols.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in symbol_rows for key in row}) if symbol_rows else ["period", "symbol"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(symbol_rows)

    mfe_rows = [{"metric": "closed_trades", "value": p0_mfe["closed_trades"]}, {"metric": "winners", "value": p0_mfe["winners"]}, {"metric": "losers", "value": p0_mfe["losers"]}]
    for label, entry in p0_mfe["losers_reaching_threshold_before_reversing"].items():
        mfe_rows.append({"metric": f"losers_reaching_{label}_fraction", "value": entry["fraction"]})
    with (args.output_dir / "orb_baseline_fourth_period_mfe_summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "value"])
        writer.writeheader()
        writer.writerows(mfe_rows)

    with (args.output_dir / "orb_baseline_fourth_period_four_period_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(four_period_rows[0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(four_period_rows)

    print(f"JSON report: {args.output_dir / 'orb_baseline_fourth_period_report.json'}")
    print(f"Four-period comparison CSV: {args.output_dir / 'orb_baseline_fourth_period_four_period_comparison.csv'} ({len(four_period_rows)} rows)")
    print(f"P0 matrix CSV: {args.output_dir / 'orb_baseline_fourth_period_p0_matrix.csv'}")
    print(f"Symbol CSV: {args.output_dir / 'orb_baseline_fourth_period_symbols.csv'} ({len(symbol_rows)} rows)")
    print(f"MFE summary CSV: {args.output_dir / 'orb_baseline_fourth_period_mfe_summary.csv'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (UniverseResearchError, BaselineFourthPeriodError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
