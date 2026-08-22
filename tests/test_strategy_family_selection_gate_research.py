"""Tests for the M51 (Issue #354) frozen strategy-family selection
protocol: the four candidate families, the ten-criterion rubric, the
gating dimensions, and the deterministic selection function. Committed
before any evidence is scored -- these tests exercise the protocol's
own logic with synthetic scores, never the real M51 research
conclusion (that lives in a later, separate results module/commit)."""
from __future__ import annotations

import pytest

from src.strategy_family_selection_gate_research import (
    CANDIDATE_FAMILIES,
    CLASSIFICATION_NO_CREDIBLE_CANDIDATE,
    CLASSIFICATION_SELECTED_FOR_M52,
    ECONOMICS_GATE_CRITERIA_IDS,
    FAMILY_MOMENTUM,
    FAMILY_OPTION_PREMIUM,
    FAMILY_PEAD,
    FAMILY_TREND_FOLLOWING,
    GATING_CRITERIA_IDS,
    M49_REFERENCE_RANGE,
    PRACTICAL_TARGET_MONTHLY_DOLLARS,
    SELECTION_RUBRIC,
    STARTING_CAPITAL,
    STRETCH_TARGET_MONTHLY_DOLLARS,
    VERDICT_FAIL,
    VERDICT_PASS,
    CriterionScore,
    evaluate_family_gates,
    select_family,
)


def test_frozen_targets_match_issue_354():
    assert STARTING_CAPITAL == 30_000
    assert PRACTICAL_TARGET_MONTHLY_DOLLARS == 500
    assert STRETCH_TARGET_MONTHLY_DOLLARS == 1_000


def test_m49_reference_range_is_labeled_frozen_and_not_reopened():
    assert M49_REFERENCE_RANGE["classification"] == "SAFE_BUT_BELOW_TARGET"
    assert M49_REFERENCE_RANGE["base_mean_monthly_dollars"] == "184.75"
    assert "not reopen" in M49_REFERENCE_RANGE["note"]


def test_exactly_four_candidate_families_no_more_no_less():
    assert len(CANDIDATE_FAMILIES) == 4
    assert set(CANDIDATE_FAMILIES) == {FAMILY_MOMENTUM, FAMILY_PEAD, FAMILY_OPTION_PREMIUM, FAMILY_TREND_FOLLOWING}


def test_rubric_has_exactly_ten_criteria_matching_issue_354():
    assert len(SELECTION_RUBRIC) == 10
    ids = [c.id for c in SELECTION_RUBRIC]
    assert len(ids) == len(set(ids))  # no duplicates
    assert ids == [
        "evidence_strength", "data_availability_cost", "survivorship_lookahead_risk",
        "account_size_feasibility", "realistic_costs", "monthly_dollar_range",
        "drawdown_tail_risk", "repo_robinhood_compatibility", "engineering_effort",
        "target_plausibility",
    ]


def test_gating_and_economics_criteria_ids_are_valid_rubric_members():
    rubric_ids = {c.id for c in SELECTION_RUBRIC}
    assert set(GATING_CRITERIA_IDS) <= rubric_ids
    assert set(ECONOMICS_GATE_CRITERIA_IDS) <= rubric_ids
    assert set(GATING_CRITERIA_IDS).isdisjoint(ECONOMICS_GATE_CRITERIA_IDS)


def test_criterion_score_rejects_invalid_verdict():
    with pytest.raises(ValueError):
        CriterionScore(family=FAMILY_MOMENTUM, criterion_id="evidence_strength", verdict="MAYBE", reasoning="x")


def test_criterion_score_rejects_unknown_criterion_id():
    with pytest.raises(ValueError):
        CriterionScore(family=FAMILY_MOMENTUM, criterion_id="not_a_real_criterion", verdict=VERDICT_PASS, reasoning="x")


def test_criterion_score_rejects_unknown_family():
    with pytest.raises(ValueError):
        CriterionScore(family="NOT_A_FAMILY", criterion_id="evidence_strength", verdict=VERDICT_PASS, reasoning="x")


def _all_pass_scores(family: str) -> tuple[CriterionScore, ...]:
    return tuple(CriterionScore(family=family, criterion_id=c.id, verdict=VERDICT_PASS, reasoning="synthetic") for c in SELECTION_RUBRIC)


def test_evaluate_family_gates_all_pass_when_every_criterion_passes():
    scores = _all_pass_scores(FAMILY_MOMENTUM)
    result = evaluate_family_gates(scores, family=FAMILY_MOMENTUM)
    assert result.all_gates_pass is True
    assert result.evidence_gate_pass is True
    assert result.data_gate_pass is True
    assert result.account_size_gate_pass is True
    assert result.economics_gate_pass is True
    assert result.total_pass_count == 10


