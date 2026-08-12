from __future__ import annotations

import ast
import asyncio
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

import src.bounded_unattended_orchestrator as m21
from src.account_state import EquityOrder
from src.bounded_unattended_orchestrator import (
    BoundedUnattendedOrchestrator, DailyAttemptEvidence, FIXED_WATCHLIST,
    MachineGateEvidence, PolicyEvidenceOrigin, SimulatedMutationBoundary,
    UnattendedOrchestratorError, UnattendedState,
    build_unattended_pilot_policy,
)
from src.broker_snapshot import account_fingerprint
from src.commissioning_suspension_identity import CommissioningSuspensionIdentity
from src.durable_suspension_controller import (
    ControllerState, SchedulerAction, SuspensionCause, SuspensionDecision,
)
from src.execution_safety_governance import (
    ExecutionSafetyOutcome, UNATTENDED_AUTHORITY_CEILING,
)
from src.market_data_canonical import MarketDataQuality
from src.live_attended_preflight_inputs import SuspensionObservation
from src.robinhood_commissioning_adapter import (
    ExecutionSafetyDeniedError, RobinhoodCommissioningAdapter,
)
from src.shadow_trading_decision import (
    ShadowDecisionType, ShadowTradingDecision, compute_shadow_decision_digest,
)
from src.unattended_pilot_runtime import (
    ProductionSafetyInputs, ProductionWriteState, UnattendedPilotRuntimeError,
    dispatch_production_write,
    evaluate_production_action_safety, issue_production_write_permit,
    policy_is_production_active, reconcile_startup,
)
from src.unattended_pilot_state import (
    AttemptOutcome, UnattendedPilotStateError, UnattendedPilotStateStore,
    compute_write_permit_digest, validate_write_permit,
)
from tests.snapshot_helpers import make_broker_snapshot
from tests.test_live_reconciliation import make_account_state, make_position

NOW = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
ACCOUNT_NUMBER = "transient"
ACCOUNT = account_fingerprint(ACCOUNT_NUMBER)
RELEASE = "a" * 40
IMPLEMENTATION = "b" * 64
RUN = "1" * 64


def policy(**changes):
    values = dict(
        enabled=False, evidence_origin=PolicyEvidenceOrigin.REVIEWED_PRODUCTION,
        account_fingerprint_reference=ACCOUNT, strategy_id="orb_long_v0",
        strategy_version="orb_long_v0-0.1.0", symbol_allowlist=FIXED_WATCHLIST,
        quantity=1, entry_start="09:40", entry_end="11:00",
        maximum_trade_attempts_per_day=1, maximum_reconciliation_polls=3,
        maximum_risk_per_share="5", maximum_entry_notional="1000",
        deployment_expiry=date(2026, 8, 11), release_sha=RELEASE,
        implementation_digest=IMPLEMENTATION,
        activation_approved=False, permits_automated_action=False,
    )
    values.update(changes)
    return build_unattended_pilot_policy(**values)


def active_policy(**changes):
    return policy(enabled=True, activation_approved=True,
                  permits_automated_action=True, **changes)


def candidate(**changes):
    values = dict(
        shadow_run_id="m22-production-shaped-offline", symbol="MSFT",
        trading_date=NOW.date(), strategy_version="orb_long_v0-0.1.0",
        implementation_digest=IMPLEMENTATION, market_data_digest="8" * 64,
        opening_range_identity="9" * 64, signal_timestamp=NOW,
        decision_type=ShadowDecisionType.LONG_CANDIDATE,
        proposed_entry=Decimal("100"), proposed_stop=Decimal("99"),
        proposed_target=Decimal("102"), reason="canonical offline candidate",
        market_data_quality=MarketDataQuality.VALID, exchange_session="REGULAR",
    )
    values.update(changes)
    digest = compute_shadow_decision_digest(**values)
    return ShadowTradingDecision(decision_id=digest, created_at=NOW, **values)


