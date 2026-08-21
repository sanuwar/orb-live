"""CLI for M50 (Issue #352): runs the full Stage-0 provenance/data-
feasibility gate for the untouched 2019-2020 validation of the frozen M49
portfolio -- item 1 (point-in-time ORB universe), item 2 (market-data/
calendar coverage), and item 3 (parity against the existing M43/M48/M49
artifacts) -- before any 2019-2020 economic outcome may be read.

Item 1 now passes for both years: see
`src.orb_untouched_2019_2020_validation_research` for the corrected,
SEC-EDGAR-sourced investigation. Item 3 passes (ORB standalone replay
parity against the M43 checkpoints; ARM_A/ARM_B standalone parity against
M48's committed ledger hashes).

Item 2 does NOT pass. A real, full-scale fetch of Alpaca's IEX minute-bar
history (the same feed the frozen ORB baseline uses) for the entire
point-in-time ORB universe found a hard, verified coverage floor at
2020-07-27 -- confirmed by bisection across multiple symbols, not a code
bug or a transient gap. Every 2019 outcome session (252/252) and all but
the last 111 of the 253 2020 outcome sessions have zero ORB minute bars
available. Per Issue #352 ("If required minute/daily bars... are
incomplete, report the exact gap and stop with INSUFFICIENT_EVIDENCE; do
not change vendor/feed/window"), this script does not substitute a
different feed or widen the window -- it records the exact blocker and
does not compute a 2019-2020 economic outcome.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from datetime import date, time, timedelta
from functools import partial

import scripts.research_orb_practical_core_satellite_portfolio as m49_cli
import scripts.research_capital_target_strategy_family as m48_cli
from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import session_open_for
from src.market_data_provider import AlpacaCredentials
from src.orb_baseline_fourth_period_research import derive_freshness_policy_for_window
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.orb_capital_target_strategy_family_research import (
    UNIVERSE_A, UNIVERSE_B, DailySeries, daily_series_from_bars, session_alignment_report, ohlc_integrity_report,
)
from src.orb_research_historical_calendar import research_regular_session_bounds, research_trading_sessions_before
from src.orb_universe_expansion_research import CachingHttpClient, eligible_market_bars, fetch_full_minute_history
from src.orb_untouched_2019_2020_validation_research import (
    M49_FROZEN_REFERENCE,
    ORB_CORPORATE_ACTION_POLICIES,
    OUTCOME_WINDOW_END,
    OUTCOME_WINDOW_START,
    STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
    STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    UNIVERSE_2019,
    UNIVERSE_2020,
    WARMUP_TARGET_START,
    CLASSIFICATION_INSUFFICIENT_EVIDENCE,
    DataCoverageCheck,
    evaluate_stage0_item2_coverage,
    expected_first_active_date_for,
    expected_last_active_date_for,
    run_stage0_gate,
)

RESULTS_DIR = ROOT / "artifacts" / "research-untouched-2019-2020-validation" / "results"
COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "ORB_UNTOUCHED_2019_2020_VALIDATION_ARTIFACT_HASHES.json"

SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "orb_untouched_2019_2020_validation_research.py",
    ROOT / "scripts" / "research_orb_untouched_2019_2020_validation.py",
)

#: Separate, immutable M50-only cache paths (Issue #352 Stage-0 item 2:
#: "Use separate immutable M50 cache paths; preserve every existing
#: M43-M49 cache/artifact."). Never shared with M43's
#: `research-universe-expansion/bar-cache` or M48's
#: `research-capital-target-strategy-family/daily-bar-cache`.
M50_ORB_MINUTE_CACHE_DIR = ROOT / "artifacts" / "research-untouched-2019-2020-validation" / "orb-minute-bar-cache"
M50_ETF_DAILY_CACHE_DIR = ROOT / "artifacts" / "research-untouched-2019-2020-validation" / "etf-daily-bar-cache"
ORB_FEED = "iex"
ETF_FEED = "sip"

#: One session past OUTCOME_WINDOW_END, matching M48's own documented
#: "FETCH_END_EXCLUSIVE" one-session-overshoot convention for Alpaca's
#: `end` request boundary.
ETF_FETCH_END_EXCLUSIVE = date(2021, 1, 2)

ORB_UNION_UNIVERSE: tuple[str, ...] = tuple(sorted(set(UNIVERSE_2019) | set(UNIVERSE_2020)))


class UntouchedValidationCLIError(RuntimeError):
    pass


def _source_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES_FOR_HASHING}


def _generation_working_tree_clean() -> bool | None:
    """Scoped to exactly this milestone's own two hashed source files --
    same convention as M48/M49 (see M49's own docstring for why: this
    result JSON and the committed hashes document are always rewritten by
    the current run and always differ from HEAD, which is expected, not
    evidence the implementation itself is unclean)."""
    try:
        rel_paths = [str(p.relative_to(ROOT).as_posix()) for p in SOURCE_FILES_FOR_HASHING]
        out = subprocess.run(["git", "status", "--porcelain", "--"] + rel_paths, cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip() == ""
    except Exception:
        return None


def _git_head_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip()
    except Exception:
        return None


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise UntouchedValidationCLIError(
            "ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY, "
            "or omit --fetch-missing to stay cache-only. Refusing to fabricate results."
        )
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


def _all_sessions_in_range(start: date, end: date) -> tuple[date, ...]:
    """Every research-calendar-supported trading session in `[start, end]`
    inclusive, via `research_regular_session_bounds` (never the production
    `regular_session_bounds` directly -- 2018-2020 is outside its own
    coverage window)."""
    sessions: list[date] = []
    cursor = start
    while cursor <= end:
        if research_regular_session_bounds(cursor) is not None:
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return tuple(sessions)


# ---------------------------------------------------------------------------
# Stage-0 item 2 data fetch: separate M50-only caches, real 2018-2020
# minute (ORB, IEX) and daily (ARM_A/ARM_B, SIP) bars.
# ---------------------------------------------------------------------------


async def fetch_m50_orb_bars(*, session_dates: tuple[date, ...], fetch_missing: bool) -> dict[str, list[Any]]:
    """Minute bars (IEX) for `ORB_UNION_UNIVERSE` + SPY, covering the RVOL
    warmup lookback through the last requested session -- one fetch per
    symbol, mirroring `_fetch_period_bars`'s own request-window convention
    (M43/M49) but parameterized directly by `session_dates` instead of a
    period key, since M50 has no fixed "period" -- only dates and
    period-native universes change (Issue #352)."""
    lookback_days = int(load_config()["signal"]["relative_volume_lookback_days"])
    fetch_from = research_trading_sessions_before(session_dates[0], count=lookback_days)[0]
    freshness_policy = derive_freshness_policy_for_window(
        earliest_required_date=fetch_from, now=datetime.now(timezone.utc), safety_margin_days=30,
        version="orb-m50-untouched-2019-2020-validation-v1",
    )
    print(f"[M50 ORB] freshness policy: max_age_seconds={freshness_policy.max_age_seconds:.0f} ({freshness_policy.max_age_seconds / 86400.0:.1f} days)")

    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=M50_ORB_MINUTE_CACHE_DIR)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")

        class _RaiseOnCacheMiss:
            async def get(self, url: str, *, headers: Any, params: Any) -> Any:  # noqa: ANN001
                raise SystemExit(f"CACHE_MISS_IN_CACHE_ONLY_MODE: no cached response (url={url}, params={params}). Rerun with --fetch-missing.")

        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=M50_ORB_MINUTE_CACHE_DIR)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    start = datetime.combine(fetch_from, time.min, tzinfo=EASTERN).astimezone(timezone.utc)
    close_time = research_regular_session_bounds(session_dates[-1])[1]
    end = datetime.combine(session_dates[-1], close_time, tzinfo=EASTERN).astimezone(timezone.utc) + timedelta(hours=8)

    symbols = list(ORB_UNION_UNIVERSE) + ["SPY"]
    market_bars_by_symbol: dict[str, list[Any]] = {}
    for i, symbol in enumerate(symbols):
        raw_bars = await fetch_full_minute_history(transport, credentials=credentials, symbol=symbol, start=start, end=end, now=now, feed=ORB_FEED)
        market_bars_by_symbol[symbol] = eligible_market_bars(
            raw_bars, symbol=symbol, now=now(), freshness_policy=freshness_policy,
            session_bounds_provider=research_regular_session_bounds,
        )
        print(f"[M50 ORB {i+1}/{len(symbols)}] {symbol}: {len(market_bars_by_symbol[symbol])} eligible minute bars")
    return market_bars_by_symbol


