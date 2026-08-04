"""Durable, calendar-driven scheduler and run-state machine (Milestone 10).

This module decides **whether an offline evaluation window is eligible**, and
records that decision durably. It does not trade, and it cannot: there is no
broker session, no order path, no manifest, no credential access, and no
network call anywhere in it. Every result it produces carries
``permits_automated_action=False``, and constructing one with ``True`` raises.

Three properties are structural rather than promised.

**Nothing here reads a clock.** Every time value is injected. A scheduler that
calls ``datetime.now()`` cannot be tested at a holiday boundary, a DST fold, or
a stale record without waiting for the calendar to reach one, so in practice it
is tested at none of them. A test parses this file and asserts the absence of
``datetime.now``, ``datetime.utcnow``, ``time.time``, and ``time.monotonic``.

**Restart never resumes into an action-capable state.** A process that died
mid-evaluation cannot know whether the evaluation finished, and the difference
between "it completed" and "it died just before completing" is exactly the
difference that matters. So a run found in ``RUNNING_OFFLINE_EVALUATION`` after
a restart becomes ``AMBIGUOUS_RESTART`` — terminal — rather than continuing.
Every other non-terminal state requires explicit re-evaluation before it may
advance.

**Suspension outlives the process that applied it.** It is a row, not a flag in
memory, and reopening the database does not clear it. Clearing requires an
operator-reviewed clearance carrying its own transition identity, and lands in
``SUPERVISED_REVIEW_REQUIRED`` rather than anything runnable — a suspension that
was worth applying is worth a human look before work resumes.

## What this module deliberately does not do

It consumes already-decided facts and re-derives none of them. Calendar
classification comes from ``src/exchange_calendar.py``; risk limits,
reconciliation outcomes, monitoring delivery, attestation verification, and gate
evidence are all supplied by their own reviewed modules as plain values. A
second implementation of any of those would be a second opinion nobody asked
for, and the two would eventually disagree.

It also does not authorize anything. It never populates
``ACTIVATION_APPROVALS``, never approves a protection state, and its most
permissive answer is "the offline checks may run now".
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.exchange_calendar import (
    EASTERN,
    CalendarSession,
    ClassificationReason,
    SessionClassification,
    classify as classify_calendar_session,
)


class SchedulerError(RuntimeError):
    """Any scheduler refusal. Never carries broker, account, or operator data."""


# --------------------------------------------------------------------------
# States.
# --------------------------------------------------------------------------


class RunState(str, Enum):
    """Every state a scheduled run can hold.

    None of these implies a broker call happened. ``COMPLETED_NO_ACTION`` is
    named the way it is for that reason: the only completion this scheduler can
    reach is one in which nothing was executed.
    """

    #: Persisted, nothing evaluated yet.
    CREATED = "CREATED"
    #: The calendar says this is not an eligible window yet. Waiting is normal.
    WAITING_FOR_SESSION = "WAITING_FOR_SESSION"
    #: Preconditions are being assessed from already-decided facts.
    PRECONDITION_CHECK = "PRECONDITION_CHECK"
    #: Offline evaluation is in flight. **The only state a crash makes
    #: ambiguous**, because its completion cannot be inferred after the fact.
    RUNNING_OFFLINE_EVALUATION = "RUNNING_OFFLINE_EVALUATION"
    #: Offline work finished cleanly and its results await a human. The normal
    #: path, and distinct from the one below.
    READY_FOR_SUPERVISED_REVIEW = "READY_FOR_SUPERVISED_REVIEW"
    #: The scheduler stopped early because something needs a human before it
    #: will do more. "We stopped, look at this" — not "we finished, look at
    #: this". The two call for different operator responses.
    SUPERVISED_REVIEW_REQUIRED = "SUPERVISED_REVIEW_REQUIRED"
    #: Durably halted. Survives restart; cleared only by operator review.
    SUSPENDED = "SUSPENDED"
    #: Finished, with nothing executed. Terminal.
    COMPLETED_NO_ACTION = "COMPLETED_NO_ACTION"
    #: Refused for a stated reason. Terminal.
    FAILED_CLOSED = "FAILED_CLOSED"
    #: A restart found this run mid-evaluation. Terminal, and never resumed.
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    #: The window this run belonged to has passed. Terminal.
    EXPIRED = "EXPIRED"


#: States no transition may leave, ever. `SUSPENDED` is deliberately absent —
#: it is durable and restart-proof, but an operator can clear it, and a state
#: nobody can ever leave would make suspension indistinguishable from failure.
TERMINAL_STATES: frozenset[RunState] = frozenset({
    RunState.COMPLETED_NO_ACTION,
    RunState.FAILED_CLOSED,
    RunState.AMBIGUOUS_RESTART,
    RunState.EXPIRED,
})

#: States that never resume automatically after a restart. This is the wider
#: set: it includes `SUSPENDED`, because reopening a database must not be a way
#: to un-suspend anything.
RESTART_HALTED_STATES: frozenset[RunState] = TERMINAL_STATES | {RunState.SUSPENDED}

#: States a restart leaves in place but which may not advance until they have
#: been explicitly re-evaluated. Their inputs were computed before the crash
#: and nothing here may assume they still hold.
RESTART_REEVALUATION_STATES: frozenset[RunState] = frozenset({
    RunState.CREATED,
    RunState.PRECONDITION_CHECK,
    RunState.READY_FOR_SUPERVISED_REVIEW,
    RunState.SUPERVISED_REVIEW_REQUIRED,
})


#: The transition matrix, stated once and consulted rather than reimplemented
#: per call site. Terminal states map to an empty set, which is what makes
#: "no exit from terminal" a property of the data instead of a special case.
#:
#: Note what is absent everywhere: no state transitions to
#: `RUNNING_OFFLINE_EVALUATION` except `PRECONDITION_CHECK`, so evaluation
#: cannot be entered without preconditions having been assessed first.
ALLOWED_TRANSITIONS: Mapping[RunState, frozenset[RunState]] = MappingProxyType({
    RunState.CREATED: frozenset({
        RunState.WAITING_FOR_SESSION,
        RunState.PRECONDITION_CHECK,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.EXPIRED,
    }),
    RunState.WAITING_FOR_SESSION: frozenset({
        RunState.PRECONDITION_CHECK,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.EXPIRED,
        RunState.COMPLETED_NO_ACTION,
    }),
    RunState.PRECONDITION_CHECK: frozenset({
        RunState.RUNNING_OFFLINE_EVALUATION,
        RunState.SUPERVISED_REVIEW_REQUIRED,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.EXPIRED,
    }),
    RunState.RUNNING_OFFLINE_EVALUATION: frozenset({
        RunState.READY_FOR_SUPERVISED_REVIEW,
        RunState.COMPLETED_NO_ACTION,
        RunState.SUPERVISED_REVIEW_REQUIRED,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.AMBIGUOUS_RESTART,
    }),
    RunState.READY_FOR_SUPERVISED_REVIEW: frozenset({
        RunState.COMPLETED_NO_ACTION,
        RunState.SUPERVISED_REVIEW_REQUIRED,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.EXPIRED,
    }),
    RunState.SUPERVISED_REVIEW_REQUIRED: frozenset({
        RunState.COMPLETED_NO_ACTION,
        RunState.SUSPENDED,
        RunState.FAILED_CLOSED,
        RunState.EXPIRED,
    }),
    # Only the operator-clearance path leaves suspension, and it lands in
    # review rather than anywhere runnable.
    RunState.SUSPENDED: frozenset({RunState.SUPERVISED_REVIEW_REQUIRED}),
    RunState.COMPLETED_NO_ACTION: frozenset(),
    RunState.FAILED_CLOSED: frozenset(),
    RunState.AMBIGUOUS_RESTART: frozenset(),
    RunState.EXPIRED: frozenset(),
})


#: Transitions that run the lifecycle backwards. Listed rather than derived
#: from an ordering, because the lifecycle branches and a derived rule would be
#: wrong at the branches in both directions.
BACKWARD_TRANSITIONS: frozenset[tuple[RunState, RunState]] = frozenset({
    (RunState.WAITING_FOR_SESSION, RunState.CREATED),
    (RunState.PRECONDITION_CHECK, RunState.CREATED),
    (RunState.PRECONDITION_CHECK, RunState.WAITING_FOR_SESSION),
    (RunState.RUNNING_OFFLINE_EVALUATION, RunState.CREATED),
    (RunState.RUNNING_OFFLINE_EVALUATION, RunState.WAITING_FOR_SESSION),
    (RunState.RUNNING_OFFLINE_EVALUATION, RunState.PRECONDITION_CHECK),
    (RunState.READY_FOR_SUPERVISED_REVIEW, RunState.CREATED),
    (RunState.READY_FOR_SUPERVISED_REVIEW, RunState.WAITING_FOR_SESSION),
    (RunState.READY_FOR_SUPERVISED_REVIEW, RunState.PRECONDITION_CHECK),
    (RunState.READY_FOR_SUPERVISED_REVIEW, RunState.RUNNING_OFFLINE_EVALUATION),
    (RunState.SUPERVISED_REVIEW_REQUIRED, RunState.CREATED),
    (RunState.SUPERVISED_REVIEW_REQUIRED, RunState.WAITING_FOR_SESSION),
    (RunState.SUPERVISED_REVIEW_REQUIRED, RunState.PRECONDITION_CHECK),
    (RunState.SUPERVISED_REVIEW_REQUIRED, RunState.RUNNING_OFFLINE_EVALUATION),
    (RunState.SUSPENDED, RunState.CREATED),
    (RunState.SUSPENDED, RunState.WAITING_FOR_SESSION),
    (RunState.SUSPENDED, RunState.PRECONDITION_CHECK),
    (RunState.SUSPENDED, RunState.RUNNING_OFFLINE_EVALUATION),
    (RunState.SUSPENDED, RunState.READY_FOR_SUPERVISED_REVIEW),
})


def transition_is_allowed(previous: RunState, current: RunState) -> bool:
    if not isinstance(previous, RunState) or not isinstance(current, RunState):
        raise SchedulerError("a transition needs two RunState values")
    return current in ALLOWED_TRANSITIONS[previous]


def transition_is_backward(previous: RunState, current: RunState) -> bool:
    return (previous, current) in BACKWARD_TRANSITIONS


# --------------------------------------------------------------------------
# Results. Never an authorization.
# --------------------------------------------------------------------------


class SchedulerAction(str, Enum):
    """What the caller should do. The most permissive is "run offline checks"."""

    WAIT = "WAIT"
    REQUIRE_OPERATOR_REVIEW = "REQUIRE_OPERATOR_REVIEW"
    RUN_OFFLINE_CHECKS = "RUN_OFFLINE_CHECKS"
    SUSPEND = "SUSPEND"
    COMPLETE_NO_ACTION = "COMPLETE_NO_ACTION"
    FAIL_CLOSED = "FAIL_CLOSED"


class SchedulerReason(str, Enum):
    """Fixed reason codes. Never free text, never an exception message."""

    OK = "OK"
    SAFETY_HALT_ACTIVE = "SAFETY_HALT_ACTIVE"
    RUN_IS_SUSPENDED = "RUN_IS_SUSPENDED"
    RUN_IS_TERMINAL = "RUN_IS_TERMINAL"
    REEVALUATION_REQUIRED = "REEVALUATION_REQUIRED"
    CALENDAR_SESSION_UNKNOWN = "CALENDAR_SESSION_UNKNOWN"
    CALENDAR_OUTSIDE_COVERAGE = "CALENDAR_OUTSIDE_COVERAGE"
    CALENDAR_EXPIRED = "CALENDAR_EXPIRED"
    MARKET_CLOSED = "MARKET_CLOSED"
    PRE_MARKET_SESSION = "PRE_MARKET_SESSION"
    POST_MARKET_SESSION = "POST_MARKET_SESSION"
    REGULAR_SESSION = "REGULAR_SESSION"
    SCHEDULE_RECORD_STALE = "SCHEDULE_RECORD_STALE"
    SCHEDULE_RECORD_FUTURE_DATED = "SCHEDULE_RECORD_FUTURE_DATED"
    UNRESOLVED_OPERATOR_ACTION = "UNRESOLVED_OPERATOR_ACTION"
    MONITORING_DELIVERY_UNCONFIRMED = "MONITORING_DELIVERY_UNCONFIRMED"
    RECONCILIATION_UNRESOLVED = "RECONCILIATION_UNRESOLVED"
    RISK_LIMITS_NOT_SATISFIED = "RISK_LIMITS_NOT_SATISFIED"
    ATTESTATION_NOT_VERIFIED = "ATTESTATION_NOT_VERIFIED"
    GATE_CLOSED = "GATE_CLOSED"


#: Reasons that mean "a human must look", as opposed to "wait and try later".
REVIEW_REASONS: frozenset[SchedulerReason] = frozenset({
    SchedulerReason.UNRESOLVED_OPERATOR_ACTION,
    SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED,
    SchedulerReason.RECONCILIATION_UNRESOLVED,
    SchedulerReason.RISK_LIMITS_NOT_SATISFIED,
    SchedulerReason.ATTESTATION_NOT_VERIFIED,
    SchedulerReason.GATE_CLOSED,
})


@dataclass(frozen=True)
class SchedulerDecision:
    """One scheduling answer. It authorizes nothing and triggers nothing."""

    action: SchedulerAction
    reason: SchedulerReason
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        # Structural, not conventional. Nothing in this module may produce a
        # result that claims authority it does not have.
        if self.permits_automated_action:
            raise SchedulerError("the scheduler never authorizes automated action")

    @property
    def is_eligible_for_offline_evaluation(self) -> bool:
        return self.action is SchedulerAction.RUN_OFFLINE_CHECKS

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "scheduler_action": self.action.value,
            "scheduler_reason": self.reason.value,
            "permits_automated_action": "false",
        }


# --------------------------------------------------------------------------
# Already-decided facts, supplied by their own reviewed modules.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class UpstreamFacts:
    """Conclusions this scheduler consumes but never computes.

    Every field is a plain bool that some other module already decided. None
    of the logic behind them is reimplemented here: a second implementation of
    a risk limit or a reconciliation rule is a second opinion nobody asked for,
    and the two would eventually disagree.

    Every default is the unsafe reading. A caller that forgets to supply a
    fact gets the answer that stops work, not the one that permits it.
    """

    #: `execution_gates` — whether the supervised gate would open. Today this
    #: is always false, because `ACTIVATION_APPROVALS` is empty.
    gate_is_open: bool = False
    #: `risk_limits` — whether the evaluated limits were satisfied.
    risk_limits_satisfied: bool = False
    #: `offline_reconciliation` — whether recovery reached a resolved outcome.
    reconciliation_resolved: bool = False
    #: `operational_monitor` — whether every required alert was confirmed
    #: delivered. An unconfirmed alert is not silence; it is an unknown.
    monitoring_delivery_confirmed: bool = False
    #: `attestation_authority` / `producer_key_registry` — whether the evidence
    #: behind the above was cryptographically verified rather than trusted.
    attestations_verified: bool = False
    #: Any operator action still outstanding from an earlier run.
    unresolved_operator_action: bool = True
    #: A kill switch or safety halt. Dominates everything below it.
    safety_halt_active: bool = False

    def first_blocking_reason(self) -> SchedulerReason | None:
        """The narrowest true reason work must not proceed, or None.

        Ordered most-serious first so a refusal names the thing an operator
        should look at, rather than whichever check happened to run first.
        """
        if self.safety_halt_active:
            return SchedulerReason.SAFETY_HALT_ACTIVE
        if self.unresolved_operator_action:
            return SchedulerReason.UNRESOLVED_OPERATOR_ACTION
        if not self.attestations_verified:
            return SchedulerReason.ATTESTATION_NOT_VERIFIED
        if not self.reconciliation_resolved:
            return SchedulerReason.RECONCILIATION_UNRESOLVED
        if not self.risk_limits_satisfied:
            return SchedulerReason.RISK_LIMITS_NOT_SATISFIED
        if not self.monitoring_delivery_confirmed:
            return SchedulerReason.MONITORING_DELIVERY_UNCONFIRMED
        if not self.gate_is_open:
            return SchedulerReason.GATE_CLOSED
        return None


# --------------------------------------------------------------------------
# Calendar eligibility. Classification is delegated, never restated.
# --------------------------------------------------------------------------

#: Calendar reasons that mean the table could not answer at all, mapped to the
#: scheduler reason a reviewer will act on. Every one fails closed.
_UNRESOLVABLE_CALENDAR_REASONS: Mapping[ClassificationReason, SchedulerReason] = (
    MappingProxyType({
        ClassificationReason.DATE_OUTSIDE_SUPPORTED_RANGE: (
            SchedulerReason.CALENDAR_OUTSIDE_COVERAGE
        ),
        ClassificationReason.SCHEDULE_EXPIRED: SchedulerReason.CALENDAR_EXPIRED,
        ClassificationReason.SCHEDULE_MALFORMED: (
            SchedulerReason.CALENDAR_SESSION_UNKNOWN
        ),
        ClassificationReason.NAIVE_TIMESTAMP: SchedulerReason.CALENDAR_SESSION_UNKNOWN,
        ClassificationReason.AMBIGUOUS_LOCAL_TIME: (
            SchedulerReason.CALENDAR_SESSION_UNKNOWN
        ),
        ClassificationReason.NOT_A_DATETIME: SchedulerReason.CALENDAR_SESSION_UNKNOWN,
    })
)


@dataclass(frozen=True)
class CalendarEligibility:
    """Whether the calendar permits an offline evaluation window right now."""

    eligible: bool
    reason: SchedulerReason
    session: CalendarSession

    def __post_init__(self) -> None:
        if self.eligible and self.session is not CalendarSession.REGULAR:
            # The one invariant worth enforcing on the object: only a confirmed
            # regular session is ever eligible, whatever a caller constructs.
            raise SchedulerError("only a regular session is eligible")


def assess_calendar(
    moment: datetime, *, today: date | None = None
) -> CalendarEligibility:
    """Delegate to the authoritative calendar and translate, never re-derive.

    `UNKNOWN` in any of its forms — unsupported date, expired table, malformed
    schedule, naive timestamp, ambiguous DST fold, non-datetime — is a refusal.
    It is never quietly treated as an open market.

    Pre-market, post-market, and closed are all ineligible but *waitable*: they
    are ordinary conditions, distinguished from one another so a reviewer can
    tell why nothing ran.
    """
    classification: SessionClassification = classify_calendar_session(
        moment, today=today
    )
    if classification.session is CalendarSession.UNKNOWN:
        return CalendarEligibility(
            eligible=False,
            reason=_UNRESOLVABLE_CALENDAR_REASONS.get(
                classification.reason, SchedulerReason.CALENDAR_SESSION_UNKNOWN
            ),
            session=CalendarSession.UNKNOWN,
        )
    if classification.session is CalendarSession.REGULAR:
        # `is_regular_session` is the calendar's own load-bearing boolean. If
        # it and the session name ever disagreed, the safe reading wins.
        if not classification.is_regular_session:
            return CalendarEligibility(
                False, SchedulerReason.CALENDAR_SESSION_UNKNOWN, CalendarSession.UNKNOWN
            )
        return CalendarEligibility(
            True, SchedulerReason.REGULAR_SESSION, CalendarSession.REGULAR
        )
    if classification.session is CalendarSession.PRE_MARKET:
        return CalendarEligibility(
            False, SchedulerReason.PRE_MARKET_SESSION, CalendarSession.PRE_MARKET
        )
    if classification.session is CalendarSession.POST_MARKET:
        return CalendarEligibility(
            False, SchedulerReason.POST_MARKET_SESSION, CalendarSession.POST_MARKET
        )
    return CalendarEligibility(
        False, SchedulerReason.MARKET_CLOSED, CalendarSession.CLOSED
    )


# --------------------------------------------------------------------------
# Operator clearance for a suspended run.
# --------------------------------------------------------------------------

#: The only reasons a suspension may be cleared for. An open-ended reason
#: string would become a free-text channel into a persisted record, and a
#: reviewer would have to read prose to find out what happened.
CLEARANCE_REASONS: frozenset[str] = frozenset({
    "OPERATOR_REVIEWED_AND_RESOLVED",
    "UPSTREAM_CONDITION_CORRECTED",
    "SUSPENSION_APPLIED_IN_ERROR",
})


@dataclass(frozen=True)
class OperatorClearance:
    """Evidence that a human reviewed a suspension and chose to clear it.

    Deliberately awkward to produce. There is no default, no timeout, and no
    "clear if it looks fine" path: every field must be supplied, and a
    malformed clearance leaves the run suspended rather than raising past it.

    `operator_reference` is a short constrained token, never a name, an email,
    or an account identifier. It is stored for correlation and is excluded from
    every sanitized projection.
    """

    transition_id: str
    operator_reference: str
    reason_code: str
    reviewed: bool = False

    def validate(self) -> None:
        if not self.reviewed:
            raise SchedulerError("clearance requires an explicit review flag")
        _validated_identifier(self.transition_id, "transition identity")
        reference = _validated_identifier(
            self.operator_reference, "operator reference"
        )
        if len(reference) > 64:
            raise SchedulerError("operator reference is too long")
        if not reference.replace("-", "").replace("_", "").isalnum():
            # Constrained charset: an unconstrained one is how an account
            # number ends up in a persisted column.
            raise SchedulerError("operator reference has an unsupported character")
        if self.reason_code not in CLEARANCE_REASONS:
            raise SchedulerError("clearance reason is not an approved code")


# --------------------------------------------------------------------------
# Persistence.
# --------------------------------------------------------------------------

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(os.fspath(REPOSITORY_ROOT / "data"))
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduler_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scheduled_runs (
    run_id                TEXT PRIMARY KEY,
    session_date          TEXT NOT NULL,
    state                 TEXT NOT NULL,
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,
    suspension_reason     TEXT,
    reevaluation_required INTEGER NOT NULL DEFAULT 0
                          CHECK (reevaluation_required IN (0, 1))
);
CREATE TABLE IF NOT EXISTS scheduler_transitions (
    transition_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL REFERENCES scheduled_runs(run_id),
    from_state    TEXT NOT NULL,
    to_state      TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    sequence      INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scheduler_transitions_by_run
    ON scheduler_transitions(run_id);
"""


