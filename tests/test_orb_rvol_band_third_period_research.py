"""Tests for the RVOL 1.50x-1.99x band third-period validation study
(Issue #317, `src/orb_rvol_band_third_period_research.py`).

Covers: exact band boundaries (>=1.50x and <2.00x, nothing else), that BAND
is always a strict post-hoc filter of BASE (no second engine run, no
neighboring-threshold search), the predeclared VALIDATED/MIXED/FAILED/
INSUFFICIENT_EVIDENCE classification and its six criteria, the final
BAND_VALIDATED_FOR_PARAMETER_REVIEW/BAND_NEEDS_MORE_VALIDATION/
BAND_NOT_VALIDATED decision rule, BASE-vs-BAND delta computation, and that
this module never reads or writes `config/strategy_v0.yaml` or touches
`strategy.enabled`.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from src.orb_gap_rvol_sensitivity_research import ParameterCombo
from src.orb_rvol_band_third_period_research import (
    CONFIG_B,
    CONFIG_C,
    DEFAULT_VALIDATION_CRITERIA,
    PRIOR_PERIOD_BAND_REFERENCE,
    RVOL_BAND_LOWER,
    RVOL_BAND_UPPER,
    ValidationCriteria,
    band_candidate_signals,
    band_effect,
    classify_band_validation,
    final_decision,
    in_band,
    three_period_comparison,
)

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Configuration identity: B/C must match the out-of-sample study's own
# lettering (0.75%/1.50x and 0.50%/1.50x respectively), not the regime
# diagnostic's differently-lettered A/B/C.
# ---------------------------------------------------------------------------


def test_config_b_is_gap_075_rvol_150():
    assert CONFIG_B == ParameterCombo(gap_percent=Decimal("0.75"), rvol_multiple=Decimal("1.50"))


def test_config_c_is_gap_050_rvol_150():
    assert CONFIG_C == ParameterCombo(gap_percent=Decimal("0.50"), rvol_multiple=Decimal("1.50"))


# ---------------------------------------------------------------------------
# Band boundary: exactly [1.50, 2.00) -- nothing else, no neighboring
# threshold search.
# ---------------------------------------------------------------------------


def test_band_bounds_are_exactly_150_and_200():
    assert RVOL_BAND_LOWER == Decimal("1.50")
    assert RVOL_BAND_UPPER == Decimal("2.00")


@pytest.mark.parametrize(
    "rvol,expected",
    [
        (Decimal("1.49"), False),
        (Decimal("1.499"), False),
        (Decimal("1.50"), True),
        (Decimal("1.75"), True),
        (Decimal("1.99"), True),
        (Decimal("1.999"), True),
        (Decimal("2.00"), False),
        (Decimal("2.01"), False),
        (Decimal("3.00"), False),
    ],
)
def test_in_band_boundary_is_lower_inclusive_upper_exclusive(rvol, expected):
    assert in_band(rvol) is expected


class _FakeDecision:
    def __init__(self, relative_volume: Decimal) -> None:
        self.relative_volume = relative_volume


def _fake_candidate(symbol: str, rvol: Decimal, *, session_date: date = date(2024, 6, 3)):
    return (symbol, session_date, None, None, _FakeDecision(rvol))


def test_band_candidate_signals_filters_to_exact_band():
    candidates = [
        _fake_candidate("AAA", Decimal("1.25")),
        _fake_candidate("BBB", Decimal("1.50")),
        _fake_candidate("CCC", Decimal("1.99")),
        _fake_candidate("DDD", Decimal("2.00")),
        _fake_candidate("EEE", Decimal("3.50")),
    ]
    filtered = band_candidate_signals(candidates)
    assert [c[0] for c in filtered] == ["BBB", "CCC"]


def test_band_candidate_signals_is_a_strict_subset_never_larger():
    candidates = [_fake_candidate(f"S{i}", Decimal(str(1.0 + i * 0.3))) for i in range(10)]
    filtered = band_candidate_signals(candidates)
    assert set(c[0] for c in filtered).issubset({c[0] for c in candidates})
    assert len(filtered) <= len(candidates)


def test_band_candidate_signals_empty_input_returns_empty():
    assert band_candidate_signals([]) == ()


# ---------------------------------------------------------------------------
# BAND effect: BASE/BAND/delta rows.
# ---------------------------------------------------------------------------


def _tq(**overrides):
    base = {
        "trades_closed": 40, "win_rate": 0.5, "expectancy_per_trade": "1.0", "profit_factor": "1.5",
        "cumulative_r_multiple": "10.0", "maximum_drawdown": "20.0", "stop_out_rate": 0.3,
    }
    base.update(overrides)
    return base


def _cell(**overrides):
    base = {"candidate_day_rate": 0.30, "trade_quality": _tq()}
    base.update(overrides)
    return base


def test_band_effect_reports_signed_deltas():
    base_cell = _cell(candidate_day_rate=0.30, trade_quality=_tq(trades_closed=40, expectancy_per_trade="1.0", profit_factor="1.5"))
    band_cell = _cell(candidate_day_rate=0.20, trade_quality=_tq(trades_closed=25, expectancy_per_trade="1.5", profit_factor="2.0"))
    effect = band_effect(base=base_cell, band=band_cell)
    assert effect["trade_count"]["delta"] == -15
    assert effect["expectancy_per_trade"]["delta"] == pytest.approx(0.5)
    assert effect["profit_factor"]["delta"] == pytest.approx(0.5)
    assert effect["candidate_day_rate"]["delta"] == pytest.approx(-0.10)


def test_band_effect_handles_infinite_profit_factor_as_none_delta():
    base_cell = _cell(trade_quality=_tq(profit_factor="1.5"))
    band_cell = _cell(trade_quality=_tq(profit_factor="INFINITE_NO_LOSSES"))
    effect = band_effect(base=base_cell, band=band_cell)
    assert effect["profit_factor"]["delta"] is None


def test_band_effect_handles_missing_trades_as_none_delta():
    base_cell = _cell(trade_quality=_tq(expectancy_per_trade=None, profit_factor=None))
    band_cell = _cell(trade_quality=_tq(expectancy_per_trade=None, profit_factor=None, trades_closed=0))
    effect = band_effect(base=base_cell, band=band_cell)
    assert effect["expectancy_per_trade"]["delta"] is None


# ---------------------------------------------------------------------------
# Predeclared classification. Criteria fixed before any third-period
# number is interpreted.
# ---------------------------------------------------------------------------


def _band_metrics(*, closed, win_rate, expectancy, pf, best_excl=True):
    return {
        "trade_quality": _tq(trades_closed=closed, win_rate=win_rate, expectancy_per_trade=str(expectancy), profit_factor=str(pf)),
        "concentration": {"profitable_excluding_best_symbol": best_excl},
    }


def _base_metrics(*, expectancy, pf):
    return {"trade_quality": _tq(expectancy_per_trade=str(expectancy), profit_factor=str(pf))}


GOOD_REFERENCE = {
    "discovery": {"expectancy_per_trade": 1.0}, "out_of_sample": {"expectancy_per_trade": 1.0},
}


def test_classify_band_validation_validated_when_all_six_criteria_pass():
    band = _band_metrics(closed=30, win_rate=0.5, expectancy=1.2, pf=1.8, best_excl=True)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "VALIDATED"
    assert all(result["checks"].values())


def test_classify_band_validation_insufficient_evidence_below_minimum_sample():
    band = _band_metrics(closed=5, win_rate=0.5, expectancy=1.2, pf=1.8)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


def test_classify_band_validation_failed_when_expectancy_negative():
    band = _band_metrics(closed=30, win_rate=0.4, expectancy=-0.5, pf=0.8)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "FAILED"


def test_classify_band_validation_failed_when_profit_factor_at_or_below_1():
    band = _band_metrics(closed=30, win_rate=0.4, expectancy=0.5, pf=1.0)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "FAILED"


def test_classify_band_validation_mixed_when_band_worse_than_base():
    band = _band_metrics(closed=30, win_rate=0.5, expectancy=0.5, pf=1.2)
    base = _base_metrics(expectancy=1.0, pf=1.8)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "MIXED"
    assert result["checks"]["band_not_materially_worse_than_base"] is False


def test_classify_band_validation_mixed_when_single_symbol_dependent():
    band = _band_metrics(closed=30, win_rate=0.5, expectancy=1.2, pf=1.8, best_excl=False)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE)
    assert result["verdict"] == "MIXED"
    assert result["checks"]["not_dependent_on_one_symbol"] is False


def test_classify_band_validation_mixed_when_prior_period_direction_inconsistent():
    band = _band_metrics(closed=30, win_rate=0.5, expectancy=1.2, pf=1.8)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    bad_reference = {"discovery": {"expectancy_per_trade": -0.5}, "out_of_sample": {"expectancy_per_trade": 1.0}}
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=bad_reference)
    assert result["verdict"] == "MIXED"
    assert result["checks"]["direction_consistent_with_prior_periods"] is False


def test_default_validation_criteria_minimum_is_15_closed_trades():
    assert DEFAULT_VALIDATION_CRITERIA.minimum_closed_trades == 15


def test_custom_criteria_are_honoured():
    band = _band_metrics(closed=10, win_rate=0.5, expectancy=1.2, pf=1.8)
    base = _base_metrics(expectancy=1.0, pf=1.5)
    strict = ValidationCriteria(minimum_closed_trades=1000)
    result = classify_band_validation(label="B", band_metrics=band, base_metrics=base, reference=GOOD_REFERENCE, criteria=strict)
    assert result["verdict"] == "INSUFFICIENT_EVIDENCE"


# ---------------------------------------------------------------------------
# Final decision.
# ---------------------------------------------------------------------------


def test_final_decision_validated_for_review_when_both_validated():
    classifications = {"B": {"verdict": "VALIDATED"}, "C": {"verdict": "VALIDATED"}}
    result = final_decision(classification_by_label=classifications)
    assert result["decision"] == "BAND_VALIDATED_FOR_PARAMETER_REVIEW"
    assert result["authorizes_production_change"] is False
    assert result["recommend_30k_capital_path_experiment_next"] is True


def test_final_decision_not_validated_when_failed_and_none_validated():
    classifications = {"B": {"verdict": "FAILED"}, "C": {"verdict": "FAILED"}}
    result = final_decision(classification_by_label=classifications)
    assert result["decision"] == "BAND_NOT_VALIDATED"
    assert result["recommend_30k_capital_path_experiment_next"] is False


def test_final_decision_needs_more_validation_when_mixed():
    classifications = {"B": {"verdict": "MIXED"}, "C": {"verdict": "VALIDATED"}}
    result = final_decision(classification_by_label=classifications)
    assert result["decision"] == "BAND_NEEDS_MORE_VALIDATION"
    assert result["recommend_30k_capital_path_experiment_next"] is False


def test_final_decision_needs_more_validation_when_insufficient_evidence():
    classifications = {"B": {"verdict": "INSUFFICIENT_EVIDENCE"}, "C": {"verdict": "VALIDATED"}}
    result = final_decision(classification_by_label=classifications)
    assert result["decision"] == "BAND_NEEDS_MORE_VALIDATION"


def test_final_decision_never_authorizes_production_change():
    for verdicts in (
        {"B": "VALIDATED", "C": "VALIDATED"}, {"B": "FAILED", "C": "FAILED"}, {"B": "MIXED", "C": "MIXED"},
    ):
        classifications = {label: {"verdict": v} for label, v in verdicts.items()}
        result = final_decision(classification_by_label=classifications)
        assert result["authorizes_production_change"] is False


# ---------------------------------------------------------------------------
# Prior-period reference: hardcoded, sourced from the merged #316 artifact.
# ---------------------------------------------------------------------------


def test_prior_period_reference_covers_both_configs_and_both_periods():
    assert set(PRIOR_PERIOD_BAND_REFERENCE) == {"B", "C"}
    for label in ("B", "C"):
        assert set(PRIOR_PERIOD_BAND_REFERENCE[label]) == {"discovery", "out_of_sample"}


def test_prior_period_reference_expectancy_is_positive_both_periods_both_configs():
    for label in ("B", "C"):
        for period in ("discovery", "out_of_sample"):
            assert PRIOR_PERIOD_BAND_REFERENCE[label][period]["expectancy_per_trade"] > 0


def test_three_period_comparison_includes_all_three_periods():
    band_metrics = {
        "trade_quality": _tq(trades_closed=50, win_rate=0.45, expectancy_per_trade="0.9", profit_factor="1.6", cumulative_r_multiple="5.0"),
        "concentration": {
            "top_3_share_of_total_pnl": 0.6, "best_contributing_symbol": "AAPL",
            "profitable_excluding_best_symbol": True, "profitable_excluding_tsla": True,
        },
    }
    comparison = three_period_comparison(label="B", third_period_band_metrics=band_metrics)
    assert comparison["config"] == "B"
    assert comparison["third_period"]["closed_trades"] == 50
    assert comparison["out_of_sample"]["closed_trades"] == 56
    assert comparison["discovery"]["closed_trades"] == 22


# ---------------------------------------------------------------------------
# No production config changes -- this module never reads or writes
# config/strategy_v0.yaml and never mentions strategy.enabled.
# ---------------------------------------------------------------------------


def test_module_never_touches_strategy_yaml_or_enabled_flag():
    source = (REPOSITORY_ROOT / "src" / "orb_rvol_band_third_period_research.py").read_text(encoding="utf-8")
    assert "strategy_v0.yaml" not in source
    assert "strategy.enabled" not in source
    assert '"enabled"' not in source


def test_script_never_writes_strategy_yaml_or_sets_enabled_flag():
    source = (REPOSITORY_ROOT / "scripts" / "research_orb_rvol_band_third_period_validation.py").read_text(encoding="utf-8")
    assert '["enabled"]' not in source
    assert "yaml.dump" not in source
    assert "yaml.safe_dump" not in source
