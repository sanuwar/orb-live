from __future__ import annotations

import ast
import json
from datetime import timedelta
from pathlib import Path

import pytest

from scripts import build_live_candidate_commissioning_handoff as subject
from src.broker_snapshot import account_fingerprint
from src.execution_safety_governance import OperatorAcknowledgements
from tests.test_attended_safety_evaluation_bridge import NOW, TRUSTED, candidate
from tests.test_live_attended_preflight_inputs import clear_observation, snapshot

SHA = "a" * 40
ACKS = OperatorAcknowledgements(True, True, True, True)


def evidence(**changes):
    value = candidate()
    decision = {
        "shadow_run_id": value.shadow_run_id,
        "decision_id": value.decision_id,
        "symbol": value.symbol,
        "trading_date": value.trading_date.isoformat(),
        "strategy_version": value.strategy_version,
        "implementation_digest": value.implementation_digest,
        "market_data_digest": value.market_data_digest,
        "opening_range_identity": value.opening_range_identity,
        "signal_timestamp": value.signal_timestamp.isoformat(),
        "decision_type": value.decision_type.value,
        "proposed_entry": format(value.proposed_entry, "f"),
        "proposed_stop": format(value.proposed_stop, "f"),
        "proposed_target": format(value.proposed_target, "f"),
        "reason": value.reason,
        "market_data_quality": value.market_data_quality.value,
        "exchange_session": value.exchange_session,
        "created_at": value.created_at.isoformat(),
    }
    result = {
        "run_mode": "LIVE_SHADOW",
        "provider": "alpaca",
        "strategy_version": value.strategy_version,
        "implementation_digest": value.implementation_digest,
        "reviewed_sha": SHA,
        "candidate_summary": {
            "candidate_count": 1,
            "candidate_decisions": [{"symbol": value.symbol, "decision_id": value.decision_id}],
        },
        "shadow_trading_decision": decision,
    }
    result.update(changes)
    return result


async def observed(**_):
    return subject.ReadOnlyInputs(
        snapshot(), account_fingerprint("TEST12345678"), clear_observation(), NOW,
    )


@pytest.fixture(autouse=True)
def trusted_runtime(monkeypatch):
    monkeypatch.setattr(subject, "resolve_head_sha", lambda root: SHA)
    monkeypatch.setattr(subject, "checkpoint_implementation_digest", lambda root: TRUSTED)


def build(raw=None, **kwargs):
    return subject.build_candidate_handoff(
        raw or evidence(), release_sha=kwargs.pop("release_sha", SHA), acknowledgements=ACKS,
        observer=kwargs.pop("observer", observed), clock=kwargs.pop("clock", lambda: NOW), **kwargs,
    )


def test_authentic_long_candidate_reaches_persisted_sanitized_handoff(tmp_path):
    artifacts = build()
    paths = subject.persist_artifacts(artifacts, tmp_path / "handoff")
    handoff = json.loads(paths["handoff"].read_text(encoding="utf-8"))

    assert artifacts.handoff.handoff_state.value == "HANDOFF_READY"
    assert artifacts.handoff.quantity == "1" and artifacts.handoff.intended_stop == "99"
    assert handoff["broker_write_count"] == 0
    assert handoff["execution_authorized"] is False
    assert handoff["protection_grounded"] is False
    assert handoff["authority_ceiling"] == "ATTENDED_COMMISSIONING_ONLY"
    assert set(paths) == {"review", "dry_run", "preflight", "live_preflight", "safety_evaluation", "handoff"}


@pytest.mark.parametrize("decision_type", ["NO_SIGNAL", "INVALID", "REJECTED"])
def test_non_candidate_evidence_cannot_produce_handoff(decision_type):
    raw = evidence()
    raw["shadow_trading_decision"]["decision_type"] = decision_type
    with pytest.raises(subject.CandidateHandoffError):
        build(raw)


def test_ambiguous_stale_wrong_strategy_and_runtime_mismatches_fail_closed():
    ambiguous = evidence()
    ambiguous["candidate_summary"]["candidate_count"] = 2
    with pytest.raises(subject.CandidateHandoffError):
        build(ambiguous)
    wrong_strategy = evidence()
    wrong_strategy["shadow_trading_decision"]["strategy_version"] = "other"
    with pytest.raises(subject.CandidateHandoffError):
        build(wrong_strategy)
    with pytest.raises(subject.CandidateHandoffError):
        build(clock=lambda: NOW + timedelta(days=1))
    with pytest.raises(subject.CandidateHandoffError):
        build(release_sha="b" * 40)
    source_mismatch = evidence(reviewed_sha="b" * 40)
    with pytest.raises(subject.CandidateHandoffError):
        build(source_mismatch)
    mismatch = evidence()
    mismatch["implementation_digest"] = "c" * 64
    mismatch["shadow_trading_decision"]["implementation_digest"] = "c" * 64
    with pytest.raises(subject.CandidateHandoffError):
        build(mismatch)


async def stale_snapshot(**_):
    return subject.ReadOnlyInputs(
        snapshot(completed_at=NOW - timedelta(seconds=31)),
        account_fingerprint("TEST12345678"), clear_observation(), NOW,
    )


async def stale_suspension(**_):
    return subject.ReadOnlyInputs(
        snapshot(), account_fingerprint("TEST12345678"),
        clear_observation(observed_at=NOW - timedelta(seconds=31)), NOW,
    )


@pytest.mark.parametrize("observer", [stale_snapshot, stale_suspension])
def test_invalid_read_only_snapshot_or_suspension_blocks(observer):
    with pytest.raises(subject.CandidateHandoffError):
        build(observer=observer)


def test_account_mismatch_and_missing_acknowledgements_block():
    async def bad_account(**_):
        return subject.ReadOnlyInputs(snapshot(), "f" * 64, clear_observation(), NOW)

    with pytest.raises(subject.CandidateHandoffError):
        build(observer=bad_account)
    with pytest.raises(subject.CandidateHandoffError):
        subject._acknowledgements(["operator_present"])


def test_next_command_preserves_inputs_and_all_explicit_acknowledgements(tmp_path):
    command = subject._next_command(
        live_shadow=Path("candidate.json"), handoff=tmp_path / "handoff.json",
        release_sha=SHA, suspension_db=tmp_path / "suspension.sqlite",
    )
    assert command.startswith("python scripts/run_production_read_only_rehearsal.py --production-read-only")
    assert "--live-shadow candidate.json" in command
    assert "--handoff" in command and "--suspension-db" in command
    for acknowledgement in subject._ACKS:
        assert f"--ack {acknowledgement}" in command


def test_cli_composition_reuses_existing_builders_and_has_no_write_or_adapter_reachability():
    tree = ast.parse(Path(subject.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    calls = [node.func.id for node in ast.walk(tree) if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    for builder in (
        "build_shadow_candidate_commissioning_review",
        "build_supervised_micro_live_dry_run_plan",
        "build_supervised_micro_live_preflight",
        "build_live_attended_preflight_result",
        "build_attended_safety_evaluation_result",
        "build_attended_commissioning_handoff",
    ):
        assert builder in calls
    assert not (names & {"RobinhoodCommissioningAdapter", "place_equity_order", "cancel_equity_order", "replace_equity_order", "flatten"})
    assert "_call" not in Path(subject.__file__).read_text(encoding="utf-8")
