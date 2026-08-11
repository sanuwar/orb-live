from __future__ import annotations

import ast
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

from src.account_state import EquityOrder
from src.execution_safety_governance import CommissioningGovernanceEvidence, CommissioningMode
from src.post_entry_protection_rehearsal import (
    ProtectionRehearsalState, build_post_entry_protection_rehearsal,
    compute_post_entry_protection_rehearsal_digest,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_attended_commissioning_handoff import handoff
from tests.test_attended_safety_evaluation_bridge import NOW, TRUSTED, allowed_acknowledgements
from tests.test_live_reconciliation import make_account_state


def entry(*, state="filled", symbol="AAPL", quantity="1", cumulative="1"):
    return EquityOrder(
        order_id="entry-1", symbol=symbol, side="buy", order_type="market", state=state,
        quantity=Decimal(quantity), cumulative_quantity=Decimal(cumulative), limit_price=None,
        stop_price=None, trigger="immediate", placed_agent="agentic",
    )


def inputs():
    value = handoff()
    state = make_account_state(orders=(entry(),))
    snapshot = make_broker_snapshot(state, reconciliation_id="cycle-1", completed_at=NOW - timedelta(seconds=1), started_at=NOW - timedelta(seconds=4), positions_read_at=NOW - timedelta(seconds=3), orders_read_at=NOW - timedelta(seconds=2))
    evidence = CommissioningGovernanceEvidence(
        commissioning_mode=CommissioningMode.ATTENDED, operator_attended=True,
        operator_acknowledgements=allowed_acknowledgements(), source_hash_verified=True,
        release_sha="a" * 40, implementation_digest=TRUSTED,
        account_fingerprint_matched=True,
        account_fingerprint_reference=snapshot.metadata.account_fingerprint,
        suspension_state="NOT_EVALUATED",
    )
    clear = SimpleNamespace(state=SimpleNamespace(value="CLEAR"), permits_automated_action=False)
    return value, snapshot, evidence, clear


def rehearse(**changes):
    value, snapshot, evidence, clear = inputs()
    return build_post_entry_protection_rehearsal(
        value, snapshot=changes.pop("snapshot", snapshot), entry_order_id=changes.pop("entry_order_id", "entry-1"),
        suspension_decision=changes.pop("suspension_decision", clear), suspension_assessed_at=changes.pop("suspension_assessed_at", NOW),
        governance_evidence=changes.pop("governance_evidence", evidence), evaluated_at=changes.pop("evaluated_at", NOW),
        release_sha=changes.pop("release_sha", "a" * 40), trusted_implementation_digest=TRUSTED,
        **changes,
    )


def test_filled_one_share_reaches_non_authorizing_protection_rehearsal_ready():
    value = rehearse()
    assert value.rehearsal_state is ProtectionRehearsalState.PROTECTION_REHEARSAL_READY
    assert value.filled_quantity == value.protective_quantity == "1"
    assert value.stop_price == "99" and value.protective_action_identity != value.entry_action_identity
    assert value.safety_result == "ALLOW"
    assert value.broker_write_count == 0 and value.execution_authorized is False and value.protection_grounded is False


def test_entry_failures_and_mismatches_fail_closed():
    value, snapshot, evidence, clear = inputs()
    for state, expected in (("confirmed", ProtectionRehearsalState.ENTRY_NOT_FILLED), ("rejected", ProtectionRehearsalState.ENTRY_REJECTED), ("cancelled", ProtectionRehearsalState.ENTRY_CANCELLED), ("partially_filled", ProtectionRehearsalState.PARTIAL_FILL_UNSUPPORTED)):
        changed = replace(snapshot.account_state, open_orders=(entry(state=state, cumulative="0" if state == "confirmed" else "0.5"),))
        result = rehearse(snapshot=replace(snapshot, account_state=changed))
        assert result.rehearsal_state is expected
    assert rehearse(entry_order_id="missing").rehearsal_state is ProtectionRehearsalState.ENTRY_NOT_FOUND


def test_snapshot_account_suspension_and_acknowledgement_failures_block():
    value, snapshot, evidence, clear = inputs()
    assert rehearse(evaluated_at=NOW + timedelta(seconds=31)).rehearsal_state is ProtectionRehearsalState.SNAPSHOT_STALE
    bad_account = replace(snapshot.metadata, account_fingerprint="b" * 64)
    assert rehearse(snapshot=replace(snapshot, metadata=bad_account)).rehearsal_state is ProtectionRehearsalState.ACCOUNT_OBSERVATION_BLOCKED
    blocked = SimpleNamespace(state=SimpleNamespace(value="SUSPENDED"), permits_automated_action=False)
    assert rehearse(suspension_decision=blocked).rehearsal_state is ProtectionRehearsalState.SUSPENSION_BLOCKED
    assert rehearse(suspension_assessed_at=NOW - timedelta(seconds=31)).rehearsal_state is ProtectionRehearsalState.SUSPENSION_BLOCKED
    assert rehearse(governance_evidence=replace(evidence, operator_attended=False)).rehearsal_state is ProtectionRehearsalState.SAFETY_DENY


def test_digest_is_deterministic_and_no_broker_boundary_is_reachable():
    first, second = rehearse(), rehearse()
    assert first == second
    assert compute_post_entry_protection_rehearsal_digest(first) == first.rehearsal_digest
    tree = ast.parse((Path(__file__).resolve().parents[1] / "src/post_entry_protection_rehearsal.py").read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    assert not (names & {"RobinhoodCommissioningAdapter", "place_equity_order", "cancel_equity_order", "call_tool", "_call"})