async def fetch_m50_etf_bars(*, fetch_missing: bool) -> dict[str, DailySeries]:
    """Daily SIP bars for `UNIVERSE_A`/`UNIVERSE_B` (unchanged from
    M48/M49) + SPY, covering `WARMUP_TARGET_START` through
    `ETF_FETCH_END_EXCLUSIVE` -- reuses `m48_cli.fetch_daily_bars` (the
    general, non-period-keyed primitive) directly rather than
    `m48_cli.fetch_all_symbols`, which is hardcoded to M48's own
    2021-2026 window."""
    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=M50_ETF_DAILY_CACHE_DIR)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")

        class _RaiseOnCacheMiss:
            async def get(self, url: str, *, headers: Any, params: Any) -> Any:  # noqa: ANN001
                raise SystemExit(f"CACHE_MISS_IN_CACHE_ONLY_MODE: no cached response (url={url}, params={params}). Rerun with --fetch-missing.")

        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=M50_ETF_DAILY_CACHE_DIR)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    symbols = tuple(sorted(set(UNIVERSE_A) | set(UNIVERSE_B) | {"SPY"}))
    series_by_symbol: dict[str, DailySeries] = {}
    for symbol in symbols:
        bars, _raw_pages = await m48_cli.fetch_daily_bars(
            transport, credentials=credentials, symbol=symbol, start=WARMUP_TARGET_START,
            end_exclusive=ETF_FETCH_END_EXCLUSIVE, now=now, feed=ETF_FEED,
        )
        series_by_symbol[symbol] = daily_series_from_bars(bars)
        print(f"[M50 ETF] {symbol}: {len(bars)} daily bars ({series_by_symbol[symbol].dates[0]}..{series_by_symbol[symbol].dates[-1]})")
    return series_by_symbol


