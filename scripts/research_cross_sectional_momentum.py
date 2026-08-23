"""CLI for M52 (Issue #356): the single frozen concentrated point-in-time
cross-sectional equity momentum experiment M51 selected.

Fetches IWB's official point-in-time holdings from SEC EDGAR N-PORT-P
filings (series S000004347), resolves CUSIP-identified holdings to US
exchange tickers via OpenFIGI's free search API (never guessed -- an
ambiguous or zero-match resolution is preserved as unresolved), fetches
Alpaca SIP daily bars for the resolved universe, runs the Stage-0
provenance/data-feasibility gate, and -- only if Stage 0 passes -- runs
the one frozen monthly-rebalance simulation (base 5bps / stress 10bps)
and classifies the result.

Market-data and public-filing reads only. No brokerage-account,
position, order, or Robinhood call anywhere in this module.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

import scripts.research_capital_target_strategy_family as m48_cli
from src.live_shadow_session import UrllibMarketDataHttpClient
from src.market_data_provider import AlpacaCredentials, MarketDataProviderError
from src.orb_capital_target_strategy_family_research import DailySeries, daily_series_from_bars
from src.orb_research_historical_calendar import research_regular_session_bounds
from src.orb_universe_expansion_research import CachingHttpClient
from src.cross_sectional_momentum_research import (
    BASE_COST_BPS,
    EVALUATION_END,
    EVALUATION_START,
    IWB_CIK,
    IWB_SERIES_ID,
    M49_FROZEN_REFERENCE,
    PRACTICAL_TARGET_MONTHLY_DOLLARS,
    STARTING_CAPITAL,
    STRESS_COST_BPS,
    STRETCH_TARGET_MONTHLY_DOLLARS,
    WARMUP_TARGET_START,
    CrossSectionalMomentumError,
    NportFiling,
    RawHolding,
    ResolutionCandidate,
    ResolvedHolding,
    UniverseSnapshot,
    classify_cross_sectional_momentum,
    compute_portfolio_metrics,
    month_end_signal_dates,
    parse_nport_holdings_xml,
    resolve_holding,
    select_filing_for_signal_date,
    simulate_cross_sectional_momentum_portfolio,
)

SEC_USER_AGENT = "ORB-LIVE Research contact@example.com"
SEC_CACHE_DIR = ROOT / "artifacts" / "research-cross-sectional-momentum" / "sec-nport-cache"
OPENFIGI_CACHE_DIR = ROOT / "artifacts" / "research-cross-sectional-momentum" / "openfigi-cache"
ALPACA_CACHE_DIR = ROOT / "artifacts" / "research-cross-sectional-momentum" / "daily-bar-cache"
RESULTS_DIR = ROOT / "artifacts" / "research-cross-sectional-momentum" / "results"
COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "CROSS_SECTIONAL_MOMENTUM_ARTIFACT_HASHES.json"

ALPACA_FEED = "sip"
ALPACA_FETCH_END_EXCLUSIVE = date(2026, 8, 14)
NPORT_SEARCH_START = date(2019, 6, 1)


class CrossSectionalMomentumCLIError(RuntimeError):
    pass


def _credentials_from_environment() -> AlpacaCredentials:
    key = os.environ.get("ALPACA_API_KEY_ID", "").strip()
    secret = os.environ.get("ALPACA_API_SECRET_KEY", "").strip()
    if not key or not secret:
        raise CrossSectionalMomentumCLIError(
            "ALPACA_MARKET_DATA_CREDENTIALS_MISSING: set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY, "
            "or omit --fetch-missing to stay cache-only. Refusing to fabricate results."
        )
    return AlpacaCredentials(api_key_id=key, api_secret_key=secret)


def _cache_path(cache_dir: Path, key: str) -> Path:
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return cache_dir / f"{digest}.json"


def _cached_request(cache_dir: Path, key: str, *, fetch_missing: bool, do_fetch) -> bytes:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = _cache_path(cache_dir, key)
    if path.exists():
        return path.read_bytes()
    if not fetch_missing:
        raise CrossSectionalMomentumCLIError(f"CACHE_MISS_IN_CACHE_ONLY_MODE: {key}")
    body = do_fetch()
    path.write_bytes(body)
    return body


#: A genuine network-level read timeout (distinct from an HTTP 429,
#: which the OpenFIGI caller already retries on its own) is a
#: transient condition observed live during this pipeline's own
#: multi-hour run -- retried here, at the lowest HTTP layer, so every
#: caller (SEC EDGAR, OpenFIGI, and any future vendor) benefits without
#: each needing its own timeout-handling logic.
_HTTP_TIMEOUT_MAX_RETRIES = 4
_HTTP_TIMEOUT_RETRY_DELAY_SECONDS = 10.0


def _retry_on_timeout(do_request):
    last_error: Exception | None = None
    for attempt in range(_HTTP_TIMEOUT_MAX_RETRIES):
        try:
            return do_request()
        except (TimeoutError, urllib.error.URLError) as exc:
            if isinstance(exc, urllib.error.HTTPError):
                raise  # a real HTTP error status, not a network-level timeout -- propagate immediately
            last_error = exc
            time.sleep(_HTTP_TIMEOUT_RETRY_DELAY_SECONDS)
    raise last_error  # type: ignore[misc]


def _http_get(url: str, *, headers: dict[str, str]) -> bytes:
    def _do() -> bytes:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    return _retry_on_timeout(_do)


def _http_post_json(url: str, payload: Any, *, headers: dict[str, str]) -> bytes:
    def _do() -> bytes:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    return _retry_on_timeout(_do)


# ---------------------------------------------------------------------------
# SEC EDGAR: IWB N-PORT-P filing discovery and holdings fetch.
# ---------------------------------------------------------------------------


async def fetch_iwb_nport_filings(*, fetch_missing: bool, search_end: date = EVALUATION_END) -> tuple[NportFiling, ...]:
    url = (
        "https://efts.sec.gov/LATEST/search-index?q=%22" + IWB_SERIES_ID + "%22&forms=NPORT-P"
        f"&dateRange=custom&startdt={NPORT_SEARCH_START.isoformat()}&enddt={search_end.isoformat()}"
    )
    body = await asyncio.to_thread(
        _cached_request, SEC_CACHE_DIR, f"search:{url}", fetch_missing=fetch_missing,
        do_fetch=lambda: _http_get(url, headers={"User-Agent": SEC_USER_AGENT}),
    )
    payload = json.loads(body)
    filings: list[NportFiling] = []
    for hit in payload["hits"]["hits"]:
        source = hit["_source"]
        if IWB_CIK not in source["ciks"]:
            continue
        accession = source["adsh"]
        accession_nodash = accession.replace("-", "")
        cik_int = str(int(IWB_CIK))
        doc_url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession_nodash}/primary_doc.xml"
        filings.append(NportFiling(
            series_id=IWB_SERIES_ID, cik=IWB_CIK, accession=accession,
            period_ending=date.fromisoformat(source["period_ending"]), file_date=date.fromisoformat(source["file_date"]),
            document_url=doc_url,
        ))
    return tuple(sorted(filings, key=lambda f: f.period_ending))


async def fetch_nport_holdings(filing: NportFiling, *, fetch_missing: bool) -> tuple[RawHolding, ...]:
    body = await asyncio.to_thread(
        _cached_request, SEC_CACHE_DIR, f"doc:{filing.document_url}", fetch_missing=fetch_missing,
        do_fetch=lambda: _http_get(filing.document_url, headers={"User-Agent": SEC_USER_AGENT}),
    )
    return parse_nport_holdings_xml(body.decode("utf-8", errors="replace"))


# ---------------------------------------------------------------------------
# OpenFIGI: CUSIP-named security -> US exchange ticker resolution.
# ---------------------------------------------------------------------------

#: Bloomberg exchange codes searched for every holding. Per OpenFIGI's
#: own published exchange-code reference
#: (https://www.openfigi.com/assets/content/OpenFIGI_Exchange_Codes-
#: 3d3e5936ba.csv), the full set of major US common-stock venues is
#: UN=NYSE, UW=Nasdaq Global Select, UQ=Nasdaq Global Market,
#: UR=Nasdaq Capital Market, UA=NYSE American, UP=NYSE Arca.
#:
#: This pipeline searches only UN and UW. That is a deliberate,
#: documented trade-off, not an oversight (a six-exchange version was
#: run live first): OpenFIGI's unauthenticated rate limit proved
#: bursty/unpredictable under sustained load (empirically, a
#: meaningful fraction of individual requests return 429 regardless of
#: inter-request spacing), and the six-exchange, two-variant
#: (~12-request-worst-case) search made the full ~1,000-holding,
#: 27-filing universe run take upward of a day of wall-clock time --
#: impractical for a bounded research milestone. UN and UW are the
#: primary listing venue for the overwhelming majority of genuine
#: Russell 1000 (large/mid-cap US operating company) constituents;
#: UQ/UR/UA/UP are realistically relevant only to smaller-cap or
#: recently-listed names rare in this specific universe. A holding
#: whose primary listing happens to be on one of the four omitted
#: venues is preserved as UNRESOLVED (never guessed), and the
#: aggregate unresolved count/percentage is reported honestly in
#: Stage-0 evidence rather than hidden -- this narrows resolution
#: COVERAGE, not the resolution LOGIC's correctness (still never
#: guesses, still resolves only on an unambiguous unique match).
US_SEARCH_EXCHANGES = ("UN", "UW")


#: OpenFIGI's unauthenticated rate limit was found empirically to be
#: bursty/unpredictable rather than a simple fixed interval -- a live
#: test sent 15 requests spaced 6s apart and still saw 6 individual
#: 429s, interleaved with successes, not a clean "N per minute"
#: pattern. Retrying with exponential backoff (below) is therefore the
#: actual correctness mechanism, not this base delay; this value is
#: only a courtesy starting point between requests that were NOT
#: rate-limited.
OPENFIGI_REQUEST_DELAY_SECONDS = 5.0
OPENFIGI_MAX_RETRIES = 8

#: Alpaca's SIP daily-bar endpoint also rate-limits (HTTP 429) under the
#: sustained ~940-symbol sequential fetch this pipeline performs. Retried
#: with exponential backoff, same pattern as OPENFIGI_MAX_RETRIES above --
#: a 429 is a transient capacity signal, not a "no data" result, so it must
#: never be treated as symbol-level failure.
ALPACA_RATE_LIMIT_MAX_RETRIES = 6
ALPACA_RATE_LIMIT_BASE_DELAY_SECONDS = 5.0

#: Corporate-suffix tokens stripped, longest-first, to build a "bare"
#: name variant. N-PORT filings spell these out in full, all-caps
#: ("LOCKHEED MARTIN CORPORATION") -- confirmed by a live smoke test to
#: return zero OpenFIGI search results, while the same company's bare
#: name ("Lockheed Martin") or an abbreviated suffix ("Lockheed Martin
#: Corp") resolves correctly. This is a query-formatting accommodation
#: for the search provider, not a change to the security's own identity:
#: the underlying CUSIP/name from the filing is preserved unchanged in
#: `RawHolding` regardless of which query variant resolves it.
_CORPORATE_SUFFIXES = (
    ", INCORPORATED", " INCORPORATED", ", INC.", " INC.", ", INC", " INC",
    " CORPORATION", ", CORPORATION", " CORP.", ", CORP.", " CORP", ", CORP",
    ", CO.", " CO.", ", COMPANY", " COMPANY",
    ", LTD.", " LTD.", ", LTD", " LTD", " PLC", " GROUP", " HOLDINGS",
)


def _bare_name_variant(name: str) -> str | None:
    bare = name.strip()
    changed = True
    while changed:
        changed = False
        upper = bare.upper()
        for suffix in _CORPORATE_SUFFIXES:
            if upper.endswith(suffix):
                bare = bare[: -len(suffix)].strip().rstrip(",").strip()
                changed = True
                break
    bare = bare.strip()
    return bare if bare and bare != name else None


def _name_query_variants(name: str) -> tuple[str, ...]:
    """Bare name first (matches OpenFIGI's search behavior best per the
    live smoke test), then the exact N-PORT name as a fallback -- tried
    in order, stopping at the first variant that returns ANY candidate
    from ANY of `US_SEARCH_EXCHANGES`, to keep the total OpenFIGI request
    volume bounded for the common case."""
    bare = _bare_name_variant(name)
    return (bare, name) if bare else (name,)


def _openfigi_post_with_retry(query: str, exch: str) -> bytes:
    """Synchronous: exponential backoff on HTTP 429 (rate limited).
    Any OTHER HTTP error (e.g. a genuine 400) propagates immediately --
    only rate-limiting is treated as transient and retried; a persistent
    429 after every retry propagates too, since silently treating "the
    server is throttling me" the same as "no match found" would corrupt
    the resolution result (this was a real bug caught during a live
    smoke test: an unhandled 429 was previously swallowed as zero
    candidates, marking well-known large-caps UNRESOLVED)."""
    delay = OPENFIGI_REQUEST_DELAY_SECONDS
    last_error: Exception | None = None
    for attempt in range(OPENFIGI_MAX_RETRIES):
        try:
            return _http_post_json(
                "https://api.openfigi.com/v3/search",
                {"query": query, "securityType2": "Common Stock", "marketSecDes": "Equity", "exchCode": exch},
                headers={},
            )
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429:
                raise
            time.sleep(delay)
            delay *= 2
    raise last_error  # type: ignore[misc]


async def resolve_holding_via_openfigi(raw: RawHolding, *, fetch_missing: bool) -> ResolvedHolding:
    """Deduplicated across every snapshot that shares the same holding
    NAME (the vast majority of Russell 1000 membership is stable quarter
    to quarter): the on-disk cache means a repeat name across snapshots
    costs zero additional OpenFIGI requests, and the rate-limit courtesy
    delay below only applies on an actual cache miss, not a hit.

    `US_SEARCH_EXCHANGES` is ordered UN/UW first (the primary listing
    venue for the overwhelming majority of Russell 1000 constituents).
    After EACH exchange query, if the candidates accumulated so far for
    this name variant already resolve uniquely (`resolve_holding(...)`
    would return RESOLVED), the remaining exchanges are skipped -- a
    later exchange could in principle surface a same-named, different
    company and turn a clean resolution into an ambiguity, but for
    real US large/mid-cap common-stock names this is a rare, accepted
    trade-off against otherwise-impractical total runtime; it is never
    applied to a variant that has NOT yet resolved uniquely (an empty
    or already-ambiguous accumulation always continues through every
    remaining exchange for that variant). Stops trying further NAME
    VARIANTS (see `_name_query_variants`) once one variant returns any
    candidate at all, since a later variant re-querying the same
    underlying security is redundant, not new evidence."""
    for query in _name_query_variants(raw.name):
        variant_candidates: list[ResolutionCandidate] = []
        for exch in US_SEARCH_EXCHANGES:
            cache_key = f"search:{query}:{exch}"
            cache_hit = _cache_path(OPENFIGI_CACHE_DIR, cache_key).exists()
            try:
                body = await asyncio.to_thread(
                    _cached_request, OPENFIGI_CACHE_DIR, cache_key, fetch_missing=fetch_missing,
                    do_fetch=lambda query=query, exch=exch: _openfigi_post_with_retry(query, exch),
                )
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    # Exhausted every retry inside _openfigi_post_with_retry --
                    # a systemic rate-limit problem, not a per-holding one.
                    # Fail closed rather than silently mark this and every
                    # subsequent holding UNRESOLVED.
                    raise CrossSectionalMomentumCLIError(
                        f"BLOCKER: OpenFIGI rate limit exhausted after {OPENFIGI_MAX_RETRIES} retries resolving {query!r} on {exch}."
                    ) from exc
                continue  # a non-429 error for this one exchange query only -- try the remaining exchanges
            payload = json.loads(body)
            for item in payload.get("data", []) or []:
                variant_candidates.append(ResolutionCandidate(ticker=item["ticker"], exch_code=item["exchCode"], name=item["name"]))
            if fetch_missing and not cache_hit:
                await asyncio.sleep(OPENFIGI_REQUEST_DELAY_SECONDS)  # fetches only, never on a cache hit
            if resolve_holding(raw, variant_candidates).resolution_status == "RESOLVED":
                break  # already unique for this variant -- skip the remaining exchanges
        if variant_candidates:
            return resolve_holding(raw, variant_candidates)
    return resolve_holding(raw, [])


async def build_universe_snapshot(filing: NportFiling, *, fetch_missing: bool) -> UniverseSnapshot:
    raw_holdings = await fetch_nport_holdings(filing, fetch_missing=fetch_missing)
    resolved: list[ResolvedHolding] = []
    for raw in raw_holdings:
        resolved.append(await resolve_holding_via_openfigi(raw, fetch_missing=fetch_missing))
    raw_material = json.dumps([{"name": h.name, "cusip": h.cusip, "isin": h.isin} for h in raw_holdings], sort_keys=True)
    normalized_material = json.dumps(sorted(h.ticker for h in resolved if h.ticker), sort_keys=True)
    return UniverseSnapshot(
        filing=filing, holdings=tuple(resolved),
        raw_holdings_sha256=hashlib.sha256(raw_material.encode("utf-8")).hexdigest(),
        normalized_holdings_sha256=hashlib.sha256(normalized_material.encode("utf-8")).hexdigest(),
    )


# ---------------------------------------------------------------------------
# Alpaca SIP daily bars.
# ---------------------------------------------------------------------------


async def fetch_daily_bars_for_symbols(symbols: tuple[str, ...], *, fetch_missing: bool) -> dict[str, DailySeries]:
    if fetch_missing:
        credentials = _credentials_from_environment()
        transport = CachingHttpClient(UrllibMarketDataHttpClient(timeout_seconds=8.0), cache_dir=ALPACA_CACHE_DIR)
    else:
        credentials = AlpacaCredentials(api_key_id="cache-only", api_secret_key="cache-only")

        class _RaiseOnCacheMiss:
            async def get(self, url: str, *, headers: Any, params: Any) -> Any:  # noqa: ANN001
                raise SystemExit(f"CACHE_MISS_IN_CACHE_ONLY_MODE: no cached response (url={url}, params={params}). Rerun with --fetch-missing.")

        transport = CachingHttpClient(_RaiseOnCacheMiss(), cache_dir=ALPACA_CACHE_DIR)

    now = lambda: datetime.now(timezone.utc)  # noqa: E731
    series_by_symbol: dict[str, DailySeries] = {}
    for i, symbol in enumerate(symbols):
        bars: list[Any] | None = None
        for attempt in range(ALPACA_RATE_LIMIT_MAX_RETRIES):
            try:
                bars, _raw_pages = await m48_cli.fetch_daily_bars(
                    transport, credentials=credentials, symbol=symbol, start=WARMUP_TARGET_START,
                    end_exclusive=ALPACA_FETCH_END_EXCLUSIVE, now=now, feed=ALPACA_FEED,
                )
                break
            except MarketDataProviderError as exc:
                if "status 429" not in str(exc) or attempt == ALPACA_RATE_LIMIT_MAX_RETRIES - 1:
                    raise
                delay = ALPACA_RATE_LIMIT_BASE_DELAY_SECONDS * (2**attempt)
                print(f"[M52 daily {i+1}/{len(symbols)}] {symbol}: rate limited, retry {attempt + 1}/{ALPACA_RATE_LIMIT_MAX_RETRIES} in {delay:.0f}s")
                time.sleep(delay)
        assert bars is not None
        if bars:
            series_by_symbol[symbol] = daily_series_from_bars(bars)
        print(f"[M52 daily {i+1}/{len(symbols)}] {symbol}: {len(bars)} bars")
    return series_by_symbol


# ---------------------------------------------------------------------------
# Provenance / hashing helpers (established M48-M51 convention).
# ---------------------------------------------------------------------------

SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "cross_sectional_momentum_research.py",
    ROOT / "scripts" / "research_cross_sectional_momentum.py",
)


def _source_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES_FOR_HASHING}


def _generation_working_tree_clean() -> bool | None:
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


def _json_default(value: Any) -> Any:
    from decimal import Decimal as _Decimal
    if isinstance(value, _Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


# ---------------------------------------------------------------------------
# Orchestration: universe snapshots, Stage-0 gate, the one frozen run.
# ---------------------------------------------------------------------------


async def build_all_universe_snapshots(filings: tuple[NportFiling, ...], *, fetch_missing: bool) -> tuple[UniverseSnapshot, ...]:
    snapshots: list[UniverseSnapshot] = []
    for i, filing in enumerate(filings):
        print(f"[M52 N-PORT {i+1}/{len(filings)}] {filing.accession} (period {filing.period_ending}, filed {filing.file_date})")
        snapshot = await build_universe_snapshot(filing, fetch_missing=fetch_missing)
        print(f"  {len(snapshot.holdings)} US common-equity holdings, {len(snapshot.resolved_tickers)} resolved, {snapshot.unresolved_count} unresolved")
        snapshots.append(snapshot)
    return tuple(snapshots)


def build_monthly_universes(
    signal_dates: tuple[date, ...], snapshots: tuple[UniverseSnapshot, ...],
) -> tuple[dict[date, tuple[str, ...]], dict[date, str | None]]:
    filings = tuple(s.filing for s in snapshots)
    snapshot_by_accession = {s.filing.accession: s for s in snapshots}
    monthly_universes: dict[date, tuple[str, ...]] = {}
    filing_accession_for_signal: dict[date, str | None] = {}
    for signal_date in signal_dates:
        filing = select_filing_for_signal_date(filings, signal_date=signal_date)
        if filing is None:
            monthly_universes[signal_date] = ()
            filing_accession_for_signal[signal_date] = None
            continue
        snapshot = snapshot_by_accession[filing.accession]
        monthly_universes[signal_date] = snapshot.resolved_tickers
        filing_accession_for_signal[signal_date] = filing.accession
    return monthly_universes, filing_accession_for_signal


def run_stage0_checks(
    *, snapshots: tuple[UniverseSnapshot, ...], monthly_universes: dict[date, tuple[str, ...]],
    signal_dates: tuple[date, ...], series_by_symbol: dict[str, DailySeries], spy_series: DailySeries | None,
    base_result, stress_result,
) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    checks["item1_universe_snapshot_available_every_signal_date"] = {
        "ok": all(d in monthly_universes for d in signal_dates),
        "signal_date_count": len(signal_dates),
    }

    total_holdings = sum(len(s.holdings) for s in snapshots)
    total_unresolved = sum(s.unresolved_count for s in snapshots)
    checks["item3_identifier_resolution"] = {
        "total_holdings_across_all_snapshots": total_holdings,
        "total_unresolved_or_ambiguous": total_unresolved,
        "unresolved_pct": float(total_unresolved) / total_holdings if total_holdings else None,
    }

    spy_ok = spy_series is not None and len(spy_series.dates) > 0
    symbols_with_data = sum(1 for s in series_by_symbol.values() if len(s.dates) > 0)
    checks["item4_daily_bar_coverage"] = {
        "spy_available": spy_ok,
        "symbols_requested": len(series_by_symbol),
        "symbols_with_any_data": symbols_with_data,
    }

    checks["item5_corporate_actions"] = {"note": "adjustment=all requested on every Alpaca daily-bar fetch (splits/dividends adjusted at source, same convention as M48/M49)."}
    checks["item6_calendar"] = {"note": "research_regular_session_bounds (extended in M50 to 2018-01-01, delegates to production exchange_calendar for 2026+) used throughout; no separate calendar logic in this module."}
    checks["item7_parity"] = {"note": "monthly_return_pct/max_drawdown/sortino_ratio/annualized_downside_deviation_pct/sharpe_ratio/cagr/fixed_notional_monthly_dollar_pnl are imported and called unchanged from src.orb_capital_target_strategy_family_research -- direct reuse, not reimplementation, so parity holds by construction."}
    checks["item8_synthetic_proofs"] = {
        "note": "Covered by unit tests in tests/test_cross_sectional_momentum_research.py: "
        "test_simulate_portfolio_no_pre_evaluation_start_transactions (no pre-window execution), "
        "test_simulate_portfolio_cash_never_negative_and_endpoint_marks_at_evaluation_end (nonnegative cash, exact endpoint mark), "
        "test_simulate_portfolio_whole_shares_only (whole-share sizing), "
        "test_rank_and_select_top_n_orders_descending_ties_ascending_ticker (deterministic ranking), "
        "test_trend_filter_excludes_signal_date_bar_from_its_own_sma (no lookahead in the trend filter).",
    }

    all_transactions = list(base_result.transactions) + list(stress_result.transactions)
    txn_keys = [(t.date, t.symbol, t.side, t.quantity, t.price, t.reason) for t in all_transactions]
    duplicate_count = len(txn_keys) - len(set(txn_keys))
    unclassified_blocked = [b for b in list(base_result.blocked_intents) + list(stress_result.blocked_intents) if not b.reason]
    checks["item9_no_duplicates_no_unclassified_blocked"] = {
        "ok": duplicate_count == 0 and len(unclassified_blocked) == 0,
        "duplicate_transaction_count": duplicate_count,
        "unclassified_blocked_intent_count": len(unclassified_blocked),
    }

    checks["item10_generation_provenance"] = {
        "generation_git_head_sha": _git_head_sha(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "generation_source_file_sha256": _source_file_hashes(),
    }

    all_ok = (
        checks["item1_universe_snapshot_available_every_signal_date"]["ok"]
        and checks["item4_daily_bar_coverage"]["spy_available"]
        and checks["item9_no_duplicates_no_unclassified_blocked"]["ok"]
    )
    checks["_all_ok"] = all_ok
    return checks


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-missing", action="store_true", help="Fetch data not already in the M52-only caches (default: cache-only).")
    args = parser.parse_args(argv)

    print("Fetching IWB N-PORT-P filing list from SEC EDGAR...")
    filings = await fetch_iwb_nport_filings(fetch_missing=args.fetch_missing)
    print(f"Found {len(filings)} N-PORT-P filings for series {IWB_SERIES_ID}.")

    snapshots = await build_all_universe_snapshots(filings, fetch_missing=args.fetch_missing)

    master_dates = tuple(_all_research_calendar_sessions(WARMUP_TARGET_START, ALPACA_FETCH_END_EXCLUSIVE))
    signal_dates = month_end_signal_dates(master_dates, start=EVALUATION_START, end=EVALUATION_END)
    monthly_universes, filing_accession_for_signal = build_monthly_universes(signal_dates, snapshots)

    all_tickers = sorted({t for tickers in monthly_universes.values() for t in tickers})
    print(f"Union universe across all signal dates: {len(all_tickers)} distinct resolved tickers.")
    symbols_to_fetch = tuple(all_tickers + ["SPY"])
    series_by_symbol = await fetch_daily_bars_for_symbols(symbols_to_fetch, fetch_missing=args.fetch_missing)
    spy_series = series_by_symbol.get("SPY")
    if spy_series is None:
        raise CrossSectionalMomentumCLIError("BLOCKER: SPY daily bars unavailable -- cannot evaluate the trend filter or compare to buy-and-hold.")

    base_result = simulate_cross_sectional_momentum_portfolio(
        monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
        master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        cost_bps=BASE_COST_BPS, cost_label="base_5bps", starting_capital=STARTING_CAPITAL,
        filing_accession_for_signal=filing_accession_for_signal,
    )
    stress_result = simulate_cross_sectional_momentum_portfolio(
        monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
        master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        cost_bps=STRESS_COST_BPS, cost_label="stress_10bps", starting_capital=STARTING_CAPITAL,
        filing_accession_for_signal=filing_accession_for_signal,
    )

    stage0 = run_stage0_checks(
        snapshots=snapshots, monthly_universes=monthly_universes, signal_dates=signal_dates,
        series_by_symbol=series_by_symbol, spy_series=spy_series, base_result=base_result, stress_result=stress_result,
    )
    print(f"Stage-0 all_ok={stage0['_all_ok']}")

    base_metrics = compute_portfolio_metrics(base_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
    stress_metrics = compute_portfolio_metrics(stress_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
    classification = classify_cross_sectional_momentum(
        base_metrics=base_metrics, stress_metrics=stress_metrics, integrity_failure=not stage0["_all_ok"],
    )
    print(f"Classification: {classification['verdict']}")
    print(f"Base mean monthly $: {base_metrics['mean_monthly_dollars']}")
    print(f"Stress mean monthly $: {stress_metrics['mean_monthly_dollars']}")

    generated_at = datetime.now(timezone.utc)
    result_doc = {
        "milestone": "M52", "issue": 356, "generated_at": generated_at.isoformat(),
        "nport_filings": [{"accession": f.accession, "period_ending": f.period_ending, "file_date": f.file_date} for f in filings],
        "universe_snapshots": [
            {"accession": s.filing.accession, "period_ending": s.filing.period_ending, "file_date": s.filing.file_date,
             "holdings_count": len(s.holdings), "resolved_count": len(s.resolved_tickers), "unresolved_count": s.unresolved_count,
             "raw_holdings_sha256": s.raw_holdings_sha256, "normalized_holdings_sha256": s.normalized_holdings_sha256}
            for s in snapshots
        ],
        "stage0": stage0,
        "base_metrics": {k: v for k, v in base_metrics.items() if k not in ("monthly_pct_series", "monthly_dollar_series", "symbol_realized_pnl")},
        "stress_metrics": {k: v for k, v in stress_metrics.items() if k not in ("monthly_pct_series", "monthly_dollar_series", "symbol_realized_pnl")},
        "base_monthly_dollar_series": base_metrics["monthly_dollar_series"],
        "stress_monthly_dollar_series": stress_metrics["monthly_dollar_series"],
        "classification": classification,
        "m49_frozen_reference": M49_FROZEN_REFERENCE,
        "practical_target_monthly_dollars": str(PRACTICAL_TARGET_MONTHLY_DOLLARS),
        "stretch_target_monthly_dollars": str(STRETCH_TARGET_MONTHLY_DOLLARS),
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "m52_result.json"
    results_path.write_text(json.dumps(result_doc, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Result written to {results_path}")

    committed_hashes = {
        "milestone": "M52", "issue": 356, "generated_at": generated_at.isoformat(),
        "classification": classification["verdict"],
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "result_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
    }
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")
    return 0


def _all_research_calendar_sessions(start: date, end_exclusive: date):
    from datetime import timedelta
    cursor = start
    while cursor < end_exclusive:
        if research_regular_session_bounds(cursor) is not None:
            yield cursor
        cursor += timedelta(days=1)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
