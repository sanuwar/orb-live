"""M46 stage 5: analysis, integrity checks, and predeclared classification
for the frozen feasibility experiment (stage 4 checkpoints).

Reports MID (IWR), SMALL (IWM), Combined SMALL_MID, and LARGE_CAP reference
separately, applies the classification frozen BEFORE any result was seen,
and writes the full trade ledger + provenance + hashes. No new simulation
here -- purely post-processing of already-computed rows.
"""

from __future__ import annotations

import csv
import hashlib
import json
import pickle
import statistics
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.orb_baseline_stability_research import excluding_symbol
from src.orb_profit_protection_exit_research import (
    ARM_BE_AFTER_050R,
    ENTRY_WAITED_FIRST_VALID_0950,
    full_stop_out_rate,
    max_drawdown_r_and_streak_from_rows,
    realized_r_distribution,
)
from src.orb_regime_diagnostic_research import MIN_SAMPLE_CLOSED_TRADES, symbol_summary, trade_quality_from_rows

BASE = ROOT / "artifacts" / "research-small-midcap-momentum-lane"
CHECKPOINT_DIR = BASE / "experiment-checkpoint"
RESULTS_DIR = BASE / "experiment-results"
FINAL_BASKET_CSV = BASE / "selection" / "final_basket.csv"

MFE_THRESHOLDS = (0.25, 0.50, 0.75, 1.00, 1.50)


def load_checkpoint(label: str) -> dict:
    with (CHECKPOINT_DIR / f"{label}.pkl").open("rb") as fh:
        return pickle.load(fh)


def load_basket_meta() -> dict[str, dict]:
    with FINAL_BASKET_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    return {r["resolved_ticker"]: r for r in rows}


def mfe_stats(rows) -> dict:
    values = [r.outcome["mfe_r"] for r in rows if r.outcome.get("entered") and r.outcome.get("mfe_r") is not None]
    if not values:
        return {"n": 0, "median_mfe_r": None, "mean_mfe_r": None, **{f"pct_reaching_{t}R".replace(".", ""): None for t in MFE_THRESHOLDS}}
    out = {
        "n": len(values),
        "median_mfe_r": statistics.median(values),
        "mean_mfe_r": statistics.mean(values),
    }
    for t in MFE_THRESHOLDS:
        key = f"pct_reaching_{t}R".replace(".", "")
        out[key] = sum(1 for v in values if v >= t) / len(values)
    return out


def trades_per_month(rows, *, session_dates: tuple[date, ...]) -> float:
    closed = [r for r in rows if r.outcome["closed"]]
    span_days = (session_dates[-1] - session_dates[0]).days + 1
    months = span_days / 30.44
    return len(closed) / months if months > 0 else 0.0


def integrity_checks(rows, *, universe: tuple[str, ...], session_dates: tuple[date, ...]) -> dict:
    session_date_counts = Counter(r.decision["session_date"] for r in rows)
    duplicate_days = {d: n for d, n in session_date_counts.items() if n > 1}
    symbols_seen = {r.decision["symbol"] for r in rows}
    unknown_symbols = symbols_seen - set(universe)
    state_counts = Counter(r.outcome["state"] for r in rows)
    entered_not_closed = sum(1 for r in rows if r.outcome["entered"] and not r.outcome["closed"])
    return {
        "candidate_occurrences": len(rows),
        "one_candidate_per_day_across_universe": len(duplicate_days) == 0,
        "duplicate_session_dates": duplicate_days,
        "symbols_outside_universe": sorted(unknown_symbols),
        "state_counts": dict(state_counts),
        "entered_but_still_open_at_data_end": entered_not_closed,
        "requested_sessions": len(session_dates),
        "sessions_with_a_candidate": len(session_date_counts),
        "sessions_without_any_candidate": len(session_dates) - len(session_date_counts),
    }


def top_n_excluded(rows, *, symbols: list[str]) -> dict:
    remaining = [r for r in rows if r.decision["symbol"] not in symbols]
    return trade_quality_from_rows(remaining)


