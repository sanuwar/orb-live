from __future__ import annotations

from datetime import timedelta
from pathlib import Path
import sqlite3

import pytest

from src.broker_action_saga import (
    BrokerActionSagaError,
    BrokerActionSagaStore,
    SagaActionStatus,
)
from src.broker_saga_orchestrator import (
    OrchestrationCrashPoint,
    SagaOrchestrator,
)
from src.broker_snapshot import (
    account_fingerprint,
)
from src.operator_resolution import (
    OperatorResolutionError,
    OperatorResolutionStore,
    ResolutionDecision,
    classify_resolution_evidence,
)
from src.simulated_broker_transport import (
    CrashInjector,
    SimulatedCrash,
    TransportBehavior,
)
from src.trade_state import TradeStateStore
from tests.test_broker_action_saga import (
    NOW,
    OTHER_STRATEGY,
    STRATEGY,
)
from tests.test_broker_saga_executor import (
    make_env,
    ready_action,
)
from tests.test_entry_recovery import (
    create_legacy_database,
)


T1 = NOW + timedelta(seconds=10)
T2 = NOW + timedelta(seconds=20)
T3 = NOW + timedelta(seconds=30)


def operator_env(
    tmp_path,
    *,
    behavior=TransportBehavior.TIMEOUT_AFTER_EFFECT,
    crash_injector=None,
):
    env = make_env(tmp_path)
    action = ready_action(env)
    env.transport.script_submit(
        action.ref_id, behavior
    )
    result = env.executor.execute_ready_action(
        action.action_id, now=NOW
    )
    assert (
        result.status
        == SagaActionStatus.OUTCOME_UNKNOWN
    )
    escalated = env.saga.require_operator(
        action.action_id,
        reason="Ambiguous simulated outcome.",
        occurred_at=NOW + timedelta(seconds=1),
    )
    assert (
        escalated.status
        == SagaActionStatus.OPERATOR_REQUIRED
    )
    resolutions = OperatorResolutionStore(
        env.saga, crash_injector=crash_injector
    )
    return env, resolutions, escalated


def open_default(resolutions, action):
    record, _created = (
        resolutions.open_resolution(
            action.action_id,
            required_evidence_type=(
                "fresh_broker_order_observation"
            ),
            occurred_at=T1,
        )
    )
    return record


def effect_observation(env, action):
    order_id = (
        env.transport.lookup_order_by_ref(
            action.ref_id
        )
    )
    return {
        "effect_present": True,
        "ref_id": action.ref_id,
        "broker_order_id": order_id,
        "symbol": "AAPL",
        "side": "sell",
        "type": "stop_market",
        "quantity": "10",
        "stop_price": "99",
    }


def submit(
    env,
    resolutions,
    record,
    observation,
    *,
    now=T2,
    strategy=STRATEGY,
):
    snapshot = env.transport.snapshot(now=now)
    return resolutions.submit_evidence(
        record.resolution_id,
        strategy=strategy,
        snapshot=snapshot,
        evaluated_at=now,
        observation=observation,
        occurred_at=now,
    )


# ---------------------------------------------------------------
# Opening and acknowledgment
# ---------------------------------------------------------------


def test_resolution_opens_only_for_operator_required(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)
    assert (
        record.decision
        == ResolutionDecision.PENDING_EVIDENCE
    )
    assert (
        record.expected_ref_id == action.ref_id
    )
    assert (
        record.expected_payload_digest
        == action.payload_digest
    )

    replay, created = (
        resolutions.open_resolution(
            action.action_id,
            required_evidence_type="anything",
            occurred_at=T2,
        )
    )
    assert created is False
    assert (
        replay.resolution_id
        == record.resolution_id
    )

    other = ready_action(
        env,
        logical_action_key=(
            f"other-stop:{env.trade.trade_id}"
        ),
        payload={
            "symbol": "AAPL",
            "side": "sell",
            "type": "stop_market",
            "quantity": "10",
            "stop_price": "98",
        },
    )
    with pytest.raises(
        OperatorResolutionError,
        match="OPERATOR_REQUIRED",
    ):
        resolutions.open_resolution(
            other.action_id,
            required_evidence_type="anything",
            occurred_at=T2,
        )