def is_repository_data_path(path: Path) -> bool:
    """True when `path` lies inside the repository's production data directory."""
    try:
        resolved = os.path.normcase(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError):
        return False
    return (
        resolved == REPOSITORY_DATA_DIRECTORY
        or os.path.commonpath([resolved, REPOSITORY_DATA_DIRECTORY])
        == REPOSITORY_DATA_DIRECTORY
    )


def _validated_identifier(value: object, label: str) -> str:
    """A correlation identifier that is safe to store and to compare.

    The charset is constrained to match `durable_suspension_controller`. Two
    collaborating stores that disagree about what an identifier may contain is
    an invitation for one to accept a value the other refuses, and for the
    difference to be discovered by a partially-applied cycle rather than by a
    test.
    """
    if not isinstance(value, str) or not value.strip():
        raise SchedulerError(f"a {label} is required")
    if value != value.strip():
        raise SchedulerError(f"{label} has surrounding whitespace")
    if len(value) > 128:
        raise SchedulerError(f"{label} is too long")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise SchedulerError(f"{label} has an unsupported character")
    return value


def _validated_moment(value: object, label: str) -> datetime:
    """A timezone-aware instant. Naive input is ambiguous by the amount that
    decides whether the market was open, so it is refused rather than assumed.
    """
    if not isinstance(value, datetime):
        raise SchedulerError(f"{label} is not a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise SchedulerError(f"{label} has no timezone")
    return value


@dataclass(frozen=True)
class ScheduledRun:
    """One persisted run. Every field is an enum, a date, or a fixed code."""

    run_id: str
    session_date: date
    state: RunState
    created_at: datetime
    updated_at: datetime
    suspension_reason: str | None = None
    reevaluation_required: bool = False

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_suspended(self) -> bool:
        return self.state is RunState.SUSPENDED

    def sanitized_facts(self, *, current_session_date: date | None = None) -> dict[str, str]:
        """Privacy-safe projection.

        The run identity and the session date are **absent by design**. An
        operator-chosen run name is exactly the sort of string that ends up
        carrying an account number, and the same reasoning already keeps the
        session identity out of the durable action ledger's projection. What a
        reviewer actually needs from the date is whether it is current, so that
        is what is reported.
        """
        facts = {
            "run_state": self.state.value,
            "run_is_terminal": "true" if self.is_terminal else "false",
            "run_is_suspended": "true" if self.is_suspended else "false",
            "reevaluation_required": "true" if self.reevaluation_required else "false",
            "suspension_reason": self.suspension_reason or "NONE",
            "permits_automated_action": "false",
        }
        if current_session_date is not None:
            facts["session_date_is_current"] = (
                "true" if self.session_date == current_session_date else "false"
            )
        return facts


@dataclass(frozen=True)
class SchedulerTransitionReceipt:
    """Proof that one transition was persisted, and nothing more.

    Frozen, and every field is an enum, a count, or a constrained identifier.
    There is deliberately no connection, cursor, statement, database path, or
    raw row on it: a receipt that carried any of those would hand a reader the
    ability to write, which is the coupling this type exists to remove.

    `persisted` is always `True`. A receipt only exists for a transition that
    landed — absence is expressed by returning `None`, not by a receipt that
    says no, because a falsy receipt is the kind of thing a caller forgets to
    check.
    """

    transition_id: str
    run_id: str
    from_state: RunState
    to_state: RunState
    sequence: int
    persisted: bool = True

    def __post_init__(self) -> None:
        if not self.persisted:
            raise SchedulerError("a receipt exists only for a persisted transition")
        if not isinstance(self.from_state, RunState) or not isinstance(
            self.to_state, RunState
        ):
            raise SchedulerError("a receipt needs two RunState values")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool):
            raise SchedulerError("receipt sequence must be an int")
        if self.sequence < 1:
            raise SchedulerError("receipt sequence must be positive")

    def sanitized_facts(self) -> dict[str, str]:
        """Identities are absent, as in every other projection here."""
        return {
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "transition_persisted": "true",
            "permits_automated_action": "false",
        }


