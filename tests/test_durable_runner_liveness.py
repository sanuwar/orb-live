"""Durable, injected-time tests for runner heartbeat liveness."""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

import src.durable_runner_liveness as liveness_module
from src.durable_runner_liveness import (
    DurableRunnerLiveness,
    FreshnessPolicy,
    LivenessDecision,
    LivenessError,
    LivenessReason,
    LivenessState,
    LivenessSuspensionRequest,
    RunnerHeartbeat,
    StoredRunnerOutcome,
)
from src.durable_suspension_controller import SuspensionCause
from src.execution_gates import ACTIVATION_APPROVALS
from src.offline_shadow_runner import RunnerOutcome
from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE
from src.read_only_broker_state import approved_active_protection_states


MODULE_PATH = pathlib.Path(liveness_module.__file__)
NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)


def policy(**overrides):
    values = {
        "expected_interval": timedelta(minutes=1),
        "allowed_lateness": timedelta(seconds=10),
        "maximum_observation_age": timedelta(minutes=2),
        "maximum_consecutive_missed_cycles": 2,
    }
    values.update(overrides)
    return FreshnessPolicy(**values)


def heartbeat(sequence=1, *, expected=NOW, observed=None, outcome=RunnerOutcome.COMPLETED_BOUND):
    return RunnerHeartbeat(
        runner_id="runner-one",
        heartbeat_sequence=sequence,
        expected_cycle_timestamp=expected,
        observed_completion_timestamp=observed or expected + timedelta(seconds=5),
        runner_outcome=outcome,
    )


def monitor(tmp_path, *, selected_policy=None):
    return DurableRunnerLiveness(
        (tmp_path / "liveness.sqlite").resolve(),
        policy=selected_policy or policy(),
    )


def evaluate(instance, sequence=1, **overrides):
    expected = overrides.pop("expected", NOW + timedelta(minutes=sequence - 1))
    observed = overrides.pop("observed", expected + timedelta(seconds=5))
    outcome = overrides.pop("outcome", RunnerOutcome.COMPLETED_BOUND)
    values = {
        "runner_id": "runner-one",
        "heartbeat": heartbeat(
            sequence, expected=expected, observed=observed, outcome=outcome
        ),
        "expected_cycle_timestamp": expected,
        "evaluation_time": observed + timedelta(seconds=5),
        "alert_delivery_confirmed": True,
        "transition_id": f"liveness-transition-{sequence}",
    }
    values.update(overrides)
    return instance.evaluate(**values)


@pytest.mark.parametrize(
    "field,value",
    [
        ("expected_interval", timedelta(0)),
        ("expected_interval", timedelta(seconds=-1)),
        ("allowed_lateness", timedelta(seconds=-1)),
        ("maximum_observation_age", timedelta(0)),
        ("maximum_consecutive_missed_cycles", 0),
        ("maximum_consecutive_missed_cycles", True),
    ],
)
def test_invalid_policy_is_rejected(field, value):
    with pytest.raises(LivenessError):
        policy(**{field: value}).validate()


def test_policy_is_frozen_and_explicit():
    selected = policy()
    selected.validate()
    with pytest.raises(Exception):
        selected.expected_interval = timedelta(days=1)


def test_changed_policy_cannot_reinterpret_durable_history(tmp_path):
    first = monitor(tmp_path)
    evaluate(first)
    first.close()
    with pytest.raises(LivenessError, match="policy"):
        monitor(tmp_path, selected_policy=policy(allowed_lateness=timedelta(seconds=9)))


def test_current_heartbeat_is_healthy(tmp_path):
    instance = monitor(tmp_path)
    result = evaluate(instance)
    assert result.record.state is LivenessState.HEALTHY
    assert result.record.reason is LivenessReason.HEARTBEAT_CURRENT
    assert result.consecutive_missed_cycles == 0
    assert result.suspension.request_suspension is False
    instance.close()


