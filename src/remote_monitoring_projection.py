"""Explicit allowlist projection for authenticated remote monitoring."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
PROJECTION_TYPE = "ORB_LIVE_REMOTE_MONITORING"
_SOURCE_STATES = frozenset({"NO_AUTOMATIC_EVIDENCE", "AUTOMATIC_EVIDENCE_AVAILABLE"})
_TERMINAL_STATES = frozenset(
    {
        "WINDOW_COMPLETE_NO_CANDIDATE",
        "CANDIDATE_CAPTURED",
        "OPERATOR_STOPPED",
        "PROVIDER_FAILURE",
    }
)
_FILTER_EVIDENCE = frozenset({"AVAILABLE", "UNAVAILABLE"})
_FILTER_STATES = frozenset(
    {"VALID_DECISION", "NO_SIGNAL", "REJECTED", "DATA_INVALID", "DATA_INCOMPLETE", "PROVIDER_FAILURE"}
)
_FILTER_RULES = frozenset(
    {"OPENING_GAP", "OPENING_RELATIVE_VOLUME", "SPY_ABOVE_VWAP", "OPENING_RANGE_BREAKOUT", "ENTRY_WINDOW", "OTHER", "UNAVAILABLE"}
)
_RECORD_STAGES = frozenset({"COMPLETE", "RUN_STARTED", "CANDIDATE_CAPTURED"})
_RECORD_SOURCES = frozenset({"AUTOMATIC_RUNTIME"})
_EVENT_SEVERITIES = frozenset({"INFO", "WARNING", "REVIEW", "ERROR"})
_EVENT_COMPONENTS = frozenset({"morning_watcher", "strategy"})
_EVENT_TYPES = frozenset({"WATCH_SESSION_COMPLETED", "SCANNER_EVALUATED", "CANDIDATE_DETECTED"})
_RECORD_STATUSES = frozenset({"EVALUATED", "WINDOW_COMPLETE_NO_CANDIDATE", "CANDIDATE_CAPTURED", "OPERATOR_STOPPED", "PROVIDER_FAILURE"})
_WATCHER_STATES = frozenset({"RUNNING", "NOT_RUNNING", "STOP_REQUESTED", "BLOCKED", "FAILED", "OWNERSHIP_UNKNOWN", "UNAVAILABLE"})
_LAST_RUN_RESULTS = frozenset({"NO_CANDIDATE", "CANDIDATE", "OPERATOR_STOPPED", "NOT_RUN_OUTSIDE_ENTRY_WINDOW", "FAILED"})


class RemoteProjectionError(ValueError):
    """Local projection cannot be exported through the remote allowlist."""


def _utc_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or len(value) > 48:
        raise RemoteProjectionError(f"invalid {field}")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RemoteProjectionError(f"invalid {field}") from exc
    if parsed.tzinfo is None:
        raise RemoteProjectionError(f"invalid {field}")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_text(value: Any, field: str, *, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum or any(ord(char) < 32 for char in value):
        raise RemoteProjectionError(f"invalid {field}")
    return value


def _safe_identifier(value: Any, field: str, *, maximum: int = 128) -> str:
    result = _safe_text(value, field, maximum=maximum)
    if not all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_:-" for char in result):
        raise RemoteProjectionError(f"invalid {field}")
    return result

def _safe_code(value: Any, field: str, allowed: frozenset[str] | None = None) -> str:
    result = _safe_text(value, field)
    if not all(char in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_:-" for char in result):
        raise RemoteProjectionError(f"invalid {field}")
    if allowed is not None and result not in allowed:
        raise RemoteProjectionError(f"unsupported {field}")
    return result


def _symbol(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.isascii() or not value.isupper() or not 1 <= len(value) <= 12 or not value.isalpha():
        raise RemoteProjectionError("invalid symbol")
    return value


def _project_filter_fact(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RemoteProjectionError("invalid filter fact")
    return {
        "symbol": _symbol(value.get("symbol")) or "",
        "state": _safe_code(value.get("state"), "filter state", _FILTER_STATES),
        "failedRule": _safe_code(value.get("failedRule"), "failed rule", _FILTER_RULES),
    }


def _project_evaluation(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteProjectionError("invalid evaluation")
    evidence = _safe_code(value.get("filterEvidence"), "filter evidence", _FILTER_EVIDENCE)
    facts = value.get("filterFacts")
    if not isinstance(facts, list) or len(facts) > 10:
        raise RemoteProjectionError("invalid filter facts")
    projected = [_project_filter_fact(item) for item in facts]
    if evidence == "AVAILABLE" and not projected:
        raise RemoteProjectionError("available filter evidence is empty")
    return {
        "scheduledAt": _utc_timestamp(value.get("scheduledAt"), "evaluation timestamp"),
        "filterEvidence": evidence,
        "filterFacts": projected,
    }


def _project_session(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteProjectionError("invalid session")
    evaluations = value.get("evaluations")
    count = value.get("evaluationCount")
    if not isinstance(evaluations, list) or len(evaluations) > 17 or type(count) is not int or count != len(evaluations):
        raise RemoteProjectionError("invalid session evaluations")
    return {
        "sessionId": _safe_identifier(value.get("sessionId"), "session id"),
        "startedAt": _utc_timestamp(value.get("startedAt"), "session start"),
        "completedAt": _utc_timestamp(value.get("completedAt"), "session completion"),
        "terminalState": _safe_code(value.get("terminalState"), "terminal state", _TERMINAL_STATES),
        "stopReason": _safe_code(value.get("stopReason"), "stop reason"),
        "evaluationCount": count,
        "evaluations": [_project_evaluation(item) for item in evaluations],
    }


def _project_record(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RemoteProjectionError("invalid record")
    source = _safe_code(value.get("source"), "record source", _RECORD_SOURCES)
    return {
        "source": source,
        "status": _safe_code(value.get("status"), "record status", _RECORD_STATUSES),
        "commissioningStage": _safe_code(value.get("commissioningStage"), "record stage", _RECORD_STAGES),
        "symbol": _symbol(value.get("symbol")),
        "reason": None if value.get("reason") is None else _safe_code(value.get("reason"), "record reason"),
        "eventTimestamp": _utc_timestamp(value.get("eventTimestamp"), "record timestamp"),
    }


def _event_component(value: Any) -> str:
    if value not in _EVENT_COMPONENTS:
        raise RemoteProjectionError("unsupported event component")
    return str(value)

def _project_event(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise RemoteProjectionError("invalid event")
    return {
        "eventId": _safe_identifier(value.get("eventId"), "event id"),
        "eventType": _safe_code(value.get("eventType"), "event type", _EVENT_TYPES),
        "occurredAt": _utc_timestamp(value.get("occurredAt"), "event timestamp"),
        "severity": _safe_code(value.get("severity"), "event severity", _EVENT_SEVERITIES),
        "component": _event_component(value.get("component")),
        "detailCode": _safe_code(value.get("detailCode"), "event detail code"),
        "source": _safe_code(value.get("source"), "event source", _RECORD_SOURCES),
    }


def utc_timestamp(value: datetime) -> str:
    normalized = value.astimezone(timezone.utc)
    return normalized.isoformat().replace("+00:00", "Z")


def _project_watcher(value: Mapping[str, Any] | None, *, observed_at: datetime) -> dict[str, Any]:
    if value is None:
        return {"currentState": "UNAVAILABLE", "observedAt": None, "lastRun": None}
    current = _safe_code(value.get("process_state"), "watcher state", _WATCHER_STATES - {"UNAVAILABLE"})
    last = value.get("last_run")
    projected_last = None
    if last is not None:
        if not isinstance(last, Mapping):
            raise RemoteProjectionError("invalid last run")
        result = _safe_code(last.get("result"), "last run result", _LAST_RUN_RESULTS)
        terminal = last.get("terminal_state")
        stop_reason = last.get("stop_reason")
        projected_last = {
            "result": result,
            "terminalState": None if terminal is None else _safe_code(terminal, "last run terminal state", _TERMINAL_STATES),
            "startedAt": None if last.get("started_at") is None else _utc_timestamp(last.get("started_at"), "last run start"),
            "finishedAt": None if last.get("finished_at") is None else _utc_timestamp(last.get("finished_at"), "last run finish"),
            "stopReason": None if stop_reason is None else _safe_code(stop_reason, "last run stop reason"),
        }
    return {"currentState": current, "observedAt": utc_timestamp(observed_at), "lastRun": projected_last}

def build_remote_projection(history: Mapping[str, Any], *, generated_at: datetime, watcher_status: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Copy only fields explicitly approved for remote display."""
    if not isinstance(history, Mapping) or history.get("schemaVersion") != 1:
        raise RemoteProjectionError("runtime history schema is invalid")
    source_status = history.get("status")
    if source_status not in _SOURCE_STATES:
        raise RemoteProjectionError("runtime history status is invalid")
    if generated_at.tzinfo is None:
        raise RemoteProjectionError("generation time must be timezone aware")
    sessions, records, events = history.get("sessions"), history.get("records"), history.get("events")
    if not all(isinstance(items, list) for items in (sessions, records, events)):
        raise RemoteProjectionError("runtime history collections are invalid")
    if len(sessions) > 366 or len(records) > 10000 or len(events) > 10000:
        raise RemoteProjectionError("runtime history exceeds remote bounds")
    return {
        "schemaVersion": SCHEMA_VERSION,
        "projectionType": PROJECTION_TYPE,
        "generatedAt": utc_timestamp(generated_at),
        "sourceStatus": source_status,
        "capabilities": {"monitoringRead": True, "localControl": False, "brokerWrite": False, "authorization": False},
        "watcher": _project_watcher(watcher_status, observed_at=generated_at),
        "sessions": [_project_session(item) for item in sessions],
        "records": [_project_record(item) for item in records],
        "events": [_project_event(item) for item in events],
    }


