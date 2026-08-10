from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from src.attended_commissioning_handoff import (
    AttendedCommissioningHandoffState,
    build_attended_commissioning_handoff,
)
from src.supervised_micro_live_rehearsal import (
    SupervisedMicroLiveRehearsalState,
    build_supervised_micro_live_rehearsal,
    compute_supervised_micro_live_rehearsal_digest,
)
from tests.test_attended_commissioning_handoff import artifacts, handoff
from tests.test_attended_safety_evaluation_bridge import NOW, TRUSTED


def _json_handoff(value):
    payload = dict(value.__dict__)
    payload["handoff_state"] = value.handoff_state.value
    for key in ("required_operator_acknowledgements", "intended_protection_sequence", "blocking_reasons"):
        payload[key] = list(payload[key])
    return payload


def test_fixture_handoff_reaches_final_rehearsal_gate_without_authority():
    result = build_supervised_micro_live_rehearsal(handoff(), evidence_source="FIXTURE_REHEARSAL")
    assert result.rehearsal_state is SupervisedMicroLiveRehearsalState.READY_FOR_EXPLICIT_ATTENDED_WRITE_AUTHORIZATION
    assert result.broker_mutation_boundary == "RobinhoodCommissioningAdapter._call()"
    assert result.quantity == "1" and result.side == "buy" and result.order_type == "market"
    assert result.execution_authorized is False and result.protection_grounded is False
    assert result.broker_write_count == 0


def test_rejected_stale_and_source_invalid_handoffs_block():
    ready = handoff()
    stale = replace(ready, handoff_state=AttendedCommissioningHandoffState.SAFETY_DECISION_STALE)
    stale_result = build_supervised_micro_live_rehearsal(stale)
    assert stale_result.rehearsal_state is SupervisedMicroLiveRehearsalState.HANDOFF_STALE
    assert "HANDOFF_DIGEST_MISMATCH" in stale_result.blocking_reasons

    evaluation, dry_run, decision = artifacts()
    actual_stale = build_attended_commissioning_handoff(
        evaluation, dry_run=dry_run, decision=decision, evaluated_at=NOW + timedelta(seconds=31),
        release_sha="a" * 40, trusted_implementation_digest=TRUSTED,
    )
    actual_result = build_supervised_micro_live_rehearsal(actual_stale)
    assert actual_result.rehearsal_state is SupervisedMicroLiveRehearsalState.HANDOFF_STALE
    assert "SAFETY_DECISION_STALE" in actual_result.blocking_reasons


def test_handoff_not_allow_or_unsatisfied_acknowledgement_blocks():
    value = handoff()
    blocked = replace(value, safety_decision_result="DENY")
    result = build_supervised_micro_live_rehearsal(blocked)
    assert result.rehearsal_state is SupervisedMicroLiveRehearsalState.HANDOFF_INVALID
    assert "HANDOFF_DIGEST_MISMATCH" in result.blocking_reasons


def test_digest_is_deterministic_and_no_broker_write_reachability_exists():
    first = build_supervised_micro_live_rehearsal(handoff())
    second = build_supervised_micro_live_rehearsal(handoff())
    assert first == second
    assert compute_supervised_micro_live_rehearsal_digest(first) == first.rehearsal_digest
    source = Path(__file__).resolve().parents[1] / "src" / "supervised_micro_live_rehearsal.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "RobinhoodClient", "place_equity_order", "cancel_equity_order", "call_tool", "_call"})


def test_runner_consumes_saved_fixture_and_stops_before_write(tmp_path: Path):
    source = tmp_path / "handoff.json"
    output = tmp_path / "rehearsal.json"
    source.write_text(json.dumps(_json_handoff(handoff())), encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "scripts/run_supervised_micro_live_rehearsal.py", "--handoff", str(source), "--output", str(output), "--fixture"],
        capture_output=True, text=True, check=False, cwd=Path(__file__).resolve().parents[1],
    )
    assert completed.returncode == 0, completed.stderr
    assert "BROKER WRITE EXECUTED: NO" in completed.stdout
    evidence = json.loads(output.read_text(encoding="utf-8"))
    assert evidence["evidence_source"] == "FIXTURE_REHEARSAL"
    assert evidence["broker_write_count"] == 0
    assert evidence["execution_authorized"] is False
