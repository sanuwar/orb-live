from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from src.broker_action_saga import (
    BrokerActionSagaStore,
    SagaActionStatus,
    SagaActionType,
)
from src.broker_saga_orchestrator import (
    OrchestrationCrashPoint,
    OrchestrationError,
    RoutingFactType,
    RoutingStatus,
    SagaOrchestrator,
)
from src.config import load_config
from src.simulated_broker_transport import (
    CrashInjector,
    SimulatedCrash,
)
from src.trade_state import (
    TradeStateStore,
    TradeStatus,
)
from tests.test_broker_action_saga import (
    ACCOUNT_NUMBER,
    FINGERPRINT,
    NOW,
    RECONCILIATION_ID,
    SNAPSHOT_DIGEST,
    STRATEGY,
    plan_stop_action,
)
from tests.test_broker_saga_executor import (
    make_entry_env,
    make_env,
    plan_cancel_stop_action,
    ready_action,
)
from tests.test_entry_recovery import (
    create_legacy_database,
)


ORCH_OWNER = "orchestrator-1"

T1 = NOW + timedelta(seconds=10)
T2 = NOW + timedelta(seconds=20)
T3 = NOW + timedelta(seconds=30)


def make_orchestrator(env, **kwargs):
    return SagaOrchestrator(
        env.saga,
        owner=kwargs.pop("owner", ORCH_OWNER),
        **kwargs,
    )


def awaiting_stop(env, *, now=NOW):
    """Executes the standard stop action to AWAITING."""
    action = ready_action(env)
    result = env.executor.execute_ready_action(
        action.action_id, now=now
    )
    assert (
        result.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    return result


def stop_evidence(env, result, *, now):
    snapshot = env.transport.snapshot(now=now)
    observation = (
        env.transport.state.as_equity_order(
            result.resulting_broker_order_id
        )
    )
    return snapshot, observation


def staged_stop_routings(env, orchestrator, *, now=T1):
    result = awaiting_stop(env)
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    snapshot, observation = stop_evidence(
        env, result, now=now
    )
    routings = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=now,
        order_observation=observation,
        occurred_at=now,
    )
    return result, routings


def routing_by_type(routings, fact_type):
    for record, _created in routings:
        if record.routing_type == fact_type:
            return record
    raise AssertionError(
        f"No routing with fact {fact_type}"
    )


def entry_routing_env(tmp_path):
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
    orchestrator = SagaOrchestrator(
        env.saga, owner=ORCH_OWNER
    )
    return env, orchestrator, result, order_id


def stage_entry(env, orchestrator, result, order_id, *, now, price=None):
    snapshot = env.transport.snapshot(now=now)
    observation = (
        env.transport.state.as_equity_order(
            order_id
        )
    )
    return orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=now,
        order_observation=observation,
        average_fill_price=price,
        occurred_at=now,
    )


def apply_record(
    env,
    orchestrator,
    record,
    *,
    now,
    order_id=None,
):
    orchestrator.claim_routing(
        record.routing_id, now=now
    )
    observation = (
        env.transport.state.as_equity_order(
            order_id
        )
        if order_id
        else None
    )
    snapshot = env.transport.snapshot(now=now)
    return orchestrator.apply_routing(
        record.routing_id,
        now=now,
        order_observation=observation,
        snapshot=snapshot,
        evaluated_at=now,
    )


# ---------------------------------------------------------------
# Routing idempotency and claims
# ---------------------------------------------------------------


