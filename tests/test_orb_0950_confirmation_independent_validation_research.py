"""Focused tests for M42 independent validation of the 09:50
first-candidate confirmation policy (Issue #332)."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from src.config import load_config
from src.exchange_calendar import EASTERN
from src.market_data_canonical import MarketBar, MarketDataQuality
from src.market_data_provider import ALPACA_IEX_SOURCE
from src.orb_baseline_fourth_period_research import ANCHORED_COHORT
from src.orb_baseline_stability_research import TradeDiagnosticRow
from src.orb_candidate_selection_timing_research import (
    ARM_CURRENT_FIRST_VALID,
    ARM_WAITED_FIRST_VALID_0950,
    CONFIG_ENTRY,
    build_all_arms_rows,
)
from src.orb_gap_rvol_sensitivity_research import ParameterCombo, precompute_inputs_table, evaluate_combo
from src.orb_0950_confirmation_independent_validation_research import (
    DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA,
    Independent0950ValidationCriteria,
    P_MINUS_2_REFERENCE_DAY,
    P_MINUS_2_SESSION_COUNT,
    POLICIES,
    POLICY_CURRENT_FIRST_VALID,
    POLICY_WAITED_FIRST_VALID_0950,
    VALIDATION_UNIVERSE,
    build_both_policies_rows,
    classify_0950_confirmation,
    derive_p_minus_2_sessions,
    recommend_next_milestone,
    wait_diagnostics,
    waited_symbol_robustness,
)
from src.orb_independent_single_trade_core_validation_research import derive_p_minus_1_sessions
from src.orb_existing_configs_reevaluation_research import PERIOD_SESSION_SPEC
from src.orb_research_historical_calendar import research_trading_sessions_before
from src.orb_universe_expansion_research import trading_sessions_before

CONFIG = load_config()
LOOKBACK_DAYS = int(CONFIG["signal"]["relative_volume_lookback_days"])


# ---------------------------------------------------------------------------
# Fixture helpers (same technique as tests/test_orb_candidate_selection_timing_research.py).
# ---------------------------------------------------------------------------

def _mbar(symbol, session_date, hh, mm, *, open_, high, low, close, volume):
    ts = datetime(session_date.year, session_date.month, session_date.day, hh, mm, tzinfo=EASTERN).astimezone(timezone.utc)
    return MarketBar(
        symbol=symbol, exchange_session="REGULAR", trading_date=session_date, event_timestamp=ts,
        end_timestamp=ts + timedelta(minutes=1), receive_timestamp=ts + timedelta(seconds=1),
        open=Decimal(open_), high=Decimal(high), low=Decimal(low), close=Decimal(close), volume=volume,
        source=ALPACA_IEX_SOURCE, interval_seconds=60, provider_sequence_id=None, quality=MarketDataQuality.VALID,
    )


def _prior_sessions(first_session: date, *, count: int) -> list[date]:
    import src.exchange_calendar as cal

    dates: list[date] = []
    cursor = first_session - timedelta(days=1)
    while len(dates) < count:
        if cal.regular_session_bounds(cursor) is not None:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    return sorted(dates)


def _historical_bars(symbol: str, dates: list[date]) -> list[MarketBar]:
    bars = []
    for d in dates:
        for minute in range(5):
            bars.append(_mbar(symbol, d, 9, 30 + minute, open_="100", high="100.5", low="99.5", close="100", volume=100_000))
    return bars


def _symbol_day_bars(symbol, session_date, *, gap_percent, rvol_multiple, minute_closes):
    previous_close = Decimal("100")
    today_open = previous_close * (Decimal("1") + gap_percent / Decimal("100"))
    opening_bar_volume = int((rvol_multiple * Decimal("500000")) / 5)
    bars = []
    for minute in range(5):
        bars.append(_mbar(symbol, session_date, 9, 30 + minute, open_=str(today_open), high=str(today_open + Decimal("0.5")), low=str(today_open - Decimal("0.3")), close=str(today_open), volume=opening_bar_volume))
    prev_close = today_open
    for (hh, mm), close_str in sorted(minute_closes.items()):
        close = Decimal(close_str)
        open_ = prev_close
        high = max(open_, close) + Decimal("0.02")
        low = min(open_, close) - Decimal("0.02")
        bars.append(_mbar(symbol, session_date, hh, mm, open_=str(open_), high=str(high), low=str(low), close=str(close), volume=20_000))
        prev_close = close
    return bars


def _spy_bars(dates, *, base_price="500"):
    bars = []
    for d in dates:
        price = Decimal(base_price)
        for minute in range(60):
            hh, mm = (9, 30 + minute) if minute < 30 else (10, minute - 30)
            price += Decimal("0.01")
            bars.append(_mbar("SPY", d, hh, mm, open_=str(price - Decimal("0.01")), high=str(price + Decimal("0.02")), low=str(price - Decimal("0.02")), close=str(price), volume=100_000))
    return bars


def _minute_bars(market_bars):
    return {symbol: [bar.to_minute_bar() for bar in bars] for symbol, bars in market_bars.items()}


SESSION_DATE = date(2026, 8, 14)
HISTORICAL_DATES = _prior_sessions(SESSION_DATE, count=LOOKBACK_DAYS)

STRONG_BREAKOUT_HOLDS = {
    (9, 40): "105.5", (9, 41): "106.0", (9, 42): "106.2", (9, 43): "106.3", (9, 44): "106.4",
    (9, 45): "106.5", (9, 46): "106.6", (9, 47): "106.7", (9, 48): "106.8", (9, 49): "106.9",
    (9, 50): "107.0", (9, 51): "107.2", (9, 52): "107.4", (9, 53): "107.6", (9, 54): "107.8", (9, 55): "108.0",
}

# Opening_range_high = 100.5 + 0.5 = 101.0 at gap=0.5%. Breaks out at 09:41
# then pulls back below the range high well before 09:50.
PULLS_BACK_BY_0950 = {
    (9, 40): "101.5", (9, 41): "101.6", (9, 42): "101.4", (9, 43): "101.2", (9, 44): "101.1",
    (9, 45): "101.05", (9, 46): "101.0", (9, 47): "100.9", (9, 48): "100.8", (9, 49): "100.7",
    (9, 50): "100.6", (9, 51): "100.5", (9, 52): "100.4",
}


def _evaluate_day(minute_closes_by_symbol, *, gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.75")):
    market_bars = {}
    for symbol, minute_closes in minute_closes_by_symbol.items():
        market_bars[symbol] = _historical_bars(symbol, HISTORICAL_DATES) + _symbol_day_bars(symbol, SESSION_DATE, gap_percent=gap_percent, rvol_multiple=rvol_multiple, minute_closes=minute_closes)
    market_bars["SPY"] = _spy_bars(HISTORICAL_DATES + [SESSION_DATE])
    minute_bars = _minute_bars(market_bars)
    universe = tuple(minute_closes_by_symbol.keys())
    inputs_table = precompute_inputs_table(universe=universe, session_dates=[SESSION_DATE], minute_bars_by_symbol=minute_bars, spy_minute_bars=minute_bars["SPY"], base_config=CONFIG)
    evaluation = evaluate_combo(combo=CONFIG_ENTRY.combo, universe=universe, session_dates=[SESSION_DATE], inputs_table=inputs_table, base_config=CONFIG)
    return evaluation, inputs_table, market_bars, minute_bars


# ---------------------------------------------------------------------------
# Frozen period.
# ---------------------------------------------------------------------------

def test_p_minus_2_reference_day_is_p_minus_1_start():
    assert P_MINUS_2_REFERENCE_DAY == date(2022, 3, 30)


def test_p_minus_2_session_count_is_252():
    assert P_MINUS_2_SESSION_COUNT == 252


def test_p_minus_2_exact_dates():
    # Corrected by Issue #334: 2021-12-31 is a regular NYSE trading
    # session (New Year's Day is the one holiday NOT observed on the
    # preceding Friday -- see src.exchange_calendar.FULL_HOLIDAYS), so the
    # 252-session count reaches back one session less far than the
    # original (incorrect) 2021-03-30.
    sessions = derive_p_minus_2_sessions()
    assert len(sessions) == 252
    assert sessions[0] == date(2021, 3, 31)
    assert sessions[-1] == date(2022, 3, 29)
    assert sessions[-1] < P_MINUS_2_REFERENCE_DAY


def test_p_minus_2_does_not_overlap_p_minus_1_or_later_periods():
    p_minus_2 = set(derive_p_minus_2_sessions())
    p_minus_1 = set(derive_p_minus_1_sessions())
    assert not (p_minus_2 & p_minus_1)
    for period in ("P0", "P1", "P2"):
        reference_day, count, _ = PERIOD_SESSION_SPEC[period]
        sessions = set(research_trading_sessions_before(reference_day, count=count))
        assert not (p_minus_2 & sessions)
    reference_day, count, uses_research = PERIOD_SESSION_SPEC["P3"]
    p3 = set(trading_sessions_before(reference_day, count=count))
    assert not (p_minus_2 & p3)


# ---------------------------------------------------------------------------
# Frozen universe / config.
# ---------------------------------------------------------------------------

def test_validation_universe_is_anchored_cohort_unchanged():
    assert VALIDATION_UNIVERSE == ANCHORED_COHORT


def test_only_two_policies_declared():
    assert POLICIES == (POLICY_CURRENT_FIRST_VALID, POLICY_WAITED_FIRST_VALID_0950)


def test_module_constructs_no_new_parameter_combo():
    import inspect

    import src.orb_0950_confirmation_independent_validation_research as mod

    source = inspect.getsource(mod)
    assert "ParameterCombo(" not in source


def test_module_never_invokes_a_ranker():
    import inspect

    import src.orb_0950_confirmation_independent_validation_research as mod

    source = inspect.getsource(mod)
    for forbidden in ("rank_max_rvol", "rank_equal_rank_strength", "RANKED_POOL"):
        assert forbidden not in source


def test_module_never_touches_stop_target_exit_or_strategy_yaml():
    import inspect

    import src.orb_0950_confirmation_independent_validation_research as mod

    source = inspect.getsource(mod)
    for forbidden in ("strategy_v0.yaml", "stop_loss_percent", "target_percent", "ExitEngine("):
        assert forbidden not in source


# ---------------------------------------------------------------------------
# M41 reuse verification: M42's WAITED rows must be byte-identical (modulo
# period/shadow_run_id labels) to what M41's own build_all_arms_rows
# produces for the identical fixture -- proving no reimplementation.
# ---------------------------------------------------------------------------

def test_m42_waited_policy_matches_m41_implementation_on_identical_fixture():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    created_at = datetime.now(timezone.utc)

    m41_rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    m42_rows, _, _ = build_both_policies_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )

    m41_waited = m41_rows[ARM_WAITED_FIRST_VALID_0950][0]
    m42_waited = m42_rows[POLICY_WAITED_FIRST_VALID_0950][0]

    assert m41_waited.outcome == m42_waited.outcome
    # decision dicts are identical except the arm/view label itself.
    m41_decision = dict(m41_waited.decision)
    m42_decision = dict(m42_waited.decision)
    assert m41_decision.pop("view") == ARM_WAITED_FIRST_VALID_0950
    assert m42_decision.pop("view") == POLICY_WAITED_FIRST_VALID_0950
    assert m41_decision == m42_decision


def test_m42_current_policy_matches_m41_implementation_on_identical_fixture():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    created_at = datetime.now(timezone.utc)

    m41_rows = build_all_arms_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        minute_bars_by_symbol=minute_bars, base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    m42_rows, _, _ = build_both_policies_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )

    m41_current = m41_rows[ARM_CURRENT_FIRST_VALID][0]
    m42_current = m42_rows[POLICY_CURRENT_FIRST_VALID][0]
    assert m41_current.outcome == m42_current.outcome


# ---------------------------------------------------------------------------
# Candidate identity cannot switch.
# ---------------------------------------------------------------------------

def test_first_candidate_identity_cannot_switch_no_fallback_on_rejection():
    # NVDA qualifies first (09:41) but pulls back by 09:50; AMD qualifies
    # later (09:46) and would still be valid at 09:50. WAITED must record
    # NO TRADE for NVDA's identity, never falling back to AMD.
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_day({
        "AAPL": PULLS_BACK_BY_0950,
        "AMD": {(9, 46): "105.5", (9, 47): "106.0", (9, 48): "106.3", (9, 49): "106.6", (9, 50): "106.9"},
    })
    created_at = datetime.now(timezone.utc)
    rows, current_winners, waited_enriched = build_both_policies_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    # Identity preserved: AAPL was the first-by-09:50 qualifier (09:41 < 09:46).
    assert len(waited_enriched) == 1
    assert waited_enriched[0].symbol == "AAPL"
    waited_row = rows[POLICY_WAITED_FIRST_VALID_0950][0]
    assert waited_row.decision["symbol"] == "AAPL"
    assert waited_row.decision["wait_reevaluation_approved"] is False
    assert waited_row.outcome["entered"] is False
    # Never a second WAITED row for AMD.
    assert len(rows[POLICY_WAITED_FIRST_VALID_0950]) == 1


def test_realistic_0950_execution_price_never_reuses_original_breakout_price():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    created_at = datetime.now(timezone.utc)
    rows, _, _ = build_both_policies_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    original_entry_price = evaluation.candidate_signals[0][4].entry_price
    waited_row = rows[POLICY_WAITED_FIRST_VALID_0950][0]
    assert waited_row.decision["wait_reevaluation_approved"] is True
    assert waited_row.outcome["entry_price"] != str(original_entry_price)
    # Decision at 09:50 corresponds to the bar beginning 09:49 (its own close).
    assert waited_row.outcome["entry_price"] == "106.9"


def test_current_first_valid_semantics_unchanged_immediate_execution():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    created_at = datetime.now(timezone.utc)
    rows, _, _ = build_both_policies_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    current_row = rows[POLICY_CURRENT_FIRST_VALID][0]
    original_entry_price = evaluation.candidate_signals[0][4].entry_price
    assert current_row.outcome["entry_price"] == str(original_entry_price)


# ---------------------------------------------------------------------------
# Wait diagnostics.
# ---------------------------------------------------------------------------

def _row(symbol, session_date_str, *, entered, closed, r_multiple=None, is_winner=None):
    return TradeDiagnosticRow(
        decision={"symbol": symbol, "session_date": session_date_str},
        outcome={"entered": entered, "closed": closed, "r_multiple": r_multiple, "is_winner": is_winner},
    )


def test_wait_diagnostics_survival_and_rejection_counts():
    waited_enriched = []  # decision-time content not needed for this assertion path
    rows_waited = [
        TradeDiagnosticRow(decision={"symbol": "AAPL", "session_date": "2021-04-01", "wait_reevaluation_approved": True}, outcome={"entered": True, "closed": True, "r_multiple": 1.0, "is_winner": True}),
        TradeDiagnosticRow(decision={"symbol": "MSFT", "session_date": "2021-04-02", "wait_reevaluation_approved": False}, outcome={"entered": False, "closed": False, "r_multiple": None, "is_winner": None}),
        TradeDiagnosticRow(decision={"symbol": "NVDA", "session_date": "2021-04-05", "wait_reevaluation_approved": None}, outcome={"entered": False, "closed": False, "r_multiple": None, "is_winner": None}),
    ]
    result = wait_diagnostics(waited_enriched=[], rows_current=[], rows_waited=rows_waited)
    assert result["days_surviving_0950_revalidation"] == 1
    assert result["days_rejected_at_0950"] == 1
    assert result["days_no_decision_time_data"] == 1


def test_wait_diagnostics_paired_r_delta_and_transitions():
    rows_current = [
        _row("AAPL", "2021-04-01", entered=True, closed=True, r_multiple=0.5, is_winner=True),
        _row("MSFT", "2021-04-02", entered=True, closed=True, r_multiple=-0.8, is_winner=False),
    ]
    rows_waited = [
        TradeDiagnosticRow(decision={"symbol": "AAPL", "session_date": "2021-04-01", "wait_reevaluation_approved": True}, outcome={"entered": True, "closed": True, "r_multiple": 1.2, "is_winner": True}),
        TradeDiagnosticRow(decision={"symbol": "MSFT", "session_date": "2021-04-02", "wait_reevaluation_approved": False}, outcome={"entered": False, "closed": False, "r_multiple": None, "is_winner": None}),
    ]
    result = wait_diagnostics(waited_enriched=[], rows_current=rows_current, rows_waited=rows_waited)
    assert result["both_policies_trade_days"] == 1
    assert result["same_symbol_overlap_count"] == 1
    assert result["paired_r_delta_mean"] == pytest.approx(1.2 - 0.5)
    assert result["win_loss_transitions"] == {"current_win_waited_win": 1}
    assert result["days_waited_skips_but_current_trades"] == 1  # MSFT: current traded, waited rejected


# ---------------------------------------------------------------------------
# Symbol robustness.
# ---------------------------------------------------------------------------

def test_waited_symbol_robustness_excludes_aapl_and_tsla_independently():
    rows = [
        TradeDiagnosticRow(decision={"symbol": "AAPL", "session_date": "2021-04-01"}, outcome={"closed": True, "realized_pnl": "5.0", "r_multiple": 2.0, "is_winner": True, "is_stopped": False}),
        TradeDiagnosticRow(decision={"symbol": "TSLA", "session_date": "2021-04-02"}, outcome={"closed": True, "realized_pnl": "-2.0", "r_multiple": -1.0, "is_winner": False, "is_stopped": True}),
        TradeDiagnosticRow(decision={"symbol": "MSFT", "session_date": "2021-04-05"}, outcome={"closed": True, "realized_pnl": "1.0", "r_multiple": 0.5, "is_winner": True, "is_stopped": False}),
    ]
    result = waited_symbol_robustness(rows)
    assert result["best_r_contributor"] == "AAPL"
    assert result["expectancy_r_excluding_aapl"] == pytest.approx((-1.0 + 0.5) / 2)
    assert result["expectancy_r_excluding_tsla"] == pytest.approx((2.0 + 0.5) / 2)
    assert result["expectancy_r_excluding_best_contributor"] == pytest.approx((-1.0 + 0.5) / 2)
    assert set(result["top3_r_contributors"]) <= {"AAPL", "TSLA", "MSFT"}


# ---------------------------------------------------------------------------
# Classification.
# ---------------------------------------------------------------------------

def _tq(*, closed, exp_r, pf_r):
    return {"closed_trades": closed, "expectancy_r_per_trade": exp_r, "profit_factor_r": pf_r}


def test_classify_insufficient_sample_below_minimum():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=5, exp_r=0.5, pf_r=2.0), current_trade_quality=_tq(closed=5, exp_r=0.1, pf_r=1.1),
        waited_max_drawdown_r=2.0, waited_excluding_best_symbol_expectancy_r=0.1,
    )
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"


def test_classify_validated_when_all_seven_conditions_pass():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.3, pf_r=1.5), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=0.1,
    )
    assert result["verdict"] == "VALIDATED"
    assert all(result["checks"].values())


def test_classify_mixed_when_waited_does_not_beat_current_expectancy():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.2), current_trade_quality=_tq(closed=20, exp_r=0.10, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=0.02,
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["expectancy_r_beats_current"] is False


def test_classify_mixed_when_drawdown_exceeds_15r():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.3, pf_r=1.5), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=20.0, waited_excluding_best_symbol_expectancy_r=0.1,
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["acceptable_drawdown"] is False


def test_classify_mixed_when_single_symbol_dependent():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.3, pf_r=1.5), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=-0.1,
    )
    assert result["verdict"] == "MIXED"
    assert result["checks"]["not_single_symbol_dependent"] is False


def test_classify_failed_when_expectancy_not_positive():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=-0.05, pf_r=0.9), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=-0.1,
    )
    assert result["verdict"] == "FAILED"


def test_classify_failed_when_profit_factor_not_above_1():
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.01, pf_r=1.0), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=0.01,
    )
    assert result["verdict"] == "FAILED"


def test_classify_ignores_price_per_share_fields():
    contaminated = {"closed_trades": 20, "expectancy_r_per_trade": -0.05, "profit_factor_r": 0.9, "expectancy_per_trade": 100.0, "profit_factor": 5.0}
    result = classify_0950_confirmation(
        waited_trade_quality=contaminated, current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=-0.1,
    )
    assert result["verdict"] == "FAILED"


def test_default_criteria_minimum_is_15():
    assert DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA.minimum_waited_closed_trades == 15
    assert DEFAULT_INDEPENDENT_0950_VALIDATION_CRITERIA.maximum_acceptable_drawdown_r == 15.0


def test_custom_criteria_are_honoured():
    strict = Independent0950ValidationCriteria(minimum_waited_closed_trades=100)
    result = classify_0950_confirmation(
        waited_trade_quality=_tq(closed=20, exp_r=0.3, pf_r=1.5), current_trade_quality=_tq(closed=20, exp_r=0.05, pf_r=1.05),
        waited_max_drawdown_r=5.0, waited_excluding_best_symbol_expectancy_r=0.1, criteria=strict,
    )
    assert result["verdict"] == "INSUFFICIENT_SAMPLE"


# ---------------------------------------------------------------------------
# Next-milestone decision.
# ---------------------------------------------------------------------------

def test_recommend_profit_protection_when_validated():
    decision = recommend_next_milestone(verdict="VALIDATED")
    assert decision["next_milestone"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"
    assert decision["core_status"] == "VALIDATED"


def test_recommend_profit_protection_provisional_when_mixed():
    decision = recommend_next_milestone(verdict="MIXED")
    assert decision["next_milestone"] == "PROFIT_PROTECTION_EXIT_EXPERIMENT"
    assert decision["core_status"] == "PROVISIONAL"


def test_recommend_pause_when_failed():
    decision = recommend_next_milestone(verdict="FAILED")
    assert decision["next_milestone"] == "PAUSE_0950_CORE"


def test_recommend_operator_decision_when_insufficient_sample():
    decision = recommend_next_milestone(verdict="INSUFFICIENT_SAMPLE")
    assert decision["next_milestone"] is None
    assert decision["requires_operator_decision"] is True
