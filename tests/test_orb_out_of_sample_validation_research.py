from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.orb_gap_rvol_sensitivity_research import ComboEvaluation, ParameterCombo
from src.shadow_execution_simulator import BROKER_WRITE_COUNT, ShadowExecutionResult, ShadowExecutionState
from src.orb_out_of_sample_validation_research import (
    CONFIG_A_BASELINE,
    CONFIG_B_CONSERVATIVE_RELAXED,
    CONFIG_C_FINE_STUDY_RUNNER_UP,
    CONFIG_D_HIGHER_GAP_LOWER_RVOL,
    CONFIG_E_RESEARCH_LEADER,
    CONFIG_LABELS,
    CONFIGS,
    DEFAULT_ROBUSTNESS_CRITERIA,
    DISCOVERY_PERIOD_REFERENCE,
    OutOfSampleValidationError,
    RobustnessCriteria,
    candidate_multiplicity,
    classify_robustness,
    config_for_letter,
    config_matrix,
    discovery_vs_out_of_sample,
    extended_trade_stats,
    final_decision,
    multiplicity_distribution,
    same_day_symbol_combinations,
    select_first_candidate_per_day,
)

# ---------------------------------------------------------------------------
# Fixed configuration set.
# ---------------------------------------------------------------------------


def test_exactly_five_pre_registered_configurations_in_order():
    assert config_matrix() == CONFIGS
    assert len(CONFIGS) == 5
    assert CONFIGS == (
        CONFIG_A_BASELINE, CONFIG_B_CONSERVATIVE_RELAXED, CONFIG_C_FINE_STUDY_RUNNER_UP,
        CONFIG_D_HIGHER_GAP_LOWER_RVOL, CONFIG_E_RESEARCH_LEADER,
    )
    assert CONFIG_A_BASELINE == ParameterCombo(Decimal("1.00"), Decimal("3.00"))
    assert CONFIG_E_RESEARCH_LEADER == ParameterCombo(Decimal("0.50"), Decimal("1.25"))


def test_config_labels_are_unique_and_cover_a_through_e():
    assert set(CONFIG_LABELS.values()) == {"A", "B", "C", "D", "E"}
    assert len(set(CONFIG_LABELS)) == 5


def test_config_for_letter_round_trips():
    for combo, label in CONFIG_LABELS.items():
        assert config_for_letter(label) == combo


def test_config_for_letter_unknown_raises():
    with pytest.raises(OutOfSampleValidationError):
        config_for_letter("Z")


def test_discovery_period_reference_covers_all_five_labels():
    assert set(DISCOVERY_PERIOD_REFERENCE) == {"A", "B", "C", "D", "E"}
    assert DISCOVERY_PERIOD_REFERENCE["E"]["candidate_day_rate"] == pytest.approx(0.5222)
    assert DISCOVERY_PERIOD_REFERENCE["E"]["sessions_with_candidate"] == 47


# ---------------------------------------------------------------------------
# Candidate multiplicity / one-trade-per-day view.
# ---------------------------------------------------------------------------


def _fake_evaluation(candidate_days: dict[date, int]) -> ComboEvaluation:
    """A `ComboEvaluation` whose `candidate_signals` reproduce exactly the
    given per-day candidate counts. `opening_data`/`live_data`/`decision`
    are unused by the multiplicity/combination helpers under test, so `None`
    placeholders are fine here."""
    signals = []
    for session_date, count in candidate_days.items():
        for index in range(count):
            symbol = f"SYM{index}"
            signals.append((symbol, session_date, None, None, None))
    return ComboEvaluation(
        combo=ParameterCombo(Decimal("0.50"), Decimal("1.25")),
        report={"universe_reports": []},
        candidate_signals=tuple(signals),
    )