def test_identical_routing_planning_is_idempotent(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, first = staged_stop_routings(
        env, orchestrator
    )
    assert all(
        created for _record, created in first
    )
    count_after_first = (
        orchestrator.count_routings()
    )

    snapshot, observation = stop_evidence(
        env, result, now=T2
    )
    second = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    assert all(
        created is False
        for _record, created in second
    )
    assert (
        orchestrator.count_routings()
        == count_after_first
    )
    first_ids = {
        record.routing_id
        for record, _created in first
    }
    second_ids = {
        record.routing_id
        for record, _created in second
    }
    assert first_ids == second_ids


def test_idempotency_key_is_stable_across_restart(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, first = staged_stop_routings(
        env, orchestrator
    )
    keys_before = {
        record.idempotency_key
        for record, _created in first
    }

    reopened_saga = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    reopened = SagaOrchestrator(
        reopened_saga, owner=ORCH_OWNER
    )
    snapshot, observation = stop_evidence(
        env, result, now=T2
    )
    replayed = reopened.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    assert all(
        created is False
        for _record, created in replayed
    )
    assert {
        record.idempotency_key
        for record, _created in replayed
    } == keys_before


def test_changed_proof_creates_distinct_routing(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, first = staged_stop_routings(
        env, orchestrator
    )
    count_before = orchestrator.count_routings()

    env.transport.state.fill_order(
        result.resulting_broker_order_id,
        Decimal("4"),
    )
    snapshot, observation = stop_evidence(
        env, result, now=T2
    )
    changed = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    partial = routing_by_type(
        changed,
        RoutingFactType
        .PROTECTIVE_STOP_PARTIAL_FILL_CONFIRMED,
    )
    assert (
        partial.proven_filled_quantity
        == Decimal("4")
    )
    assert (
        orchestrator.count_routings()
        > count_before
    )


def test_applied_routing_cannot_be_reopened(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, routings = staged_stop_routings(
        env, orchestrator
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    applied, _ = apply_record(
        env, orchestrator, active, now=T1
    )
    assert (
        applied.status == RoutingStatus.APPLIED
    )

    with pytest.raises(
        OrchestrationError,
        match="Only READY",
    ):
        orchestrator.claim_routing(
            active.routing_id, now=T2
        )
    with pytest.raises(
        OrchestrationError,
        match="active claim",
    ):
        orchestrator.apply_routing(
            active.routing_id,
            now=T2,
            snapshot=env.transport.snapshot(
                now=T2
            ),
            evaluated_at=T2,
        )

    snapshot, observation = stop_evidence(
        env, result, now=T2
    )
    restaged = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    replay = routing_by_type(
        restaged,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    assert (
        replay.routing_id == active.routing_id
    )
    assert replay.status == RoutingStatus.APPLIED


def test_simultaneous_routing_claims_produce_one_winner(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    _result, routings = staged_stop_routings(
        env, orchestrator
    )
    record = routings[0][0]

    other = make_orchestrator(
        env, owner="orchestrator-2"
    )
    _, claimed = orchestrator.claim_routing(
        record.routing_id, now=T1
    )
    assert claimed is True
    with pytest.raises(
        OrchestrationError,
        match="unexpired routing claim",
    ):
        other.claim_routing(
            record.routing_id, now=T1
        )


def test_restart_preserves_routing_claims_and_results(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    _result, routings = staged_stop_routings(
        env, orchestrator
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    visible = routing_by_type(
        routings,
        RoutingFactType.ORDER_VISIBLE,
    )
    orchestrator.claim_routing(
        visible.routing_id, now=T1
    )
    apply_record(
        env, orchestrator, active, now=T1
    )

    reopened = SagaOrchestrator(
        BrokerActionSagaStore(
            TradeStateStore(
                tmp_path / "state.sqlite"
            )
        ),
        owner=ORCH_OWNER,
    )
    restored_visible = reopened.get_routing(
        visible.routing_id
    )
    restored_active = reopened.get_routing(
        active.routing_id
    )
    assert (
        restored_visible.status
        == RoutingStatus.CLAIMED
    )
    assert (
        restored_visible.claim_owner
        == ORCH_OWNER
    )
    assert (
        restored_active.status
        == RoutingStatus.APPLIED
    )
    assert (
        restored_active.completed_at is not None
    )


# ---------------------------------------------------------------
# Crash and restart safety
# ---------------------------------------------------------------


def test_crash_before_lifecycle_invocation_is_safe(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(
        env,
        crash_injector=CrashInjector(
            (
                OrchestrationCrashPoint
                .BEFORE_LIFECYCLE_INVOCATION,
            )
        ),
    )
    _result, routings = staged_stop_routings(
        env, orchestrator
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    orchestrator.claim_routing(
        active.routing_id, now=T1
    )
    with pytest.raises(SimulatedCrash):
        orchestrator.apply_routing(
            active.routing_id,
            now=T1,
            snapshot=env.transport.snapshot(
                now=T1
            ),
            evaluated_at=T1,
        )

    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.PROTECTION_PENDING
    )

    restarted = make_orchestrator(env)
    recovered = restarted.recover_routing(
        active.routing_id,
        now=T1 + timedelta(seconds=61),
    )
    assert (
        recovered.status == RoutingStatus.READY
    )
    later = T1 + timedelta(seconds=62)
    restarted.claim_routing(
        active.routing_id, now=later
    )
    applied, _ = restarted.apply_routing(
        active.routing_id,
        now=later,
        snapshot=env.transport.snapshot(
            now=later
        ),
        evaluated_at=later,
    )
    assert (
        applied.status == RoutingStatus.APPLIED
    )
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).status
        == TradeStatus.OPEN_PROTECTED
    )


def entry_full_fill_setup(tmp_path, crash_points=()):
    env, orchestrator, result, order_id = (
        entry_routing_env(tmp_path)
    )
    env.transport.state.fill_order(
        order_id, Decimal("10")
    )
    routings = stage_entry(
        env,
        orchestrator,
        result,
        order_id,
        now=T1,
        price=Decimal("500"),
    )
    full = routing_by_type(
        routings,
        RoutingFactType.FULL_FILL_CONFIRMED,
    )
    crashing = SagaOrchestrator(
        env.saga,
        owner=ORCH_OWNER,
        crash_injector=(
            CrashInjector(crash_points)
            if crash_points
            else None
        ),
    )
    return env, crashing, full, order_id


def count_entry_events(env, trade_id):
    import sqlite3

    with sqlite3.connect(
        env.store.db_path
    ) as connection:
        return connection.execute(
            """
            SELECT COUNT(*)
            FROM entry_recovery_events
            WHERE trade_id = ?
            """,
            (trade_id,),
        ).fetchone()[0]


def test_crash_after_lifecycle_mutation_does_not_duplicate(
    tmp_path,
):
    env, crashing, full, order_id = (
        entry_full_fill_setup(
            tmp_path,
            (
                OrchestrationCrashPoint
                .AFTER_LIFECYCLE_MUTATION,
            ),
        )
    )
    crashing.claim_routing(
        full.routing_id, now=T2
    )
    with pytest.raises(SimulatedCrash):
        crashing.apply_routing(
            full.routing_id,
            now=T2,
            order_observation=(
                env.transport.state
                .as_equity_order(order_id)
            ),
        )

    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.PROTECTION_PENDING
    )
    events_after_crash = count_entry_events(
        env, env.trade.trade_id
    )
    assert (
        crashing.get_routing(
            full.routing_id
        ).status
        == RoutingStatus.CLAIMED
    )

    restarted = SagaOrchestrator(
        env.saga, owner=ORCH_OWNER
    )
    recovered = restarted.recover_routing(
        full.routing_id,
        now=T2 + timedelta(seconds=61),
    )
    assert (
        recovered.status == RoutingStatus.APPLIED
    )
    assert (
        count_entry_events(
            env, env.trade.trade_id
        )
        == events_after_crash
    )
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).filled_quantity
        == 10
    )


def test_crash_after_event_persistence_fails_closed(
    tmp_path,
):
    env, crashing, full, order_id = (
        entry_full_fill_setup(
            tmp_path,
            (
                OrchestrationCrashPoint
                .AFTER_EVENT_PERSISTENCE,
            ),
        )
    )
    crashing.claim_routing(
        full.routing_id, now=T2
    )
    events_before = (
        crashing.count_routing_events()
    )
    with pytest.raises(SimulatedCrash):
        crashing.apply_routing(
            full.routing_id,
            now=T2,
            order_observation=(
                env.transport.state
                .as_equity_order(order_id)
            ),
        )

    record = crashing.get_routing(
        full.routing_id
    )
    assert record.status == RoutingStatus.CLAIMED
    assert (
        crashing.count_routing_events()
        == events_before
    )

    restarted = SagaOrchestrator(
        env.saga, owner=ORCH_OWNER
    )
    recovered = restarted.recover_routing(
        full.routing_id,
        now=T2 + timedelta(seconds=61),
    )
    assert (
        recovered.status == RoutingStatus.APPLIED
    )
    assert (
        count_entry_events(
            env, env.trade.trade_id
        )
        == 1
    )


def test_crash_before_dependency_unlock_is_idempotent(
    tmp_path,
):
    env = make_env(tmp_path)
    upstream_stop = awaiting_stop(env)
    downstream, _ = plan_stop_action(
        env.saga,
        env.trade,
        env.authorization,
        logical_action_key=(
            f"downstream:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "105",
        },
        action_type=SagaActionType.PLACE_EXIT,
        expected_postcondition={
            "order_present": True,
        },
        depends_on=(upstream_stop.action_id,),
    )
    downstream = env.saga.evaluate_readiness(
        downstream.action_id, occurred_at=NOW
    )
    assert (
        downstream.status
        == SagaActionStatus.BLOCKED
    )

    snapshot = env.transport.snapshot(now=T1)
    confirmed = (
        env.saga.confirm_action_succeeded(
            upstream_stop.action_id,
            snapshot=snapshot,
            evaluated_at=T1,
            observation={
                "active_stop_present": True,
                "stop_quantity": "10",
                "no_conflicting_sell_order": (
                    True
                ),
            },
        )
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )

    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    orchestrator = make_orchestrator(
        env,
        crash_injector=CrashInjector(
            (
                OrchestrationCrashPoint
                .AFTER_ROUTING_COMPLETION_BEFORE_UNLOCK,
            )
        ),
    )
    snapshot2, observation = stop_evidence(
        env, upstream_stop, now=T2
    )
    routings = orchestrator.stage_proven_facts(
        upstream_stop.action_id,
        snapshot=snapshot2,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    orchestrator.claim_routing(
        active.routing_id, now=T2
    )
    with pytest.raises(SimulatedCrash):
        orchestrator.apply_routing(
            active.routing_id,
            now=T2,
            snapshot=env.transport.snapshot(
                now=T2
            ),
            evaluated_at=T2,
        )
    assert (
        orchestrator.get_routing(
            active.routing_id
        ).status
        == RoutingStatus.APPLIED
    )
    assert (
        env.saga.get_action(
            downstream.action_id
        ).status
        == SagaActionStatus.BLOCKED
    )

    restarted = make_orchestrator(env)
    restarted.unlock_dependents(
        upstream_stop.action_id, now=T3
    )
    once = env.saga.get_action(
        downstream.action_id
    )
    assert once.status == SagaActionStatus.READY
    version_after = once.version
    restarted.unlock_dependents(
        upstream_stop.action_id, now=T3
    )
    assert (
        env.saga.get_action(
            downstream.action_id
        ).version
        == version_after
    )


# ---------------------------------------------------------------
# Entry routing
# ---------------------------------------------------------------


def test_acknowledgment_and_visibility_do_not_route_fill(
    tmp_path,
):
    env, orchestrator, result, order_id = (
        entry_routing_env(tmp_path)
    )
    routings = stage_entry(
        env, orchestrator, result, order_id,
        now=T1,
    )
    fact_types = {
        record.routing_type
        for record, _created in routings
    }
    assert fact_types == {
        RoutingFactType.ORDER_VISIBLE
    }

    visible = routing_by_type(
        routings, RoutingFactType.ORDER_VISIBLE
    )
    applied, lifecycle = apply_record(
        env,
        orchestrator,
        visible,
        now=T1,
        order_id=order_id,
    )
    assert (
        applied.status == RoutingStatus.APPLIED
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert trade.filled_quantity == 0
    assert (
        trade.status == TradeStatus.ENTRY_PENDING
    )


def test_partial_entry_fill_routes_exactly_once(tmp_path):
    env, orchestrator, result, order_id = (
        entry_routing_env(tmp_path)
    )
    env.transport.state.fill_order(
        order_id, Decimal("6")
    )
    env.transport.state.cancel_order_state(
        order_id
    )
    routings = stage_entry(
        env,
        orchestrator,
        result,
        order_id,
        now=T1,
        price=Decimal("500"),
    )
    partial = routing_by_type(
        routings,
        RoutingFactType.PARTIAL_FILL_CONFIRMED,
    )
    apply_record(
        env,
        orchestrator,
        partial,
        now=T1,
        order_id=order_id,
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert trade.filled_quantity == 6
    events_once = count_entry_events(
        env, env.trade.trade_id
    )

    replayed = stage_entry(
        env,
        orchestrator,
        result,
        order_id,
        now=T2,
        price=Decimal("500"),
    )
    replay = routing_by_type(
        replayed,
        RoutingFactType.PARTIAL_FILL_CONFIRMED,
    )
    assert (
        replay.routing_id == partial.routing_id
    )
    assert replay.status == RoutingStatus.APPLIED
    assert (
        count_entry_events(
            env, env.trade.trade_id
        )
        == events_once
    )
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).filled_quantity
        == 6
    )


def test_full_entry_fill_routes_to_protection_pending_once(
    tmp_path,
):
    env, crashing, full, order_id = (
        entry_full_fill_setup(tmp_path)
    )
    orchestrator = SagaOrchestrator(
        env.saga, owner=ORCH_OWNER
    )
    orchestrator.claim_routing(
        full.routing_id, now=T2
    )
    applied, lifecycle = (
        orchestrator.apply_routing(
            full.routing_id,
            now=T2,
            order_observation=(
                env.transport.state
                .as_equity_order(order_id)
            ),
        )
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.PROTECTION_PENDING
    )
    assert trade.filled_quantity == 10
    assert (
        count_entry_events(
            env, env.trade.trade_id
        )
        == 1
    )


def test_cancelled_remainder_requires_terminal_proof(
    tmp_path,
):
    env, orchestrator, result, order_id = (
        entry_routing_env(tmp_path)
    )
    env.transport.state.fill_order(
        order_id, Decimal("6")
    )

    active_routings = stage_entry(
        env,
        orchestrator,
        result,
        order_id,
        now=T1,
        price=Decimal("500"),
    )
    active_types = {
        record.routing_type
        for record, _created in active_routings
    }
    assert (
        RoutingFactType.ORDER_CANCELLED
        not in active_types
    )
    assert (
        RoutingFactType.ORDER_TERMINAL
        not in active_types
    )

    env.transport.state.cancel_order_state(
        order_id
    )
    terminal_routings = stage_entry(
        env,
        orchestrator,
        result,
        order_id,
        now=T2,
        price=Decimal("500"),
    )
    partial = routing_by_type(
        terminal_routings,
        RoutingFactType.PARTIAL_FILL_CONFIRMED,
    )
    apply_record(
        env,
        orchestrator,
        partial,
        now=T2,
        order_id=order_id,
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert trade.filled_quantity == 6
    assert trade.status in {
        TradeStatus.ENTRY_PARTIALLY_FILLED,
        TradeStatus.RECOVERY_REQUIRED,
    }


# ---------------------------------------------------------------
# Protective-stop routing
# ---------------------------------------------------------------


def test_stop_linking_alone_does_not_prove_protection(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, routings = staged_stop_routings(
        env, orchestrator
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.PROTECTION_PENDING
    )
    assert trade.protection_confirmed_at is None

    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    applied, _ = apply_record(
        env, orchestrator, active, now=T1
    )
    protected = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        protected.status
        == TradeStatus.OPEN_PROTECTED
    )
    assert (
        protected.protection_confirmed_at
        is not None
    )


def test_confirmed_protection_routes_exactly_once(tmp_path):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, routings = staged_stop_routings(
        env, orchestrator
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    apply_record(
        env, orchestrator, active, now=T1
    )
    version_after = env.store.get_trade(
        env.trade.trade_id
    ).version

    snapshot, observation = stop_evidence(
        env, result, now=T2
    )
    restaged = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T2,
        order_observation=observation,
        occurred_at=T2,
    )
    replay = routing_by_type(
        restaged,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    assert replay.status == RoutingStatus.APPLIED
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).status
        == TradeStatus.OPEN_PROTECTED
    )


def test_degraded_stop_evidence_cannot_prove_protection(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result = awaiting_stop(env)
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )

    env.transport.force_next_snapshot(
        age=timedelta(minutes=5)
    )
    stale = env.transport.snapshot(now=T1)
    with pytest.raises(
        OrchestrationError,
        match="older than the permitted",
    ):
        orchestrator.stage_proven_facts(
            result.action_id,
            snapshot=stale,
            evaluated_at=T1,
            occurred_at=T1,
        )

    env.transport.force_next_snapshot(
        orders_complete=False
    )
    incomplete = env.transport.snapshot(now=T1)
    with pytest.raises(
        OrchestrationError,
        match="incomplete",
    ):
        orchestrator.stage_proven_facts(
            result.action_id,
            snapshot=incomplete,
            evaluated_at=T1,
            occurred_at=T1,
        )

    from src.broker_snapshot import (
        account_fingerprint,
    )

    env.transport.force_next_snapshot(
        account_fingerprint_value=(
            account_fingerprint("OTHER-ACCOUNT")
        )
    )
    mismatched = env.transport.snapshot(now=T1)
    with pytest.raises(
        OrchestrationError,
        match="does not match its evidence",
    ):
        orchestrator.stage_proven_facts(
            result.action_id,
            snapshot=mismatched,
            evaluated_at=T1,
            occurred_at=T1,
        )
    assert orchestrator.count_routings() == 0


def test_conflicting_stop_evidence_cannot_prove_protection(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result = awaiting_stop(env)
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=Decimal("105"),
    )

    snapshot, observation = stop_evidence(
        env, result, now=T1
    )
    routings = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T1,
        order_observation=observation,
        occurred_at=T1,
    )
    fact_types = {
        record.routing_type
        for record, _created in routings
    }
    assert (
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED
        not in fact_types
    )


def test_blocked_reconciliation_routing_on_changed_proof(
    tmp_path,
):
    env = make_env(tmp_path)
    orchestrator = make_orchestrator(env)
    result, routings = staged_stop_routings(
        env, orchestrator
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    orchestrator.claim_routing(
        active.routing_id, now=T1
    )

    env.transport.state.add_order(
        symbol="AAPL",
        side="sell",
        order_type="limit",
        quantity=Decimal("5"),
        limit_price=Decimal("105"),
    )
    with pytest.raises(
        OrchestrationError,
        match="no longer proves",
    ):
        orchestrator.apply_routing(
            active.routing_id,
            now=T2,
            snapshot=env.transport.snapshot(
                now=T2
            ),
            evaluated_at=T2,
        )
    assert (
        orchestrator.get_routing(
            active.routing_id
        ).status
        == RoutingStatus.BLOCKED
    )
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).status
        == TradeStatus.PROTECTION_PENDING
    )


def test_cancelled_stop_does_not_prove_safety(tmp_path):
    env = make_env(tmp_path)
    result = awaiting_stop(env)
    order_id = result.resulting_broker_order_id
    snapshot = env.transport.snapshot(now=T1)
    env.saga.confirm_action_succeeded(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T1,
        observation={
            "active_stop_present": True,
            "stop_quantity": "10",
            "no_conflicting_sell_order": True,
        },
    )
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    orchestrator = make_orchestrator(env)
    snapshot2, observation2 = stop_evidence(
        env, result, now=T1
    )
    routings = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot2,
        evaluated_at=T1,
        order_observation=observation2,
        occurred_at=T1,
    )
    active = routing_by_type(
        routings,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    apply_record(
        env, orchestrator, active, now=T1
    )
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).status
        == TradeStatus.OPEN_PROTECTED
    )

    cancel_action = plan_cancel_stop_action(
        env, source_order_id=order_id
    )
    env.executor.execute_ready_action(
        cancel_action.action_id, now=T2
    )
    snapshot3 = env.transport.snapshot(now=T3)
    cancelled_observation = (
        env.transport.state.as_equity_order(
            order_id
        )
    )
    cancel_routings = (
        orchestrator.stage_proven_facts(
            cancel_action.action_id,
            snapshot=snapshot3,
            evaluated_at=T3,
            order_observation=(
                cancelled_observation
            ),
            occurred_at=T3,
        )
    )
    fact_types = {
        record.routing_type
        for record, _created in cancel_routings
    }
    assert (
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED
        not in fact_types
    )
    terminal = routing_by_type(
        cancel_routings,
        RoutingFactType
        .PROTECTIVE_STOP_TERMINAL_CONFIRMED,
    )
    apply_record(
        env,
        orchestrator,
        terminal,
        now=T3,
        order_id=order_id,
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert trade.status != TradeStatus.CLOSED
    assert trade.status in {
        TradeStatus.STOP_RECOVERY_REQUIRED,
        TradeStatus.OPEN_UNPROTECTED,
    }
    assert trade.unprotected_quantity > 0


def test_replacement_stop_requires_exact_quantity(tmp_path):
    env = make_env(tmp_path)
    authorization = env.authorization
    replacement, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=(
            SagaActionType
            .PLACE_REPLACEMENT_PROTECTIVE_STOP
        ),
        logical_action_key=(
            f"replace:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop_market",
            "quantity": "6",
            "stop_price": "100",
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
        },
        reconciliation_id=RECONCILIATION_ID,
        snapshot_digest=SNAPSHOT_DIGEST,
        account_fingerprint=FINGERPRINT,
        expected_precondition={},
        expected_postcondition={
            "active_stop_present": True,
            "stop_quantity": "6",
        },
        authorization_id=(
            authorization.authorization_id
        ),
        requested_quantity=6,
        stop_price=Decimal("100"),
        occurred_at=NOW,
    )
    replacement = env.saga.evaluate_readiness(
        replacement.action_id, occurred_at=NOW
    )
    result = env.executor.execute_ready_action(
        replacement.action_id, now=NOW
    )
    order_id = result.resulting_broker_order_id

    orchestrator = make_orchestrator(env)
    env.transport.state.positions["AAPL"] = (
        Decimal("6")
    )
    wrong_order = env.transport.state.orders[
        order_id
    ]
    wrong_order.quantity = Decimal("7")
    snapshot = env.transport.snapshot(now=T1)
    routings = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T1,
        occurred_at=T1,
    )
    fact_types = {
        record.routing_type
        for record, _created in routings
    }
    assert (
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED
        not in fact_types
    )

    wrong_order.quantity = Decimal("6")
    snapshot2 = env.transport.snapshot(now=T2)
    exact = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot2,
        evaluated_at=T2,
        occurred_at=T2,
    )
    active = routing_by_type(
        exact,
        RoutingFactType
        .ACTIVE_PROTECTIVE_STOP_CONFIRMED,
    )
    assert (
        active.proven_protected_quantity
        == Decimal("6")
    )


