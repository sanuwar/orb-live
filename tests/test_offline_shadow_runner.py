"""Persistence and adversarial tests for the finite offline shadow runner."""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

import src.offline_shadow_runner as runner_module
from src.attestation_authority import AttestationAuthority, ProducerIdentity
from src.durable_scheduler import DurableScheduler
from src.durable_suspension_controller import (
    DurableSuspensionController,
    SuspensionCause,
)
from src.exchange_calendar import EASTERN, classify
from src.execution_gates import (
    ACTIVATION_APPROVALS,
    EvidenceSource,
    GateDecision,
    GateMode,
    GateVerdict,
)
from src.offline_cycle_driver import (
    AttestationBundle,
    DriverOutcome,
    DriverProviders,
    DriverResult,
    DriverState,
    OfflineCycleDriver,
    ProviderKind,
    ProviderResult,
)
from src.offline_reconciliation import RecoveryOutcome, RecoveryResult
from src.offline_safety_orchestrator import (
    REQUIRED_CYCLE_ATTESTATIONS,
    OfflineSafetyOrchestrator,
)
from src.offline_shadow_runner import (
    ALLOWED_TRANSITIONS,
    BoundedOfflineShadowRunner,
    ReasonSetting,
    RunnerConfig,
    RunnerError,
    RunnerMode,
    RunnerOutcome,
    RunnerReason,
    RunnerResult,
    RunnerState,
    TickState,
    derive_driver_tick,
)
from src.operational_monitor import HeartbeatAssessment, HeartbeatStatus
from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE
from src.read_only_broker_state import approved_active_protection_states
from src.risk_limits import RiskAssessment, RiskDecision


MODULE_PATH = pathlib.Path(runner_module.__file__)
SESSION_DATE = date(2026, 8, 3)
NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
MONOTONIC = 1_000.0
FIXTURE_KEY = b"bounded-offline-runner-test-key-000000000"


def config(tmp_path, *, moments=None, **overrides):
    values = {
        "runner_id": "runner-one",
        "maximum_tick_count": 3,
        "tick_timestamps": tuple(moments if moments is not None else (NOW,)),
        "session_date": SESSION_DATE,
        "mode": RunnerMode.OFFLINE_SHADOW,
        "runner_database_path": (tmp_path / "runner.sqlite").resolve(),
        "driver_database_path": (tmp_path / "driver.sqlite").resolve(),
        "reason_setting": ReasonSetting.FIXED_CODES_ONLY,
    }
    values.update(overrides)
    return RunnerConfig(**values)


def authority():
    specs = {
        "risk-engine": ({"risk_within_limits"}, {EvidenceSource.MEASURED}),
        "recovery-probe": ({"recovery_consistent"}, {EvidenceSource.MEASURED}),
        "monitor": ({"monitoring_healthy"}, {EvidenceSource.MEASURED}),
        "alert-harness": ({"alerting_verified_reachable"}, {EvidenceSource.TESTED}),
        "kill-switch-probe": ({"kill_switch_disengaged"}, {EvidenceSource.MEASURED}),
        "calendar-probe": ({"market_session_valid"}, {EvidenceSource.MEASURED}),
        "operator-console": (
            {"protection_grounding_complete"},
            {EvidenceSource.OPERATOR_ATTESTED},
        ),
    }
    return AttestationAuthority(
        FIXTURE_KEY,
        {
            name: ProducerIdentity(name, frozenset(facts), frozenset(sources))
            for name, (facts, sources) in specs.items()
        },
    )


PRODUCERS = {
    "risk_within_limits": ("risk-engine", EvidenceSource.MEASURED),
    "recovery_consistent": ("recovery-probe", EvidenceSource.MEASURED),
    "monitoring_healthy": ("monitor", EvidenceSource.MEASURED),
    "alerting_verified_reachable": ("alert-harness", EvidenceSource.TESTED),
    "kill_switch_disengaged": ("kill-switch-probe", EvidenceSource.MEASURED),
    "market_session_valid": ("calendar-probe", EvidenceSource.MEASURED),
    "protection_grounding_complete": (
        "operator-console",
        EvidenceSource.OPERATOR_ATTESTED,
    ),
}


