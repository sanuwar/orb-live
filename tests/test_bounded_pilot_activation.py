from __future__ import annotations

import ast
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.bounded_pilot_activation import (
    CommissioningReadinessState,
    assess_bounded_pilot_activation,
    compute_bounded_pilot_activation_digest,
)
from src.bounded_unattended_orchestrator import (
    FIXED_WATCHLIST,
    PolicyEvidenceOrigin,
    UnattendedOrchestratorError,
    build_unattended_pilot_policy,
)
from src.protection_grounding_assessment import (
    AuthoritativeGroundingOutcome,
    AuthoritativeProtectionGroundingRequest,
    evaluate_authoritative_working_stop_grounding,
)
from src.protection_observation_model import EvidenceSourceClass
from tests.test_attended_safety_evaluation_bridge import TRUSTED
from tests.test_authoritative_protection_grounding import (
    ACCOUNT,
    RELEASE,
    NOW,
    snapshot as working_snapshot,
)
from tests.test_protective_stop_exit_rehearsal import (
    build as exit_rehearsal,
    observed,
    order,
    snapshot as exit_snapshot,
)


ACTIVATED = datetime(2026, 7, 20, 14, 1, tzinfo=timezone.utc)


def policy(**changes):
    values = dict(
        enabled=False, evidence_origin=PolicyEvidenceOrigin.REVIEWED_PRODUCTION,
        account_fingerprint_reference=ACCOUNT, strategy_id="orb_long_v0",
        strategy_version="orb_long_v0-0.1.0", symbol_allowlist=FIXED_WATCHLIST,
        quantity=1, entry_start="09:40", entry_end="11:00",
        maximum_trade_attempts_per_day=1, maximum_reconciliation_polls=3,
        maximum_risk_per_share="5", maximum_entry_notional="1000",
        deployment_expiry=NOW.date(), release_sha=RELEASE,
        implementation_digest=TRUSTED,
        activation_approved=False, permits_automated_action=False,
    )
    values.update(changes)
    return build_unattended_pilot_policy(**values)


def completed():
    return exit_rehearsal(
        stop_observation=observed(order(state="filled", cumulative="1"), average="99"),
        snapshot=exit_snapshot(quantity="0"),
        protection_evidence_source_class=(
            EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
        ),
    )


def grounding(value=None, **changes):
    value = value or completed()
    request = AuthoritativeProtectionGroundingRequest(
        source_candidate_id=value.source_shadow_decision_id,
        source_handoff_id=value.source_handoff_id,
        source_protection_plan_id=value.source_protection_rehearsal_id,
        symbol=value.symbol, expected_stop_order_id="stop-1",
        expected_position_quantity=Decimal("1"), expected_stop_price=Decimal("99"),
        release_identity=RELEASE, implementation_digest=TRUSTED,
        account_fingerprint_reference=ACCOUNT,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
    )
    broker_snapshot = changes.pop("broker_snapshot", working_snapshot())
    return evaluate_authoritative_working_stop_grounding(
        request, snapshot=broker_snapshot, assessed_at=NOW,
        expected_release_identity=RELEASE,
        expected_implementation_digest=TRUSTED,
        expected_account_fingerprint_reference=ACCOUNT, **changes,
    )


def assess(*, current_policy=None, lifecycle="default", evidence="default", **changes):
    lifecycle = completed() if lifecycle == "default" else lifecycle
    evidence = grounding(lifecycle) if evidence == "default" else evidence
    return assess_bounded_pilot_activation(
        current_policy or policy(), completed_commissioning=lifecycle,
        protection_grounding=evidence, activated_at=ACTIVATED,
        release_sha=RELEASE, trusted_implementation_digest=TRUSTED, **changes,
    )


