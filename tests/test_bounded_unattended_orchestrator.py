from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from src.bounded_unattended_orchestrator import (
    APPROVED_SAFETY_SOURCE,
    AUTHORITY_CEILING,
    FIXED_WATCHLIST,
    MUTATION_BOUNDARY,
    BoundedUnattendedOrchestrator,
    DailyAttemptEvidence,
    ExistingSafetyEvidence,
    MachineGateEvidence,
    PolicyEvidenceOrigin,
    SafetyResult,
    SimulatedAction,
    SimulatedMutationBoundary,
    UnattendedOrchestratorError,
    UnattendedState,
    build_unattended_pilot_policy,
    compute_policy_digest,
    compute_run_digest,
)
from src.broker_snapshot import account_fingerprint
from src.exit_engine import ExitAction, ExitDecision
from src.market_data_canonical import MarketDataQuality
from src.order_lifecycle_model import LifecycleState
from src.protection_grounding_assessment import (
    AuthoritativeProtectionGroundingRequest,
    evaluate_authoritative_working_stop_grounding,
)
from src.protection_observation_model import EvidenceSourceClass
from src.shadow_trading_decision import (
    ShadowDecisionType,
    ShadowTradingDecision,
    compute_shadow_decision_digest,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_authoritative_protection_grounding import stop_order
from tests.test_live_reconciliation import make_account_state, make_position


NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)  # 10:00 ET
RELEASE = "a" * 40
IMPLEMENTATION = "b" * 64
ACCOUNT_NUMBER = "M21-FIXTURE-ACCOUNT"
ACCOUNT = account_fingerprint(ACCOUNT_NUMBER)


def policy(**changes):
    values = {
        "enabled": True,
        "evidence_origin": PolicyEvidenceOrigin.TEST_FIXTURE,
        "account_fingerprint_reference": ACCOUNT,
        "strategy_id": "orb_long_v0",
        "strategy_version": "orb_long_v0-0.1.0",
        "symbol_allowlist": FIXED_WATCHLIST,
        "quantity": 1,
        "entry_start": "09:40",
        "entry_end": "11:00",
        "maximum_trade_attempts_per_day": 1,
        "maximum_reconciliation_polls": 3,
        "maximum_risk_per_share": "5",
        "maximum_entry_notional": "1000",
        "deployment_expiry": date(2026, 8, 11),
        "release_sha": RELEASE,
        "implementation_digest": IMPLEMENTATION,
    }
    values.update(changes)
    return build_unattended_pilot_policy(**values)


def candidate(**changes):
    values = {
        "shadow_run_id": "m21-fixture",
        "symbol": "AAPL",
        "trading_date": NOW.date(),
        "strategy_version": "orb_long_v0-0.1.0",
        "implementation_digest": IMPLEMENTATION,
        "market_data_digest": "c" * 64,
        "opening_range_identity": "d" * 64,
        "signal_timestamp": NOW,
        "decision_type": ShadowDecisionType.LONG_CANDIDATE,
        "proposed_entry": Decimal("100"),
        "proposed_stop": Decimal("99"),
        "proposed_target": Decimal("102"),
        "reason": "fixture candidate",
        "market_data_quality": MarketDataQuality.VALID,
        "exchange_session": "REGULAR",
    }
    values.update(changes)
    digest = compute_shadow_decision_digest(**values)
    return ShadowTradingDecision(
        decision_id=digest, created_at=NOW, **values
    )


def snapshot(*, positions=(), orders=(), account_number=ACCOUNT_NUMBER,
             completed_at=NOW - timedelta(seconds=1)):
    state = make_account_state(
        positions=positions, orders=orders, account_number=account_number
    )
    return make_broker_snapshot(
        state,
        reconciliation_id="m21-cycle",
        started_at=completed_at - timedelta(seconds=3),
        positions_read_at=completed_at - timedelta(seconds=2),
        orders_read_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
    )


def daily(**changes):
    values = {
        "durable_local_source_available": True,
        "broker_same_day_source_available": True,
        "durable_attempt_recorded": False,
        "broker_same_day_entry_seen": False,
        "evidence_id": "e" * 64,
    }
    values.update(changes)
    return DailyAttemptEvidence(**values)