# ---------------------------------------------------------------
# Exit routing
# ---------------------------------------------------------------


def exit_orchestration_env(tmp_path):
    env = make_env(tmp_path)
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
            "time_in_force": "gfd",
            "market_hours": "regular_hours",
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
    exit_action = env.saga.evaluate_readiness(
        exit_action.action_id, occurred_at=NOW
    )
    result = env.executor.execute_ready_action(
        exit_action.action_id, now=NOW
    )
    order_id = result.resulting_broker_order_id
    env.store.begin_exit(
        env.trade.trade_id,
        broker_order_id=order_id,
        requested_quantity=10,
        close_reason="profit_target",
        intent_type="profit_target",
        intended_price=Decimal("102"),
        occurred_at=NOW + timedelta(seconds=1),
    )
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    orchestrator = SagaOrchestrator(
        env.saga, owner=ORCH_OWNER
    )
    return env, orchestrator, result, order_id


def stage_exit(env, orchestrator, result, order_id, *, now):
    snapshot = env.transport.snapshot(now=now)
    observation = (
        env.transport.state.as_equity_order(
            order_id
        )
    )
    return orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=now,
        order_observation=observation,
        average_fill_price=Decimal("102"),
        occurred_at=now,
    )


def test_exit_acknowledgment_does_not_close_trade(tmp_path):
    env, orchestrator, result, order_id = (
        exit_orchestration_env(tmp_path)
    )
    routings = stage_exit(
        env, orchestrator, result, order_id,
        now=T1,
    )
    fact_types = {
        record.routing_type
        for record, _created in routings
    }
    assert fact_types == {
        RoutingFactType.ORDER_VISIBLE
    }
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status == TradeStatus.EXIT_PENDING
    )


