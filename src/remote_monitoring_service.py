"""Authenticated, provider-neutral read-only remote monitoring service."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any, Callable, Mapping

from src.remote_monitoring_projection import RemoteProjectionError, read_remote_projection, utc_timestamp, validate_remote_projection


REMOTE_HOST = "127.0.0.1"
REMOTE_PORT = 8777
SESSION_COOKIE = "orb_remote_session"
SESSION_TTL_SECONDS = 3600
FRESHNESS_SECONDS = 600
LOGIN_WINDOW_SECONDS = 300
LOGIN_ATTEMPTS = 5
REMOTE_ENDPOINTS = frozenset(
    {
        "GET /api/remote/session",
        "GET /api/remote/projection",
        "POST /api/auth/login",
        "POST /api/auth/logout",
    }
)


class RemoteMonitoringError(ValueError):
    """Remote monitoring configuration or request is unsafe."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)



def hash_password(password: str, *, salt: bytes | None = None, n: int = 2**14, r: int = 8, p: int = 1) -> str:
    """Return a self-describing scrypt hash; plaintext is never persisted."""
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise RemoteMonitoringError("password must contain at least 12 characters")
    if n not in {2**14, 2**15, 2**16} or r != 8 or p not in {1, 2}:
        raise RemoteMonitoringError("scrypt parameters are below the security floor")
    selected_salt = secrets.token_bytes(16) if salt is None else salt
    if not isinstance(selected_salt, bytes) or len(selected_salt) < 16:
        raise RemoteMonitoringError("password salt is invalid")
    digest = hashlib.scrypt(password.encode("utf-8"), salt=selected_salt, n=n, r=r, p=p, dklen=32)
    return "$".join(
        (
            "scrypt",
            str(n),
            str(r),
            str(p),
            base64.urlsafe_b64encode(selected_salt).decode("ascii").rstrip("="),
            base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
        )
    )


def _decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, TypeError) as exc:
        raise RemoteMonitoringError("password hash is invalid") from exc


class PasswordVerifier:
    def __init__(self, encoded_hash: str) -> None:
        try:
            algorithm, raw_n, raw_r, raw_p, raw_salt, raw_digest = encoded_hash.split("$")
            self._n, self._r, self._p = int(raw_n), int(raw_r), int(raw_p)
            self._salt, self._digest = _decode(raw_salt), _decode(raw_digest)
        except (AttributeError, ValueError) as exc:
            raise RemoteMonitoringError("password hash is invalid") from exc
        if algorithm != "scrypt" or self._n not in {2**14, 2**15, 2**16} or self._r != 8 or self._p not in {1, 2}:
            raise RemoteMonitoringError("password hash parameters are invalid")
        if len(self._salt) < 16 or len(self._digest) != 32:
            raise RemoteMonitoringError("password hash is invalid")

    def verify(self, candidate: str) -> bool:
        if not isinstance(candidate, str) or len(candidate) > 1024:
            return False
        calculated = hashlib.scrypt(candidate.encode("utf-8"), salt=self._salt, n=self._n, r=self._r, p=self._p, dklen=32)
        return hmac.compare_digest(calculated, self._digest)