def sector_diagnostics(rows, *, basket_meta: dict[str, dict]) -> dict:
    by_sector: dict[str, list] = defaultdict(list)
    for r in rows:
        sym = r.decision["symbol"]
        sector = basket_meta.get(sym, {}).get("sector_major_group", "UNKNOWN")
        by_sector[sector].append(r)
    per_sector = {}
    for sector, sector_rows in by_sector.items():
        tq = trade_quality_from_rows(sector_rows)
        per_sector[sector] = {
            "symbols": sorted({r.decision["symbol"] for r in sector_rows}),
            "trade_count": tq["closed_trades"],
            "cumulative_r": tq["cumulative_r"],
            "expectancy_r": tq["expectancy_r_per_trade"],
        }
    closed_rows = [r for r in rows if r.outcome["closed"]]
    total_r = sum(r.outcome["r_multiple"] for r in closed_rows if r.outcome["r_multiple"] is not None)
    sector_r = {s: d["cumulative_r"] or 0.0 for s, d in per_sector.items()}
    ranked = sorted(sector_r.items(), key=lambda kv: kv[1], reverse=True)
    best_sector_share = (ranked[0][1] / total_r) if ranked and total_r not in (0, None) else None
    top2_share = (sum(v for _, v in ranked[:2]) / total_r) if len(ranked) >= 2 and total_r not in (0, None) else None
    return {
        "per_sector": per_sector,
        "sectors_with_closed_trades": sum(1 for d in per_sector.values() if d["trade_count"] > 0),
        "best_sector_share_of_total_r": best_sector_share,
        "top_2_sector_share_of_total_r": top2_share,
        "sectors_with_positive_cum_r": sum(1 for d in per_sector.values() if (d["cumulative_r"] or 0) > 0),
    }


def long_short_breakdown(rows) -> dict:
    # ORB V0 is long-only by construction; verify rather than assume.
    directions = set()
    for r in rows:
        entry = r.outcome.get("entry_price")
        stop = r.outcome.get("initial_stop_price")
        if entry is not None and stop is not None:
            directions.add("LONG" if Decimal(stop) < Decimal(entry) else "SHORT_OR_INVERTED")
    return {"directions_observed": sorted(directions), "is_long_only": directions <= {"LONG"}}


def full_lane_report(*, label: str, rows, universe: tuple[str, ...], session_dates: tuple[date, ...], basket_meta: dict[str, dict] | None) -> dict:
    tq = trade_quality_from_rows(rows)
    dd = max_drawdown_r_and_streak_from_rows(rows)
    fso = full_stop_out_rate(rows)
    mfe = mfe_stats(rows)
    tpm = trades_per_month(rows, session_dates=session_dates)
    sym_summ = symbol_summary(rows)
    integrity = integrity_checks(rows, universe=universe, session_dates=session_dates)
    ls = long_short_breakdown(rows)

    best_symbol = None
    top = sym_summ.get("top_profitable_contributors") or []
    if top:
        best_symbol = top[0]["symbol"]
    excl_best = excluding_symbol(rows, symbol=best_symbol) if best_symbol else None

    top3_symbols = [c["symbol"] for c in top[:3]]
    excl_top3 = top_n_excluded(rows, symbols=top3_symbols) if top3_symbols else None

    report = {
        "label": label,
        "trade_quality": tq,
        "max_drawdown_r": dd["maximum_drawdown_r"],
        "longest_losing_streak": dd["longest_losing_streak"],
        "full_stop_out_rate": fso,
        "mfe_stats": mfe,
        "trades_per_month": tpm,
        "symbol_summary": sym_summ,
        "expectancy_excluding_best_symbol": excl_best,
        "expectancy_excluding_top3_symbols": excl_top3,
        "integrity": integrity,
        "long_short_breakdown": ls,
    }
    if basket_meta is not None:
        report["sector_diagnostics"] = sector_diagnostics(rows, basket_meta=basket_meta)
    return report


