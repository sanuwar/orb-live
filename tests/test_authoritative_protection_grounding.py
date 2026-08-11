"""M20 authoritative working-stop grounding tests."""
from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.account_state import EquityOrder
from src.broker_snapshot import account_fingerprint
from src.protection_grounding_assessment import (
    AUTHORITATIVE_GROUNDING_ACTIVE_STATES,
    AuthoritativeGroundingOutcome,
    AuthoritativeGroundingReason,
    AuthoritativeProtectionGroundingRequest,
    compute_authoritative_grounding_digest,
    evaluate_authoritative_working_stop_grounding,
)
from src.protection_observation_model import EvidenceSourceClass
from src.protective_stop_exit_rehearsal import (
    StopExitRehearsalState,
    build_protective_stop_exit_rehearsal,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_attended_safety_evaluation_bridge import TRUSTED
from tests.test_live_reconciliation import make_account_state, make_position
from tests.test_post_entry_protection_rehearsal import rehearse
from tests.test_protective_stop_exit_rehearsal import (
    entry_observation,
    observed,
)


NOW = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
RELEASE = "a" * 40
ACCOUNT_NUMBER = "TEST12345678"
ACCOUNT = account_fingerprint(ACCOUNT_NUMBER)


def stop_order(
    *,
    order_id="stop-1",
    symbol="AAPL",
    side="sell",
    state="confirmed",
    quantity="1",
    cumulative="0",
    stop_price="99",
    trigger="stop",
    order_type="stop_market",
    placed_agent="agentic",
) -> EquityOrder:
    return EquityOrder(
        order_id=order_id,
        symbol=symbol,
        side=side,
        order_type=order_type,
        state=state,
        quantity=Decimal(quantity),
        cumulative_quantity=Decimal(cumulative),
        limit_price=None,
        stop_price=Decimal(stop_price),
        trigger=trigger,
        placed_agent=placed_agent,
    )


def snapshot(
    *,
    position_quantity: str | None = "1",
    orders: tuple[EquityOrder, ...] | None = None,
    account_number: str = ACCOUNT_NUMBER,
    completed_at: datetime | None = None,
    positions_complete: bool = True,
):
    positions = (
        ()
        if position_quantity is None
        else (make_position(quantity=position_quantity),)
    )
    state = make_account_state(
        positions=positions,
        orders=(stop_order(),) if orders is None else orders,
        account_number=account_number,
    )
    completed = completed_at or NOW - timedelta(seconds=1)
    return make_broker_snapshot(
        state,
        reconciliation_id="m20-cycle",
        started_at=completed - timedelta(seconds=3),
        positions_read_at=completed - timedelta(seconds=2),
        orders_read_at=completed - timedelta(seconds=1),
        completed_at=completed,
        positions_complete=positions_complete,
    )


def request(
    *,
    evidence_source_class: EvidenceSourceClass = (
        EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
    ),
    **changes,
) -> AuthoritativeProtectionGroundingRequest:
    values = {
        "source_candidate_id": "1" * 64,
        "source_handoff_id": "2" * 64,
        "source_protection_plan_id": "3" * 64,
        "symbol": "AAPL",
        "expected_stop_order_id": "stop-1",
        "expected_position_quantity": Decimal("1"),
        "expected_stop_price": Decimal("99"),
        "release_identity": RELEASE,
        "implementation_digest": TRUSTED,
        "account_fingerprint_reference": ACCOUNT,
        "evidence_source_class": evidence_source_class,
    }
    values.update(changes)
    return AuthoritativeProtectionGroundingRequest(**values)


def assess(*, source=None, broker_snapshot=None, assessed_at=NOW, **changes):
    return evaluate_authoritative_working_stop_grounding(
        source or request(),
        snapshot=broker_snapshot or snapshot(),
        assessed_at=assessed_at,
        expected_release_identity=changes.pop(
            "expected_release_identity", RELEASE
        ),
        expected_implementation_digest=changes.pop(
            "expected_implementation_digest", TRUSTED
        ),
        expected_account_fingerprint_reference=changes.pop(
            "expected_account_fingerprint_reference", ACCOUNT
        ),
        **changes,
    )


def reason_values(result) -> set[str]:
    return {reason.value for reason in result.reason_codes}


def test_exact_live_position_and_confirmed_stop_ground_protection():
    result = assess()
    assert AUTHORITATIVE_GROUNDING_ACTIVE_STATES == frozenset({"confirmed"})
    assert result.outcome is AuthoritativeGroundingOutcome.GROUNDED
    assert result.reason_codes == (AuthoritativeGroundingReason.GROUNDED,)
    assert result.coverage_assessment == "VERIFIED_ACTIVE_PROTECTION"
    assert result.authoritative_position_quantity == "1"
    assert result.protective_quantity == "1"
    assert result.protection_grounded is True
    assert result.promotion_permitted is False
    assert result.broker_write_count == 0
    assert result.execution_authorized is False
    assert result.activation_approved is False
    assert result.permits_automated_action is False
    assert result.live_ready is False and result.unattended_ready is False
    assert result.authority_ceiling == "ATTENDED_COMMISSIONING_ONLY"


def test_flat_position_is_not_required_and_never_grounded():
    result = assess(broker_snapshot=snapshot(position_quantity=None, orders=()))
    assert result.outcome is AuthoritativeGroundingOutcome.NOT_REQUIRED
    assert reason_values(result) == {"POSITION_FLAT"}
    assert result.protection_grounded is False


@pytest.mark.parametrize(
    ("orders", "expected_reason"),
    [
        ((), "STOP_NOT_FOUND"),
        ((stop_order(state="new"),), "STOP_STATE_NOT_ACTIVE"),
        ((stop_order(state="rejected"),), "STOP_STATE_NOT_ACTIVE"),
        ((stop_order(state="cancelled"),), "STOP_STATE_NOT_ACTIVE"),
        ((stop_order(state="mystery"),), "STOP_STATE_NOT_ACTIVE"),
        (
            (stop_order(state="replaced"),),
            "UNSUPPORTED_REPLACEMENT_LINEAGE",
        ),
    ],
)
def test_planned_terminal_unknown_and_replaced_stops_never_ground(
    orders, expected_reason
):
    result = assess(broker_snapshot=snapshot(orders=orders))
    assert result.protection_grounded is False
    assert expected_reason in reason_values(result)


@pytest.mark.parametrize(
    ("order_changes", "expected_reason"),
    [
        ({"symbol": "MSFT"}, "STOP_IDENTITY_MISMATCH"),
        ({"side": "buy"}, "STOP_IDENTITY_MISMATCH"),
        ({"trigger": "immediate"}, "STOP_IDENTITY_MISMATCH"),
        ({"order_type": "limit"}, "STOP_IDENTITY_MISMATCH"),
        ({"placed_agent": "manual"}, "STOP_IDENTITY_MISMATCH"),
        ({"stop_price": "98"}, "STOP_PRICE_MISMATCH"),
        ({"quantity": "0.5"}, "STOP_QUANTITY_MISMATCH"),
        ({"quantity": "2"}, "STOP_QUANTITY_MISMATCH"),
        (
            {"state": "partially_filled", "cumulative": "0.5"},
            "STOP_STATE_NOT_ACTIVE",
        ),
    ],
)
def test_stop_shape_price_and_full_quantity_mismatches_fail_closed(
    order_changes, expected_reason
):
    result = assess(
        broker_snapshot=snapshot(orders=(stop_order(**order_changes),))
    )
    assert result.protection_grounded is False
    assert expected_reason in reason_values(result)


def test_conflicting_multiple_active_stops_fail_closed():
    result = assess(
        broker_snapshot=snapshot(
            orders=(stop_order(), stop_order(order_id="stop-2"))
        )
    )
    assert result.protection_grounded is False
    assert "CONFLICTING_ACTIVE_STOPS" in reason_values(result)


def test_stale_invalid_account_and_source_evidence_fail_closed():
    stale = assess(
        broker_snapshot=snapshot(
            completed_at=NOW - timedelta(seconds=31)
        )
    )
    invalid = assess(broker_snapshot=snapshot(positions_complete=False))
    mismatch = assess(expected_account_fingerprint_reference="b" * 64)
    release = assess(expected_release_identity="b" * 40)
    implementation = assess(expected_implementation_digest="b" * 64)
    assert "SNAPSHOT_STALE" in reason_values(stale)
    assert "SNAPSHOT_INVALID" in reason_values(invalid)
    assert "ACCOUNT_MISMATCH" in reason_values(mismatch)
    assert "SOURCE_MISMATCH" in reason_values(release)
    assert "IMPLEMENTATION_MISMATCH" in reason_values(implementation)
    assert all(
        not item.protection_grounded
        for item in (stale, invalid, mismatch, release, implementation)
    )


def test_non_finite_position_and_order_quantities_fail_closed():
    bad_position = assess(broker_snapshot=snapshot(position_quantity="NaN"))
    bad_order = assess(
        broker_snapshot=snapshot(orders=(stop_order(quantity="NaN"),))
    )
    bad_price = assess(
        broker_snapshot=snapshot(orders=(stop_order(stop_price="NaN"),))
    )
    assert "POSITION_QUANTITY_MISMATCH" in reason_values(bad_position)
    assert "STOP_QUANTITY_MISMATCH" in reason_values(bad_order)
    assert "BROKER_ORDER_INVALID" in reason_values(bad_price)
    assert not bad_position.protection_grounded
    assert not bad_order.protection_grounded
    assert not bad_price.protection_grounded

def test_fixture_is_explicit_and_cannot_qualify_as_live_evidence():
    result = assess(
        source=request(
            evidence_source_class=EvidenceSourceClass.TEST_FIXTURE
        )
    )
    assert result.coverage_assessment == "VERIFIED_ACTIVE_PROTECTION"
    assert result.outcome is AuthoritativeGroundingOutcome.NOT_GROUNDED
    assert "EVIDENCE_SOURCE_INELIGIBLE" in reason_values(result)
    assert result.protection_grounded is False


def test_grounding_demotes_when_stop_disappears_cancels_or_quantity_changes():
    assert assess().protection_grounded is True
    disappeared = assess(broker_snapshot=snapshot(orders=()))
    cancelled = assess(
        broker_snapshot=snapshot(
            orders=(stop_order(state="cancelled"),)
        )
    )
    changed = assess(broker_snapshot=snapshot(position_quantity="2"))
    assert disappeared.protection_grounded is False
    assert cancelled.protection_grounded is False
    assert changed.protection_grounded is False
    assert "POSITION_QUANTITY_MISMATCH" in reason_values(changed)


def test_digest_is_deterministic_sensitive_and_reconstructable():
    first, second = assess(), assess()
    changed = assess(assessed_at=NOW + timedelta(seconds=1))
    assert first == second
    assert (
        first.assessment_digest
        == compute_authoritative_grounding_digest(first)
    )
    assert changed.assessment_digest != first.assessment_digest
    assert len(first.protective_stop_reference or "") == 64
    assert first.protective_stop_reference != "stop-1"


def test_m19_reconciliation_feeds_m13_without_duplicate_state_normalization():
    result = build_protective_stop_exit_rehearsal(
        rehearse(),
        snapshot=snapshot(orders=(stop_order(),)),
        protective_stop_order_id="stop-1",
        stop_observation=observed(stop_order()),
        stop_observed_at=NOW,
        entry_observation=entry_observation(),
        evaluated_at=NOW,
        release_sha=RELEASE,
        trusted_implementation_digest=TRUSTED,
        protection_evidence_source_class=(
            EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
        ),
    )
    assert result.rehearsal_state is StopExitRehearsalState.OPEN_PROTECTED
    assert result.stop_reconciliation_state == "STOP_WORKING"
    assert result.m13_protection_reason == "GROUNDED"
    assert result.m13_grounding_assessment_id
    assert result.protection_grounded is True
    assert result.execution_authorized is False
    assert result.broker_write_count == 0


def test_grounding_module_has_no_network_broker_write_or_adapter_boundary():
    path = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "protection_grounding_assessment.py"
    )
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = {
        node.id for node in ast.walk(tree) if isinstance(node, ast.Name)
    } | {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
    }
    assert not (
        names
        & {
            "RobinhoodCommissioningAdapter",
            "place_equity_order",
            "cancel_equity_order",
            "replace",
            "flatten",
            "call_tool",
            "_call",
        }
    )
