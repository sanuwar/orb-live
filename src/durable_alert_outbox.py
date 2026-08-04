"""Durable, privacy-safe intent outbox for offline operational alerts.

Nothing in this module delivers a message.  Callers explicitly create, ready,
claim, and submit fixed delivery evidence.  The outbox owns only its SQLite
journal and emits no network traffic or authorization.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.durable_runner_liveness import LivenessDecision, LivenessState
from src.durable_suspension_controller import ControllerState, SuspensionDecision
from src.operational_status_report import OperationalStatusReport, OverallStatus


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1


class OutboxError(RuntimeError):
    """Fixed privacy-safe outbox refusal."""


class AlertState(str, Enum):
    CREATED = "CREATED"
    READY = "READY"
    CLAIMED = "CLAIMED"
    DELIVERED_CONFIRMED = "DELIVERED_CONFIRMED"
    DELIVERY_UNCONFIRMED = "DELIVERY_UNCONFIRMED"
    FAILED_CLOSED = "FAILED_CLOSED"
    EXPIRED = "EXPIRED"
    CANCELLED_BY_SAFETY = "CANCELLED_BY_SAFETY"


class AlertCategory(str, Enum):
    SUSPENSION = "SUSPENSION"
    MISSED_CYCLE = "MISSED_CYCLE"
    RUNNER_AMBIGUITY = "RUNNER_AMBIGUITY"
    FAILED_CLOSED = "FAILED_CLOSED"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    HEARTBEAT_STALE = "HEARTBEAT_STALE"
    STATUS_DEGRADED = "STATUS_DEGRADED"
    INTERNAL_INVARIANT_FAILURE = "INTERNAL_INVARIANT_FAILURE"


class AlertSeverity(str, Enum):
    NOTICE = "NOTICE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AlertReason(str, Enum):
    STATUS_PROJECTION = "STATUS_PROJECTION"
    LIVENESS_PROJECTION = "LIVENESS_PROJECTION"
    SUSPENSION_PROJECTION = "SUSPENSION_PROJECTION"
    DELIVERY_EVIDENCE_MISSING = "DELIVERY_EVIDENCE_MISSING"
    DELIVERY_EVIDENCE_INVALID = "DELIVERY_EVIDENCE_INVALID"
    DELIVERY_EVIDENCE_STALE = "DELIVERY_EVIDENCE_STALE"
    CONFLICTING_CONFIRMATION = "CONFLICTING_CONFIRMATION"
    CLAIM_STALE = "CLAIM_STALE"
    INTERNAL_INVARIANT = "INTERNAL_INVARIANT"


class AttemptBucket(str, Enum):
    ZERO = "ZERO"
    ONE = "ONE"
    MANY = "MANY"


class AcknowledgmentStatus(str, Enum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    UNCONFIRMED = "UNCONFIRMED"


class DeliveryProvenance(str, Enum):
    VERIFIED_ATTESTATION = "VERIFIED_ATTESTATION"
    UNVERIFIED = "UNVERIFIED"
    SELF_DECLARED = "SELF_DECLARED"


@runtime_checkable
class DeliveryAttestationVerifier(Protocol):
    """Trusted boundary that verifies delivery evidence outside this outbox."""

    verifier_id: str

    def verify(self, evidence: "DeliveryEvidence") -> bool: ...


TERMINAL_STATES = frozenset({
    AlertState.DELIVERED_CONFIRMED,
    AlertState.FAILED_CLOSED,
    AlertState.EXPIRED,
    AlertState.CANCELLED_BY_SAFETY,
})


@dataclass(frozen=True)
class OutboxPolicy:
    claim_timeout: timedelta
    maximum_evidence_age: timedelta
    maximum_attempts: int

    def __post_init__(self) -> None:
        if not isinstance(self.claim_timeout, timedelta) or self.claim_timeout <= timedelta(0):
            raise OutboxError("outbox policy is invalid")
        if not isinstance(self.maximum_evidence_age, timedelta) or self.maximum_evidence_age <= timedelta(0):
            raise OutboxError("outbox policy is invalid")
        if not isinstance(self.maximum_attempts, int) or isinstance(self.maximum_attempts, bool) or self.maximum_attempts < 1:
            raise OutboxError("outbox policy is invalid")

    @property
    def signature(self) -> str:
        return "|".join((
            str(self.claim_timeout // timedelta(microseconds=1)),
            str(self.maximum_evidence_age // timedelta(microseconds=1)),
            str(self.maximum_attempts),
        ))


@dataclass(frozen=True)
class AlertIntent:
    alert_id: str
    category: AlertCategory
    severity: AlertSeverity
    reason: AlertReason
    created_at: datetime
    correlation_id: str
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.alert_id, "alert-", "alert identity")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        if not isinstance(self.category, AlertCategory) or not isinstance(self.severity, AlertSeverity) or not isinstance(self.reason, AlertReason):
            raise OutboxError("alert intent is invalid")
        _moment(self.created_at, "creation timestamp")
        if self.permits_automated_action is not False:
            raise OutboxError("alert intent never authorizes action")


@dataclass(frozen=True)
class DeliveryEvidence:
    evidence_id: str
    alert_id: str
    claim_id: str
    observed_at: datetime
    delivered: bool
    provenance: DeliveryProvenance
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.evidence_id, "delivery-evidence-", "delivery evidence identity")
        _identifier(self.alert_id, "alert-", "alert identity")
        _identifier(self.claim_id, "claim-", "claim identity")
        _moment(self.observed_at, "delivery evidence timestamp")
        if not isinstance(self.delivered, bool) or not isinstance(self.provenance, DeliveryProvenance):
            raise OutboxError("delivery evidence is invalid")
        if self.permits_automated_action is not False:
            raise OutboxError("delivery evidence never authorizes action")


@dataclass(frozen=True)
class AlertRecord:
    alert_id: str
    category: AlertCategory
    severity: AlertSeverity
    reason: AlertReason
    state: AlertState
    created_at: datetime
    updated_at: datetime
    correlation_id: str
    claim_id: str | None
    claimed_at: datetime | None
    attempt_count: int
    attempt_bucket: AttemptBucket
    acknowledgment_status: AcknowledgmentStatus
    transition_sequence: int
    last_transition_id: str
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.alert_id, "alert-", "alert identity")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        if self.claim_id is not None:
            _identifier(self.claim_id, "claim-", "claim identity")
        _moment(self.created_at, "creation timestamp")
        _moment(self.updated_at, "update timestamp")
        if self.claimed_at is not None:
            _moment(self.claimed_at, "claim timestamp")
        if not all((
            isinstance(self.category, AlertCategory), isinstance(self.severity, AlertSeverity),
            isinstance(self.reason, AlertReason), isinstance(self.state, AlertState),
            isinstance(self.attempt_bucket, AttemptBucket),
            isinstance(self.acknowledgment_status, AcknowledgmentStatus),
        )):
            raise OutboxError("alert record is invalid")
        if self.updated_at < self.created_at:
            raise OutboxError("alert timestamps conflict")
        if (self.claim_id is None) != (self.claimed_at is None):
            raise OutboxError("claim fields conflict")
        if not isinstance(self.attempt_count, int) or isinstance(self.attempt_count, bool) or self.attempt_count < 0:
            raise OutboxError("attempt count is invalid")
        if self.attempt_bucket is not _bucket(self.attempt_count):
            raise OutboxError("attempt bucket conflicts")
        if not isinstance(self.transition_sequence, int) or isinstance(self.transition_sequence, bool) or self.transition_sequence < 1:
            raise OutboxError("transition sequence is invalid")
        _identifier(self.last_transition_id, "alert-transition-", "transition identity")
        if self.permits_automated_action is not False:
            raise OutboxError("alert record never authorizes action")


@dataclass(frozen=True)
class AlertTransitionReceipt:
    transition_id: str
    alert_id: str
    sequence: int
    from_state: AlertState
    to_state: AlertState
    occurred_at: datetime
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.transition_id, "alert-transition-", "transition identity")
        _identifier(self.alert_id, "alert-", "alert identity")
        _moment(self.occurred_at, "transition timestamp")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise OutboxError("transition sequence is invalid")
        if not isinstance(self.from_state, AlertState) or not isinstance(self.to_state, AlertState):
            raise OutboxError("transition state is invalid")
        if self.permits_automated_action is not False:
            raise OutboxError("transition never authorizes action")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS alerts (
 alert_id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL,
 reason TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, correlation_id TEXT NOT NULL, claim_id TEXT UNIQUE,
 claimed_at TEXT, attempt_count INTEGER NOT NULL CHECK(attempt_count >= 0),
 attempt_bucket TEXT NOT NULL, acknowledgment_status TEXT NOT NULL,
 transition_sequence INTEGER NOT NULL CHECK(transition_sequence >= 1),
 last_transition_id TEXT NOT NULL UNIQUE,
 permits_automated_action INTEGER NOT NULL DEFAULT 0 CHECK(permits_automated_action = 0)
);
CREATE TABLE IF NOT EXISTS alert_origins (
 alert_id TEXT PRIMARY KEY, category TEXT NOT NULL, severity TEXT NOT NULL,
 initial_reason TEXT NOT NULL, created_at TEXT NOT NULL, correlation_id TEXT NOT NULL,
 FOREIGN KEY(alert_id) REFERENCES alerts(alert_id)
);
CREATE TABLE IF NOT EXISTS alert_transitions (
 transition_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 from_state TEXT NOT NULL, to_state TEXT NOT NULL, occurred_at TEXT NOT NULL,
 resulting_reason TEXT NOT NULL, attempt_count INTEGER NOT NULL,
 attempt_bucket TEXT NOT NULL, acknowledgment_status TEXT NOT NULL,
 claim_id TEXT, evidence_id TEXT, UNIQUE(alert_id, sequence),
 FOREIGN KEY(alert_id) REFERENCES alerts(alert_id)
);
CREATE TABLE IF NOT EXISTS delivery_evidence (
 evidence_id TEXT PRIMARY KEY, alert_id TEXT NOT NULL, claim_id TEXT NOT NULL,
 observed_at TEXT NOT NULL, delivered INTEGER NOT NULL CHECK(delivered IN (0,1)),
 provenance TEXT NOT NULL, attestation_verified INTEGER NOT NULL CHECK(attestation_verified IN (0,1)),
 FOREIGN KEY(alert_id) REFERENCES alerts(alert_id)
);
CREATE TABLE IF NOT EXISTS spent_identities (
 identity TEXT PRIMARY KEY, alert_id TEXT NOT NULL, identity_kind TEXT NOT NULL,
 spent_at TEXT NOT NULL, FOREIGN KEY(alert_id) REFERENCES alerts(alert_id)
);
"""


