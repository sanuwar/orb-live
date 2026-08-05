"""Compact, deterministic projection of bounded offline session evidence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Sequence

from src.bounded_shadow_session import BoundedShadowSessionReport


class EvidenceSummaryError(ValueError):
    """Evidence is malformed, stale, conflicting, or privacy unsafe."""


class CountBucket(str, Enum):
    ZERO = "ZERO"
    ONE = "ONE"
    TWO_TO_FIVE = "TWO_TO_FIVE"
    MANY = "MANY"


class OfflineHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    FAILED_CLOSED = "FAILED_CLOSED"


class SummaryReason(str, Enum):
    PROTECTION_NOT_GROUNDED = "PROTECTION_NOT_GROUNDED"
    ACTIVATION_APPROVALS_EMPTY = "ACTIVATION_APPROVALS_EMPTY"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    MISSED_CYCLE = "MISSED_CYCLE"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    OPERATOR_REVIEW_PENDING = "OPERATOR_REVIEW_PENDING"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    INSUFFICIENT_SESSION_HISTORY = "INSUFFICIENT_SESSION_HISTORY"
    FAILED_COMPONENT = "FAILED_COMPONENT"


class ReadinessStatus(str, Enum):
    OFFLINE_HEALTHY = "OFFLINE_HEALTHY"
    OFFLINE_DEGRADED = "OFFLINE_DEGRADED"
    SUPERVISED_BLOCKED = "SUPERVISED_BLOCKED"


_KEYS = frozenset({
    "batch_id", "session_count", "completed_session_count", "failed_session_count",
    "suspended_session_count", "ambiguous_restart_count", "operator_review_count",
    "missed_cycle_count", "unconfirmed_alert_count", "stale_evidence_count",
    "last_safe_injected_timestamp", "blocker_reasons", "offline_health_status",
    "supervised_readiness_status", "live_ready", "unattended_ready",
    "protection_grounded", "activation_approved", "permits_automated_action",
    "occurrence_ids", "evidence_digest",
})
_FORBIDDEN = ("account", "balance", "broker", "credential", "exception", "order", "price", "quantity", "secret", "symbol", "token", "username")
_MAX_AGE = timedelta(days=1)


def _bucket(value: int) -> CountBucket:
    if value == 0:
        return CountBucket.ZERO
    if value == 1:
        return CountBucket.ONE
    if value <= 5:
        return CountBucket.TWO_TO_FIVE
    return CountBucket.MANY


def _id(value: object) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 96 or not value.startswith("batch-") or not value.replace("-", "").isalnum() or any(x in value.casefold() for x in _FORBIDDEN):
        raise EvidenceSummaryError("batch identity is invalid")
    return value


@dataclass(frozen=True)
class SessionEvidence:
    occurrence_id: str
    report: BoundedShadowSessionReport

    def __post_init__(self) -> None:
        if not isinstance(self.occurrence_id, str) or not 12 <= len(self.occurrence_id) <= 96 or not self.occurrence_id.startswith("occurrence-") or not self.occurrence_id.replace("-", "").isalnum():
            raise EvidenceSummaryError("occurrence identity is invalid")
        if not isinstance(self.report, BoundedShadowSessionReport):
            raise EvidenceSummaryError("session report is invalid")


@dataclass(frozen=True)
class OfflineEvidenceSummary:
    batch_id: str
    session_count: CountBucket
    completed_session_count: CountBucket
    failed_session_count: CountBucket
    suspended_session_count: CountBucket
    ambiguous_restart_count: CountBucket
    operator_review_count: CountBucket
    missed_cycle_count: CountBucket
    unconfirmed_alert_count: CountBucket
    stale_evidence_count: CountBucket
    last_safe_injected_timestamp: datetime
    blocker_reasons: tuple[SummaryReason, ...]
    offline_health_status: OfflineHealthStatus
    supervised_readiness_status: ReadinessStatus
    live_ready: bool = False
    unattended_ready: bool = False
    protection_grounded: bool = False
    activation_approved: bool = False
    permits_automated_action: bool = False
    occurrence_ids: tuple[str, ...] = ()
    evidence_digest: str = ""

    def __post_init__(self) -> None:
        _id(self.batch_id)
        if any(not isinstance(x, CountBucket) for x in (
            self.session_count, self.completed_session_count, self.failed_session_count,
            self.suspended_session_count, self.ambiguous_restart_count,
            self.operator_review_count, self.missed_cycle_count,
            self.unconfirmed_alert_count, self.stale_evidence_count,
        )) or not isinstance(self.offline_health_status, OfflineHealthStatus) or not isinstance(self.supervised_readiness_status, ReadinessStatus):
            raise EvidenceSummaryError("summary enum values are invalid")
        if self.last_safe_injected_timestamp.tzinfo is None or self.last_safe_injected_timestamp.tzinfo.utcoffset(self.last_safe_injected_timestamp) is None:
            raise EvidenceSummaryError("summary timestamp is invalid")
        if not isinstance(self.blocker_reasons, tuple) or any(not isinstance(x, SummaryReason) for x in self.blocker_reasons) or len(set(self.blocker_reasons)) != len(self.blocker_reasons):
            raise EvidenceSummaryError("summary reasons are invalid")
        if any(x is not False for x in (self.live_ready, self.unattended_ready, self.protection_grounded, self.activation_approved, self.permits_automated_action)):
            raise EvidenceSummaryError("summary cannot authorize or ground operation")
        if not self.occurrence_ids or tuple(sorted(self.occurrence_ids)) != self.occurrence_ids or len(set(self.occurrence_ids)) != len(self.occurrence_ids):
            raise EvidenceSummaryError("summary occurrence binding is invalid")
        if len(self.evidence_digest) != 64 or any(x not in "0123456789abcdef" for x in self.evidence_digest):
            raise EvidenceSummaryError("summary evidence digest is invalid")
        expected_reasons = {SummaryReason.PROTECTION_NOT_GROUNDED, SummaryReason.ACTIVATION_APPROVALS_EMPTY}
        counts = {
            SummaryReason.ACTIVE_SUSPENSION: self.suspended_session_count,
            SummaryReason.AMBIGUOUS_RESTART: self.ambiguous_restart_count,
            SummaryReason.OPERATOR_REVIEW_PENDING: self.operator_review_count,
            SummaryReason.MISSED_CYCLE: self.missed_cycle_count,
            SummaryReason.ALERT_DELIVERY_UNCONFIRMED: self.unconfirmed_alert_count,
            SummaryReason.STALE_EVIDENCE: self.stale_evidence_count,
            SummaryReason.FAILED_COMPONENT: self.failed_session_count,
        }
        for reason, count in counts.items():
            if count is CountBucket.ZERO and reason in self.blocker_reasons:
                raise EvidenceSummaryError("summary blocker reason is unsupported")
            if count is not CountBucket.ZERO and reason not in self.blocker_reasons:
                raise EvidenceSummaryError("summary blocker reason is incomplete")
            if count is not CountBucket.ZERO:
                expected_reasons.add(reason)
        if self.session_count is CountBucket.ONE:
            expected_reasons.add(SummaryReason.INSUFFICIENT_SESSION_HISTORY)
        elif SummaryReason.INSUFFICIENT_SESSION_HISTORY in self.blocker_reasons:
            raise EvidenceSummaryError("summary history reason is unsupported")
        if set(self.blocker_reasons) != expected_reasons:
            raise EvidenceSummaryError("summary blocker projection is invalid")
        adverse = expected_reasons - {SummaryReason.PROTECTION_NOT_GROUNDED, SummaryReason.ACTIVATION_APPROVALS_EMPTY}
        expected_health = OfflineHealthStatus.FAILED_CLOSED if self.failed_session_count is not CountBucket.ZERO or self.ambiguous_restart_count is not CountBucket.ZERO else (OfflineHealthStatus.DEGRADED if adverse else OfflineHealthStatus.HEALTHY)
        if self.offline_health_status is not expected_health or self.supervised_readiness_status is not ReadinessStatus.SUPERVISED_BLOCKED:
            raise EvidenceSummaryError("summary status projection is invalid")

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "batch_id": self.batch_id,
            "session_count": self.session_count.value,
            "completed_session_count": self.completed_session_count.value,
            "failed_session_count": self.failed_session_count.value,
            "suspended_session_count": self.suspended_session_count.value,
            "ambiguous_restart_count": self.ambiguous_restart_count.value,
            "operator_review_count": self.operator_review_count.value,
            "missed_cycle_count": self.missed_cycle_count.value,
            "unconfirmed_alert_count": self.unconfirmed_alert_count.value,
            "stale_evidence_count": self.stale_evidence_count.value,
            "last_safe_injected_timestamp": self.last_safe_injected_timestamp.isoformat(),
            "blocker_reasons": [x.value for x in self.blocker_reasons],
            "offline_health_status": self.offline_health_status.value,
            "supervised_readiness_status": self.supervised_readiness_status.value,
            "live_ready": False, "unattended_ready": False,
            "protection_grounded": False, "activation_approved": False,
            "permits_automated_action": False,
            "occurrence_ids": list(self.occurrence_ids),
            "evidence_digest": self.evidence_digest,
        }
        return result

    def serialize(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"))


def _evidence_digest(batch_id: str, sessions: Sequence[SessionEvidence]) -> str:
    bound = [{"occurrence_id": item.occurrence_id, "report": item.report.to_dict()} for item in sorted(sessions, key=lambda item: item.occurrence_id)]
    encoded = json.dumps({"batch_id": batch_id, "evidence": bound}, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _project_summary(batch_id: str, sessions: tuple[SessionEvidence, ...], evaluated_at: datetime) -> OfflineEvidenceSummary:
    _id(batch_id)
    if not isinstance(sessions, tuple) or not sessions or any(not isinstance(x, SessionEvidence) for x in sessions):
        raise EvidenceSummaryError("session evidence is invalid")
    if evaluated_at.tzinfo is None or evaluated_at.tzinfo.utcoffset(evaluated_at) is None:
        raise EvidenceSummaryError("evaluation timestamp is invalid")
    occurrences: set[str] = set()
    reports: list[BoundedShadowSessionReport] = []
    occurrence_prefix = "occurrence-" + batch_id.removeprefix("batch-") + "-"
    for evidence in sessions:
        session = evidence.report
        if not evidence.occurrence_id.startswith(occurrence_prefix):
            raise EvidenceSummaryError("session occurrence is bound to another batch")
        if evidence.occurrence_id in occurrences:
            raise EvidenceSummaryError("duplicate session evidence")
        occurrences.add(evidence.occurrence_id)
        reports.append(session)
        if session.evaluated_at > evaluated_at or evaluated_at - session.evaluated_at > _MAX_AGE:
            raise EvidenceSummaryError("session evidence is stale or future dated")
    observations = [item for session in reports for item in session.observations]
    failed = sum(not session.all_passed for session in reports)
    suspended = sum(item.observed_state in {"STOPPED_ON_SUSPENSION", "SUSPENSION_REQUIRED"} for item in observations)
    ambiguous = sum(item.observed_state == "AMBIGUOUS_RESTART" for item in observations)
    reviews = sum(item.observed_state == "STOPPED_ON_REVIEW" for item in observations)
    missed = sum(item.observed_state == "SUSPENSION_REQUIRED" for item in observations)
    alerts = sum(item.observed_state == "DELIVERY_UNCONFIRMED" for item in observations)
    stale = sum(item.observed_state == "STALE" for item in observations)
    reasons = [SummaryReason.PROTECTION_NOT_GROUNDED, SummaryReason.ACTIVATION_APPROVALS_EMPTY]
    if suspended: reasons.append(SummaryReason.ACTIVE_SUSPENSION)
    if ambiguous: reasons.append(SummaryReason.AMBIGUOUS_RESTART)
    if missed: reasons.append(SummaryReason.MISSED_CYCLE)
    if alerts: reasons.append(SummaryReason.ALERT_DELIVERY_UNCONFIRMED)
    if reviews: reasons.append(SummaryReason.OPERATOR_REVIEW_PENDING)
    if stale: reasons.append(SummaryReason.STALE_EVIDENCE)
    if failed: reasons.append(SummaryReason.FAILED_COMPONENT)
    if len(reports) < 2: reasons.append(SummaryReason.INSUFFICIENT_SESSION_HISTORY)
    health = OfflineHealthStatus.FAILED_CLOSED if failed or ambiguous else (OfflineHealthStatus.HEALTHY if not any(r not in {SummaryReason.PROTECTION_NOT_GROUNDED, SummaryReason.ACTIVATION_APPROVALS_EMPTY} for r in reasons) else OfflineHealthStatus.DEGRADED)
    return OfflineEvidenceSummary(
        batch_id, _bucket(len(reports)), _bucket(len(reports) - failed), _bucket(failed),
        _bucket(suspended), _bucket(ambiguous), _bucket(reviews), _bucket(missed), _bucket(alerts), _bucket(stale),
        max(session.evaluated_at for session in reports), tuple(reasons), health,
        ReadinessStatus.SUPERVISED_BLOCKED if reasons else ReadinessStatus.OFFLINE_HEALTHY,
        occurrence_ids=tuple(sorted(occurrences)), evidence_digest=_evidence_digest(batch_id, sessions),
    )


def summarize_sessions(batch_id: str, sessions: Sequence[SessionEvidence], *, evaluated_at: datetime) -> OfflineEvidenceSummary:
    if not isinstance(sessions, tuple):
        raise EvidenceSummaryError("session evidence is invalid")
    return _project_summary(batch_id, sessions, evaluated_at)


def load_summary(payload: str, sessions: Sequence[SessionEvidence], *, evaluated_at: datetime) -> OfflineEvidenceSummary:
    try:
        raw: Any = json.loads(payload)
    except Exception:
        raise EvidenceSummaryError("summary payload is invalid") from None
    if not isinstance(raw, dict) or set(raw) != _KEYS:
        raise EvidenceSummaryError("summary keys are invalid")
    try:
        result = OfflineEvidenceSummary(
            raw["batch_id"], CountBucket(raw["session_count"]), CountBucket(raw["completed_session_count"]), CountBucket(raw["failed_session_count"]), CountBucket(raw["suspended_session_count"]), CountBucket(raw["ambiguous_restart_count"]), CountBucket(raw["operator_review_count"]), CountBucket(raw["missed_cycle_count"]), CountBucket(raw["unconfirmed_alert_count"]), CountBucket(raw["stale_evidence_count"]), datetime.fromisoformat(raw["last_safe_injected_timestamp"]), tuple(SummaryReason(x) for x in raw["blocker_reasons"]), OfflineHealthStatus(raw["offline_health_status"]), ReadinessStatus(raw["supervised_readiness_status"]), raw["live_ready"], raw["unattended_ready"], raw["protection_grounded"], raw["activation_approved"], raw["permits_automated_action"],
            tuple(raw["occurrence_ids"]), raw["evidence_digest"],
        )
        if evaluated_at.tzinfo is None or evaluated_at - result.last_safe_injected_timestamp > _MAX_AGE or result.last_safe_injected_timestamp > evaluated_at:
            raise EvidenceSummaryError("summary evidence is stale or future dated")
        if not isinstance(sessions, tuple):
            raise EvidenceSummaryError("summary evidence is invalid")
        projected = _project_summary(result.batch_id, sessions, evaluated_at)
        if result != projected:
            raise EvidenceSummaryError("summary does not match bound evidence")
        return projected
    except EvidenceSummaryError:
        raise
    except Exception:
        raise EvidenceSummaryError("summary values are invalid") from None


__all__ = ["CountBucket", "EvidenceSummaryError", "OfflineEvidenceSummary", "OfflineHealthStatus", "ReadinessStatus", "SessionEvidence", "SummaryReason", "load_summary", "summarize_sessions"]