def test_evaluate_family_gates_fails_closed_on_missing_score():
    # Only 9 of 10 criteria scored -- the missing one (evidence_strength)
    # must count as FAIL, not be silently skipped.
    scores = tuple(CriterionScore(family=FAMILY_MOMENTUM, criterion_id=c.id, verdict=VERDICT_PASS, reasoning="synthetic") for c in SELECTION_RUBRIC if c.id != "evidence_strength")
    result = evaluate_family_gates(scores, family=FAMILY_MOMENTUM)
    assert result.evidence_gate_pass is False
    assert result.all_gates_pass is False
    assert result.total_pass_count == 9


def test_evaluate_family_gates_economics_gate_fails_if_any_one_of_three_fails():
    base = list(_all_pass_scores(FAMILY_MOMENTUM))
    idx = next(i for i, s in enumerate(base) if s.criterion_id == "target_plausibility")
    base[idx] = CriterionScore(family=FAMILY_MOMENTUM, criterion_id="target_plausibility", verdict=VERDICT_FAIL, reasoning="synthetic failure")
    result = evaluate_family_gates(tuple(base), family=FAMILY_MOMENTUM)
    assert result.economics_gate_pass is False
    assert result.all_gates_pass is False
    # the other three gates are unaffected
    assert result.evidence_gate_pass is True
    assert result.data_gate_pass is True
    assert result.account_size_gate_pass is True


def test_evaluate_family_gates_only_scores_the_requested_family():
    scores = _all_pass_scores(FAMILY_MOMENTUM) + (
        CriterionScore(family=FAMILY_PEAD, criterion_id="evidence_strength", verdict=VERDICT_FAIL, reasoning="synthetic"),
    )
    result = evaluate_family_gates(scores, family=FAMILY_MOMENTUM)
    assert result.all_gates_pass is True  # unaffected by FAMILY_PEAD's failing score


def test_select_family_returns_no_credible_candidate_when_zero_families_pass_all_gates():
    scores = tuple(
        CriterionScore(family=f, criterion_id="evidence_strength", verdict=VERDICT_FAIL, reasoning="synthetic")
        for f in CANDIDATE_FAMILIES
    )
    result = select_family(scores)
    assert result.classification == CLASSIFICATION_NO_CREDIBLE_CANDIDATE
    assert result.selected_family is None
    assert result.eligible_families == ()


def test_select_family_selects_the_single_eligible_family():
    scores = _all_pass_scores(FAMILY_PEAD)
    for f in CANDIDATE_FAMILIES:
        if f != FAMILY_PEAD:
            scores += (CriterionScore(family=f, criterion_id="evidence_strength", verdict=VERDICT_FAIL, reasoning="synthetic"),)
    result = select_family(scores)
    assert result.classification == CLASSIFICATION_SELECTED_FOR_M52
    assert result.selected_family == FAMILY_PEAD
    assert result.eligible_families == (FAMILY_PEAD,)
    assert result.tiebreak_used is False


def test_select_family_breaks_ties_deterministically_by_total_pass_count_then_declaration_order():
    # FAMILY_OPTION_PREMIUM passes all gates and all 10 criteria;
    # FAMILY_TREND_FOLLOWING passes all gates but only 8 of 10 criteria
    # (two non-gating criteria fail) -- OPTION_PREMIUM must win.
    scores = _all_pass_scores(FAMILY_OPTION_PREMIUM)
    trend_scores = list(_all_pass_scores(FAMILY_TREND_FOLLOWING))
    for i, s in enumerate(trend_scores):
        if s.criterion_id in ("survivorship_lookahead_risk", "engineering_effort"):
            trend_scores[i] = CriterionScore(family=FAMILY_TREND_FOLLOWING, criterion_id=s.criterion_id, verdict=VERDICT_FAIL, reasoning="synthetic")
    scores += tuple(trend_scores)
    result = select_family(scores)
    assert result.classification == CLASSIFICATION_SELECTED_FOR_M52
    assert set(result.eligible_families) == {FAMILY_OPTION_PREMIUM, FAMILY_TREND_FOLLOWING}
    assert result.selected_family == FAMILY_OPTION_PREMIUM
    assert result.tiebreak_used is True


def test_select_family_never_selects_more_than_one_family():
    scores = _all_pass_scores(FAMILY_MOMENTUM) + _all_pass_scores(FAMILY_PEAD) + _all_pass_scores(FAMILY_OPTION_PREMIUM) + _all_pass_scores(FAMILY_TREND_FOLLOWING)
    result = select_family(scores)
    assert result.classification == CLASSIFICATION_SELECTED_FOR_M52
    assert result.selected_family in CANDIDATE_FAMILIES
    assert isinstance(result.selected_family, str)  # exactly one, not a list/tuple