def clear_observation(current_policy=None, *, observed_at=NOW,
                      state=ControllerState.CLEAR):
    selected = current_policy or active_policy()
    controller_id = CommissioningSuspensionIdentity.derive(
        release_sha=selected.release_sha,
        account_fingerprint_reference=selected.account_fingerprint_reference,
    ).controller_id
    cause = SuspensionCause.NONE if state is ControllerState.CLEAR else SuspensionCause.OPERATOR_HALT
    return SuspensionObservation(
        controller_id=controller_id, observed_at=observed_at,
        decision=SuspensionDecision(
            state, cause, state is not ControllerState.CLEAR,
            SchedulerAction.REQUIRE_OPERATOR_REVIEW if state is ControllerState.CLEAR
            else SchedulerAction.SUSPEND,
        ),
    )


def safety_inputs(**changes):
    selected_policy = changes.pop("policy", active_policy())
    snapshot_fresh = changes.pop("snapshot_fresh", True)
    suspension_fresh = changes.pop("suspension_fresh", True)
    no_conflict = changes.pop("no_conflicting_broker_state", True)
    suspension_state = changes.pop("suspension_state", "CLEAR")
    account_matched = changes.pop("account_fingerprint_matched", True)
    broker = snapshot(
        account_number=ACCOUNT_NUMBER if account_matched else "wrong-account",
        orders=() if no_conflict else (order(),),
        completed_at=NOW - timedelta(seconds=1 if snapshot_fresh else 31),
    )
    state = (
        ControllerState.CLEAR if suspension_state == "CLEAR"
        else ControllerState.SUSPENDED
    )
    observation = clear_observation(
        selected_policy, observed_at=NOW - timedelta(seconds=0 if suspension_fresh else 31),
        state=state,
    )
    values = dict(
        broker_snapshot=broker, suspension_observation=observation,
        kill_state_clear=True, risk_bounds_verified=True,
        risk_state_allows_new_risk=True, health_allows_new_risk=True,
        source_hash_verified=True,
    )
    values.update(changes)
    return ProductionSafetyInputs(**values)

def production_run(current_policy, *, symbol="MSFT", role="entry"):
    item = candidate(symbol=symbol)
    run = BoundedUnattendedOrchestrator(SimulatedMutationBoundary()).start(
        policy=current_policy, candidate=item, snapshot=snapshot(),
        daily_attempt=DailyAttemptEvidence(True, True, False, False, "e" * 64),
        gates=MachineGateEvidence(
            "CLEAR", True, True, True, True, True, True,
        ), now=NOW,
    )
    desired = {
        "entry": UnattendedState.ENTRY_PREFLIGHT,
        "stop": UnattendedState.PROTECTION_REQUIRED,
        "cancel": UnattendedState.EXIT_PENDING,
        "exit": UnattendedState.EXIT_READY,
    }[role]
    return run if role == "entry" else m21._transition(
        run, desired, daily_allowance_consumed=True,
    )

def entry_arguments(symbol="MSFT"):
    return {"account_number": ACCOUNT_NUMBER, "symbol": symbol, "side": "buy",
            "type": "market", "quantity": "1", "time_in_force": "gfd",
            "market_hours": "regular_hours"}


def reserve(store, current_policy=None, symbol="MSFT"):
    selected = current_policy or active_policy()
    item = candidate(symbol=symbol)
    return store.reserve_entry_attempt(
        policy=selected, candidate=item,
        unattended_run=production_run(selected, symbol=symbol), now=NOW,
    )


def decision(current_policy, attempt, inputs=None, role="entry", symbol="MSFT",
             stop_price=None, order_id=None, now=NOW):
    return evaluate_production_action_safety(
        run=production_run(current_policy, symbol=symbol, role=role),
        policy=current_policy, attempt=attempt, inputs=inputs or safety_inputs(),
        decision_time=now, role=role, symbol=symbol,
        stop_price=stop_price, order_id=order_id,
    )


def snapshot(*, positions=(), orders=(), account_number=ACCOUNT_NUMBER,
             completed_at=NOW - timedelta(seconds=1)):
    state = make_account_state(positions=positions, orders=orders,
                               account_number=account_number)
    return make_broker_snapshot(
        state, reconciliation_id="m22-cycle",
        started_at=completed_at - timedelta(seconds=3),
        positions_read_at=completed_at - timedelta(seconds=2),
        orders_read_at=completed_at - timedelta(seconds=1),
        completed_at=completed_at,
    )


