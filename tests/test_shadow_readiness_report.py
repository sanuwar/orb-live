from __future__ import annotations

import ast
import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest

import src.shadow_readiness_report as module
from src.durable_alert_outbox import (
    AcknowledgmentStatus as AlertAcknowledgmentStatus,
    AlertCategory,
    AlertReason,
    AlertRecord,
    AlertSeverity,
    AlertState,
    AttemptBucket,
)
from src.durable_runner_liveness import LivenessReason, LivenessState
from src.durable_scheduler import RunState, SchedulerAction, SchedulerTransitionReceipt
from src.durable_suspension_controller import ControllerRecord, ControllerState, EvidenceClass, SuspensionCause, SuspensionDecision, SuspensionTransitionReceipt
from src.execution_gates import ACTIVATION_APPROVALS, GateDecision, GateMode, GateVerdict
from src.offline_safety_orchestrator import CycleOutcome
from src.offline_shadow_runner import RunnerOutcome, RunnerReason, RunnerState
from src.operator_acknowledgment import (
    AcknowledgmentCategory,
    AcknowledgmentReason,
    AcknowledgmentRecord,
    AcknowledgmentSourceKind,
    AcknowledgmentState,
    AttestedProvenance,
)
from src.read_only_broker_state import approved_active_protection_states
from src.shadow_readiness_report import (
    REPORT_KEYS,
    AssessmentScope,
    EvidenceProvenance,
    HistoryProvenance,
    RatioBucket,
    ReadinessContext,
    ReadinessError,
    ReadinessEvidence,
    ReadinessEvidenceAttestation,
    ReadinessReason,
    ReadinessStatus,
    ShadowHistory,
    ShadowHistoryPolicy,
    ShadowReadinessReport,
    evidence_fingerprint,
    history_fingerprint,
    project_readiness as _project_readiness,
)
from tests.test_operational_status_report import (
    NOW,
    context as status_context,
    cycle,
    inputs as status_inputs,
    liveness,
    project_status,
    runner,
    scheduled,
    suspension,
)


MODULE_PATH = Path(module.__file__)


class StaticVerifier:
    verifier_id = "readiness-verifier-test"

    def __init__(
        self,
        accepted_fingerprint: str,
        accepted_history_fingerprint: str,
        accepted_history_producer_id: str,
    ) -> None:
        self.accepted_fingerprint = accepted_fingerprint
        self.accepted_history_fingerprint = accepted_history_fingerprint
        self.accepted_history_producer_id = accepted_history_producer_id

    def verify(self, attestation: ReadinessEvidenceAttestation) -> bool:
        return (
            attestation.provenance is EvidenceProvenance.VERIFIED_ATTESTATION
            and attestation.history_provenance is HistoryProvenance.DURABLE_OFFLINE_RUNTIME
            and attestation.evidence_fingerprint == self.accepted_fingerprint
            and attestation.history_fingerprint == self.accepted_history_fingerprint
            and attestation.history_producer_id == self.accepted_history_producer_id
        )


def project_readiness(selected_context, selected_policy, selected_evidence):
    attestation = selected_evidence.attestation
    return _project_readiness(
        selected_context, selected_policy, selected_evidence,
        StaticVerifier(
            attestation.evidence_fingerprint,
            attestation.history_fingerprint,
            attestation.history_producer_id,
        ),
    )


def load_report(value, *, evaluation_time, maximum_age, verifier=None, selected_context=None, selected_evidence=None):
    chosen_context = selected_context or context()
    chosen_evidence = selected_evidence or evidence()
    return ShadowReadinessReport.from_dict(
        value, context=chosen_context, policy=policy(), evidence=chosen_evidence,
        evaluation_time=evaluation_time, maximum_age=maximum_age,
        verifier=verifier or StaticVerifier(
            value["evidence_fingerprint"],
            chosen_evidence.attestation.history_fingerprint,
            chosen_evidence.attestation.history_producer_id,
        ),
    )


def policy(**changes) -> ShadowHistoryPolicy:
    values = dict(
        minimum_completed_cycles=5,
        maximum_consecutive_failures=0,
        maximum_allowed_stale_events=0,
        required_alert_confirmation_ratio_bucket=RatioBucket.COMPLETE,
    )
    values.update(changes)
    return ShadowHistoryPolicy(**values)


