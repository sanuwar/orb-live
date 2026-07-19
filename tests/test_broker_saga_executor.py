from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.broker_action_saga import (
    BrokerActionSagaError,
    BrokerActionSagaStore,
    SagaActionStatus,
    SagaActionType,
)
from src.broker_saga_executor import (
    ExecutorLimits,
    SagaExecutor,
    SagaExecutorError,
    derive_observation,
)
from src.entry_lifecycle import (
    EntryLifecycleAction,
)
from src.simulated_broker_transport import (
    CrashInjector,
    CrashPoint,
    FakeBrokerState,
    FakeTransportError,
    SimulatedBrokerTransport,
    SimulatedCrash,
    TransportBehavior,
)
from src.trade_state import (
    ExitIntentType,
    TradeStateStore,
    TradeStatus,
)
from src.account_state import (
    AccountInfo,
    AccountState,
    PortfolioState,
)
from tests.snapshot_helpers import (
    apply_broker_snapshot,
    confirm_linked_stop,
)
from tests.test_broker_action_saga import (
    ACCOUNT_NUMBER,
    FINGERPRINT,
    NOW,
    PLAN_DIGEST,
    RECONCILIATION_ID,
    SNAPSHOT_DIGEST,
    STRATEGY,
    make_authorization,
    make_stores,
    plan_stop_action,
    ready_stop_action,
    stop_payload,
)


OWNER = "sim-exec-1"


def make_env(
    tmp_path,
    *,
    limits: ExecutorLimits | None = None,
    crash_points=(),
    name="state.sqlite",
):
    store, saga, trade = make_stores(
        tmp_path, name=name
    )
    authorization = make_authorization(
        store, trade
    )
    transport = SimulatedBrokerTransport(
        FakeBrokerState(
            account_number=ACCOUNT_NUMBER
        )
    )
    injector = (
        CrashInjector(crash_points)
        if crash_points
        else None
    )
    executor = SagaExecutor(
        saga,
        transport,
        owner=OWNER,
        limits=limits,
        crash_injector=injector,
    )
    return SimpleNamespace(
        store=store,
        saga=saga,
        trade=trade,
        authorization=authorization,
        transport=transport,
        executor=executor,
        injector=injector,
    )


def restart_executor(env, **kwargs):
    return SagaExecutor(
        env.saga,
        env.transport,
        owner=OWNER,
        **kwargs,
    )


def ready_action(env, **overrides):
    if overrides:
        action, _ = plan_stop_action(
            env.saga,
            env.trade,
            env.authorization,
            **overrides,
        )
        return env.saga.evaluate_readiness(
            action.action_id, occurred_at=NOW
        )
    return ready_stop_action(
        env.saga, env.trade, env.authorization
    )


def plan_cancel_stop_action(
    env,
    *,
    source_order_id: str,
    key_suffix="1",
):
    action, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=(
            SagaActionType.CANCEL_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"cancel-stop:{env.trade.trade_id}:"
            f"{key_suffix}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop_market",
            "quantity": "10",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "source_order_inactive": True,
        },
        authorization_id=(
            env.authorization.authorization_id
        ),
        source_broker_order_id=source_order_id,
        occurred_at=NOW,
    )
    return env.saga.evaluate_readiness(
        action.action_id, occurred_at=NOW
    )


def make_entry_env(tmp_path):
    store = TradeStateStore(
        tmp_path / "entry.sqlite"
    )
    saga = BrokerActionSagaStore(store)
    trade = store.reserve_entry(
        strategy=STRATEGY,
        symbol="NVDA",
        session_date=date(2026, 7, 18),
        quantity=10,
        planned_entry=Decimal("500"),
        initial_stop=Decimal("495"),
        target_price=Decimal("510"),
        occurred_at=NOW - timedelta(minutes=3),
    )
    authorization, _ = (
        store.authorize_broker_action_plan(
            strategy=STRATEGY,
            trade_id=trade.trade_id,
            purpose="entry",
            plan_digest=PLAN_DIGEST,
            source_trade_version=trade.version,
            reconciliation_id=RECONCILIATION_ID,
            snapshot_digest=SNAPSHOT_DIGEST,
            account_fingerprint=FINGERPRINT,
            authorized_at=NOW,
            expires_at=NOW + timedelta(seconds=15),
        )
    )
    transport = SimulatedBrokerTransport(
        FakeBrokerState(
            account_number=ACCOUNT_NUMBER
        )
    )
    executor = SagaExecutor(
        saga, transport, owner=OWNER
    )
    entry_action, _ = saga.plan_action(
        strategy=STRATEGY,
        trade_id=trade.trade_id,
        action_type=SagaActionType.PLACE_ENTRY,
        logical_action_key=(
            f"entry:{trade.trade_id}"
        ),
        payload={
            "symbol": "NVDA",
            "side": "buy",
            "type": "limit",
            "quantity": "10",
            "limit_price": "500",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "position_quantity": "10",
        },
        authorization_id=(
            authorization.authorization_id
        ),
        requested_quantity=10,
        limit_price=Decimal("500"),
        occurred_at=NOW,
    )
    entry_action = saga.evaluate_readiness(
        entry_action.action_id, occurred_at=NOW
    )
    return SimpleNamespace(
        store=store,
        saga=saga,
        trade=trade,
        authorization=authorization,
        transport=transport,
        executor=executor,
        entry_action=entry_action,
    )