class DurableScheduler:
    """A scheduler whose state outlives the process.

    Uniqueness is enforced by the database, not by a check-then-insert in
    Python. `INSERT` against a `PRIMARY KEY` has no window between the check
    and the write; a Python check has one, and two callers racing through it
    both win. A duplicate transition identity must fail, and a primary-key
    violation is exactly that failure.

    Transition identities are **global and permanent**. A transition identity
    spent against one run cannot be reused against another, cannot be reused
    after a restart, and is not released when a run reaches a terminal state.
    Archiving is marking, never removing: deleting a row would make every
    identity it held reusable, which is the replay this ledger exists to stop.
    """

    def __init__(
        self,
        database_path: Path,
        *,
        maximum_record_age_days: int = 1,
    ) -> None:
        if not isinstance(database_path, Path):
            raise SchedulerError("a database path is required")
        if is_repository_data_path(database_path):
            raise SchedulerError(
                "the scheduler may not be opened inside repository data"
            )
        if (
            not isinstance(maximum_record_age_days, int)
            or isinstance(maximum_record_age_days, bool)
            or maximum_record_age_days < 0
        ):
            raise SchedulerError("maximum record age must be a non-negative int")
        self.database_path = database_path
        self.maximum_record_age_days = maximum_record_age_days
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
            # A refusal during construction must not leave a live handle on a
            # file the caller believes was never opened.
            self._connection.close()
            raise

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM scheduler_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO scheduler_meta (key, value) VALUES "
                "('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            raise SchedulerError("scheduler schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableScheduler":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- run lifecycle ----------------------------------------------------

    def create_run(
        self, run_id: str, *, session_date: date, now: datetime
    ) -> ScheduledRun:
        """Register a new run. A used identity is never re-registered."""
        _validated_identifier(run_id, "run identity")
        moment = _validated_moment(now, "creation time")
        if not isinstance(session_date, date) or isinstance(session_date, datetime):
            raise SchedulerError("a plain session date is required")
        stamp = moment.isoformat()
        try:
            self._connection.execute(
                "INSERT INTO scheduled_runs "
                "(run_id, session_date, state, created_at, updated_at, "
                " suspension_reason, reevaluation_required) "
                "VALUES (?, ?, ?, ?, ?, NULL, 0)",
                (
                    run_id,
                    session_date.isoformat(),
                    RunState.CREATED.value,
                    stamp,
                    stamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise SchedulerError("run identity has already been used") from exc
        loaded = self.load_run(run_id)
        assert loaded is not None  # just inserted
        return loaded

    def load_run(self, run_id: str) -> ScheduledRun | None:
        """Read one run, refusing anything the schema cannot vouch for."""
        _validated_identifier(run_id, "run identity")
        row = self._connection.execute(
            "SELECT run_id, session_date, state, created_at, updated_at, "
            "       suspension_reason, reevaluation_required "
            "FROM scheduled_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_run(row)

    @staticmethod
    def _row_to_run(row: Any) -> ScheduledRun:
        (
            stored_id,
            session_date,
            state,
            created_at,
            updated_at,
            suspension_reason,
            reevaluation_required,
        ) = row
        try:
            parsed_state = RunState(state)
        except ValueError as exc:
            # An unrecognized persisted state is not a state this process
            # knows how to be safe about. Guessing is how a suspended run
            # comes back runnable.
            raise SchedulerError("persisted run state is not recognized") from exc
        for label, value in (
            ("session date", session_date),
            ("creation time", created_at),
            ("update time", updated_at),
        ):
            if not isinstance(value, str) or not value:
                raise SchedulerError(f"persisted {label} is missing")
        try:
            parsed_date = date.fromisoformat(session_date)
            parsed_created = datetime.fromisoformat(created_at)
            parsed_updated = datetime.fromisoformat(updated_at)
        except ValueError as exc:
            raise SchedulerError("persisted timestamp is malformed") from exc
        if parsed_created.tzinfo is None or parsed_updated.tzinfo is None:
            raise SchedulerError("persisted timestamp has no timezone")
        if suspension_reason is not None and (
            not isinstance(suspension_reason, str)
            or suspension_reason not in CLEARANCE_REASONS | {"SAFETY_HALT", "OPERATOR"}
        ):
            raise SchedulerError("persisted suspension reason is not an approved code")
        if reevaluation_required not in (0, 1):
            raise SchedulerError("persisted re-evaluation flag is malformed")
        return ScheduledRun(
            run_id=stored_id,
            session_date=parsed_date,
            state=parsed_state,
            created_at=parsed_created,
            updated_at=parsed_updated,
            suspension_reason=suspension_reason,
            reevaluation_required=bool(reevaluation_required),
        )

    def _require_run(self, run_id: str) -> ScheduledRun:
        run = self.load_run(run_id)
        if run is None:
            raise SchedulerError("run identity is not registered")
        return run

    # -- transitions ------------------------------------------------------

    def apply_transition(
        self,
        run_id: str,
        *,
        target: RunState,
        transition_id: str,
        now: datetime,
        suspension_reason: str | None = None,
        clearance: OperatorClearance | None = None,
    ) -> ScheduledRun:
        """Move a run, or refuse. Every refusal leaves the record untouched.

        Idempotency is narrow on purpose. Re-applying the *same* transition
        identity is accepted only when it asks for the state the run is already
        in — that is a retry of a transition that already succeeded. The same
        identity asking for a different state is a conflict, and a different
        identity asking for the same state is a second transition rather than a
        retry. Both fail.
        """
        _validated_identifier(transition_id, "transition identity")
        moment = _validated_moment(now, "transition time")
        if not isinstance(target, RunState):
            raise SchedulerError("a target RunState is required")
        run = self._require_run(run_id)

        recorded = self._connection.execute(
            "SELECT run_id, to_state FROM scheduler_transitions "
            "WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
        if recorded is not None:
            recorded_run, recorded_target = recorded
            if recorded_run != run_id:
                # A transition identity is global. Reusing one across runs
                # would let a replay land on the wrong record entirely.
                raise SchedulerError(
                    "transition identity belongs to a different run"
                )
            if recorded_target != target.value or run.state is not target:
                raise SchedulerError("transition identity was used for another move")
            return run

        if run.state in TERMINAL_STATES:
            raise SchedulerError("a terminal run cannot transition")
        if run.state is RunState.SUSPENDED and clearance is None:
            raise SchedulerError("a suspended run requires operator clearance")
        if run.reevaluation_required and target not in (
            RunState.FAILED_CLOSED,
            RunState.EXPIRED,
            RunState.SUSPENDED,
        ):
            # After a restart the inputs behind this state are unverified.
            # Only halting moves are permitted until it is re-evaluated.
            raise SchedulerError("run requires explicit re-evaluation first")
        if transition_is_backward(run.state, target):
            raise SchedulerError("a backward transition is refused")
        if not transition_is_allowed(run.state, target):
            raise SchedulerError("transition is not permitted from this state")

        if run.state is RunState.SUSPENDED:
            assert clearance is not None  # checked above
            clearance.validate()
            if clearance.transition_id != transition_id:
                raise SchedulerError("clearance names a different transition")
            if target is not RunState.SUPERVISED_REVIEW_REQUIRED:
                # A cleared suspension lands in review, never in anything
                # runnable. A suspension worth applying is worth a look before
                # work resumes.
                raise SchedulerError("a cleared suspension must enter review")

        if target is RunState.SUSPENDED:
            reason = suspension_reason or "OPERATOR"
            if reason not in CLEARANCE_REASONS | {"SAFETY_HALT", "OPERATOR"}:
                raise SchedulerError("suspension reason is not an approved code")
        else:
            reason = None

        stamp = moment.isoformat()
        # BEGIN is outside the try on purpose. If it fails there is no
        # transaction to roll back, and rolling back anyway raises a second
        # error that replaces the first — so the caller would be told "no
        # transaction is active" instead of why the write could not start.
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT INTO scheduler_transitions "
                "(transition_id, run_id, from_state, to_state, recorded_at, sequence) "
                "VALUES (?, ?, ?, ?, ?, "
                " (SELECT COALESCE(MAX(sequence), 0) + 1 FROM scheduler_transitions))",
                (transition_id, run_id, run.state.value, target.value, stamp),
            )
            self._connection.execute(
                "UPDATE scheduled_runs SET state = ?, updated_at = ?, "
                "       suspension_reason = ?, reevaluation_required = 0 "
                "WHERE run_id = ?",
                (target.value, stamp, reason, run_id),
            )
        except sqlite3.IntegrityError as exc:
            self._rollback_quietly()
            raise SchedulerError("transition identity has already been used") from exc
        except Exception:
            self._rollback_quietly()
            raise
        self._connection.execute("COMMIT")
        return self._require_run(run_id)

    def _rollback_quietly(self) -> None:
        """Roll back without letting the rollback replace the real error.

        A failed rollback is worth nothing next to the exception that caused
        it, and raising here would hide the reason the write was refused.
        """
        try:
            self._connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    def clear_suspension(
        self,
        run_id: str,
        *,
        clearance: OperatorClearance,
        now: datetime,
    ) -> ScheduledRun:
        """Clear a suspension, with review. Never automatic, never timed out."""
        if not isinstance(clearance, OperatorClearance):
            raise SchedulerError("operator clearance is required")
        clearance.validate()
        run = self._require_run(run_id)
        if run.state is not RunState.SUSPENDED:
            raise SchedulerError("run is not suspended")
        return self.apply_transition(
            run_id,
            target=RunState.SUPERVISED_REVIEW_REQUIRED,
            transition_id=clearance.transition_id,
            now=now,
            clearance=clearance,
        )

    # -- restart ----------------------------------------------------------

    def recover_on_restart(
        self, run_id: str, *, transition_id: str, now: datetime
    ) -> ScheduledRun:
        """Bring one run to a state a restart can honestly justify.

        A run found mid-evaluation becomes `AMBIGUOUS_RESTART`. Nothing about a
        crashed process distinguishes "the evaluation finished" from "it died
        one instruction before finishing", and only one of those is safe to
        continue from — so neither is continued from.

        Terminal and suspended runs are returned untouched. Reopening the
        database is not a way to un-suspend anything.
        """
        moment = _validated_moment(now, "recovery time")
        run = self._require_run(run_id)

        if run.state in RESTART_HALTED_STATES:
            return run
        if run.state is RunState.RUNNING_OFFLINE_EVALUATION:
            return self.apply_transition(
                run_id,
                target=RunState.AMBIGUOUS_RESTART,
                transition_id=transition_id,
                now=moment,
            )
        if run.state is RunState.WAITING_FOR_SESSION:
            # Waiting is a claim about the calendar, not about work in flight.
            # It survives a restart intact.
            return run
        if run.state in RESTART_REEVALUATION_STATES:
            self._connection.execute(
                "UPDATE scheduled_runs SET reevaluation_required = 1, updated_at = ? "
                "WHERE run_id = ?",
                (moment.isoformat(), run_id),
            )
            return self._require_run(run_id)
        raise SchedulerError("run state has no defined restart behaviour")

    def mark_reevaluated(
        self, run_id: str, *, decision: SchedulerDecision, now: datetime
    ) -> ScheduledRun:
        """Record that a human or a fresh evaluation re-established the inputs.

        Requires a decision that is not itself a refusal: re-evaluating and
        getting `FAIL_CLOSED` does not clear the flag, because the flag exists
        to stop exactly that run from advancing.
        """
        moment = _validated_moment(now, "re-evaluation time")
        if not isinstance(decision, SchedulerDecision):
            raise SchedulerError("a scheduler decision is required")
        run = self._require_run(run_id)
        if not run.reevaluation_required:
            raise SchedulerError("run does not require re-evaluation")
        if decision.action in (SchedulerAction.FAIL_CLOSED, SchedulerAction.SUSPEND):
            raise SchedulerError("a refusing decision does not clear re-evaluation")
        self._connection.execute(
            "UPDATE scheduled_runs SET reevaluation_required = 0, updated_at = ? "
            "WHERE run_id = ?",
            (moment.isoformat(), run_id),
        )
        return self._require_run(run_id)

    # -- the decision -----------------------------------------------------

    def decide(
        self,
        run_id: str,
        *,
        now: datetime,
        facts: UpstreamFacts,
        today: date | None = None,
    ) -> SchedulerDecision:
        """Answer what should happen next. Authorizes nothing.

        Evaluation order is itself a safety property: halt, then run state,
        then record coherence, then calendar, then upstream facts. A calendar
        answer drawn for a suspended run would look like a result while
        describing nothing that may happen.
        """
        moment = _validated_moment(now, "decision time")
        if not isinstance(facts, UpstreamFacts):
            raise SchedulerError("upstream facts are required")
        run = self._require_run(run_id)

        # 1. A safety halt dominates everything, including the calendar.
        if facts.safety_halt_active:
            return SchedulerDecision(
                SchedulerAction.SUSPEND, SchedulerReason.SAFETY_HALT_ACTIVE
            )

        # 2. Where the run already is.
        if run.state is RunState.SUSPENDED:
            return SchedulerDecision(
                SchedulerAction.SUSPEND, SchedulerReason.RUN_IS_SUSPENDED
            )
        if run.state in TERMINAL_STATES:
            return SchedulerDecision(
                SchedulerAction.COMPLETE_NO_ACTION
                if run.state is RunState.COMPLETED_NO_ACTION
                else SchedulerAction.FAIL_CLOSED,
                SchedulerReason.RUN_IS_TERMINAL,
            )
        if run.reevaluation_required:
            return SchedulerDecision(
                SchedulerAction.REQUIRE_OPERATOR_REVIEW,
                SchedulerReason.REEVALUATION_REQUIRED,
            )

        # 3. Does the record describe a moment that makes sense?
        eastern_today = today if today is not None else moment.astimezone(EASTERN).date()
        if run.session_date > eastern_today:
            return SchedulerDecision(
                SchedulerAction.FAIL_CLOSED,
                SchedulerReason.SCHEDULE_RECORD_FUTURE_DATED,
            )
        if eastern_today - run.session_date > timedelta(
            days=self.maximum_record_age_days
        ):
            return SchedulerDecision(
                SchedulerAction.FAIL_CLOSED, SchedulerReason.SCHEDULE_RECORD_STALE
            )

        # 4. What does the authoritative calendar say?
        eligibility = assess_calendar(moment, today=eastern_today)
        if eligibility.session is CalendarSession.UNKNOWN:
            return SchedulerDecision(SchedulerAction.FAIL_CLOSED, eligibility.reason)
        if not eligibility.eligible:
            return SchedulerDecision(SchedulerAction.WAIT, eligibility.reason)

        # 5. Only now, the facts other modules already decided.
        blocking = facts.first_blocking_reason()
        if blocking is None:
            return SchedulerDecision(
                SchedulerAction.RUN_OFFLINE_CHECKS, SchedulerReason.REGULAR_SESSION
            )
        if blocking is SchedulerReason.SAFETY_HALT_ACTIVE:
            return SchedulerDecision(SchedulerAction.SUSPEND, blocking)
        if blocking in REVIEW_REASONS:
            return SchedulerDecision(
                SchedulerAction.REQUIRE_OPERATOR_REVIEW, blocking
            )
        return SchedulerDecision(SchedulerAction.FAIL_CLOSED, blocking)

    # -- inspection -------------------------------------------------------

    def inspect_transition(
        self, transition_id: str
    ) -> "SchedulerTransitionReceipt | None":
        """Whether a transition landed, and what it did. Read-only.

        Exists so a caller can establish that an effect was actually persisted
        without reaching into this object's database. A collaborator that had
        to read `_connection` to answer "did my write land?" would hold, in
        passing, the ability to make writes nobody reviewed — the coupling and
        the capability arrive together.

        The receipt carries only the fields the question needs. No connection,
        cursor, SQL, path, or arbitrary row escapes, and there is no mutation
        path on what is returned.
        """
        _validated_identifier(transition_id, "transition identity")
        row = self._connection.execute(
            "SELECT run_id, from_state, to_state, sequence "
            "FROM scheduler_transitions WHERE transition_id = ?",
            (transition_id,),
        ).fetchone()
        if row is None:
            return None
        run_id, from_state, to_state, sequence = row
        try:
            parsed_from = RunState(from_state)
            parsed_to = RunState(to_state)
        except ValueError as exc:
            raise SchedulerError("persisted transition state is not recognized") from exc
        if not isinstance(sequence, int) or sequence < 1:
            raise SchedulerError("persisted transition sequence is malformed")
        return SchedulerTransitionReceipt(
            transition_id=transition_id,
            run_id=run_id,
            from_state=parsed_from,
            to_state=parsed_to,
            sequence=sequence,
        )

    # -- reporting --------------------------------------------------------

    @property
    def transition_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM scheduler_transitions"
        ).fetchone()
        return int(row[0])

    def sanitized_facts(self) -> dict[str, str]:
        """Store-level projection. Correlates by nothing."""
        runs = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM scheduled_runs"
            ).fetchone()[0]
        )
        suspended = int(
            self._connection.execute(
                "SELECT COUNT(*) FROM scheduled_runs WHERE state = ?",
                (RunState.SUSPENDED.value,),
            ).fetchone()[0]
        )
        return {
            "scheduled_run_bucket": _count_bucket(runs),
            "suspended_run_bucket": _count_bucket(suspended),
            "transition_bucket": _count_bucket(self.transition_count),
            "permits_automated_action": "false",
        }


def _count_bucket(value: int) -> str:
    if value <= 0:
        return "none"
    if value == 1:
        return "one"
    return "many"


__all__ = [
    "ALLOWED_TRANSITIONS",
    "BACKWARD_TRANSITIONS",
    "CLEARANCE_REASONS",
    "REPOSITORY_DATA_DIRECTORY",
    "RESTART_HALTED_STATES",
    "RESTART_REEVALUATION_STATES",
    "REVIEW_REASONS",
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "CalendarEligibility",
    "DurableScheduler",
    "OperatorClearance",
    "RunState",
    "ScheduledRun",
    "SchedulerAction",
    "SchedulerTransitionReceipt",
    "SchedulerDecision",
    "SchedulerError",
    "SchedulerReason",
    "UpstreamFacts",
    "assess_calendar",
    "is_repository_data_path",
    "transition_is_allowed",
    "transition_is_backward",
]