def context(**changes) -> ReadinessContext:
    values = dict(
        report_id="readiness-safe", scope=AssessmentScope.OFFLINE,
        evaluated_at=NOW, maximum_evidence_age=timedelta(minutes=10),
        correlation_id="correlation-readiness",
        evidence_set_id="evidence-set-safe", verifier_id="readiness-verifier-test",
    )
    values.update(changes)
    return ReadinessContext(**values)


def history(**changes) -> ShadowHistory:
    values = dict(
        history_id="shadow-history-safe", runner_id="runner-safe", completed_cycles=5,
        consecutive_failures=0, stale_events=0, ambiguous_restarts=0,
        last_evidence_at=NOW - timedelta(minutes=1), transition_sequence=10,
        runner_transition_sequence=7,
        completed_cycle_transition_ids=tuple(f"shadow-transition-completed-{index}" for index in range(changes.get("completed_cycles", 5))),
        failure_transition_ids=tuple(f"shadow-transition-failure-{index}" for index in range(changes.get("consecutive_failures", 0))),
        stale_transition_ids=tuple(f"shadow-transition-stale-{index}" for index in range(changes.get("stale_events", 0))),
        ambiguous_transition_ids=tuple(f"shadow-transition-ambiguous-{index}" for index in range(changes.get("ambiguous_restarts", 0))),
    )
    values.update(changes)
    return ShadowHistory(**values)


def alert(state: AlertState = AlertState.DELIVERED_CONFIRMED) -> AlertRecord:
    reason = {
        AlertState.DELIVERED_CONFIRMED: AlertReason.STATUS_PROJECTION,
        AlertState.DELIVERY_UNCONFIRMED: AlertReason.DELIVERY_EVIDENCE_MISSING,
        AlertState.FAILED_CLOSED: AlertReason.DELIVERY_EVIDENCE_INVALID,
    }[state]
    ack = AlertAcknowledgmentStatus.CONFIRMED if state is AlertState.DELIVERED_CONFIRMED else AlertAcknowledgmentStatus.UNCONFIRMED
    return AlertRecord(
        "alert-safe", AlertCategory.STATUS_DEGRADED, AlertSeverity.WARNING, reason,
        state, NOW - timedelta(minutes=2), NOW - timedelta(minutes=1),
        "correlation-readiness", None, None, 1, AttemptBucket.ONE, ack, 4,
        "alert-transition-safe",
    )


def acknowledgment(state: AcknowledgmentState = AcknowledgmentState.ACKNOWLEDGED) -> AcknowledgmentRecord:
    if state is AcknowledgmentState.ACKNOWLEDGED:
        reason, reviewed, provenance, artifact_id = (
            AcknowledgmentReason.OPERATOR_AWARE, True,
            AttestedProvenance.VERIFIED_ATTESTATION, "ack-artifact-safe",
        )
    elif state is AcknowledgmentState.PENDING:
        reason, reviewed, provenance, artifact_id = AcknowledgmentReason.REVIEW_PENDING, False, None, None
    elif state is AcknowledgmentState.REJECTED:
        reason, reviewed, provenance, artifact_id = AcknowledgmentReason.OPERATOR_REJECTED, True, AttestedProvenance.VERIFIED_ATTESTATION, "ack-artifact-safe"
    elif state is AcknowledgmentState.EXPIRED:
        reason, reviewed, provenance, artifact_id = AcknowledgmentReason.REVIEW_EXPIRED, False, None, None
    elif state is AcknowledgmentState.SUPERSEDED:
        reason, reviewed, provenance, artifact_id = AcknowledgmentReason.REVIEW_SUPERSEDED, False, AttestedProvenance.VERIFIED_ATTESTATION, "ack-artifact-safe"
    else:
        reason, reviewed, provenance, artifact_id = (
            AcknowledgmentReason.ARTIFACT_INVALID, False,
            AttestedProvenance.SELF_DECLARED, "ack-artifact-safe",
        )
    return AcknowledgmentRecord(
        "ack-safe", "review-item-status-report-status-safe", 1, AcknowledgmentCategory.STATUS_DEGRADED,
        state, reason, reviewed, provenance, NOW - timedelta(minutes=5),
        NOW + timedelta(hours=1), NOW - timedelta(minutes=1), "correlation-readiness",
        2, "ack-transition-safe", artifact_id, AcknowledgmentSourceKind.STATUS_REPORT,
        "source-status-report-status-safe", 1,
        "review-item-successor" if state is AcknowledgmentState.SUPERSEDED else None,
        2 if state is AcknowledgmentState.SUPERSEDED else None, True,
    )