def test_missing_alert_delivery_is_never_healthy(tmp_path):
    instance = monitor(tmp_path)
    result = evaluate(instance, alert_delivery_confirmed=None)
    assert result.record.state is LivenessState.DEGRADED
    assert result.record.reason is LivenessReason.UNKNOWN_REASON
    instance.close()


@pytest.mark.parametrize("alert", [None, False])
def test_missing_or_failed_alert_evidence_stays_degraded(tmp_path, alert):
    instance = monitor(tmp_path)
    assert evaluate(instance, alert_delivery_confirmed=alert).record.state is LivenessState.DEGRADED
    instance.close()


def test_exact_observation_age_boundary_is_current(tmp_path):
    instance = monitor(tmp_path)
    observed = NOW + timedelta(seconds=5)
    result = evaluate(
        instance,
        observed=observed,
        evaluation_time=observed + policy().maximum_observation_age,
    )
    assert result.record.state is LivenessState.HEALTHY
    instance.close()


def test_one_microsecond_after_age_boundary_is_stale_and_requests_suspension(tmp_path):
    instance = monitor(tmp_path)
    observed = NOW + timedelta(seconds=5)
    result = evaluate(
        instance,
        observed=observed,
        evaluation_time=observed + policy().maximum_observation_age + timedelta(microseconds=1),
    )
    assert result.record.state is LivenessState.STALE
    assert result.record.reason is LivenessReason.HEARTBEAT_STALE
    assert result.suspension.cause is SuspensionCause.STALE_DATA
    instance.close()


def test_exact_lateness_boundary_is_current_and_one_unit_after_is_missed(tmp_path):
    exact = monitor(tmp_path / "exact")
    result = evaluate(exact, observed=NOW + policy().allowed_lateness)
    assert result.record.state is LivenessState.HEALTHY
    exact.close()
    late = monitor(tmp_path / "late")
    result = evaluate(
        late,
        observed=NOW + policy().allowed_lateness + timedelta(microseconds=1),
    )
    assert result.record.state is LivenessState.MISSED_CYCLE
    assert result.record.reason is LivenessReason.EXPECTED_TICK_MISSED
    late.close()