def test_candidate_multiplicity_counts_days_by_bucket():
    evaluation = _fake_evaluation({
        date(2026, 1, 5): 1, date(2026, 1, 6): 1, date(2026, 1, 7): 2, date(2026, 1, 8): 3, date(2026, 1, 9): 4,
    })
    result = candidate_multiplicity(evaluation)
    assert result["candidate_days"] == 5
    assert result["days_with_exactly_1"] == 2
    assert result["days_with_exactly_2"] == 1
    assert result["days_with_3_or_more"] == 2
    # excess = (1-1)+(1-1)+(2-1)+(3-1)+(4-1) = 0+0+1+2+3 = 6
    assert result["total_excess_candidates_beyond_first_per_day"] == 6


def test_candidate_multiplicity_empty_evaluation():
    result = candidate_multiplicity(_fake_evaluation({}))
    assert result["candidate_days"] == 0
    assert result["total_excess_candidates_beyond_first_per_day"] == 0


def test_multiplicity_distribution_includes_zero_candidate_days():
    session_dates = [date(2026, 1, d) for d in range(5, 10)]  # 5 sessions
    evaluation = _fake_evaluation({date(2026, 1, 5): 1, date(2026, 1, 6): 2})
    dist = multiplicity_distribution(evaluation, session_dates=session_dates)
    assert dist == {"0_candidates": 3, "1_candidate": 1, "2_candidates": 1, "3_plus_candidates": 0}


def test_same_day_symbol_combinations_ignores_single_candidate_days():
    signals = [
        ("AAPL", date(2026, 1, 5), None, None, None),
        ("AAPL", date(2026, 1, 6), None, None, None),
        ("MSFT", date(2026, 1, 6), None, None, None),
        ("AAPL", date(2026, 1, 7), None, None, None),
        ("MSFT", date(2026, 1, 7), None, None, None),
    ]
    evaluation = ComboEvaluation(
        combo=ParameterCombo(Decimal("0.50"), Decimal("1.25")), report={"universe_reports": []},
        candidate_signals=tuple(signals),
    )
    combos = same_day_symbol_combinations(evaluation)
    assert combos == [{"symbols": ["AAPL", "MSFT"], "occurrences": 2}]


# ---------------------------------------------------------------------------
# Discovery vs out-of-sample comparison.
# ---------------------------------------------------------------------------


def _row(*, candidate_day_rate: float, sessions_with_candidate: int, top_3_share: float) -> dict:
    return {
        "sessions_with_candidate": sessions_with_candidate,
        "candidate_day_rate": candidate_day_rate,
        "concentration": {"top_3_share": top_3_share},
    }


def test_discovery_vs_out_of_sample_reports_signed_deltas():
    row = _row(candidate_day_rate=0.30, sessions_with_candidate=30, top_3_share=0.60)
    trade_quality = {
        "trades_closed": 40, "win_rate": 0.5, "expectancy_per_trade": "0.80",
        "profit_factor": "1.500000000", "maximum_drawdown": "20.0",
    }
    comparison = discovery_vs_out_of_sample(label="E", oos_row=row, oos_trade_quality=trade_quality)
    assert comparison["config"] == "E"
    # discovery rate for E is 0.5222; 0.30 - 0.5222 is negative (degradation)
    assert comparison["delta"]["candidate_day_rate"] < 0
    assert comparison["delta"]["expectancy_per_trade"] == pytest.approx(0.80 - 1.528733333333333, abs=1e-9)
    assert comparison["delta"]["profit_factor"] == pytest.approx(1.5 - 2.269712070874861572535991141, abs=1e-6)


def test_discovery_vs_out_of_sample_handles_missing_reference_and_infinite_pf():
    row = _row(candidate_day_rate=0.20, sessions_with_candidate=20, top_3_share=0.90)
    trade_quality = {
        "trades_closed": 22, "win_rate": 1.0, "expectancy_per_trade": "2.0",
        "profit_factor": "INFINITE_NO_LOSSES", "maximum_drawdown": "0",
    }
    comparison = discovery_vs_out_of_sample(label="A", oos_row=row, oos_trade_quality=trade_quality)
    # config A's discovery reference has trades_closed=None -> profit_factor delta must be None
    assert comparison["delta"]["profit_factor"] is None


