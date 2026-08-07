"""M13 Issue 7: operator application protection-evidence route tests."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.operator_app_store import initialize_store
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from src.protection_assessment_store import ProtectionAssessmentStore
from src.protection_grounding_assessment import evaluate_protection_grounding
from src.protection_observation_ledger import ProtectionObservationLedger
from src.read_only_authorization_manifest import (
    checkpoint_implementation_digest,
    resolve_head_sha,
)
from tests._protection_observation_fixtures import (
    active_protective_order,
    digest,
    no_protective_order,
    simulated_evidence,
)

AS_OF = datetime(2026, 8, 5, 20, tzinfo=timezone.utc)
RELEASE_SHA = digest("release")
IMPLEMENTATION_DIGEST = digest("implementation")
ACCOUNT_REF = digest("account")
REPO_ROOT = Path(__file__).resolve().parent.parent


def request(application, path, query=""):
    result = {}

    def start(status, headers):
        result["status"] = status
        result["headers"] = headers

    body = b"".join(
        application(
            {"REQUEST_METHOD": "GET", "PATH_INFO": path, "QUERY_STRING": query},
            start,
        )
    )
    return result["status"], body, result["headers"]


def _build(tmp_path, *, with_evidence=True):
    db = tmp_path / "operator.sqlite"
    initialize_store(
        db,
        release_commit_sha=resolve_head_sha(REPO_ROOT),
        implementation_digest=checkpoint_implementation_digest(REPO_ROOT),
    )
    ledger_path = tmp_path / "protection_ledger.sqlite3"
    store_path = tmp_path / "protection_assessments.sqlite3"
    assessment = None
    live_obs = None
    if with_evidence:
        live_obs = active_protective_order("run-a", observed_at_monotonic=1.0)
        sim_obs = simulated_evidence("run-a", observation_id="obs-sim")
        with ProtectionObservationLedger(ledger_path, "run-a") as ledger:
            ledger.append(live_obs)
            ledger.append(sim_obs)
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

    application = OperatorWebApp(
        OperatorWebAppConfig(
            runtime_root=tmp_path,
            default_as_of=AS_OF,
            backend_mode="SQLITE_READ_MODEL",
            database_path=db,
            protection_ledger_path=ledger_path,
            protection_assessment_store_path=store_path,
        )
    )
    return application, live_obs, assessment, ledger_path, store_path


def test_observation_list_renders_json_and_html(tmp_path):
    app, live_obs, _, _, _ = _build(tmp_path)
    status, body, _ = request(app, "/api/protection-observations")
    assert status == "200 OK"
    payload = json.loads(body)
    assert payload["total_count"] == 2
    ids = {item["observation_id"] for item in payload["items"]}
    assert live_obs.observation_id in ids

    status, body, _ = request(app, "/protection-observations")
    assert status == "200 OK"
    assert b"Protection observations" in body


def test_observation_detail_renders_and_distinguishes_source(tmp_path):
    app, live_obs, assessment, _, _ = _build(tmp_path)
    status, body, _ = request(app, f"/api/protection-observations/{live_obs.observation_id}")
    assert status == "200 OK"
    payload = json.loads(body)
    assert payload["observation"]["evidence_source_class"] == "LIVE_READ_ONLY_BROKER_OBSERVATION"
    assert assessment.assessment_id in payload["linked_assessment_ids"]

    status, body, _ = request(app, f"/protection-observations/{live_obs.observation_id}")
    assert status == "200 OK"
    assert b"LIVE BROKER-AUTHORITATIVE" in body


def test_simulated_observation_is_labeled_simulated(tmp_path):
    app, _, _, _, _ = _build(tmp_path)
    status, body, _ = request(app, "/protection-observations/obs-sim")
    assert status == "200 OK"
    assert b"SIMULATED_BROKER" in body
    assert b"LIVE BROKER-AUTHORITATIVE" not in body


def test_unknown_observation_is_404(tmp_path):
    app, _, _, _, _ = _build(tmp_path)
    status, _, _ = request(app, "/protection-observations/does-not-exist")
    assert status == "404 Not Found"
    status, _, _ = request(app, "/api/protection-observations/does-not-exist")
    assert status == "404 Not Found"


def test_assessment_list_and_detail_render(tmp_path):
    app, _, assessment, _, _ = _build(tmp_path)
    status, body, _ = request(app, "/api/protection-assessments")
    assert status == "200 OK"
    payload = json.loads(body)
    assert payload["total_count"] == 1
    assert payload["items"][0]["assessment_id"] == assessment.assessment_id

    status, body, _ = request(app, f"/protection-assessments/{assessment.assessment_id}")
    assert status == "200 OK"
    assert b"Recommendation is evidence for review" in body
    assert b"is not approval" in body

    for oid in assessment.excluded_observation_ids:
        assert oid.encode() in body

    status, body, _ = request(app, f"/api/protection-assessments/{assessment.assessment_id}")
    payload = json.loads(body)
    assert payload["assessment"]["assessment_digest"] == assessment.assessment_digest
    assert payload["assessment"]["promotion_permitted"] is False
    assert payload["assessment"]["protection_grounded"] is False


def test_unknown_assessment_is_404(tmp_path):
    app, _, _, _, _ = _build(tmp_path)
    status, _, _ = request(app, "/protection-assessments/does-not-exist")
    assert status == "404 Not Found"


def test_source_class_and_recommendation_filters(tmp_path):
    app, live_obs, assessment, _, _ = _build(tmp_path)
    status, body, _ = request(
        app, "/api/protection-observations", "source_class=LIVE_READ_ONLY_BROKER_OBSERVATION"
    )
    payload = json.loads(body)
    assert payload["total_count"] == 1
    assert payload["items"][0]["observation_id"] == live_obs.observation_id

    status, body, _ = request(
        app, "/api/protection-assessments", f"recommendation={assessment.recommendation.value}"
    )
    payload = json.loads(body)
    assert payload["total_count"] == 1

    status, body, _ = request(app, "/api/protection-observations", "not_a_real_filter=x")
    assert status == "400 Bad Request"
    status, body, _ = request(app, "/api/protection-assessments", "not_a_real_filter=x")
    assert status == "400 Bad Request"


def test_exports_include_protection_evidence_and_route_executes(tmp_path):
    app, live_obs, assessment, _, _ = _build(tmp_path)
    status, body, _ = request(app, "/api/exports")
    assert status == "200 OK"
    payload = json.loads(body)
    types = {item["export_type"] for item in payload["items"]}
    assert "PROTECTION_OBSERVATION" in types
    assert "PROTECTION_ASSESSMENT" in types

    status, body, _ = request(app, f"/exports/protection_observation/{live_obs.observation_id}.json")
    assert status == "200 OK"
    obs_export = json.loads(body)
    assert obs_export["export_type"] == "PROTECTION_OBSERVATION"
    assert obs_export["observation"]["observation_id"] == live_obs.observation_id

    status, body, _ = request(app, f"/exports/protection_assessment/{assessment.assessment_id}.json")
    assert status == "200 OK"
    assessment_export = json.loads(body)
    assert assessment_export["export_type"] == "PROTECTION_ASSESSMENT"
    assert assessment_export["assessment"]["assessment_digest"] == assessment.assessment_digest

    status, _, _ = request(app, "/exports/protection_observation/does-not-exist.json")
    assert status == "404 Not Found"
    status, _, _ = request(app, "/exports/protection_assessment/does-not-exist.json")
    assert status == "404 Not Found"


def test_routes_registered_matchable_and_executable(tmp_path):
    from src.operator_web_app import build_operator_route_table

    table = build_operator_route_table()
    paths = {r.path for r in table.routes}
    assert "/protection-observations" in paths
    assert "/api/protection-observations" in paths
    assert "/protection-observations/{observation_id}" in paths
    assert "/api/protection-observations/{observation_id}" in paths
    assert "/protection-assessments" in paths
    assert "/api/protection-assessments" in paths
    assert "/protection-assessments/{assessment_id}" in paths
    assert "/api/protection-assessments/{assessment_id}" in paths
    for route in table.routes:
        assert route.permits_database_write is False
        assert route.permits_runtime_write is False
        assert route.permits_outbound_network is False
        assert route.exposes_private_data is False
        assert route.safety_authority == "NONE"


def test_get_routes_do_not_mutate_ledger_or_store(tmp_path):
    app, live_obs, assessment, ledger_path, store_path = _build(tmp_path)
    with ProtectionObservationLedger(ledger_path, "operator-evidence-query") as ledger:
        observations_before = [
            (o.observation_id, o.evidence_digest) for o in ledger.all_observations()
        ]
    with ProtectionAssessmentStore(store_path) as store:
        assessments_before = [
            (a.assessment_id, a.assessment_digest) for a in store.all_assessments()
        ]

    request(app, "/protection-observations")
    request(app, f"/protection-observations/{live_obs.observation_id}")
    request(app, "/protection-assessments")
    request(app, f"/protection-assessments/{assessment.assessment_id}")
    request(app, f"/exports/protection_observation/{live_obs.observation_id}.json")
    request(app, f"/exports/protection_assessment/{assessment.assessment_id}.json")

    with ProtectionObservationLedger(ledger_path, "operator-evidence-query") as ledger:
        observations_after = [
            (o.observation_id, o.evidence_digest) for o in ledger.all_observations()
        ]
    with ProtectionAssessmentStore(store_path) as store:
        assessments_after = [
            (a.assessment_id, a.assessment_digest) for a in store.all_assessments()
        ]
    assert observations_after == observations_before
    assert assessments_after == assessments_before


def test_only_get_is_permitted(tmp_path):
    app, live_obs, _, _, _ = _build(tmp_path)
    result = {}

    def start(status, headers):
        result["status"] = status

    body = b"".join(
        app(
            {
                "REQUEST_METHOD": "POST",
                "PATH_INFO": "/protection-observations",
                "QUERY_STRING": "",
            },
            start,
        )
    )
    assert result["status"] == "405 Method Not Allowed"


def test_missing_configuration_fails_closed(tmp_path):
    db = tmp_path / "operator.sqlite"
    initialize_store(
        db,
        release_commit_sha=resolve_head_sha(REPO_ROOT),
        implementation_digest=checkpoint_implementation_digest(REPO_ROOT),
    )
    application = OperatorWebApp(
        OperatorWebAppConfig(
            runtime_root=tmp_path,
            default_as_of=AS_OF,
            backend_mode="SQLITE_READ_MODEL",
            database_path=db,
        )
    )
    status, _, _ = request(application, "/protection-observations")
    assert status == "503 Service Unavailable"
    status, _, _ = request(application, "/protection-assessments")
    assert status == "503 Service Unavailable"


def test_no_broker_or_checkpoint_import_in_operator_module():
    import src.operator_web_app as mod
    import inspect

    source = inspect.getsource(mod)
    assert "robinhood_client" not in source
    assert "read_only_observation_runtime" not in source
    assert "protection_checkpoint_integration" not in source
    assert "run_read_only_observation_checkpoint" not in source