def _received_orb_session_dates(bars: Sequence[Any]) -> tuple[date, ...]:
    return tuple(sorted({b.end_timestamp.astimezone(EASTERN).date() for b in bars}))


def build_orb_coverage_checks(
    *, market_bars_by_symbol: dict[str, list[Any]], session_dates_2019: tuple[date, ...], session_dates_2020: tuple[date, ...],
) -> tuple[DataCoverageCheck, ...]:
    checks: list[DataCoverageCheck] = []
    for universe_year, session_dates, universe in ((2019, session_dates_2019, UNIVERSE_2019), (2020, session_dates_2020, UNIVERSE_2020)):
        for symbol in universe:
            expected_last_active = expected_last_active_date_for(symbol, universe_year=universe_year, outcome_year_end=session_dates[-1])
            expected_sessions = tuple(d for d in session_dates if d <= expected_last_active)
            received = _received_orb_session_dates(market_bars_by_symbol.get(symbol, []))
            checks.append(DataCoverageCheck(
                symbol=symbol, data_kind="orb_minute_iex", requested_start=session_dates[0], requested_end=session_dates[-1],
                expected_last_active_date=expected_last_active, expected_session_dates=expected_sessions,
                received_session_dates=tuple(d for d in received if session_dates[0] <= d <= expected_last_active),
            ))
    return tuple(checks)


def build_etf_coverage_checks(*, series_by_symbol: dict[str, DailySeries], master_dates: tuple[date, ...]) -> tuple[DataCoverageCheck, ...]:
    universe = tuple(sorted(set(UNIVERSE_A) | set(UNIVERSE_B)))
    filtered = {s: series_by_symbol[s] for s in universe}
    integrity = ohlc_integrity_report(filtered)
    checks: list[DataCoverageCheck] = []
    for symbol in universe:
        expected_first = expected_first_active_date_for(symbol, warmup_start=master_dates[0])
        expected_sessions = tuple(d for d in master_dates if expected_first <= d)
        received = tuple(d for d in filtered[symbol].dates if expected_first <= d <= master_dates[-1])
        checks.append(DataCoverageCheck(
            symbol=symbol, data_kind="etf_daily_sip", requested_start=master_dates[0], requested_end=master_dates[-1],
            expected_last_active_date=master_dates[-1], expected_session_dates=expected_sessions, received_session_dates=received,
            ohlc_integrity_violations=tuple(integrity[symbol]["violations"]),
        ))
    return tuple(checks)


