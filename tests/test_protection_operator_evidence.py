"""M13 Issue 7: read-only protection evidence projection tests."""
from __future__ import annotations

import pytest

from src.protection_assessment_store import ProtectionAssessmentStore
from src.protection_grounding_assessment import evaluate_protection_grounding
from src.protection_observation_ledger import ProtectionObservationLedger
from src.protection_observation_model import EvidenceSourceClass
from src.protection_operator_evidence import (
    ProtectionEvidenceQueryError,
    get_protection_assessment,
    get_protection_observation,
    linked_assessment_ids,
    list_protection_assessments,
    list_protection_observations,
    observation_to_dict,
)
from tests._protection_observation_fixtures import (
    active_protective_order,
    digest,
    no_protective_order,
    simulated_evidence,
)

RELEASE_SHA = digest("release")
IMPLEMENTATION_DIGEST = digest("implementation")
ACCOUNT_REF = digest("account")


def _write_ledger(path, observations):
    with ProtectionObservationLedger(path, "run-a") as ledger:
        for obs in observations:
            ledger.append(obs)


def test_list_and_filter_observations(tmp_path):
    ledger_path = tmp_path / "ledger.sqlite3"
    live = active_protective_order("run-a", observed_at_monotonic=1.0)
    simulated = simulated_evidence("run-a", observation_id="obs-sim")
    _write_ledger(ledger_path, [live, simulated])

    items, total = list_protection_observations(ledger_path)
    assert total == 2
    assert {i.observation_id for i in items} == {live.observation_id, "obs-sim"}

    live_only, live_total = list_protection_observations(
        ledger_path, source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION.value
    )
    assert live_total == 1
    assert live_only[0].observation_id == live.observation_id


def test_get_observation_not_found(tmp_path):
    ledger_path = tmp_path / "ledger.sqlite3"
    _write_ledger(ledger_path, [no_protective_order("run-a")])
    with pytest.raises(ProtectionEvidenceQueryError):
        get_protection_observation(ledger_path, "does-not-exist")


def test_get_observation_found_and_dict_has_no_raw_identifiers(tmp_path):
    ledger_path = tmp_path / "ledger.sqlite3"
    obs = active_protective_order("run-a", observed_at_monotonic=1.0)
    _write_ledger(ledger_path, [obs])
    item = get_protection_observation(ledger_path, obs.observation_id)
    d = observation_to_dict(item)
    serialized = repr(d)
    assert "account_number" not in d
    assert "order_id" not in d
    assert "position_id" not in d
    assert "token" not in serialized.lower()


def test_pagination_bounds(tmp_path):
    ledger_path = tmp_path / "ledger.sqlite3"
    _write_ledger(ledger_path, [no_protective_order("run-a")])
    with pytest.raises(ProtectionEvidenceQueryError):
        list_protection_observations(ledger_path, page=0)
    with pytest.raises(ProtectionEvidenceQueryError):
        list_protection_observations(ledger_path, page_size=0)


def test_list_and_get_assessments(tmp_path):
    ledger_path = tmp_path / "ledger.sqlite3"
    store_path = tmp_path / "assessments.sqlite3"
    obs = active_protective_order("run-a", observed_at_monotonic=1.0)
    _write_ledger(ledger_path, [obs])
    with ProtectionObservationLedger(ledger_path, "operator-evidence-query") as ledger:
        summaries = ledger.all_observations()
    assessment = evaluate_protection_grounding(
        summaries,
        evaluated_at_monotonic=2.0,
        expected_release_identity=RELEASE_SHA,
        expected_implementation_digest=IMPLEMENTATION_DIGEST,
        expected_account_fingerprint_reference=ACCOUNT_REF,
    )
    with ProtectionAssessmentStore(store_path) as store:
        store.record_assessment(assessment)

    items, total = list_protection_assessments(store_path)
    assert total == 1
    assert items[0].assessment_id == assessment.assessment_id

    fetched = get_protection_assessment(store_path, assessment.assessment_id)
    assert fetched.assessment_id == assessment.assessment_id

    with pytest.raises(ProtectionEvidenceQueryError):
        get_protection_assessment(store_path, "missing")

    filtered, filtered_total = list_protection_assessments(
        store_path, recommendation=assessment.recommendation.value
    )
    assert filtered_total == 1
    empty, empty_total = list_protection_assessments(
        store_path, recommendation="NOT_A_REAL_RECOMMENDATION"
    )
    assert empty_total == 0

    linked = linked_assessment_ids(store_path, obs.observation_id)
    assert linked == (assessment.assessment_id,)
    assert linked_assessment_ids(store_path, "not-a-real-observation") == ()