def test_partial_exit_routes_once_without_duplicate_pnl(
    tmp_path,
):
    env, orchestrator, result, order_id = (
        exit_orchestration_env(tmp_path)
    )
    env.transport.state.fill_order(
        order_id, Decimal("4")
    )
    routings = stage_exit(
        env, orchestrator, result, order_id,
        now=T1,
    )
    partial = routing_by_type(
        routings,
        RoutingFactType.PARTIAL_FILL_CONFIRMED,
    )
    apply_record(
        env,
        orchestrator,
        partial,
        now=T1,
        order_id=order_id,
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.EXIT_PARTIALLY_FILLED
    )
    assert trade.cumulative_exited_quantity == 4
    pnl_once = trade.cumulative_realized_pnl
    assert pnl_once == Decimal("8")
    assert trade.unprotected_quantity > 0

    replayed = stage_exit(
        env, orchestrator, result, order_id,
        now=T2,
    )
    replay = routing_by_type(
        replayed,
        RoutingFactType.PARTIAL_FILL_CONFIRMED,
    )
    assert (
        replay.routing_id == partial.routing_id
    )
    assert replay.status == RoutingStatus.APPLIED
    assert (
        env.store.get_trade(
            env.trade.trade_id
        ).cumulative_realized_pnl
        == pnl_once
    )