def executed_stop(env, *, now=NOW):
    action = ready_action(env)
    result = env.executor.execute_ready_action(
        action.action_id, now=now
    )
    return result


# ---------------------------------------------------------------
# Executor consumption rules
# ---------------------------------------------------------------


def test_executor_claims_only_ready_actions(tmp_path):
    env = make_env(tmp_path)
    planned, _ = plan_stop_action(
        env.saga, env.trade, env.authorization
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="Only READY",
    ):
        env.executor.execute_ready_action(
            planned.action_id, now=NOW
        )
    assert env.transport.submit_calls == 0


def test_executor_cannot_consume_blocked_actions(tmp_path):
    env = make_env(tmp_path)
    action, _ = plan_stop_action(
        env.saga,
        env.trade,
        env.authorization,
        expected_precondition={
            "unprotected_quantity": "10",
        },
    )
    blocked = env.saga.evaluate_readiness(
        action.action_id,
        occurred_at=NOW,
        precondition_observation={
            "unprotected_quantity": "3",
        },
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="Only READY",
    ):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )
    assert env.transport.submit_calls == 0


def test_executor_cannot_consume_unknown_actions(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.TIMEOUT_BEFORE_RESPONSE,
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )

    with pytest.raises(BrokerActionSagaError):
        env.executor.execute_ready_action(
            action.action_id,
            now=NOW + timedelta(seconds=5),
        )
    assert env.transport.submit_calls == 1


def test_executor_cannot_alter_payload_or_ref_id(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    orders = list(
        env.transport.state.orders.values()
    )
    assert len(orders) == 1
    order = orders[0]
    assert order.ref_id == action.ref_id
    assert order.symbol == "AAPL"
    assert order.quantity == Decimal("10")
    assert order.stop_price == Decimal("99")
    assert env.saga.verify_action_integrity(
        action.action_id, occurred_at=NOW
    )
    attempts = env.saga.get_attempts(
        action.action_id
    )
    assert attempts[-1].ref_id == action.ref_id
    assert (
        attempts[-1].payload_digest
        == action.payload_digest
    )


def test_intent_and_attempt_precede_fake_invocation(tmp_path):
    env = make_env(
        tmp_path,
        crash_points=(
            CrashPoint.BEFORE_TRANSPORT_INVOCATION,
        ),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )

    persisted = env.saga.get_action(
        action.action_id
    )
    assert (
        persisted.status
        == SagaActionStatus
        .SUBMISSION_INTENT_RECORDED
    )
    assert (
        persisted.submission_intent_at is not None
    )
    attempts = env.saga.get_attempts(
        action.action_id
    )
    assert len(attempts) == 1
    assert attempts[0].finalized is False
    assert env.transport.submit_calls == 0


# ---------------------------------------------------------------
# Crash and restart safety
# ---------------------------------------------------------------


def test_crash_after_claim_is_restart_safe(tmp_path):
    env = make_env(
        tmp_path,
        crash_points=(CrashPoint.AFTER_CLAIM,),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.CLAIMED
    )

    restarted = restart_executor(env)
    recovered = restarted.recover_claim(
        action.action_id,
        now=NOW + timedelta(seconds=61),
    )
    assert (
        recovered.status == SagaActionStatus.READY
    )
    result = restarted.execute_ready_action(
        action.action_id,
        now=NOW + timedelta(seconds=62),
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert env.transport.submit_calls == 1


def test_crash_after_attempt_creation_is_restart_safe(
    tmp_path,
):
    env = make_env(
        tmp_path,
        crash_points=(
            CrashPoint.AFTER_ATTEMPT_CREATION,
        ),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )
    assert env.transport.submit_calls == 0

    restarted = restart_executor(env)
    recovered = restarted.recover_claim(
        action.action_id,
        now=NOW + timedelta(seconds=61),
    )
    assert (
        recovered.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )


def test_crash_after_submission_intent_does_not_resubmit(
    tmp_path,
):
    env = make_env(
        tmp_path,
        crash_points=(
            CrashPoint.AFTER_SUBMISSION_INTENT,
        ),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )

    restarted = restart_executor(env)
    restarted.recover_claim(
        action.action_id,
        now=NOW + timedelta(seconds=61),
    )
    reconciled = restarted.reconcile_unknown(
        action.action_id,
        now=NOW + timedelta(seconds=62),
    )
    assert (
        reconciled.status
        == SagaActionStatus.CONFIRMED_NOT_PERFORMED
    )
    assert env.transport.submit_calls == 0

    env.saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=NOW + timedelta(seconds=63),
    )
    retried = restarted.maybe_retry(
        action.action_id,
        now=NOW + timedelta(seconds=64),
    )
    assert (
        retried.status == SagaActionStatus.READY
    )
    result = restarted.execute_ready_action(
        action.action_id,
        now=NOW + timedelta(seconds=65),
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert env.transport.submit_calls == 1
    attempts = env.saga.get_attempts(
        action.action_id
    )
    assert [
        attempt.attempt_number
        for attempt in attempts
    ] == [1, 2]
    assert attempts[0].finalized is True
    assert attempts[1].finalized is True
    assert attempts[1].ref_id == action.ref_id


def test_crash_after_response_before_status_is_safe(
    tmp_path,
):
    env = make_env(
        tmp_path,
        crash_points=(
            CrashPoint.AFTER_RESPONSE_BEFORE_STATUS,
        ),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )
    assert env.transport.submit_calls == 1

    restarted = restart_executor(env)
    recovered = restarted.recover_claim(
        action.action_id,
        now=NOW + timedelta(seconds=61),
    )
    assert (
        recovered.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )

    reconciled = restarted.reconcile_unknown(
        action.action_id,
        now=NOW + timedelta(seconds=62),
    )
    assert (
        reconciled.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    confirmed = restarted.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=63),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )
    assert env.transport.submit_calls == 1


def test_fake_side_effect_before_timeout_becomes_unknown(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.TIMEOUT_AFTER_EFFECT,
    )

    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )
    assert len(env.transport.state.orders) == 1

    reopened = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    assert reopened.is_broker_action_halted(
        STRATEGY
    )