def gates(**changes):
    values = {
        "suspension_state": "CLEAR",
        "suspension_fresh": True,
        "risk_state_available": True,
        "risk_state_allows_new_risk": True,
        "operational_health_available": True,
        "operational_health_allows_new_risk": True,
        "source_hash_verified": True,
    }
    values.update(changes)
    return MachineGateEvidence(**values)


def safety(action, result=SafetyResult.ALLOW, *, moment=NOW):
    token = action.value.lower()
    return ExistingSafetyEvidence(
        source_component=APPROVED_SAFETY_SOURCE,
        action=action,
        action_identity=(hashlib_sha(token + "-action")),
        safety_decision_identity=(hashlib_sha(token + "-decision")),
        result=result,
        evaluated_at=moment,
        simulation_only=True,
    )


def hashlib_sha(value: str) -> str:
    import hashlib
    return hashlib.sha256(value.encode()).hexdigest()


def started(*, item=None, broker=None, attempt=None, machine_gates=None,
            current_policy=None, now=NOW):
    boundary = SimulatedMutationBoundary()
    orchestrator = BoundedUnattendedOrchestrator(boundary)
    run = orchestrator.start(
        policy=current_policy or policy(),
        candidate=candidate() if item is None else item,
        snapshot=broker or snapshot(),
        daily_attempt=attempt or daily(),
        gates=machine_gates or gates(),
        now=now,
    )
    return orchestrator, boundary, run


def grounded(run, *, active=True):
    broker = snapshot(
        positions=(make_position(quantity="1"),),
        orders=(stop_order(order_id="stop-1", stop_price="99"),) if active else (),
    )
    request = AuthoritativeProtectionGroundingRequest(
        source_candidate_id=run.candidate_id,
        source_handoff_id="1" * 64,
        source_protection_plan_id="2" * 64,
        symbol="AAPL",
        expected_stop_order_id="stop-1",
        expected_position_quantity=Decimal("1"),
        expected_stop_price=Decimal("99"),
        release_identity=RELEASE,
        implementation_digest=IMPLEMENTATION,
        account_fingerprint_reference=ACCOUNT,
        evidence_source_class=(
            EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
        ),
    )
    return evaluate_authoritative_working_stop_grounding(
        request,
        snapshot=broker,
        assessed_at=NOW,
        expected_release_identity=RELEASE,
        expected_implementation_digest=IMPLEMENTATION,
        expected_account_fingerprint_reference=ACCOUNT,
    )


def test_disabled_invalid_and_production_policies_cannot_trade():
    _, boundary, run = started(current_policy=policy(enabled=False))
    assert run.state is UnattendedState.DISABLED and boundary.calls == []
    production = policy(evidence_origin=PolicyEvidenceOrigin.REVIEWED_PRODUCTION)
    _, boundary, run = started(current_policy=production)
    assert run.state is UnattendedState.POLICY_BLOCKED
    assert run.blocking_reasons == ("PRODUCTION_ACTIVATION_NOT_IMPLEMENTED",)
    assert boundary.calls == []
    with pytest.raises(UnattendedOrchestratorError):
        policy(quantity=2)
    with pytest.raises(UnattendedOrchestratorError):
        policy(evidence_origin=PolicyEvidenceOrigin.TEST_FIXTURE,
               activation_approved=True, permits_automated_action=True)


@pytest.mark.parametrize(
    ("kwargs", "state"),
    [
        ({"broker": snapshot(account_number="OTHER")}, UnattendedState.ACCOUNT_BLOCKED),
        ({"broker": snapshot(positions=(make_position(quantity="1"),))}, UnattendedState.ACCOUNT_BLOCKED),
        ({"broker": snapshot(orders=(stop_order(),))}, UnattendedState.ACCOUNT_BLOCKED),
        ({"attempt": daily(durable_attempt_recorded=True)}, UnattendedState.POLICY_BLOCKED),
        ({"attempt": daily(broker_same_day_source_available=False)}, UnattendedState.KILL_STATE),
        ({"machine_gates": gates(suspension_state="SUSPENDED")}, UnattendedState.SUSPENSION_BLOCKED),
        ({"machine_gates": gates(risk_state_allows_new_risk=False)}, UnattendedState.RISK_BLOCKED),
        ({"machine_gates": gates(operational_health_allows_new_risk=False)}, UnattendedState.HEALTH_BLOCKED),
    ],
)
def test_machine_verifiable_entry_gates_fail_closed(kwargs, state):
    _, boundary, run = started(**kwargs)
    assert run.state is state and boundary.calls == []


