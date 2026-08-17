"""CLI entry point for the existing-configuration R-normalized
re-evaluation study (Issue #325).

Research-only, re-ranking of ALREADY-tested configurations -- NOT a new
parameter search. Evaluates the frozen 18-configuration set
(`src.orb_existing_configs_reevaluation_research.FROZEN_CONFIG_SET`, the
union of Issue #300's and Issue #302's grids) across the four
already-established periods (P0-P3), using R-normalized metrics
(`expectancy_r_per_trade`/`profit_factor_r`, PR #322) as primary. Each
period's own already-established liquidity-qualified universe is reused
unchanged; only that period's own native-universe symbols (+ SPY) are
fetched, and precompute runs exactly once per period regardless of how
many of the 18 configurations are evaluated against it. Never writes
`config/strategy_v0.yaml`, never sets `strategy.enabled`, never calls a
broker, never changes entry/exit/stop/target/SPY/timing logic.

`--cache-only` (default here): reuses the existing shared on-disk bar
cache; fails closed on any cache miss. Pass `--fetch-missing` to allow a
network fetch for cache gaps (requires `ALPACA_API_KEY_ID`/
`ALPACA_API_SECRET_KEY`, read from the environment only, never printed).
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
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
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window, max_drawdown_and_streak_from_rows
from src.orb_baseline_stability_research import build_stability_rows, excluding_symbol
from src.orb_existing_configs_reevaluation_research import (
    DEFAULT_SELECTION_CRITERIA,
    FROZEN_CONFIG_SET,
    PERIOD_NATIVE_UNIVERSES,
    PERIOD_SESSION_SPEC,
    PERIODS,
    best_contributing_symbol_by_r,
    classify_configuration,
    combined_trade_quality,
    recommend_next_milestone,
)
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, matrix_row, precompute_inputs_table
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import symbol_summary, trade_quality_from_rows
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    UniverseResearchError,
    eligible_market_bars,
    fetch_full_minute_history,
    trading_sessions_before,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-existing-configs-reevaluation" / "results"
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


def _session_dates_for(period: str) -> tuple:
    reference_day, count, uses_research_calendar = PERIOD_SESSION_SPEC[period]
    if uses_research_calendar:
        return research_trading_sessions_before(reference_day, count=count)
    return trading_sessions_before(reference_day, count=count)


def _session_bounds_provider_for(period: str):
    _, _, uses_research_calendar = PERIOD_SESSION_SPEC[period]
    return research_regular_session_bounds if uses_research_calendar else None


async def _fetch_period_bars(*, period: str, session_dates: tuple, cache_dir: Path, fetch_missing: bool, feed: str) -> dict[str, list]:
    universe = PERIOD_NATIVE_UNIVERSES[period]
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    session_bounds_provider = _session_bounds_provider_for(period)
    if session_bounds_provider is None:
        fetch_from = trading_sessions_before(session_dates[0], count=lookback_days)[0]
        resolver = session_open_for
    else:
        fetch_from = research_trading_sessions_before(session_dates[0], count=lookback_days)[0]
        resolver = partial(session_open_for, session_bounds_provider=session_bounds_provider)

    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=fetch_from, now=datetime.now(timezone.utc), safety_margin_days=30,
        version=f"orb-existing-configs-reeval-{period}-v1",
    )
    print(f"[{period}] freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} ({freshness_policy.max_age_seconds / 86400.0:.1f} days)")

    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")
        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(session_dates[-1], resolver(session_dates[-1]), tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

    symbols = list(universe) + ["SPY"]
    market_bars_by_symbol: dict[str, list] = {}
    for symbol in symbols:
        raw_bars = await fetch_full_minute_history(transport, credentials=credentials, symbol=symbol, start=start, end=end, now=now, feed=feed)
        kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
        market_bars_by_symbol[symbol] = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy, **kwargs,
        )
    return market_bars_by_symbol


async def _evaluate_period(*, period: str, cache_dir: Path, fetch_missing: bool, feed: str, implementation_digest: str, created_at: datetime) -> dict:
    session_dates = _session_dates_for(period)
    universe = PERIOD_NATIVE_UNIVERSES[period]
    print(f"[{period}] {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]}), reused universe: {universe}")

    market_bars_by_symbol = await _fetch_period_bars(period=period, session_dates=session_dates, cache_dir=cache_dir, fetch_missing=fetch_missing, feed=feed)
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    session_bounds_provider = _session_bounds_provider_for(period)
    resolver_kwargs = {} if session_bounds_provider is None else {
        "session_open_resolver": partial(session_open_for, session_bounds_provider=session_bounds_provider)
    }
    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(), **resolver_kwargs,
    )

    cells: dict[str, dict] = {}
    for label, entry in FROZEN_CONFIG_SET.items():
        evaluation = evaluate_combo(combo=entry.combo, universe=universe, session_dates=session_dates, inputs_table=inputs_table, base_config=load_config())
        row = matrix_row(evaluation)
        all_rows = build_stability_rows(
            period=period, view="NATIVE", config_label=label, combo=entry.combo,
            candidate_signals=evaluation.candidate_signals, market_bars_by_symbol=market_bars_by_symbol,
            base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=session_bounds_provider,
        )
        one_per_day_signals = select_first_candidate_per_day(evaluation.candidate_signals)
        one_per_day_rows = build_stability_rows(
            period=period, view="NATIVE", config_label=label, combo=entry.combo,
            candidate_signals=one_per_day_signals, market_bars_by_symbol=market_bars_by_symbol,
            base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=session_bounds_provider,
        )
        all_tq = trade_quality_from_rows(all_rows)
        one_tq = trade_quality_from_rows(one_per_day_rows)
        all_dd = max_drawdown_and_streak_from_rows(all_rows)
        one_dd = max_drawdown_and_streak_from_rows(one_per_day_rows)
        cells[label] = {
            "candidate_days": row["sessions_with_candidate"], "candidate_day_rate": row["candidate_day_rate"],
            "coverage_pct": row["coverage_pct"], "coverage_status": row["coverage_status"],
            "all_candidates": {"trade_quality": all_tq, "maximum_drawdown": all_dd["maximum_drawdown"], "longest_losing_streak": all_dd["longest_losing_streak"], "rows": all_rows},
            "one_trade_per_day": {"trade_quality": one_tq, "maximum_drawdown": one_dd["maximum_drawdown"], "longest_losing_streak": one_dd["longest_losing_streak"], "rows": one_per_day_rows},
        }
        print(f"[{period}] {label}: all_candidates closed={all_tq['closed_trades']} exp_r={all_tq['expectancy_r_per_trade']} | one_per_day closed={one_tq['closed_trades']} exp_r={one_tq['expectancy_r_per_trade']}")

    return {"period": period, "session_dates": session_dates, "universe": universe, "cells": cells}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print("Frozen configuration set (18, no new thresholds):")
    print(f"{'LABEL':<20}{'GAP':>8}{'RVOL':>8}  SOURCE STUDIES")
    for label, entry in FROZEN_CONFIG_SET.items():
        print(f"{label:<20}{str(entry.combo.gap_percent) + '%':>8}{str(entry.combo.rvol_multiple) + 'x':>8}  {', '.join(entry.source_studies)}")

    empirical_start = datetime.now(timezone.utc)
    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    period_results: dict[str, dict] = {}
    for period in PERIODS:
        period_results[period] = asyncio.run(_evaluate_period(
            period=period, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
            implementation_digest=implementation_digest, created_at=created_at,
        ))

    empirical_elapsed_seconds = (datetime.now(timezone.utc) - empirical_start).total_seconds()
    print(f"Empirical runtime (all periods, all 18 configs): {empirical_elapsed_seconds:.1f}s")

    # ---- Combine across periods, per config ----
    config_results: dict[str, dict] = {}
    for label in FROZEN_CONFIG_SET:
        all_rows_by_period = {p: period_results[p]["cells"][label]["all_candidates"]["rows"] for p in PERIODS}
        one_rows_by_period = {p: period_results[p]["cells"][label]["one_trade_per_day"]["rows"] for p in PERIODS}

        combined_all = combined_trade_quality(all_rows_by_period)
        combined_one = combined_trade_quality(one_rows_by_period)

        combined_all_rows = [r for rows in all_rows_by_period.values() for r in rows]
        combined_one_rows = [r for rows in one_rows_by_period.values() for r in rows]

        one_excl_tsla = excluding_symbol(combined_one_rows, symbol="TSLA")
        best_symbol_one = best_contributing_symbol_by_r(combined_one_rows)
        one_excl_best = excluding_symbol(combined_one_rows, symbol=best_symbol_one) if best_symbol_one else combined_one

        all_excl_tsla = excluding_symbol(combined_all_rows, symbol="TSLA")
        best_symbol_all = best_contributing_symbol_by_r(combined_all_rows)
        all_excl_best = excluding_symbol(combined_all_rows, symbol=best_symbol_all) if best_symbol_all else combined_all

        period_one_exp_r = {p: trade_quality_from_rows(one_rows_by_period[p]).get("expectancy_r_per_trade") for p in PERIODS}

        classification = classify_configuration(
            combined_one_per_day=combined_one, combined_all_candidates=combined_all,
            period_one_per_day_expectancy_r=period_one_exp_r, one_per_day_excluding_best_symbol=one_excl_best,
        )

        symbol_contrib_one = symbol_summary(combined_one_rows)
        top3_symbols = sorted(symbol_contrib_one["per_symbol"], key=lambda s: symbol_contrib_one["per_symbol"][s].get("cumulative_r") or 0, reverse=True)[:3]

        config_results[label] = {
            "combo": {"gap_percent": str(FROZEN_CONFIG_SET[label].combo.gap_percent), "rvol_multiple": str(FROZEN_CONFIG_SET[label].combo.rvol_multiple)},
            "source_studies": FROZEN_CONFIG_SET[label].source_studies, "prior_names": FROZEN_CONFIG_SET[label].prior_names,
            "combined_all_candidates": combined_all, "combined_one_trade_per_day": combined_one,
            "period_one_trade_per_day_expectancy_r": period_one_exp_r,
            "one_trade_per_day_excluding_tsla": one_excl_tsla, "one_trade_per_day_excluding_best_r_symbol": one_excl_best,
            "all_candidates_excluding_tsla": all_excl_tsla, "all_candidates_excluding_best_r_symbol": all_excl_best,
            "best_r_contributing_symbol_one_per_day": best_symbol_one, "best_r_contributing_symbol_all_candidates": best_symbol_all,
            "top3_r_symbols_one_per_day": top3_symbols,
            "classification": classification,
        }
        print(f"CONFIG {label}: verdict={classification['verdict']} one/day exp_r={classification['one_per_day_expectancy_r']} PF_r={classification['one_per_day_profit_factor_r']}")

    core_candidates = [l for l, r in config_results.items() if r["classification"]["verdict"] == "CORE_CANDIDATE"]
    promising = [l for l, r in config_results.items() if r["classification"]["verdict"] == "PROMISING_BUT_UNSTABLE"]
    decision = recommend_next_milestone(any_core_candidate=bool(core_candidates), any_promising_but_unstable=bool(promising))

    print(f"CORE_CANDIDATE configs: {core_candidates}")
    print(f"NEXT MILESTONE: {decision['next_milestone']}")

    report = {
        "issue": 325, "criteria": {
            "minimum_combined_one_per_day_closed_trades": DEFAULT_SELECTION_CRITERIA.minimum_combined_one_per_day_closed_trades,
            "all_candidates_not_materially_negative_threshold": DEFAULT_SELECTION_CRITERIA.all_candidates_not_materially_negative_threshold,
            "minimum_periods_non_negative_of_four": DEFAULT_SELECTION_CRITERIA.minimum_periods_non_negative_of_four,
        },
        "periods": {p: {"first_session": period_results[p]["session_dates"][0].isoformat(), "last_session": period_results[p]["session_dates"][-1].isoformat(), "sessions": len(period_results[p]["session_dates"]), "universe": list(period_results[p]["universe"])} for p in PERIODS},
        "configurations": config_results,
        "core_candidates": core_candidates, "promising_but_unstable": promising,
        "next_milestone": decision, "empirical_runtime_seconds": empirical_elapsed_seconds,
        "implementation_digest": implementation_digest,
    }

    def _strip(cells: dict) -> dict:
        out = {}
        for label, cell in cells.items():
            out[label] = {
                "candidate_days": cell["candidate_days"], "candidate_day_rate": cell["candidate_day_rate"],
                "coverage_pct": cell["coverage_pct"], "coverage_status": cell["coverage_status"],
                "all_candidates": {k: v for k, v in cell["all_candidates"].items() if k != "rows"},
                "one_trade_per_day": {k: v for k, v in cell["one_trade_per_day"].items() if k != "rows"},
            }
        return out

    report["cells_by_period"] = {p: _strip(period_results[p]["cells"]) for p in PERIODS}

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_existing_configs_reevaluation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    inventory_rows = [
        {"config_label": label, "gap_percent": str(entry.combo.gap_percent), "rvol_multiple": str(entry.combo.rvol_multiple), "source_studies": "; ".join(entry.source_studies), "prior_names": "; ".join(entry.prior_names)}
        for label, entry in FROZEN_CONFIG_SET.items()
    ]
    with (args.output_dir / "orb_existing_configs_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(inventory_rows[0].keys()))
        writer.writeheader()
        writer.writerows(inventory_rows)

    period_config_rows = []
    for period in PERIODS:
        for label in FROZEN_CONFIG_SET:
            cell = period_results[period]["cells"][label]
            for view in ("all_candidates", "one_trade_per_day"):
                tq = cell[view]["trade_quality"]
                period_config_rows.append({
                    "period": period, "config_label": label, "view": view,
                    "closed_trades": tq["closed_trades"], "win_rate": tq["win_rate"],
                    "expectancy_r_per_trade": tq["expectancy_r_per_trade"], "profit_factor_r": tq["profit_factor_r"],
                    "cumulative_r": tq["cumulative_r"], "maximum_drawdown": cell[view]["maximum_drawdown"],
                    "longest_losing_streak": cell[view]["longest_losing_streak"],
                    "expectancy_price_per_share": tq["expectancy_price_per_share"], "profit_factor_price_per_share": tq["profit_factor_price_per_share"],
                })
    with (args.output_dir / "orb_existing_configs_period_metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(period_config_rows[0].keys()))
        writer.writeheader()
        writer.writerows(period_config_rows)

    one_day_rows = [
        {
            "config_label": label, "verdict": r["classification"]["verdict"],
            "closed_trades": r["combined_one_trade_per_day"]["closed_trades"],
            "expectancy_r_per_trade": r["combined_one_trade_per_day"]["expectancy_r_per_trade"],
            "profit_factor_r": r["combined_one_trade_per_day"]["profit_factor_r"],
            "win_rate": r["combined_one_trade_per_day"]["win_rate"], "cumulative_r": r["combined_one_trade_per_day"]["cumulative_r"],
            "excluding_tsla_expectancy_r": r["one_trade_per_day_excluding_tsla"]["expectancy_r_per_trade"],
            "excluding_best_r_symbol_expectancy_r": r["one_trade_per_day_excluding_best_r_symbol"]["expectancy_r_per_trade"],
            "best_r_symbol": r["best_r_contributing_symbol_one_per_day"],
        }
        for label, r in config_results.items()
    ]
    with (args.output_dir / "orb_existing_configs_one_trade_per_day_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(one_day_rows[0].keys()))
        writer.writeheader()
        writer.writerows(one_day_rows)

    all_cand_rows = [
        {
            "config_label": label,
            "closed_trades": r["combined_all_candidates"]["closed_trades"],
            "expectancy_r_per_trade": r["combined_all_candidates"]["expectancy_r_per_trade"],
            "profit_factor_r": r["combined_all_candidates"]["profit_factor_r"],
            "win_rate": r["combined_all_candidates"]["win_rate"], "cumulative_r": r["combined_all_candidates"]["cumulative_r"],
        }
        for label, r in config_results.items()
    ]
    with (args.output_dir / "orb_existing_configs_all_candidates_comparison.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_cand_rows[0].keys()))
        writer.writeheader()
        writer.writerows(all_cand_rows)

    symbol_rows = []
    for label in FROZEN_CONFIG_SET:
        one_rows_by_period = {p: period_results[p]["cells"][label]["one_trade_per_day"]["rows"] for p in PERIODS}
        combined_one_rows = [r for rows in one_rows_by_period.values() for r in rows]
        for symbol, stats in symbol_summary(combined_one_rows)["per_symbol"].items():
            symbol_rows.append({"config_label": label, "symbol": symbol, **stats})
    with (args.output_dir / "orb_existing_configs_symbol_sensitivity.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in symbol_rows for key in row}) if symbol_rows else ["config_label", "symbol"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(symbol_rows)

    (args.output_dir / "orb_existing_configs_combined_summary.json").write_text(
        json.dumps({"core_candidates": core_candidates, "promising_but_unstable": promising, "next_milestone": decision}, indent=2, default=str),
        encoding="utf-8",
    )

    print(f"JSON report: {args.output_dir / 'orb_existing_configs_reevaluation_report.json'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except UniverseResearchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
