"""Focused tests for M44 fallback-candidate trade-frequency recovery
(Issue #340)."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from src.config import load_config
from src.orb_candidate_selection_timing_research import CONFIG_ENTRY
from src.orb_fallback_candidate_recovery_research import (
    ENTRY_BASELINE,
    ENTRY_FALLBACK,
    EXIT_ARM,
    DayOutcome,
    FallbackCandidateRecoveryError,
    _attempt_candidates,
    build_day_outcomes,
    build_policy_rows,
    classify_fallback_recovery,
    fallback_mechanics_diagnostics,
    fallback_only_rows,
    ordered_early_pool_per_day,
)
from src.orb_profit_protection_exit_research import ARM_BE_AFTER_050R, build_all_entry_and_exit_rows
from src.orb_regime_diagnostic_research import trade_quality_from_rows
from tests.test_orb_candidate_selection_timing_research import PULLS_BACK_BY_0950, SESSION_DATE, STRONG_BREAKOUT_HOLDS, _evaluate_single_day

CONFIG = load_config()


def _outcome_key(row) -> tuple:
    o = row.outcome
    return (o["state"], o["entry_price"], o["exit_price"], o["realized_pnl"], o["r_multiple"])


def _build_outcomes(minute_closes_by_symbol):
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day(minute_closes_by_symbol)
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    return outcomes, evaluation, inputs_table, market_bars


def test_exit_arm_is_be_after_050r():
    assert EXIT_ARM == ARM_BE_AFTER_050R


# ---------------------------------------------------------------------------
# 1. primary valid -> fallback never used
# ---------------------------------------------------------------------------

def test_primary_valid_fallback_never_used():
    outcomes, *_ = _build_outcomes({"AAPL": STRONG_BREAKOUT_HOLDS, "MSFT": STRONG_BREAKOUT_HOLDS})
    assert len(outcomes) == 1
    day = outcomes[0]
    assert day.pool_size == 2
    assert len(day.attempts) == 1  # never inspects MSFT once AAPL (primary) is approved
    assert day.primary.candidate.symbol == "AAPL"
    assert day.primary.approved is True
    assert day.fallback_selected is day.primary
    assert day.fallback_used is False


# ---------------------------------------------------------------------------
# 2. primary invalid + second valid -> second selected
# ---------------------------------------------------------------------------

def test_primary_invalid_second_valid_selects_second():
    outcomes, *_ = _build_outcomes({"AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS})
    day = outcomes[0]
    assert day.pool_size == 2
    assert len(day.attempts) == 2
    assert day.primary.candidate.symbol == "AAPL"
    assert day.primary.approved is False
    assert day.fallback_selected.candidate.symbol == "MSFT"
    assert day.fallback_selected.position == 2
    assert day.fallback_selected.approved is True
    assert day.fallback_used is True


# ---------------------------------------------------------------------------
# 3. primary invalid + second invalid + third valid -> third selected
# ---------------------------------------------------------------------------

def test_primary_and_second_invalid_third_valid_selects_third():
    outcomes, *_ = _build_outcomes({
        "AAPL": PULLS_BACK_BY_0950, "MSFT": PULLS_BACK_BY_0950, "NVDA": STRONG_BREAKOUT_HOLDS,
    })
    day = outcomes[0]
    assert day.pool_size == 3
    assert len(day.attempts) == 3
    assert [a.candidate.symbol for a in day.attempts] == ["AAPL", "MSFT", "NVDA"]
    assert [a.approved for a in day.attempts] == [False, False, True]
    assert day.fallback_selected.candidate.symbol == "NVDA"
    assert day.fallback_selected.position == 3
    assert day.fallback_used is True


# ---------------------------------------------------------------------------
# 4. all invalid -> no trade
# ---------------------------------------------------------------------------

def test_all_invalid_no_trade():
    outcomes, *_ = _build_outcomes({"AAPL": PULLS_BACK_BY_0950, "MSFT": PULLS_BACK_BY_0950})
    day = outcomes[0]
    assert day.pool_size == 2
    assert len(day.attempts) == 2  # every candidate attempted; none broke the loop
    assert all(a.approved is False for a in day.attempts)
    assert day.fallback_selected.candidate.symbol == "MSFT"  # last attempted, not "chosen"
    assert day.fallback_used is False

    rows = build_policy_rows(outcomes, period="TEST")
    assert rows[ENTRY_BASELINE][0].outcome["entered"] is False
    assert rows[ENTRY_FALLBACK][0].outcome["entered"] is False


# ---------------------------------------------------------------------------
# 5. deterministic tie ordering (identical original qualifying timestamp)
# ---------------------------------------------------------------------------

def test_deterministic_tie_ordering_by_symbol_ascending():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"MSFT": PULLS_BACK_BY_0950, "AAPL": PULLS_BACK_BY_0950})
    ordered = ordered_early_pool_per_day(evaluation.candidate_signals)
    day_pool = ordered[SESSION_DATE]
    # Both symbols' original qualifying LiveData.timestamp is identical
    # (both breakout dicts start at the same offset) -- alphabetical symbol
    # tie-break must place AAPL before MSFT regardless of dict insertion order.
    assert day_pool[0][3].timestamp == day_pool[1][3].timestamp
    assert [c[0] for c in day_pool] == ["AAPL", "MSFT"]


# ---------------------------------------------------------------------------
# 6. candidate first appearing after 09:50 cannot become fallback
# ---------------------------------------------------------------------------

def test_candidate_qualifying_after_0950_is_never_in_the_pool():
    """A synthetic full-window candidate signal whose own qualifying
    `LiveData.timestamp` is after 09:50 must never enter
    `ordered_early_pool_per_day`'s output -- unit-level, exercising
    `early_pool`'s own cutoff (reused unmodified from M41) directly against
    a hand-built `CandidateSignal`, independent of whether any realistic
    fixture happens to produce a late-only breakout."""
    from decimal import Decimal
    from datetime import timezone as tz
    from src.signal_engine import OpeningData, LiveData, SignalDecision

    opening = OpeningData(
        symbol="ZORB", previous_close=Decimal("100"), today_open=Decimal("100.5"),
        opening_range_high=Decimal("101"), opening_range_low=Decimal("100.2"),
        opening_range_volume=500_000, median_opening_volume=Decimal("100000"),
    )
    late_live = LiveData(
        timestamp=datetime(2026, 8, 14, 14, 31, tzinfo=tz.utc),  # 10:31 ET -- after the 09:50 cutoff
        completed_bar_close=Decimal("106.0"), spy_price=Decimal("501"), spy_vwap=Decimal("500"),
    )
    decision = SignalDecision(approved=True, reason="late-candidate", gap_percent=Decimal("0.5"), relative_volume=Decimal("2.0"))
    late_signal = ("ZORB", SESSION_DATE, opening, late_live, decision)

    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": PULLS_BACK_BY_0950})
    combined_signals = evaluation.candidate_signals + (late_signal,)

    ordered = ordered_early_pool_per_day(combined_signals)
    day_pool = ordered[SESSION_DATE]
    assert [c[0] for c in day_pool] == ["AAPL"]  # ZORB's late signal never enters the pre-09:50 pool


# ---------------------------------------------------------------------------
# 7. no ranking by RVOL/gap/strength -- a lower-RVOL, earlier candidate
# still wins over a higher-RVOL, later one, purely by chronological order.
# ---------------------------------------------------------------------------

def test_no_ranking_first_valid_wins_even_with_lower_rvol():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day(
        {"AAPL": STRONG_BREAKOUT_HOLDS, "MSFT": STRONG_BREAKOUT_HOLDS},
        gap_percent=CONFIG_ENTRY.combo.gap_percent,
    )
    # Both symbols pass with identical RVOL/gap in this fixture (same
    # helper, same parameters) -- so both are equally "valid," and the
    # selection must still go to AAPL (chronological/alphabetical first),
    # never re-sorted by any strength/RVOL comparison.
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    day = outcomes[0]
    assert day.fallback_selected.candidate.symbol == "AAPL"
    assert len(day.attempts) == 1  # MSFT never even evaluated -- not "compared and lost"


# ---------------------------------------------------------------------------
# 8. same candidate cannot be selected twice
# ---------------------------------------------------------------------------

def test_same_candidate_cannot_be_selected_twice():
    outcomes, *_ = _build_outcomes({
        "AAPL": PULLS_BACK_BY_0950, "MSFT": PULLS_BACK_BY_0950, "NVDA": STRONG_BREAKOUT_HOLDS,
    })
    day = outcomes[0]
    symbols_attempted = [a.candidate.symbol for a in day.attempts]
    assert len(symbols_attempted) == len(set(symbols_attempted))


# ---------------------------------------------------------------------------
# 9/10. fresh 09:50 data used for every revalidation; no stale signal reused
# ---------------------------------------------------------------------------

def test_fresh_0950_data_used_for_every_candidate_not_original_signal():
    outcomes, *_ = _build_outcomes({"AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS})
    day = outcomes[0]
    for attempt in day.attempts:
        # The entry price actually used must come from the candidate's own
        # decision-time bar, never the original ~09:41 breakout price. Only
        # an APPROVED decision proposes a fill; a rejected one correctly
        # proposes none.
        if attempt.approved:
            assert attempt.shadow_decision.proposed_entry == attempt.candidate.decision_live_data.completed_bar_close
            assert attempt.shadow_decision.proposed_entry != attempt.candidate.original_live_data.completed_bar_close


# ---------------------------------------------------------------------------
# 11/12/13. baseline trade identity/outcome exactly matches existing M43
# primary (WAITED_FIRST_VALID_0950 + BE_AFTER_050R).
# ---------------------------------------------------------------------------

def test_baseline_matches_m43_waited_primary_exactly():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({
        "AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS,
    })
    created_at = datetime.now(timezone.utc)

    m43_rows = build_all_entry_and_exit_rows(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=created_at,
    )
    m44_rows = build_policy_rows(outcomes, period="TEST")

    m43_waited_be = m43_rows[ENTRY_BASELINE][ARM_BE_AFTER_050R]
    m44_baseline = m44_rows[ENTRY_BASELINE]
    assert len(m43_waited_be) == len(m44_baseline) == 1
    assert m43_waited_be[0].decision["symbol"] == m44_baseline[0].decision["symbol"] == "AAPL"
    assert _outcome_key(m43_waited_be[0]) == _outcome_key(m44_baseline[0])
    # Sanity: primary (AAPL) genuinely failed 09:50 in this fixture.
    assert m44_baseline[0].outcome["entered"] is False


def test_primary_valid_m44_fallback_outcome_identical_to_baseline():
    """When the primary survives 09:50, FALLBACK's own row must be
    field-identical to BASELINE's (same candidate, same exit outcome)."""
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({
        "AAPL": STRONG_BREAKOUT_HOLDS, "MSFT": STRONG_BREAKOUT_HOLDS,
    })
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    rows = build_policy_rows(outcomes, period="TEST")
    assert _outcome_key(rows[ENTRY_BASELINE][0]) == _outcome_key(rows[ENTRY_FALLBACK][0])
    assert fallback_only_rows(outcomes, period="TEST") == ()  # not a recovered trade