class StaticProvider:
    def __init__(self, kind, decision):
        self.kind = kind
        self.decision = decision
        self.calls = []

    def collect(self, context):
        self.calls.append(context.driver_id)
        return ProviderResult(
            kind=self.kind,
            driver_id=context.driver_id,
            cycle_id=context.cycle_id,
            scheduler_run_id=context.scheduler_run_id,
            controller_id=context.controller_id,
            observed_at=context.now,
            sequence=1,
            decision=self.decision,
        )


class RaisingProvider:
    def collect(self, context):
        raise RuntimeError("PRIVATE account-123 token-secret")


def providers(calendar_result=None, *, raising=False):
    auth = authority()
    signed = tuple(
        auth.issue(
            producer_id=PRODUCERS[fact][0],
            fact_name=fact,
            satisfied=True,
            source=PRODUCERS[fact][1],
            recorded_monotonic=MONOTONIC,
        )
        for fact in REQUIRED_CYCLE_ATTESTATIONS
    )
    decisions = {
        ProviderKind.CALENDAR: calendar_result
        or classify(datetime(2026, 8, 3, 5, 0, tzinfo=EASTERN)),
        ProviderKind.RECONCILIATION: RecoveryResult(RecoveryOutcome.NO_PENDING_WORK),
        ProviderKind.RISK: RiskAssessment(RiskDecision.WITHIN_LIMITS),
        ProviderKind.MONITORING: HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        ProviderKind.GATE: GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED,
            GateMode.SUPERVISED,
            evidence_verified=True,
        ),
        ProviderKind.ATTESTATIONS: AttestationBundle(signed, auth, MONOTONIC),
    }
    made = {kind: StaticProvider(kind, value) for kind, value in decisions.items()}
    if raising:
        made[ProviderKind.CALENDAR] = RaisingProvider()
    return DriverProviders(
        calendar=made[ProviderKind.CALENDAR],
        reconciliation=made[ProviderKind.RECONCILIATION],
        risk=made[ProviderKind.RISK],
        monitoring=made[ProviderKind.MONITORING],
        gate=made[ProviderKind.GATE],
        attestations=made[ProviderKind.ATTESTATIONS],
    )


def stack(tmp_path, *, moments=None, calendar_result=None, raising=False):
    cfg = config(tmp_path, moments=moments)
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    for index, stamp in enumerate(cfg.tick_timestamps):
        controller.register(derive_driver_tick(cfg, index).controller_id, now=stamp)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "orchestrator.sqlite", scheduler=scheduler, controller=controller
    )
    driver = OfflineCycleDriver(
        cfg.driver_database_path,
        scheduler=scheduler,
        controller=controller,
        orchestrator=orchestrator,
        providers=providers(calendar_result, raising=raising),
    )
    runner = BoundedOfflineShadowRunner(cfg, driver=driver)
    return cfg, runner, driver, orchestrator, scheduler, controller


def close_stack(items):
    for item in items[1:]:
        item.close()


def reopen(items):
    items[1].close()
    return BoundedOfflineShadowRunner(items[0], driver=items[2])


def start_first_tick(items):
    runner = items[1]
    runner._transition(RunnerState.VALIDATED, NOW)
    runner._transition(RunnerState.TICK_PENDING, NOW)
    runner._start_tick(runner.load_record(), runner.load_tick(0))


# Configuration and structural boundaries.


@pytest.mark.parametrize("maximum", [0, -1, True, 1_001])
def test_invalid_maximum_is_rejected(tmp_path, maximum):
    with pytest.raises(RunnerError, match="maximum tick count"):
        config(tmp_path, maximum_tick_count=maximum).validate()


def test_timestamps_cannot_exceed_maximum(tmp_path):
    cfg = config(tmp_path, moments=(NOW, NOW + timedelta(minutes=1)), maximum_tick_count=1)
    with pytest.raises(RunnerError, match="exceed"):
        cfg.validate()


def test_duplicate_and_out_of_order_timestamps_are_rejected(tmp_path):
    with pytest.raises(RunnerError, match="duplicate"):
        config(tmp_path, moments=(NOW, NOW)).validate()
    with pytest.raises(RunnerError, match="not ordered"):
        config(tmp_path, moments=(NOW + timedelta(minutes=1), NOW)).validate()


@pytest.mark.parametrize("value", [datetime(2026, 8, 3, 14), "2026-08-03T14:00:00Z", float("nan")])
def test_malformed_or_naive_timestamps_are_rejected(tmp_path, value):
    with pytest.raises(RunnerError):
        config(tmp_path, moments=(value,)).validate()