def test_acknowledgment_preserves_halts_and_outcome(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    acknowledged = resolutions.acknowledge(
        record.resolution_id,
        operator_label="operator-1",
        note="Reviewed; requesting fresh proof.",
        occurred_at=T2,
    )
    assert (
        acknowledged.decision
        == ResolutionDecision.PENDING_EVIDENCE
    )
    assert (
        acknowledged.acknowledged_by
        == "operator-1"
    )

    refreshed = env.saga.get_action(
        action.action_id
    )
    assert (
        refreshed.status
        == SagaActionStatus.OPERATOR_REQUIRED
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )
    assert env.store.is_entry_halted(STRATEGY)

    with pytest.raises(
        BrokerActionSagaError,
        match="unresolved operator",
    ):
        env.saga.clear_broker_action_halt(
            strategy=STRATEGY,
            halt_key=(
                f"saga-action:{action.action_id}"
            ),
            occurred_at=T3,
        )
    with pytest.raises(BrokerActionSagaError):
        env.saga.approve_retry(
            action.action_id, occurred_at=T3
        )


# ---------------------------------------------------------------
# Evidence-based resolution
# ---------------------------------------------------------------


def test_fresh_effect_evidence_resolves_operator_required(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    resolved = submit(
        env,
        resolutions,
        record,
        effect_observation(env, action),
    )
    assert (
        resolved.decision
        == ResolutionDecision.EFFECT_CONFIRMED
    )
    assert resolved.resolved_at is not None
    assert (
        resolved.evidence_snapshot_digest
        is not None
    )

    refreshed = env.saga.get_action(
        action.action_id
    )
    assert (
        refreshed.status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )
    assert refreshed.confirmed_at is None

    confirmed = env.executor.try_confirm(
        action.action_id, now=T3
    )
    assert (
        confirmed.status
        == SagaActionStatus.CONFIRMED_SUCCEEDED
    )


def test_no_effect_evidence_permits_controlled_retry(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path,
        behavior=(
            TransportBehavior
            .TIMEOUT_BEFORE_RESPONSE
        ),
    )
    record = open_default(resolutions, action)

    resolved = submit(
        env,
        resolutions,
        record,
        {
            "effect_present": False,
            "ref_id": action.ref_id,
            "symbol": "AAPL",
        },
    )
    assert (
        resolved.decision
        == ResolutionDecision.NO_EFFECT_CONFIRMED
    )
    refreshed = env.saga.get_action(
        action.action_id
    )
    assert (
        refreshed.status
        == SagaActionStatus.CONFIRMED_NOT_PERFORMED
    )

    env.saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=T3,
    )
    retried = env.saga.approve_retry(
        action.action_id, occurred_at=T3
    )
    assert (
        retried.status == SagaActionStatus.READY
    )
    assert retried.ref_id == action.ref_id


@pytest.mark.parametrize(
    ("snapshot_overrides", "reason_match"),
    (
        (
            {"age": timedelta(minutes=5)},
            "older than the permitted",
        ),
        (
            {"orders_complete": False},
            "incomplete",
        ),
        (
            {
                "account_fingerprint_value": (
                    account_fingerprint(
                        "OTHER-ACCOUNT"
                    )
                )
            },
            "does not match",
        ),
    ),
)
def test_degraded_evidence_preserves_operator_required(
    tmp_path, snapshot_overrides, reason_match
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    env.transport.force_next_snapshot(
        **snapshot_overrides
    )
    resolved = submit(
        env,
        resolutions,
        record,
        effect_observation(env, action),
    )
    assert (
        resolved.decision
        == ResolutionDecision.REMAINS_AMBIGUOUS
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )


@pytest.mark.parametrize(
    "mutation",
    (
        {"quantity": "7"},
        {"stop_price": "42"},
        {"type": "limit"},
        {"ref_id": "wrong-ref"},
        {"symbol": "MSFT"},
        {"conflicting_active_orders": True},
        {"effect_present": None},
    ),
)
def test_inconsistent_evidence_remains_ambiguous(
    tmp_path, mutation
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    observation = effect_observation(env, action)
    observation.update(mutation)
    resolved = submit(
        env, resolutions, record, observation
    )
    assert (
        resolved.decision
        == ResolutionDecision.REMAINS_AMBIGUOUS
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )


def test_another_strategy_cannot_resolve(tmp_path):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    with pytest.raises(
        OperatorResolutionError,
        match="Another strategy",
    ):
        submit(
            env,
            resolutions,
            record,
            effect_observation(env, action),
            strategy=OTHER_STRATEGY,
        )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )


def test_halt_clearing_scope_is_exact(tmp_path):
    env, resolutions, action = operator_env(
        tmp_path,
        behavior=(
            TransportBehavior
            .TIMEOUT_BEFORE_RESPONSE
        ),
    )
    env.saga.activate_broker_action_halt(
        strategy=STRATEGY,
        reason="Unrelated durable halt.",
        halt_key="unrelated-halt",
        occurred_at=T1,
    )
    record = open_default(resolutions, action)
    submit(
        env,
        resolutions,
        record,
        {
            "effect_present": False,
            "ref_id": action.ref_id,
        },
    )

    env.saga.clear_broker_action_halt(
        strategy=STRATEGY,
        halt_key=f"saga-action:{action.action_id}",
        occurred_at=T3,
    )
    halt = env.saga.get_broker_action_halt(
        STRATEGY
    )
    assert halt.active is True
    assert (
        "Unrelated durable halt."
        in halt.reasons
    )


def test_terminal_and_superseded_preserve_history(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)
    terminal = resolutions.mark_terminal(
        record.resolution_id,
        failure_code="manual_review_dead_end",
        failure_reason=(
            "No automated recovery is possible."
        ),
        occurred_at=T2,
    )
    assert (
        terminal.decision
        == ResolutionDecision.TERMINAL_FAILURE
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )
    assert env.saga.is_broker_action_halted(
        STRATEGY
    )

    reopened, created = (
        resolutions.open_resolution(
            action.action_id,
            required_evidence_type="second-pass",
            occurred_at=T3,
        )
    )
    assert created is True
    superseded = resolutions.supersede(
        reopened.resolution_id,
        reason="Replaced by a newer workflow.",
        occurred_at=T3,
    )
    assert (
        superseded.decision
        == ResolutionDecision.SUPERSEDED
    )
    history = resolutions.list_resolutions(
        action.action_id
    )
    assert len(history) == 2


def test_restart_preserves_resolution_records(tmp_path):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)
    resolutions.acknowledge(
        record.resolution_id,
        operator_label="operator-1",
        note="Recorded before restart.",
        occurred_at=T2,
    )

    reopened = OperatorResolutionStore(
        BrokerActionSagaStore(
            TradeStateStore(
                tmp_path / "state.sqlite"
            )
        )
    )
    restored = reopened.get_resolution(
        record.resolution_id
    )
    assert (
        restored.decision
        == ResolutionDecision.PENDING_EVIDENCE
    )
    assert (
        restored.acknowledged_by == "operator-1"
    )
    assert (
        restored.expected_ref_id
        == action.ref_id
    )


def test_resolution_history_is_append_only(tmp_path):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)

    with sqlite3.connect(
        tmp_path / "state.sqlite"
    ) as connection:
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            connection.execute(
                """
                DELETE FROM operator_resolutions
                WHERE resolution_id = ?
                """,
                (record.resolution_id,),
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            connection.execute(
                """
                UPDATE
                    operator_resolution_events
                SET event_type = 'tampered'
                """
            )
        with pytest.raises(
            sqlite3.IntegrityError,
            match="append-only",
        ):
            connection.execute(
                """
                DELETE FROM
                    operator_resolution_events
                """
            )


# ---------------------------------------------------------------
# Crash safety
# ---------------------------------------------------------------


def test_crash_during_ack_persistence_fails_closed(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path,
        crash_injector=CrashInjector(
            (
                OrchestrationCrashPoint
                .DURING_ACK_PERSISTENCE,
            )
        ),
    )
    record = open_default(resolutions, action)

    with pytest.raises(SimulatedCrash):
        resolutions.acknowledge(
            record.resolution_id,
            operator_label="operator-1",
            note="Crashing acknowledgment.",
            occurred_at=T2,
        )
    rolled_back = resolutions.get_resolution(
        record.resolution_id
    )
    assert rolled_back.acknowledged_at is None
    assert rolled_back.acknowledged_by is None

    recovered = resolutions.acknowledge(
        record.resolution_id,
        operator_label="operator-1",
        note="Retried acknowledgment.",
        occurred_at=T3,
    )
    assert (
        recovered.acknowledged_by == "operator-1"
    )


def test_crash_after_evidence_before_saga_transition(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path,
        crash_injector=CrashInjector(
            (
                OrchestrationCrashPoint
                .AFTER_EVIDENCE_BEFORE_SAGA_TRANSITION,
            )
        ),
    )
    record = open_default(resolutions, action)

    with pytest.raises(SimulatedCrash):
        submit(
            env,
            resolutions,
            record,
            effect_observation(env, action),
        )
    decided = resolutions.get_resolution(
        record.resolution_id
    )
    assert (
        decided.decision
        == ResolutionDecision.EFFECT_CONFIRMED
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )

    snapshot = env.transport.snapshot(now=T3)
    applied = (
        resolutions.apply_decided_resolution(
            record.resolution_id,
            snapshot=snapshot,
            evaluated_at=T3,
            occurred_at=T3,
        )
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )

    replay = (
        resolutions.apply_decided_resolution(
            record.resolution_id,
            snapshot=env.transport.snapshot(
                now=T3 + timedelta(seconds=1)
            ),
            evaluated_at=(
                T3 + timedelta(seconds=1)
            ),
        )
    )
    assert (
        replay.resolution_id
        == applied.resolution_id
    )


def test_crash_after_saga_transition_is_consistent(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path,
        crash_injector=CrashInjector(
            (
                OrchestrationCrashPoint
                .AFTER_SAGA_TRANSITION_BEFORE_ROUTING,
            )
        ),
    )
    record = open_default(resolutions, action)

    with pytest.raises(SimulatedCrash):
        submit(
            env,
            resolutions,
            record,
            effect_observation(env, action),
        )
    assert (
        resolutions.get_resolution(
            record.resolution_id
        ).decision
        == ResolutionDecision.EFFECT_CONFIRMED
    )
    assert (
        env.saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION
    )


# ---------------------------------------------------------------
# Migration and static safety
# ---------------------------------------------------------------


def test_migration_is_additive_idempotent_and_honest(
    tmp_path,
):
    database_path = tmp_path / "legacy.sqlite"
    create_legacy_database(database_path)

    for _ in range(2):
        store = TradeStateStore(database_path)
        saga = BrokerActionSagaStore(store)
        resolutions = OperatorResolutionStore(
            saga
        )
        assert (
            resolutions.count_resolutions() == 0
        )
        assert (
            resolutions.count_resolution_events()
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
    assert "operator_resolutions" in tables
    assert (
        "operator_resolution_events" in tables
    )


def test_legacy_operator_required_is_not_auto_resolved(
    tmp_path,
):
    env, resolutions, action = operator_env(
        tmp_path
    )

    reopened_saga = BrokerActionSagaStore(
        TradeStateStore(tmp_path / "state.sqlite")
    )
    OperatorResolutionStore(reopened_saga)
    SagaOrchestrator(
        reopened_saga, owner="orchestrator-1"
    )

    assert (
        reopened_saga.get_action(
            action.action_id
        ).status
        == SagaActionStatus.OPERATOR_REQUIRED
    )
    assert reopened_saga.is_broker_action_halted(
        STRATEGY
    )
    fresh_resolutions = OperatorResolutionStore(
        reopened_saga
    )
    assert (
        fresh_resolutions.count_resolutions()
        == 0
    )


def test_classification_is_deterministic(tmp_path):
    env, resolutions, action = operator_env(
        tmp_path
    )
    record = open_default(resolutions, action)
    saga_action = env.saga.get_action(
        action.action_id
    )
    snapshot = env.transport.snapshot(now=T2)
    observation = effect_observation(env, action)

    first = classify_resolution_evidence(
        record,
        saga_action,
        snapshot=snapshot,
        evaluated_at=T2,
        observation=observation,
    )
    second = classify_resolution_evidence(
        record,
        saga_action,
        snapshot=snapshot,
        evaluated_at=T2,
        observation=observation,
    )
    assert first == second
    assert first[0] == "effect_confirmed"


def resolution_module_source() -> str:
    return (
        Path(__file__).parent.parent
        / "src"
        / "operator_resolution.py"
    ).read_text(encoding="utf-8")


def test_resolution_module_has_no_live_broker_capability():
    lowered = resolution_module_source().lower()
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
    ):
        assert forbidden not in lowered, forbidden


def test_resolution_store_requires_saga_store():
    with pytest.raises(
        OperatorResolutionError,
        match="BrokerActionSagaStore is required",
    ):
        OperatorResolutionStore("data")
