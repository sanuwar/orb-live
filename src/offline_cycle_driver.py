"""Durable, single-tick caller for the offline safety orchestrator.

This module owns sequencing, not domain decisions.  It accepts already-decided,
cycle-bound values from typed providers, calls :class:`OfflineSafetyOrchestrator`
at most once, and persists a deliberately small receipt.  It has no loop, wall
clock, broker, manifest, checkpoint, credential, order, or suspension-clearance
surface.  Every public result is permanently incapable of authorizing action.
"""

from __future__ import annotations

import math
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Generic, Mapping, Protocol, TypeVar

from src.attestation_authority import AttestationAuthority, SignedAttestation
from src.durable_scheduler import DurableScheduler, RunState
from src.durable_suspension_controller import (
    ControllerState,
    DurableSuspensionController,
    SuspensionDecision,
)
from src.exchange_calendar import SessionClassification
from src.execution_gates import GateDecision
from src.offline_reconciliation import RecoveryResult
from src.offline_safety_orchestrator import (
    CycleInputs,
    CycleOutcome,
    CycleReason,
    OfflineSafetyOrchestrator,
    TERMINAL_PHASES,
)
from src.operational_monitor import HeartbeatAssessment
from src.risk_limits import RiskAssessment


class DriverError(RuntimeError):
    """A privacy-safe refusal from the offline driver."""


class _ProviderRaised(RuntimeError):
    """Internal marker.  The provider's exception and text are discarded."""


class DriverState(str, Enum):
    CREATED = "CREATED"
    INPUTS_COLLECTED = "INPUTS_COLLECTED"
    ORCHESTRATOR_STARTED = "ORCHESTRATOR_STARTED"
    ORCHESTRATOR_COMPLETED = "ORCHESTRATOR_COMPLETED"
    RECEIPT_PERSISTED = "RECEIPT_PERSISTED"
    COMPLETED_NO_ACTION = "COMPLETED_NO_ACTION"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    EXPIRED = "EXPIRED"


class DriverOutcome(str, Enum):
    WAIT = "WAIT"
    OPERATOR_REVIEW_REQUIRED = "OPERATOR_REVIEW_REQUIRED"
    SUSPENDED = "SUSPENDED"
    OFFLINE_COMPLETE = "OFFLINE_COMPLETE"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    COMPLETED_NO_ACTION = "COMPLETED_NO_ACTION"


class DriverReason(str, Enum):
    ORCHESTRATOR_RESULT = "ORCHESTRATOR_RESULT"
    PROVIDER_FAILURE = "PROVIDER_FAILURE"
    INVALID_PROVIDER_RESULT = "INVALID_PROVIDER_RESULT"
    DURABLE_STATE_INVALID = "DURABLE_STATE_INVALID"
    SCHEDULER_AMBIGUOUS = "SCHEDULER_AMBIGUOUS"
    ORCHESTRATOR_RESULT_MISSING = "ORCHESTRATOR_RESULT_MISSING"
    ORCHESTRATOR_RESULT_MISMATCH = "ORCHESTRATOR_RESULT_MISMATCH"
    PERSISTENCE_FAILED = "PERSISTENCE_FAILED"
    EXPIRED = "EXPIRED"
    ALREADY_COMPLETED = "ALREADY_COMPLETED"


class ProviderKind(str, Enum):
    CALENDAR = "CALENDAR"
    RECONCILIATION = "RECONCILIATION"
    RISK = "RISK"
    MONITORING = "MONITORING"
    GATE = "GATE"
    ATTESTATIONS = "ATTESTATIONS"


TERMINAL_STATES = frozenset(
    {
        DriverState.COMPLETED_NO_ACTION,
        DriverState.FAILED_CLOSED,
        DriverState.AMBIGUOUS_RESTART,
        DriverState.EXPIRED,
    }
)

ALLOWED_TRANSITIONS: Mapping[DriverState, frozenset[DriverState]] = MappingProxyType(
    {
        DriverState.CREATED: frozenset(
            {
                DriverState.INPUTS_COLLECTED,
                DriverState.FAILED_CLOSED,
                DriverState.AMBIGUOUS_RESTART,
                DriverState.EXPIRED,
            }
        ),
        DriverState.INPUTS_COLLECTED: frozenset(
            {
                DriverState.ORCHESTRATOR_STARTED,
                DriverState.FAILED_CLOSED,
                DriverState.EXPIRED,
            }
        ),
        DriverState.ORCHESTRATOR_STARTED: frozenset(
            {
                DriverState.ORCHESTRATOR_COMPLETED,
                DriverState.AMBIGUOUS_RESTART,
                DriverState.EXPIRED,
            }
        ),
        DriverState.ORCHESTRATOR_COMPLETED: frozenset(
            {
                DriverState.RECEIPT_PERSISTED,
                DriverState.FAILED_CLOSED,
                DriverState.EXPIRED,
            }
        ),
        DriverState.RECEIPT_PERSISTED: frozenset(
            {
                DriverState.COMPLETED_NO_ACTION,
                DriverState.FAILED_CLOSED,
                DriverState.AMBIGUOUS_RESTART,
                DriverState.EXPIRED,
            }
        ),
        DriverState.COMPLETED_NO_ACTION: frozenset(),
        DriverState.FAILED_CLOSED: frozenset(),
        DriverState.AMBIGUOUS_RESTART: frozenset(),
        DriverState.EXPIRED: frozenset(),
    }
)