def test_broker_proof_resolves_unknown_as_performed(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.TIMEOUT_AFTER_EFFECT,
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    reconciled = env.executor.reconcile_unknown(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        reconciled.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    confirmed = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=3),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )
    assert env.transport.submit_calls == 1


def test_proof_of_no_effect_permits_controlled_retry(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.TIMEOUT_BEFORE_RESPONSE,
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    reconciled = env.executor.reconcile_unknown(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        reconciled.status
        == SagaActionStatus.CONFIRMED_NOT_PERFORMED
    )

    env.saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=NOW + timedelta(seconds=3),
    )
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.ACKNOWLEDGED,
    )
    retried = env.executor.maybe_retry(
        action.action_id,
        now=NOW + timedelta(seconds=4),
    )
    assert (
        retried.status == SagaActionStatus.READY
    )
    result = env.executor.execute_ready_action(
        action.action_id,
        now=NOW + timedelta(seconds=5),
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert result.ref_id == action.ref_id


def test_ambiguous_evidence_prevents_retry(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.TIMEOUT_BEFORE_RESPONSE,
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    env.transport.script_lookup(
        action.ref_id, "ambiguous"
    )

    reconciled = env.executor.reconcile_unknown(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        reconciled.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )
    with pytest.raises(BrokerActionSagaError):
        env.executor.maybe_retry(
            action.action_id,
            now=NOW + timedelta(seconds=3),
        )


# ---------------------------------------------------------------
# Confirmation proof through the executor
# ---------------------------------------------------------------


def test_local_fake_acknowledgment_cannot_confirm(tmp_path):
    env = make_env(tmp_path)
    result = executed_stop(env)

    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert result.confirmed_at is None


def test_fake_read_only_proof_is_required(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.ACKNOWLEDGED_HIDDEN,
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    pending = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        pending.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )

    env.transport.state.reveal_order(
        result.resulting_broker_order_id
    )
    confirmed = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )


def test_delayed_visibility_polling_is_bounded(tmp_path):
    env = make_env(
        tmp_path,
        limits=ExecutorLimits(
            max_confirmation_polls=2
        ),
    )
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.ACKNOWLEDGED_HIDDEN,
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    result = env.executor.poll_until_confirmed(
        action.action_id,
        start=NOW + timedelta(seconds=1),
    )
    assert (
        result.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )


def test_stale_proof_cannot_confirm(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    executed_result = (
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )
    )
    assert executed_result.confirmed_at is None

    env.transport.force_next_snapshot(
        age=timedelta(minutes=5)
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="older than the permitted",
    ):
        env.executor.try_confirm(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


def test_incomplete_proof_cannot_confirm(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    env.transport.force_next_snapshot(
        orders_complete=False
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="incomplete",
    ):
        env.executor.try_confirm(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


def test_account_mismatch_cannot_confirm(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    from src.broker_snapshot import (
        account_fingerprint,
    )

    env.transport.force_next_snapshot(
        account_fingerprint_value=(
            account_fingerprint("OTHER-ACCOUNT")
        )
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="does not match its evidence",
    ):
        env.executor.try_confirm(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


def test_payload_mismatch_cannot_confirm(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    order = env.transport.state.orders[
        result.resulting_broker_order_id
    ]
    order.quantity = Decimal("7")
    pending = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        pending.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )


def test_conflicting_broker_evidence_cannot_confirm(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=Decimal("105"),
    )
    pending = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        pending.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )


# ---------------------------------------------------------------
# Duplicate ref_id handling
# ---------------------------------------------------------------


def test_duplicate_same_ref_and_payload_is_idempotent(
    tmp_path,
):
    transport = SimulatedBrokerTransport(
        FakeBrokerState()
    )
    payload = stop_payload()
    first = transport.submit_order(
        payload, ref_id="ref-1"
    )
    second = transport.submit_order(
        payload, ref_id="ref-1"
    )

    assert first == second
    assert len(transport.state.orders) == 1
    assert transport.submit_calls == 2


def test_duplicate_ref_with_different_payload_fails_closed(
    tmp_path,
):
    transport = SimulatedBrokerTransport(
        FakeBrokerState()
    )
    transport.submit_order(
        stop_payload(), ref_id="ref-1"
    )
    with pytest.raises(
        FakeTransportError,
        match="different payload",
    ):
        transport.submit_order(
            stop_payload(stop_price="55"),
            ref_id="ref-1",
        )

    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.submit_order(
        stop_payload(stop_price="55"),
        ref_id=action.ref_id,
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.TERMINAL_FAILURE
    )
    assert (
        result.failure_code
        == "transport_fail_closed"
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )
    with pytest.raises(BrokerActionSagaError):
        env.executor.maybe_retry(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


# ---------------------------------------------------------------
# Retry limits and terminal handling
# ---------------------------------------------------------------


def test_bounded_retry_count_is_enforced(tmp_path):
    env = make_env(
        tmp_path,
        limits=ExecutorLimits(max_attempts=1),
    )
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.REJECTED_RETRYABLE,
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.RETRYABLE_FAILURE
    )

    escalated = env.executor.maybe_retry(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        escalated.status
        == SagaActionStatus.OPERATOR_REQUIRED
    )


def test_terminal_failure_is_not_retried(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.REJECTED_TERMINAL,
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.TERMINAL_FAILURE
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="proof of no effect",
    ):
        env.executor.maybe_retry(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


def test_operator_required_action_is_not_retried(tmp_path):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.saga.require_operator(
        action.action_id,
        reason="Escalated for testing.",
        occurred_at=NOW,
    )

    with pytest.raises(
        BrokerActionSagaError,
        match="proof of no effect",
    ):
        env.executor.maybe_retry(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )


def test_retryable_failure_within_budget_can_retry(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.REJECTED_RETRYABLE,
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    env.transport.script_submit(
        action.ref_id,
        TransportBehavior.ACKNOWLEDGED,
    )
    retried = env.executor.maybe_retry(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        retried.status == SagaActionStatus.READY
    )
    result = env.executor.execute_ready_action(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    attempts = env.saga.get_attempts(
        action.action_id
    )
    assert [
        attempt.attempt_number
        for attempt in attempts
    ] == [1, 2]


# ---------------------------------------------------------------
# Claim exclusivity and recovery
# ---------------------------------------------------------------


def test_two_executors_cannot_submit_the_same_action(
    tmp_path,
):
    env = make_env(
        tmp_path,
        crash_points=(CrashPoint.AFTER_CLAIM,),
    )
    action = ready_action(env)

    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )

    second = SagaExecutor(
        env.saga,
        env.transport,
        owner="sim-exec-2",
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="unexpired claim",
    ):
        second.execute_ready_action(
            action.action_id,
            now=NOW + timedelta(seconds=5),
        )
    assert env.transport.submit_calls == 0


def test_crash_during_claim_recovery_is_safe(tmp_path):
    env = make_env(
        tmp_path,
        crash_points=(CrashPoint.AFTER_CLAIM,),
    )
    action = ready_action(env)
    with pytest.raises(SimulatedCrash):
        env.executor.execute_ready_action(
            action.action_id, now=NOW
        )

    recovering = restart_executor(
        env,
        crash_injector=CrashInjector(
            (CrashPoint.DURING_CLAIM_RECOVERY,)
        ),
    )
    with pytest.raises(SimulatedCrash):
        recovering.recover_claim(
            action.action_id,
            now=NOW + timedelta(seconds=61),
        )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.CLAIMED
    )

    recovered = recovering.recover_claim(
        action.action_id,
        now=NOW + timedelta(seconds=62),
    )
    assert (
        recovered.status == SagaActionStatus.READY
    )


def test_crash_while_awaiting_confirmation_is_safe(
    tmp_path,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )

    crashing = restart_executor(
        env,
        crash_injector=CrashInjector(
            (
                CrashPoint
                .WHILE_AWAITING_CONFIRMATION,
            )
        ),
    )
    with pytest.raises(SimulatedCrash):
        crashing.try_confirm(
            action.action_id,
            now=NOW + timedelta(seconds=1),
        )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )

    confirmed = crashing.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )


# ---------------------------------------------------------------
# Dependencies through the executor
# ---------------------------------------------------------------


def make_cancel_then_stop_pair(env):
    entry_order = env.transport.state.add_order(
        symbol="AAPL",
        side="buy",
        order_type="limit",
        quantity=Decimal("10"),
        limit_price=Decimal("100"),
        order_id="fake-entry-remainder",
    )
    upstream, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=(
            SagaActionType.CANCEL_ENTRY_REMAINDER
        ),
        logical_action_key=(
            f"cancel-remainder:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "buy",
            "type": "limit",
            "quantity": "4",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "source_order_inactive": True,
        },
        authorization_id=(
            env.authorization.authorization_id
        ),
        source_broker_order_id=(
            entry_order.order_id
        ),
        occurred_at=NOW,
    )
    downstream, _ = plan_stop_action(
        env.saga,
        env.trade,
        env.authorization,
        depends_on=(upstream.action_id,),
    )
    upstream = env.saga.evaluate_readiness(
        upstream.action_id, occurred_at=NOW
    )
    downstream = env.saga.evaluate_readiness(
        downstream.action_id, occurred_at=NOW
    )
    return upstream, downstream


def test_dependency_remains_blocked_until_proof(tmp_path):
    env = make_env(tmp_path)
    upstream, downstream = (
        make_cancel_then_stop_pair(env)
    )

    blocked = env.saga.evaluate_readiness(
        downstream.action_id, occurred_at=NOW
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )

    result = env.executor.execute_ready_action(
        upstream.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    still_blocked = env.saga.evaluate_readiness(
        downstream.action_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert (
        still_blocked.status
        == SagaActionStatus.BLOCKED
    )


def test_dependency_unlock_occurs_once(tmp_path):
    env = make_env(tmp_path)
    upstream, downstream = (
        make_cancel_then_stop_pair(env)
    )
    env.executor.execute_ready_action(
        upstream.action_id, now=NOW
    )
    confirmed = env.executor.try_confirm(
        upstream.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )

    unlocked = env.saga.get_action(
        downstream.action_id
    )
    assert (
        unlocked.status == SagaActionStatus.READY
    )
    version_after_unlock = unlocked.version

    env.executor.unlock_dependents(
        upstream.action_id,
        now=NOW + timedelta(seconds=2),
    )
    restarted = restart_executor(env)
    restarted.unlock_dependents(
        upstream.action_id,
        now=NOW + timedelta(seconds=3),
    )
    final = env.saga.get_action(
        downstream.action_id
    )
    assert final.version == version_after_unlock
    assert (
        final.status == SagaActionStatus.READY
    )


def test_crash_after_proof_before_unlock_is_safe(tmp_path):
    env = make_env(
        tmp_path,
        crash_points=(
            CrashPoint.AFTER_PROOF_BEFORE_UNLOCK,
        ),
    )
    upstream, downstream = (
        make_cancel_then_stop_pair(env)
    )
    env.executor.execute_ready_action(
        upstream.action_id, now=NOW
    )

    with pytest.raises(SimulatedCrash):
        env.executor.try_confirm(
            upstream.action_id,
            now=NOW + timedelta(seconds=1),
        )
    assert (
        env.saga.get_action(
            upstream.action_id
        ).status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )
    assert (
        env.saga.get_action(
            downstream.action_id
        ).status
        == SagaActionStatus.BLOCKED
    )

    restarted = restart_executor(env)
    restarted.unlock_dependents(
        upstream.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        env.saga.get_action(
            downstream.action_id
        ).status
        == SagaActionStatus.READY
    )


# ---------------------------------------------------------------
# Cancellation semantics
# ---------------------------------------------------------------


def test_cancellation_ack_requires_observation_proof(
    tmp_path,
):
    env = make_env(tmp_path)
    stop_order = env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("10"),
        stop_price=Decimal("99"),
        order_id="fake-linked-stop",
    )
    action = plan_cancel_stop_action(
        env, source_order_id=stop_order.order_id
    )

    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert result.confirmed_at is None

    confirmed = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )


def test_stop_cancellation_does_not_prove_position_safety(
    tmp_path,
):
    env = make_env(tmp_path)
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    stop_order = env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("10"),
        stop_price=Decimal("99"),
        order_id="fake-linked-stop",
    )
    action = plan_cancel_stop_action(
        env, source_order_id=stop_order.order_id
    )
    env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    confirmed = env.executor.try_confirm(
        action.action_id,
        now=NOW + timedelta(seconds=1),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )

    snapshot = env.transport.snapshot(
        now=NOW + timedelta(seconds=2)
    )
    observation = derive_observation(
        confirmed, snapshot
    )
    assert (
        observation["active_stop_present"]
        is False
    )
    assert (
        observation["position_quantity"] == "10"
    )


def test_stop_replacement_sequence_fails_closed_on_gap(
    tmp_path,
):
    env = make_env(tmp_path)
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    stop_order = env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("10"),
        stop_price=Decimal("99"),
        order_id="fake-old-stop",
    )
    cancel_action = plan_cancel_stop_action(
        env, source_order_id=stop_order.order_id
    )
    replacement, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=(
            SagaActionType
            .PLACE_REPLACEMENT_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"replace-stop:{env.trade.trade_id}"
        ),
        payload=stop_payload(stop_price="100"),
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "active_stop_present": True,
            "stop_quantity": "10",
        },
        authorization_id=(
            env.authorization.authorization_id
        ),
        depends_on=(cancel_action.action_id,),
        occurred_at=NOW,
    )

    blocked = env.saga.evaluate_readiness(
        replacement.action_id, occurred_at=NOW
    )
    assert (
        blocked.status == SagaActionStatus.BLOCKED
    )
    with pytest.raises(
        BrokerActionSagaError,
        match="Only READY",
    ):
        env.executor.execute_ready_action(
            replacement.action_id, now=NOW
        )

    env.executor.execute_ready_action(
        cancel_action.action_id, now=NOW
    )
    env.executor.try_confirm(
        cancel_action.action_id,
        now=NOW + timedelta(seconds=1),
    )

    gap_snapshot = env.transport.snapshot(
        now=NOW + timedelta(seconds=2)
    )
    gap = derive_observation(
        env.saga.get_action(
            replacement.action_id
        ),
        gap_snapshot,
    )
    assert gap["active_stop_present"] is False
    assert gap["position_quantity"] == "10"

    unlocked = env.saga.get_action(
        replacement.action_id
    )
    assert (
        unlocked.status == SagaActionStatus.READY
    )