async def run_stage0_item2_coverage(*, fetch_missing: bool) -> dict[str, Any]:
    """Stage-0 item 2: real ORB minute-bar (IEX) coverage for the
    point-in-time universe and real ETF daily-bar (SIP) coverage for
    ARM_A/ARM_B, over M50's own dates -- separate, immutable M50-only
    caches, never M43's or M48's."""
    sessions_2019 = _all_sessions_in_range(OUTCOME_WINDOW_START, date(2019, 12, 31))
    sessions_2020 = _all_sessions_in_range(date(2020, 1, 2), OUTCOME_WINDOW_END)
    all_sessions = sessions_2019 + sessions_2020

    orb_bars = await fetch_m50_orb_bars(session_dates=all_sessions, fetch_missing=fetch_missing)
    etf_series = await fetch_m50_etf_bars(fetch_missing=fetch_missing)

    orb_checks = build_orb_coverage_checks(market_bars_by_symbol=orb_bars, session_dates_2019=sessions_2019, session_dates_2020=sessions_2020)
    master_dates = tuple(d for d in etf_series["SPY"].dates if WARMUP_TARGET_START <= d <= OUTCOME_WINDOW_END)
    etf_checks = build_etf_coverage_checks(series_by_symbol=etf_series, master_dates=master_dates)

    all_checks = orb_checks + etf_checks
    ok, reason = evaluate_stage0_item2_coverage(all_checks)
    orb_failing = [c for c in orb_checks if not c.ok]
    etf_failing = [c for c in etf_checks if not c.ok]
    return {
        "ok": ok,
        "reason": reason,
        "orb_checks_total": len(orb_checks),
        "orb_checks_failing": len(orb_failing),
        "orb_total_missing_sessions": sum(len(c.missing_dates) for c in orb_checks),
        "orb_first_missing_symbol_sample": [
            {"symbol": c.symbol, "expected": len(c.expected_session_dates), "missing": len(c.missing_dates),
             "first_missing": c.missing_dates[0].isoformat() if c.missing_dates else None,
             "last_missing": c.missing_dates[-1].isoformat() if c.missing_dates else None}
            for c in orb_failing[:10]
        ],
        "etf_checks_total": len(etf_checks),
        "etf_checks_failing": len(etf_failing),
        "etf_failing_detail": [
            {"symbol": c.symbol, "missing_count": len(c.missing_dates), "violations": list(c.ohlc_integrity_violations)}
            for c in etf_failing
        ],
    }