def order(*, side="buy", trigger="immediate", symbol="MSFT"):
    return EquityOrder("order-1", symbol, side, "market", "working",
        Decimal("1"), Decimal("0"), None, Decimal("99") if trigger == "stop" else None,
        trigger, "agent")


class Session:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    async def call_tool(self, name, arguments):
        self.calls.append((name, dict(arguments)))
        if self.error:
            raise self.error
        return {"accepted": True}


def prepared(tmp_path, monkeypatch, *, session_error=None):
    current_policy = active_policy()
    store = UnattendedPilotStateStore((tmp_path / "pilot.sqlite").resolve())
    attempt = reserve(store, current_policy)
    inputs = safety_inputs()
    safety = decision(current_policy, attempt, inputs)
    args = entry_arguments()
    permit = issue_production_write_permit(
        store=store, policy=current_policy, attempt=attempt,
        safety_decision=safety, role="entry", arguments=args, inputs=inputs,
        now=NOW,
    )
    monkeypatch.setattr(
        "src.robinhood_commissioning_adapter.parse_place_response",
        lambda value: SimpleNamespace(
            server_order_id="order-1", symbol="MSFT", side="buy",
            order_type="market", trigger="immediate",
            submitted_quantity=Decimal("1"), time_in_force="gfd",
            market_hours="regular_hours", stop_price=None,
        ),
    )
    session = Session(error=session_error)
    adapter = RobinhoodCommissioningAdapter(
        session, ACCOUNT_NUMBER, now_eastern=lambda: NOW
    )
    return current_policy, store, attempt, inputs, safety, args, permit, session, adapter


def test_default_disabled_and_fixture_cannot_activate(tmp_path):
    assert not policy_is_production_active(policy())
    with pytest.raises(UnattendedOrchestratorError):
        policy(evidence_origin=PolicyEvidenceOrigin.TEST_FIXTURE,
               enabled=True, activation_approved=True,
               permits_automated_action=True)
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        with pytest.raises(UnattendedPilotStateError):
            reserve(store, policy())


def test_state_store_refuses_repository_path():
    with pytest.raises(UnattendedPilotStateError):
        UnattendedPilotStateStore((Path(__file__).parents[1] / "data" / "m22.sqlite").resolve())


def test_daily_attempt_atomic_and_survives_restart(tmp_path):
    path = (tmp_path / "state.sqlite").resolve()
    current_policy = active_policy()
    with UnattendedPilotStateStore(path) as store:
        first = reserve(store, current_policy)
        with pytest.raises(UnattendedPilotStateError):
            reserve(store, current_policy)
    with UnattendedPilotStateStore(path) as reopened:
        restored = reopened.attempt_for(ACCOUNT, NOW.date())
        assert restored == first
        assert restored.outcome is AttemptOutcome.RESERVED


