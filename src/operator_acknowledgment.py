"""Durable offline operator-awareness workflow.

Acknowledgment records that a reviewed condition was seen.  It is deliberately
incapable of clearing suspension, approving activation or protection, opening
an execution gate, or authorizing automated action.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Protocol, runtime_checkable

from src.durable_alert_outbox import AlertCategory, AlertRecord, AlertState
from src.durable_scheduler import REVIEW_REASONS, RunState, ScheduledRun, SchedulerAction, SchedulerDecision, SchedulerReason, SchedulerTransitionReceipt
from src.durable_suspension_controller import ControllerRecord, ControllerState, SuspensionDecision
from src.operational_status_report import OperationalStatusReport, OverallStatus


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1


class AcknowledgmentError(RuntimeError):
    """Fixed privacy-safe workflow refusal."""


class AcknowledgmentState(str, Enum):
    PENDING = "PENDING"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    FAILED_CLOSED = "FAILED_CLOSED"


class AcknowledgmentCategory(str, Enum):
    SUSPENSION_NOTICE = "SUSPENSION_NOTICE"
    MISSED_CYCLE = "MISSED_CYCLE"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    FAILED_CLOSED = "FAILED_CLOSED"
    STATUS_DEGRADED = "STATUS_DEGRADED"
    ALERT_UNCONFIRMED = "ALERT_UNCONFIRMED"
    PROTECTION_NOT_GROUNDED = "PROTECTION_NOT_GROUNDED"
    ACTIVATION_NOT_APPROVED = "ACTIVATION_NOT_APPROVED"


class AcknowledgmentReason(str, Enum):
    REVIEW_PENDING = "REVIEW_PENDING"
    OPERATOR_AWARE = "OPERATOR_AWARE"
    OPERATOR_REJECTED = "OPERATOR_REJECTED"
    REVIEW_EXPIRED = "REVIEW_EXPIRED"
    REVIEW_SUPERSEDED = "REVIEW_SUPERSEDED"
    ARTIFACT_INVALID = "ARTIFACT_INVALID"
    ARTIFACT_STALE = "ARTIFACT_STALE"
    ARTIFACT_FUTURE_DATED = "ARTIFACT_FUTURE_DATED"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    SEQUENCE_MISMATCH = "SEQUENCE_MISMATCH"


class AttestedProvenance(str, Enum):
    VERIFIED_ATTESTATION = "VERIFIED_ATTESTATION"
    UNVERIFIED = "UNVERIFIED"
    SELF_DECLARED = "SELF_DECLARED"


class AcknowledgmentDisposition(str, Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    REJECT = "REJECT"
    SUPERSEDE = "SUPERSEDE"


class AcknowledgmentSourceKind(str, Enum):
    REVIEW_ITEM = "REVIEW_ITEM"
    STATUS_REPORT = "STATUS_REPORT"
    ALERT_OUTBOX = "ALERT_OUTBOX"
    SUSPENSION_CONTROLLER = "SUSPENSION_CONTROLLER"
    SCHEDULER = "SCHEDULER"


@dataclass(frozen=True)
class AcknowledgmentRequest:
    acknowledgment_id: str
    review_item_id: str
    expected_review_sequence: int
    category: AcknowledgmentCategory
    issued_at: datetime
    expires_at: datetime
    correlation_id: str
    source_kind: AcknowledgmentSourceKind
    source_id: str
    source_sequence: int
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.acknowledgment_id, "ack-", "acknowledgment identity")
        _identifier(self.review_item_id, "review-item-", "review item identity")
        _sequence(self.expected_review_sequence)
        if not isinstance(self.category, AcknowledgmentCategory):
            raise AcknowledgmentError("acknowledgment category is invalid")
        _moment(self.issued_at, "issue timestamp")
        _moment(self.expires_at, "expiry timestamp")
        if self.expires_at <= self.issued_at:
            raise AcknowledgmentError("acknowledgment lifetime is invalid")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        if not isinstance(self.source_kind, AcknowledgmentSourceKind):
            raise AcknowledgmentError("acknowledgment source kind is invalid")
        _identifier(self.source_id, "source-", "source identity")
        _sequence(self.source_sequence)
        if self.source_sequence != self.expected_review_sequence:
            raise AcknowledgmentError("source and review sequence conflict")
        if self.source_kind is AcknowledgmentSourceKind.REVIEW_ITEM:
            source_binding_valid = self.source_id == f"source-{self.review_item_id}"
        else:
            tag = self.source_kind.value.lower().replace("_", "-")
            source_prefix, review_prefix = f"source-{tag}-", f"review-item-{tag}-"
            source_binding_valid = (
                self.source_id.startswith(source_prefix)
                and self.review_item_id.startswith(review_prefix)
                and self.source_id.removeprefix(source_prefix)
                == self.review_item_id.removeprefix(review_prefix)
                and bool(self.source_id.removeprefix(source_prefix))
            )
        if not source_binding_valid:
            raise AcknowledgmentError("source and review identity conflict")
        _false(self.permits_automated_action)


@dataclass(frozen=True)
class OperatorAcknowledgmentArtifact:
    artifact_id: str
    acknowledgment_id: str
    review_item_id: str
    review_sequence: int
    category: AcknowledgmentCategory
    operator_reviewed: bool
    provenance: AttestedProvenance
    signature_present: bool
    issued_at: datetime
    expires_at: datetime
    disposition: AcknowledgmentDisposition = AcknowledgmentDisposition.ACKNOWLEDGE
    successor_review_item_id: str | None = None
    successor_review_sequence: int | None = None
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.artifact_id, "ack-artifact-", "artifact identity")
        _identifier(self.acknowledgment_id, "ack-", "acknowledgment identity")
        _identifier(self.review_item_id, "review-item-", "review item identity")
        _sequence(self.review_sequence)
        if not isinstance(self.category, AcknowledgmentCategory):
            raise AcknowledgmentError("artifact category is invalid")
        if not isinstance(self.operator_reviewed, bool) or not isinstance(self.signature_present, bool):
            raise AcknowledgmentError("artifact flags are invalid")
        if not isinstance(self.provenance, AttestedProvenance):
            raise AcknowledgmentError("artifact provenance is invalid")
        _moment(self.issued_at, "artifact issue timestamp")
        _moment(self.expires_at, "artifact expiry timestamp")
        if self.expires_at <= self.issued_at:
            raise AcknowledgmentError("artifact lifetime is invalid")
        if not isinstance(self.disposition, AcknowledgmentDisposition):
            raise AcknowledgmentError("artifact disposition is invalid")
        if self.disposition is AcknowledgmentDisposition.SUPERSEDE:
            _identifier(self.successor_review_item_id, "review-item-", "successor review item identity")
            _sequence(self.successor_review_sequence)
            if (
                self.successor_review_sequence != self.review_sequence + 1
                or self.successor_review_item_id != _successor_id(
                    self.review_item_id, self.successor_review_sequence,
                )
            ):
                raise AcknowledgmentError("successor review binding is invalid")
        elif self.successor_review_item_id is not None or self.successor_review_sequence is not None:
            raise AcknowledgmentError("non-supersession artifact contains a successor")
        _false(self.permits_automated_action)


@runtime_checkable
class AcknowledgmentArtifactVerifier(Protocol):
    verifier_id: str

    def verify(self, artifact: OperatorAcknowledgmentArtifact) -> bool: ...


@dataclass(frozen=True)
class AcknowledgmentRecord:
    acknowledgment_id: str
    review_item_id: str
    expected_review_sequence: int
    category: AcknowledgmentCategory
    state: AcknowledgmentState
    reason: AcknowledgmentReason
    operator_reviewed: bool
    provenance: AttestedProvenance | None
    issued_at: datetime
    expires_at: datetime
    updated_at: datetime
    correlation_id: str
    transition_sequence: int
    last_transition_id: str
    artifact_id: str | None
    source_kind: AcknowledgmentSourceKind
    source_id: str
    source_sequence: int
    successor_review_item_id: str | None
    successor_review_sequence: int | None
    requires_further_review: bool
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.acknowledgment_id, "ack-", "acknowledgment identity")
        _identifier(self.review_item_id, "review-item-", "review item identity")
        _sequence(self.expected_review_sequence)
        if not isinstance(self.category, AcknowledgmentCategory) or not isinstance(self.state, AcknowledgmentState) or not isinstance(self.reason, AcknowledgmentReason):
            raise AcknowledgmentError("acknowledgment record is invalid")
        if not isinstance(self.operator_reviewed, bool):
            raise AcknowledgmentError("review indicator is invalid")
        if self.provenance is not None and not isinstance(self.provenance, AttestedProvenance):
            raise AcknowledgmentError("stored provenance is invalid")
        for value, label in ((self.issued_at, "issue timestamp"), (self.expires_at, "expiry timestamp"), (self.updated_at, "update timestamp")):
            _moment(value, label)
        if self.expires_at <= self.issued_at or self.updated_at < self.issued_at:
            raise AcknowledgmentError("stored timestamps conflict")
        _identifier(self.correlation_id, "correlation-", "correlation identity")
        _sequence(self.transition_sequence)
        _identifier(self.last_transition_id, "ack-transition-", "transition identity")
        if self.artifact_id is not None:
            _identifier(self.artifact_id, "ack-artifact-", "artifact identity")
        if not isinstance(self.source_kind, AcknowledgmentSourceKind):
            raise AcknowledgmentError("stored source kind is invalid")
        _identifier(self.source_id, "source-", "source identity")
        _sequence(self.source_sequence)
        if self.source_sequence != self.expected_review_sequence:
            raise AcknowledgmentError("stored source and review sequence conflict")
        if not isinstance(self.requires_further_review, bool) or self.requires_further_review is not True:
            raise AcknowledgmentError("further review obligation is invalid")
        if self.state is AcknowledgmentState.SUPERSEDED:
            _identifier(self.successor_review_item_id, "review-item-", "successor review item identity")
            _sequence(self.successor_review_sequence)
            if (
                self.successor_review_sequence != self.expected_review_sequence + 1
                or self.successor_review_item_id != _successor_id(
                    self.review_item_id, self.successor_review_sequence,
                )
            ):
                raise AcknowledgmentError("stored successor review binding is invalid")
        elif self.successor_review_item_id is not None or self.successor_review_sequence is not None:
            raise AcknowledgmentError("non-superseded state contains a successor")
        if self.state is AcknowledgmentState.ACKNOWLEDGED and (
            not self.operator_reviewed or self.provenance is not AttestedProvenance.VERIFIED_ATTESTATION or self.artifact_id is None
        ):
            raise AcknowledgmentError("acknowledged state lacks verified awareness")
        if self.state is AcknowledgmentState.REJECTED and not self.operator_reviewed:
            raise AcknowledgmentError("rejected state lacks verified operator review")
        if self.state not in {AcknowledgmentState.ACKNOWLEDGED, AcknowledgmentState.REJECTED} and self.operator_reviewed:
            raise AcknowledgmentError("non-acknowledged state cannot claim operator review")
        if self.state in {
            AcknowledgmentState.PENDING, AcknowledgmentState.EXPIRED,
        } and (self.provenance is not None or self.artifact_id is not None):
            raise AcknowledgmentError("non-artifact state contains artifact fields")
        if self.state in {AcknowledgmentState.REJECTED, AcknowledgmentState.SUPERSEDED} and (
            self.provenance is not AttestedProvenance.VERIFIED_ATTESTATION or self.artifact_id is None
        ):
            raise AcknowledgmentError("terminal review action lacks verified evidence")
        if self.state is AcknowledgmentState.FAILED_CLOSED and self.artifact_id is None:
            raise AcknowledgmentError("failed acknowledgment lacks rejected artifact")
        _false(self.permits_automated_action)


@dataclass(frozen=True)
class AcknowledgmentDecision:
    record: AcknowledgmentRecord
    acknowledgment_obligation_closed: bool
    route_to_further_review: bool
    clears_suspension: bool = False
    activation_approved: bool = False
    protection_grounded: bool = False
    opens_execution_gate: bool = False
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.record, AcknowledgmentRecord):
            raise AcknowledgmentError("typed acknowledgment record is required")
        if self.acknowledgment_obligation_closed is not (self.record.state is AcknowledgmentState.ACKNOWLEDGED):
            raise AcknowledgmentError("obligation state conflicts")
        if self.route_to_further_review is not self.record.requires_further_review or self.route_to_further_review is not True:
            raise AcknowledgmentError("acknowledgment must route further review")
        for value in (self.clears_suspension, self.activation_approved, self.protection_grounded, self.opens_execution_gate, self.permits_automated_action):
            _false(value)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS acknowledgment_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS acknowledgment_origins (
 acknowledgment_id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL UNIQUE,
 expected_review_sequence INTEGER NOT NULL, category TEXT NOT NULL,
 issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, correlation_id TEXT NOT NULL,
 source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS acknowledgments (
 acknowledgment_id TEXT PRIMARY KEY, review_item_id TEXT NOT NULL UNIQUE,
 expected_review_sequence INTEGER NOT NULL, category TEXT NOT NULL,
 state TEXT NOT NULL, reason TEXT NOT NULL, operator_reviewed INTEGER NOT NULL CHECK(operator_reviewed IN(0,1)),
 provenance TEXT, issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 correlation_id TEXT NOT NULL, transition_sequence INTEGER NOT NULL,
 last_transition_id TEXT NOT NULL UNIQUE, artifact_id TEXT UNIQUE,
 source_kind TEXT NOT NULL, source_id TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 successor_review_item_id TEXT, successor_review_sequence INTEGER,
 requires_further_review INTEGER NOT NULL DEFAULT 1 CHECK(requires_further_review=1),
 permits_automated_action INTEGER NOT NULL DEFAULT 0 CHECK(permits_automated_action=0)
);
CREATE TABLE IF NOT EXISTS acknowledgment_transitions (
 transition_id TEXT PRIMARY KEY, acknowledgment_id TEXT NOT NULL, sequence INTEGER NOT NULL,
 from_state TEXT NOT NULL, to_state TEXT NOT NULL, reason TEXT NOT NULL,
 occurred_at TEXT NOT NULL, operator_reviewed INTEGER NOT NULL CHECK(operator_reviewed IN(0,1)),
 provenance TEXT, artifact_id TEXT, UNIQUE(acknowledgment_id,sequence),
 FOREIGN KEY(acknowledgment_id) REFERENCES acknowledgments(acknowledgment_id)
);
CREATE TABLE IF NOT EXISTS acknowledgment_artifacts (
 artifact_id TEXT PRIMARY KEY, acknowledgment_id TEXT NOT NULL, review_item_id TEXT NOT NULL,
 review_sequence INTEGER NOT NULL, category TEXT NOT NULL, operator_reviewed INTEGER NOT NULL CHECK(operator_reviewed IN(0,1)),
 provenance TEXT NOT NULL, signature_present INTEGER NOT NULL CHECK(signature_present IN(0,1)),
 issued_at TEXT NOT NULL, expires_at TEXT NOT NULL, attestation_verified INTEGER NOT NULL CHECK(attestation_verified IN(0,1)),
 disposition TEXT NOT NULL, successor_review_item_id TEXT, successor_review_sequence INTEGER,
 FOREIGN KEY(acknowledgment_id) REFERENCES acknowledgments(acknowledgment_id)
);
CREATE TABLE IF NOT EXISTS acknowledgment_spent (
 identity TEXT PRIMARY KEY, acknowledgment_id TEXT NOT NULL, identity_kind TEXT NOT NULL, spent_at TEXT NOT NULL,
 FOREIGN KEY(acknowledgment_id) REFERENCES acknowledgments(acknowledgment_id)
);
"""


