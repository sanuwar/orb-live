"""CLI entry point for M43: profit-protection exit experiment (Issue #336).

Reuses M41/M42's own entry-policy implementation completely unmodified
(`src.orb_candidate_selection_timing_research.early_pool`/
`enrich_with_decision_time`, `src.orb_out_of_sample_validation_research.
select_first_candidate_per_day`) for both frozen entry policies --
`WAITED_FIRST_VALID_0950` (PRIMARY) and `CURRENT_FIRST_VALID` (sensitivity
benchmark) -- on the single frozen Issue #325 CHALLENGER config
(`GAP_050_RVOL_175`), across all six already-established historical periods
(P_MINUS_2, P_MINUS_1, P0, P1, P2, P3), through exactly four frozen exit
arms (`src.orb_profit_protection_exit_research.EXIT_ARMS`). No gap/RVOL/
09:50/ranking/universe/second-trade/sizing rule is touched anywhere in this
script or the module it drives.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss. Pass `--fetch-missing` to allow a network
fetch for cache gaps (requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
from the environment only, never printed) -- expected to be unnecessary
since every period's own universe was already fully fetched by M39-M42.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import pickle
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
from src.orb_0950_confirmation_independent_validation_research import (
    P_MINUS_2_REFERENCE_DAY,
    P_MINUS_2_SESSION_COUNT,
    VALIDATION_UNIVERSE as P_MINUS_2_UNIVERSE,
    derive_p_minus_2_sessions,
)
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    HALF_LIFE_SESSIONS,
    build_session_index,
    recency_weighted_trade_quality,
)
from src.orb_existing_configs_reevaluation_research import PERIOD_NATIVE_UNIVERSES, PERIOD_SESSION_SPEC, best_contributing_symbol_by_r
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_gap_rvol_sensitivity_research import precompute_inputs_table, evaluate_combo
from src.orb_independent_single_trade_core_validation_research import VALIDATION_UNIVERSE as P_MINUS_1_UNIVERSE, derive_p_minus_1_sessions
from src.orb_profit_protection_exit_research import (
    ARM_BASELINE,
    ENTRY_CURRENT_FIRST_VALID,
    ENTRY_WAITED_FIRST_VALID_0950,
    EXIT_ARMS,
    PROTECTION_ARMS,
    build_all_entry_and_exit_rows,
    full_stop_out_rate,
    max_drawdown_r_and_streak_from_rows,
    protection_diagnostics,
)
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    eligible_market_bars,
    fetch_full_minute_history,
    trading_sessions_before,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-profit-protection-exit" / "results"
# Per-period checkpoint of the (expensive, ~hours-long) simulation stage,
# keyed by period name only -- so a bug in the cheap post-processing/
# reporting stage below never forces redoing the empirical run. Never
# committed (covered by the same `artifacts/research-profit-protection-
# exit/` .gitignore prefix as the results dir); safe to delete any time to
# force a clean recompute.
CHECKPOINT_CACHE_DIR = ROOT / "artifacts" / "research-profit-protection-exit" / "period-checkpoint"
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"
PERIODS: tuple[str, ...] = ("P_MINUS_2", "P_MINUS_1", "P0", "P1", "P2", "P3")
RESEARCH_CALENDAR_PERIODS = {"P_MINUS_2", "P_MINUS_1"}


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


def _period_universe(period: str) -> tuple[str, ...]:
    if period == "P_MINUS_2":
        return P_MINUS_2_UNIVERSE
    if period == "P_MINUS_1":
        return P_MINUS_1_UNIVERSE
    return PERIOD_NATIVE_UNIVERSES[period]


def _session_dates_for(period: str) -> tuple[date, ...]:
    if period == "P_MINUS_2":
        return derive_p_minus_2_sessions()
    if period == "P_MINUS_1":
        return derive_p_minus_1_sessions()
    reference_day, count, uses_research_calendar = PERIOD_SESSION_SPEC[period]
    if uses_research_calendar:
        return research_trading_sessions_before(reference_day, count=count)
    return trading_sessions_before(reference_day, count=count)


def _session_bounds_provider_for(period: str):
    if period in RESEARCH_CALENDAR_PERIODS:
        return research_regular_session_bounds
    _, _, uses_research_calendar = PERIOD_SESSION_SPEC[period]
    return research_regular_session_bounds if uses_research_calendar else None


async def _fetch_period_bars(*, period: str, session_dates: tuple[date, ...], cache_dir: Path, fetch_missing: bool, feed: str) -> dict[str, list]:
    universe = _period_universe(period)
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
        version=f"orb-m43-profit-protection-exit-{period}-v1",
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
    # `end` must match each period's own already-cached fetch window exactly
    # (the cache is keyed by exact request params) -- P_MINUS_2/P_MINUS_1
    # were fetched using the session's own CLOSE time + 8h (M42/M40); P0-P3
    # were fetched using the session's own OPEN time + 8h (M39).
    if period in RESEARCH_CALENDAR_PERIODS:
        end_time = research_regular_session_bounds(session_dates[-1])[1]
    else:
        end_time = resolver(session_dates[-1])
    end = datetime.combine(session_dates[-1], end_time, tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

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

    rows = build_all_entry_and_exit_rows(
        period=period, evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars_by_symbol,
        base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=session_bounds_provider,
    )

    for entry_policy in (ENTRY_WAITED_FIRST_VALID_0950, ENTRY_CURRENT_FIRST_VALID):
        for arm in EXIT_ARMS:
            tq = trade_quality_from_rows(rows[entry_policy][arm])
            print(f"[{period}] {entry_policy}/{arm}: closed={tq['closed_trades']} exp_r={tq['expectancy_r_per_trade']} pf_r={tq['profit_factor_r']}")

    return {"period": period, "session_dates": session_dates, "universe": universe, "rows": rows}


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


def _pf_r(tq) -> float | None:
    tq = tq or {}
    raw = tq.get("profit_factor_r")
    if raw is None and tq.get("profit_factor_r_infinite_no_losses"):
        return float("inf")
    return raw


def _arm_summary(rows, *, session_index) -> dict:
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


def _vs_baseline_delta(arm_summary: dict, baseline_summary: dict) -> dict:
    def _d(key):
        a, b = arm_summary.get(key), baseline_summary.get(key)
        return (a - b) if (a is not None and b is not None) else None
    return {
        "delta_expectancy_r": _d("expectancy_r"), "delta_pf_r": _d("pf_r"), "delta_cumulative_r": _d("cumulative_r"),
        "delta_r_per_month": _d("r_per_month"), "delta_max_drawdown_r": _d("max_drawdown_r"),
        "delta_full_stop_out_rate": _d("full_stop_out_rate"),
    }


DEFAULT_SUPPORT_CRITERIA = {
    "min_closed_trades": MIN_SAMPLE_CLOSED_TRADES,
    "max_expectancy_shortfall_r": 0.01,
    "min_r_per_month_ratio": 0.90,
    "min_drawdown_reduction_pct": 0.15,
    "min_full_stop_rate_reduction_pct": 0.20,
    "min_periods_with_nonnegative_paired_delta": 4,
}


def classify_leading_arm(
    *, arm_pooled: dict, baseline_pooled: dict, arm_excl_best_symbol_expectancy_r: float | None,
    period_paired_deltas: dict[str, float | None], criteria: dict = DEFAULT_SUPPORT_CRITERIA,
) -> dict:
    """SUPPORTED / MIXED / NOT_SUPPORTED / INSUFFICIENT_EVIDENCE, per this
    study's own predeclared rule (frozen before P_MINUS_2..P3 results were
    read)."""
    closed = arm_pooled["closed_trades"]
    if closed < criteria["min_closed_trades"]:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reason": f"closed trades={closed} below minimum {criteria['min_closed_trades']}"}

    exp_r = arm_pooled["expectancy_r"]
    pf_r = arm_pooled["pf_r"]
    b_exp_r = baseline_pooled["expectancy_r"]
    b_dd = baseline_pooled["max_drawdown_r"]
    a_dd = arm_pooled["max_drawdown_r"]
    b_full_stop = baseline_pooled["full_stop_out_rate"]
    a_full_stop = arm_pooled["full_stop_out_rate"]
    b_r_per_month = baseline_pooled["r_per_month"]
    a_r_per_month = arm_pooled["r_per_month"]

    drawdown_reduced_enough = (
        b_dd is not None and b_dd > 0 and a_dd is not None and ((b_dd - a_dd) / b_dd) >= criteria["min_drawdown_reduction_pct"]
    )
    full_stop_reduced_enough = (
        b_full_stop is not None and b_full_stop > 0 and a_full_stop is not None
        and ((b_full_stop - a_full_stop) / b_full_stop) >= criteria["min_full_stop_rate_reduction_pct"]
    )
    risk_improvement = bool(drawdown_reduced_enough or full_stop_reduced_enough)
    periods_nonnegative = sum(1 for d in period_paired_deltas.values() if d is not None and d >= 0)

    checks = {
        "pooled_expectancy_positive": (exp_r or 0) > 0,
        "pooled_pf_above_1": (pf_r or 0) > 1,
        "expectancy_within_001r_of_baseline": (
            exp_r is not None and b_exp_r is not None and exp_r >= b_exp_r - criteria["max_expectancy_shortfall_r"]
        ),
        "r_per_month_at_least_90pct_baseline": (
            b_r_per_month is not None and a_r_per_month is not None and b_r_per_month > 0
            and a_r_per_month >= criteria["min_r_per_month_ratio"] * b_r_per_month
        ),
        "meaningful_risk_improvement": risk_improvement,
        "drawdown_not_increased": (a_dd is not None and b_dd is not None and a_dd <= b_dd),
        "excl_best_symbol_expectancy_positive": (arm_excl_best_symbol_expectancy_r or 0) > 0,
        "paired_delta_nonnegative_in_at_least_4_periods": periods_nonnegative >= criteria["min_periods_with_nonnegative_paired_delta"],
    }

    if all(checks.values()):
        verdict = "SUPPORTED"
    elif checks["pooled_expectancy_positive"] and checks["pooled_pf_above_1"] and checks["meaningful_risk_improvement"]:
        verdict = "MIXED"
    else:
        verdict = "NOT_SUPPORTED"

    return {
        "verdict": verdict, "checks": checks, "expectancy_r": exp_r, "pf_r": pf_r,
        "baseline_expectancy_r": b_exp_r, "max_drawdown_r": a_dd, "baseline_max_drawdown_r": b_dd,
        "full_stop_out_rate": a_full_stop, "baseline_full_stop_out_rate": b_full_stop,
        "periods_with_nonnegative_paired_delta": periods_nonnegative,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--checkpoint-dir", type=Path, default=CHECKPOINT_CACHE_DIR)
    parser.add_argument("--no-checkpoint", action="store_true", help="ignore/skip the per-period simulation checkpoint cache")
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print(f"Frozen config (Issue #325 CHALLENGER only): {CONFIG_LABEL} gap={CONFIG_ENTRY.combo.gap_percent}% rvol={CONFIG_ENTRY.combo.rvol_multiple}x")
    print(f"Exit arms: {EXIT_ARMS}")
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
    print(f"Empirical runtime (all periods, all entry policies, all arms): {empirical_elapsed_seconds:.1f}s")

    # `build_session_index`'s own default `start_date` (2022-01-01) does not
    # reach P_MINUS_2 (2021-03-31..2022-03-29) -- extend it back to the
    # research calendar's own 2021-01-01 floor (the same floor M42/Issue
    # #332 established), or every P_MINUS_2 recency-weight lookup below
    # raises KeyError.
    session_index = build_session_index(start_date=date(2021, 1, 1))
    total_sessions = sum(len(period_results[p]["session_dates"]) for p in PERIODS)

    report: dict = {
        "issue": 336, "config": CONFIG_LABEL, "arms": list(EXIT_ARMS), "protection_arms": list(PROTECTION_ARMS),
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

    entry_policy_reports: dict[str, dict] = {}
    for entry_policy in (ENTRY_WAITED_FIRST_VALID_0950, ENTRY_CURRENT_FIRST_VALID):
        pooled_rows_by_arm = {arm: [] for arm in EXIT_ARMS}
        per_period_summary = {arm: {} for arm in EXIT_ARMS}
        for period in PERIODS:
            sessions = len(period_results[period]["session_dates"])
            for arm in EXIT_ARMS:
                rows = period_results[period]["rows"][entry_policy][arm]
                pooled_rows_by_arm[arm].extend(rows)
                per_period_summary[arm][period] = _with_frequency(_arm_summary(rows, session_index=session_index), sessions=sessions)

        pooled_summary = {
            arm: _with_frequency(_arm_summary(tuple(pooled_rows_by_arm[arm]), session_index=session_index), sessions=total_sessions)
            for arm in EXIT_ARMS
        }
        baseline_pooled = pooled_summary[ARM_BASELINE]
        deltas_vs_baseline = {arm: _vs_baseline_delta(pooled_summary[arm], baseline_pooled) for arm in EXIT_ARMS}

        protection_diag = {}
        symbol_robustness = {}
        classification = {}
        for arm in PROTECTION_ARMS:
            diag = protection_diagnostics(baseline_rows=tuple(pooled_rows_by_arm[ARM_BASELINE]), arm_rows=tuple(pooled_rows_by_arm[arm]))
            protection_diag[arm] = diag

            arm_rows = tuple(pooled_rows_by_arm[arm])
            best_symbol = best_contributing_symbol_by_r(arm_rows)
            per_symbol = symbol_summary(arm_rows)["per_symbol"]
            excl_best = excluding_symbol(arm_rows, symbol=best_symbol) if best_symbol else trade_quality_from_rows(arm_rows)
            excl_aapl = excluding_symbol(arm_rows, symbol="AAPL")
            excl_tsla = excluding_symbol(arm_rows, symbol="TSLA")
            top3 = sorted(per_symbol, key=lambda s: per_symbol[s].get("cumulative_r") or 0, reverse=True)[:3]
            top3_share = None
            total_r = pooled_summary[arm]["cumulative_r"]
            if total_r:
                top3_r = sum((per_symbol[s].get("cumulative_r") or 0) for s in top3)
                top3_share = top3_r / total_r
            best_contribution_share = None
            if best_symbol and total_r:
                best_contribution_share = (per_symbol[best_symbol].get("cumulative_r") or 0) / total_r
            symbol_robustness[arm] = {
                "best_r_contributor": best_symbol, "best_r_contribution_share": best_contribution_share,
                "expectancy_r_excluding_best_contributor": excl_best.get("expectancy_r_per_trade"),
                "expectancy_r_excluding_aapl": excl_aapl.get("expectancy_r_per_trade"),
                "expectancy_r_excluding_tsla": excl_tsla.get("expectancy_r_per_trade"),
                "top3_r_contributors": top3, "top3_share_of_cumulative_r": top3_share,
            }

            period_paired_deltas = {}
            for period in PERIODS:
                b_rows = period_results[period]["rows"][entry_policy][ARM_BASELINE]
                a_rows = period_results[period]["rows"][entry_policy][arm]
                d = protection_diagnostics(baseline_rows=b_rows, arm_rows=a_rows)
                period_paired_deltas[period] = d["paired_mean_delta_r"]

            classification[arm] = classify_leading_arm(
                arm_pooled=pooled_summary[arm], baseline_pooled=baseline_pooled,
                arm_excl_best_symbol_expectancy_r=symbol_robustness[arm]["expectancy_r_excluding_best_contributor"],
                period_paired_deltas=period_paired_deltas,
            )
            classification[arm]["period_paired_mean_delta_r"] = period_paired_deltas

        leading_arm = max(PROTECTION_ARMS, key=lambda a: (pooled_summary[a]["expectancy_r"] if pooled_summary[a]["expectancy_r"] is not None else float("-inf")))

        entry_policy_reports[entry_policy] = {
            "pooled_summary": pooled_summary, "per_period_summary": per_period_summary,
            "deltas_vs_baseline": deltas_vs_baseline, "protection_diagnostics": protection_diag,
            "symbol_robustness": symbol_robustness, "classification": classification,
            "leading_protection_arm": leading_arm, "leading_arm_verdict": classification[leading_arm]["verdict"],
        }

        print(f"=== {entry_policy} ===")
        for arm in EXIT_ARMS:
            s = pooled_summary[arm]
            print(f"  {arm}: closed={s['closed_trades']} exp_r={s['expectancy_r']} pf_r={s['pf_r']} cum_r={s['cumulative_r']} dd_r={s['max_drawdown_r']} full_stop_rate={s['full_stop_out_rate']}")
        print(f"  LEADING PROTECTION ARM: {leading_arm} -> {classification[leading_arm]['verdict']}")

    report["entry_policies"] = entry_policy_reports
    primary = entry_policy_reports[ENTRY_WAITED_FIRST_VALID_0950]
    sensitivity = entry_policy_reports[ENTRY_CURRENT_FIRST_VALID]
    primary_leading = primary["leading_protection_arm"]
    sensitivity_same_arm_verdict = sensitivity["classification"].get(primary_leading, {}).get("verdict")
    report["primary_classification"] = {
        "entry_policy": ENTRY_WAITED_FIRST_VALID_0950, "leading_protection_arm": primary_leading,
        "verdict": primary["leading_arm_verdict"],
    }
    report["current_sensitivity_check"] = {
        "same_arm": primary_leading, "current_first_valid_verdict": sensitivity_same_arm_verdict,
        "directionally_consistent": (
            sensitivity_same_arm_verdict in ("SUPPORTED", "MIXED") if primary["leading_arm_verdict"] in ("SUPPORTED", "MIXED")
            else sensitivity_same_arm_verdict == primary["leading_arm_verdict"]
        ),
    }
    print(f"PRIMARY CLASSIFICATION (WAITED_FIRST_VALID_0950, leading arm={primary_leading}): {primary['leading_arm_verdict']}")
    print(f"CURRENT_FIRST_VALID sensitivity (same arm): {sensitivity_same_arm_verdict}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_profit_protection_exit_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    summary_csv = args.output_dir / "orb_profit_protection_exit_summary.csv"
    with summary_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow([
            "entry_policy", "exit_arm", "closed_trades", "expectancy_r", "pf_r", "cumulative_r",
            "win_rate", "full_stop_out_rate", "max_drawdown_r", "longest_losing_streak", "r_per_month", "trades_per_month",
        ])
        for entry_policy in (ENTRY_WAITED_FIRST_VALID_0950, ENTRY_CURRENT_FIRST_VALID):
            for arm in EXIT_ARMS:
                s = entry_policy_reports[entry_policy]["pooled_summary"][arm]
                writer.writerow([
                    entry_policy, arm, s["closed_trades"], s["expectancy_r"], s["pf_r"], s["cumulative_r"],
                    s["win_rate"], s["full_stop_out_rate"], s["max_drawdown_r"], s["longest_losing_streak"],
                    s["r_per_month"], s["trades_per_month"],
                ])

    print(f"JSON report: {args.output_dir / 'orb_profit_protection_exit_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
