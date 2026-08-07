"""M13 Issue 7: protection assessment store tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.protection_grounding_assessment import evaluate_protection_grounding
from src.protection_observation_ledger import ProtectionObservationLedger
from src.protection_assessment_store import (
    ProtectionAssessmentStore,
    ProtectionAssessmentStoreError,
)
from tests._protection_observation_fixtures import (
    active_protective_order,
    digest,
    no_protective_order,
)

RELEASE_SHA = digest("release")
IMPLEMENTATION_DIGEST = digest("implementation")
ACCOUNT_REF = digest("account")


def _assessment(observations, evaluated_at=200.0, **overrides):
    kwargs = dict(
        evaluated_at_monotonic=evaluated_at,
        expected_release_identity=RELEASE_SHA,
        expected_implementation_digest=IMPLEMENTATION_DIGEST,
        expected_account_fingerprint_reference=ACCOUNT_REF,
    )
    kwargs.update(overrides)
    return evaluate_protection_grounding(observations, **kwargs)


def test_basic_record_list_get(tmp_path):
    obs = active_protective_order("run-a", observed_at_monotonic=199.0)
    with ProtectionObservationLedger(tmp_path / "ledger.sqlite3", "run-a") as ledger:
        ledger.append(obs)
        summaries = ledger.all_observations()
    assessment = _assessment(summaries)

    with ProtectionAssessmentStore(tmp_path / "assessments.sqlite3") as store:
        recorded = store.record_assessment(assessment)
        assert recorded.assessment_id == assessment.assessment_id
        assert recorded.recommendation == assessment.recommendation
        assert recorded.assessment_digest == assessment.assessment_digest

        fetched = store.get_assessment(assessment.assessment_id)
        assert fetched.assessment_id == assessment.assessment_id
        assert fetched.included_observation_ids == assessment.included_observation_ids
        assert fetched.candidate_observation_ids == assessment.candidate_observation_ids

        all_items = store.all_assessments()
        assert [a.assessment_id for a in all_items] == [assessment.assessment_id]


def test_re_recording_identical_assessment_is_idempotent(tmp_path):
    obs = no_protective_order("run-a")
    with ProtectionObservationLedger(tmp_path / "ledger.sqlite3", "run-a") as ledger:
        ledger.append(obs)
        summaries = ledger.all_observations()
    assessment = _assessment(summaries)

    with ProtectionAssessmentStore(tmp_path / "assessments.sqlite3") as store:
        store.record_assessment(assessment)
        store.record_assessment(assessment)
        assert len(store.all_assessments()) == 1


def test_get_missing_assessment_raises(tmp_path):
    with ProtectionAssessmentStore(tmp_path / "assessments.sqlite3") as store:
        with pytest.raises(ProtectionAssessmentStoreError):
            store.get_assessment("does-not-exist")


def test_repository_data_path_is_refused():
    repo_root = Path(__file__).resolve().parent.parent
    with pytest.raises(ProtectionAssessmentStoreError):
        ProtectionAssessmentStore(repo_root / "data" / "assessments.sqlite3")


def test_only_grounding_assessment_type_may_be_recorded(tmp_path):
    with ProtectionAssessmentStore(tmp_path / "assessments.sqlite3") as store:
        with pytest.raises(ProtectionAssessmentStoreError):
            store.record_assessment("not-an-assessment")


def test_dimension_pass_fields_round_trip(tmp_path):
    obs = active_protective_order("run-a", observed_at_monotonic=199.0)
    with ProtectionObservationLedger(tmp_path / "ledger.sqlite3", "run-a") as ledger:
        ledger.append(obs)
        summaries = ledger.all_observations()
    assessment = _assessment(summaries)

    with ProtectionAssessmentStore(tmp_path / "assessments.sqlite3") as store:
        recorded = store.record_assessment(assessment)
        assert recorded.zero_write_passed == assessment.zero_write_analysis.passed
        assert recorded.privacy_passed == assessment.privacy_analysis.passed
        assert recorded.freshness_passed == assessment.freshness_analysis.passed
        d = recorded.to_dict()
        assert d["promotion_permitted"] is False
        assert d["protection_grounded"] is False
