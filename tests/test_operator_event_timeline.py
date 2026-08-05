from __future__ import annotations

import json
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from src.operator_event_timeline import EVENT_DETAIL_KEYS, EVENT_KEYS, EVENT_LIST_KEYS, EventSeverity, EventType, OperatorEventTimeline, TimelineError, TimelineNotFound
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


def _timeline(tmp_path: Path):
    root, _ = _history_fixture(tmp_path)
    history = SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1])
    return root, OperatorEventTimeline(history)


def test_timeline_derives_only_validated_changes_and_orders_deterministically(tmp_path):
    _, timeline = _timeline(tmp_path)
    first = timeline.build(as_of=AS_OF)
    second = timeline.build(as_of=AS_OF)
    assert first == second and first
    assert all(event.event_id.startswith("event-") for event in first)
    assert any(event.event_type is EventType.READINESS_BLOCKER_ADDED for event in first)
    assert any(event.event_type is EventType.READINESS_BLOCKER_REMOVED for event in first)
    assert any(event.event_type is EventType.HEARTBEAT_RECOVERED for event in first)
    assert list(first) == sorted(first, key=lambda event: (event.occurred_at, event.event_id), reverse=True)


def test_filters_and_pagination_are_bounded(tmp_path):
    _, timeline = _timeline(tmp_path)
    events, total = timeline.query(as_of=AS_OF, event_type=EventType.READINESS_BLOCKER_ADDED.value)
    assert total >= 1 and all(event.event_type is EventType.READINESS_BLOCKER_ADDED for event in events)
    events, total = timeline.query(as_of=AS_OF, severity=EventSeverity.INFO.value)
    assert total >= 1
    events, total = timeline.query(as_of=AS_OF, page=1, page_size=10)
    assert len(events) <= 10
    with pytest.raises(TimelineError):
        timeline.query(as_of=AS_OF, event_type="ARBITRARY")
    with pytest.raises(TimelineError):
        timeline.query(as_of=AS_OF, severity="SUCCESS")
    with pytest.raises(TimelineError):
        timeline.query(as_of=AS_OF, from_value="2026-08-05T00:00:00")


def test_api_and_pages_use_exact_event_schemas(tmp_path):
    root, timeline = _timeline(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    status, _, body = _request(app, "/api/timeline")
    payload = json.loads(body)
    assert status == "200 OK" and set(payload) == EVENT_LIST_KEYS and payload["items"]
    event_id = payload["items"][0]["event_id"]
    status, _, body = _request(app, f"/api/timeline/{event_id}")
    detail = json.loads(body)
    assert status == "200 OK" and set(detail) == EVENT_DETAIL_KEYS and set(detail["event"]) == EVENT_KEYS
    assert _request(app, "/timeline")[0] == "200 OK"
    assert _request(app, f"/timeline/{event_id}")[0] == "200 OK"


def test_event_detail_and_missing_event_are_privacy_safe(tmp_path):
    root, timeline = _timeline(tmp_path)
    event = timeline.build(as_of=AS_OF)[0]
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    text = _request(app, f"/timeline/{event.event_id}")[2].decode().casefold()
    assert "observational" in text and "causality" in text
    assert all(term not in text for term in ("account", "quantity", "price", "balance", "order", "credential"))
    assert _request(app, "/api/timeline/event-invalid")[0] == "404 Not Found"
    with pytest.raises(TimelineNotFound):
        timeline.get(event_id="event-invalid", as_of=AS_OF)


def test_no_single_snapshot_transition_is_fabricated(tmp_path):
    root, _ = _history_fixture(tmp_path / "single", count=1)
    timeline = OperatorEventTimeline(SnapshotHistory(runtime_root=root, repository_root=Path(__file__).resolve().parents[1]))
    assert timeline.build(as_of=AS_OF) == ()


def test_timeline_requests_are_read_only_and_no_write_routes_exist(tmp_path):
    root, _ = _timeline(tmp_path)
    app = OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    _request(app, "/api/timeline")
    _request(app, "/timeline")
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        assert _request(app, "/timeline", method=method)[0] == "405 Method Not Allowed"