@pytest.mark.parametrize("runner_id", ["bad", "runner-account-123", "runner-user_name", "runner-a/b"])
def test_private_or_unconstrained_runner_identity_is_rejected(tmp_path, runner_id):
    with pytest.raises(RunnerError):
        config(tmp_path, runner_id=runner_id).validate()


def test_repository_relative_production_and_memory_paths_are_rejected(tmp_path):
    cases = (
        pathlib.Path("data/runner.sqlite"),
        pathlib.Path(":memory:"),
        (tmp_path / "production" / "runner.sqlite").resolve(),
        (pathlib.Path.cwd() / "outside-data.sqlite").resolve(),
    )
    for path in cases:
        with pytest.raises(RunnerError):
            config(tmp_path, runner_database_path=path).validate()


def test_config_is_frozen_and_paths_are_explicit(tmp_path):
    cfg = config(tmp_path)
    cfg.validate()
    with pytest.raises(Exception):
        cfg.maximum_tick_count = 99
    assert cfg.runner_database_path.is_absolute()
    assert cfg.driver_database_path.is_absolute()


def dotted_calls():
    calls = set()
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        parts, current = [], node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        calls.add(".".join(reversed(parts)))
    return calls


def test_no_unbounded_loop_sleep_thread_subprocess_wall_clock_or_scheduling():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    assert not any(isinstance(node, ast.While) for node in ast.walk(tree))
    calls = dotted_calls()
    forbidden = {
        "sleep", "time.sleep", "datetime.now", "datetime.utcnow", "time.time",
        "Thread", "threading.Thread", "subprocess.run", "subprocess.Popen",
        "scheduler.apply_transition", "scheduler.create_run",
        "scheduler.recover_on_restart", "controller.suspend",
        "controller.apply_clearance", "orchestrator.run_cycle",
    }
    assert calls.isdisjoint(forbidden)
    assert "range" in calls


def test_no_broker_network_manifest_or_private_collaborator_imports():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = {
        alias.name.casefold()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    forbidden = {"robinhood", "requests", "urllib", "socket", "mcp", "manifest", "broker"}
    assert not any(any(token in name for token in forbidden) for name in imported)
    private_driver_access = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Attribute)
        and isinstance(node.value.value, ast.Name)
        and node.value.value.id == "self"
        and node.value.attr == "driver"
        and node.attr.startswith("_")
    }
    assert private_driver_access == set()
    assert "OfflineCycleDriver" in {alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) for alias in node.names}


def test_new_module_is_outside_milestone_9_pinned_closure():
    assert "src/offline_shadow_runner.py" not in PINNED_IMPLEMENTATION_CLOSURE


# Bounded outcomes and real persistence.


def test_empty_schedule_completes_without_driver_call(tmp_path):
    items = stack(tmp_path, moments=())
    result = items[1].execute()
    assert result.state is RunnerState.COMPLETED
    assert result.outcome is RunnerOutcome.COMPLETED_BOUND
    assert result.reason is RunnerReason.EMPTY_SCHEDULE
    assert items[3].cycle_count == 0
    close_stack(items)


def test_one_closed_tick_completes_the_bound(tmp_path):
    items = stack(tmp_path)
    result = items[1].execute()
    assert result.outcome is RunnerOutcome.COMPLETED_BOUND
    assert result.completed_tick_count == 1
    assert items[3].cycle_count == 1
    assert items[1].load_tick(0).state is TickState.COMPLETED
    close_stack(items)


def test_multiple_ticks_run_once_each_in_order(tmp_path, monkeypatch):
    moments = (NOW, NOW + timedelta(minutes=1), NOW + timedelta(minutes=2))
    items = stack(tmp_path, moments=moments)
    calls = []
    original = items[2].run_tick

    def counted(driver_tick):
        calls.append(driver_tick.driver_id)
        return original(driver_tick)

    monkeypatch.setattr(items[2], "run_tick", counted)
    result = items[1].execute()
    assert result.completed_tick_count == 3
    assert items[3].cycle_count == 3
    assert calls == [derive_driver_tick(items[0], i).driver_id for i in range(3)]
    assert [items[1].load_tick(i).scheduled_at for i in range(3)] == list(moments)
    for index in range(3):
        identity = derive_driver_tick(items[0], index).driver_id
        assert items[2].load_record(identity).created_at == moments[index]
    close_stack(items)


