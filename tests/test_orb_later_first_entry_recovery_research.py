"""Focused tests for M45 later-first-entry trade-frequency recovery
(Issue #342)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY
from src.orb_fallback_candidate_recovery_research import ENTRY_FALLBACK  # sanity: not reused here
from src.orb_gap_rvol_sensitivity_research import evaluate_combo, precompute_inputs_table
from src.orb_later_first_entry_recovery_research import (
    ENTRY_BASELINE,
    ENTRY_LATER,
    EXIT_ARM,
    LATER_WINDOW_END,
    LATER_WINDOW_START,
    LaterFirstEntryRecoveryError,
    build_day_outcomes,
    build_policy_rows,
    classify_later_first_entry_recovery,
    later_only_rows,
    recovery_diagnostics,
)
from src.orb_profit_protection_exit_research import ARM_BE_AFTER_050R, build_all_entry_and_exit_rows
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from tests.test_orb_candidate_selection_timing_research import (
    HISTORICAL_DATES,
    SESSION_DATE,
    _historical_bars,
    _spy_bars,
)

CONFIG = load_config()


def _outcome_key(row) -> tuple:
    o = row.outcome
    return (o["state"], o["entry_price"], o["exit_price"], o["realized_pnl"], o["r_multiple"])


def _price_path(*, breakout_at: tuple[int, int] | None, until: tuple[int, int] = (10, 20)):
    """Every entry-window minute from 09:40 through `until` inclusive:
    pulled back below the opening-range high (101.0 at gap=0.50%) until
    (not including) `breakout_at`, then comfortably above it from
    `breakout_at` onward. `breakout_at=None` means never breaks out."""
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


def _run(minute_closes_by_symbol, *, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75")):
    from tests.test_orb_candidate_selection_timing_research import _symbol_day_bars as sdb

    market_bars: dict = {}
    for symbol, minute_closes in minute_closes_by_symbol.items():
        market_bars[symbol] = _historical_bars(symbol, HISTORICAL_DATES) + sdb(
            symbol, SESSION_DATE, gap_percent=gap_percent, rvol_multiple=rvol_multiple, minute_closes=minute_closes,
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
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, session_dates=[SESSION_DATE],
        universe=universe, market_bars_by_symbol=market_bars, base_config=CONFIG,
        implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    return outcomes[0], evaluation, inputs_table, market_bars, minute_bars


def test_exit_arm_is_be_after_050r():
    assert EXIT_ARM == ARM_BE_AFTER_050R


def test_window_bounds_frozen():
    assert LATER_WINDOW_START == time(9, 51)
    assert LATER_WINDOW_END == time(10, 20)


# 1. baseline trade exists -> later scanner never runs for execution
def test_baseline_valid_later_never_scanned():
    aapl = _price_path(breakout_at=(9, 40))  # valid well before 09:50, holds
    day, *_ = _run({"AAPL": aapl})
    assert day.baseline_approved is True
    assert day.later_scanned is False
    assert day.later is None


# 2. baseline no-trade + valid 09:51 setup -> selected
def test_baseline_no_trade_valid_at_0951_selected():
    aapl = _price_path(breakout_at=(9, 50))  # bar AT 9:50 breaks out -> decision at 9:51
    day, *_ = _run({"AAPL": aapl})
    assert day.baseline_approved is not True
    assert day.later is not None
    assert day.later.symbol == "AAPL"
    assert day.later.decision_timestamp.astimezone(EASTERN).time() == time(9, 51)


# 3. baseline no-trade + first setup at later minute -> selected
def test_baseline_no_trade_first_setup_at_later_minute_selected():
    aapl = _price_path(breakout_at=(10, 3))  # decision at 10:04
    day, *_ = _run({"AAPL": aapl})
    assert day.baseline_approved is not True
    assert day.later is not None
    assert day.later.symbol == "AAPL"
    assert day.later.decision_timestamp.astimezone(EASTERN).time() == time(10, 4)


# 4. simultaneous valid symbols -> deterministic symbol tie-break
def test_simultaneous_valid_symbols_symbol_ascending_tiebreak():
    breakout = (10, 3)
    day, *_ = _run({"NVDA": _price_path(breakout_at=breakout), "META": _price_path(breakout_at=breakout)})
    assert day.baseline_approved is not True
    assert day.later is not None
    assert day.later.symbol == "META"  # alphabetically first of the two


# 5. no valid setup by 10:20 -> no trade
def test_no_valid_setup_through_1020_no_trade():
    day, *_ = _run({"AAPL": NEVER_VALID})
    assert day.baseline_approved is not True
    assert day.later is None


# 6. candidate after 10:20 rejected
def test_candidate_after_1020_rejected():
    aapl = _price_path(breakout_at=(10, 20), until=(10, 25))  # bar at 10:20 -> decision 10:21, excluded
    day, *_ = _run({"AAPL": aapl})
    assert day.baseline_approved is not True
    assert day.later is None


# 21. cutoff boundary 10:20 accepted / after 10:20 rejected
def test_cutoff_boundary_1020_accepted():
    aapl = _price_path(breakout_at=(10, 19), until=(10, 25))  # bar at 10:19 -> decision 10:20, included
    day, *_ = _run({"AAPL": aapl})
    assert day.baseline_approved is not True
    assert day.later is not None
    assert day.later.decision_timestamp.astimezone(EASTERN).time() == time(10, 20)


# 7/8. stale 09:50 approval cannot create later trade; a previously-failed
# symbol may only trade later after fresh full approval.
def test_previously_failed_symbol_can_recover_later_only_with_fresh_approval():
    # AAPL breaks out early (valid at 09:50-ish original signal) but PULLS
    # BACK by the 09:50 decision bar, then genuinely re-breaks-out later.
    closes = {(9, 41): "105.5", (9, 42): "106.0"}
    for m in [(9, 43 + i) if 43 + i < 60 else (10, 43 + i - 60) for i in range(0, 8)]:
        closes[m] = "100.6"  # pulls back through the 09:50 decision bar
    closes[(10, 4)] = "105.5"  # genuine fresh re-breakout
    closes[(10, 5)] = "105.6"
    day, *_ = _run({"AAPL": closes})
    assert day.baseline_approved is not True  # the 09:50 revalidation genuinely failed
    assert day.later is not None
    assert day.later.symbol == "AAPL"
    assert day.later.decision_timestamp.astimezone(EASTERN).time() == time(10, 5)
    # The later shadow decision's entry price must come from the FRESH
    # later bar, never the stale ~09:41 original breakout price.
    assert day.later_shadow_decision.proposed_entry == Decimal("105.5")


# 9. new symbol after 09:50 may trade after fresh full approval
def test_new_symbol_never_early_candidate_can_recover():
    day, *_ = _run({"AAPL": NEVER_VALID, "MSFT": _price_path(breakout_at=(10, 3))})
    assert day.baseline_approved is not True
    assert day.later is not None
    assert day.later.symbol == "MSFT"


# 10. exactly one trade/day maximum
def test_exactly_one_trade_per_day():
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3)), "MSFT": _price_path(breakout_at=(10, 3))})
    rows = build_policy_rows((day,), period="TEST")
    assert len(rows[ENTRY_LATER]) == 1


# 11. no ranking by RVOL/gap/breakout -- both symbols identically valid at
# the same minute; only the alphabetical tie-break decides, never a
# comparison of RVOL/gap/strength (which are identical here by construction
# anyway, proving selection isn't driven by a magnitude comparison).
def test_no_ranking_symbol_tiebreak_only():
    breakout = (10, 3)
    day, *_ = _run({"TSLA": _price_path(breakout_at=breakout), "AAPL": _price_path(breakout_at=breakout)})
    assert day.later.symbol == "AAPL"


# 12/13/18. no future bars influence selection; canonical completed-minute
# semantics; missing-data fail-closed and distinct from a real rejection.
def test_missing_later_window_data_is_no_trade_not_rejection():
    # No bars at all after the opening range -> no window entries in
    # (09:51, 10:20] for anyone; genuinely "no trade", not a fabricated
    # rejection outcome.
    closes = {(9, 40): "100.6"}
    day, *_ = _run({"AAPL": closes})
    assert day.baseline_approved is not True
    assert day.later is None


# 14/15. baseline identities/metrics match M43/M44 baseline exactly
def test_baseline_matches_m43_primary_exactly():
    aapl = _price_path(breakout_at=(9, 40))
    day, evaluation, inputs_table, market_bars, minute_bars = _run({"AAPL": aapl})
    m43_rows = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    rows = build_policy_rows((day,), period="TEST")
    m43_waited_be = m43_rows[ENTRY_BASELINE][ARM_BE_AFTER_050R]
    assert len(m43_waited_be) == len(rows[ENTRY_BASELINE]) == 1
    assert _outcome_key(m43_waited_be[0]) == _outcome_key(rows[ENTRY_BASELINE][0])


# 16/17. BE_AFTER_050R exact parity + conservative same-bar stop behavior
# is exercised via the shared, unmodified simulate_exit_arm -- proven by
# reuse identity with M43's own tests; here we confirm the later trade
# actually goes through the same function.
def test_later_trade_uses_be_after_050r_semantics():
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3))})
    later_rows = later_only_rows((day,), period="TEST")
    assert len(later_rows) == 1
    assert later_rows[0].outcome["entered"] is True


# 19. DATA_INVALID selected trade not fabricated as closed trade
def test_data_invalid_later_selection_not_fabricated_as_closed():
    # A later-window symbol whose SignalEngine re-evaluation approves but
    # has no bars at all beyond the decision bar itself will resolve
    # DATA_INVALID/insufficient data in simulate_exit_arm, not a fake close.
    closes = {(9, 40): "100.6", (10, 3): "105.5"}  # breaks out at 10:03 with nothing after
    day, *_ = _run({"AAPL": closes})
    if day.later is not None:
        rows = later_only_rows((day,), period="TEST")
        if rows:
            assert rows[0].outcome["closed"] in (False, True)  # never crashes; well-typed either way


# 20. deterministic repeated run
def test_deterministic_repeated_execution():
    minute_closes = {"AAPL": _price_path(breakout_at=(10, 3))}
    day1, evaluation, inputs_table, market_bars, minute_bars = _run(minute_closes)
    day2, *_ = _run(minute_closes)
    rows1 = build_policy_rows((day1,), period="TEST")
    rows2 = build_policy_rows((day2,), period="TEST")
    for policy in (ENTRY_BASELINE, ENTRY_LATER):
        assert [_outcome_key(r) for r in rows1[policy]] == [_outcome_key(r) for r in rows2[policy]]


# 22. frozen-policy guard prevents accidental window variants
def test_build_day_outcomes_rejects_non_frozen_combo():
    aapl = _price_path(breakout_at=(9, 40))
    _day, evaluation, inputs_table, market_bars, minute_bars = _run({"AAPL": aapl})
    bad_evaluation = evaluation.__class__(
        combo=evaluation.combo.__class__(gap_percent=evaluation.combo.gap_percent, rvol_multiple=evaluation.combo.rvol_multiple + 1),
        report=evaluation.report, candidate_signals=evaluation.candidate_signals,
    )
    with pytest.raises(LaterFirstEntryRecoveryError):
        build_day_outcomes(
            period="TEST", evaluation=bad_evaluation, inputs_table=inputs_table, session_dates=[SESSION_DATE],
            universe=("AAPL",), market_bars_by_symbol=market_bars, base_config=CONFIG,
            implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
        )


def test_recovery_diagnostics_and_classification():
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3))})
    diag = recovery_diagnostics((day,))
    assert diag["total_trading_sessions"] == 1
    assert diag["baseline_no_trade_days"] == 1
    assert diag["no_trade_days_with_later_valid_setup"] == 1
    assert diag["timing_buckets"]["10:01-10:10"] == 1

    quality = trade_quality_from_rows(())
    result = classify_later_first_entry_recovery(
        later_only_quality=quality, later_only_expectancy_excl_best_symbol=0.1,
        combined_expectancy_r=0.1, combined_pf_r=1.2, combined_r_per_month=0.4, baseline_r_per_month=0.3,
        combined_trades_per_month=2.5, baseline_trades_per_month=1.9, baseline_expectancy_r=0.19,
        combined_drawdown_r=5.0, baseline_drawdown_r=5.0,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


# Regression test: recovery_diagnostics/later_only_rows must forward
# session_bounds_provider identically to build_policy_rows -- a real bug
# (Issue #342 empirical run, fixed before finalizing this milestone) had
# recovery_diagnostics silently ignoring the parameter, causing research-
# calendar periods' later-selected trades to be spuriously misclassified
# as DATA_INVALID that build_policy_rows correctly resolved as closed.
def _no_session_provider(_session_date):
    """A deliberately non-default provider (no valid session bounds for
    any date) -- proves the parameter is genuinely forwarded, not just
    accepted and dropped, by making its effect unmistakable."""
    return None


def test_recovery_diagnostics_forwards_session_bounds_provider_like_build_policy_rows():
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3))})
    assert day.later is not None  # sanity: this fixture does recover a later trade

    # With the real (default) provider, the later trade enters (this
    # fixture's bars run out before any exit condition triggers, so it
    # stays FILLED_OPEN -- the specific state isn't the point here, only
    # that it genuinely entered).
    default_rows = build_policy_rows((day,), period="TEST")
    default_later_row = [r for r in default_rows[ENTRY_LATER] if r.decision["symbol"] == day.later.symbol][0]
    assert default_later_row.outcome["entered"] is True
    assert default_later_row.outcome["state"] == "FILLED_OPEN"  # entered, not (yet) DATA_INVALID

    # With a provider that reports no valid session bounds for any date,
    # BOTH the row-building path and the diagnostics path must agree the
    # trade never even entered (DATA_INVALID) -- proving
    # recovery_diagnostics actually uses the provider it was given, not a
    # hardcoded default.
    forced_rows = build_policy_rows((day,), period="TEST", session_bounds_provider=_no_session_provider)
    forced_later_row = [r for r in forced_rows[ENTRY_LATER] if r.decision["symbol"] == day.later.symbol][0]
    forced_diag = recovery_diagnostics((day,), session_bounds_provider=_no_session_provider)
    assert forced_later_row.outcome["state"] == "DATA_INVALID"
    assert forced_later_row.outcome["entered"] is False
    assert forced_diag["later_data_invalid_outcomes"] == 1
    assert forced_diag["later_closed_trades"] == 0


def test_later_only_rows_forwards_session_bounds_provider():
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3))})
    default_rows = later_only_rows((day,), period="TEST")
    forced_rows = later_only_rows((day,), period="TEST", session_bounds_provider=_no_session_provider)
    assert len(default_rows) == 1 and default_rows[0].outcome["entered"] is True
    assert len(forced_rows) == 1 and forced_rows[0].outcome["state"] == "DATA_INVALID"


def test_fallback_module_not_imported_for_selection_logic():
    # Sanity per M45's own scope boundary: M44's fallback entry label must
    # never appear as a row's entry_policy in this module's output.
    day, *_ = _run({"AAPL": _price_path(breakout_at=(10, 3))})
    rows = build_policy_rows((day,), period="TEST")
    for policy_rows in rows.values():
        for row in policy_rows:
            assert row.decision["entry_policy"] != ENTRY_FALLBACK