def test_full_exit_requires_flat_proof_and_closes_once(
    tmp_path,
):
    env, orchestrator, result, order_id = (
        exit_orchestration_env(tmp_path)
    )
    env.transport.state.fill_order(
        order_id, Decimal("10")
    )
    routings = stage_exit(
        env, orchestrator, result, order_id,
        now=T1,
    )
    full = routing_by_type(
        routings,
        RoutingFactType.FULL_FILL_CONFIRMED,
    )
    apply_record(
        env,
        orchestrator,
        full,
        now=T1,
        order_id=order_id,
    )
    trade = env.store.get_trade(
        env.trade.trade_id
    )
    assert (
        trade.status
        == TradeStatus.EXIT_RECOVERY_REQUIRED
    )
    assert trade.status != TradeStatus.CLOSED

    flat = routing_by_type(
        routings,
        RoutingFactType.POSITION_FLAT_CONFIRMED,
    )
    apply_record(
        env, orchestrator, flat, now=T2
    )
    closed = env.store.get_trade(
        env.trade.trade_id
    )
    assert closed.status == TradeStatus.CLOSED
    assert (
        closed.cumulative_realized_pnl
        == Decimal("20")
    )
    assert closed.close_reason == "profit_target"

    restarted = SagaOrchestrator(
        BrokerActionSagaStore(
            TradeStateStore(
                tmp_path / "state.sqlite"
            )
        ),
        owner=ORCH_OWNER,
    )
    snapshot = env.transport.snapshot(now=T3)
    replayed = restarted.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T3,
        order_observation=(
            env.transport.state.as_equity_order(
                order_id
            )
        ),
        average_fill_price=Decimal("102"),
        occurred_at=T3,
    )
    for record, created in replayed:
        assert created is False
    final = env.store.get_trade(
        env.trade.trade_id
    )
    assert final.status == TradeStatus.CLOSED
    assert (
        final.cumulative_realized_pnl
        == Decimal("20")
    )


