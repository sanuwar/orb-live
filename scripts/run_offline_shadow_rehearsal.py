"""Deterministic, finite rehearsal of the public ORB-LIVE offline chain."""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Sequence

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if os.fspath(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(REPOSITORY_ROOT))

from src.attestation_authority import AttestationAuthority, ProducerIdentity
from src.config import load_config
from src.durable_alert_outbox import (
    AlertCategory, AlertIntent, AlertReason, AlertSeverity, AlertState,
    DeliveryEvidence, DeliveryProvenance, DurableAlertOutbox, OutboxPolicy,
)
from src.durable_runner_liveness import (
    DurableRunnerLiveness, FreshnessPolicy, RunnerHeartbeat,
)
from src.durable_scheduler import DurableScheduler, SchedulerAction
from src.durable_suspension_controller import (
    DurableSuspensionController, SuspensionCause,
)
from src.exchange_calendar import EASTERN, classify
from src.execution_gates import (
    ACTIVATION_APPROVALS, EvidenceSource, GateDecision, GateMode, GateVerdict,
)
from src.offline_cycle_driver import (
    AttestationBundle, DriverProviders, DriverResult, OfflineCycleDriver,
    ProviderKind, ProviderResult,
)
from src.offline_reconciliation import RecoveryOutcome, RecoveryResult
from src.offline_safety_orchestrator import (
    CycleResult, OfflineSafetyOrchestrator, REQUIRED_CYCLE_ATTESTATIONS,
)
from src.offline_shadow_cli import CliDependencies, CliReceipt, exit_code_for_result, run_cli
from src.offline_shadow_runner import (
    BoundedOfflineShadowRunner, ReasonSetting, RunnerConfig, RunnerMode,
    RunnerOutcome, RunnerResult, derive_driver_tick,
)
from src.operational_monitor import HeartbeatAssessment, HeartbeatStatus
from src.operational_status_report import StatusContext, StatusInputs, project_status
from src.operator_acknowledgment import (
    AcknowledgmentCategory, AcknowledgmentRequest, AcknowledgmentSourceKind,
    AttestedProvenance, OperatorAcknowledgmentArtifact,
    OperatorAcknowledgmentStore,
)
from src.risk_limits import RiskAssessment, RiskDecision
from src.read_only_broker_state import approved_active_protection_states
from src.shadow_readiness_report import (
    AssessmentScope, EvidenceProvenance, HistoryProvenance, RatioBucket,
    ReadinessContext, ReadinessEvidence, ReadinessEvidenceAttestation,
    ShadowHistory, ShadowHistoryPolicy, evidence_fingerprint,
    history_fingerprint, project_readiness,
)


NOW = datetime(2026, 8, 3, 14, 0, tzinfo=timezone.utc)
SESSION_DATE = date(2026, 8, 3)
MONOTONIC = 1_000.0
_KEY = b"offline-shadow-rehearsal-key-000000000000"
_SCENARIOS = (
    "safe_bounded_completion", "operator_review_required", "active_suspension",
    "ambiguous_restart", "missed_heartbeat", "failed_alert_confirmation",
    "acknowledgment_preserves_suspension", "readiness_live_blocked",
    "stale_evidence", "restart_and_recovery",
)


@dataclass(frozen=True)
class ScenarioResult:
    scenario: str
    expected_state: str
    actual_state: str
    durable_files: tuple[str, ...]
    privacy_valid: bool
    safety_invariants: bool

    @property
    def passed(self) -> bool:
        return (
            self.expected_state == self.actual_state
            and self.privacy_valid
            and self.safety_invariants
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "scenario": self.scenario,
            "expected_state": self.expected_state,
            "actual_state": self.actual_state,
            "pass": self.passed,
            "durable_files": list(self.durable_files),
            "privacy_validation": self.privacy_valid,
            "safety_invariants": self.safety_invariants,
        }


@dataclass(frozen=True)
class RehearsalReport:
    scenarios: tuple[ScenarioResult, ...]
    permits_automated_action: bool = False
    protection_grounded: bool = False
    activation_approved: bool = False

    def __post_init__(self) -> None:
        if tuple(item.scenario for item in self.scenarios) != _SCENARIOS:
            raise ValueError("rehearsal scenarios are incomplete or out of order")
        if any((self.permits_automated_action, self.protection_grounded, self.activation_approved)):
            raise ValueError("rehearsal cannot authorize or ground live operation")

    def to_dict(self) -> dict[str, object]:
        return {
            "scenarios": [item.to_dict() for item in self.scenarios],
            "all_passed": all(item.passed for item in self.scenarios),
            "permits_automated_action": False,
            "protection_grounded": False,
            "activation_approved": False,
        }

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


