from __future__ import annotations

import ast
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from wsgiref.util import setup_testing_defaults

import pytest

from src.remote_monitoring_service import (
    LOGIN_ATTEMPTS,
    REMOTE_ENDPOINTS,
    REMOTE_HOST,
    REMOTE_PORT,
    LoginRateLimiter,
    PasswordVerifier,
    RemoteMonitoringApp,
    RemoteMonitoringConfig,
    RemoteMonitoringError,
    SessionStore,
    hash_password,
)


PASSWORD = "correct horse battery staple"
NOW = datetime(2026, 8, 14, 14, 20, tzinfo=timezone.utc)


class Clock:
    def __init__(self): self.value = NOW
    def __call__(self): return self.value


def projection(generated_at="2026-08-14T14:20:00Z"):
    return {"schemaVersion": 1, "projectionType": "ORB_LIVE_REMOTE_MONITORING", "generatedAt": generated_at, "sourceStatus": "AUTOMATIC_EVIDENCE_AVAILABLE", "capabilities": {"monitoringRead": True, "localControl": False, "brokerWrite": False, "authorization": False}, "watcher": {"currentState": "NOT_RUNNING", "observedAt": generated_at, "lastRun": None}, "sessions": [], "records": [], "events": []}


def config(tmp_path, **changes):
    static = tmp_path / "dist"
    static.mkdir(exist_ok=True)
    (static / "remote.html").write_text("remote login", encoding="utf-8")
    values = {"projection_path": tmp_path / "projection.json", "static_root": static, "expected_origin": "https://orb-monitor.example.com"}
    values.update(changes)
    return RemoteMonitoringConfig(**values)


def app(tmp_path, *, clock=None, limiter=None, provider=None):
    selected = clock or Clock()
    return RemoteMonitoringApp(config(tmp_path), PasswordVerifier(hash_password(PASSWORD, salt=b"0123456789abcdef")), sessions=SessionStore(clock=selected), limiter=limiter, clock=selected, projection_provider=provider or projection)


def call(value, path, *, method="GET", body=None, origin=None, cookie=None, csrf=None, remote="203.0.113.9"):
    raw = b"" if body is None else json.dumps(body).encode("utf-8")
    environ = {}
    setup_testing_defaults(environ)
    environ.update({"PATH_INFO": path, "REQUEST_METHOD": method, "REMOTE_ADDR": remote, "CONTENT_LENGTH": str(len(raw)), "wsgi.input": io.BytesIO(raw)})
    if body is not None: environ["CONTENT_TYPE"] = "application/json"
    if origin is not None: environ["HTTP_ORIGIN"] = origin
    if cookie is not None: environ["HTTP_COOKIE"] = cookie
    if csrf is not None: environ["HTTP_X_ORB_CSRF"] = csrf
    captured = {}
    data = b"".join(value(environ, lambda status, headers: captured.update(status=status, headers=headers)))
    return captured["status"], dict(captured["headers"]), json.loads(data) if data and dict(captured["headers"]).get("Content-Type") == "application/json" else data


def login(value):
    status, headers, payload = call(value, "/api/auth/login", method="POST", body={"password": PASSWORD}, origin="https://orb-monitor.example.com")
    assert status == "200 OK"
    return headers["Set-Cookie"].split(";", 1)[0], payload["csrfToken"]


def test_config_has_separate_loopback_port_and_https_production_origin(tmp_path):
    assert config(tmp_path).validate().host == REMOTE_HOST == "127.0.0.1"
    assert config(tmp_path).port == REMOTE_PORT == 8777
    with pytest.raises(RemoteMonitoringError): config(tmp_path, host="0.0.0.0").validate()
    with pytest.raises(RemoteMonitoringError): config(tmp_path, port=8766).validate()
    with pytest.raises(RemoteMonitoringError): config(tmp_path, expected_origin="http://public.example").validate()


def test_password_is_scrypt_hashed_and_valid_login_creates_secure_server_session(tmp_path):
    encoded = hash_password(PASSWORD, salt=b"0123456789abcdef")
    assert PASSWORD not in encoded and encoded.startswith("scrypt$")
    assert PasswordVerifier(encoded).verify(PASSWORD)
    assert not PasswordVerifier(encoded).verify("wrong password")
    value = app(tmp_path)
    assert call(value, "/api/remote/projection")[0] == "401 Unauthorized"
    assert call(value, "/api/auth/login", method="POST", body={"password": "wrong password"}, origin="https://orb-monitor.example.com")[0] == "401 Unauthorized"
    cookie, _ = login(value)
    headers = call(value, "/api/auth/login", method="POST", body={"password": PASSWORD}, origin="https://orb-monitor.example.com")[1]
    set_cookie = headers["Set-Cookie"]
    assert all(part in set_cookie for part in ("HttpOnly", "Secure", "SameSite=Strict"))
    assert call(value, "/api/remote/projection", cookie=cookie)[0] == "200 OK"