def evidence(**changes) -> ReadinessEvidence:
    operational = project_status(status_context(), status_inputs())
    selected_history = changes.get("history")
    selected_runner = changes.get("runner")
    if selected_history is None and selected_runner is None:
        selected_history = history()
        selected_runner = replace(runner(), completed_tick_count=5, next_tick_index=5)
    elif selected_history is not None and selected_runner is None:
        selected_runner = replace(
            runner(), completed_tick_count=selected_history.completed_cycles,
            next_tick_index=selected_history.completed_cycles,
            transition_sequence=selected_history.runner_transition_sequence,
        )
    elif selected_history is None:
        selected_history = history(
            completed_cycles=selected_runner.completed_tick_count,
            runner_transition_sequence=selected_runner.transition_sequence,
        )
    selected_suspension = changes.get("suspension", suspension())
    controller_sequence = 2
    controller_record = ControllerRecord(
        "controller-safe", selected_suspension.state, selected_suspension.cause,
        selected_suspension.evidence_class, controller_sequence,
        selected_suspension.state is ControllerState.CLEAR,
        NOW - timedelta(minutes=2), NOW - timedelta(minutes=1),
    )
    suspension_receipt = SuspensionTransitionReceipt(
        "transition-suspension-safe", "controller-safe",
        ControllerState.CLEARANCE_PENDING_REVIEW if selected_suspension.state is ControllerState.CLEAR else ControllerState.CLEAR,
        selected_suspension.state, selected_suspension.cause,
        selected_suspension.evidence_class, controller_sequence,
    )
    selected_scheduler = changes.get("scheduler", scheduled())
    scheduler_receipt = SchedulerTransitionReceipt(
        "transition-scheduler-safe", selected_scheduler.run_id,
        RunState.READY_FOR_SUPERVISED_REVIEW if selected_scheduler.state is RunState.COMPLETED_NO_ACTION else RunState.CREATED,
        selected_scheduler.state, 3,
    )
    placeholder = ReadinessEvidenceAttestation(
        "readiness-attestation-safe", "evidence-set-safe", "0" * 64,
        NOW, EvidenceProvenance.VERIFIED_ATTESTATION,
        history_fingerprint(selected_history), HistoryProvenance.DURABLE_OFFLINE_RUNTIME,
        "history-producer-offline-runtime",
    )
    values = dict(
        history=selected_history, runner=selected_runner, liveness=liveness(),
        operational_status=operational, alerts=(alert(),),
        acknowledgments=(), suspension=selected_suspension,
        suspension_record=controller_record, suspension_receipt=suspension_receipt,
        scheduler=selected_scheduler, scheduler_receipt=scheduler_receipt,
        orchestrator=cycle(),
        execution_gate=GateDecision(GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.SUPERVISED, evidence_verified=True),
        runner_observed_at=NOW - timedelta(minutes=1),
        orchestrator_observed_at=NOW - timedelta(minutes=1),
        gate_evaluated_at=NOW - timedelta(minutes=1),
        attestation=placeholder,
    )
    values.update(changes)
    selected = ReadinessEvidence(**values)
    if "attestation" not in changes:
        selected = replace(
            selected,
            attestation=replace(selected.attestation, evidence_fingerprint=evidence_fingerprint(selected)),
        )
    return selected