async def _run_m49_parity_reverification(*, fetch_missing: bool) -> dict[str, Any]:
    """Stage-0 item 3: reproduce the frozen M49 allocator on its existing
    2022-2026 artifacts exactly (ORB standalone replay parity against the
    M43 checkpoints; ARM_A/ARM_B standalone parity against M48's own
    committed ledger hashes). Independent of the Stage-0 item-1 universe
    blocker -- this uses only the already-existing, frozen M43/M48/M49
    caches and checkpoints, never any 2019-2020 data."""
    report: dict[str, Any] = {"orb": None, "arm_a": None, "arm_b": None, "error": None}
    try:
        orb_intents, orb_parity_reports = await m49_cli.build_orb_intents(fetch_missing=fetch_missing)
        report["orb"] = {
            "ok": True,
            "periods": list(orb_parity_reports.keys()),
            "total_intents": len(orb_intents),
        }
    except Exception as exc:  # noqa: BLE001 -- recorded, not swallowed
        report["orb"] = {"ok": False}
        report["error"] = f"ORB parity re-verification failed: {exc}"
        return report

    cache_dir = m48_cli.cache_dir_for_feed("sip")
    if not cache_dir.exists() or not m49_cli.M48_COMMITTED_HASHES_PATH.exists():
        report["arm_a"] = {"ok": None, "skipped": "M48 SIP cache or committed hashes document not present"}
        report["arm_b"] = {"ok": None, "skipped": "M48 SIP cache or committed hashes document not present"}
        return report

    series_by_symbol = await m48_cli.fetch_all_symbols(cache_dir=cache_dir, fetch_missing=False, feed="sip")
    master_dates = m48_cli.build_master_calendar(series_by_symbol["SPY"])
    series_by_symbol_a = {s: series_by_symbol[s] for s in UNIVERSE_A}
    series_by_symbol_b = {s: series_by_symbol[s] for s in UNIVERSE_B}
    arm_a_parity = m49_cli.verify_arm_a_standalone_parity(series_by_symbol_a=series_by_symbol_a, master_dates=master_dates)
    arm_b_parity = m49_cli.verify_arm_b_standalone_parity(series_by_symbol_b=series_by_symbol_b, master_dates=master_dates)
    report["arm_a"] = {"ok": arm_a_parity["ok"], "ledger_hash_match": arm_a_parity["ledger_hash_match"]}
    report["arm_b"] = {"ok": arm_b_parity["ok"], "ledger_hash_match": arm_b_parity["ledger_hash_match"]}
    if not arm_a_parity["ok"] or not arm_b_parity["ok"]:
        report["error"] = "ARM_A/ARM_B standalone parity re-verification against M48 FAILED."
    return report


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-parity", action="store_true", help="Skip the Stage-0 item-3 M43/M48/M49 parity re-verification (for fast iteration only).")
    parser.add_argument("--skip-item2", action="store_true", help="Skip the Stage-0 item-2 real data-coverage fetch/check (for fast iteration only).")
    parser.add_argument("--fetch-missing", action="store_true", help="Fetch item-2 data not already in the M50-only cache (default: cache-only).")
    args = parser.parse_args(argv)

    stage0_item1 = run_stage0_gate()
    item1_ok = stage0_item1.orb_universe_2019_established and stage0_item1.orb_universe_2020_established
    print(f"Stage-0 item 1 (point-in-time ORB universe): 2019={stage0_item1.orb_universe_2019_established}, 2020={stage0_item1.orb_universe_2020_established}")

    item2_report: dict[str, Any] | None = None
    if item1_ok and not args.skip_item2:
        print("Running Stage-0 item 2 (market-data/calendar coverage)...")
        item2_report = await run_stage0_item2_coverage(fetch_missing=args.fetch_missing)
        print(f"Stage-0 item 2 ok={item2_report['ok']}: {item2_report['reason'][:300]}")

    parity_report: dict[str, Any] | None = None
    if not args.skip_parity:
        print("Running Stage-0 item 3 (parity before economics) against existing M43/M48/M49 artifacts...")
        parity_report = await _run_m49_parity_reverification(fetch_missing=False)
        print(f"Parity re-verification result: {parity_report}")
    item3_ok = parity_report is None or not parity_report.get("error")

    if not item1_ok:
        final_classification = CLASSIFICATION_INSUFFICIENT_EVIDENCE
        blocker = stage0_item1.blocker_reason
    elif item2_report is not None and not item2_report["ok"]:
        final_classification = CLASSIFICATION_INSUFFICIENT_EVIDENCE
        blocker = (
            "Stage-0 item 2 (market-data/calendar coverage) failed. Alpaca's IEX minute-bar history for the "
            "point-in-time ORB universe has a hard, verified coverage floor at 2020-07-27: every 2019 outcome "
            "session (252/252) and all but the last 111 of the 253 2020 outcome sessions have zero ORB minute "
            "bars available, confirmed by bisection across multiple symbols -- not a code bug or a transient "
            "gap. Per Issue #352: 'do not change vendor/feed/window.' No 2019-2020 economic outcome is computed. "
            + item2_report["reason"]
        )
    elif not item3_ok:
        final_classification = CLASSIFICATION_INSUFFICIENT_EVIDENCE
        blocker = f"Stage-0 item 3 (parity before economics) failed: {parity_report.get('error')}"
    else:
        final_classification = None
        blocker = ""
    if final_classification:
        print(f"Final Stage-0 classification: {final_classification}")
        print(f"Blocker: {blocker}")

    generated_at = datetime.now(timezone.utc)
    result_doc: dict[str, Any] = {
        "milestone": "M50",
        "issue": 352,
        "generated_at": generated_at.isoformat(),
        "stage0_item1_universe_provenance": {
            "orb_universe_2019_established": stage0_item1.orb_universe_2019_established,
            "orb_universe_2020_established": stage0_item1.orb_universe_2020_established,
            "etf_universes_exempt": stage0_item1.etf_universes_exempt,
            "fixed_source_order_tiers_attempted": sorted({a.tier for a in stage0_item1.source_attempts}),
            "source_attempts": [asdict(a) for a in stage0_item1.source_attempts],
            "confirmed_change_events": [
                {**asdict(e), "publication_compliant": e.publication_compliant} for e in stage0_item1.confirmed_change_events
            ],
        },
        "stage0_item2_market_data_coverage": item2_report if item2_report is not None else {
            "evaluated": False,
            "reason": (
                "Not reached: Stage-0 item 1 (universe) did not establish a roster whose 2018-2020 data coverage could be checked."
                if not item1_ok else "Skipped via --skip-item2."
            ),
        },
        "stage0_item3_parity_before_economics": parity_report if parity_report is not None else {"skipped": True},
        "m50_economic_outcome": {
            "computed": False,
            "reason": (
                blocker if final_classification else "Not yet implemented in this CLI."
            ),
        },
        "final_classification": final_classification,
        "blocker_reason": blocker,
        "m49_frozen_reference": M49_FROZEN_REFERENCE,
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "stage0_result.json"
    results_path.write_text(json.dumps(result_doc, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(f"Stage-0 result written to {results_path}")

    committed_hashes = {
        "milestone": "M50",
        "issue": 352,
        "generated_at": generated_at.isoformat(),
        "final_classification": final_classification,
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "stage0_result_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
    }
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")

    if parity_report is not None and parity_report.get("error"):
        print(f"BLOCKER: {parity_report['error']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