class _StaticProvider:
    def __init__(self, kind: ProviderKind, decision: object) -> None:
        self.kind = kind
        self.decision = decision

    def collect(self, context: object) -> ProviderResult:
        return ProviderResult(
            kind=self.kind, driver_id=context.driver_id, cycle_id=context.cycle_id,
            scheduler_run_id=context.scheduler_run_id,
            controller_id=context.controller_id, observed_at=context.now,
            sequence=1, decision=self.decision,
        )


class _DeliveryVerifier:
    verifier_id = "verifier-rehearsal"

    def verify(self, evidence: DeliveryEvidence) -> bool:
        return (
            evidence.provenance is DeliveryProvenance.VERIFIED_ATTESTATION
            and evidence.evidence_id == "delivery-evidence-rehearsal"
        )


class _AcknowledgmentVerifier:
    verifier_id = "ack-verifier-rehearsal"

    def verify(self, artifact: OperatorAcknowledgmentArtifact) -> bool:
        return (
            artifact.provenance is AttestedProvenance.VERIFIED_ATTESTATION
            and artifact.signature_present
            and artifact.artifact_id == "ack-artifact-rehearsal"
        )


class _ReadinessVerifier:
    verifier_id = "readiness-verifier-rehearsal"

    def verify(self, attestation: ReadinessEvidenceAttestation) -> bool:
        return attestation.provenance is EvidenceProvenance.VERIFIED_ATTESTATION


@dataclass
class _Stack:
    root: Path
    config: RunnerConfig
    driver: OfflineCycleDriver
    orchestrator: OfflineSafetyOrchestrator
    scheduler: DurableScheduler
    controller: DurableSuspensionController

    def close(self) -> None:
        with ExitStack() as cleanup:
            cleanup.callback(self.controller.close)
            cleanup.callback(self.scheduler.close)
            cleanup.callback(self.orchestrator.close)
            cleanup.callback(self.driver.close)

    def __enter__(self) -> "_Stack":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _authority() -> AttestationAuthority:
    specs = {
        "risk-engine": ({"risk_within_limits"}, {EvidenceSource.MEASURED}),
        "recovery-probe": ({"recovery_consistent"}, {EvidenceSource.MEASURED}),
        "monitor": ({"monitoring_healthy"}, {EvidenceSource.MEASURED}),
        "alert-harness": ({"alerting_verified_reachable"}, {EvidenceSource.TESTED}),
        "kill-switch-probe": ({"kill_switch_disengaged"}, {EvidenceSource.MEASURED}),
        "calendar-probe": ({"market_session_valid"}, {EvidenceSource.MEASURED}),
        "operator-console": ({"protection_grounding_complete"}, {EvidenceSource.OPERATOR_ATTESTED}),
    }
    return AttestationAuthority(_KEY, {
        name: ProducerIdentity(name, frozenset(facts), frozenset(sources))
        for name, (facts, sources) in specs.items()
    })