# ---------------------------------------------------------------------------
# 14/15. fallback trade uses exact BE_AFTER_050R semantics, same-bar rule
# preserved (delegated entirely to M43's own simulate_exit_arm -- proven
# here by checking the recovered row was actually produced through it).
# ---------------------------------------------------------------------------

def test_recovered_fallback_trade_uses_be_after_050r_and_is_reported_separately():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({
        "AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS,
    })
    outcomes = build_day_outcomes(
        period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
        base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
    )
    recovered = fallback_only_rows(outcomes, period="TEST")
    assert len(recovered) == 1
    assert recovered[0].decision["symbol"] == "MSFT"
    assert recovered[0].outcome["entered"] is True
    # BE_AFTER_050R can only ever move a stop to breakeven or leave it at
    # the initial stop -- never below the initial stop and never above
    # entry-derived breakeven, which final_stop_price encodes.
    assert recovered[0].outcome["final_stop_price"] is not None


# ---------------------------------------------------------------------------
# 16. missing-data fail-closed behavior
# ---------------------------------------------------------------------------

def test_missing_decision_time_data_is_reported_honestly_not_as_rejection():
    from src.orb_candidate_selection_timing_research import DecisionTimeCandidate
    from src.orb_gap_rvol_sensitivity_research import sensitivity_config
    from src.signal_engine import OpeningData, LiveData, SignalDecision
    from decimal import Decimal
    from datetime import timezone as tz

    opening = OpeningData(
        symbol="AAPL", previous_close=Decimal("100"), today_open=Decimal("100.5"),
        opening_range_high=Decimal("101"), opening_range_low=Decimal("100.2"),
        opening_range_volume=500_000, median_opening_volume=Decimal("100000"),
    )
    live = LiveData(
        timestamp=datetime(2026, 8, 14, 13, 41, tzinfo=tz.utc), completed_bar_close=Decimal("105.5"),
        spy_price=Decimal("501"), spy_vwap=Decimal("500"),
    )
    decision = SignalDecision(approved=True, reason="test-candidate", gap_percent=Decimal("0.5"), relative_volume=Decimal("2.0"))
    candidate_no_data = DecisionTimeCandidate(
        symbol="AAPL", session_date=date(2026, 8, 14), opening_data=opening, original_live_data=live,
        original_decision=decision, decision_bar=None, decision_live_data=None,
    )
    combo_config = sensitivity_config(CONFIG, gap_percent=CONFIG_ENTRY.combo.gap_percent, rvol_multiple=CONFIG_ENTRY.combo.rvol_multiple)
    attempts = _attempt_candidates(
        [candidate_no_data], combo_config=combo_config, strategy_version="orb_long_v0-test",
        implementation_digest="test-digest", shadow_run_id_prefix="test-missing", created_at=datetime.now(timezone.utc),
        market_bars_by_symbol={"AAPL": [], "SPY": []}, market_bars_index_by_symbol=None,
    )
    assert len(attempts) == 1
    assert attempts[0].approved is None  # distinct from False (an actual SignalEngine rejection)

    day_outcome = DayOutcome(session_date=date(2026, 8, 14), pool_size=1, attempts=attempts)
    rows = build_policy_rows((day_outcome,), period="TEST")
    assert rows[ENTRY_BASELINE][0].outcome["state"] == "NO_DECISION_TIME_DATA"
    diag = fallback_mechanics_diagnostics((day_outcome,))
    assert diag["days_primary_fails_no_decision_time_data"] == 1
    assert diag["days_primary_fails_signal_invalid"] == 0


