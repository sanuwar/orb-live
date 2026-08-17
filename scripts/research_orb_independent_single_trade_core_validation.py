"""CLI entry point for the independent single-trade-core validation study
(local milestone M40).

Evaluates ONLY the two Issue #325 CORE_CANDIDATE winners (PRIMARY
`GAP_100_RVOL_250`, CHALLENGER `GAP_050_RVOL_175`) against P_MINUS_1 --
252 research-calendar sessions strictly before P0's own start date
(2023-03-31), a window neither config's selection could have seen. No new
gap/RVOL threshold, no re-optimization, no regime/day-of-week filter, no
profitability-based universe/symbol choice.

Before attempting any fetch, checks whether P_MINUS_1 is fully computable
from the current research-calendar coverage and bar cache
(`p_minus_1_coverage_gap`). If not, prints a structured
`DATA_COVERAGE_GAP` report describing exactly what is missing and exits
without performing any simulation -- it does not silently narrow the
window, estimate, or guess at un-cached dates.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss. Pass `--fetch-missing` to allow a network
fetch for cache gaps (requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
from the environment only, never printed) -- irrelevant while P_MINUS_1
itself is calendar-blocked, since no session dates can be derived at all.
"""
from __future__ import annotations

import argparse
import asyncio
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
from src.orb_existing_configs_reevaluation_research import FROZEN_CONFIG_SET, best_contributing_symbol_by_r
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, matrix_row, precompute_inputs_table
from src.orb_independent_single_trade_core_validation_research import (
    CHALLENGER_CONFIG_LABEL,
    DEFAULT_VALIDATION_CRITERIA,
    P0_START,
    P_MINUS_1_SESSION_COUNT,
    PRIMARY_CONFIG_LABEL,
    VALIDATION_CONFIG_LABELS,
    VALIDATION_UNIVERSE,
    classify_validation,
    derive_p_minus_1_sessions,
    p_minus_1_coverage_gap,
    recommend_next_milestone,
)
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import symbol_summary, trade_quality_from_rows
from src.orb_research_historical_calendar import research_regular_session_bounds
from src.orb_universe_expansion_research import CachingHttpClient, DEFAULT_CACHE_DIR, DEFAULT_FEED, fetch_full_minute_history, trading_sessions_before, validate_cache_integrity
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-independent-single-trade-core-validation" / "results"
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


async def _fetch_p_minus_1_bars(*, session_dates: tuple, cache_dir: Path, fetch_missing: bool, feed: str) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    fetch_from = derive_p_minus_1_sessions(reference_day=session_dates[0], count=lookback_days)[0]

    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=fetch_from, now=datetime.now(timezone.utc), safety_margin_days=30,
        version="orb-independent-single-trade-core-validation-p-minus-1-v1",
    )
    print(f"[P_MINUS_1] freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} ({freshness_policy.max_age_seconds / 86400.0:.1f} days)")

    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")
        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(
        session_dates[-1], research_regular_session_bounds(session_dates[-1])[1], tzinfo=EASTERN,
    ).astimezone(timezone.utc) + timedelta(hours=8)

    symbols = list(VALIDATION_UNIVERSE) + ["SPY"]
    market_bars_by_symbol: dict[str, list] = {}
    for symbol in symbols:
        raw_bars = await fetch_full_minute_history(transport, credentials=credentials, symbol=symbol, start=start, end=end, now=now, feed=feed)
        market_bars_by_symbol[symbol] = eligible_market_bars_compat(raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy)
    return market_bars_by_symbol


