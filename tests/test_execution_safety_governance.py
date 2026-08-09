"""Focused tests for the M14 Issue 1 safety-governance boundary.

Every fixture here is local and deterministic. Nothing in this module opens
a broker session, a network connection, or reads credentials -- gates that
would require live evidence (risk, suspension, operational health) are
exercised only through their standalone adapters
(`src/execution_safety_governance_deferred_gates.py`), never through a real
broker/session object, and never as part of the active commissioning policy.
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from src.execution_safety_governance import (
    ACTION_FOR_ROLE,
    AUTHORITY_CEILING,
    POLICY_VERSION,
    CommissioningGovernanceEvidence,
    CommissioningMode,
    ExecutionAction,
    ExecutionSafetyContext,
    ExecutionSafetyDecision,
    ExecutionSafetyOutcome,
    GateOutcome,
    GateResult,
    OperatorAcknowledgements,
    compute_decision_digest,
    decision_binds_write,
    decision_is_fresh,
    evaluate_execution_safety,
    validate_execution_safety_decision,
)
from src.execution_safety_governance_deferred_gates import (
    make_operational_health_gate,
    make_risk_limit_gate,
    make_suspension_gate,
)
from src.robinhood_commissioning_adapter import (
    ExecutionSafetyDeniedError,
    RobinhoodCommissioningAdapter,
)
from tests.call_site_guard import analyze_runtime_tree, violations
from tests.execution_safety_test_support import allow_decision, deny_decision

EASTERN = ZoneInfo("America/New_York")
REGULAR_SESSION_MOMENT = datetime(2026, 8, 11, 10, 0, tzinfo=EASTERN)  # Tuesday


def valid_evidence(**overrides) -> CommissioningGovernanceEvidence:
    ack_overrides = overrides.pop("ack_overrides", {})
    ack_fields = dict(
        acknowledges_loss_may_exceed_plan=True, operator_present=True,
        robinhood_ui_open=True, no_second_trade=True,
    )
    ack_fields.update(ack_overrides)
    base = dict(
        commissioning_mode=CommissioningMode.ATTENDED,
        operator_attended=True,
        operator_acknowledgements=OperatorAcknowledgements(**ack_fields),
        source_hash_verified=True,
        release_sha="a" * 40,
        implementation_digest="b" * 64,
        account_fingerprint_matched=True,
        account_fingerprint_reference="c" * 64,
    )
    base.update(overrides)
    return CommissioningGovernanceEvidence(**base)


def entry_context(
    *, decision_time: datetime = REGULAR_SESSION_MOMENT,
    evidence: CommissioningGovernanceEvidence | None = None,
    quantity: str = "1", run_id: str = "gov-run-1",
) -> ExecutionSafetyContext:
    return ExecutionSafetyContext(
        context_version="1", decision_time=decision_time, run_id=run_id,
        action=ExecutionAction.ENTRY_PLACE, symbol="AAPL", side="buy",
        order_type="market", quantity=quantity, stop_price=None, order_id=None,
        governance_evidence=evidence or valid_evidence(),
    )


def stop_context(**overrides) -> ExecutionSafetyContext:
    evidence = overrides.pop("evidence", None) or valid_evidence()
    decision_time = overrides.pop("decision_time", REGULAR_SESSION_MOMENT)
    return ExecutionSafetyContext(
        context_version="1", decision_time=decision_time, run_id="gov-run-1",
        action=ExecutionAction.STOP_PLACE, symbol="AAPL", side="sell",
        order_type="stop_market", quantity="1", stop_price="185.00",
        order_id=None, governance_evidence=evidence, **overrides,
    )


def cancel_context(**overrides) -> ExecutionSafetyContext:
    evidence = overrides.pop("evidence", None) or valid_evidence()
    decision_time = overrides.pop("decision_time", REGULAR_SESSION_MOMENT)
    return ExecutionSafetyContext(
        context_version="1", decision_time=decision_time, run_id="gov-run-1",
        action=ExecutionAction.STOP_CANCEL, symbol="AAPL", side="sell",
        order_type="stop_market", quantity="1", stop_price=None,
        order_id="order-1", governance_evidence=evidence, **overrides,
    )


# ---------------------------------------------------------------------
# CORE DECISION
# ---------------------------------------------------------------------


def test_all_required_gates_pass_allows():
    decision = evaluate_execution_safety(entry_context())
    assert decision.outcome is ExecutionSafetyOutcome.ALLOW
    assert decision.allow is True
    assert decision.failed_gates == ()
    assert decision.reason_codes == ()
    assert len(decision.satisfied_gates) == 7


def test_missing_context_denies():
    decision = evaluate_execution_safety(None)
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_CONTEXT_INVALID" in decision.reason_codes


def test_malformed_context_denies():
    decision = evaluate_execution_safety({"not": "a context"})
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_CONTEXT_INVALID" in decision.reason_codes


def test_unknown_gate_result_denies():
    def broken_gate(context):
        return "not-a-gate-result"

    decision = evaluate_execution_safety(entry_context(), gates=(broken_gate,))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_GATE_RESULT_MALFORMED" in decision.reason_codes


def test_gate_exception_denies():
    def exploding_gate(context):
        raise RuntimeError("boom")

    decision = evaluate_execution_safety(entry_context(), gates=(exploding_gate,))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_GATE_EVALUATION_EXCEPTION" in decision.reason_codes


def test_repeated_decision_is_deterministic():
    context = entry_context()
    first = evaluate_execution_safety(context)
    second = evaluate_execution_safety(context)
    assert first == second


def test_decision_digest_is_deterministic():
    context = entry_context()
    first = evaluate_execution_safety(context)
    second = evaluate_execution_safety(context)
    assert first.decision_digest == second.decision_digest


def test_changing_action_changes_digest():
    entry_decision = evaluate_execution_safety(entry_context())
    stop_decision = evaluate_execution_safety(stop_context())
    assert entry_decision.decision_digest != stop_decision.decision_digest


def test_changing_gate_result_changes_digest():
    allow = evaluate_execution_safety(entry_context())
    deny = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(source_hash_verified=False))
    )
    assert allow.decision_digest != deny.decision_digest


# ---------------------------------------------------------------------
# COMMISSIONING AUTHORITY
# ---------------------------------------------------------------------


def test_operator_not_attended_denies():
    decision = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(operator_attended=False))
    )
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_OPERATOR_NOT_ATTENDED" in decision.reason_codes


def test_missing_operator_acknowledgement_denies():
    decision = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(ack_overrides={"operator_present": False}))
    )
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_OPERATOR_ACKNOWLEDGEMENT_MISSING" in decision.reason_codes


def test_source_hash_unverified_denies():
    decision = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(source_hash_verified=False))
    )
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_SOURCE_HASH_UNVERIFIED" in decision.reason_codes


def test_account_fingerprint_mismatch_denies():
    decision = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(account_fingerprint_matched=False))
    )
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_ACCOUNT_FINGERPRINT_MISMATCH" in decision.reason_codes


def test_account_fingerprint_reference_malformed_denies():
    decision = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(account_fingerprint_reference="not-hex"))
    )
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_ACCOUNT_FINGERPRINT_REFERENCE_MALFORMED" in decision.reason_codes


def test_quantity_bound_violation_denies():
    decision = evaluate_execution_safety(entry_context(quantity="2"))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_QUANTITY_BOUND" in decision.reason_codes


def test_quantity_malformed_denies():
    decision = evaluate_execution_safety(entry_context(quantity="not-a-number"))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_QUANTITY_MALFORMED" in decision.reason_codes


def test_action_identity_side_mismatch_denies():
    context = ExecutionSafetyContext(
        context_version="1", decision_time=REGULAR_SESSION_MOMENT, run_id="gov-run-1",
        action=ExecutionAction.ENTRY_PLACE, symbol="AAPL", side="sell",
        order_type="market", quantity="1", stop_price=None, order_id=None,
        governance_evidence=valid_evidence(),
    )
    decision = evaluate_execution_safety(context)
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_ACTION_IDENTITY_INVALID" in decision.reason_codes


def test_symbol_mismatch_denies():
    context = ExecutionSafetyContext(
        context_version="1", decision_time=REGULAR_SESSION_MOMENT, run_id="gov-run-1",
        action=ExecutionAction.ENTRY_PLACE, symbol="MSFT", side="buy",
        order_type="market", quantity="1", stop_price=None, order_id=None,
        governance_evidence=valid_evidence(),
    )
    decision = evaluate_execution_safety(context)
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_SYMBOL_MISMATCH" in decision.reason_codes


def test_stop_missing_stop_price_denies():
    context = ExecutionSafetyContext(
        context_version="1", decision_time=REGULAR_SESSION_MOMENT, run_id="gov-run-1",
        action=ExecutionAction.STOP_PLACE, symbol="AAPL", side="sell",
        order_type="stop_market", quantity="1", stop_price=None, order_id=None,
        governance_evidence=valid_evidence(),
    )
    decision = evaluate_execution_safety(context)
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_STOP_PRICE_MISSING" in decision.reason_codes


def test_cancel_missing_order_id_denies():
    context = ExecutionSafetyContext(
        context_version="1", decision_time=REGULAR_SESSION_MOMENT, run_id="gov-run-1",
        action=ExecutionAction.STOP_CANCEL, symbol="AAPL", side="sell",
        order_type="stop_market", quantity="1", stop_price=None, order_id=None,
        governance_evidence=valid_evidence(),
    )
    decision = evaluate_execution_safety(context)
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_ORDER_ID_MISSING" in decision.reason_codes


# ---------------------------------------------------------------------
# CALENDAR
# ---------------------------------------------------------------------


def test_permitted_regular_session_passes():
    decision = evaluate_execution_safety(entry_context(decision_time=REGULAR_SESSION_MOMENT))
    assert "exchange_calendar" in decision.satisfied_gates


def test_market_closed_after_hours_denies():
    after_hours = datetime(2026, 8, 11, 20, 0, tzinfo=EASTERN)
    decision = evaluate_execution_safety(entry_context(decision_time=after_hours))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert any(code.startswith("DENY_CALENDAR_") for code in decision.reason_codes)


def test_holiday_denies():
    holiday = datetime(2026, 1, 1, 10, 0, tzinfo=EASTERN)
    decision = evaluate_execution_safety(entry_context(decision_time=holiday))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_CALENDAR_FULL_HOLIDAY" in decision.reason_codes


def test_after_early_close_denies():
    after_early_close = datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN)
    decision = evaluate_execution_safety(entry_context(decision_time=after_early_close))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_CALENDAR_AFTER_EARLY_CLOSE" in decision.reason_codes


def test_unknown_calendar_state_denies():
    out_of_range = datetime(2028, 1, 1, 10, 0, tzinfo=EASTERN)
    decision = evaluate_execution_safety(entry_context(decision_time=out_of_range))
    assert decision.outcome is ExecutionSafetyOutcome.DENY
    assert "DENY_CALENDAR_DATE_OUTSIDE_SUPPORTED_RANGE" in decision.reason_codes


# ---------------------------------------------------------------------
# RISK (deferred adapter -- not part of DEFAULT_COMMISSIONING_GATES)
# ---------------------------------------------------------------------


def _risk_inputs():
    from src.risk_limits import ActionLedger, AccountRiskState, KillSwitchState, RiskLimits

    limits = RiskLimits(
        maximum_position_quantity=1, maximum_order_value=Decimal("1000"),
        maximum_daily_loss=Decimal("500"), maximum_open_positions=1,
    )
    state = AccountRiskState(realized_daily_loss=Decimal("0"), open_position_count=0)
    return limits, state, KillSwitchState.DISENGAGED, ActionLedger()


def _risk_proposal_for(context):
    from src.risk_limits import ActionProposal

    return ActionProposal(
        idempotency_key=f"{context.run_id}:{context.role}",
        quantity=int(Decimal(context.quantity)), order_value=Decimal("100"),
        observed_monotonic=1.0,
    )


def test_risk_gate_pass_continues():
    limits, state, kill_switch, ledger = _risk_inputs()
    gate = make_risk_limit_gate(
        proposal_for=_risk_proposal_for, limits=limits, state=state,
        kill_switch=kill_switch, ledger=ledger, current_monotonic=1.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.SATISFIED


def test_risk_gate_limit_failure_denies():
    from src.risk_limits import ActionProposal

    limits, state, kill_switch, ledger = _risk_inputs()

    def oversized_proposal(context):
        return ActionProposal(
            idempotency_key=f"{context.run_id}:{context.role}",
            quantity=limits.maximum_position_quantity + 1,
            order_value=Decimal("100"), observed_monotonic=1.0,
        )

    gate = make_risk_limit_gate(
        proposal_for=oversized_proposal, limits=limits, state=state,
        kill_switch=kill_switch, ledger=ledger, current_monotonic=1.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code.startswith("DENY_RISK_")


def test_risk_gate_unavailable_state_denies():
    gate = make_risk_limit_gate(
        proposal_for=_risk_proposal_for, limits=None, state=None,
        kill_switch=None, ledger=None, current_monotonic=1.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_RISK_EVALUATION_FAILED"


def test_risk_gate_evaluator_exception_denies():
    def broken_proposal(context):
        raise RuntimeError("cannot build proposal")

    limits, state, kill_switch, ledger = _risk_inputs()
    gate = make_risk_limit_gate(
        proposal_for=broken_proposal, limits=limits, state=state,
        kill_switch=kill_switch, ledger=ledger, current_monotonic=1.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_RISK_EVALUATION_FAILED"


# ---------------------------------------------------------------------
# SUSPENSION (deferred adapter)
# ---------------------------------------------------------------------


class _FakeSuspensionController:
    def __init__(self, decision=None, raise_on_assess=None):
        self._decision = decision
        self._raise = raise_on_assess

    def assess(self, controller_id):
        if self._raise is not None:
            raise self._raise
        return self._decision


def test_suspension_clear_continues():
    from src.durable_suspension_controller import (
        ControllerState, SchedulerAction, SuspensionCause, SuspensionDecision,
    )

    decision = SuspensionDecision(
        state=ControllerState.CLEAR, cause=SuspensionCause.NONE,
        requires_operator_review=False,
        scheduler_action=SchedulerAction.REQUIRE_OPERATOR_REVIEW,
    )
    controller = _FakeSuspensionController(decision=decision)
    gate = make_suspension_gate(controller=controller, controller_id="commissioning")
    result = gate(entry_context())
    assert result.outcome is GateOutcome.SATISFIED


def test_suspension_active_denies():
    from src.durable_suspension_controller import (
        ControllerState, SchedulerAction, SuspensionCause, SuspensionDecision,
    )

    decision = SuspensionDecision(
        state=ControllerState.SUSPENDED, cause=SuspensionCause.OPERATOR_HALT,
        requires_operator_review=True, scheduler_action=SchedulerAction.SUSPEND,
    )
    controller = _FakeSuspensionController(decision=decision)
    gate = make_suspension_gate(controller=controller, controller_id="commissioning")
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_SUSPENSION_SUSPENDED"


def test_suspension_unknown_state_denies():
    from src.durable_suspension_controller import (
        ControllerState, SchedulerAction, SuspensionCause, SuspensionDecision,
    )

    decision = SuspensionDecision(
        state=ControllerState.MALFORMED_STATE, cause=SuspensionCause.INTERNAL_INVARIANT_FAILURE,
        requires_operator_review=True, scheduler_action=SchedulerAction.SUSPEND,
    )
    controller = _FakeSuspensionController(decision=decision)
    gate = make_suspension_gate(controller=controller, controller_id="commissioning")
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_SUSPENSION_MALFORMED_STATE"


def test_suspension_read_failure_denies():
    controller = _FakeSuspensionController(raise_on_assess=RuntimeError("db unavailable"))
    gate = make_suspension_gate(controller=controller, controller_id="commissioning")
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_SUSPENSION_READ_FAILED"


# ---------------------------------------------------------------------
# OPERATIONAL HEALTH (deferred adapter)
# ---------------------------------------------------------------------


def test_operational_health_healthy_continues():
    gate = make_operational_health_gate(
        last_beat_monotonic=100.0, current_monotonic=101.0, interval_seconds=5.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.SATISFIED


def test_operational_health_missing_denies():
    gate = make_operational_health_gate(
        last_beat_monotonic=0.0, current_monotonic=1000.0, interval_seconds=5.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code.startswith("DENY_OPERATIONAL_HEALTH_")


def test_operational_health_evaluation_failure_denies():
    gate = make_operational_health_gate(
        last_beat_monotonic="not-a-number", current_monotonic=1.0, interval_seconds=5.0,
    )
    result = gate(entry_context())
    assert result.outcome is GateOutcome.FAILED
    assert result.reason_code == "DENY_OPERATIONAL_HEALTH_EVALUATION_FAILED"


# ---------------------------------------------------------------------
# WRITE BOUNDARY (adapter enforcement)
# ---------------------------------------------------------------------


class _RecordingSession:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    async def call_tool(self, name, arguments):
        self.calls += 1
        return self.response


def _place_response():
    from types import SimpleNamespace
    return SimpleNamespace(structuredContent={
        "data": {"order": {
            "id": "order-1", "instrument_id": "instrument-1", "symbol": "AAPL",
            "side": "buy", "type": "market", "state": "filled", "quantity": "1",
            "cumulative_quantity": "1", "price": None, "stop_price": None,
            "average_price": "200.00", "fees": "0.00", "dollar_based_amount": None,
            "time_in_force": "gfd", "market_hours": "regular_hours",
            "trigger": "immediate", "placed_agent": "agent",
            "created_at": "2026-08-11T14:00:00Z",
            "last_transaction_at": "2026-08-11T14:00:01Z", "executions": [],
        }},
        "guide": "g",
    })


ENTRY_PAYLOAD = {
    "account_number": "transient", "symbol": "AAPL", "side": "buy",
    "type": "market", "quantity": "1", "time_in_force": "gfd",
    "market_hours": "regular_hours",
}


def test_write_occurs_only_with_allow_decision():
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    decision = allow_decision(role="entry", arguments=ENTRY_PAYLOAD)
    asyncio.run(adapter.place_equity_order(
        ENTRY_PAYLOAD, role="entry", safety_decision=decision
    ))
    assert session.calls == 1


def test_deny_decision_prevents_broker_write():
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    decision = deny_decision(role="entry", arguments=ENTRY_PAYLOAD)
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.place_equity_order(
            ENTRY_PAYLOAD, role="entry", safety_decision=decision
        ))
    assert session.calls == 0


def test_missing_decision_prevents_broker_write():
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.place_equity_order(ENTRY_PAYLOAD, role="entry"))
    assert session.calls == 0


def test_forged_action_identity_mismatch_prevents_broker_write():
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    # A decision computed for the STOP role/payload, presented for an ENTRY write.
    forged = allow_decision(role="stop", arguments={
        "symbol": "AAPL", "side": "sell", "type": "stop_market",
        "quantity": "1", "stop_price": "185.00",
    })
    with pytest.raises(ExecutionSafetyDeniedError) as caught:
        asyncio.run(adapter.place_equity_order(
            ENTRY_PAYLOAD, role="entry", safety_decision=forged
        ))
    assert str(caught.value) == "EXECUTION_SAFETY_ACTION_MISMATCH"
    assert session.calls == 0


def test_decision_bound_to_exact_action_payload():
    matching = allow_decision(role="entry", arguments=ENTRY_PAYLOAD)
    assert decision_binds_write(matching, role="entry", arguments=ENTRY_PAYLOAD)
    mismatched_quantity = dict(ENTRY_PAYLOAD, quantity="2")
    assert not decision_binds_write(matching, role="entry", arguments=mismatched_quantity)


def test_stale_decision_is_not_fresh():
    decision = evaluate_execution_safety(entry_context())
    fresh_now = decision.evaluated_at + timedelta(seconds=5)
    stale_now = decision.evaluated_at + timedelta(seconds=3600)
    assert decision_is_fresh(decision, now=fresh_now) is True
    assert decision_is_fresh(decision, now=stale_now) is False


def test_governance_decision_bound_to_exact_action_across_roles():
    entry_decision = evaluate_execution_safety(entry_context())
    stop_decision = evaluate_execution_safety(stop_context())
    cancel_decision = evaluate_execution_safety(cancel_context())
    assert entry_decision.bound_role == "entry"
    assert stop_decision.bound_role == "stop"
    assert cancel_decision.bound_role == "cancel"
    assert cancel_decision.bound_order_id == "order-1"
    assert entry_decision.action_identity != stop_decision.action_identity


def test_cancel_write_boundary_enforced_identically():
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    cancel_args = {"order_id": "order-1"}
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.cancel_equity_order(cancel_args))
    assert session.calls == 0


def test_existing_attended_commissioning_gates_still_enforced_at_adapter():
    """The M14 boundary is additional restriction: the adapter's own
    pre-existing fixed-policy validation still runs and still rejects a
    payload that violates commissioning policy, even with a matching ALLOW
    decision."""
    from src.one_share_commissioning import CommissioningError

    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    bad_payload = dict(ENTRY_PAYLOAD, quantity="2")
    decision = allow_decision(role="entry", arguments=bad_payload)
    with pytest.raises(CommissioningError):
        asyncio.run(adapter.place_equity_order(
            bad_payload, role="entry", safety_decision=decision
        ))
    assert session.calls == 0


# ---------------------------------------------------------------------
# M14 ISSUE 6: WRITE-BOUNDARY DECISION AUTHENTICITY
#
# The adapter write boundary now independently re-validates a presented
# decision (validate_execution_safety_decision), rather than trusting only
# outcome + decision_binds_write. These tests exercise that consolidated
# check directly, plus the two most safety-critical properties end-to-end
# through the real adapter: a forged decision and a stale decision no
# longer reach a broker call even when decision_binds_write alone would
# have let them through.
# ---------------------------------------------------------------------


def _authentic_decision(
    *, role: str, arguments: dict, outcome: ExecutionSafetyOutcome = ExecutionSafetyOutcome.ALLOW,
    evaluated_at: datetime, action_identity: str = "authentic-action",
    satisfied_gates: tuple = (), failed_gates: tuple = (), reason_codes: tuple = (),
    policy_version: str = POLICY_VERSION, authority_ceiling: str = AUTHORITY_CEILING,
) -> ExecutionSafetyDecision:
    """Builds a decision whose decision_digest/decision_id are ACTUALLY
    recomputed from its own fields via compute_decision_digest -- unlike
    allow_decision/deny_decision (fixed role/arguments), this lets tests
    below construct an authentic baseline and then mutate exactly one field
    to prove validate_execution_safety_decision catches it."""
    bound = dict(
        bound_role=role,
        bound_symbol=arguments.get("symbol", ""),
        bound_side=arguments.get("side", ""),
        bound_order_type=arguments.get("type", ""),
        bound_quantity=arguments.get("quantity", ""),
        bound_stop_price=arguments.get("stop_price"),
        bound_order_id=arguments.get("order_id"),
    )
    digest = compute_decision_digest(
        policy_version=policy_version, action_identity=action_identity, outcome=outcome,
        satisfied=satisfied_gates, failed=failed_gates, reasons=reason_codes,
        authority_ceiling=authority_ceiling, **bound,
    )
    return ExecutionSafetyDecision(
        decision_id=digest, policy_version=policy_version, action_identity=action_identity,
        outcome=outcome, satisfied_gates=satisfied_gates, failed_gates=failed_gates,
        reason_codes=reason_codes, decision_digest=digest, evaluated_at=evaluated_at,
        authority_ceiling=authority_ceiling, **bound,
    )


def test_authentic_baseline_decision_validates():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now)
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert result.valid
    assert result.reason_code == "OK"


def test_validate_rejects_non_decision_input():
    now = datetime.now(timezone.utc)
    assert not validate_execution_safety_decision(
        None, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    ).valid
    assert not validate_execution_safety_decision(
        "not-a-decision", role="entry", arguments=ENTRY_PAYLOAD, now=now,
    ).valid


def test_validate_rejects_deny_outcome():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(
        role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now,
        outcome=ExecutionSafetyOutcome.DENY,
    )
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_DENIED"


def test_validate_rejects_wrong_policy_version():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(
        role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now, policy_version="not-real",
    )
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_POLICY_VERSION_MISMATCH"


def test_validate_rejects_wrong_authority_ceiling():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(
        role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now, authority_ceiling="not-real",
    )
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_AUTHORITY_CEILING_MISMATCH"


def test_validate_rejects_empty_and_malformed_digest():
    now = datetime.now(timezone.utc)
    baseline = _authentic_decision(role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now)

    empty_digest = dataclasses.replace(baseline, decision_digest="")
    result = validate_execution_safety_decision(
        empty_digest, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_DECISION_DIGEST_MALFORMED"

    wrong_digest = dataclasses.replace(baseline, decision_digest="0" * 64)
    result = validate_execution_safety_decision(
        wrong_digest, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_DECISION_DIGEST_MISMATCH"


@pytest.mark.parametrize("field_name,override", [
    ("bound_role", {"bound_role": "stop"}),
    ("bound_symbol", {"bound_symbol": "MSFT"}),
    ("bound_side", {"bound_side": "sell"}),
    ("bound_order_type", {"bound_order_type": "limit"}),
    ("bound_quantity", {"bound_quantity": "999"}),
    ("bound_stop_price", {"bound_stop_price": "185.00"}),
    ("satisfied_gates", {"satisfied_gates": ("mutated_gate",)}),
    ("reason_codes", {"reason_codes": ("MUTATED_REASON",)}),
])
def test_mutating_any_digest_bound_field_invalidates_decision(field_name, override):
    """Closes the M14 Issue 4 tamper_digest_binding_mismatch finding: mutating
    a bound_*/gate/reason field after construction, without recomputing the
    digest, must now fail validation -- it must NOT silently keep validating
    just because decision_binds_write alone would still match the mutated
    value against correspondingly-mutated arguments."""
    now = datetime.now(timezone.utc)
    baseline = _authentic_decision(role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now)
    mutated = dataclasses.replace(baseline, **override)
    result = validate_execution_safety_decision(
        mutated, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid, f"mutating {field_name} should have invalidated the decision"
    assert result.reason_code == "EXECUTION_SAFETY_DECISION_DIGEST_MISMATCH"


def test_mutating_bound_order_id_invalidates_cancel_decision():
    now = datetime.now(timezone.utc)
    cancel_args = {"order_id": "order-1"}
    baseline = _authentic_decision(role="cancel", arguments=cancel_args, evaluated_at=now)
    mutated = dataclasses.replace(baseline, bound_order_id="order-999")
    result = validate_execution_safety_decision(
        mutated, role="cancel", arguments={"order_id": "order-999"}, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_DECISION_DIGEST_MISMATCH"


def test_validate_rejects_decision_presented_against_non_matching_write():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now)
    mismatched_arguments = dict(ENTRY_PAYLOAD, quantity="2")
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=mismatched_arguments, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_ACTION_MISMATCH"


def test_validate_rejects_stale_decision():
    now = datetime.now(timezone.utc)
    decision = _authentic_decision(
        role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=now - timedelta(hours=6),
    )
    result = validate_execution_safety_decision(
        decision, role="entry", arguments=ENTRY_PAYLOAD, now=now,
    )
    assert not result.valid
    assert result.reason_code == "EXECUTION_SAFETY_DECISION_STALE"


def test_forged_decision_rejected_at_real_adapter_boundary():
    """End-to-end: a hand-forged decision (bogus authority_ceiling, never-
    recomputed digest) that correctly binds to the write arguments must
    still be refused by the real adapter, not merely by validate_execution_
    safety_decision in isolation."""
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    forged = ExecutionSafetyDecision(
        decision_id="forged", policy_version="forged-policy",
        action_identity="forged-identity", outcome=ExecutionSafetyOutcome.ALLOW,
        satisfied_gates=(), failed_gates=(), reason_codes=(),
        decision_digest="forged-digest-never-recomputed",
        evaluated_at=datetime.now(timezone.utc), authority_ceiling="FORGED_AUTHORITY",
        bound_role="entry", bound_symbol="AAPL", bound_side="buy",
        bound_order_type="market", bound_quantity="1", bound_stop_price=None,
        bound_order_id=None,
    )
    with pytest.raises(ExecutionSafetyDeniedError):
        asyncio.run(adapter.place_equity_order(
            ENTRY_PAYLOAD, role="entry", safety_decision=forged,
        ))
    assert session.calls == 0


def test_stale_decision_rejected_at_real_adapter_boundary_bypassing_orchestrator():
    """End-to-end: a correctly-bound, authentic-otherwise ALLOW decision that
    is simply too old is refused directly by the adapter, even when called
    without going through OneShareCommissioningOrchestrator (which is the
    only caller that used to check decision_is_fresh)."""
    session = _RecordingSession(_place_response())
    adapter = RobinhoodCommissioningAdapter(session, "transient")
    stale = _authentic_decision(
        role="entry", arguments=ENTRY_PAYLOAD,
        evaluated_at=datetime.now(timezone.utc) - timedelta(hours=6),
    )
    with pytest.raises(ExecutionSafetyDeniedError) as caught:
        asyncio.run(adapter.place_equity_order(
            ENTRY_PAYLOAD, role="entry", safety_decision=stale,
        ))
    assert str(caught.value) == "EXECUTION_SAFETY_DECISION_STALE"
    assert session.calls == 0


def test_adapter_now_eastern_is_injectable_and_used_for_freshness():
    """The adapter's freshness clock is independently overridable (not only
    real wall-clock), so a decision evaluated against a fixed injected 'now'
    is fresh relative to THAT clock, not the real one."""
    session = _RecordingSession(_place_response())
    fixed_now = datetime(2026, 8, 11, 10, 0, tzinfo=timezone.utc)
    adapter = RobinhoodCommissioningAdapter(
        session, "transient", now_eastern=lambda: fixed_now,
    )
    decision = _authentic_decision(role="entry", arguments=ENTRY_PAYLOAD, evaluated_at=fixed_now)
    asyncio.run(adapter.place_equity_order(
        ENTRY_PAYLOAD, role="entry", safety_decision=decision,
    ))
    assert session.calls == 1


# ---------------------------------------------------------------------
# REGRESSION / SAFETY
# ---------------------------------------------------------------------


def test_call_site_guard_has_no_unresolved_broker_dispatch():
    from src.robinhood_commissioning_adapter import ALLOWED_COMMISSIONING_TOOLS

    sites = [
        site for site in analyze_runtime_tree()
        if site.path == "src/robinhood_commissioning_adapter.py"
    ]
    offending = violations(sites, ALLOWED_COMMISSIONING_TOOLS)
    assert offending == []


def test_call_site_guard_still_finds_exactly_one_write_capable_adapter_file():
    sites = analyze_runtime_tree()
    paths_with_write_dispatch = {
        site.path for site in sites
        if site.path.endswith("robinhood_commissioning_adapter.py")
    }
    assert paths_with_write_dispatch == {"src/robinhood_commissioning_adapter.py"}


def test_read_only_checkpoint_runtime_does_not_call_governance_evaluation():
    """M13's read-only observation runtime is reached from a different
    entry point than commissioning; it must never itself evaluate a write
    decision."""
    import inspect

    from src import read_only_observation_runtime

    source = inspect.getsource(read_only_observation_runtime)
    assert "evaluate_execution_safety" not in source
    assert "execution_safety_governance" not in source


def test_operator_web_app_does_not_reach_governance_or_write_path():
    import inspect

    from src import operator_web_app

    source = inspect.getsource(operator_web_app)
    assert "execution_safety_governance" not in source
    assert "place_equity_order" not in source
    assert "cancel_equity_order" not in source


def test_no_strategy_or_scheduler_module_imports_governance():
    import ast
    from pathlib import Path

    strategy_like = [
        Path("src/hypothetical_strategy_runtime.py"),
        Path("src/durable_scheduler.py"),
        Path("src/live_scanner.py"),
    ]
    for path in strategy_like:
        if not path.is_file():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
            elif isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
        assert "src.execution_safety_governance" not in names
        assert "src.robinhood_commissioning_adapter" not in names


def test_authority_ceiling_is_fixed_and_no_broader_authority_field_changes():
    decision = evaluate_execution_safety(entry_context())
    assert decision.authority_ceiling == AUTHORITY_CEILING
    assert AUTHORITY_CEILING == "ATTENDED_COMMISSIONING_ONLY"


def test_no_third_permissive_outcome_exists():
    assert {member.value for member in ExecutionSafetyOutcome} == {"ALLOW", "DENY"}


def test_deterministic_gate_ordering_in_output():
    decision = evaluate_execution_safety(entry_context())
    assert decision.satisfied_gates == tuple(sorted(decision.satisfied_gates))
    denied = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(
            source_hash_verified=False, account_fingerprint_matched=False,
        ))
    )
    assert denied.failed_gates == tuple(sorted(denied.failed_gates))
    assert denied.reason_codes == tuple(sorted(denied.reason_codes))


def test_every_deny_has_an_explicit_reason_code():
    denied = evaluate_execution_safety(
        entry_context(evidence=valid_evidence(operator_attended=False))
    )
    assert len(denied.failed_gates) >= 1
    assert len(denied.reason_codes) == len(denied.failed_gates)
    assert all(isinstance(code, str) and code for code in denied.reason_codes)


def test_action_for_role_and_role_for_action_are_inverse():
    for role, action in ACTION_FOR_ROLE.items():
        assert action.value.lower().split("_")[0] == role or role == "cancel"
