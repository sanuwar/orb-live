from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from src.operator_web_app import ALLOWED_HOST, OperatorWebApp, OperatorWebAppConfig, WebAppConfigError
from src.supervised_operator_console import RESULT_KEYS
from test_supervised_operator_console import AS_OF, _fixture


def _request(app, path="/", method="GET"):
    environ = {}
    setup_testing_defaults(environ)
    request_path, _, query = path.partition("?")
    environ.update({"REQUEST_METHOD": method, "PATH_INFO": request_path, "QUERY_STRING": query})
    status = []
    headers = []
    body = b"".join(app(environ, lambda value, fields: (status.append(value), headers.extend(fields))))
    return status[0], dict(headers), body


def _app(tmp_path: Path) -> tuple[Path, OperatorWebApp]:
    root = _fixture(tmp_path / "runtime")
    return root, OperatorWebApp(OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF))


def _mutate(root: Path, **changes):
    path = root / "evidence/operator-console-evidence.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(changes)
    path.write_text(json.dumps(raw), encoding="utf-8")


def test_homepage_contains_required_sections_and_read_only_label(tmp_path):
    _, app = _app(tmp_path)
    status, headers, body = _request(app)
    text = body.decode()
    assert status == "200 OK"
    assert "READ-ONLY" in text
    assert all(section in text for section in ("Overall state", "Runtime components", "Safety boundaries", "Blockers", "No broker access"))
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Referrer-Policy"] == "no-referrer"
    assert "Content-Security-Policy" in headers and "default-src 'none'" in headers["Content-Security-Policy"]


def test_snapshot_endpoint_is_exact_and_health_is_narrow(tmp_path):
    _, app = _app(tmp_path)
    status, headers, body = _request(app, "/api/snapshot")
    assert status == "200 OK"
    assert set(json.loads(body)) == RESULT_KEYS
    assert headers["Cache-Control"] == "no-store"
    status, _, body = _request(app, "/healthz")
    assert status == "200 OK"
    assert json.loads(body) == {"mode": "READ_ONLY_OPERATOR_UI", "status": "UP"}


def test_about_has_authorization_limitations(tmp_path):
    _, app = _app(tmp_path)
    text = _request(app, "/about")[2].decode().casefold()
    assert all(term in text for term in ("read-only", "does not authorize deployment", "broker access", "unattended"))


@pytest.mark.parametrize(("field", "value", "visible"), [
    ("suspension_status", "ACTIVE", "ACTIVE"), ("heartbeat_status", "STALE", "STALE"),
    ("alert_status", "UNCONFIRMED", "UNCONFIRMED"), ("recovery_status", "AMBIGUOUS", "AMBIGUOUS"),
    ("evidence_completeness", "PARTIAL", "PARTIAL"),
])
def test_adverse_states_are_visible(field, value, visible, tmp_path):
    root, app = _app(tmp_path)
    _mutate(root, **{field: value})
    status, _, body = _request(app)
    assert status == "200 OK"
    assert visible in body.decode()


@pytest.mark.parametrize("field", ["strategy_enabled", "activation_approvals_present", "permits_automated_action", "live_ready", "unattended_ready"])
def test_safety_violation_fails_closed(field, tmp_path):
    root, app = _app(tmp_path)
    _mutate(root, **{field: True})
    status, _, body = _request(app)
    assert status == "503 Service Unavailable"
    assert "Evidence unavailable" in body.decode()


def test_html_injection_and_private_data_are_not_returned(tmp_path):
    root, app = _app(tmp_path)
    _mutate(root, blocker_reasons=["<script>alert(1)</script>"])
    body = _request(app)[2].decode()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert "<script>" not in body
    _mutate(root, blocker_reasons=["C:\\Users\\private"])
    status, _, body = _request(app)
    assert status == "503 Service Unavailable"
    assert "private" not in body.decode().casefold()


def test_query_as_of_is_supported_but_naive_and_unknown_queries_fail(tmp_path):
    _, app = _app(tmp_path)
    status, _, _ = _request(app, "/?as_of=2026-08-05T16%3A00%3A00Z")
    assert status == "200 OK"
    status, _, _ = _request(app, "/?as_of=2026-08-05T16%3A00%3A00")
    assert status == "503 Service Unavailable"
    status, _, _ = _request(app, "/?other=value")
    assert status == "503 Service Unavailable"


def test_unknown_and_write_routes_are_rejected(tmp_path):
    _, app = _app(tmp_path)
    assert _request(app, "/private-file")[0] == "404 Not Found"
    assert _request(app, "/", method="POST")[0] == "405 Method Not Allowed"
    assert _request(app, "/api/snapshot", method="POST")[0] == "405 Method Not Allowed"


def test_requests_do_not_modify_runtime_evidence(tmp_path):
    root, app = _app(tmp_path)
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    _request(app, "/")
    _request(app, "/api/snapshot")
    _request(app, "/healthz")
    after = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    assert before == after


@pytest.mark.parametrize("kwargs", [
    {"host": "0.0.0.0"}, {"port": 80}, {"port": 70000}, {"refresh_interval": -1}, {"refresh_interval": 301},
])
def test_unsafe_configuration_is_rejected(tmp_path, kwargs):
    root = _fixture(tmp_path / "runtime")
    with pytest.raises(WebAppConfigError):
        OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF, **kwargs).validate()


def test_unsafe_root_and_naive_default_are_rejected(tmp_path):
    with pytest.raises(WebAppConfigError):
        OperatorWebAppConfig(runtime_root=Path("."), default_as_of=AS_OF).validate()
    root = _fixture(tmp_path / "runtime")
    with pytest.raises(WebAppConfigError):
        OperatorWebAppConfig(runtime_root=root, default_as_of=AS_OF.replace(tzinfo=None)).validate()


def test_deterministic_html_and_no_external_resources(tmp_path):
    _, app = _app(tmp_path)
    first = _request(app)[2]
    second = _request(app)[2]
    assert first == second
    text = first.decode().casefold()
    assert "http://" not in text and "https://" not in text and "<script" not in text
    assert "account" not in text and "credential" not in text and "quantity" not in text
