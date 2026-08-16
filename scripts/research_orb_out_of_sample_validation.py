"""CLI entry point for the ORB out-of-sample historical validation study
(Issue #304).

Research-only. Reuses the merged liquidity-qualified universe methodology
and fetch/coverage infrastructure from Issue #298
(`src/orb_universe_expansion_research.py`), and the gap/RVOL evaluator plus
M15 shadow-execution trade-quality bridge from the merged Issue #300/#302
studies (`src/orb_gap_rvol_sensitivity_research.py`) unchanged -- see
`src/orb_out_of_sample_validation_research.py` for this study's own logic
(the five pre-registered configurations, discovery-period comparison,
candidate-multiplicity views, robustness classification). Never writes
`config/strategy_v0.yaml`, never sets `strategy.enabled` in tracked config,
never calls a broker.

Session dates for the primary out-of-sample window come from
`src.orb_research_historical_calendar.research_trading_sessions_before`
(RESEARCH-ONLY, isolated from production and the pinned checkpoint
closure), because the window reaches into 2025 -- outside
`src.exchange_calendar`'s own 2026-2027 coverage.

Two data-source modes, identical posture to
`scripts/research_orb_fine_rvol_validation.py`:

* Credentialed (default): requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
  in the environment (never `config/local.yaml`) and refuses to run without
  them rather than fabricate a result.
* `--cache-only`: reuses the existing shared on-disk bar cache
  (`artifacts/research-universe-expansion/bar-cache/`, gitignored) with an
  inner HTTP client that raises loudly on any cache miss instead of making a
  network call.

Session-count ladder: per this study's own goal ("target ~252, fallback
~180, minimum 120"), when `--sessions` is not explicitly given this script
tries 252, then 180, then 120 trailing sessions ending strictly before
`--reference-day` (default 2026-04-07), reporting the first one the
research calendar and (in `--cache-only` mode) the cache can fully satisfy.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from collections import Counter
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
from src.orb_out_of_sample_validation_research import (
    CONFIG_LABELS,
    DISCOVERY_PERIOD,
    DISCOVERY_PERIOD_REFERENCE,
    REPOSITORY_ROOT,
    RESEARCH_UNIVERSE_LABEL,
    ParameterCombo,
    aggregate_trade_quality,
    candidate_multiplicity,
    classify_robustness,
    config_matrix,
    discovery_vs_out_of_sample,
    evaluate_combo,
    final_decision,
    matrix_row,
    multiplicity_distribution,
    one_trade_per_day_vs_all_candidates,
    precompute_inputs_table,
    same_day_symbol_combinations,
    simulate_candidate_signals_detailed,
    simulate_combo_trade_quality,
)
from src.orb_research_historical_calendar import (
    ResearchCalendarError,
    research_regular_session_bounds,
    research_trading_sessions_before,
)

#: The injectable-calendar seam (Issue #305, merged) bound to the
#: RESEARCH-ONLY 2025 provider -- passed explicitly everywhere this
#: script touches session-open resolution or bar quality-gating so the
#: canonical evaluator can process out-of-sample dates outside
#: `src.exchange_calendar`'s own 2026-2027 coverage. Every default in the
#: pinned `src.market_data_canonical` functions stays
#: `regular_session_bounds`; only this research script ever supplies this.
research_session_open_for = partial(session_open_for, session_bounds_provider=research_regular_session_bounds)
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
    production_universe,
    rank_liquidity_qualified_symbols,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest
from src.market_data_canonical import MarketDataFreshnessPolicy

DEFAULT_OUTPUT_DIR = REPOSITORY_ROOT / "artifacts" / "research-out-of-sample-validation" / "results"
#: `orb_universe_expansion_research.RESEARCH_FRESHNESS_POLICY` (400 days)
#: comfortably covers Issue #298/#300/#302's <=180-day windows but not this
#: study's ~252-session window reaching back into 2025 -- run today, the
#: oldest study session is well over 400 days old. 800 days comfortably
#: covers this study's own window with headroom, without touching the
#: shared default every other research script still uses unchanged.
OUT_OF_SAMPLE_FRESHNESS_POLICY = MarketDataFreshnessPolicy(
    version="orb-out-of-sample-validation-v1", max_age_seconds=800 * 86400.0,
)
DEFAULT_REFERENCE_DAY = date(2026, 4, 7)
#: "target ~252 sessions, fallback ~180, minimum 120" -- tried in this order
#: when `--sessions` is not explicitly given.
SESSION_LADDER: tuple[int, ...] = (252, 180, 120)
ABSOLUTE_MINIMUM_SESSIONS = 120
DEFAULT_WARMUP_MULTIPLIER = 1
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"


class _RaiseOnCacheMiss:
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


def _research_historical_fetch_start(first_session_date: date, *, lookback_days: int, warmup_multiplier: int) -> date:
    """Research-calendar equivalent of
    `orb_universe_expansion_research.historical_fetch_start`, using
    `research_trading_sessions_before` so the RVOL-lookback warmup window
    can reach into 2025."""
    return research_trading_sessions_before(first_session_date, count=lookback_days * warmup_multiplier)[0]


async def _run(
    *, session_count: int, reference_day: date, output_dir: Path, cache_dir: Path, cache_only: bool,
    feed: str, warmup_multiplier: int,
) -> dict:
    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    session_dates = research_trading_sessions_before(reference_day, count=session_count)
    if session_dates[-1] >= date(2026, 4, 7):
        raise SystemExit("out-of-sample window must end before the discovery period (2026-04-07)")
    fetch_from = _research_historical_fetch_start(
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
        session_dates[-1], research_session_open_for(session_dates[-1]), tzinfo=EASTERN,
    ).astimezone(timezone.utc) + timedelta(hours=8)

    pool_symbols = list(all_pool_symbols_with_production())
    fetch_symbols = pool_symbols + ["SPY"]
    print(
        f"studying {len(session_dates)} out-of-sample sessions "
        f"({session_dates[0].isoformat()} to {session_dates[-1].isoformat()}), "
        f"ending before discovery period start 2026-04-07; "
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
        market_bars = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), session_bounds_provider=research_regular_session_bounds,
            freshness_policy=OUT_OF_SAMPLE_FRESHNESS_POLICY,
        )
        market_bars_by_symbol[symbol] = market_bars
        minute_bars_by_symbol[symbol] = [bar.to_minute_bar() for bar in market_bars]
        print(f"fetched {symbol} {index}/{len(fetch_symbols)}: {len(market_bars)} valid minute bars")

    print(f"Phase 1/3: liquidity-qualifying {len(pool_symbols)} pool symbols against the out-of-sample period itself")
    profiles = {
        symbol: compute_symbol_liquidity_profile(
            symbol=symbol, bars=minute_bars_by_symbol[symbol], session_dates=session_dates,
            lookback_days=lookback_days, thresholds=DEFAULT_LIQUIDITY_THRESHOLDS,
            session_open_resolver=research_session_open_for,
        )
        for symbol in pool_symbols
    }
    research_universe = rank_liquidity_qualified_symbols(profiles)
    production = production_universe()
    print(
        f"liquidity-qualified out-of-sample universe: {len(research_universe)} of "
        f"{len(pool_symbols)} pool symbols -> {research_universe}"
    )
    print(f"current production universe (context only): {len(production)} symbols -> {production}")

    print("Phase 2/3: evaluating the five pre-registered configurations")
    # Every configuration is evaluated over research_universe only (never
    # production directly -- config A, the production baseline pair, is
    # itself one of the five evaluated points), so precomputing inputs for
    # production symbols outside research_universe would compute
    # per-session RVOL-lookback data (~1.4s/session/symbol at this
    # ~252-session, ~90k-bar-per-symbol scale) that evaluate_combo never
    # reads. Restricting to research_universe avoids that wasted work.
    inputs_table = precompute_inputs_table(
        universe=research_universe, session_dates=session_dates,
        minute_bars_by_symbol=minute_bars_by_symbol, spy_minute_bars=minute_bars_by_symbol["SPY"],
        base_config=config, session_open_resolver=research_session_open_for,
    )
    evaluations_by_combo: dict[ParameterCombo, object] = {}
    for combo in config_matrix():
        evaluations_by_combo[combo] = evaluate_combo(
            combo=combo, universe=research_universe, session_dates=session_dates,
            inputs_table=inputs_table, base_config=config,
        )
        row = evaluations_by_combo[combo].universe_report
        print(
            f"  [{CONFIG_LABELS[combo]}] gap={combo.gap_percent}% rvol={combo.rvol_multiple}x: "
            f"coverage={row['coverage']['completeness']:.2%} ({row['coverage']['status']}), "
            f"candidate_day_rate={row['candidate_day_rate']:.2%}, "
            f"total_candidates={row['total_candidates']}"
        )

    rows_by_combo = {combo: matrix_row(evaluations_by_combo[combo]) for combo in config_matrix()}

    print("Trade quality: replaying every CANDIDATE through the merged shadow-execution simulator")
    implementation_digest = checkpoint_implementation_digest(REPOSITORY_ROOT)
    created_at = now()
    trade_quality_by_combo = {}
    for combo in config_matrix():
        trade_quality_by_combo[combo] = simulate_combo_trade_quality(
            evaluation=evaluations_by_combo[combo], market_bars_by_symbol=market_bars_by_symbol,
            base_config=config, implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )
        tq = trade_quality_by_combo[combo]
        print(
            f"  [{CONFIG_LABELS[combo]}] {tq['status']}, trades_closed={tq['trades_closed']}, "
            f"win_rate={tq['win_rate']}, expectancy={tq['expectancy_per_trade']}, pf={tq['profit_factor']}"
        )

    print("Validation: proving the Issue #311 calendar seam removed the 2025 DATA_INVALID truncation")
    validation_by_combo = {}
    for combo in config_matrix():
        _, raw_results = simulate_candidate_signals_detailed(
            candidate_signals=evaluations_by_combo[combo].candidate_signals, combo=combo,
            market_bars_by_symbol=market_bars_by_symbol, base_config=config,
            implementation_digest=implementation_digest, created_at=created_at,
            run_label="validation", session_bounds_provider=research_regular_session_bounds,
        )
        state_counts = Counter(r.state.value for r in raw_results)
        data_invalid_reasons = Counter(r.reason_code for r in raw_results if r.state.value == "DATA_INVALID")
        closed_by_month = Counter(
            r.trading_date.strftime("%Y-%m") for r in raw_results if r.realized_pnl is not None
        )
        validation_by_combo[combo] = {
            "state_distribution": dict(sorted(state_counts.items())),
            "data_invalid_reason_distribution": dict(sorted(data_invalid_reasons.items())),
            "closed_trades_by_month": dict(sorted(closed_by_month.items())),
        }
        market_holiday_data_invalid = data_invalid_reasons.get("MARKET_HOLIDAY", 0)
        print(
            f"  [{CONFIG_LABELS[combo]}] states={dict(sorted(state_counts.items()))}, "
            f"MARKET_HOLIDAY-attributed DATA_INVALID={market_holiday_data_invalid}"
        )
        print(f"  [{CONFIG_LABELS[combo]}] closed trades by month: {dict(sorted(closed_by_month.items()))}")

    print("One-trade-per-day vs all-candidates: replaying both views per configuration")
    one_vs_all_by_combo = {}
    for combo in config_matrix():
        one_vs_all_by_combo[combo] = one_trade_per_day_vs_all_candidates(
            combo=combo, evaluation=evaluations_by_combo[combo], market_bars_by_symbol=market_bars_by_symbol,
            base_config=config, implementation_digest=implementation_digest, created_at=created_at,
            session_bounds_provider=research_regular_session_bounds,
        )
        ova = one_vs_all_by_combo[combo]
        print(
            f"  [{CONFIG_LABELS[combo]}] ALL_CANDIDATES trades={ova['ALL_CANDIDATES']['trade_quality']['trades_closed']} "
            f"vs ONE_TRADE_PER_DAY trades={ova['ONE_TRADE_PER_DAY']['trade_quality']['trades_closed']}"
        )

    print("Phase 3/3: multiplicity views, discovery comparison, and robustness classification")
    multiplicity_by_combo = {combo: candidate_multiplicity(evaluations_by_combo[combo]) for combo in config_matrix()}
    distribution_by_combo = {
        combo: multiplicity_distribution(evaluations_by_combo[combo], session_dates=session_dates)
        for combo in config_matrix()
    }
    combinations_by_combo = {combo: same_day_symbol_combinations(evaluations_by_combo[combo]) for combo in config_matrix()}

    comparison_by_label = {}
    for combo in config_matrix():
        label = CONFIG_LABELS[combo]
        comparison_by_label[label] = discovery_vs_out_of_sample(
            label=label, oos_row=rows_by_combo[combo], oos_trade_quality=trade_quality_by_combo[combo],
        )

    classification_by_label = {}
    for combo in config_matrix():
        label = CONFIG_LABELS[combo]
        discovery_rate = DISCOVERY_PERIOD_REFERENCE[label]["candidate_day_rate"]
        classification_by_label[label] = classify_robustness(
            row=rows_by_combo[combo], trade_quality=trade_quality_by_combo[combo],
            discovery_candidate_day_rate=discovery_rate,
        )
        print(f"  [{label}] robustness: {classification_by_label[label]['verdict']}")

    decision = final_decision(classification_by_label=classification_by_label)
    print(f"  FINAL DECISION: {decision['decision']}")

    report = {
        "issue": 313,
        "corrects_issue": 304,
        "corrects_pr": 307,
        "correction_root_cause_issue": 311,
        "correction_note": (
            "This report supersedes the trade-quality figures in the merged Issue #304/PR #307 "
            "artifact. That prior run never supplied a session_bounds_provider into "
            "simulate_shadow_execution, so every subsequent bar on a 2025 date was classified "
            "MARKET_HOLIDAY against the production-only calendar and the whole simulated trade "
            "returned DATA_INVALID -- silently excluded from 'closed trades.' Candidate selection "
            "(candidate days/rate, universe) was unaffected and is expected to match #307 exactly; "
            "only trade-quality figures (expectancy, profit factor, win rate, drawdown, robustness) "
            "changed. See docs/research/ORB_OUT_OF_SAMPLE_VALIDATION_CORRECTION.md for the full "
            "old-vs-corrected comparison."
        ),
        "discovery_period": DISCOVERY_PERIOD,
        "out_of_sample_period": {
            "reference_day": reference_day.isoformat(),
            "first_session": session_dates[0].isoformat(),
            "last_session": session_dates[-1].isoformat(),
            "sessions": len(session_dates),
            "session_ladder_target": list(SESSION_LADDER),
            "absolute_minimum_sessions": ABSOLUTE_MINIMUM_SESSIONS,
            "calendar_source": "src.orb_research_historical_calendar (RESEARCH-ONLY; isolated from production runtime)",
        },
        "data_source": {
            "mode": "cache-only" if cache_only else "credentialed",
            "cache_dir": str(cache_dir),
            "feed": feed,
            "warmup_multiplier": warmup_multiplier,
            "fetch_from": fetch_from.isoformat(),
        },
        "universe": {
            "label": RESEARCH_UNIVERSE_LABEL,
            "symbols": list(research_universe),
            "size": len(research_universe),
            "selection_methodology": (
                "Liquidity-qualified ranking (src.orb_universe_expansion_research."
                "rank_liquidity_qualified_symbols / compute_symbol_liquidity_profile), "
                "computed entirely from bars observed within this study's own out-of-sample "
                "session_dates -- not from the discovery-period universe and not hardcoded. "
                "The candidate pool it ranks (UNIVERSE_POOL) is a static, hand-curated list of "
                "mega/large-cap US equities authored 2026-08 from general market-cap knowledge; "
                "it is not derived from either period's own liquidity data, but its authorship "
                "date is after the out-of-sample period, so a symbol's tier placement could in "
                "principle reflect information not available in real time within that period. "
                "This is the unavoidable universe-selection limitation the study goal document "
                "asked to be documented explicitly."
            ),
        },
        "current_production_universe": {"symbols": list(production), "size": len(production)},
        "liquidity_thresholds": {
            "min_median_price": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_median_price),
            "min_avg_daily_dollar_volume": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_avg_daily_dollar_volume),
            "min_avg_daily_share_volume": str(DEFAULT_LIQUIDITY_THRESHOLDS.min_avg_daily_share_volume),
            "min_session_opening_range_complete_pct": DEFAULT_LIQUIDITY_THRESHOLDS.min_session_opening_range_complete_pct,
            "min_rvol_history_satisfiable_pct": DEFAULT_LIQUIDITY_THRESHOLDS.min_rvol_history_satisfiable_pct,
        },
        "configurations": {
            CONFIG_LABELS[combo]: {
                "gap_percent": str(combo.gap_percent), "rvol_multiple": str(combo.rvol_multiple),
                "row": rows_by_combo[combo],
                "trade_quality": trade_quality_by_combo[combo],
                "candidate_multiplicity": multiplicity_by_combo[combo],
                "multiplicity_distribution": distribution_by_combo[combo],
                "common_same_day_symbol_combinations": combinations_by_combo[combo],
                "one_trade_per_day_vs_all_candidates": one_vs_all_by_combo[combo],
                "calendar_truncation_validation": validation_by_combo[combo],
            }
            for combo in config_matrix()
        },
        "discovery_vs_out_of_sample": comparison_by_label,
        "robustness_classification": classification_by_label,
        "final_decision": decision,
        "implementation_digest": implementation_digest,
    }

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "orb_out_of_sample_validation_study.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")

    csv_path = output_dir / "orb_out_of_sample_validation_matrix.csv"
    fieldnames = [
        "config", "gap_percent", "rvol_multiple", "sessions_studied", "coverage_pct", "coverage_status",
        "sessions_with_candidate", "candidate_day_rate", "total_candidates", "trades_closed", "win_rate",
        "expectancy_per_trade", "profit_factor", "maximum_drawdown", "robustness_verdict",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for combo in config_matrix():
            label = CONFIG_LABELS[combo]
            row = rows_by_combo[combo]
            tq = trade_quality_by_combo[combo]
            writer.writerow({
                "config": label, "gap_percent": row["gap_percent"], "rvol_multiple": row["rvol_multiple"],
                "sessions_studied": row["sessions_studied"], "coverage_pct": row["coverage_pct"],
                "coverage_status": row["coverage_status"], "sessions_with_candidate": row["sessions_with_candidate"],
                "candidate_day_rate": row["candidate_day_rate"], "total_candidates": row["total_candidates"],
                "trades_closed": tq["trades_closed"], "win_rate": tq["win_rate"],
                "expectancy_per_trade": tq["expectancy_per_trade"], "profit_factor": tq["profit_factor"],
                "maximum_drawdown": tq["maximum_drawdown"],
                "robustness_verdict": classification_by_label[label]["verdict"],
            })

    print(f"JSON report: {json_path}")
    print(f"CSV matrix: {csv_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions", type=int, default=None,
        help="trailing out-of-sample sessions to study; omit to try the 252/180/120 ladder",
    )
    parser.add_argument(
        "--reference-day", type=date.fromisoformat, default=DEFAULT_REFERENCE_DAY,
        help="out-of-sample sessions end strictly before this date (default 2026-04-07, the discovery-period start)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="shared with the Issue #298/#300/#302 bar cache")
    parser.add_argument("--cache-only", action="store_true", help="reuse the existing bar cache with no network call; fails closed on any cache miss")
    parser.add_argument("--feed", choices=sorted(SUPPORTED_FEEDS), default=DEFAULT_FEED)
    parser.add_argument("--warmup-multiplier", type=int, default=DEFAULT_WARMUP_MULTIPLIER)
    args = parser.parse_args(argv)

    if args.sessions is not None and args.sessions < ABSOLUTE_MINIMUM_SESSIONS:
        print(f"refusing to run below the {ABSOLUTE_MINIMUM_SESSIONS}-session absolute minimum", file=sys.stderr)
        return 2

    session_ladder = [args.sessions] if args.sessions is not None else list(SESSION_LADDER)

    for attempt_sessions in session_ladder:
        is_last_rung = attempt_sessions == session_ladder[-1]
        try:
            asyncio.run(_run(
                session_count=attempt_sessions, reference_day=args.reference_day, output_dir=args.output_dir,
                cache_dir=args.cache_dir, cache_only=args.cache_only, feed=args.feed,
                warmup_multiplier=args.warmup_multiplier,
            ))
            return 0
        except SystemExit as exc:
            if not is_last_rung and CACHE_MISS_MARKER in str(exc):
                print(f"{attempt_sessions}-session request exceeds cached range; falling back to the next rung", file=sys.stderr)
                continue
            raise
        except (UniverseResearchError, ResearchCalendarError) as exc:
            if not is_last_rung:
                print(f"{attempt_sessions}-session request exceeds supported calendar coverage ({exc}); falling back", file=sys.stderr)
                continue
            raise

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