def test_regular_session_stops_on_operator_review_and_never_calls_second_tick(tmp_path):
    moments = (NOW, NOW + timedelta(minutes=1))
    regular = classify(datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN))
    items = stack(tmp_path, moments=moments, calendar_result=regular)
    result = items[1].execute()
    assert result.outcome is RunnerOutcome.STOPPED_ON_REVIEW
    assert result.completed_tick_count == 1
    assert items[3].cycle_count == 1
    assert items[2].load_record(derive_driver_tick(items[0], 1).driver_id) is None
    close_stack(items)


def test_suspension_stops_all_later_ticks(tmp_path):
    moments = (NOW, NOW + timedelta(minutes=1))
    items = stack(tmp_path, moments=moments)
    first = derive_driver_tick(items[0], 0)
    items[5].suspend(
        first.controller_id,
        cause=SuspensionCause.OPERATOR_HALT,
        transition_id="suspend-runner-one",
        now=NOW,
    )
    result = items[1].execute()
    assert result.outcome is RunnerOutcome.STOPPED_ON_SUSPENSION
    assert items[3].cycle_count == 1
    assert items[2].load_record(derive_driver_tick(items[0], 1).driver_id) is None
    close_stack(items)


def test_provider_failure_stops_and_never_leaks_exception_text(tmp_path):
    items = stack(tmp_path, moments=(NOW, NOW + timedelta(minutes=1)), raising=True)
    result = items[1].execute()
    assert result.state is RunnerState.FAILED_CLOSED
    assert result.outcome is RunnerOutcome.STOPPED_ON_FAILURE
    assert items[3].cycle_count == 0
    assert b"PRIVATE" not in items[0].runner_database_path.read_bytes()
    assert b"account-123" not in items[0].runner_database_path.read_bytes()
    close_stack(items)


@pytest.mark.parametrize(
    "calendar_result,expected_state,expected_outcome,expected_reason",
    [
        (
            classify(datetime(2026, 8, 3, 5, 0, tzinfo=EASTERN)),
            RunnerState.COMPLETED,
            RunnerOutcome.COMPLETED_BOUND,
            RunnerReason.DRIVER_WAIT,
        ),
        (
            classify(datetime(2026, 8, 8, 12, 0, tzinfo=EASTERN)),
            RunnerState.COMPLETED,
            RunnerOutcome.COMPLETED_BOUND,
            RunnerReason.DRIVER_WAIT,
        ),
        (
            classify(datetime(2028, 8, 3, 10, 0, tzinfo=EASTERN), today=date(2028, 8, 3)),
            RunnerState.FAILED_CLOSED,
            RunnerOutcome.STOPPED_ON_FAILURE,
            RunnerReason.DRIVER_FAILED,
        ),
        (
            classify(datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN), today=date(2028, 1, 1)),
            RunnerState.FAILED_CLOSED,
            RunnerOutcome.STOPPED_ON_FAILURE,
            RunnerReason.DRIVER_FAILED,
        ),
    ],
)
def test_closed_unsupported_and_expired_session_fixtures_stop_safely(
    tmp_path, calendar_result, expected_state, expected_outcome, expected_reason
):
    items = stack(tmp_path, calendar_result=calendar_result)
    result = items[1].execute()
    assert result.permits_automated_action is False
    assert result.state is expected_state
    assert result.outcome is expected_outcome
    assert result.reason is expected_reason
    close_stack(items)


# Restart and identity binding.


def test_restart_before_first_tick_revalidates_and_completes(tmp_path):
    items = stack(tmp_path)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    assert replacement.execute().outcome is RunnerOutcome.COMPLETED_BOUND
    assert items[3].cycle_count == 1
    close_stack(items)


def test_restart_from_tick_pending_calls_driver_once(tmp_path):
    items = stack(tmp_path)
    items[1]._transition(RunnerState.VALIDATED, NOW)
    items[1]._transition(RunnerState.TICK_PENDING, NOW)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    assert replacement.execute().outcome is RunnerOutcome.COMPLETED_BOUND
    assert items[3].cycle_count == 1
    close_stack(items)


def test_restart_from_validated_state_resumes_before_first_tick(tmp_path):
    items = stack(tmp_path)
    items[1]._transition(RunnerState.VALIDATED, NOW)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    result = replacement.execute()
    assert result.outcome is RunnerOutcome.COMPLETED_BOUND
    assert items[3].cycle_count == 1
    close_stack(items)


