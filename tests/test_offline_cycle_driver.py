"""The offline cycle driver is durable orchestration, never execution.

All SQLite files in this module are created below ``tmp_path``.  The providers
return existing domain decision types, and the signing key is test-only.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

import src.offline_cycle_driver as driver_module
from src.attestation_authority import AttestationAuthority, ProducerIdentity
from src.durable_scheduler import DurableScheduler, RunState, SchedulerAction
from src.durable_suspension_controller import (
    ControllerState,
    DurableSuspensionController,
    SuspensionCause,
)
from src.exchange_calendar import EASTERN, SessionClassification, classify
from src.execution_gates import (
    ACTIVATION_APPROVALS,
    EvidenceSource,
    GateDecision,
    GateMode,
    GateVerdict,
)
from src.offline_cycle_driver import (
    ALLOWED_TRANSITIONS,
    AttestationBundle,
    DriverError,
    DriverOutcome,
    DriverProviders,
    DriverReason,
    DriverReceipt,
    DriverResult,
    DriverState,
    DriverTick,
    OfflineCycleDriver,
    ProviderKind,
    ProviderResult,
    is_repository_data_path,
)
from src.offline_reconciliation import RecoveryOutcome, RecoveryResult
from src.offline_safety_orchestrator import (
    REQUIRED_CYCLE_ATTESTATIONS,
    CycleInputs,
    CycleOutcome,
    CycleReason,
    OfflineSafetyOrchestrator,
)
from src.operational_monitor import HeartbeatAssessment, HeartbeatStatus
from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE
from src.read_only_broker_state import approved_active_protection_states
from src.risk_limits import RiskAssessment, RiskDecision


MODULE_PATH = pathlib.Path(driver_module.__file__)
NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 8, 3)
MONOTONIC = 1_000.0
FIXTURE_KEY = b"offline-cycle-driver-test-key-000000000"


def authority() -> AttestationAuthority:
    identities = {
        "risk-engine": ({"risk_within_limits"}, {EvidenceSource.MEASURED}),
        "recovery-probe": ({"recovery_consistent"}, {EvidenceSource.MEASURED}),
        "monitor": ({"monitoring_healthy"}, {EvidenceSource.MEASURED}),
        "alert-harness": (
            {"alerting_verified_reachable"},
            {EvidenceSource.TESTED},
        ),
        "kill-switch-probe": (
            {"kill_switch_disengaged"},
            {EvidenceSource.MEASURED},
        ),
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
            for name, (facts, sources) in identities.items()
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


def attestations(auth: AttestationAuthority):
    return tuple(
        auth.issue(
            producer_id=PRODUCERS[fact][0],
            fact_name=fact,
            satisfied=True,
            source=PRODUCERS[fact][1],
            recorded_monotonic=MONOTONIC,
        )
        for fact in REQUIRED_CYCLE_ATTESTATIONS
    )


def closed_gate() -> GateDecision:
    return GateDecision(
        GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED,
        GateMode.SUPERVISED,
        evidence_verified=True,
    )


def tick(**overrides) -> DriverTick:
    values = {
        "driver_id": "driver-one",
        "cycle_id": "cycle-one",
        "scheduler_run_id": "scheduler-one",
        "controller_id": "controller-one",
        "driver_transition_id": "driver-transition-one",
        "scheduler_transition_id": "scheduler-transition-one",
        "controller_transition_id": "controller-transition-one",
        "scheduler_restart_transition_id": "scheduler-restart-one",
        "session_date": SESSION_DATE,
        "now": NOW,
        "expires_at": NOW + timedelta(hours=1),
    }
    values.update(overrides)
    return DriverTick(**values)


class StaticProvider:
    def __init__(self, kind, decision, *, mutate=None):
        self.kind = kind
        self.decision = decision
        self.mutate = mutate or {}
        self.calls = 0

    def collect(self, context):
        self.calls += 1
        values = {
            "kind": self.kind,
            "driver_id": context.driver_id,
            "cycle_id": context.cycle_id,
            "scheduler_run_id": context.scheduler_run_id,
            "controller_id": context.controller_id,
            "observed_at": context.now,
            "sequence": 1,
            "decision": self.decision,
        }
        values.update(self.mutate)
        return ProviderResult(**values)


class RaisingProvider:
    def collect(self, context):
        raise RuntimeError("PRIVATE provider failure account-123 token-secret")


class RawProvider:
    def __init__(self, value):
        self.value = value

    def collect(self, context):
        return self.value


def provider_set(*, auth=None, overrides=None):
    auth = auth or authority()
    decisions = {
        ProviderKind.CALENDAR: classify(
            datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN)
        ),
        ProviderKind.RECONCILIATION: RecoveryResult(
            RecoveryOutcome.NO_PENDING_WORK
        ),
        ProviderKind.RISK: RiskAssessment(RiskDecision.WITHIN_LIMITS),
        ProviderKind.MONITORING: HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        ProviderKind.GATE: closed_gate(),
        ProviderKind.ATTESTATIONS: AttestationBundle(
            attestations(auth), auth, MONOTONIC
        ),
    }
    decisions.update(overrides or {})
    made = {kind: StaticProvider(kind, value) for kind, value in decisions.items()}
    return DriverProviders(
        calendar=made[ProviderKind.CALENDAR],
        reconciliation=made[ProviderKind.RECONCILIATION],
        risk=made[ProviderKind.RISK],
        monitoring=made[ProviderKind.MONITORING],
        gate=made[ProviderKind.GATE],
        attestations=made[ProviderKind.ATTESTATIONS],
    )


def stack(tmp_path, *, providers=None, create_scheduler=False):
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    controller.register("controller-one", now=NOW)
    if create_scheduler:
        scheduler.create_run("scheduler-one", session_date=SESSION_DATE, now=NOW)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "orchestrator.sqlite",
        scheduler=scheduler,
        controller=controller,
    )
    driver = OfflineCycleDriver(
        tmp_path / "driver.sqlite",
        scheduler=scheduler,
        controller=controller,
        orchestrator=orchestrator,
        providers=providers or provider_set(),
    )
    return driver, orchestrator, scheduler, controller


def close_stack(items):
    for item in items:
        item.close()


def set_state(driver, state, *, outcome=None, reason=None, orch_outcome=None, orch_reason=None):
    with sqlite3.connect(driver.database_path) as connection:
        connection.execute(
            "UPDATE driver_runs SET state=?, outcome=COALESCE(?, outcome), "
            "reason=COALESCE(?, reason), "
            "orchestrator_outcome=COALESCE(?, orchestrator_outcome), "
            "orchestrator_reason=COALESCE(?, orchestrator_reason) WHERE driver_id=?",
            (
                state.value,
                outcome.value if outcome else None,
                reason.value if reason else None,
                orch_outcome.value if orch_outcome else None,
                orch_reason.value if orch_reason else None,
                "driver-one",
            ),
        )


def orchestrator_inputs(auth, *, suspension, **overrides):
    values = {
        "cycle_id": "cycle-one",
        "scheduler_run_id": "scheduler-one",
        "controller_id": "controller-one",
        "now": NOW,
        "scheduler_transition_id": "scheduler-transition-one",
        "controller_transition_id": "controller-transition-one",
        "session": classify(datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN)),
        "recovery": RecoveryResult(RecoveryOutcome.NO_PENDING_WORK),
        "risk": RiskAssessment(RiskDecision.WITHIN_LIMITS),
        "monitoring": HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        "gate": closed_gate(),
        "suspension": suspension,
        "attestations": {item.fact_name: item for item in attestations(auth)},
        "authority": auth,
        "attestation_monotonic": MONOTONIC,
    }
    values.update(overrides)
    return CycleInputs(**values)


# ---------------------------------------------------------------------------
# Construction and structural safety.
# ---------------------------------------------------------------------------


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


def test_no_broker_network_or_pinned_runtime_imports():
    forbidden = (
        "robinhood",
        "mcp",
        "broker",
        "socket",
        "http",
        "urllib",
        "requests",
        "manifest",
        "checkpoint",
        "commissioning",
    )
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    assert not any(word in name.lower() for name in imported for word in forbidden)
    assert MODULE_PATH.relative_to(MODULE_PATH.parents[1]).as_posix() not in (
        PINNED_IMPLEMENTATION_CLOSURE
    )


def test_no_wall_clock_loop_thread_sleep_or_scheduler_bypass_calls():
    forbidden = {
        "datetime.now",
        "datetime.utcnow",
        "date.today",
        "time.time",
        "time.monotonic",
        "time.sleep",
        "threading.Thread",
        "scheduler.apply_transition",
        "controller.suspend",
        "controller.apply_clearance",
        "controller.complete_review",
        "orchestrator.recover_cycle",
    }
    assert dotted_calls() & forbidden == set()


def test_no_clearance_order_activation_or_private_storage_references():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for forbidden in (
        "clear_suspension",
        "apply_clearance",
        "complete_review",
        "ACTIVATION_APPROVALS",
        "place_equity_order",
        "cancel_equity_order",
        "account_number",
        "local.yaml",
        "_connection.execute(\"UPDATE scheduled_runs",
    ):
        assert forbidden not in source


def test_strategy_approvals_and_protection_are_still_inert():
    config = pathlib.Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
    assert "enabled: false" in config
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()


@pytest.mark.parametrize("outcome", list(DriverOutcome))
def test_every_result_outcome_is_inert(outcome):
    result = DriverResult(DriverState.COMPLETED_NO_ACTION, outcome, DriverReason.ALREADY_COMPLETED)
    assert result.permits_automated_action is False
    assert result.sanitized_facts()["permits_automated_action"] == "false"


def test_true_action_permission_is_impossible():
    for value in (True, None, 0, "false"):
        with pytest.raises(DriverError):
            DriverResult(
                DriverState.COMPLETED_NO_ACTION,
                DriverOutcome.OFFLINE_COMPLETE,
                DriverReason.ORCHESTRATOR_RESULT,
                permits_automated_action=value,
            )
    with pytest.raises(DriverError):
        DriverReceipt(
            "driver-one",
            "cycle-one",
            "scheduler-one",
            "controller-one",
            DriverOutcome.OFFLINE_COMPLETE,
            DriverReason.ORCHESTRATOR_RESULT,
            CycleOutcome.OFFLINE_CHECKS_COMPLETE,
            CycleReason.OK,
            1,
            NOW,
            True,
        )


def test_database_under_repository_data_is_refused(tmp_path):
    providers = provider_set()
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    controller.register("controller-one", now=NOW)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "orchestrator.sqlite", scheduler=scheduler, controller=controller
    )
    with pytest.raises(DriverError, match="repository data"):
        OfflineCycleDriver(
            pathlib.Path("data/driver.sqlite"),
            scheduler=scheduler,
            controller=controller,
            orchestrator=orchestrator,
            providers=providers,
        )
    assert is_repository_data_path(pathlib.Path("data/driver.sqlite"))
    orchestrator.close(); scheduler.close(); controller.close()


def test_driver_rejects_orchestrator_wired_to_other_durable_stores(tmp_path):
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    other_scheduler = DurableScheduler(tmp_path / "other-scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    controller.register("controller-one", now=NOW)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "orchestrator.sqlite",
        scheduler=other_scheduler,
        controller=controller,
    )
    with pytest.raises(DriverError, match="do not match"):
        OfflineCycleDriver(
            tmp_path / "driver.sqlite",
            scheduler=scheduler,
            controller=controller,
            orchestrator=orchestrator,
            providers=provider_set(),
        )
    orchestrator.close(); other_scheduler.close(); scheduler.close(); controller.close()


def test_all_test_database_paths_are_tmp_path(tmp_path):
    items = stack(tmp_path)
    assert all(str(tmp_path) in str(item.database_path) for item in (items[0], items[2], items[3]))
    close_stack(items)


# ---------------------------------------------------------------------------
# State machine, normal outcomes, and dominance.
# ---------------------------------------------------------------------------


def test_transition_matrix_names_every_state_and_terminals_are_final():
    assert set(ALLOWED_TRANSITIONS) == set(DriverState)
    for state in (
        DriverState.COMPLETED_NO_ACTION,
        DriverState.FAILED_CLOSED,
        DriverState.AMBIGUOUS_RESTART,
        DriverState.EXPIRED,
    ):
        assert ALLOWED_TRANSITIONS[state] == frozenset()


@pytest.mark.parametrize(
    "moment,expected_outcome,expected_reason",
    [
        (datetime(2026, 8, 3, 5, 0, tzinfo=EASTERN), DriverOutcome.WAIT, CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 8, 3, 8, 0, tzinfo=EASTERN), DriverOutcome.WAIT, CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN), DriverOutcome.OPERATOR_REVIEW_REQUIRED, CycleReason.GATE_CLOSED),
        (datetime(2026, 8, 3, 18, 0, tzinfo=EASTERN), DriverOutcome.WAIT, CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2025, 8, 4, 10, 0, tzinfo=EASTERN), DriverOutcome.FAILED_CLOSED, CycleReason.CALENDAR_UNUSABLE),
        (datetime(2028, 8, 7, 10, 0, tzinfo=EASTERN), DriverOutcome.FAILED_CLOSED, CycleReason.CALENDAR_UNUSABLE),
    ],
)
def test_calendar_cases(tmp_path, moment, expected_outcome, expected_reason):
    providers = provider_set(overrides={ProviderKind.CALENDAR: classify(moment, today=SESSION_DATE)})
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.outcome is expected_outcome
    assert result.orchestrator_reason is expected_reason
    assert result.state is (
        DriverState.FAILED_CLOSED
        if expected_outcome is DriverOutcome.FAILED_CLOSED
        else DriverState.COMPLETED_NO_ACTION
    )
    close_stack(items)


@pytest.mark.parametrize(
    "kind,decision,reason",
    [
        (ProviderKind.RISK, RiskAssessment(RiskDecision.DENIED_POSITION_SIZE), CycleReason.RISK_DENIED),
        (ProviderKind.RECONCILIATION, RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_CORRUPT), CycleReason.RECONCILIATION_UNRESOLVED),
        (ProviderKind.MONITORING, HeartbeatAssessment(HeartbeatStatus.MISSING, 3), CycleReason.MONITORING_UNHEALTHY),
    ],
)
def test_safety_denials_suspend(tmp_path, kind, decision, reason):
    items = stack(tmp_path, providers=provider_set(overrides={kind: decision}))
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.SUSPENDED
    assert result.orchestrator_reason is reason
    assert items[3].load("controller-one").state is ControllerState.SUSPENDED
    close_stack(items)


def test_missing_alert_delivery_is_not_healthy(tmp_path):
    auth = authority()
    signed = tuple(
        item for item in attestations(auth) if item.fact_name != "alerting_verified_reachable"
    )
    providers = provider_set(
        auth=auth,
        overrides={ProviderKind.ATTESTATIONS: AttestationBundle(signed, auth, MONOTONIC)},
    )
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.SUSPENDED
    assert result.orchestrator_reason is CycleReason.ALERT_DELIVERY_UNCONFIRMED
    close_stack(items)


def test_missing_non_alert_attestation_requires_review(tmp_path):
    auth = authority()
    signed = tuple(item for item in attestations(auth) if item.fact_name != "market_session_valid")
    items = stack(
        tmp_path,
        providers=provider_set(
            auth=auth,
            overrides={ProviderKind.ATTESTATIONS: AttestationBundle(signed, auth, MONOTONIC)},
        ),
    )
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.OPERATOR_REVIEW_REQUIRED
    assert result.orchestrator_reason is CycleReason.ATTESTATION_MISSING
    close_stack(items)


@pytest.mark.parametrize("permanent", [False, True])
def test_active_suspension_dominates_favorable_inputs(tmp_path, permanent):
    items = stack(tmp_path)
    if permanent:
        items[3].escalate_to_permanent_halt(
            "controller-one", transition_id="halt-one", now=NOW
        )
    else:
        items[3].suspend(
            "controller-one",
            cause=SuspensionCause.OPERATOR_HALT,
            transition_id="halt-one",
            now=NOW,
        )
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.SUSPENDED
    assert result.orchestrator_reason in {CycleReason.ACTIVE_SUSPENSION, CycleReason.PERMANENT_HALT}
    close_stack(items)


def test_gate_closed_remains_closed_and_receipt_is_fixed(tmp_path):
    items = stack(tmp_path)
    result = items[0].run_tick(tick())
    receipt = items[0].load_receipt("driver-one")
    assert result == DriverResult(
        DriverState.COMPLETED_NO_ACTION,
        DriverOutcome.OPERATOR_REVIEW_REQUIRED,
        DriverReason.ORCHESTRATOR_RESULT,
        CycleOutcome.REQUIRE_OPERATOR_REVIEW,
        CycleReason.GATE_CLOSED,
    )
    assert receipt is not None and receipt.permits_automated_action is False
    assert items[1].cycle_count == 1
    close_stack(items)


def test_expired_driver_tick_never_collects_or_calls_orchestrator(tmp_path):
    providers = provider_set()
    items = stack(tmp_path, providers=providers)
    expired = tick(now=NOW + timedelta(hours=2), expires_at=NOW + timedelta(hours=1))
    result = items[0].run_tick(expired)
    assert result.state is DriverState.EXPIRED
    assert result.outcome is DriverOutcome.COMPLETED_NO_ACTION
    assert items[1].cycle_count == 0
    assert all(provider.calls == 0 for provider in providers.__dict__.values())
    close_stack(items)


# ---------------------------------------------------------------------------
# Provider contracts and privacy.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("raw", [None, True, False, {}, {"approved": True}, object()])
def test_arbitrary_missing_and_boolean_provider_results_fail_closed(tmp_path, raw):
    providers = provider_set()
    object.__setattr__(providers, "risk", RawProvider(raw))
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert result.reason is DriverReason.INVALID_PROVIDER_RESULT
    assert items[1].cycle_count == 0
    close_stack(items)


@pytest.mark.parametrize(
    "mutation",
    [
        {"driver_id": "driver-wrong"},
        {"cycle_id": "cycle-wrong"},
        {"scheduler_run_id": "scheduler-wrong"},
        {"controller_id": "controller-wrong"},
        {"kind": ProviderKind.CALENDAR},
        {"observed_at": NOW - timedelta(minutes=6)},
        {"observed_at": NOW + timedelta(seconds=1)},
        {"sequence": 0},
        {"sequence": True},
        {"decision": True},
        {"decision": {}},
    ],
)
def test_malformed_stale_future_and_wrong_identity_results_fail_closed(tmp_path, mutation):
    providers = provider_set()
    object.__setattr__(
        providers,
        "risk",
        StaticProvider(ProviderKind.RISK, RiskAssessment(RiskDecision.WITHIN_LIMITS), mutate=mutation),
    )
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert result.reason is DriverReason.INVALID_PROVIDER_RESULT
    close_stack(items)


def test_provider_exception_text_is_never_persisted(tmp_path):
    providers = provider_set()
    object.__setattr__(providers, "risk", RaisingProvider())
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert result.reason is DriverReason.PROVIDER_FAILURE
    items[0].close()
    content = (tmp_path / "driver.sqlite").read_bytes()
    assert b"PRIVATE" not in content
    assert b"account-123" not in content
    assert b"token-secret" not in content
    items[1].close(); items[2].close(); items[3].close()


def test_duplicate_provider_objects_are_rejected(tmp_path):
    providers = provider_set()
    duplicate = providers.calendar
    providers = replace(providers, reconciliation=duplicate)
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    controller.register("controller-one", now=NOW)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "orchestrator.sqlite", scheduler=scheduler, controller=controller
    )
    with pytest.raises(DriverError, match="duplicate provider"):
        OfflineCycleDriver(
            tmp_path / "driver.sqlite",
            scheduler=scheduler,
            controller=controller,
            orchestrator=orchestrator,
            providers=providers,
        )
    orchestrator.close(); scheduler.close(); controller.close()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_attestation_observation_is_malformed(tmp_path, value):
    auth = authority()
    items = stack(
        tmp_path,
        providers=provider_set(
            auth=auth,
            overrides={
                ProviderKind.ATTESTATIONS: AttestationBundle(
                    attestations(auth), auth, value
                )
            },
        ),
    )
    result = items[0].run_tick(tick())
    assert result.reason is DriverReason.INVALID_PROVIDER_RESULT
    assert items[1].cycle_count == 0
    close_stack(items)


def test_duplicate_attestation_submission_fails_closed(tmp_path):
    auth = authority()
    signed = attestations(auth)
    bundle = AttestationBundle(signed + (signed[0],), auth, MONOTONIC)
    items = stack(
        tmp_path,
        providers=provider_set(auth=auth, overrides={ProviderKind.ATTESTATIONS: bundle}),
    )
    result = items[0].run_tick(tick())
    assert result.reason is DriverReason.INVALID_PROVIDER_RESULT
    assert items[1].cycle_count == 0
    close_stack(items)


def test_persisted_schema_contains_only_allowlisted_projection(tmp_path):
    items = stack(tmp_path)
    items[0].run_tick(tick())
    with sqlite3.connect(tmp_path / "driver.sqlite") as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"driver_meta", "driver_runs", "spent_driver_identities", "driver_receipts"}
        for table in tables - {"driver_meta"}:
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            forbidden = {"account", "token", "credential", "path", "exception", "alert", "payload", "financial", "username"}
            assert not any(word in column.lower() for column in columns for word in forbidden)
    close_stack(items)


# ---------------------------------------------------------------------------
# Identity spending and durable-state validation.
# ---------------------------------------------------------------------------


def test_duplicate_driver_identity_with_other_cycle_is_rejected(tmp_path):
    items = stack(tmp_path)
    items[0].run_tick(tick())
    with pytest.raises(DriverError, match="another tick"):
        items[0].run_tick(tick(cycle_id="cycle-two"))
    assert items[1].cycle_count == 1
    close_stack(items)


def test_duplicate_cycle_identity_across_drivers_is_rejected(tmp_path):
    items = stack(tmp_path)
    items[0].run_tick(tick())
    with pytest.raises(DriverError, match="already spent"):
        items[0].run_tick(
            tick(
                driver_id="driver-two",
                driver_transition_id="driver-transition-two",
                scheduler_transition_id="scheduler-transition-two",
                controller_transition_id="controller-transition-two",
                scheduler_restart_transition_id="scheduler-restart-two",
            )
        )
    close_stack(items)


@pytest.mark.parametrize(
    "field",
    [
        "driver_transition_id",
        "scheduler_transition_id",
        "controller_transition_id",
        "scheduler_restart_transition_id",
    ],
)
def test_transition_identity_is_permanently_spent(tmp_path, field):
    items = stack(tmp_path)
    first = tick()
    items[0].run_tick(first)
    values = {
        "driver_id": "driver-two",
        "cycle_id": "cycle-two",
        "scheduler_run_id": "scheduler-two",
        "controller_id": "controller-one",
        "driver_transition_id": "driver-transition-two",
        "scheduler_transition_id": "scheduler-transition-two",
        "controller_transition_id": "controller-transition-two",
        "scheduler_restart_transition_id": "scheduler-restart-two",
        field: getattr(first, field),
    }
    with pytest.raises(DriverError, match="already spent"):
        items[0].run_tick(tick(**values))
    close_stack(items)


def test_wrong_controller_identity_fails_closed(tmp_path):
    items = stack(tmp_path)
    result = items[0].run_tick(tick(controller_id="controller-wrong"))
    assert result.reason is DriverReason.DURABLE_STATE_INVALID
    assert items[1].cycle_count == 0
    close_stack(items)


def test_existing_driver_rejects_a_wrong_scheduler_identity(tmp_path):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    with pytest.raises(DriverError, match="another tick"):
        items[0].run_tick(tick(scheduler_run_id="scheduler-wrong"))
    assert items[1].cycle_count == 0
    close_stack(items)


@pytest.mark.parametrize(
    "field,value",
    [
        ("driver_transition_id", "driver-transition-other"),
        ("scheduler_transition_id", "scheduler-transition-other"),
        ("controller_transition_id", "controller-transition-other"),
        ("scheduler_restart_transition_id", "scheduler-restart-other"),
        ("session_date", date(2026, 8, 4)),
    ],
)
def test_restart_rejects_changed_bound_identity(tmp_path, field, value):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    with pytest.raises(DriverError, match="another tick"):
        items[0].run_tick(tick(**{field: value}))
    assert items[1].cycle_count == 0
    close_stack(items)


def test_restart_time_cannot_move_backward(tmp_path):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    items[0]._transition(
        "driver-one", DriverState.INPUTS_COLLECTED, NOW + timedelta(seconds=2)
    )
    with pytest.raises(DriverError, match="another tick"):
        items[0].run_tick(tick(now=NOW + timedelta(seconds=1)))
    close_stack(items)


def test_scheduler_ambiguous_restart_stops_before_providers(tmp_path):
    providers = provider_set()
    items = stack(tmp_path, providers=providers, create_scheduler=True)
    scheduler = items[2]
    scheduler.apply_transition("scheduler-one", target=RunState.PRECONDITION_CHECK, transition_id="prep-one", now=NOW)
    scheduler.apply_transition("scheduler-one", target=RunState.RUNNING_OFFLINE_EVALUATION, transition_id="run-one", now=NOW)
    result = items[0].run_tick(tick())
    assert result.state is DriverState.AMBIGUOUS_RESTART
    assert result.reason is DriverReason.SCHEDULER_AMBIGUOUS
    assert all(provider.calls == 0 for provider in providers.__dict__.values())
    assert items[1].cycle_count == 0
    close_stack(items)


def test_malformed_durable_state_cannot_continue(tmp_path):
    items = stack(tmp_path, create_scheduler=True)
    items[0]._create_record(tick())
    items[0].close()
    with sqlite3.connect(tmp_path / "scheduler.sqlite") as connection:
        connection.execute("UPDATE scheduled_runs SET state='NOT_A_STATE' WHERE run_id='scheduler-one'")
    driver = OfflineCycleDriver(
        tmp_path / "driver.sqlite",
        scheduler=items[2],
        controller=items[3],
        orchestrator=items[1],
        providers=provider_set(),
    )
    result = driver.run_tick(tick())
    assert result.reason is DriverReason.DURABLE_STATE_INVALID
    driver.close(); items[1].close(); items[2].close(); items[3].close()


# ---------------------------------------------------------------------------
# Restart from every durable state and adversarial finalization.
# ---------------------------------------------------------------------------


def test_created_restart_revalidates_and_completes(tmp_path):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    result = items[0].run_tick(tick())
    assert result.state is DriverState.COMPLETED_NO_ACTION
    assert items[1].cycle_count == 1
    close_stack(items)


def test_inputs_collected_restart_recollects(tmp_path):
    providers = provider_set()
    items = stack(tmp_path, providers=providers)
    items[0]._create_record(tick())
    items[0]._transition("driver-one", DriverState.INPUTS_COLLECTED, NOW)
    result = items[0].run_tick(tick())
    assert result.state is DriverState.COMPLETED_NO_ACTION
    assert all(provider.calls == 1 for provider in providers.__dict__.values())
    close_stack(items)


def test_orchestrator_started_without_final_journal_is_ambiguous_and_never_retried(tmp_path):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    items[0]._transition("driver-one", DriverState.INPUTS_COLLECTED, NOW)
    items[0]._transition("driver-one", DriverState.ORCHESTRATOR_STARTED, NOW)
    first = items[0].run_tick(tick())
    second = items[0].run_tick(tick())
    assert first == second
    assert first.state is DriverState.AMBIGUOUS_RESTART
    assert items[1].cycle_count == 0
    close_stack(items)


def prepare_final_orchestrator(items):
    auth = authority()
    scheduler = items[2]
    if scheduler.load_run("scheduler-one") is None:
        scheduler.create_run("scheduler-one", session_date=SESSION_DATE, now=NOW)
    suspension = items[3].assess("controller-one")
    return items[1].run_cycle(orchestrator_inputs(auth, suspension=suspension))


def test_orchestrator_started_recovers_only_an_exact_final_journal(tmp_path):
    items = stack(tmp_path)
    result = prepare_final_orchestrator(items)
    items[0]._create_record(tick())
    items[0]._transition("driver-one", DriverState.INPUTS_COLLECTED, NOW)
    items[0]._transition("driver-one", DriverState.ORCHESTRATOR_STARTED, NOW)
    recovered = items[0].run_tick(tick())
    assert recovered.state is DriverState.COMPLETED_NO_ACTION
    assert recovered.orchestrator_outcome is result.outcome
    assert items[1].cycle_count == 1
    close_stack(items)


def test_orchestrator_completed_reinspects_exact_result(tmp_path):
    items = stack(tmp_path)
    result = prepare_final_orchestrator(items)
    items[0]._create_record(tick())
    set_state(
        items[0],
        DriverState.ORCHESTRATOR_COMPLETED,
        orch_outcome=result.outcome,
        orch_reason=result.reason,
    )
    recovered = items[0].run_tick(tick())
    assert recovered.state is DriverState.COMPLETED_NO_ACTION
    assert items[0].load_receipt("driver-one") is not None
    close_stack(items)


def test_wrong_cycle_journal_cannot_be_attached(tmp_path):
    items = stack(tmp_path)
    prepare_final_orchestrator(items)
    items[0]._create_record(tick())
    set_state(
        items[0],
        DriverState.ORCHESTRATOR_COMPLETED,
        orch_outcome=CycleOutcome.REQUIRE_OPERATOR_REVIEW,
        orch_reason=CycleReason.RISK_DENIED,
    )
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert result.reason is DriverReason.ORCHESTRATOR_RESULT_MISMATCH
    assert items[0].load_receipt("driver-one") is None
    close_stack(items)


def test_another_cycles_receipt_cannot_be_attached(tmp_path):
    items = stack(tmp_path)
    items[0].run_tick(tick())
    set_state(items[0], DriverState.ORCHESTRATOR_COMPLETED)
    with sqlite3.connect(items[0].database_path) as connection:
        connection.execute(
            "UPDATE driver_receipts SET cycle_id='cycle-other' "
            "WHERE driver_id='driver-one'"
        )
    result = items[0].run_tick(tick())
    assert result.state is DriverState.FAILED_CLOSED
    assert result.reason is DriverReason.ORCHESTRATOR_RESULT_MISMATCH
    close_stack(items)


def test_receipt_persisted_restart_finalizes_idempotently(tmp_path):
    items = stack(tmp_path)
    completed = items[0].run_tick(tick())
    set_state(items[0], DriverState.RECEIPT_PERSISTED)
    first = items[0].run_tick(tick())
    second = items[0].run_tick(tick())
    assert first == second == completed
    close_stack(items)


@pytest.mark.parametrize(
    "state,outcome,reason",
    [
        (DriverState.COMPLETED_NO_ACTION, DriverOutcome.COMPLETED_NO_ACTION, DriverReason.ALREADY_COMPLETED),
        (DriverState.FAILED_CLOSED, DriverOutcome.FAILED_CLOSED, DriverReason.DURABLE_STATE_INVALID),
        (DriverState.AMBIGUOUS_RESTART, DriverOutcome.AMBIGUOUS_RESTART, DriverReason.ORCHESTRATOR_RESULT_MISSING),
        (DriverState.EXPIRED, DriverOutcome.COMPLETED_NO_ACTION, DriverReason.EXPIRED),
    ],
)
def test_terminal_states_remain_terminal(tmp_path, state, outcome, reason):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    set_state(items[0], state, outcome=outcome, reason=reason)
    before = items[0].load_record("driver-one").sequence
    result = items[0].run_tick(tick())
    assert result.state is state
    assert items[0].load_record("driver-one").sequence == before
    assert items[1].cycle_count == 0
    close_stack(items)


def test_finalization_before_receipt_is_refused(tmp_path):
    items = stack(tmp_path)
    items[0]._create_record(tick())
    set_state(items[0], DriverState.RECEIPT_PERSISTED)
    with pytest.raises(DriverError, match="receipt persistence"):
        items[0].run_tick(tick())
    close_stack(items)


def test_orchestrator_is_called_exactly_once(tmp_path, monkeypatch):
    items = stack(tmp_path)
    calls = 0
    original = items[1].run_cycle

    def counted(inputs):
        nonlocal calls
        calls += 1
        return original(inputs)

    monkeypatch.setattr(items[1], "run_cycle", counted)
    first = items[0].run_tick(tick())
    second = items[0].run_tick(tick())
    assert first == second
    assert calls == 1
    close_stack(items)


def test_exception_after_durable_orchestrator_completion_uses_exact_journal(
    tmp_path, monkeypatch
):
    items = stack(tmp_path)
    calls = 0
    original = items[1].run_cycle

    def completed_then_raised(inputs):
        nonlocal calls
        calls += 1
        original(inputs)
        raise RuntimeError("return path failed")

    monkeypatch.setattr(items[1], "run_cycle", completed_then_raised)
    result = items[0].run_tick(tick())
    assert result.state is DriverState.COMPLETED_NO_ACTION
    assert result.outcome is DriverOutcome.OPERATOR_REVIEW_REQUIRED
    assert calls == 1
    assert items[0].load_receipt("driver-one") is not None
    close_stack(items)


def test_orchestrator_failed_closed_result_is_receipted_and_terminal(
    tmp_path, monkeypatch
):
    items = stack(
        tmp_path,
        providers=provider_set(
            overrides={
                ProviderKind.RISK: RiskAssessment(
                    RiskDecision.DENIED_POSITION_SIZE
                )
            }
        ),
    )

    def fail_transition(*args, **kwargs):
        raise sqlite3.OperationalError("test persistence refusal")

    monkeypatch.setattr(items[2], "apply_transition", fail_transition)
    result = items[0].run_tick(tick())
    assert result.state is DriverState.FAILED_CLOSED
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert result.orchestrator_outcome is CycleOutcome.FAILED_CLOSED
    assert items[0].load_receipt("driver-one") is not None
    close_stack(items)


def test_orchestrator_ambiguous_result_is_receipted_and_never_retried(
    tmp_path, monkeypatch
):
    items = stack(
        tmp_path,
        providers=provider_set(
            overrides={
                ProviderKind.RISK: RiskAssessment(
                    RiskDecision.DENIED_POSITION_SIZE
                )
            }
        ),
    )

    def fail_controller(*args, **kwargs):
        raise sqlite3.OperationalError("test controller refusal")

    monkeypatch.setattr(items[3], "suspend", fail_controller)
    first = items[0].run_tick(tick())
    second = items[0].run_tick(tick())
    assert first == second
    assert first.state is DriverState.AMBIGUOUS_RESTART
    assert first.outcome is DriverOutcome.AMBIGUOUS_RESTART
    assert first.orchestrator_outcome is CycleOutcome.AMBIGUOUS_RESTART
    assert items[0].load_receipt("driver-one") is not None
    assert items[1].cycle_count == 1
    close_stack(items)


def test_provider_failure_can_never_report_success(tmp_path):
    providers = provider_set()
    object.__setattr__(providers, "calendar", RaisingProvider())
    items = stack(tmp_path, providers=providers)
    result = items[0].run_tick(tick())
    assert result.state is DriverState.FAILED_CLOSED
    assert result.outcome is DriverOutcome.FAILED_CLOSED
    assert items[0].load_receipt("driver-one") is None
    close_stack(items)


def test_favorable_risk_cannot_override_suspension(tmp_path):
    items = stack(tmp_path)
    items[3].suspend(
        "controller-one",
        cause=SuspensionCause.KILL_SWITCH_ENGAGED,
        transition_id="halt-one",
        now=NOW,
    )
    result = items[0].run_tick(tick())
    assert result.outcome is DriverOutcome.SUSPENDED
    assert result.orchestrator_reason is CycleReason.KILL_SWITCH_ENGAGED
    assert items[3].load("controller-one").state is ControllerState.SUSPENDED
    close_stack(items)


def test_tracked_tree_has_no_database_artifact():
    assert not any(path.suffix in {".sqlite", ".sqlite3", ".db"} for path in pathlib.Path("src").glob("**/*"))
    assert not any(path.suffix in {".sqlite", ".sqlite3", ".db"} for path in pathlib.Path("tests").glob("**/*"))