MAXIMUM_PROVIDER_AGE = timedelta(minutes=5)
MAXIMUM_TICK_LIFETIME = timedelta(days=1)
SCHEMA_VERSION = 1
REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, "data"))
)


@dataclass(frozen=True)
class DriverTick:
    driver_id: str
    cycle_id: str
    scheduler_run_id: str
    controller_id: str
    driver_transition_id: str
    scheduler_transition_id: str
    controller_transition_id: str
    scheduler_restart_transition_id: str
    session_date: date
    now: datetime
    expires_at: datetime

    def validate(self) -> None:
        identifiers = (
            (self.driver_id, "driver-", "driver identity"),
            (self.cycle_id, "cycle-", "cycle identity"),
            (self.scheduler_run_id, "scheduler-", "scheduler run identity"),
            (self.controller_id, "controller-", "controller identity"),
            (self.driver_transition_id, "driver-transition-", "driver transition identity"),
            (
                self.scheduler_transition_id,
                "scheduler-transition-",
                "scheduler transition identity",
            ),
            (
                self.controller_transition_id,
                "controller-transition-",
                "controller transition identity",
            ),
            (
                self.scheduler_restart_transition_id,
                "scheduler-restart-",
                "scheduler restart transition identity",
            ),
        )
        values: list[str] = []
        for value, prefix, label in identifiers:
            values.append(_validated_identifier(value, prefix, label))
        if len(set(values)) != len(values):
            raise DriverError("tick identities must be distinct")
        if not isinstance(self.session_date, date) or isinstance(self.session_date, datetime):
            raise DriverError("session date is invalid")
        now = _validated_moment(self.now, "tick time")
        expires = _validated_moment(self.expires_at, "tick expiry")
        if expires - now > MAXIMUM_TICK_LIFETIME:
            raise DriverError("tick lifetime is too long")


T = TypeVar("T")


@dataclass(frozen=True)
class ProviderResult(Generic[T]):
    kind: ProviderKind
    driver_id: str
    cycle_id: str
    scheduler_run_id: str
    controller_id: str
    observed_at: datetime
    sequence: int
    decision: T


@dataclass(frozen=True)
class AttestationBundle:
    attestations: tuple[SignedAttestation, ...]
    authority: AttestationAuthority
    observed_monotonic: float

    def as_mapping(self) -> Mapping[str, SignedAttestation]:
        if not isinstance(self.attestations, tuple):
            raise DriverError("attestations are not a typed tuple")
        result: dict[str, SignedAttestation] = {}
        for signed in self.attestations:
            if not isinstance(signed, SignedAttestation):
                raise DriverError("an attestation result is malformed")
            if signed.fact_name in result:
                raise DriverError("duplicate provider submission")
            result[signed.fact_name] = signed
        if not isinstance(self.authority, AttestationAuthority):
            raise DriverError("attestation authority is malformed")
        if not isinstance(self.observed_monotonic, (int, float)) or isinstance(
            self.observed_monotonic, bool
        ):
            raise DriverError("attestation observation time is malformed")
        if not math.isfinite(float(self.observed_monotonic)):
            raise DriverError("attestation observation time is malformed")
        return MappingProxyType(result)


class CalendarProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[SessionClassification]: ...


class ReconciliationProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[RecoveryResult]: ...


class RiskProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[RiskAssessment]: ...


class MonitoringProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[HeartbeatAssessment]: ...


class GateProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[GateDecision]: ...


class AttestationProvider(Protocol):
    def collect(self, context: DriverTick) -> ProviderResult[AttestationBundle]: ...


@dataclass(frozen=True)
class DriverProviders:
    calendar: CalendarProvider
    reconciliation: ReconciliationProvider
    risk: RiskProvider
    monitoring: MonitoringProvider
    gate: GateProvider
    attestations: AttestationProvider

    def validate(self) -> None:
        providers = (
            self.calendar,
            self.reconciliation,
            self.risk,
            self.monitoring,
            self.gate,
            self.attestations,
        )
        if any(provider is None or not callable(getattr(provider, "collect", None)) for provider in providers):
            raise DriverError("all typed providers are required")
        if len({id(provider) for provider in providers}) != len(providers):
            raise DriverError("duplicate provider submission")