def test_restart_during_driver_call_is_ambiguous_and_never_retries(tmp_path):
    items = stack(tmp_path)
    start_first_tick(items)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    first = replacement.execute()
    second = replacement.execute()
    assert first == second
    assert first.state is RunnerState.AMBIGUOUS_RESTART
    assert items[3].cycle_count == 0
    close_stack(items)


def test_driver_result_is_recovered_without_second_driver_call(tmp_path, monkeypatch):
    items = stack(tmp_path)
    start_first_tick(items)
    driver_tick = derive_driver_tick(items[0], 0)
    items[2].run_tick(driver_tick)
    assert items[3].cycle_count == 1
    def forbidden_second_call(_tick):
        raise AssertionError("driver tick repeated")

    monkeypatch.setattr(items[2], "run_tick", forbidden_second_call)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    result = replacement.execute()
    assert result.outcome is RunnerOutcome.COMPLETED_BOUND
    assert items[3].cycle_count == 1
    close_stack(items)


def test_restart_from_tick_completed_continues_without_repeating_tick(tmp_path):
    moments = (NOW, NOW + timedelta(minutes=1))
    items = stack(tmp_path, moments=moments)
    start_first_tick(items)
    driver_tick = derive_driver_tick(items[0], 0)
    driver_result = items[2].run_tick(driver_tick)
    items[1]._accept_driver_result(0, driver_tick, driver_result)
    assert items[1].load_record().state is RunnerState.TICK_COMPLETED
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    result = replacement.execute()
    assert result.completed_tick_count == 2
    assert items[3].cycle_count == 2
    close_stack(items)


def test_stop_request_intent_survives_restart(tmp_path, monkeypatch):
    regular = classify(datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN))
    items = stack(tmp_path, calendar_result=regular)
    runner = items[1]
    runner._transition(RunnerState.VALIDATED, NOW)
    runner._transition(RunnerState.TICK_PENDING, NOW)
    runner._start_tick(runner.load_record(), runner.load_tick(0))
    driver_tick = derive_driver_tick(items[0], 0)
    driver_result = items[2].run_tick(driver_tick)
    runner._accept_driver_result(0, driver_tick, driver_result)

    def crash_after_stop(*args, **kwargs):
        raise RunnerError("simulated process loss")

    monkeypatch.setattr(runner, "_terminal_record", crash_after_stop)
    with pytest.raises(RunnerError, match="simulated process loss"):
        runner._request_stop(
            RunnerOutcome.STOPPED_ON_REVIEW,
            RunnerReason.DRIVER_REVIEW_REQUIRED,
            NOW,
        )
    assert runner.load_record().state is RunnerState.STOP_REQUESTED
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    recovered = replacement.execute()
    assert recovered.state is RunnerState.COMPLETED
    assert recovered.outcome is RunnerOutcome.STOPPED_ON_REVIEW
    assert items[3].cycle_count == 1
    close_stack(items)


def test_wrong_driver_receipt_can_never_be_attached_on_restart(tmp_path):
    items = stack(tmp_path)
    start_first_tick(items)
    driver_tick = derive_driver_tick(items[0], 0)
    items[2].run_tick(driver_tick)
    with sqlite3.connect(items[0].driver_database_path) as connection:
        connection.execute(
            "UPDATE driver_receipts SET cycle_id='cycle-other' WHERE driver_id=?",
            (driver_tick.driver_id,),
        )
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    result = replacement.execute()
    assert result.state is RunnerState.AMBIGUOUS_RESTART
    assert result.outcome is RunnerOutcome.STOPPED_ON_AMBIGUITY
    assert items[3].cycle_count == 1
    close_stack(items)


def test_exact_expired_driver_evidence_stops_runner_as_expired(tmp_path):
    items = stack(tmp_path)
    start_first_tick(items)
    driver_tick = derive_driver_tick(items[0], 0)
    items[2]._create_record(driver_tick)
    expired_call = replace(driver_tick, now=driver_tick.expires_at + timedelta(seconds=1))
    driver_result = items[2].run_tick(expired_call)
    assert driver_result.state is DriverState.EXPIRED
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    result = replacement.execute()
    assert result.state is RunnerState.EXPIRED
    assert result.outcome is RunnerOutcome.EXPIRED
    assert items[3].cycle_count == 0
    close_stack(items)


