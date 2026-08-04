"""Offline safety orchestrator — the first real wiring between the merged
safety modules (Milestone 10).

Every module it coordinates already decides something on its own. This one
decides **nothing about safety**; it decides only which already-made decision
dominates, and records the result durably. It reimplements no risk rule, no
calendar table, no lifecycle normalization, no reconciliation logic, no
attestation verification, and no monitoring policy — a second implementation of
any of those would be a second opinion nobody asked for, and the two would
eventually disagree.

It cannot execute. There is no broker session, no order path, no manifest, no
credential, and no network call anywhere in it, and every result it produces
carries ``permits_automated_action=False``.

## Dominance is an ordering, not a vote

Components are consulted in a fixed priority order and the first failing one
wins. A favourable answer from a lower-priority component never overrides a
higher-priority failure: a clean risk assessment cannot outvote a kill switch,
a regular market session cannot outvote an undelivered alert, and a valid
attestation cannot outvote a persisted record nobody can read.

That is why the order is data (`DOMINANCE_ORDER`) rather than a chain of
``if`` statements — the sequence is the safety property, and it should be
readable in one place rather than reconstructed from control flow.

## The effect asymmetry

A cycle may move the scheduler, and it may **raise** a controller suspension.
It may never **clear** one. Suspending automatically is safe and sometimes
urgent; clearing is a reviewed operator act with its own two-step, attested
path in the controller, and an orchestrator that could shortcut it would make
that path decorative.

## Crash recovery

Two durable stores cannot be updated in one transaction, so partial completion
is possible and is made **detectable** rather than prevented. An orchestrator
journal records the phase before each effect, and restart classifies what it
finds:

* nothing applied yet — the cycle failed closed, and its identities stay spent;
* the scheduler applied but not the controller — ``AMBIGUOUS_RESTART``. The
  missing half is never completed on a later run, because "the process died
  before applying it" and "the process applied it and died" are the same
  observation from here;
* both applied but not finalized — finalization is completed **only after
  verifying both stores actually carry this cycle's transition identities**.
  Finalizing is a record, not an effect, so completing it replays nothing.

Nothing is ever rolled back. A rollback would erase the evidence that a partial
effect happened, which is the one thing a reviewer needs most.

## No wall clock

Every time value is injected, asserted by parsing this file's call targets.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.attestation_authority import AttestationAuthority, SignedAttestation
from src.durable_scheduler import (
    DurableScheduler,
    RunState,
    SchedulerAction,
)
from src.durable_suspension_controller import (
    ControllerState,
    DurableSuspensionController,
    SuspensionCause,
    SuspensionDecision,
)
from src.exchange_calendar import CalendarSession, SessionClassification
from src.execution_gates import (
    MAXIMUM_ATTESTATION_AGE_SECONDS,
    EvidenceSource,
    GateDecision,
    GateVerdict,
)
from src.offline_reconciliation import RecoveryOutcome, RecoveryResult
from src.operational_monitor import HeartbeatAssessment, HeartbeatStatus
from src.offline_path_safety import REPOSITORY_DATA_DIRECTORY, is_repository_data_path
from src.risk_limits import RiskAssessment, RiskDecision


class OrchestratorError(RuntimeError):
    """Any refusal. Never carries broker, account, operator, or secret data."""


# --------------------------------------------------------------------------
# Outcomes.
# --------------------------------------------------------------------------


class CycleOutcome(str, Enum):
    """How one offline cycle ended. None implies a broker action occurred."""

    WAIT_FOR_SESSION = "WAIT_FOR_SESSION"
    REQUIRE_OPERATOR_REVIEW = "REQUIRE_OPERATOR_REVIEW"
    OFFLINE_CHECKS_COMPLETE = "OFFLINE_CHECKS_COMPLETE"
    SUSPENDED = "SUSPENDED"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    COMPLETED_NO_ACTION = "COMPLETED_NO_ACTION"


class CycleReason(str, Enum):
    """Fixed reason codes, one per dominance level plus the clean result."""

    OK = "OK"
    PERMANENT_HALT = "PERMANENT_HALT"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    KILL_SWITCH_ENGAGED = "KILL_SWITCH_ENGAGED"
    MALFORMED_DURABLE_STATE = "MALFORMED_DURABLE_STATE"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    RECONCILIATION_UNRESOLVED = "RECONCILIATION_UNRESOLVED"
    RISK_DENIED = "RISK_DENIED"
    MONITORING_UNHEALTHY = "MONITORING_UNHEALTHY"
    ALERT_DELIVERY_UNCONFIRMED = "ALERT_DELIVERY_UNCONFIRMED"
    CALENDAR_UNUSABLE = "CALENDAR_UNUSABLE"
    MARKET_NOT_IN_SESSION = "MARKET_NOT_IN_SESSION"
    ATTESTATION_MISSING = "ATTESTATION_MISSING"
    ATTESTATION_UNVERIFIED = "ATTESTATION_UNVERIFIED"
    ATTESTATION_STALE = "ATTESTATION_STALE"
    ATTESTATION_SELF_DECLARED = "ATTESTATION_SELF_DECLARED"
    PROTECTION_GROUNDING_MISSING = "PROTECTION_GROUNDING_MISSING"
    GATE_CLOSED = "GATE_CLOSED"
    SCHEDULER_STATE_TERMINAL = "SCHEDULER_STATE_TERMINAL"
    SCHEDULER_REEVALUATION_REQUIRED = "SCHEDULER_REEVALUATION_REQUIRED"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    DUPLICATE_CYCLE = "DUPLICATE_CYCLE"


@dataclass(frozen=True)
class CycleResult:
    """One cycle's answer. Authorizes nothing and triggers nothing."""

    outcome: CycleOutcome
    reason: CycleReason
    scheduler_action: SchedulerAction
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action:
            raise OrchestratorError("the orchestrator never authorizes action")
        if self.scheduler_action is SchedulerAction.RUN_OFFLINE_CHECKS and (
            self.outcome is not CycleOutcome.OFFLINE_CHECKS_COMPLETE
        ):
            raise OrchestratorError("only a complete cycle may run offline checks")

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "cycle_outcome": self.outcome.value,
            "cycle_reason": self.reason.value,
            "scheduler_action": self.scheduler_action.value,
            "permits_automated_action": "false",
        }


