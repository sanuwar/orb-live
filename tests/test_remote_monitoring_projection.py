from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.remote_monitoring_projection import (
    PROJECTION_TYPE,
    RemoteProjectionError,
    build_remote_projection,
    read_remote_projection,
    write_remote_projection,
)
from src.remote_monitoring_source import LOCAL_STATUS_ORIGIN, LOCAL_STATUS_URL, collect_remote_projection


NOW = datetime(2026, 8, 14, 14, 20, tzinfo=timezone.utc)


def history() -> dict:
    return {
        "schemaVersion": 1,
        "status": "AUTOMATIC_EVIDENCE_AVAILABLE",
        "private_top_level": "must-not-cross",
        "sessions": [{
            "sourceEvidenceId": "not-remotely-needed",
            "sessionId": "watch-safe",
            "startedAt": "2026-08-14T13:40:00Z",
            "completedAt": "2026-08-14T14:17:00Z",
            "terminalState": "CANDIDATE_CAPTURED",
            "stopReason": "CANDIDATE_CAPTURED",
            "evaluationCount": 1,
            "evaluations": [{"scheduledAt": "2026-08-14T14:15:00Z", "filterEvidence": "AVAILABLE", "filterFacts": [{"symbol": "NVDA", "state": "VALID_DECISION", "failedRule": "UNAVAILABLE", "account_id": "drop"}]}],
        }],
        "records": [{"journalRecordId": "drop", "runId": "drop", "source": "AUTOMATIC_RUNTIME", "status": "CANDIDATE_CAPTURED", "commissioningStage": "CANDIDATE_CAPTURED", "symbol": "NVDA", "reason": "LONG_CANDIDATE", "eventTimestamp": "2026-08-14T14:17:00Z", "quantity": 1}],
        "events": [{"eventId": "event-safe", "eventType": "CANDIDATE_DETECTED", "occurredAt": "2026-08-14T14:17:00Z", "severity": "REVIEW", "component": "strategy", "detailCode": "LONG_CANDIDATE", "source": "AUTOMATIC_RUNTIME", "raw_response": "drop"}],
    }


def watcher() -> dict:
    return {"service": "LOCAL_CONTROL_ONLY", "process_state": "NOT_RUNNING", "pid": 123, "readiness": {"credential": "drop"}, "last_run": {"result": "CANDIDATE", "terminal_state": "CANDIDATE_CAPTURED", "started_at": "2026-08-14T13:40:00Z", "finished_at": "2026-08-14T14:17:00Z", "stop_reason": "CANDIDATE_CAPTURED", "artifact_path": "drop"}}


def test_explicit_allowlist_preserves_monitoring_facts_and_drops_private_or_unneeded_fields():
    value = build_remote_projection(history(), generated_at=NOW, watcher_status=watcher())
    assert value["projectionType"] == PROJECTION_TYPE
    assert value["watcher"] == {"currentState": "NOT_RUNNING", "observedAt": "2026-08-14T14:20:00Z", "lastRun": {"result": "CANDIDATE", "terminalState": "CANDIDATE_CAPTURED", "startedAt": "2026-08-14T13:40:00Z", "finishedAt": "2026-08-14T14:17:00Z", "stopReason": "CANDIDATE_CAPTURED"}}
    assert value["records"][0]["symbol"] == "NVDA"
    serialized = json.dumps(value).casefold()
    for forbidden in ("private_top_level", "account_id", "credential", "raw_response", "artifact_path", "journalrecordid", "runid", '"pid"'):
        assert forbidden not in serialized
    assert value["capabilities"] == {"monitoringRead": True, "localControl": False, "brokerWrite": False, "authorization": False}


def test_missing_local_control_fails_closed_without_inventing_last_run():
    value = build_remote_projection(history(), generated_at=NOW, watcher_status=None)
    assert value["watcher"] == {"currentState": "UNAVAILABLE", "observedAt": None, "lastRun": None}


def test_invalid_or_ambiguous_source_facts_are_rejected():
    bad = history()
    bad["sessions"][0]["evaluations"][0]["filterFacts"][0]["failedRule"] = "INVENTED_SCORE"
    with pytest.raises(RemoteProjectionError):
        build_remote_projection(bad, generated_at=NOW, watcher_status=watcher())


def test_path_like_components_and_non_ascii_symbols_are_rejected():
    bad_component = history()
    bad_component["events"][0]["component"] = "C:/private"
    with pytest.raises(RemoteProjectionError):
        build_remote_projection(bad_component, generated_at=NOW, watcher_status=watcher())
    bad_symbol = history()
    bad_symbol["records"][0]["symbol"] = "Δ"
    with pytest.raises(RemoteProjectionError):
        build_remote_projection(bad_symbol, generated_at=NOW, watcher_status=watcher())

def test_persisted_projection_rejects_any_field_outside_schema(tmp_path: Path):
    target = tmp_path / "remote.json"
    value = build_remote_projection(history(), generated_at=NOW, watcher_status=watcher())
    write_remote_projection(value, target)
    assert read_remote_projection(target) == value
    value["filesystemPath"] = "C:/private"
    target.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RemoteProjectionError):
        read_remote_projection(target)


def test_collector_uses_injected_sanitized_status_and_never_needs_a_network_call(tmp_path: Path):
    source = tmp_path / "history.json"
    sanitized = history()
    sanitized.pop("private_top_level")
    sanitized["sessions"][0]["evaluations"][0]["filterFacts"][0].pop("account_id")
    sanitized["events"][0].pop("raw_response")
    source.write_text(json.dumps(sanitized), encoding="utf-8")
    value = collect_remote_projection(history_path=source, status_reader=watcher, clock=lambda: NOW)
    assert value["watcher"]["currentState"] == "NOT_RUNNING"
    assert LOCAL_STATUS_URL == "http://127.0.0.1:8766/api/local-control/status"
    assert LOCAL_STATUS_ORIGIN == "http://127.0.0.1:5173"
