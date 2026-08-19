"""M46 stage 3: liquidity screening, sector tagging, and deterministic
sector-capped selection of 10 MID (from IWR) + 10 SMALL (from IWM) names.

Pipeline (all steps mechanical / deterministic -- no discretionary picks;
the dedup / overlap / liquidity / selection primitives are the tested pure
functions in `src.orb_small_midcap_momentum_research`):
  1. Load stage-1 ticker resolutions; drop UNRESOLVED_* rows into the audit
     with their reason (never guessed).
  2. Deduplicate share classes of the same issuer within each fund.
  3. Exclude any ticker already in the production 15-symbol universe.
  4. Liquidity screen from stage-2 cached daily bars over the 60-session
     pre-P3 window (2026-01-08..2026-04-06): median-based, frozen floors.
  5. Cross-fund overlap resolution (assign to the larger-weight fund).
  6. Sector-tag eligible survivors only (SEC SIC major-group, 2-digit
     prefix of the registrant's official SIC code).
  7. Deterministic sector-capped selection per fund via
     `select_sector_capped_basket`. A bucket shortfall is reported, not
     silently accepted.

Every candidate's full disposition is written to a single audit CSV.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.orb_small_midcap_momentum_research import (
    DailyBar,
    HoldingCandidate,
    MAX_PER_SECTOR,
    MIN_MATERIAL_POSITION_WEIGHT,
    MIN_MEDIAN_CLOSE_PRICE,
    MIN_MEDIAN_DOLLAR_VOLUME,
    MIN_MEDIAN_SHARE_VOLUME,
    MIN_VALID_SESSION_COVERAGE_PCT,
    SelectionCandidate,
    TARGET_PER_BUCKET,
    compute_liquidity_profile,
    dedupe_share_classes,
    filter_material_positions,
    resolve_cross_fund_overlap,
    select_sector_capped_basket,
)
from src.orb_universe_expansion_research import production_universe

BASE = Path(__file__).resolve().parent.parent / "artifacts" / "research-small-midcap-momentum-lane"
TICKER_RES_DIR = BASE / "ticker-resolution"
DAILY_BARS_CACHE = BASE / "daily-bars-cache"
SECTOR_CACHE_DIR = BASE / "sector-cache"
OUT_DIR = BASE / "selection"

LIQUIDITY_WINDOW_START = date(2026, 1, 8)
LIQUIDITY_WINDOW_END = date(2026, 4, 6)  # inclusive
LIQUIDITY_WINDOW_SESSION_COUNT = 60

SEC_UA = "ORB-LIVE-Research/1.0 (research-only; contact: orb-live-research@example.com)"


def load_resolutions() -> list[dict]:
    with (TICKER_RES_DIR / "identifier_resolution.csv").open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


#: Mirrors scripts/m46_fetch_daily_bars.py's _WINDOWS_RESERVED_NAMES.
#: Ticker 'CON' (a real Russell 2000 constituent) collides with the
#: Windows CON device: opening 'CON.json' blocks indefinitely waiting for
#: console input instead of behaving as a missing/real file, which hung
#: the liquidity-screen loop entirely without any error or timeout.
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10)),
}


def _cache_filename(symbol: str) -> str:
    """Mirrors scripts/m46_fetch_daily_bars.py's cache_filename() -- a
    handful of resolved tickers contain '/' (e.g. 'BF/A'), not valid in a
    filename, and some collide with Windows reserved device names."""
    sanitized = symbol.replace("/", "_SLASH_")
    if sanitized.upper() in _WINDOWS_RESERVED_NAMES:
        sanitized = f"_TICKER_{sanitized}"
    return sanitized + ".json"


def load_daily_bars_raw(symbol: str) -> dict | None:
    path = DAILY_BARS_CACHE / _cache_filename(symbol)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def liquidity_metrics(symbol: str) -> dict:
    """Adapts cached raw Alpaca daily bars into the pure
    `compute_liquidity_profile` and returns a flat dict for the audit row."""
    cached = load_daily_bars_raw(symbol)
    if cached is None:
        return {"status": "NO_CACHED_BARS", "eligible": False, "exclusion_reasons": []}
    if cached.get("error"):
        return {"status": cached["error"].upper(), "eligible": False, "exclusion_reasons": [], "session_coverage_pct": 0.0}

    bars = [DailyBar(close=float(b["c"]), volume=int(b["v"])) for b in (cached.get("bars") or [])]
    profile = compute_liquidity_profile(symbol, bars, window_session_count=LIQUIDITY_WINDOW_SESSION_COUNT)
    return {
        "status": "OK",
        "eligible": profile.eligible,
        "exclusion_reasons": list(profile.exclusion_reasons),
        "session_coverage_pct": round(profile.session_coverage_pct, 4),
        "sessions_observed": profile.sessions_observed,
        "median_dollar_volume": round(profile.median_dollar_volume, 2),
        "median_share_volume": round(profile.median_share_volume, 2),
        "median_close_price": round(profile.median_close_price, 4),
    }


# --- SEC SIC sector tagging (only run for eligible survivors) -------------

_COMPANY_TICKERS_CACHE = SECTOR_CACHE_DIR / "sec_company_tickers.json"
_ticker_to_cik: dict[str, int] | None = None


def _load_ticker_to_cik() -> dict[str, int]:
    global _ticker_to_cik
    if _ticker_to_cik is not None:
        return _ticker_to_cik
    SECTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not _COMPANY_TICKERS_CACHE.exists():
        req = urllib.request.Request("https://www.sec.gov/files/company_tickers.json", headers={"User-Agent": SEC_UA})
        with urllib.request.urlopen(req, timeout=30) as resp:
            _COMPANY_TICKERS_CACHE.write_bytes(resp.read())
    data = json.loads(_COMPANY_TICKERS_CACHE.read_text(encoding="utf-8"))
    mapping = {}
    for v in data.values():
        mapping[v["ticker"].upper()] = v["cik_str"]
    _ticker_to_cik = mapping
    return mapping


def sector_for_ticker(symbol: str) -> dict:
    cache_path = SECTOR_CACHE_DIR / _cache_filename(symbol)
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))

    mapping = _load_ticker_to_cik()
    cik = mapping.get(symbol.upper())
    if cik is None:
        result = {"symbol": symbol, "status": "CIK_NOT_FOUND", "sic": None, "sic_description": None, "sector_major_group": None}
        cache_path.write_text(json.dumps(result), encoding="utf-8")
        return result

    url = f"https://data.sec.gov/submissions/CIK{cik:010d}.json"
    req = urllib.request.Request(url, headers={"User-Agent": SEC_UA})
    sub = None
    last_error = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                sub = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            result = {"symbol": symbol, "status": f"SEC_HTTP_{exc.code}", "sic": None, "sic_description": None, "sector_major_group": None}
            cache_path.write_text(json.dumps(result), encoding="utf-8")
            return result
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            last_error = exc
            time.sleep(2 * (attempt + 1))
    if sub is None:
        result = {"symbol": symbol, "status": f"SEC_NETWORK_ERROR:{last_error}", "sic": None, "sic_description": None, "sector_major_group": None}
        cache_path.write_text(json.dumps(result), encoding="utf-8")
        return result

    sic = sub.get("sic")
    sic_desc = sub.get("sicDescription")
    major_group = sic[:2] if sic and len(sic) >= 2 else None
    result = {"symbol": symbol, "status": "OK", "sic": sic, "sic_description": sic_desc, "sector_major_group": major_group}
    cache_path.write_text(json.dumps(result), encoding="utf-8")
    return result


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    SECTOR_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_resolutions()
    audit: list[dict] = []
    prod_universe = set(production_universe())

    resolved_rows = [r for r in rows if r["resolved_ticker"]]
    unresolved_rows = [r for r in rows if not r["resolved_ticker"]]
    for r in unresolved_rows:
        audit.append({**r, "disposition": "EXCLUDED", "disposition_reason": r["resolution_status"], "stage": "IDENTIFIER_RESOLUTION"})

    candidates = []
    row_by_candidate_id: dict[int, dict] = {}
    for r in resolved_rows:
        c = HoldingCandidate(
            source_fund=r["source_fund"], resolved_ticker=r["resolved_ticker"],
            issuer_name=r["issuer_name"], pct_net_assets=float(r["pct_net_assets"]),
            value_usd=float(r["value_usd"]),
        )
        candidates.append(c)
        row_by_candidate_id[id(c)] = r

    def _row_for(c: HoldingCandidate) -> dict:
        return row_by_candidate_id[id(c)]

    kept, dup_excluded = dedupe_share_classes(candidates)
    for c in dup_excluded:
        r = _row_for(c)
        audit.append({**r, "disposition": "EXCLUDED", "disposition_reason": "EXCLUDED_DUPLICATE_SHARE_CLASS", "stage": "DEDUPLICATION"})

    prod_kept, prod_excluded = [], []
    for c in kept:
        (prod_excluded if c.resolved_ticker in prod_universe else prod_kept).append(c)
    for c in prod_excluded:
        r = _row_for(c)
        audit.append({**r, "disposition": "EXCLUDED", "disposition_reason": "EXCLUDED_PRODUCTION_UNIVERSE_OVERLAP", "stage": "PRODUCTION_OVERLAP"})

    material_kept, negligible_excluded = filter_material_positions(prod_kept)
    for c in negligible_excluded:
        r = _row_for(c)
        audit.append({**r, "disposition": "EXCLUDED", "disposition_reason": "EXCLUDED_NEGLIGIBLE_POSITION_WEIGHT", "stage": "MATERIALITY_FILTER"})

    # Liquidity screen.
    liquidity_pass: list[dict] = []
    for c in material_kept:
        r = dict(_row_for(c))
        metrics = liquidity_metrics(c.resolved_ticker)
        r.update({k: v for k, v in metrics.items() if k not in ("eligible", "exclusion_reasons")})
        if metrics["eligible"]:
            r["disposition"] = "LIQUIDITY_ELIGIBLE"
            r["disposition_reason"] = ""
            liquidity_pass.append(r)
        else:
            r["disposition"] = "EXCLUDED"
            reason = metrics.get("status") if metrics["status"] != "OK" else "+".join(metrics.get("exclusion_reasons", []))
            r["disposition_reason"] = f"EXCLUDED_LIQUIDITY:{reason}"
            r["stage"] = "LIQUIDITY_SCREEN"
            audit.append(r)

    # Cross-fund overlap resolution (reuse the tested pure function).
    liq_candidates = []
    liq_row_by_id: dict[int, dict] = {}
    for r in liquidity_pass:
        c = HoldingCandidate(source_fund=r["source_fund"], resolved_ticker=r["resolved_ticker"],
                              issuer_name=r["issuer_name"], pct_net_assets=float(r["pct_net_assets"]))
        liq_candidates.append(c)
        liq_row_by_id[id(c)] = r
    winners, losers = resolve_cross_fund_overlap(liq_candidates)
    overlapped_tickers = {c.resolved_ticker for c in losers}

    final_candidates: list[dict] = []
    for c in winners:
        r = liq_row_by_id[id(c)]
        if c.resolved_ticker in overlapped_tickers:
            r["stage"] = "CROSS_FUND_OVERLAP"
        final_candidates.append(r)
    for c in losers:
        r = liq_row_by_id[id(c)]
        winner_fund = next(w.source_fund for w in winners if w.resolved_ticker == c.resolved_ticker)
        r["disposition"] = "EXCLUDED"
        r["disposition_reason"] = f"EXCLUDED_CROSS_FUND_OVERLAP:assigned_to_{winner_fund}"
        r["stage"] = "CROSS_FUND_OVERLAP"
        audit.append(r)

    # Sector tagging for survivors only.
    for r in final_candidates:
        sec = sector_for_ticker(r["resolved_ticker"])
        r["sic"] = sec["sic"]
        r["sic_description"] = sec["sic_description"]
        r["sector_major_group"] = sec["sector_major_group"]

    # Deterministic sector-capped selection, per fund (tested pure function).
    selection_report = {}
    final_basket = []
    for fund, bucket_label in (("IWR", "MID"), ("IWM", "SMALL")):
        pool_rows = [r for r in final_candidates if r["source_fund"] == fund]
        row_by_ticker = {r["resolved_ticker"]: r for r in pool_rows}
        pool = [
            SelectionCandidate(ticker=r["resolved_ticker"], sector=r["sector_major_group"] or "UNKNOWN",
                                median_dollar_volume=r["median_dollar_volume"])
            for r in pool_rows
        ]
        result = select_sector_capped_basket(pool, target_per_bucket=TARGET_PER_BUCKET, max_per_sector=MAX_PER_SECTOR)

        for rank, sc in enumerate(result.selected, start=1):
            r = row_by_ticker[sc.ticker]
            r["disposition"] = "SELECTED"
            r["disposition_reason"] = ""
            r["stage"] = "SELECTION"
            r["bucket"] = bucket_label
            r["rank_in_bucket"] = rank
            final_basket.append(r)
            audit.append(r)
        for sc in result.excluded_sector_cap:
            r = row_by_ticker[sc.ticker]
            r["disposition"] = "EXCLUDED"
            r["disposition_reason"] = "EXCLUDED_SECTOR_CAP"
            r["stage"] = "SELECTION"
            audit.append(r)
        for sc in result.excluded_beyond_target:
            r = row_by_ticker[sc.ticker]
            r["disposition"] = "EXCLUDED"
            r["disposition_reason"] = "EXCLUDED_RANK_BEYOND_TARGET"
            r["stage"] = "SELECTION"
            audit.append(r)

        selection_report[fund] = {"bucket": bucket_label, "eligible_pool_size": len(pool), "selected_count": len(result.selected)}
        if result.shortfall > 0:
            selection_report[fund]["SHORTFALL"] = True
            selection_report[fund]["shortfall_count"] = result.shortfall

    # --- write artifacts ---
    audit_path = OUT_DIR / "selection_audit.csv"
    audit_fields = sorted({k for r in audit for k in r.keys()})
    with audit_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=audit_fields)
        writer.writeheader()
        for r in audit:
            writer.writerow(r)

    basket_path = OUT_DIR / "final_basket.csv"
    basket_fields = ["bucket", "resolved_ticker", "source_fund", "sector_major_group", "sic_description",
                      "rank_in_bucket", "median_dollar_volume", "median_share_volume", "median_close_price",
                      "session_coverage_pct", "pct_net_assets", "issuer_name", "cusip", "isin"]
    with basket_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=basket_fields)
        writer.writeheader()
        for r in sorted(final_basket, key=lambda x: (x["bucket"], x["rank_in_bucket"])):
            writer.writerow({k: r.get(k, "") for k in basket_fields})

    config = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "liquidity_window": {"start": LIQUIDITY_WINDOW_START.isoformat(), "end": LIQUIDITY_WINDOW_END.isoformat(), "session_count": LIQUIDITY_WINDOW_SESSION_COUNT},
        "thresholds": {
            "min_median_dollar_volume": MIN_MEDIAN_DOLLAR_VOLUME,
            "min_median_share_volume": MIN_MEDIAN_SHARE_VOLUME,
            "min_median_close_price": MIN_MEDIAN_CLOSE_PRICE,
            "min_valid_session_coverage_pct": MIN_VALID_SESSION_COVERAGE_PCT,
            "min_material_position_weight": MIN_MATERIAL_POSITION_WEIGHT,
        },
        "sector_cap": MAX_PER_SECTOR,
        "target_per_bucket": TARGET_PER_BUCKET,
        "sector_source": "SEC EDGAR submissions API sicDescription, 2-digit SIC major group",
        "selection_report": selection_report,
    }
    config_path = OUT_DIR / "selection_config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

    def sha256_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    hashes = {str(p.relative_to(BASE)): sha256_file(p) for p in (audit_path, basket_path, config_path)}
    (OUT_DIR / "artifact_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    print(json.dumps(selection_report, indent=2))
    print(f"Final basket size: {len(final_basket)}")
    print(f"Audit: {audit_path}")
    print(f"Basket: {basket_path}")


if __name__ == "__main__":
    main()
