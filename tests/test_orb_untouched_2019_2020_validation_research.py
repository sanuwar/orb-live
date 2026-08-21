"""Tests for the M50 (Issue #352) Stage-0 provenance-gate module: the
frozen validation window, the frozen classification labels/thresholds
(copied verbatim from Issue #352), and the point-in-time ORB universe
evaluator built from the actually-recorded source-attempt investigation."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from src.orb_capital_target_strategy_family_research import EVALUATION_END as M48_EVALUATION_END
from src.orb_capital_target_strategy_family_research import EVALUATION_START as M48_EVALUATION_START
from src.orb_untouched_2019_2020_validation_research import (
    CLASSIFICATION_FAILED_VALIDATION,
    CLASSIFICATION_INDEPENDENTLY_VALIDATED,
    CLASSIFICATION_INSUFFICIENT_EVIDENCE,
    CLASSIFICATION_MIXED_VALIDATION,
    FIXED_SOURCE_ORDER,
    INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT,
    INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS,
    INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT,
    INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT,
    MIXED_VALIDATION_MAX_DRAWDOWN_PCT,
    M49_FROZEN_REFERENCE,
    OUTCOME_WINDOW_END,
    OUTCOME_WINDOW_START,
    REQUIRED_OUTCOME_MONTHS,
    STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS,
    STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS,
    SourceAttempt,
    stage0_orb_universe_established,
    run_stage0_gate,
)


def test_frozen_window_matches_issue_352():
    assert OUTCOME_WINDOW_START == date(2019, 1, 2)
    assert OUTCOME_WINDOW_END == date(2020, 12, 31)
    assert REQUIRED_OUTCOME_MONTHS == 24


def test_outcome_window_does_not_overlap_m48_m49_evaluation_window():
    assert OUTCOME_WINDOW_END < M48_EVALUATION_START
    assert OUTCOME_WINDOW_START < OUTCOME_WINDOW_END < M48_EVALUATION_START < M48_EVALUATION_END


def test_classification_labels_match_issue_352_exactly():
    assert CLASSIFICATION_INDEPENDENTLY_VALIDATED == "INDEPENDENTLY_VALIDATED"
    assert CLASSIFICATION_MIXED_VALIDATION == "MIXED_VALIDATION"
    assert CLASSIFICATION_FAILED_VALIDATION == "FAILED_VALIDATION"
    assert CLASSIFICATION_INSUFFICIENT_EVIDENCE == "INSUFFICIENT_EVIDENCE"


def test_independently_validated_thresholds_match_issue_352_exactly():
    assert INDEPENDENTLY_VALIDATED_MIN_MONTHLY_DOLLARS == Decimal("150")
    assert INDEPENDENTLY_VALIDATED_MAX_DRAWDOWN_PCT == Decimal("0.15")
    assert INDEPENDENTLY_VALIDATED_MIN_PROFITABLE_MONTH_PCT == Decimal("0.55")
    assert INDEPENDENTLY_VALIDATED_WORST_MONTH_FLOOR_PCT == Decimal("-0.10")
    assert MIXED_VALIDATION_MAX_DRAWDOWN_PCT == Decimal("0.20")


def test_fixed_source_order_has_exactly_three_tiers():
    assert len(FIXED_SOURCE_ORDER) == 3
    assert "official issuer" in FIXED_SOURCE_ORDER[0]
    assert "SEC EDGAR" in FIXED_SOURCE_ORDER[1]
    assert "another primary official source" in FIXED_SOURCE_ORDER[2]


def test_all_three_tiers_were_actually_attempted():
    tiers_attempted = {a.tier for a in STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS}
    assert tiers_attempted == {1, 2, 3}


def test_no_recorded_source_attempt_falsely_claims_a_complete_compliant_roster():
    # The real investigation never found one; this pins that fact so a
    # future edit cannot silently smuggle in a false positive.
    assert not any(
        a.outcome == "COMPLETE_ROSTER_ESTABLISHED_COMPLIANT" for a in STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS
    )


def test_stage0_orb_universe_established_is_false_given_real_recorded_evidence():
    assert stage0_orb_universe_established(STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS) is False


def test_stage0_orb_universe_established_is_a_real_evaluator_not_hardcoded():
    synthetic = STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS + (
        SourceAttempt(
            tier=1, source_name="synthetic compliant source", url="https://example.invalid",
            access_method="test", outcome="COMPLETE_ROSTER_ESTABLISHED_COMPLIANT", notes="synthetic",
        ),
    )
    assert stage0_orb_universe_established(synthetic) is True


def test_confirmed_djia_change_events_are_all_publication_compliant():
    assert len(STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS) >= 2
    for event in STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS:
        assert event.publication_compliant is True
        assert event.announcement_date <= event.effective_date


def test_run_stage0_gate_concludes_insufficient_evidence_from_real_evidence():
    result = run_stage0_gate()
    assert result.orb_universe_2019_established is False
    assert result.orb_universe_2020_established is False
    assert result.etf_universes_exempt is True
    assert result.classification == CLASSIFICATION_INSUFFICIENT_EVIDENCE
    assert "INSUFFICIENT_EVIDENCE" in result.blocker_reason
    assert len(result.blocker_reason) > 0
    assert result.source_attempts == STAGE0_ORB_UNIVERSE_SOURCE_ATTEMPTS
    assert result.confirmed_change_events == STAGE0_CONFIRMED_DJIA_CHANGE_EVENTS


def test_run_stage0_gate_leaves_classification_none_when_universe_is_established():
    synthetic = (
        SourceAttempt(
            tier=1, source_name="synthetic compliant source", url="https://example.invalid",
            access_method="test", outcome="COMPLETE_ROSTER_ESTABLISHED_COMPLIANT", notes="synthetic",
        ),
    )
    result = run_stage0_gate(source_attempts=synthetic)
    assert result.orb_universe_2019_established is True
    assert result.orb_universe_2020_established is True
    assert result.classification is None
    assert result.blocker_reason == ""


def test_m49_frozen_reference_is_labeled_as_reference_only_and_not_recombined():
    assert M49_FROZEN_REFERENCE["classification"] == "SAFE_BUT_BELOW_TARGET"
    assert M49_FROZEN_REFERENCE["evaluation_window"] == "2022-01-03..2026-08-13"
    assert "not combine" in M49_FROZEN_REFERENCE["note"] or "does not combine" in M49_FROZEN_REFERENCE["note"]