def test_flatten_requires_flat_position_proof(tmp_path):
    env = make_env(tmp_path)
    flatten, _ = env.saga.plan_action(
        strategy=env.trade.strategy,
        trade_id=env.trade.trade_id,
        action_type=(
            SagaActionType.FLATTEN_POSITION
        ),
        logical_action_key=(
            f"flatten:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "limit",
            "quantity": "10",
            "limit_price": "100",
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
        close_reason="operator_exit",
        occurred_at=NOW,
    )
    flatten = env.saga.evaluate_readiness(
        flatten.action_id, occurred_at=NOW
    )
    result = env.executor.execute_ready_action(
        flatten.action_id, now=NOW
    )
    order_id = result.resulting_broker_order_id
    env.transport.state.positions["AAPL"] = (
        Decimal("10")
    )
    orchestrator = make_orchestrator(env)

    snapshot = env.transport.snapshot(now=T1)
    holding = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot,
        evaluated_at=T1,
        occurred_at=T1,
    )
    fact_types = {
        record.routing_type
        for record, _created in holding
    }
    assert (
        RoutingFactType.POSITION_FLAT_CONFIRMED
        not in fact_types
    )

    env.transport.state.fill_order(
        order_id, Decimal("10")
    )
    snapshot2 = env.transport.snapshot(now=T2)
    flat_now = orchestrator.stage_proven_facts(
        result.action_id,
        snapshot=snapshot2,
        evaluated_at=T2,
        occurred_at=T2,
    )
    flat = routing_by_type(
        flat_now,
        RoutingFactType.POSITION_FLAT_CONFIRMED,
    )
    assert flat.status == RoutingStatus.READY