def _providers(*, regular: bool = False) -> DriverProviders:
    producers = {
        "risk_within_limits": ("risk-engine", EvidenceSource.MEASURED),
        "recovery_consistent": ("recovery-probe", EvidenceSource.MEASURED),
        "monitoring_healthy": ("monitor", EvidenceSource.MEASURED),
        "alerting_verified_reachable": ("alert-harness", EvidenceSource.TESTED),
        "kill_switch_disengaged": ("kill-switch-probe", EvidenceSource.MEASURED),
        "market_session_valid": ("calendar-probe", EvidenceSource.MEASURED),
        "protection_grounding_complete": ("operator-console", EvidenceSource.OPERATOR_ATTESTED),
    }
    authority = _authority()
    signed = tuple(authority.issue(
        producer_id=producers[fact][0], fact_name=fact, satisfied=True,
        source=producers[fact][1], recorded_monotonic=MONOTONIC,
    ) for fact in REQUIRED_CYCLE_ATTESTATIONS)
    calendar_time = datetime(2026, 8, 3, 10 if regular else 5, 0, tzinfo=EASTERN)
    values = {
        ProviderKind.CALENDAR: classify(calendar_time),
        ProviderKind.RECONCILIATION: RecoveryResult(RecoveryOutcome.NO_PENDING_WORK),
        ProviderKind.RISK: RiskAssessment(RiskDecision.WITHIN_LIMITS),
        ProviderKind.MONITORING: HeartbeatAssessment(HeartbeatStatus.HEALTHY),
        ProviderKind.GATE: GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.SUPERVISED,
            evidence_verified=True,
        ),
        ProviderKind.ATTESTATIONS: AttestationBundle(signed, authority, MONOTONIC),
    }
    made = {kind: _StaticProvider(kind, value) for kind, value in values.items()}
    return DriverProviders(
        calendar=made[ProviderKind.CALENDAR],
        reconciliation=made[ProviderKind.RECONCILIATION],
        risk=made[ProviderKind.RISK], monitoring=made[ProviderKind.MONITORING],
        gate=made[ProviderKind.GATE], attestations=made[ProviderKind.ATTESTATIONS],
    )


def _stack(
    root: Path, runner_id: str, *, regular: bool = False,
    suspend: bool = False, tick_count: int = 1,
    tick_timestamps: tuple[datetime, ...] | None = None,
    maximum_tick_count: int | None = None,
) -> _Stack:
    root.mkdir(parents=True, exist_ok=False)
    config = RunnerConfig(
        runner_id=runner_id, maximum_tick_count=maximum_tick_count or tick_count,
        tick_timestamps=(tick_timestamps if tick_timestamps is not None else tuple(
            NOW + timedelta(minutes=index) for index in range(tick_count)
        )),
        session_date=SESSION_DATE, mode=RunnerMode.OFFLINE_SHADOW,
        runner_database_path=(root / "runner.sqlite").resolve(),
        driver_database_path=(root / "driver.sqlite").resolve(),
        reason_setting=ReasonSetting.FIXED_CODES_ONLY,
    )
    with ExitStack() as cleanup:
        scheduler = DurableScheduler((root / "scheduler.sqlite").resolve())
        cleanup.callback(scheduler.close)
        controller = DurableSuspensionController((root / "controller.sqlite").resolve())
        cleanup.callback(controller.close)
        for index, stamp in enumerate(config.tick_timestamps):
            controller.register(derive_driver_tick(config, index).controller_id, now=stamp)
        tick = derive_driver_tick(config, 0)
        if suspend:
            controller.suspend(
                tick.controller_id, cause=SuspensionCause.OPERATOR_HALT,
                transition_id=tick.controller_transition_id, now=NOW,
            )
        orchestrator = OfflineSafetyOrchestrator(
            (root / "orchestrator.sqlite").resolve(), scheduler=scheduler,
            controller=controller,
        )
        cleanup.callback(orchestrator.close)
        driver = OfflineCycleDriver(
            config.driver_database_path, scheduler=scheduler, controller=controller,
            orchestrator=orchestrator, providers=_providers(regular=regular),
        )
        cleanup.callback(driver.close)
        stack = _Stack(root, config, driver, orchestrator, scheduler, controller)
        cleanup.pop_all()
        return stack


def _reopen_stack(root: Path, config: RunnerConfig) -> _Stack:
    with ExitStack() as cleanup:
        scheduler = DurableScheduler((root / "scheduler.sqlite").resolve())
        cleanup.callback(scheduler.close)
        controller = DurableSuspensionController((root / "controller.sqlite").resolve())
        cleanup.callback(controller.close)
        orchestrator = OfflineSafetyOrchestrator(
            (root / "orchestrator.sqlite").resolve(), scheduler=scheduler,
            controller=controller,
        )
        cleanup.callback(orchestrator.close)
        driver = OfflineCycleDriver(
            config.driver_database_path, scheduler=scheduler, controller=controller,
            orchestrator=orchestrator, providers=_providers(),
        )
        cleanup.callback(driver.close)
        stack = _Stack(root, config, driver, orchestrator, scheduler, controller)
        cleanup.pop_all()
        return stack


