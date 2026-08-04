"""Durable kill-switch and suspension controller (Milestone 10).

Converts reviewed safety facts into persistent suspension state that
`src/durable_scheduler.py` consumes. It does not trade, enable strategy,
contact a broker, or authorize automated action. Its most permissive output is
"the scheduler should require operator review".

## Suspension dominates

A suspension outranks every ordinary favourable condition — a runnable
calendar, a passing risk check, a clean reconciliation, healthy monitoring, an
eligible supervised gate, an eligible limited-unattended gate. None of them may
override it, and none of them is even consulted while one is active. That is
the whole point of the module: the conditions that would let work proceed are
exactly the conditions a halt has to survive.

## Evidence, not booleans

A caller passing `True` is asserting something about itself. Suspension
*clearance* therefore requires a `SignedAttestation` verified through
`AttestationAuthority`, issued by a producer permitted to own that fact and to
cite that source. `SELF_DECLARED` cannot appear: the authority refuses to grant
it to any producer, so it cannot reach here even by mistake.

Raising a suspension is deliberately easier than clearing one. A halt signal
without attestation is still honoured — refusing to stop because the paperwork
was incomplete would be the wrong failure — but nothing without reviewed,
verified, non-self-declared evidence can start work again.

## Clearing is two steps, and neither reaches a runnable state

A valid clearance moves `SUSPENDED` to `CLEARANCE_PENDING_REVIEW`, never to
`CLEAR`. Returning to `CLEAR` is a second, separately attested act. A
suspension worth applying is worth a human looking at the result of clearing
it, and a single artifact that took the system from halted to running would be
one forged or replayed document away from doing exactly that.

`PERMANENT_HALT` has no clearance path at all.

## No wall clock

Every time value is injected. A module that reads the clock cannot be tested at
an expiry boundary without waiting for one, so in practice it is not tested
there. A test parses this file and asserts the absence of every wall-clock call.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.attestation_authority import AttestationAuthority, SignedAttestation
from src.durable_scheduler import SchedulerAction
from src.execution_gates import EvidenceSource
from src.offline_path_safety import REPOSITORY_DATA_DIRECTORY, is_repository_data_path


class SuspensionControllerError(RuntimeError):
    """Any refusal. Never carries broker, account, operator, or secret data."""


# --------------------------------------------------------------------------
# Causes.
# --------------------------------------------------------------------------


class SuspensionCause(str, Enum):
    """Why work is halted. Fixed codes only — never free text."""

    OPERATOR_HALT = "OPERATOR_HALT"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"
    RECONCILIATION_AMBIGUOUS = "RECONCILIATION_AMBIGUOUS"
    BROKER_STATE_UNKNOWN = "BROKER_STATE_UNKNOWN"
    STALE_DATA = "STALE_DATA"
    CALENDAR_INVALID = "CALENDAR_INVALID"
    MONITORING_UNHEALTHY = "MONITORING_UNHEALTHY"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    DUPLICATE_ACTION_RISK = "DUPLICATE_ACTION_RISK"
    RESTART_AMBIGUITY = "RESTART_AMBIGUITY"
    INTERNAL_INVARIANT_FAILURE = "INTERNAL_INVARIANT_FAILURE"
    #: Recorded when a cause cannot be established. Always fails closed, and is
    #: never treated as "probably nothing".
    UNKNOWN_CAUSE = "UNKNOWN_CAUSE"
    #: The absence of a cause. Only valid while the controller is CLEAR.
    NONE = "NONE"


#: How strongly each cause halts, used for the replace-or-augment rule. Higher
#: wins. `UNKNOWN_CAUSE` ranks **above** every ordinary cause on purpose: not
#: knowing why work stopped is worse than knowing, and must not be displaced by
#: a later, better-understood signal that happens to arrive second.
CAUSE_SEVERITY: Mapping[SuspensionCause, int] = MappingProxyType({
    SuspensionCause.NONE: 0,
    SuspensionCause.STALE_DATA: 10,
    SuspensionCause.CALENDAR_INVALID: 20,
    SuspensionCause.MONITORING_UNHEALTHY: 30,
    SuspensionCause.ALERT_DELIVERY_UNCONFIRMED: 40,
    SuspensionCause.BROKER_STATE_UNKNOWN: 50,
    SuspensionCause.RECONCILIATION_AMBIGUOUS: 60,
    SuspensionCause.RESTART_AMBIGUITY: 70,
    SuspensionCause.RISK_LIMIT_BREACH: 80,
    SuspensionCause.DUPLICATE_ACTION_RISK: 90,
    SuspensionCause.OPERATOR_HALT: 100,
    SuspensionCause.KILL_SWITCH_ENGAGED: 110,
    SuspensionCause.UNKNOWN_CAUSE: 120,
    SuspensionCause.INTERNAL_INVARIANT_FAILURE: 130,
})

#: Causes that make the halt irreversible. An internal invariant failure means
#: this process's own reasoning is untrustworthy, and a controller that could
#: clear that condition by consulting the same reasoning proves nothing.
PERMANENT_HALT_CAUSES: frozenset[SuspensionCause] = frozenset({
    SuspensionCause.INTERNAL_INVARIANT_FAILURE,
})

#: The gate fact that can evidence each externally-provable cause, and the
#: sources acceptable for it. Names come from `execution_gates.REQUIRED_EVIDENCE`
#: rather than being invented here, so a producer registered against the gate
#: contract is usable here without a second registry.
#:
#: Causes **absent** from this map — stale data, duplicate-action risk, restart
#: ambiguity, internal invariant failure, unknown cause — can only be raised
#: internally by this system observing itself. There is no external fact that
#: proves them, so there is nothing an attestation could say about them.
CAUSE_EVIDENCE_FACTS: Mapping[SuspensionCause, str] = MappingProxyType({
    SuspensionCause.OPERATOR_HALT: "no_unresolved_operator_action",
    SuspensionCause.KILL_SWITCH_ENGAGED: "kill_switch_disengaged",
    SuspensionCause.RISK_LIMIT_BREACH: "risk_within_limits",
    SuspensionCause.RECONCILIATION_AMBIGUOUS: "recovery_consistent",
    SuspensionCause.BROKER_STATE_UNKNOWN: "broker_state_verified_fresh",
    SuspensionCause.CALENDAR_INVALID: "market_session_valid",
    SuspensionCause.MONITORING_UNHEALTHY: "monitoring_healthy",
    SuspensionCause.ALERT_DELIVERY_UNCONFIRMED: "alerting_verified_reachable",
})

#: The fact a clearance must carry. Distinct from every cause fact: clearing is
#: its own reviewed act, not the negation of whatever raised the suspension.
CLEARANCE_FACT = "supervised_evidence_reviewed"

#: The only source that can clear. `MEASURED` and `TESTED` describe machinery
#: observing itself, which is not a review.
CLEARANCE_SOURCES: frozenset[EvidenceSource] = frozenset({
    EvidenceSource.OPERATOR_ATTESTED,
})


# --------------------------------------------------------------------------
# States.
# --------------------------------------------------------------------------


class ControllerState(str, Enum):
    CLEAR = "CLEAR"
    SUSPENDED = "SUSPENDED"
    CLEARANCE_PENDING_REVIEW = "CLEARANCE_PENDING_REVIEW"
    PERMANENT_HALT = "PERMANENT_HALT"
    #: Never persisted. Produced when a stored record cannot be read, so a
    #: corrupt row cannot masquerade as a state the controller understands.
    MALFORMED_STATE = "MALFORMED_STATE"


TERMINAL_CONTROLLER_STATES: frozenset[ControllerState] = frozenset({
    ControllerState.PERMANENT_HALT,
})

#: States in which work must not proceed. `MALFORMED_STATE` is here because an
#: unreadable record is not evidence of safety.
HALTED_STATES: frozenset[ControllerState] = frozenset({
    ControllerState.SUSPENDED,
    ControllerState.CLEARANCE_PENDING_REVIEW,
    ControllerState.PERMANENT_HALT,
    ControllerState.MALFORMED_STATE,
})

#: Transition matrix, stated once. `SUSPENDED -> SUSPENDED` is a real edge: a
#: further halt signal re-records without weakening anything.
ALLOWED_CONTROLLER_TRANSITIONS: Mapping[
    ControllerState, frozenset[ControllerState]
] = MappingProxyType({
    ControllerState.CLEAR: frozenset({
        ControllerState.SUSPENDED,
        ControllerState.PERMANENT_HALT,
    }),
    ControllerState.SUSPENDED: frozenset({
        ControllerState.SUSPENDED,
        ControllerState.CLEARANCE_PENDING_REVIEW,
        ControllerState.PERMANENT_HALT,
    }),
    ControllerState.CLEARANCE_PENDING_REVIEW: frozenset({
        ControllerState.CLEAR,
        ControllerState.SUSPENDED,
        ControllerState.PERMANENT_HALT,
    }),
    ControllerState.PERMANENT_HALT: frozenset(),
    ControllerState.MALFORMED_STATE: frozenset(),
})


def controller_transition_is_allowed(
    previous: ControllerState, current: ControllerState
) -> bool:
    if not isinstance(previous, ControllerState) or not isinstance(
        current, ControllerState
    ):
        raise SuspensionControllerError("a transition needs two ControllerState values")
    return current in ALLOWED_CONTROLLER_TRANSITIONS[previous]


# --------------------------------------------------------------------------
# The decided result the scheduler consumes.
# --------------------------------------------------------------------------


class EvidenceClass(str, Enum):
    """How well-founded the current record is. Never a payload, never text."""

    NONE = "NONE"
    #: Raised by this system observing itself; no external attestation exists.
    INTERNAL_OBSERVATION = "INTERNAL_OBSERVATION"
    #: Backed by a verified producer-bound attestation.
    VERIFIED_ATTESTATION = "VERIFIED_ATTESTATION"
    #: Something was offered and failed verification. Never treated as absent.
    UNVERIFIED = "UNVERIFIED"


@dataclass(frozen=True)
class SuspensionDecision:
    """What the scheduler should do. Authorizes nothing.

    `scheduler_action` is deliberately limited to `SUSPEND` and
    `REQUIRE_OPERATOR_REVIEW`. This controller has no vocabulary for letting
    work start — the most it can say is that it is no longer the thing standing
    in the way.
    """

    state: ControllerState
    cause: SuspensionCause
    requires_operator_review: bool
    scheduler_action: SchedulerAction
    evidence_class: EvidenceClass = EvidenceClass.NONE
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise SuspensionControllerError(
                "the suspension controller never authorizes automated action"
            )
        if self.scheduler_action not in (
            SchedulerAction.SUSPEND,
            SchedulerAction.REQUIRE_OPERATOR_REVIEW,
        ):
            # Structural: no result of this module may drive the scheduler
            # into evaluation or completion, whatever a caller constructs.
            raise SuspensionControllerError(
                "a suspension decision may only suspend or require review"
            )

    @property
    def blocks_work(self) -> bool:
        return self.state in HALTED_STATES

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "controller_state": self.state.value,
            "suspension_cause": self.cause.value,
            "evidence_class": self.evidence_class.value,
            "requires_operator_review": (
                "true" if self.requires_operator_review else "false"
            ),
            "scheduler_action": self.scheduler_action.value,
            "permits_automated_action": "false",
        }


# --------------------------------------------------------------------------
# The clearance artifact.
# --------------------------------------------------------------------------

#: How long a clearance may remain valid. A review that outlives the situation
#: it reviewed is not a review of the current situation.
MAXIMUM_CLEARANCE_VALIDITY = timedelta(hours=12)


@dataclass(frozen=True)
class SuspensionClearance:
    """An operator-reviewed artifact authorizing one clearance, once.

    Every field is pinned, and each one alone can refuse. It names the
    controller, the state it expects to find, and the exact event sequence it
    reviewed — so a clearance written against one suspension cannot silently
    clear a different, later one that happened to arrive in between.
    """

    controller_id: str
    expected_state: ControllerState
    expected_sequence: int
    reviewed: bool
    evidence: SignedAttestation
    issued_at: datetime
    expires_at: datetime
    transition_id: str


def validate_clearance(
    clearance: SuspensionClearance,
    *,
    authority: AttestationAuthority,
    controller_id: str,
    current_state: ControllerState,
    current_sequence: int,
    now: datetime,
) -> None:
    """Every check is independent, and all of them must pass.

    Ordered cheapest-and-most-specific first so a refusal names the narrowest
    true reason, but no check is skipped on the strength of another.
    """
    if not isinstance(clearance, SuspensionClearance):
        raise SuspensionControllerError("a clearance artifact is required")
    if not isinstance(clearance.reviewed, bool) or not clearance.reviewed:
        # A bare caller boolean is not sufficient on its own, but its absence
        # is still disqualifying: an unreviewed artifact is not a review.
        raise SuspensionControllerError("clearance was not marked reviewed")
    _validated_identifier(clearance.transition_id, "clearance transition identity")
    if clearance.controller_id != controller_id:
        raise SuspensionControllerError("clearance names a different controller")
    if not isinstance(clearance.expected_state, ControllerState):
        raise SuspensionControllerError("clearance expected state is invalid")
    if clearance.expected_state is not current_state:
        raise SuspensionControllerError("clearance expected a different state")
    if not isinstance(clearance.expected_sequence, int) or isinstance(
        clearance.expected_sequence, bool
    ):
        raise SuspensionControllerError("clearance sequence is not an integer")
    if clearance.expected_sequence != current_sequence:
        # The suspension moved on. A clearance reviewed against an earlier
        # event says nothing about the one now in force.
        raise SuspensionControllerError("clearance is for an outdated event")

    issued = _validated_moment(clearance.issued_at, "clearance issue time")
    expires = _validated_moment(clearance.expires_at, "clearance expiry")
    moment = _validated_moment(now, "evaluation time")
    if expires <= issued:
        raise SuspensionControllerError("clearance expires before it is issued")
    if expires - issued > MAXIMUM_CLEARANCE_VALIDITY:
        raise SuspensionControllerError("clearance validity window is too long")
    if moment < issued:
        raise SuspensionControllerError("clearance is not yet valid")
    if moment >= expires:
        raise SuspensionControllerError("clearance has expired")

    if not isinstance(authority, AttestationAuthority):
        raise SuspensionControllerError("an attestation authority is required")
    if not isinstance(clearance.evidence, SignedAttestation):
        raise SuspensionControllerError("clearance evidence is required")
    if clearance.evidence.fact_name != CLEARANCE_FACT:
        raise SuspensionControllerError("clearance evidence names the wrong fact")
    if not authority.verify(clearance.evidence):
        raise SuspensionControllerError("clearance evidence failed verification")
    attestation = clearance.evidence.attestation
    if attestation.source not in CLEARANCE_SOURCES:
        # `SELF_DECLARED` cannot reach here — the authority refuses to grant it
        # — but `MEASURED` and `TESTED` can, and neither is a review.
        raise SuspensionControllerError("clearance evidence source is not a review")
    if not attestation.satisfied:
        raise SuspensionControllerError("clearance evidence does not affirm review")


# --------------------------------------------------------------------------
# Persistence.
# --------------------------------------------------------------------------

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS controller_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS controllers (
    controller_id      TEXT PRIMARY KEY,
    state              TEXT NOT NULL,
    cause              TEXT NOT NULL,
    evidence_class     TEXT NOT NULL,
    sequence           INTEGER NOT NULL,
    clearance_reviewed INTEGER NOT NULL DEFAULT 0
                       CHECK (clearance_reviewed IN (0, 1)),
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS controller_events (
    transition_id  TEXT PRIMARY KEY,
    controller_id  TEXT NOT NULL REFERENCES controllers(controller_id),
    sequence       INTEGER NOT NULL,
    from_state     TEXT NOT NULL,
    to_state       TEXT NOT NULL,
    cause          TEXT NOT NULL,
    evidence_class TEXT NOT NULL,
    recorded_at    TEXT NOT NULL,
    UNIQUE (controller_id, sequence)
);
CREATE INDEX IF NOT EXISTS controller_events_by_controller
    ON controller_events(controller_id);
"""


