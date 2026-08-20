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


def _price_path_stop_after(*, breakout_at: tuple[int, int], crash_at: tuple[int, int], until: tuple[int, int] = (10, 20)):
    """Breaks out at `breakout_at` and holds (so the 09:50 WAITED re-check
    stays valid and the trade enters), then crashes hard starting at
    `crash_at` -- forces a full protective-stop exit shortly after entry,
    deterministically closing the first trade well before 10:20 ET so the
    second-trade scan actually runs."""
    closes: dict[tuple[int, int], str] = {}
    hh, mm = 9, 40
    while (hh, mm) <= until:
        if (hh, mm) >= crash_at:
            closes[(hh, mm)] = "90.0"
        elif (hh, mm) >= breakout_at:
            closes[(hh, mm)] = "105.5"
        else:
            closes[(hh, mm)] = "100.6"
        mm += 1
        if mm == 60:
            mm = 0
            hh += 1
    return closes


def _price_path_revert(*, breakout_at: tuple[int, int], revert_at: tuple[int, int], until: tuple[int, int] = (10, 20)):
    """Breaks out at `breakout_at` (would be independently valid if
    evaluated then) but reverts back below the breakout level at
    `revert_at` -- used to prove the second-trade scan evaluates each
    minute fresh and never carries an earlier minute's approval forward."""
    closes: dict[tuple[int, int], str] = {}
    hh, mm = 9, 40
    while (hh, mm) <= until:
        if (hh, mm) >= revert_at:
            closes[(hh, mm)] = "100.6"
        elif (hh, mm) >= breakout_at:
            closes[(hh, mm)] = "105.5"
        else:
            closes[(hh, mm)] = "100.6"
        mm += 1
        if mm == 60:
            mm = 0
            hh += 1
    return closes


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
    # AAPL is the baseline trade (stops out early); META qualifies later.
    # Non-vacuous: the second trade is unconditionally required to be
    # selected and to never be AAPL.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    meta = _price_path(breakout_at=(9, 55), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.baseline_symbol == "AAPL"
    assert d.second_scan.status == "SELECTED"
    assert d.second_row is not None
    assert d.second_row.decision["symbol"] == "META"
    assert d.second_row.decision["symbol"] != "AAPL"


def test_determinism_across_repeated_runs():
    # Non-vacuous: this fixture always produces a SELECTED second trade
    # (verified below), so the determinism check actually exercises that
    # path on both runs rather than passing trivially on an unselected one.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    meta = _price_path(breakout_at=(9, 55), until=(15, 45))
    r1, *_ = _run({"AAPL": aapl, "META": meta})
    r2, *_ = _run({"AAPL": aapl, "META": meta})
    assert len(r1) == len(r2) == 1
    assert r1[0].second_eligible == r2[0].second_eligible is True
    assert r1[0].second_scan.status == r2[0].second_scan.status == "SELECTED"
    assert r1[0].second_row is not None and r2[0].second_row is not None
    assert r1[0].second_row.outcome == r2[0].second_row.outcome
    assert r1[0].second_row.decision["symbol"] == r2[0].second_row.decision["symbol"] == "META"


# --------------------------------------------------------------------------
# Non-vacuous coverage of the SELECTED (second trade actually entered and
# simulated) path -- the empirical M47 run only ever exercised
# NO_VALID_CANDIDATE, so these deterministic synthetic fixtures are the
# only place the SELECTED branch of `build_day_results`/
# `build_second_shadow_outcome` is exercised at all.
# --------------------------------------------------------------------------

def test_second_trade_full_lifecycle_entered_and_closed():
    # AAPL enters at 09:50, is stopped out at 09:52 ET (well before 10:20).
    # META independently breaks out at 09:55 and stays flat through the
    # 15:45 time-exit, so its own second trade both enters AND closes.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    meta = _price_path(breakout_at=(9, 55), until=(15, 45))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]

    # First trade: entered, closed, well before 10:20 ET.
    assert d.baseline_row.outcome["entered"] is True
    assert d.baseline_row.outcome["closed"] is True
    first_exit = datetime.fromisoformat(d.baseline_row.outcome["exit_timestamp"])
    assert first_exit.astimezone(EASTERN).time() < SECOND_SCAN_END_ET

    # Second-trade eligibility and scan.
    assert d.second_eligible is True
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "META"
    assert d.second_scan.selected_timestamp == datetime(2026, 8, 14, 13, 56, tzinfo=timezone.utc)

    # A real second row: entered, closed, distinct symbol.
    assert d.second_row is not None
    assert d.second_row.decision["symbol"] == "META"
    assert d.second_row.outcome["entered"] is True
    assert d.second_row.outcome["closed"] is True

    # Second entry strictly later than first exit.
    second_entry = datetime.fromisoformat(d.second_row.outcome["entry_timestamp"])
    assert second_entry > first_exit
    assert d.minutes_between_first_exit_and_second_decision is not None
    assert d.minutes_between_first_exit_and_second_decision > 0


