"""M46 stage 1: deterministic CUSIP-first / ISIN-fallback ticker resolution
for the IWR/IWM common-stock holdings extracted from the frozen 2025-12-31
NPORT-P snapshot. Research-only, no broker calls. Uses OpenFIGI's public
mapping API (no API key required; rate-limited to 25 req/min, 10 jobs per
request for unregistered access) to deterministically resolve identifiers
to a single primary US composite ticker. Never guesses: anything ambiguous
or unresolved is recorded in the exclusion/audit ledger, not silently
dropped or picked.

Checkpointed per batch (JSONL append) so a transient failure loses at most
one batch of progress, not the whole ~277-request run.
"""

from __future__ import annotations

import csv
import json
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent / "artifacts" / "research-small-midcap-momentum-lane"
UNIVERSE_SOURCE = BASE / "universe-source"
NORMALIZED = UNIVERSE_SOURCE / "normalized"
OUT_DIR = BASE / "ticker-resolution"
CHECKPOINT_PATH = OUT_DIR / "identifier_resolution_checkpoint.jsonl"

OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"
BATCH_SIZE = 10  # unregistered OpenFIGI access caps requests at 10 mapping jobs each
REQUESTS_PER_WINDOW = 25
WINDOW_SECONDS = 65  # margin over the documented 60s window
MAX_RETRIES = 5


def load_holdings(ticker: str) -> list[dict]:
    path = NORMALIZED / f"{ticker.lower()}_common_stock_holdings_2025-12-31.csv"
    with path.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_jobs(rows: list[dict]) -> list[dict]:
    jobs = []
    for row in rows:
        cusip = row.get("cusip", "").strip()
        isin = row.get("isin", "").strip()
        if cusip and cusip != "N/A":
            jobs.append({"idType": "ID_CUSIP", "idValue": cusip, "identifier_used": cusip, "identifier_type": "CUSIP"})
        elif isin and isin != "N/A":
            jobs.append({"idType": "ID_ISIN", "idValue": isin, "identifier_used": isin, "identifier_type": "ISIN"})
        else:
            jobs.append({"idType": None, "idValue": None, "identifier_used": "", "identifier_type": "NONE"})
    return jobs


def call_openfigi(batch: list[dict]) -> list[dict]:
    payload = [
        {"idType": j["idType"], "idValue": j["idValue"]} if j["idType"] else {"idType": "ID_CUSIP", "idValue": "0000000000"}
        for j in batch
    ]
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENFIGI_URL, data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


#: Primary-listing exchange codes tried in order. 'US' (OpenFIGI's
#: consolidated-tape composite) resolves the large majority of names; a
#: fixed, documented fallback tier of single-market US equity exchanges
#: (NYSE / Nasdaq / NYSE American / NYSE Arca) catches the remainder that
#: OpenFIGI indexes only under a specific exchange rather than also
#: publishing a separate composite row. This is a uniform rule applied to
#: every identifier identically -- not a per-company judgment call.
_EXCHANGE_CODE_TIERS: tuple[tuple[str, ...], ...] = (
    ("US",),
    ("UN", "UW", "UA", "UR"),
)


def pick_canonical(data_entries: list[dict]) -> dict | None:
    """Deterministic selection among an identifier's FIGI matches. Does NOT
    filter on OpenFIGI's own securityType2 (e.g. 'Common Stock' vs 'REIT'
    vs 'MLP'): the source NPORT-P filing already restricts every input
    CUSIP/ISIN to assetCat == 'EC' (equity common shares), so re-filtering
    here on OpenFIGI's finer-grained, non-authoritative taxonomy only
    produces false ambiguity for ordinary listed REIT/MLP common shares."""
    for tier_codes in _EXCHANGE_CODE_TIERS:
        matches = [e for e in data_entries if e.get("exchCode") in tier_codes]
        if not matches:
            continue
        tickers = {e["ticker"] for e in matches}
        if len(tickers) == 1:
            return matches[0]
        return None  # ambiguous within the first tier that had any match at all
    return None