def eligible_market_bars_compat(raw_bars, *, symbol, now, freshness_policy):
    from src.orb_universe_expansion_research import eligible_market_bars

    return eligible_market_bars(raw_bars, symbol=symbol, now=now, freshness_policy=freshness_policy, session_bounds_provider=research_regular_session_bounds)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print("Frozen validation configuration (2 of Issue #325's 18, no new thresholds):")
    for label in VALIDATION_CONFIG_LABELS:
        entry = FROZEN_CONFIG_SET[label]
        role = "PRIMARY" if label == PRIMARY_CONFIG_LABEL else "CHALLENGER"
        print(f"  {role}: {label} gap={entry.combo.gap_percent}% rvol={entry.combo.rvol_multiple}x (Issue #325 CORE_CANDIDATE)")
    print(f"Universe (frozen, #320 ANCHORED_COHORT, unchanged): {VALIDATION_UNIVERSE}")
    print(f"P_MINUS_1 target: {P_MINUS_1_SESSION_COUNT} sessions strictly before {P0_START.isoformat()}")

    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    gap = p_minus_1_coverage_gap(cache_dir=args.cache_dir)
    if gap is not None:
        print("DATA_COVERAGE_GAP: P_MINUS_1 cannot be computed from current research-calendar/bar-cache coverage.")
        print(f"  requested_session_count={gap.requested_session_count}")
        print(f"  reference_day={gap.reference_day.isoformat()}")
        print(f"  calendar_lower_bound={gap.calendar_lower_bound.isoformat()}")
        print(f"  sessions_available_within_calendar_coverage={gap.sessions_available_within_calendar_coverage}")
        print(f"  sessions_short={gap.sessions_short}")
        print(f"  earliest_cached_bar_utc={gap.earliest_cached_bar_utc}")
        print(f"  detail: {gap.detail}")

        args.output_dir.mkdir(parents=True, exist_ok=True)
        (args.output_dir / "orb_independent_validation_data_coverage_gap.json").write_text(
            json.dumps(
                {
                    "status": "DATA_COVERAGE_GAP",
                    "requested_session_count": gap.requested_session_count,
                    "reference_day": gap.reference_day.isoformat(),
                    "calendar_lower_bound": gap.calendar_lower_bound.isoformat(),
                    "sessions_available_within_calendar_coverage": gap.sessions_available_within_calendar_coverage,
                    "sessions_short": gap.sessions_short,
                    "earliest_cached_bar_utc": gap.earliest_cached_bar_utc,
                    "detail": gap.detail,
                    "primary_verdict": "INSUFFICIENT_SAMPLE",
                    "challenger_verdict": "INSUFFICIENT_SAMPLE",
                    "next_milestone": recommend_next_milestone(primary_verdict="INSUFFICIENT_SAMPLE", challenger_verdict="INSUFFICIENT_SAMPLE"),
                },
                indent=2, sort_keys=True,
            ),
            encoding="utf-8",
        )
        print("Wrote orb_independent_validation_data_coverage_gap.json -- no simulation was performed.")
        return 3

    # --- Reachable only once the calendar/cache gap above is closed. ---
    empirical_start = datetime.now(timezone.utc)
    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    session_dates = derive_p_minus_1_sessions()
    print(f"[P_MINUS_1] {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]})")

    market_bars_by_symbol = asyncio.run(_fetch_p_minus_1_bars(session_dates=session_dates, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed))
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    inputs_table = precompute_inputs_table(
        universe=VALIDATION_UNIVERSE, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(),
        session_open_resolver=partial(session_open_for, session_bounds_provider=research_regular_session_bounds),
    )

    config_results: dict[str, dict] = {}
    for label in VALIDATION_CONFIG_LABELS:
        entry = FROZEN_CONFIG_SET[label]
        evaluation = evaluate_combo(combo=entry.combo, universe=VALIDATION_UNIVERSE, session_dates=session_dates, inputs_table=inputs_table, base_config=load_config())
        row = matrix_row(evaluation)

        all_rows = build_stability_rows(
            period="P_MINUS_1", view="NATIVE", config_label=label, combo=entry.combo,
            candidate_signals=evaluation.candidate_signals, market_bars_by_symbol=market_bars_by_symbol,
            base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )
        one_per_day_signals = select_first_candidate_per_day(evaluation.candidate_signals)
        one_rows = build_stability_rows(
            period="P_MINUS_1", view="NATIVE", config_label=label, combo=entry.combo,
            candidate_signals=one_per_day_signals, market_bars_by_symbol=market_bars_by_symbol,
            base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )

        all_tq = trade_quality_from_rows(all_rows)
        one_tq = trade_quality_from_rows(one_rows)
        one_dd = max_drawdown_and_streak_from_rows(one_rows)

        best_symbol = best_contributing_symbol_by_r(one_rows)
        one_excl_best = excluding_symbol(one_rows, symbol=best_symbol) if best_symbol else one_tq

        drawdown_r = float(one_dd["maximum_drawdown"]) if one_dd["maximum_drawdown"] is not None else None
        classification = classify_validation(
            one_per_day=one_tq, all_candidates=all_tq, one_per_day_excluding_best_symbol=one_excl_best,
            drawdown_r=drawdown_r, coverage_gap=None,
        )

        months = len(session_dates) / 21.0
        frequency = {
            "qualifying_candidate_days": row["sessions_with_candidate"],
            "one_trade_executed_days": len(one_per_day_signals),
            "pct_sessions_producing_a_trade": (len(one_per_day_signals) / len(session_dates)) * 100.0,
            "average_trades_per_month": len(one_per_day_signals) / months if months else None,
        }

        symbol_contrib = symbol_summary(one_rows)

        config_results[label] = {
            "combo": {"gap_percent": str(entry.combo.gap_percent), "rvol_multiple": str(entry.combo.rvol_multiple)},
            "all_candidates": all_tq, "one_trade_per_day": one_tq,
            "one_trade_per_day_maximum_drawdown_r": drawdown_r,
            "one_trade_per_day_longest_losing_streak": one_dd["longest_losing_streak"],
            "one_trade_per_day_excluding_best_r_symbol": one_excl_best, "best_r_contributing_symbol": best_symbol,
            "symbol_summary": symbol_contrib, "frequency": frequency, "classification": classification,
        }
        print(f"[P_MINUS_1] {label}: all_candidates closed={all_tq['closed_trades']} exp_r={all_tq['expectancy_r_per_trade']} | one_per_day closed={one_tq['closed_trades']} exp_r={one_tq['expectancy_r_per_trade']} verdict={classification['verdict']}")

    empirical_elapsed_seconds = (datetime.now(timezone.utc) - empirical_start).total_seconds()
    decision = recommend_next_milestone(
        primary_verdict=config_results[PRIMARY_CONFIG_LABEL]["classification"]["verdict"],
        challenger_verdict=config_results[CHALLENGER_CONFIG_LABEL]["classification"]["verdict"],
    )
    print(f"NEXT MILESTONE: {decision['next_milestone']}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_independent_validation_report.json").write_text(
        json.dumps(
            {
                "status": "COMPLETED",
                "p_minus_1": {"first_session": session_dates[0].isoformat(), "last_session": session_dates[-1].isoformat(), "sessions": len(session_dates)},
                "universe": list(VALIDATION_UNIVERSE), "configurations": config_results,
                "next_milestone": decision, "empirical_runtime_seconds": empirical_elapsed_seconds,
                "implementation_digest": implementation_digest,
                "criteria": {
                    "minimum_one_per_day_closed_trades": DEFAULT_VALIDATION_CRITERIA.minimum_one_per_day_closed_trades,
                    "all_candidates_not_materially_negative_threshold": DEFAULT_VALIDATION_CRITERIA.all_candidates_not_materially_negative_threshold,
                    "maximum_acceptable_drawdown_r": DEFAULT_VALIDATION_CRITERIA.maximum_acceptable_drawdown_r,
                },
            },
            indent=2, sort_keys=True, default=str,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