def test_second_trade_scan_excludes_exact_first_exit_timestamp_but_next_minute_qualifies():
    # NVDA is valid starting exactly at the first-exit minute (09:52 ET) --
    # the scan is strictly *after* exit, so 09:52 itself must be excluded.
    # NVDA remains valid at 09:53 too (the very next completed minute),
    # which must be picked up.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    nvda = _price_path(breakout_at=(9, 52), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "NVDA": nvda})
    assert len(day_results) == 1
    d = day_results[0]
    first_exit = datetime.fromisoformat(d.baseline_row.outcome["exit_timestamp"])
    assert first_exit == datetime(2026, 8, 14, 13, 52, tzinfo=timezone.utc)
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "NVDA"
    assert d.second_scan.selected_timestamp == datetime(2026, 8, 14, 13, 53, tzinfo=timezone.utc)
    assert d.second_scan.selected_timestamp > first_exit  # strictly after, never equal


def test_second_trade_scan_same_minute_tie_break_is_alphabetical():
    # NVDA and META both become valid at exactly the same minute -- the
    # frozen tie-break is ticker-ascending, never RVOL/gap/strength.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    nvda = _price_path(breakout_at=(9, 55), until=(10, 20))
    meta = _price_path(breakout_at=(9, 55), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "NVDA": nvda, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "META"  # META < NVDA alphabetically
    assert set(d.second_scan.tied_symbols) == {"META", "NVDA"}


def test_second_trade_scan_never_carries_forward_a_stale_earlier_approval():
    # NVDA breaks out early (09:40, same as AAPL) but reverts back below
    # the breakout level before the scan window opens -- an implementation
    # that reused an earlier minute's stale approval would wrongly select
    # NVDA; the real fresh-per-minute SignalEngine evaluation must reject
    # it and fall through to META (valid later, on its own genuine merits).
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    nvda = _price_path_revert(breakout_at=(9, 40), revert_at=(9, 52), until=(10, 20))
    meta = _price_path(breakout_at=(9, 55), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "NVDA": nvda, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "META"  # not NVDA -- its early approval is stale, not reused


def test_second_trade_scan_uses_period_native_universe_not_production_default():
    # Regression for the universe-membership bug: BAC is not in the
    # tracked production config's default universe
    # (src/config.py / config/strategy_v0.yaml), but IS the frozen native
    # universe for several periods (P_MINUS_2/P_MINUS_1/P1/P2/P3). Before
    # the fix, SignalEngine rejected it with "Symbol is not in the
    # approved universe." before gap/RVOL was ever evaluated, regardless
    # of how genuinely valid its breakout was.
    assert "BAC" not in CONFIG["universe"]["symbols"]
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    bac = _price_path(breakout_at=(9, 55), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "BAC": bac})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "BAC"
    assert d.second_row is not None
    assert d.second_row.decision["symbol"] == "BAC"


def test_second_trade_scan_evaluation_universe_matches_requested_universe():
    # Audit field: `evaluation_universe` must always equal the exact
    # `universe` the scan was called with, for the SELECTED, NO_VALID_
    # CANDIDATE, and no-other-symbols paths alike.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    bac = _price_path(breakout_at=(9, 55), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "BAC": bac})
    d = day_results[0]
    assert d.second_scan.evaluation_universe == ("AAPL", "BAC")

    combo_config = sensitivity_config(CONFIG, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75"))
    no_others = scan_for_second_candidate(
        session_date=SESSION_DATE, first_trade_symbol="AAPL", universe=("AAPL",),
        scan_start_exclusive=datetime(2026, 8, 14, 14, 0, tzinfo=timezone.utc),
        inputs_table={}, combo_config=combo_config,
    )
    assert no_others.evaluation_universe == ("AAPL",)


def test_second_trade_scan_max_one_second_trade_per_day_even_with_multiple_qualifiers():
    # NVDA, META, and a third later-qualifying symbol are all
    # independently valid at different minutes -- exactly one second
    # trade (the earliest) is ever produced; the scan stops at the first
    # qualifying minute rather than continuing to look for more.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    nvda = _price_path(breakout_at=(9, 55), until=(10, 20))
    meta = _price_path(breakout_at=(10, 0), until=(10, 20))
    day_results, *_ = _run({"AAPL": aapl, "NVDA": nvda, "META": meta})
    assert len(day_results) == 1
    d = day_results[0]
    assert d.second_scan.status == "SELECTED"
    assert d.second_scan.selected_symbol == "NVDA"  # earliest, not "best"
    assert d.second_row is not None  # exactly one row -- the dataclass shape itself forbids a second


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


def test_exit_arm_outcome_timestamps_propagate_and_serialize_for_both_legs():
    # The two additive ExitArmOutcome fields (entry_timestamp/
    # exit_timestamp) must round-trip correctly through _outcome_dict for
    # BOTH the baseline leg and a real, closed second leg -- not just be
    # present as keys (already checked elsewhere) but hold the exact
    # values the simulator computed, correctly ISO-serialized.
    aapl = _price_path_stop_after(breakout_at=(9, 40), crash_at=(9, 52))
    meta = _price_path(breakout_at=(9, 55), until=(15, 45))
    day_results, *_ = _run({"AAPL": aapl, "META": meta})
    d = day_results[0]

    assert d.baseline_row.outcome["entry_timestamp"] == "2026-08-14T13:50:00+00:00"
    assert d.baseline_row.outcome["exit_timestamp"] == "2026-08-14T13:52:00+00:00"
    assert d.second_row.outcome["entry_timestamp"] == "2026-08-14T13:56:00+00:00"
    assert d.second_row.outcome["exit_timestamp"] == "2026-08-14T19:45:00+00:00"

    for row in (d.baseline_row, d.second_row):
        entry_ts = datetime.fromisoformat(row.outcome["entry_timestamp"])
        exit_ts = datetime.fromisoformat(row.outcome["exit_timestamp"])
        assert entry_ts.tzinfo is not None and exit_ts.tzinfo is not None
        assert exit_ts >= entry_ts


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


def test_second_only_symbol_robustness_excludes_net_negative_symbol_from_denominator():
    # META's every trade is a winner, NVDA's every trade is a loser -- NVDA's
    # NET contribution is negative, so it contributes nothing to either the
    # numerator or the denominator, leaving META's share at exactly 1.0.
    # (This particular fixture happens to agree with the old, incorrect
    # per-trade-positive-sum formula too, since NVDA has zero winning
    # trades to sum; see the mixed-win/loss test below for a fixture that
    # only the corrected NET-per-symbol formula gets right.)
    rows = _synthetic_rows(n=5, symbol="META", r_multiple=1.0) + _synthetic_rows(n=5, symbol="NVDA", r_multiple=-0.5)
    robustness = second_only_symbol_robustness(rows)
    assert robustness["best_r_contributor"] == "META"
    assert robustness["best_r_contribution_share"] == pytest.approx(1.0)


def test_second_only_symbol_robustness_uses_net_per_symbol_not_per_trade_positive_sum():
    # Symbol A: 3 winners (+2.0 each) and 3 losers (-1.9 each) -> NET =
    # +0.3, barely positive. Symbol B: 1 winner (+1.0) and no losers ->
    # NET = +1.0. The correct convention (each symbol's own NET
    # cumulative R, keep only positive nets, denominator = sum of those)
    # gives: positive_net_total = 0.3 + 1.0 = 1.3, best = B (net 1.0),
    # share = 1.0 / 1.3 ~= 0.769.
    #
    # The prior, INCORRECT formula (sum each trade's own max(r, 0),
    # ignoring per-symbol netting) would instead give: positive_total =
    # (3 * 2.0) + (1 * 1.0) = 7.0 (A's three losing trades contribute
    # nothing to the denominator despite A's net edge being tiny), and
    # share = 1.0 / 7.0 ~= 0.143 -- a materially different, wrong number.
    # This fixture fails under that old formula and passes under the
    # corrected one.
    rows_a = (
        _synthetic_rows(n=3, symbol="AAA", r_multiple=2.0)
        + _synthetic_rows(n=3, symbol="AAA", r_multiple=-1.9)
    )
    rows_b = _synthetic_rows(n=1, symbol="BBB", r_multiple=1.0)
    rows = rows_a + rows_b
    robustness = second_only_symbol_robustness(rows)
    assert robustness["best_r_contributor"] == "BBB"
    assert robustness["best_r_contribution_share"] == pytest.approx(1.0 / 1.3, rel=1e-6)
    assert robustness["best_r_contribution_share"] != pytest.approx(1.0 / 7.0, rel=1e-6)


def test_second_only_symbol_robustness_symbol_with_negative_net_contributes_nothing():
    # A symbol with winners AND losers whose NET is negative must be
    # excluded from the denominator entirely, even though it has some
    # individually-positive trades that the old per-trade formula would
    # have summed in.
    rows_a = (
        _synthetic_rows(n=1, symbol="AAA", r_multiple=1.0)  # one winner
        + _synthetic_rows(n=1, symbol="AAA", r_multiple=-1.5)  # one bigger loser -> net -0.5
    )
    rows_b = _synthetic_rows(n=1, symbol="BBB", r_multiple=0.8)
    rows = rows_a + rows_b
    robustness = second_only_symbol_robustness(rows)
    assert robustness["best_r_contributor"] == "BBB"
    # Denominator is ONLY BBB's positive net (0.8) -- AAA's net-negative
    # -0.5 contributes zero, not its individual +1.0 winning trade.
    assert robustness["best_r_contribution_share"] == pytest.approx(1.0)


def test_second_only_symbol_robustness_empty_rows():
    robustness = second_only_symbol_robustness(())
    assert robustness["best_r_contributor"] is None
    assert robustness["best_r_contribution_share"] is None