def classify(job: dict, entry: dict) -> dict:
    if job["identifier_type"] == "NONE":
        return {"resolution_status": "UNRESOLVED_NO_IDENTIFIER", "resolved_ticker": "", "figi": ""}
    if "error" in entry:
        return {"resolution_status": "UNRESOLVED_NOT_FOUND", "resolved_ticker": "", "figi": ""}
    canonical = pick_canonical(entry.get("data", []))
    if canonical is None:
        return {"resolution_status": "UNRESOLVED_AMBIGUOUS", "resolved_ticker": "", "figi": ""}
    return {
        "resolution_status": f"RESOLVED_{job['identifier_type']}",
        "resolved_ticker": canonical["ticker"],
        "figi": canonical["figi"],
    }


def load_checkpoint() -> dict[str, dict]:
    done: dict[str, dict] = {}
    if CHECKPOINT_PATH.exists():
        with CHECKPOINT_PATH.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                key = f"{rec['source_fund']}:{rec['row_index']}"
                done[key] = rec
    return done


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    done = load_checkpoint()
    print(f"Resuming with {len(done)} already-checkpointed rows")

    rate_state = {"count": 0, "window_start": time.monotonic()}
    checkpoint_fh = CHECKPOINT_PATH.open("a", encoding="utf-8")

    for ticker in ("IWR", "IWM"):
        rows = load_holdings(ticker)
        jobs = build_jobs(rows)
        for start in range(0, len(jobs), BATCH_SIZE):
            batch_idx = list(range(start, min(start + BATCH_SIZE, len(jobs))))
            if all(f"{ticker}:{i}" in done for i in batch_idx):
                continue

            batch = [jobs[i] for i in batch_idx]

            rate_state["count"] += 1
            if rate_state["count"] > REQUESTS_PER_WINDOW:
                elapsed = time.monotonic() - rate_state["window_start"]
                if elapsed < WINDOW_SECONDS:
                    time.sleep(WINDOW_SECONDS - elapsed)
                rate_state["window_start"] = time.monotonic()
                rate_state["count"] = 1

            api_result = None
            last_err = None
            for attempt in range(MAX_RETRIES):
                try:
                    api_result = call_openfigi(batch)
                    break
                except urllib.error.HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    last_err = f"{exc.code} {body}"
                    if exc.code == 429:
                        time.sleep(WINDOW_SECONDS)
                    else:
                        time.sleep(2 * (attempt + 1))
                except (urllib.error.URLError, TimeoutError) as exc:
                    last_err = str(exc)
                    time.sleep(2 * (attempt + 1))
            if api_result is None:
                raise SystemExit(f"OpenFIGI request failed for {ticker} batch starting at {start} after {MAX_RETRIES} retries: {last_err}")

            for i, job, entry in zip(batch_idx, batch, api_result):
                row = rows[i]
                record = {
                    "source_fund": ticker,
                    "row_index": i,
                    "issuer_name": row["issuer_name"],
                    "cusip": row["cusip"],
                    "isin": row["isin"],
                    "identifier_used": job["identifier_used"],
                    "identifier_type": job["identifier_type"],
                    "value_usd": row["value_usd"],
                    "pct_net_assets": row["pct_net_assets"],
                    **classify(job, entry),
                }
                checkpoint_fh.write(json.dumps(record) + "\n")
                checkpoint_fh.flush()
                done[f"{ticker}:{i}"] = record
            print(f"{ticker}: resolved batch {start}-{start+len(batch)} of {len(jobs)}")

    checkpoint_fh.close()

    all_results = list(done.values())
    all_results.sort(key=lambda r: (r["source_fund"], r["row_index"]))
    out_path = OUT_DIR / "identifier_resolution.csv"
    fieldnames = [k for k in all_results[0].keys() if k != "row_index"]
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for r in all_results:
            writer.writerow({k: r[k] for k in fieldnames})

    from collections import Counter
    status_counts = Counter(r["resolution_status"] for r in all_results)
    print("Resolution status counts:", dict(status_counts))
    print(f"Written: {out_path}")


if __name__ == "__main__":
    main()