# ---------------------------------------------------------------
# Lifecycle integration
# ---------------------------------------------------------------


def test_complete_entry_to_protection_sequence(tmp_path):
    env = make_entry_env(tmp_path)
    result = env.executor.execute_ready_action(
        env.entry_action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    order_id = result.resulting_broker_order_id

    env.store.attach_entry_order(
        env.trade.trade_id,
        broker_order_id=order_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    env.transport.state.fill_order(
        order_id, Decimal("10")
    )

    confirmed = env.executor.try_confirm(
        env.entry_action.action_id,
        now=NOW + timedelta(seconds=2),
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )

    lifecycle = (
        env.executor.route_lifecycle_observation(
            env.entry_action.action_id,
            order_id=order_id,
            now=NOW + timedelta(seconds=3),
            average_fill_price=Decimal("500"),
        )
    )
    assert (
        lifecycle.decision.action
        == EntryLifecycleAction.MARK_FULL_FILL
    )
    assert (
        lifecycle.trade_after.status
        == TradeStatus.PROTECTION_PENDING
    )
    assert (
        lifecycle.trade_after.filled_quantity
        == 10
    )


def test_partial_entry_fill_routes_to_recovery(tmp_path):
    env = make_entry_env(tmp_path)
    result = env.executor.execute_ready_action(
        env.entry_action.action_id, now=NOW
    )
    order_id = result.resulting_broker_order_id

    env.store.attach_entry_order(
        env.trade.trade_id,
        broker_order_id=order_id,
        occurred_at=NOW + timedelta(seconds=1),
    )
    env.transport.state.fill_order(
        order_id, Decimal("6")
    )
    env.transport.state.cancel_order_state(
        order_id
    )

    lifecycle = (
        env.executor.route_lifecycle_observation(
            env.entry_action.action_id,
            order_id=order_id,
            now=NOW + timedelta(seconds=2),
            average_fill_price=Decimal("500"),
        )
    )
    assert (
        lifecycle.decision.action
        == EntryLifecycleAction
        .PROTECT_PARTIAL_FILL
    )
    assert not lifecycle.decision.fail_closed
    assert lifecycle.trade_after.status in {
        TradeStatus.ENTRY_PARTIALLY_FILLED,
        TradeStatus.RECOVERY_REQUIRED,
    }
    assert (
        lifecycle.trade_after.filled_quantity == 6
    )


def test_protective_stop_partial_fill_recovery_routes(
    tmp_path,
):
    env = make_env(tmp_path)
    trade = env.store.attach_protective_stop(
        env.trade.trade_id,
        broker_order_id="fake-linked-stop",
        occurred_at=NOW,
    )
    trade = confirm_linked_stop(
        env.store,
        env.store.get_trade(trade.trade_id),
        account_number=ACCOUNT_NUMBER,
    )
    assert trade.status in {
        TradeStatus.STOP_LINKED,
        TradeStatus.OPEN_PROTECTED,
    }

    env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="stop_market",
        quantity=Decimal("10"),
        stop_price=Decimal("99"),
        order_id="fake-linked-stop",
    )
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    env.transport.state.fill_order(
        "fake-linked-stop", Decimal("4")
    )

    stop_action, _ = plan_stop_action(
        env.saga, env.trade, env.authorization
    )
    lifecycle = (
        env.executor.route_lifecycle_observation(
            stop_action.action_id,
            order_id="fake-linked-stop",
            now=NOW + timedelta(seconds=1),
            average_fill_price=Decimal("99"),
        )
    )
    assert lifecycle.recovery_event_recorded
    assert (
        lifecycle.trade_after.status
        == TradeStatus.STOP_RECOVERY_REQUIRED
    )


def exit_env(tmp_path):
    env = make_env(tmp_path)
    exit_order = env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="limit",
        quantity=Decimal("10"),
        limit_price=Decimal("102"),
        order_id="fake-exit-1",
    )
    env.store.begin_exit(
        env.trade.trade_id,
        broker_order_id=exit_order.order_id,
        requested_quantity=10,
        close_reason="profit_target",
        intent_type=ExitIntentType.PROFIT_TARGET,
        intended_price=Decimal("102"),
        occurred_at=NOW,
    )
    exit_action, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=SagaActionType.PLACE_EXIT,
        logical_action_key=(
            f"exit:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "102",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "order_present": True,
        },
        authorization_id=(
            env.authorization.authorization_id
        ),
        close_reason="profit_target",
        occurred_at=NOW,
    )
    return env, exit_action, exit_order