def test_completed_and_failed_states_are_terminal_across_restart(tmp_path):
    completed = stack(tmp_path / "complete")
    first = completed[1].execute()
    replacement = reopen(completed)
    completed = (completed[0], replacement, *completed[2:])
    assert replacement.execute() == first
    assert completed[3].cycle_count == 1
    close_stack(completed)

    failed = stack(tmp_path / "failed", raising=True)
    first_failed = failed[1].execute()
    replacement_failed = reopen(failed)
    failed = (failed[0], replacement_failed, *failed[2:])
    assert replacement_failed.execute() == first_failed
    close_stack(failed)


def test_duplicate_runner_identity_with_other_schedule_is_rejected(tmp_path):
    items = stack(tmp_path)
    items[1].close()
    changed = replace(items[0], tick_timestamps=(NOW + timedelta(minutes=1),))
    with pytest.raises(RunnerError, match="another tick schedule"):
        BoundedOfflineShadowRunner(changed, driver=items[2])
    for item in items[2:]:
        item.close()


def test_runner_identity_is_bound_to_the_same_driver_store(tmp_path):
    original = stack(tmp_path / "original")
    original[1].close()
    other = stack(tmp_path / "other")
    other[1].close()
    changed = replace(
        original[0],
        driver_database_path=other[0].driver_database_path,
    )
    with pytest.raises(RunnerError, match="another driver store"):
        BoundedOfflineShadowRunner(changed, driver=other[2])
    for item in original[2:]:
        item.close()
    for item in other[2:]:
        item.close()


def test_separate_runner_store_cannot_attach_another_runners_driver_receipt(tmp_path):
    items = stack(tmp_path)
    first = items[1].execute()
    first_tick = derive_driver_tick(items[0], 0)
    items[1].close()
    second_config = replace(
        items[0], runner_database_path=(tmp_path / "runner-two.sqlite").resolve()
    )
    second_tick = derive_driver_tick(second_config, 0)
    assert second_tick.driver_id != first_tick.driver_id
    items[5].register(second_tick.controller_id, now=NOW)
    second_runner = BoundedOfflineShadowRunner(second_config, driver=items[2])
    second = second_runner.execute()
    assert first.outcome is second.outcome is RunnerOutcome.COMPLETED_BOUND
    assert items[3].cycle_count == 2
    assert items[2].load_receipt(first_tick.driver_id).driver_id == first_tick.driver_id
    assert items[2].load_receipt(second_tick.driver_id).driver_id == second_tick.driver_id
    second_runner.close()
    for item in items[2:]:
        item.close()


def test_corrupt_tick_identity_fails_closed_before_driver_call(tmp_path):
    items = stack(tmp_path)
    with sqlite3.connect(items[0].runner_database_path) as connection:
        connection.execute(
            "UPDATE runner_ticks SET driver_id='driver-other' WHERE runner_id=?",
            (items[0].runner_id,),
        )
    result = items[1].execute()
    assert result.state is RunnerState.FAILED_CLOSED
    assert result.outcome is RunnerOutcome.FAILED_CLOSED
    assert items[3].cycle_count == 0
    close_stack(items)


def test_forged_tick_completion_without_driver_evidence_fails_closed(tmp_path):
    items = stack(tmp_path)
    driver_id = derive_driver_tick(items[0], 0).driver_id
    with sqlite3.connect(items[0].runner_database_path) as connection:
        connection.execute(
            "UPDATE runner_runs SET state=?, next_tick_index=1, "
            "completed_tick_count=1, current_driver_id=? WHERE runner_id=?",
            (RunnerState.TICK_COMPLETED.value, driver_id, items[0].runner_id),
        )
        connection.execute(
            "UPDATE runner_ticks SET state=?, driver_record_sequence=6, "
            "driver_receipt_sequence=5, driver_state=?, driver_outcome=?, "
            "driver_reason=?, driver_receipt_at=? WHERE runner_id=? AND tick_index=0",
            (
                TickState.COMPLETED.value,
                DriverState.COMPLETED_NO_ACTION.value,
                DriverOutcome.WAIT.value,
                "ORCHESTRATOR_RESULT",
                NOW.isoformat(),
                items[0].runner_id,
            ),
        )
    result = items[1].execute()
    assert result.state is RunnerState.FAILED_CLOSED
    assert result.outcome is RunnerOutcome.FAILED_CLOSED
    assert items[2].load_record(driver_id) is None
    assert items[3].cycle_count == 0
    close_stack(items)


