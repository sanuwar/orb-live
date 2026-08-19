"""M46 stage 4: the frozen feasibility experiment itself.

Runs the SAME frozen config (GAP_050_RVOL_175 + WAITED_FIRST_VALID_0950 +
BE_AFTER_050R) over the SAME P3 sessions (2026-04-07..2026-08-13) against
two universes:

  - SMALL_MID: the verified 20-symbol basket from stage 3
    (artifacts/research-small-midcap-momentum-lane/selection/final_basket.csv)
  - LARGE_CAP: the existing 15-symbol production universe, as a REFERENCE
    only -- never combined with SMALL_MID trades, never a second trade/day.

Reuses the unmodified M41-M43 pipeline
(`precompute_inputs_table`/`evaluate_combo`/`build_all_entry_and_exit_rows`)
exactly as `scripts/research_orb_profit_protection_exit.py` does, just with
two different `universe` arguments instead of period-native ones. Only the
`BE_AFTER_050R` arm is computed (the milestone does not need BASELINE/
PROFIT_LADDER/STRUCTURE_TRAIL_AFTER_1R here).

Checkpointed per lane so a bug in downstream reporting never forces
redoing the empirical simulation.
"""

from __future__ import annotations

import asyncio
import csv
import json
import os
import pickle
import statistics
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window
from src.orb_baseline_stability_research import excluding_symbol
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table
from src.orb_profit_protection_exit_research import (
    ARM_BE_AFTER_050R,
    ENTRY_WAITED_FIRST_VALID_0950,
    build_all_entry_and_exit_rows,
    full_stop_out_rate,
    max_drawdown_r_and_streak_from_rows,
    realized_r_distribution,
)
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows
from src.orb_small_midcap_momentum_research import research_config_for_universe
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    eligible_market_bars,
    fetch_full_minute_history,
    production_universe,
    trading_sessions_before,
)
from src.read_only_authorization_manifest import checkpoint_implementation_digest

BASE = ROOT / "artifacts" / "research-small-midcap-momentum-lane"
CHECKPOINT_DIR = BASE / "experiment-checkpoint"
RESULTS_DIR = BASE / "experiment-results"
FINAL_BASKET_CSV = BASE / "selection" / "final_basket.csv"

P3_REFERENCE_DAY = date(2026, 8, 14)
P3_SESSION_COUNT = 90


def load_small_mid_basket() -> tuple[tuple[str, ...], dict[str, dict]]:
    with FINAL_BASKET_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    universe = tuple(r["resolved_ticker"] for r in rows)
    meta = {r["resolved_ticker"]: r for r in rows}
    return universe, meta


async def _fetch_universe_bars(*, universe: tuple[str, ...], session_dates: tuple[date, ...], cache_dir: Path, credentials: AlpacaCredentials, feed: str) -> dict[str, list]:
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    fetch_from = trading_sessions_before(session_dates[0], count=lookback_days)[0]

    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=fetch_from, now=datetime.now(timezone.utc), safety_margin_days=30,
        version="orb-m46-feasibility-P3-v1",
    )
    print(f"freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} ({freshness_policy.max_age_seconds / 86400.0:.1f} days)")

    transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir)
    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end_time = session_open_for(session_dates[-1])
    end = datetime.combine(session_dates[-1], end_time, tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

    symbols = list(universe) + ["SPY"]
    market_bars_by_symbol: dict[str, list] = {}
    for i, symbol in enumerate(symbols):
        raw_bars = await fetch_full_minute_history(transport, credentials=credentials, symbol=symbol, start=start, end=end, now=now, feed=feed)
        market_bars_by_symbol[symbol] = eligible_market_bars(raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy)
        print(f"  [{i+1}/{len(symbols)}] {symbol}: {len(market_bars_by_symbol[symbol])} eligible bars")
    return market_bars_by_symbol


async def _run_lane(*, label: str, universe: tuple[str, ...], implementation_digest: str, created_at: datetime) -> dict:
    session_dates = trading_sessions_before(P3_REFERENCE_DAY, count=P3_SESSION_COUNT)
    print(f"[{label}] {len(session_dates)} sessions ({session_dates[0]}..{session_dates[-1]}), universe: {universe}")

    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise SystemExit("ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY")
    credentials = AlpacaCredentials(api_key_id=key, api_secret_key=secret)

    market_bars_by_symbol = await _fetch_universe_bars(
        universe=universe, session_dates=session_dates, cache_dir=DEFAULT_CACHE_DIR,
        credentials=credentials, feed=DEFAULT_FEED,
    )
    minute_bars_by_symbol = {s: [b.to_minute_bar() for b in bars] for s, bars in market_bars_by_symbol.items()}

    research_config = research_config_for_universe(load_config(), universe)

    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=session_dates, minute_bars_by_symbol=minute_bars_by_symbol,
        spy_minute_bars=minute_bars_by_symbol["SPY"], base_config=research_config,
    )
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=universe, session_dates=session_dates, inputs_table=inputs_table, base_config=research_config)

    rows = build_all_entry_and_exit_rows(
        period="P3", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars_by_symbol,
        base_config=research_config, implementation_digest=implementation_digest, created_at=created_at,
        session_bounds_provider=None, arms=(ARM_BE_AFTER_050R,),
    )

    be_rows = rows[ENTRY_WAITED_FIRST_VALID_0950][ARM_BE_AFTER_050R]
    tq = trade_quality_from_rows(be_rows)
    print(f"[{label}] WAITED_FIRST_VALID_0950/BE_AFTER_050R: closed={tq['closed_trades']} exp_r={tq['expectancy_r_per_trade']} pf_r={tq['profit_factor_r']}")

    return {"label": label, "period": "P3", "session_dates": session_dates, "universe": universe, "rows": rows}


def _checkpoint_path(label: str) -> Path:
    return CHECKPOINT_DIR / f"{label}.pkl"


def _load_checkpoint(label: str) -> dict | None:
    path = _checkpoint_path(label)
    if not path.exists():
        return None
    with path.open("rb") as fh:
        return pickle.load(fh)


def _save_checkpoint(label: str, result: dict) -> None:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    with _checkpoint_path(label).open("wb") as fh:
        pickle.dump(result, fh)


async def main() -> None:
    implementation_digest = checkpoint_implementation_digest(ROOT)
    created_at = datetime.now(timezone.utc)

    small_mid_universe, basket_meta = load_small_mid_basket()
    large_cap_universe = production_universe()

    results = {}
    for label, universe in (("SMALL_MID", small_mid_universe), ("LARGE_CAP", large_cap_universe)):
        cached = _load_checkpoint(label)
        if cached is not None:
            print(f"[{label}] using checkpoint")
            results[label] = cached
            continue
        result = await _run_lane(label=label, universe=universe, implementation_digest=implementation_digest, created_at=created_at)
        _save_checkpoint(label, result)
        results[label] = result

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / "raw_results_marker.json").write_text(json.dumps({
        "small_mid_universe": small_mid_universe, "large_cap_universe": large_cap_universe,
        "generated_at_utc": created_at.isoformat(),
    }, indent=2), encoding="utf-8")
    print("Simulation stage complete. Checkpoints saved under", CHECKPOINT_DIR)


if __name__ == "__main__":
    asyncio.run(main())