@pytest.mark.parametrize(
    ("selected_context", "selected_evidence", "expected"),
    [
        (context(), evidence(), ReadinessStatus.OFFLINE_HEALTHY),
        (context(scope=AssessmentScope.SUPERVISED), evidence(), ReadinessStatus.SUPERVISED_BLOCKED),
        (context(scope=AssessmentScope.LIVE), evidence(), ReadinessStatus.LIVE_BLOCKED),
        (context(scope=AssessmentScope.UNATTENDED), evidence(execution_gate=GateDecision(GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.LIMITED_UNATTENDED, evidence_verified=True)), ReadinessStatus.UNATTENDED_BLOCKED),
        (context(), evidence(history=history(completed_cycles=1)), ReadinessStatus.OFFLINE_DEGRADED),
        (context(), evidence(acknowledgments=(acknowledgment(AcknowledgmentState.PENDING),)), ReadinessStatus.OPERATOR_REVIEW_REQUIRED),
        (context(), evidence(history=history(ambiguous_restarts=1)), ReadinessStatus.AMBIGUOUS),
    ],
)
def test_readiness_statuses(selected_context: ReadinessContext, selected_evidence: ReadinessEvidence, expected: ReadinessStatus) -> None:
    assert project_readiness(selected_context, policy(), selected_evidence).status is expected


def test_active_suspension_produces_supervised_blocked() -> None:
    halted = suspension(ControllerState.SUSPENDED, review=True)
    operational = project_status(status_context(), status_inputs(suspension=halted))
    report = project_readiness(context(), policy(), evidence(suspension=halted, operational_status=operational))
    assert report.status is ReadinessStatus.SUPERVISED_BLOCKED
    assert ReadinessReason.ACTIVE_SUSPENSION in report.reason_codes


def test_liveness_suspension_request_cannot_hide_behind_healthy_components() -> None:
    unsafe_liveness = liveness(
        LivenessState.HEALTHY,
        LivenessReason.HEARTBEAT_CURRENT,
        request=True,
    )
    report = project_readiness(context(), policy(), evidence(liveness=unsafe_liveness))
    assert report.status is ReadinessStatus.SUPERVISED_BLOCKED
    assert ReadinessReason.ACTIVE_SUSPENSION in report.reason_codes


@pytest.mark.parametrize(
    ("selected_evidence", "reason"),
    [
        (
            evidence(
                operational_status=project_status(
                    status_context(),
                    status_inputs(runner=None, driver=None, orchestrator=None, liveness=None),
                ),
            ),
            ReadinessReason.COMPONENT_WAITING,
        ),
        (
            evidence(
                liveness=liveness(LivenessState.DEGRADED, LivenessReason.UNKNOWN_REASON),
                operational_status=project_status(
                    status_context(),
                    status_inputs(liveness=liveness(LivenessState.DEGRADED, LivenessReason.UNKNOWN_REASON)),
                ),
            ),
            ReadinessReason.COMPONENT_DEGRADED,
        ),
        (
            evidence(
                scheduler=scheduled(RunState.EXPIRED),
                operational_status=project_status(
                    status_context(), status_inputs(scheduler=scheduled(RunState.EXPIRED)),
                ),
            ),
            ReadinessReason.COMPONENT_EXPIRED,
        ),
    ],
)
def test_adverse_public_component_states_never_project_healthy(
    selected_evidence: ReadinessEvidence, reason: ReadinessReason,
) -> None:
    report = project_readiness(context(), policy(), selected_evidence)
    assert report.status is ReadinessStatus.OFFLINE_DEGRADED
    assert reason in report.reason_codes


def test_failed_component_dominates_suspension_waiting_and_review() -> None:
    failed_runner = runner(RunnerState.FAILED_CLOSED, RunnerOutcome.STOPPED_ON_FAILURE, RunnerReason.DRIVER_FAILED)
    halted = suspension(ControllerState.SUSPENDED, review=True)
    report = project_readiness(
        context(), policy(), evidence(
            runner=failed_runner, suspension=halted,
            acknowledgments=(acknowledgment(AcknowledgmentState.PENDING),),
        ),
    )
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.FAILED_COMPONENT in report.reason_codes


def test_ambiguity_dominates_healthy_liveness_and_review() -> None:
    report = project_readiness(
        context(), policy(), evidence(
            history=history(ambiguous_restarts=1),
            acknowledgments=(acknowledgment(AcknowledgmentState.PENDING),),
        ),
    )
    assert report.status is ReadinessStatus.AMBIGUOUS


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"history": history(completed_cycles=1)}, ReadinessReason.INSUFFICIENT_SHADOW_HISTORY),
        ({"history": history(consecutive_failures=1)}, ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES),
        ({"history": history(stale_events=1)}, ReadinessReason.EXCESSIVE_STALE_EVENTS),
        ({"history": history(ambiguous_restarts=1)}, ReadinessReason.AMBIGUOUS_RESTART),
        ({"alerts": (alert(AlertState.DELIVERY_UNCONFIRMED),)}, ReadinessReason.ALERT_DELIVERY_UNCONFIRMED),
        ({"acknowledgments": (acknowledgment(AcknowledgmentState.PENDING),)}, ReadinessReason.OPERATOR_ACK_PENDING),
        ({"liveness": replace(liveness(), consecutive_missed_cycles=1)}, ReadinessReason.MISSED_CYCLE),
    ],
)
def test_each_offline_blocker_is_reported(changes: dict, reason: ReadinessReason) -> None:
    report = project_readiness(context(), policy(), evidence(**changes))
    assert reason in report.reason_codes