def _validated_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SuspensionControllerError(f"a {label} is required")
    if value != value.strip():
        raise SuspensionControllerError(f"{label} has surrounding whitespace")
    if len(value) > 128:
        raise SuspensionControllerError(f"{label} is too long")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise SuspensionControllerError(f"{label} has an unsupported character")
    return value


def _validated_moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise SuspensionControllerError(f"{label} is not a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise SuspensionControllerError(f"{label} has no timezone")
    return value


@dataclass(frozen=True)
class ControllerRecord:
    """One persisted controller. Enums, counts, and timestamps only."""

    controller_id: str
    state: ControllerState
    cause: SuspensionCause
    evidence_class: EvidenceClass
    sequence: int
    clearance_reviewed: bool
    created_at: datetime
    updated_at: datetime

    @property
    def blocks_work(self) -> bool:
        return self.state in HALTED_STATES

    def sanitized_facts(self) -> dict[str, str]:
        """The controller identity is absent by design, as elsewhere in this
        repository: an operator-chosen name is where an account number ends up.
        """
        return {
            "controller_state": self.state.value,
            "suspension_cause": self.cause.value,
            "evidence_class": self.evidence_class.value,
            "clearance_reviewed": "true" if self.clearance_reviewed else "false",
            "sequence_bucket": (
                "none" if self.sequence == 0
                else "one" if self.sequence == 1
                else "many"
            ),
            "permits_automated_action": "false",
        }


@dataclass(frozen=True)
class SuspensionTransitionReceipt:
    """Proof that one controller transition was persisted, and nothing more.

    Frozen, allowlisted, and deliberately incapable of carrying a connection,
    cursor, statement, database path, or raw row. A collaborator that had to
    read `_connection` to learn whether its write landed would acquire, in the
    same gesture, the ability to make writes nobody reviewed.

    Absence is `None` rather than a receipt saying no — a falsy receipt is the
    kind of thing a caller forgets to check.
    """

    transition_id: str
    controller_id: str
    from_state: ControllerState
    to_state: ControllerState
    cause: SuspensionCause
    evidence_class: EvidenceClass
    sequence: int
    persisted: bool = True

    def __post_init__(self) -> None:
        if not self.persisted:
            raise SuspensionControllerError(
                "a receipt exists only for a persisted transition"
            )
        for value in (self.from_state, self.to_state):
            if not isinstance(value, ControllerState):
                raise SuspensionControllerError("a receipt needs ControllerState values")
        if not isinstance(self.cause, SuspensionCause):
            raise SuspensionControllerError("a receipt needs a SuspensionCause")
        if not isinstance(self.evidence_class, EvidenceClass):
            raise SuspensionControllerError("a receipt needs an EvidenceClass")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise SuspensionControllerError("receipt sequence must be an int")
        if self.sequence < 1:
            raise SuspensionControllerError("receipt sequence must be positive")

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "suspension_cause": self.cause.value,
            "evidence_class": self.evidence_class.value,
            "transition_persisted": "true",
            "permits_automated_action": "false",
        }