def test_partial_exit_routes_to_exit_recovery(tmp_path):
    env, exit_action, exit_order = exit_env(
        tmp_path
    )
    env.transport.state.fill_order(
        exit_order.order_id, Decimal("4")
    )

    lifecycle = (
        env.executor.route_lifecycle_observation(
            exit_action.action_id,
            order_id=exit_order.order_id,
            now=NOW + timedelta(seconds=1),
            average_fill_price=Decimal("102"),
        )
    )
    assert (
        lifecycle.trade_after.status
        == TradeStatus.EXIT_PARTIALLY_FILLED
    )
    assert (
        lifecycle.trade_after
        .cumulative_exited_quantity
        == 4
    )


def test_full_exit_closes_exactly_once_without_duplicate_pnl(
    tmp_path,
):
    env, exit_action, exit_order = exit_env(
        tmp_path
    )
    env.transport.state.fill_order(
        exit_order.order_id, Decimal("10")
    )

    lifecycle = (
        env.executor.route_lifecycle_observation(
            exit_action.action_id,
            order_id=exit_order.order_id,
            now=NOW + timedelta(seconds=1),
            average_fill_price=Decimal("102"),
        )
    )
    assert (
        lifecycle.trade_after.status
        == TradeStatus.EXIT_RECOVERY_REQUIRED
    )
    assert (
        lifecycle.trade_after
        .cumulative_exited_quantity
        == 10
    )

    flat_state = AccountState(
        account=AccountInfo(
            account_number=ACCOUNT_NUMBER,
            masked_account_number="test-account",
            brokerage_account_type="individual",
            trading_type="cash",
            nickname="Test",
            agentic_allowed=True,
            state="active",
        ),
        portfolio=PortfolioState(
            total_value=Decimal("10000"),
            equity_value=Decimal("0"),
            cash=Decimal("10000"),
            pending_deposits=Decimal("0"),
            buying_power=Decimal("10000"),
            unleveraged_buying_power=(
                Decimal("10000")
            ),
            intraday_buying_power=None,
            currency="USD",
        ),
        positions=(),
        open_orders=(),
    )
    apply_broker_snapshot(
        env.store,
        strategy=STRATEGY,
        account_state=flat_state,
        reconciliation_id="flat-proof-sim-1",
    )
    closed = env.store.get_trade(
        env.trade.trade_id
    )
    assert closed.status == TradeStatus.CLOSED
    first_pnl = closed.cumulative_realized_pnl
    assert first_pnl == Decimal("20")

    replay = (
        env.executor.route_lifecycle_observation(
            exit_action.action_id,
            order_id=exit_order.order_id,
            now=NOW + timedelta(seconds=2),
            average_fill_price=Decimal("102"),
        )
    )
    assert replay.ledger_changed is False
    assert (
        replay.trade_after
        .cumulative_realized_pnl
        == first_pnl
    )
    assert (
        replay.trade_after.status
        == TradeStatus.CLOSED
    )


