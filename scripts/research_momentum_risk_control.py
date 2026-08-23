"""CLI for M53 (Issue #358): bounded cross-sectional momentum
risk-control feasibility.

Reproduces M52's exact baseline from the existing immutable caches
(`artifacts/research-cross-sectional-momentum/`) via
`scripts.research_cross_sectional_momentum`'s own fetch/build
functions -- no new network I/O and no repeated SEC/OpenFIGI fetch
unless a cache file is demonstrably missing (pass --fetch-missing to
allow filling a genuine gap; default is cache-only, matching M53's
"reuse existing caches" requirement). Verifies bit-for-bit parity
against the committed M52 result before running either new arm, then
runs exactly two frozen arms (BREADTH_30, MARKET_VOL_15) once each,
classifies both independently via M52's own unmodified classification
function, and applies the frozen M53 selection rule
(`src/momentum_risk_control_research.py`).

Market-data and public-filing reads only (via the reused M52 cache
layer). No brokerage-account, position, order, or Robinhood call
anywhere in this module.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

import scripts.research_cross_sectional_momentum as m52_cli
from src.cross_sectional_momentum_research import (
    BASE_COST_BPS,
    EVALUATION_END,
    EVALUATION_START,
    STARTING_CAPITAL,
    STRESS_COST_BPS,
    classify_cross_sectional_momentum,
    compute_portfolio_metrics,
    month_end_signal_dates,
    simulate_cross_sectional_momentum_portfolio,
)
from src.momentum_risk_control_research import (
    ARM_A_TOP_N,
    ARM_B_TOP_N,
    ARM_BREADTH_30,
    ARM_MARKET_VOL_15,
    MARKET_VOL_LOOKBACK_SESSIONS,
    MARKET_VOL_TARGET_ANNUALIZED,
    compute_opportunity_cost,
    select_m53_design,
)
from src.orb_capital_target_strategy_family_research import annualized_volatility_pct

M52_RESULT_PATH = ROOT / "artifacts" / "research-cross-sectional-momentum" / "results" / "m52_result.json"
RESULTS_DIR = ROOT / "artifacts" / "research-momentum-risk-control" / "results"
COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "MOMENTUM_RISK_CONTROL_ARTIFACT_HASHES.json"

SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "cross_sectional_momentum_research.py",
    ROOT / "src" / "momentum_risk_control_research.py",
    ROOT / "scripts" / "research_momentum_risk_control.py",
)

#: Baseline/arm metric fields compared for exact parity/reporting. Every
#: entry must be a scalar (Decimal, int, or None) present in
#: `compute_portfolio_metrics`'s return value -- segment-level parity is
#: checked separately, below.
_COMPARABLE_METRIC_FIELDS = (
    "mean_monthly_dollars", "median_monthly_dollars", "gross_pnl", "realized_net_pnl", "equity_net_pnl",
    "endpoint_marked_net_pnl", "cagr", "sharpe_ratio", "sortino_ratio", "annualized_downside_deviation_pct",
    "max_drawdown_dollars", "max_drawdown_pct", "worst_month_dollars", "worst_month_pct",
    "fifth_percentile_month_pct", "profitable_month_pct", "longest_losing_streak_months", "total_costs",
    "turnover", "avg_invested_positions", "max_invested_positions", "avg_cash_utilization_pct",
    "best_symbol_positive_contribution_share", "signal_count", "intended_order_count", "fill_count",
    "blocked_intent_count", "final_equity", "final_cash",
)


class MomentumRiskControlCLIError(RuntimeError):
    """Refused: an M53 (Issue #358) CLI-level precondition failed -- a
    required cache/result file is missing, or baseline parity did not
    hold. Never silently substituted or guessed."""


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
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _load_m52_baseline_result() -> dict[str, Any]:
    if not M52_RESULT_PATH.exists():
        raise MomentumRiskControlCLIError(
            f"M52 result not found at {M52_RESULT_PATH} -- cannot verify baseline parity without the real, "
            "already-committed M52 empirical result. M52's own caches/results must already exist locally; "
            "this script never re-derives them."
        )
    return json.loads(M52_RESULT_PATH.read_text(encoding="utf-8"))


def verify_baseline_parity(*, recomputed_base_metrics: dict[str, Any], recomputed_stress_metrics: dict[str, Any]) -> dict[str, Any]:
    """M53 (Issue #358) sequencing requirement: bit-for-bit parity
    against the exact M52 baseline before either new arm is examined.
    Compares every scalar metric field plus every calendar segment for
    EXACT equality -- Decimal values parsed back from the committed M52
    result JSON and compared as Decimal, never as strings, since
    trailing-zero formatting differences are not a real mismatch. Any
    genuine value mismatch is a hard stop, never silently tolerated."""
    m52_result = _load_m52_baseline_result()
    mismatches: list[dict[str, Any]] = []

    for scenario, recomputed, committed in (
        ("base", recomputed_base_metrics, m52_result["base_metrics"]),
        ("stress", recomputed_stress_metrics, m52_result["stress_metrics"]),
    ):
        for field in _COMPARABLE_METRIC_FIELDS:
            recomputed_value = recomputed.get(field)
            committed_raw = committed.get(field)
            committed_value: Any = committed_raw
            if isinstance(recomputed_value, Decimal) and committed_raw is not None:
                committed_value = Decimal(committed_raw)
            if recomputed_value != committed_value:
                mismatches.append({"scenario": scenario, "field": field, "recomputed": str(recomputed_value), "committed": str(committed_value)})

        for seg_label, seg in recomputed["segments"].items():
            committed_seg = committed["segments"].get(seg_label)
            if committed_seg is None:
                mismatches.append({"scenario": scenario, "field": f"segments.{seg_label}", "recomputed": str(seg), "committed": None})
                continue
            committed_net_pnl = Decimal(committed_seg["net_pnl"])
            if seg["net_pnl"] != committed_net_pnl or seg["positive"] != committed_seg["positive"] or seg["month_count"] != committed_seg["month_count"]:
                mismatches.append({"scenario": scenario, "field": f"segments.{seg_label}", "recomputed": str(seg["net_pnl"]), "committed": str(committed_net_pnl)})

    if mismatches:
        raise MomentumRiskControlCLIError(
            f"BASELINE PARITY FAILED -- {len(mismatches)} field(s) diverge from the committed M52 result "
            f"(commit {m52_result.get('generation_git_head_sha')}). Refusing to proceed to Arm A / Arm B "
            f"until this is diagnosed. Details: {json.dumps(mismatches, default=str)}"
        )
    return {
        "ok": True,
        "compared_scalar_fields": len(_COMPARABLE_METRIC_FIELDS) * 2,
        "compared_segments": sum(len(m["segments"]) for m in (recomputed_base_metrics, recomputed_stress_metrics)),
        "m52_generated_at": m52_result["generated_at"],
        "m52_git_head_sha": m52_result["generation_git_head_sha"],
    }


def _report_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    """M53's own reporting layer adds annualized volatility (a required
    M53 output not previously in M52's metrics dict) without mutating
    M52's frozen `compute_portfolio_metrics` -- computed here from the
    same M48 primitive `compute_portfolio_metrics` already reuses for
    Sharpe/Sortino, applied to the same monthly percent-return series."""
    pct_values = [p for _, p in metrics["monthly_pct_series"]]
    reported = {k: v for k, v in metrics.items() if k not in ("monthly_pct_series", "monthly_dollar_series", "symbol_realized_pnl")}
    reported["annualized_volatility_pct"] = annualized_volatility_pct(pct_values)
    return reported


async def _run_baseline_and_arms(*, fetch_missing: bool) -> dict[str, Any]:
    print("Fetching IWB N-PORT-P filing list from SEC EDGAR (cache-only unless --fetch-missing)...")
    filings = await m52_cli.fetch_iwb_nport_filings(fetch_missing=fetch_missing)
    print(f"Found {len(filings)} N-PORT-P filings.")

    snapshots = await m52_cli.build_all_universe_snapshots(filings, fetch_missing=fetch_missing)

    master_dates = tuple(m52_cli._all_research_calendar_sessions(m52_cli.WARMUP_TARGET_START, m52_cli.ALPACA_FETCH_END_EXCLUSIVE))
    signal_dates = month_end_signal_dates(master_dates, start=EVALUATION_START, end=EVALUATION_END)
    monthly_universes, filing_accession_for_signal = m52_cli.build_monthly_universes(signal_dates, snapshots)

    all_tickers = sorted({t for tickers in monthly_universes.values() for t in tickers})
    symbols_to_fetch = tuple(all_tickers + ["SPY"])
    print(f"Union universe across all signal dates: {len(all_tickers)} distinct resolved tickers.")
    series_by_symbol = await m52_cli.fetch_daily_bars_for_symbols(symbols_to_fetch, fetch_missing=fetch_missing)
    spy_series = series_by_symbol.get("SPY")
    if spy_series is None:
        raise MomentumRiskControlCLIError("BLOCKER: SPY daily bars unavailable -- cannot evaluate the trend filter, the vol-exposure cap, or compare to buy-and-hold.")

    def _simulate_and_measure(*, top_n: int, vol_target_annualized: Decimal | None, vol_lookback_sessions: int) -> dict[str, Any]:
        base_result = simulate_cross_sectional_momentum_portfolio(
            monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
            master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
            cost_bps=BASE_COST_BPS, cost_label="base_5bps", starting_capital=STARTING_CAPITAL,
            filing_accession_for_signal=filing_accession_for_signal, top_n=top_n,
            vol_target_annualized=vol_target_annualized, vol_lookback_sessions=vol_lookback_sessions,
        )
        stress_result = simulate_cross_sectional_momentum_portfolio(
            monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
            master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
            cost_bps=STRESS_COST_BPS, cost_label="stress_10bps", starting_capital=STARTING_CAPITAL,
            filing_accession_for_signal=filing_accession_for_signal, top_n=top_n,
            vol_target_annualized=vol_target_annualized, vol_lookback_sessions=vol_lookback_sessions,
        )
        stage0 = m52_cli.run_stage0_checks(
            snapshots=snapshots, monthly_universes=monthly_universes, signal_dates=signal_dates,
            series_by_symbol=series_by_symbol, spy_series=spy_series, base_result=base_result, stress_result=stress_result,
        )
        base_metrics = compute_portfolio_metrics(base_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
        stress_metrics = compute_portfolio_metrics(stress_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
        classification = classify_cross_sectional_momentum(
            base_metrics=base_metrics, stress_metrics=stress_metrics, integrity_failure=not stage0["_all_ok"],
        )
        return {"base_metrics": base_metrics, "stress_metrics": stress_metrics, "stage0": stage0, "classification": classification}

    print("Running baseline (M52 exact reproduction: top_n=15, no vol cap)...")
    baseline = _simulate_and_measure(top_n=15, vol_target_annualized=None, vol_lookback_sessions=MARKET_VOL_LOOKBACK_SESSIONS)
    print(f"Baseline Stage-0 all_ok={baseline['stage0']['_all_ok']}, classification={baseline['classification']['verdict']}")

    parity = verify_baseline_parity(recomputed_base_metrics=baseline["base_metrics"], recomputed_stress_metrics=baseline["stress_metrics"])
    print(f"Baseline parity vs. committed M52 result: {parity['ok']} ({parity['compared_scalar_fields']} scalar fields, {parity['compared_segments']} segment entries)")

    print(f"Running Arm A ({ARM_BREADTH_30}: top_n={ARM_A_TOP_N})...")
    arm_a = _simulate_and_measure(top_n=ARM_A_TOP_N, vol_target_annualized=None, vol_lookback_sessions=MARKET_VOL_LOOKBACK_SESSIONS)
    print(f"Arm A Stage-0 all_ok={arm_a['stage0']['_all_ok']}, classification={arm_a['classification']['verdict']}")

    print(f"Running Arm B ({ARM_MARKET_VOL_15}: top_n={ARM_B_TOP_N}, vol_target={MARKET_VOL_TARGET_ANNUALIZED})...")
    arm_b = _simulate_and_measure(top_n=ARM_B_TOP_N, vol_target_annualized=MARKET_VOL_TARGET_ANNUALIZED, vol_lookback_sessions=MARKET_VOL_LOOKBACK_SESSIONS)
    print(f"Arm B Stage-0 all_ok={arm_b['stage0']['_all_ok']}, classification={arm_b['classification']['verdict']}")

    return {
        "filings": filings, "snapshots": snapshots, "parity": parity,
        "baseline": baseline, "arm_a": arm_a, "arm_b": arm_b,
    }


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-missing", action="store_true", help="Fetch data not already in the M52-only caches (default: cache-only; M53 must not repeat the multi-hour fetch unless a cache file is demonstrably missing).")
    args = parser.parse_args(argv)

    run = await _run_baseline_and_arms(fetch_missing=args.fetch_missing)
    baseline, arm_a, arm_b = run["baseline"], run["arm_a"], run["arm_b"]

    decision = select_m53_design(
        arm_a_label=ARM_BREADTH_30, arm_a_classification=arm_a["classification"]["verdict"],
        arm_a_stress_metrics=arm_a["stress_metrics"], arm_a_base_metrics=arm_a["base_metrics"],
        arm_b_label=ARM_MARKET_VOL_15, arm_b_classification=arm_b["classification"]["verdict"],
        arm_b_stress_metrics=arm_b["stress_metrics"], arm_b_base_metrics=arm_b["base_metrics"],
    )
    print(f"M53 decision: {decision['decision']} (selected_arm={decision['selected_arm']}, reason={decision['reason']})")

    opportunity_cost = {
        "arm_a_base": compute_opportunity_cost(arm_a["base_metrics"], baseline_metrics=baseline["base_metrics"]),
        "arm_a_stress": compute_opportunity_cost(arm_a["stress_metrics"], baseline_metrics=baseline["stress_metrics"]),
        "arm_b_base": compute_opportunity_cost(arm_b["base_metrics"], baseline_metrics=baseline["base_metrics"]),
        "arm_b_stress": compute_opportunity_cost(arm_b["stress_metrics"], baseline_metrics=baseline["stress_metrics"]),
    }

    generated_at = datetime.now(timezone.utc)
    result_doc = {
        "milestone": "M53", "issue": 358, "generated_at": generated_at.isoformat(),
        "baseline_parity": run["parity"],
        "baseline": {
            "base_metrics": _report_metrics(baseline["base_metrics"]), "stress_metrics": _report_metrics(baseline["stress_metrics"]),
            "stage0": baseline["stage0"], "classification": baseline["classification"],
        },
        "arm_a_breadth_30": {
            "top_n": ARM_A_TOP_N,
            "base_metrics": _report_metrics(arm_a["base_metrics"]), "stress_metrics": _report_metrics(arm_a["stress_metrics"]),
            "stage0": arm_a["stage0"], "classification": arm_a["classification"],
        },
        "arm_b_market_vol_15": {
            "top_n": ARM_B_TOP_N, "vol_lookback_sessions": MARKET_VOL_LOOKBACK_SESSIONS, "vol_target_annualized": str(MARKET_VOL_TARGET_ANNUALIZED),
            "base_metrics": _report_metrics(arm_b["base_metrics"]), "stress_metrics": _report_metrics(arm_b["stress_metrics"]),
            "stage0": arm_b["stage0"], "classification": arm_b["classification"],
        },
        "opportunity_cost_vs_m52_baseline": opportunity_cost,
        "m53_decision": decision,
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
    }

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    results_path = RESULTS_DIR / "m53_result.json"
    results_path.write_text(json.dumps(result_doc, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Result written to {results_path}")

    committed_hashes = {
        "milestone": "M53", "issue": 358, "generated_at": generated_at.isoformat(),
        "m53_decision": decision,
        "arm_a_classification": arm_a["classification"]["verdict"],
        "arm_b_classification": arm_b["classification"]["verdict"],
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "result_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
    }
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")

    return 0


if __name__ == "__main__":
    import asyncio
    raise SystemExit(asyncio.run(main()))