# --------------------------------------------------------------------------
# Dominance.
# --------------------------------------------------------------------------


class DominanceLevel(str, Enum):
    """The priority ladder, most severe first. Order is the safety property."""

    PERMANENT_HALT = "PERMANENT_HALT"
    ACTIVE_SUSPENSION = "ACTIVE_SUSPENSION"
    MALFORMED_DURABLE_STATE = "MALFORMED_DURABLE_STATE"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    RECONCILIATION = "RECONCILIATION"
    RISK = "RISK"
    MONITORING = "MONITORING"
    CALENDAR = "CALENDAR"
    ATTESTATIONS = "ATTESTATIONS"
    EXECUTION_GATE = "EXECUTION_GATE"
    OFFLINE_CHECKS_COMPLETE = "OFFLINE_CHECKS_COMPLETE"


#: Evaluated in this exact sequence. Stated as data so the ordering is
#: readable in one place instead of reconstructed from nested conditionals.
DOMINANCE_ORDER: tuple[DominanceLevel, ...] = tuple(DominanceLevel)


#: Facts every cycle must carry verified, fresh, producer-bound evidence for.
#: Names come from `execution_gates.REQUIRED_EVIDENCE`; this module does not
#: invent a second vocabulary.
REQUIRED_CYCLE_ATTESTATIONS: tuple[str, ...] = (
    "risk_within_limits",
    "recovery_consistent",
    "monitoring_healthy",
    "alerting_verified_reachable",
    "kill_switch_disengaged",
    "market_session_valid",
    "protection_grounding_complete",
)


# --------------------------------------------------------------------------
# Inputs. Typed decisions only — never a loose mapping of booleans.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CycleInputs:
    """Already-decided results from the modules this orchestrator coordinates.

    Every field is the *type that module actually returns*. A bare bool would
    let a caller assert something no component ever concluded, which is the
    honour system the attestation work exists to end.
    """

    cycle_id: str
    scheduler_run_id: str
    controller_id: str
    now: datetime
    scheduler_transition_id: str
    controller_transition_id: str

    session: SessionClassification
    recovery: RecoveryResult
    risk: RiskAssessment
    monitoring: HeartbeatAssessment
    gate: GateDecision
    suspension: SuspensionDecision

    attestations: Mapping[str, SignedAttestation]
    authority: AttestationAuthority
    #: Monotonic reading paired with the attestations, for freshness. Injected
    #: like every other clock value.
    attestation_monotonic: float

    def validate(self) -> None:
        for label, value in (
            ("cycle identity", self.cycle_id),
            ("scheduler run identity", self.scheduler_run_id),
            ("controller identity", self.controller_id),
            ("scheduler transition identity", self.scheduler_transition_id),
            ("controller transition identity", self.controller_transition_id),
        ):
            _validated_identifier(value, label)
        if self.scheduler_transition_id == self.controller_transition_id:
            # Two stores, two identities. Sharing one would make a partial
            # application indistinguishable from a complete one.
            raise OrchestratorError("each store needs its own transition identity")
        _validated_moment(self.now, "cycle time")
        for label, value, expected in (
            ("session classification", self.session, SessionClassification),
            ("recovery result", self.recovery, RecoveryResult),
            ("risk assessment", self.risk, RiskAssessment),
            ("monitoring assessment", self.monitoring, HeartbeatAssessment),
            ("gate decision", self.gate, GateDecision),
            ("suspension decision", self.suspension, SuspensionDecision),
            ("attestation authority", self.authority, AttestationAuthority),
        ):
            if not isinstance(value, expected):
                raise OrchestratorError(f"{label} is not a decided result")
        if not isinstance(self.attestations, Mapping):
            raise OrchestratorError("attestations must be a mapping")
        if not isinstance(self.attestation_monotonic, (int, float)) or isinstance(
            self.attestation_monotonic, bool
        ):
            raise OrchestratorError("attestation time must be a number")


@dataclass(frozen=True)
class AttestationVerdict:
    """Why the evidence set was or was not acceptable."""

    acceptable: bool
    reason: CycleReason


