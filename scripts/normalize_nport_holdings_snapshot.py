"""One-off, read-only normalizer for the M46 IWR/IWM NPORT-P source snapshot.

Parses the raw SEC EDGAR NPORT-P ``primary_doc.xml`` filings for iShares
Russell Mid-Cap ETF (IWR) and iShares Russell 2000 ETF (IWM), report period
2025-12-31, and writes normalized common-stock holdings CSVs plus a
provenance manifest (source URLs, accession numbers, series/class IDs,
SHA-256 of raw and normalized files). This is a research data-preparation
step, not part of the trading system: no broker calls, no strategy code.
"""

from __future__ import annotations

import csv
import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path

NS = {"n": "http://www.sec.gov/edgar/nport"}

BASE = Path(__file__).resolve().parent.parent / "artifacts" / "research-small-midcap-momentum-lane" / "universe-source"
RAW = BASE / "raw"
NORMALIZED = BASE / "normalized"

FUNDS = {
    "IWR": {
        "raw_file": RAW / "iwr_nport_p_20260225_0002071691-26-004214.xml",
        "accession": "0002071691-26-004214",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/000207169126004214/primary_doc.xml",
        "filing_index_url": "https://www.sec.gov/Archives/edgar/data/1100663/000207169126004214/index.json",
    },
    "IWM": {
        "raw_file": RAW / "iwm_nport_p_20260225_0002071691-26-004226.xml",
        "accession": "0002071691-26-004226",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1100663/000207169126004226/primary_doc.xml",
        "filing_index_url": "https://www.sec.gov/Archives/edgar/data/1100663/000207169126004226/index.json",
    },
}

# SEC's official ticker -> (cik, seriesId, classId) mapping, independently
# confirmed via https://www.sec.gov/files/company_tickers_mf.json
EXPECTED_IDENTITY = {
    "IWR": {"cik": "0001100663", "seriesId": "S000004338", "classId": "C000012068"},
    "IWM": {"cik": "0001100663", "seriesId": "S000004344", "classId": "C000012074"},
}


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()


def text(el: ET.Element | None) -> str:
    return el.text.strip() if el is not None and el.text else ""


def extract_fund(ticker: str, cfg: dict) -> dict:
    raw_path: Path = cfg["raw_file"]
    tree = ET.parse(raw_path)
    root = tree.getroot()

    gen_info = root.find(".//n:formData/n:genInfo", NS)
    reg_cik = text(root.find(".//n:filerInfo/n:filer/n:issuerCredentials/n:cik", NS))
    series_id = text(root.find(".//n:filerInfo/n:seriesClassInfo/n:seriesId", NS))
    class_id = text(root.find(".//n:filerInfo/n:seriesClassInfo/n:classId", NS))
    series_name = text(gen_info.find("n:seriesName", NS)) if gen_info is not None else ""
    reg_name = text(gen_info.find("n:regName", NS)) if gen_info is not None else ""
    report_date = text(gen_info.find("n:repPdDate", NS)) if gen_info is not None else ""
    net_assets = text(root.find(".//n:formData/n:fundInfo/n:netAssets", NS))

    expected = EXPECTED_IDENTITY[ticker]
    identity_ok = (
        reg_cik == expected["cik"]
        and series_id == expected["seriesId"]
        and class_id == expected["classId"]
        and report_date == "2025-12-31"
    )
    if not identity_ok:
        raise SystemExit(
            f"IDENTITY VERIFICATION FAILED for {ticker}: "
            f"cik={reg_cik} seriesId={series_id} classId={class_id} repPdDate={report_date} "
            f"(expected {expected}, repPdDate=2025-12-31)"
        )

    holdings = []
    for sec in root.findall(".//n:invstOrSecs/n:invstOrSec", NS):
        asset_cat = text(sec.find("n:assetCat", NS))
        if asset_cat != "EC":
            continue
        isin_el = sec.find("n:identifiers/n:isin", NS)
        holdings.append(
            {
                "issuer_name": text(sec.find("n:name", NS)),
                "title": text(sec.find("n:title", NS)),
                "cusip": text(sec.find("n:cusip", NS)),
                "isin": isin_el.get("value", "") if isin_el is not None else "",
                "balance_shares": text(sec.find("n:balance", NS)),
                "value_usd": text(sec.find("n:valUSD", NS)),
                "pct_net_assets": text(sec.find("n:pctVal", NS)),
                "asset_category": asset_cat,
                "issuer_category": text(sec.find("n:issuerCat", NS)),
                "invest_country": text(sec.find("n:invCountry", NS)),
            }
        )

    NORMALIZED.mkdir(parents=True, exist_ok=True)
    out_csv = NORMALIZED / f"{ticker.lower()}_common_stock_holdings_2025-12-31.csv"
    with out_csv.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(holdings[0].keys()))
        writer.writeheader()
        writer.writerows(holdings)

    return {
        "ticker": ticker,
        "reg_name": reg_name,
        "series_name": series_name,
        "reg_cik": reg_cik,
        "series_id": series_id,
        "class_id": class_id,
        "report_date": report_date,
        "net_assets_usd": net_assets,
        "accession_number": cfg["accession"],
        "filing_type": "NPORT-P",
        "source_url": cfg["source_url"],
        "filing_index_url": cfg["filing_index_url"],
        "raw_file": str(raw_path.relative_to(BASE.parent.parent.parent)),
        "raw_file_sha256": sha256_of(raw_path),
        "normalized_file": str(out_csv.relative_to(BASE.parent.parent.parent)),
        "normalized_file_sha256": sha256_of(out_csv),
        "common_stock_holdings_count": len(holdings),
        "identity_verified_against_sec_ticker_mapping": True,
    }


def main() -> None:
    manifest = {"source_as_of_date": "2025-12-31", "retrieval_method": "SEC EDGAR NPORT-P primary_doc.xml (route 1: full text search -> filing index -> primary document)", "funds": {}}
    for ticker, cfg in FUNDS.items():
        manifest["funds"][ticker] = extract_fund(ticker, cfg)
        print(f"{ticker}: {manifest['funds'][ticker]['common_stock_holdings_count']} common-stock holdings extracted, identity verified")

    manifest_path = BASE / "provenance_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"Provenance manifest written to {manifest_path}")


if __name__ == "__main__":
    main()
