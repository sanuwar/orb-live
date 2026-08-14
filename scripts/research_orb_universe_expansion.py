"""CLI entry point for the ORB universe-expansion research study (Issue #298).

Research-only. Reuses the canonical `orb_long_v0` evaluator and the existing
Alpaca market-data boundary unchanged -- see
`src/orb_universe_expansion_research.py` for the full explanation. Never
writes `config/strategy_v0.yaml`, never sets `strategy.enabled` in tracked
config, never calls a broker.

Real historical retrieval requires Alpaca market-data-only API credentials
(`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` in the environment -- never
`config/local.yaml`, which this script does not read). Without them this
script refuses to run rather than fabricate a result; see
`docs/research/ORB_UNIVERSE_EXPANSION_STUDY.md` for why no live run has been
completed yet in this environment.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials, AlpacaMarketDataProvider
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_OUTPUT_DIR,
    all_pool_symbols_with_production,
    build_study_report,
    csv_summary_rows,
    evaluate_symbol_session,
    trading_sessions_before,
)


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        print(
            "ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID and "
            "ALPACA_API_SECRET_KEY (market-data-only Alpaca keys) to run a real "
            "study. Refusing to fabricate results.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


async def _run(*, session_count: int, output_dir: Path, cache_dir: Path) -> dict:
    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    today = datetime.now(EASTERN).date()
    session_dates = trading_sessions_before(today, count=session_count)
    fetch_from = trading_sessions_before(session_dates[0], count=lookback_days)[0]

    credentials = _credentials_from_environment()
    transport = CachingHttpClient(
        UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir,
    )
    provider = AlpacaMarketDataProvider(
        credentials=credentials, http_client=transport, now=lambda: datetime.now(timezone.utc),
    )

    symbols = list(all_pool_symbols_with_production()) + ["SPY"]
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(session_dates[-1], session_open_for(session_dates[-1]), tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

    bars_by_symbol = {}
    for symbol in symbols:
        market_bars = await provider.fetch_minute_bars(symbol=symbol, start=start, end=end)
        bars_by_symbol[symbol] = [bar.to_minute_bar() for bar in market_bars]

    spy_bars = bars_by_symbol["SPY"]
    evaluations = {}
    for symbol in all_pool_symbols_with_production():
        candidate_bars = bars_by_symbol[symbol]
        for session_date in session_dates:
            evaluations[(symbol, session_date)] = evaluate_symbol_session(
                symbol=symbol, session_date=session_date, candidate_bars=candidate_bars,
                spy_bars=spy_bars, config=config,
            )

    report = build_study_report(session_dates=session_dates, evaluations=evaluations)

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "orb_universe_expansion_study.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    csv_path = output_dir / "orb_universe_expansion_summary.csv"
    rows = csv_summary_rows(report)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"JSON report: {json_path}")
    print(f"CSV summary: {csv_path}")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions", type=int, default=90,
        help="number of trailing trading sessions to study (default 90; minimum 60)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)

    if args.sessions < 60:
        print("refusing to run below the 60-session minimum sample size", file=sys.stderr)
        return 2

    asyncio.run(_run(session_count=args.sessions, output_dir=args.output_dir, cache_dir=args.cache_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