# ---------------------------------------------------------------------------
# Predeclared classification (frozen before any P3 result was computed --
# reproduced verbatim from the M46 specification).
# ---------------------------------------------------------------------------

def classify(*, small_mid: dict, large_cap: dict) -> dict:
    tq = small_mid["trade_quality"]
    reasons = []

    if tq["closed_trades"] < 10:
        return {"verdict": "INSUFFICIENT_EVIDENCE", "reasons": [f"SMALL_MID closed_trades={tq['closed_trades']} < 10"]}

    c1 = tq["closed_trades"] >= 20
    c2 = (tq["expectancy_r_per_trade"] or 0) > 0
    c3 = (tq["profit_factor_r"] or 0) > 1.10
    c4 = small_mid["trades_per_month"] > large_cap["trades_per_month"]
    c5 = (tq["cumulative_r"] or 0) > 0
    excl_best = small_mid["expectancy_excluding_best_symbol"]
    c6 = excl_best is not None and (excl_best["expectancy_r_per_trade"] or -1) > 0
    sector_diag = small_mid.get("sector_diagnostics", {})
    c7 = sector_diag.get("sectors_with_closed_trades", 0) >= 4
    c8 = (sector_diag.get("best_sector_share_of_total_r") is None) or (sector_diag.get("best_sector_share_of_total_r") <= 0.50)
    # "no single symbol contributes >50% of positive cumulative R"
    sym_summ = small_mid["symbol_summary"]
    top3_share = sym_summ.get("top_3_share_of_total_pnl")
    top1_share = None
    top = sym_summ.get("top_profitable_contributors") or []
    if top and sym_summ.get("total_realized_pnl") not in (None, "0"):
        try:
            total = Decimal(sym_summ["total_realized_pnl"])
            top1_share = float(Decimal(top[0]["realized_pnl"]) / total) if total != 0 else None
        except Exception:
            top1_share = None
    c8_symbol = (top1_share is None) or (top1_share <= 0.50)

    small_mfe = small_mid["mfe_stats"]
    large_mfe = large_cap["mfe_stats"]
    c9a = (small_mfe.get("pct_reaching_050R") or 0) > (large_mfe.get("pct_reaching_050R") or 0)
    c9b = (small_mfe.get("pct_reaching_100R") or 0) > (large_mfe.get("pct_reaching_100R") or 0)
    c9 = c9a or c9b

    core = {"c1_closed_ge_20": c1, "c2_expectancy_positive": c2, "c3_pf_r_gt_1_10": c3,
            "c4_trades_per_month_gt_large_cap": c4, "c5_r_per_month_positive": c5,
            "c6_expectancy_excl_best_positive": c6, "c7_sectors_with_trades_ge_4": c7,
            "c8_best_sector_share_le_50pct": c8, "c8_symbol_no_single_gt_50pct": c8_symbol,
            "c9_mfe_edge_050_or_100": c9, "c9a_mfe_050_edge": c9a, "c9b_mfe_100_edge": c9b}

    all_core_pass = c1 and c2 and c3 and c4 and c5 and c6 and c7 and c8 and c8_symbol and c9
    if all_core_pass:
        verdict = "PROMISING"
    else:
        failed = [k for k, v in core.items() if not v and not k.startswith("c9a") and not k.startswith("c9b")]
        # MIXED vs NOT_PROMISING: MIXED if expectancy positive & PF_R>1.0 despite missing some secondary criteria.
        if c2 and (tq["profit_factor_r"] or 0) > 1.0:
            verdict = "MIXED"
        else:
            verdict = "NOT_PROMISING"
        reasons = failed

    return {"verdict": verdict, "criteria": core, "reasons": reasons}