@pytest.mark.parametrize(
    "changes",
    [
        {"decision_type": ShadowDecisionType.REJECTED},
        {"symbol": "IBM"},
        {"strategy_version": "other-v1"},
        {"implementation_digest": "f" * 64},
        {"market_data_quality": MarketDataQuality.STALE},
        {"exchange_session": "EXTENDED"},
        {"proposed_stop": Decimal("101")},
    ],
)
def test_candidate_strategy_symbol_direction_and_data_must_match(changes):
    _, boundary, run = started(item=candidate(**changes))
    assert run.state is UnattendedState.MARKET_DATA_BLOCKED
    assert boundary.calls == []


def test_entry_uses_existing_safety_evidence_and_consumes_daily_allowance():
    orchestrator, boundary, run = started()
    assert run.state is UnattendedState.ENTRY_PREFLIGHT
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    run = orchestrator.mark_safety(run, entry_safety, now=NOW)
    assert run.state is UnattendedState.ENTRY_READY
    run = orchestrator.simulate_write(run, entry_safety, now=NOW)
    assert run.state is UnattendedState.ENTRY_SUBMITTED
    assert run.daily_allowance_consumed is True
    assert run.simulated_write_count == 1 and run.broker_write_count == 0
    assert boundary.calls[0][0] is SimulatedAction.ENTRY_PLACE
    assert run.mutation_boundary == MUTATION_BOUNDARY


@pytest.mark.parametrize(
    ("lifecycle", "state"),
    [
        (LifecycleState.SUBMITTED, UnattendedState.ENTRY_RECONCILING),
        (LifecycleState.FILLED, UnattendedState.PROTECTION_REQUIRED),
        (LifecycleState.REJECTED, UnattendedState.ENTRY_FAILED),
        (LifecycleState.CANCELED, UnattendedState.ENTRY_FAILED),
        (LifecycleState.PARTIALLY_FILLED, UnattendedState.PROTECTION_FAILED),
        (LifecycleState.UNKNOWN, UnattendedState.ENTRY_STATE_UNKNOWN),
    ],
)
def test_entry_reconciliation_is_conservative(lifecycle, state):
    orchestrator, _, run = started()
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    run = orchestrator.simulate_write(
        orchestrator.mark_safety(run, entry_safety, now=NOW), entry_safety,
        now=NOW,
    )
    run = orchestrator.reconcile_entry(run, lifecycle)
    assert run.state is state
    assert run.daily_allowance_consumed is True


def test_filled_entry_requires_stop_and_m20_grounding_before_open_protected():
    orchestrator, boundary, run = started()
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    run = orchestrator.simulate_write(orchestrator.mark_safety(run, entry_safety, now=NOW), entry_safety, now=NOW)
    run = orchestrator.reconcile_entry(run, LifecycleState.FILLED)
    assert run.state is UnattendedState.PROTECTION_REQUIRED
    stop_safety = safety(SimulatedAction.STOP_PLACE)
    run = orchestrator.simulate_write(orchestrator.mark_safety(run, stop_safety, now=NOW), stop_safety, now=NOW)
    assert run.state is UnattendedState.STOP_SUBMITTED
    assessment = grounded(run)
    run = orchestrator.reconcile_stop(run, assessment)
    assert run.state is UnattendedState.OPEN_PROTECTED
    assert run.protection_grounded is True
    assert len(boundary.calls) == 2


def test_grounding_false_or_later_demotion_never_resumes_candidate_processing():
    orchestrator, _, run = started()
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    stop_safety = safety(SimulatedAction.STOP_PLACE)
    run = orchestrator.reconcile_entry(
        orchestrator.simulate_write(orchestrator.mark_safety(run, entry_safety, now=NOW), entry_safety, now=NOW),
        LifecycleState.FILLED,
    )
    run = orchestrator.simulate_write(orchestrator.mark_safety(run, stop_safety, now=NOW), stop_safety, now=NOW)
    good = grounded(run)
    open_run = orchestrator.reconcile_stop(run, good)
    bad = grounded(open_run, active=False)
    demoted = orchestrator.observe_grounding(open_run, bad)
    assert demoted.state is UnattendedState.UNPROTECTED_POSITION
    assert demoted.protection_grounded is False