@dataclass(frozen=True)
class RemoteMonitoringConfig:
    projection_path: Path
    static_root: Path
    expected_origin: str
    host: str = REMOTE_HOST
    port: int = REMOTE_PORT
    secure_cookie: bool = True
    session_ttl_seconds: int = SESSION_TTL_SECONDS
    freshness_seconds: int = FRESHNESS_SECONDS

    def validate(self) -> "RemoteMonitoringConfig":
        if self.host != REMOTE_HOST:
            raise RemoteMonitoringError("remote service must bind behind the loopback reverse-proxy boundary")
        if type(self.port) is not int or not 1024 <= self.port <= 65535 or self.port == 8766:
            raise RemoteMonitoringError("remote monitoring port is invalid")
        if not isinstance(self.expected_origin, str) or not self.expected_origin:
            raise RemoteMonitoringError("expected monitoring origin is required")
        if self.secure_cookie and not self.expected_origin.startswith("https://"):
            raise RemoteMonitoringError("production monitoring origin must use HTTPS")
        if not self.secure_cookie and self.expected_origin not in {
            f"http://127.0.0.1:{self.port}",
            f"http://localhost:{self.port}",
        }:
            raise RemoteMonitoringError("insecure development cookies are loopback-only")
        if not 300 <= self.session_ttl_seconds <= 86400 or not 60 <= self.freshness_seconds <= 3600:
            raise RemoteMonitoringError("remote monitoring timing configuration is invalid")
        if not Path(self.static_root).is_dir():
            raise RemoteMonitoringError("built remote frontend is unavailable")
        return self


@dataclass(frozen=True)
class Session:
    expires_at: datetime
    csrf_token: str


class SessionStore:
    """In-memory, server-side sessions keyed only by a token digest."""

    def __init__(self, *, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._sessions: dict[str, Session] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self, ttl_seconds: int) -> tuple[str, Session]:
        token = secrets.token_urlsafe(32)
        session = Session(self._clock() + timedelta(seconds=ttl_seconds), secrets.token_urlsafe(24))
        self._sessions[self._key(token)] = session
        return token, session

    def get(self, token: str | None) -> Session | None:
        if not isinstance(token, str) or not token:
            return None
        key = self._key(token)
        session = self._sessions.get(key)
        if session is None:
            return None
        if self._clock() >= session.expires_at:
            self._sessions.pop(key, None)
            return None
        return session

    def revoke(self, token: str | None) -> None:
        if isinstance(token, str) and token:
            self._sessions.pop(self._key(token), None)


class LoginRateLimiter:
    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._attempts: dict[str, list[float]] = {}

    def allow(self, client: str) -> bool:
        now = self._clock()
        recent = [value for value in self._attempts.get(client, []) if now - value < LOGIN_WINDOW_SECONDS]
        self._attempts[client] = recent
        return len(recent) < LOGIN_ATTEMPTS

    def record_failure(self, client: str) -> None:
        self._attempts.setdefault(client, []).append(self._clock())

    def clear(self, client: str) -> None:
        self._attempts.pop(client, None)


def _cookie_token(environ: Mapping[str, Any]) -> str | None:
    raw = environ.get("HTTP_COOKIE")
    if not isinstance(raw, str):
        return None
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except Exception:
        return None
    morsel = cookie.get(SESSION_COOKIE)
    return morsel.value if morsel is not None else None


def _unavailable_projection(threshold_seconds: int) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "projectionType": "ORB_LIVE_REMOTE_MONITORING",
        "generatedAt": None,
        "sourceStatus": "NO_AUTOMATIC_EVIDENCE",
        "capabilities": {"monitoringRead": True, "localControl": False, "brokerWrite": False, "authorization": False},
        "watcher": {"currentState": "UNAVAILABLE", "observedAt": None, "lastRun": None},
        "sessions": [],
        "records": [],
        "events": [],
        "freshness": {"status": "NO_EVIDENCE", "lastUpdatedAt": None, "thresholdSeconds": threshold_seconds},
        "candidateActionable": False,
    }


def _with_freshness(value: Mapping[str, Any], *, now: datetime, threshold_seconds: int) -> dict[str, Any]:
    try:
        generated = datetime.fromisoformat(str(value["generatedAt"]).replace("Z", "+00:00"))
        age = (now.astimezone(timezone.utc) - generated).total_seconds()
    except (KeyError, TypeError, ValueError):
        return _unavailable_projection(threshold_seconds)
    watcher = value.get("watcher")
    watcher_unavailable = not isinstance(watcher, Mapping) or watcher.get("currentState") == "UNAVAILABLE"
    if value.get("sourceStatus") == "NO_AUTOMATIC_EVIDENCE":
        state = "NO_EVIDENCE"
    elif watcher_unavailable or age < -60 or age > threshold_seconds:
        state = "STALE"
    else:
        state = "CURRENT"
    return {
        **value,
        "freshness": {"status": state, "lastUpdatedAt": value["generatedAt"], "thresholdSeconds": threshold_seconds},
        "candidateActionable": state == "CURRENT",
    }


