"""Privacy-safe status projection for the durable offline execution stack.

The projection consumes public, already-decided values.  It never opens a
collaborator's store, reads a clock, clears suspension, or authorizes action.
Persistence is limited to the deterministic, allowlisted projection itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from src.durable_runner_liveness import LivenessDecision, LivenessState
from src.durable_scheduler import RunState, ScheduledRun
from src.durable_suspension_controller import ControllerState, SuspensionDecision
from src.offline_cycle_driver import DriverOutcome, DriverResult, DriverState
from src.offline_safety_orchestrator import CycleOutcome, CycleResult
from src.offline_shadow_runner import RunnerOutcome, RunnerResult, RunnerState


class StatusReportError(RuntimeError):
    """A fixed, privacy-safe refusal from the status projection."""


class OverallStatus(str, Enum):
    HEALTHY_OFFLINE = "HEALTHY_OFFLINE"
    WAITING = "WAITING"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    SUSPENDED = "SUSPENDED"
    DEGRADED = "DEGRADED"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS = "AMBIGUOUS"
    EXPIRED = "EXPIRED"


class StatusReason(str, Enum):
    MALFORMED_DURABLE_STATE = "MALFORMED_DURABLE_STATE"
    PERMANENT_HALT = "PERMANENT_HALT"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    LIVENESS_SUSPENSION_REQUIRED = "LIVENESS_SUSPENSION_REQUIRED"
    COMPONENT_FAILED = "COMPONENT_FAILED"
    COMPONENT_EXPIRED = "COMPONENT_EXPIRED"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    WAITING_FOR_OFFLINE_CYCLE = "WAITING_FOR_OFFLINE_CYCLE"
    LIVENESS_DEGRADED = "LIVENESS_DEGRADED"
    HEALTHY_OFFLINE = "HEALTHY_OFFLINE"


class CountBucket(str, Enum):
    ZERO = "ZERO"
    ONE = "ONE"
    MANY = "MANY"


@dataclass(frozen=True)
class ReportFreshnessPolicy:
    maximum_age: timedelta

    def __post_init__(self) -> None:
        if not isinstance(self.maximum_age, timedelta) or self.maximum_age <= timedelta(0):
            raise StatusReportError("report freshness policy is invalid")


_FORBIDDEN_ID_PARTS = (
    "account", "broker", "credential", "manifest", "order", "symbol",
    "token", "username", "robinhood", "\\", "/", ":", "@", ".",
)


def _identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or value != value.strip():
        raise StatusReportError(f"{label} is invalid")
    if len(value) > 96 or not value.replace("-", "").replace("_", "").isalnum():
        raise StatusReportError(f"{label} is invalid")
    if any(part in value.lower() for part in _FORBIDDEN_ID_PARTS):
        raise StatusReportError(f"{label} is invalid")
    return value


def _moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise StatusReportError(f"{label} is invalid")
    return value


def _bucket(value: int) -> CountBucket:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise StatusReportError("count is invalid")
    if value == 0:
        return CountBucket.ZERO
    if value == 1:
        return CountBucket.ONE
    return CountBucket.MANY


@dataclass(frozen=True)
class StatusContext:
    report_id: str
    runner_id: str
    scheduler_run_id: str
    reported_at: datetime
    maximum_component_age: timedelta

    def __post_init__(self) -> None:
        _identifier(self.report_id, "status-", "report identity")
        _identifier(self.runner_id, "runner-", "runner identity")
        _identifier(self.scheduler_run_id, "run-", "scheduler run identity")
        _moment(self.reported_at, "report timestamp")
        if not isinstance(self.maximum_component_age, timedelta) or self.maximum_component_age <= timedelta(0):
            raise StatusReportError("component freshness bound is invalid")


@dataclass(frozen=True)
class StatusInputs:
    runner: RunnerResult | None
    driver: DriverResult | None
    orchestrator: CycleResult | None
    scheduler: ScheduledRun | None
    suspension: SuspensionDecision | None
    liveness: LivenessDecision | None

    def __post_init__(self) -> None:
        expected = (
            (self.runner, RunnerResult, "runner"),
            (self.driver, DriverResult, "driver"),
            (self.orchestrator, CycleResult, "orchestrator"),
            (self.scheduler, ScheduledRun, "scheduler"),
            (self.suspension, SuspensionDecision, "suspension"),
            (self.liveness, LivenessDecision, "liveness"),
        )
        for value, kind, label in expected:
            if value is not None and not isinstance(value, kind):
                raise StatusReportError(f"{label} input is not a public decided result")


REPORT_KEYS = frozenset({
    "report_id", "runner_id", "scheduler_run_id", "overall_status",
    "reason_codes", "runner_state", "runner_outcome", "driver_state",
    "driver_outcome", "orchestrator_state",
    "scheduler_state", "suspension_state", "liveness_state",
    "completed_tick_bucket", "missed_cycle_bucket", "liveness_transition_id",
    "reported_at", "scheduler_updated_at", "liveness_evaluated_at",
    "requires_operator_review", "suspension_active", "protection_grounded",
    "activation_approved", "permits_automated_action",
})


@dataclass(frozen=True)
class OperationalStatusReport:
    report_id: str
    runner_id: str
    scheduler_run_id: str
    overall_status: OverallStatus
    reason_codes: tuple[StatusReason, ...]
    runner_state: RunnerState | None
    runner_outcome: RunnerOutcome | None
    driver_state: DriverState | None
    driver_outcome: DriverOutcome | None
    orchestrator_state: CycleOutcome | None
    scheduler_state: RunState | None
    suspension_state: ControllerState | None
    liveness_state: LivenessState | None
    completed_tick_bucket: CountBucket
    missed_cycle_bucket: CountBucket
    liveness_transition_id: str | None
    reported_at: datetime
    scheduler_updated_at: datetime | None
    liveness_evaluated_at: datetime | None
    requires_operator_review: bool
    suspension_active: bool
    protection_grounded: bool = False
    activation_approved: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.report_id, "status-", "report identity")
        _identifier(self.runner_id, "runner-", "runner identity")
        _identifier(self.scheduler_run_id, "run-", "scheduler run identity")
        if not isinstance(self.overall_status, OverallStatus):
            raise StatusReportError("overall status is invalid")
        if not self.reason_codes or any(not isinstance(x, StatusReason) for x in self.reason_codes):
            raise StatusReportError("reason codes are invalid")
        if len(set(self.reason_codes)) != len(self.reason_codes):
            raise StatusReportError("reason codes are duplicated")
        enum_fields = (
            (self.runner_state, RunnerState), (self.runner_outcome, RunnerOutcome),
            (self.driver_state, DriverState), (self.driver_outcome, DriverOutcome),
            (self.orchestrator_state, CycleOutcome), (self.scheduler_state, RunState),
            (self.suspension_state, ControllerState), (self.liveness_state, LivenessState),
        )
        if any(value is not None and not isinstance(value, kind) for value, kind in enum_fields):
            raise StatusReportError("component state is invalid")
        if not isinstance(self.completed_tick_bucket, CountBucket) or not isinstance(self.missed_cycle_bucket, CountBucket):
            raise StatusReportError("count bucket is invalid")
        if self.liveness_transition_id is not None:
            _identifier(self.liveness_transition_id, "liveness-transition-", "liveness transition identity")
        _moment(self.reported_at, "report timestamp")
        for value, label in ((self.scheduler_updated_at, "scheduler timestamp"), (self.liveness_evaluated_at, "liveness timestamp")):
            if value is not None:
                _moment(value, label)
        if any(not isinstance(x, bool) for x in (self.requires_operator_review, self.suspension_active)):
            raise StatusReportError("status flags are invalid")
        if self.protection_grounded is not False or self.activation_approved is not False or self.permits_automated_action is not False:
            raise StatusReportError("an offline status report never authorizes action")
        if self.reason_codes[0] not in _STATUS_PRIMARY_REASONS[self.overall_status]:
            raise StatusReportError("overall status and primary reason conflict")
        _validate_report_semantics(self)

    def to_dict(self) -> dict[str, Any]:
        def enum_value(value: Enum | None) -> str | None:
            return value.value if value is not None else None

        return {
            "report_id": self.report_id,
            "runner_id": self.runner_id,
            "scheduler_run_id": self.scheduler_run_id,
            "overall_status": self.overall_status.value,
            "reason_codes": [reason.value for reason in self.reason_codes],
            "runner_state": enum_value(self.runner_state),
            "runner_outcome": enum_value(self.runner_outcome),
            "driver_state": enum_value(self.driver_state),
            "driver_outcome": enum_value(self.driver_outcome),
            "orchestrator_state": enum_value(self.orchestrator_state),
            "scheduler_state": enum_value(self.scheduler_state),
            "suspension_state": enum_value(self.suspension_state),
            "liveness_state": enum_value(self.liveness_state),
            "completed_tick_bucket": self.completed_tick_bucket.value,
            "missed_cycle_bucket": self.missed_cycle_bucket.value,
            "liveness_transition_id": self.liveness_transition_id,
            "reported_at": self.reported_at.isoformat(),
            "scheduler_updated_at": self.scheduler_updated_at.isoformat() if self.scheduler_updated_at else None,
            "liveness_evaluated_at": self.liveness_evaluated_at.isoformat() if self.liveness_evaluated_at else None,
            "requires_operator_review": self.requires_operator_review,
            "suspension_active": self.suspension_active,
            "protection_grounded": False,
            "activation_approved": False,
            "permits_automated_action": False,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "OperationalStatusReport":
        if not isinstance(value, dict) or set(value) != REPORT_KEYS:
            raise StatusReportError("stored report fields are invalid")
        def optional_enum(kind: type[Enum], raw: object) -> Enum | None:
            if raw is None:
                return None
            if not isinstance(raw, str):
                raise StatusReportError("stored component state is invalid")
            try:
                return kind(raw)
            except ValueError:
                raise StatusReportError("stored component state is invalid") from None
        def optional_moment(raw: object) -> datetime | None:
            if raw is None:
                return None
            if not isinstance(raw, str):
                raise StatusReportError("stored timestamp is invalid")
            try:
                return _moment(datetime.fromisoformat(raw), "stored timestamp")
            except ValueError:
                raise StatusReportError("stored timestamp is invalid") from None
        reasons = value["reason_codes"]
        if not isinstance(reasons, list) or any(not isinstance(x, str) for x in reasons):
            raise StatusReportError("stored reason codes are invalid")
        try:
            return cls(
                report_id=value["report_id"], runner_id=value["runner_id"],
                scheduler_run_id=value["scheduler_run_id"],
                overall_status=OverallStatus(value["overall_status"]),
                reason_codes=tuple(StatusReason(x) for x in reasons),
                runner_state=optional_enum(RunnerState, value["runner_state"]),
                runner_outcome=optional_enum(RunnerOutcome, value["runner_outcome"]),
                driver_state=optional_enum(DriverState, value["driver_state"]),
                driver_outcome=optional_enum(DriverOutcome, value["driver_outcome"]),
                orchestrator_state=optional_enum(CycleOutcome, value["orchestrator_state"]),
                scheduler_state=optional_enum(RunState, value["scheduler_state"]),
                suspension_state=optional_enum(ControllerState, value["suspension_state"]),
                liveness_state=optional_enum(LivenessState, value["liveness_state"]),
                completed_tick_bucket=CountBucket(value["completed_tick_bucket"]),
                missed_cycle_bucket=CountBucket(value["missed_cycle_bucket"]),
                liveness_transition_id=value["liveness_transition_id"],
                reported_at=optional_moment(value["reported_at"]),
                scheduler_updated_at=optional_moment(value["scheduler_updated_at"]),
                liveness_evaluated_at=optional_moment(value["liveness_evaluated_at"]),
                requires_operator_review=value["requires_operator_review"],
                suspension_active=value["suspension_active"],
                protection_grounded=value["protection_grounded"],
                activation_approved=value["activation_approved"],
                permits_automated_action=value["permits_automated_action"],
            )
        except (ValueError, KeyError, TypeError):
            raise StatusReportError("stored report value is invalid") from None


_STATUS_PRIMARY_REASONS = {
    OverallStatus.HEALTHY_OFFLINE: {StatusReason.HEALTHY_OFFLINE},
    OverallStatus.WAITING: {StatusReason.WAITING_FOR_OFFLINE_CYCLE},
    OverallStatus.REVIEW_REQUIRED: {StatusReason.OPERATOR_REVIEW_REQUIRED},
    OverallStatus.SUSPENDED: {StatusReason.ACTIVE_SUSPENSION, StatusReason.LIVENESS_SUSPENSION_REQUIRED},
    OverallStatus.DEGRADED: {StatusReason.LIVENESS_DEGRADED},
    OverallStatus.FAILED_CLOSED: {
        StatusReason.MALFORMED_DURABLE_STATE,
        StatusReason.PERMANENT_HALT,
        StatusReason.COMPONENT_FAILED,
    },
    OverallStatus.AMBIGUOUS: {StatusReason.AMBIGUOUS_RESTART},
    OverallStatus.EXPIRED: {StatusReason.COMPONENT_EXPIRED},
}


def _validate_report_semantics(report: OperationalStatusReport) -> None:
    """Prevent a stored headline from hiding adverse component fields."""
    if (report.runner_state is None) != (report.runner_outcome is None):
        raise StatusReportError("runner projection fields conflict")
    if report.runner_state is None and report.completed_tick_bucket is not CountBucket.ZERO:
        raise StatusReportError("runner count lacks a component")
    if (report.driver_state is None) != (report.driver_outcome is None):
        raise StatusReportError("driver projection fields conflict")
    if (report.scheduler_state is None) != (report.scheduler_updated_at is None):
        raise StatusReportError("scheduler timestamp conflicts with component")
    if report.liveness_state is None:
        if report.liveness_transition_id is not None or report.liveness_evaluated_at is not None or report.missed_cycle_bucket is not CountBucket.ZERO:
            raise StatusReportError("liveness fields lack a component")
    elif report.liveness_transition_id is None or report.liveness_evaluated_at is None:
        raise StatusReportError("liveness component lacks durable identity")
    for value in (report.scheduler_updated_at, report.liveness_evaluated_at):
        if value is not None and value > report.reported_at:
            raise StatusReportError("component timestamp is future-dated")
    malformed_primary = report.reason_codes[0] is StatusReason.MALFORMED_DURABLE_STATE
    if malformed_primary and report.overall_status is not OverallStatus.FAILED_CLOSED:
        raise StatusReportError("malformed durable state must fail closed")
    permanent = report.suspension_state is ControllerState.PERMANENT_HALT
    active_component = any((
        report.runner_outcome is RunnerOutcome.STOPPED_ON_SUSPENSION,
        report.driver_outcome is DriverOutcome.SUSPENDED,
        report.orchestrator_state is CycleOutcome.SUSPENDED,
        report.scheduler_state is RunState.SUSPENDED,
        report.suspension_state in {
            ControllerState.SUSPENDED, ControllerState.CLEARANCE_PENDING_REVIEW,
            ControllerState.PERMANENT_HALT, ControllerState.MALFORMED_STATE,
        },
    ))
    ambiguous = any((
        report.runner_state is RunnerState.AMBIGUOUS_RESTART,
        report.runner_outcome is RunnerOutcome.STOPPED_ON_AMBIGUITY,
        report.driver_state is DriverState.AMBIGUOUS_RESTART,
        report.driver_outcome is DriverOutcome.AMBIGUOUS_RESTART,
        report.orchestrator_state is CycleOutcome.AMBIGUOUS_RESTART,
        report.scheduler_state is RunState.AMBIGUOUS_RESTART,
    ))
    unsafe_liveness = report.liveness_state in {
        LivenessState.STALE, LivenessState.SUSPENSION_REQUIRED, LivenessState.FAILED_CLOSED,
    }
    failed = any((
        report.runner_state is RunnerState.FAILED_CLOSED,
        report.runner_outcome in {RunnerOutcome.STOPPED_ON_FAILURE, RunnerOutcome.FAILED_CLOSED},
        report.driver_state is DriverState.FAILED_CLOSED,
        report.driver_outcome is DriverOutcome.FAILED_CLOSED and report.driver_state is not DriverState.EXPIRED,
        report.orchestrator_state is CycleOutcome.FAILED_CLOSED,
        report.scheduler_state is RunState.FAILED_CLOSED,
        report.liveness_state is LivenessState.FAILED_CLOSED,
    ))
    expired = any((
        report.runner_state is RunnerState.EXPIRED,
        report.runner_outcome is RunnerOutcome.EXPIRED,
        report.driver_state is DriverState.EXPIRED,
        report.scheduler_state is RunState.EXPIRED,
    ))
    review = any((
        report.runner_outcome is RunnerOutcome.STOPPED_ON_REVIEW,
        report.driver_outcome is DriverOutcome.OPERATOR_REVIEW_REQUIRED,
        report.orchestrator_state is CycleOutcome.REQUIRE_OPERATOR_REVIEW,
        report.scheduler_state in {RunState.READY_FOR_SUPERVISED_REVIEW, RunState.SUPERVISED_REVIEW_REQUIRED},
    ))
    degraded = report.liveness_state in {
        LivenessState.DEGRADED, LivenessState.STALE, LivenessState.MISSED_CYCLE,
    }
    missing = any(value is None for value in (
        report.runner_state, report.runner_outcome, report.driver_state,
        report.driver_outcome, report.orchestrator_state, report.scheduler_state,
        report.suspension_state, report.liveness_state,
    ))
    waiting_signal = any((
        report.driver_outcome is DriverOutcome.WAIT,
        report.orchestrator_state is CycleOutcome.WAIT_FOR_SESSION,
        report.scheduler_state in {
            RunState.CREATED, RunState.WAITING_FOR_SESSION, RunState.PRECONDITION_CHECK,
            RunState.RUNNING_OFFLINE_EVALUATION,
        },
        report.liveness_state is LivenessState.UNKNOWN,
    ))
    liveness_latched = report.suspension_active and not active_component
    if malformed_primary:
        expected, primary = OverallStatus.FAILED_CLOSED, StatusReason.MALFORMED_DURABLE_STATE
    elif permanent:
        expected, primary = OverallStatus.FAILED_CLOSED, StatusReason.PERMANENT_HALT
    elif active_component:
        expected, primary = OverallStatus.SUSPENDED, StatusReason.ACTIVE_SUSPENSION
    elif ambiguous:
        expected, primary = OverallStatus.AMBIGUOUS, StatusReason.AMBIGUOUS_RESTART
    elif unsafe_liveness or liveness_latched:
        expected, primary = OverallStatus.SUSPENDED, StatusReason.LIVENESS_SUSPENSION_REQUIRED
    elif failed:
        expected, primary = OverallStatus.FAILED_CLOSED, StatusReason.COMPONENT_FAILED
    elif expired:
        expected, primary = OverallStatus.EXPIRED, StatusReason.COMPONENT_EXPIRED
    elif review or report.requires_operator_review:
        expected, primary = OverallStatus.REVIEW_REQUIRED, StatusReason.OPERATOR_REVIEW_REQUIRED
    elif degraded:
        expected, primary = OverallStatus.DEGRADED, StatusReason.LIVENESS_DEGRADED
    elif missing or waiting_signal:
        expected, primary = OverallStatus.WAITING, StatusReason.WAITING_FOR_OFFLINE_CYCLE
    else:
        expected, primary = OverallStatus.HEALTHY_OFFLINE, StatusReason.HEALTHY_OFFLINE
    if report.overall_status is not expected or report.reason_codes[0] is not primary:
        raise StatusReportError("status headline hides an adverse component")
    expected_suspension = active_component or unsafe_liveness or liveness_latched
    if report.suspension_active != expected_suspension:
        raise StatusReportError("suspension flag conflicts with component state")
    if expected_suspension or permanent:
        if not report.requires_operator_review:
            raise StatusReportError("operator review flag conflicts with halted state")
    required_reasons: list[StatusReason] = [primary]
    for present, reason in (
        (permanent, StatusReason.PERMANENT_HALT),
        (active_component, StatusReason.ACTIVE_SUSPENSION),
        (ambiguous, StatusReason.AMBIGUOUS_RESTART),
        (unsafe_liveness or liveness_latched, StatusReason.LIVENESS_SUSPENSION_REQUIRED),
        (failed, StatusReason.COMPONENT_FAILED),
        (expired, StatusReason.COMPONENT_EXPIRED),
        (report.requires_operator_review, StatusReason.OPERATOR_REVIEW_REQUIRED),
        (degraded, StatusReason.LIVENESS_DEGRADED),
    ):
        if present and reason not in required_reasons:
            required_reasons.append(reason)
    if report.reason_codes != tuple(required_reasons):
        raise StatusReportError("status reasons omit or add component facts")


def _append(reasons: list[StatusReason], reason: StatusReason) -> None:
    if reason not in reasons:
        reasons.append(reason)


def project_status(context: StatusContext, inputs: StatusInputs) -> OperationalStatusReport:
    """Project one deterministic report using the documented dominance ladder."""
    if not isinstance(context, StatusContext) or not isinstance(inputs, StatusInputs):
        raise StatusReportError("typed status context and inputs are required")

    malformed = False
    if inputs.runner is not None and inputs.runner.runner_id != context.runner_id:
        malformed = True
    if inputs.scheduler is not None and inputs.scheduler.run_id != context.scheduler_run_id:
        malformed = True
    if inputs.liveness is not None and inputs.liveness.record.runner_id != context.runner_id:
        malformed = True
    if inputs.suspension is not None and inputs.suspension.state is ControllerState.MALFORMED_STATE:
        malformed = True
    if inputs.orchestrator is not None and inputs.driver is None:
        malformed = True
    if inputs.driver is not None:
        has_orchestrator_fields = (
            inputs.driver.orchestrator_outcome is not None
            or inputs.driver.orchestrator_reason is not None
        )
        if has_orchestrator_fields != (inputs.orchestrator is not None):
            malformed = True
        elif inputs.orchestrator is not None and (
            inputs.driver.orchestrator_outcome is not inputs.orchestrator.outcome
            or inputs.driver.orchestrator_reason is not inputs.orchestrator.reason
        ):
            malformed = True
    if inputs.runner is not None and inputs.driver is not None:
        allowed_driver_outcomes = {
            RunnerOutcome.COMPLETED_BOUND: {"WAIT", "OFFLINE_COMPLETE", "COMPLETED_NO_ACTION"},
            RunnerOutcome.STOPPED_ON_REVIEW: {"OPERATOR_REVIEW_REQUIRED"},
            RunnerOutcome.STOPPED_ON_SUSPENSION: {"SUSPENDED"},
            RunnerOutcome.STOPPED_ON_FAILURE: {"FAILED_CLOSED"},
            RunnerOutcome.STOPPED_ON_AMBIGUITY: {"AMBIGUOUS_RESTART"},
            RunnerOutcome.EXPIRED: {"FAILED_CLOSED"},
            RunnerOutcome.FAILED_CLOSED: {"FAILED_CLOSED"},
        }
        if inputs.driver.outcome.value not in allowed_driver_outcomes[inputs.runner.outcome]:
            malformed = True
    scheduler_at = inputs.scheduler.updated_at if inputs.scheduler else None
    liveness_at = inputs.liveness.record.evaluation_time if inputs.liveness else None
    if any(value is not None and value > context.reported_at for value in (scheduler_at, liveness_at)):
        malformed = True
    if any(
        value is not None and context.reported_at - value > context.maximum_component_age
        for value in (scheduler_at, liveness_at)
    ):
        malformed = True

    controller = inputs.suspension
    permanent = controller is not None and controller.state is ControllerState.PERMANENT_HALT
    active = any((
        controller is not None and controller.blocks_work,
        inputs.runner is not None and inputs.runner.outcome is RunnerOutcome.STOPPED_ON_SUSPENSION,
        inputs.driver is not None and inputs.driver.outcome is DriverOutcome.SUSPENDED,
        inputs.orchestrator is not None and inputs.orchestrator.outcome is CycleOutcome.SUSPENDED,
        inputs.scheduler is not None and inputs.scheduler.state is RunState.SUSPENDED,
    ))
    liveness_suspend = inputs.liveness is not None and inputs.liveness.suspension.request_suspension
    ambiguous = any((
        inputs.runner is not None and inputs.runner.state is RunnerState.AMBIGUOUS_RESTART,
        inputs.driver is not None and inputs.driver.state is DriverState.AMBIGUOUS_RESTART,
        inputs.orchestrator is not None and inputs.orchestrator.outcome is CycleOutcome.AMBIGUOUS_RESTART,
        inputs.scheduler is not None and inputs.scheduler.state is RunState.AMBIGUOUS_RESTART,
    ))
    failed = any((
        inputs.runner is not None and inputs.runner.state is RunnerState.FAILED_CLOSED,
        inputs.driver is not None and inputs.driver.state is DriverState.FAILED_CLOSED,
        inputs.orchestrator is not None and inputs.orchestrator.outcome is CycleOutcome.FAILED_CLOSED,
        inputs.scheduler is not None and inputs.scheduler.state is RunState.FAILED_CLOSED,
        inputs.liveness is not None and inputs.liveness.record.state is LivenessState.FAILED_CLOSED,
    ))
    expired = any((
        inputs.runner is not None and inputs.runner.state is RunnerState.EXPIRED,
        inputs.driver is not None and inputs.driver.state is DriverState.EXPIRED,
        inputs.scheduler is not None and inputs.scheduler.state is RunState.EXPIRED,
    ))
    review = any((
        inputs.runner is not None and inputs.runner.outcome is RunnerOutcome.STOPPED_ON_REVIEW,
        inputs.driver is not None and inputs.driver.outcome.value == "OPERATOR_REVIEW_REQUIRED",
        inputs.orchestrator is not None and inputs.orchestrator.outcome is CycleOutcome.REQUIRE_OPERATOR_REVIEW,
        inputs.scheduler is not None and inputs.scheduler.state in {RunState.READY_FOR_SUPERVISED_REVIEW, RunState.SUPERVISED_REVIEW_REQUIRED},
        controller is not None and controller.requires_operator_review,
    ))
    degraded = inputs.liveness is not None and inputs.liveness.record.state in {
        LivenessState.DEGRADED, LivenessState.STALE, LivenessState.MISSED_CYCLE,
    }
    operator_review = (
        review or permanent or active or liveness_suspend or ambiguous or failed or malformed
    )
    waiting = any((
        inputs.runner is None,
        inputs.driver is None,
        inputs.orchestrator is None,
        inputs.scheduler is None,
        inputs.suspension is None,
        inputs.liveness is None,
        inputs.driver is not None and inputs.driver.outcome.value == "WAIT",
        inputs.orchestrator is not None and inputs.orchestrator.outcome is CycleOutcome.WAIT_FOR_SESSION,
        inputs.scheduler is not None and inputs.scheduler.state in {RunState.CREATED, RunState.WAITING_FOR_SESSION, RunState.PRECONDITION_CHECK, RunState.RUNNING_OFFLINE_EVALUATION},
        inputs.liveness is not None and inputs.liveness.record.state is LivenessState.UNKNOWN,
    ))

    reasons: list[StatusReason] = []
    if malformed:
        status = OverallStatus.FAILED_CLOSED
        _append(reasons, StatusReason.MALFORMED_DURABLE_STATE)
    elif permanent:
        status = OverallStatus.FAILED_CLOSED
        _append(reasons, StatusReason.PERMANENT_HALT)
    elif active:
        status = OverallStatus.SUSPENDED
        _append(reasons, StatusReason.ACTIVE_SUSPENSION)
    elif ambiguous:
        status = OverallStatus.AMBIGUOUS
        _append(reasons, StatusReason.AMBIGUOUS_RESTART)
    elif liveness_suspend:
        status = OverallStatus.SUSPENDED
        _append(reasons, StatusReason.LIVENESS_SUSPENSION_REQUIRED)
    elif failed:
        status = OverallStatus.FAILED_CLOSED
        _append(reasons, StatusReason.COMPONENT_FAILED)
    elif expired:
        status = OverallStatus.EXPIRED
        _append(reasons, StatusReason.COMPONENT_EXPIRED)
    elif review:
        status = OverallStatus.REVIEW_REQUIRED
        _append(reasons, StatusReason.OPERATOR_REVIEW_REQUIRED)
    elif degraded:
        status = OverallStatus.DEGRADED
        _append(reasons, StatusReason.LIVENESS_DEGRADED)
    elif waiting:
        status = OverallStatus.WAITING
        _append(reasons, StatusReason.WAITING_FOR_OFFLINE_CYCLE)
    else:
        status = OverallStatus.HEALTHY_OFFLINE
        _append(reasons, StatusReason.HEALTHY_OFFLINE)

    # Preserve every adverse fact after the dominant reason; a favorable top
    # line can therefore never hide a failed or suspended component.
    if permanent: _append(reasons, StatusReason.PERMANENT_HALT)
    if active: _append(reasons, StatusReason.ACTIVE_SUSPENSION)
    if ambiguous: _append(reasons, StatusReason.AMBIGUOUS_RESTART)
    if liveness_suspend: _append(reasons, StatusReason.LIVENESS_SUSPENSION_REQUIRED)
    if failed: _append(reasons, StatusReason.COMPONENT_FAILED)
    if expired: _append(reasons, StatusReason.COMPONENT_EXPIRED)
    if operator_review: _append(reasons, StatusReason.OPERATOR_REVIEW_REQUIRED)
    if degraded: _append(reasons, StatusReason.LIVENESS_DEGRADED)

    return OperationalStatusReport(
        report_id=context.report_id, runner_id=context.runner_id,
        scheduler_run_id=context.scheduler_run_id, overall_status=status,
        reason_codes=tuple(reasons),
        runner_state=inputs.runner.state if inputs.runner else None,
        runner_outcome=inputs.runner.outcome if inputs.runner else None,
        driver_state=inputs.driver.state if inputs.driver else None,
        driver_outcome=inputs.driver.outcome if inputs.driver else None,
        orchestrator_state=inputs.orchestrator.outcome if inputs.orchestrator else None,
        scheduler_state=inputs.scheduler.state if inputs.scheduler else None,
        suspension_state=controller.state if controller else None,
        liveness_state=inputs.liveness.record.state if inputs.liveness else None,
        completed_tick_bucket=_bucket(inputs.runner.completed_tick_count if inputs.runner else 0),
        missed_cycle_bucket=_bucket(inputs.liveness.consecutive_missed_cycles if inputs.liveness else 0),
        liveness_transition_id=inputs.liveness.record.transition_id if inputs.liveness else None,
        reported_at=context.reported_at, scheduler_updated_at=scheduler_at,
        liveness_evaluated_at=liveness_at,
        requires_operator_review=operator_review,
        suspension_active=active or liveness_suspend,
    )


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_SCHEMA = """
CREATE TABLE IF NOT EXISTS status_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS status_reports (
    report_id TEXT PRIMARY KEY,
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    stored_at TEXT NOT NULL
);
"""


def _database_path(value: object) -> Path:
    if not isinstance(value, (str, os.PathLike)) or str(value).strip() in {"", ":memory:"}:
        raise StatusReportError("status database path is invalid")
    path = Path(value)
    if path.is_absolute():
        raise StatusReportError("status database path must be relative")
    resolved = (Path.cwd() / path).resolve()
    repository_data = (REPOSITORY_ROOT / "data").resolve()
    if resolved == repository_data or repository_data in resolved.parents:
        raise StatusReportError("repository data paths are prohibited")
    return resolved


class OperationalStatusStore:
    """Append-only storage for allowlisted status projections."""

    def __init__(self, database_path: str | os.PathLike[str], policy: ReportFreshnessPolicy):
        if not isinstance(policy, ReportFreshnessPolicy):
            raise StatusReportError("report freshness policy is required")
        self._path = _database_path(database_path)
        self._policy = policy
        self._path.parent.mkdir(parents=True, exist_ok=True)
        signature = str(policy.maximum_age // timedelta(microseconds=1))
        try:
            with sqlite3.connect(self._path) as connection:
                connection.executescript(_SCHEMA)
                existing = connection.execute(
                    "SELECT value FROM status_meta WHERE key = 'maximum_age_microseconds'"
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO status_meta(key,value) VALUES('maximum_age_microseconds',?)",
                        (signature,),
                    )
                elif existing != (signature,):
                    raise StatusReportError("stored report policy conflicts with requested policy")
        except sqlite3.Error:
            raise StatusReportError("status persistence failed") from None

    @staticmethod
    def _encoded(report: OperationalStatusReport) -> str:
        return json.dumps(report.to_dict(), sort_keys=True, separators=(",", ":"))

    def persist(self, report: OperationalStatusReport, *, stored_at: datetime) -> None:
        if not isinstance(report, OperationalStatusReport):
            raise StatusReportError("typed status report is required")
        _moment(stored_at, "storage timestamp")
        if stored_at < report.reported_at:
            raise StatusReportError("storage timestamp predates report")
        payload = self._encoded(report)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        try:
            with sqlite3.connect(self._path) as connection:
                existing = connection.execute(
                    "SELECT payload_json, payload_sha256, stored_at FROM status_reports WHERE report_id = ?",
                    (report.report_id,),
                ).fetchone()
                row = (payload, digest, stored_at.isoformat())
                if existing is not None:
                    if existing != row:
                        raise StatusReportError("report identity is already bound")
                    return
                connection.execute(
                    "INSERT INTO status_reports(report_id,payload_json,payload_sha256,stored_at) VALUES(?,?,?,?)",
                    (report.report_id, *row),
                )
        except sqlite3.Error as exc:
            raise StatusReportError("status persistence failed") from None

    def load(self, report_id: str, *, evaluation_time: datetime) -> OperationalStatusReport | None:
        _identifier(report_id, "status-", "report identity")
        _moment(evaluation_time, "evaluation timestamp")
        try:
            with sqlite3.connect(self._path) as connection:
                row = connection.execute(
                    "SELECT payload_json,payload_sha256,stored_at FROM status_reports WHERE report_id = ?",
                    (report_id,),
                ).fetchone()
        except sqlite3.Error:
            raise StatusReportError("status persistence failed") from None
        if row is None:
            return None
        payload, digest, stored_raw = row
        if not isinstance(payload, str) or not isinstance(digest, str) or hashlib.sha256(payload.encode("utf-8")).hexdigest() != digest:
            raise StatusReportError("stored report integrity is invalid")
        try:
            raw = json.loads(payload)
            stored_at = _moment(datetime.fromisoformat(stored_raw), "storage timestamp")
        except (ValueError, TypeError, json.JSONDecodeError):
            raise StatusReportError("stored report is malformed") from None
        report = OperationalStatusReport.from_dict(raw)
        if stored_at < report.reported_at or report.reported_at > evaluation_time:
            raise StatusReportError("stored report time is invalid")
        if evaluation_time - report.reported_at > self._policy.maximum_age:
            raise StatusReportError("stored report is stale")
        for component_time in (report.scheduler_updated_at, report.liveness_evaluated_at):
            if component_time is not None and report.reported_at - component_time > self._policy.maximum_age:
                raise StatusReportError("stored component evidence is stale")
        return report