@pytest.mark.parametrize("state", list(AcknowledgmentState))
def test_every_durable_acknowledgment_obligation_requires_further_review(state: AcknowledgmentState) -> None:
    report = project_readiness(context(), policy(), evidence(acknowledgments=(acknowledgment(state),)))
    assert report.status in {ReadinessStatus.OPERATOR_REVIEW_REQUIRED, ReadinessStatus.FAILED_CLOSED}
    assert ReadinessReason.FURTHER_REVIEW_REQUIRED in report.reason_codes
    assert (ReadinessReason.OPERATOR_ACK_PENDING in report.reason_codes) is (state is AcknowledgmentState.PENDING)


def test_freshness_exact_boundary_is_valid_and_one_unit_after_is_stale() -> None:
    bound = timedelta(minutes=10)
    at_boundary = history(last_evidence_at=NOW - bound)
    report = project_readiness(context(maximum_evidence_age=bound), policy(), evidence(history=at_boundary))
    assert ReadinessReason.STALE_EVIDENCE not in report.reason_codes
    after = replace(at_boundary, last_evidence_at=NOW - bound - timedelta(microseconds=1))
    report = project_readiness(context(maximum_evidence_age=bound), policy(), evidence(history=after))
    assert ReadinessReason.STALE_EVIDENCE in report.reason_codes
    assert report.status is ReadinessStatus.OFFLINE_DEGRADED


def test_future_evidence_fails_closed() -> None:
    report = project_readiness(
        context(), policy(), evidence(history=history(last_evidence_at=NOW + timedelta(microseconds=1))),
    )
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.FUTURE_EVIDENCE in report.reason_codes


def test_conflicting_public_component_projection_fails_closed() -> None:
    report = project_readiness(
        context(), policy(), evidence(scheduler=scheduled(RunState.WAITING_FOR_SESSION)),
    )
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.CONFLICTING_EVIDENCE in report.reason_codes


def test_alert_ratio_policy_uses_fixed_buckets() -> None:
    alerts = tuple(
        replace(
            alert(), alert_id=f"alert-ratio-{index}",
            last_transition_id=f"alert-transition-ratio-{index}",
            **({} if index < 4 else dict(
                state=AlertState.DELIVERY_UNCONFIRMED,
                reason=AlertReason.DELIVERY_EVIDENCE_MISSING,
                acknowledgment_status=AlertAcknowledgmentStatus.UNCONFIRMED,
            )),
        )
        for index in range(5)
    )
    report = project_readiness(
        context(), policy(required_alert_confirmation_ratio_bucket=RatioBucket.COMPLETE),
        evidence(alerts=alerts),
    )
    assert report.alert_confirmation_ratio_bucket is RatioBucket.HIGH
    assert ReadinessReason.ALERT_CONFIRMATION_RATIO_LOW in report.reason_codes


def test_healthy_offline_history_never_implies_supervised_live_or_unattended_readiness() -> None:
    report = project_readiness(context(), policy(), evidence())
    assert report.status is ReadinessStatus.OFFLINE_HEALTHY
    assert report.offline_healthy is True
    assert report.supervised_ready is False
    assert report.live_ready is False
    assert report.unattended_ready is False
    assert report.protection_grounded is False
    assert report.activation_approved is False
    assert report.permits_automated_action is False
    assert ReadinessReason.PROTECTION_NOT_GROUNDED in report.reason_codes
    assert ReadinessReason.ACTIVATION_APPROVALS_EMPTY in report.reason_codes