def _cli(stack: _Stack) -> tuple[int, RunnerResult]:
    receipt = stack.root / "receipt.json"
    args = [
        "--runner-id", stack.config.runner_id,
        "--max-ticks", str(stack.config.maximum_tick_count),
        "--session-date", SESSION_DATE.isoformat(),
        "--runner-db", os.fspath(stack.config.runner_database_path),
        "--driver-db", os.fspath(stack.config.driver_database_path),
        "--scheduler-db", os.fspath((stack.root / "scheduler.sqlite").resolve()),
        "--controller-db", os.fspath((stack.root / "controller.sqlite").resolve()),
        "--orchestrator-db", os.fspath((stack.root / "orchestrator.sqlite").resolve()),
        "--receipt", os.fspath(receipt.resolve()), "--mode", "offline-shadow",
    ]
    for stamp in stack.config.tick_timestamps:
        args.extend(("--tick", stamp.isoformat()))
    stdout = io.StringIO()
    code = run_cli(
        args,
        dependencies=CliDependencies(
            lambda config: BoundedOfflineShadowRunner(config, driver=stack.driver)
        ), stdout=stdout, stderr=io.StringIO(),
    )
    reopened = BoundedOfflineShadowRunner(stack.config, driver=stack.driver)
    try:
        result = reopened.execute()
    finally:
        reopened.close()
    expected = CliReceipt(
        result.runner_id, result.state, result.outcome, result.reason,
        result.completed_tick_count, result.next_tick_index,
        result.transition_sequence, exit_code_for_result(result),
    ).to_dict()
    if json.loads(receipt.read_text(encoding="utf-8")) != expected:
        raise RuntimeError("CLI receipt identity binding failed")
    if json.loads(stdout.getvalue()) != expected:
        raise RuntimeError("CLI published output conflicts with its durable receipt")
    return code, result


def _files(root: Path) -> tuple[str, ...]:
    return tuple(sorted(
        path.name for path in root.iterdir()
        if path.is_file() and path.suffix.casefold() in {".sqlite", ".json"}
    ))


def _privacy_safe_projection(
    name: str, expected: str, actual: str, files: tuple[str, ...],
) -> bool:
    fixed_value = lambda value: bool(value) and value.replace("_", "").isalnum()
    return (
        name in _SCENARIOS and fixed_value(expected) and fixed_value(actual)
        and all(
            Path(value).name == value
            and Path(value).suffix.casefold() in {".sqlite", ".json"}
            for value in files
        )
    )


def _scenario(
    name: str, expected: str, actual: str, root: Path,
    global_safety: bool, *values: object,
) -> ScenarioResult:
    permissions = tuple(
        value.permits_automated_action for value in values
        if hasattr(value, "permits_automated_action")
    )
    inert = bool(permissions) and all(value is False for value in permissions)
    files = _files(root)
    return ScenarioResult(
        name, expected, actual, files,
        _privacy_safe_projection(name, expected, actual, files),
        global_safety and inert,
    )


