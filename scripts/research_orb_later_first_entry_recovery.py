"""CLI entry point for M45: later-first-entry trade-frequency recovery
(Issue #342).

One material question: on a day where the frozen `WAITED_FIRST_VALID_0950`
primary produces NO TRADE, does allowing the first genuinely valid ORB
setup that appears strictly after 09:50 through 10:20 ET inclusive
recover meaningful trade frequency while preserving positive trade
quality? BASELINE = the existing WAITED_FIRST_VALID_0950 primary (M44's
own fallback-candidate policy is NOT reused here -- M44 classified
INSUFFICIENT_EVIDENCE). LATER = `LATER_FIRST_VALID_1020`. Both use the
single frozen exit arm `BE_AFTER_050R` (M43, Issue #336) -- no
exit-rule variant, no ranking, no cutoff sweep, no threshold/universe
change anywhere in this script or the module it drives.

Reuses M43's own period/universe/fetch machinery
(`scripts.research_orb_profit_protection_exit`) unmodified, and the
M43.5 (Issue #338) date-indexed research path throughout.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss. Pass `--fetch-missing` to allow a network
fetch for cache gaps -- expected to be unnecessary since every period's
universe was already fully fetched by M39-M44.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import pickle
import sys
from datetime import date, datetime, timezone
from functools import partial
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import session_open_for
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    HALF_LIFE_SESSIONS,
    build_session_index,
    recency_weighted_trade_quality,
)
from src.orb_existing_configs_reevaluation_research import best_contributing_symbol_by_r
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table
from src.orb_later_first_entry_recovery_research import (
    ENTRY_BASELINE,
    ENTRY_LATER,
    ENTRY_POLICIES,
    EXIT_ARM,
    build_day_outcomes,
    build_policy_rows,
    classify_later_first_entry_recovery,
    later_only_rows,
    recovery_diagnostics,
    symbol_robustness,
)
from src.orb_profit_protection_exit_research import full_stop_out_rate, max_drawdown_r_and_streak_from_rows
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, trade_quality_from_rows
from src.read_only_authorization_manifest import checkpoint_implementation_digest

from scripts.research_orb_profit_protection_exit import (
    PERIODS,
    _fetch_period_bars,
    _period_universe,
    _session_bounds_provider_for,
    _session_dates_for,
)
from src.orb_universe_expansion_research import DEFAULT_CACHE_DIR, DEFAULT_FEED, validate_cache_integrity

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-later-first-entry-recovery" / "results"
CHECKPOINT_CACHE_DIR = ROOT / "artifacts" / "research-later-first-entry-recovery" / "period-checkpoint"


def _checkpoint_path(period: str, *, checkpoint_dir: Path) -> Path:
    return checkpoint_dir / f"{period}.pkl"


def _load_period_checkpoint(period: str, *, checkpoint_dir: Path) -> dict | None:
    path = _checkpoint_path(period, checkpoint_dir=checkpoint_dir)
    if not path.exists():
        return None
    with path.open("rb") as handle:
        return pickle.load(handle)


def _save_period_checkpoint(period: str, result: dict, *, checkpoint_dir: Path) -> None:
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(period, checkpoint_dir=checkpoint_dir)
    tmp_path = path.with_suffix(".pkl.tmp")
    with tmp_path.open("wb") as handle:
        pickle.dump(result, handle)
    tmp_path.replace(path)


async def _evaluate_period(*, period: str, cache_dir: Path, fetch_missing: bool, feed: str, implementation_digest: str, created_at: datetime) -> dict:
    session_dates = _session_dates_for(period)
    universe = _period_universe(period)
    print(f"[{period}] {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]}), universe: {universe}")

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
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=universe, session_dates=session_dates, inputs_table=inputs_table, base_config=load_config())

    day_outcomes = build_day_outcomes(
        period=period, evaluation=evaluation, inputs_table=inputs_table, session_dates=session_dates, universe=universe,
        market_bars_by_symbol=market_bars_by_symbol, base_config=load_config(), implementation_digest=implementation_digest,
        created_at=created_at, session_bounds_provider=session_bounds_provider,
    )
    rows = build_policy_rows(day_outcomes, period=period, session_bounds_provider=session_bounds_provider)

    for entry_policy in ENTRY_POLICIES:
        tq = trade_quality_from_rows(rows[entry_policy])
        print(f"[{period}] {entry_policy}: closed={tq['closed_trades']} exp_r={tq['expectancy_r_per_trade']} pf_r={tq['profit_factor_r']}")
    diag = recovery_diagnostics(day_outcomes, session_bounds_provider=session_bounds_provider)
    print(f"[{period}] baseline_no_trade={diag['baseline_no_trade_days']} later_selections={diag['later_selections']} later_closed={diag['later_closed_trades']} data_invalid={diag['later_data_invalid_outcomes']}")

    return {"period": period, "session_dates": session_dates, "universe": universe, "day_outcomes": day_outcomes, "rows": rows}


def _pf_r(tq) -> float | None:
    tq = tq or {}
    raw = tq.get("profit_factor_r")
    if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
        return float("inf")
    return raw


def _quality_summary(rows, *, session_index) -> dict:
    tq = trade_quality_from_rows(rows)
    dd = max_drawdown_r_and_streak_from_rows(rows)
    weighted = recency_weighted_trade_quality(rows, session_index=session_index, anchor_date=ANCHOR_DATE, half_life_sessions=HALF_LIFE_SESSIONS)
    return {
        "closed_trades": tq["closed_trades"], "expectancy_r": tq["expectancy_r_per_trade"], "pf_r": _pf_r(tq),
        "cumulative_r": tq["cumulative_r"], "gross_profit_r": tq["gross_profit_r"], "gross_loss_r": tq["gross_loss_r"],
        "average_winner_r": tq["average_winner_r"], "average_loser_r": tq["average_loser_r"], "win_rate": tq["win_rate"],
        "full_stop_out_rate": full_stop_out_rate(rows), "max_drawdown_r": dd["maximum_drawdown_r"],
        "longest_losing_streak": dd["longest_losing_streak"], "recency_weighted": weighted,
    }


def _with_frequency(summary: dict, *, sessions: int) -> dict:
    months = sessions / 21.0
    summary = dict(summary)
    summary["r_per_month"] = (summary["cumulative_r"] or 0) / months if months else None
    summary["trades_per_month"] = summary["closed_trades"] / months if months else None
    return summary


ACCOUNT_SIZE_USD = 30_000
FIXED_RISK_PCT = 0.01
ONE_R_USD = ACCOUNT_SIZE_USD * FIXED_RISK_PCT  # $300
TARGET_TRADES_PER_MONTH_LOW = 17.0
TARGET_TRADES_PER_MONTH_HIGH = 20.0


def _economic_target_tracking(*, baseline_trades_per_month: float, later_trades_per_month: float, baseline_r_per_month: float, later_r_per_month: float) -> dict:
    absolute_gain = later_trades_per_month - baseline_trades_per_month
    pct_gain = (absolute_gain / baseline_trades_per_month) if baseline_trades_per_month else None
    return {
        "baseline_trades_per_month": baseline_trades_per_month, "later_policy_trades_per_month": later_trades_per_month,
        "absolute_gain_trades_per_month": absolute_gain, "percentage_gain": pct_gain,
        "remaining_gap_to_17_trades_per_month": max(0.0, TARGET_TRADES_PER_MONTH_LOW - later_trades_per_month),
        "remaining_gap_to_20_trades_per_month": max(0.0, TARGET_TRADES_PER_MONTH_HIGH - later_trades_per_month),
        "account_size_usd": ACCOUNT_SIZE_USD, "fixed_risk_pct": FIXED_RISK_PCT, "one_r_usd": ONE_R_USD,
        "baseline_monthly_equivalent_usd_simple_arithmetic": baseline_r_per_month * ONE_R_USD,
        "later_policy_monthly_equivalent_usd_simple_arithmetic": later_r_per_month * ONE_R_USD,
        "disclaimer": (
            "Simple fixed-risk research arithmetic (R_per_month * $300) only. "
            "NOT a chronological capital simulation, NOT expected live monthly income, "
            "NOT the final $1K feasibility study. No compounding; risk % held fixed."
        ),
    }


def _pool_recovery_diagnostics(per_period_diagnostics: dict[str, dict]) -> dict:
    """Sum per-period `recovery_diagnostics` results (each already computed
    with that period's own correct `session_bounds_provider`) into one
    pooled dict, rather than recomputing from day_outcomes pooled across
    periods with a single provider -- different periods can require
    different providers, so a single pooled `simulate_exit_arm` call would
    silently misclassify research-calendar periods."""
    count_keys = (
        "total_trading_sessions", "baseline_trade_days", "baseline_no_trade_days",
        "no_trade_days_with_later_valid_setup", "later_selections", "later_closed_trades",
        "later_data_invalid_outcomes",
    )
    pooled = {k: sum(d[k] for d in per_period_diagnostics.values()) for k in count_keys}
    pooled["recovery_rate_among_baseline_no_trade_days"] = (
        pooled["no_trade_days_with_later_valid_setup"] / pooled["baseline_no_trade_days"]
        if pooled["baseline_no_trade_days"] else None
    )
    pooled_buckets = {"09:51-10:00": 0, "10:01-10:10": 0, "10:11-10:20": 0}
    for d in per_period_diagnostics.values():
        for bucket, count in d["timing_buckets"].items():
            pooled_buckets[bucket] += count
    pooled["timing_buckets"] = pooled_buckets
    baseline_trades_per_month = pooled["baseline_trade_days"] / (pooled["total_trading_sessions"] / 21.0) if pooled["total_trading_sessions"] else None
    combined_trade_days = pooled["baseline_trade_days"] + pooled["no_trade_days_with_later_valid_setup"]
    combined_trades_per_month = combined_trade_days / (pooled["total_trading_sessions"] / 21.0) if pooled["total_trading_sessions"] else None
    pooled["percentage_increase_trades_per_month"] = (
        (combined_trades_per_month - baseline_trades_per_month) / baseline_trades_per_month if baseline_trades_per_month else None
    )
    pooled["percentage_increase_trade_days"] = (
        (combined_trade_days - pooled["baseline_trade_days"]) / pooled["baseline_trade_days"] if pooled["baseline_trade_days"] else None
    )
    return pooled


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=CHECKPOINT_CACHE_DIR)
    parser.add_argument("--no-checkpoint", action="store_true")
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print(f"Frozen config (Issue #325 CHALLENGER only): {CONFIG_LABEL} gap={CONFIG_ENTRY.combo.gap_percent}% rvol={CONFIG_ENTRY.combo.rvol_multiple}x")
    print(f"Entry policies: {ENTRY_POLICIES}  Exit arm: {EXIT_ARM}")
    print(f"Anchor date={ANCHOR_DATE} half_life_sessions={HALF_LIFE_SESSIONS}")

    empirical_start = datetime.now(timezone.utc)
    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    period_results: dict[str, dict] = {}
    for period in PERIODS:
        cached = None if args.no_checkpoint else _load_period_checkpoint(period, checkpoint_dir=args.checkpoint_dir)
        if cached is not None:
            print(f"[{period}] reusing simulation checkpoint from {_checkpoint_path(period, checkpoint_dir=args.checkpoint_dir)}")
            period_results[period] = cached
            continue
        result = asyncio.run(_evaluate_period(
            period=period, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed,
            implementation_digest=implementation_digest, created_at=created_at,
        ))
        period_results[period] = result
        if not args.no_checkpoint:
            _save_period_checkpoint(period, result, checkpoint_dir=args.checkpoint_dir)

    empirical_elapsed_seconds = (datetime.now(timezone.utc) - empirical_start).total_seconds()
    print(f"Empirical runtime (all periods, both entry policies): {empirical_elapsed_seconds:.1f}s")

    session_index = build_session_index(start_date=date(2021, 1, 1))
    total_sessions = sum(len(period_results[p]["session_dates"]) for p in PERIODS)

    report: dict = {
        "issue": 342, "config": CONFIG_LABEL, "entry_policies": list(ENTRY_POLICIES), "exit_arm": EXIT_ARM,
        "anchor_date": ANCHOR_DATE.isoformat(), "half_life_sessions": HALF_LIFE_SESSIONS,
        "periods": {
            p: {
                "first_session": period_results[p]["session_dates"][0].isoformat(),
                "last_session": period_results[p]["session_dates"][-1].isoformat(),
                "sessions": len(period_results[p]["session_dates"]), "universe": list(period_results[p]["universe"]),
            }
            for p in PERIODS
        },
        "empirical_runtime_seconds": empirical_elapsed_seconds, "implementation_digest": implementation_digest,
    }

    pooled_rows = {policy: [] for policy in ENTRY_POLICIES}
    pooled_later_only_rows: list = []
    per_period_summary = {policy: {} for policy in ENTRY_POLICIES}
    per_period_later_only = {}
    per_period_diagnostics = {}

    for period in PERIODS:
        sessions = len(period_results[period]["session_dates"])
        day_outcomes = period_results[period]["day_outcomes"]
        # Different periods require different session_bounds_provider
        # (research-calendar periods use a non-default provider) -- every
        # simulate_exit_arm call below (inside later_only_rows/
        # recovery_diagnostics, mirroring build_policy_rows above) must use
        # THIS period's own provider, never a single provider pooled
        # across periods.
        period_session_bounds_provider = _session_bounds_provider_for(period)
        for policy in ENTRY_POLICIES:
            rows = period_results[period]["rows"][policy]
            pooled_rows[policy].extend(rows)
            per_period_summary[policy][period] = _with_frequency(_quality_summary(rows, session_index=session_index), sessions=sessions)
        l_only = later_only_rows(day_outcomes, period=period, session_bounds_provider=period_session_bounds_provider)
        pooled_later_only_rows.extend(l_only)
        l_tq = trade_quality_from_rows(l_only)
        per_period_later_only[period] = {"n": len(l_only), "expectancy_r": l_tq["expectancy_r_per_trade"], "cumulative_r": l_tq["cumulative_r"]}
        per_period_diagnostics[period] = recovery_diagnostics(day_outcomes, session_bounds_provider=period_session_bounds_provider)

    pooled_summary = {
        policy: _with_frequency(_quality_summary(tuple(pooled_rows[policy]), session_index=session_index), sessions=total_sessions)
        for policy in ENTRY_POLICIES
    }
    later_only_summary = _with_frequency(_quality_summary(tuple(pooled_later_only_rows), session_index=session_index), sessions=total_sessions)
    later_only_symbol_robustness = symbol_robustness(tuple(pooled_later_only_rows))

    diagnostics = _pool_recovery_diagnostics(per_period_diagnostics)
    all_later_times = [
        d.later.decision_timestamp.astimezone(EASTERN).time()
        for p in PERIODS for d in period_results[p]["day_outcomes"] if d.later is not None
    ]
    if all_later_times:
        all_later_times.sort()
        diagnostics["median_later_decision_time"] = all_later_times[len(all_later_times) // 2].isoformat()
        diagnostics["earliest_later_decision_time"] = all_later_times[0].isoformat()
        diagnostics["latest_later_decision_time"] = all_later_times[-1].isoformat()
    else:
        diagnostics["median_later_decision_time"] = None
        diagnostics["earliest_later_decision_time"] = None
        diagnostics["latest_later_decision_time"] = None

    baseline_summary = pooled_summary[ENTRY_BASELINE]
    later_summary = pooled_summary[ENTRY_LATER]

    classification = classify_later_first_entry_recovery(
        later_only_quality=trade_quality_from_rows(tuple(pooled_later_only_rows)),
        later_only_expectancy_excl_best_symbol=later_only_symbol_robustness["expectancy_r_excluding_best_contributor"],
        combined_expectancy_r=later_summary["expectancy_r"], combined_pf_r=later_summary["pf_r"],
        combined_r_per_month=later_summary["r_per_month"] or 0.0, baseline_r_per_month=baseline_summary["r_per_month"] or 0.0,
        combined_trades_per_month=later_summary["trades_per_month"] or 0.0, baseline_trades_per_month=baseline_summary["trades_per_month"] or 0.0,
        baseline_expectancy_r=baseline_summary["expectancy_r"] or 0.0,
        combined_drawdown_r=later_summary["max_drawdown_r"] or 0.0, baseline_drawdown_r=baseline_summary["max_drawdown_r"] or 0.0,
    )

    economic = _economic_target_tracking(
        baseline_trades_per_month=baseline_summary["trades_per_month"] or 0.0, later_trades_per_month=later_summary["trades_per_month"] or 0.0,
        baseline_r_per_month=baseline_summary["r_per_month"] or 0.0, later_r_per_month=later_summary["r_per_month"] or 0.0,
    )

    report["pooled_summary"] = pooled_summary
    report["per_period_summary"] = per_period_summary
    report["later_only"] = {
        "pooled": later_only_summary, "symbol_robustness": later_only_symbol_robustness, "per_period": per_period_later_only,
    }
    report["recovery_diagnostics"] = diagnostics
    report["recovery_diagnostics_by_period"] = per_period_diagnostics
    report["classification"] = classification
    report["economic_target_tracking"] = economic

    print(f"=== POOLED ===")
    for policy in ENTRY_POLICIES:
        s = pooled_summary[policy]
        print(f"  {policy}: closed={s['closed_trades']} trades/mo={s['trades_per_month']:.3f} exp_r={s['expectancy_r']} pf_r={s['pf_r']} r/mo={s['r_per_month']} maxDD={s['max_drawdown_r']}")
    print(f"  LATER_ONLY: n={later_only_summary['closed_trades']} exp_r={later_only_summary['expectancy_r']} pf_r={later_only_summary['pf_r']}")
    print(f"CLASSIFICATION: {classification['verdict']}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_later_first_entry_recovery_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    summary_csv = args.output_dir / "orb_later_first_entry_recovery_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "policy", "closed_trades", "trades_per_month", "expectancy_r", "pf_r", "cumulative_r",
            "r_per_month", "win_rate", "full_stop_out_rate", "max_drawdown_r", "longest_losing_streak",
        ])
        for policy in ENTRY_POLICIES:
            s = pooled_summary[policy]
            writer.writerow([
                policy, s["closed_trades"], s["trades_per_month"], s["expectancy_r"], s["pf_r"], s["cumulative_r"],
                s["r_per_month"], s["win_rate"], s["full_stop_out_rate"], s["max_drawdown_r"], s["longest_losing_streak"],
            ])
        writer.writerow([
            "LATER_ONLY", later_only_summary["closed_trades"], later_only_summary["trades_per_month"],
            later_only_summary["expectancy_r"], later_only_summary["pf_r"], later_only_summary["cumulative_r"],
            later_only_summary["r_per_month"], later_only_summary["win_rate"], later_only_summary["full_stop_out_rate"],
            later_only_summary["max_drawdown_r"], later_only_summary["longest_losing_streak"],
        ])

    print(f"JSON report: {args.output_dir / 'orb_later_first_entry_recovery_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