class OperatorAcknowledgmentStore:
    def __init__(self, database_path: Path, verifier: AcknowledgmentArtifactVerifier) -> None:
        if not isinstance(verifier, AcknowledgmentArtifactVerifier):
            raise AcknowledgmentError("typed artifact verifier is required")
        _identifier(verifier.verifier_id, "ack-verifier-", "verifier identity")
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
            raise AcknowledgmentError("acknowledgment initialization failed") from None

    def close(self) -> None:
        try:
            self._connection.close()
        except Exception:
            raise AcknowledgmentError("acknowledgment close failed") from None

    def _bind_meta(self) -> None:
        expected = {"schema_version": str(SCHEMA_VERSION), "verifier_identity": self._verifier.verifier_id}
        for key, value in expected.items():
            row = self._connection.execute("SELECT value FROM acknowledgment_meta WHERE key=?", (key,)).fetchone()
            if row is None:
                self._connection.execute("INSERT INTO acknowledgment_meta VALUES(?,?)", (key, value))
            elif row != (value,):
                raise AcknowledgmentError("durable acknowledgment metadata conflicts")

    def create(self, request: AcknowledgmentRequest, *, transition_id: str) -> AcknowledgmentRecord:
        if not isinstance(request, AcknowledgmentRequest):
            raise AcknowledgmentError("typed acknowledgment request is required")
        _identifier(transition_id, "ack-transition-", "transition identity")
        self._begin()
        try:
            if self._connection.execute("SELECT 1 FROM acknowledgments WHERE acknowledgment_id=? OR review_item_id=?", (request.acknowledgment_id, request.review_item_id)).fetchone():
                raise AcknowledgmentError("acknowledgment identity is already bound")
            origin = (
                request.acknowledgment_id, request.review_item_id, request.expected_review_sequence,
                request.category.value, request.issued_at.isoformat(), request.expires_at.isoformat(),
                request.correlation_id, request.source_kind.value, request.source_id, request.source_sequence,
            )
            self._connection.execute("INSERT INTO acknowledgment_origins VALUES(?,?,?,?,?,?,?,?,?,?)", origin)
            self._connection.execute(
                "INSERT INTO acknowledgments VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,0)",
                (request.acknowledgment_id, request.review_item_id, request.expected_review_sequence,
                 request.category.value, AcknowledgmentState.PENDING.value, AcknowledgmentReason.REVIEW_PENDING.value,
                 0, None, request.issued_at.isoformat(), request.expires_at.isoformat(), request.issued_at.isoformat(),
                 request.correlation_id, 1, transition_id, None,
                 request.source_kind.value, request.source_id, request.source_sequence, None, None),
            )
            self._spend(transition_id, request.acknowledgment_id, "TRANSITION", request.issued_at)
            self._insert_transition(transition_id, request.acknowledgment_id, 1, AcknowledgmentState.PENDING, AcknowledgmentState.PENDING, AcknowledgmentReason.REVIEW_PENDING, request.issued_at, False, None, None)
            self._commit()
        except Exception as exc:
            self._rollback()
            if isinstance(exc, AcknowledgmentError):
                raise
            raise AcknowledgmentError("acknowledgment creation failed") from None
        return self.load(request.acknowledgment_id)

    def acknowledge(self, artifact: OperatorAcknowledgmentArtifact, *, transition_id: str, now: datetime) -> AcknowledgmentDecision:
        if not isinstance(artifact, OperatorAcknowledgmentArtifact) or artifact.disposition is not AcknowledgmentDisposition.ACKNOWLEDGE:
            raise AcknowledgmentError("typed acknowledgment disposition is required")
        return self._submit_review_artifact(artifact, transition_id=transition_id, now=now)

    def reject(self, artifact: OperatorAcknowledgmentArtifact, *, transition_id: str, now: datetime) -> AcknowledgmentDecision:
        if not isinstance(artifact, OperatorAcknowledgmentArtifact) or artifact.disposition is not AcknowledgmentDisposition.REJECT:
            raise AcknowledgmentError("typed rejection disposition is required")
        return self._submit_review_artifact(artifact, transition_id=transition_id, now=now)

    def supersede(self, artifact: OperatorAcknowledgmentArtifact, *, transition_id: str, now: datetime) -> AcknowledgmentDecision:
        if not isinstance(artifact, OperatorAcknowledgmentArtifact) or artifact.disposition is not AcknowledgmentDisposition.SUPERSEDE:
            raise AcknowledgmentError("typed supersession disposition is required")
        return self._submit_review_artifact(artifact, transition_id=transition_id, now=now)

    def _submit_review_artifact(self, artifact: OperatorAcknowledgmentArtifact, *, transition_id: str, now: datetime) -> AcknowledgmentDecision:
        if not isinstance(artifact, OperatorAcknowledgmentArtifact):
            raise AcknowledgmentError("typed acknowledgment artifact is required")
        _moment(now, "evaluation timestamp")
        self._begin()
        try:
            record = self._load_audited(artifact.acknowledgment_id)
            if record.state is not AcknowledgmentState.PENDING:
                raise AcknowledgmentError("acknowledgment is terminal")
            self._spend(artifact.artifact_id, record.acknowledgment_id, "ARTIFACT", now)
            if artifact.review_item_id != record.review_item_id or artifact.category is not record.category:
                target, reason = AcknowledgmentState.FAILED_CLOSED, AcknowledgmentReason.IDENTITY_MISMATCH
            elif artifact.review_sequence != record.expected_review_sequence:
                target, reason = AcknowledgmentState.FAILED_CLOSED, AcknowledgmentReason.SEQUENCE_MISMATCH
            elif artifact.issued_at > now:
                target, reason = AcknowledgmentState.FAILED_CLOSED, AcknowledgmentReason.ARTIFACT_FUTURE_DATED
            elif now >= artifact.expires_at or now >= record.expires_at or artifact.issued_at < record.issued_at:
                target, reason = AcknowledgmentState.FAILED_CLOSED, AcknowledgmentReason.ARTIFACT_STALE
            else:
                verified = (
                    artifact.operator_reviewed and artifact.signature_present
                    and artifact.provenance is AttestedProvenance.VERIFIED_ATTESTATION
                    and self._verify(artifact)
                )
                disposition_targets = {
                    AcknowledgmentDisposition.ACKNOWLEDGE: (AcknowledgmentState.ACKNOWLEDGED, AcknowledgmentReason.OPERATOR_AWARE),
                    AcknowledgmentDisposition.REJECT: (AcknowledgmentState.REJECTED, AcknowledgmentReason.OPERATOR_REJECTED),
                    AcknowledgmentDisposition.SUPERSEDE: (AcknowledgmentState.SUPERSEDED, AcknowledgmentReason.REVIEW_SUPERSEDED),
                }
                target, reason = disposition_targets[artifact.disposition] if verified else (AcknowledgmentState.FAILED_CLOSED, AcknowledgmentReason.ARTIFACT_INVALID)
            verified = target in {AcknowledgmentState.ACKNOWLEDGED, AcknowledgmentState.REJECTED, AcknowledgmentState.SUPERSEDED}
            self._connection.execute(
                "INSERT INTO acknowledgment_artifacts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact.artifact_id, artifact.acknowledgment_id, artifact.review_item_id,
                 artifact.review_sequence, artifact.category.value, int(artifact.operator_reviewed),
                 artifact.provenance.value, int(artifact.signature_present), artifact.issued_at.isoformat(),
                 artifact.expires_at.isoformat(), int(verified), artifact.disposition.value,
                 artifact.successor_review_item_id, artifact.successor_review_sequence),
            )
            result = self._transition(record, transition_id, now, target, reason,
                                      operator_reviewed=verified and target is not AcknowledgmentState.SUPERSEDED,
                                      provenance=AttestedProvenance.VERIFIED_ATTESTATION if verified else artifact.provenance,
                                      artifact_id=artifact.artifact_id,
                                      successor_review_item_id=artifact.successor_review_item_id if target is AcknowledgmentState.SUPERSEDED else None,
                                      successor_review_sequence=artifact.successor_review_sequence if target is AcknowledgmentState.SUPERSEDED else None)
            self._commit()
            return AcknowledgmentDecision(result, target is AcknowledgmentState.ACKNOWLEDGED, True)
        except Exception as exc:
            self._rollback()
            if isinstance(exc, AcknowledgmentError):
                raise
            raise AcknowledgmentError("acknowledgment persistence failed") from None

    def expire(self, acknowledgment_id: str, *, transition_id: str, now: datetime) -> AcknowledgmentDecision:
        _moment(now, "expiry evaluation timestamp")
        record = self.load(acknowledgment_id)
        if now < record.expires_at:
            raise AcknowledgmentError("acknowledgment is not expired")
        result = self._terminal_transition(acknowledgment_id, transition_id, now, AcknowledgmentState.EXPIRED, AcknowledgmentReason.REVIEW_EXPIRED)
        return AcknowledgmentDecision(result, False, True)

    def load(self, acknowledgment_id: str) -> AcknowledgmentRecord:
        _identifier(acknowledgment_id, "ack-", "acknowledgment identity")
        try:
            return self._load_audited(acknowledgment_id)
        except AcknowledgmentError:
            raise
        except Exception:
            raise AcknowledgmentError("durable acknowledgment is invalid") from None

    def _terminal_transition(self, acknowledgment_id: str, transition_id: str, now: datetime, target: AcknowledgmentState, reason: AcknowledgmentReason) -> AcknowledgmentRecord:
        _moment(now, "transition timestamp")
        self._begin()
        try:
            record = self._load_audited(acknowledgment_id)
            if record.state is not AcknowledgmentState.PENDING:
                raise AcknowledgmentError("acknowledgment is terminal")
            result = self._transition(record, transition_id, now, target, reason)
            self._commit()
            return result
        except Exception as exc:
            self._rollback()
            if isinstance(exc, AcknowledgmentError):
                raise
            raise AcknowledgmentError("acknowledgment transition failed") from None

    def _transition(self, record: AcknowledgmentRecord, transition_id: str, now: datetime, target: AcknowledgmentState, reason: AcknowledgmentReason, *, operator_reviewed: bool = False, provenance: AttestedProvenance | None = None, artifact_id: str | None = None, successor_review_item_id: str | None = None, successor_review_sequence: int | None = None) -> AcknowledgmentRecord:
        _identifier(transition_id, "ack-transition-", "transition identity")
        _moment(now, "transition timestamp")
        if now < record.updated_at:
            raise AcknowledgmentError("transition timestamp regressed")
        self._spend(transition_id, record.acknowledgment_id, "TRANSITION", now)
        sequence = record.transition_sequence + 1
        self._connection.execute(
            "UPDATE acknowledgments SET state=?,reason=?,operator_reviewed=?,provenance=?,updated_at=?,transition_sequence=?,last_transition_id=?,artifact_id=?,successor_review_item_id=?,successor_review_sequence=? WHERE acknowledgment_id=?",
            (target.value, reason.value, int(operator_reviewed), provenance.value if provenance else None,
             now.isoformat(), sequence, transition_id, artifact_id,
             successor_review_item_id, successor_review_sequence, record.acknowledgment_id),
        )
        self._insert_transition(transition_id, record.acknowledgment_id, sequence, record.state, target, reason, now, operator_reviewed, provenance, artifact_id)
        return self._load_audited(record.acknowledgment_id)

    def _load_audited(self, acknowledgment_id: str) -> AcknowledgmentRecord:
        row = self._connection.execute("SELECT * FROM acknowledgments WHERE acknowledgment_id=?", (acknowledgment_id,)).fetchone()
        if row is None:
            raise AcknowledgmentError("acknowledgment identity is unknown")
        record = AcknowledgmentRecord(
            row[0], row[1], row[2], AcknowledgmentCategory(row[3]), AcknowledgmentState(row[4]),
            AcknowledgmentReason(row[5]), bool(row[6]), AttestedProvenance(row[7]) if row[7] else None,
            _parse_moment(row[8]), _parse_moment(row[9]), _parse_moment(row[10]), row[11], row[12], row[13], row[14],
            AcknowledgmentSourceKind(row[15]), row[16], row[17], row[18], row[19], bool(row[20]), bool(row[21]),
        )
        origin = self._connection.execute("SELECT * FROM acknowledgment_origins WHERE acknowledgment_id=?", (acknowledgment_id,)).fetchone()
        if origin != (
            record.acknowledgment_id, record.review_item_id, record.expected_review_sequence,
            record.category.value, record.issued_at.isoformat(), record.expires_at.isoformat(),
            record.correlation_id, record.source_kind.value, record.source_id, record.source_sequence,
        ):
            raise AcknowledgmentError("acknowledgment origin is invalid")
        rows = self._connection.execute(
            "SELECT transition_id,sequence,from_state,to_state,reason,occurred_at,operator_reviewed,provenance,artifact_id FROM acknowledgment_transitions WHERE acknowledgment_id=? ORDER BY sequence",
            (acknowledgment_id,),
        ).fetchall()
        if len(rows) != record.transition_sequence or not rows:
            raise AcknowledgmentError("acknowledgment history is invalid")
        previous = AcknowledgmentState.PENDING
        previous_time = record.issued_at
        for index, item in enumerate(rows, start=1):
            source, target = AcknowledgmentState(item[2]), AcknowledgmentState(item[3])
            reason, occurred = AcknowledgmentReason(item[4]), _parse_moment(item[5])
            reviewed = bool(item[6])
            provenance = AttestedProvenance(item[7]) if item[7] else None
            if item[1] != index or source is not previous or occurred < previous_time:
                raise AcknowledgmentError("acknowledgment history is invalid")
            if index == 1:
                valid = target is AcknowledgmentState.PENDING and reason is AcknowledgmentReason.REVIEW_PENDING and occurred == record.issued_at and not reviewed and provenance is None and item[8] is None
            elif target in {AcknowledgmentState.ACKNOWLEDGED, AcknowledgmentState.REJECTED, AcknowledgmentState.SUPERSEDED}:
                successful_reasons = {
                    AcknowledgmentState.ACKNOWLEDGED: AcknowledgmentReason.OPERATOR_AWARE,
                    AcknowledgmentState.REJECTED: AcknowledgmentReason.OPERATOR_REJECTED,
                    AcknowledgmentState.SUPERSEDED: AcknowledgmentReason.REVIEW_SUPERSEDED,
                }
                valid = (
                    previous is AcknowledgmentState.PENDING and reason is successful_reasons[target]
                    and reviewed is (target is not AcknowledgmentState.SUPERSEDED)
                    and provenance is AttestedProvenance.VERIFIED_ATTESTATION and item[8] is not None
                )
            elif target is AcknowledgmentState.FAILED_CLOSED:
                valid = previous is AcknowledgmentState.PENDING and reason in {AcknowledgmentReason.ARTIFACT_INVALID, AcknowledgmentReason.ARTIFACT_STALE, AcknowledgmentReason.ARTIFACT_FUTURE_DATED, AcknowledgmentReason.IDENTITY_MISMATCH, AcknowledgmentReason.SEQUENCE_MISMATCH} and not reviewed and item[8] is not None
            else:
                terminal_reasons = {
                    AcknowledgmentState.EXPIRED: AcknowledgmentReason.REVIEW_EXPIRED,
                }
                valid = (
                    previous is AcknowledgmentState.PENDING
                    and target in terminal_reasons and reason is terminal_reasons[target]
                    and not reviewed and provenance is None and item[8] is None
                )
            if not valid or self._spent(item[0]) != (acknowledgment_id, "TRANSITION"):
                raise AcknowledgmentError("acknowledgment history is invalid")
            if item[8] is not None:
                artifact = self._connection.execute("SELECT * FROM acknowledgment_artifacts WHERE artifact_id=?", (item[8],)).fetchone()
                if artifact is None or artifact[1] != acknowledgment_id or self._spent(item[8]) != (acknowledgment_id, "ARTIFACT"):
                    raise AcknowledgmentError("acknowledgment artifact history is invalid")
                artifact_issued, artifact_expires = _parse_moment(artifact[8]), _parse_moment(artifact[9])
                if artifact_expires <= artifact_issued:
                    raise AcknowledgmentError("acknowledgment artifact history is invalid")
                disposition = AcknowledgmentDisposition(artifact[11])
                if disposition is AcknowledgmentDisposition.SUPERSEDE:
                    _identifier(artifact[12], "review-item-", "successor review item identity")
                    _sequence(artifact[13])
                    if (
                        artifact[13] != record.expected_review_sequence + 1
                        or artifact[12] != _successor_id(record.review_item_id, artifact[13])
                    ):
                        raise AcknowledgmentError("acknowledgment artifact history is invalid")
                elif artifact[12] is not None or artifact[13] is not None:
                    raise AcknowledgmentError("acknowledgment artifact history is invalid")
                identity_binding = artifact[2] == record.review_item_id and artifact[4] == record.category.value
                exact_binding = identity_binding and artifact[3] == record.expected_review_sequence
                if target in {AcknowledgmentState.ACKNOWLEDGED, AcknowledgmentState.REJECTED, AcknowledgmentState.SUPERSEDED}:
                    disposition_targets = {
                        AcknowledgmentState.ACKNOWLEDGED: AcknowledgmentDisposition.ACKNOWLEDGE.value,
                        AcknowledgmentState.REJECTED: AcknowledgmentDisposition.REJECT.value,
                        AcknowledgmentState.SUPERSEDED: AcknowledgmentDisposition.SUPERSEDE.value,
                    }
                    temporally_valid = (
                        artifact_issued >= record.issued_at and artifact_issued <= occurred
                        and occurred < artifact_expires and occurred < record.expires_at
                    )
                    successor_valid = (
                        artifact[12] is not None and artifact[13] is not None
                        and artifact[13] == record.expected_review_sequence + 1
                        and artifact[12] == _successor_id(record.review_item_id, artifact[13])
                    ) if target is AcknowledgmentState.SUPERSEDED else artifact[12] is None and artifact[13] is None
                    if not (
                        exact_binding and temporally_valid and successor_valid
                        and artifact[5] == 1 and artifact[6] == AttestedProvenance.VERIFIED_ATTESTATION.value
                        and artifact[7] == 1 and artifact[10] == 1
                        and artifact[11] == disposition_targets[target]
                    ):
                        raise AcknowledgmentError("acknowledgment artifact history is invalid")
                if target is AcknowledgmentState.FAILED_CLOSED:
                    temporal_stale = occurred >= artifact_expires or occurred >= record.expires_at or artifact_issued < record.issued_at
                    temporal_future = artifact_issued > occurred
                    invalid_attestation = not (
                        artifact[5] == 1 and artifact[6] == AttestedProvenance.VERIFIED_ATTESTATION.value
                        and artifact[7] == 1 and artifact[10] == 1
                    )
                    expected_reason = (
                        AcknowledgmentReason.IDENTITY_MISMATCH if not identity_binding
                        else AcknowledgmentReason.SEQUENCE_MISMATCH if artifact[3] != record.expected_review_sequence
                        else AcknowledgmentReason.ARTIFACT_FUTURE_DATED if temporal_future
                        else AcknowledgmentReason.ARTIFACT_STALE if temporal_stale
                        else AcknowledgmentReason.ARTIFACT_INVALID if invalid_attestation
                        else None
                    )
                    if reason is not expected_reason:
                        raise AcknowledgmentError("acknowledgment artifact history is invalid")
            previous, previous_time = target, occurred
        last = rows[-1]
        if (last[0], AcknowledgmentState(last[3]), AcknowledgmentReason(last[4]), _parse_moment(last[5]), bool(last[6]), AttestedProvenance(last[7]) if last[7] else None, last[8]) != (record.last_transition_id, record.state, record.reason, record.updated_at, record.operator_reviewed, record.provenance, record.artifact_id):
            raise AcknowledgmentError("acknowledgment history is invalid")
        if record.state is AcknowledgmentState.SUPERSEDED:
            artifact = self._connection.execute("SELECT successor_review_item_id,successor_review_sequence FROM acknowledgment_artifacts WHERE artifact_id=?", (record.artifact_id,)).fetchone()
            if artifact != (record.successor_review_item_id, record.successor_review_sequence):
                raise AcknowledgmentError("successor review binding is invalid")
        expected_spent = {(item[0], "TRANSITION") for item in rows} | {(item[8], "ARTIFACT") for item in rows if item[8] is not None}
        artifact_ids = {item[0] for item in self._connection.execute("SELECT artifact_id FROM acknowledgment_artifacts WHERE acknowledgment_id=?", (acknowledgment_id,)).fetchall()}
        if artifact_ids != {item[8] for item in rows if item[8] is not None}:
            raise AcknowledgmentError("acknowledgment artifact history is invalid")
        actual_spent = set(self._connection.execute("SELECT identity,identity_kind FROM acknowledgment_spent WHERE acknowledgment_id=?", (acknowledgment_id,)).fetchall())
        if actual_spent != expected_spent:
            raise AcknowledgmentError("acknowledgment identity history is invalid")
        return record

    def _insert_transition(self, transition_id: str, acknowledgment_id: str, sequence: int, source: AcknowledgmentState, target: AcknowledgmentState, reason: AcknowledgmentReason, now: datetime, reviewed: bool, provenance: AttestedProvenance | None, artifact_id: str | None) -> None:
        self._connection.execute(
            "INSERT INTO acknowledgment_transitions VALUES(?,?,?,?,?,?,?,?,?,?)",
            (transition_id, acknowledgment_id, sequence, source.value, target.value, reason.value,
             now.isoformat(), int(reviewed), provenance.value if provenance else None, artifact_id),
        )

    def _spend(self, identity: str, acknowledgment_id: str, kind: str, now: datetime) -> None:
        try:
            self._connection.execute("INSERT INTO acknowledgment_spent VALUES(?,?,?,?)", (identity, acknowledgment_id, kind, now.isoformat()))
        except sqlite3.IntegrityError:
            raise AcknowledgmentError("durable identity is already spent") from None

    def _spent(self, identity: str) -> tuple[str, str] | None:
        return self._connection.execute("SELECT acknowledgment_id,identity_kind FROM acknowledgment_spent WHERE identity=?", (identity,)).fetchone()

    def _verify(self, artifact: OperatorAcknowledgmentArtifact) -> bool:
        try:
            return self._verifier.verify(artifact) is True
        except Exception:
            return False

    def _begin(self) -> None:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
        except Exception:
            raise AcknowledgmentError("acknowledgment transaction could not begin") from None

    def _commit(self) -> None:
        try:
            self._connection.execute("COMMIT")
        except Exception:
            raise AcknowledgmentError("acknowledgment transaction could not commit") from None

    def _rollback(self) -> None:
        try:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
        except Exception:
            pass


