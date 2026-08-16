"""CLI entry point for the RVOL 1.50x-1.99x band third-period validation
study (Issue #317).

Research-only, validation (not optimization). The merged regime diagnostic
(Issue #310/#315, PR #316) found that restricting RVOL to the 1.50x-1.99x
range specifically improved expectancy for configs B (gap>=0.75%/RVOL base
1.50x) and C (gap>=0.50%/RVOL base 1.50x) in both the 90-session discovery
period and the 252-session out-of-sample period. This script measures the
identical band, on the identical two configurations, on a THIRD
independent period: the ~252 sessions immediately preceding the
out-of-sample period's own first session (2025-04-03), derived from the
research-only historical calendar rather than hardcoded.

No threshold tuning, no new filters, no gap/timing/SPY-rule variation, and
no neighboring-band search -- see `src/orb_rvol_band_third_period_research.py`
for this study's own logic. Never writes `config/strategy_v0.yaml`, never
sets `strategy.enabled`, never calls a broker.

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
from src.market_data_canonical import MarketDataFreshnessPolicy, session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, matrix_row, precompute_inputs_table
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_rvol_band_third_period_research import (
    PRIOR_PERIOD_BAND_REFERENCE,
    STUDY_CONFIGS,
    band_candidate_signals,
    band_effect,
    cell_metrics,
    classify_band_validation,
    final_decision,
    three_period_comparison,
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

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-rvol-band-third-period" / "results"

#: The third period: the `THIRD_PERIOD_SESSIONS` research-calendar sessions
#: strictly before this reference day -- the out-of-sample study's own
#: first session, so the third period ends exactly where the OOS period
#: begins with zero gap and zero overlap by construction.
THIRD_PERIOD_REFERENCE_DAY = date(2025, 4, 3)
THIRD_PERIOD_SESSIONS_PREFERRED = 252
THIRD_PERIOD_SESSIONS_FALLBACK = 180
THIRD_PERIOD_SESSIONS_MINIMUM = 120

#: Prior two periods, restated only for the non-overlap assertion below --
#: never re-fetched or re-simulated here (see
#: `src/orb_rvol_band_third_period_research.py::PRIOR_PERIOD_BAND_REFERENCE`
#: for their already-measured BAND figures, hardcoded from the merged #316
#: artifact).
OOS_REFERENCE_DAY = date(2026, 4, 7)
OOS_SESSIONS = 252
DISCOVERY_REFERENCE_DAY = date(2026, 8, 14)
DISCOVERY_SESSIONS = 90

#: Reaches back into 2024 (this period's own lookback floor is ~2024-03) --
#: the 800-day widened policy Issues #304/#310 used for their own reach into
#: 2025 is NOT wide enough here (2024-03 is ~900 days before a 2026-08 run
#: date, so 800 days silently dropped everything before ~2024-06-07 as
#: STALE, which is exactly what starved liquidity qualification down to zero
#: symbols on the first run of this script -- caught and fixed before any
#: reported result, never a silent truncation). 1100 days covers back to
#: ~2023-08, comfortably past this period's own floor with margin.
THIRD_PERIOD_FRESHNESS_POLICY = MarketDataFreshnessPolicy(
    version="orb-rvol-band-third-period-v1", max_age_seconds=1100 * 86400.0,
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


def _select_third_period_dates() -> tuple[date, ...]:
    """Preferred 252 sessions; falls back to 180, then the absolute
    minimum 120, only if the research calendar cannot support the larger
    window -- never a silent guess at which dates to use."""
    from src.orb_research_historical_calendar import ResearchCalendarError

    for count in (THIRD_PERIOD_SESSIONS_PREFERRED, THIRD_PERIOD_SESSIONS_FALLBACK, THIRD_PERIOD_SESSIONS_MINIMUM):
        try:
            dates = research_trading_sessions_before(THIRD_PERIOD_REFERENCE_DAY, count=count)
        except ResearchCalendarError:
            continue
        print(f"third period: {count} sessions requested, calendar coverage sufficient -> using {count}")
        return dates
    raise SystemExit(
        f"REFUSED: research calendar cannot support even the {THIRD_PERIOD_SESSIONS_MINIMUM}-session "
        "absolute minimum ending before " + THIRD_PERIOD_REFERENCE_DAY.isoformat()
    )


async def _fetch_period_bars(
    *, session_dates: tuple[date, ...], cache_dir: Path, fetch_missing: bool, feed: str,
) -> dict[str, list]:
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
            raw_bars, symbol=symbol, now=now(), freshness_policy=THIRD_PERIOD_FRESHNESS_POLICY,
            session_bounds_provider=research_regular_session_bounds,
        )
    return market_bars_by_symbol


def _liquidity_qualify(*, market_bars_by_symbol: dict[str, list], session_dates: tuple[date, ...]) -> tuple[str, ...]:
    """Liquidity-qualified universe computed entirely from bars observed
    within this study's own third-period session dates -- the same
    non-look-ahead methodology the corrected OOS study (#304/#307/#314)
    and the regime diagnostic (#310/#315/#316) already use. Documented
    limitation: the candidate pool (`all_pool_symbols_with_production`,
    ~100 mega/large-cap symbols) is a static, hand-curated list authored
    2026-08, after this third period -- a symbol's presence in the pool
    could in principle reflect information not perfectly available in real
    time within 2024-2025. The ranking *within* the pool is genuinely
    computed from in-period data only."""
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    pool_symbols = list(all_pool_symbols_with_production())
    resolver_kwargs = {"session_open_resolver": partial(session_open_for, session_bounds_provider=research_regular_session_bounds)}
    profiles = {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=[b.to_minute_bar() for b in market_bars_by_symbol[symbol]],
            session_dates=session_dates, lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
            **resolver_kwargs,
        )
        for symbol in pool_symbols
    }
    return rank_liquidity_qualified_symbols(profiles)


async def _evaluate_third_period(
    *, session_dates: tuple[date, ...], cache_dir: Path, fetch_missing: bool, feed: str,
    implementation_digest: str, created_at: datetime,
) -> dict:
    print(f"[THIRD_PERIOD] fetching/loading {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]})")
    market_bars_by_symbol = await _fetch_period_bars(
        session_dates=session_dates, cache_dir=cache_dir, fetch_missing=fetch_missing, feed=feed,
    )
    research_universe = _liquidity_qualify(market_bars_by_symbol=market_bars_by_symbol, session_dates=session_dates)
    print(f"[THIRD_PERIOD] liquidity-qualified universe: {len(research_universe)} symbols -> {research_universe}")

    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}
    resolver_kwargs = {"session_open_resolver": partial(session_open_for, session_bounds_provider=research_regular_session_bounds)}
    inputs_table = precompute_inputs_table(
        universe=research_universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(), **resolver_kwargs,
    )

    cells: dict[str, dict] = {}
    coverage_by_label: dict[str, dict] = {}
    for label, combo in STUDY_CONFIGS.items():
        evaluation = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=session_dates,
            inputs_table=inputs_table, base_config=load_config(),
        )
        row = matrix_row(evaluation)
        coverage_by_label[label] = {"coverage_pct": row["coverage_pct"], "coverage_status": row["coverage_status"]}
        base_signals = evaluation.candidate_signals
        band_signals = band_candidate_signals(base_signals)

        base_metrics = cell_metrics(
            cell_label=f"{label}_BASE", combo=combo, candidate_signals=base_signals, total_sessions=len(session_dates),
            market_bars_by_symbol=market_bars_by_symbol, base_config=load_config(),
            implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )
        band_metrics = cell_metrics(
            cell_label=f"{label}_BAND", combo=combo, candidate_signals=band_signals, total_sessions=len(session_dates),
            market_bars_by_symbol=market_bars_by_symbol, base_config=load_config(),
            implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )
        cells[f"{label}_BASE"] = base_metrics
        cells[f"{label}_BAND"] = band_metrics
        print(
            f"[THIRD_PERIOD] {label}: BASE candidates={len(base_signals)} closed={base_metrics['trade_quality']['trades_closed']} "
            f"| BAND candidates={len(band_signals)} closed={band_metrics['trade_quality']['trades_closed']}"
        )

    return {
        "session_dates": session_dates, "universe": research_universe, "cells": cells,
        "coverage_by_config": coverage_by_label,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true", help="allow a network fetch for cache gaps (requires Alpaca credentials); default is cache-only, fails closed on any miss")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    integrity = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity: {len(integrity['kept'])} usable, {len(integrity['removed'])} removed")

    third_period_dates = _select_third_period_dates()
    oos_dates = research_trading_sessions_before(OOS_REFERENCE_DAY, count=OOS_SESSIONS)
    discovery_dates = trading_sessions_before(DISCOVERY_REFERENCE_DAY, count=DISCOVERY_SESSIONS)
    if set(third_period_dates) & set(oos_dates):
        raise SystemExit("third period overlaps the out-of-sample period")
    if set(third_period_dates) & set(discovery_dates):
        raise SystemExit("third period overlaps the discovery period")
    print(
        f"third period: {third_period_dates[0]}..{third_period_dates[-1]} ({len(third_period_dates)} sessions), "
        "zero overlap with OOS and discovery periods confirmed"
    )

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    third_period = asyncio.run(_evaluate_third_period(
        session_dates=third_period_dates, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing,
        feed=args.feed, implementation_digest=implementation_digest, created_at=created_at,
    ))

    print("Building RVOL band third-period validation report...")
    cells = third_period["cells"]
    band_effects = {label: band_effect(base=cells[f"{label}_BASE"], band=cells[f"{label}_BAND"]) for label in STUDY_CONFIGS}
    comparisons = {label: three_period_comparison(label=label, third_period_band_metrics=cells[f"{label}_BAND"]) for label in STUDY_CONFIGS}
    classifications = {
        label: classify_band_validation(
            label=label, band_metrics=cells[f"{label}_BAND"], base_metrics=cells[f"{label}_BASE"],
            reference=PRIOR_PERIOD_BAND_REFERENCE[label],
        )
        for label in STUDY_CONFIGS
    }
    decision = final_decision(classification_by_label=classifications)

    for label, classification in classifications.items():
        print(f"  {label}_BAND classification: {classification['verdict']}")
    print(f"FINAL DECISION: {decision['decision']}")

    report = {
        "issue": 317,
        "third_period": {
            "first_session": third_period_dates[0].isoformat(), "last_session": third_period_dates[-1].isoformat(),
            "sessions": len(third_period_dates), "universe": list(third_period["universe"]),
            "coverage_by_config": third_period["coverage_by_config"],
        },
        "out_of_sample_period_reference": {"first_session": "2025-04-03", "last_session": "2026-04-06", "sessions": OOS_SESSIONS},
        "discovery_period_reference": {"first_session": "2026-04-07", "last_session": "2026-08-13", "sessions": DISCOVERY_SESSIONS},
        "cells": cells,
        "band_effect": band_effects,
        "three_period_comparison": comparisons,
        "classification": classifications,
        "final_decision": decision,
        "implementation_digest": implementation_digest,
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_rvol_band_third_period_validation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )

    result_rows = []
    for label in STUDY_CONFIGS:
        for view in ("BASE", "BAND"):
            cell = cells[f"{label}_{view}"]
            tq = cell["trade_quality"]
            result_rows.append({
                "config": f"{label}_{view}", "gap_percent": cell["gap_percent"], "rvol_multiple": cell["rvol_multiple"],
                "candidate_days": cell["candidate_days"], "candidate_day_rate": cell["candidate_day_rate"],
                "candidate_occurrences": cell["candidate_occurrences"], "trades_closed": tq["trades_closed"],
                "winners": tq["winners"], "losers": tq["losers"], "win_rate": tq["win_rate"],
                "expectancy_per_trade": tq["expectancy_per_trade"], "profit_factor": tq["profit_factor"],
                "cumulative_r_multiple": tq["cumulative_r_multiple"], "maximum_drawdown": tq["maximum_drawdown"],
                "stop_out_rate": tq["stop_out_rate"], "longest_losing_streak": cell["longest_losing_streak"],
            })
    with (args.output_dir / "orb_rvol_band_third_period_validation_matrix.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = list(result_rows[0].keys())
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(result_rows)

    monthly_rows = []
    for label in STUDY_CONFIGS:
        for view in ("BASE", "BAND"):
            cell = cells[f"{label}_{view}"]
            for month, stats in cell["monthly"].items():
                monthly_rows.append({"config": f"{label}_{view}", "month": month, **stats})
    with (args.output_dir / "orb_rvol_band_third_period_validation_monthly.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in monthly_rows for key in row}) if monthly_rows else ["config", "month"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(monthly_rows)

    symbol_rows = []
    for label in STUDY_CONFIGS:
        for view in ("BASE", "BAND"):
            cell = cells[f"{label}_{view}"]
            for symbol, stats in cell["symbol_contribution"].items():
                symbol_rows.append({"config": f"{label}_{view}", "symbol": symbol, **stats})
    with (args.output_dir / "orb_rvol_band_third_period_validation_symbols.csv").open("w", newline="", encoding="utf-8") as handle:
        fieldnames = sorted({key for row in symbol_rows for key in row}) if symbol_rows else ["config", "symbol"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(symbol_rows)

    print(f"JSON report: {args.output_dir / 'orb_rvol_band_third_period_validation_report.json'}")
    print(f"Matrix CSV: {args.output_dir / 'orb_rvol_band_third_period_validation_matrix.csv'} ({len(result_rows)} rows)")
    print(f"Monthly CSV: {args.output_dir / 'orb_rvol_band_third_period_validation_monthly.csv'} ({len(monthly_rows)} rows)")
    print(f"Symbol CSV: {args.output_dir / 'orb_rvol_band_third_period_validation_symbols.csv'} ({len(symbol_rows)} rows)")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except UniverseResearchError as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        raise SystemExit(2)