@dataclass(frozen=True)
class DriverResult:
    state: DriverState
    outcome: DriverOutcome
    reason: DriverReason
    orchestrator_outcome: CycleOutcome | None = None
    orchestrator_reason: CycleReason | None = None
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.state, DriverState):
            raise DriverError("driver result state is malformed")
        if not isinstance(self.outcome, DriverOutcome):
            raise DriverError("driver result outcome is malformed")
        if not isinstance(self.reason, DriverReason):
            raise DriverError("driver result reason is malformed")
        if self.orchestrator_outcome is not None and not isinstance(
            self.orchestrator_outcome, CycleOutcome
        ):
            raise DriverError("orchestrator outcome is malformed")
        if self.orchestrator_reason is not None and not isinstance(
            self.orchestrator_reason, CycleReason
        ):
            raise DriverError("orchestrator reason is malformed")
        if self.permits_automated_action is not False:
            raise DriverError("the offline driver never authorizes action")

    def sanitized_facts(self) -> dict[str, str]:
        return {
            "driver_state": self.state.value,
            "driver_outcome": self.outcome.value,
            "driver_reason": self.reason.value,
            "orchestrator_outcome": (
                self.orchestrator_outcome.value if self.orchestrator_outcome else "NONE"
            ),
            "orchestrator_reason": (
                self.orchestrator_reason.value if self.orchestrator_reason else "NONE"
            ),
            "permits_automated_action": "false",
        }


@dataclass(frozen=True)
class DriverRecord:
    driver_id: str
    cycle_id: str
    scheduler_run_id: str
    controller_id: str
    driver_transition_id: str
    scheduler_transition_id: str
    controller_transition_id: str
    scheduler_restart_transition_id: str
    session_date: date
    state: DriverState
    outcome: DriverOutcome | None
    reason: DriverReason | None
    orchestrator_outcome: CycleOutcome | None
    orchestrator_reason: CycleReason | None
    sequence: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True)
