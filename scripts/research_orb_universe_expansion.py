"""CLI entry point for the ORB universe-expansion research study (Issue #298).

Research-only. Reuses the canonical `orb_long_v0` evaluator and the existing
Alpaca market-data wire format unchanged -- see
`src/orb_universe_expansion_research.py` for the full explanation. Never
writes `config/strategy_v0.yaml`, never sets `strategy.enabled` in tracked
config, never calls a broker.

Historical retrieval uses `fetch_full_minute_history`, which follows
Alpaca's `next_page_token` pagination cursor to completion (see that
function's docstring for why: the production `AlpacaMarketDataProvider.
fetch_minute_bars` intentionally returns only one 10,000-bar page, which
silently truncated the first real run of this study to its oldest ~26
trading days and caused DATA_INCOMPLETE to dominate).

Real historical retrieval requires Alpaca market-data-only API credentials
(`ALPACA_API_KEY_ID` / `ALPACA_API_SECRET_KEY` in the environment -- never
`config/local.yaml`, which this script does not read). Without them this
script refuses to run rather than fabricate a result.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_universe_expansion_research import (
    CachingHttpClient,
    DEFAULT_CACHE_DIR,
    DEFAULT_FEED,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_RVOL_WARMUP_MULTIPLIER,
    INVALID_INSUFFICIENT_COVERAGE,
    SUPPORTED_FEEDS,
    all_pool_symbols_with_production,
    build_study_report,
    csv_summary_rows,
    eligible_minute_bars,
    evaluate_symbol_session,
    fetch_full_minute_history,
    historical_fetch_start,
    trading_sessions_before,
    validate_cache_integrity,
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


async def _run(
    *, session_count: int, output_dir: Path, cache_dir: Path,
    feed: str = DEFAULT_FEED, warmup_multiplier: int = DEFAULT_RVOL_WARMUP_MULTIPLIER,
    symbols_override: list[str] | None = None,
) -> dict:
    config = load_config()
    lookback_days = int(config["signal"]["relative_volume_lookback_days"])
    today = datetime.now(EASTERN).date()
    session_dates = trading_sessions_before(today, count=session_count)
    # See `historical_fetch_start`'s docstring: a plain `lookback_days`
    # calendar-session buffer assumes every one of those sessions has a
    # complete opening range, which real (sparse) IEX coverage does not
    # guarantee -- so this fetches a larger, still-bounded buffer instead.
    fetch_from = historical_fetch_start(
        session_dates[0], lookback_days=lookback_days, warmup_multiplier=warmup_multiplier,
    )

    integrity = validate_cache_integrity(cache_dir)
    if integrity["removed"]:
        print(f"cache integrity: removed {len(integrity['removed'])} corrupt entr(y/ies)")
    print(f"cache integrity: {len(integrity['kept'])} usable cached response(s) reused")

    credentials = _credentials_from_environment()
    transport = CachingHttpClient(
        UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir,
    )
    now = lambda: datetime.now(timezone.utc)  # noqa: E731

    study_symbols = list(symbols_override) if symbols_override is not None else list(all_pool_symbols_with_production())
    symbols = study_symbols + ["SPY"]
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    end = datetime.combine(
        session_dates[-1], session_open_for(session_dates[-1]), tzinfo=EASTERN,
    ).astimezone(timezone.utc) + timedelta(hours=8)

    print(
        f"studying {len(session_dates)} sessions "
        f"({session_dates[0].isoformat()} to {session_dates[-1].isoformat()}), "
        f"{len(symbols)} symbols (including SPY market context), feed={feed}, "
        f"RVOL warmup buffer back to {fetch_from.isoformat()} "
        f"({lookback_days}x{warmup_multiplier}={lookback_days * warmup_multiplier} sessions)"
    )

    bars_by_symbol: dict[str, list] = {}
    for index, symbol in enumerate(symbols, start=1):

        def _progress(page_number: int, bars_so_far: int, *, _symbol: str = symbol) -> None:
            print(f"  fetching {_symbol} {index}/{len(symbols)}: page {page_number}, {bars_so_far} bars so far")

        raw_bars = await fetch_full_minute_history(
            transport, credentials=credentials, symbol=symbol, start=start, end=end,
            now=now, feed=feed, on_page=_progress,
        )
        bars_by_symbol[symbol] = eligible_minute_bars(raw_bars, symbol=symbol, now=now())
        print(f"fetched {symbol} {index}/{len(symbols)}: {len(bars_by_symbol[symbol])} valid minute bars")

    spy_bars = bars_by_symbol["SPY"]
    evaluations = {}
    for symbol_index, symbol in enumerate(study_symbols, start=1):
        candidate_bars = bars_by_symbol[symbol]
        for session_date in session_dates:
            evaluations[(symbol, session_date)] = evaluate_symbol_session(
                symbol=symbol, session_date=session_date, candidate_bars=candidate_bars,
                spy_bars=spy_bars, config=config,
            )
        print(f"evaluating {symbol} {symbol_index}/{len(study_symbols)}: {len(session_dates)} sessions scored")

    report = build_study_report(session_dates=session_dates, evaluations=evaluations)

    print("completeness by universe size:")
    for universe_report in report["universe_reports"]:
        coverage = universe_report["coverage"]
        print(
            f"  {universe_report['label']}: {coverage['completeness']:.1%} complete "
            f"({coverage['complete_symbol_days']}/{coverage['possible_symbol_days']} symbol-days) "
            f"-> {coverage['status']}"
        )
        if coverage["status"] == INVALID_INSUFFICIENT_COVERAGE:
            print(f"    top incomplete reasons: {coverage['data_incomplete_by_reason']}")

    report["feed"] = feed
    suffix = "" if feed == DEFAULT_FEED else f"_{feed}"
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"orb_universe_expansion_study{suffix}.json"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    csv_path = output_dir / f"orb_universe_expansion_summary{suffix}.csv"
    rows = csv_summary_rows(report)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)

    print(f"JSON report: {json_path}")
    print(f"CSV summary: {csv_path}")
    return report


async def _run_feed_comparison(*, symbols: list[str], session_dates: list, cache_dir: Path) -> None:
    """A small, bounded IEX-vs-SIP raw-response comparison for the
    09:30-09:35 ET opening range only -- NOT the full study. Prints, per
    symbol/session, exactly which opening-range minutes each feed's raw
    response contains, so an operator can see directly whether SIP recovers
    the minutes IEX is missing. Requires a real Alpaca subscription with SIP
    entitlement; a 403/subscription error from Alpaca for `feed=sip` means
    the account does not have that entitlement."""
    credentials = _credentials_from_environment()
    transport = CachingHttpClient(
        UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=cache_dir,
    )
    now = lambda: datetime.now(timezone.utc)  # noqa: E731

    print(f"{'symbol':<8}{'date':<12}{'feed':<6}{'09:30-35 bars':<16}minutes present")
    for symbol in symbols:
        for session_date in session_dates:
            window_start = datetime.combine(session_date, time(9, 25), tzinfo=EASTERN).astimezone(timezone.utc)
            window_end = datetime.combine(session_date, time(9, 40), tzinfo=EASTERN).astimezone(timezone.utc)
            for feed in ("iex", "sip"):
                try:
                    bars = await fetch_full_minute_history(
                        transport, credentials=credentials, symbol=symbol,
                        start=window_start, end=window_end, now=now, feed=feed,
                    )
                except Exception as error:  # noqa: BLE001 -- surfaced directly to the operator, not swallowed
                    print(f"{symbol:<8}{session_date.isoformat():<12}{feed:<6}ERROR: {error}")
                    continue
                minutes = sorted(
                    bar.event_timestamp.astimezone(EASTERN).strftime("%H:%M") for bar in bars
                )
                opening_minutes = [m for m in minutes if "09:30" <= m < "09:35"]
                print(
                    f"{symbol:<8}{session_date.isoformat():<12}{feed:<6}"
                    f"{len(opening_minutes)}/5{'':<12}{opening_minutes}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--sessions", type=int, default=90,
        help="number of trailing trading sessions to study (default 90; minimum 60)",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument(
        "--feed", choices=sorted(SUPPORTED_FEEDS), default=DEFAULT_FEED,
        help="RESEARCH-ONLY Alpaca feed for this script's own requests. Never "
             "changes the production feed (src/market_data_provider.py stays iex). "
             "'sip' requires a paid Alpaca market-data subscription entitlement.",
    )
    parser.add_argument(
        "--warmup-multiplier", type=int, default=DEFAULT_RVOL_WARMUP_MULTIPLIER,
        help="fetch this many times the RVOL lookback window as historical buffer, "
             "to absorb sparse opening-range coverage in the lookback search (default 3)",
    )
    parser.add_argument(
        "--compare-feeds", action="store_true",
        help="run a small IEX-vs-SIP raw-response comparison for --compare-symbols "
             "on --compare-dates (09:30-09:35 ET only) instead of the full study",
    )
    parser.add_argument("--compare-symbols", help="comma-separated symbols for --compare-feeds")
    parser.add_argument("--compare-dates", help="comma-separated ISO dates (YYYY-MM-DD) for --compare-feeds")
    args = parser.parse_args(argv)

    if args.compare_feeds:
        if not args.compare_symbols or not args.compare_dates:
            print("--compare-feeds requires --compare-symbols and --compare-dates", file=sys.stderr)
            return 2
        from datetime import date as _date
        symbols = [s.strip().upper() for s in args.compare_symbols.split(",") if s.strip()]
        dates = [_date.fromisoformat(d.strip()) for d in args.compare_dates.split(",") if d.strip()]
        asyncio.run(_run_feed_comparison(symbols=symbols, session_dates=dates, cache_dir=args.cache_dir))
        return 0

    if args.sessions < 60:
        print("refusing to run below the 60-session minimum sample size", file=sys.stderr)
        return 2

    asyncio.run(_run(
        session_count=args.sessions, output_dir=args.output_dir, cache_dir=args.cache_dir,
        feed=args.feed, warmup_multiplier=args.warmup_multiplier,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