def assess_attestations(inputs: CycleInputs) -> AttestationVerdict:
    """Verified, fresh, producer-appropriate, and affirming — or refused.

    Verification is delegated to the authority. What is added here is the
    checks the authority does not make because they are not its job: that
    every required fact is *present*, that it is *recent*, that it *affirms*
    what it claims, and that it is not self-declared.
    """
    for fact in REQUIRED_CYCLE_ATTESTATIONS:
        signed = inputs.attestations.get(fact)
        if signed is None:
            return AttestationVerdict(False, CycleReason.ATTESTATION_MISSING)
        if not isinstance(signed, SignedAttestation):
            return AttestationVerdict(False, CycleReason.ATTESTATION_UNVERIFIED)
        if signed.fact_name != fact:
            # A valid signature moved onto a different fact.
            return AttestationVerdict(False, CycleReason.ATTESTATION_UNVERIFIED)
        if signed.attestation.source is EvidenceSource.SELF_DECLARED:
            return AttestationVerdict(False, CycleReason.ATTESTATION_SELF_DECLARED)
        if not inputs.authority.verify(signed):
            return AttestationVerdict(False, CycleReason.ATTESTATION_UNVERIFIED)
        age = inputs.attestation_monotonic - signed.attestation.recorded_monotonic
        if age < 0:
            # Evidence from the future means the clock or the record is wrong.
            return AttestationVerdict(False, CycleReason.ATTESTATION_STALE)
        if age > MAXIMUM_ATTESTATION_AGE_SECONDS:
            return AttestationVerdict(False, CycleReason.ATTESTATION_STALE)
        if not signed.attestation.satisfied:
            if fact == "protection_grounding_complete":
                return AttestationVerdict(
                    False, CycleReason.PROTECTION_GROUNDING_MISSING
                )
            return AttestationVerdict(False, CycleReason.ATTESTATION_UNVERIFIED)
    return AttestationVerdict(True, CycleReason.OK)


# --------------------------------------------------------------------------
# The pure decision.
# --------------------------------------------------------------------------


#: Verdicts that halt rather than merely ask for a look.
#:
#: The split is between an **operational safety failure** and an **evidence or
#: authorization gap**. A breached risk limit, an unresolved reconciliation, a
#: dead monitor, and an undelivered alert are all things that went wrong in the
#: world; work stops. A missing attestation or a closed gate means nobody has
#: established that work may start — which is the normal state of this system
#: today, and halting on it would make suspension meaningless.
SUSPENDING_REASONS: frozenset[CycleReason] = frozenset({
    CycleReason.PERMANENT_HALT,
    CycleReason.ACTIVE_SUSPENSION,
    CycleReason.KILL_SWITCH_ENGAGED,
    CycleReason.RECONCILIATION_UNRESOLVED,
    CycleReason.RISK_DENIED,
    CycleReason.MONITORING_UNHEALTHY,
    CycleReason.ALERT_DELIVERY_UNCONFIRMED,
})


#: Scheduler states a cycle never moves, whatever it concluded.
SCHEDULER_TERMINAL_STATES: frozenset[RunState] = frozenset({
    RunState.COMPLETED_NO_ACTION,
    RunState.FAILED_CLOSED,
    RunState.AMBIGUOUS_RESTART,
    RunState.EXPIRED,
})

#: (cycle verdict, current run state) -> the transition to request.
#:
#: **Orchestration policy, not scheduler truth.** The scheduler owns which
#: transitions are legal; this owns only which legal one a verdict asks for.
#: An absent pair means "request nothing", so the table is a strict allowlist
#: and every unlisted combination fails closed by doing nothing.
#:
#: The direction of every entry is deliberate. `SUSPEND` and `FAIL_CLOSED` move
#: a run toward halting. `REQUIRE_OPERATOR_REVIEW` moves it toward a human.
#: `WAIT` only takes a brand-new run into waiting. The one advancing entry,
#: `WAITING_FOR_SESSION -> PRECONDITION_CHECK`, is a step into *checking* and
#: is unreachable today because the gate cannot open.
ORCHESTRATION_TRANSITION_POLICY: Mapping[
    tuple[SchedulerAction, RunState], RunState
] = MappingProxyType({
    # Halting: from anywhere non-terminal that is not already suspended.
    (SchedulerAction.SUSPEND, RunState.CREATED): RunState.SUSPENDED,
    (SchedulerAction.SUSPEND, RunState.WAITING_FOR_SESSION): RunState.SUSPENDED,
    (SchedulerAction.SUSPEND, RunState.PRECONDITION_CHECK): RunState.SUSPENDED,
    (SchedulerAction.SUSPEND, RunState.RUNNING_OFFLINE_EVALUATION): (
        RunState.SUSPENDED
    ),
    (SchedulerAction.SUSPEND, RunState.READY_FOR_SUPERVISED_REVIEW): (
        RunState.SUSPENDED
    ),
    (SchedulerAction.SUSPEND, RunState.SUPERVISED_REVIEW_REQUIRED): (
        RunState.SUSPENDED
    ),
    # Failing closed.
    (SchedulerAction.FAIL_CLOSED, RunState.CREATED): RunState.FAILED_CLOSED,
    (SchedulerAction.FAIL_CLOSED, RunState.WAITING_FOR_SESSION): (
        RunState.FAILED_CLOSED
    ),
    (SchedulerAction.FAIL_CLOSED, RunState.PRECONDITION_CHECK): (
        RunState.FAILED_CLOSED
    ),
    (SchedulerAction.FAIL_CLOSED, RunState.RUNNING_OFFLINE_EVALUATION): (
        RunState.FAILED_CLOSED
    ),
    (SchedulerAction.FAIL_CLOSED, RunState.READY_FOR_SUPERVISED_REVIEW): (
        RunState.FAILED_CLOSED
    ),
    (SchedulerAction.FAIL_CLOSED, RunState.SUPERVISED_REVIEW_REQUIRED): (
        RunState.FAILED_CLOSED
    ),
    # Waiting: only a brand-new run enters waiting; an existing one just stays.
    (SchedulerAction.WAIT, RunState.CREATED): RunState.WAITING_FOR_SESSION,
    # Escalating to a human, from the one state where that is a real move.
    (SchedulerAction.REQUIRE_OPERATOR_REVIEW, RunState.PRECONDITION_CHECK): (
        RunState.SUPERVISED_REVIEW_REQUIRED
    ),
    (SchedulerAction.REQUIRE_OPERATOR_REVIEW, RunState.RUNNING_OFFLINE_EVALUATION): (
        RunState.SUPERVISED_REVIEW_REQUIRED
    ),
    (SchedulerAction.REQUIRE_OPERATOR_REVIEW, RunState.READY_FOR_SUPERVISED_REVIEW): (
        RunState.SUPERVISED_REVIEW_REQUIRED
    ),
    # The only advancing entry, and it advances into checking rather than
    # acting. Unreachable today: the gate cannot open.
    (SchedulerAction.RUN_OFFLINE_CHECKS, RunState.WAITING_FOR_SESSION): (
        RunState.PRECONDITION_CHECK
    ),
})