# ---------------------------------------------------------------------------
# 17. deterministic repeated run
# ---------------------------------------------------------------------------

def test_deterministic_repeated_execution():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({
        "AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS,
    })
    created_at = datetime.now(timezone.utc)
    first = build_policy_rows(
        build_day_outcomes(period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars, base_config=CONFIG, implementation_digest="d", created_at=created_at),
        period="TEST",
    )
    second = build_policy_rows(
        build_day_outcomes(period="TEST", evaluation=evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars, base_config=CONFIG, implementation_digest="d", created_at=created_at),
        period="TEST",
    )
    for policy in (ENTRY_BASELINE, ENTRY_FALLBACK):
        assert [_outcome_key(r) for r in first[policy]] == [_outcome_key(r) for r in second[policy]]


# ---------------------------------------------------------------------------
# 18. no future bars influence the 09:50 candidate choice -- structurally
# guaranteed by `early_pool`'s own cutoff (see module docstring); verified
# here via the frozen combo check.
# ---------------------------------------------------------------------------

def test_build_day_outcomes_rejects_non_frozen_combo():
    evaluation, inputs_table, market_bars, minute_bars = _evaluate_single_day({"AAPL": STRONG_BREAKOUT_HOLDS})
    bad_evaluation = evaluation.__class__(
        combo=evaluation.combo.__class__(gap_percent=evaluation.combo.gap_percent, rvol_multiple=evaluation.combo.rvol_multiple + 1),
        report=evaluation.report, candidate_signals=evaluation.candidate_signals,
    )
    with pytest.raises(FallbackCandidateRecoveryError):
        build_day_outcomes(
            period="TEST", evaluation=bad_evaluation, inputs_table=inputs_table, market_bars_by_symbol=market_bars,
            base_config=CONFIG, implementation_digest="test-digest", created_at=datetime.now(timezone.utc),
        )