def test_one_successful_cycle_cannot_satisfy_policy() -> None:
    report = project_readiness(context(), policy(), evidence(history=history(completed_cycles=1)))
    assert report.offline_healthy is False
    with pytest.raises(ReadinessError, match="one successful cycle"):
        policy(minimum_completed_cycles=1)


def test_history_policy_exact_count_boundaries_are_inclusive() -> None:
    exact = project_readiness(
        context(), policy(maximum_consecutive_failures=1, maximum_allowed_stale_events=1),
        evidence(history=history(consecutive_failures=1, stale_events=1)),
    )
    assert ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES not in exact.reason_codes
    assert ReadinessReason.EXCESSIVE_STALE_EVENTS not in exact.reason_codes
    over = project_readiness(
        context(), policy(maximum_consecutive_failures=1, maximum_allowed_stale_events=1),
        evidence(history=history(consecutive_failures=2, stale_events=2)),
    )
    assert ReadinessReason.EXCESSIVE_CONSECUTIVE_FAILURES in over.reason_codes
    assert ReadinessReason.EXCESSIVE_STALE_EVENTS in over.reason_codes


def test_serialization_is_deterministic_exact_and_restart_safe() -> None:
    report = project_readiness(context(), policy(), evidence())
    value = report.to_dict()
    assert set(value) == REPORT_KEYS
    assert json.dumps(value, sort_keys=True, separators=(",", ":")) == json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))
    assert load_report(
        value, evaluation_time=NOW + timedelta(minutes=1), maximum_age=timedelta(minutes=5),
    ) == report


@pytest.mark.parametrize("field", ["exception", "symbol", "account", "quantity", "passing_tests", "commit_count", "pr_count"])
def test_unknown_private_or_activity_fields_are_rejected(field: str) -> None:
    value = project_readiness(context(), policy(), evidence()).to_dict()
    value[field] = "PRIVATE raw text"
    with pytest.raises(ReadinessError, match="fields"):
        load_report(value, evaluation_time=NOW, maximum_age=timedelta(minutes=5))


def test_stale_future_and_malformed_stored_reports_are_rejected() -> None:
    value = project_readiness(context(), policy(), evidence()).to_dict()
    with pytest.raises(ReadinessError, match="stale"):
        load_report(value, evaluation_time=NOW + timedelta(minutes=6), maximum_age=timedelta(minutes=5))
    with pytest.raises(ReadinessError, match="future"):
        load_report(value, evaluation_time=NOW - timedelta(microseconds=1), maximum_age=timedelta(minutes=5))
    malformed = dict(value, status="LIVE_READY")
    with pytest.raises(ReadinessError):
        load_report(malformed, evaluation_time=NOW, maximum_age=timedelta(minutes=5))


def test_stored_report_cannot_hide_a_dominant_blocker_or_change_scope_status() -> None:
    value = project_readiness(context(), policy(), evidence()).to_dict()
    value["reason_codes"] = [ReadinessReason.ACTIVE_SUSPENSION.value, *value["reason_codes"]]
    with pytest.raises(ReadinessError):
        load_report(value, evaluation_time=NOW, maximum_age=timedelta(minutes=5))
    live = project_readiness(context(scope=AssessmentScope.LIVE), policy(), evidence()).to_dict()
    live["status"] = ReadinessStatus.OFFLINE_HEALTHY.value
    with pytest.raises(ReadinessError):
        load_report(live, evaluation_time=NOW, maximum_age=timedelta(minutes=5))


def test_arbitrary_dictionaries_are_not_component_inputs() -> None:
    with pytest.raises(ReadinessError):
        replace(evidence(), history={})  # type: ignore[arg-type]
    with pytest.raises(ReadinessError):
        _project_readiness(context(), policy(), {}, object())  # type: ignore[arg-type]


def test_duplicate_alert_or_acknowledgment_cannot_pad_counts_or_ratio() -> None:
    with pytest.raises(ReadinessError, match="alert identities"):
        evidence(alerts=(alert(), alert()))
    with pytest.raises(ReadinessError, match="acknowledgment identities"):
        evidence(acknowledgments=(acknowledgment(), acknowledgment()))


def test_history_counts_and_sequence_are_bound_to_public_runner_result() -> None:
    with pytest.raises(ReadinessError, match="bound"):
        evidence(
            history=history(completed_cycles=99, runner_transition_sequence=99, transition_sequence=100),
            runner=replace(runner(), completed_tick_count=5, next_tick_index=5),
        )