#: Transitions this policy is permitted to request at all. Nothing here enters
#: `RUNNING_OFFLINE_EVALUATION`: starting evaluation is the scheduler's own
#: step, taken deliberately, never a side effect of a safety verdict.
POLICY_PERMITTED_TARGETS: frozenset[RunState] = frozenset({
    RunState.SUSPENDED,
    RunState.FAILED_CLOSED,
    RunState.WAITING_FOR_SESSION,
    RunState.SUPERVISED_REVIEW_REQUIRED,
    RunState.PRECONDITION_CHECK,
})


def _outcome_for(reason: CycleReason) -> tuple[CycleOutcome, SchedulerAction]:
    """Map a dominance verdict to an outcome and the scheduler's marching order."""
    if reason in SUSPENDING_REASONS:
        return CycleOutcome.SUSPENDED, SchedulerAction.SUSPEND
    if reason is CycleReason.MALFORMED_DURABLE_STATE:
        return CycleOutcome.FAILED_CLOSED, SchedulerAction.SUSPEND
    if reason is CycleReason.AMBIGUOUS_RESTART:
        return CycleOutcome.AMBIGUOUS_RESTART, SchedulerAction.REQUIRE_OPERATOR_REVIEW
    if reason is CycleReason.CALENDAR_UNUSABLE:
        return CycleOutcome.FAILED_CLOSED, SchedulerAction.FAIL_CLOSED
    if reason is CycleReason.MARKET_NOT_IN_SESSION:
        return CycleOutcome.WAIT_FOR_SESSION, SchedulerAction.WAIT
    if reason is CycleReason.OK:
        return CycleOutcome.OFFLINE_CHECKS_COMPLETE, SchedulerAction.RUN_OFFLINE_CHECKS
    return CycleOutcome.REQUIRE_OPERATOR_REVIEW, SchedulerAction.REQUIRE_OPERATOR_REVIEW


def decide_cycle(inputs: CycleInputs) -> CycleResult:
    """Reduce every component's decision to one, by priority.

    Pure: no I/O, no persistence, no clock. The first failing level wins, and a
    favourable answer below it is never consulted — which is the whole point of
    an ordering rather than a score.
    """
    inputs.validate()

    for level in DOMINANCE_ORDER:
        reason = _evaluate_level(level, inputs)
        if reason is not None:
            outcome, action = _outcome_for(reason)
            return CycleResult(outcome, reason, action)

    # Unreachable: the final level always yields a verdict.
    raise OrchestratorError("dominance ordering produced no verdict")