def test_authentic_completed_commissioning_is_eligible_but_non_authorizing():
    result = assess()
    assert result.commissioning_status is CommissioningReadinessState.ACTIVATION_ELIGIBLE
    assert result.blocking_reasons == ()
    assert result.quantity == 1 and result.symbol_allowlist == FIXED_WATCHLIST
    assert result.activation_approved is False
    assert result.permits_automated_action is False
    assert result.live_ready is False and result.unattended_ready is False
    assert result.execution_authorized is False and result.broker_write_count == 0
    assert result.protection_grounded is False


def test_missing_tampered_and_mismatched_commissioning_fail_closed():
    missing = assess(lifecycle=None, evidence=None)
    assert "COMMISSIONING_EVIDENCE_MISSING" in missing.blocking_reasons

    lifecycle = completed()
    tampered = assess(lifecycle=replace(lifecycle, rehearsal_id="0" * 64, rehearsal_digest="0" * 64))
    assert "COMMISSIONING_DIGEST_MISMATCH" in tampered.blocking_reasons

    mismatched = assess(lifecycle=replace(lifecycle, release_sha="f" * 40))
    assert "COMMISSIONING_RUNTIME_MISMATCH" in mismatched.blocking_reasons
    assert mismatched.commissioning_status is not CommissioningReadinessState.ACTIVATION_ELIGIBLE


def test_incomplete_or_failed_lifecycle_never_becomes_eligible():
    result = assess(lifecycle=exit_rehearsal(protection_evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION))
    assert "COMMISSIONING_EXIT_NOT_RECONCILED" in result.blocking_reasons
    assert result.commissioning_status is CommissioningReadinessState.COMMISSIONING_EVIDENCE_INCOMPLETE


@pytest.mark.parametrize("change, expected", [
    ({"account_fingerprint_reference": "f" * 64}, "COMMISSIONING_RUNTIME_MISMATCH"),
    ({"implementation_digest": "f" * 64}, "POLICY_RUNTIME_MISMATCH"),
    ({"enabled": True}, "POLICY_RUNTIME_MISMATCH"),
])
def test_policy_change_or_activation_attempt_invalidates_eligibility(change, expected):
    result = assess(current_policy=policy(**change))
    assert expected in result.blocking_reasons
    assert result.commissioning_status is CommissioningReadinessState.ACTIVATION_BLOCKED


def test_invalid_quantity_or_watchlist_cannot_construct_a_pilot_policy():
    with pytest.raises(UnattendedOrchestratorError):
        policy(quantity=2)
    with pytest.raises(UnattendedOrchestratorError):
        policy(symbol_allowlist=("AAPL",))


def test_grounding_must_be_live_authoritative_and_bound_to_lifecycle():
    lifecycle = completed()
    invalid = grounding(
        lifecycle, broker_snapshot=working_snapshot(position_quantity=None, orders=()),
    )
    assert invalid.outcome is AuthoritativeGroundingOutcome.NOT_REQUIRED
    result = assess(lifecycle=lifecycle, evidence=invalid)
    assert "GROUNDING_NOT_PROVEN" in result.blocking_reasons

    valid = grounding(lifecycle)
    result = assess(
        lifecycle=replace(lifecycle, source_handoff_id="0" * 64), evidence=valid,
    )
    assert "GROUNDING_SOURCE_MISMATCH" in result.blocking_reasons


def test_activation_digest_is_deterministic_and_reconstructs():
    left = assess()
    right = assess()
    assert left == right
    assert left.activation_digest == compute_bounded_pilot_activation_digest(left)


def test_readiness_gate_is_not_a_runtime_authorization_root():
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE
    assert "src/bounded_pilot_activation.py" not in PINNED_IMPLEMENTATION_CLOSURE


def test_activation_module_has_no_broker_or_network_reachability():
    source = Path("src/bounded_pilot_activation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        node.module for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    forbidden = {
        "src.robinhood_commissioning_adapter", "src.robinhood_client",
        "src.unattended_pilot_runtime",
    }
    assert not names & forbidden
    assert all(
        term not in source
        for term in ("place_equity_order", "cancel_equity_order", "session.call_tool", "requests", "httpx")
    )