class DriverReceipt:
    driver_id: str
    cycle_id: str
    scheduler_run_id: str
    controller_id: str
    outcome: DriverOutcome
    reason: DriverReason
    orchestrator_outcome: CycleOutcome
    orchestrator_reason: CycleReason
    sequence: int
    persisted_at: datetime
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if self.permits_automated_action is not False:
            raise DriverError("a driver receipt never authorizes action")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS driver_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS driver_runs (
    driver_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL UNIQUE,
    scheduler_run_id TEXT NOT NULL,
    controller_id TEXT NOT NULL,
    driver_transition_id TEXT NOT NULL UNIQUE,
    scheduler_transition_id TEXT NOT NULL UNIQUE,
    controller_transition_id TEXT NOT NULL UNIQUE,
    scheduler_restart_transition_id TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    reason TEXT,
    orchestrator_outcome TEXT,
    orchestrator_reason TEXT,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    permits_automated_action INTEGER NOT NULL DEFAULT 0
        CHECK (permits_automated_action = 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spent_driver_identities (
    identity TEXT PRIMARY KEY,
    driver_id TEXT NOT NULL,
    identity_kind TEXT NOT NULL,
    spent_at TEXT NOT NULL,
    FOREIGN KEY(driver_id) REFERENCES driver_runs(driver_id)
);
CREATE TABLE IF NOT EXISTS driver_receipts (
    driver_id TEXT PRIMARY KEY,
    cycle_id TEXT NOT NULL UNIQUE,
    scheduler_run_id TEXT NOT NULL,
    controller_id TEXT NOT NULL,
    outcome TEXT NOT NULL,
    reason TEXT NOT NULL,
    orchestrator_outcome TEXT NOT NULL,
    orchestrator_reason TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    permits_automated_action INTEGER NOT NULL DEFAULT 0
        CHECK (permits_automated_action = 0),
    persisted_at TEXT NOT NULL,
    FOREIGN KEY(driver_id) REFERENCES driver_runs(driver_id)
);
"""


class OfflineCycleDriver:
    """Execute exactly one explicit offline tick, durably and fail closed."""

    def __init__(
        self,
        database_path: Path,
        *,
        scheduler: DurableScheduler,
        controller: DurableSuspensionController,
        orchestrator: OfflineSafetyOrchestrator,
        providers: DriverProviders,
    ) -> None:
        if not isinstance(database_path, Path):
            raise DriverError("a driver database path is required")
        if is_repository_data_path(database_path):
            raise DriverError("the driver may not open a database inside repository data")
        if not isinstance(scheduler, DurableScheduler):
            raise DriverError("a durable scheduler is required")
        if not isinstance(controller, DurableSuspensionController):
            raise DriverError("a durable suspension controller is required")
        if not isinstance(orchestrator, OfflineSafetyOrchestrator):
            raise DriverError("an offline safety orchestrator is required")
        if orchestrator.scheduler is not scheduler or orchestrator.controller is not controller:
            raise DriverError("orchestrator durable stores do not match the driver")
        if not isinstance(providers, DriverProviders):
            raise DriverError("typed providers are required")
        providers.validate()
        self.database_path = database_path
        self.scheduler = scheduler
        self.controller = controller
        self.orchestrator = orchestrator
        self.providers = providers
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(os.fspath(database_path), isolation_level=None)
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
            "SELECT value FROM driver_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO driver_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif row[0] != str(SCHEMA_VERSION):
            raise DriverError("driver schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "OfflineCycleDriver":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def run_tick(self, tick: DriverTick) -> DriverResult:
        """Run or recover one named tick.  Never retries an ambiguous call."""
        if not isinstance(tick, DriverTick):
            raise DriverError("a typed driver tick is required")
        tick.validate()
        record = self.load_record(tick.driver_id)
        created_here = record is None
        if created_here:
            record = self._create_record(tick)
        else:
            self._require_same_tick(record, tick)
        if record.is_terminal:
            return self._result_from_record(record)
        if tick.now > record.expires_at:
            return self._conclude(
                record.driver_id,
                DriverState.EXPIRED,
                DriverOutcome.COMPLETED_NO_ACTION,
                DriverReason.EXPIRED,
                tick.now,
            )
        if record.state is DriverState.ORCHESTRATOR_STARTED:
            return self._recover_started(record, tick)
        if record.state is DriverState.ORCHESTRATOR_COMPLETED:
            return self._resume_completed(record, tick)
        if record.state is DriverState.RECEIPT_PERSISTED:
            return self._finalize(record.driver_id, tick.now)

        try:
            scheduler_run = self._load_or_create_scheduler(tick, created_here)
            controller_record = self.controller.load(tick.controller_id)
            suspension = self.controller.assess(tick.controller_id)
        except Exception:
            return self._conclude(
                tick.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.DURABLE_STATE_INVALID,
                tick.now,
            )
        if scheduler_run.run_id != tick.scheduler_run_id or controller_record.controller_id != tick.controller_id:
            return self._conclude(
                tick.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.DURABLE_STATE_INVALID,
                tick.now,
            )
        if scheduler_run.state is RunState.AMBIGUOUS_RESTART:
            return self._conclude(
                tick.driver_id,
                DriverState.AMBIGUOUS_RESTART,
                DriverOutcome.AMBIGUOUS_RESTART,
                DriverReason.SCHEDULER_AMBIGUOUS,
                tick.now,
            )

        try:
            collected = self._collect_inputs(tick)
        except _ProviderRaised:
            return self._conclude(
                tick.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.PROVIDER_FAILURE,
                tick.now,
            )
        except Exception:
            return self._conclude(
                tick.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.INVALID_PROVIDER_RESULT,
                tick.now,
            )
        if self._require_record(tick.driver_id).state is DriverState.CREATED:
            self._transition(tick.driver_id, DriverState.INPUTS_COLLECTED, tick.now)
        self._transition(tick.driver_id, DriverState.ORCHESTRATOR_STARTED, tick.now)
        inputs = CycleInputs(
            cycle_id=tick.cycle_id,
            scheduler_run_id=tick.scheduler_run_id,
            controller_id=tick.controller_id,
            now=tick.now,
            scheduler_transition_id=tick.scheduler_transition_id,
            controller_transition_id=tick.controller_transition_id,
            session=collected[ProviderKind.CALENDAR],
            recovery=collected[ProviderKind.RECONCILIATION],
            risk=collected[ProviderKind.RISK],
            monitoring=collected[ProviderKind.MONITORING],
            gate=collected[ProviderKind.GATE],
            suspension=suspension,
            attestations=collected[ProviderKind.ATTESTATIONS][0],
            authority=collected[ProviderKind.ATTESTATIONS][1],
            attestation_monotonic=collected[ProviderKind.ATTESTATIONS][2],
        )
        try:
            result = self.orchestrator.run_cycle(inputs)
        except Exception:
            try:
                cycle = self.orchestrator.load_cycle(tick.cycle_id)
            except Exception:
                cycle = None
            if self._cycle_is_exact_final(cycle, tick):
                self._record_orchestrator_result(
                    tick.driver_id, cycle.outcome, cycle.reason, tick.now
                )
                return self._resume_completed(
                    self._require_record(tick.driver_id), tick
                )
            return self._conclude(
                tick.driver_id,
                DriverState.AMBIGUOUS_RESTART,
                DriverOutcome.AMBIGUOUS_RESTART,
                DriverReason.ORCHESTRATOR_RESULT_MISSING,
                tick.now,
            )
        try:
            self._record_orchestrator_result(
                tick.driver_id, result.outcome, result.reason, tick.now
            )
            return self._resume_completed(self._require_record(tick.driver_id), tick)
        except sqlite3.Error as exc:
            raise DriverError("driver persistence is ambiguous; the tick may not retry") from exc

    def _load_or_create_scheduler(self, tick: DriverTick, created_here: bool):
        run = self.scheduler.load_run(tick.scheduler_run_id)
        if run is None:
            return self.scheduler.create_run(
                tick.scheduler_run_id, session_date=tick.session_date, now=tick.now
            )
        if run.session_date != tick.session_date:
            raise DriverError("scheduler run names another session")
        if created_here or self._require_record(tick.driver_id).state is DriverState.CREATED:
            return self.scheduler.recover_on_restart(
                tick.scheduler_run_id,
                transition_id=tick.scheduler_restart_transition_id,
                now=tick.now,
            )
        return run

    def _collect_inputs(self, tick: DriverTick) -> dict[ProviderKind, object]:
        specifications = (
            (ProviderKind.CALENDAR, self.providers.calendar, SessionClassification),
            (ProviderKind.RECONCILIATION, self.providers.reconciliation, RecoveryResult),
            (ProviderKind.RISK, self.providers.risk, RiskAssessment),
            (ProviderKind.MONITORING, self.providers.monitoring, HeartbeatAssessment),
            (ProviderKind.GATE, self.providers.gate, GateDecision),
            (ProviderKind.ATTESTATIONS, self.providers.attestations, AttestationBundle),
        )
        collected: dict[ProviderKind, object] = {}
        for kind, provider, expected in specifications:
            try:
                envelope = provider.collect(tick)
            except Exception:
                raise _ProviderRaised from None
            decision = self._validate_provider_result(envelope, kind, expected, tick)
            if kind in collected:
                raise DriverError("duplicate provider submission")
            if kind is ProviderKind.ATTESTATIONS:
                mapping = decision.as_mapping()
                collected[kind] = (
                    mapping,
                    decision.authority,
                    decision.observed_monotonic,
                )
            else:
                collected[kind] = decision
        return collected

    @staticmethod
    def _validate_provider_result(envelope, kind, expected, tick):
        if not isinstance(envelope, ProviderResult):
            raise DriverError("provider result is not a typed envelope")
        if envelope.kind is not kind:
            raise DriverError("provider result has the wrong kind")
        if (
            envelope.driver_id != tick.driver_id
            or envelope.cycle_id != tick.cycle_id
            or envelope.scheduler_run_id != tick.scheduler_run_id
            or envelope.controller_id != tick.controller_id
        ):
            raise DriverError("provider result has the wrong identity")
        observed = _validated_moment(envelope.observed_at, "provider observation time")
        age = tick.now - observed
        if age < timedelta(0):
            raise DriverError("provider result is future dated")
        if age > MAXIMUM_PROVIDER_AGE:
            raise DriverError("provider result is stale")
        if not isinstance(envelope.sequence, int) or isinstance(envelope.sequence, bool) or envelope.sequence < 1:
            raise DriverError("provider sequence is invalid")
        if not isinstance(envelope.decision, expected):
            raise DriverError("provider decision is malformed")
        return envelope.decision

    def _recover_started(self, record: DriverRecord, tick: DriverTick) -> DriverResult:
        cycle = self.orchestrator.load_cycle(record.cycle_id)
        if not self._cycle_is_exact_final(cycle, tick):
            return self._conclude(
                record.driver_id,
                DriverState.AMBIGUOUS_RESTART,
                DriverOutcome.AMBIGUOUS_RESTART,
                DriverReason.ORCHESTRATOR_RESULT_MISSING,
                tick.now,
            )
        self._record_orchestrator_result(
            record.driver_id, cycle.outcome, cycle.reason, tick.now
        )
        return self._resume_completed(self._require_record(record.driver_id), tick)

    def _resume_completed(self, record: DriverRecord, tick: DriverTick) -> DriverResult:
        cycle = self.orchestrator.load_cycle(record.cycle_id)
        if not self._cycle_is_exact_final(cycle, tick):
            return self._conclude(
                record.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.ORCHESTRATOR_RESULT_MISMATCH,
                tick.now,
            )
        if cycle.outcome is not record.orchestrator_outcome or cycle.reason is not record.orchestrator_reason:
            return self._conclude(
                record.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.ORCHESTRATOR_RESULT_MISMATCH,
                tick.now,
            )
        outcome = _OUTCOME_PROJECTION[cycle.outcome]
        try:
            self._persist_receipt(
                record, outcome, cycle.outcome, cycle.reason, tick.now
            )
        except DriverError:
            return self._conclude(
                record.driver_id,
                DriverState.FAILED_CLOSED,
                DriverOutcome.FAILED_CLOSED,
                DriverReason.ORCHESTRATOR_RESULT_MISMATCH,
                tick.now,
            )
        self._transition(record.driver_id, DriverState.RECEIPT_PERSISTED, tick.now)
        return self._finalize(record.driver_id, tick.now)

    @staticmethod
    def _cycle_is_exact_final(cycle, tick: DriverTick) -> bool:
        return bool(
            cycle is not None
            and cycle.phase in TERMINAL_PHASES
            and cycle.outcome is not None
            and cycle.reason is not None
            and cycle.cycle_id == tick.cycle_id
            and cycle.scheduler_run_id == tick.scheduler_run_id
            and cycle.controller_id == tick.controller_id
            and cycle.scheduler_transition_id == tick.scheduler_transition_id
            and cycle.controller_transition_id == tick.controller_transition_id
        )

    def _create_record(self, tick: DriverTick) -> DriverRecord:
        stamp = tick.now.isoformat()
        identities = (
            (tick.driver_transition_id, "DRIVER_TRANSITION"),
            (tick.scheduler_transition_id, "SCHEDULER_TRANSITION"),
            (tick.controller_transition_id, "CONTROLLER_TRANSITION"),
            (tick.scheduler_restart_transition_id, "SCHEDULER_RESTART_TRANSITION"),
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT INTO driver_runs (driver_id, cycle_id, scheduler_run_id, "
                "controller_id, driver_transition_id, scheduler_transition_id, "
                "controller_transition_id, scheduler_restart_transition_id, "
                "session_date, state, sequence, created_at, updated_at, expires_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)",
                (
                    tick.driver_id,
                    tick.cycle_id,
                    tick.scheduler_run_id,
                    tick.controller_id,
                    tick.driver_transition_id,
                    tick.scheduler_transition_id,
                    tick.controller_transition_id,
                    tick.scheduler_restart_transition_id,
                    tick.session_date.isoformat(),
                    DriverState.CREATED.value,
                    stamp,
                    stamp,
                    tick.expires_at.isoformat(),
                ),
            )
            self._connection.executemany(
                "INSERT INTO spent_driver_identities "
                "(identity, driver_id, identity_kind, spent_at) VALUES (?, ?, ?, ?)",
                [(identity, tick.driver_id, kind, stamp) for identity, kind in identities],
            )
            self._connection.execute("COMMIT")
        except sqlite3.IntegrityError as exc:
            self._connection.execute("ROLLBACK")
            raise DriverError("driver, cycle, or transition identity is already spent") from exc
        return self._require_record(tick.driver_id)

    def _record_orchestrator_result(self, driver_id, outcome, reason, moment) -> None:
        record = self._require_record(driver_id)
        if record.state is not DriverState.ORCHESTRATOR_STARTED:
            raise DriverError("orchestrator result arrived in the wrong state")
        cursor = self._connection.execute(
            "UPDATE driver_runs SET state = ?, orchestrator_outcome = ?, "
            "orchestrator_reason = ?, sequence = sequence + 1, updated_at = ? "
            "WHERE driver_id = ? AND state = ?",
            (
                DriverState.ORCHESTRATOR_COMPLETED.value,
                outcome.value,
                reason.value,
                moment.isoformat(),
                driver_id,
                DriverState.ORCHESTRATOR_STARTED.value,
            ),
        )
        if cursor.rowcount != 1:
            raise DriverError("driver state changed concurrently")

    def _persist_receipt(self, record, outcome, orchestrator_outcome, orchestrator_reason, moment):
        existing = self.load_receipt(record.driver_id)
        if existing is not None:
            if (
                existing.cycle_id != record.cycle_id
                or existing.scheduler_run_id != record.scheduler_run_id
                or existing.controller_id != record.controller_id
                or existing.outcome is not outcome
                or existing.reason is not DriverReason.ORCHESTRATOR_RESULT
                or existing.orchestrator_outcome is not orchestrator_outcome
                or existing.orchestrator_reason is not orchestrator_reason
            ):
                raise DriverError("another cycle receipt cannot be attached")
            return
        try:
            self._connection.execute(
                "INSERT INTO driver_receipts (driver_id, cycle_id, scheduler_run_id, "
                "controller_id, outcome, reason, orchestrator_outcome, "
                "orchestrator_reason, sequence, persisted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.driver_id,
                    record.cycle_id,
                    record.scheduler_run_id,
                    record.controller_id,
                    outcome.value,
                    DriverReason.ORCHESTRATOR_RESULT.value,
                    orchestrator_outcome.value,
                    orchestrator_reason.value,
                    record.sequence + 1,
                    moment.isoformat(),
                ),
            )
        except sqlite3.IntegrityError as exc:
            raise DriverError("receipt identity is already used") from exc

    def _finalize(self, driver_id: str, moment: datetime) -> DriverResult:
        record = self._require_record(driver_id)
        receipt = self.load_receipt(driver_id)
        if receipt is None:
            raise DriverError("receipt persistence is required before finalization")
        if record.state is DriverState.RECEIPT_PERSISTED:
            target = (
                DriverState.FAILED_CLOSED
                if receipt.outcome is DriverOutcome.FAILED_CLOSED
                else DriverState.AMBIGUOUS_RESTART
                if receipt.outcome is DriverOutcome.AMBIGUOUS_RESTART
                else DriverState.COMPLETED_NO_ACTION
            )
            return self._conclude(
                driver_id,
                target,
                receipt.outcome,
                DriverReason.ORCHESTRATOR_RESULT,
                moment,
            )
        return self._result_from_record(self._require_record(driver_id))

    def _conclude(self, driver_id, state, outcome, reason, moment) -> DriverResult:
        record = self._require_record(driver_id)
        if record.is_terminal:
            return self._result_from_record(record)
        if state not in ALLOWED_TRANSITIONS[record.state]:
            raise DriverError("driver transition is not permitted")
        cursor = self._connection.execute(
            "UPDATE driver_runs SET state = ?, outcome = ?, reason = ?, "
            "sequence = sequence + 1, updated_at = ? "
            "WHERE driver_id = ? AND state = ?",
            (
                state.value,
                outcome.value,
                reason.value,
                moment.isoformat(),
                driver_id,
                record.state.value,
            ),
        )
        if cursor.rowcount != 1:
            raise DriverError("driver state changed concurrently")
        return self._result_from_record(self._require_record(driver_id))

    def _transition(self, driver_id: str, target: DriverState, moment: datetime) -> None:
        record = self._require_record(driver_id)
        if target not in ALLOWED_TRANSITIONS[record.state]:
            raise DriverError("driver transition is not permitted")
        cursor = self._connection.execute(
            "UPDATE driver_runs SET state = ?, sequence = sequence + 1, "
            "updated_at = ? WHERE driver_id = ? AND state = ?",
            (target.value, moment.isoformat(), driver_id, record.state.value),
        )
        if cursor.rowcount != 1:
            raise DriverError("driver state changed concurrently")

    def load_record(self, driver_id: str) -> DriverRecord | None:
        _validated_identifier(driver_id, "driver-", "driver identity")
        row = self._connection.execute(
            "SELECT driver_id, cycle_id, scheduler_run_id, controller_id, "
            "driver_transition_id, scheduler_transition_id, "
            "controller_transition_id, scheduler_restart_transition_id, "
            "session_date, state, outcome, reason, orchestrator_outcome, "
            "orchestrator_reason, sequence, created_at, updated_at, expires_at "
            "FROM driver_runs WHERE driver_id = ?",
            (driver_id,),
        ).fetchone()
        if row is None:
            return None
        return _row_to_record(row)

    def _require_record(self, driver_id: str) -> DriverRecord:
        record = self.load_record(driver_id)
        if record is None:
            raise DriverError("driver record is missing")
        return record

    def load_receipt(self, driver_id: str) -> DriverReceipt | None:
        _validated_identifier(driver_id, "driver-", "driver identity")
        row = self._connection.execute(
            "SELECT driver_id, cycle_id, scheduler_run_id, controller_id, outcome, "
            "reason, orchestrator_outcome, orchestrator_reason, sequence, persisted_at "
            "FROM driver_receipts WHERE driver_id = ?",
            (driver_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return DriverReceipt(
                driver_id=row[0],
                cycle_id=row[1],
                scheduler_run_id=row[2],
                controller_id=row[3],
                outcome=DriverOutcome(row[4]),
                reason=DriverReason(row[5]),
                orchestrator_outcome=CycleOutcome(row[6]),
                orchestrator_reason=CycleReason(row[7]),
                sequence=_validated_sequence(row[8]),
                persisted_at=_parsed_moment(row[9], "receipt time"),
            )
        except ValueError as exc:
            raise DriverError("persisted receipt value is malformed") from exc

    @staticmethod
    def _require_same_tick(record: DriverRecord, tick: DriverTick) -> None:
        if (
            record.cycle_id != tick.cycle_id
            or record.scheduler_run_id != tick.scheduler_run_id
            or record.controller_id != tick.controller_id
            or record.driver_transition_id != tick.driver_transition_id
            or record.scheduler_transition_id != tick.scheduler_transition_id
            or record.controller_transition_id != tick.controller_transition_id
            or record.scheduler_restart_transition_id
            != tick.scheduler_restart_transition_id
            or record.session_date != tick.session_date
            or record.created_at > tick.now
            or record.updated_at > tick.now
            or record.expires_at != tick.expires_at
        ):
            raise DriverError("driver identity is bound to another tick")

    @staticmethod
    def _result_from_record(record: DriverRecord) -> DriverResult:
        if record.outcome is None or record.reason is None:
            raise DriverError("terminal driver result is incomplete")
        return DriverResult(
            state=record.state,
            outcome=record.outcome,
            reason=record.reason,
            orchestrator_outcome=record.orchestrator_outcome,
            orchestrator_reason=record.orchestrator_reason,
        )


def _row_to_record(row) -> DriverRecord:
    try:
        return DriverRecord(
            driver_id=row[0],
            cycle_id=row[1],
            scheduler_run_id=row[2],
            controller_id=row[3],
            driver_transition_id=row[4],
            scheduler_transition_id=row[5],
            controller_transition_id=row[6],
            scheduler_restart_transition_id=row[7],
            session_date=date.fromisoformat(row[8]),
            state=DriverState(row[9]),
            outcome=DriverOutcome(row[10]) if row[10] else None,
            reason=DriverReason(row[11]) if row[11] else None,
            orchestrator_outcome=CycleOutcome(row[12]) if row[12] else None,
            orchestrator_reason=CycleReason(row[13]) if row[13] else None,
            sequence=_validated_sequence(row[14]),
            created_at=_parsed_moment(row[15], "creation time"),
            updated_at=_parsed_moment(row[16], "update time"),
            expires_at=_parsed_moment(row[17], "expiry time"),
        )
    except ValueError as exc:
        raise DriverError("persisted driver value is malformed") from exc


def _validated_sequence(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise DriverError("persisted sequence is malformed")
    return value


def _validated_identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise DriverError(f"{label} is not constrained")
    if value != value.strip() or len(value) > 96:
        raise DriverError(f"{label} is malformed")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise DriverError(f"{label} has an unsupported character")
    return value


def _validated_moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise DriverError(f"{label} is not timezone aware")
    return value


def _parsed_moment(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DriverError(f"persisted {label} is missing")
    try:
        return _validated_moment(datetime.fromisoformat(value), label)
    except ValueError as exc:
        raise DriverError(f"persisted {label} is malformed") from exc


def is_repository_data_path(path: Path) -> bool:
    try:
        resolved = os.path.normcase(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError):
        return False
    return resolved == REPOSITORY_DATA_DIRECTORY or os.path.commonpath(
        [resolved, REPOSITORY_DATA_DIRECTORY]
    ) == REPOSITORY_DATA_DIRECTORY


_OUTCOME_PROJECTION: Mapping[CycleOutcome, DriverOutcome] = MappingProxyType(
    {
        CycleOutcome.WAIT_FOR_SESSION: DriverOutcome.WAIT,
        CycleOutcome.REQUIRE_OPERATOR_REVIEW: DriverOutcome.OPERATOR_REVIEW_REQUIRED,
        CycleOutcome.SUSPENDED: DriverOutcome.SUSPENDED,
        CycleOutcome.OFFLINE_CHECKS_COMPLETE: DriverOutcome.OFFLINE_COMPLETE,
        CycleOutcome.FAILED_CLOSED: DriverOutcome.FAILED_CLOSED,
        CycleOutcome.AMBIGUOUS_RESTART: DriverOutcome.AMBIGUOUS_RESTART,
        CycleOutcome.COMPLETED_NO_ACTION: DriverOutcome.COMPLETED_NO_ACTION,
    }
)


__all__ = [
    "ALLOWED_TRANSITIONS",
    "MAXIMUM_PROVIDER_AGE",
    "REPOSITORY_DATA_DIRECTORY",
    "SCHEMA_VERSION",
    "TERMINAL_STATES",
    "AttestationBundle",
    "AttestationProvider",
    "CalendarProvider",
    "DriverError",
    "DriverOutcome",
    "DriverProviders",
    "DriverReason",
    "DriverReceipt",
    "DriverRecord",
    "DriverResult",
    "DriverState",
    "DriverTick",
    "GateProvider",
    "MonitoringProvider",
    "OfflineCycleDriver",
    "ProviderKind",
    "ProviderResult",
    "ReconciliationProvider",
    "RiskProvider",
    "is_repository_data_path",
]