def _evaluate_level(level: DominanceLevel, inputs: CycleInputs) -> CycleReason | None:
    """One rung of the ladder. Returns a reason to stop, or None to continue."""
    suspension = inputs.suspension

    if level is DominanceLevel.PERMANENT_HALT:
        if suspension.state is ControllerState.PERMANENT_HALT:
            return CycleReason.PERMANENT_HALT
        return None

    if level is DominanceLevel.ACTIVE_SUSPENSION:
        if suspension.state is ControllerState.SUSPENDED:
            if suspension.cause is SuspensionCause.KILL_SWITCH_ENGAGED:
                return CycleReason.KILL_SWITCH_ENGAGED
            return CycleReason.ACTIVE_SUSPENSION
        if suspension.state is ControllerState.CLEARANCE_PENDING_REVIEW:
            return CycleReason.ACTIVE_SUSPENSION
        return None

    if level is DominanceLevel.MALFORMED_DURABLE_STATE:
        if suspension.state is ControllerState.MALFORMED_STATE:
            return CycleReason.MALFORMED_DURABLE_STATE
        return None

    if level is DominanceLevel.AMBIGUOUS_RESTART:
        # Nothing to consult yet — the scheduler record is examined during the
        # cycle, and a prior ambiguous cycle is caught by the journal.
        return None

    if level is DominanceLevel.RECONCILIATION:
        if inputs.recovery.requires_operator:
            return CycleReason.RECONCILIATION_UNRESOLVED
        return None

    if level is DominanceLevel.RISK:
        if inputs.risk.is_denial:
            if inputs.risk.decision is RiskDecision.DENIED_KILL_SWITCH_ENGAGED:
                return CycleReason.KILL_SWITCH_ENGAGED
            return CycleReason.RISK_DENIED
        return None

    if level is DominanceLevel.MONITORING:
        if inputs.monitoring.status is not HeartbeatStatus.HEALTHY:
            return CycleReason.MONITORING_UNHEALTHY
        delivery = inputs.attestations.get("alerting_verified_reachable")
        if delivery is None or not isinstance(delivery, SignedAttestation):
            # An unconfirmed delivery is an unknown, not silence.
            return CycleReason.ALERT_DELIVERY_UNCONFIRMED
        if not inputs.authority.verify(delivery) or not delivery.attestation.satisfied:
            return CycleReason.ALERT_DELIVERY_UNCONFIRMED
        return None

    if level is DominanceLevel.CALENDAR:
        if inputs.session.session is CalendarSession.UNKNOWN:
            return CycleReason.CALENDAR_UNUSABLE
        if not inputs.session.is_regular_session:
            return CycleReason.MARKET_NOT_IN_SESSION
        return None

    if level is DominanceLevel.ATTESTATIONS:
        verdict = assess_attestations(inputs)
        if not verdict.acceptable:
            return verdict.reason
        return None

    if level is DominanceLevel.EXECUTION_GATE:
        if inputs.gate.verdict is not GateVerdict.OPEN:
            return CycleReason.GATE_CLOSED
        return None

    return CycleReason.OK