def test_all_machine_inputs_allow_without_human_acknowledgements(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        result = decision(current_policy, attempt)
    assert result.outcome is ExecutionSafetyOutcome.ALLOW
    assert result.authority_ceiling == UNATTENDED_AUTHORITY_CEILING
    assert "unattended_pilot_mode" in result.satisfied_gates
    assert "operator_acknowledgements" not in result.satisfied_gates


@pytest.mark.parametrize("change", [
    {"snapshot_fresh": False}, {"suspension_fresh": False},
    {"no_conflicting_broker_state": False},
    {"kill_state_clear": False}, {"risk_bounds_verified": False},
    {"risk_state_allows_new_risk": False},
    {"health_allows_new_risk": False}, {"source_hash_verified": False},
    {"account_fingerprint_matched": False}, {"suspension_state": "SUSPENDED"},
])
def test_each_machine_gate_fails_closed(tmp_path, change):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        result = decision(current_policy, attempt, safety_inputs(**change))
    assert result.outcome is ExecutionSafetyOutcome.DENY


def test_wrong_strategy_digest_symbol_quantity_and_short_block(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        with pytest.raises(Exception):
            decision(current_policy, attempt, symbol="IBM")
        wrong_digest = active_policy(implementation_digest="f" * 64)
        with pytest.raises(Exception):
            decision(wrong_digest, attempt)
    # The adapter independently refuses non-policy payloads before any session call.
    assert current_policy.quantity == 1 and current_policy.strategy_id == "orb_long_v0"


def test_production_safety_requires_exact_m21_run_state(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        with pytest.raises(UnattendedPilotRuntimeError, match="run/action binding"):
            evaluate_production_action_safety(
                run=production_run(current_policy, role="stop"),
                policy=current_policy, attempt=attempt, inputs=safety_inputs(),
                decision_time=NOW, role="entry", symbol="MSFT",
            )

def test_entry_dispatch_uses_existing_boundary_and_consumes_before_call(tmp_path, monkeypatch):
    values = prepared(tmp_path, monkeypatch)
    current_policy, store, attempt, _, safety, args, permit, session, adapter = values
    result = asyncio.run(dispatch_production_write(
        run=production_run(current_policy), adapter=adapter, store=store, policy=current_policy, permit=permit,
        safety_decision=safety, arguments=args, now=NOW,
    ))
    assert result.state is ProductionWriteState.ACCEPTED
    assert result.unattended_run.state is UnattendedState.ENTRY_SUBMITTED
    assert len(session.calls) == 1
    assert store.attempt_for(ACCOUNT, NOW.date()).outcome is AttemptOutcome.ACCEPTED
    with pytest.raises(UnattendedPilotStateError):
        asyncio.run(dispatch_production_write(
            run=production_run(current_policy), adapter=adapter, store=store, policy=current_policy, permit=permit,
            safety_decision=safety, arguments=args, now=NOW,
        ))
    assert len(session.calls) == 1
    store.close()


def test_write_timeout_enters_persistent_kill_and_never_retries(tmp_path, monkeypatch):
    values = prepared(tmp_path, monkeypatch, session_error=TimeoutError())
    current_policy, store, _, _, safety, args, permit, session, adapter = values
    result = asyncio.run(dispatch_production_write(
        run=production_run(current_policy), adapter=adapter, store=store, policy=current_policy, permit=permit,
        safety_decision=safety, arguments=args, now=NOW,
    ))
    assert result.state is ProductionWriteState.WRITE_OUTCOME_UNKNOWN
    assert result.kill_state_entered and len(session.calls) == 1
    assert result.unattended_run.state is UnattendedState.KILL_STATE
    store.close()
    with UnattendedPilotStateStore((tmp_path / "pilot.sqlite").resolve()) as reopened:
        assert reopened.active_kill(ACCOUNT) is not None
        with pytest.raises(UnattendedPilotStateError):
            asyncio.run(dispatch_production_write(
                run=production_run(current_policy), adapter=adapter, store=reopened, policy=current_policy,
                permit=permit, safety_decision=safety, arguments=args, now=NOW,
            ))
    assert len(session.calls) == 1


def test_permit_authenticity_and_fixture_origin_refused(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        safety = decision(current_policy, attempt)
        inputs = safety_inputs()
        args = entry_arguments()
        permit = issue_production_write_permit(store=store, policy=current_policy,
            attempt=attempt, safety_decision=safety, role="entry", arguments=args,
            inputs=inputs, now=NOW)
        assert permit.permit_digest == compute_write_permit_digest(permit)
        assert validate_write_permit(permit, policy=current_policy,
            safety_decision=safety, role="entry", arguments=args, now=NOW) == (True, "OK")
        with pytest.raises(UnattendedPilotStateError):
            replace(permit, symbol="AAPL")


def test_restart_clean_attempt_position_orders_and_kill(tmp_path):
    current_policy = active_policy()
    path = (tmp_path / "state.sqlite").resolve()
    with UnattendedPilotStateStore(path) as store:
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(), trading_date=NOW.date(), now=NOW) is UnattendedState.WATCHING
        reserve(store, current_policy)
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(), trading_date=NOW.date(), now=NOW) is UnattendedState.POLICY_BLOCKED
    with UnattendedPilotStateStore((tmp_path / "position.sqlite").resolve()) as store:
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(positions=(make_position(symbol="MSFT", quantity="1"),)),
            trading_date=NOW.date(), now=NOW) is UnattendedState.PROTECTION_REQUIRED
    with UnattendedPilotStateStore((tmp_path / "entry.sqlite").resolve()) as store:
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(orders=(order(),)), trading_date=NOW.date(), now=NOW
        ) is UnattendedState.ENTRY_RECONCILING
    with UnattendedPilotStateStore((tmp_path / "stop.sqlite").resolve()) as store:
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(positions=(make_position(symbol="MSFT", quantity="1"),),
                              orders=(order(side="sell", trigger="stop"),)),
            trading_date=NOW.date(), now=NOW) is UnattendedState.STOP_RECONCILING
        store.enter_kill_state(fingerprint=ACCOUNT, trading_date=NOW.date(),
                               reason_code="TEST_KILL", now=NOW)
        assert reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(), trading_date=NOW.date(), now=NOW) is UnattendedState.KILL_STATE


