from __future__ import annotations

import json
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from src.operator_event_timeline import EventType, OperatorEventTimeline
from src.operator_incident_export import INCIDENT_KEYS, IncidentError, IncidentNotFound, OperatorIncidentStore
from src.operator_snapshot_history import SnapshotHistory
from src.operator_web_app import OperatorWebApp, OperatorWebAppConfig
from test_operator_snapshot_history import _history_fixture
from test_supervised_operator_console import AS_OF


def _request(app, path="/", method="GET"):
    environ = {}
    setup_testing_defaults(environ)
    request_path, _, query = path.partition("?")
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": request_path, "QUERY_STRING": query})
    status, headers = [], []
    body = b"".join(app(environ, lambda value, fields: (status.append(value), headers.extend(fields))))
    return status[0], dict(headers), body


def _incident_fixture(tmp_path: Path):
    root, _ = _history_fixture(tmp_path)
    timeline = OperatorEventTimeline(SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]))
    event = next(event for event in timeline.build(as_of=AS_OF) if event.severity.value != "INFO")
    store = OperatorIncidentStore(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    return root, event, store.get(incident_id="incident-" + event.event_id, as_of=AS_OF)


def test_adverse_events_produce_deterministic_incidents(tmp_path):
    root, event, incident = _incident_fixture(tmp_path)
    assert incident.incident_id == "incident-" + event.event_id
    assert incident.incident_type in {"ACTIVE_SUSPENSION", "STALE_HEARTBEAT", "EVIDENCE_INCOMPLETE"}
    assert incident.active == "ACTIVE"
    assert set(incident.to_dict()) == INCIDENT_KEYS
    assert all(not incident.to_dict()[field] for field in ("strategy_enabled", "permits_automated_action", "live_ready", "unattended_ready"))
    assert root.exists()


def test_incident_routes_and_exports_share_exact_truth(tmp_path):
    root, event, incident = _incident_fixture(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    incident_id = incident.incident_id
    status, _, body = _request(app, f"/api/incidents/{incident_id}")
    api = json.loads(body)
    assert status == "200 OK" and set(api) == INCIDENT_KEYS
    for suffix in ("/export.json", "/export.html"):
        status, _, body = _request(app, f"/incidents/{incident_id}{suffix}")
        assert status == "200 OK"
        assert incident_id in body.decode()
    status, _, body = _request(app, f"/incidents/{incident_id}")
    text = body.decode()
    assert status == "200 OK" and "OBSERVATIONAL AND READ-ONLY" in text
    assert "does not resolve the incident" in text


def test_info_event_does_not_become_incident(tmp_path):
    root, _ = _history_fixture(tmp_path)
    timeline = OperatorEventTimeline(SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]))
    info = next(event for event in timeline.build(as_of=AS_OF) if event.severity.value == "INFO")
    store = OperatorIncidentStore(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    with pytest.raises(IncidentNotFound):
        store.get(incident_id="incident-" + info.event_id, as_of=AS_OF)


def test_active_status_uses_current_adverse_evidence(tmp_path):
    root, _, incident = _incident_fixture(tmp_path)
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    raw["blocker_reasons"] = ["ACTIVE_SUSPENSION"]
    raw["operational_status"] = "BLOCKED"
    raw["suspension_status"] = "ACTIVE"
    path.write_text(json.dumps(raw))
    store = OperatorIncidentStore(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    refreshed = store.get(incident_id=incident.incident_id, as_of=AS_OF)
    assert refreshed.active == "ACTIVE"


def test_missing_or_conflicting_evidence_fails_closed(tmp_path):
    root, event, _ = _incident_fixture(tmp_path)
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text())
    raw["permits_automated_action"] = True
    path.write_text(json.dumps(raw))
    store = OperatorIncidentStore(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    with pytest.raises(IncidentError):
        store.get(incident_id="incident-" + event.event_id, as_of=AS_OF)
    assert _request(OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF)), "/api/incidents/incident-invalid")[0] == "404 Not Found"


def test_incident_exports_are_read_only_and_private_safe(tmp_path):
    root, event, incident = _incident_fixture(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    body = _request(app, f"/incidents/{incident.incident_id}/export.html")[2].decode().casefold()
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    assert all(term not in body for term in ("account", "quantity", "price", "balance", "order", "credential", "http://", "<script"))
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _request(app, f"/incidents/{incident.incident_id}", method=method)[0] == "405 Method Not Allowed"