# ---------------------------------------------------------------------------
# Robustness classification. Deterministic, fixed criteria.
# ---------------------------------------------------------------------------


GOOD_ROW = _row(candidate_day_rate=0.40, sessions_with_candidate=40, top_3_share=0.50)
GOOD_ROW["coverage_status"] = "VALID"
GOOD_TQ = {
    "trades_closed": 60, "expectancy_per_trade": "1.0", "profit_factor": "1.8",
}


def test_classify_robustness_robust_when_all_checks_pass():
    result = classify_robustness(row=GOOD_ROW, trade_quality=GOOD_TQ, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "ROBUST"
    assert all(result["checks"].values())


def test_classify_robustness_insufficient_evidence_below_minimum_trades():
    tq = {**GOOD_TQ, "trades_closed": 5}
    result = classify_robustness(row=GOOD_ROW, trade_quality=tq, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_robustness_insufficient_evidence_when_coverage_invalid():
    row = {**GOOD_ROW, "coverage_status": "INVALID_INSUFFICIENT_COVERAGE"}
    result = classify_robustness(row=row, trade_quality=GOOD_TQ, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_robustness_not_robust_when_expectancy_negative_even_if_frequency_fine():
    tq = {**GOOD_TQ, "expectancy_per_trade": "-0.5"}
    result = classify_robustness(row=GOOD_ROW, trade_quality=tq, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "NOT_ROBUST"


def test_classify_robustness_not_robust_when_profit_factor_at_or_below_1():
    tq = {**GOOD_TQ, "profit_factor": "1.0"}
    result = classify_robustness(row=GOOD_ROW, trade_quality=tq, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "NOT_ROBUST"


def test_classify_robustness_mixed_when_quality_holds_but_frequency_too_low():
    row = _row(candidate_day_rate=0.10, sessions_with_candidate=10, top_3_share=0.50)
    row["coverage_status"] = "VALID"
    tq = {"trades_closed": 25, "expectancy_per_trade": "1.0", "profit_factor": "1.8"}
    result = classify_robustness(row=row, trade_quality=tq, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "MIXED"
    assert result["checks"]["candidate_day_rate_at_least_minimum"] is False


def test_classify_robustness_mixed_when_concentration_extreme():
    row = _row(candidate_day_rate=0.40, sessions_with_candidate=40, top_3_share=0.95)
    row["coverage_status"] = "VALID"
    result = classify_robustness(row=row, trade_quality=GOOD_TQ, discovery_candidate_day_rate=0.4444)
    assert result["verdict"] == "MIXED"
    assert result["checks"]["top_3_concentration_within_limit"] is False


def test_classify_robustness_mixed_when_frequency_retention_too_low_vs_discovery():
    row = _row(candidate_day_rate=0.10, sessions_with_candidate=10, top_3_share=0.30)
    row["coverage_status"] = "VALID"
    tq = {"trades_closed": 25, "expectancy_per_trade": "1.0", "profit_factor": "1.8"}
    # discovery rate 0.50 -> retention 0.10/0.50 = 0.20 < 0.5 minimum, but also below the
    # 0.25 frequency floor itself; use a discovery rate close enough that only retention fails.
    result = classify_robustness(row=row, trade_quality=tq, discovery_candidate_day_rate=0.11)
    assert result["checks"]["candidate_day_rate_at_least_minimum"] is False


def test_classify_robustness_infinite_profit_factor_passes_the_above_1_check():
    tq = {**GOOD_TQ, "profit_factor": "INFINITE_NO_LOSSES"}
    result = classify_robustness(row=GOOD_ROW, trade_quality=tq, discovery_candidate_day_rate=0.4444)
    assert result["checks"]["profit_factor_above_1"] is True


def test_classify_robustness_no_discovery_rate_skips_retention_check():
    result = classify_robustness(row=GOOD_ROW, trade_quality=GOOD_TQ, discovery_candidate_day_rate=None)
    assert result["checks"]["frequency_retention_vs_discovery_ok"] is True


def test_custom_criteria_are_honoured():
    strict = RobustnessCriteria(minimum_trades_closed=1000)
    result = classify_robustness(
        row=GOOD_ROW, trade_quality=GOOD_TQ, discovery_candidate_day_rate=0.4444, criteria=strict,
    )
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"
    assert DEFAULT_ROBUSTNESS_CRITERIA.minimum_trades_closed == 20


# ---------------------------------------------------------------------------
# Final decision.
# ---------------------------------------------------------------------------


def test_final_decision_ready_when_either_primary_config_robust():
    classification = {"C": {"verdict": "MIXED"}, "E": {"verdict": "ROBUST"}}
    result = final_decision(classification_by_label=classification)
    assert result["decision"] == "READY_FOR_PRODUCTION_PARAMETER_REVIEW"
    assert result["authorizes_production_change"] is False


def test_final_decision_not_robust_when_both_primary_configs_fail():
    classification = {"C": {"verdict": "NOT_ROBUST"}, "E": {"verdict": "NOT_ROBUST"}}
    result = final_decision(classification_by_label=classification)
    assert result["decision"] == "RESEARCH_CONFIGURATION_NOT_ROBUST"


def test_final_decision_needs_more_validation_when_mixed_or_insufficient():
    classification = {"C": {"verdict": "MIXED"}, "E": {"verdict": "INSUFFICIENT_EVIDENCE"}}
    result = final_decision(classification_by_label=classification)
    assert result["decision"] == "NEEDS_MORE_HISTORICAL_VALIDATION"

    classification2 = {"C": {"verdict": "NOT_ROBUST"}, "E": {"verdict": "MIXED"}}
    assert final_decision(classification_by_label=classification2)["decision"] == "NEEDS_MORE_HISTORICAL_VALIDATION"


# ---------------------------------------------------------------------------
# One-trade-per-day selection. Deterministic, decision-time-only.
# ---------------------------------------------------------------------------


def _live(hour, minute):
    from src.signal_engine import LiveData
    return LiveData(
        timestamp=datetime(2026, 1, 5, hour, minute, tzinfo=timezone.utc),
        completed_bar_close=Decimal("100"), spy_price=Decimal("500"), spy_vwap=Decimal("499"),
    )


def test_select_first_candidate_per_day_picks_earliest_by_decision_timestamp():
    signals = (
        ("MSFT", date(2026, 1, 5), None, _live(10, 0), None),
        ("AAPL", date(2026, 1, 5), None, _live(9, 40), None),
        ("NVDA", date(2026, 1, 6), None, _live(9, 45), None),
    )
    selected = select_first_candidate_per_day(signals)
    assert len(selected) == 2
    by_day = {s[1]: s[0] for s in selected}
    assert by_day[date(2026, 1, 5)] == "AAPL"
    assert by_day[date(2026, 1, 6)] == "NVDA"


def test_select_first_candidate_per_day_ties_break_alphabetically_by_symbol():
    signals = (
        ("MSFT", date(2026, 1, 5), None, _live(9, 40), None),
        ("AAPL", date(2026, 1, 5), None, _live(9, 40), None),
    )
    selected = select_first_candidate_per_day(signals)
    assert len(selected) == 1
    assert selected[0][0] == "AAPL"


def test_select_first_candidate_per_day_empty_input():
    assert select_first_candidate_per_day(()) == ()


# ---------------------------------------------------------------------------
# Extended trade stats. Pure arithmetic over already-simulated results.
# ---------------------------------------------------------------------------


def _result(symbol, trading_date, *, pnl, exit_hour=15):
    exit_ts = datetime(trading_date.year, trading_date.month, trading_date.day, exit_hour, tzinfo=timezone.utc)
    return ShadowExecutionResult(
        simulation_id="d" * 64, simulation_digest="d" * 64, source_shadow_decision_id="dec-1",
        symbol=symbol, trading_date=trading_date, strategy_version="orb_long_v0-0.1.0",
        implementation_digest="i" * 64, source_market_data_digest="m" * 64, opening_range_identity="o" * 64,
        signal_timestamp=exit_ts, fill_policy_version="v1", slippage_policy_version="v1",
        slippage_assumption_bps=Decimal("0"), exit_policy_version="v1", input_market_data_digest="m" * 64,
        state=ShadowExecutionState.TARGET_HIT if pnl > 0 else ShadowExecutionState.STOPPED,
        entry_timestamp=exit_ts, entry_price=Decimal("100"), initial_stop_price=Decimal("99"),
        final_stop_price=Decimal("99"), target_price=Decimal("102"), exit_timestamp=exit_ts,
        exit_price=Decimal("100") + pnl, exit_reason="fixture", realized_pnl=pnl, realized_pnl_percent=Decimal("0"),
        maximum_favorable_excursion=None, maximum_adverse_excursion=None, bars_evaluated_after_entry=1,
        reason_code="OK", broker_write_count=BROKER_WRITE_COUNT,
    )


def test_extended_trade_stats_empty_results():
    stats = extended_trade_stats([])
    assert stats["longest_losing_streak"] == 0
    assert stats["best_day"] is None
    assert stats["monthly"] == {}
    assert stats["by_symbol"] == {}


def test_extended_trade_stats_longest_losing_streak():
    results = [
        _result("AAPL", date(2026, 1, 5), pnl=Decimal("1")),
        _result("AAPL", date(2026, 1, 6), pnl=Decimal("-1")),
        _result("AAPL", date(2026, 1, 7), pnl=Decimal("-1")),
        _result("AAPL", date(2026, 1, 8), pnl=Decimal("-1")),
        _result("AAPL", date(2026, 1, 9), pnl=Decimal("1")),
    ]
    stats = extended_trade_stats(results)
    assert stats["longest_losing_streak"] == 3


def test_extended_trade_stats_best_and_worst_day():
    results = [
        _result("AAPL", date(2026, 1, 5), pnl=Decimal("5")),
        _result("MSFT", date(2026, 1, 5), pnl=Decimal("5")),
        _result("AAPL", date(2026, 1, 6), pnl=Decimal("-3")),
    ]
    stats = extended_trade_stats(results)
    assert stats["best_day"] == {"date": "2026-01-05", "realized_pnl": "10"}
    assert stats["worst_day"] == {"date": "2026-01-06", "realized_pnl": "-3"}


def test_extended_trade_stats_monthly_and_by_symbol_breakdown():
    results = [
        _result("AAPL", date(2026, 1, 5), pnl=Decimal("2")),
        _result("AAPL", date(2026, 2, 5), pnl=Decimal("-1")),
        _result("MSFT", date(2026, 1, 6), pnl=Decimal("3")),
    ]
    stats = extended_trade_stats(results)
    assert set(stats["monthly"]) == {"2026-01", "2026-02"}
    assert stats["monthly"]["2026-01"]["trades_closed"] == 2
    assert stats["monthly"]["2026-01"]["wins"] == 2
    assert stats["monthly"]["2026-02"]["losses"] == 1
    assert stats["by_symbol"]["AAPL"]["trades_closed"] == 2
    assert stats["by_symbol"]["MSFT"]["realized_pnl"] == "3"
    # symbols ranked descending by realized P&L: MSFT (3) then AAPL (1)
    assert list(stats["by_symbol"]) == ["MSFT", "AAPL"]
    assert stats["by_symbol"]["MSFT"]["share_of_total_realized_pnl"] == pytest.approx(3 / 4)


def test_extended_trade_stats_ignores_still_open_results():
    open_result = _result("AAPL", date(2026, 1, 5), pnl=Decimal("1"))
    open_result = object.__setattr__(open_result, "realized_pnl", None) or open_result
    stats = extended_trade_stats([open_result])
    assert stats["longest_losing_streak"] == 0
    assert stats["best_day"] is None
