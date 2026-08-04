"""Deterministic privacy-safe readiness projection for the offline shadow stack.

The projection evaluates offline and supervised evidence.  It cannot express
live or unattended approval, and it has no persistence or collaborator write
surface.  Stored projections round-trip through an exact allowlisted mapping.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from src.durable_alert_outbox import AcknowledgmentStatus as AlertAcknowledgmentStatus, AlertRecord, AlertState
from src.durable_runner_liveness import LivenessDecision, LivenessState
from src.durable_scheduler import RunState, ScheduledRun, SchedulerAction, SchedulerTransitionReceipt
from src.durable_suspension_controller import ControllerRecord, ControllerState, SuspensionDecision, SuspensionTransitionReceipt
from src.execution_gates import AUTHORITY_EXPLANATION, GateDecision, GateMode, GateVerdict
from src.offline_safety_orchestrator import CycleOutcome, CycleResult
from src.offline_shadow_runner import RunnerOutcome, RunnerResult, RunnerState
from src.operational_status_report import OperationalStatusReport, OverallStatus
from src.operator_acknowledgment import AcknowledgmentRecord, AcknowledgmentState


class ReadinessError(RuntimeError):
    """Fixed privacy-safe readiness refusal."""


class ReadinessStatus(str, Enum):
    OFFLINE_HEALTHY = "OFFLINE_HEALTHY"
    OFFLINE_DEGRADED = "OFFLINE_DEGRADED"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    SUPERVISED_BLOCKED = "SUPERVISED_BLOCKED"
    LIVE_BLOCKED = "LIVE_BLOCKED"
    UNATTENDED_BLOCKED = "UNATTENDED_BLOCKED"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS = "AMBIGUOUS"


class ReadinessReason(str, Enum):
    PROTECTION_NOT_GROUNDED = "PROTECTION_NOT_GROUNDED"
    ACTIVATION_APPROVALS_EMPTY = "ACTIVATION_APPROVALS_EMPTY"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    MISSED_CYCLE = "MISSED_CYCLE"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    OPERATOR_ACK_PENDING = "OPERATOR_ACK_PENDING"
    FURTHER_REVIEW_REQUIRED = "FURTHER_REVIEW_REQUIRED"
    FAILED_COMPONENT = "FAILED_COMPONENT"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    FUTURE_EVIDENCE = "FUTURE_EVIDENCE"
    CONFLICTING_EVIDENCE = "CONFLICTING_EVIDENCE"
    EVIDENCE_ATTESTATION_INVALID = "EVIDENCE_ATTESTATION_INVALID"
    GATE_EVIDENCE_UNVERIFIED = "GATE_EVIDENCE_UNVERIFIED"
    INSUFFICIENT_SHADOW_HISTORY = "INSUFFICIENT_SHADOW_HISTORY"
    EXCESSIVE_CONSECUTIVE_FAILURES = "EXCESSIVE_CONSECUTIVE_FAILURES"
    EXCESSIVE_STALE_EVENTS = "EXCESSIVE_STALE_EVENTS"
    ALERT_CONFIRMATION_RATIO_LOW = "ALERT_CONFIRMATION_RATIO_LOW"
    OPERATIONAL_REVIEW_REQUIRED = "OPERATIONAL_REVIEW_REQUIRED"
    SCHEDULER_REEVALUATION_REQUIRED = "SCHEDULER_REEVALUATION_REQUIRED"
    EXECUTION_GATE_CLOSED = "EXECUTION_GATE_CLOSED"
    COMPONENT_WAITING = "COMPONENT_WAITING"
    COMPONENT_DEGRADED = "COMPONENT_DEGRADED"
    COMPONENT_EXPIRED = "COMPONENT_EXPIRED"
    OFFLINE_EVIDENCE_HEALTHY = "OFFLINE_EVIDENCE_HEALTHY"


_REASON_DOMINANCE = (
    ReadinessReason.FUTURE_EVIDENCE,
    ReadinessReason.CONFLICTING_EVIDENCE,
    ReadinessReason.EVIDENCE_ATTESTATION_INVALID,
    ReadinessReason.FAILED_COMPONENT,
    ReadinessReason.AMBIGUOUS_RESTART,
    ReadinessReason.ACTIVE_SUSPENSION,
    ReadinessReason.MISSED_CYCLE,
    ReadinessReason.ALERT_DELIVERY_UNCONFIRMED,
    ReadinessReason.OPERATOR_ACK_PENDING,
    ReadinessReason.FURTHER_REVIEW_REQUIRED,
    ReadinessReason.OPERATIONAL_REVIEW_REQUIRED,
    ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED,
    ReadinessReason.STALE_EVIDENCE,
    ReadinessReason.INSUFFICIENT_SHADOW_HISTORY,
    ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES,
    ReadinessReason.EXCESSIVE_STALE_EVENTS,
    ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW,
    ReadinessReason.EXECUTION_GATE_CLOSED,
    ReadinessReason.GATE_EVIDENCE_UNVERIFIED,
    ReadinessReason.COMPONENT_WAITING,
    ReadinessReason.COMPONENT_DEGRADED,
    ReadinessReason.COMPONENT_EXPIRED,
    ReadinessReason.PROTECTION_NOT_GROUNDED,
    ReadinessReason.ACTIVATION_APPROVALS_EMPTY,
    ReadinessReason.OFFLINE_EVIDENCE_HEALTHY,
)
_REASON_INDEX = {reason: index for index, reason in enumerate(_REASON_DOMINANCE)}


class AssessmentScope(str, Enum):
    OFFLINE = "OFFLINE"
    SUPERVISED = "SUPERVISED"
    LIVE = "LIVE"
    UNATTENDED = "UNATTENDED"


class CountBucket(str, Enum):
    ZERO = "ZERO"
    ONE = "ONE"
    MANY = "MANY"


class RatioBucket(str, Enum):
    ZERO = "ZERO"
    LOW = "LOW"
    HIGH = "HIGH"
    COMPLETE = "COMPLETE"


class EvidenceProvenance(str, Enum):
    VERIFIED_ATTESTATION = "VERIFIED_ATTESTATION"
    UNVERIFIED = "UNVERIFIED"
    SELF_DECLARED = "SELF_DECLARED"


class HistoryProvenance(str, Enum):
    DURABLE_OFFLINE_RUNTIME = "DURABLE_OFFLINE_RUNTIME"


@dataclass(frozen=True)
class ReadinessEvidenceAttestation:
    attestation_id: str
    evidence_set_id: str
    evidence_fingerprint: str
    observed_at: datetime
    provenance: EvidenceProvenance
    history_fingerprint: str
    history_provenance: HistoryProvenance
    history_producer_id: str
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.attestation_id, "readiness-attestation-", "attestation identity")
        _identifier(self.evidence_set_id, "evidence-set-", "evidence set identity")
        if not isinstance(self.evidence_fingerprint, str) or len(self.evidence_fingerprint) != 64 or any(character not in "0123456789abcdef" for character in self.evidence_fingerprint):
            raise ReadinessError("evidence fingerprint is invalid")
        _moment(self.observed_at, "attestation timestamp")
        if not isinstance(self.provenance, EvidenceProvenance):
            raise ReadinessError("evidence provenance is invalid")
        if not isinstance(self.history_fingerprint, str) or len(self.history_fingerprint) != 64 or any(character not in "0123456789abcdef" for character in self.history_fingerprint):
            raise ReadinessError("history fingerprint is invalid")
        if self.history_provenance is not HistoryProvenance.DURABLE_OFFLINE_RUNTIME:
            raise ReadinessError("history provenance is invalid")
        _identifier(self.history_producer_id, "history-producer-", "history producer identity")
        _false(self.permits_automated_action)


@runtime_checkable
class ReadinessEvidenceVerifier(Protocol):
    verifier_id: str

    def verify(self, attestation: ReadinessEvidenceAttestation) -> bool: ...


_RATIO_ORDER = {
    RatioBucket.ZERO: 0,
    RatioBucket.LOW: 1,
    RatioBucket.HIGH: 2,
    RatioBucket.COMPLETE: 3,
}


@dataclass(frozen=True)
class ShadowHistoryPolicy:
    minimum_completed_cycles: int
    maximum_consecutive_failures: int
    maximum_allowed_stale_events: int
    required_alert_confirmation_ratio_bucket: RatioBucket
    required_zero_ambiguous_restarts: bool = True

    def __post_init__(self) -> None:
        _nonnegative(self.minimum_completed_cycles, "minimum completed cycles")
        if self.minimum_completed_cycles < 2:
            raise ReadinessError("one successful cycle is insufficient history")
        _nonnegative(self.maximum_consecutive_failures, "maximum consecutive failures")
        _nonnegative(self.maximum_allowed_stale_events, "maximum stale events")
        if not isinstance(self.required_alert_confirmation_ratio_bucket, RatioBucket):
            raise ReadinessError("alert confirmation ratio policy is invalid")
        if self.required_zero_ambiguous_restarts is not True:
            raise ReadinessError("readiness requires zero ambiguous restarts")


@dataclass(frozen=True)
class ShadowHistory:
    history_id: str
    runner_id: str
    completed_cycles: int
    consecutive_failures: int
    stale_events: int
    ambiguous_restarts: int
    last_evidence_at: datetime
    transition_sequence: int
    runner_transition_sequence: int
    completed_cycle_transition_ids: tuple[str, ...]
    failure_transition_ids: tuple[str, ...]
    stale_transition_ids: tuple[str, ...]
    ambiguous_transition_ids: tuple[str, ...]
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.history_id, "shadow-history-", "history identity")
        _identifier(self.runner_id, "runner-", "runner identity")
        for value, label in (
            (self.completed_cycles, "completed cycles"),
            (self.consecutive_failures, "consecutive failures"),
            (self.stale_events, "stale events"),
            (self.ambiguous_restarts, "ambiguous restarts"),
        ):
            _nonnegative(value, label)
        _moment(self.last_evidence_at, "history timestamp")
        if not isinstance(self.transition_sequence, int) or isinstance(self.transition_sequence, bool) or self.transition_sequence < 1:
            raise ReadinessError("history transition sequence is invalid")
        if not isinstance(self.runner_transition_sequence, int) or isinstance(self.runner_transition_sequence, bool) or self.runner_transition_sequence < 1:
            raise ReadinessError("runner transition sequence is invalid")
        collections = (
            (self.completed_cycle_transition_ids, self.completed_cycles),
            (self.failure_transition_ids, self.consecutive_failures),
            (self.stale_transition_ids, self.stale_events),
            (self.ambiguous_transition_ids, self.ambiguous_restarts),
        )
        if any(not isinstance(values, tuple) or len(values) != count for values, count in collections):
            raise ReadinessError("history evidence counts conflict")
        identities = tuple(identity for values, _ in collections for identity in values)
        for identity in identities:
            _identifier(identity, "shadow-transition-", "history transition identity")
        if len(set(identities)) != len(identities) or self.transition_sequence < len(identities):
            raise ReadinessError("history transition evidence is not distinct")
        _false(self.permits_automated_action)


@dataclass(frozen=True)
class ReadinessContext:
    report_id: str
    scope: AssessmentScope
    evaluated_at: datetime
    maximum_evidence_age: timedelta
    correlation_id: str
    evidence_set_id: str
    verifier_id: str

    def __post_init__(self) -> None:
        _identifier(self.report_id, "readiness-", "readiness identity")
        if not isinstance(self.scope, AssessmentScope):
            raise ReadinessError("assessment scope is invalid")
        _moment(self.evaluated_at, "evaluation timestamp")
        if not isinstance(self.maximum_evidence_age, timedelta) or self.maximum_evidence_age <= timedelta(0):
            raise ReadinessError("evidence freshness bound is invalid")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        _identifier(self.evidence_set_id, "evidence-set-", "evidence set identity")
        _identifier(self.verifier_id, "readiness-verifier-", "verifier identity")


@dataclass(frozen=True)
class ReadinessEvidence:
    history: ShadowHistory
    runner: RunnerResult
    liveness: LivenessDecision
    operational_status: OperationalStatusReport
    alerts: tuple[AlertRecord, ...]
    acknowledgments: tuple[AcknowledgmentRecord, ...]
    suspension: SuspensionDecision
    suspension_record: ControllerRecord
    suspension_receipt: SuspensionTransitionReceipt
    scheduler: ScheduledRun
    scheduler_receipt: SchedulerTransitionReceipt
    orchestrator: CycleResult
    execution_gate: GateDecision
    runner_observed_at: datetime
    orchestrator_observed_at: datetime
    gate_evaluated_at: datetime
    attestation: ReadinessEvidenceAttestation

    def __post_init__(self) -> None:
        expected = (
            (self.history, ShadowHistory), (self.runner, RunnerResult),
            (self.liveness, LivenessDecision), (self.operational_status, OperationalStatusReport),
            (self.suspension, SuspensionDecision), (self.suspension_record, ControllerRecord),
            (self.suspension_receipt, SuspensionTransitionReceipt), (self.scheduler, ScheduledRun),
            (self.scheduler_receipt, SchedulerTransitionReceipt),
            (self.orchestrator, CycleResult), (self.execution_gate, GateDecision),
        )
        if any(not isinstance(value, kind) for value, kind in expected):
            raise ReadinessError("readiness input is not a public typed projection")
        if not isinstance(self.alerts, tuple) or any(not isinstance(value, AlertRecord) for value in self.alerts):
            raise ReadinessError("alert inputs are invalid")
        if not isinstance(self.acknowledgments, tuple) or any(not isinstance(value, AcknowledgmentRecord) for value in self.acknowledgments):
            raise ReadinessError("acknowledgment inputs are invalid")
        if len({value.alert_id for value in self.alerts}) != len(self.alerts):
            raise ReadinessError("alert identities are duplicated")
        if len({value.acknowledgment_id for value in self.acknowledgments}) != len(self.acknowledgments):
            raise ReadinessError("acknowledgment identities are duplicated")
        if tuple(value.alert_id for value in self.alerts) != tuple(sorted(value.alert_id for value in self.alerts)):
            raise ReadinessError("alert evidence order is not canonical")
        if tuple(value.acknowledgment_id for value in self.acknowledgments) != tuple(sorted(value.acknowledgment_id for value in self.acknowledgments)):
            raise ReadinessError("acknowledgment evidence order is not canonical")
        for value, label in (
            (self.runner_observed_at, "runner observation timestamp"),
            (self.orchestrator_observed_at, "orchestrator observation timestamp"),
            (self.gate_evaluated_at, "gate evaluation timestamp"),
        ):
            _moment(value, label)
        if not isinstance(self.attestation, ReadinessEvidenceAttestation):
            raise ReadinessError("typed readiness attestation is required")
        if self.execution_gate.explanation != AUTHORITY_EXPLANATION:
            raise ReadinessError("execution gate explanation is not canonical")
        runner_ids = {
            self.history.runner_id, self.runner.runner_id,
            self.liveness.record.runner_id, self.operational_status.runner_id,
        }
        if len(runner_ids) != 1:
            raise ReadinessError("runner evidence identity conflicts")
        if self.operational_status.scheduler_run_id != self.scheduler.run_id:
            raise ReadinessError("scheduler evidence identity conflicts")
        if self.scheduler_receipt.run_id != self.scheduler.run_id or self.scheduler_receipt.to_state is not self.scheduler.state:
            raise ReadinessError("scheduler receipt binding conflicts")
        if (
            self.suspension_record.state is not self.suspension.state
            or self.suspension_record.cause is not self.suspension.cause
            or self.suspension_record.evidence_class is not self.suspension.evidence_class
            or self.suspension_receipt.controller_id != self.suspension_record.controller_id
            or self.suspension_receipt.to_state is not self.suspension_record.state
            or self.suspension_receipt.cause is not self.suspension_record.cause
            or self.suspension_receipt.evidence_class is not self.suspension_record.evidence_class
            or self.suspension_receipt.sequence != self.suspension_record.sequence
        ):
            raise ReadinessError("suspension evidence binding conflicts")
        if (
            self.history.completed_cycles != self.runner.completed_tick_count
            or self.history.runner_transition_sequence != self.runner.transition_sequence
        ):
            raise ReadinessError("shadow history is not bound to the runner result")


REPORT_KEYS = frozenset({
    "report_id", "scope", "status", "reason_codes", "history_id", "runner_id",
    "runner_state", "liveness_state", "operational_status", "suspension_state",
    "scheduler_state", "orchestrator_state", "gate_verdict", "completed_cycle_bucket",
    "consecutive_failure_bucket", "stale_event_bucket", "ambiguous_restart_bucket",
    "alert_count_bucket", "acknowledgment_count_bucket", "alert_confirmation_ratio_bucket",
    "evaluated_at", "last_safe_evidence_at", "offline_healthy", "supervised_ready",
    "live_ready", "unattended_ready", "protection_grounded", "activation_approved",
    "permits_automated_action",
    "evidence_set_id", "evidence_attestation_id", "evidence_verifier_id", "evidence_fingerprint",
    "gate_evidence_verified",
    "evidence_attested_at",
})


@dataclass(frozen=True)
class ShadowReadinessReport:
    report_id: str
    scope: AssessmentScope
    status: ReadinessStatus
    reason_codes: tuple[ReadinessReason, ...]
    history_id: str
    runner_id: str
    runner_state: RunnerState
    liveness_state: LivenessState
    operational_status: OverallStatus
    suspension_state: ControllerState
    scheduler_state: RunState
    orchestrator_state: CycleOutcome
    gate_verdict: GateVerdict
    completed_cycle_bucket: CountBucket
    consecutive_failure_bucket: CountBucket
    stale_event_bucket: CountBucket
    ambiguous_restart_bucket: CountBucket
    alert_count_bucket: CountBucket
    acknowledgment_count_bucket: CountBucket
    alert_confirmation_ratio_bucket: RatioBucket
    evaluated_at: datetime
    last_safe_evidence_at: datetime
    offline_healthy: bool
    supervised_ready: bool
    evidence_set_id: str
    evidence_attestation_id: str
    evidence_verifier_id: str
    evidence_fingerprint: str
    gate_evidence_verified: bool
    evidence_attested_at: datetime
    live_ready: bool = False
    unattended_ready: bool = False
    protection_grounded: bool = False
    activation_approved: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.report_id, "readiness-", "readiness identity")
        _identifier(self.history_id, "shadow-history-", "history identity")
        _identifier(self.runner_id, "runner-", "runner identity")
        if not isinstance(self.scope, AssessmentScope) or not isinstance(self.status, ReadinessStatus):
            raise ReadinessError("readiness status is invalid")
        if not self.reason_codes or any(not isinstance(value, ReadinessReason) for value in self.reason_codes) or len(set(self.reason_codes)) != len(self.reason_codes):
            raise ReadinessError("readiness reasons are invalid")
        enums = (
            (self.runner_state, RunnerState), (self.liveness_state, LivenessState),
            (self.operational_status, OverallStatus), (self.suspension_state, ControllerState),
            (self.scheduler_state, RunState), (self.orchestrator_state, CycleOutcome),
            (self.gate_verdict, GateVerdict),
        )
        if any(not isinstance(value, kind) for value, kind in enums):
            raise ReadinessError("component state is invalid")
        buckets = (
            self.completed_cycle_bucket, self.consecutive_failure_bucket,
            self.stale_event_bucket, self.ambiguous_restart_bucket,
            self.alert_count_bucket, self.acknowledgment_count_bucket,
        )
        if any(not isinstance(value, CountBucket) for value in buckets) or not isinstance(self.alert_confirmation_ratio_bucket, RatioBucket):
            raise ReadinessError("readiness bucket is invalid")
        _moment(self.evaluated_at, "evaluation timestamp")
        _moment(self.last_safe_evidence_at, "evidence timestamp")
        if self.last_safe_evidence_at > self.evaluated_at:
            raise ReadinessError("readiness evidence is future-dated")
        if not isinstance(self.offline_healthy, bool) or not isinstance(self.supervised_ready, bool):
            raise ReadinessError("readiness flags are invalid")
        if not isinstance(self.gate_evidence_verified, bool):
            raise ReadinessError("gate evidence flag is invalid")
        _moment(self.evidence_attested_at, "evidence attestation timestamp")
        if self.evidence_attested_at > self.evaluated_at:
            raise ReadinessError("evidence attestation is future-dated")
        _identifier(self.evidence_set_id, "evidence-set-", "evidence set identity")
        _identifier(self.evidence_attestation_id, "readiness-attestation-", "attestation identity")
        _identifier(self.evidence_verifier_id, "readiness-verifier-", "verifier identity")
        if not isinstance(self.evidence_fingerprint, str) or len(self.evidence_fingerprint) != 64 or any(character not in "0123456789abcdef" for character in self.evidence_fingerprint):
            raise ReadinessError("evidence fingerprint is invalid")
        if self.supervised_ready is not False or self.gate_verdict is GateVerdict.OPEN:
            raise ReadinessError("supervised readiness remains blocked")
        for value in (self.live_ready, self.unattended_ready, self.protection_grounded, self.activation_approved, self.permits_automated_action):
            _false(value)
        if self.offline_healthy != (self.status in {ReadinessStatus.OFFLINE_HEALTHY, ReadinessStatus.LIVE_BLOCKED, ReadinessStatus.UNATTENDED_BLOCKED}):
            raise ReadinessError("offline health conflicts with status")
        if ReadinessReason.PROTECTION_NOT_GROUNDED not in self.reason_codes or ReadinessReason.ACTIVATION_APPROVALS_EMPTY not in self.reason_codes:
            raise ReadinessError("readiness blockers are incomplete")
        if tuple(sorted(self.reason_codes, key=_REASON_INDEX.__getitem__)) != self.reason_codes:
            raise ReadinessError("readiness reasons are not canonical")
        required_by_status = {
            ReadinessStatus.FAILED_CLOSED: {ReadinessReason.FUTURE_EVIDENCE, ReadinessReason.CONFLICTING_EVIDENCE, ReadinessReason.EVIDENCE_ATTESTATION_INVALID, ReadinessReason.FAILED_COMPONENT},
            ReadinessStatus.AMBIGUOUS: {ReadinessReason.AMBIGUOUS_RESTART},
            ReadinessStatus.SUPERVISED_BLOCKED: {ReadinessReason.ACTIVE_SUSPENSION, ReadinessReason.EXECUTION_GATE_CLOSED, ReadinessReason.PROTECTION_NOT_GROUNDED, ReadinessReason.ACTIVATION_APPROVALS_EMPTY},
            ReadinessStatus.OPERATOR_REVIEW_REQUIRED: {ReadinessReason.ALERT_DELIVERY_UNCONFIRMED, ReadinessReason.OPERATOR_ACK_PENDING, ReadinessReason.FURTHER_REVIEW_REQUIRED, ReadinessReason.OPERATIONAL_REVIEW_REQUIRED, ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED},
            ReadinessStatus.OFFLINE_DEGRADED: {ReadinessReason.MISSED_CYCLE, ReadinessReason.STALE_EVIDENCE, ReadinessReason.INSUFFICIENT_SHADOW_HISTORY, ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES, ReadinessReason.EXCESSIVE_STALE_EVENTS, ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW, ReadinessReason.GATE_EVIDENCE_UNVERIFIED, ReadinessReason.COMPONENT_WAITING, ReadinessReason.COMPONENT_DEGRADED, ReadinessReason.COMPONENT_EXPIRED},
            ReadinessStatus.OFFLINE_HEALTHY: {ReadinessReason.OFFLINE_EVIDENCE_HEALTHY},
            ReadinessStatus.LIVE_BLOCKED: {ReadinessReason.PROTECTION_NOT_GROUNDED, ReadinessReason.ACTIVATION_APPROVALS_EMPTY},
            ReadinessStatus.UNATTENDED_BLOCKED: {ReadinessReason.PROTECTION_NOT_GROUNDED, ReadinessReason.ACTIVATION_APPROVALS_EMPTY},
        }
        if not required_by_status[self.status].intersection(self.reason_codes):
            raise ReadinessError("readiness status lacks its dominant reason")
        component_requirements = (
            (self.runner_state is RunnerState.FAILED_CLOSED, ReadinessReason.FAILED_COMPONENT),
            (self.liveness_state is LivenessState.FAILED_CLOSED, ReadinessReason.FAILED_COMPONENT),
            (self.operational_status is OverallStatus.FAILED_CLOSED, ReadinessReason.FAILED_COMPONENT),
            (self.scheduler_state is RunState.FAILED_CLOSED, ReadinessReason.FAILED_COMPONENT),
            (self.orchestrator_state is CycleOutcome.FAILED_CLOSED, ReadinessReason.FAILED_COMPONENT),
            (self.runner_state is RunnerState.AMBIGUOUS_RESTART, ReadinessReason.AMBIGUOUS_RESTART),
            (self.operational_status is OverallStatus.AMBIGUOUS, ReadinessReason.AMBIGUOUS_RESTART),
            (self.scheduler_state is RunState.AMBIGUOUS_RESTART, ReadinessReason.AMBIGUOUS_RESTART),
            (self.orchestrator_state is CycleOutcome.AMBIGUOUS_RESTART, ReadinessReason.AMBIGUOUS_RESTART),
            (self.suspension_state is not ControllerState.CLEAR, ReadinessReason.ACTIVE_SUSPENSION),
            (self.operational_status is OverallStatus.REVIEW_REQUIRED, ReadinessReason.OPERATIONAL_REVIEW_REQUIRED),
            (self.operational_status is OverallStatus.SUSPENDED, ReadinessReason.ACTIVE_SUSPENSION),
            (self.liveness_state in {LivenessState.STALE, LivenessState.SUSPENSION_REQUIRED, LivenessState.FAILED_CLOSED}, ReadinessReason.ACTIVE_SUSPENSION),
            (self.scheduler_state is RunState.SUSPENDED, ReadinessReason.ACTIVE_SUSPENSION),
            (self.orchestrator_state is CycleOutcome.SUSPENDED, ReadinessReason.ACTIVE_SUSPENSION),
            (self.liveness_state is LivenessState.MISSED_CYCLE, ReadinessReason.MISSED_CYCLE),
            (self.liveness_state is LivenessState.STALE, ReadinessReason.STALE_EVIDENCE),
            (self.liveness_state is LivenessState.UNKNOWN, ReadinessReason.COMPONENT_WAITING),
            (self.liveness_state is LivenessState.DEGRADED, ReadinessReason.COMPONENT_DEGRADED),
            (self.operational_status is OverallStatus.WAITING, ReadinessReason.COMPONENT_WAITING),
            (self.operational_status is OverallStatus.DEGRADED, ReadinessReason.COMPONENT_DEGRADED),
            (self.operational_status is OverallStatus.EXPIRED, ReadinessReason.COMPONENT_EXPIRED),
            (self.scheduler_state is RunState.WAITING_FOR_SESSION, ReadinessReason.COMPONENT_WAITING),
            (self.scheduler_state is RunState.EXPIRED, ReadinessReason.COMPONENT_EXPIRED),
            (self.runner_state is RunnerState.EXPIRED, ReadinessReason.COMPONENT_EXPIRED),
            (self.scheduler_state in {RunState.CREATED, RunState.PRECONDITION_CHECK, RunState.RUNNING_OFFLINE_EVALUATION}, ReadinessReason.COMPONENT_WAITING),
            (self.scheduler_state in {RunState.READY_FOR_SUPERVISED_REVIEW, RunState.SUPERVISED_REVIEW_REQUIRED}, ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED),
            (self.orchestrator_state is CycleOutcome.WAIT_FOR_SESSION, ReadinessReason.COMPONENT_WAITING),
            (self.orchestrator_state is CycleOutcome.REQUIRE_OPERATOR_REVIEW, ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED),
            (self.completed_cycle_bucket in {CountBucket.ZERO, CountBucket.ONE}, ReadinessReason.INSUFFICIENT_SHADOW_HISTORY),
            (self.ambiguous_restart_bucket is not CountBucket.ZERO, ReadinessReason.AMBIGUOUS_RESTART),
            (self.acknowledgment_count_bucket is not CountBucket.ZERO, ReadinessReason.FURTHER_REVIEW_REQUIRED),
            (self.alert_confirmation_ratio_bucket is not RatioBucket.COMPLETE, ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW),
            (self.gate_verdict is not GateVerdict.OPEN, ReadinessReason.EXECUTION_GATE_CLOSED),
            (not self.gate_evidence_verified, ReadinessReason.GATE_EVIDENCE_UNVERIFIED),
        )
        if any(condition and reason not in self.reason_codes for condition, reason in component_requirements):
            raise ReadinessError("component state lacks its blocker reason")
        if self.status is not _dominant_status(self.scope, set(self.reason_codes)):
            raise ReadinessError("readiness blocker dominance conflicts")
        if (ReadinessReason.OFFLINE_EVIDENCE_HEALTHY in self.reason_codes) is not self.offline_healthy:
            raise ReadinessError("offline evidence reason conflicts")

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "scope": self.scope.value,
            "status": self.status.value,
            "reason_codes": [value.value for value in self.reason_codes],
            "history_id": self.history_id,
            "runner_id": self.runner_id,
            "runner_state": self.runner_state.value,
            "liveness_state": self.liveness_state.value,
            "operational_status": self.operational_status.value,
            "suspension_state": self.suspension_state.value,
            "scheduler_state": self.scheduler_state.value,
            "orchestrator_state": self.orchestrator_state.value,
            "gate_verdict": self.gate_verdict.value,
            "completed_cycle_bucket": self.completed_cycle_bucket.value,
            "consecutive_failure_bucket": self.consecutive_failure_bucket.value,
            "stale_event_bucket": self.stale_event_bucket.value,
            "ambiguous_restart_bucket": self.ambiguous_restart_bucket.value,
            "alert_count_bucket": self.alert_count_bucket.value,
            "acknowledgment_count_bucket": self.acknowledgment_count_bucket.value,
            "alert_confirmation_ratio_bucket": self.alert_confirmation_ratio_bucket.value,
            "evaluated_at": self.evaluated_at.isoformat(),
            "last_safe_evidence_at": self.last_safe_evidence_at.isoformat(),
            "offline_healthy": self.offline_healthy,
            "supervised_ready": self.supervised_ready,
            "live_ready": False,
            "unattended_ready": False,
            "protection_grounded": False,
            "activation_approved": False,
            "permits_automated_action": False,
            "evidence_set_id": self.evidence_set_id,
            "evidence_attestation_id": self.evidence_attestation_id,
            "evidence_verifier_id": self.evidence_verifier_id,
            "evidence_fingerprint": self.evidence_fingerprint,
            "gate_evidence_verified": self.gate_evidence_verified,
            "evidence_attested_at": self.evidence_attested_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, value: object, *, context: ReadinessContext, policy: ShadowHistoryPolicy, evidence: ReadinessEvidence, evaluation_time: datetime, maximum_age: timedelta, verifier: ReadinessEvidenceVerifier) -> "ShadowReadinessReport":
        _moment(evaluation_time, "load evaluation timestamp")
        if not isinstance(context, ReadinessContext) or not isinstance(policy, ShadowHistoryPolicy) or not isinstance(evidence, ReadinessEvidence) or not isinstance(maximum_age, timedelta) or maximum_age <= timedelta(0) or not isinstance(verifier, ReadinessEvidenceVerifier):
            raise ReadinessError("stored readiness freshness policy is invalid")
        if not isinstance(value, dict) or set(value) != REPORT_KEYS:
            raise ReadinessError("stored readiness fields are invalid")
        prohibited = {"account", "symbol", "quantity", "price", "balance", "order_id", "exception", "credential", "username", "manifest"}
        if set(value) & prohibited:
            raise ReadinessError("stored readiness contains private fields")
        expected = project_readiness(context, policy, evidence, verifier)
        if value != expected.to_dict():
            raise ReadinessError("stored readiness projection conflicts with public evidence")
        try:
            report = cls(
                report_id=value["report_id"], scope=AssessmentScope(value["scope"]),
                status=ReadinessStatus(value["status"]),
                reason_codes=tuple(ReadinessReason(item) for item in value["reason_codes"]),
                history_id=value["history_id"], runner_id=value["runner_id"],
                runner_state=RunnerState(value["runner_state"]),
                liveness_state=LivenessState(value["liveness_state"]),
                operational_status=OverallStatus(value["operational_status"]),
                suspension_state=ControllerState(value["suspension_state"]),
                scheduler_state=RunState(value["scheduler_state"]),
                orchestrator_state=CycleOutcome(value["orchestrator_state"]),
                gate_verdict=GateVerdict(value["gate_verdict"]),
                completed_cycle_bucket=CountBucket(value["completed_cycle_bucket"]),
                consecutive_failure_bucket=CountBucket(value["consecutive_failure_bucket"]),
                stale_event_bucket=CountBucket(value["stale_event_bucket"]),
                ambiguous_restart_bucket=CountBucket(value["ambiguous_restart_bucket"]),
                alert_count_bucket=CountBucket(value["alert_count_bucket"]),
                acknowledgment_count_bucket=CountBucket(value["acknowledgment_count_bucket"]),
                alert_confirmation_ratio_bucket=RatioBucket(value["alert_confirmation_ratio_bucket"]),
                evaluated_at=_parse_moment(value["evaluated_at"]),
                last_safe_evidence_at=_parse_moment(value["last_safe_evidence_at"]),
                offline_healthy=value["offline_healthy"], supervised_ready=value["supervised_ready"],
                evidence_set_id=value["evidence_set_id"], evidence_attestation_id=value["evidence_attestation_id"],
                evidence_verifier_id=value["evidence_verifier_id"], evidence_fingerprint=value["evidence_fingerprint"],
                gate_evidence_verified=value["gate_evidence_verified"],
                evidence_attested_at=_parse_moment(value["evidence_attested_at"]),
                live_ready=value["live_ready"], unattended_ready=value["unattended_ready"],
                protection_grounded=value["protection_grounded"], activation_approved=value["activation_approved"],
                permits_automated_action=value["permits_automated_action"],
            )
            if report.evaluated_at > evaluation_time:
                raise ReadinessError("stored readiness report is future-dated")
            if evaluation_time - report.evaluated_at > maximum_age:
                raise ReadinessError("stored readiness report is stale")
            if verifier.verifier_id != report.evidence_verifier_id or not _verify(
                verifier,
                ReadinessEvidenceAttestation(
                    report.evidence_attestation_id, report.evidence_set_id,
                    report.evidence_fingerprint, report.evidence_attested_at,
                    EvidenceProvenance.VERIFIED_ATTESTATION,
                    evidence.attestation.history_fingerprint,
                    evidence.attestation.history_provenance,
                    evidence.attestation.history_producer_id,
                ),
            ):
                raise ReadinessError("stored readiness attestation is invalid")
            return report
        except ReadinessError:
            raise
        except Exception:
            raise ReadinessError("stored readiness report is invalid") from None


def project_readiness(context: ReadinessContext, policy: ShadowHistoryPolicy, evidence: ReadinessEvidence, verifier: ReadinessEvidenceVerifier) -> ShadowReadinessReport:
    if not isinstance(context, ReadinessContext) or not isinstance(policy, ShadowHistoryPolicy) or not isinstance(evidence, ReadinessEvidence) or not isinstance(verifier, ReadinessEvidenceVerifier):
        raise ReadinessError("typed readiness inputs are required")
    _identifier(verifier.verifier_id, "readiness-verifier-", "verifier identity")
    if verifier.verifier_id != context.verifier_id or evidence.attestation.evidence_set_id != context.evidence_set_id:
        raise ReadinessError("readiness trust binding conflicts")
    fingerprint = evidence_fingerprint(evidence)
    attestation_valid = (
        evidence.attestation.provenance is EvidenceProvenance.VERIFIED_ATTESTATION
        and evidence.attestation.evidence_fingerprint == fingerprint
        and evidence.attestation.history_fingerprint == history_fingerprint(evidence.history)
        and evidence.attestation.history_provenance is HistoryProvenance.DURABLE_OFFLINE_RUNTIME
        and _verify(verifier, evidence.attestation)
    )
    evidence_timestamps = [
        evidence.history.last_evidence_at, evidence.liveness.record.evaluation_time,
        evidence.operational_status.reported_at, evidence.scheduler.updated_at,
        evidence.runner_observed_at, evidence.orchestrator_observed_at,
        evidence.gate_evaluated_at,
        evidence.suspension_record.updated_at,
        *(item.updated_at for item in evidence.alerts),
        *(item.updated_at for item in evidence.acknowledgments),
    ]
    timestamps = [*evidence_timestamps, evidence.attestation.observed_at]
    future = any(value > context.evaluated_at for value in timestamps)
    stale = any(context.evaluated_at - value > context.maximum_evidence_age for value in timestamps if value <= context.evaluated_at)
    last_safe = max((value for value in timestamps if value <= context.evaluated_at), default=context.evaluated_at)
    attestation_chronology_valid = evidence.attestation.observed_at >= max(evidence_timestamps)
    alerts_confirmed = sum(item.state is AlertState.DELIVERED_CONFIRMED for item in evidence.alerts)
    ratio = _ratio(alerts_confirmed, len(evidence.alerts))
    projected = evidence.operational_status
    scheduler_actions = {
        RunState.WAITING_FOR_SESSION: {SchedulerAction.WAIT},
        RunState.READY_FOR_SUPERVISED_REVIEW: {SchedulerAction.REQUIRE_OPERATOR_REVIEW, SchedulerAction.RUN_OFFLINE_CHECKS},
        RunState.SUPERVISED_REVIEW_REQUIRED: {SchedulerAction.REQUIRE_OPERATOR_REVIEW},
        RunState.SUSPENDED: {SchedulerAction.SUSPEND},
        RunState.COMPLETED_NO_ACTION: {SchedulerAction.COMPLETE_NO_ACTION},
        RunState.FAILED_CLOSED: {SchedulerAction.FAIL_CLOSED},
        RunState.AMBIGUOUS_RESTART: {SchedulerAction.FAIL_CLOSED},
        RunState.EXPIRED: {SchedulerAction.WAIT, SchedulerAction.COMPLETE_NO_ACTION},
    }
    acknowledgments_bound = all(_acknowledgment_is_bound(item, evidence) for item in evidence.acknowledgments)
    conflicting = any((
        projected.runner_state is not None and projected.runner_state is not evidence.runner.state,
        projected.runner_outcome is not None and projected.runner_outcome is not evidence.runner.outcome,
        projected.liveness_state is not None and projected.liveness_state is not evidence.liveness.record.state,
        projected.suspension_state is not None and projected.suspension_state is not evidence.suspension.state,
        projected.scheduler_state is not None and projected.scheduler_state is not evidence.scheduler.state,
        projected.orchestrator_state is not None and projected.orchestrator_state is not evidence.orchestrator.outcome,
        evidence.liveness.record.runner_outcome.value != evidence.runner.outcome.value,
        evidence.scheduler.state in scheduler_actions and evidence.orchestrator.scheduler_action not in scheduler_actions[evidence.scheduler.state],
        evidence.suspension.state is ControllerState.CLEAR and evidence.suspension.scheduler_action is SchedulerAction.SUSPEND,
        any(
            item.state is AlertState.DELIVERED_CONFIRMED
            and item.acknowledgment_status is not AlertAcknowledgmentStatus.CONFIRMED
            for item in evidence.alerts
        ),
        any(
            item.state is not AlertState.DELIVERED_CONFIRMED
            and item.acknowledgment_status is AlertAcknowledgmentStatus.CONFIRMED
            for item in evidence.alerts
        ),
        context.scope is AssessmentScope.UNATTENDED and evidence.execution_gate.mode is not GateMode.LIMITED_UNATTENDED,
        context.scope in {AssessmentScope.SUPERVISED, AssessmentScope.LIVE} and evidence.execution_gate.mode is not GateMode.SUPERVISED,
        any(item.correlation_id != context.correlation_id for item in evidence.alerts),
        any(item.correlation_id != context.correlation_id for item in evidence.acknowledgments),
        not acknowledgments_bound,
    ))
    failed = any((
        evidence.runner.state is RunnerState.FAILED_CLOSED,
        evidence.liveness.record.state is LivenessState.FAILED_CLOSED,
        evidence.operational_status.overall_status is OverallStatus.FAILED_CLOSED,
        evidence.scheduler.state is RunState.FAILED_CLOSED,
        evidence.orchestrator.outcome is CycleOutcome.FAILED_CLOSED,
        evidence.execution_gate.verdict is GateVerdict.CLOSED_MALFORMED_INPUT,
        any(item.state is AlertState.FAILED_CLOSED for item in evidence.alerts),
        any(item.state is AcknowledgmentState.FAILED_CLOSED for item in evidence.acknowledgments),
    ))
    ambiguous = any((
        evidence.runner.state is RunnerState.AMBIGUOUS_RESTART,
        evidence.operational_status.overall_status is OverallStatus.AMBIGUOUS,
        evidence.scheduler.state is RunState.AMBIGUOUS_RESTART,
        evidence.orchestrator.outcome is CycleOutcome.AMBIGUOUS_RESTART,
        evidence.history.ambiguous_restarts > 0,
    ))
    suspension = (
        evidence.suspension.state is not ControllerState.CLEAR
        or evidence.operational_status.suspension_active
        or evidence.liveness.suspension.request_suspension
        or evidence.scheduler.state is RunState.SUSPENDED
        or evidence.orchestrator.outcome is CycleOutcome.SUSPENDED
        or evidence.runner.outcome is RunnerOutcome.STOPPED_ON_SUSPENSION
    )
    pending_ack = any(item.state is AcknowledgmentState.PENDING for item in evidence.acknowledgments)
    further_review = any(item.requires_further_review for item in evidence.acknowledgments)
    unconfirmed_alert = any(item.state is not AlertState.DELIVERED_CONFIRMED for item in evidence.alerts)
    history_short = evidence.history.completed_cycles < policy.minimum_completed_cycles
    too_many_failures = evidence.history.consecutive_failures > policy.maximum_consecutive_failures
    too_many_stale = evidence.history.stale_events > policy.maximum_allowed_stale_events
    ratio_low = _RATIO_ORDER[ratio] < _RATIO_ORDER[policy.required_alert_confirmation_ratio_bucket]
    missed = evidence.liveness.consecutive_missed_cycles > 0
    waiting = any((
        evidence.liveness.record.state is LivenessState.UNKNOWN,
        evidence.operational_status.overall_status is OverallStatus.WAITING,
        evidence.scheduler.state is RunState.WAITING_FOR_SESSION,
        evidence.scheduler.state in {RunState.CREATED, RunState.PRECONDITION_CHECK, RunState.RUNNING_OFFLINE_EVALUATION},
        evidence.orchestrator.outcome is CycleOutcome.WAIT_FOR_SESSION,
    ))
    degraded = any((
        evidence.liveness.record.state is LivenessState.DEGRADED,
        evidence.operational_status.overall_status is OverallStatus.DEGRADED,
    ))
    expired = any((
        evidence.runner.state is RunnerState.EXPIRED,
        evidence.operational_status.overall_status is OverallStatus.EXPIRED,
        evidence.scheduler.state is RunState.EXPIRED,
    ))

    reasons: list[ReadinessReason] = []
    def add(reason: ReadinessReason, condition: bool = True) -> None:
        if condition and reason not in reasons:
            reasons.append(reason)
    add(ReadinessReason.FUTURE_EVIDENCE, future)
    add(ReadinessReason.CONFLICTING_EVIDENCE, conflicting)
    add(ReadinessReason.EVIDENCE_ATTESTATION_INVALID, not attestation_valid or not attestation_chronology_valid)
    add(ReadinessReason.FAILED_COMPONENT, failed)
    add(ReadinessReason.AMBIGUOUS_RESTART, ambiguous)
    add(ReadinessReason.ACTIVE_SUSPENSION, suspension)
    add(ReadinessReason.MISSED_CYCLE, missed)
    add(ReadinessReason.ALERT_DELIVERY_UNCONFIRMED, unconfirmed_alert)
    add(ReadinessReason.OPERATOR_ACK_PENDING, pending_ack)
    add(ReadinessReason.FURTHER_REVIEW_REQUIRED, further_review)
    add(
        ReadinessReason.OPERATIONAL_REVIEW_REQUIRED,
        evidence.operational_status.requires_operator_review
        or evidence.runner.outcome is RunnerOutcome.STOPPED_ON_REVIEW,
    )
    add(
        ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED,
        evidence.scheduler.reevaluation_required
        or evidence.suspension.requires_operator_review
        or evidence.scheduler.state in {RunState.READY_FOR_SUPERVISED_REVIEW, RunState.SUPERVISED_REVIEW_REQUIRED}
        or evidence.orchestrator.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW,
    )
    add(ReadinessReason.STALE_EVIDENCE, stale)
    add(ReadinessReason.INSUFFICIENT_SHADOW_HISTORY, history_short)
    add(ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES, too_many_failures)
    add(ReadinessReason.EXCESSIVE_STALE_EVENTS, too_many_stale)
    add(ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW, ratio_low)
    add(ReadinessReason.EXECUTION_GATE_CLOSED, evidence.execution_gate.verdict is not GateVerdict.OPEN)
    add(ReadinessReason.GATE_EVIDENCE_UNVERIFIED, not evidence.execution_gate.evidence_verified)
    add(ReadinessReason.COMPONENT_WAITING, waiting)
    add(ReadinessReason.COMPONENT_DEGRADED, degraded)
    add(ReadinessReason.COMPONENT_EXPIRED, expired)
    add(ReadinessReason.PROTECTION_NOT_GROUNDED)
    add(ReadinessReason.ACTIVATION_APPROVALS_EMPTY)

    status = _dominant_status(context.scope, set(reasons))
    offline_healthy = status in {ReadinessStatus.OFFLINE_HEALTHY, ReadinessStatus.LIVE_BLOCKED, ReadinessStatus.UNATTENDED_BLOCKED}
    add(ReadinessReason.OFFLINE_EVIDENCE_HEALTHY, offline_healthy)
    supervised_ready = False
    return ShadowReadinessReport(
        context.report_id, context.scope, status, tuple(reasons), evidence.history.history_id,
        evidence.runner.runner_id, evidence.runner.state, evidence.liveness.record.state,
        evidence.operational_status.overall_status, evidence.suspension.state,
        evidence.scheduler.state, evidence.orchestrator.outcome, evidence.execution_gate.verdict,
        _bucket(evidence.history.completed_cycles), _bucket(evidence.history.consecutive_failures),
        _bucket(evidence.history.stale_events), _bucket(evidence.history.ambiguous_restarts),
        _bucket(len(evidence.alerts)), _bucket(len(evidence.acknowledgments)), ratio,
        context.evaluated_at, last_safe, offline_healthy, supervised_ready,
        evidence.attestation.evidence_set_id, evidence.attestation.attestation_id,
        verifier.verifier_id, fingerprint, evidence.execution_gate.evidence_verified,
        evidence.attestation.observed_at,
    )


def _ratio(confirmed: int, total: int) -> RatioBucket:
    if total == 0:
        return RatioBucket.COMPLETE
    if confirmed == 0:
        return RatioBucket.ZERO
    if confirmed == total:
        return RatioBucket.COMPLETE
    return RatioBucket.HIGH if confirmed * 5 >= total * 4 else RatioBucket.LOW


def evidence_fingerprint(evidence: ReadinessEvidence) -> str:
    if not isinstance(evidence, ReadinessEvidence):
        raise ReadinessError("typed readiness evidence is required")
    history = evidence.history
    payload = {
        "evidence_set_id": evidence.attestation.evidence_set_id,
        "attestation_id": evidence.attestation.attestation_id,
        "attestation_observed_at": evidence.attestation.observed_at.isoformat(),
        "attestation_provenance": evidence.attestation.provenance.value,
        "history_fingerprint": evidence.attestation.history_fingerprint,
        "history_provenance": evidence.attestation.history_provenance.value,
        "history_producer_id": evidence.attestation.history_producer_id,
        "history": {
            "history_id": history.history_id, "runner_id": history.runner_id,
            "completed_cycles": history.completed_cycles,
            "consecutive_failures": history.consecutive_failures,
            "stale_events": history.stale_events, "ambiguous_restarts": history.ambiguous_restarts,
            "last_evidence_at": history.last_evidence_at.isoformat(),
            "transition_sequence": history.transition_sequence,
            "runner_transition_sequence": history.runner_transition_sequence,
            "completed_ids": list(history.completed_cycle_transition_ids),
            "failure_ids": list(history.failure_transition_ids),
            "stale_ids": list(history.stale_transition_ids),
            "ambiguous_ids": list(history.ambiguous_transition_ids),
        },
        "runner": evidence.runner.sanitized_facts(),
        "liveness": {
            "runner_id": evidence.liveness.record.runner_id,
            "heartbeat_sequence": evidence.liveness.record.heartbeat_sequence,
            "expected_cycle_timestamp": evidence.liveness.record.expected_cycle_timestamp.isoformat(),
            "observed_completion_timestamp": (
                evidence.liveness.record.observed_completion_timestamp.isoformat()
                if evidence.liveness.record.observed_completion_timestamp is not None else None
            ),
            "transition_id": evidence.liveness.record.transition_id,
            "transition_sequence": evidence.liveness.record.transition_sequence,
            "state": evidence.liveness.record.state.value,
            "reason": evidence.liveness.record.reason.value,
            "outcome": evidence.liveness.record.runner_outcome.value,
            "missed": evidence.liveness.consecutive_missed_cycles,
            "evaluated_at": evidence.liveness.record.evaluation_time.isoformat(),
            "suspension": {
                "runner_id": evidence.liveness.suspension.runner_id,
                "requested": evidence.liveness.suspension.request_suspension,
                "cause": evidence.liveness.suspension.cause.value,
                "reason": evidence.liveness.suspension.reason.value,
                "transition_id": evidence.liveness.suspension.transition_id,
                "permits_automated_action": evidence.liveness.suspension.permits_automated_action,
            },
            "permits_automated_action": evidence.liveness.permits_automated_action,
        },
        "status": evidence.operational_status.to_dict(),
        "alerts": [
            {
                "id": item.alert_id, "category": item.category.value,
                "severity": item.severity.value, "state": item.state.value,
                "reason": item.reason.value, "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(), "correlation": item.correlation_id,
                "claim_id": item.claim_id,
                "claimed_at": item.claimed_at.isoformat() if item.claimed_at is not None else None,
                "attempt_count": item.attempt_count, "attempt_bucket": item.attempt_bucket.value,
                "acknowledgment_status": item.acknowledgment_status.value,
                "sequence": item.transition_sequence, "transition": item.last_transition_id,
                "permits_automated_action": item.permits_automated_action,
            }
            for item in evidence.alerts
        ],
        "acknowledgments": [
            {
                "id": item.acknowledgment_id, "review_item": item.review_item_id,
                "expected_review_sequence": item.expected_review_sequence,
                "category": item.category.value, "state": item.state.value, "reason": item.reason.value,
                "operator_reviewed": item.operator_reviewed,
                "provenance": item.provenance.value if item.provenance is not None else None,
                "issued_at": item.issued_at.isoformat(), "expires_at": item.expires_at.isoformat(),
                "sequence": item.transition_sequence, "transition": item.last_transition_id,
                "artifact_id": item.artifact_id,
                "source_kind": item.source_kind.value, "source_id": item.source_id,
                "source_sequence": item.source_sequence,
                "successor_review_item_id": item.successor_review_item_id,
                "successor_review_sequence": item.successor_review_sequence,
                "requires_further_review": item.requires_further_review,
                "updated_at": item.updated_at.isoformat(), "correlation": item.correlation_id,
                "permits_automated_action": item.permits_automated_action,
            }
            for item in evidence.acknowledgments
        ],
        "suspension": {
            "controller_id": evidence.suspension_record.controller_id,
            "decision": {
                "state": evidence.suspension.state.value, "cause": evidence.suspension.cause.value,
                "requires_operator_review": evidence.suspension.requires_operator_review,
                "scheduler_action": evidence.suspension.scheduler_action.value,
                "evidence_class": evidence.suspension.evidence_class.value,
                "permits_automated_action": evidence.suspension.permits_automated_action,
            },
            "record": {
                "state": evidence.suspension_record.state.value,
                "cause": evidence.suspension_record.cause.value,
                "evidence_class": evidence.suspension_record.evidence_class.value,
                "sequence": evidence.suspension_record.sequence,
                "clearance_reviewed": evidence.suspension_record.clearance_reviewed,
                "created_at": evidence.suspension_record.created_at.isoformat(),
                "updated_at": evidence.suspension_record.updated_at.isoformat(),
            },
            "receipt": {
                "transition": evidence.suspension_receipt.transition_id,
                "controller_id": evidence.suspension_receipt.controller_id,
                "from_state": evidence.suspension_receipt.from_state.value,
                "to_state": evidence.suspension_receipt.to_state.value,
                "cause": evidence.suspension_receipt.cause.value,
                "evidence_class": evidence.suspension_receipt.evidence_class.value,
                "sequence": evidence.suspension_receipt.sequence,
                "persisted": evidence.suspension_receipt.persisted,
            },
        },
        "scheduler": {
            "run_id": evidence.scheduler.run_id, "state": evidence.scheduler.state.value,
            "session_date": evidence.scheduler.session_date.isoformat(),
            "created_at": evidence.scheduler.created_at.isoformat(),
            "updated_at": evidence.scheduler.updated_at.isoformat(),
            "suspension_reason": evidence.scheduler.suspension_reason,
            "reevaluation_required": evidence.scheduler.reevaluation_required,
            "receipt": {
                "transition": evidence.scheduler_receipt.transition_id,
                "run_id": evidence.scheduler_receipt.run_id,
                "from_state": evidence.scheduler_receipt.from_state.value,
                "to_state": evidence.scheduler_receipt.to_state.value,
                "sequence": evidence.scheduler_receipt.sequence,
                "persisted": evidence.scheduler_receipt.persisted,
            },
        },
        "orchestrator": {
            "outcome": evidence.orchestrator.outcome.value,
            "reason": evidence.orchestrator.reason.value,
            "scheduler_action": evidence.orchestrator.scheduler_action.value,
        },
        "gate": {
            "verdict": evidence.execution_gate.verdict.value,
            "mode": evidence.execution_gate.mode.value,
            "evidence_verified": evidence.execution_gate.evidence_verified,
            "unmet_bucket": evidence.execution_gate.sanitized_facts()["unmet_precondition_bucket"],
            "unmet_count": evidence.execution_gate.unmet_precondition_count,
            "explanation": evidence.execution_gate.explanation,
            "permits_automated_action": evidence.execution_gate.permits_automated_action,
        },
        "observed_at": {
            "runner": evidence.runner_observed_at.isoformat(),
            "orchestrator": evidence.orchestrator_observed_at.isoformat(),
            "gate": evidence.gate_evaluated_at.isoformat(),
        },
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def history_fingerprint(history: ShadowHistory) -> str:
    if not isinstance(history, ShadowHistory):
        raise ReadinessError("typed shadow history is required")
    payload = {
        "history_id": history.history_id,
        "runner_id": history.runner_id,
        "completed_cycles": history.completed_cycles,
        "consecutive_failures": history.consecutive_failures,
        "stale_events": history.stale_events,
        "ambiguous_restarts": history.ambiguous_restarts,
        "last_evidence_at": history.last_evidence_at.isoformat(),
        "transition_sequence": history.transition_sequence,
        "runner_transition_sequence": history.runner_transition_sequence,
        "completed_cycle_transition_ids": list(history.completed_cycle_transition_ids),
        "failure_transition_ids": list(history.failure_transition_ids),
        "stale_transition_ids": list(history.stale_transition_ids),
        "ambiguous_transition_ids": list(history.ambiguous_transition_ids),
        "permits_automated_action": history.permits_automated_action,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _verify(verifier: ReadinessEvidenceVerifier, attestation: ReadinessEvidenceAttestation) -> bool:
    try:
        return verifier.verify(attestation) is True
    except Exception:
        return False


def _acknowledgment_is_bound(item: AcknowledgmentRecord, evidence: ReadinessEvidence) -> bool:
    suffixes: dict[str, int] = {
        f"source-status-report-{evidence.operational_status.report_id}": 1,
        f"source-scheduler-{evidence.scheduler.run_id}": evidence.scheduler_receipt.sequence,
        f"source-suspension-controller-{evidence.suspension_record.controller_id}": evidence.suspension_record.sequence,
    }
    suffixes.update({f"source-alert-outbox-{alert.alert_id}": alert.transition_sequence for alert in evidence.alerts})
    return suffixes.get(item.source_id) == item.source_sequence


def _dominant_status(scope: AssessmentScope, reasons: set[ReadinessReason]) -> ReadinessStatus:
    if reasons & {ReadinessReason.FUTURE_EVIDENCE, ReadinessReason.CONFLICTING_EVIDENCE, ReadinessReason.EVIDENCE_ATTESTATION_INVALID, ReadinessReason.FAILED_COMPONENT}:
        return ReadinessStatus.FAILED_CLOSED
    if ReadinessReason.AMBIGUOUS_RESTART in reasons:
        return ReadinessStatus.AMBIGUOUS
    if ReadinessReason.ACTIVE_SUSPENSION in reasons:
        return ReadinessStatus.SUPERVISED_BLOCKED
    if reasons & {ReadinessReason.ALERT_DELIVERY_UNCONFIRMED, ReadinessReason.OPERATOR_ACK_PENDING, ReadinessReason.FURTHER_REVIEW_REQUIRED, ReadinessReason.OPERATIONAL_REVIEW_REQUIRED, ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED}:
        return ReadinessStatus.OPERATOR_REVIEW_REQUIRED
    if reasons & {
        ReadinessReason.MISSED_CYCLE, ReadinessReason.STALE_EVIDENCE,
        ReadinessReason.INSUFFICIENT_SHADOW_HISTORY,
        ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES,
        ReadinessReason.EXCESSIVE_STALE_EVENTS,
        ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW,
        ReadinessReason.GATE_EVIDENCE_UNVERIFIED,
        ReadinessReason.COMPONENT_WAITING,
        ReadinessReason.COMPONENT_DEGRADED,
        ReadinessReason.COMPONENT_EXPIRED,
    }:
        return ReadinessStatus.OFFLINE_DEGRADED
    if scope is AssessmentScope.SUPERVISED:
        return ReadinessStatus.SUPERVISED_BLOCKED
    if scope is AssessmentScope.LIVE:
        return ReadinessStatus.LIVE_BLOCKED
    if scope is AssessmentScope.UNATTENDED:
        return ReadinessStatus.UNATTENDED_BLOCKED
    return ReadinessStatus.OFFLINE_HEALTHY


def _bucket(value: int) -> CountBucket:
    _nonnegative(value, "count")
    return CountBucket.ZERO if value == 0 else CountBucket.ONE if value == 1 else CountBucket.MANY


def _nonnegative(value: object, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReadinessError(f"{label} is invalid")
    return value


def _false(value: object) -> None:
    if value is not False:
        raise ReadinessError("shadow readiness never authorizes action")


def _identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or value != value.strip() or len(value) > 96:
        raise ReadinessError(f"{label} is invalid")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise ReadinessError(f"{label} is invalid")
    if any(part in value.casefold() for part in ("account", "credential", "email", "order", "phone", "private", "secret", "symbol", "token", "username", "wallet", "webhook")):
        raise ReadinessError(f"{label} is private")
    return value


def _moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ReadinessError(f"{label} is invalid")
    return value


def _parse_moment(value: object) -> datetime:
    if not isinstance(value, str):
        raise ReadinessError("stored timestamp is invalid")
    try:
        return _moment(datetime.fromisoformat(value), "stored timestamp")
    except Exception:
        raise ReadinessError("stored timestamp is invalid") from None
