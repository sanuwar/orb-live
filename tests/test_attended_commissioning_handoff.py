from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path

from src.attended_commissioning_handoff import (
    AttendedCommissioningHandoffState, build_attended_commissioning_handoff,
    compute_attended_commissioning_handoff_digest,
)
from src.execution_safety_governance import (
    CommissioningGovernanceEvidence, CommissioningMode, ExecutionAction,
    ExecutionSafetyContext, evaluate_execution_safety,
)
from src.shadow_candidate_commissioning_review import build_shadow_candidate_commissioning_review
from src.supervised_micro_live_dry_run import build_supervised_micro_live_dry_run_plan
from tests.test_attended_safety_evaluation_bridge import (
    NOW, TRUSTED, allowed_acknowledgements, candidate, inputs, source_live_preflight,
)
from src.attended_safety_evaluation_bridge import build_attended_safety_evaluation_result


def artifacts():
    live = source_live_preflight()
    review = build_shadow_candidate_commissioning_review(
        candidate(), reviewed_at=NOW, trusted_implementation_digest=TRUSTED,
    )
    dry_run = build_supervised_micro_live_dry_run_plan(
        review, account_fingerprint_reference=live.account_fingerprint_reference,
        trusted_implementation_digest=TRUSTED,
    )
    safety_inputs = inputs(
        acknowledgements=allowed_acknowledgements(), attended=True, source_hash=True,
    )
    evaluation = build_attended_safety_evaluation_result(
        live, inputs=safety_inputs, trusted_implementation_digest=TRUSTED,
    )
    context = ExecutionSafetyContext(
        context_version="1", decision_time=NOW, run_id=live.live_preflight_id,
        action=ExecutionAction.ENTRY_PLACE, symbol="AAPL", side="buy",
        order_type="market", quantity="1", stop_price=None, order_id=None,
        governance_evidence=CommissioningGovernanceEvidence(
            commissioning_mode=CommissioningMode.ATTENDED, operator_attended=True,
            operator_acknowledgements=allowed_acknowledgements(), source_hash_verified=True,
            release_sha="a" * 40, implementation_digest=TRUSTED,
            account_fingerprint_matched=True,
            account_fingerprint_reference=live.account_fingerprint_reference,
            suspension_state="CLEAR",
        ),
    )
    return evaluation, dry_run, evaluate_execution_safety(context)


def handoff(**changes):
    evaluation, dry_run, decision = artifacts()
    return build_attended_commissioning_handoff(
        evaluation, dry_run=dry_run, decision=decision, evaluated_at=NOW,
        release_sha="a" * 40, trusted_implementation_digest=TRUSTED, **changes,
    )


def test_authentic_fresh_allow_produces_non_authorizing_handoff_ready_evidence():
    value = handoff()
    assert value.handoff_state is AttendedCommissioningHandoffState.HANDOFF_READY
    assert value.safety_decision_result == "ALLOW"
    assert value.safety_decision_freshness == "FRESH"
    assert value.quantity == "1" and value.intended_entry == "100"
    assert value.intended_stop == "99" and value.intended_target == "102"
    assert value.execution_authorized is False and value.protection_grounded is False
    assert value.broker_write_count == 0 and value.operator_action_required is True


def test_stale_deny_or_incomplete_safety_evidence_blocks():
    evaluation, dry_run, decision = artifacts()
    stale = build_attended_commissioning_handoff(
        evaluation, dry_run=dry_run, decision=decision, evaluated_at=NOW + timedelta(seconds=31),
        release_sha="a" * 40, trusted_implementation_digest=TRUSTED,
    )
    assert stale.handoff_state is AttendedCommissioningHandoffState.SAFETY_DECISION_STALE
    denied = build_attended_safety_evaluation_result(
        source_live_preflight(), inputs=inputs(), trusted_implementation_digest=TRUSTED,
    )
    blocked = build_attended_commissioning_handoff(
        denied, dry_run=dry_run, decision=None, evaluated_at=NOW,
        release_sha="a" * 40, trusted_implementation_digest=TRUSTED,
    )
    assert blocked.handoff_state is AttendedCommissioningHandoffState.HANDOFF_BLOCKED


def test_identity_fingerprint_suspension_source_and_quantity_mismatches_block():
    evaluation, dry_run, decision = artifacts()
    wrong_sha = build_attended_commissioning_handoff(
        evaluation, dry_run=dry_run, decision=decision, evaluated_at=NOW,
        release_sha="b" * 40, trusted_implementation_digest=TRUSTED,
    )
    assert "SOURCE_HASH_MISMATCH" in wrong_sha.blocking_reasons
    object.__setattr__(decision, "bound_quantity", "2")
    bad = build_attended_commissioning_handoff(
        evaluation, dry_run=dry_run, decision=decision, evaluated_at=NOW,
        release_sha="a" * 40, trusted_implementation_digest=TRUSTED,
    )
    assert bad.handoff_state is AttendedCommissioningHandoffState.HANDOFF_BLOCKED


def test_handoff_digest_is_deterministic_and_has_no_broker_or_adapter_reachability():
    first, second = handoff(), handoff()
    assert first == second
    assert compute_attended_commissioning_handoff_digest(first) == first.handoff_digest
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/attended_commissioning_handoff.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "RobinhoodClient", "place_equity_order", "cancel_equity_order", "call_tool", "_call"})