def request_from_status(report: OperationalStatusReport, **fields: object) -> AcknowledgmentRequest:
    if not isinstance(report, OperationalStatusReport) or not report.requires_operator_review:
        raise AcknowledgmentError("typed review-required status is required")
    mapping = {
        OverallStatus.SUSPENDED: AcknowledgmentCategory.SUSPENSION_NOTICE,
        OverallStatus.AMBIGUOUS: AcknowledgmentCategory.AMBIGUOUS_RESTART,
        OverallStatus.FAILED_CLOSED: AcknowledgmentCategory.FAILED_CLOSED,
        OverallStatus.DEGRADED: AcknowledgmentCategory.STATUS_DEGRADED,
        OverallStatus.REVIEW_REQUIRED: AcknowledgmentCategory.STATUS_DEGRADED,
        OverallStatus.EXPIRED: AcknowledgmentCategory.STATUS_DEGRADED,
    }
    if report.overall_status not in mapping:
        raise AcknowledgmentError("status does not create an acknowledgment")
    return _request(mapping[report.overall_status], fields, AcknowledgmentSourceKind.STATUS_REPORT, report.report_id, 1)


def request_from_alert(record: AlertRecord, **fields: object) -> AcknowledgmentRequest:
    if not isinstance(record, AlertRecord):
        raise AcknowledgmentError("typed alert record is required")
    mapping = {
        AlertCategory.SUSPENSION: AcknowledgmentCategory.SUSPENSION_NOTICE,
        AlertCategory.MISSED_CYCLE: AcknowledgmentCategory.MISSED_CYCLE,
        AlertCategory.RUNNER_AMBIGUITY: AcknowledgmentCategory.AMBIGUOUS_RESTART,
        AlertCategory.FAILED_CLOSED: AcknowledgmentCategory.FAILED_CLOSED,
    }
    category = mapping.get(record.category, AcknowledgmentCategory.ALERT_UNCONFIRMED if record.state is not AlertState.DELIVERED_CONFIRMED else AcknowledgmentCategory.STATUS_DEGRADED)
    return _request(category, fields, AcknowledgmentSourceKind.ALERT_OUTBOX, record.alert_id, record.transition_sequence)