# ---------------------------------------------------------------
# Simulated review evidence
# ---------------------------------------------------------------


def test_review_evidence_is_separate_and_payload_bound(
    tmp_path,
):
    transport = SimulatedBrokerTransport(
        FakeBrokerState()
    )
    payload = stop_payload()
    evidence = transport.review_order(
        payload, ref_id="ref-review-1"
    )

    assert evidence.approved is True
    assert transport.review_log == [evidence]
    assert transport.state.orders == {}
    assert "ref-review-1" not in (
        transport._seen_submissions
    )

    changed = transport.review_order(
        stop_payload(stop_price="55"),
        ref_id="ref-review-1",
    )
    assert (
        changed.payload_digest
        != evidence.payload_digest
    )

    transport.script_review(
        "ref-review-2",
        TransportBehavior.REVIEW_REJECTED,
    )
    rejected = transport.review_order(
        payload, ref_id="ref-review-2"
    )
    assert rejected.approved is False


# ---------------------------------------------------------------
# Static and behavioral live-broker guards
# ---------------------------------------------------------------


def module_source(name: str) -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / name
    ).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "module_name",
    (
        "broker_saga_executor.py",
        "simulated_broker_transport.py",
    ),
)
def test_executor_modules_have_no_live_broker_capability(
    module_name,
):
    lowered = module_source(module_name).lower()
    for forbidden in (
        "robinhood",
        "call_tool",
        "review_equity_order",
        "place_equity_order",
        "cancel_equity_order",
        "clientsession",
        "mcp",
        "http",
        "requests",
        "urllib",
        "socket",
        "websocket",
        "aiohttp",
        "random",
        "os.environ",
        "getenv",
        "rh_tokens",
        "open(",
    ):
        assert forbidden not in lowered, forbidden