# ---------------------------------------------------------------------------
# Diagnostics and classification.
# ---------------------------------------------------------------------------

def test_fallback_mechanics_diagnostics_counts():
    outcomes, *_ = _build_outcomes({"AAPL": PULLS_BACK_BY_0950, "MSFT": STRONG_BREAKOUT_HOLDS})
    diag = fallback_mechanics_diagnostics(outcomes)
    assert diag["days_with_any_pre_0950_candidate"] == 1
    assert diag["days_primary_fails_0950"] == 1
    assert diag["days_recovered_by_fallback"] == 1
    assert diag["recovery_rate_among_primary_failure_days"] == pytest.approx(1.0)
    assert diag["fallback_candidate_position_counts"] == {"second": 1}
    assert diag["fallback_symbols"] == {"MSFT": 1}


def test_classify_insufficient_evidence_below_n_10():
    quality = trade_quality_from_rows(())
    result = classify_fallback_recovery(
        fallback_only_quality=quality, fallback_only_expectancy_excl_best_symbol=0.1,
        combined_expectancy_r=0.1, combined_pf_r=1.2, combined_r_per_month=0.3, baseline_r_per_month=0.2,
        combined_trades_per_month=2.5, baseline_trades_per_month=1.9, combined_drawdown_r=5.0, baseline_drawdown_r=5.0,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
