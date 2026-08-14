from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.runtime_filter_projection import FilterProjectionError, project_filter_facts, static_filter_eliminations
from src.runtime_journal_projection import RuntimeProjectionError, project_watch_summary


def result(*, state: str, rule: str | None, reason: str | None, gap: str = "1.25", rvol: str = "3.5", spy_enabled: bool = True) -> dict:
    return {
        "evaluation_state": state,
        "no_signal_or_rejection_reason": reason,
        "strategy_diagnostics": {
            "failed_rule": rule,
            "observed_gap_pct": gap,
            "required_gap_pct": "1.0",
            "observed_opening_relative_volume": rvol,
            "required_opening_relative_volume": "3.0",
            "spy_vwap_filter_enabled": spy_enabled,
        },
    }


def evidence() -> dict:
    return {"symbol_results": {
        "AAPL": result(state="REJECTED", rule="OPENING_GAP", reason="Opening gap is below the required threshold.", gap="0.5", rvol="0"),
        "MSFT": result(state="REJECTED", rule="OPENING_RANGE_BREAKOUT", reason="Completed bar has not broken above the opening-range high."),
    }}


def test_safe_filter_facts_preserve_authentic_progression_and_reason(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"
    artifact.write_text(json.dumps(evidence()), encoding="utf-8")
    facts = project_filter_facts(str(artifact))

    assert facts[0] == {
        "symbol": "AAPL", "state": "REJECTED", "failedRule": "OPENING_GAP",
        "rejectionReason": "Opening gap is below the required threshold.",
        "terminalRejectionReason": "Opening gap is below the required threshold.",
        "spyVwapEnabled": True,
        "gates": {"openingGap": "REJECTED", "openingRvol": "NOT_EVALUATED", "spyVwap": "NOT_EVALUATED", "orbBreakout": "NOT_EVALUATED"},
        "observedGapPct": "0.5", "requiredGapPct": "1.0", "observedOpeningRvol": "0", "requiredOpeningRvol": "3.0",
    }
    assert facts[1]["gates"] == {"openingGap": "PASSED", "openingRvol": "PASSED", "spyVwap": "PASSED", "orbBreakout": "WAITING"}
    assert facts[1]["terminalRejectionReason"] is None
    with pytest.raises(FilterProjectionError):
        project_filter_facts(str(tmp_path.parent / "outside.json"))


def test_watcher_projection_builds_runtime_owned_funnel_without_raw_reference(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"
    artifact.write_text(json.dumps(evidence()), encoding="utf-8")
    summary = {"started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T15:00:00Z", "final_state": "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "ENTRY_WINDOW_COMPLETE", "evaluations": [{"scheduled_at": "2026-08-13T13:40:00Z", "quality_outcome": "VALID", "candidate_decisions": [], "evidence_artifact": str(artifact)}]}
    projected = project_watch_summary(summary, artifact_name="watch.json")
    evaluation = projected["session"]["evaluations"][0]

    assert evaluation["filterEvidence"] == "AVAILABLE"
    assert evaluation["funnel"] == {
        "status": "AVAILABLE", "startingSymbolCount": 2, "survivorsAfterGap": 1,
        "survivorsAfterOpeningRvol": 1, "survivorsAfterSpyVwap": 1,
        "survivorsAfterBreakout": 0, "spyVwapEnabled": True,
        "gapMinimumPct": "1.0", "openingRvolMinimum": "3.0",
        "aliveSymbols": ["MSFT"],
        "rejectedSymbols": [{"symbol": "AAPL", "reason": "Opening gap is below the required threshold.", "failedRule": "OPENING_GAP"}],
        "unavailableSymbols": [],
    }
    rendered = json.dumps(projected)
    assert str(artifact) not in rendered
    assert "strategy_diagnostics" not in rendered


def test_missing_or_incomplete_facts_never_become_survivors(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"
    artifact.write_text(json.dumps({"symbol_results": {"AAPL": {"evaluation_state": "DATA_INCOMPLETE", "strategy_diagnostics": None, "no_signal_or_rejection_reason": "DATA_INCOMPLETE"}}}), encoding="utf-8")
    summary = {"started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T15:00:00Z", "final_state": "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "ENTRY_WINDOW_COMPLETE", "evaluations": [{"scheduled_at": "2026-08-13T13:40:00Z", "quality_outcome": "VALID", "candidate_decisions": [], "evidence_artifact": str(artifact)}]}
    funnel = project_watch_summary(summary, artifact_name="watch.json")["session"]["evaluations"][0]["funnel"]

    assert funnel["status"] == "INCOMPLETE"
    assert funnel["survivorsAfterGap"] is None
    assert funnel["aliveSymbols"] == []
    assert funnel["unavailableSymbols"] == ["AAPL"]


def test_static_elimination_requires_exact_evaluator_rule_state_and_reason():
    values = evidence()
    assert static_filter_eliminations(values, symbols=("AAPL", "MSFT")) == frozenset({"AAPL"})
    values["symbol_results"]["AAPL"]["no_signal_or_rejection_reason"] = "different"
    assert static_filter_eliminations(values, symbols=("AAPL", "MSFT")) == frozenset()


def test_contradictory_gate_evidence_fails_closed(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    value = evidence()
    value["symbol_results"]["AAPL"]["no_signal_or_rejection_reason"] = "Completed bar has not broken above the opening-range high."
    artifact = tmp_path / "contradictory.json"
    artifact.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(FilterProjectionError, match="contradictory"):
        project_filter_facts(str(artifact))


def test_early_terminal_summary_with_safe_request_counts_projects_normally(monkeypatch, tmp_path: Path):
    import src.runtime_filter_projection as projection

    monkeypatch.setattr(projection, "_EVIDENCE_ROOT", tmp_path.resolve())
    artifact = tmp_path / "evaluation.json"
    artifact.write_text(json.dumps(evidence()), encoding="utf-8")
    summary = {
        "started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T13:40:01Z",
        "final_state": "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "NO_SURVIVORS_AFTER_STATIC_FILTERS",
        "total_http_request_count": 21,
        "evaluations": [{"scheduled_at": "2026-08-13T13:40:00Z", "quality_outcome": "VALID", "candidate_decisions": [], "request_count": 21, "evidence_artifact": str(artifact)}],
    }
    projected = project_watch_summary(summary, artifact_name="watch.json")

    assert projected["session"]["stopReason"] == "NO_SURVIVORS_AFTER_STATIC_FILTERS"
    assert "request_count" not in json.dumps(projected)


def test_raw_request_or_response_fields_remain_rejected():
    summary = {"started_at": "2026-08-13T13:40:00Z", "completed_at": "2026-08-13T13:40:01Z", "final_state": "WINDOW_COMPLETE_NO_CANDIDATE", "stop_reason": "ENTRY_WINDOW_COMPLETE", "evaluations": [], "raw_request": "unsafe"}
    with pytest.raises(RuntimeProjectionError, match="unsafe watch evidence"):
        project_watch_summary(summary, artifact_name="watch.json")
