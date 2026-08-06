"""Minimal local WSGI read-only operator application."""

from __future__ import annotations

import html
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

from src.offline_path_safety import is_repository_data_path
from src.operator_snapshot_history import HistoryError, HistoryNotFound, SnapshotHistory, compare_snapshots
from src.operator_event_timeline import EVENT_DETAIL_KEYS, EVENT_LIST_KEYS, OperatorEventTimeline, TimelineError, TimelineNotFound
from src.operator_incident_export import IncidentError, IncidentNotFound, OperatorIncidentStore, render_incident_html
from src.operator_app_store import (OperatorAppStoreError, inspect_store_identity, verify_store_integrity, list_snapshots, get_snapshot, list_events, get_event, get_incident_projection, snapshot_projection, event_projection)
from src.operator_store_status import read_store_status
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
    backend_mode: str = "FILE_FIXTURE"
    database_path: Path | None = None

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
        if self.backend_mode not in {"FILE_FIXTURE", "SQLITE_READ_MODEL"}:
            raise WebAppConfigError("backend mode is invalid")
        if self.backend_mode == "SQLITE_READ_MODEL":
            if self.database_path is None:
                raise WebAppConfigError("SQLite database is required")
            try:
                identity = inspect_store_identity(self.database_path)
                if identity["schema_version"] != 1 or identity["release_commit_sha"] != resolve_head_sha(REPOSITORY_ROOT) or identity["implementation_digest"] != checkpoint_implementation_digest(REPOSITORY_ROOT) or not verify_store_integrity(self.database_path):
                    raise WebAppConfigError("SQLite read model identity is invalid")
            except (OperatorAppStoreError, OSError, KeyError):
                raise WebAppConfigError("SQLite read model is unavailable") from None
        return self


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise WebAppConfigError("as-of timestamp is invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WebAppConfigError("as-of timestamp must be timezone-aware")
    return parsed

class _SQLiteHistory:
    def __init__(self, database): self.database = database
    def query(self, *, as_of, status=None, blocker=None, from_value=None, to_value=None, page=1, page_size=25):
        rows, _ = list_snapshots(self.database, page=1, page_size=100); items=[snapshot_projection(x) for x in rows]
        if status: items=[x for x in items if x.to_dict()["operational_status"]==status or x.to_dict()["supervised_readiness_status"]==status]
        if blocker: items=[x for x in items if blocker in x.to_dict()["blocker_reasons"]]
        start=(page-1)*page_size; return tuple(items[start:start+page_size]),len(items)
    def get(self, *, snapshot_id, as_of):
        try:return snapshot_projection(get_snapshot(self.database,snapshot_id))
        except OperatorAppStoreError: raise HistoryNotFound("snapshot is not present") from None
    def current(self, as_of):
        rows,_=list_snapshots(self.database,page=1,page_size=1); return snapshot_projection(rows[0]).snapshot if rows else None

class _SQLiteTimeline:
    def __init__(self, database): self.database=database
    def query(self, *, as_of, event_type=None, severity=None, from_value=None, to_value=None, page=1, page_size=25):
        rows,_=list_events(self.database,event_type=event_type,severity=severity,page=1,page_size=100); events=[event_projection(x) for x in rows]; start=(page-1)*page_size; return tuple(events[start:start+page_size]),len(events)
    def get(self, *, event_id, as_of):
        try:return event_projection(get_event(self.database,event_id))
        except OperatorAppStoreError: raise TimelineNotFound("event is not present") from None

class _SQLiteIncidents:
    def __init__(self, database): self.database=database
    def get(self, *, incident_id, as_of):
        try:return get_incident_projection(self.database,incident_id)
        except OperatorAppStoreError: raise IncidentNotFound("incident is not present") from None


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

def _store_status_body(status) -> str:
    template=(TEMPLATE_ROOT / "operator_store_status.html").read_text(encoding="utf-8"); d=status.to_dict()
    values={"STATUS":d["status"],"SCHEMA":str(d["schema_version"] or "unknown"),"SHA":d["release_commit_sha_short"] or "unknown","DIGEST":d["implementation_digest_short"] or "unknown","INTEGRITY":d["integrity_status"],"IDENTITY":d["identity_status"],"IMPORTS":str(d["import_batch_count"]),"SNAPSHOTS":str(d["snapshot_count"]),"EVENTS":str(d["event_count"]),"INCIDENTS":str(d["incident_count"]),"LINKS":str(d["evidence_link_count"]),"NEWEST_IMPORT":str(d["newest_imported_at"] or "none"),"NEWEST_SNAPSHOT":str(d["newest_snapshot_at"] or "none"),"NEWEST_EVENT":str(d["newest_event_at"] or "none"),"NEWEST_INCIDENT":str(d["newest_incident_at"] or "none"),"BLOCKERS":"".join("<li>"+html.escape(x)+"</li>" for x in d["blocker_reasons"]) or "<li>None</li>"}
    for key,value in values.items(): template=template.replace("{{"+key+"}}",html.escape(value) if key!="BLOCKERS" else value)
    return template


def _history_list_body(items, *, page: int, page_size: int, total: int) -> str:
    template = (TEMPLATE_ROOT / "operator_history.html").read_text(encoding="utf-8")
    rows = "".join(
        "<tr>" + "".join((f"<td>{html.escape(str(value))}</td>" if key != "snapshot_id" else f'<td><a href="/history/{html.escape(str(value), quote=True)}">{html.escape(str(value))}</a></td>') for key, value in (
            ("snapshot_id", item["snapshot_id"]), ("generated_as_of", item["generated_as_of"]), ("operational_status", item["operational_status"]),
            ("supervised_readiness_status", item["supervised_readiness_status"]), ("recommended_operator_action", item["recommended_operator_action"]),
            ("blocker_reasons", ", ".join(item["blocker_reasons"])), ("suspension_status", item["suspension_status"]),
            ("heartbeat_status", item["heartbeat_status"]), ("alert_status", item["alert_status"]), ("recovery_status", item["recovery_status"]),
        )) + "</tr>"
        for item in items
    ) or "<tr><td colspan=10>No snapshots found.</td></tr>"
    values = {"ROWS": rows, "PAGE": str(page), "PAGE_SIZE": str(page_size), "TOTAL": str(total)}
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _history_detail_body(item, comparison: dict[str, Any]) -> str:
    template = (TEMPLATE_ROOT / "operator_history_detail.html").read_text(encoding="utf-8")
    data = item.to_dict()
    values = {
        "ID": html.escape(item.snapshot_id), "TIMESTAMP": html.escape(data["generated_as_of"]),
        "STATUS": html.escape(data["operational_status"]), "READINESS": html.escape(data["supervised_readiness_status"]),
        "ACTION": html.escape(data["recommended_operator_action"]), "BLOCKERS": html.escape(", ".join(data["blocker_reasons"]) or "None"),
        "SUSPENSION": html.escape(data["suspension_status"]), "HEARTBEAT": html.escape(data["heartbeat_status"]),
        "ALERT": html.escape(data["alert_status"]), "RECOVERY": html.escape(data["recovery_status"]),
        "COMPARISON": "".join(f"<li>{html.escape(key.replace('_', ' ').title())}: {html.escape(', '.join(value) if isinstance(value, list) else str(value))}</li>" for key, value in comparison.items()),
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _timeline_list_body(events, *, page: int, page_size: int, total: int) -> str:
    template = (TEMPLATE_ROOT / "operator_timeline.html").read_text(encoding="utf-8")
    rows = "".join(
        f'<tr><td><a href="/timeline/{html.escape(event.event_id, quote=True)}">{html.escape(event.occurred_at)}</a></td><td>{html.escape(event.severity.value)}</td><td>{html.escape(event.event_type.value)}</td><td>{html.escape(event.headline)}</td><td>{html.escape(event.source_component)}</td><td>{html.escape(event.operator_action)}</td><td>{html.escape(", ".join(event.blocker_reasons) or "None")}</td></tr>'
        for event in events
    ) or "<tr><td colspan=7>No events found.</td></tr>"
    for key, value in {"ROWS": rows, "PAGE": str(page), "PAGE_SIZE": str(page_size), "TOTAL": str(total)}.items():
        template = template.replace("{{" + key + "}}", value)
    return template


def _timeline_detail_body(event) -> str:
    template = (TEMPLATE_ROOT / "operator_timeline_detail.html").read_text(encoding="utf-8")
    data = event.to_dict()
    values = {
        "ID": html.escape(data["event_id"]), "TYPE": html.escape(data["event_type"]), "SEVERITY": html.escape(data["severity"]),
        "OCCURRED": html.escape(data["occurred_at"]), "SOURCE": html.escape(data["source_snapshot_id"]),
        "COMPONENT": html.escape(data["source_component"]), "HEADLINE": html.escape(data["headline"]),
        "CODE": html.escape(data["detail_code"]), "STATUS": html.escape(data["operational_status"]),
        "READINESS": html.escape(data["supervised_readiness_status"]), "BLOCKERS": html.escape(", ".join(data["blocker_reasons"]) or "None"),
        "ACTION": html.escape(data["operator_action"]),
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template


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

    def _history_request(self, environ):
        query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        allowed = {"as_of", "status", "blocker", "from", "to", "page", "page_size"}
        if set(query) - allowed or any(len(values) != 1 for values in query.values()):
            raise HistoryError("history query is invalid")
        as_of = self.config.default_as_of if "as_of" not in query else _parse_as_of(query["as_of"][0])
        def integer(name: str, default: int) -> int:
            try:
                return default if name not in query else int(query[name][0])
            except ValueError:
                raise HistoryError("history pagination is invalid") from None
        history = _SQLiteHistory(self.config.database_path) if self.config.backend_mode == "SQLITE_READ_MODEL" else SnapshotHistory(runtime_root=self.config.runtime_root, repository_root=REPOSITORY_ROOT)
        items, total = history.query(as_of=as_of, status=query.get("status", [None])[0], blocker=query.get("blocker", [None])[0], from_value=query.get("from", [None])[0], to_value=query.get("to", [None])[0], page=integer("page", 1), page_size=integer("page_size", 25))
        return as_of, history, items, total, integer("page", 1), integer("page_size", 25)

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/")
        if method != "GET":
            return _response(start_response, "405 Method Not Allowed", "text/plain; charset=utf-8", b"Method not allowed\n")
        if path == "/healthz":
            return _response(start_response, "200 OK", "application/json; charset=utf-8", b'{"mode":"READ_ONLY_OPERATOR_UI","status":"UP"}\n')
        if path == "/about":
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _about_body().encode("utf-8"))
        if path in {"/data-status", "/api/data-status"}:
            status = read_store_status(self.config.database_path, generated_as_of=self.config.default_as_of.isoformat()) if self.config.backend_mode == "SQLITE_READ_MODEL" else read_store_status(None, generated_as_of=self.config.default_as_of.isoformat())
            if path == "/api/data-status":
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (json.dumps(status.to_dict(),sort_keys=True,separators=(",",":"))+"\n").encode("utf-8"))
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _store_status_body(status).encode("utf-8"))
        if path in {"/history", "/api/history"}:
            try:
                as_of, history, items, total, page, page_size = self._history_request(environ)
            except (HistoryError, WebAppConfigError):
                if path == "/api/history":
                    return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8", b'{"status":"FAIL_CLOSED_HISTORY_LOAD"}\n')
                return _response(start_response, "503 Service Unavailable", "text/html; charset=utf-8", _error_body().encode("utf-8"))
            if path == "/api/history":
                payload = {"schema_version": 1, "page": page, "page_size": page_size, "total_count": total, "items": [item.to_dict() for item in items]}
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _history_list_body([item.to_dict() for item in items], page=page, page_size=page_size, total=total).encode("utf-8"))
        if path in {"/timeline", "/api/timeline"}:
            try:
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                allowed = {"as_of", "event_type", "severity", "from", "to", "page", "page_size"}
                if set(query) - allowed or any(len(values) != 1 for values in query.values()):
                    raise TimelineError("timeline query is invalid")
                as_of = self.config.default_as_of if "as_of" not in query else _parse_as_of(query["as_of"][0])
                def integer(name: str, default: int) -> int:
                    try:
                        return default if name not in query else int(query[name][0])
                    except ValueError:
                        raise TimelineError("timeline pagination is invalid") from None
                timeline = _SQLiteTimeline(self.config.database_path) if self.config.backend_mode == "SQLITE_READ_MODEL" else OperatorEventTimeline(SnapshotHistory(runtime_root=self.config.runtime_root, repository_root=REPOSITORY_ROOT))
                events, total = timeline.query(as_of=as_of, event_type=query.get("event_type", [None])[0], severity=query.get("severity", [None])[0], from_value=query.get("from", [None])[0], to_value=query.get("to", [None])[0], page=integer("page", 1), page_size=integer("page_size", 25))
                page, page_size = integer("page", 1), integer("page_size", 25)
            except TimelineError:
                if path == "/api/timeline":
                    return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8", b'{"status":"FAIL_CLOSED_TIMELINE_LOAD"}\n')
                return _response(start_response, "503 Service Unavailable", "text/html; charset=utf-8", _error_body().encode("utf-8"))
            if path == "/api/timeline":
                payload = {"schema_version": 1, "page": page, "page_size": page_size, "total_count": total, "items": [event.to_dict() for event in events]}
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _timeline_list_body(events, page=page, page_size=page_size, total=total).encode("utf-8"))
        history_match = path.startswith("/history/") or path.startswith("/api/history/")
        if history_match:
            api_detail = path.startswith("/api/")
            snapshot_id = path.split("/", 3)[-1] if api_detail else path.split("/", 2)[-1]
            try:
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                if set(query) - {"as_of"} or len(query.get("as_of", [])) > 1:
                    raise HistoryError("history query is invalid")
                as_of = self.config.default_as_of if "as_of" not in query else _parse_as_of(query["as_of"][0])
                history = _SQLiteHistory(self.config.database_path) if self.config.backend_mode == "SQLITE_READ_MODEL" else SnapshotHistory(runtime_root=self.config.runtime_root, repository_root=REPOSITORY_ROOT)
                item = history.get(snapshot_id=snapshot_id, as_of=as_of)
                current = history.current(as_of) if self.config.backend_mode == "SQLITE_READ_MODEL" else load_console_snapshot(runtime_root=self.config.runtime_root, as_of=as_of)
            except HistoryNotFound:
                return _response(start_response, "404 Not Found", "text/plain; charset=utf-8", b"Not found\n")
            except (HistoryError, ConsoleError, ConsoleIdentityError, ConsoleSafetyError):
                return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8" if api_detail else "text/html; charset=utf-8", b'{"status":"FAIL_CLOSED_HISTORY_LOAD"}\n' if api_detail else _error_body().encode("utf-8"))
            if api_detail:
                payload = {"schema_version": 1, "snapshot_id": item.snapshot_id, "snapshot": item.snapshot.to_dict(), "comparison": compare_snapshots(current, item.snapshot)}
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _history_detail_body(item, compare_snapshots(current, item.snapshot)).encode("utf-8"))
        timeline_match = path.startswith("/timeline/") or path.startswith("/api/timeline/")
        if timeline_match:
            api_detail = path.startswith("/api/")
            event_id = path.split("/", 3)[-1] if api_detail else path.split("/", 2)[-1]
            try:
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                if set(query) - {"as_of"} or len(query.get("as_of", [])) > 1:
                    raise TimelineError("timeline query is invalid")
                as_of = self.config.default_as_of if "as_of" not in query else _parse_as_of(query["as_of"][0])
                timeline = _SQLiteTimeline(self.config.database_path) if self.config.backend_mode == "SQLITE_READ_MODEL" else OperatorEventTimeline(SnapshotHistory(runtime_root=self.config.runtime_root, repository_root=REPOSITORY_ROOT))
                event = timeline.get(event_id=event_id, as_of=as_of)
            except TimelineNotFound:
                return _response(start_response, "404 Not Found", "text/plain; charset=utf-8", b"Not found\n")
            except TimelineError:
                return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8" if api_detail else "text/html; charset=utf-8", b'{"status":"FAIL_CLOSED_TIMELINE_LOAD"}\n' if api_detail else _error_body().encode("utf-8"))
            if api_detail:
                payload = {"schema_version": 1, "event": event.to_dict()}
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8"))
            return _response(start_response, "200 OK", "text/html; charset=utf-8", _timeline_detail_body(event).encode("utf-8"))
        incident_match = path.startswith("/incidents/") or path.startswith("/api/incidents/")
        if incident_match:
            api_detail = path.startswith("/api/")
            export_json = path.endswith("/export.json")
            export_html = path.endswith("/export.html")
            prefix = "/api/incidents/" if api_detail else "/incidents/"
            incident_id = path.removeprefix(prefix).removesuffix("/export.json").removesuffix("/export.html")
            try:
                query = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
                if query:
                    raise IncidentError("incident query is invalid")
                store = _SQLiteIncidents(self.config.database_path) if self.config.backend_mode == "SQLITE_READ_MODEL" else OperatorIncidentStore(runtime_root=self.config.runtime_root, repository_root=REPOSITORY_ROOT)
                incident = store.get(incident_id=incident_id, as_of=self.config.default_as_of)
            except IncidentNotFound:
                return _response(start_response, "404 Not Found", "text/plain; charset=utf-8", b"Not found\n")
            except IncidentError:
                return _response(start_response, "503 Service Unavailable", "application/json; charset=utf-8" if api_detail or export_json else "text/html; charset=utf-8", b'{"status":"FAIL_CLOSED_INCIDENT_LOAD"}\n' if api_detail or export_json else _error_body().encode("utf-8"))
            if api_detail:
                return _response(start_response, "200 OK", "application/json; charset=utf-8", (incident.serialize()).encode("utf-8"))
            if export_json:
                response = _response(start_response, "200 OK", "application/json; charset=utf-8", incident.serialize().encode("utf-8"))
                return response
            html_body = render_incident_html(incident, template_path=TEMPLATE_ROOT / "operator_incident.html").encode("utf-8")
            return _response(start_response, "200 OK", "text/html; charset=utf-8", html_body)
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
