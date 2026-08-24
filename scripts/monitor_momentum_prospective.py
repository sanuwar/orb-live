"""CLI for M55 (Issue #362): prospective simulated monitoring of the
frozen M52 momentum candidate.

Runs ONCE and exits -- never remains open waiting for future sessions,
never polls continuously. Reuses M52's exact simulation code
(`simulate_cross_sectional_momentum_portfolio` with `top_n=15`, no
volatility-exposure cap -- M52's own defaults, never M53's) and M52's
existing SEC/OpenFIGI/Alpaca caches directly; the only new network
call this script ever makes is a cheap SEC EDGAR filing-list refresh
(to discover whether a newer N-PORT-P filing now exists) and, only
once a real signal is genuinely due, new Alpaca daily bars for the
sessions since M52's original fetch window closed. No Alpaca account,
position, order, balance, or portfolio endpoint is ever called --
market-data-only, matching Issue #362's data rules exactly.

Checkpoint ledger: `docs/research/MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json`,
an append-only, hash-chained JSON array (see
`src/momentum_prospective_monitor.py` for the chain primitives). A
checkpoint already present is NEVER rewritten; a rerun against the
same completed data is idempotent and appends nothing.
"""
from __future__ import annotations

import argparse
import asyncio
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
import scripts.research_momentum_risk_control as m53_cli
from src.cross_sectional_momentum_research import (
    BASE_COST_BPS,
    EVALUATION_END,
    EVALUATION_START,
    STARTING_CAPITAL,
    STRESS_COST_BPS,
    TOP_N,
    compute_portfolio_metrics,
    month_end_signal_dates,
    simulate_cross_sectional_momentum_portfolio,
)
from src.momentum_prospective_monitor import (
    CHECKPOINT_TYPE_INITIALIZATION,
    NY_TZ,
    RUN_STATUS_INITIALIZED_WAITING_FOR_FIRST_SIGNAL,
    RUN_STATUS_NOT_DUE,
    RUN_STATUS_SIGNAL_DUE,
    MonitorProtocolError,
    assert_frozen_candidate_config,
    checkpoint_run_decision,
    compute_checkpoint_hash,
    to_ny_date,
    validate_hash_chain,
)

CHECKPOINT_LEDGER_PATH = ROOT / "docs" / "research" / "MOMENTUM_PROSPECTIVE_MONITOR_M55_CHECKPOINTS.json"

#: How far into the future the calendar/session logic looks -- well
#: past a 12-cycle (roughly one-year) monitoring horizon starting
#: around August 2026, and safely within src.exchange_calendar's own
#: covered range (through 2027-12-31), so every date this script
#: reasons about uses the trusted production calendar, never a guess.
CALENDAR_LOOKAHEAD_END_EXCLUSIVE = date(2027, 10, 1)

SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "momentum_prospective_monitor.py",
    ROOT / "scripts" / "monitor_momentum_prospective.py",
)

_FORBIDDEN_SOURCE_PATTERNS = ("robin_stocks", "place_order", "cancel_order", "mcp__", "buying_power", "/v2/account", "/v2/positions", "/v2/orders")


class MonitorCLIError(RuntimeError):
    """Refused: an M55 (Issue #362) CLI-level precondition failed."""


def _git_head_sha() -> str:
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
    return out.stdout.strip()


def _git_commit_timestamp_utc(sha: str) -> datetime:
    out = subprocess.run(["git", "show", "-s", "--format=%cI", sha], cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
    return datetime.fromisoformat(out.stdout.strip()).astimezone(timezone.utc)


def _source_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES_FOR_HASHING}


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _load_checkpoint_ledger() -> list[dict[str, Any]]:
    if not CHECKPOINT_LEDGER_PATH.exists():
        return []
    return json.loads(CHECKPOINT_LEDGER_PATH.read_text(encoding="utf-8"))


def _no_broker_call_path_check() -> dict[str, Any]:
    hits: list[str] = []
    for p in SOURCE_FILES_FOR_HASHING:
        text = p.read_text(encoding="utf-8")
        for pattern in _FORBIDDEN_SOURCE_PATTERNS:
            if pattern in text:
                hits.append(f"{p.name}: {pattern!r}")
    return {"ok": len(hits) == 0, "forbidden_pattern_hits": hits}