def request_from_suspension(decision: SuspensionDecision, record: ControllerRecord, **fields: object) -> AcknowledgmentRequest:
    if not isinstance(decision, SuspensionDecision) or not isinstance(record, ControllerRecord) or decision.state is ControllerState.CLEAR:
        raise AcknowledgmentError("typed active suspension decision is required")
    if decision.state is not record.state:
        raise AcknowledgmentError("suspension source state conflicts")
    return _request(AcknowledgmentCategory.SUSPENSION_NOTICE, fields, AcknowledgmentSourceKind.SUSPENSION_CONTROLLER, record.controller_id, record.sequence)


def request_from_scheduler(decision: SchedulerDecision, run: ScheduledRun, receipt: SchedulerTransitionReceipt, **fields: object) -> AcknowledgmentRequest:
    if not isinstance(decision, SchedulerDecision) or not isinstance(run, ScheduledRun) or not isinstance(receipt, SchedulerTransitionReceipt) or decision.action not in {SchedulerAction.REQUIRE_OPERATOR_REVIEW, SchedulerAction.SUSPEND, SchedulerAction.FAIL_CLOSED}:
        raise AcknowledgmentError("typed scheduler review decision is required")
    if receipt.run_id != run.run_id or receipt.to_state is not run.state:
        raise AcknowledgmentError("scheduler source binding conflicts")
    state_binding = {
        SchedulerAction.REQUIRE_OPERATOR_REVIEW: run.state in {
            RunState.READY_FOR_SUPERVISED_REVIEW,
            RunState.SUPERVISED_REVIEW_REQUIRED,
        } and decision.reason in REVIEW_REASONS | {SchedulerReason.REEVALUATION_REQUIRED},
        SchedulerAction.SUSPEND: run.state is RunState.SUSPENDED and decision.reason in {
            SchedulerReason.SAFETY_HALT_ACTIVE,
            SchedulerReason.RUN_IS_SUSPENDED,
        },
        SchedulerAction.FAIL_CLOSED: run.state in {
            RunState.FAILED_CLOSED,
            RunState.AMBIGUOUS_RESTART,
            RunState.EXPIRED,
        } and decision.reason is SchedulerReason.RUN_IS_TERMINAL,
    }
    if not state_binding[decision.action]:
        raise AcknowledgmentError("scheduler decision state conflicts")
    category = {
        SchedulerAction.REQUIRE_OPERATOR_REVIEW: AcknowledgmentCategory.STATUS_DEGRADED,
        SchedulerAction.SUSPEND: AcknowledgmentCategory.SUSPENSION_NOTICE,
        SchedulerAction.FAIL_CLOSED: AcknowledgmentCategory.FAILED_CLOSED,
    }[decision.action]
    if decision.reason is SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED:
        category = AcknowledgmentCategory.ALERT_UNCONFIRMED
    return _request(category, fields, AcknowledgmentSourceKind.SCHEDULER, run.run_id, receipt.sequence)