def test_target_and_time_exit_preserve_cancel_then_exit_sequence():
    orchestrator, boundary, run = started()
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    stop_safety = safety(SimulatedAction.STOP_PLACE)
    run = orchestrator.reconcile_entry(orchestrator.simulate_write(orchestrator.mark_safety(run, entry_safety, now=NOW), entry_safety, now=NOW), LifecycleState.FILLED)
    run = orchestrator.simulate_write(orchestrator.mark_safety(run, stop_safety, now=NOW), stop_safety, now=NOW)
    run = orchestrator.reconcile_stop(run, grounded(run))
    run = orchestrator.request_exit(run, ExitDecision(ExitAction.EXIT_TIME, "15:45 ET time exit"))
    assert run.state is UnattendedState.EXIT_PENDING
    cancel_safety = safety(SimulatedAction.STOP_CANCEL)
    run = orchestrator.mark_safety(run, cancel_safety, now=NOW)
    run = orchestrator.simulate_write(run, cancel_safety, now=NOW)
    run = orchestrator.confirm_stop_cancelled(run, True)
    exit_safety = safety(SimulatedAction.EXIT_PLACE)
    run = orchestrator.mark_safety(run, exit_safety, now=NOW)
    run = orchestrator.simulate_write(run, exit_safety, now=NOW)
    run = orchestrator.reconcile_exit(run, position_flat=True, unresolved_orders=False)
    assert run.state is UnattendedState.TRADE_COMPLETE
    assert [call[0] for call in boundary.calls] == [
        SimulatedAction.ENTRY_PLACE, SimulatedAction.STOP_PLACE,
        SimulatedAction.STOP_CANCEL, SimulatedAction.EXIT_PLACE,
    ]
    assert run.daily_allowance_consumed is True
    assert run.broker_write_count == 0 and run.execution_authorized is False


def test_exit_cannot_assume_oco_or_remove_protection_without_confirmation():
    _, _, run = started()
    # A non-grounded run cannot enter any target/time-exit transition.
    blocked = BoundedUnattendedOrchestrator.request_exit(
        run, ExitDecision(ExitAction.EXIT_PROFIT_TARGET, "+2R")
    )
    assert blocked.state is UnattendedState.EXIT_BLOCKED


def test_restart_reconciles_authoritative_state_before_watching():
    current = policy()
    assert BoundedUnattendedOrchestrator.restart(
        policy=current, snapshot=snapshot(), daily_attempt=daily(), now=NOW
    ) is UnattendedState.WATCHING
    assert BoundedUnattendedOrchestrator.restart(
        policy=current,
        snapshot=snapshot(positions=(make_position(quantity="1"),)),
        daily_attempt=daily(durable_attempt_recorded=True), now=NOW,
    ) is UnattendedState.PROTECTION_REQUIRED
    assert BoundedUnattendedOrchestrator.restart(
        policy=current, snapshot=snapshot(orders=(stop_order(),)),
        daily_attempt=daily(durable_attempt_recorded=True), now=NOW,
    ) is UnattendedState.ENTRY_RECONCILING
    assert BoundedUnattendedOrchestrator.restart(
        policy=current, snapshot=snapshot(),
        daily_attempt=daily(broker_same_day_source_available=False), now=NOW,
    ) is UnattendedState.KILL_STATE


def test_polling_is_bounded_and_unknown_state_fails_closed():
    orchestrator, _, run = started()
    entry_safety = safety(SimulatedAction.ENTRY_PLACE)
    run = orchestrator.simulate_write(orchestrator.mark_safety(run, entry_safety, now=NOW), entry_safety, now=NOW)
    for _ in range(3):
        run = orchestrator.reconcile_entry(run, LifecycleState.SUBMITTED)
    run = orchestrator.enforce_poll_bound(run, policy())
    assert run.state is UnattendedState.ENTRY_STATE_UNKNOWN
    assert "RECONCILIATION_POLL_LIMIT" in run.blocking_reasons


