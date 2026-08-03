"""Offline safety orchestrator — dominance, persistence, and crash recovery.

Every test is offline. Nothing opens a broker session, reaches the network,
issues a manifest, runs a checkpoint, or touches a credential. All signing
material is a fixture; every database lives under `tmp_path`.

The properties that matter most:

1. **Dominance is an ordering.** A favourable lower-priority result never
   overrides a higher-priority failure.
2. **Partial completion is detectable**, never reported as success and never
   silently completed.
3. **A cycle identity is spent once**, and a restart never replays it.
4. **Nothing here can authorize action** — construction with
   `permits_automated_action=True` raises.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

import src.offline_safety_orchestrator as orchestrator_module
from src.attestation_authority import (
    AttestationAuthority,
    ProducerIdentity,
    SignedAttestation,
)
from src.durable_scheduler import DurableScheduler, RunState, SchedulerAction
from src.durable_suspension_controller import (
    ControllerState,
    DurableSuspensionController,
    EvidenceClass,
    SuspensionCause,
    SuspensionDecision,
)
from src.exchange_calendar import (
    CalendarSession,
    ClassificationReason,
    DayKind,
    SessionClassification,
)
from src.exchange_calendar import EASTERN, classify as classify_calendar
from src.execution_gates import (
    Attestation,
    EvidenceSource,
    GateDecision,
    GateMode,
    GateVerdict,
)
from src.offline_reconciliation import RecoveryOutcome, RecoveryResult
from src.offline_safety_orchestrator import (
    DOMINANCE_ORDER,
    REQUIRED_CYCLE_ATTESTATIONS,
    TERMINAL_PHASES,
    CycleInputs,
    CycleOutcome,
    CyclePhase,
    CycleReason,
    CycleResult,
    DominanceLevel,
    OfflineSafetyOrchestrator,
    OrchestratorError,
    assess_attestations,
    decide_cycle,
    is_repository_data_path,
)
from src.operational_monitor import HeartbeatAssessment, HeartbeatStatus
from src.risk_limits import RiskAssessment, RiskDecision

MODULE_PATH = pathlib.Path(orchestrator_module.__file__)

NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 8, 3)
REGULAR_MOMENT = datetime(2026, 8, 3, 10, 0, tzinfo=EASTERN)
MONOTONIC = 1000.0

FIXTURE_KEY = b"orb-live-orchestrator-fixture-key-0123456"

RUN_ID = "run-1"
CONTROLLER_ID = "ctl-1"


def authority() -> AttestationAuthority:
    return AttestationAuthority(
        FIXTURE_KEY,
        {
            "operator-console": ProducerIdentity(
                "operator-console",
                frozenset({"protection_grounding_complete"}),
                frozenset({EvidenceSource.OPERATOR_ATTESTED}),
            ),
            "risk-engine": ProducerIdentity(
                "risk-engine",
                frozenset({"risk_within_limits"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "recovery-probe": ProducerIdentity(
                "recovery-probe",
                frozenset({"recovery_consistent"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "monitor": ProducerIdentity(
                "monitor",
                frozenset({"monitoring_healthy"}),
                frozenset({EvidenceSource.MEASURED, EvidenceSource.TESTED}),
            ),
            "alert-harness": ProducerIdentity(
                "alert-harness",
                frozenset({"alerting_verified_reachable"}),
                frozenset({EvidenceSource.TESTED}),
            ),
            "kill-switch-probe": ProducerIdentity(
                "kill-switch-probe",
                frozenset({"kill_switch_disengaged"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            "calendar-probe": ProducerIdentity(
                "calendar-probe",
                frozenset({"market_session_valid"}),
                frozenset({EvidenceSource.MEASURED}),
            ),
            # A producer that owns the wrong fact, for the wrong-producer probe.
            "impostor": ProducerIdentity(
                "impostor",
                frozenset({"deployment_not_expired"}),
                frozenset({EvidenceSource.OPERATOR_ATTESTED}),
            ),
        },
    )


_PRODUCERS = {
    "risk_within_limits": ("risk-engine", EvidenceSource.MEASURED),
    "recovery_consistent": ("recovery-probe", EvidenceSource.MEASURED),
    "monitoring_healthy": ("monitor", EvidenceSource.MEASURED),
    "alerting_verified_reachable": ("alert-harness", EvidenceSource.TESTED),
    "kill_switch_disengaged": ("kill-switch-probe", EvidenceSource.MEASURED),
    "market_session_valid": ("calendar-probe", EvidenceSource.MEASURED),
    "protection_grounding_complete": (
        "operator-console", EvidenceSource.OPERATOR_ATTESTED,
    ),
}


def good_attestations(auth, *, monotonic=MONOTONIC, **overrides):
    signed = {}
    for fact in REQUIRED_CYCLE_ATTESTATIONS:
        producer, source = _PRODUCERS[fact]
        signed[fact] = auth.issue(
            producer_id=producer, fact_name=fact, satisfied=True,
            source=source, recorded_monotonic=monotonic,
        )
    signed.update(overrides)
    return signed


def open_gate() -> GateDecision:
    """The gate can never be OPEN while ACTIVATION_APPROVALS is empty, which is
    itself a tested property. Every cycle therefore sees a closed gate."""
    return GateDecision(
        verdict=GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, mode=GateMode.SUPERVISED
    )


def clear_suspension() -> SuspensionDecision:
    return SuspensionDecision(
        state=ControllerState.CLEAR,
        cause=SuspensionCause.NONE,
        requires_operator_review=False,
        scheduler_action=SchedulerAction.REQUIRE_OPERATOR_REVIEW,
        evidence_class=EvidenceClass.NONE,
    )


def inputs(auth, **overrides) -> CycleInputs:
    fields = dict(
        cycle_id="cyc-1",
        scheduler_run_id=RUN_ID,
        controller_id=CONTROLLER_ID,
        now=NOW,
        scheduler_transition_id="sch-1",
        controller_transition_id="ctlx-1",
        session=classify_calendar(REGULAR_MOMENT),
        recovery=RecoveryResult(RecoveryOutcome.NO_PENDING_WORK),
        risk=RiskAssessment(RiskDecision.WITHIN_LIMITS),
        monitoring=HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        gate=open_gate(),
        suspension=clear_suspension(),
        attestations=good_attestations(auth),
        authority=auth,
        attestation_monotonic=MONOTONIC,
    )
    fields.update(overrides)
    return CycleInputs(**fields)


def stack(tmp_path):
    scheduler = DurableScheduler(tmp_path / "scheduler.sqlite")
    controller = DurableSuspensionController(tmp_path / "controller.sqlite")
    scheduler.create_run(RUN_ID, session_date=SESSION_DATE, now=NOW)
    controller.register(CONTROLLER_ID, now=NOW)
    orchestrator = OfflineSafetyOrchestrator(
        tmp_path / "journal.sqlite", scheduler=scheduler, controller=controller
    )
    return orchestrator, scheduler, controller


# --------------------------------------------------------------------------
# 1. Offline by construction.
# --------------------------------------------------------------------------


def _code_identifiers(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(
            node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
        ):
            doc = ast.get_docstring(node, clean=False)
            if doc is not None:
                docstrings.add(doc)
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value not in docstrings:
                found.add(node.value)
    return found


def _dotted_calls(path):
    names = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not isinstance(node, ast.Call):
            continue
        parts, current = [], node.func
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        if parts:
            names.add(".".join(reversed(parts)))
    return names


def test_no_broker_or_network_imports():
    forbidden = (
        "robinhood", "mcp", "adapter", "socket", "http", "urllib", "requests",
        "commissioning", "manifest", "broker_saga", "read_only_observation",
    )
    for node in ast.walk(ast.parse(MODULE_PATH.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not any(word in name.lower() for word in forbidden), name


def test_no_wall_clock():
    forbidden = {
        "datetime.now", "datetime.utcnow", "time.time", "time.monotonic",
        "time.perf_counter", "date.today",
    }
    assert _dotted_calls(MODULE_PATH) & forbidden == set()


def test_no_order_or_activation_vocabulary():
    identifiers = {item.lower() for item in _code_identifiers(MODULE_PATH)}
    for forbidden in (
        "place_equity_order", "cancel_equity_order", "call_tool",
        "activation_approvals", "account_number", "signing_key",
    ):
        assert not any(forbidden in item for item in identifiers), forbidden


def test_no_manifest_path_and_no_embedded_keys():
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "authorization_manifest" not in source
    assert "b'" not in source and 'b"' not in source


def test_the_orchestrator_is_not_in_the_milestone_9_pinned_closure():
    from src.read_only_authorization_manifest import PINNED_IMPLEMENTATION_CLOSURE

    assert "src/offline_safety_orchestrator.py" not in PINNED_IMPLEMENTATION_CLOSURE


# --------------------------------------------------------------------------
# 2. Result model.
# --------------------------------------------------------------------------


def test_a_result_can_never_permit_automated_action():
    for outcome in CycleOutcome:
        with pytest.raises(OrchestratorError):
            CycleResult(
                outcome, CycleReason.OK, SchedulerAction.SUSPEND,
                permits_automated_action=True,
            )


def test_only_a_complete_cycle_may_run_offline_checks():
    for outcome in CycleOutcome:
        if outcome is CycleOutcome.OFFLINE_CHECKS_COMPLETE:
            continue
        with pytest.raises(OrchestratorError):
            CycleResult(outcome, CycleReason.OK, SchedulerAction.RUN_OFFLINE_CHECKS)


def test_every_projection_says_false():
    result = CycleResult(
        CycleOutcome.SUSPENDED, CycleReason.ACTIVE_SUSPENSION, SchedulerAction.SUSPEND
    )
    assert result.sanitized_facts()["permits_automated_action"] == "false"


def test_no_outcome_or_reason_is_free_text():
    for member in list(CycleOutcome) + list(CycleReason) + list(CyclePhase):
        assert member.value.replace("_", "").isalnum()
        assert member.value == member.value.upper()


# --------------------------------------------------------------------------
# 3. Dominance.
# --------------------------------------------------------------------------


def test_the_dominance_order_is_the_declared_priority():
    assert DOMINANCE_ORDER == tuple(DominanceLevel)
    assert DOMINANCE_ORDER[0] is DominanceLevel.PERMANENT_HALT
    assert DOMINANCE_ORDER[-1] is DominanceLevel.OFFLINE_CHECKS_COMPLETE
    assert DOMINANCE_ORDER.index(DominanceLevel.ACTIVE_SUSPENSION) < (
        DOMINANCE_ORDER.index(DominanceLevel.RISK)
    )
    assert DOMINANCE_ORDER.index(DominanceLevel.MONITORING) < (
        DOMINANCE_ORDER.index(DominanceLevel.CALENDAR)
    )
    assert DOMINANCE_ORDER.index(DominanceLevel.MALFORMED_DURABLE_STATE) < (
        DOMINANCE_ORDER.index(DominanceLevel.ATTESTATIONS)
    )


def suspension(state, cause=SuspensionCause.OPERATOR_HALT):
    return SuspensionDecision(
        state=state,
        cause=cause if state is not ControllerState.CLEAR else SuspensionCause.NONE,
        requires_operator_review=state is not ControllerState.CLEAR,
        scheduler_action=(
            SchedulerAction.SUSPEND
            if state in (ControllerState.SUSPENDED, ControllerState.PERMANENT_HALT,
                         ControllerState.MALFORMED_STATE)
            else SchedulerAction.REQUIRE_OPERATOR_REVIEW
        ),
    )


def test_a_clean_cycle_still_stops_at_the_closed_gate():
    """The gate cannot open while ACTIVATION_APPROVALS is empty."""
    result = decide_cycle(inputs(authority()))
    assert result.reason is CycleReason.GATE_CLOSED
    assert result.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW


def test_permanent_halt_dominates_everything():
    auth = authority()
    result = decide_cycle(
        inputs(auth, suspension=suspension(ControllerState.PERMANENT_HALT))
    )
    assert result.reason is CycleReason.PERMANENT_HALT
    assert result.outcome is CycleOutcome.SUSPENDED


def test_favourable_risk_cannot_override_permanent_halt():
    auth = authority()
    result = decide_cycle(
        inputs(
            auth,
            suspension=suspension(ControllerState.PERMANENT_HALT),
            risk=RiskAssessment(RiskDecision.WITHIN_LIMITS),
            recovery=RecoveryResult(RecoveryOutcome.NO_PENDING_WORK),
            monitoring=HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        )
    )
    assert result.reason is CycleReason.PERMANENT_HALT


def test_favourable_risk_cannot_override_suspension():
    auth = authority()
    result = decide_cycle(
        inputs(auth, suspension=suspension(ControllerState.SUSPENDED))
    )
    assert result.reason is CycleReason.ACTIVE_SUSPENSION
    assert result.outcome is CycleOutcome.SUSPENDED


def test_a_kill_switch_suspension_is_named_as_such():
    auth = authority()
    result = decide_cycle(
        inputs(
            auth,
            suspension=suspension(
                ControllerState.SUSPENDED, SuspensionCause.KILL_SWITCH_ENGAGED
            ),
        )
    )
    assert result.reason is CycleReason.KILL_SWITCH_ENGAGED


def test_successful_reconciliation_cannot_override_a_kill_switch():
    auth = authority()
    result = decide_cycle(
        inputs(
            auth,
            suspension=suspension(
                ControllerState.SUSPENDED, SuspensionCause.KILL_SWITCH_ENGAGED
            ),
            recovery=RecoveryResult(RecoveryOutcome.CONSISTENT_RESUME_CANDIDATE),
        )
    )
    assert result.reason is CycleReason.KILL_SWITCH_ENGAGED


def test_clearance_pending_still_blocks():
    auth = authority()
    result = decide_cycle(
        inputs(auth, suspension=suspension(ControllerState.CLEARANCE_PENDING_REVIEW))
    )
    assert result.reason is CycleReason.ACTIVE_SUSPENSION


def test_malformed_durable_state_dominates_valid_attestations():
    auth = authority()
    result = decide_cycle(
        inputs(auth, suspension=suspension(ControllerState.MALFORMED_STATE))
    )
    assert result.reason is CycleReason.MALFORMED_DURABLE_STATE
    assert result.outcome is CycleOutcome.FAILED_CLOSED


@pytest.mark.parametrize(
    "outcome",
    [o for o in RecoveryOutcome if o.value.startswith("OPERATOR_REQUIRED")],
)
def test_every_unresolved_reconciliation_outcome_blocks(outcome):
    auth = authority()
    result = decide_cycle(inputs(auth, recovery=RecoveryResult(outcome)))
    assert result.reason is CycleReason.RECONCILIATION_UNRESOLVED


def test_unknown_reconciliation_is_not_consistent():
    auth = authority()
    result = decide_cycle(
        inputs(
            auth,
            recovery=RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_UNKNOWN_STATE),
        )
    )
    assert result.reason is CycleReason.RECONCILIATION_UNRESOLVED


@pytest.mark.parametrize(
    "decision", [d for d in RiskDecision if d is not RiskDecision.WITHIN_LIMITS]
)
def test_every_risk_denial_blocks(decision):
    auth = authority()
    result = decide_cycle(inputs(auth, risk=RiskAssessment(decision)))
    expected = (
        CycleReason.KILL_SWITCH_ENGAGED
        if decision is RiskDecision.DENIED_KILL_SWITCH_ENGAGED
        else CycleReason.RISK_DENIED
    )
    assert result.reason is expected


def test_cumulative_exposure_denial_blocks():
    auth = authority()
    result = decide_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_POSITION_SIZE))
    )
    assert result.reason is CycleReason.RISK_DENIED


@pytest.mark.parametrize(
    "status", [HeartbeatStatus.MISSING, HeartbeatStatus.NEVER_STARTED]
)
def test_unhealthy_monitoring_blocks(status):
    auth = authority()
    result = decide_cycle(inputs(auth, monitoring=HeartbeatAssessment(status)))
    assert result.reason is CycleReason.MONITORING_UNHEALTHY


def test_a_regular_session_cannot_override_failed_alert_delivery():
    auth = authority()
    bad = good_attestations(auth)
    bad["alerting_verified_reachable"] = auth.issue(
        producer_id="alert-harness", fact_name="alerting_verified_reachable",
        satisfied=False, source=EvidenceSource.TESTED, recorded_monotonic=MONOTONIC,
    )
    result = decide_cycle(
        inputs(auth, attestations=bad, session=classify_calendar(REGULAR_MOMENT))
    )
    assert result.reason is CycleReason.ALERT_DELIVERY_UNCONFIRMED


def test_missing_delivery_confirmation_blocks():
    auth = authority()
    missing = good_attestations(auth)
    del missing["alerting_verified_reachable"]
    result = decide_cycle(inputs(auth, attestations=missing))
    assert result.reason is CycleReason.ALERT_DELIVERY_UNCONFIRMED


@pytest.mark.parametrize(
    "moment,reason",
    [
        (datetime(2026, 8, 3, 5, 0, tzinfo=EASTERN), CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 8, 3, 2, 0, tzinfo=EASTERN), CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 8, 1, 10, 0, tzinfo=EASTERN), CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 9, 7, 10, 0, tzinfo=EASTERN), CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2026, 11, 27, 12, 0, tzinfo=EASTERN), None),
        (datetime(2026, 11, 27, 14, 0, tzinfo=EASTERN),
         CycleReason.MARKET_NOT_IN_SESSION),
        (datetime(2025, 8, 4, 10, 0, tzinfo=EASTERN), CycleReason.CALENDAR_UNUSABLE),
        (datetime(2028, 8, 7, 10, 0, tzinfo=EASTERN), CycleReason.CALENDAR_UNUSABLE),
    ],
)
def test_calendar_conditions(moment, reason):
    auth = authority()
    result = decide_cycle(inputs(auth, session=classify_calendar(moment)))
    if reason is None:
        # An early-close morning is a real regular session; the gate stops it.
        assert result.reason is CycleReason.GATE_CLOSED
    else:
        assert result.reason is reason


def test_an_expired_calendar_fails_closed():
    auth = authority()
    expired = classify_calendar(REGULAR_MOMENT, today=date(2028, 1, 1))
    result = decide_cycle(inputs(auth, session=expired))
    assert result.reason is CycleReason.CALENDAR_UNUSABLE
    assert result.outcome is CycleOutcome.FAILED_CLOSED


def test_a_forged_regular_classification_is_still_refused():
    """`is_regular_session` is the load-bearing boolean, not the name."""
    auth = authority()
    forged = SessionClassification(
        CalendarSession.REGULAR, DayKind.WEEKEND, ClassificationReason.WEEKEND, False
    )
    result = decide_cycle(inputs(auth, session=forged))
    assert result.reason is CycleReason.MARKET_NOT_IN_SESSION


# --------------------------------------------------------------------------
# 4. Attestations.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("fact", REQUIRED_CYCLE_ATTESTATIONS)
def test_a_missing_attestation_blocks(fact):
    auth = authority()
    missing = good_attestations(auth)
    del missing[fact]
    result = decide_cycle(inputs(auth, attestations=missing))
    assert result.reason in (
        CycleReason.ATTESTATION_MISSING, CycleReason.ALERT_DELIVERY_UNCONFIRMED
    )


def test_a_stale_attestation_blocks():
    auth = authority()
    stale = good_attestations(auth, monotonic=0.0)
    result = decide_cycle(
        inputs(auth, attestations=stale, attestation_monotonic=100_000.0)
    )
    assert result.reason is CycleReason.ATTESTATION_STALE


def test_a_future_attestation_blocks():
    auth = authority()
    future = good_attestations(auth, monotonic=MONOTONIC + 500)
    result = decide_cycle(inputs(auth, attestations=future))
    assert result.reason is CycleReason.ATTESTATION_STALE


def test_a_wrong_producer_attestation_blocks():
    auth = authority()
    wrong = good_attestations(auth)
    wrong["protection_grounding_complete"] = SignedAttestation(
        fact_name="protection_grounding_complete",
        attestation=Attestation(True, EvidenceSource.OPERATOR_ATTESTED, MONOTONIC),
        producer_id="impostor",
        signature="0" * 64,
    )
    result = decide_cycle(inputs(auth, attestations=wrong))
    assert result.reason is CycleReason.ATTESTATION_UNVERIFIED


def test_a_tampered_attestation_blocks():
    auth = authority()
    tampered = good_attestations(auth)
    genuine = tampered["risk_within_limits"]
    tampered["risk_within_limits"] = SignedAttestation(
        fact_name=genuine.fact_name,
        attestation=Attestation(True, EvidenceSource.MEASURED, MONOTONIC + 1),
        producer_id=genuine.producer_id,
        signature=genuine.signature,
    )
    result = decide_cycle(inputs(auth, attestations=tampered))
    assert result.reason is CycleReason.ATTESTATION_UNVERIFIED


def test_a_self_declared_attestation_blocks():
    auth = authority()
    declared = good_attestations(auth)
    declared["risk_within_limits"] = SignedAttestation(
        fact_name="risk_within_limits",
        attestation=Attestation(True, EvidenceSource.SELF_DECLARED, MONOTONIC),
        producer_id="risk-engine",
        signature="0" * 64,
    )
    result = decide_cycle(inputs(auth, attestations=declared))
    assert result.reason is CycleReason.ATTESTATION_SELF_DECLARED


def test_a_signature_moved_onto_another_fact_blocks():
    auth = authority()
    swapped = good_attestations(auth)
    genuine = swapped["risk_within_limits"]
    swapped["recovery_consistent"] = genuine
    result = decide_cycle(inputs(auth, attestations=swapped))
    assert result.reason is CycleReason.ATTESTATION_UNVERIFIED


def test_missing_protection_grounding_is_named_specifically():
    auth = authority()
    ungrounded = good_attestations(auth)
    ungrounded["protection_grounding_complete"] = auth.issue(
        producer_id="operator-console", fact_name="protection_grounding_complete",
        satisfied=False, source=EvidenceSource.OPERATOR_ATTESTED,
        recorded_monotonic=MONOTONIC,
    )
    result = decide_cycle(inputs(auth, attestations=ungrounded))
    assert result.reason is CycleReason.PROTECTION_GROUNDING_MISSING
    assert result.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW


def test_the_real_protection_grounding_is_still_absent():
    from src.read_only_broker_state import approved_active_protection_states

    assert approved_active_protection_states() == frozenset()


def test_assess_attestations_accepts_a_clean_set():
    auth = authority()
    assert assess_attestations(inputs(auth)).acceptable is True


# --------------------------------------------------------------------------
# 5. Input contract.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "override",
    [
        {"session": True},
        {"recovery": "consistent"},
        {"risk": True},
        {"monitoring": "healthy"},
        {"gate": "open"},
        {"suspension": False},
        {"authority": None},
        {"attestations": "everything is fine"},
        {"attestation_monotonic": "soon"},
        {"now": datetime(2026, 8, 3, 10, 0)},
        {"cycle_id": ""},
        {"scheduler_transition_id": "sch 1"},
    ],
)
def test_loose_inputs_are_refused(override):
    auth = authority()
    with pytest.raises(OrchestratorError):
        decide_cycle(inputs(auth, **override))


def test_the_two_stores_need_distinct_transition_identities():
    auth = authority()
    with pytest.raises(OrchestratorError):
        decide_cycle(
            inputs(auth, scheduler_transition_id="same", controller_transition_id="same")
        )


# --------------------------------------------------------------------------
# 6. Persistence and the cycle.
# --------------------------------------------------------------------------


def test_a_journal_under_repository_data_is_refused(tmp_path):
    from src.offline_safety_orchestrator import REPOSITORY_ROOT

    scheduler = DurableScheduler(tmp_path / "s.sqlite")
    controller = DurableSuspensionController(tmp_path / "c.sqlite")
    with pytest.raises(OrchestratorError):
        OfflineSafetyOrchestrator(
            REPOSITORY_ROOT / "data" / "j.sqlite",
            scheduler=scheduler, controller=controller,
        )
    assert is_repository_data_path(REPOSITORY_ROOT / "data" / "x.sqlite")
    scheduler.close()
    controller.close()


def test_a_cycle_is_journaled_and_finalized(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    result = orch.run_cycle(inputs(auth))
    assert result.reason is CycleReason.GATE_CLOSED
    record = orch.load_cycle("cyc-1")
    assert record.phase is CyclePhase.FINALIZED
    assert record.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW
    orch.close(); scheduler.close(); controller.close()


def test_a_duplicate_cycle_identity_is_refused(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    with pytest.raises(OrchestratorError):
        orch.run_cycle(inputs(auth))
    assert orch.cycle_count == 1
    orch.close(); scheduler.close(); controller.close()


def test_a_reused_scheduler_transition_identity_is_refused(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    with pytest.raises(OrchestratorError):
        orch.run_cycle(inputs(auth, cycle_id="cyc-2", controller_transition_id="ctlx-2"))
    orch.close(); scheduler.close(); controller.close()


def test_a_suspending_cycle_moves_both_stores(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    result = orch.run_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_KILL_SWITCH_ENGAGED))
    )
    assert result.outcome is CycleOutcome.SUSPENDED
    assert scheduler.load_run(RUN_ID).state is RunState.SUSPENDED
    record = controller.load(CONTROLLER_ID)
    assert record.state is ControllerState.SUSPENDED
    assert record.cause is SuspensionCause.KILL_SWITCH_ENGAGED
    orch.close(); scheduler.close(); controller.close()


def test_the_orchestrator_never_clears_a_controller_suspension(tmp_path):
    """Clearing is a reviewed operator act with its own two-step path."""
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    controller.suspend(
        CONTROLLER_ID, cause=SuspensionCause.OPERATOR_HALT,
        transition_id="manual-1", now=NOW,
    )
    orch.run_cycle(inputs(auth, suspension=suspension(ControllerState.SUSPENDED)))
    assert controller.load(CONTROLLER_ID).state is ControllerState.SUSPENDED
    source = MODULE_PATH.read_text(encoding="utf-8")
    assert "clear_suspension" not in source
    assert "apply_clearance" not in source
    orch.close(); scheduler.close(); controller.close()


def test_a_scheduler_persistence_failure_is_not_success(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)

    def refuse(*args, **kwargs):
        raise RuntimeError("scheduler is unavailable")

    scheduler.apply_transition = refuse  # type: ignore[method-assign]
    result = orch.run_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_POSITION_SIZE))
    )
    assert result.outcome is CycleOutcome.FAILED_CLOSED
    assert result.reason is CycleReason.PERSISTENCE_FAILED
    assert orch.load_cycle("cyc-1").phase is CyclePhase.FAILED_CLOSED
    orch.close(); scheduler.close(); controller.close()


def test_a_controller_persistence_failure_is_ambiguous_not_success(tmp_path):
    """The scheduler moved and the controller did not. Calling that a failure
    would understate it; calling it success would be a lie."""
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)

    def refuse(*args, **kwargs):
        raise RuntimeError("controller is unavailable")

    controller.suspend = refuse  # type: ignore[method-assign]
    result = orch.run_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_POSITION_SIZE))
    )
    assert result.outcome is CycleOutcome.AMBIGUOUS_RESTART
    assert orch.load_cycle("cyc-1").phase is CyclePhase.AMBIGUOUS_RESTART
    assert scheduler.load_run(RUN_ID).state is RunState.SUSPENDED
    orch.close(); scheduler.close(); controller.close()


def test_an_unknown_schema_version_is_refused(tmp_path):
    orch, scheduler, controller = stack(tmp_path)
    orch.close()
    connection = sqlite3.connect(tmp_path / "journal.sqlite")
    connection.execute(
        "UPDATE orchestrator_meta SET value = '99' WHERE key = 'schema_version'"
    )
    connection.commit(); connection.close()
    with pytest.raises(OrchestratorError):
        OfflineSafetyOrchestrator(
            tmp_path / "journal.sqlite", scheduler=scheduler, controller=controller
        )
    scheduler.close(); controller.close()


@pytest.mark.parametrize(
    "column,value",
    [("phase", "NEARLY"), ("outcome", "FINE"), ("created_at", "yesterday")],
)
def test_a_malformed_journal_row_fails_closed(tmp_path, column, value):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    orch._connection.execute(
        f"UPDATE cycle_journal SET {column} = ? WHERE cycle_id = ?", (value, "cyc-1")
    )
    with pytest.raises(OrchestratorError):
        orch.load_cycle("cyc-1")
    orch.close(); scheduler.close(); controller.close()


def test_persisted_columns_cannot_carry_private_values(tmp_path):
    orch, scheduler, controller = stack(tmp_path)
    columns = {
        row[1].lower()
        for row in orch._connection.execute("PRAGMA table_info(cycle_journal)")
    }
    for forbidden in (
        "symbol", "quantity", "price", "balance", "account", "payload", "raw",
        "message", "credential", "token", "path", "manifest", "username", "alert",
    ):
        assert not any(forbidden in column for column in columns), forbidden
    orch.close(); scheduler.close(); controller.close()


def test_the_cycle_projection_omits_identities(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth, cycle_id="operator-named-cycle-777"))
    record = orch.load_cycle("operator-named-cycle-777")
    assert "operator-named-cycle-777" not in repr(record.sanitized_facts())
    orch.close(); scheduler.close(); controller.close()


# --------------------------------------------------------------------------
# 7. Crash recovery, phase by phase.
# --------------------------------------------------------------------------


def crashed_at(
    tmp_path, phase, *, apply_scheduler=False, apply_controller=False,
    scheduler_effect_required=True,
):
    """Journal a cycle stopped at `phase`, optionally with real store effects."""
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth, cycle_id="cyc-0", scheduler_transition_id="sch-0",
                          controller_transition_id="ctlx-0"))
    orch._connection.execute(
        "INSERT INTO cycle_journal (cycle_id, phase, outcome, reason, "
        " scheduler_run_id, controller_id, scheduler_transition_id, "
        " controller_transition_id, scheduler_effect_required, "
        " controller_effect_required, created_at, updated_at) "
        "VALUES ('cyc-crash', ?, NULL, NULL, ?, ?, 'sch-c', 'ctlx-c', ?, ?, ?, ?)",
        (phase.value, RUN_ID, CONTROLLER_ID,
         1 if scheduler_effect_required else 0,
         1 if apply_controller else 0,
         NOW.isoformat(), NOW.isoformat()),
    )
    if apply_scheduler:
        scheduler.apply_transition(
            RUN_ID, target=RunState.SUSPENDED, transition_id="sch-c", now=NOW,
            suspension_reason="SAFETY_HALT",
        )
    if apply_controller:
        controller.suspend(
            CONTROLLER_ID, cause=SuspensionCause.RISK_LIMIT_BREACH,
            transition_id="ctlx-c", now=NOW,
        )
    return orch, scheduler, controller


@pytest.mark.parametrize("phase", [CyclePhase.CREATED, CyclePhase.VALIDATED])
def test_restart_before_any_effect_fails_closed(tmp_path, phase):
    orch, scheduler, controller = crashed_at(tmp_path, phase)
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.FAILED_CLOSED
    orch.close(); scheduler.close(); controller.close()


def test_restart_from_prepared_without_effect_fails_closed(tmp_path):
    orch, scheduler, controller = crashed_at(tmp_path, CyclePhase.PREPARED)
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.FAILED_CLOSED
    orch.close(); scheduler.close(); controller.close()


def test_restart_from_prepared_with_a_landed_effect_is_ambiguous(tmp_path):
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.PREPARED, apply_scheduler=True
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.AMBIGUOUS_RESTART
    orch.close(); scheduler.close(); controller.close()


def test_restart_from_scheduler_applied_is_ambiguous(tmp_path):
    """The controller half is never completed on a later run."""
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.SCHEDULER_APPLIED, apply_scheduler=True
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.AMBIGUOUS_RESTART
    assert controller.load(CONTROLLER_ID).state is ControllerState.CLEAR
    orch.close(); scheduler.close(); controller.close()


def test_restart_from_controller_applied_finalizes_only_when_both_landed(tmp_path):
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.CONTROLLER_APPLIED,
        apply_scheduler=True, apply_controller=True,
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.FINALIZED
    orch.close(); scheduler.close(); controller.close()


def test_restart_from_controller_applied_is_ambiguous_when_one_is_missing(tmp_path):
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.CONTROLLER_APPLIED, apply_scheduler=False,
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.AMBIGUOUS_RESTART
    orch.close(); scheduler.close(); controller.close()


@pytest.mark.parametrize("phase", sorted(TERMINAL_PHASES, key=lambda p: p.value))
def test_a_terminal_cycle_is_returned_unchanged(tmp_path, phase):
    orch, scheduler, controller = crashed_at(tmp_path, phase)
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is phase
    orch.close(); scheduler.close(); controller.close()


def test_a_missing_journal_row_is_not_a_new_cycle(tmp_path):
    orch, scheduler, controller = stack(tmp_path)
    with pytest.raises(OrchestratorError):
        orch.recover_cycle("never-journaled", now=NOW)
    orch.close(); scheduler.close(); controller.close()


def test_a_recovered_cycle_cannot_be_re_run(tmp_path):
    auth = authority()
    orch, scheduler, controller = crashed_at(tmp_path, CyclePhase.PREPARED)
    orch.recover_cycle("cyc-crash", now=NOW)
    with pytest.raises(OrchestratorError):
        orch.run_cycle(inputs(auth, cycle_id="cyc-crash"))
    orch.close(); scheduler.close(); controller.close()


def test_recovery_refuses_a_naive_clock(tmp_path):
    orch, scheduler, controller = crashed_at(tmp_path, CyclePhase.PREPARED)
    with pytest.raises(OrchestratorError):
        orch.recover_cycle("cyc-crash", now=datetime(2026, 8, 3, 10, 0))
    orch.close(); scheduler.close(); controller.close()


# --------------------------------------------------------------------------
# 8. Scheduler and controller integration.
# --------------------------------------------------------------------------


def test_scheduler_ambiguous_restart_dominates_favourable_components(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    scheduler.apply_transition(
        RUN_ID, target=RunState.WAITING_FOR_SESSION, transition_id="pre-1", now=NOW
    )
    scheduler.apply_transition(
        RUN_ID, target=RunState.PRECONDITION_CHECK, transition_id="pre-2", now=NOW
    )
    scheduler.apply_transition(
        RUN_ID, target=RunState.RUNNING_OFFLINE_EVALUATION,
        transition_id="pre-3", now=NOW,
    )
    scheduler.recover_on_restart(RUN_ID, transition_id="pre-4", now=NOW)
    assert scheduler.load_run(RUN_ID).state is RunState.AMBIGUOUS_RESTART

    orch.run_cycle(inputs(auth))
    # A terminal scheduler run is never moved by a cycle, whatever it decided.
    assert scheduler.load_run(RUN_ID).state is RunState.AMBIGUOUS_RESTART
    orch.close(); scheduler.close(); controller.close()


def test_clear_controller_does_not_make_the_scheduler_runnable(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    assert scheduler.load_run(RUN_ID).state is not RunState.RUNNING_OFFLINE_EVALUATION
    orch.close(); scheduler.close(); controller.close()


def test_no_cycle_output_opens_a_gate_or_enables_strategy():
    import yaml

    from src.execution_gates import ACTIVATION_APPROVALS
    from src.live_order_state_model import ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES

    assert ACTIVATION_APPROVALS == frozenset()
    assert ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES == frozenset()
    assert yaml.safe_load(
        pathlib.Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
    )["strategy"]["enabled"] is False
    assert "ACTIVATION_APPROVALS" not in _code_identifiers(MODULE_PATH)


def test_the_gate_cannot_be_open_while_no_mode_is_approved():
    from src.execution_gates import ExecutionGateError

    with pytest.raises(ExecutionGateError):
        GateDecision(verdict=GateVerdict.OPEN, mode=GateMode.SUPERVISED)


# --------------------------------------------------------------------------
# 9. Adversarial probes.
# --------------------------------------------------------------------------


def test_probe_a_regular_session_cannot_override_suspension():
    auth = authority()
    result = decide_cycle(
        inputs(
            auth,
            suspension=suspension(ControllerState.SUSPENDED),
            session=classify_calendar(REGULAR_MOMENT),
        )
    )
    assert result.outcome is CycleOutcome.SUSPENDED


def test_probe_failed_alert_delivery_is_never_health():
    auth = authority()
    bad = good_attestations(auth)
    bad["alerting_verified_reachable"] = auth.issue(
        producer_id="alert-harness", fact_name="alerting_verified_reachable",
        satisfied=False, source=EvidenceSource.TESTED, recorded_monotonic=MONOTONIC,
    )
    result = decide_cycle(
        inputs(auth, attestations=bad,
               monitoring=HeartbeatAssessment(HeartbeatStatus.HEALTHY))
    )
    assert result.reason is CycleReason.ALERT_DELIVERY_UNCONFIRMED


def test_probe_missing_monitoring_evidence_is_never_health():
    auth = authority()
    missing = good_attestations(auth)
    del missing["monitoring_healthy"]
    result = decide_cycle(inputs(auth, attestations=missing))
    assert result.reason is CycleReason.ATTESTATION_MISSING


def test_probe_never_started_heartbeat_is_not_healthy():
    auth = authority()
    result = decide_cycle(
        inputs(auth, monitoring=HeartbeatAssessment(HeartbeatStatus.NEVER_STARTED))
    )
    assert result.reason is CycleReason.MONITORING_UNHEALTHY


def test_probe_a_completed_cycle_cannot_be_replayed(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    for cycle_id in ("cyc-1",):
        with pytest.raises(OrchestratorError):
            orch.run_cycle(inputs(auth, cycle_id=cycle_id))
    orch.close(); scheduler.close(); controller.close()


def test_probe_finalizing_with_only_one_store_updated_is_refused(tmp_path):
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.CONTROLLER_APPLIED,
        apply_scheduler=True, apply_controller=False,
    )
    # The journal says a controller effect was required, but the controller
    # store does not carry it. Finalizing would be a lie.
    orch._connection.execute(
        "UPDATE cycle_journal SET controller_effect_required = 1 WHERE cycle_id = ?",
        ("cyc-crash",),
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.AMBIGUOUS_RESTART
    orch.close(); scheduler.close(); controller.close()


def test_probe_scheduler_suspension_is_not_cleared_without_controller_clearance(
    tmp_path,
):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_POSITION_SIZE))
    )
    assert scheduler.load_run(RUN_ID).state is RunState.SUSPENDED
    assert controller.load(CONTROLLER_ID).state is ControllerState.SUSPENDED

    # A later clean cycle does not undo either.
    orch.run_cycle(
        inputs(auth, cycle_id="cyc-2", scheduler_transition_id="sch-2",
               controller_transition_id="ctlx-2",
               suspension=suspension(ControllerState.SUSPENDED))
    )
    assert scheduler.load_run(RUN_ID).state is RunState.SUSPENDED
    assert controller.load(CONTROLLER_ID).state is ControllerState.SUSPENDED
    orch.close(); scheduler.close(); controller.close()


def test_probe_a_persistence_exception_is_never_swallowed(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)

    def explode(*args, **kwargs):
        raise sqlite3.OperationalError("disk is on fire")

    scheduler.apply_transition = explode  # type: ignore[method-assign]
    result = orch.run_cycle(
        inputs(auth, risk=RiskAssessment(RiskDecision.DENIED_POSITION_SIZE))
    )
    assert result.outcome is not CycleOutcome.OFFLINE_CHECKS_COMPLETE
    assert result.reason is CycleReason.PERSISTENCE_FAILED
    orch.close(); scheduler.close(); controller.close()


def test_an_operational_failure_suspends_and_an_evidence_gap_asks_for_review():
    """The split is deliberate.

    A breached limit, a dead monitor, an unresolved reconciliation and an
    undelivered alert are things that went wrong in the world; work stops. A
    missing attestation or a closed gate means nobody has established that work
    may start — the normal state of this system today, and halting on it would
    make suspension mean nothing.
    """
    from src.offline_safety_orchestrator import SUSPENDING_REASONS

    auth = authority()
    halting = {
        CycleReason.RISK_DENIED: dict(
            risk=RiskAssessment(RiskDecision.DENIED_DAILY_LOSS)
        ),
        CycleReason.RECONCILIATION_UNRESOLVED: dict(
            recovery=RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_STALE)
        ),
        CycleReason.MONITORING_UNHEALTHY: dict(
            monitoring=HeartbeatAssessment(HeartbeatStatus.MISSING)
        ),
    }
    for reason, override in halting.items():
        result = decide_cycle(inputs(auth, **override))
        assert result.reason is reason
        assert result.outcome is CycleOutcome.SUSPENDED
        assert reason in SUSPENDING_REASONS

    # An evidence gap asks, it does not halt.
    ungrounded = good_attestations(auth)
    ungrounded["protection_grounding_complete"] = auth.issue(
        producer_id="operator-console", fact_name="protection_grounding_complete",
        satisfied=False, source=EvidenceSource.OPERATOR_ATTESTED,
        recorded_monotonic=MONOTONIC,
    )
    for override in ({}, dict(attestations=ungrounded)):
        result = decide_cycle(inputs(auth, **override))
        assert result.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW
        assert result.reason not in SUSPENDING_REASONS


def test_a_cycle_that_moved_neither_store_still_finalizes_after_a_crash(tmp_path):
    """The common case must not read as a partial application.

    A cycle whose verdict implies no transition records
    `scheduler_effect_required=0`. Without that flag a restart would look for a
    transition nobody intended to make and call the cycle ambiguous.
    """
    orch, scheduler, controller = crashed_at(
        tmp_path, CyclePhase.CONTROLLER_APPLIED,
        apply_scheduler=False, apply_controller=False,
        scheduler_effect_required=False,
    )
    record = orch.recover_cycle("cyc-crash", now=NOW)
    assert record.phase is CyclePhase.FINALIZED
    orch.close(); scheduler.close(); controller.close()


def test_a_no_effect_cycle_records_that_no_effect_was_required(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    # A CREATED run with a REQUIRE_OPERATOR_REVIEW verdict moves nothing.
    orch.run_cycle(inputs(auth))
    record = orch.load_cycle("cyc-1")
    assert record.scheduler_effect_required is False
    assert record.controller_effect_required is False
    assert record.phase is CyclePhase.FINALIZED
    orch.close(); scheduler.close(); controller.close()


def test_a_malformed_effect_flag_fails_closed(tmp_path):
    auth = authority()
    orch, scheduler, controller = stack(tmp_path)
    orch.run_cycle(inputs(auth))
    orch._connection.execute("PRAGMA ignore_check_constraints=ON")
    orch._connection.execute(
        "UPDATE cycle_journal SET scheduler_effect_required = 9 WHERE cycle_id = ?",
        ("cyc-1",),
    )
    with pytest.raises(OrchestratorError):
        orch.load_cycle("cyc-1")
    orch.close(); scheduler.close(); controller.close()


def test_probe_every_dominance_level_is_reachable():
    """A level nothing can reach is a rung that does not exist."""
    auth = authority()
    reachable = {
        CycleReason.PERMANENT_HALT: dict(
            suspension=suspension(ControllerState.PERMANENT_HALT)
        ),
        CycleReason.ACTIVE_SUSPENSION: dict(
            suspension=suspension(ControllerState.SUSPENDED)
        ),
        CycleReason.MALFORMED_DURABLE_STATE: dict(
            suspension=suspension(ControllerState.MALFORMED_STATE)
        ),
        CycleReason.RECONCILIATION_UNRESOLVED: dict(
            recovery=RecoveryResult(RecoveryOutcome.OPERATOR_REQUIRED_JOURNAL_ABSENT)
        ),
        CycleReason.RISK_DENIED: dict(
            risk=RiskAssessment(RiskDecision.DENIED_DAILY_LOSS)
        ),
        CycleReason.MONITORING_UNHEALTHY: dict(
            monitoring=HeartbeatAssessment(HeartbeatStatus.MISSING)
        ),
        CycleReason.CALENDAR_UNUSABLE: dict(
            session=classify_calendar(datetime(2025, 1, 2, 10, 0, tzinfo=EASTERN))
        ),
        CycleReason.GATE_CLOSED: {},
    }
    for expected, override in reachable.items():
        assert decide_cycle(inputs(auth, **override)).reason is expected