def _fresh_projection(path: Path, *, now: datetime, threshold_seconds: int) -> dict[str, Any]:
    try:
        value = read_remote_projection(path)
    except RemoteProjectionError:
        return _unavailable_projection(threshold_seconds)
    return _with_freshness(value, now=now, threshold_seconds=threshold_seconds)


class RemoteMonitoringApp:
    """WSGI app with exactly two auth mutations and two authenticated reads."""

    def __init__(
        self,
        config: RemoteMonitoringConfig,
        password_verifier: PasswordVerifier,
        *,
        sessions: SessionStore | None = None,
        limiter: LoginRateLimiter | None = None,
        clock: Callable[[], datetime] = _utc_now,
        projection_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.config = config.validate()
        self._password = password_verifier
        self._sessions = sessions or SessionStore(clock=clock)
        self._limiter = limiter or LoginRateLimiter()
        self._clock = clock
        self._projection_provider = projection_provider

    def __call__(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = str(environ.get("PATH_INFO", "/"))
        if path.startswith("/api/local-control"):
            return self._json(start_response, "404 Not Found", {"status": "NOT_FOUND"})
        if method == "POST" and environ.get("HTTP_ORIGIN") != self.config.expected_origin:
            return self._json(start_response, "403 Forbidden", {"status": "ORIGIN_REJECTED"})
        if method == "POST" and path == "/api/auth/login":
            return self._login(environ, start_response)
        if method == "POST" and path == "/api/auth/logout":
            return self._logout(environ, start_response)
        if method == "GET" and path == "/api/remote/session":
            return self._session(environ, start_response)
        if method == "GET" and path == "/api/remote/projection":
            return self._projection(environ, start_response)
        if path.startswith("/api/"):
            return self._json(start_response, "404 Not Found", {"status": "NOT_FOUND"})
        if method != "GET":
            return self._json(start_response, "405 Method Not Allowed", {"status": "METHOD_NOT_ALLOWED"})
        return self._static(path, start_response)

    def _login(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        client = str(environ.get("REMOTE_ADDR") or "unknown")
        if not self._limiter.allow(client):
            return self._json(start_response, "429 Too Many Requests", {"status": "RATE_LIMITED"}, extra_headers=(("Retry-After", str(LOGIN_WINDOW_SECONDS)),))
        value = self._read_json(environ)
        password = value.get("password") if value is not None else None
        if not isinstance(password, str) or not self._password.verify(password):
            self._limiter.record_failure(client)
            return self._json(start_response, "401 Unauthorized", {"status": "INVALID_CREDENTIALS"})
        self._limiter.clear(client)
        token, session = self._sessions.create(self.config.session_ttl_seconds)
        attributes = [f"{SESSION_COOKIE}={token}", "Path=/", "HttpOnly", "SameSite=Strict", f"Max-Age={self.config.session_ttl_seconds}"]
        if self.config.secure_cookie:
            attributes.append("Secure")
        return self._json(
            start_response,
            "200 OK",
            {"status": "AUTHENTICATED", "csrfToken": session.csrf_token, "expiresAt": utc_timestamp(session.expires_at)},
            extra_headers=(("Set-Cookie", "; ".join(attributes)),),
        )

    def _logout(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        token = _cookie_token(environ)
        session = self._sessions.get(token)
        csrf = environ.get("HTTP_X_ORB_CSRF")
        if session is None:
            return self._json(start_response, "401 Unauthorized", {"status": "AUTHENTICATION_REQUIRED"})
        if not isinstance(csrf, str) or not hmac.compare_digest(csrf, session.csrf_token):
            return self._json(start_response, "403 Forbidden", {"status": "CSRF_REJECTED"})
        self._sessions.revoke(token)
        suffix = "; Secure" if self.config.secure_cookie else ""
        return self._json(start_response, "200 OK", {"status": "LOGGED_OUT"}, extra_headers=(("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{suffix}"),))

    def _session(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        session = self._sessions.get(_cookie_token(environ))
        if session is None:
            return self._json(start_response, "401 Unauthorized", {"status": "AUTHENTICATION_REQUIRED"})
        return self._json(start_response, "200 OK", {"status": "AUTHENTICATED", "csrfToken": session.csrf_token, "expiresAt": utc_timestamp(session.expires_at)})

    def _projection(self, environ: Mapping[str, Any], start_response: Callable[..., Any]):
        if self._sessions.get(_cookie_token(environ)) is None:
            return self._json(start_response, "401 Unauthorized", {"status": "AUTHENTICATION_REQUIRED"})
        if self._projection_provider is None:
            value = _fresh_projection(self.config.projection_path, now=self._clock(), threshold_seconds=self.config.freshness_seconds)
        else:
            try:
                value = _with_freshness(validate_remote_projection(self._projection_provider()), now=self._clock(), threshold_seconds=self.config.freshness_seconds)
            except (OSError, RemoteProjectionError, ValueError):
                value = _unavailable_projection(self.config.freshness_seconds)
        return self._json(start_response, "200 OK", value)

    @staticmethod
    def _read_json(environ: Mapping[str, Any]) -> dict[str, Any] | None:
        if environ.get("CONTENT_TYPE") != "application/json":
            return None
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            return None
        if not 1 <= length <= 2048:
            return None
        try:
            raw = environ["wsgi.input"].read(length)
            value = json.loads(raw)
        except (KeyError, OSError, UnicodeError, ValueError):
            return None
        return value if isinstance(value, dict) and set(value) == {"password"} else None

    def _static(self, path: str, start_response: Callable[..., Any]):
        relative = "remote.html" if path in {"/", "/remote.html"} else path.lstrip("/")
        if relative != "remote.html" and (not relative.startswith("assets/") or "\\" in relative):
            return self._json(start_response, "404 Not Found", {"status": "NOT_FOUND"})
        try:
            root = Path(self.config.static_root).resolve(strict=True)
            target = (root / relative).resolve(strict=True)
            if target != root and root not in target.parents:
                raise OSError
            if not target.is_file():
                raise OSError
            body = target.read_bytes()
        except OSError:
            return self._json(start_response, "404 Not Found", {"status": "NOT_FOUND"})
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        headers = self._security_headers() + [("Content-Type", kind), ("Content-Length", str(len(body))), ("Cache-Control", "no-store")]
        start_response("200 OK", headers)
        return [body]

    @staticmethod
    def _security_headers() -> list[tuple[str, str]]:
        return [
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            ("Content-Security-Policy", "default-src 'self'; connect-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"),
        ]

    def _json(self, start_response: Callable[..., Any], status: str, value: Mapping[str, Any], *, extra_headers: tuple[tuple[str, str], ...] = ()):
        body = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        headers = self._security_headers() + [("Content-Type", "application/json"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store"), *extra_headers]
        start_response(status, headers)
        return [body]


__all__ = [
    "FRESHNESS_SECONDS", "LOGIN_ATTEMPTS", "LOGIN_WINDOW_SECONDS", "REMOTE_ENDPOINTS", "REMOTE_HOST", "REMOTE_PORT",
    "LoginRateLimiter", "PasswordVerifier", "RemoteMonitoringApp", "RemoteMonitoringConfig", "RemoteMonitoringError", "SessionStore", "hash_password",
]