def test_forged_terminal_success_is_never_returned(tmp_path):
    items = stack(tmp_path)
    driver_id = derive_driver_tick(items[0], 0).driver_id
    with sqlite3.connect(items[0].runner_database_path) as connection:
        connection.execute(
            "UPDATE runner_runs SET state=?, outcome=?, reason=?, next_tick_index=1, "
            "completed_tick_count=1, current_driver_id=? WHERE runner_id=?",
            (
                RunnerState.COMPLETED.value,
                RunnerOutcome.COMPLETED_BOUND.value,
                RunnerReason.DRIVER_WAIT.value,
                driver_id,
                items[0].runner_id,
            ),
        )
        connection.execute(
            "UPDATE runner_ticks SET state=?, driver_record_sequence=6, "
            "driver_receipt_sequence=5, driver_state=?, driver_outcome=?, "
            "driver_reason=?, driver_receipt_at=? WHERE runner_id=? AND tick_index=0",
            (
                TickState.COMPLETED.value,
                DriverState.COMPLETED_NO_ACTION.value,
                DriverOutcome.WAIT.value,
                "ORCHESTRATOR_RESULT",
                NOW.isoformat(),
                items[0].runner_id,
            ),
        )
    with pytest.raises(RunnerError, match="cannot be concluded safely"):
        items[1].execute()
    assert items[2].load_record(driver_id) is None
    assert items[3].cycle_count == 0
    close_stack(items)


def test_wrong_current_driver_identity_is_never_returned(tmp_path):
    items = stack(tmp_path)
    items[1].execute()
    with sqlite3.connect(items[0].runner_database_path) as connection:
        connection.execute(
            "UPDATE runner_runs SET current_driver_id='driver-other' WHERE runner_id=?",
            (items[0].runner_id,),
        )
    with pytest.raises(RunnerError, match="cannot be concluded safely"):
        items[1].execute()
    close_stack(items)


def test_transition_journal_regression_is_never_accepted(tmp_path):
    items = stack(tmp_path)
    items[1].execute()
    with sqlite3.connect(items[0].runner_database_path) as connection:
        connection.execute(
            "UPDATE runner_transitions SET target_state=? WHERE runner_id=? "
            "AND sequence=1",
            (RunnerState.VALIDATED.value, items[0].runner_id),
        )
    with pytest.raises(RunnerError, match="cannot be concluded safely"):
        items[1].execute()
    close_stack(items)


def test_derived_tick_identities_are_unique_and_stable(tmp_path):
    cfg = config(tmp_path, moments=(NOW, NOW + timedelta(minutes=1)))
    first = derive_driver_tick(cfg, 0)
    assert first == derive_driver_tick(cfg, 0)
    assert first.driver_id != derive_driver_tick(cfg, 1).driver_id
    identities = {
        first.driver_id,
        first.cycle_id,
        first.scheduler_run_id,
        first.controller_id,
        first.driver_transition_id,
        first.scheduler_transition_id,
        first.controller_transition_id,
        first.scheduler_restart_transition_id,
    }
    assert len(identities) == 8


# Model, privacy, and durable schema.


def test_transition_matrix_names_every_state_and_terminal_states_are_final():
    assert set(ALLOWED_TRANSITIONS) == set(RunnerState)
    for state in (
        RunnerState.COMPLETED,
        RunnerState.FAILED_CLOSED,
        RunnerState.AMBIGUOUS_RESTART,
        RunnerState.EXPIRED,
    ):
        assert ALLOWED_TRANSITIONS[state] == frozenset()
    reached = {target for targets in ALLOWED_TRANSITIONS.values() for target in targets}
    assert set(RunnerState) - {RunnerState.CREATED} <= reached