@pytest.mark.parametrize(
    "expected,observed,evaluation",
    [
        (NOW + timedelta(seconds=1), NOW + timedelta(seconds=2), NOW),
        (NOW, NOW + timedelta(seconds=2), NOW + timedelta(seconds=1)),
        (NOW, NOW - timedelta(seconds=1), NOW + timedelta(seconds=1)),
    ],
)
def test_future_and_inverted_timestamps_fail_closed(
    tmp_path, expected, observed, evaluation
):
    instance = monitor(tmp_path)
    result = evaluate(
        instance,
        expected=expected,
        observed=observed,
        evaluation_time=evaluation,
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.CLOCK_INVALID
    assert result.suspension.request_suspension is True
    instance.close()


def test_no_heartbeat_is_unknown_then_requires_suspension(tmp_path):
    instance = monitor(tmp_path)
    first = instance.evaluate(
        "runner-one", heartbeat=None, expected_cycle_timestamp=NOW,
        evaluation_time=NOW + timedelta(minutes=1), alert_delivery_confirmed=None,
        transition_id="liveness-transition-missing-1",
    )
    second = instance.evaluate(
        "runner-one", heartbeat=None,
        expected_cycle_timestamp=NOW + timedelta(minutes=1),
        evaluation_time=NOW + timedelta(minutes=2), alert_delivery_confirmed=None,
        transition_id="liveness-transition-missing-2",
    )
    assert first.record.state is LivenessState.UNKNOWN
    assert first.record.reason is LivenessReason.NO_HEARTBEAT
    assert second.record.state is LivenessState.SUSPENSION_REQUIRED
    assert second.record.reason is LivenessReason.EXPECTED_TICK_MISSED
    assert second.consecutive_missed_cycles == 2
    instance.close()


def test_missed_count_and_suspension_latch_survive_restart(tmp_path):
    instance = monitor(tmp_path)
    for index in range(2):
        instance.evaluate(
            "runner-one", heartbeat=None,
            expected_cycle_timestamp=NOW + timedelta(minutes=index),
            evaluation_time=NOW + timedelta(minutes=index + 1),
            alert_delivery_confirmed=None,
            transition_id=f"liveness-transition-miss-{index}",
        )
    instance.close()
    restarted = monitor(tmp_path)
    recovery_expected = NOW + timedelta(minutes=2)
    healthy = evaluate(
        restarted,
        1,
        expected=recovery_expected,
        observed=recovery_expected + timedelta(seconds=5),
        transition_id="liveness-transition-recovery",
    )
    assert healthy.record.state is LivenessState.HEALTHY
    assert healthy.consecutive_missed_cycles == 0
    assert healthy.suspension.request_suspension is True
    assert healthy.suspension.cause is SuspensionCause.MONITORING_UNHEALTHY
    restarted.close()


def test_sequence_gap_accumulates_missed_cycles(tmp_path):
    instance = monitor(tmp_path, selected_policy=policy(maximum_consecutive_missed_cycles=3))
    evaluate(instance, 1)
    result = evaluate(instance, 3, transition_id="liveness-transition-gap")
    assert result.record.state is LivenessState.MISSED_CYCLE
    assert result.consecutive_missed_cycles == 1
    instance.close()


def test_sequence_regression_fails_closed(tmp_path):
    instance = monitor(tmp_path)
    evaluate(instance, 2, transition_id="liveness-transition-first")
    result = evaluate(instance, 1, transition_id="liveness-transition-regression")
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.SEQUENCE_REGRESSION
    assert result.suspension.cause is SuspensionCause.INTERNAL_INVARIANT_FAILURE
    later = evaluate(
        instance,
        2,
        expected=NOW + timedelta(minutes=2),
        observed=NOW + timedelta(minutes=2, seconds=5),
        transition_id="liveness-transition-after-regression",
    )
    assert later.record.reason is LivenessReason.DUPLICATE_HEARTBEAT
    instance.close()


def test_expected_interval_grid_and_evaluation_monotonicity_are_enforced(tmp_path):
    cadence = monitor(tmp_path / "cadence")
    evaluate(cadence, 1)
    bad_expected = NOW + timedelta(seconds=30)
    result = evaluate(
        cadence,
        2,
        expected=bad_expected,
        observed=bad_expected + timedelta(seconds=5),
        transition_id="liveness-transition-bad-cadence",
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.CONFIG_INVALID
    cadence.close()

    clock = monitor(tmp_path / "clock")
    first = evaluate(clock, 1)
    expected = NOW + timedelta(minutes=1)
    regressed = evaluate(
        clock,
        2,
        expected=expected,
        observed=expected,
        evaluation_time=first.record.evaluation_time - timedelta(microseconds=1),
        transition_id="liveness-transition-clock-regression",
    )
    assert regressed.record.reason is LivenessReason.CLOCK_INVALID
    clock.close()


def test_timestamp_leap_cannot_bypass_missed_cycle_threshold(tmp_path):
    instance = monitor(tmp_path)
    evaluate(instance, 1)
    expected = NOW + timedelta(minutes=10)
    result = evaluate(
        instance,
        2,
        expected=expected,
        observed=expected + timedelta(seconds=5),
        transition_id="liveness-transition-leap",
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.CONFIG_INVALID
    assert result.suspension.request_suspension is True
    instance.close()


def test_missing_cycle_must_advance_by_exact_expected_interval(tmp_path):
    instance = monitor(tmp_path)
    instance.evaluate(
        "runner-one", heartbeat=None, expected_cycle_timestamp=NOW,
        evaluation_time=NOW + timedelta(seconds=1), alert_delivery_confirmed=None,
        transition_id="liveness-transition-first-miss",
    )
    result = instance.evaluate(
        "runner-one", heartbeat=None,
        expected_cycle_timestamp=NOW + timedelta(seconds=30),
        evaluation_time=NOW + timedelta(minutes=1), alert_delivery_confirmed=None,
        transition_id="liveness-transition-bad-miss-grid",
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.CONFIG_INVALID
    instance.close()


def test_latched_suspension_cause_can_only_strengthen(tmp_path):
    instance = monitor(tmp_path)
    observed = NOW + timedelta(minutes=1, seconds=5)
    stale = evaluate(
        instance,
        2,
        expected=NOW + timedelta(minutes=1),
        observed=observed,
        evaluation_time=observed + timedelta(minutes=3),
        transition_id="liveness-transition-stale-first",
    )
    assert stale.suspension.cause is SuspensionCause.STALE_DATA
    regression = evaluate(
        instance,
        1,
        expected=NOW,
        transition_id="liveness-transition-stronger-regression",
    )
    assert regression.suspension.cause is SuspensionCause.INTERNAL_INVARIANT_FAILURE
    instance.close()


def test_integer_is_not_accepted_as_alert_delivery_evidence(tmp_path):
    instance = monitor(tmp_path)
    with pytest.raises(LivenessError, match="alert delivery"):
        evaluate(instance, alert_delivery_confirmed=1)
    instance.close()


def test_identical_duplicate_is_degraded_and_conflicting_duplicate_fails_closed(tmp_path):
    identical = monitor(tmp_path / "identical")
    evaluate(identical)
    duplicate = evaluate(identical, transition_id="liveness-transition-duplicate")
    assert duplicate.record.state is LivenessState.DEGRADED
    assert duplicate.record.reason is LivenessReason.DUPLICATE_HEARTBEAT
    identical.close()

    conflict = monitor(tmp_path / "conflict")
    evaluate(conflict)
    result = evaluate(
        conflict,
        observed=NOW + timedelta(seconds=6),
        transition_id="liveness-transition-conflict",
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.DUPLICATE_HEARTBEAT
    assert result.suspension.request_suspension is True
    conflict.close()


def test_duplicate_transition_is_never_released_or_reinterpreted(tmp_path):
    instance = monitor(tmp_path)
    evaluate(instance)
    with pytest.raises(LivenessError, match="already used"):
        evaluate(instance)
    with pytest.raises(LivenessError, match="already used"):
        evaluate(instance, observed=NOW + timedelta(seconds=6))
    instance.close()


def test_duplicate_transition_cannot_reuse_healthy_result_without_alert_evidence(tmp_path):
    instance = monitor(tmp_path)
    healthy = evaluate(instance, alert_delivery_confirmed=True)
    assert healthy.record.state is LivenessState.HEALTHY
    with pytest.raises(LivenessError, match="already used"):
        evaluate(instance, alert_delivery_confirmed=None)
    instance.close()


def test_expired_runner_is_stale_and_requires_suspension(tmp_path):
    instance = monitor(tmp_path)
    result = evaluate(instance, outcome=RunnerOutcome.EXPIRED)
    assert result.record.state is LivenessState.STALE
    assert result.record.reason is LivenessReason.HEARTBEAT_STALE
    assert result.suspension.cause is SuspensionCause.STALE_DATA
    instance.close()


@pytest.mark.parametrize(
    "outcome,state,reason,cause",
    [
        (
            RunnerOutcome.STOPPED_ON_AMBIGUITY,
            LivenessState.SUSPENSION_REQUIRED,
            LivenessReason.RUNNER_AMBIGUOUS,
            SuspensionCause.RESTART_AMBIGUITY,
        ),
        (
            RunnerOutcome.STOPPED_ON_FAILURE,
            LivenessState.FAILED_CLOSED,
            LivenessReason.RUNNER_FAILED,
            SuspensionCause.MONITORING_UNHEALTHY,
        ),
        (
            RunnerOutcome.FAILED_CLOSED,
            LivenessState.FAILED_CLOSED,
            LivenessReason.RUNNER_FAILED,
            SuspensionCause.MONITORING_UNHEALTHY,
        ),
        (
            RunnerOutcome.STOPPED_ON_SUSPENSION,
            LivenessState.FAILED_CLOSED,
            LivenessReason.RUNNER_FAILED,
            SuspensionCause.MONITORING_UNHEALTHY,
        ),
    ],
)
def test_runner_failure_and_ambiguity_require_suspension(
    tmp_path, outcome, state, reason, cause
):
    instance = monitor(tmp_path)
    result = evaluate(instance, outcome=outcome)
    assert result.record.state is state
    assert result.record.reason is reason
    assert result.suspension.cause is cause
    instance.close()


def test_malformed_heartbeat_fails_closed_without_arbitrary_dictionary_trust(tmp_path):
    instance = monitor(tmp_path)
    result = instance.evaluate(
        "runner-one", heartbeat={"runner_id": "runner-one"},
        expected_cycle_timestamp=NOW, evaluation_time=NOW + timedelta(seconds=1),
        alert_delivery_confirmed=True,
        transition_id="liveness-transition-malformed",
    )
    assert result.record.state is LivenessState.FAILED_CLOSED
    assert result.record.reason is LivenessReason.RECEIPT_INVALID
    instance.close()


def test_malformed_durable_state_latches_suspension_and_does_not_become_healthy_clear(tmp_path):
    instance = monitor(tmp_path)
    evaluate(instance)
    with sqlite3.connect(instance.database_path) as connection:
        connection.execute("UPDATE liveness_runs SET state='NOT_A_STATE' WHERE runner_id='runner-one'")
    result = evaluate(instance, 2)
    assert result.record.state is LivenessState.HEALTHY
    assert result.suspension.request_suspension is True
    assert result.suspension.cause is SuspensionCause.INTERNAL_INVARIANT_FAILURE
    instance.close()


def test_mutable_summary_cannot_clear_append_only_suspension_latch(tmp_path):
    instance = monitor(tmp_path)
    observed = NOW + timedelta(seconds=5)
    stale = evaluate(
        instance,
        evaluation_time=(
            observed + policy().maximum_observation_age + timedelta(microseconds=1)
        ),
    )
    assert stale.suspension.request_suspension is True
    with sqlite3.connect(instance.database_path) as connection:
        connection.execute(
            "UPDATE liveness_runs SET suspension_required=0, suspension_cause='NONE' "
            "WHERE runner_id='runner-one'"
        )
    expected = NOW + timedelta(minutes=1)
    recovered = evaluate(
        instance,
        2,
        expected=expected,
        observed=expected + timedelta(seconds=5),
        evaluation_time=(
            observed + policy().maximum_observation_age + timedelta(microseconds=2)
        ),
        transition_id="liveness-transition-after-clear-attempt",
    )
    assert recovered.record.state is LivenessState.HEALTHY
    assert recovered.suspension.request_suspension is True
    assert recovered.suspension.cause is SuspensionCause.INTERNAL_INVARIANT_FAILURE
    instance.close()


def test_persistence_schema_is_privacy_safe_and_restart_preserves_sequence(tmp_path):
    instance = monitor(tmp_path)
    first = evaluate(instance)
    instance.close()
    restarted = monitor(tmp_path)
    second = evaluate(restarted, 2)
    assert second.record.transition_sequence == first.record.transition_sequence + 1
    with sqlite3.connect(restarted.database_path) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {
            "liveness_meta", "liveness_runs", "heartbeat_records",
            "liveness_checkpoints",
        }
        for table in tables:
            columns = {row[1].casefold() for row in connection.execute(f"PRAGMA table_info({table})")}
            assert columns.isdisjoint({"account", "symbol", "quantity", "price", "balance", "exception", "credential", "username", "path", "manifest"})
    assert str(tmp_path).encode() not in restarted.database_path.read_bytes()
    restarted.close()


def test_suspension_request_can_never_clear_and_is_always_inert():
    with pytest.raises(LivenessError):
        LivenessSuspensionRequest(
            "runner-one", False, SuspensionCause.STALE_DATA,
            LivenessReason.HEARTBEAT_STALE, "liveness-transition-one",
        )
    with pytest.raises(LivenessError, match="never authorizes"):
        LivenessSuspensionRequest(
            "runner-one", True, SuspensionCause.STALE_DATA,
            LivenessReason.HEARTBEAT_STALE, "liveness-transition-one", True,
        )


def test_decision_true_automated_permission_is_impossible(tmp_path):
    instance = monitor(tmp_path)
    decision = evaluate(instance)
    with pytest.raises(LivenessError, match="never authorizes"):
        LivenessDecision(
            decision.record, decision.consecutive_missed_cycles,
            decision.suspension, True,
        )
    instance.close()


def test_public_decision_and_record_identity_binding_is_enforced(tmp_path):
    instance = monitor(tmp_path)
    decision = evaluate(instance)
    with pytest.raises(LivenessError, match="another record"):
        LivenessDecision(
            decision.record,
            decision.consecutive_missed_cycles,
            replace(decision.suspension, runner_id="runner-other"),
        )
    with pytest.raises(LivenessError, match="never authorizes"):
        replace(decision.record, permits_automated_action=True)
    instance.close()


def test_public_model_cannot_forge_healthy_without_heartbeat_or_unsafe_without_suspension(tmp_path):
    instance = monitor(tmp_path)
    healthy = evaluate(instance)
    with pytest.raises(LivenessError, match="requires heartbeat evidence"):
        replace(
            healthy.record,
            heartbeat_sequence=None,
            observed_completion_timestamp=None,
            runner_outcome=StoredRunnerOutcome.NO_RESULT,
        )
    stale_record = replace(
        healthy.record,
        state=LivenessState.STALE,
        reason=LivenessReason.HEARTBEAT_STALE,
    )
    no_suspension = LivenessSuspensionRequest(
        runner_id=stale_record.runner_id,
        request_suspension=False,
        cause=SuspensionCause.NONE,
        reason=stale_record.reason,
        transition_id=stale_record.transition_id,
    )
    with pytest.raises(LivenessError, match="requires suspension"):
        LivenessDecision(stale_record, 0, no_suspension)
    for unsafe_outcome in (
        StoredRunnerOutcome.STOPPED_ON_AMBIGUITY,
        StoredRunnerOutcome.STOPPED_ON_FAILURE,
        StoredRunnerOutcome.STOPPED_ON_SUSPENSION,
        StoredRunnerOutcome.FAILED_CLOSED,
        StoredRunnerOutcome.EXPIRED,
    ):
        with pytest.raises(LivenessError, match="conflicts with liveness projection"):
            replace(healthy.record, runner_outcome=unsafe_outcome)
    instance.close()


def test_every_state_reason_and_stored_runner_outcome_is_fixed():
    assert len(LivenessState) == 7
    assert len(LivenessReason) == 12
    assert {item.value for item in StoredRunnerOutcome} == {
        "NO_RESULT", *(item.value for item in RunnerOutcome)
    }


def test_no_runner_execution_clearance_broker_network_manifest_or_wall_clock_surface():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    calls = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            parts, current = [], node.func
            while isinstance(current, ast.Attribute):
                parts.append(current.attr); current = current.value
            if isinstance(current, ast.Name):
                parts.append(current.id)
            calls.add(".".join(reversed(parts)))
    assert calls.isdisjoint({
        "datetime.now", "datetime.utcnow", "time.time", "runner.execute",
        "controller.clear", "controller.apply_clearance", "controller.complete_review",
        "requests.get", "socket.socket",
    })
    source = MODULE_PATH.read_text(encoding="utf-8").casefold()
    assert "offlinecycledriver" not in source.replace("_", "")
    assert "authorization_manifest" not in source
    assert "robinhood" not in source


def test_global_safety_state_and_pinned_closure_remain_unchanged():
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "src/durable_runner_liveness.py" not in PINNED_IMPLEMENTATION_CLOSURE


def test_tracked_tree_contains_no_liveness_database():
    assert not any(
        path.suffix in {".sqlite", ".sqlite3", ".db"}
        for root in (pathlib.Path("src"), pathlib.Path("tests"))
        for path in root.glob("**/*")
    )