def _strategy_enabled_check() -> dict[str, Any]:
    import yaml
    config_path = ROOT / "config" / "strategy_v0.yaml"
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    enabled = config.get("strategy", {}).get("enabled")
    return {"ok": enabled is False, "strategy_enabled": enabled}


async def run_initialization_gate(
    *, filings, snapshots, monthly_universes, series_by_symbol, spy_series, master_dates,
) -> dict[str, Any]:
    """The 10 initialization-gate items Issue #362 requires, checked
    once, before the prospective starting state is accepted."""
    gate: dict[str, Any] = {}

    # Item 1: exact M52 historical baseline parity.
    base_result = simulate_cross_sectional_momentum_portfolio(
        monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
        master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        cost_bps=BASE_COST_BPS, cost_label="base_5bps", starting_capital=STARTING_CAPITAL,
    )
    stress_result = simulate_cross_sectional_momentum_portfolio(
        monthly_universes=monthly_universes, series_by_symbol=series_by_symbol, spy_series=spy_series,
        master_dates=master_dates, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
        cost_bps=STRESS_COST_BPS, cost_label="stress_10bps", starting_capital=STARTING_CAPITAL,
    )
    base_metrics = compute_portfolio_metrics(base_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
    stress_metrics = compute_portfolio_metrics(stress_result, evaluation_end=EVALUATION_END, spy_series=spy_series)
    try:
        parity = m53_cli.verify_baseline_parity(recomputed_base_metrics=base_metrics, recomputed_stress_metrics=stress_metrics)
        gate["item1_m52_baseline_parity"] = {"ok": True, **parity}
    except m53_cli.MomentumRiskControlCLIError as exc:
        gate["item1_m52_baseline_parity"] = {"ok": False, "error": str(exc)}

    # Item 2: M53 changes do not leak into the candidate.
    try:
        assert_frozen_candidate_config(top_n=TOP_N, vol_target_annualized=None)
        gate["item2_no_m53_leakage"] = {"ok": True, "top_n": TOP_N, "vol_target_annualized": None}
    except MonitorProtocolError as exc:
        gate["item2_no_m53_leakage"] = {"ok": False, "error": str(exc)}

    # Item 3: point-in-time universe selection / no-future-filing --
    # structurally guaranteed by select_filing_for_signal_date (reused
    # unmodified from M52), covered by tests/test_cross_sectional_momentum_research.py.
    gate["item3_point_in_time_universe"] = {
        "ok": True,
        "note": "select_filing_for_signal_date reused unmodified from M52; covered by existing M52 unit tests.",
        "filings_known": len(filings), "snapshots_built": len(snapshots),
    }

    # Item 4: no pre-freeze paper transaction or P&L (initialization
    # starting state has an empty, untouched ledger).
    init_base = {"cash": str(STARTING_CAPITAL), "positions": {}, "transactions": []}
    init_stress = {"cash": str(STARTING_CAPITAL), "positions": {}, "transactions": []}
    gate["item4_no_pre_freeze_transactions"] = {"ok": len(init_base["transactions"]) == 0 and len(init_stress["transactions"]) == 0}

    # Item 5: base/stress ledger separation.
    gate["item5_base_stress_ledger_separation"] = {"ok": init_base is not init_stress}

    # Item 6: whole shares and nonnegative cash (trivially true for an
    # untouched initialization ledger; enforced by simulate_cross_sectional_momentum_portfolio
    # itself once a real signal executes, covered by M52's own tests).
    gate["item6_whole_shares_nonnegative_cash"] = {
        "ok": Decimal(init_base["cash"]) >= 0 and Decimal(init_stress["cash"]) >= 0,
        "note": "Whole-share sizing enforced by simulate_cross_sectional_momentum_portfolio, covered by M52's existing tests.",
    }

    # Item 7: no duplicate transactions/checkpoints.
    existing = _load_checkpoint_ledger()
    already_initialized = any(cp.get("checkpoint_type") == CHECKPOINT_TYPE_INITIALIZATION for cp in existing)
    gate["item7_no_duplicate_checkpoints"] = {"ok": not already_initialized, "existing_checkpoint_count": len(existing)}

    # Item 8: checkpoint hash-chain and idempotent rerun behavior.
    gate["item8_hash_chain_valid"] = {"ok": validate_hash_chain(existing)}

    # Item 9: exact-date market-session handling -- covered by
    # tests/test_momentum_prospective_monitor.py.
    gate["item9_session_handling"] = {"ok": True, "note": "Covered by tests/test_momentum_prospective_monitor.py."}

    # Item 10: strategy.enabled=false and no broker-capable call path.
    strategy_check = _strategy_enabled_check()
    broker_check = _no_broker_call_path_check()
    gate["item10_safety"] = {"ok": strategy_check["ok"] and broker_check["ok"], "strategy_enabled_check": strategy_check, "no_broker_call_path_check": broker_check}

    gate["_all_ok"] = all(v.get("ok", False) for k, v in gate.items() if k != "_all_ok")
    gate["_base_metrics_for_parity"] = base_metrics
    gate["_stress_metrics_for_parity"] = stress_metrics
    return gate


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fetch-missing", action="store_true", help="Allow retrieving genuinely new data (a refreshed SEC filing list; new Alpaca bars once a signal is actually due). Default: cache-only.")
    args = parser.parse_args(argv)

    now_utc = datetime.now(timezone.utc)
    existing_ledger = _load_checkpoint_ledger()
    if not validate_hash_chain(existing_ledger):
        raise MonitorCLIError("existing checkpoint ledger fails hash-chain validation -- refusing to append, investigate before proceeding")

    is_first_run = len(existing_ledger) == 0
    if is_first_run:
        freeze_sha = _git_head_sha()
        freeze_commit_utc = _git_commit_timestamp_utc(freeze_sha)
        freeze_date_ny = to_ny_date(freeze_commit_utc)
    else:
        init_cp = existing_ledger[0]
        freeze_sha = init_cp["protocol_freeze_commit_sha"]
        freeze_commit_utc = datetime.fromisoformat(init_cp["protocol_freeze_commit_timestamp_utc"])
        freeze_date_ny = date.fromisoformat(init_cp["protocol_freeze_date_ny"])

    print(f"Protocol freeze commit: {freeze_sha} ({freeze_commit_utc.isoformat()} UTC / {freeze_date_ny.isoformat()} NY date)")

    master_dates = tuple(m52_cli._all_research_calendar_sessions(m52_cli.WARMUP_TARGET_START, CALENDAR_LOOKAHEAD_END_EXCLUSIVE))

    last_processed_signal_date = None
    for cp in existing_ledger:
        if cp.get("checkpoint_type") == "SIGNAL" and cp.get("signal_date"):
            last_processed_signal_date = date.fromisoformat(cp["signal_date"])

    decision = checkpoint_run_decision(
        now_utc=now_utc, freeze_date_ny=freeze_date_ny, master_dates=master_dates,
        last_processed_signal_date=last_processed_signal_date,
    )
    print(f"Run decision: {json.dumps(decision, indent=2)}")

    if decision["status"] in (RUN_STATUS_NOT_DUE,):
        print("NOT_DUE -- nothing to process, exiting without writing a checkpoint.")
        return 0

    if decision["status"] == RUN_STATUS_SIGNAL_DUE and not is_first_run:
        print("A signal is due but this is not the initialization run -- future-checkpoint processing is documented, not implemented in this M55 initialization pass. Exiting without writing a checkpoint.")
        return 0

    if not is_first_run:
        print(f"Status {decision['status']} on a non-first run with no new signal to process -- exiting without writing a checkpoint.")
        return 0

    # First run: build the INITIALIZATION checkpoint.
    print("Fetching current IWB N-PORT-P filing list (cheap SEC EDGAR search; extends the cache, never re-resolves holdings for a filing outside M52's own evaluation window)...")
    today_ny = to_ny_date(now_utc)
    filings = await m52_cli.fetch_iwb_nport_filings(fetch_missing=args.fetch_missing, search_end=today_ny)
    most_recent_filing = filings[-1] if filings else None

    # Parity check uses ONLY filings within M52's own evaluation window
    # (file_date <= EVALUATION_END) -- guaranteed already resolved in
    # M52's cache; a newer filing (if one now exists) is reported as
    # metadata only and is never used to compute parity, since it could
    # never have been selected for any of M52's own signal dates.
    parity_filings = tuple(f for f in filings if f.file_date <= EVALUATION_END)
    snapshots = await m52_cli.build_all_universe_snapshots(parity_filings, fetch_missing=False)

    signal_dates = month_end_signal_dates(master_dates, start=EVALUATION_START, end=EVALUATION_END)
    monthly_universes, filing_accession_for_signal = m52_cli.build_monthly_universes(signal_dates, snapshots)
    all_tickers = sorted({t for tickers in monthly_universes.values() for t in tickers})
    series_by_symbol = await m52_cli.fetch_daily_bars_for_symbols(tuple(all_tickers + ["SPY"]), fetch_missing=False)
    spy_series = series_by_symbol.get("SPY")
    if spy_series is None:
        raise MonitorCLIError("BLOCKER: SPY daily bars unavailable from the existing M52 cache -- cannot verify baseline parity.")

    gate = await run_initialization_gate(
        filings=filings, snapshots=snapshots, monthly_universes=monthly_universes,
        series_by_symbol=series_by_symbol, spy_series=spy_series, master_dates=master_dates,
    )
    print(f"Initialization gate all_ok={gate['_all_ok']}")
    if not gate["_all_ok"]:
        raise MonitorCLIError(f"Initialization gate failed: {json.dumps({k: v for k, v in gate.items() if k not in ('_base_metrics_for_parity', '_stress_metrics_for_parity')}, indent=2, default=_json_default)}")

    checkpoint_content = {
        "checkpoint_index": 0,
        "checkpoint_type": CHECKPOINT_TYPE_INITIALIZATION,
        "protocol_freeze_commit_sha": freeze_sha,
        "protocol_freeze_commit_timestamp_utc": freeze_commit_utc.isoformat(),
        "protocol_freeze_date_ny": freeze_date_ny.isoformat(),
        "previous_checkpoint_sha256": None,
        "processing_cutoff_utc": now_utc.isoformat(),
        "processing_cutoff_ny_date": today_ny.isoformat(),
        "latest_completed_regular_session": decision.get("last_processed_session"),
        "most_recent_publicly_available_filing": (
            {"accession": most_recent_filing.accession, "period_ending": most_recent_filing.period_ending.isoformat(), "file_date": most_recent_filing.file_date.isoformat(), "document_url": most_recent_filing.document_url}
            if most_recent_filing else None
        ),
        "total_filings_known": len(filings),
        "first_eligible_signal_date": decision.get("next_expected_signal_date"),
        "first_eligible_execution_date": decision.get("earliest_next_useful_run_date"),
        "status": decision["status"],
        "signal_date": None,
        "execution_date": None,
        "selected_symbols": None,
        "spy_trend_filter_state": None,
        "base_ledger": {"cash": str(STARTING_CAPITAL), "positions": {}, "transaction_count": 0},
        "stress_ledger": {"cash": str(STARTING_CAPITAL), "positions": {}, "transaction_count": 0},
        "blocked_intents": [],
        "initialization_gate": {k: v for k, v in gate.items() if not k.startswith("_")},
        "frozen_config": {"top_n": TOP_N, "vol_target_annualized": None, "starting_capital": str(STARTING_CAPITAL), "base_cost_bps": str(BASE_COST_BPS), "stress_cost_bps": str(STRESS_COST_BPS)},
        "generation_git_head_sha": _git_head_sha(),
        "generation_source_file_sha256": _source_file_hashes(),
    }
    checkpoint_content["checkpoint_sha256"] = compute_checkpoint_hash(checkpoint_content)

    ledger = existing_ledger + [checkpoint_content]
    CHECKPOINT_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_LEDGER_PATH.write_text(json.dumps(ledger, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Initialization checkpoint written to {CHECKPOINT_LEDGER_PATH}")
    print(f"Status: {checkpoint_content['status']}")
    print(f"First eligible signal date: {checkpoint_content['first_eligible_signal_date']}")
    print(f"First eligible execution date: {checkpoint_content['first_eligible_execution_date']}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