def test_logout_requires_csrf_and_session_expiry_is_enforced(tmp_path):
    clock = Clock(); value = app(tmp_path, clock=clock)
    cookie, csrf = login(value)
    assert call(value, "/api/auth/logout", method="POST", body={}, origin="https://orb-monitor.example.com", cookie=cookie)[0] == "403 Forbidden"
    assert call(value, "/api/auth/logout", method="POST", body={}, origin="https://orb-monitor.example.com", cookie=cookie, csrf=csrf)[0] == "200 OK"
    assert call(value, "/api/remote/session", cookie=cookie)[0] == "401 Unauthorized"
    cookie, _ = login(value)
    clock.value += timedelta(seconds=3601)
    assert call(value, "/api/remote/session", cookie=cookie)[0] == "401 Unauthorized"


def test_login_is_origin_protected_and_rate_limited(tmp_path):
    tick = [0.0]
    limiter = LoginRateLimiter(clock=lambda: tick[0])
    value = app(tmp_path, limiter=limiter)
    assert call(value, "/api/auth/login", method="POST", body={"password": PASSWORD}, origin="https://evil.example")[0] == "403 Forbidden"
    for _ in range(LOGIN_ATTEMPTS):
        assert call(value, "/api/auth/login", method="POST", body={"password": "wrong password"}, origin="https://orb-monitor.example.com")[0] == "401 Unauthorized"
    assert call(value, "/api/auth/login", method="POST", body={"password": PASSWORD}, origin="https://orb-monitor.example.com")[0] == "429 Too Many Requests"


def test_only_authenticated_projection_get_is_exposed_and_local_control_is_not_proxied(tmp_path):
    calls = []
    value = app(tmp_path, provider=lambda: calls.append(True) or projection())
    cookie, _ = login(value)
    status, _, payload = call(value, "/api/remote/projection", cookie=cookie)
    assert status == "200 OK" and payload["freshness"]["status"] == "CURRENT" and calls == [True]
    for path in ("/api/local-control/status", "/api/local-control/readiness", "/api/local-control/start", "/api/local-control/stop", "/api/remote/start", "/api/remote/authorize"):
        assert call(value, path, method="POST", body={}, origin="https://orb-monitor.example.com", cookie=cookie)[0] == "404 Not Found"
    assert calls == [True]
    assert REMOTE_ENDPOINTS == {"GET /api/remote/session", "GET /api/remote/projection", "POST /api/auth/login", "POST /api/auth/logout"}


def test_stale_or_unavailable_projection_fails_closed(tmp_path):
    value = app(tmp_path, provider=lambda: projection("2026-08-14T13:00:00Z"))
    cookie, _ = login(value)
    payload = call(value, "/api/remote/projection", cookie=cookie)[2]
    assert payload["freshness"]["status"] == "STALE" and payload["candidateActionable"] is False
    value = app(tmp_path, provider=lambda: (_ for _ in ()).throw(ValueError("private path C:/secret")))
    cookie, _ = login(value)
    payload = call(value, "/api/remote/projection", cookie=cookie)[2]
    assert payload["freshness"]["status"] == "NO_EVIDENCE"
    assert "secret" not in json.dumps(payload).casefold()


def test_static_allowlist_serves_remote_entry_but_refuses_local_ui(tmp_path):
    static = tmp_path / "dist"
    static.mkdir()
    (static / "remote.html").write_text("remote login", encoding="utf-8")
    (static / "index.html").write_text("local controls", encoding="utf-8")
    value = RemoteMonitoringApp(config(tmp_path), PasswordVerifier(hash_password(PASSWORD, salt=b"0123456789abcdef")), projection_provider=projection)
    assert call(value, "/")[0] == "200 OK"
    assert call(value, "/index.html")[0] == "404 Not Found"


def test_in_memory_provider_cannot_bypass_exact_allowlist(tmp_path):
    unsafe = projection()
    unsafe["environment"] = {"ALPACA_API_SECRET_KEY": "private"}
    value = app(tmp_path, provider=lambda: unsafe)
    cookie, _ = login(value)
    payload = call(value, "/api/remote/projection", cookie=cookie)[2]
    assert payload["freshness"]["status"] == "NO_EVIDENCE"
    assert "private" not in json.dumps(payload).casefold()

def test_service_source_has_no_broker_process_control_or_local_control_import():
    paths = [Path("src/remote_monitoring_service.py"), Path("src/remote_monitoring_projection.py"), Path("src/remote_monitoring_source.py")]
    source = "\n".join(path.read_text(encoding="utf-8") for path in paths)
    tree = ast.parse(source)
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not ({"RobinhoodCommissioningAdapter", "call_tool", "subprocess", "Popen", "place_equity_order", "cancel_equity_order", "flatten"} & names)
    assert "from src.local_watcher_control" not in source
    assert 'method="POST"' not in Path("src/remote_monitoring_source.py").read_text(encoding="utf-8")
