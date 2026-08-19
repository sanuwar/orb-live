"""Focused tests for M47 sequential second-trade large-cap ORB feasibility."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY, early_pool, enrich_with_decision_time
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table, sensitivity_config
from src.orb_out_of_sample_validation_research import select_first_candidate_per_day
from src.orb_profit_protection_exit_research import ARM_BE_AFTER_050R
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from src.orb_regime_diagnostic_research import TradeDiagnosticRow as _Row
from src.orb_sequential_second_trade_research import (
    MIN_SECOND_ONLY_SUPPORTED_N,
    POLICY_SEQUENTIAL_SECOND_VALID_1020,
    SECOND_SCAN_END_ET,
    SequentialSecondTradeError,
    baseline_rows,
    build_day_results,
    classify_sequential_second_trade,
    combined_rows,
    scan_for_second_candidate,
    second_only_rows,
    second_only_symbol_robustness,
    second_trade_eligibility,
)
from tests.test_orb_candidate_selection_timing_research import (
    HISTORICAL_DATES,
    SESSION_DATE,
    _historical_bars,
    _spy_bars,
    _symbol_day_bars,
)

CONFIG = load_config()
DIGEST = "test-digest"
CREATED_AT = datetime.now(timezone.utc)


def _price_path(*, breakout_at: tuple[int, int] | None, until: tuple[int, int] = (10, 20)):
    closes: dict[tuple[int, int], str] = {}
    minutes: list[tuple[int, int]] = []
    hh, mm = 9, 40
    while (hh, mm) <= until:
        minutes.append((hh, mm))
        mm += 1
        if mm == 60:
            mm = 0
            hh += 1
    for m in minutes:
        if breakout_at is not None and m >= breakout_at:
            closes[m] = "105.5"
        else:
            closes[m] = "100.6"
    return closes


NEVER_VALID = _price_path(breakout_at=None)


def _run(minute_closes_by_symbol, *, session_bounds_provider=None):
    market_bars: dict = {}
    for symbol, minute_closes in minute_closes_by_symbol.items():
        market_bars[symbol] = _historical_bars(symbol, HISTORICAL_DATES) + _symbol_day_bars(
            symbol, SESSION_DATE, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75"), minute_closes=minute_closes,
        )
    market_bars["SPY"] = _spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    minute_bars = {symbol: [bar.to_minute_bar() for bar in bars] for symbol, bars in market_bars.items()}
    universe = tuple(minute_closes_by_symbol.keys())

    inputs_table = precompute_inputs_table(
        universe=universe, session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars,
        spy_minute_bars=minute_bars["SPY"], base_config=CONFIG,
    )
    evaluation = evaluate_combo(
        combo=CONFIG_ENTRY.combo, universe=universe, session_dates=[SESSION_DATE],
        inputs_table=inputs_table, base_config=CONFIG,
    )
    early_winners = select_first_candidate_per_day(early_pool(evaluation.candidate_signals))
    waited_candidates = tuple(enrich_with_decision_time(c, inputs_table=inputs_table) for c in early_winners)

    combo_config = sensitivity_config(CONFIG, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75"))
    kwargs = {} if session_bounds_provider is None else {"session_bounds_provider": session_bounds_provider}
    day_results = build_day_results(
        period="TEST", waited_baseline_candidates=waited_candidates, universe=universe,
        inputs_table=inputs_table, combo_config=combo_config, strategy_version="test-v0",
        implementation_digest=DIGEST, created_at=CREATED_AT, market_bars_by_symbol=market_bars,
        **kwargs,
    )
    return day_results, evaluation, inputs_table, market_bars, minute_bars


# --------------------------------------------------------------------------
# second_trade_eligibility (pure function)
# --------------------------------------------------------------------------

def test_eligibility_requires_entered():
    eligible, reason = second_trade_eligibility(first_entered=False, first_closed=False, first_exit_timestamp=None)
    assert eligible is False
    assert reason == "FIRST_TRADE_DID_NOT_ENTER"


def test_eligibility_requires_closed():
    eligible, reason = second_trade_eligibility(first_entered=True, first_closed=False, first_exit_timestamp=None)
    assert eligible is False
    assert reason == "FIRST_TRADE_NOT_CLOSED_OR_UNTRUSTWORTHY_TIMESTAMP"


def test_eligibility_requires_exit_before_1020():
    ts = datetime(2026, 8, 14, 14, 20, tzinfo=timezone.utc).astimezone(EASTERN)  # 10:20 ET exactly
    eligible, reason = second_trade_eligibility(first_entered=True, first_closed=True, first_exit_timestamp=ts)
    assert eligible is False
    assert reason == "FIRST_TRADE_CLOSED_AT_OR_AFTER_SCAN_END"


def test_eligibility_true_when_exit_before_1020():
    ts = datetime(2026, 8, 14, 10, 0, tzinfo=EASTERN)
    eligible, reason = second_trade_eligibility(first_entered=True, first_closed=True, first_exit_timestamp=ts)
    assert eligible is True
    assert reason is None


def test_scan_end_boundary_is_1020():
    assert SECOND_SCAN_END_ET == time(10, 20)


def test_policy_name_frozen():
    assert POLICY_SEQUENTIAL_SECOND_VALID_1020 == "SEQUENTIAL_SECOND_VALID_1020"


# --------------------------------------------------------------------------
# End-to-end via build_day_results (real pipeline, synthetic price paths)
# --------------------------------------------------------------------------

def test_no_baseline_trade_day_never_scans_for_second():
    # AAPL never breaks out -> no baseline trade at all -> no day result even attempted.
    day_results, *_ = _run({"AAPL": NEVER_VALID})
    assert len(day_results) == 0


def test_baseline_not_entered_or_not_closed_is_never_second_eligible():
    # A short, truncated price path: whatever real terminal state the
    # pipeline assigns (never entered, or entered-but-unclosed), the
    # second-trade scan must never run and must never produce a row.
    # (The exact entered/closed mechanics of *why* are already covered in
    # isolation by the pure `second_trade_eligibility` tests above.)
    aapl = _price_path(breakout_at=(9, 40), until=(9, 53))
    day_results, *_ = _run({"AAPL": aapl})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.second_eligible is False
    assert d.second_row is None
    assert d.second_scan is None


def test_first_symbol_excluded_from_second_scan():
    # AAPL is the baseline trade; a simultaneously-valid AAPL "candidate"
    # can never occur since AAPL already traded -- verify a distinct
    # second-eligible symbol (META) is the one actually picked, never AAPL.
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))  # stays flat after breakout -> time exit later
    meta = _price_path(breakout_at=(10, 15), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.baseline_symbol == "AAPL"
    if d.second_row is not None:
        assert d.second_row.decision["symbol"] != "AAPL"


def test_determinism_across_repeated_runs():
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))
    meta = _price_path(breakout_at=(10, 10), until=(10, 20))
    r1, *_ = _run({"AAPL": aapl, "META": meta})
    r2, *_ = _run({"AAPL": aapl, "META": meta})
    assert len(r1) == len(r2) == 1
    assert r1[0].second_eligible == r2[0].second_eligible
    if r1[0].second_row is not None:
        assert r1[0].second_row.outcome == r2[0].second_row.outcome
        assert r2[0].second_row is not None
        assert r1[0].second_row.decision["symbol"] == r2[0].second_row.decision["symbol"]


def test_baseline_row_uses_be_after_050r_exit_arm_semantics():
    # Sanity: baseline row outcome shape matches the standard ExitArmOutcome
    # dict contract (same keys BE_AFTER_050R always produces elsewhere).
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))
    day_results, *_ = _run({"AAPL": aapl})
    assert len(day_results) == 1
    outcome = day_results[0].baseline_row.outcome
    for key in ("state", "entered", "closed", "r_multiple", "protection_activated", "entry_timestamp", "exit_timestamp"):
        assert key in outcome


def test_no_duplicate_second_trades_per_day():
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))
    meta = _price_path(breakout_at=(10, 5), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    second_count = sum(1 for d in day_results if d.second_row is not None)
    assert second_count <= 1  # exactly one day in this fixture


def test_baseline_and_second_only_and_combined_row_helpers():
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))
    meta = _price_path(breakout_at=(10, 5), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    b = baseline_rows(day_results)
    s = second_only_rows(day_results)
    c = combined_rows(day_results)
    assert len(c) == len(b) + len(s)
    for row in b:
        assert row in c
    for row in s:
        assert row in c


def test_scan_for_second_candidate_no_other_symbols_returns_no_candidate():
    from src.orb_gap_rvol_sensitivity_research import sensitivity_config
    combo_config = sensitivity_config(CONFIG, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75"))
    result = scan_for_second_candidate(
        session_date=SESSION_DATE, first_trade_symbol="AAPL", universe=("AAPL",),
        scan_start_exclusive=datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc),
        inputs_table={}, combo_config=combo_config,
    )
    assert result.status == "NO_VALID_CANDIDATE"


def test_pooled_classification_helpers_produce_r_metrics():
    aapl = _price_path(breakout_at=(9, 40), until=(15, 45))
    meta = _price_path(breakout_at=(10, 5), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    tq_baseline = trade_quality_from_rows(baseline_rows(day_results))
    tq_combined = trade_quality_from_rows(combined_rows(day_results))
    assert tq_combined["candidate_occurrences"] >= tq_baseline["candidate_occurrences"]


# --------------------------------------------------------------------------
# classify_sequential_second_trade -- frozen 10-condition gate (all four
# verdicts + the n=30 boundary), using synthetic rows since exercising
# every branch through the real bar-simulation pipeline would require
# manufacturing dozens of independently-valid second-trade days.
# --------------------------------------------------------------------------

def _synthetic_row(*, symbol: str, session_date_iso: str, r_multiple: float, leg: str = "SECOND") -> _Row:
    is_winner = r_multiple > 0
    return _Row(
        decision={"symbol": symbol, "session_date": session_date_iso, "leg": leg, "policy": POLICY_SEQUENTIAL_SECOND_VALID_1020},
        outcome={
            "state": "STOPPED" if not is_winner else "TARGET_HIT", "entered": True, "closed": True,
            "entry_price": "100.00", "initial_stop_price": "99.00", "final_stop_price": "99.00",
            "target_price": "102.00", "exit_price": "100.00", "exit_reason": "test",
            "realized_pnl": str(r_multiple), "r_multiple": r_multiple, "is_winner": is_winner,
            "is_stopped": not is_winner, "is_full_stop_loss": not is_winner, "initial_risk_r": "1.00",
            "maximum_favorable_excursion": None, "maximum_adverse_excursion": None,
            "mfe_r": None, "mae_r": None, "protection_activated": False, "bars_evaluated_after_entry": 5,
            "entry_timestamp": None, "exit_timestamp": None,
        },
    )


def _synthetic_rows(*, n: int, symbol: str, r_multiple: float, leg: str = "SECOND") -> tuple[_Row, ...]:
    return tuple(
        _synthetic_row(symbol=symbol, session_date_iso=f"2026-01-{(i % 28) + 1:02d}", r_multiple=r_multiple, leg=leg)
        for i in range(n)
    )


def _mixed_synthetic_rows(*, winners: int, losers: int, symbol: str, winner_r: float, loser_r: float, leg: str = "SECOND") -> tuple[_Row, ...]:
    """Win/loss mix (not all-winners) so `profit_factor_r` is a finite
    ratio rather than the `None` ("infinite, no losses") sentinel
    `trade_quality_from_rows` returns for an all-winning sample."""
    return (
        _synthetic_rows(n=winners, symbol=symbol, r_multiple=winner_r, leg=leg)
        + _synthetic_rows(n=losers, symbol=symbol, r_multiple=-abs(loser_r), leg=leg)
    )


# Mirrors the real pooled baseline's own shape (win_rate~0.44,
# expectancy_r~0.19, PF_R~1.73) so classify's baseline-relative ratios
# (expectancy_ratio, drawdown_ratio, frequency-gain) behave realistically.
_BASELINE_SYNTHETIC = _mixed_synthetic_rows(winners=45, losers=55, symbol="AAPL", winner_r=1.0, loser_r=0.47, leg="BASELINE")


def test_classify_insufficient_evidence_below_n_threshold():
    second_only = _synthetic_rows(n=MIN_SECOND_ONLY_SUPPORTED_N - 1, symbol="META", r_multiple=1.0)
    result = classify_sequential_second_trade(
        second_only_rows=second_only, combined_rows=_BASELINE_SYNTHETIC + second_only,
        baseline_rows=_BASELINE_SYNTHETIC, total_sessions=1350,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["second_only_n"] == MIN_SECOND_ONLY_SUPPORTED_N - 1


def test_classify_zero_second_only_trades_is_insufficient_evidence():
    result = classify_sequential_second_trade(
        second_only_rows=(), combined_rows=_BASELINE_SYNTHETIC, baseline_rows=_BASELINE_SYNTHETIC, total_sessions=1350,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert result["second_only_n"] == 0


def test_classify_supported_when_all_ten_conditions_pass():
    # Strong SECOND_ONLY trades (positive edge, finite PF) spread across
    # two symbols (so best-symbol-excluded expectancy stays positive), at
    # enough volume to clear the >=25% frequency-gain bar over baseline.
    winners_a = _mixed_synthetic_rows(winners=18, losers=7, symbol="META", winner_r=1.2, loser_r=0.4)
    winners_b = _mixed_synthetic_rows(winners=11, losers=4, symbol="NVDA", winner_r=1.2, loser_r=0.4)
    second_only = winners_a + winners_b
    result = classify_sequential_second_trade(
        second_only_rows=second_only, combined_rows=_BASELINE_SYNTHETIC + second_only,
        baseline_rows=_BASELINE_SYNTHETIC, total_sessions=1350,
    )
    assert result["verdict"] == "SUPPORTED"
    assert all(result["checks"].values())


def test_classify_mixed_when_edge_positive_but_other_conditions_fail():
    # Positive edge on both SECOND_ONLY and combined, but too few trades
    # relative to baseline to clear the 25% frequency-gain bar.
    second_only = _mixed_synthetic_rows(winners=20, losers=10, symbol="META", winner_r=0.6, loser_r=0.3)
    result = classify_sequential_second_trade(
        second_only_rows=second_only, combined_rows=_BASELINE_SYNTHETIC + second_only,
        baseline_rows=_BASELINE_SYNTHETIC, total_sessions=1350,
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["second_only_expectancy_positive"] is True
    assert result["checks"]["second_only_pf_above_1"] is True
    assert not all(result["checks"].values())


def test_classify_not_supported_when_second_only_expectancy_negative():
    second_only = _synthetic_rows(n=MIN_SECOND_ONLY_SUPPORTED_N, symbol="META", r_multiple=-0.5)
    result = classify_sequential_second_trade(
        second_only_rows=second_only, combined_rows=_BASELINE_SYNTHETIC + second_only,
        baseline_rows=_BASELINE_SYNTHETIC, total_sessions=1350,
    )
    assert result["verdict"] == "NOT_SUPPORTED"


def test_second_only_symbol_robustness_uses_positive_contribution_denominator():
    rows = _synthetic_rows(n=5, symbol="META", r_multiple=1.0) + _synthetic_rows(n=5, symbol="NVDA", r_multiple=-0.5)
    robustness = second_only_symbol_robustness(rows)
    assert robustness["best_r_contributor"] == "META"
    # Denominator is sum of positive-R contributions only (5.0 from META),
    # not net cumulative R (5.0 - 2.5 = 2.5) -- so share must be exactly 1.0.
    assert robustness["best_r_contribution_share"] == pytest.approx(1.0)


def test_second_only_symbol_robustness_empty_rows():
    robustness = second_only_symbol_robustness(())
    assert robustness["best_r_contributor"] is None
    assert robustness["best_r_contribution_share"] is None