def validate_remote_projection(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schemaVersion") != SCHEMA_VERSION or value.get("projectionType") != PROJECTION_TYPE:
        raise RemoteProjectionError("remote projection schema is invalid")
    # Re-apply the same allowlist to persisted data. No extra key can cross the boundary.
    allowed = {"schemaVersion", "projectionType", "generatedAt", "sourceStatus", "capabilities", "watcher", "sessions", "records", "events"}
    if set(value) != allowed:
        raise RemoteProjectionError("remote projection contains unapproved fields")
    capabilities = value.get("capabilities")
    if capabilities != {"monitoringRead": True, "localControl": False, "brokerWrite": False, "authorization": False}:
        raise RemoteProjectionError("remote capabilities are invalid")
    watcher = value.get("watcher")
    if not isinstance(watcher, Mapping):
        raise RemoteProjectionError("remote watcher projection is invalid")
    last_run = watcher.get("lastRun")
    if last_run is not None and not isinstance(last_run, Mapping):
        raise RemoteProjectionError("remote last-run projection is invalid")
    persisted_watcher = {
        "process_state": watcher.get("currentState"),
        "last_run": None if last_run is None else {
            "result": last_run.get("result"),
            "terminal_state": last_run.get("terminalState"),
            "started_at": last_run.get("startedAt"),
            "finished_at": last_run.get("finishedAt"),
            "stop_reason": last_run.get("stopReason"),
        },
    }
    regenerated = build_remote_projection(
        {"schemaVersion": 1, "status": value.get("sourceStatus"), "sessions": value.get("sessions"), "records": value.get("records"), "events": value.get("events")},
        generated_at=datetime.fromisoformat(_utc_timestamp(value.get("generatedAt"), "generation time").replace("Z", "+00:00")),
        watcher_status=persisted_watcher if watcher.get("currentState") != "UNAVAILABLE" else None,
    )
    return regenerated


def read_remote_projection(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RemoteProjectionError("remote projection is unavailable") from exc
    return validate_remote_projection(value)


def write_remote_projection(value: Mapping[str, Any], path: Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="orb-remote-", suffix=".json", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
        os.replace(temporary, target)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


__all__ = ["PROJECTION_TYPE", "RemoteProjectionError", "build_remote_projection", "read_remote_projection", "utc_timestamp", "validate_remote_projection", "write_remote_projection"]
