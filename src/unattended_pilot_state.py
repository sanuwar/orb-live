"""Durable attempt, single-use permit, and kill-state evidence for M22.

Broker observations remain authoritative.  This store only makes conservative
local blockers survive a crash.  It has no delete, retry, or automatic clear.
"""
from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from src.bounded_unattended_orchestrator import validate_unattended_candidate
from src.exchange_calendar import EASTERN
from src.offline_path_safety import REPOSITORY_ROOT
from src.protection_observation_model import canonical_bytes

PERMIT_MAXIMUM_AGE_SECONDS = 30.0
_DOMAIN = b"orb-live/unattended-pilot-state/v1"


class UnattendedPilotStateError(RuntimeError):
    pass


def _sha64(value: Any) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", value or ""))


def _safe_reason(value: Any) -> bool:
    return bool(re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", value or ""))


class AttemptOutcome(str, Enum):
    RESERVED = "RESERVED"
    DISPATCHING = "DISPATCHING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WRITE_OUTCOME_UNKNOWN = "WRITE_OUTCOME_UNKNOWN"


class PermitStatus(str, Enum):
    ISSUED = "ISSUED"
    DISPATCHING = "DISPATCHING"
    ACCEPTED = "ACCEPTED"
    REJECTED = "REJECTED"
    WRITE_OUTCOME_UNKNOWN = "WRITE_OUTCOME_UNKNOWN"


@dataclass(frozen=True)
class DailyAttemptRecord:
    attempt_id: str
    trading_date: date
    run_id: str
    trade_id: str
    candidate_id: str
    entry_action_identity: str | None
    symbol: str
    account_fingerprint_reference: str
    release_sha: str
    implementation_digest: str
    reserved_at: datetime
    outcome: AttemptOutcome


@dataclass(frozen=True)
class KillStateRecord:
    kill_id: str
    account_fingerprint_reference: str
    trading_date: date
    reason_code: str
    entered_at: datetime


@dataclass(frozen=True)
class UnattendedWritePermit:
    permit_id: str
    permit_digest: str
    policy_id: str
    attempt_id: str
    safety_decision_id: str
    safety_action_identity: str
    role: str
    symbol: str
    side: str
    order_type: str
    quantity: str
    stop_price: str | None
    order_id: str | None
    trading_date: date
    account_fingerprint_reference: str
    release_sha: str
    implementation_digest: str
    broker_snapshot_identity: str
    suspension_decision_identity: str
    issued_at: datetime
    evidence_origin: str = "REVIEWED_PRODUCTION"

    def __post_init__(self) -> None:
        if self.permit_id != self.permit_digest:
            raise UnattendedPilotStateError("permit identity mismatch")
        if self.permit_digest != compute_write_permit_digest(self):
            raise UnattendedPilotStateError("permit digest mismatch")


def _aware(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise UnattendedPilotStateError("timestamp must be timezone-aware")
    return value


def _payload(value: Any) -> dict[str, Any]:
    return {
        key: item.value if isinstance(item, Enum) else item.isoformat()
        if isinstance(item, (date, datetime)) else item
        for key, item in value.__dict__.items()
        if key not in {"permit_id", "permit_digest"}
    }


def compute_write_permit_digest(value: Any) -> str:
    return hashlib.sha256(
        _DOMAIN + b"\0permit\0" + canonical_bytes(_payload(value))
    ).hexdigest()


def validate_write_permit(
    permit: Any, *, policy: Any, safety_decision: Any, role: str,
    arguments: Mapping[str, Any], now: datetime,
) -> tuple[bool, str]:
    if not isinstance(permit, UnattendedWritePermit):
        return False, "UNATTENDED_WRITE_PERMIT_MISSING"
    if permit.permit_digest != compute_write_permit_digest(permit):
        return False, "UNATTENDED_WRITE_PERMIT_DIGEST_MISMATCH"
    origin = getattr(getattr(policy, "evidence_origin", None), "value", None)
    if permit.evidence_origin != "REVIEWED_PRODUCTION" or origin != "REVIEWED_PRODUCTION":
        return False, "UNATTENDED_WRITE_PERMIT_NOT_PRODUCTION"
    if not all((getattr(policy, "enabled", False) is True,
                getattr(policy, "activation_approved", False) is True,
                getattr(policy, "permits_automated_action", False) is True)):
        return False, "UNATTENDED_POLICY_NOT_ACTIVE"
    checks = (
        permit.policy_id == getattr(policy, "policy_id", None),
        permit.account_fingerprint_reference == getattr(policy, "account_fingerprint_reference", None),
        permit.release_sha == getattr(policy, "release_sha", None),
        permit.implementation_digest == getattr(policy, "implementation_digest", None),
        permit.safety_decision_id == getattr(safety_decision, "decision_id", None),
        permit.safety_action_identity == getattr(safety_decision, "action_identity", None),
        permit.role == role,
    )
    if not all(checks):
        return False, "UNATTENDED_WRITE_BINDING_MISMATCH"
    if role == "cancel":
        if permit.order_id != arguments.get("order_id"):
            return False, "UNATTENDED_CANCEL_IDENTITY_MISMATCH"
    else:
        expected = {"entry": ("buy", "market"), "stop": ("sell", "stop_market"),
                    "exit": ("sell", "market")}.get(role)
        if expected is None or not all((
            permit.symbol == arguments.get("symbol"), permit.symbol in policy.symbol_allowlist,
            permit.side == arguments.get("side") == expected[0],
            permit.order_type == arguments.get("type") == expected[1],
            permit.quantity == arguments.get("quantity") == "1",
            permit.stop_price == arguments.get("stop_price"),
        )):
            return False, "UNATTENDED_ACTION_MISMATCH"
    age = (_aware(now) - permit.issued_at).total_seconds()
    if not 0 <= age <= PERMIT_MAXIMUM_AGE_SECONDS:
        return False, "UNATTENDED_WRITE_PERMIT_STALE"
    return True, "OK"


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pilot_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS daily_attempts(
 account_fingerprint TEXT NOT NULL,trading_date TEXT NOT NULL,attempt_id TEXT UNIQUE NOT NULL,
 run_id TEXT NOT NULL,trade_id TEXT NOT NULL,candidate_id TEXT NOT NULL,action_identity TEXT,symbol TEXT NOT NULL,
 release_sha TEXT NOT NULL,implementation_digest TEXT NOT NULL,reserved_at TEXT NOT NULL,
 outcome TEXT NOT NULL,PRIMARY KEY(account_fingerprint,trading_date));
CREATE TABLE IF NOT EXISTS write_permits(
 permit_id TEXT PRIMARY KEY,action_identity TEXT UNIQUE NOT NULL,account_fingerprint TEXT NOT NULL,
 trading_date TEXT NOT NULL,role TEXT NOT NULL,issued_at TEXT NOT NULL,status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS kill_events(
 kill_id TEXT PRIMARY KEY,account_fingerprint TEXT NOT NULL,trading_date TEXT NOT NULL,
 reason_code TEXT NOT NULL,entered_at TEXT NOT NULL);
"""


class UnattendedPilotStateStore:
    def __init__(self, database_path: Path) -> None:
        if not isinstance(database_path, Path) or not database_path.is_absolute():
            raise UnattendedPilotStateError("state path must be absolute")
        resolved = database_path.resolve(strict=False)
        root = REPOSITORY_ROOT.resolve(strict=False)
        if resolved == root or root in resolved.parents:
            raise UnattendedPilotStateError("state store must be outside the repository")
        self.database_path = resolved
        resolved.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(os.fspath(resolved), isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_SCHEMA)
        row = self._connection.execute("SELECT value FROM pilot_meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._connection.execute("INSERT INTO pilot_meta VALUES('schema_version','1')")
        elif row[0] != "1":
            raise UnattendedPilotStateError("unsupported state schema")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "UnattendedPilotStateStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def active_kill(self, fingerprint: str) -> KillStateRecord | None:
        row = self._connection.execute(
            "SELECT kill_id,trading_date,reason_code,entered_at FROM kill_events "
            "WHERE account_fingerprint=? ORDER BY entered_at DESC LIMIT 1", (fingerprint,)
        ).fetchone()
        return None if row is None else KillStateRecord(
            row[0], fingerprint, date.fromisoformat(row[1]), row[2], datetime.fromisoformat(row[3]))

    def attempt_for(self, fingerprint: str, trading_date: date) -> DailyAttemptRecord | None:
        row = self._connection.execute(
            "SELECT attempt_id,run_id,trade_id,candidate_id,action_identity,symbol,release_sha,"
            "implementation_digest,reserved_at,outcome FROM daily_attempts "
            "WHERE account_fingerprint=? AND trading_date=?", (fingerprint, trading_date.isoformat())
        ).fetchone()
        return None if row is None else DailyAttemptRecord(
            row[0], trading_date, row[1], row[2], row[3], row[4], row[5], fingerprint,
            row[6], row[7], datetime.fromisoformat(row[8]), AttemptOutcome(row[9]))

    def reserve_entry_attempt(self, *, policy: Any, candidate: Any, trading_date: date,
                              run_id: str, trade_id: str, now: datetime) -> DailyAttemptRecord:
        origin = getattr(getattr(policy, "evidence_origin", None), "value", None)
        if origin != "REVIEWED_PRODUCTION" or not all((policy.enabled,
                policy.activation_approved, policy.permits_automated_action)):
            raise UnattendedPilotStateError("production policy is not active")
        authentic, reasons = validate_unattended_candidate(candidate, policy)
        if not authentic:
            raise UnattendedPilotStateError("candidate is not authentic: " + ",".join(reasons))
        if not _sha64(run_id) or trade_id != candidate.decision_id:
            raise UnattendedPilotStateError("run/trade identity is not canonical")
        timestamp = _aware(now)
        if candidate.trading_date != trading_date or (
            candidate.signal_timestamp.astimezone(EASTERN).date() != trading_date
        ):
            raise UnattendedPilotStateError("candidate trading date mismatch")
        body = {"policy": policy.policy_id, "date": trading_date.isoformat(), "run": run_id,
                "trade": trade_id, "candidate": candidate.decision_id,
                "symbol": candidate.symbol, "at": timestamp.isoformat()}
        attempt_id = hashlib.sha256(_DOMAIN + b"\0attempt\0" + canonical_bytes(body)).hexdigest()
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            if self.active_kill(policy.account_fingerprint_reference):
                raise UnattendedPilotStateError("kill state blocks entry")
            self._connection.execute("INSERT INTO daily_attempts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (
                policy.account_fingerprint_reference, trading_date.isoformat(), attempt_id,
                run_id, trade_id, candidate.decision_id, None, candidate.symbol,
                policy.release_sha, policy.implementation_digest, timestamp.isoformat(),
                AttemptOutcome.RESERVED.value))
            self._connection.execute("COMMIT")
        except Exception as exc:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            if isinstance(exc, UnattendedPilotStateError):
                raise
            raise UnattendedPilotStateError("daily entry allowance is consumed") from exc
        record = self.attempt_for(policy.account_fingerprint_reference, trading_date)
        assert record is not None
        return record

    def issue_write_permit(self, *, policy: Any, attempt: DailyAttemptRecord,
                           safety_decision: Any, role: str, arguments: Mapping[str, Any],
                           broker_snapshot_identity: str, suspension_decision_identity: str,
                           now: datetime) -> UnattendedWritePermit:
        if self.active_kill(attempt.account_fingerprint_reference):
            raise UnattendedPilotStateError("kill state blocks permit")
        if not _sha64(broker_snapshot_identity) or not _sha64(
            suspension_decision_identity
        ):
            raise UnattendedPilotStateError("read-only observation identity is invalid")
        current_attempt = self.attempt_for(
            attempt.account_fingerprint_reference, attempt.trading_date
        )
        if current_attempt != attempt:
            raise UnattendedPilotStateError("attempt evidence mismatch")
        if role == "entry":
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                count = self._connection.execute(
                    "UPDATE daily_attempts SET action_identity=? "
                    "WHERE attempt_id=? AND action_identity IS NULL",
                    (safety_decision.action_identity, attempt.attempt_id),
                ).rowcount
                if count != 1:
                    row = self._connection.execute(
                        "SELECT action_identity FROM daily_attempts WHERE attempt_id=?",
                        (attempt.attempt_id,),
                    ).fetchone()
                    if row is None or row[0] != safety_decision.action_identity:
                        raise UnattendedPilotStateError("entry action identity mismatch")
                self._connection.execute("COMMIT")
            except Exception:
                if self._connection.in_transaction:
                    self._connection.execute("ROLLBACK")
                raise
        elif current_attempt.entry_action_identity is None:
            raise UnattendedPilotStateError("entry action has not been durably bound")
        values = dict(policy_id=policy.policy_id, attempt_id=attempt.attempt_id,
            safety_decision_id=safety_decision.decision_id,
            safety_action_identity=safety_decision.action_identity, role=role,
            symbol=str(arguments.get("symbol", "")), side=str(arguments.get("side", "")),
            order_type=str(arguments.get("type", "")), quantity=str(arguments.get("quantity", "")),
            stop_price=arguments.get("stop_price"), order_id=arguments.get("order_id"),
            trading_date=attempt.trading_date,
            account_fingerprint_reference=attempt.account_fingerprint_reference,
            release_sha=attempt.release_sha, implementation_digest=attempt.implementation_digest,
            broker_snapshot_identity=broker_snapshot_identity,
            suspension_decision_identity=suspension_decision_identity, issued_at=_aware(now),
            evidence_origin="REVIEWED_PRODUCTION")
        holder = type("PermitPayload", (), {})()
        holder.__dict__.update(values)
        digest = compute_write_permit_digest(holder)
        permit = UnattendedWritePermit(digest, digest, **values)
        try:
            self._connection.execute("INSERT INTO write_permits VALUES(?,?,?,?,?,?,?)", (
                permit.permit_id, permit.safety_action_identity, permit.account_fingerprint_reference,
                permit.trading_date.isoformat(), permit.role, permit.issued_at.isoformat(),
                PermitStatus.ISSUED.value))
        except sqlite3.IntegrityError as exc:
            raise UnattendedPilotStateError("action already permitted; retry refused") from exc
        return permit

    def permit_status(self, permit_id: str) -> PermitStatus | None:
        row = self._connection.execute(
            "SELECT status FROM write_permits WHERE permit_id=?", (permit_id,)
        ).fetchone()
        return None if row is None else PermitStatus(row[0])

    def dispatch_is_authorized(self, permit: UnattendedWritePermit) -> bool:
        return bool(
            self.permit_status(permit.permit_id) is PermitStatus.DISPATCHING
            and self.active_kill(permit.account_fingerprint_reference) is None
            and self.attempt_for(
                permit.account_fingerprint_reference, permit.trading_date
            ) is not None
        )
    def mark_dispatching(self, permit: UnattendedWritePermit) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            count = self._connection.execute(
                "UPDATE write_permits SET status=? WHERE permit_id=? AND status=?",
                (PermitStatus.DISPATCHING.value, permit.permit_id, PermitStatus.ISSUED.value)).rowcount
            if count != 1:
                raise UnattendedPilotStateError("permit already spent; blind retry refused")
            if permit.role == "entry":
                self._connection.execute("UPDATE daily_attempts SET outcome=? WHERE attempt_id=?",
                    (AttemptOutcome.DISPATCHING.value, permit.attempt_id))
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                self._connection.execute("ROLLBACK")
            raise

    def mark_known_outcome(self, permit: UnattendedWritePermit, *, accepted: bool) -> None:
        status = PermitStatus.ACCEPTED if accepted else PermitStatus.REJECTED
        self._connection.execute("UPDATE write_permits SET status=? WHERE permit_id=?",
                                 (status.value, permit.permit_id))
        if permit.role == "entry":
            outcome = AttemptOutcome.ACCEPTED if accepted else AttemptOutcome.REJECTED
            self._connection.execute("UPDATE daily_attempts SET outcome=? WHERE attempt_id=?",
                                     (outcome.value, permit.attempt_id))

    def enter_kill_state(self, *, fingerprint: str, trading_date: date,
                         reason_code: str, now: datetime) -> KillStateRecord:
        timestamp = _aware(now)
        if not _sha64(fingerprint) or not _safe_reason(reason_code):
            raise UnattendedPilotStateError("kill evidence is not sanitized")
        body = {"account": fingerprint, "date": trading_date.isoformat(),
                "reason": reason_code, "at": timestamp.isoformat()}
        kill_id = hashlib.sha256(_DOMAIN + b"\0kill\0" + canonical_bytes(body)).hexdigest()
        self._connection.execute("INSERT OR IGNORE INTO kill_events VALUES(?,?,?,?,?)",
            (kill_id, fingerprint, trading_date.isoformat(), reason_code, timestamp.isoformat()))
        record = self.active_kill(fingerprint)
        assert record is not None
        return record

    def mark_write_outcome_unknown(self, permit: UnattendedWritePermit, *,
                                   reason_code: str, now: datetime) -> KillStateRecord:
        self._connection.execute("UPDATE write_permits SET status=? WHERE permit_id=?",
            (PermitStatus.WRITE_OUTCOME_UNKNOWN.value, permit.permit_id))
        if permit.role == "entry":
            self._connection.execute("UPDATE daily_attempts SET outcome=? WHERE attempt_id=?",
                (AttemptOutcome.WRITE_OUTCOME_UNKNOWN.value, permit.attempt_id))
        return self.enter_kill_state(fingerprint=permit.account_fingerprint_reference,
            trading_date=permit.trading_date, reason_code=reason_code, now=now)


__all__ = ["AttemptOutcome", "DailyAttemptRecord", "KillStateRecord",
    "PERMIT_MAXIMUM_AGE_SECONDS", "PermitStatus", "UnattendedPilotStateError",
    "UnattendedPilotStateStore", "UnattendedWritePermit",
    "compute_write_permit_digest", "validate_write_permit"]