def main() -> None:
    large_cap_result = load_checkpoint("LARGE_CAP")
    small_mid_result = load_checkpoint("SMALL_MID")
    basket_meta = load_basket_meta()

    large_cap_rows = large_cap_result["rows"][ENTRY_WAITED_FIRST_VALID_0950][ARM_BE_AFTER_050R]
    small_mid_rows = small_mid_result["rows"][ENTRY_WAITED_FIRST_VALID_0950][ARM_BE_AFTER_050R]

    mid_rows = [r for r in small_mid_rows if basket_meta.get(r.decision["symbol"], {}).get("bucket") == "MID"]
    small_rows = [r for r in small_mid_rows if basket_meta.get(r.decision["symbol"], {}).get("bucket") == "SMALL"]

    mid_universe = tuple(t for t, m in basket_meta.items() if m["bucket"] == "MID")
    small_universe = tuple(t for t, m in basket_meta.items() if m["bucket"] == "SMALL")

    session_dates = small_mid_result["session_dates"]
    large_cap_session_dates = large_cap_result["session_dates"]

    reports = {
        "MID": full_lane_report(label="MID (IWR)", rows=mid_rows, universe=mid_universe, session_dates=session_dates, basket_meta=basket_meta),
        "SMALL": full_lane_report(label="SMALL (IWM)", rows=small_rows, universe=small_universe, session_dates=session_dates, basket_meta=basket_meta),
        "SMALL_MID_COMBINED": full_lane_report(label="SMALL_MID combined", rows=small_mid_rows, universe=small_mid_result["universe"], session_dates=session_dates, basket_meta=basket_meta),
        "LARGE_CAP": full_lane_report(label="LARGE_CAP reference", rows=large_cap_rows, universe=large_cap_result["universe"], session_dates=large_cap_session_dates, basket_meta=None),
    }

    classification = classify(small_mid=reports["SMALL_MID_COMBINED"], large_cap=reports["LARGE_CAP"])

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    def _json_default(o):
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, date):
            return o.isoformat()
        raise TypeError(f"not serializable: {type(o)}")

    results_path = RESULTS_DIR / "feasibility_results.json"
    results_path.write_text(json.dumps({"reports": reports, "classification": classification}, indent=2, default=_json_default), encoding="utf-8")

    # Full trade ledger.
    ledger_path = RESULTS_DIR / "trade_ledger.csv"
    ledger_rows = []
    for lane_label, rows in (("MID", mid_rows), ("SMALL", small_rows), ("LARGE_CAP", large_cap_rows)):
        for r in rows:
            ledger_rows.append({"lane": lane_label, **r.decision, **r.outcome})
    if ledger_rows:
        fieldnames = sorted({k for row in ledger_rows for k in row.keys()})
        with ledger_path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in ledger_rows:
                writer.writerow(row)

    config_path = RESULTS_DIR / "experiment_config.json"
    config_path.write_text(json.dumps({
        "entry_setup": "GAP_050_RVOL_175", "timing_policy": "WAITED_FIRST_VALID_0950",
        "exit_policy": "BE_AFTER_050R", "period": "P3",
        "small_mid_universe": small_mid_result["universe"], "large_cap_universe": large_cap_result["universe"],
        "small_mid_session_dates": [d.isoformat() for d in session_dates],
        "large_cap_session_dates": [d.isoformat() for d in large_cap_session_dates],
    }, indent=2), encoding="utf-8")

    def sha256_file(p: Path) -> str:
        return hashlib.sha256(p.read_bytes()).hexdigest()

    hashes = {p.name: sha256_file(p) for p in (results_path, ledger_path, config_path) if p.exists()}
    (RESULTS_DIR / "artifact_hashes.json").write_text(json.dumps(hashes, indent=2), encoding="utf-8")

    print("=== Classification ===")
    print(json.dumps(classification, indent=2))
    print()
    for key in ("MID", "SMALL", "SMALL_MID_COMBINED", "LARGE_CAP"):
        tq = reports[key]["trade_quality"]
        print(f"{key}: closed={tq['closed_trades']} win_rate={tq['win_rate']} exp_r={tq['expectancy_r_per_trade']} pf_r={tq['profit_factor_r']} cum_r={tq['cumulative_r']} max_dd_r={reports[key]['max_drawdown_r']} trades/mo={reports[key]['trades_per_month']:.2f}")


if __name__ == "__main__":
    main()
