"""CLI entry point for M49: $30K practical-return core-satellite portfolio
feasibility (Issue #350).

Reuses (never refetches, never modifies):
- M48's own daily SIP bar cache for ARM_A/ARM_B (`fetch_all_symbols`,
  `cache_dir_for_feed` from `scripts.research_capital_target_strategy_family`).
- The frozen ORB baseline's minute-bar cache and period/universe/session
  helpers (from `scripts.research_orb_profit_protection_exit`), and the
  already-committed M43 checkpoints
  (`artifacts/research-profit-protection-exit/period-checkpoint/*.pkl`,
  read-only, never regenerated).

Every network call is cache-only by default (fails closed on a cache
miss); `--fetch-missing` is required to populate a missing cache entry
with real Alpaca credentials, exactly matching M48's own convention.

Before any combined-portfolio economics are read, this script:
1. Replays every frozen ORB period and verifies bit-for-bit parity
   against its own already-committed checkpoint.
2. Replays ARM_A/ARM_B standalone (through the SAME shared allocator,
   with the other two lanes disabled and no core-allocation cap) and
   verifies both an exact-object match against `run_arm_a`/
   `run_monthly_rebalance_arm` called directly AND a ledger-hash match
   against M48's own committed hashes document.
Any parity failure raises and halts before the combined run.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import pickle
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from src.config import load_config
from src.market_data_canonical import session_open_for
from src.orb_capital_target_strategy_family_research import (
    ARM_A,
    ARM_B,
    EVALUATION_END,
    EVALUATION_START,
    UNIVERSE_A,
    UNIVERSE_B,
    arm_b_selector,
    compute_arm_metrics,
    run_arm_a,
    run_monthly_rebalance_arm,
    simple_moving_average,
    TREND_SMA_PERIOD,
)
from src.orb_practical_core_satellite_portfolio_research import (
    ARM_ORB,
    BASE_COST_BPS,
    CORE_ALLOCATION_CAP_PCT,
    STARTING_CAPITAL,
    STRESS_COST_BPS,
    OrbTradeIntent,
    PracticalCoreSatelliteError,
    classify_practical_core_satellite_portfolio,
    compute_portfolio_metrics,
    portfolio_end_mark_transactions,
    replay_orb_baseline_intents,
    simulate_practical_core_satellite_portfolio,
    verify_orb_baseline_parity,
)

import scripts.research_capital_target_strategy_family as m48_cli
import scripts.research_orb_profit_protection_exit as ppe_cli

ORB_CHECKPOINT_DIR = ROOT / "artifacts" / "research-profit-protection-exit" / "period-checkpoint"
ORB_PERIODS: tuple[str, ...] = ("P_MINUS_2", "P_MINUS_1", "P0", "P1", "P2", "P3")
#: The frozen ORB baseline's minute-bar cache was originally fetched with
#: `feed=iex` (`src.orb_universe_expansion_research.DEFAULT_FEED`) -- this
#: is a DIFFERENT feed than M48's own SIP daily-bar cache, and is kept
#: exactly as originally frozen: changing it would invalidate the cache
#: key and either force a refetch or silently fail closed.
ORB_MINUTE_CACHE_DIR = ROOT / "artifacts" / "research-universe-expansion" / "bar-cache"
ORB_FEED = "iex"

RESULTS_DIR = ROOT / "artifacts" / "research-practical-core-satellite-portfolio" / "results"
PROVENANCE_PATH = RESULTS_DIR / "provenance_manifest.json"
COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "ORB_PRACTICAL_CORE_SATELLITE_PORTFOLIO_ARTIFACT_HASHES.json"
M48_COMMITTED_HASHES_PATH = ROOT / "docs" / "research" / "ORB_CAPITAL_TARGET_STRATEGY_FAMILY_ARTIFACT_HASHES.json"

CALENDAR_SEGMENTS = ("2022", "2023", "2024", "2025", "2026_YTD")


class PracticalCoreSatelliteCLIError(RuntimeError):
    pass


def _json_default(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _sha256_of_json(value: Any) -> str:
    material = json.dumps(value, sort_keys=True, default=_json_default)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# ORB baseline: replay every frozen period from the existing minute-bar
# cache, verifying bit-for-bit parity against the already-committed M43
# checkpoint for EACH period before returning anything.
# ---------------------------------------------------------------------------


async def _fetch_orb_period_bars(*, period: str, fetch_missing: bool) -> dict[str, Any]:
    session_dates = ppe_cli._session_dates_for(period)
    universe = ppe_cli._period_universe(period)
    market_bars_by_symbol = await ppe_cli._fetch_period_bars(
        period=period, session_dates=session_dates, cache_dir=ORB_MINUTE_CACHE_DIR,
        fetch_missing=fetch_missing, feed=ORB_FEED,
    )
    return {"session_dates": session_dates, "universe": universe, "market_bars_by_symbol": market_bars_by_symbol}


def _load_frozen_orb_checkpoint(period: str) -> dict[str, Any]:
    path = ORB_CHECKPOINT_DIR / f"{period}.pkl"
    if not path.exists():
        raise PracticalCoreSatelliteCLIError(
            f"BLOCKER: the frozen ORB baseline checkpoint for period {period} is missing at {path}. "
            "This is a required, already-committed M43 artifact -- it must never be regenerated or "
            "substituted; report this exact blocker rather than proceeding."
        )
    with open(path, "rb") as fh:
        return pickle.load(fh)


async def build_orb_intents(*, fetch_missing: bool) -> tuple[tuple[OrbTradeIntent, ...], dict[str, Any]]:
    """Every frozen ORB period, replayed with real timestamps and
    verified bit-for-bit against its own already-committed checkpoint.
    Raises `PracticalCoreSatelliteCLIError` (fail closed) on the first
    period whose parity check does not pass."""
    all_intents: list[OrbTradeIntent] = []
    parity_reports: dict[str, Any] = {}
    base_config = load_config()
    created_at = datetime.now(timezone.utc)
    for period in ORB_PERIODS:
        data = await _fetch_orb_period_bars(period=period, fetch_missing=fetch_missing)
        session_bounds_provider = ppe_cli._session_bounds_provider_for(period)
        session_open_resolver = None if session_bounds_provider is None else partial(session_open_for, session_bounds_provider=session_bounds_provider)
        intents = replay_orb_baseline_intents(
            period=period, universe=data["universe"], session_dates=data["session_dates"],
            market_bars_by_symbol=data["market_bars_by_symbol"], base_config=base_config, created_at=created_at,
            session_bounds_provider=session_bounds_provider, session_open_resolver=session_open_resolver,
        )
        frozen = _load_frozen_orb_checkpoint(period)
        frozen_rows = frozen["rows"]["WAITED_FIRST_VALID_0950"]["BE_AFTER_050R"]
        report = verify_orb_baseline_parity(period=period, replayed_intents=intents, frozen_rows=frozen_rows)
        parity_reports[period] = report
        if not report["ok"]:
            raise PracticalCoreSatelliteCLIError(
                f"BLOCKER: ORB standalone replay parity FAILED for period {period}: "
                f"{report['mismatch_count']} mismatches out of {len(intents)} rows. "
                f"First mismatches: {report['mismatches'][:5]}"
            )
        entered = sum(1 for i in intents if i.entered)
        print(f"[ORB {period}] parity OK: {len(intents)} candidates, {entered} entered, universe={data['universe']}")
        all_intents.extend(intents)
    return tuple(all_intents), parity_reports


# ---------------------------------------------------------------------------
# ARM_A / ARM_B standalone parity: the shared allocator, run with only one
# lane active and no core-allocation cap, must reproduce M48's own frozen
# engines EXACTLY -- both object-for-object AND by ledger-hash equality
# against M48's own committed hashes document.
# ---------------------------------------------------------------------------


def _load_m48_committed_hashes() -> dict[str, Any]:
    if not M48_COMMITTED_HASHES_PATH.exists():
        raise PracticalCoreSatelliteCLIError(f"BLOCKER: M48's committed hashes document is missing at {M48_COMMITTED_HASHES_PATH}")
    return json.loads(M48_COMMITTED_HASHES_PATH.read_text(encoding="utf-8"))


def verify_arm_a_standalone_parity(*, series_by_symbol_a: Mapping[str, Any], master_dates: Sequence[date]) -> dict[str, Any]:
    direct = run_arm_a(series_by_symbol_a, master_dates, evaluation_start=EVALUATION_START)
    via_allocator = simulate_practical_core_satellite_portfolio(
        series_by_symbol_a=series_by_symbol_a, series_by_symbol_b={}, master_dates=master_dates, orb_intents=(),
        evaluation_start=EVALUATION_START, include_orb=False, include_arm_a=True, include_arm_b=False,
    )
    a_trades = [t for t in via_allocator.closed_trades if t.lane == ARM_A]
    mismatches: list[Any] = []
    if len(direct.closed_trades) != len(a_trades):
        mismatches.append({"reason": f"closed-trade count mismatch: direct={len(direct.closed_trades)} allocator={len(a_trades)}"})
    else:
        for d, v in zip(direct.closed_trades, a_trades):
            if (d.symbol, d.entry_date, d.exit_date, d.quantity, d.entry_price, d.exit_price, d.net_pnl, d.exit_reason) != \
               (v.symbol, v.entry_date, v.exit_date, v.quantity, v.entry_price, v.exit_price, v.net_pnl, v.reason):
                mismatches.append({"symbol": v.symbol, "entry_date": v.entry_date.isoformat()})
    if dict(direct.daily_equity) != dict(via_allocator.daily_equity):
        mismatches.append({"reason": "daily_equity mismatch"})

    my_strategy_hash = m48_cli._strategy_transaction_ledger_hash(direct.transactions)
    my_position_hash = m48_cli._position_equity_ledger_hash(direct.daily_equity, direct.daily_position_count, direct.daily_position_snapshots)
    committed = _load_m48_committed_hashes()
    committed_entry = committed["ledger_hashes"]["ETF_PULLBACK_RSI2__base_5bps"]
    hash_match = (
        my_strategy_hash == committed_entry["strategy_transaction_ledger_sha256"]
        and my_position_hash == committed_entry["position_equity_ledger_sha256"]
    )
    if not hash_match:
        mismatches.append({
            "reason": "ledger-hash mismatch against M48's committed hashes document",
            "my_strategy_transaction_ledger_sha256": my_strategy_hash, "committed_strategy_transaction_ledger_sha256": committed_entry["strategy_transaction_ledger_sha256"],
            "my_position_equity_ledger_sha256": my_position_hash, "committed_position_equity_ledger_sha256": committed_entry["position_equity_ledger_sha256"],
        })
    return {"ok": not mismatches, "mismatches": mismatches, "closed_trade_count": len(direct.closed_trades), "ledger_hash_match": hash_match}


def verify_arm_b_standalone_parity(*, series_by_symbol_b: Mapping[str, Any], master_dates: Sequence[date]) -> dict[str, Any]:
    sma200_b = {s: simple_moving_average(series_by_symbol_b[s].closes, TREND_SMA_PERIOD) for s in series_by_symbol_b}
    selector_b = arm_b_selector(series_by_symbol_b, sma200_b)
    direct = run_monthly_rebalance_arm(series_by_symbol_b, master_dates, arm=ARM_B, selector=selector_b, evaluation_start=EVALUATION_START)
    via_allocator = simulate_practical_core_satellite_portfolio(
        series_by_symbol_a={}, series_by_symbol_b=series_by_symbol_b, master_dates=master_dates, orb_intents=(),
        evaluation_start=EVALUATION_START, include_orb=False, include_arm_a=False, include_arm_b=True,
        core_allocation_cap_pct=Decimal("1"),
    )
    b_trades = [t for t in via_allocator.closed_trades if t.lane == ARM_B]
    mismatches: list[Any] = []
    if len(direct.closed_trades) != len(b_trades):
        mismatches.append({"reason": f"closed-trade count mismatch: direct={len(direct.closed_trades)} allocator={len(b_trades)}"})
    else:
        for d, v in zip(direct.closed_trades, b_trades):
            # Reason labels legitimately differ (M48's REBALANCE_* vs
            # this module's CORE_*) -- every OTHER field must match exactly.
            if (d.symbol, d.entry_date, d.exit_date, d.quantity, d.entry_price, d.exit_price, d.net_pnl) != \
               (v.symbol, v.entry_date, v.exit_date, v.quantity, v.entry_price, v.exit_price, v.net_pnl):
                mismatches.append({"symbol": v.symbol, "entry_date": v.entry_date.isoformat()})
    if dict(direct.daily_equity) != dict(via_allocator.daily_equity):
        mismatches.append({"reason": "daily_equity mismatch"})

    my_strategy_hash = m48_cli._strategy_transaction_ledger_hash(direct.transactions)
    my_position_hash = m48_cli._position_equity_ledger_hash(direct.daily_equity, direct.daily_position_count, direct.daily_position_snapshots)
    committed = _load_m48_committed_hashes()
    committed_entry = committed["ledger_hashes"]["SECTOR_MOMENTUM_12_1__base_5bps"]
    hash_match = (
        my_strategy_hash == committed_entry["strategy_transaction_ledger_sha256"]
        and my_position_hash == committed_entry["position_equity_ledger_sha256"]
    )
    if not hash_match:
        mismatches.append({
            "reason": "ledger-hash mismatch against M48's committed hashes document",
            "my_strategy_transaction_ledger_sha256": my_strategy_hash, "committed_strategy_transaction_ledger_sha256": committed_entry["strategy_transaction_ledger_sha256"],
            "my_position_equity_ledger_sha256": my_position_hash, "committed_position_equity_ledger_sha256": committed_entry["position_equity_ledger_sha256"],
        })
    return {"ok": not mismatches, "mismatches": mismatches, "closed_trade_count": len(direct.closed_trades), "ledger_hash_match": hash_match}


SOURCE_FILES_FOR_HASHING = (
    ROOT / "src" / "orb_practical_core_satellite_portfolio_research.py",
    ROOT / "scripts" / "research_orb_practical_core_satellite_portfolio.py",
)


def _source_file_hashes() -> dict[str, str]:
    return {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in SOURCE_FILES_FOR_HASHING}


def _generation_working_tree_clean() -> bool | None:
    """`True` only if the two hashed source files are byte-identical to
    `HEAD` (Issue #348's own Finding 13 -- generalized here) -- so
    `generation_git_head_sha` genuinely identifies the implementation
    that generated this run. Scoped to exactly those two files, never
    the whole working tree: `study_results.json` and this hashes
    document are always rewritten by the current run before this is
    computed, so they always differ from whatever was last committed --
    the expected, intended effect of a rerun, not evidence the
    implementation itself was unclean."""
    import subprocess
    try:
        rel_paths = [str(p.relative_to(ROOT).as_posix()) for p in SOURCE_FILES_FOR_HASHING]
        out = subprocess.run(["git", "status", "--porcelain", "--"] + rel_paths, cwd=ROOT, capture_output=True, text=True, timeout=10, check=True)
        return out.stdout.strip() == ""
    except Exception:
        return None


def segment_label(day: date) -> str | None:
    if day.year in (2022, 2023, 2024, 2025):
        return str(day.year)
    if day.year == 2026:
        return "2026_YTD"
    return None


def run_full_study(
    *, series_by_symbol_a: Mapping[str, Any], series_by_symbol_b: Mapping[str, Any],
    master_dates: Sequence[date], orb_intents_in_window: Sequence[OrbTradeIntent],
) -> dict[str, Any]:
    """Runs the combined `PRACTICAL_CORE_SATELLITE_30K` portfolio at both
    cost cases, plus each lane's own standalone (uncontended, full-$30K)
    run at the SAME cost case for opportunity-cost attribution, computes
    pooled/segment metrics and the frozen classification, and returns the
    complete structured results."""
    all_symbols = list(UNIVERSE_A) + list(UNIVERSE_B)
    end_price_by_symbol = m48_cli.end_price_by_symbol_for({**series_by_symbol_a, **series_by_symbol_b}, all_symbols, end_date=EVALUATION_END)

    results: dict[str, Any] = {}
    cost_cases = (("base_5bps", BASE_COST_BPS), ("stress_10bps", STRESS_COST_BPS))
    combined_by_cost: dict[str, Any] = {}
    standalone_by_cost: dict[str, dict[str, Any]] = {}
    ledger_hashes: dict[str, Any] = {}

    for cost_label, cost_bps in cost_cases:
        combined = simulate_practical_core_satellite_portfolio(
            series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b, master_dates=master_dates,
            orb_intents=orb_intents_in_window, cost_bps=cost_bps, evaluation_start=EVALUATION_START,
            core_allocation_cap_pct=CORE_ALLOCATION_CAP_PCT, include_orb=True, include_arm_a=True, include_arm_b=True,
        )
        combined_by_cost[cost_label] = combined

        standalone_by_cost[cost_label] = {
            ARM_ORB: simulate_practical_core_satellite_portfolio(
                series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b, master_dates=master_dates,
                orb_intents=orb_intents_in_window, cost_bps=cost_bps, evaluation_start=EVALUATION_START,
                include_orb=True, include_arm_a=False, include_arm_b=False,
            ),
            ARM_A: simulate_practical_core_satellite_portfolio(
                series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b, master_dates=master_dates,
                orb_intents=(), cost_bps=cost_bps, evaluation_start=EVALUATION_START,
                include_orb=False, include_arm_a=True, include_arm_b=False,
            ),
            ARM_B: simulate_practical_core_satellite_portfolio(
                series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b, master_dates=master_dates,
                orb_intents=(), cost_bps=cost_bps, evaluation_start=EVALUATION_START,
                include_orb=False, include_arm_a=False, include_arm_b=True, core_allocation_cap_pct=Decimal("1"),
            ),
        }

        end_marks = portfolio_end_mark_transactions(
            tactical_positions_at_end=combined.open_tactical_positions_at_end,
            core_holdings_at_end=combined.open_core_holdings_at_end,
            end_date=EVALUATION_END, end_price_by_symbol=end_price_by_symbol, cost_bps=cost_bps,
        )

        ledger_hashes[cost_label] = {
            "transactions_sha256": _sha256_of_json([{
                "date": t.date.isoformat(), "lane": t.lane, "symbol": t.symbol, "side": t.side,
                "quantity": t.quantity, "price": str(t.price), "notional": str(t.notional), "cost": str(t.cost), "reason": t.reason,
            } for t in combined.transactions]),
            "endpoint_mark_ledger_sha256": _sha256_of_json([{
                "date": t.date.isoformat(), "lane": t.lane, "symbol": t.symbol, "side": t.side,
                "quantity": t.quantity, "price": str(t.price), "notional": str(t.notional), "cost": str(t.cost), "reason": t.reason,
            } for t in end_marks]),
            "position_snapshot_sha256": _sha256_of_json([{
                "date": s.date.isoformat(), "lane": s.lane, "symbol": s.symbol, "quantity": s.quantity,
                "mark_price": str(s.mark_price), "market_value": str(s.market_value),
            } for s in combined.daily_position_snapshots]),
            "blocked_intent_ledger_sha256": _sha256_of_json([{
                "date": b.date.isoformat(), "lane": b.lane, "symbol": b.symbol, "side": b.side,
                "desired_quantity": b.desired_quantity, "filled_quantity": b.filled_quantity, "reason": b.reason,
            } for b in combined.blocked_intents]),
            "transaction_count": len(combined.transactions), "endpoint_mark_count": len(end_marks),
            "blocked_intent_count": len(combined.blocked_intents),
        }

        base_metrics = compute_portfolio_metrics(
            result=combined, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END, end_mark_transactions=end_marks,
        )

        segment_metrics: dict[str, Any] = {}
        for seg in CALENDAR_SEGMENTS:
            seg_dates = [d for d in master_dates if segment_label(d) == seg and EVALUATION_START <= d <= EVALUATION_END]
            if not seg_dates:
                continue
            seg_end_marks = end_marks if seg_dates[-1] == EVALUATION_END else ()
            segment_metrics[seg] = compute_portfolio_metrics(
                result=combined, evaluation_start=seg_dates[0], evaluation_end=seg_dates[-1], end_mark_transactions=seg_end_marks,
            )

        opportunity_cost: dict[str, Any] = {}
        for lane in (ARM_ORB, ARM_A, ARM_B):
            standalone_result = standalone_by_cost[cost_label][lane]
            standalone_end_marks = portfolio_end_mark_transactions(
                tactical_positions_at_end=standalone_result.open_tactical_positions_at_end,
                core_holdings_at_end=standalone_result.open_core_holdings_at_end,
                end_date=EVALUATION_END, end_price_by_symbol=end_price_by_symbol, cost_bps=cost_bps,
            )
            standalone_metrics = compute_portfolio_metrics(
                result=standalone_result, evaluation_start=EVALUATION_START, evaluation_end=EVALUATION_END,
                end_mark_transactions=standalone_end_marks,
            )
            shared_lane_net = base_metrics["sleeve_concentration"]["positive_contributors"].get(lane)
            # `positive_contributors` only carries POSITIVE contributions
            # (by construction of `positive_contribution_concentration`);
            # recompute the lane's own full net (positive or negative)
            # directly from the ledgers for an honest opportunity-cost
            # delta, not just its positive share.
            lane_trades = [t for t in combined.closed_trades if t.lane == lane and EVALUATION_START <= t.exit_date <= EVALUATION_END]
            lane_realized_net = sum((t.net_pnl for t in lane_trades), Decimal(0))
            standalone_lane_trades = [t for t in standalone_result.closed_trades if t.lane == lane and EVALUATION_START <= t.exit_date <= EVALUATION_END]
            standalone_lane_realized_net = sum((t.net_pnl for t in standalone_lane_trades), Decimal(0))
            opportunity_cost[lane] = {
                "standalone_net_pnl_marked": standalone_metrics["net_pnl_marked"],
                "standalone_mean_monthly_dollar_pnl_fixed_30k": standalone_metrics["mean_monthly_dollar_pnl_fixed_30k"],
                "shared_realized_net_pnl": lane_realized_net,
                "standalone_realized_net_pnl": standalone_lane_realized_net,
                "shared_minus_standalone_realized_net_pnl": lane_realized_net - standalone_lane_realized_net,
                "shared_blocked_intent_count": sum(1 for b in combined.blocked_intents if b.lane == lane and EVALUATION_START <= b.date <= EVALUATION_END),
            }

        results[cost_label] = {
            "pooled": base_metrics, "segments": segment_metrics, "opportunity_cost": opportunity_cost,
            "closed_lot_count": len(combined.closed_trades),
            "core_allocation_pct_series": [[d.isoformat(), str(p)] for d, p in combined.daily_core_allocation_pct],
            "tactical_position_count_series": [[d.isoformat(), c] for d, c in combined.daily_tactical_position_count],
        }

    correlation_to_spy = None
    correlation_to_orb = None
    try:
        spy_series = series_by_symbol_a.get("SPY") or series_by_symbol_b.get("SPY")
        if spy_series is not None:
            spy_bh = m48_cli.compute_spy_buy_and_hold(spy_series, master_dates)
            portfolio_monthly = results["base_5bps"]["pooled"]["monthly_return_pct_series"]
            spy_monthly_pct_pairs = m48_cli.monthly_return_pct([(d, spy_series.closes[i]) for i, d in enumerate(master_dates)])
            spy_monthly_pct_pairs = [(d, v) for d, v in spy_monthly_pct_pairs if EVALUATION_START <= d <= EVALUATION_END]
            aligned_spy = m48_cli.align_monthly_series(portfolio_monthly, spy_monthly_pct_pairs)
            correlation_to_spy = m48_cli.pearson_correlation([r[1] for r in aligned_spy], [r[2] for r in aligned_spy]) if len(aligned_spy) >= 2 else None
        orb_monthly = m48_cli.load_orb_monthly_r_series()
        portfolio_monthly = results["base_5bps"]["pooled"]["monthly_return_pct_series"]
        aligned_orb = m48_cli.align_monthly_series(portfolio_monthly, orb_monthly)
        correlation_to_orb = m48_cli.pearson_correlation([r[1] for r in aligned_orb], [r[2] for r in aligned_orb]) if len(aligned_orb) >= 2 else None
    except Exception as exc:  # pragma: no cover -- diagnostic only, never gates classification
        print(f"WARNING: correlation computation failed: {exc}")

    base_pooled = results["base_5bps"]["pooled"]
    stress_pooled = results["stress_10bps"]["pooled"]
    best_share = base_pooled["concentration"]["best_contribution_share"]
    sleeve_share = base_pooled["sleeve_concentration"]["best_contribution_share"]
    worst_combined_share = max(v for v in (best_share, sleeve_share) if v is not None) if (best_share is not None or sleeve_share is not None) else None
    classification = classify_practical_core_satellite_portfolio(
        mean_monthly_dollars_base=Decimal(str(base_pooled["mean_monthly_dollar_pnl_fixed_30k"] or 0)),
        segment_net_returns={seg: v["net_pnl_marked"] for seg, v in results["base_5bps"]["segments"].items()},
        max_drawdown_pct=base_pooled["max_drawdown_pct"],
        pooled_net_base=base_pooled["net_pnl_marked"], pooled_net_stress=stress_pooled["net_pnl_marked"],
        profitable_month_pct=base_pooled["profitable_month_pct"], worst_month_pct=base_pooled["fifth_percentile_month_pct"],
        best_single_contributor_positive_share=worst_combined_share, integrity_ok=True,
    )

    results["classification"] = classification
    results["correlation_to_spy_buy_and_hold"] = correlation_to_spy
    results["correlation_to_orb_baseline"] = correlation_to_orb
    results["ledger_hashes"] = ledger_hashes
    return results


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fetch-missing", action="store_true", default=False)
    parser.add_argument("--feed", default="sip", help="Feed for ARM_A/ARM_B daily bars (reuses M48's own cache).")
    parser.add_argument("--parity-only", action="store_true", default=False, help="Run every integrity/standalone-parity gate and write the provenance manifest, but do not read combined-portfolio economics.")
    args = parser.parse_args(argv)

    # --- ARM_A / ARM_B daily SIP bars: reuse M48's own cache, unchanged. ---
    cache_dir = m48_cli.cache_dir_for_feed(args.feed)
    series_by_symbol = await m48_cli.fetch_all_symbols(cache_dir=cache_dir, fetch_missing=args.fetch_missing, feed=args.feed)
    master_dates = m48_cli.build_master_calendar(series_by_symbol["SPY"])
    series_by_symbol_a = {s: series_by_symbol[s] for s in UNIVERSE_A}
    series_by_symbol_b = {s: series_by_symbol[s] for s in UNIVERSE_B}

    # --- ORB baseline: replay + per-period parity gate. ---
    orb_intents, orb_parity_reports = await build_orb_intents(fetch_missing=args.fetch_missing)
    orb_intents_in_window = tuple(i for i in orb_intents if EVALUATION_START <= i.session_date <= EVALUATION_END)
    print(f"ORB: {len(orb_intents)} total replayed candidates across {len(ORB_PERIODS)} periods, {len(orb_intents_in_window)} within the evaluation window.")

    # --- Standalone parity gates for ARM_A / ARM_B. ---
    arm_a_parity = verify_arm_a_standalone_parity(series_by_symbol_a=series_by_symbol_a, master_dates=master_dates)
    if not arm_a_parity["ok"]:
        raise PracticalCoreSatelliteCLIError(f"BLOCKER: ARM_A standalone parity FAILED: {arm_a_parity['mismatches'][:5]}")
    print(f"[ARM_A] standalone parity OK: {arm_a_parity['closed_trade_count']} closed trades, ledger hashes match M48's committed document.")

    arm_b_parity = verify_arm_b_standalone_parity(series_by_symbol_b=series_by_symbol_b, master_dates=master_dates)
    if not arm_b_parity["ok"]:
        raise PracticalCoreSatelliteCLIError(f"BLOCKER: ARM_B standalone parity FAILED: {arm_b_parity['mismatches'][:5]}")
    print(f"[ARM_B] standalone parity OK: {arm_b_parity['closed_trade_count']} closed trades, ledger hashes match M48's committed document.")

    print("All standalone-parity and integrity gates passed. Proceeding to the combined portfolio.")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    provenance = {
        "retrieval_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "feed": args.feed, "orb_feed": ORB_FEED, "orb_periods": list(ORB_PERIODS),
        "evaluation_start": EVALUATION_START.isoformat(), "evaluation_end": EVALUATION_END.isoformat(),
        "orb_parity_reports": {p: {"ok": r["ok"], "matched_count": r["matched_count"], "mismatch_count": r["mismatch_count"]} for p, r in orb_parity_reports.items()},
        "arm_a_standalone_parity": {"ok": arm_a_parity["ok"], "closed_trade_count": arm_a_parity["closed_trade_count"], "ledger_hash_match": arm_a_parity["ledger_hash_match"]},
        "arm_b_standalone_parity": {"ok": arm_b_parity["ok"], "closed_trade_count": arm_b_parity["closed_trade_count"], "ledger_hash_match": arm_b_parity["ledger_hash_match"]},
    }
    PROVENANCE_PATH.write_text(json.dumps(provenance, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Provenance manifest written to {PROVENANCE_PATH}")

    if args.parity_only:
        print("--parity-only: stopping before combined-portfolio economics are read.")
        return 0

    results = run_full_study(
        series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b,
        master_dates=master_dates, orb_intents_in_window=orb_intents_in_window,
    )
    results_path = RESULTS_DIR / "study_results.json"
    results_path.write_text(json.dumps(results, indent=2, sort_keys=True, default=_json_default), encoding="utf-8")
    print(f"Study results written to {results_path}")
    print(f"Classification: {results['classification']['verdict']}")
    print(f"Mean monthly $ (fixed $30K, base cost): {results['base_5bps']['pooled']['mean_monthly_dollar_pnl_fixed_30k']}")
    print(f"Max drawdown: {results['base_5bps']['pooled']['max_drawdown_pct']}")

    committed_hashes = {
        "feed": args.feed, "orb_feed": ORB_FEED, "evaluation_start": EVALUATION_START.isoformat(), "evaluation_end": EVALUATION_END.isoformat(),
        "orb_periods": list(ORB_PERIODS),
        "orb_parity_reports": provenance["orb_parity_reports"],
        "arm_a_standalone_parity": provenance["arm_a_standalone_parity"],
        "arm_b_standalone_parity": provenance["arm_b_standalone_parity"],
        "ledger_hashes": results["ledger_hashes"],
        "classification": results["classification"]["verdict"],
        "study_results_json_sha256": hashlib.sha256(results_path.read_bytes()).hexdigest(),
        "generation_git_head_sha": m48_cli._git_head_sha(),
        "generation_working_tree_clean": _generation_working_tree_clean(),
        "generation_source_file_sha256": _source_file_hashes(),
        "retrieval_timestamp_utc": provenance["retrieval_timestamp_utc"],
    }
    COMMITTED_HASHES_PATH.parent.mkdir(parents=True, exist_ok=True)
    COMMITTED_HASHES_PATH.write_text(json.dumps(committed_hashes, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Committed artifact-hashes document written to {COMMITTED_HASHES_PATH}")
    print("Empirical study stage complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