# ---------------------------------------------------------------
# Migration and static safety
# ---------------------------------------------------------------


def test_migration_is_additive_and_idempotent(tmp_path):
    import sqlite3

    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    for _ in range(2):
        store = TradeStateStore(database_path)
        saga = BrokerActionSagaStore(store)
        orchestrator = SagaOrchestrator(
            saga, owner=ORCH_OWNER
        )
        assert orchestrator.count_routings() == 0
        assert (
            orchestrator.count_routing_events()
            == 0
        )

    with sqlite3.connect(
        database_path
    ) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table'
                """
            )
        }
    assert "broker_saga_routings" in tables
    assert (
        "broker_saga_routing_events" in tables
    )
    legacy = TradeStateStore(
        database_path
    ).get_trade("legacy-trade")
    assert legacy.status == TradeStatus.CLOSED


def test_legacy_saga_records_get_no_fabricated_routing(
    tmp_path,
):
    env = make_env(tmp_path)
    awaiting_stop(env)

    orchestrator = make_orchestrator(env)
    assert orchestrator.count_routings() == 0
    assert (
        orchestrator.count_routing_events() == 0
    )


def orchestrator_module_source() -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / "broker_saga_orchestrator.py"
    ).read_text(encoding="utf-8")


def test_orchestrator_has_no_live_broker_capability():
    lowered = (
        orchestrator_module_source().lower()
    )
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
        "simulated_broker_transport",
        "broker_saga_executor",
    ):
        assert forbidden not in lowered, forbidden


def test_orchestrator_requires_saga_store():
    with pytest.raises(
        OrchestrationError,
        match="BrokerActionSagaStore is required",
    ):
        SagaOrchestrator(
            "data", owner=ORCH_OWNER
        )


def test_strategy_enabled_remains_false():
    config = load_config()
    assert (
        config["strategy"]["enabled"] is False
    )


def test_risk_configuration_is_unchanged():
    config = load_config()
    assert config["risk"] == {
        "risk_per_trade_usd": 15,
        "maximum_position_usd": 3000,
        "maximum_open_positions": 1,
        "maximum_entries_per_day": 2,
        "daily_loss_limit_usd": 45,
        "weekly_loss_limit_usd": 120,
        "total_drawdown_halt_usd": 300,
        "margin_allowed": False,
        "short_selling_allowed": False,
        "averaging_down_allowed": False,
    }


def test_repository_guard_attempts_remain_zero(
    repository_data_database_guard,
):
    assert (
        repository_data_database_guard.attempts
        == []
    )