def test_history_requires_distinct_durable_transition_references() -> None:
    with pytest.raises(ReadinessError, match="distinct"):
        history(transition_sequence=1)


def test_fresh_id_padding_or_replayed_observation_cannot_bypass_pinned_attestation() -> None:
    original = evidence()
    verifier = StaticVerifier(
        original.attestation.evidence_fingerprint,
        original.attestation.history_fingerprint,
        original.attestation.history_producer_id,
    )
    padded = replace(
        alert(), alert_id="alert-padding", last_transition_id="alert-transition-padding",
    )
    tampered = replace(
        original, alerts=tuple(sorted((*original.alerts, padded), key=lambda item: item.alert_id)),
        runner_observed_at=NOW,
    )
    tampered = replace(
        tampered,
        attestation=replace(tampered.attestation, evidence_fingerprint=evidence_fingerprint(tampered)),
    )
    report = _project_readiness(context(), policy(), tampered, verifier)
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.EVIDENCE_ATTESTATION_INVALID in report.reason_codes


def test_caller_reauthored_history_requires_the_pinned_runtime_attestation() -> None:
    original = evidence()
    verifier = StaticVerifier(
        original.attestation.evidence_fingerprint,
        original.attestation.history_fingerprint,
        original.attestation.history_producer_id,
    )
    changed_history = history(consecutive_failures=1)
    changed = evidence(history=changed_history)
    report = _project_readiness(context(), policy(), changed, verifier)
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.EVIDENCE_ATTESTATION_INVALID in report.reason_codes


def test_self_declared_evidence_set_attestation_fails_closed() -> None:
    selected = evidence()
    selected = replace(
        selected,
        attestation=replace(selected.attestation, provenance=EvidenceProvenance.SELF_DECLARED),
    )
    selected = replace(
        selected,
        attestation=replace(selected.attestation, evidence_fingerprint=evidence_fingerprint(selected)),
    )
    report = project_readiness(context(), policy(), selected)
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.EVIDENCE_ATTESTATION_INVALID in report.reason_codes


def test_unbound_acknowledgment_source_and_alert_correlation_fail_closed() -> None:
    unbound = replace(
        acknowledgment(), review_item_id="review-item-alert-outbox-alert-missing",
        expected_review_sequence=4, source_kind=AcknowledgmentSourceKind.ALERT_OUTBOX,
        source_id="source-alert-outbox-alert-missing", source_sequence=4,
    )
    wrong_alert = replace(alert(), correlation_id="correlation-other")
    report = project_readiness(
        context(), policy(), evidence(alerts=(wrong_alert,), acknowledgments=(unbound,)),
    )
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.CONFLICTING_EVIDENCE in report.reason_codes


def test_gate_verification_mode_and_scheduler_orchestrator_action_are_checked() -> None:
    unverified = project_readiness(
        context(), policy(), evidence(execution_gate=GateDecision(
            GateVerdict.CLOSED_ACTIVATION_NOT_APPROVED, GateMode.SUPERVISED,
        )),
    )
    assert unverified.status is ReadinessStatus.OFFLINE_DEGRADED
    assert ReadinessReason.GATE_EVIDENCE_UNVERIFIED in unverified.reason_codes

    waiting = scheduled(RunState.WAITING_FOR_SESSION)
    waiting_status = project_status(status_context(), status_inputs(scheduler=waiting))
    mismatch = project_readiness(
        context(), policy(), evidence(scheduler=waiting, operational_status=waiting_status),
    )
    assert mismatch.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.CONFLICTING_EVIDENCE in mismatch.reason_codes


def test_scheduler_reevaluation_is_bound_and_requires_review() -> None:
    original = evidence()
    changed_scheduler = replace(original.scheduler, reevaluation_required=True)
    changed = evidence(scheduler=changed_scheduler)
    assert evidence_fingerprint(changed) != original.attestation.evidence_fingerprint
    report = project_readiness(context(), policy(), changed)
    assert report.status is ReadinessStatus.OPERATOR_REVIEW_REQUIRED
    assert ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED in report.reason_codes


