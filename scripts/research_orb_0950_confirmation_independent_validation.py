"""CLI entry point for M42: independent validation of the 09:50
first-candidate confirmation policy (Issue #332).

Evaluates ONLY the single frozen Issue #325 CHALLENGER config
(`GAP_050_RVOL_175`) on ONE completely untouched period ("P_MINUS_2": 252
research-calendar sessions strictly before P_MINUS_1's own start date,
2021-03-30..2022-03-29), through exactly TWO policies:
`CURRENT_FIRST_VALID` (today's existing baseline) and
`WAITED_FIRST_VALID_0950` (reusing M41's exact, unmodified implementation
via `src.orb_0950_confirmation_independent_validation_research.
build_both_policies_rows`). No ranking, no fallback candidate, no new
timing variant, no threshold search.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss. Pass `--fetch-missing` to allow a network
fetch for cache gaps (requires `ALPACA_API_KEY_ID`/`ALPACA_API_SECRET_KEY`
from the environment only, never printed) -- expected to be required
since P_MINUS_2 is a genuinely new period, not previously fetched by any
prior milestone.
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
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY, CONFIG_LABEL
from src.orb_0950_confirmation_independent_validation_research import (
    DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA,
    POLICIES,
    POLICY_CURRENT_FIRST_VALID,
    POLICY_WAITED_FIRST_VALID_0950,
    VALIDATION_UNIVERSE,
    build_both_policies_rows,
    classify_0950_confirmation,
    derive_p_minus_2_sessions,
    recommend_next_milestone,
    wait_diagnostics,
    waited_symbol_robustness,
)
from src.orb_gap_rvol_sensitivity_research import precompute_inputs_table, evaluate_combo
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    eligible_market_bars,
    fetch_full_minute_history,
    validate_cache_integrity,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_OUTPUT_DIR = ROOT / "artifacts" / "research-0950-confirmation-independent-validation" / "results"
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"
PERIOD = "P_MINUS_2"


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


async def _fetch_p_minus_2_bars(*, session_dates: tuple, cache_dir: Path, fetch_missing: bool, feed: str) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    fetch_from = research_trading_sessions_before(session_dates[0], count=lookback_days)[0]

    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=fetch_from, now=datetime.now(timezone.utc), safety_margin_days=30,
        version="orb-0950-confirmation-independent-validation-p-minus-2-v1",
    )
    print(f"[{PERIOD}] freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} ({freshness_policy.max_age_seconds / 86400.0:.1f} days)")

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
        market_bars_by_symbol[symbol] = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy, session_bounds_provider=research_regular_session_bounds,
        )
    return market_bars_by_symbol


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--fetch-missing", action="store_true")
    parser.add_argument("--feed", default=DEFAULT_FEED)
    args = parser.parse_args(argv)

    print(f"Frozen config (Issue #325 CHALLENGER only): {CONFIG_LABEL} gap={CONFIG_ENTRY.combo.gap_percent}% rvol={CONFIG_ENTRY.combo.rvol_multiple}x")
    print(f"Policies: {POLICIES}")

    session_dates = derive_p_minus_2_sessions()
    print(f"[{PERIOD}] {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]}), universe: {VALIDATION_UNIVERSE}")

    empirical_start = datetime.now(timezone.utc)
    integrity_before = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity BEFORE: {len(integrity_before['kept'])} usable, {len(integrity_before['removed'])} removed")

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    market_bars_by_symbol = asyncio.run(_fetch_p_minus_2_bars(session_dates=session_dates, cache_dir=args.cache_dir, fetch_missing=args.fetch_missing, feed=args.feed))
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}

    integrity_after = validate_cache_integrity(args.cache_dir)
    print(f"cache integrity AFTER: {len(integrity_after['kept'])} usable, {len(integrity_after['removed'])} removed")
    print(f"new cache files this run: {len(integrity_after['kept']) - len(integrity_before['kept'])}")

    inputs_table = precompute_inputs_table(
        universe=VALIDATION_UNIVERSE, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=load_config(),
        session_open_resolver=partial(session_open_for, session_bounds_provider=research_regular_session_bounds),
    )
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=VALIDATION_UNIVERSE, session_dates=session_dates, inputs_table=inputs_table, base_config=load_config())

    rows_by_policy, current_winners, waited_enriched = build_both_policies_rows(
        period=PERIOD, evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars_by_symbol,
        base_config=load_config(), implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=research_regular_session_bounds,
    )

    empirical_elapsed_seconds = (datetime.now(timezone.utc) - empirical_start).total_seconds()
    print(f"Empirical runtime: {empirical_elapsed_seconds:.1f}s")

    tq_current = trade_quality_from_rows(rows_by_policy[POLICY_CURRENT_FIRST_VALID])
    tq_waited = trade_quality_from_rows(rows_by_policy[POLICY_WAITED_FIRST_VALID_0950])
    dd_current = max_drawdown_and_streak_from_rows(rows_by_policy[POLICY_CURRENT_FIRST_VALID])
    dd_waited = max_drawdown_and_streak_from_rows(rows_by_policy[POLICY_WAITED_FIRST_VALID_0950])
    waited_drawdown_r = float(dd_waited["maximum_drawdown"])

    diagnostics = wait_diagnostics(
        waited_enriched=waited_enriched, rows_current=rows_by_policy[POLICY_CURRENT_FIRST_VALID],
        rows_waited=rows_by_policy[POLICY_WAITED_FIRST_VALID_0950],
    )
    symbol_robustness = waited_symbol_robustness(rows_by_policy[POLICY_WAITED_FIRST_VALID_0950])

    classification = classify_0950_confirmation(
        waited_trade_quality=tq_waited, current_trade_quality=tq_current, waited_max_drawdown_r=waited_drawdown_r,
        waited_excluding_best_symbol_expectancy_r=symbol_robustness["expectancy_r_excluding_best_contributor"],
    )
    decision = recommend_next_milestone(verdict=classification["verdict"])

    months = len(session_dates) / 21.0
    frequency_current = {
        "trades_per_month": tq_current["closed_trades"] / months if months else None,
        "pct_sessions_traded": (tq_current["closed_trades"] / len(session_dates)) * 100.0,
        "r_per_month": (tq_current.get("cumulative_r") or 0) / months if months else None,
    }
    frequency_waited = {
        "trades_per_month": tq_waited["closed_trades"] / months if months else None,
        "pct_sessions_traded": (tq_waited["closed_trades"] / len(session_dates)) * 100.0,
        "r_per_month": (tq_waited.get("cumulative_r") or 0) / months if months else None,
    }

    print(f"CURRENT_FIRST_VALID: closed={tq_current['closed_trades']} exp_r={tq_current['expectancy_r_per_trade']} pf_r={tq_current['profit_factor_r']} cum_r={tq_current['cumulative_r']} drawdown_r={dd_current['maximum_drawdown']}")
    print(f"WAITED_FIRST_VALID_0950: closed={tq_waited['closed_trades']} exp_r={tq_waited['expectancy_r_per_trade']} pf_r={tq_waited['profit_factor_r']} cum_r={tq_waited['cumulative_r']} drawdown_r={dd_waited['maximum_drawdown']}")
    print(f"CLASSIFICATION: {classification['verdict']}")
    print(f"NEXT MILESTONE: {decision['next_milestone']} (core_status={decision['core_status']})")

    report = {
        "issue": 332, "config": CONFIG_LABEL, "period": PERIOD,
        "period_first_session": session_dates[0].isoformat(), "period_last_session": session_dates[-1].isoformat(),
        "period_sessions": len(session_dates), "universe": list(VALIDATION_UNIVERSE),
        "current_first_valid": {"trade_quality": tq_current, "maximum_drawdown_r": dd_current["maximum_drawdown"], "longest_losing_streak": dd_current["longest_losing_streak"], "frequency": frequency_current},
        "waited_first_valid_0950": {"trade_quality": tq_waited, "maximum_drawdown_r": dd_waited["maximum_drawdown"], "longest_losing_streak": dd_waited["longest_losing_streak"], "frequency": frequency_waited},
        "wait_diagnostics": diagnostics, "symbol_robustness": symbol_robustness,
        "classification": classification, "next_milestone": decision,
        "empirical_runtime_seconds": empirical_elapsed_seconds, "implementation_digest": implementation_digest,
        "cache_integrity_before": {"usable": len(integrity_before["kept"]), "removed": len(integrity_before["removed"])},
        "cache_integrity_after": {"usable": len(integrity_after["kept"]), "removed": len(integrity_after["removed"])},
    }

    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "orb_0950_confirmation_independent_validation_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(f"JSON report: {args.output_dir / 'orb_0950_confirmation_independent_validation_report.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