class DurableSuspensionController:
    """Persistent suspension state that outlives the process.

    Uniqueness is the database's job. A transition identity is a primary key,
    so a duplicate fails at the write rather than in a check that has a window
    before it.
    """

    def __init__(self, database_path: Path) -> None:
        if not isinstance(database_path, Path):
            raise SuspensionControllerError("a database path is required")
        if is_repository_data_path(database_path):
            raise SuspensionControllerError(
                "the controller may not be opened inside repository data"
            )
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            os.fspath(database_path), isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        try:
            self._connection.executescript(_SCHEMA)
            self._assert_schema_version()
        except Exception:
            self._connection.close()
            raise

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM controller_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO controller_meta (key, value) VALUES "
                "('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            raise SuspensionControllerError("controller schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableSuspensionController":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _rollback_quietly(self) -> None:
        """A failed rollback must not replace the exception that caused it."""
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    # -- lifecycle --------------------------------------------------------

    def register(self, controller_id: str, *, now: datetime) -> ControllerRecord:
        """Create a controller in CLEAR. A used identity is never re-created."""
        _validated_identifier(controller_id, "controller identity")
        stamp = _validated_moment(now, "creation time").isoformat()
        try:
            self._connection.execute(
                "INSERT INTO controllers (controller_id, state, cause, "
                " evidence_class, sequence, clearance_reviewed, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 0, 0, ?, ?)",
                (
                    controller_id,
                    ControllerState.CLEAR.value,
                    SuspensionCause.NONE.value,
                    EvidenceClass.NONE.value,
                    stamp,
                    stamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SuspensionControllerError(
                "controller identity has already been used"
            ) from exc
        return self.load(controller_id)

    def load(self, controller_id: str) -> ControllerRecord:
        """Read a controller, refusing anything the schema cannot vouch for."""
        _validated_identifier(controller_id, "controller identity")
        rows = self._connection.execute(
            "SELECT controller_id, state, cause, evidence_class, sequence, "
            "       clearance_reviewed, created_at, updated_at "
            "FROM controllers WHERE controller_id = ?",
            (controller_id,),
        ).fetchall()
        if not rows:
            raise SuspensionControllerError("controller is not registered")
        if len(rows) > 1:
            # Impossible through the primary key, but a corrupted or
            # hand-edited file could produce it, and picking one would be
            # choosing which history to believe.
            raise SuspensionControllerError("controller records conflict")
        return self._row_to_record(rows[0])

    @staticmethod
    def _row_to_record(row: Any) -> ControllerRecord:
        (
            stored_id,
            state,
            cause,
            evidence_class,
            sequence,
            clearance_reviewed,
            created_at,
            updated_at,
        ) = row
        try:
            parsed_state = ControllerState(state)
            parsed_cause = SuspensionCause(cause)
            parsed_evidence = EvidenceClass(evidence_class)
        except ValueError as exc:
            raise SuspensionControllerError(
                "persisted controller value is not recognized"
            ) from exc
        if parsed_state is ControllerState.MALFORMED_STATE:
            # Never a legitimate stored value. Its presence means something
            # wrote a derived state into the record.
            raise SuspensionControllerError("persisted state is not storable")
        for label, value in (("creation time", created_at), ("update time", updated_at)):
            if not isinstance(value, str) or not value:
                raise SuspensionControllerError(f"persisted {label} is missing")
        try:
            parsed_created = datetime.fromisoformat(created_at)
            parsed_updated = datetime.fromisoformat(updated_at)
        except ValueError as exc:
            raise SuspensionControllerError("persisted timestamp is malformed") from exc
        if parsed_created.tzinfo is None or parsed_updated.tzinfo is None:
            raise SuspensionControllerError("persisted timestamp has no timezone")
        if not isinstance(sequence, int) or sequence < 0:
            raise SuspensionControllerError("persisted sequence is malformed")
        if clearance_reviewed not in (0, 1):
            raise SuspensionControllerError("persisted clearance flag is malformed")
        if parsed_state is ControllerState.CLEAR and (
            parsed_cause is not SuspensionCause.NONE
        ):
            raise SuspensionControllerError("a clear controller cannot carry a cause")
        if parsed_state is not ControllerState.CLEAR and (
            parsed_cause is SuspensionCause.NONE
        ):
            raise SuspensionControllerError("a halted controller needs a cause")
        return ControllerRecord(
            controller_id=stored_id,
            state=parsed_state,
            cause=parsed_cause,
            evidence_class=parsed_evidence,
            sequence=sequence,
            clearance_reviewed=bool(clearance_reviewed),
            created_at=parsed_created,
            updated_at=parsed_updated,
        )

    # -- raising a suspension --------------------------------------------

    def suspend(
        self,
        controller_id: str,
        *,
        cause: SuspensionCause,
        transition_id: str,
        now: datetime,
        evidence: SignedAttestation | None = None,
        authority: AttestationAuthority | None = None,
    ) -> ControllerRecord:
        """Halt, or strengthen an existing halt. Never weakens one.

        Attestation is optional here and mandatory for clearing. Refusing to
        stop because the paperwork was incomplete would be the wrong failure;
        refusing to start for the same reason is the right one.

        An unverifiable attestation does not block the suspension — it is
        recorded as `UNVERIFIED`, which is strictly worse than no evidence and
        is never read as absent.
        """
        _validated_identifier(transition_id, "transition identity")
        moment = _validated_moment(now, "suspension time")
        if not isinstance(cause, SuspensionCause) or cause is SuspensionCause.NONE:
            raise SuspensionControllerError("a real suspension cause is required")
        record = self.load(controller_id)

        evidence_class = self._classify_evidence(cause, evidence, authority)
        target = (
            ControllerState.PERMANENT_HALT
            if cause in PERMANENT_HALT_CAUSES
            else ControllerState.SUSPENDED
        )
        if record.state is ControllerState.PERMANENT_HALT:
            raise SuspensionControllerError("a permanently halted controller is final")
        if not controller_transition_is_allowed(record.state, target):
            raise SuspensionControllerError("transition is not permitted")

        # Replace-or-augment: a strictly more severe cause takes over; anything
        # else is recorded in history but leaves the active cause standing. A
        # weaker later signal must never look like the reason work stopped.
        if CAUSE_SEVERITY[cause] > CAUSE_SEVERITY[record.cause]:
            effective_cause = cause
            effective_evidence = evidence_class
        else:
            effective_cause = record.cause
            effective_evidence = record.evidence_class

        return self._write_transition(
            record,
            target=target,
            cause=effective_cause,
            evidence_class=effective_evidence,
            event_cause=cause,
            event_evidence=evidence_class,
            transition_id=transition_id,
            moment=moment,
            clearance_reviewed=False,
        )

    def _classify_evidence(
        self,
        cause: SuspensionCause,
        evidence: SignedAttestation | None,
        authority: AttestationAuthority | None,
    ) -> EvidenceClass:
        if evidence is None:
            return EvidenceClass.INTERNAL_OBSERVATION
        if authority is None:
            return EvidenceClass.UNVERIFIED
        expected_fact = CAUSE_EVIDENCE_FACTS.get(cause)
        if expected_fact is None or evidence.fact_name != expected_fact:
            return EvidenceClass.UNVERIFIED
        if not authority.verify(evidence):
            return EvidenceClass.UNVERIFIED
        return EvidenceClass.VERIFIED_ATTESTATION

    def escalate_to_permanent_halt(
        self, controller_id: str, *, transition_id: str, now: datetime
    ) -> ControllerRecord:
        """Make the halt irreversible. There is no path back."""
        return self.suspend(
            controller_id,
            cause=SuspensionCause.INTERNAL_INVARIANT_FAILURE,
            transition_id=transition_id,
            now=now,
        )

    # -- clearing ---------------------------------------------------------

    def apply_clearance(
        self,
        controller_id: str,
        *,
        clearance: SuspensionClearance,
        authority: AttestationAuthority,
        now: datetime,
    ) -> ControllerRecord:
        """Move a suspension into pending review. Never into CLEAR.

        A single artifact that took the system from halted to running would be
        one forged or replayed document away from doing exactly that, so
        returning to CLEAR is a separate attested act.
        """
        moment = _validated_moment(now, "clearance time")
        record = self.load(controller_id)
        if record.state is ControllerState.PERMANENT_HALT:
            raise SuspensionControllerError(
                "a permanent halt has no clearance path"
            )
        if record.state is not ControllerState.SUSPENDED:
            raise SuspensionControllerError("controller is not suspended")
        validate_clearance(
            clearance,
            authority=authority,
            controller_id=controller_id,
            current_state=record.state,
            current_sequence=record.sequence,
            now=moment,
        )
        return self._write_transition(
            record,
            target=ControllerState.CLEARANCE_PENDING_REVIEW,
            cause=record.cause,
            evidence_class=EvidenceClass.VERIFIED_ATTESTATION,
            event_cause=record.cause,
            event_evidence=EvidenceClass.VERIFIED_ATTESTATION,
            transition_id=clearance.transition_id,
            moment=moment,
            clearance_reviewed=True,
        )

    def complete_review(
        self,
        controller_id: str,
        *,
        clearance: SuspensionClearance,
        authority: AttestationAuthority,
        now: datetime,
    ) -> ControllerRecord:
        """The second step. Only this returns a controller to CLEAR."""
        moment = _validated_moment(now, "review completion time")
        record = self.load(controller_id)
        if record.state is not ControllerState.CLEARANCE_PENDING_REVIEW:
            raise SuspensionControllerError("controller is not pending review")
        validate_clearance(
            clearance,
            authority=authority,
            controller_id=controller_id,
            current_state=record.state,
            current_sequence=record.sequence,
            now=moment,
        )
        return self._write_transition(
            record,
            target=ControllerState.CLEAR,
            cause=SuspensionCause.NONE,
            evidence_class=EvidenceClass.NONE,
            event_cause=record.cause,
            event_evidence=EvidenceClass.VERIFIED_ATTESTATION,
            transition_id=clearance.transition_id,
            moment=moment,
            clearance_reviewed=False,
        )

    # -- the write --------------------------------------------------------

    def _write_transition(
        self,
        record: ControllerRecord,
        *,
        target: ControllerState,
        cause: SuspensionCause,
        evidence_class: EvidenceClass,
        event_cause: SuspensionCause,
        event_evidence: EvidenceClass,
        transition_id: str,
        moment: datetime,
        clearance_reviewed: bool,
    ) -> ControllerRecord:
        if not controller_transition_is_allowed(record.state, target):
            raise SuspensionControllerError("transition is not permitted")
        stamp = moment.isoformat()
        sequence = record.sequence + 1
        # BEGIN outside the try: if it fails there is no transaction, and
        # rolling back anyway raises a second error that hides the first.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT INTO controller_events (transition_id, controller_id, "
                " sequence, from_state, to_state, cause, evidence_class, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_id,
                    record.controller_id,
                    sequence,
                    record.state.value,
                    target.value,
                    event_cause.value,
                    event_evidence.value,
                    stamp,
                ),
            )
            self._connection.execute(
                "UPDATE controllers SET state = ?, cause = ?, evidence_class = ?, "
                "       sequence = ?, clearance_reviewed = ?, updated_at = ? "
                "WHERE controller_id = ?",
                (
                    target.value,
                    cause.value,
                    evidence_class.value,
                    sequence,
                    1 if clearance_reviewed else 0,
                    stamp,
                    record.controller_id,
                ),
            )
        except sqlite3.IntegrityError as exc:
            self._rollback_quietly()
            # Two different constraints can land here and they mean different
            # things. Attributing a sequence collision to a duplicate identity
            # would send an operator looking for a replay that never happened.
            raise SuspensionControllerError(
                self._integrity_reason(transition_id, record.controller_id, sequence)
            ) from exc
        except Exception:
            self._rollback_quietly()
            raise
        self._connection.execute("COMMIT")
        return self.load(record.controller_id)

    def _integrity_reason(
        self, transition_id: str, controller_id: str, sequence: int
    ) -> str:
        """Name the constraint that actually refused the write."""
        spent = self._connection.execute(
            "SELECT 1 FROM controller_events WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
        if spent is not None:
            return "transition identity has already been used"
        collided = self._connection.execute(
            "SELECT 1 FROM controller_events WHERE controller_id = ? AND sequence = ?",
            (controller_id, sequence),
        ).fetchone()
        if collided is not None:
            # Another writer advanced this controller between the read and the
            # write. Refusing is correct; the caller must re-read and decide
            # again against the state that actually exists now.
            return "controller advanced concurrently; re-read and retry"
        return "the transition violated a persistence constraint"

    # -- restart and assessment -------------------------------------------

    def recover_on_restart(self, controller_id: str) -> ControllerRecord:
        """Read the persisted state back, unchanged.

        There is deliberately nothing to do here. Suspension is a row, so a
        restart preserves it by not touching it — and this method exists so
        that "restart changes nothing" is a tested claim rather than an
        absence somebody has to notice.
        """
        return self.load(controller_id)

    def assess(self, controller_id: str) -> SuspensionDecision:
        """The decided result the scheduler consumes.

        An unreadable record yields `MALFORMED_STATE` with an internal-invariant
        cause rather than an exception: the scheduler needs an answer it can act
        on, and the only safe answer to "I cannot read my own state" is stop.
        """
        try:
            record = self.load(controller_id)
        except SuspensionControllerError:
            return SuspensionDecision(
                state=ControllerState.MALFORMED_STATE,
                cause=SuspensionCause.INTERNAL_INVARIANT_FAILURE,
                requires_operator_review=True,
                scheduler_action=SchedulerAction.SUSPEND,
                evidence_class=EvidenceClass.UNVERIFIED,
            )
        if record.state is ControllerState.CLEAR:
            return SuspensionDecision(
                state=ControllerState.CLEAR,
                cause=SuspensionCause.NONE,
                requires_operator_review=False,
                scheduler_action=SchedulerAction.REQUIRE_OPERATOR_REVIEW,
                evidence_class=record.evidence_class,
            )
        if record.state is ControllerState.CLEARANCE_PENDING_REVIEW:
            return SuspensionDecision(
                state=record.state,
                cause=record.cause,
                requires_operator_review=True,
                scheduler_action=SchedulerAction.REQUIRE_OPERATOR_REVIEW,
                evidence_class=record.evidence_class,
            )
        return SuspensionDecision(
            state=record.state,
            cause=record.cause,
            requires_operator_review=True,
            scheduler_action=SchedulerAction.SUSPEND,
            evidence_class=record.evidence_class,
        )

    # -- reporting --------------------------------------------------------

    def inspect_transition(
        self, transition_id: str
    ) -> "SuspensionTransitionReceipt | None":
        """Whether a controller transition landed, and what it did. Read-only.

        The public answer to "did my write persist?", so a collaborator never
        needs this object's database to find out.
        """
        _validated_identifier(transition_id, "transition identity")
        row = self._connection.execute(
            "SELECT controller_id, from_state, to_state, cause, evidence_class, "
            "       sequence "
            "FROM controller_events WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
        if row is None:
            return None
        controller_id, from_state, to_state, cause, evidence_class, sequence = row
        try:
            parsed_from = ControllerState(from_state)
            parsed_to = ControllerState(to_state)
            parsed_cause = SuspensionCause(cause)
            parsed_evidence = EvidenceClass(evidence_class)
        except ValueError as exc:
            raise SuspensionControllerError(
                "persisted transition value is not recognized"
            ) from exc
        if not isinstance(sequence, int) or sequence < 1:
            raise SuspensionControllerError("persisted transition sequence is malformed")
        return SuspensionTransitionReceipt(
            transition_id=transition_id,
            controller_id=controller_id,
            from_state=parsed_from,
            to_state=parsed_to,
            cause=parsed_cause,
            evidence_class=parsed_evidence,
            sequence=sequence,
        )

    def event_count(self, controller_id: str) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM controller_events WHERE controller_id = ?",
            (controller_id,),
        ).fetchone()
        return int(row[0])

    @property
    def total_event_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM controller_events"
        ).fetchone()
        return int(row[0])


__all__ = [
    "ALLOWED_CONTROLLER_TRANSITIONS",
    "CAUSE_EVIDENCE_FACTS",
    "CAUSE_SEVERITY",
    "CLEARANCE_FACT",
    "CLEARANCE_SOURCES",
    "HALTED_STATES",
    "MAXIMUM_CLEARANCE_VALIDITY",
    "PERMANENT_HALT_CAUSES",
    "REPOSITORY_DATA_DIRECTORY",
    "SCHEMA_VERSION",
    "TERMINAL_CONTROLLER_STATES",
    "ControllerRecord",
    "ControllerState",
    "DurableSuspensionController",
    "EvidenceClass",
    "SuspensionCause",
    "SuspensionClearance",
    "SuspensionControllerError",
    "SuspensionDecision",
    "SuspensionTransitionReceipt",
    "controller_transition_is_allowed",
    "is_repository_data_path",
    "validate_clearance",
]
