"""CLI entry point for M41: candidate-selection timing/ranking research
(Issue #330).

Evaluates ONLY the single frozen Issue #325 CHALLENGER config
(`GAP_050_RVOL_175`) across all five already-established periods
(P_MINUS_1, P0, P1, P2, P3), reusing each period's own already-established
universe unchanged, through five candidate-selection/execution arms:
CURRENT_FIRST_VALID, EARLY_FIRST_VALID, WAITED_FIRST_VALID_0950,
RANKED_POOL_0950_MAX_RVOL, RANKED_POOL_0950_EQUAL_RANK_STRENGTH. No new
gap/RVOL/universe/SPY/breakout/stop/target/exit rule; no PRIMARY config.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss. Pass `--fetch-missing` to allow a network
fetch for cache gaps (requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
from the environment only, never printed) -- expected to be unnecessary
since every period's own universe was already fully fetched by prior
milestones (#325/#39, #40/M40).
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
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window, max_drawdown_and_streak_from_rows
from src.orb_candidate_selection_timing_research import (
    ANCHOR_DATE,
    ARM_CURRENT_FIRST_VALID,
    ARM_EARLY_FIRST_VALID,
    ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH,
    ARM_RANKED_POOL_0950_MAX_RVOL,
    ARM_WAITED_FIRST_VALID_0950,
    ARMS,
    CONFIG_ENTRY,
    CONFIG_LABEL,
    HALF_LIFE_SESSIONS,
    build_all_arms_rows,
    build_session_index,
    recency_weighted_trade_quality,
)
from src.orb_existing_configs_reevaluation_research import PERIOD_NATIVE_UNIVERSES, PERIOD_SESSION_SPEC
from src.orb_gap_rvol_sensitivity_research import precompute_inputs_table, evaluate_combo
from src.orb_independent_single_trade_core_validation_research import VALIDATION_UNIVERSE, derive_p_minus_1_sessions
from src.orb_regime_diagnostic_research import symbol_summary, trade_quality_from_rows
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

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-candidate-selection-timing" / "results"
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"
PERIODS: tuple[str, ...] = ("P_MINUS_1", "P0", "P1", "P2", "P3")


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
    return VALIDATION_UNIVERSE if period == "P_MINUS_1" else PERIOD_NATIVE_UNIVERSES[period]


def _session_dates_for(period: str) -> tuple[date, ...]:
    if period == "P_MINUS_1":
        return derive_p_minus_1_sessions()
    reference_day, count, uses_research_calendar = PERIOD_SESSION_SPEC[period]
    if uses_research_calendar:
        return research_trading_sessions_before(reference_day, count=count)
    return trading_sessions_before(reference_day, count=count)


def _session_bounds_provider_for(period: str):
    if period == "P_MINUS_1":
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
        version=f"orb-candidate-selection-timing-{period}-v1",
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
    # `end` must match each period's own already-cached fetch window
    # exactly (the cache is keyed by exact request params, not by which
    # bars are substantively covered) -- P_MINUS_1 was originally fetched
    # by the M40 script using the session's own CLOSE time + 8h; P0-P3
    # were originally fetched by the M39 script using the session's own
    # OPEN time + 8h. Using the wrong formula for either produces a cache
    # miss even though the underlying dates fully overlap.
    if period == "P_MINUS_1":
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

    arms_rows = build_all_arms_rows(
        period=period, evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars_by_symbol,
        minute_bars_by_symbol=minute_bars_by_symbol, base_config=load_config(), implementation_digest=implementation_digest,
        created_at=created_at, session_bounds_provider=session_bounds_provider,
    )

    for arm, rows in arms_rows.items():
        tq = trade_quality_from_rows(rows)
        print(f"[{period}] {arm}: occurrences={len(rows)} closed={tq['closed_trades']} exp_r={tq['expectancy_r_per_trade']} pf_r={tq['profit_factor_r']}")

    return {"period": period, "session_dates": session_dates, "universe": universe, "arms_rows": arms_rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print(f"Frozen config (Issue #325 CHALLENGER only): {CONFIG_LABEL} gap={CONFIG_ENTRY.combo.gap_percent}% rvol={CONFIG_ENTRY.combo.rvol_multiple}x")
    print(f"Arms: {ARMS}")
    print(f"Anchor date={ANCHOR_DATE} half_life_sessions={HALF_LIFE_SESSIONS}")

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
    print(f"Empirical runtime (all periods, all arms): {empirical_elapsed_seconds:.1f}s")

    session_index = build_session_index()

    # ---- Combine across periods, per arm ----
    combined_rows_by_arm: dict[str, tuple] = {}
    for arm in ARMS:
        combined = []
        for period in PERIODS:
            combined.extend(period_results[period]["arms_rows"][arm])
        combined_rows_by_arm[arm] = tuple(combined)

    arm_summaries: dict[str, dict] = {}
    for arm in ARMS:
        rows = combined_rows_by_arm[arm]
        combined_tq = trade_quality_from_rows(rows)
        dd = max_drawdown_and_streak_from_rows(rows)
        weighted = recency_weighted_trade_quality(rows, session_index=session_index)
        per_period_tq = {p: trade_quality_from_rows(period_results[p]["arms_rows"][arm]) for p in PERIODS}
        symbols = symbol_summary(rows)
        arm_summaries[arm] = {
            "combined_trade_quality": combined_tq,
            "combined_maximum_drawdown_r": dd["maximum_drawdown"],
            "combined_longest_losing_streak": dd["longest_losing_streak"],
            "recency_weighted": weighted,
            "per_period_trade_quality": per_period_tq,
            "symbol_summary": symbols,
        }
        print(f"COMBINED {arm}: closed={combined_tq['closed_trades']} exp_r={combined_tq['expectancy_r_per_trade']} pf_r={combined_tq['profit_factor_r']} weighted_exp_r={weighted['weighted_expectancy_r']} weighted_pf_r={weighted['weighted_profit_factor_r']}")

    def _delta(a: str, b: str) -> dict:
        ea = arm_summaries[a]["combined_trade_quality"]["expectancy_r_per_trade"]
        eb = arm_summaries[b]["combined_trade_quality"]["expectancy_r_per_trade"]
        pa = arm_summaries[a]["combined_trade_quality"]["profit_factor_r"]
        pb = arm_summaries[b]["combined_trade_quality"]["profit_factor_r"]
        return {
            "from": a, "to": b,
            "expectancy_r_delta": (eb - ea) if (ea is not None and eb is not None) else None,
            "profit_factor_r_delta": (pb - pa) if (pa is not None and pb is not None) else None,
        }

    comparisons = {
        "early_entry_effect_A_to_B": _delta(ARM_CURRENT_FIRST_VALID, ARM_EARLY_FIRST_VALID),
        "cost_of_waiting_B_to_C": _delta(ARM_EARLY_FIRST_VALID, ARM_WAITED_FIRST_VALID_0950),
        "value_of_ranking_max_rvol_C_to_D": _delta(ARM_WAITED_FIRST_VALID_0950, ARM_RANKED_POOL_0950_MAX_RVOL),
        "value_of_ranking_equal_rank_C_to_D": _delta(ARM_WAITED_FIRST_VALID_0950, ARM_RANKED_POOL_0950_EQUAL_RANK_STRENGTH),
    }
    print(f"COMPARISONS: {json.dumps(comparisons, indent=2, default=str)}")

    report = {
        "issue": 330, "config": CONFIG_LABEL, "arms": list(ARMS),
        "anchor_date": ANCHOR_DATE.isoformat(), "half_life_sessions": HALF_LIFE_SESSIONS,
        "periods": {p: {"first_session": period_results[p]["session_dates"][0].isoformat(), "last_session": period_results[p]["session_dates"][-1].isoformat(), "sessions": len(period_results[p]["session_dates"]), "universe": list(period_results[p]["universe"])} for p in PERIODS},
        "arm_summaries": arm_summaries, "comparisons": comparisons,
        "empirical_runtime_seconds": empirical_elapsed_seconds, "implementation_digest": implementation_digest,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_candidate_selection_timing_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    combined_rows_csv = args.output_dir / "orb_candidate_selection_timing_combined_summary.csv"
    with combined_rows_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["arm", "closed_trades", "expectancy_r_per_trade", "profit_factor_r", "cumulative_r", "maximum_drawdown_r", "longest_losing_streak", "weighted_expectancy_r", "weighted_win_rate", "weighted_profit_factor_r"])
        for arm in ARMS:
            tq = arm_summaries[arm]["combined_trade_quality"]
            weighted = arm_summaries[arm]["recency_weighted"]
            writer.writerow([
                arm, tq["closed_trades"], tq["expectancy_r_per_trade"], tq["profit_factor_r"], tq["cumulative_r"],
                arm_summaries[arm]["combined_maximum_drawdown_r"], arm_summaries[arm]["combined_longest_losing_streak"],
                weighted["weighted_expectancy_r"], weighted["weighted_win_rate"], weighted["weighted_profit_factor_r"],
            ])

    period_arm_csv = args.output_dir / "orb_candidate_selection_timing_period_arm_metrics.csv"
    with period_arm_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["period", "arm", "closed_trades", "expectancy_r_per_trade", "profit_factor_r", "cumulative_r"])
        for arm in ARMS:
            for period in PERIODS:
                tq = arm_summaries[arm]["per_period_trade_quality"][period]
                writer.writerow([period, arm, tq["closed_trades"], tq["expectancy_r_per_trade"], tq["profit_factor_r"], tq["cumulative_r"]])

    print(f"JSON report: {args.output_dir / 'orb_candidate_selection_timing_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