def run_rehearsal(
    root: Path, *, tick_timestamps: tuple[datetime, ...] | None = None,
    maximum_tick_count: int | None = None,
) -> RehearsalReport:
    """Run all scenarios once beneath an empty, caller-supplied directory."""
    root = Path(root).resolve(strict=False)
    if root.exists() or root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents:
        raise ValueError("rehearsal root must be a new directory outside the repository")
    root.mkdir(parents=True)
    results: list[ScenarioResult] = []
    strategy = load_config()["strategy"]
    global_safety = (
        isinstance(strategy, dict) and strategy.get("enabled") is False
        and not ACTIVATION_APPROVALS
        and not approved_active_protection_states()
    )
    live = ExitStack()
    try:
        safe = live.enter_context(_stack(
            root / "safe", "runner-rehearsal-safe", tick_count=2,
            tick_timestamps=tick_timestamps, maximum_tick_count=maximum_tick_count,
        ))
        review = live.enter_context(_stack(root / "review", "runner-rehearsal-review", regular=True))
        suspended = live.enter_context(_stack(root / "suspended", "runner-rehearsal-suspended", suspend=True))
        safe_code, safe_result = _cli(safe)
        review_code, review_result = _cli(review)
        suspended_code, suspended_result = _cli(suspended)
        results.append(_scenario(_SCENARIOS[0], "COMPLETED_BOUND", safe_result.outcome.value, safe.root, global_safety, safe_result))
        results.append(_scenario(_SCENARIOS[1], "STOPPED_ON_REVIEW", review_result.outcome.value, review.root, global_safety, review_result))
        results.append(_scenario(_SCENARIOS[2], "STOPPED_ON_SUSPENSION", suspended_result.outcome.value, suspended.root, global_safety, suspended_result))
        if (safe_code, review_code, suspended_code) != (0, 10, 20):
            raise RuntimeError("CLI exit-code contract failed")

        liveness_root = root / "liveness"
        liveness_root.mkdir()
        policy = FreshnessPolicy(timedelta(minutes=1), timedelta(seconds=10), timedelta(minutes=2), 2)
        monitor = live.enter_context(DurableRunnerLiveness((liveness_root / "liveness.sqlite").resolve(), policy=policy))
        ambiguous = monitor.evaluate(
            "runner-rehearsal-ambiguous",
            heartbeat=RunnerHeartbeat(
                "runner-rehearsal-ambiguous", 1, NOW, NOW + timedelta(seconds=5),
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
            ), expected_cycle_timestamp=NOW, evaluation_time=NOW + timedelta(seconds=10),
            alert_delivery_confirmed=True,
            transition_id="liveness-transition-ambiguous",
        )
        results.append(_scenario(_SCENARIOS[3], "SUSPENSION_REQUIRED", ambiguous.record.state.value, liveness_root, global_safety, ambiguous))
        monitor.evaluate(
            "runner-rehearsal-missed", heartbeat=None, expected_cycle_timestamp=NOW,
            evaluation_time=NOW + timedelta(minutes=2), alert_delivery_confirmed=True,
            transition_id="liveness-transition-missed-one",
        )
        missed = monitor.evaluate(
            "runner-rehearsal-missed", heartbeat=None,
            expected_cycle_timestamp=NOW + timedelta(minutes=1),
            evaluation_time=NOW + timedelta(minutes=3), alert_delivery_confirmed=True,
            transition_id="liveness-transition-missed-two",
        )
        results.append(_scenario(_SCENARIOS[4], "SUSPENSION_REQUIRED", missed.record.state.value, liveness_root, global_safety, missed))
        stale = monitor.evaluate(
            "runner-rehearsal-stale", heartbeat=RunnerHeartbeat(
                "runner-rehearsal-stale", 1, NOW, NOW, RunnerOutcome.COMPLETED_BOUND,
            ), expected_cycle_timestamp=NOW, evaluation_time=NOW + timedelta(minutes=3),
            alert_delivery_confirmed=True, transition_id="liveness-transition-stale",
        )

        outbox_root = root / "outbox"
        outbox_root.mkdir()
        outbox = live.enter_context(DurableAlertOutbox(
            (outbox_root / "outbox.sqlite").resolve(),
            OutboxPolicy(timedelta(minutes=5), timedelta(minutes=2), 3),
            _DeliveryVerifier(),
        ))
        outbox.create(AlertIntent(
            "alert-rehearsal", AlertCategory.STATUS_DEGRADED, AlertSeverity.WARNING,
            AlertReason.STATUS_PROJECTION, NOW, "correlation-rehearsal",
        ), transition_id="alert-transition-create")
        outbox.ready("alert-rehearsal", transition_id="alert-transition-ready", now=NOW + timedelta(seconds=1))
        outbox.claim("alert-rehearsal", claim_id="claim-rehearsal", transition_id="alert-transition-claim", now=NOW + timedelta(seconds=2))
        alert = outbox.submit_delivery(DeliveryEvidence(
            "delivery-evidence-rehearsal", "alert-rehearsal", "claim-rehearsal",
            NOW + timedelta(seconds=3), False, DeliveryProvenance.VERIFIED_ATTESTATION,
        ), transition_id="alert-transition-unconfirmed", now=NOW + timedelta(seconds=4))
        results.append(_scenario(_SCENARIOS[5], "DELIVERY_UNCONFIRMED", alert.state.value, outbox_root, global_safety, alert))

        ack_root = root / "acknowledgment"
        ack_root.mkdir()
        ack_store = OperatorAcknowledgmentStore((ack_root / "ack.sqlite").resolve(), _AcknowledgmentVerifier())
        live.callback(ack_store.close)
        ack_store.create(AcknowledgmentRequest(
            "ack-rehearsal", "review-item-rehearsal", 1,
            AcknowledgmentCategory.SUSPENSION_NOTICE, NOW, NOW + timedelta(hours=1),
            "correlation-rehearsal", AcknowledgmentSourceKind.REVIEW_ITEM,
            "source-review-item-rehearsal", 1,
        ), transition_id="ack-transition-create")
        ack = ack_store.acknowledge(OperatorAcknowledgmentArtifact(
            "ack-artifact-rehearsal", "ack-rehearsal", "review-item-rehearsal", 1,
            AcknowledgmentCategory.SUSPENSION_NOTICE, True,
            AttestedProvenance.VERIFIED_ATTESTATION, True,
            NOW + timedelta(minutes=1), NOW + timedelta(minutes=30),
        ), transition_id="ack-transition-aware", now=NOW + timedelta(minutes=2))
        suspension_after_ack = suspended.controller.assess(derive_driver_tick(suspended.config, 0).controller_id)
        actual = "ACKNOWLEDGED_SUSPENSION_ACTIVE" if ack.acknowledgment_obligation_closed and suspension_after_ack.blocks_work else "INVALID"
        results.append(_scenario(_SCENARIOS[6], "ACKNOWLEDGED_SUSPENSION_ACTIVE", actual, ack_root, global_safety, ack, suspension_after_ack))

        tick = derive_driver_tick(safe.config, 0)
        safe.controller.suspend(
            tick.controller_id, cause=SuspensionCause.OPERATOR_HALT,
            transition_id="controller-transition-readiness", now=NOW + timedelta(minutes=1),
        )
        readiness_suspension = safe.controller.assess(tick.controller_id)
        driver_record = safe.driver.load_record(tick.driver_id)
        cycle_record = safe.orchestrator.load_cycle(tick.cycle_id)
        scheduler_record = safe.scheduler.load_run(tick.scheduler_run_id)
        scheduler_receipt = safe.scheduler.inspect_transition(tick.scheduler_transition_id)
        controller_record = safe.controller.load(tick.controller_id)
        controller_receipt = safe.controller.inspect_transition("controller-transition-readiness")
        liveness = monitor.evaluate(
            safe.config.runner_id,
            heartbeat=RunnerHeartbeat(safe.config.runner_id, 1, NOW, NOW, safe_result.outcome),
            expected_cycle_timestamp=NOW, evaluation_time=NOW + timedelta(seconds=5),
            alert_delivery_confirmed=True, transition_id="liveness-transition-suspended",
        )
        driver_result = DriverResult(
            driver_record.state, driver_record.outcome, driver_record.reason,
            driver_record.orchestrator_outcome, driver_record.orchestrator_reason,
        )
        cycle_result = CycleResult(cycle_record.outcome, cycle_record.reason, SchedulerAction.WAIT)
        status = project_status(StatusContext(
            "status-rehearsal", safe.config.runner_id, scheduler_record.run_id,
            NOW + timedelta(seconds=5), timedelta(minutes=10),
        ), StatusInputs(
            safe_result, driver_result, cycle_result, scheduler_record,
            readiness_suspension, liveness,
        ))
        history = ShadowHistory(
            "shadow-history-rehearsal", safe.config.runner_id,
            safe_result.completed_tick_count, 0, 0, 0, NOW,
            safe_result.completed_tick_count, safe_result.transition_sequence,
            tuple(f"shadow-transition-completed-{i}" for i in range(safe_result.completed_tick_count)),
            (), (), (),
        )
        placeholder = ReadinessEvidenceAttestation(
            "readiness-attestation-rehearsal", "evidence-set-rehearsal", "0" * 64,
            NOW + timedelta(minutes=2), EvidenceProvenance.VERIFIED_ATTESTATION,
            history_fingerprint(history), HistoryProvenance.DURABLE_OFFLINE_RUNTIME,
            "history-producer-rehearsal",
        )
        evidence = ReadinessEvidence(
            history, safe_result, liveness, status, (), (),
            readiness_suspension, controller_record, controller_receipt,
            scheduler_record, scheduler_receipt, cycle_result,
            GateDecision(GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.SUPERVISED, evidence_verified=True),
            NOW, NOW, NOW, placeholder,
        )
        evidence = replace(evidence, attestation=replace(
            placeholder, evidence_fingerprint=evidence_fingerprint(evidence),
        ))
        readiness = project_readiness(ReadinessContext(
            "readiness-rehearsal", AssessmentScope.LIVE, NOW + timedelta(minutes=2),
            timedelta(minutes=10), "correlation-rehearsal", "evidence-set-rehearsal",
            _ReadinessVerifier.verifier_id,
        ), ShadowHistoryPolicy(2, 0, 0, RatioBucket.COMPLETE), evidence, _ReadinessVerifier())
        required_reasons = {
            "ACTIVE_SUSPENSION", "OPERATIONAL_REVIEW_REQUIRED",
            "EXECUTION_GATE_CLOSED",
            "PROTECTION_NOT_GROUNDED", "ACTIVATION_APPROVALS_EMPTY",
            "SCHEDULER_REEVALUATION_REQUIRED", "COMPONENT_WAITING",
        }
        readiness_exact = (
            readiness.status.value == "SUPERVISED_BLOCKED"
            and {reason.value for reason in readiness.reason_codes} == required_reasons
            and readiness.live_ready is False
            and readiness.unattended_ready is False
            and readiness.protection_grounded is False
            and readiness.activation_approved is False
            and readiness.permits_automated_action is False
        )
        results.append(_scenario(
            _SCENARIOS[7], "SUPERVISED_BLOCKED",
            readiness.status.value if readiness_exact else "INVALID",
            safe.root, global_safety, readiness,
        ))
        results.append(_scenario(_SCENARIOS[8], "STALE", stale.record.state.value, liveness_root, global_safety, stale))
        safe_ticks = tuple(
            derive_driver_tick(safe.config, index)
            for index in range(len(safe.config.tick_timestamps))
        )
        durable_snapshot = tuple(
            (
                safe.driver.load_record(item.driver_id),
                safe.orchestrator.load_cycle(item.cycle_id),
                safe.scheduler.load_run(item.scheduler_run_id),
                safe.scheduler.inspect_transition(item.scheduler_transition_id),
                safe.controller.load(item.controller_id),
            )
            for item in safe_ticks
        )
    finally:
        live.close()

    with ExitStack() as restarted_resources:
        restarted_stack = restarted_resources.enter_context(_reopen_stack(safe.root, safe.config))
        restarted = BoundedOfflineShadowRunner(safe.config, driver=restarted_stack.driver)
        restarted_resources.callback(restarted.close)
        recovered = restarted.execute()
        restarted_monitor = restarted_resources.enter_context(DurableRunnerLiveness(
            (liveness_root / "liveness.sqlite").resolve(), policy=policy,
        ))
        persisted_liveness = restarted_monitor.load_record("liveness-transition-ambiguous")
        restarted_outbox = restarted_resources.enter_context(DurableAlertOutbox(
            (outbox_root / "outbox.sqlite").resolve(),
            OutboxPolicy(timedelta(minutes=5), timedelta(minutes=2), 3),
            _DeliveryVerifier(),
        ))
        persisted_alert = restarted_outbox.load("alert-rehearsal")
        restarted_ack = OperatorAcknowledgmentStore(
            (ack_root / "ack.sqlite").resolve(), _AcknowledgmentVerifier(),
        )
        restarted_resources.callback(restarted_ack.close)
        persisted_ack = restarted_ack.load("ack-rehearsal")
        reopened_snapshot = tuple(
            (
                restarted_stack.driver.load_record(item.driver_id),
                restarted_stack.orchestrator.load_cycle(item.cycle_id),
                restarted_stack.scheduler.load_run(item.scheduler_run_id),
                restarted_stack.scheduler.inspect_transition(item.scheduler_transition_id),
                restarted_stack.controller.load(item.controller_id),
            )
            for item in safe_ticks
        )
        recovered_ok = all((
            recovered == safe_result,
            reopened_snapshot == durable_snapshot,
            restarted_stack.controller.inspect_transition("controller-transition-readiness") == controller_receipt,
            persisted_liveness == ambiguous.record,
            persisted_alert == alert,
            persisted_ack == ack.record,
        ))
        results.append(_scenario(
            _SCENARIOS[9], "RECOVERED", "RECOVERED" if recovered_ok else "INVALID",
            safe.root, global_safety, recovered, persisted_alert, persisted_ack,
        ))

    return RehearsalReport(tuple(results))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_rehearsal(args.root)
    except Exception:
        print('{"all_passed":false,"failed_closed":true}', file=sys.stderr)
        return 1
    print(report.serialize())
    return 0 if all(item.passed for item in report.scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
