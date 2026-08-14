from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_filter_projection import FilterProjectionError, project_filter_facts
from src.runtime_journal_projection import project_watch_summary


def evidence() -> dict:
    return {"symbol_results": {"AAPL": {"evaluation_state": "REJECTED", "strategy_diagnostics": {"failed_rule": "OPENING_GAP"}}, "MSFT": {"evaluation_state": "VALID_DECISION", "strategy_diagnostics": {"failed_rule": None}}}}


def test_safe_filter_facts_are_extracted_only_from_local_evidence(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"; artifact.write_text(json.dumps(evidence()), encoding="utf-8")
    assert project_filter_facts(str(artifact)) == [{"symbol": "AAPL", "state": "REJECTED", "failedRule": "OPENING_GAP"}, {"symbol": "MSFT", "state": "VALID_DECISION", "failedRule": "UNAVAILABLE"}]
    with pytest.raises(FilterProjectionError): project_filter_facts(str(tmp_path.parent / "outside.json"))


def test_watcher_projection_preserves_only_safe_filter_facts(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"; artifact.write_text(json.dumps(evidence()), encoding="utf-8")
    summary = {"started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T15:00:00Z", "final_state": "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "ENTRY_WINDOW_COMPLETE", "evaluations": [{"scheduled_at": "2026-08-13T13:40:00Z", "quality_outcome": "VALID", "candidate_decisions": [], "evidence_artifact": str(artifact)}]}
    projected = project_watch_summary(summary, artifact_name="watch.json")
    evaluation = projected["session"]["evaluations"][0]
    assert evaluation["filterEvidence"] == "AVAILABLE"
    assert evaluation["filterFacts"][0] == {"symbol": "AAPL", "state": "REJECTED", "failedRule": "OPENING_GAP"}
    rendered = json.dumps(projected)
    assert str(artifact) not in rendered
    assert "strategy_diagnostics" not in rendered