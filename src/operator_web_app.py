"""Minimal local WSGI read-only operator application."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from src.offline_path_safety import is_repository_data_path
from src.read_only_authorization_manifest import checkpoint_implementation_digest, resolve_head_sha
from src.supervised_operator_console import (
    ConsoleError,
    ConsoleIdentityError,
    ConsoleSafetyError,
    load_console_snapshot,
    render_text,
)


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_ROOT = REPOSITORY_ROOT / "templates"
STATIC_ROOT = REPOSITORY_ROOT / "static"
ALLOWED_HOST = "127.0.0.1"
PORT_MINIMUM = 1024
PORT_MAXIMUM = 65535
REFRESH_MINIMUM = 0
REFRESH_MAXIMUM = 300


class WebAppConfigError(ValueError):
    """The local web application configuration is unsafe or malformed."""


@dataclass(frozen=True)
class OperatorWebAppConfig:
    runtime_root: Path
    default_as_of: datetime
    host: str = ALLOWED_HOST
    port: int = 8765
    refresh_interval: int = 0

    def validate(self) -> "OperatorWebAppConfig":
        if self.host != ALLOWED_HOST:
            raise WebAppConfigError("only the loopback bind address is allowed")
        if type(self.port) is not int or not PORT_MINIMUM <= self.port <= PORT_MAXIMUM:
            raise WebAppConfigError("port is outside the allowed range")
        if type(self.refresh_interval) is not int or not REFRESH_MINIMUM <= self.refresh_interval <= REFRESH_MAXIMUM:
            raise WebAppConfigError("refresh interval is outside the allowed range")
        if self.default_as_of.tzinfo is None or self.default_as_of.utcoffset() is None:
            raise WebAppConfigError("as-of timestamp must be timezone-aware")
        try:
            root = self.runtime_root.resolve(strict=True)
        except OSError as exc:
            raise WebAppConfigError("runtime root is unavailable") from exc
        if not root.is_dir() or is_repository_data_path(root) or root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents:
            raise WebAppConfigError("runtime root is unsafe")
        if any(part.casefold() in {"src", "tests", "test", "examples"} for part in root.parts):
            raise WebAppConfigError("runtime root is unsafe")
        return self


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WebAppConfigError("as-of timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WebAppConfigError("as-of timestamp must be timezone-aware")
    return parsed


def _headers(content_type: str, status: str = "200 OK") -> list[tuple[str, str]]:
    return [
        ("Content-Type", content_type), ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"),
        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
        ("Cache-Control", "no-store"), ("Content-Length", "0"),
    ] if status != "200 OK" else [
        ("Content-Type", content_type), ("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"),
        ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer"),
        ("Cache-Control", "no-store"),
    ]


def _response(start_response, status: str, content_type: str, body: bytes):
    response_headers = _headers(content_type, status)
    response_headers = [(key, str(len(body)) if key == "Content-Length" else value) for key, value in response_headers]
    start_response(status, response_headers)
    return [body]


def _error_body() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>ORB-LIVE unavailable</title></head><body><main><h1>ORB-LIVE Supervised Operator</h1><p>READ-ONLY</p><h2>Evidence unavailable</h2><p>The current snapshot could not be safely displayed. Review the offline evidence before continuing.</p></main></body></html>"""