@pytest.mark.parametrize(
    "state,outcome,reason",
    [
        (RunnerState.COMPLETED, RunnerOutcome.COMPLETED_BOUND, RunnerReason.BOUND_EXHAUSTED),
        (RunnerState.COMPLETED, RunnerOutcome.STOPPED_ON_REVIEW, RunnerReason.DRIVER_REVIEW_REQUIRED),
        (RunnerState.COMPLETED, RunnerOutcome.STOPPED_ON_SUSPENSION, RunnerReason.DRIVER_SUSPENDED),
        (RunnerState.FAILED_CLOSED, RunnerOutcome.STOPPED_ON_FAILURE, RunnerReason.DRIVER_FAILED),
        (RunnerState.AMBIGUOUS_RESTART, RunnerOutcome.STOPPED_ON_AMBIGUITY, RunnerReason.DRIVER_AMBIGUOUS),
        (RunnerState.EXPIRED, RunnerOutcome.EXPIRED, RunnerReason.DRIVER_EXPIRED),
        (RunnerState.FAILED_CLOSED, RunnerOutcome.FAILED_CLOSED, RunnerReason.DURABLE_STATE_INVALID),
    ],
)
def test_every_outcome_is_permanently_inert(state, outcome, reason):
    result = RunnerResult(
        runner_id="runner-one",
        state=state,
        outcome=outcome,
        reason=reason,
        completed_tick_count=0,
        next_tick_index=0,
        transition_sequence=1,
        current_driver_id=None,
    )
    assert result.permits_automated_action is False
    assert result.sanitized_facts()["permits_automated_action"] == "false"


def test_true_automated_permission_is_impossible():
    with pytest.raises(RunnerError, match="never authorizes"):
        RunnerResult(
            "runner-one",
            RunnerState.COMPLETED,
            RunnerOutcome.COMPLETED_BOUND,
            RunnerReason.BOUND_EXHAUSTED,
            0,
            0,
            1,
            None,
            True,
        )


def test_conflicting_result_state_and_outcome_is_rejected():
    with pytest.raises(RunnerError, match="conflict"):
        RunnerResult(
            "runner-one",
            RunnerState.COMPLETED,
            RunnerOutcome.FAILED_CLOSED,
            RunnerReason.DURABLE_STATE_INVALID,
            0,
            0,
            1,
            None,
        )


@pytest.mark.parametrize(
    "field,value",
    [
        ("completed_tick_count", -1),
        ("next_tick_index", -1),
        ("transition_sequence", 0),
        ("current_driver_id", "driver-private/path"),
    ],
)
def test_malformed_result_counters_and_identity_are_rejected(field, value):
    values = {
        "runner_id": "runner-one",
        "state": RunnerState.COMPLETED,
        "outcome": RunnerOutcome.COMPLETED_BOUND,
        "reason": RunnerReason.BOUND_EXHAUSTED,
        "completed_tick_count": 0,
        "next_tick_index": 0,
        "transition_sequence": 1,
        "current_driver_id": None,
    }
    values[field] = value
    with pytest.raises(RunnerError):
        RunnerResult(**values)


def test_schema_persists_only_allowlisted_privacy_safe_fields(tmp_path):
    items = stack(tmp_path)
    items[1].execute()
    with sqlite3.connect(items[0].runner_database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert tables == {"runner_meta", "runner_runs", "runner_ticks", "runner_transitions"}
        for table in tables:
            columns = {
                row[1].casefold() for row in connection.execute(f"PRAGMA table_info({table})")
            }
            assert columns.isdisjoint(
                {"account", "symbol", "quantity", "price", "balance", "exception", "path", "username", "manifest"}
            )
    content = items[0].runner_database_path.read_bytes()
    assert str(tmp_path).encode() not in content
    close_stack(items)


def test_transition_identities_and_counts_survive_restart(tmp_path):
    items = stack(tmp_path, moments=(NOW, NOW + timedelta(minutes=1)))
    result = items[1].execute()
    before = items[1].load_record()
    with sqlite3.connect(items[0].runner_database_path) as connection:
        transitions = connection.execute(
            "SELECT transition_id, sequence FROM runner_transitions "
            "WHERE runner_id=? ORDER BY sequence",
            (items[0].runner_id,),
        ).fetchall()
    assert len(transitions) == before.transition_sequence
    assert len({row[0] for row in transitions}) == len(transitions)
    replacement = reopen(items)
    items = (items[0], replacement, *items[2:])
    assert replacement.execute() == result
    assert replacement.load_record().transition_sequence == before.transition_sequence
    close_stack(items)


def test_strategy_activation_and_protection_remain_inert():
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "src/offline_shadow_runner.py" not in PINNED_IMPLEMENTATION_CLOSURE


def test_tracked_tree_contains_no_runner_database_artifact():
    assert not any(
        path.suffix in {".sqlite", ".sqlite3", ".db"}
        for root in (pathlib.Path("src"), pathlib.Path("tests"))
        for path in root.glob("**/*")
    )