def test_executor_refuses_non_simulated_transport(tmp_path):
    store, saga, trade = make_stores(tmp_path)

    class UnmarkedTransport:
        pass

    with pytest.raises(
        SagaExecutorError,
        match="simulated_only",
    ):
        SagaExecutor(
            saga,
            UnmarkedTransport(),
            owner=OWNER,
        )


def test_executor_refuses_live_named_transport(tmp_path):
    store, saga, trade = make_stores(tmp_path)

    class DisguisedTransport:
        simulated_only = True

    DisguisedTransport.__module__ = (
        "src.robinhood_client"
    )
    with pytest.raises(
        SagaExecutorError,
        match="refuses any live broker transport",
    ):
        SagaExecutor(
            saga,
            DisguisedTransport(),
            owner=OWNER,
        )


def test_executor_module_exposes_no_robinhood_symbols():
    import src.broker_saga_executor as executor_module
    import src.simulated_broker_transport as transport_module

    for module in (
        executor_module,
        transport_module,
    ):
        for name in dir(module):
            assert (
                "robinhood" not in name.lower()
            )


def test_saga_schema_reinitialization_stays_idempotent(
    tmp_path,
):
    env = make_env(tmp_path)
    ready_action(env)
    actions_before = (
        env.saga.count_saga_actions()
    )

    for _ in range(2):
        reopened = BrokerActionSagaStore(
            TradeStateStore(
                tmp_path / "state.sqlite"
            )
        )
        assert (
            reopened.count_saga_actions()
            == actions_before
        )


def test_repository_data_guard_attempts_stay_zero(
    repository_data_database_guard,
):
    assert (
        repository_data_database_guard.attempts
        == []
    )