# --------------------------------------------------------------------------
# Journal persistence.
# --------------------------------------------------------------------------

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orchestrator_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cycle_journal (
    cycle_id                   TEXT PRIMARY KEY,
    phase                      TEXT NOT NULL,
    outcome                    TEXT,
    reason                     TEXT,
    scheduler_run_id           TEXT NOT NULL,
    controller_id              TEXT NOT NULL,
    scheduler_transition_id    TEXT NOT NULL UNIQUE,
    controller_transition_id   TEXT NOT NULL UNIQUE,
    scheduler_effect_required  INTEGER NOT NULL DEFAULT 0
                               CHECK (scheduler_effect_required IN (0, 1)),
    controller_effect_required INTEGER NOT NULL DEFAULT 0
                               CHECK (controller_effect_required IN (0, 1)),
    created_at                 TEXT NOT NULL,
    updated_at                 TEXT NOT NULL
);
"""


class CyclePhase(str, Enum):
    """Where a cycle got to. Recorded before each effect, never after."""

    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    PREPARED = "PREPARED"
    SCHEDULER_APPLIED = "SCHEDULER_APPLIED"
    CONTROLLER_APPLIED = "CONTROLLER_APPLIED"
    FINALIZED = "FINALIZED"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"


TERMINAL_PHASES: frozenset[CyclePhase] = frozenset({
    CyclePhase.FINALIZED,
    CyclePhase.FAILED_CLOSED,
    CyclePhase.AMBIGUOUS_RESTART,
})

#: What a restart concludes from each unfinished phase, before any store is
#: consulted. `PREPARED` and `CONTROLLER_APPLIED` are refined by inspection;
#: the rest are decided here.
_RESTART_BY_PHASE: Mapping[CyclePhase, CyclePhase] = MappingProxyType({
    CyclePhase.CREATED: CyclePhase.FAILED_CLOSED,
    CyclePhase.VALIDATED: CyclePhase.FAILED_CLOSED,
    CyclePhase.SCHEDULER_APPLIED: CyclePhase.AMBIGUOUS_RESTART,
})


def _validated_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise OrchestratorError(f"a {label} is required")
    if value != value.strip():
        raise OrchestratorError(f"{label} has surrounding whitespace")
    if len(value) > 128:
        raise OrchestratorError(f"{label} is too long")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise OrchestratorError(f"{label} has an unsupported character")
    return value


def _validated_moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise OrchestratorError(f"{label} is not a datetime")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise OrchestratorError(f"{label} has no timezone")
    return value


@dataclass(frozen=True)
class CycleRecord:
    cycle_id: str
    phase: CyclePhase
    outcome: CycleOutcome | None
    reason: CycleReason | None
    scheduler_run_id: str
    controller_id: str
    scheduler_transition_id: str
    controller_transition_id: str
    scheduler_effect_required: bool
    controller_effect_required: bool
    created_at: datetime
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.phase in TERMINAL_PHASES

    def sanitized_facts(self) -> dict[str, str]:
        """Identities are absent, as everywhere else in this repository."""
        return {
            "cycle_phase": self.phase.value,
            "cycle_outcome": self.outcome.value if self.outcome else "NONE",
            "cycle_reason": self.reason.value if self.reason else "NONE",
            "scheduler_effect_required": (
                "true" if self.scheduler_effect_required else "false"
            ),
            "controller_effect_required": (
                "true" if self.controller_effect_required else "false"
            ),
            "permits_automated_action": "false",
        }


class OfflineSafetyOrchestrator:
    """Runs one offline cycle and records it durably.

    The journal is this object's own store. The scheduler and controller keep
    theirs, because they are the authorities on their own state and a shared
    database would make that ownership ambiguous.
    """

    def __init__(
        self,
        journal_path: Path,
        *,
        scheduler: DurableScheduler,
        controller: DurableSuspensionController,
    ) -> None:
        if not isinstance(journal_path, Path):
            raise OrchestratorError("a journal path is required")
        if is_repository_data_path(journal_path):
            raise OrchestratorError(
                "the journal may not be opened inside repository data"
            )
        if not isinstance(scheduler, DurableScheduler):
            raise OrchestratorError("a durable scheduler is required")
        if not isinstance(controller, DurableSuspensionController):
            raise OrchestratorError("a durable suspension controller is required")
        self.journal_path = journal_path
        self.scheduler = scheduler
        self.controller = controller
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            os.fspath(journal_path), isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            self._connection.executescript(_SCHEMA)
            self._assert_schema_version()
        except Exception:
            self._connection.close()
            raise

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM orchestrator_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO orchestrator_meta (key, value) VALUES "
                "('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            raise OrchestratorError("journal schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "OfflineSafetyOrchestrator":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- journal ----------------------------------------------------------

    def load_cycle(self, cycle_id: str) -> CycleRecord | None:
        _validated_identifier(cycle_id, "cycle identity")
        row = self._connection.execute(
            "SELECT cycle_id, phase, outcome, reason, scheduler_run_id, "
            "       controller_id, scheduler_transition_id, "
            "       controller_transition_id, scheduler_effect_required, "
            "       controller_effect_required, created_at, updated_at "
            "FROM cycle_journal WHERE cycle_id = ?",
            (cycle_id,),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_cycle(row)

    @staticmethod
    def _row_to_cycle(row: Any) -> CycleRecord:
        (
            cycle_id, phase, outcome, reason, run_id, controller_id,
            scheduler_transition_id, controller_transition_id,
            scheduler_effect_required, controller_effect_required,
            created_at, updated_at,
        ) = row
        try:
            parsed_phase = CyclePhase(phase)
            parsed_outcome = CycleOutcome(outcome) if outcome else None
            parsed_reason = CycleReason(reason) if reason else None
        except ValueError as exc:
            raise OrchestratorError("persisted cycle value is not recognized") from exc
        for label, value in (("creation time", created_at), ("update time", updated_at)):
            if not isinstance(value, str) or not value:
                raise OrchestratorError(f"persisted {label} is missing")
        try:
            parsed_created = datetime.fromisoformat(created_at)
            parsed_updated = datetime.fromisoformat(updated_at)
        except ValueError as exc:
            raise OrchestratorError("persisted timestamp is malformed") from exc
        if parsed_created.tzinfo is None or parsed_updated.tzinfo is None:
            raise OrchestratorError("persisted timestamp has no timezone")
        for flag in (scheduler_effect_required, controller_effect_required):
            if flag not in (0, 1):
                raise OrchestratorError("persisted effect flag is malformed")
        return CycleRecord(
            cycle_id=cycle_id,
            phase=parsed_phase,
            outcome=parsed_outcome,
            reason=parsed_reason,
            scheduler_run_id=run_id,
            controller_id=controller_id,
            scheduler_transition_id=scheduler_transition_id,
            controller_transition_id=controller_transition_id,
            scheduler_effect_required=bool(scheduler_effect_required),
            controller_effect_required=bool(controller_effect_required),
            created_at=parsed_created,
            updated_at=parsed_updated,
        )

    def _advance_phase(
        self,
        cycle_id: str,
        phase: CyclePhase,
        moment: datetime,
        *,
        outcome: CycleOutcome | None = None,
        reason: CycleReason | None = None,
    ) -> None:
        self._connection.execute(
            "UPDATE cycle_journal SET phase = ?, updated_at = ?, "
            "       outcome = COALESCE(?, outcome), reason = COALESCE(?, reason) "
            "WHERE cycle_id = ?",
            (
                phase.value,
                moment.isoformat(),
                outcome.value if outcome else None,
                reason.value if reason else None,
                cycle_id,
            ),
        )

    # -- the cycle --------------------------------------------------------

    def run_cycle(self, inputs: CycleInputs) -> CycleResult:
        """One offline cycle: decide, then persist at most two transitions.

        A cycle identity is spent the moment the journal row exists, whether or
        not any effect follows. Re-running one is a duplicate, not a retry.
        """
        inputs.validate()
        moment = inputs.now
        stamp = moment.isoformat()

        existing = self.load_cycle(inputs.cycle_id)
        if existing is not None:
            raise OrchestratorError("cycle identity has already been used")

        result = decide_cycle(inputs)
        controller_effect = (
            result.outcome is CycleOutcome.SUSPENDED
            and inputs.suspension.state is ControllerState.CLEAR
        )
        # Computed before the journal row exists, so the row can record whether
        # an effect was ever going to be attempted. Without that, a restart
        # cannot tell "no scheduler transition was needed" from "the scheduler
        # transition is missing", and would call the first one ambiguous.
        target = self.orchestration_transition_policy(result, inputs)
        try:
            self._connection.execute(
                "INSERT INTO cycle_journal (cycle_id, phase, outcome, reason, "
                " scheduler_run_id, controller_id, scheduler_transition_id, "
                " controller_transition_id, scheduler_effect_required, "
                " controller_effect_required, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    inputs.cycle_id,
                    CyclePhase.CREATED.value,
                    result.outcome.value,
                    result.reason.value,
                    inputs.scheduler_run_id,
                    inputs.controller_id,
                    inputs.scheduler_transition_id,
                    inputs.controller_transition_id,
                    1 if target is not None else 0,
                    1 if controller_effect else 0,
                    stamp,
                    stamp,
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise OrchestratorError("cycle or transition identity is already used") from exc

        self._advance_phase(inputs.cycle_id, CyclePhase.VALIDATED, moment)
        self._advance_phase(inputs.cycle_id, CyclePhase.PREPARED, moment)

        if target is not None:
            try:
                self.scheduler.apply_transition(
                    inputs.scheduler_run_id,
                    target=target,
                    transition_id=inputs.scheduler_transition_id,
                    now=moment,
                    suspension_reason=(
                        "SAFETY_HALT" if target is RunState.SUSPENDED else None
                    ),
                )
            except Exception:
                # A failed effect is never reported as success, and the phase
                # stays where it was so a restart can see how far this got.
                self._advance_phase(
                    inputs.cycle_id,
                    CyclePhase.FAILED_CLOSED,
                    moment,
                    outcome=CycleOutcome.FAILED_CLOSED,
                    reason=CycleReason.PERSISTENCE_FAILED,
                )
                return CycleResult(
                    CycleOutcome.FAILED_CLOSED,
                    CycleReason.PERSISTENCE_FAILED,
                    SchedulerAction.SUSPEND,
                )
        self._advance_phase(inputs.cycle_id, CyclePhase.SCHEDULER_APPLIED, moment)

        if controller_effect:
            try:
                self.controller.suspend(
                    inputs.controller_id,
                    cause=_cause_for(result.reason),
                    transition_id=inputs.controller_transition_id,
                    now=moment,
                )
            except Exception:
                self._advance_phase(
                    inputs.cycle_id,
                    CyclePhase.AMBIGUOUS_RESTART,
                    moment,
                    outcome=CycleOutcome.AMBIGUOUS_RESTART,
                    reason=CycleReason.PERSISTENCE_FAILED,
                )
                # The scheduler moved and the controller did not. That is a
                # partial effect, and calling it a failure would understate it.
                return CycleResult(
                    CycleOutcome.AMBIGUOUS_RESTART,
                    CycleReason.PERSISTENCE_FAILED,
                    SchedulerAction.REQUIRE_OPERATOR_REVIEW,
                )
        self._advance_phase(inputs.cycle_id, CyclePhase.CONTROLLER_APPLIED, moment)
        self._advance_phase(inputs.cycle_id, CyclePhase.FINALIZED, moment)
        return result

    def orchestration_transition_policy(
        self, result: CycleResult, inputs: CycleInputs
    ) -> RunState | None:
        """Which scheduler transition this cycle's verdict asks for, if any.

        **This is orchestration policy, not scheduler truth.** The scheduler
        owns what transitions are *legal*; this owns the much smaller question
        of which legal transition a cycle verdict should request. Naming it
        `_scheduler_target` invited reading it as the scheduler's own rule,
        which it is not — a reviewer looking for the state machine should find
        it in `durable_scheduler.ALLOWED_TRANSITIONS`, and this table should
        never be mistaken for it.

        `None` means "request nothing", and it is the default for every
        combination the table below does not name. Waiting is not a transition;
        it is the absence of one.

        Every entry is conservative in the same direction: the mapping may move
        a run *toward* halting or review, and never toward evaluation on its
        own initiative. The single advancing entry —
        `WAITING_FOR_SESSION → PRECONDITION_CHECK` — is a step into checking,
        not into acting, and it is unreachable today because the gate cannot
        open. The scheduler still validates every request, so an entry here
        that named an illegal move would be refused there rather than applied.
        """
        run = self.scheduler.load_run(inputs.scheduler_run_id)
        if run is None:
            raise OrchestratorError("scheduler run is not registered")
        if run.state in SCHEDULER_TERMINAL_STATES:
            # A finished run is never moved by a later cycle, whatever it
            # decided. The scheduler would refuse it; asking is still wrong.
            return None
        return ORCHESTRATION_TRANSITION_POLICY.get(
            (result.scheduler_action, run.state)
        )

    # -- restart ----------------------------------------------------------

    def recover_cycle(self, cycle_id: str, *, now: datetime) -> CycleRecord:
        """Classify an unfinished cycle. Never replays, never assumes success."""
        moment = _validated_moment(now, "recovery time")
        record = self.load_cycle(cycle_id)
        if record is None:
            # A cycle nobody recorded is not a cycle to recover. Treating a
            # missing row as a fresh start is how a replay looks legitimate.
            raise OrchestratorError("cycle is not journaled")
        if record.is_terminal:
            return record

        scheduler_applied = self._scheduler_carries(record)
        controller_applied = self._controller_carries(record)

        if record.phase is CyclePhase.CONTROLLER_APPLIED:
            # An effect that was never required counts as satisfied. Demanding
            # a transition nobody intended to make would report a clean cycle
            # as ambiguous — a cycle whose verdict moved neither store is the
            # common case, not an anomaly.
            expected_scheduler = (
                scheduler_applied if record.scheduler_effect_required else True
            )
            expected_controller = (
                controller_applied if record.controller_effect_required else True
            )
            if expected_scheduler and expected_controller:
                # Finalizing is a record, not an effect. Completing it replays
                # nothing, and only happens once both stores are confirmed.
                self._advance_phase(cycle_id, CyclePhase.FINALIZED, moment)
                return self._require_cycle(cycle_id)
            return self._conclude(cycle_id, CyclePhase.AMBIGUOUS_RESTART, moment)

        if record.phase is CyclePhase.PREPARED:
            if record.scheduler_effect_required and scheduler_applied:
                # An effect may have landed. Never completed from here.
                return self._conclude(cycle_id, CyclePhase.AMBIGUOUS_RESTART, moment)
            return self._conclude(cycle_id, CyclePhase.FAILED_CLOSED, moment)

        conclusion = _RESTART_BY_PHASE.get(record.phase)
        if conclusion is None:
            raise OrchestratorError("cycle phase has no defined restart behaviour")
        return self._conclude(cycle_id, conclusion, moment)

    def _conclude(
        self, cycle_id: str, phase: CyclePhase, moment: datetime
    ) -> CycleRecord:
        outcome = (
            CycleOutcome.AMBIGUOUS_RESTART
            if phase is CyclePhase.AMBIGUOUS_RESTART
            else CycleOutcome.FAILED_CLOSED
        )
        reason = (
            CycleReason.AMBIGUOUS_RESTART
            if phase is CyclePhase.AMBIGUOUS_RESTART
            else CycleReason.PERSISTENCE_FAILED
        )
        self._advance_phase(cycle_id, phase, moment, outcome=outcome, reason=reason)
        return self._require_cycle(cycle_id)

    def _require_cycle(self, cycle_id: str) -> CycleRecord:
        record = self.load_cycle(cycle_id)
        if record is None:
            raise OrchestratorError("cycle disappeared during recovery")
        return record

    def _scheduler_carries(self, record: CycleRecord) -> bool:
        """Did this cycle's scheduler effect land, on the run it named?

        Asked through the scheduler's public receipt API. The receipt's run
        identity is checked too: a transition that landed against a *different*
        run is not this cycle's effect, and counting it would be a partial
        application reported as a complete one.
        """
        receipt = self.scheduler.inspect_transition(record.scheduler_transition_id)
        if receipt is None:
            return False
        return receipt.run_id == record.scheduler_run_id and receipt.persisted

    def _controller_carries(self, record: CycleRecord) -> bool:
        """Did this cycle's controller effect land, on the controller it named?"""
        receipt = self.controller.inspect_transition(record.controller_transition_id)
        if receipt is None:
            return False
        return receipt.controller_id == record.controller_id and receipt.persisted

    # -- reporting --------------------------------------------------------

    @property
    def cycle_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM cycle_journal"
        ).fetchone()
        return int(row[0])