def _request(category: AcknowledgmentCategory, fields: dict[str, object], source_kind: AcknowledgmentSourceKind, source_value: str, source_sequence: int) -> AcknowledgmentRequest:
    expected = {"acknowledgment_id", "issued_at", "expires_at", "correlation_id"}
    if set(fields) != expected:
        raise AcknowledgmentError("acknowledgment request fields are invalid")
    suffix = source_value.replace("_", "-")
    source_id = f"source-{source_kind.value.lower().replace('_', '-')}-{suffix}"
    review_item_id = f"review-item-{source_kind.value.lower().replace('_', '-')}-{suffix}"
    return AcknowledgmentRequest(
        category=category, review_item_id=review_item_id,
        expected_review_sequence=source_sequence, source_kind=source_kind,
        source_id=source_id, source_sequence=source_sequence, **fields,
    )  # type: ignore[arg-type]


def _successor_id(review_item_id: str, sequence: int) -> str:
    return _identifier(
        f"{review_item_id}-successor-{sequence}",
        "review-item-",
        "successor review item identity",
    )


def _false(value: object) -> None:
    if value is not False:
        raise AcknowledgmentError("operator acknowledgment never authorizes action")


def _sequence(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise AcknowledgmentError("review sequence is invalid")
    return value


def _identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix) or value != value.strip() or len(value) > 96:
        raise AcknowledgmentError(f"{label} is invalid")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise AcknowledgmentError(f"{label} is invalid")
    if any(part in value.casefold() for part in ("account", "credential", "email", "order", "phone", "private", "secret", "symbol", "token", "username", "wallet", "webhook")):
        raise AcknowledgmentError(f"{label} is private")
    return value


def _moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise AcknowledgmentError(f"{label} is invalid")
    return value


def _parse_moment(value: str) -> datetime:
    try:
        return _moment(datetime.fromisoformat(value), "stored timestamp")
    except Exception:
        raise AcknowledgmentError("stored timestamp is invalid") from None


def _database_path(path: object) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or os.fspath(path) == ":memory:":
        raise AcknowledgmentError("acknowledgment database path is invalid")
    try:
        resolved = path.resolve(strict=False)
    except Exception:
        raise AcknowledgmentError("acknowledgment database path is invalid") from None
    try:
        if os.path.commonpath((os.fspath(resolved), os.fspath(REPOSITORY_ROOT))) == os.fspath(REPOSITORY_ROOT):
            raise AcknowledgmentError("repository data path is prohibited")
    except ValueError:
        pass
    private = ("account", "credential", "email", "order", "phone", "private", "prod", "secret", "symbol", "token", "username", "wallet", "webhook")
    if any(fragment in part.casefold() for part in resolved.parts for fragment in private):
        raise AcknowledgmentError("private acknowledgment path is prohibited")
    if resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise AcknowledgmentError("acknowledgment database path is invalid")
    return resolved