class DurableAlertOutbox:
    def __init__(self, database_path: Path, policy: OutboxPolicy, verifier: DeliveryAttestationVerifier) -> None:
        if not isinstance(policy, OutboxPolicy):
            raise OutboxError("typed outbox policy is required")
        if not isinstance(verifier, DeliveryAttestationVerifier):
            raise OutboxError("typed delivery verifier is required")
        _identifier(verifier.verifier_id, "verifier-", "verifier identity")
        self.policy = policy
        self._verifier = verifier
        try:
            self.database_path = _database_path(database_path)
            self.database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(self.database_path, isolation_level=None)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(_SCHEMA)
            self._bind_meta()
        except Exception:
            if hasattr(self, "_connection"):
                try:
                    self._connection.close()
                except Exception:
                    pass
            raise OutboxError("outbox initialization failed") from None

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:
            raise OutboxError("outbox close failed") from None

    def __enter__(self) -> "DurableAlertOutbox":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _bind_meta(self) -> None:
        expected = {
            "schema_version": str(SCHEMA_VERSION),
            "policy_signature": self.policy.signature,
            "verifier_identity": self._verifier.verifier_id,
        }
        for key, value in expected.items():
            row = self._connection.execute("SELECT value FROM outbox_meta WHERE key=?", (key,)).fetchone()
            if row is None:
                self._connection.execute("INSERT INTO outbox_meta(key,value) VALUES(?,?)", (key, value))
            elif row != (value,):
                raise OutboxError("durable outbox metadata conflicts")

    def create(self, intent: AlertIntent, *, transition_id: str) -> AlertRecord:
        if not isinstance(intent, AlertIntent):
            raise OutboxError("typed alert intent is required")
        _identifier(transition_id, "alert-transition-", "transition identity")
        self._begin()
        try:
            if self._connection.execute("SELECT 1 FROM alerts WHERE alert_id=?", (intent.alert_id,)).fetchone():
                raise OutboxError("alert identity is already bound")
            self._connection.execute(
                "INSERT INTO alerts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
                (intent.alert_id, intent.category.value, intent.severity.value, intent.reason.value,
                 AlertState.CREATED.value, intent.created_at.isoformat(), intent.created_at.isoformat(),
                 intent.correlation_id, None, None, 0, AttemptBucket.ZERO.value,
                 AcknowledgmentStatus.PENDING.value, 1, transition_id),
            )
            self._connection.execute(
                "INSERT INTO alert_origins VALUES(?,?,?,?,?,?)",
                (intent.alert_id, intent.category.value, intent.severity.value,
                 intent.reason.value, intent.created_at.isoformat(), intent.correlation_id),
            )
            self._spend(transition_id, intent.alert_id, "TRANSITION", intent.created_at)
            self._insert_transition(
                transition_id, intent.alert_id, 1, AlertState.CREATED,
                AlertState.CREATED, intent.created_at, intent.reason, 0,
                AcknowledgmentStatus.PENDING,
            )
            self._commit()
        except Exception as exc:
            self._rollback()
            if isinstance(exc, OutboxError):
                raise
            raise OutboxError("alert creation failed") from None
        return self.load(intent.alert_id)

    def ready(self, alert_id: str, *, transition_id: str, now: datetime) -> AlertRecord:
        return self._simple_transition(
            alert_id, transition_id, now,
            {AlertState.CREATED, AlertState.DELIVERY_UNCONFIRMED}, AlertState.READY,
        )

    def claim(self, alert_id: str, *, claim_id: str, transition_id: str, now: datetime) -> AlertRecord:
        _identifier(claim_id, "claim-", "claim identity")
        _moment(now, "claim timestamp")
        self._begin()
        try:
            record = self._load_audited(alert_id)
            if record.state is AlertState.CLAIMED:
                if now < record.claimed_at:
                    raise OutboxError("claim timestamp is future-dated")
                if now - record.claimed_at > self.policy.claim_timeout:
                    result = self._transition(record, transition_id, now, AlertState.FAILED_CLOSED, reason=AlertReason.CLAIM_STALE)
                    self._commit()
                    return result
            if record.state is not AlertState.READY:
                raise OutboxError("alert is not claimable")
            if record.attempt_count >= self.policy.maximum_attempts:
                result = self._transition(record, transition_id, now, AlertState.FAILED_CLOSED, reason=AlertReason.INTERNAL_INVARIANT)
                self._commit()
                return result
            self._spend(claim_id, alert_id, "CLAIM", now)
            result = self._transition(record, transition_id, now, AlertState.CLAIMED, claim_id=claim_id, attempt_count=record.attempt_count + 1)
            self._commit()
            return result
        except Exception as exc:
            self._rollback()
            if isinstance(exc, OutboxError):
                raise
            raise OutboxError("alert claim failed") from None

    def submit_delivery(self, evidence: DeliveryEvidence, *, transition_id: str, now: datetime) -> AlertRecord:
        if not isinstance(evidence, DeliveryEvidence):
            raise OutboxError("typed delivery evidence is required")
        _moment(now, "delivery evaluation timestamp")
        self._begin()
        try:
            record = self._load_audited(evidence.alert_id)
            prior = self._connection.execute(
                "SELECT alert_id,claim_id,observed_at,delivered,provenance FROM delivery_evidence WHERE evidence_id=?",
                (evidence.evidence_id,),
            ).fetchone()
            submitted = (
                evidence.alert_id, evidence.claim_id, evidence.observed_at.isoformat(),
                int(evidence.delivered), evidence.provenance.value,
            )
            if prior is not None:
                if prior == submitted:
                    raise OutboxError("delivery evidence identity is duplicated")
                if record.state in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED}:
                    result = self._transition(
                        record, transition_id, now, AlertState.FAILED_CLOSED,
                        reason=AlertReason.CONFLICTING_CONFIRMATION,
                        acknowledgment=AcknowledgmentStatus.UNCONFIRMED,
                    )
                    self._commit()
                    return result
                raise OutboxError("delivery evidence identity conflicts")
            if record.state in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED}:
                expected = record.state is AlertState.DELIVERED_CONFIRMED
                if evidence.delivered is expected:
                    raise OutboxError("terminal delivery evidence is duplicated")
                self._spend(evidence.evidence_id, evidence.alert_id, "EVIDENCE", now)
                self._connection.execute(
                    "INSERT INTO delivery_evidence VALUES(?,?,?,?,?,?,?)",
                    (evidence.evidence_id, *submitted, 0),
                )
                result = self._transition(
                    record, transition_id, now, AlertState.FAILED_CLOSED,
                    reason=AlertReason.CONFLICTING_CONFIRMATION,
                    acknowledgment=AcknowledgmentStatus.UNCONFIRMED,
                    evidence_id=evidence.evidence_id,
                )
                self._commit()
                return result
            if record.state in {AlertState.EXPIRED, AlertState.CANCELLED_BY_SAFETY, AlertState.FAILED_CLOSED}:
                raise OutboxError("terminal alert cannot accept delivery evidence")
            if record.state is not AlertState.CLAIMED or record.claim_id != evidence.claim_id:
                raise OutboxError("delivery evidence is bound to another claim")
            temporal_invalid = evidence.observed_at > now or evidence.observed_at < record.claimed_at
            attestation_verified = (
                not temporal_invalid
                and evidence.provenance is DeliveryProvenance.VERIFIED_ATTESTATION
                and self._verify(evidence)
            )
            self._spend(evidence.evidence_id, evidence.alert_id, "EVIDENCE", now)
            self._connection.execute(
                "INSERT INTO delivery_evidence VALUES(?,?,?,?,?,?,?)",
                (evidence.evidence_id, *submitted, int(attestation_verified)),
            )
            if temporal_invalid:
                target, reason = AlertState.FAILED_CLOSED, AlertReason.DELIVERY_EVIDENCE_INVALID
            elif now - evidence.observed_at > self.policy.maximum_evidence_age:
                target, reason = AlertState.FAILED_CLOSED, AlertReason.DELIVERY_EVIDENCE_STALE
            elif not attestation_verified:
                target, reason = AlertState.FAILED_CLOSED, AlertReason.DELIVERY_EVIDENCE_INVALID
            elif evidence.delivered:
                target, reason = AlertState.DELIVERED_CONFIRMED, record.reason
            else:
                target, reason = AlertState.DELIVERY_UNCONFIRMED, AlertReason.DELIVERY_EVIDENCE_MISSING
            acknowledgment = AcknowledgmentStatus.CONFIRMED if target is AlertState.DELIVERED_CONFIRMED else AcknowledgmentStatus.UNCONFIRMED
            result = self._transition(record, transition_id, now, target, reason=reason, acknowledgment=acknowledgment, evidence_id=evidence.evidence_id)
            self._commit()
            return result
        except Exception as exc:
            self._rollback()
            if isinstance(exc, OutboxError):
                raise
            raise OutboxError("delivery evidence persistence failed") from None

    def expire(self, alert_id: str, *, transition_id: str, now: datetime) -> AlertRecord:
        return self._simple_transition(
            alert_id, transition_id, now,
            {AlertState.CREATED, AlertState.READY, AlertState.CLAIMED}, AlertState.EXPIRED,
            prohibit_suspension=True,
        )

    def cancel_by_safety(self, alert_id: str, *, transition_id: str, now: datetime) -> AlertRecord:
        return self._simple_transition(
            alert_id, transition_id, now,
            {AlertState.CREATED, AlertState.READY, AlertState.CLAIMED},
            AlertState.CANCELLED_BY_SAFETY, prohibit_suspension=True,
        )

    def load(self, alert_id: str) -> AlertRecord:
        _identifier(alert_id, "alert-", "alert identity")
        try:
            return self._load_audited(alert_id)
        except OutboxError:
            raise
        except Exception:
            raise OutboxError("durable alert state is invalid") from None

    def transition_history(self, alert_id: str) -> tuple[AlertTransitionReceipt, ...]:
        self.load(alert_id)
        try:
            rows = self._connection.execute(
                "SELECT transition_id,alert_id,sequence,from_state,to_state,occurred_at FROM alert_transitions WHERE alert_id=? ORDER BY sequence",
                (alert_id,),
            ).fetchall()
            return tuple(AlertTransitionReceipt(row[0], row[1], row[2], AlertState(row[3]), AlertState(row[4]), _parse_moment(row[5])) for row in rows)
        except OutboxError:
            raise
        except Exception:
            raise OutboxError("durable transition history is invalid") from None

    def _simple_transition(self, alert_id: str, transition_id: str, now: datetime, allowed: set[AlertState], target: AlertState, *, prohibit_suspension: bool = False) -> AlertRecord:
        _moment(now, "transition timestamp")
        self._begin()
        try:
            record = self._load_audited(alert_id)
            if prohibit_suspension and record.category is AlertCategory.SUSPENSION:
                raise OutboxError("suspension alert cannot be discarded")
            if record.state not in allowed:
                raise OutboxError("alert transition is not allowed")
            result = self._transition(
                record, transition_id, now, target,
                acknowledgment=(
                    AcknowledgmentStatus.PENDING
                    if target is AlertState.READY else None
                ),
                clear_claim=(
                    target is AlertState.READY
                    and record.state is AlertState.DELIVERY_UNCONFIRMED
                ),
            )
            self._commit()
            return result
        except Exception as exc:
            self._rollback()
            if isinstance(exc, OutboxError):
                raise
            raise OutboxError("alert transition failed") from None

    def _transition(self, record: AlertRecord, transition_id: str, now: datetime, target: AlertState, *, reason: AlertReason | None = None, claim_id: str | None = None, attempt_count: int | None = None, acknowledgment: AcknowledgmentStatus | None = None, evidence_id: str | None = None, clear_claim: bool = False) -> AlertRecord:
        _identifier(transition_id, "alert-transition-", "transition identity")
        _moment(now, "transition timestamp")
        if now < record.updated_at:
            raise OutboxError("transition timestamp regressed")
        self._spend(transition_id, record.alert_id, "TRANSITION", now)
        sequence = record.transition_sequence + 1
        next_claim = None if clear_claim else claim_id if claim_id is not None else record.claim_id
        next_claimed_at = None if clear_claim else now if claim_id is not None else record.claimed_at
        attempts = record.attempt_count if attempt_count is None else attempt_count
        next_reason = record.reason if reason is None else reason
        next_ack = record.acknowledgment_status if acknowledgment is None else acknowledgment
        self._connection.execute(
            "UPDATE alerts SET reason=?,state=?,updated_at=?,claim_id=?,claimed_at=?,attempt_count=?,attempt_bucket=?,acknowledgment_status=?,transition_sequence=?,last_transition_id=? WHERE alert_id=?",
            (next_reason.value, target.value, now.isoformat(), next_claim,
             next_claimed_at.isoformat() if next_claimed_at else None, attempts,
             _bucket(attempts).value, next_ack.value, sequence, transition_id, record.alert_id),
        )
        self._insert_transition(
            transition_id, record.alert_id, sequence, record.state, target, now,
            next_reason, attempts, next_ack, next_claim, evidence_id,
        )
        return self._load_audited(record.alert_id)

    def _load_audited(self, alert_id: str) -> AlertRecord:
        row = self._connection.execute("SELECT * FROM alerts WHERE alert_id=?", (alert_id,)).fetchone()
        if row is None:
            raise OutboxError("alert identity is unknown")
        record = AlertRecord(
            row[0], AlertCategory(row[1]), AlertSeverity(row[2]), AlertReason(row[3]), AlertState(row[4]),
            _parse_moment(row[5]), _parse_moment(row[6]), row[7], row[8],
            _parse_moment(row[9]) if row[9] else None, row[10], AttemptBucket(row[11]),
            AcknowledgmentStatus(row[12]), row[13], row[14], bool(row[15]),
        )
        origin = self._connection.execute(
            "SELECT category,severity,initial_reason,created_at,correlation_id FROM alert_origins WHERE alert_id=?",
            (alert_id,),
        ).fetchone()
        if origin is None or (
            origin[0], origin[1], origin[3], origin[4]
        ) != (
            record.category.value, record.severity.value,
            record.created_at.isoformat(), record.correlation_id,
        ):
            raise OutboxError("alert transition history is invalid")
        rows = self._connection.execute(
            "SELECT transition_id,sequence,from_state,to_state,occurred_at,resulting_reason,attempt_count,attempt_bucket,acknowledgment_status,claim_id,evidence_id FROM alert_transitions WHERE alert_id=? ORDER BY sequence",
            (alert_id,),
        ).fetchall()
        if len(rows) != record.transition_sequence:
            raise OutboxError("alert transition history is invalid")
        if not rows or rows[0][5] != origin[2]:
            raise OutboxError("alert transition history is invalid")
        previous_state = AlertState.CREATED
        previous_time = record.created_at
        previous_reason = AlertReason(origin[2])
        previous_attempts = 0
        previous_ack = AcknowledgmentStatus.PENDING
        previous_claim: str | None = None
        previous_claimed_at: datetime | None = None
        for index, item in enumerate(rows, start=1):
            if item[1] != index or AlertState(item[2]) is not previous_state:
                raise OutboxError("alert transition history is invalid")
            target = AlertState(item[3])
            occurred = _parse_moment(item[4])
            reason = AlertReason(item[5])
            attempts = item[6]
            bucket = AttemptBucket(item[7])
            acknowledgment = AcknowledgmentStatus(item[8])
            claim_identity = item[9]
            evidence_identity = item[10]
            if occurred < previous_time or bucket is not _bucket(attempts):
                raise OutboxError("alert transition history is invalid")
            if index == 1:
                valid = (
                    target is AlertState.CREATED and occurred == record.created_at
                    and reason is previous_reason and attempts == 0
                    and acknowledgment is AcknowledgmentStatus.PENDING
                    and claim_identity is None and evidence_identity is None
                )
            elif target is AlertState.READY:
                valid = (
                    previous_state in {AlertState.CREATED, AlertState.DELIVERY_UNCONFIRMED}
                    and reason is previous_reason and attempts == previous_attempts
                    and acknowledgment is AcknowledgmentStatus.PENDING
                    and claim_identity is None and evidence_identity is None
                )
                previous_claimed_at = None
            elif target is AlertState.CLAIMED:
                valid = (
                    previous_state is AlertState.READY and reason is previous_reason
                    and attempts == previous_attempts + 1
                    and acknowledgment is AcknowledgmentStatus.PENDING
                    and claim_identity is not None and evidence_identity is None
                )
                previous_claimed_at = occurred
            elif target in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED}:
                valid = (
                    previous_state is AlertState.CLAIMED and attempts == previous_attempts
                    and claim_identity == previous_claim and evidence_identity is not None
                    and acknowledgment is (
                        AcknowledgmentStatus.CONFIRMED
                        if target is AlertState.DELIVERED_CONFIRMED
                        else AcknowledgmentStatus.UNCONFIRMED
                    )
                    and (
                        reason is previous_reason
                        if target is AlertState.DELIVERED_CONFIRMED
                        else reason is AlertReason.DELIVERY_EVIDENCE_MISSING
                    )
                )
            elif target is AlertState.FAILED_CLOSED:
                valid = (
                    previous_state in {AlertState.READY, AlertState.CLAIMED, AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED}
                    and attempts == previous_attempts and claim_identity == previous_claim
                    and (
                        (reason is AlertReason.CLAIM_STALE and previous_state is AlertState.CLAIMED
                         and evidence_identity is None and acknowledgment is previous_ack)
                        or (reason is AlertReason.INTERNAL_INVARIANT and previous_state is AlertState.READY
                            and evidence_identity is None and acknowledgment is previous_ack)
                        or (reason in {AlertReason.DELIVERY_EVIDENCE_INVALID, AlertReason.DELIVERY_EVIDENCE_STALE}
                            and previous_state is AlertState.CLAIMED and evidence_identity is not None
                            and acknowledgment is AcknowledgmentStatus.UNCONFIRMED)
                        or (reason is AlertReason.CONFLICTING_CONFIRMATION
                            and previous_state in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED}
                            and acknowledgment is AcknowledgmentStatus.UNCONFIRMED)
                    )
                )
            elif target in {AlertState.EXPIRED, AlertState.CANCELLED_BY_SAFETY}:
                valid = (
                    record.category is not AlertCategory.SUSPENSION
                    and previous_state in {AlertState.CREATED, AlertState.READY, AlertState.CLAIMED}
                    and reason is previous_reason and attempts == previous_attempts
                    and acknowledgment is previous_ack and claim_identity == previous_claim
                    and evidence_identity is None
                )
            else:
                valid = False
            if not valid:
                raise OutboxError("alert transition history is invalid")
            spent = self._connection.execute(
                "SELECT alert_id,identity_kind FROM spent_identities WHERE identity=?",
                (item[0],),
            ).fetchone()
            if spent != (alert_id, "TRANSITION"):
                raise OutboxError("alert transition history is invalid")
            if claim_identity is not None:
                _identifier(claim_identity, "claim-", "claim identity")
                if self._connection.execute(
                    "SELECT alert_id,identity_kind FROM spent_identities WHERE identity=?",
                    (claim_identity,),
                ).fetchone() != (alert_id, "CLAIM"):
                    raise OutboxError("alert claim history is invalid")
            if evidence_identity is not None:
                evidence_row = self._connection.execute(
                    "SELECT alert_id,claim_id,observed_at,delivered,provenance,attestation_verified FROM delivery_evidence WHERE evidence_id=?",
                    (evidence_identity,),
                ).fetchone()
                if evidence_row is None or evidence_row[0] != alert_id or evidence_row[1] != claim_identity:
                    raise OutboxError("alert evidence history is invalid")
                observed = _parse_moment(evidence_row[2])
                provenance = DeliveryProvenance(evidence_row[4])
                verified = evidence_row[5] == 1
                if previous_claimed_at is None:
                    raise OutboxError("alert evidence history is invalid")
                if target in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED} and (
                    observed < previous_claimed_at or observed > occurred
                ):
                    raise OutboxError("alert evidence history is invalid")
                if target is AlertState.DELIVERED_CONFIRMED and (
                    evidence_row[3] != 1 or provenance is not DeliveryProvenance.VERIFIED_ATTESTATION
                    or not verified
                ):
                    raise OutboxError("alert evidence history is invalid")
                if target is AlertState.DELIVERY_UNCONFIRMED and evidence_row[3] != 0:
                    raise OutboxError("alert evidence history is invalid")
                if target is AlertState.DELIVERY_UNCONFIRMED and (
                    provenance is not DeliveryProvenance.VERIFIED_ATTESTATION or not verified
                ):
                    raise OutboxError("alert evidence history is invalid")
                if target is AlertState.FAILED_CLOSED:
                    if reason is AlertReason.CONFLICTING_CONFIRMATION:
                        expected_delivery = previous_state is AlertState.DELIVERY_UNCONFIRMED
                        if previous_state not in {AlertState.DELIVERED_CONFIRMED, AlertState.DELIVERY_UNCONFIRMED} or bool(evidence_row[3]) is not expected_delivery:
                            raise OutboxError("alert evidence history is invalid")
                    elif reason is AlertReason.DELIVERY_EVIDENCE_STALE:
                        if observed < previous_claimed_at or observed > occurred or occurred - observed <= self.policy.maximum_evidence_age:
                            raise OutboxError("alert evidence history is invalid")
                    elif reason is AlertReason.DELIVERY_EVIDENCE_INVALID:
                        if not (
                            observed < previous_claimed_at or observed > occurred
                            or provenance is not DeliveryProvenance.VERIFIED_ATTESTATION or not verified
                        ):
                            raise OutboxError("alert evidence history is invalid")
            previous_state, previous_time = target, occurred
            previous_reason, previous_attempts = reason, attempts
            previous_ack, previous_claim = acknowledgment, claim_identity
        last = rows[-1]
        if (
            last[0], AlertState(last[3]), _parse_moment(last[4]), AlertReason(last[5]),
            last[6], AttemptBucket(last[7]), AcknowledgmentStatus(last[8]), last[9]
        ) != (
            record.last_transition_id, record.state, record.updated_at, record.reason,
            record.attempt_count, record.attempt_bucket, record.acknowledgment_status,
            record.claim_id,
        ):
            raise OutboxError("alert transition history is invalid")
        if record.claimed_at != previous_claimed_at:
            raise OutboxError("alert claim history is invalid")
        evidence_rows = self._connection.execute(
            "SELECT evidence_id FROM delivery_evidence WHERE alert_id=?", (alert_id,)
        ).fetchall()
        transition_evidence = {item[10] for item in rows if item[10] is not None}
        if {item[0] for item in evidence_rows} != transition_evidence:
            raise OutboxError("alert evidence history is invalid")
        for (evidence_identity,) in evidence_rows:
            spent_evidence = self._connection.execute(
                "SELECT alert_id,identity_kind FROM spent_identities WHERE identity=?",
                (evidence_identity,),
            ).fetchone()
            if spent_evidence != (alert_id, "EVIDENCE"):
                raise OutboxError("alert evidence history is invalid")
        expected_spent = (
            {(item[0], "TRANSITION") for item in rows}
            | {(item[9], "CLAIM") for item in rows if item[9] is not None}
            | {(item[0], "EVIDENCE") for item in evidence_rows}
        )
        actual_spent = set(self._connection.execute(
            "SELECT identity,identity_kind FROM spent_identities WHERE alert_id=?", (alert_id,)
        ).fetchall())
        if actual_spent != expected_spent:
            raise OutboxError("alert identity history is invalid")
        return record

    def _verify(self, evidence: DeliveryEvidence) -> bool:
        try:
            result = self._verifier.verify(evidence)
        except Exception:
            return False
        return result is True

    def _insert_transition(self, transition_id: str, alert_id: str, sequence: int, source: AlertState, target: AlertState, now: datetime, reason: AlertReason, attempt_count: int, acknowledgment: AcknowledgmentStatus, claim_id: str | None = None, evidence_id: str | None = None) -> None:
        self._connection.execute(
            "INSERT INTO alert_transitions VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (transition_id, alert_id, sequence, source.value, target.value,
             now.isoformat(), reason.value, attempt_count, _bucket(attempt_count).value,
             acknowledgment.value, claim_id, evidence_id),
        )

    def _spend(self, identity: str, alert_id: str, kind: str, now: datetime) -> None:
        try:
            self._connection.execute("INSERT INTO spent_identities VALUES(?,?,?,?)", (identity, alert_id, kind, now.isoformat()))
        except sqlite3.IntegrityError:
            raise OutboxError("durable identity is already spent") from None

    def _begin(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except Exception:
            raise OutboxError("outbox transaction could not begin") from None

    def _commit(self) -> None:
        try:
            self._connection.execute("COMMIT")
        except Exception:
            raise OutboxError("outbox transaction could not commit") from None

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except Exception:
            pass


def intent_from_status(report: OperationalStatusReport, *, alert_id: str, correlation_id: str, created_at: datetime) -> AlertIntent:
    if not isinstance(report, OperationalStatusReport):
        raise OutboxError("typed operational status is required")
    mapping = {
        OverallStatus.SUSPENDED: (AlertCategory.SUSPENSION, AlertSeverity.CRITICAL),
        OverallStatus.AMBIGUOUS: (AlertCategory.RUNNER_AMBIGUITY, AlertSeverity.CRITICAL),
        OverallStatus.FAILED_CLOSED: (AlertCategory.FAILED_CLOSED, AlertSeverity.CRITICAL),
        OverallStatus.REVIEW_REQUIRED: (AlertCategory.OPERATOR_REVIEW_REQUIRED, AlertSeverity.WARNING),
        OverallStatus.DEGRADED: (AlertCategory.STATUS_DEGRADED, AlertSeverity.WARNING),
        OverallStatus.EXPIRED: (AlertCategory.HEARTBEAT_STALE, AlertSeverity.WARNING),
    }
    if report.overall_status not in mapping:
        raise OutboxError("operational status does not require an alert")
    category, severity = mapping[report.overall_status]
    return AlertIntent(alert_id, category, severity, AlertReason.STATUS_PROJECTION, created_at, correlation_id)


def intent_from_liveness(decision: LivenessDecision, *, alert_id: str, correlation_id: str, created_at: datetime) -> AlertIntent:
    if not isinstance(decision, LivenessDecision):
        raise OutboxError("typed liveness decision is required")
    mapping = {
        LivenessState.STALE: AlertCategory.HEARTBEAT_STALE,
        LivenessState.MISSED_CYCLE: AlertCategory.MISSED_CYCLE,
        LivenessState.SUSPENSION_REQUIRED: AlertCategory.SUSPENSION,
        LivenessState.FAILED_CLOSED: AlertCategory.FAILED_CLOSED,
        LivenessState.DEGRADED: AlertCategory.STATUS_DEGRADED,
    }
    if decision.record.state not in mapping:
        raise OutboxError("liveness decision does not require an alert")
    category = mapping[decision.record.state]
    severity = AlertSeverity.CRITICAL if category in {AlertCategory.SUSPENSION, AlertCategory.FAILED_CLOSED} else AlertSeverity.WARNING
    return AlertIntent(alert_id, category, severity, AlertReason.LIVENESS_PROJECTION, created_at, correlation_id)


def intent_from_suspension(decision: SuspensionDecision, *, alert_id: str, correlation_id: str, created_at: datetime) -> AlertIntent:
    if not isinstance(decision, SuspensionDecision):
        raise OutboxError("typed suspension decision is required")
    if decision.state is ControllerState.CLEAR:
        raise OutboxError("clear controller state does not require an alert")
    category = AlertCategory.INTERNAL_INVARIANT_FAILURE if decision.state is ControllerState.MALFORMED_STATE else AlertCategory.SUSPENSION
    return AlertIntent(alert_id, category, AlertSeverity.CRITICAL, AlertReason.SUSPENSION_PROJECTION, created_at, correlation_id)


def _bucket(count: int) -> AttemptBucket:
    return AttemptBucket.ZERO if count == 0 else AttemptBucket.ONE if count == 1 else AttemptBucket.MANY


def _identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or value != value.strip() or len(value) > 96:
        raise OutboxError(f"{label} is invalid")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise OutboxError(f"{label} is invalid")
    if any(fragment in value.casefold() for fragment in ("account", "credential", "email", "order", "phone", "symbol", "token", "user", "webhook")):
        raise OutboxError(f"{label} is private")
    return value


def _moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise OutboxError(f"{label} is invalid")
    return value


def _parse_moment(value: str) -> datetime:
    try:
        return _moment(datetime.fromisoformat(value), "stored timestamp")
    except Exception:
        raise OutboxError("stored timestamp is invalid") from None


def _database_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or os.fspath(path) == ":memory:":
        raise OutboxError("outbox database path is invalid")
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        raise OutboxError("outbox database path is invalid") from None
    try:
        if os.path.commonpath((os.fspath(resolved), os.fspath(REPOSITORY_ROOT))) == os.fspath(REPOSITORY_ROOT):
            raise OutboxError("repository data path is prohibited")
    except ValueError:
        pass
    private_fragments = (
        "account", "credential", "email", "order", "phone", "private",
        "secret", "symbol", "token", "username", "wallet", "webhook",
    )
    if any(part.casefold().startswith(("prod", "production")) for part in resolved.parts):
        raise OutboxError("production path is prohibited")
    if any(fragment in part.casefold() for part in resolved.parts for fragment in private_fragments):
        raise OutboxError("private outbox path is prohibited")
    if resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise OutboxError("outbox database path is invalid")
    return resolved
