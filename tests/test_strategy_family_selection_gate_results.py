"""Tests for the M51 (Issue #354) results module: completeness of the
evidence table and the ten-criterion scores, and the derived
classification/selection -- pinned so a future edit cannot silently
change the recorded M51 conclusion without a visible test failure."""
from __future__ import annotations

from src.strategy_family_selection_gate_research import (
    CANDIDATE_FAMILIES,
    CLASSIFICATION_SELECTED_FOR_M52,
    FAMILY_MOMENTUM,
    FAMILY_OPTION_PREMIUM,
    FAMILY_PEAD,
    FAMILY_TREND_FOLLOWING,
    SELECTION_RUBRIC,
)
from src.strategy_family_selection_gate_results import CRITERION_SCORES, EVIDENCE_SOURCES, M51_SELECTION_RESULT


def test_every_family_has_at_least_one_evidence_source():
    families_with_evidence = {e.family for e in EVIDENCE_SOURCES}
    assert families_with_evidence == set(CANDIDATE_FAMILIES)


def test_every_evidence_source_has_a_real_looking_citation_and_url():
    for e in EVIDENCE_SOURCES:
        assert e.citation.strip() != ""
        assert e.url.strip() != ""
        assert e.key_finding.strip() != ""
        assert e.source_type in {
            "peer_reviewed_paper", "exchange_regulatory_filing", "official_broker_provider_doc", "reproducible_dataset",
        }


def test_every_family_has_exactly_one_score_per_rubric_criterion():
    rubric_ids = {c.id for c in SELECTION_RUBRIC}
    for family in CANDIDATE_FAMILIES:
        scored_ids = [s.criterion_id for s in CRITERION_SCORES if s.family == family]
        assert set(scored_ids) == rubric_ids, f"{family} is missing or duplicates a criterion"
        assert len(scored_ids) == len(rubric_ids)


def test_every_score_has_nonempty_reasoning():
    for s in CRITERION_SCORES:
        assert s.reasoning.strip() != "", f"{s.family}/{s.criterion_id} has empty reasoning"


def test_total_score_count_is_four_families_times_ten_criteria():
    assert len(CRITERION_SCORES) == len(CANDIDATE_FAMILIES) * len(SELECTION_RUBRIC)


# ---------------------------------------------------------------------------
# The derived M51 conclusion, pinned.
# ---------------------------------------------------------------------------


def test_m51_selects_concentrated_momentum_for_m52():
    assert M51_SELECTION_RESULT.classification == CLASSIFICATION_SELECTED_FOR_M52
    assert M51_SELECTION_RESULT.selected_family == FAMILY_MOMENTUM


def test_m51_eligible_families_are_momentum_and_option_premium():
    assert set(M51_SELECTION_RESULT.eligible_families) == {FAMILY_MOMENTUM, FAMILY_OPTION_PREMIUM}


def test_m51_pead_is_not_eligible_due_to_data_and_economics_gates():
    gate = M51_SELECTION_RESULT.gate_results_by_family[FAMILY_PEAD]
    assert gate.all_gates_pass is False
    assert gate.data_gate_pass is False
    assert gate.economics_gate_pass is False
    assert gate.evidence_gate_pass is True  # the academic evidence itself is strong
    assert gate.account_size_gate_pass is True


def test_m51_trend_following_is_not_eligible_due_to_economics_gate_only():
    gate = M51_SELECTION_RESULT.gate_results_by_family[FAMILY_TREND_FOLLOWING]
    assert gate.all_gates_pass is False
    assert gate.economics_gate_pass is False
    assert gate.evidence_gate_pass is True
    assert gate.data_gate_pass is True
    assert gate.account_size_gate_pass is True


def test_m51_tiebreak_was_used_between_momentum_and_option_premium():
    assert M51_SELECTION_RESULT.tiebreak_used is True
    momentum_gate = M51_SELECTION_RESULT.gate_results_by_family[FAMILY_MOMENTUM]
    option_gate = M51_SELECTION_RESULT.gate_results_by_family[FAMILY_OPTION_PREMIUM]
    assert momentum_gate.total_pass_count > option_gate.total_pass_count


def test_m51_option_premium_fails_robinhood_compatibility_and_engineering_effort_only():
    scores_by_criterion = {s.criterion_id: s.verdict for s in CRITERION_SCORES if s.family == FAMILY_OPTION_PREMIUM}
    failing = {cid for cid, v in scores_by_criterion.items() if v == "FAIL"}
    assert failing == {"repo_robinhood_compatibility", "engineering_effort"}
