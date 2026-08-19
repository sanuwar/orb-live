"""M46 stage 2: fetch 60-session daily bars (2026-01-08..2026-04-06, strictly
before the P3 start 2026-04-07 -- prevents look-ahead) for every uniquely
resolved ticker from stage 1. Read-only Alpaca Market Data fetch through the
existing ALPACA_API_KEY_ID/ALPACA_API_SECRET_KEY environment variables
(never printed). Cached per-symbol on disk so a rerun only fetches missing
symbols. No broker calls, no strategy activation.
"""

from __future__ import annotations

import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orb_universe_expansion_research import trading_sessions_before

BASE = Path(__file__).resolve().parent.parent / "artifacts" / "research-small-midcap-momentum-lane"
TICKER_RES_DIR = BASE / "ticker-resolution"
CACHE_DIR = BASE / "daily-bars-cache"

P3_START = date(2026, 4, 7)
LIQUIDITY_WINDOW_SESSIONS = 60
ALPACA_BASE = "https://data.alpaca.markets/v2"


def resolved_tickers() -> list[str]:
    path = TICKER_RES_DIR / "identifier_resolution.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    tickers = sorted({r["resolved_ticker"] for r in rows if r["resolved_ticker"]})
    return tickers


#: Windows reserved device names -- a file literally named e.g. 'CON.json'
#: does not behave as an ordinary file: opening it can redirect to the
#: console device and block indefinitely waiting for console input rather
#: than erroring, which is exactly what happened here (ticker 'CON' is a
#: real Russell 2000 constituent and hung the whole liquidity-screen loop
#: on this exact collision). Every one of these must be escaped.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}


def cache_filename(symbol: str) -> str:
    """A small number of resolved tickers contain '/' (e.g. Bloomberg-style
    dual-class tickers like 'BF/A'), which is not a valid filename
    character. Substitute deterministically so caching never collides with
    a real path separator. Also escapes Windows reserved device names
    (e.g. ticker 'CON') that would otherwise hang file I/O."""
    sanitized = symbol.replace("/", "_SLASH_")
    if sanitized.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"_TICKER_{sanitized}"
    return sanitized + ".json"


def alpaca_symbol(symbol: str) -> str:
    """Alpaca's own dual-class-share convention uses '.' (e.g. 'BF.A'), not
    the '/' OpenFIGI returns (e.g. 'BF/A'). The '/' form remains the
    canonical `resolved_ticker` used everywhere else in the pipeline; this
    conversion is purely for the Alpaca request path."""
    return symbol.replace("/", ".")


def fetch_daily_bars(symbol: str, start: date, end_exclusive: date, key: str, secret: str) -> list[dict]:
    encoded_symbol = urllib.parse.quote(alpaca_symbol(symbol), safe="")
    # SIP (consolidated tape), not IEX: a liquidity screen against real
    # dollar/share-volume floors needs total market volume. IEX alone
    # captures only a small fraction of consolidated volume (spot-checked
    # at roughly 7% for one sample day), which would fail nearly every
    # genuinely liquid name against the milestone's floors. This is a
    # separate, research-only daily-bar fetch -- it does not touch
    # src/market_data_provider.py's deliberate IEX choice for live/shadow
    # minute bars.
    url = (
        f"{ALPACA_BASE}/stocks/{encoded_symbol}/bars"
        f"?timeframe=1Day&start={start.isoformat()}T00:00:00Z&end={end_exclusive.isoformat()}T00:00:00Z"
        f"&feed=sip&limit=10000&adjustment=split"
    )
    req = urllib.request.Request(url, headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    return payload.get("bars") or []


def main() -> None:
    key = os.environ["ALPACA_API_KEY_ID"]
    secret = os.environ["ALPACA_API_SECRET_KEY"]

    sessions = trading_sessions_before(P3_START, count=LIQUIDITY_WINDOW_SESSIONS)
    window_start = sessions[0]
    window_end_exclusive = P3_START  # exclusive end -> never includes P3 itself

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tickers = resolved_tickers()
    print(f"{len(tickers)} unique resolved tickers to fetch; window {window_start}..{sessions[-1]} ({len(sessions)} sessions)")

    fetched, cached, failed = 0, 0, []
    for i, symbol in enumerate(tickers):
        cache_path = CACHE_DIR / cache_filename(symbol)
        if cache_path.exists():
            cached += 1
            continue
        for attempt in range(4):
            try:
                bars = fetch_daily_bars(symbol, window_start, window_end_exclusive, key, secret)
                cache_path.write_text(json.dumps({"symbol": symbol, "window_start": window_start.isoformat(), "window_end_exclusive": window_end_exclusive.isoformat(), "bars": bars}), encoding="utf-8")
                fetched += 1
                break
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(3 * (attempt + 1))
                    continue
                if exc.code == 404:
                    cache_path.write_text(json.dumps({"symbol": symbol, "window_start": window_start.isoformat(), "window_end_exclusive": window_end_exclusive.isoformat(), "bars": [], "error": "404_not_found"}), encoding="utf-8")
                    failed.append((symbol, "404"))
                    break
                time.sleep(2 * (attempt + 1))
            except (urllib.error.URLError, TimeoutError):
                time.sleep(2 * (attempt + 1))
        else:
            failed.append((symbol, "exhausted_retries"))
        if (i + 1) % 100 == 0:
            print(f"progress: {i+1}/{len(tickers)} (fetched={fetched}, cached={cached}, failed={len(failed)})")

    print(f"done. fetched={fetched} cached={cached} failed={len(failed)}")
    if failed:
        print("failed symbols:", failed[:20], "..." if len(failed) > 20 else "")


if __name__ == "__main__":
    main()