def test_suspension_decision_semantics_are_bound_and_conflicts_fail_closed() -> None:
    original = evidence()
    review = replace(original.suspension, requires_operator_review=True)
    reviewed = evidence(suspension=review)
    assert evidence_fingerprint(reviewed) != original.attestation.evidence_fingerprint
    assert ReadinessReason.SCHEDULER_REEVALUATION_REQUIRED in project_readiness(
        context(), policy(), reviewed,
    ).reason_codes

    contradictory = replace(
        original.suspension,
        requires_operator_review=True,
        scheduler_action=SchedulerAction.SUSPEND,
    )
    report = project_readiness(context(), policy(), evidence(suspension=contradictory))
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.CONFLICTING_EVIDENCE in report.reason_codes


def test_alert_delivery_confirmation_requires_confirmed_acknowledgment() -> None:
    original = evidence()
    contradictory_alert = replace(
        original.alerts[0], acknowledgment_status=AlertAcknowledgmentStatus.UNCONFIRMED,
    )
    changed = evidence(alerts=(contradictory_alert,))
    assert evidence_fingerprint(changed) != original.attestation.evidence_fingerprint
    report = project_readiness(context(), policy(), changed)
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.CONFLICTING_EVIDENCE in report.reason_codes


def test_attestation_must_be_at_or_after_every_observation() -> None:
    selected = evidence()
    assert project_readiness(context(), policy(), selected).status is ReadinessStatus.OFFLINE_HEALTHY

    early = replace(
        selected,
        attestation=replace(selected.attestation, observed_at=NOW - timedelta(microseconds=1)),
    )
    early = replace(
        early,
        attestation=replace(early.attestation, evidence_fingerprint=evidence_fingerprint(early)),
    )
    report = project_readiness(context(), policy(), early)
    assert report.status is ReadinessStatus.FAILED_CLOSED
    assert ReadinessReason.EVIDENCE_ATTESTATION_INVALID in report.reason_codes


def test_noncanonical_gate_explanation_is_rejected() -> None:
    with pytest.raises(ReadinessError, match="explanation"):
        evidence(execution_gate=replace(evidence().execution_gate, explanation="arbitrary provider text"))


def test_stored_component_state_and_attestation_cannot_be_forged() -> None:
    report = project_readiness(context(), policy(), evidence())
    value = report.to_dict()
    failed_component = dict(value, runner_state=RunnerState.FAILED_CLOSED.value)
    with pytest.raises(ReadinessError, match="projection"):
        load_report(failed_component, evaluation_time=NOW, maximum_age=timedelta(minutes=5))
    forged_fingerprint = dict(value, evidence_fingerprint="f" * 64)
    with pytest.raises(ReadinessError, match="projection"):
        load_report(
            forged_fingerprint, evaluation_time=NOW, maximum_age=timedelta(minutes=5),
            verifier=StaticVerifier(
                value["evidence_fingerprint"],
                evidence().attestation.history_fingerprint,
                evidence().attestation.history_producer_id,
            ),
        )


def test_true_readiness_or_permission_flags_are_impossible() -> None:
    report = project_readiness(context(), policy(), evidence())
    for field in ("supervised_ready", "live_ready", "unattended_ready", "protection_grounded", "activation_approved", "permits_automated_action"):
        with pytest.raises(ReadinessError):
            replace(report, **{field: True})
    with pytest.raises(ReadinessError):
        replace(history(), permits_automated_action=True)


def test_no_private_collaborator_network_broker_manifest_clock_or_readiness_expansion() -> None:
    source = MODULE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name.split(".")[0]
        for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert imports.isdisjoint({"requests", "httpx", "socket", "smtplib", "subprocess", "robin_stocks"})
    lowered = source.lower()
    for forbidden in ("._connection", "._cursor", "datetime.now", "datetime.utcnow", "time.time", "authorization_manifest", "live_ready = true", "unattended_ready = true"):
        assert forbidden not in lowered
    assert "LIVE_READY" not in {item.value for item in ReadinessStatus}
    assert "UNATTENDED_READY" not in {item.value for item in ReadinessStatus}


def test_global_safety_state_remains_closed() -> None:
    assert ACTIVATION_APPROVALS == frozenset()
    assert approved_active_protection_states() == frozenset()
    assert "enabled: false" in Path("config/strategy_v0.yaml").read_text(encoding="utf-8")