def test_safety_deny_incomplete_and_synthetic_acknowledgements_fail_closed():
    orchestrator, boundary, run = started()
    denied = orchestrator.mark_safety(
        run, safety(SimulatedAction.ENTRY_PLACE, SafetyResult.DENY),
        now=NOW,
    )
    assert denied.state is UnattendedState.ENTRY_FAILED
    incomplete = orchestrator.mark_safety(
        run, safety(SimulatedAction.ENTRY_PLACE, SafetyResult.INCOMPLETE),
        now=NOW,
    )
    assert incomplete.state is UnattendedState.KILL_STATE
    with pytest.raises(UnattendedOrchestratorError):
        replace(safety(SimulatedAction.ENTRY_PLACE),
                attended_acknowledgements_synthesized=True)
    assert boundary.calls == []


def test_deterministic_policy_and_run_evidence_and_closed_authority():
    left_policy = policy()
    right_policy = policy()
    assert left_policy.policy_id == right_policy.policy_id
    assert left_policy.policy_digest == compute_policy_digest(left_policy)
    _, _, left = started(current_policy=left_policy)
    _, _, right = started(current_policy=right_policy)
    assert left.state_digest == right.state_digest == compute_run_digest(left)
    assert left.broker_write_count == 0
    assert left.execution_authorized is False
    assert left.protection_grounded is False
    assert left.activation_approved is False
    assert left.permits_automated_action is False
    assert left.live_ready is False and left.unattended_ready is False
    assert left.authority_ceiling == AUTHORITY_CEILING


def test_no_parallel_broker_or_governance_path_is_added():
    path = Path("src/bounded_unattended_orchestrator.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    calls = {
        node.func.attr if isinstance(node.func, ast.Attribute) else node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Attribute, ast.Name))
    }
    assert "src.robinhood_commissioning_adapter" not in imports
    assert "src.execution_safety_governance" not in imports
    assert "evaluate_execution_safety" not in calls
    assert not {"place_equity_order", "cancel_equity_order", "replace", "flatten"} & calls



def test_policy_risk_and_notional_caps_are_machine_enforced():
    _, _, risk = started(
        item=candidate(
            proposed_entry=Decimal("100"),
            proposed_stop=Decimal("94"),
        )
    )
    _, _, notional = started(
        item=candidate(
            proposed_entry=Decimal("1001"),
            proposed_stop=Decimal("1000"),
            proposed_target=Decimal("1003"),
        )
    )
    assert risk.state is UnattendedState.MARKET_DATA_BLOCKED
    assert "RISK_PER_SHARE_EXCEEDS_POLICY" in risk.blocking_reasons
    assert notional.state is UnattendedState.MARKET_DATA_BLOCKED
    assert "ENTRY_NOTIONAL_EXCEEDS_POLICY" in notional.blocking_reasons


def test_before_and_after_entry_window_are_bounded_without_writes():
    before = datetime(2026, 8, 11, 13, 35, tzinfo=timezone.utc)
    after = datetime(2026, 8, 11, 15, 1, tzinfo=timezone.utc)
    _, before_boundary, waiting = started(
        now=before,
        broker=snapshot(completed_at=before - timedelta(seconds=1)),
    )
    _, after_boundary, blocked = started(
        now=after,
        broker=snapshot(completed_at=after - timedelta(seconds=1)),
    )
    assert waiting.state is UnattendedState.WAITING_FOR_WINDOW
    assert blocked.state is UnattendedState.MARKET_DATA_BLOCKED
    assert before_boundary.calls == after_boundary.calls == []


def test_stale_or_future_safety_evidence_never_reaches_simulated_boundary():
    orchestrator, boundary, run = started()
    stale = safety(
        SimulatedAction.ENTRY_PLACE,
        moment=NOW - timedelta(seconds=31),
    )
    future = safety(
        SimulatedAction.ENTRY_PLACE,
        moment=NOW + timedelta(seconds=1),
    )
    assert (
        orchestrator.mark_safety(run, stale, now=NOW).state
        is UnattendedState.KILL_STATE
    )
    assert (
        orchestrator.mark_safety(run, future, now=NOW).state
        is UnattendedState.KILL_STATE
    )
    assert boundary.calls == []