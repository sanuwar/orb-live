from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_journal_projection import RuntimeProjectionError, append_watch_artifact, append_watch_summary, read_history


def summary(*, candidate: bool = False) -> dict:
    evaluation = {"scheduled_at": "2026-08-13T13:40:00Z", "quality_outcome": "VALID", "candidate_decisions": []}
    if candidate:
        evaluation["candidate_decisions"] = [{"symbol": "AAPL", "decision_id": "decision-safe"}]
    return {"started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T15:00:00Z", "final_state": "CANDIDATE_CAPTURED" if candidate else "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "FIRST_GENUINE_LONG_CANDIDATE" if candidate else "ENTRY_WINDOW_COMPLETE", "evaluations": [evaluation], "broker_write_count": 0, "execution_authorized": False, "protection_grounded": False}


def test_no_candidate_projection_is_sanitized_idempotent_and_ordered(tmp_path: Path):
    history = tmp_path / "runtime-journal.json"
    first = append_watch_summary(summary(), artifact_name="watch.json", history_path=history)
    second = append_watch_summary(summary(), artifact_name="watch.json", history_path=history)
    assert first["status"] == "AUTOMATIC_EVIDENCE_AVAILABLE"
    assert second["status"] == "ALREADY_PROJECTED"
    assert len(second["sessions"]) == 1
    assert {record["source"] for record in second["records"]} == {"AUTOMATIC_RUNTIME"}
    assert [event["occurredAt"] for event in second["events"]] == sorted((event["occurredAt"] for event in second["events"]), reverse=True)
    assert "artifact" not in json.dumps(second).casefold()


def test_candidate_projection_records_candidate_only_when_authentic(tmp_path: Path):
    result = append_watch_summary(summary(candidate=True), artifact_name="candidate.json", history_path=tmp_path / "history.json")
    candidate = next(record for record in result["records"] if record["status"] == "CANDIDATE_CAPTURED" and record["symbol"] == "AAPL")
    assert candidate["symbol"] == "AAPL"
    assert candidate["commissioningStage"] == "CANDIDATE_CAPTURED"


def test_malformed_or_private_evidence_rejected_without_history_write(tmp_path: Path):
    history = tmp_path / "history.json"
    unsafe = summary(); unsafe["account_number"] = "private"
    with pytest.raises(RuntimeProjectionError):
        append_watch_summary(unsafe, artifact_name="unsafe.json", history_path=history)
    assert read_history(history)["status"] == "NO_AUTOMATIC_EVIDENCE"
    artifact = tmp_path / "broken.json"; artifact.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeProjectionError):
        append_watch_artifact(artifact, history_path=history)
