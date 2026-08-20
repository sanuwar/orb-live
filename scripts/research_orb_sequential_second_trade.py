"""CLI entry point for M47: sequential second-trade large-cap ORB
feasibility (Issue #346).

Reuses the exact M43/M45 period/universe/fetch wiring (`_period_universe`,
`_session_dates_for`, `_session_bounds_provider_for`, `_fetch_period_bars`
-- replicated here following the same established per-milestone-CLI
convention M44/M45/M46 each already use, not a change to any of them) and
the exact frozen `GAP_050_RVOL_175` + `WAITED_FIRST_VALID_0950` +
`BE_AFTER_050R` baseline machinery
(`src.orb_profit_protection_exit_research._waited_shadow_decision`,
`simulate_exit_arm`) via the new orchestration in
`src.orb_sequential_second_trade_research`.

`--cache-only` (default): reuses the existing shared on-disk bar cache;
fails closed on any cache miss, per this milestone's own requirement to
stop and report a genuine cache gap rather than silently refetching a
different dataset.
"""
from __future__ import annotations

import argparse
import asyncio
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
    VALIDATION_UNIVERSE as P_MINUS_2_UNIVERSE,
    derive_p_minus_2_sessions,
)
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY, early_pool, enrich_with_decision_time
from src.orb_existing_configs_reevaluation_research import PERIOD_NATIVE_UNIVERSES, PERIOD_SESSION_SPEC
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table, sensitivity_config
from src.orb_independent_single_trade_core_validation_research import VALIDATION_UNIVERSE as P_MINUS_1_UNIVERSE, derive_p_minus_1_sessions
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_sequential_second_trade_research import build_day_results
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    eligible_market_bars,
    fetch_full_minute_history,
    trading_sessions_before,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

DEFAULT_RESULTS_DIR = ROOT / "artifacts" / "research-sequential-second-trade" / "results"
CHECKPOINT_CACHE_DIR = ROOT / "artifacts" / "research-sequential-second-trade" / "period-checkpoint"
CACHE_MISS_MARKER = "CACHE_MISS_IN_CACHE_ONLY_MODE"
PERIODS: tuple[str, ...] = ("P_MINUS_2", "P_MINUS_1", "P0", "P1", "P2", "P3")
RESEARCH_CALENDAR_PERIODS = {"P_MINUS_2", "P_MINUS_1"}


class _RaiseOnCacheMiss:
    async def get(self, url: str, *, headers, params):  # noqa: ANN001
        raise SystemExit(
            f"{CACHE_MISS_MARKER}: no cached response for this request "
            f"(url={url}, params={params}). This milestone requires cache-only reuse; "
            f"report the exact gap rather than silently fetching a different dataset."
        )


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        print("ALPACA_MARKET_DATA_CREDENTIALS_MISSING", file=sys.stderr)
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
        version=f"orb-m47-sequential-second-trade-{period}-v1",
    )

    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")
        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=cache_dir)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
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
        market_bars_by_symbol[symbol] = eligible_market_bars(raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy, **kwargs)
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
    base_config = load_config()
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=universe, session_dates=session_dates, inputs_table=inputs_table, base_config=base_config)

    early_winners = select_first_candidate_per_day(early_pool(evaluation.candidate_signals))
    waited_candidates = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)

    # The frozen GAP_050_RVOL_175 combo, not the repository's raw default
    # config (minimum_gap_percent=1.0/minimum_relative_volume=3.0) -- the
    # WAITED 09:50 re-check and the second-trade scan must re-evaluate
    # against the exact same thresholds `evaluate_combo` used to produce
    # the candidate pool above, or the fresh SignalEngine re-checks reject
    # candidates that were genuinely valid under the frozen baseline.
    combo_config = sensitivity_config(base_config, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)

    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    day_results = build_day_results(
        period=period, waited_baseline_candidates=waited_candidates, universe=universe,
        inputs_table=inputs_table, combo_config=combo_config, strategy_version=f"{base_config['strategy']['name']}-{base_config['strategy']['version']}",
        implementation_digest=implementation_digest, created_at=created_at, market_bars_by_symbol=market_bars_by_symbol,
        **kwargs,
    )

    baseline_closed = sum(1 for d in day_results if d.baseline_row and d.baseline_row.outcome["closed"])
    second_closed = sum(1 for d in day_results if d.second_row and d.second_row.outcome["closed"])
    print(f"[{period}] baseline_days={len(day_results)} baseline_closed={baseline_closed} second_closed={second_closed}")

    return {"period": period, "session_dates": session_dates, "universe": universe, "day_results": day_results}


def _checkpoint_path(period: str) -> Path:
    return CHECKPOINT_CACHE_DIR / f"{period}.pkl"


def _load_period_checkpoint(period: str) -> dict | None:
    path = _checkpoint_path(period)
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return pickle.load(fh)


def _save_period_checkpoint(period: str, result: dict) -> None:
    CHECKPOINT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _checkpoint_path(period)
    tmp = path.with_suffix(".pkl.tmp")
    with tmp.open("wb") as fh:
        pickle.dump(result, fh)
    tmp.replace(path)


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-missing", action="store_true", default=False)
    parser.add_argument("--periods", nargs="*", default=list(PERIODS))
    args = parser.parse_args(argv)

    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    for period in args.periods:
        cached = _load_period_checkpoint(period)
        if cached is not None:
            print(f"[{period}] using checkpoint")
            continue
        result = await _evaluate_period(
            period=period, cache_dir=DEFAULT_CACHE_DIR, fetch_missing=args.fetch_missing, feed=DEFAULT_FEED,
            implementation_digest=implementation_digest, created_at=created_at,
        )
        _save_period_checkpoint(period, result)

    print("Simulation stage complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
