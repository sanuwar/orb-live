"""Focused tests for the M48 CLI's exact-date endpoint resolution
(Issue #348, Finding 11 -- independent review) and ledger/provenance
completeness (Finding 12 -- independent review)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest

import scripts.research_capital_target_strategy_family as cli
from src.orb_capital_target_strategy_family_research import DailySeries, PositionSnapshot, Transaction


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


def test_close_on_exact_date_returns_correct_close_not_positional_last():
    dates = _business_days(date(2026, 8, 10), 5)  # includes 2026-08-13 and 2026-08-14
    closes = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103"), Decimal("999")]
    series = _series_with_post_end_bar("AAA", dates, closes)
    assert dates[-2] == date(2026, 8, 13)
    assert dates[-1] == date(2026, 8, 14)
    result = cli.close_on_exact_date(series, date(2026, 8, 13))
    assert result == Decimal("103")  # NOT closes[-1] (999, the 8/14 bar)


def test_close_on_exact_date_fails_closed_on_missing_date():
    dates = _business_days(date(2026, 8, 10), 3)
    closes = [Decimal("100"), Decimal("101"), Decimal("102")]
    series = _series_with_post_end_bar("AAA", dates, closes)
    with pytest.raises(cli.CapitalTargetCLIError):
        # a date not present at all
        cli.close_on_exact_date(series, date(2099, 1, 1))


def test_end_price_by_symbol_for_uses_exact_date_for_every_symbol():
    dates = _business_days(date(2026, 8, 10), 5)
    aaa = _series_with_post_end_bar("AAA", dates, [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103"), Decimal("999")])
    bbb = _series_with_post_end_bar("BBB", dates, [Decimal("50"), Decimal("51"), Decimal("52"), Decimal("53"), Decimal("111")])
    series_by_symbol = {"AAA": aaa, "BBB": bbb}
    result = cli.end_price_by_symbol_for(series_by_symbol, ["AAA", "BBB"], end_date=date(2026, 8, 13))
    assert result == {"AAA": Decimal("103"), "BBB": Decimal("53")}


def test_post_end_bar_radically_changed_does_not_affect_endpoint_mark():
    dates = _business_days(date(2026, 8, 10), 5)
    base_closes = [Decimal("100"), Decimal("101"), Decimal("102"), Decimal("103")]
    series_original = _series_with_post_end_bar("AAA", dates, base_closes + [Decimal("999")])
    series_altered = _series_with_post_end_bar("AAA", dates, base_closes + [Decimal("1")])  # radically different 8/14 close
    mark_original = cli.close_on_exact_date(series_original, date(2026, 8, 13))
    mark_altered = cli.close_on_exact_date(series_altered, date(2026, 8, 13))
    assert mark_original == mark_altered == Decimal("103")


def _full_synthetic_series_by_symbol(*, last_close_override: dict[str, Decimal] | None = None) -> tuple[dict[str, DailySeries], list[date]]:
    """A complete synthetic dataset spanning `cli.WARMUP_START` through
    one session past `cli.EVALUATION_END` (mirroring the real cache's
    own one-session overshoot), for every symbol in `cli.ALL_SYMBOLS`.
    `last_close_override`, if given, replaces only the very last
    (post-end) session's close for the named symbols -- used to prove
    that session can never affect any reported result."""
    all_dates = _business_days(cli.WARMUP_START, 1500)
    dates = [d for d in all_dates if d <= cli.EVALUATION_END]
    post_end_date = next(d for d in all_dates if d > cli.EVALUATION_END)
    dates_with_tail = dates + [post_end_date]

    series_by_symbol: dict[str, DailySeries] = {}
    for idx, symbol in enumerate(cli.ALL_SYMBOLS):
        base = Decimal(50 + idx * 3)
        closes = [base + Decimal("0.05") * Decimal(i) for i in range(len(dates_with_tail))]
        if last_close_override and symbol in last_close_override:
            closes[-1] = last_close_override[symbol]
        series_by_symbol[symbol] = _series_with_post_end_bar(symbol, dates_with_tail, closes)
    return series_by_symbol, dates


def test_full_study_post_end_bar_invariance():
    # A radically altered (or wildly different) 2026-08-14-equivalent
    # bar must not change a single reported metric, classification, or
    # correlation -- proving the fixed-endpoint mark genuinely never
    # reads past EVALUATION_END anywhere in the full pipeline.
    series_original, dates = _full_synthetic_series_by_symbol()
    override = {s: Decimal("1") for s in cli.ALL_SYMBOLS}  # drastically different post-end closes
    series_altered, _ = _full_synthetic_series_by_symbol(last_close_override=override)

    master_dates = tuple(d for d in dates if cli.WARMUP_START <= d <= cli.EVALUATION_END)
    results_original = cli.run_full_study(series_original, master_dates)
    results_altered = cli.run_full_study(series_altered, master_dates)

    for arm in cli.ARMS:
        base_o = results_original["arms"][arm]["base_cost"]
        base_a = results_altered["arms"][arm]["base_cost"]
        assert base_o["net_pnl_marked"] == base_a["net_pnl_marked"]
        assert base_o["net_pnl"] == base_a["net_pnl"]
        assert base_o["concentration"] == base_a["concentration"]
        assert base_o["turnover"] == base_a["turnover"]
        assert results_original["arms"][arm]["classification"]["verdict"] == results_altered["arms"][arm]["classification"]["verdict"]
    assert results_original["ledger_hashes"] == results_altered["ledger_hashes"]
    assert results_original["master_calendar_sha256"] == results_altered["master_calendar_sha256"]


def test_position_equity_ledger_hash_differs_for_same_equity_and_count_but_different_symbols_or_quantities():
    # Two portfolios can share identical aggregate equity and identical
    # open-position COUNT while holding entirely different underlying
    # positions -- the ledger hash must still distinguish them (Finding
    # 12: an aggregate-only hash cannot reconstruct or verify the real
    # ledger).
    d = date(2026, 1, 5)
    daily_equity = [(d, Decimal("30000"))]
    daily_position_count = [(d, 2)]

    snapshots_a = (
        PositionSnapshot(date=d, symbol="AAA", quantity=100, mark_price=Decimal("150"), market_value=Decimal("15000"), planned_risk=None),
        PositionSnapshot(date=d, symbol="BBB", quantity=100, mark_price=Decimal("150"), market_value=Decimal("15000"), planned_risk=None),
    )
    # Same date, same aggregate equity, same count -- different symbol
    # and quantity mix entirely.
    snapshots_b = (
        PositionSnapshot(date=d, symbol="CCC", quantity=50, mark_price=Decimal("300"), market_value=Decimal("15000"), planned_risk=None),
        PositionSnapshot(date=d, symbol="DDD", quantity=200, mark_price=Decimal("75"), market_value=Decimal("15000"), planned_risk=None),
    )

    hash_a = cli._position_equity_ledger_hash(daily_equity, daily_position_count, snapshots_a)
    hash_b = cli._position_equity_ledger_hash(daily_equity, daily_position_count, snapshots_b)
    assert hash_a != hash_b

    # Identical inputs must still hash identically (determinism check).
    hash_a_again = cli._position_equity_ledger_hash(daily_equity, daily_position_count, snapshots_a)
    assert hash_a == hash_a_again

    # A quantity-only change (same symbol, same date, same equity/count)
    # must also change the hash.
    snapshots_a_diff_qty = (
        PositionSnapshot(date=d, symbol="AAA", quantity=101, mark_price=Decimal("150"), market_value=Decimal("15150"), planned_risk=None),
        PositionSnapshot(date=d, symbol="BBB", quantity=100, mark_price=Decimal("150"), market_value=Decimal("15000"), planned_risk=None),
    )
    hash_a_diff_qty = cli._position_equity_ledger_hash(daily_equity, daily_position_count, snapshots_a_diff_qty)
    assert hash_a_diff_qty != hash_a


def test_endpoint_mark_ledger_hash_changes_with_quantity_or_price():
    # `_endpoint_mark_ledger_hash` must be sensitive to the hypothetical
    # EVALUATION_END_MARK rows themselves -- a changed endpoint quantity
    # or price must change the hash (Finding 12).
    base = Transaction(
        date=date(2026, 8, 13), arm="ARM_A", symbol="AAA", side="SELL",
        quantity=100, price=Decimal("150.00"), notional=Decimal("15000.00"),
        cost=Decimal("7.50"), reason="EVALUATION_END_MARK",
    )
    hash_base = cli._endpoint_mark_ledger_hash((base,))

    changed_qty = Transaction(
        date=base.date, arm=base.arm, symbol=base.symbol, side=base.side,
        quantity=101, price=base.price, notional=Decimal("15150.00"),
        cost=base.cost, reason=base.reason,
    )
    hash_changed_qty = cli._endpoint_mark_ledger_hash((changed_qty,))
    assert hash_changed_qty != hash_base

    changed_price = Transaction(
        date=base.date, arm=base.arm, symbol=base.symbol, side=base.side,
        quantity=base.quantity, price=Decimal("151.00"), notional=Decimal("15100.00"),
        cost=base.cost, reason=base.reason,
    )
    hash_changed_price = cli._endpoint_mark_ledger_hash((changed_price,))
    assert hash_changed_price != hash_base

    # Determinism: identical input hashes identically.
    assert cli._endpoint_mark_ledger_hash((base,)) == hash_base

    # `_combined_accounting_ledger_hash` must also reflect an endpoint-
    # mark-only change even when the real transaction list is empty and
    # unchanged, and must differ from the strategy-only hash when
    # end-mark rows are non-empty.
    combined_base = cli._combined_accounting_ledger_hash((), (base,))
    combined_changed = cli._combined_accounting_ledger_hash((), (changed_qty,))
    assert combined_base != combined_changed
    assert combined_base != cli._strategy_transaction_ledger_hash(())


def test_build_committed_hashes_document_includes_source_hashes_and_clean_tree_flag(tmp_path):
    # Finding 13: the committed hashes document must record SHA-256
    # hashes of the exact research module and orchestration script that
    # generated the run, and an explicit `generation_working_tree_clean`
    # flag -- so `generation_git_head_sha` is never misread as
    # identifying the generating code when the working tree was dirty.
    results_path = tmp_path / "study_results.json"
    results_path.write_text("{}", encoding="utf-8")
    manifest = {
        "master_calendar_session_count": 3,
        "per_symbol": {"AAA": {"sha256": "deadbeef"}},
        "retrieval_timestamp_utc": "2026-08-13T00:00:00Z",
    }
    results = {"master_calendar_sha256": "cafef00d", "ledger_hashes": {}}

    doc = cli.build_committed_hashes_document(manifest=manifest, results=results, results_path=results_path, feed="sip")

    assert "generation_source_file_sha256" in doc
    expected_hashes = cli._source_file_hashes()
    assert doc["generation_source_file_sha256"] == expected_hashes
    assert set(expected_hashes) == {
        "src/orb_capital_target_strategy_family_research.py",
        "scripts/research_capital_target_strategy_family.py",
    }
    # Every recorded hash must equal the real, current on-disk file hash
    # (not a stale or placeholder value).
    for rel_path, sha in expected_hashes.items():
        assert sha == __import__("hashlib").sha256((cli.ROOT / rel_path).read_bytes()).hexdigest()

    assert "generation_working_tree_clean" in doc
    assert doc["generation_working_tree_clean"] in (True, False, None)


def test_full_study_arm_a_risk_views_never_fabricate_a_fixed_notional_figure():
    # Finding 14: ARM_A's $150/$300 risk views must either report the
    # primary fixed-$30K marked view too, or explicitly explain what
    # can't be validly derived without a rerun -- never silently omit
    # both.
    series, dates = _full_synthetic_series_by_symbol()
    master_dates = tuple(d for d in dates if cli.WARMUP_START <= d <= cli.EVALUATION_END)
    results = cli.run_full_study(series, master_dates)
    risk_views = results["arms"][cli.ARM_A]["risk_views"]
    assert set(risk_views.keys()) == {"300", "150"}
    for label, view in risk_views.items():
        assert "gross_pnl" in view and "net_pnl" in view
        assert view["fixed_notional_30k_monthly_dollar_pnl_available"] is False
        assert "note" in view and len(view["note"]) > 0


def test_full_study_reports_finding_14_counts_and_volatility_for_every_arm():
    series, dates = _full_synthetic_series_by_symbol()
    master_dates = tuple(d for d in dates if cli.WARMUP_START <= d <= cli.EVALUATION_END)
    results = cli.run_full_study(series, master_dates)
    for arm in cli.ARMS:
        base = results["arms"][arm]["base_cost"]
        for key in ("signal_count", "scheduled_rebalance_count", "executed_rebalance_count",
                    "buy_transaction_count", "sell_transaction_count", "closed_lot_count",
                    "annualized_volatility_pct", "annualized_downside_deviation_pct"):
            assert key in base
        assert "closed_lot_count" in base
        assert "trade_or_rebalance_count" not in base


def test_fetch_end_exclusive_is_documented_not_a_hard_guarantee():
    # The constant name/comment must not claim the fetched data stops at
    # EVALUATION_END -- the real cached series extends one session past
    # it (2026-08-14), which is exactly why close_on_exact_date exists.
    assert cli.FETCH_END_EXCLUSIVE == date(2026, 8, 14)
    doc = cli.__doc__ or ""
    # Structural check: the module actually defines and uses the exact-
    # date resolver rather than a positional index anywhere in the
    # endpoint-mark construction.
    import inspect
    source = inspect.getsource(cli.run_full_study)
    assert "closes[-1]" not in source
    assert "end_price_by_symbol_for" in source