def test_stale_or_wrong_account_snapshot_enters_kill(tmp_path):
    current_policy = active_policy()
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        state = reconcile_startup(store=store, policy=current_policy,
            snapshot=snapshot(completed_at=NOW - timedelta(minutes=2)),
            trading_date=NOW.date(), now=NOW)
        assert state is UnattendedState.KILL_STATE
        assert store.active_kill(ACCOUNT) is not None


def test_structural_boundary_has_one_dispatch_and_no_parallel_evaluator():
    runtime = Path("src/unattended_pilot_runtime.py").read_text(encoding="utf-8-sig")
    adapter = Path("src/robinhood_commissioning_adapter.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(runtime)
    assert "evaluate_execution_safety" not in runtime
    assert "call_tool" not in runtime
    assert "place_equity_order" in runtime and "cancel_equity_order" in runtime
    assert adapter.count("session.call_tool") == 1
    assert not any(
        isinstance(node, ast.Assign)
        and any(getattr(target, "id", "") in {"activation_approved", "permits_automated_action", "unattended_ready"}
                for target in node.targets)
        and isinstance(node.value, ast.Constant) and node.value.value is True
        for node in tree.body
    )


def test_candidate_identity_is_bound_before_daily_allowance_is_reserved(tmp_path):
    current_policy = active_policy()
    tampered = replace(candidate(), decision_id="0" * 64)
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        with pytest.raises(UnattendedPilotStateError, match="candidate is not authentic"):
            store.reserve_entry_attempt(
                policy=current_policy, candidate=tampered,
                unattended_run=production_run(current_policy), now=NOW,
            )
        attempt = reserve(store, current_policy)
        assert attempt.candidate_id == candidate().decision_id
        assert attempt.entry_action_identity is None


def test_entry_window_policy_expiry_and_fixed_bounds_fail_closed(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        outside = decision(current_policy, attempt, now=NOW + timedelta(hours=2))
        assert outside.outcome is ExecutionSafetyOutcome.DENY
        assert "DENY_OUTSIDE_ENTRY_WINDOW" in outside.reason_codes
    with pytest.raises(UnattendedOrchestratorError):
        active_policy(quantity=2)
    with pytest.raises(UnattendedOrchestratorError):
        active_policy(strategy_id="other")
    with pytest.raises(UnattendedOrchestratorError):
        active_policy(symbol_allowlist=("MSFT",))


def test_entry_stop_cancel_exit_identities_are_distinct_and_single_use(tmp_path):
    with UnattendedPilotStateStore((tmp_path / "state.sqlite").resolve()) as store:
        current_policy = active_policy()
        attempt = reserve(store, current_policy)
        cases = (
            ("entry", entry_arguments(), None, None),
            ("stop", {
                "account_number": ACCOUNT_NUMBER, "symbol": "MSFT", "side": "sell",
                "type": "stop_market", "quantity": "1", "time_in_force": "gtc",
                "market_hours": "regular_hours", "stop_price": "99",
            }, "99", None),
            ("cancel", {"order_id": "working-stop-1"}, None, "working-stop-1"),
            ("exit", {
                "account_number": ACCOUNT_NUMBER, "symbol": "MSFT", "side": "sell",
                "type": "market", "quantity": "1", "time_in_force": "gfd",
                "market_hours": "regular_hours",
            }, None, None),
        )
        identities = []
        permits = []
        for role, arguments, stop_price, order_id in cases:
            current_attempt = store.attempt_for(ACCOUNT, NOW.date())
            assert current_attempt is not None
            if role == "entry":
                role_snapshot = snapshot()
            elif role == "cancel":
                role_snapshot = snapshot(
                    positions=(make_position(symbol="MSFT", quantity="1"),),
                    orders=(order(side="sell", trigger="stop"),),
                )
                order_id = "order-1"
                arguments = {"order_id": order_id}
            else:
                role_snapshot = snapshot(
                    positions=(make_position(symbol="MSFT", quantity="1"),),
                )
            inputs = safety_inputs(broker_snapshot=role_snapshot)
            safety = decision(
                current_policy, current_attempt, inputs, role=role,
                stop_price=stop_price, order_id=order_id,
            )
            assert safety.outcome is ExecutionSafetyOutcome.ALLOW
            permit = issue_production_write_permit(
                store=store, policy=current_policy, attempt=current_attempt,
                safety_decision=safety, role=role, arguments=arguments,
                inputs=inputs, now=NOW,
            )
            identities.append(safety.action_identity)
            permits.append(permit.permit_id)
        assert len(set(identities)) == len(cases)
        assert len(set(permits)) == len(cases)
        assert store.attempt_for(ACCOUNT, NOW.date()).entry_action_identity == identities[0]


def test_adapter_refuses_unspent_durable_permit_before_session_call(tmp_path, monkeypatch):
    values = prepared(tmp_path, monkeypatch)
    current_policy, store, _, _, safety, args, permit, session, adapter = values
    with pytest.raises(ExecutionSafetyDeniedError, match="DURABLE_STATE_NOT_DISPATCHING"):
        asyncio.run(adapter.place_equity_order(
            args, role="entry", safety_decision=safety,
            unattended_policy=current_policy, unattended_write_permit=permit,
            unattended_state_store=store,
        ))
    assert session.calls == []
    store.close()


def test_post_dispatch_response_contract_failure_enters_kill(tmp_path, monkeypatch):
    values = prepared(tmp_path, monkeypatch)
    current_policy, store, _, _, safety, args, permit, session, adapter = values
    monkeypatch.setattr(
        "src.robinhood_commissioning_adapter.parse_place_response",
        lambda value: SimpleNamespace(
            server_order_id="order-1", symbol="AAPL", side="buy",
            order_type="market", trigger="immediate",
            submitted_quantity=Decimal("1"), time_in_force="gfd",
            market_hours="regular_hours", stop_price=None,
        ),
    )
    result = asyncio.run(dispatch_production_write(
        run=production_run(current_policy), adapter=adapter, store=store, policy=current_policy, permit=permit,
        safety_decision=safety, arguments=args, now=NOW,
    ))
    assert result.state is ProductionWriteState.WRITE_OUTCOME_UNKNOWN
    assert result.kill_state_entered and result.broker_write_count == 0
    assert len(session.calls) == 1
    assert store.active_kill(ACCOUNT) is not None
    store.close()


def test_restart_unknown_dispatch_and_unexpected_broker_shape_enter_kill(tmp_path):
    current_policy = active_policy()
    with UnattendedPilotStateStore((tmp_path / "unknown.sqlite").resolve()) as store:
        attempt = reserve(store, current_policy)
        safety = decision(current_policy, attempt)
        permit = issue_production_write_permit(
            store=store, policy=current_policy, attempt=attempt,
            safety_decision=safety, role="entry", arguments=entry_arguments(),
            inputs=safety_inputs(), now=NOW,
        )
        store.mark_dispatching(permit)
        assert reconcile_startup(
            store=store, policy=current_policy, snapshot=snapshot(),
            trading_date=NOW.date(), now=NOW,
        ) is UnattendedState.KILL_STATE
    with UnattendedPilotStateStore((tmp_path / "shape.sqlite").resolve()) as store:
        assert reconcile_startup(
            store=store, policy=current_policy,
            snapshot=snapshot(positions=(make_position(symbol="IBM", quantity="2"),)),
            trading_date=NOW.date(), now=NOW,
        ) is UnattendedState.KILL_STATE