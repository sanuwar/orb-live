"""Focused tests for the M49 CLI (Issue #350): the shared-allocator
combined study, provenance/hash helpers, and segment/date bookkeeping.
Uses synthetic DailySeries fixtures for ARM_A/ARM_B (mirroring M48's own
CLI test convention) -- ORB replay/parity against the real minute-bar
cache and frozen checkpoints is verified separately, as part of the
required empirical-generation sequencing, not in this fast unit-test
file."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

import scripts.research_capital_target_strategy_family as m48_cli
import scripts.research_orb_practical_core_satellite_portfolio as cli
from src.orb_capital_target_strategy_family_research import DailySeries, UNIVERSE_A, UNIVERSE_B
from src.orb_practical_core_satellite_portfolio_research import ARM_A, ARM_B, ARM_ORB, OrbTradeIntent


def _business_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    d = start
    while len(days) < count:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _series_with_post_end_bar(symbol: str, dates: list[date], closes: list[Decimal]) -> DailySeries:
    opens = closes
    highs = [c + Decimal("0.1") for c in closes]
    lows = [c - Decimal("0.1") for c in closes]
    return DailySeries(symbol=symbol, dates=tuple(dates), opens=tuple(opens), highs=tuple(highs), lows=tuple(lows), closes=tuple(closes), volumes=tuple([1000] * len(dates)))


def _full_synthetic_series_by_symbol() -> tuple[dict[str, DailySeries], dict[str, DailySeries], list[date]]:
    all_dates = _business_days(m48_cli.WARMUP_START, 1500)
    dates = [d for d in all_dates if d <= m48_cli.EVALUATION_END]
    post_end_date = next(d for d in all_dates if d > m48_cli.EVALUATION_END)
    dates_with_tail = dates + [post_end_date]

    symbols = list(UNIVERSE_A) + list(UNIVERSE_B)
    series_by_symbol: dict[str, DailySeries] = {}
    for idx, symbol in enumerate(symbols):
        base = Decimal(50 + idx * 3)
        closes = [base + Decimal("0.05") * Decimal(i) for i in range(len(dates_with_tail))]
        series_by_symbol[symbol] = _series_with_post_end_bar(symbol, dates_with_tail, closes)
    series_by_symbol_a = {s: series_by_symbol[s] for s in UNIVERSE_A}
    series_by_symbol_b = {s: series_by_symbol[s] for s in UNIVERSE_B}
    return series_by_symbol_a, series_by_symbol_b, dates


def test_segment_label_boundaries():
    assert cli.segment_label(date(2021, 12, 31)) is None
    assert cli.segment_label(date(2022, 1, 3)) == "2022"
    assert cli.segment_label(date(2025, 12, 31)) == "2025"
    assert cli.segment_label(date(2026, 1, 2)) == "2026_YTD"
    assert cli.segment_label(date(2027, 1, 1)) is None


def test_sha256_of_json_is_deterministic_and_order_independent():
    a = cli._sha256_of_json({"b": 2, "a": 1})
    b = cli._sha256_of_json({"a": 1, "b": 2})
    assert a == b
    c = cli._sha256_of_json({"a": 1, "b": 3})
    assert a != c


def test_source_file_hashes_match_actual_files_on_disk():
    import hashlib
    hashes = cli._source_file_hashes()
    assert set(hashes) == {
        "src/orb_practical_core_satellite_portfolio_research.py",
        "scripts/research_orb_practical_core_satellite_portfolio.py",
    }
    for rel_path, sha in hashes.items():
        assert sha == hashlib.sha256((cli.ROOT / rel_path).read_bytes()).hexdigest()


def test_generation_working_tree_clean_is_scoped_to_source_files():
    import subprocess
    rel_paths = [str(p.relative_to(cli.ROOT).as_posix()) for p in cli.SOURCE_FILES_FOR_HASHING]
    status = subprocess.run(["git", "status", "--porcelain", "--"] + rel_paths, cwd=cli.ROOT, capture_output=True, text=True, timeout=10, check=True)
    expected = status.stdout.strip() == ""
    assert cli._generation_working_tree_clean() == expected


def test_run_full_study_synthetic_orb_disabled_produces_complete_structure():
    series_by_symbol_a, series_by_symbol_b, dates = _full_synthetic_series_by_symbol()
    results = cli.run_full_study(
        series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b,
        master_dates=tuple(dates), orb_intents_in_window=(),
    )
    for cost_label in ("base_5bps", "stress_10bps"):
        assert cost_label in results
        assert "pooled" in results[cost_label]
        assert set(results[cost_label]["segments"].keys()) <= set(cli.CALENDAR_SEGMENTS)
        assert "opportunity_cost" in results[cost_label]
        for lane in (ARM_ORB, ARM_A, ARM_B):
            assert lane in results[cost_label]["opportunity_cost"]
    assert results["classification"]["verdict"] in (
        "PRACTICAL_TARGET_FEASIBLE", "SAFE_BUT_BELOW_TARGET", "POSITIVE_BUT_INSUFFICIENT", "NOT_PRACTICAL",
    )
    assert "ledger_hashes" in results
    for cost_label in ("base_5bps", "stress_10bps"):
        assert cost_label in results["ledger_hashes"]


def test_run_full_study_with_a_synthetic_orb_intent_included():
    series_by_symbol_a, series_by_symbol_b, dates = _full_synthetic_series_by_symbol()
    # An ORB symbol disjoint from ARM_A/ARM_B's own universes, entering
    # and exiting same-day, well inside the evaluation window.
    orb_day = next(d for d in dates if d >= m48_cli.EVALUATION_START and d.weekday() == 1)
    intent = OrbTradeIntent(
        period="P0", symbol="ZORB", session_date=orb_day, entered=True, closed=True,
        entry_timestamp=datetime.combine(orb_day, datetime.min.time(), tzinfo=timezone.utc),
        entry_price=Decimal("100"), initial_stop_price=Decimal("98"), target_price=Decimal("104"),
        exit_timestamp=datetime.combine(orb_day, datetime.min.time(), tzinfo=timezone.utc),
        exit_price=Decimal("102"), exit_reason="profit_target", r_multiple=1.0,
    )
    results = cli.run_full_study(
        series_by_symbol_a=series_by_symbol_a, series_by_symbol_b=series_by_symbol_b,
        master_dates=tuple(dates), orb_intents_in_window=(intent,),
    )
    assert results["base_5bps"]["opportunity_cost"][ARM_ORB]["shared_realized_net_pnl"] != Decimal(0)


def test_verify_arm_a_and_arm_b_standalone_parity_against_real_m48_cache():
    # This exercises the real M48 SIP cache and the real committed
    # hashes document (both gitignored/committed artifacts already
    # present from M48) -- a genuine end-to-end parity proof, not a
    # synthetic approximation. Skips cleanly if the cache or the
    # committed hashes document is not present in this environment.
    import asyncio
    if not cli.M48_COMMITTED_HASHES_PATH.exists():
        pytest.skip("M48 committed hashes document not present")
    cache_dir = m48_cli.cache_dir_for_feed("sip")
    if not cache_dir.exists():
        pytest.skip("M48 SIP daily-bar cache not present")

    async def _run():
        series_by_symbol = await m48_cli.fetch_all_symbols(cache_dir=cache_dir, fetch_missing=False, feed="sip")
        master_dates = m48_cli.build_master_calendar(series_by_symbol["SPY"])
        series_by_symbol_a = {s: series_by_symbol[s] for s in UNIVERSE_A}
        series_by_symbol_b = {s: series_by_symbol[s] for s in UNIVERSE_B}
        return (
            cli.verify_arm_a_standalone_parity(series_by_symbol_a=series_by_symbol_a, master_dates=master_dates),
            cli.verify_arm_b_standalone_parity(series_by_symbol_b=series_by_symbol_b, master_dates=master_dates),
        )

    arm_a_parity, arm_b_parity = asyncio.run(_run())
    assert arm_a_parity["ok"], arm_a_parity["mismatches"]
    assert arm_a_parity["ledger_hash_match"] is True
    assert arm_b_parity["ok"], arm_b_parity["mismatches"]
    assert arm_b_parity["ledger_hash_match"] is True