def _about_body() -> str:
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>About ORB-LIVE</title></head><body><main><h1>ORB-LIVE Supervised Operator</h1><p>READ-ONLY local interface.</p><p>This application displays privacy-safe offline evidence only. It does not authorize deployment, broker access, trading, or unattended operation.</p></main></body></html>"""


def _dashboard_body(result) -> str:
    source = Path(TEMPLATE_ROOT / "operator_dashboard.html").read_text(encoding="utf-8")
    css = Path(STATIC_ROOT / "operator_dashboard.css").read_text(encoding="utf-8")
    full = render_text(result)
    data = result.to_dict()
    rows = "".join(f"<tr><th>{html.escape(key)}</th><td>{html.escape(value)}</td></tr>" for key, value in result.source_component_statuses)
    blockers = "".join(f"<li>{html.escape(value)}</li>" for value in result.blocker_reasons) or "<li>None recorded</li>"
    values = {
        "STYLE": css, "AS_OF": html.escape(data["generated_as_of"]), "COMMIT": html.escape(data["release_commit_sha"][:12]),
        "DIGEST": html.escape(data["implementation_digest"][:12]), "MODE": html.escape(data["operating_mode"]),
        "OPERATIONAL": html.escape(data["operational_status"]), "READINESS": html.escape(data["supervised_readiness_status"]),
        "ACTION": html.escape(data["recommended_operator_action"]), "COMPLETENESS": html.escape(data["evidence_completeness"]),
        "FRESHNESS": html.escape(data["evidence_freshness"]), "RUNTIME": full.replace("\n", "<br>") and "".join(
            f"<tr><th>{html.escape(label)}</th><td>{html.escape(data[field])}</td></tr>" for label, field in (
                ("Scheduler", "scheduler_status"), ("Runner", "runner_status"), ("Suspension", "suspension_status"),
                ("Heartbeat", "heartbeat_status"), ("Alerts", "alert_status"), ("Acknowledgment", "acknowledgment_status"),
                ("Recovery", "recovery_status"),
            )),
        "COMPONENTS": rows, "BLOCKERS": blockers,
        "SAFETY": "".join(f"<li>{html.escape(label)}: NO</li>" for label in (
            "Strategy enabled", "Approvals present", "Protection grounded", "Activation approved",
            "Automated action permitted", "Live ready", "Unattended ready",
        )),
    }
    for key, value in values.items():
        source = source.replace("{{" + key + "}}", value)
    return source


class OperatorWebApp:
    """WSGI application with only read-only GET routes."""

    def __init__(self, config: OperatorWebAppConfig):
        self.config = config.validate()

    def _snapshot(self, environ):
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        if set(query) - {"as_of"} or len(query.get("as_of", [])) > 1:
            raise WebAppConfigError("unsupported query parameters")
        as_of = self.config.default_as_of if "as_of" not in query else _parse_as_of(query["as_of"][0])
        return load_console_snapshot(runtime_root=self.config.runtime_root, as_of=as_of)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if method != "GET":
            return _response(start_response, "405 Method Not Allowed", "text/plain; charset=utf-8", b"Method not allowed\n")
        if path == "/healthz":
            return _response(start_response, "200 OK", "application/json; charset=utf-8", b'{"mode":"READ_ONLY_OPERATOR_UI","status":"UP"}\n')
        if path == "/about":
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _about_body().encode("utf-8"))
        if path not in {"/", "/api/snapshot"}:
            return _response(start_response, "404 Not Found", "text/plain; charset=utf-8", b"Not found\n")
        try:
            result = self._snapshot(environ)
        except (ConsoleError, ConsoleIdentityError, ConsoleSafetyError, WebAppConfigError):
            if path == "/api/snapshot":
                return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8", b'{"status":"FAIL_CLOSED_INCOMPLETE_EVIDENCE"}\n')
            return _response(start_response, "503 Service Unavailable", "text/html; charset=utf-8", _error_body().encode("utf-8"))
        if path == "/api/snapshot":
            return _response(start_response, "200 OK", "application/json; charset=utf-8", result.serialize().encode("utf-8"))
        return _response(start_response, "200 OK", "text/html; charset=utf-8", _dashboard_body(result).encode("utf-8"))


__all__ = ["ALLOWED_HOST", "OperatorWebApp", "OperatorWebAppConfig", "PORT_MAXIMUM", "PORT_MINIMUM", "REFRESH_MAXIMUM", "REFRESH_MINIMUM", "WebAppConfigError"]