def _cause_for(reason: CycleReason) -> SuspensionCause:
    """Translate a cycle verdict into the controller's vocabulary."""
    return _CAUSE_BY_REASON.get(reason, SuspensionCause.UNKNOWN_CAUSE)


#: Deliberately partial. A reason with no mapping becomes `UNKNOWN_CAUSE`,
#: which the controller ranks above every ordinary cause — an unmapped verdict
#: is a gap in this table, and should halt harder rather than softer.
_CAUSE_BY_REASON: Mapping[CycleReason, SuspensionCause] = MappingProxyType({
    CycleReason.KILL_SWITCH_ENGAGED: SuspensionCause.KILL_SWITCH_ENGAGED,
    CycleReason.ACTIVE_SUSPENSION: SuspensionCause.OPERATOR_HALT,
    CycleReason.PERMANENT_HALT: SuspensionCause.INTERNAL_INVARIANT_FAILURE,
    CycleReason.MALFORMED_DURABLE_STATE: SuspensionCause.INTERNAL_INVARIANT_FAILURE,
    CycleReason.RECONCILIATION_UNRESOLVED: SuspensionCause.RECONCILIATION_AMBIGUOUS,
    CycleReason.RISK_DENIED: SuspensionCause.RISK_LIMIT_BREACH,
    CycleReason.MONITORING_UNHEALTHY: SuspensionCause.MONITORING_UNHEALTHY,
    CycleReason.ALERT_DELIVERY_UNCONFIRMED: (
        SuspensionCause.ALERT_DELIVERY_UNCONFIRMED
    ),
    CycleReason.CALENDAR_UNUSABLE: SuspensionCause.CALENDAR_INVALID,
    CycleReason.AMBIGUOUS_RESTART: SuspensionCause.RESTART_AMBIGUITY,
})


__all__ = [
    "DOMINANCE_ORDER",
    "ORCHESTRATION_TRANSITION_POLICY",
    "POLICY_PERMITTED_TARGETS",
    "REPOSITORY_DATA_DIRECTORY",
    "SCHEDULER_TERMINAL_STATES",
    "REQUIRED_CYCLE_ATTESTATIONS",
    "SCHEMA_VERSION",
    "SUSPENDING_REASONS",
    "TERMINAL_PHASES",
    "AttestationVerdict",
    "CycleInputs",
    "CycleOutcome",
    "CyclePhase",
    "CycleReason",
    "CycleRecord",
    "CycleResult",
    "DominanceLevel",
    "OfflineSafetyOrchestrator",
    "OrchestratorError",
    "assess_attestations",
    "decide_cycle",
    "is_repository_data_path",
]
