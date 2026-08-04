"""Finite, durable process runner for :mod:`src.offline_cycle_driver`.

The runner owns only its journal.  It never schedules itself, sleeps, reads a
clock, reaches a broker, or mutates any scheduler, controller, or orchestrator
store.  Every timestamp and database path is supplied by the caller and every
public result is permanently action-inert.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from src.offline_cycle_driver import (
    DriverOutcome,
    DriverReason,
    DriverReceipt,
    DriverRecord,
    DriverResult,
    DriverState,
    DriverTick,
    OfflineCycleDriver,
)


class RunnerError(RuntimeError):
    """A fixed, privacy-safe refusal from the bounded runner."""


class RunnerMode(str, Enum):
    OFFLINE_SHADOW = "OFFLINE_SHADOW"


class ReasonSetting(str, Enum):
    FIXED_CODES_ONLY = "FIXED_CODES_ONLY"


class RunnerState(str, Enum):
    CREATED = "CREATED"
    VALIDATED = "VALIDATED"
    TICK_PENDING = "TICK_PENDING"
    TICK_STARTED = "TICK_STARTED"
    TICK_COMPLETED = "TICK_COMPLETED"
    STOP_REQUESTED = "STOP_REQUESTED"
    COMPLETED = "COMPLETED"
    FAILED_CLOSED = "FAILED_CLOSED"
    AMBIGUOUS_RESTART = "AMBIGUOUS_RESTART"
    EXPIRED = "EXPIRED"


class RunnerOutcome(str, Enum):
    COMPLETED_BOUND = "COMPLETED_BOUND"
    STOPPED_ON_REVIEW = "STOPPED_ON_REVIEW"
    STOPPED_ON_SUSPENSION = "STOPPED_ON_SUSPENSION"
    STOPPED_ON_FAILURE = "STOPPED_ON_FAILURE"
    STOPPED_ON_AMBIGUITY = "STOPPED_ON_AMBIGUITY"
    EXPIRED = "EXPIRED"
    FAILED_CLOSED = "FAILED_CLOSED"


class RunnerReason(str, Enum):
    BOUND_EXHAUSTED = "BOUND_EXHAUSTED"
    EMPTY_SCHEDULE = "EMPTY_SCHEDULE"
    DRIVER_WAIT = "DRIVER_WAIT"
    DRIVER_COMPLETE = "DRIVER_COMPLETE"
    DRIVER_REVIEW_REQUIRED = "DRIVER_REVIEW_REQUIRED"
    DRIVER_SUSPENDED = "DRIVER_SUSPENDED"
    DRIVER_FAILED = "DRIVER_FAILED"
    DRIVER_AMBIGUOUS = "DRIVER_AMBIGUOUS"
    DRIVER_EXPIRED = "DRIVER_EXPIRED"
    DURABLE_STATE_INVALID = "DURABLE_STATE_INVALID"


class TickState(str, Enum):
    PENDING = "PENDING"
    STARTED = "STARTED"
    COMPLETED = "COMPLETED"


TERMINAL_STATES = frozenset(
    {
        RunnerState.COMPLETED,
        RunnerState.FAILED_CLOSED,
        RunnerState.AMBIGUOUS_RESTART,
        RunnerState.EXPIRED,
    }
)

ALLOWED_TRANSITIONS: Mapping[RunnerState, frozenset[RunnerState]] = MappingProxyType(
    {
        RunnerState.CREATED: frozenset(
            {RunnerState.VALIDATED, RunnerState.FAILED_CLOSED}
        ),
        RunnerState.VALIDATED: frozenset(
            {RunnerState.TICK_PENDING, RunnerState.COMPLETED, RunnerState.FAILED_CLOSED}
        ),
        RunnerState.TICK_PENDING: frozenset(
            {RunnerState.TICK_STARTED, RunnerState.FAILED_CLOSED}
        ),
        RunnerState.TICK_STARTED: frozenset(
            {
                RunnerState.TICK_COMPLETED,
                RunnerState.FAILED_CLOSED,
                RunnerState.AMBIGUOUS_RESTART,
                RunnerState.EXPIRED,
            }
        ),
        RunnerState.TICK_COMPLETED: frozenset(
            {
                RunnerState.TICK_PENDING,
                RunnerState.STOP_REQUESTED,
                RunnerState.COMPLETED,
                RunnerState.FAILED_CLOSED,
                RunnerState.AMBIGUOUS_RESTART,
                RunnerState.EXPIRED,
            }
        ),
        RunnerState.STOP_REQUESTED: frozenset(
            {RunnerState.COMPLETED, RunnerState.FAILED_CLOSED}
        ),
        RunnerState.COMPLETED: frozenset(),
        RunnerState.FAILED_CLOSED: frozenset(),
        RunnerState.AMBIGUOUS_RESTART: frozenset(),
        RunnerState.EXPIRED: frozenset(),
    }
)

MAXIMUM_TICKS = 1_000
SCHEMA_VERSION = 1
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
_FORBIDDEN_ID_FRAGMENTS = (
    "account",
    "credential",
    "email",
    "order",
    "symbol",
    "token",
    "user",
    "username",
)


@dataclass(frozen=True)
class RunnerConfig:
    runner_id: str
    maximum_tick_count: int
    tick_timestamps: tuple[datetime, ...]
    session_date: date
    mode: RunnerMode
    runner_database_path: Path
    driver_database_path: Path
    reason_setting: ReasonSetting

    def validate(self) -> None:
        _validated_identifier(self.runner_id, "runner-", "runner identity")
        lowered = self.runner_id.casefold()
        if any(fragment in lowered for fragment in _FORBIDDEN_ID_FRAGMENTS):
            raise RunnerError("runner identity contains a private identifier")
        if (
            not isinstance(self.maximum_tick_count, int)
            or isinstance(self.maximum_tick_count, bool)
            or not 1 <= self.maximum_tick_count <= MAXIMUM_TICKS
        ):
            raise RunnerError("maximum tick count is invalid")
        if not isinstance(self.tick_timestamps, tuple):
            raise RunnerError("tick timestamps must be an ordered tuple")
        if len(self.tick_timestamps) > self.maximum_tick_count:
            raise RunnerError("tick timestamps exceed the maximum")
        if not isinstance(self.session_date, date) or isinstance(
            self.session_date, datetime
        ):
            raise RunnerError("session date is invalid")
        previous: datetime | None = None
        for moment in self.tick_timestamps:
            checked = _validated_moment(moment, "tick timestamp")
            if checked.date() != self.session_date:
                raise RunnerError("tick timestamp is outside the session date")
            if previous is not None and checked <= previous:
                if checked == previous:
                    raise RunnerError("duplicate tick timestamps are prohibited")
                raise RunnerError("tick timestamps are not ordered")
            previous = checked
        if self.mode is not RunnerMode.OFFLINE_SHADOW:
            raise RunnerError("runner mode is invalid")
        if self.reason_setting is not ReasonSetting.FIXED_CODES_ONLY:
            raise RunnerError("reason setting is invalid")
        for label, path in (
            ("runner database", self.runner_database_path),
            ("driver database", self.driver_database_path),
        ):
            _validated_database_path(path, label)
        if self.runner_database_path.resolve(strict=False) == (
            self.driver_database_path.resolve(strict=False)
        ):
            raise RunnerError("durable database paths must be distinct")


@dataclass(frozen=True)
class RunnerRecord:
    runner_id: str
    state: RunnerState
    outcome: RunnerOutcome | None
    reason: RunnerReason | None
    maximum_tick_count: int
    next_tick_index: int
    completed_tick_count: int
    current_driver_id: str | None
    transition_sequence: int
    last_transition_id: str
    session_date: date
    mode: RunnerMode
    reason_setting: ReasonSetting
    updated_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES


@dataclass(frozen=True)
class RunnerTickRecord:
    runner_id: str
    tick_index: int
    scheduled_at: datetime
    driver_id: str
    state: TickState
    driver_record_sequence: int | None
    driver_receipt_sequence: int | None
    driver_state: DriverState | None
    driver_outcome: DriverOutcome | None
    driver_reason: DriverReason | None
    driver_receipt_at: datetime | None


@dataclass(frozen=True)
class RunnerResult:
    runner_id: str
    state: RunnerState
    outcome: RunnerOutcome
    reason: RunnerReason
    completed_tick_count: int
    next_tick_index: int
    transition_sequence: int
    current_driver_id: str | None
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _validated_identifier(self.runner_id, "runner-", "runner identity")
        if not isinstance(self.state, RunnerState):
            raise RunnerError("runner result state is malformed")
        if not isinstance(self.outcome, RunnerOutcome):
            raise RunnerError("runner result outcome is malformed")
        if not isinstance(self.reason, RunnerReason):
            raise RunnerError("runner result reason is malformed")
        if self.permits_automated_action is not False:
            raise RunnerError("the offline runner never authorizes action")
        allowed = {
            RunnerState.COMPLETED: {
                RunnerOutcome.COMPLETED_BOUND,
                RunnerOutcome.STOPPED_ON_REVIEW,
                RunnerOutcome.STOPPED_ON_SUSPENSION,
            },
            RunnerState.FAILED_CLOSED: {
                RunnerOutcome.STOPPED_ON_FAILURE,
                RunnerOutcome.FAILED_CLOSED,
            },
            RunnerState.AMBIGUOUS_RESTART: {
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
            },
            RunnerState.EXPIRED: {RunnerOutcome.EXPIRED},
        }
        if self.state not in allowed or self.outcome not in allowed[self.state]:
            raise RunnerError("runner result state and outcome conflict")
        allowed_reasons = {
            RunnerOutcome.COMPLETED_BOUND: {
                RunnerReason.BOUND_EXHAUSTED,
                RunnerReason.EMPTY_SCHEDULE,
                RunnerReason.DRIVER_WAIT,
                RunnerReason.DRIVER_COMPLETE,
            },
            RunnerOutcome.STOPPED_ON_REVIEW: {RunnerReason.DRIVER_REVIEW_REQUIRED},
            RunnerOutcome.STOPPED_ON_SUSPENSION: {RunnerReason.DRIVER_SUSPENDED},
            RunnerOutcome.STOPPED_ON_FAILURE: {RunnerReason.DRIVER_FAILED},
            RunnerOutcome.STOPPED_ON_AMBIGUITY: {RunnerReason.DRIVER_AMBIGUOUS},
            RunnerOutcome.EXPIRED: {RunnerReason.DRIVER_EXPIRED},
            RunnerOutcome.FAILED_CLOSED: {RunnerReason.DURABLE_STATE_INVALID},
        }
        if self.reason not in allowed_reasons[self.outcome]:
            raise RunnerError("runner result outcome and reason conflict")
        for value in (self.completed_tick_count, self.next_tick_index):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise RunnerError("runner result count is malformed")
        if self.completed_tick_count != self.next_tick_index:
            raise RunnerError("runner result counters conflict")
        if (
            not isinstance(self.transition_sequence, int)
            or isinstance(self.transition_sequence, bool)
            or self.transition_sequence < 1
        ):
            raise RunnerError("runner result transition sequence is malformed")
        if self.current_driver_id is not None:
            _validated_identifier(
                self.current_driver_id, "driver-", "current driver identity"
            )

    def sanitized_facts(self) -> dict[str, str | int | None]:
        return {
            "runner_id": self.runner_id,
            "runner_state": self.state.value,
            "runner_outcome": self.outcome.value,
            "runner_reason": self.reason.value,
            "completed_tick_count": self.completed_tick_count,
            "next_tick_index": self.next_tick_index,
            "transition_sequence": self.transition_sequence,
            "current_driver_id": self.current_driver_id,
            "permits_automated_action": "false",
        }


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runner_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runner_runs (
    runner_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    outcome TEXT,
    reason TEXT,
    maximum_tick_count INTEGER NOT NULL CHECK (maximum_tick_count >= 1),
    next_tick_index INTEGER NOT NULL CHECK (next_tick_index >= 0),
    completed_tick_count INTEGER NOT NULL CHECK (completed_tick_count >= 0),
    current_driver_id TEXT,
    transition_sequence INTEGER NOT NULL CHECK (transition_sequence >= 1),
    last_transition_id TEXT NOT NULL UNIQUE,
    session_date TEXT NOT NULL,
    mode TEXT NOT NULL,
    reason_setting TEXT NOT NULL,
    driver_store_id TEXT NOT NULL,
    permits_automated_action INTEGER NOT NULL DEFAULT 0
        CHECK (permits_automated_action = 0),
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS runner_ticks (
    runner_id TEXT NOT NULL,
    tick_index INTEGER NOT NULL CHECK (tick_index >= 0),
    scheduled_at TEXT NOT NULL,
    driver_id TEXT NOT NULL UNIQUE,
    state TEXT NOT NULL,
    driver_record_sequence INTEGER,
    driver_receipt_sequence INTEGER,
    driver_state TEXT,
    driver_outcome TEXT,
    driver_reason TEXT,
    driver_receipt_at TEXT,
    PRIMARY KEY (runner_id, tick_index),
    FOREIGN KEY (runner_id) REFERENCES runner_runs(runner_id)
);
CREATE TABLE IF NOT EXISTS runner_transitions (
    transition_id TEXT PRIMARY KEY,
    runner_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    source_state TEXT,
    target_state TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    UNIQUE (runner_id, sequence),
    FOREIGN KEY (runner_id) REFERENCES runner_runs(runner_id)
);
"""


class BoundedOfflineShadowRunner:
    """Execute an explicitly finite schedule through one public driver API."""

    def __init__(self, config: RunnerConfig, *, driver: OfflineCycleDriver) -> None:
        if not isinstance(config, RunnerConfig):
            raise RunnerError("typed runner configuration is required")
        config.validate()
        if not isinstance(driver, OfflineCycleDriver):
            raise RunnerError("an offline cycle driver is required")
        if driver.database_path.resolve(strict=False) != (
            config.driver_database_path.resolve(strict=False)
        ):
            raise RunnerError("driver database identity does not match configuration")
        self.config = config
        self.driver = driver
        config.runner_database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            os.fspath(config.runner_database_path), isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        try:
            self._connection.executescript(_SCHEMA)
            self._assert_schema_version()
            self._load_or_create()
        except RunnerError:
            self._connection.close()
            raise
        except Exception:
            self._connection.close()
            raise RunnerError("runner initialization failed") from None

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM runner_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO runner_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
        elif row[0] != str(SCHEMA_VERSION):
            raise RunnerError("runner schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "BoundedOfflineShadowRunner":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def execute(self) -> RunnerResult:
        """Run no more than the configured finite number of ticks."""
        try:
            return self._execute_bounded()
        except Exception:
            try:
                record = self.load_record()
                if record.is_terminal:
                    raise RunnerError("terminal runner evidence is invalid")
                return self._terminal(
                    RunnerState.FAILED_CLOSED,
                    RunnerOutcome.FAILED_CLOSED,
                    RunnerReason.DURABLE_STATE_INVALID,
                    self._state_time(record),
                )
            except Exception:
                raise RunnerError("runner durable state cannot be concluded safely") from None

    def _execute_bounded(self) -> RunnerResult:
        record = self.load_record()
        self._validate_transition_journal(record)
        self._validate_current_driver(record)
        self._validate_progress(record)
        if record.is_terminal:
            return self._result(record)
        if record.state is RunnerState.CREATED:
            record = self._transition(RunnerState.VALIDATED, self._state_time(record))

        remaining = self.config.maximum_tick_count - record.completed_tick_count
        for _bounded_iteration in range(remaining + 1):
            record = self.load_record()
            if record.is_terminal:
                return self._result(record)
            if record.state is RunnerState.VALIDATED:
                if not self.config.tick_timestamps:
                    return self._terminal(
                        RunnerState.COMPLETED,
                        RunnerOutcome.COMPLETED_BOUND,
                        RunnerReason.EMPTY_SCHEDULE,
                        self._state_time(record),
                    )
                record = self._transition(
                    RunnerState.TICK_PENDING,
                    self.config.tick_timestamps[record.next_tick_index],
                )
            if record.state is RunnerState.STOP_REQUESTED:
                return self._terminal(
                    RunnerState.COMPLETED,
                    record.outcome,
                    record.reason,
                    record.updated_at,
                )
            if record.state is RunnerState.TICK_STARTED:
                record = self._recover_started(record)
            elif record.state is RunnerState.TICK_PENDING:
                record = self._run_pending(record)
            if record.is_terminal:
                return self._result(record)
            if record.state is RunnerState.TICK_COMPLETED:
                record = self._after_completed(record)
                if record.is_terminal:
                    return self._result(record)

        return self._terminal(
            RunnerState.FAILED_CLOSED,
            RunnerOutcome.FAILED_CLOSED,
            RunnerReason.DURABLE_STATE_INVALID,
            self._state_time(self.load_record()),
        )

    def _run_pending(self, record: RunnerRecord) -> RunnerRecord:
        index = record.next_tick_index
        tick_record = self.load_tick(index)
        if tick_record.state is not TickState.PENDING:
            return self._terminal_record(
                RunnerState.FAILED_CLOSED,
                RunnerOutcome.FAILED_CLOSED,
                RunnerReason.DURABLE_STATE_INVALID,
                tick_record.scheduled_at,
            )
        self._start_tick(record, tick_record)
        driver_tick = derive_driver_tick(self.config, index)
        try:
            result = self.driver.run_tick(driver_tick)
        except Exception:
            return self._recover_started(self.load_record())
        return self._accept_driver_result(index, driver_tick, result)

    def _recover_started(self, record: RunnerRecord) -> RunnerRecord:
        index = record.next_tick_index
        runner_receipt = self.load_tick(index)
        driver_tick = derive_driver_tick(self.config, index)
        if runner_receipt.state is TickState.COMPLETED:
            return self._transition(RunnerState.TICK_COMPLETED, runner_receipt.scheduled_at)
        try:
            driver_record = self.driver.load_record(driver_tick.driver_id)
        except Exception:
            driver_record = None
        if not _driver_record_is_exact(driver_record, driver_tick):
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                runner_receipt.scheduled_at,
            )
        if driver_record.state is DriverState.AMBIGUOUS_RESTART:
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                runner_receipt.scheduled_at,
            )
        if driver_record.state is DriverState.FAILED_CLOSED:
            return self._terminal_record(
                RunnerState.FAILED_CLOSED,
                RunnerOutcome.STOPPED_ON_FAILURE,
                RunnerReason.DRIVER_FAILED,
                runner_receipt.scheduled_at,
            )
        if driver_record.state is DriverState.EXPIRED:
            return self._terminal_record(
                RunnerState.EXPIRED,
                RunnerOutcome.EXPIRED,
                RunnerReason.DRIVER_EXPIRED,
                runner_receipt.scheduled_at,
            )
        if driver_record.state is not DriverState.COMPLETED_NO_ACTION:
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                runner_receipt.scheduled_at,
            )
        try:
            receipt = self.driver.load_receipt(driver_tick.driver_id)
        except Exception:
            receipt = None
        if not _driver_receipt_is_exact(receipt, driver_record):
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                runner_receipt.scheduled_at,
            )
        result = DriverResult(
            state=driver_record.state,
            outcome=driver_record.outcome,
            reason=driver_record.reason,
            orchestrator_outcome=driver_record.orchestrator_outcome,
            orchestrator_reason=driver_record.orchestrator_reason,
        )
        return self._accept_driver_result(index, driver_tick, result)

    def _accept_driver_result(
        self,
        index: int,
        driver_tick: DriverTick,
        result: DriverResult,
    ) -> RunnerRecord:
        try:
            record = self.driver.load_record(driver_tick.driver_id)
            receipt = self.driver.load_receipt(driver_tick.driver_id)
        except Exception:
            record = None
            receipt = None
        if not _driver_record_is_exact(record, driver_tick) or not _result_matches_record(
            result, record
        ):
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                self.config.tick_timestamps[index],
            )
        may_continue = result.outcome in {
            DriverOutcome.WAIT,
            DriverOutcome.OFFLINE_COMPLETE,
            DriverOutcome.COMPLETED_NO_ACTION,
        }
        if may_continue and not _driver_receipt_is_exact(receipt, record):
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                self.config.tick_timestamps[index],
            )
        if receipt is not None and not _driver_receipt_is_exact(receipt, record):
            return self._terminal_record(
                RunnerState.FAILED_CLOSED,
                RunnerOutcome.FAILED_CLOSED,
                RunnerReason.DURABLE_STATE_INVALID,
                self.config.tick_timestamps[index],
            )
        return self._complete_tick(index, record, receipt)

    def _after_completed(self, record: RunnerRecord) -> RunnerRecord:
        self._validate_completed_tick(record.next_tick_index - 1)
        completed = self.load_tick(record.next_tick_index - 1)
        moment = completed.scheduled_at
        if completed.driver_state is DriverState.EXPIRED:
            return self._terminal_record(
                RunnerState.EXPIRED,
                RunnerOutcome.EXPIRED,
                RunnerReason.DRIVER_EXPIRED,
                moment,
            )
        if completed.driver_outcome is DriverOutcome.OPERATOR_REVIEW_REQUIRED:
            return self._request_stop(
                RunnerOutcome.STOPPED_ON_REVIEW,
                RunnerReason.DRIVER_REVIEW_REQUIRED,
                moment,
            )
        if completed.driver_outcome is DriverOutcome.SUSPENDED:
            return self._request_stop(
                RunnerOutcome.STOPPED_ON_SUSPENSION,
                RunnerReason.DRIVER_SUSPENDED,
                moment,
            )
        if completed.driver_outcome is DriverOutcome.FAILED_CLOSED:
            return self._terminal_record(
                RunnerState.FAILED_CLOSED,
                RunnerOutcome.STOPPED_ON_FAILURE,
                RunnerReason.DRIVER_FAILED,
                moment,
            )
        if completed.driver_outcome is DriverOutcome.AMBIGUOUS_RESTART:
            return self._terminal_record(
                RunnerState.AMBIGUOUS_RESTART,
                RunnerOutcome.STOPPED_ON_AMBIGUITY,
                RunnerReason.DRIVER_AMBIGUOUS,
                moment,
            )
        if record.completed_tick_count >= len(self.config.tick_timestamps):
            reason = (
                RunnerReason.DRIVER_WAIT
                if completed.driver_outcome is DriverOutcome.WAIT
                else RunnerReason.BOUND_EXHAUSTED
            )
            return self._terminal_record(
                RunnerState.COMPLETED,
                RunnerOutcome.COMPLETED_BOUND,
                reason,
                moment,
            )
        return self._transition(
            RunnerState.TICK_PENDING,
            self.config.tick_timestamps[record.next_tick_index],
        )

    def _request_stop(
        self, outcome: RunnerOutcome, reason: RunnerReason, moment: datetime
    ) -> RunnerRecord:
        record = self.load_record()
        if RunnerState.STOP_REQUESTED not in ALLOWED_TRANSITIONS[record.state]:
            raise RunnerError("runner stop transition is not permitted")
        sequence = record.transition_sequence + 1
        transition_id = _transition_id(record.runner_id, sequence)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "UPDATE runner_runs SET state=?, outcome=?, reason=?, "
                "transition_sequence=?, last_transition_id=?, updated_at=? "
                "WHERE runner_id=? AND state=? AND transition_sequence=?",
                (
                    RunnerState.STOP_REQUESTED.value,
                    outcome.value,
                    reason.value,
                    sequence,
                    transition_id,
                    moment.isoformat(),
                    record.runner_id,
                    record.state.value,
                    record.transition_sequence,
                ),
            )
            if cursor.rowcount != 1:
                raise RunnerError("runner state changed concurrently")
            self._insert_transition(
                transition_id,
                record.runner_id,
                sequence,
                record.state,
                RunnerState.STOP_REQUESTED,
                moment,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner stop persistence failed") from None
        return self._terminal_record(RunnerState.COMPLETED, outcome, reason, moment)

    def _start_tick(
        self, record: RunnerRecord, tick_record: RunnerTickRecord
    ) -> RunnerRecord:
        target_sequence = record.transition_sequence + 1
        transition_id = _transition_id(record.runner_id, target_sequence)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            tick_cursor = self._connection.execute(
                "UPDATE runner_ticks SET state=? WHERE runner_id=? AND tick_index=? "
                "AND state=?",
                (
                    TickState.STARTED.value,
                    record.runner_id,
                    tick_record.tick_index,
                    TickState.PENDING.value,
                ),
            )
            run_cursor = self._connection.execute(
                "UPDATE runner_runs SET state=?, current_driver_id=?, "
                "transition_sequence=?, last_transition_id=?, updated_at=? "
                "WHERE runner_id=? AND state=? AND transition_sequence=?",
                (
                    RunnerState.TICK_STARTED.value,
                    tick_record.driver_id,
                    target_sequence,
                    transition_id,
                    tick_record.scheduled_at.isoformat(),
                    record.runner_id,
                    RunnerState.TICK_PENDING.value,
                    record.transition_sequence,
                ),
            )
            if tick_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise RunnerError("runner state changed concurrently")
            self._insert_transition(
                transition_id,
                record.runner_id,
                target_sequence,
                RunnerState.TICK_PENDING,
                RunnerState.TICK_STARTED,
                tick_record.scheduled_at,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner tick start persistence failed") from None
        return self.load_record()

    def _complete_tick(
        self,
        index: int,
        driver_record: DriverRecord,
        driver_receipt: DriverReceipt | None,
    ) -> RunnerRecord:
        record = self.load_record()
        tick_record = self.load_tick(index)
        if record.state is not RunnerState.TICK_STARTED or tick_record.state is not TickState.STARTED:
            raise RunnerError("runner tick completion is in the wrong state")
        sequence = record.transition_sequence + 1
        transition_id = _transition_id(record.runner_id, sequence)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            tick_cursor = self._connection.execute(
                "UPDATE runner_ticks SET state=?, driver_record_sequence=?, "
                "driver_receipt_sequence=?, driver_state=?, driver_outcome=?, "
                "driver_reason=?, driver_receipt_at=? WHERE runner_id=? "
                "AND tick_index=? AND state=?",
                (
                    TickState.COMPLETED.value,
                    driver_record.sequence,
                    driver_receipt.sequence if driver_receipt else None,
                    driver_record.state.value,
                    driver_record.outcome.value,
                    driver_record.reason.value,
                    driver_receipt.persisted_at.isoformat() if driver_receipt else None,
                    record.runner_id,
                    index,
                    TickState.STARTED.value,
                ),
            )
            run_cursor = self._connection.execute(
                "UPDATE runner_runs SET state=?, next_tick_index=?, "
                "completed_tick_count=?, transition_sequence=?, "
                "last_transition_id=?, updated_at=? WHERE runner_id=? AND state=? "
                "AND transition_sequence=?",
                (
                    RunnerState.TICK_COMPLETED.value,
                    index + 1,
                    record.completed_tick_count + 1,
                    sequence,
                    transition_id,
                    tick_record.scheduled_at.isoformat(),
                    record.runner_id,
                    RunnerState.TICK_STARTED.value,
                    record.transition_sequence,
                ),
            )
            if tick_cursor.rowcount != 1 or run_cursor.rowcount != 1:
                raise RunnerError("runner state changed concurrently")
            self._insert_transition(
                transition_id,
                record.runner_id,
                sequence,
                RunnerState.TICK_STARTED,
                RunnerState.TICK_COMPLETED,
                tick_record.scheduled_at,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner tick receipt persistence failed") from None
        return self.load_record()

    def _transition(self, target: RunnerState, moment: datetime) -> RunnerRecord:
        record = self.load_record()
        if target not in ALLOWED_TRANSITIONS[record.state]:
            raise RunnerError("runner transition is not permitted")
        sequence = record.transition_sequence + 1
        transition_id = _transition_id(record.runner_id, sequence)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "UPDATE runner_runs SET state=?, transition_sequence=?, "
                "last_transition_id=?, updated_at=? WHERE runner_id=? AND state=? "
                "AND transition_sequence=?",
                (
                    target.value,
                    sequence,
                    transition_id,
                    moment.isoformat(),
                    record.runner_id,
                    record.state.value,
                    record.transition_sequence,
                ),
            )
            if cursor.rowcount != 1:
                raise RunnerError("runner state changed concurrently")
            self._insert_transition(
                transition_id,
                record.runner_id,
                sequence,
                record.state,
                target,
                moment,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner transition persistence failed") from None
        return self.load_record()

    def _terminal(
        self,
        state: RunnerState,
        outcome: RunnerOutcome,
        reason: RunnerReason,
        moment: datetime,
    ) -> RunnerResult:
        return self._result(self._terminal_record(state, outcome, reason, moment))

    def _terminal_record(
        self,
        state: RunnerState,
        outcome: RunnerOutcome,
        reason: RunnerReason,
        moment: datetime,
    ) -> RunnerRecord:
        record = self.load_record()
        if record.is_terminal:
            return record
        if state not in ALLOWED_TRANSITIONS[record.state]:
            raise RunnerError("runner terminal transition is not permitted")
        sequence = record.transition_sequence + 1
        transition_id = _transition_id(record.runner_id, sequence)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            cursor = self._connection.execute(
                "UPDATE runner_runs SET state=?, outcome=?, reason=?, "
                "transition_sequence=?, last_transition_id=?, updated_at=? "
                "WHERE runner_id=? AND state=? AND transition_sequence=?",
                (
                    state.value,
                    outcome.value,
                    reason.value,
                    sequence,
                    transition_id,
                    moment.isoformat(),
                    record.runner_id,
                    record.state.value,
                    record.transition_sequence,
                ),
            )
            if cursor.rowcount != 1:
                raise RunnerError("runner state changed concurrently")
            self._insert_transition(
                transition_id,
                record.runner_id,
                sequence,
                record.state,
                state,
                moment,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner final persistence failed") from None
        return self.load_record()

    def _insert_transition(
        self,
        transition_id: str,
        runner_id: str,
        sequence: int,
        source: RunnerState | None,
        target: RunnerState,
        moment: datetime,
    ) -> None:
        self._connection.execute(
            "INSERT INTO runner_transitions (transition_id, runner_id, sequence, "
            "source_state, target_state, occurred_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                transition_id,
                runner_id,
                sequence,
                source.value if source else None,
                target.value,
                moment.isoformat(),
            ),
        )

    def _load_or_create(self) -> RunnerRecord:
        existing = self.load_record(required=False)
        if existing is not None:
            self._require_same_config(existing)
            return existing
        moment = self._initial_time()
        transition_id = _transition_id(self.config.runner_id, 1)
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute(
                "INSERT INTO runner_runs (runner_id, state, maximum_tick_count, "
                "next_tick_index, completed_tick_count, transition_sequence, "
                "last_transition_id, session_date, mode, reason_setting, "
                "driver_store_id, updated_at) VALUES (?, ?, ?, 0, 0, 1, ?, ?, ?, ?, ?, ?)",
                (
                    self.config.runner_id,
                    RunnerState.CREATED.value,
                    self.config.maximum_tick_count,
                    transition_id,
                    self.config.session_date.isoformat(),
                    self.config.mode.value,
                    self.config.reason_setting.value,
                    _store_id(self.config.driver_database_path),
                    moment.isoformat(),
                ),
            )
            self._connection.executemany(
                "INSERT INTO runner_ticks (runner_id, tick_index, scheduled_at, "
                "driver_id, state) VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        self.config.runner_id,
                        index,
                        stamp.isoformat(),
                        derive_driver_tick(self.config, index).driver_id,
                        TickState.PENDING.value,
                    )
                    for index, stamp in enumerate(self.config.tick_timestamps)
                ],
            )
            self._insert_transition(
                transition_id,
                self.config.runner_id,
                1,
                None,
                RunnerState.CREATED,
                moment,
            )
            self._connection.execute("COMMIT")
        except Exception:
            self._rollback()
            raise RunnerError("runner identity is already used or persistence failed") from None
        return self.load_record()

    def load_record(self, *, required: bool = True) -> RunnerRecord | None:
        row = self._connection.execute(
            "SELECT runner_id, state, outcome, reason, maximum_tick_count, "
            "next_tick_index, completed_tick_count, current_driver_id, "
            "transition_sequence, last_transition_id, session_date, mode, "
            "reason_setting, updated_at FROM runner_runs WHERE runner_id=?",
            (self.config.runner_id,),
        ).fetchone()
        if row is None:
            if required:
                raise RunnerError("runner record is missing")
            return None
        try:
            record = RunnerRecord(
                runner_id=_validated_identifier(row[0], "runner-", "runner identity"),
                state=RunnerState(row[1]),
                outcome=RunnerOutcome(row[2]) if row[2] else None,
                reason=RunnerReason(row[3]) if row[3] else None,
                maximum_tick_count=_validated_count(row[4]),
                next_tick_index=_validated_nonnegative(row[5]),
                completed_tick_count=_validated_nonnegative(row[6]),
                current_driver_id=(
                    _validated_identifier(row[7], "driver-", "driver identity")
                    if row[7]
                    else None
                ),
                transition_sequence=_validated_count(row[8]),
                last_transition_id=_validated_identifier(
                    row[9], "runner-transition-", "runner transition identity"
                ),
                session_date=date.fromisoformat(row[10]),
                mode=RunnerMode(row[11]),
                reason_setting=ReasonSetting(row[12]),
                updated_at=_parsed_moment(row[13], "runner update time"),
            )
        except (TypeError, ValueError) as exc:
            raise RunnerError("persisted runner state is malformed") from None
        _validate_record_invariants(record, len(self.config.tick_timestamps))
        return record

    def load_tick(self, index: int) -> RunnerTickRecord:
        if not isinstance(index, int) or isinstance(index, bool) or index < 0:
            raise RunnerError("tick index is invalid")
        row = self._connection.execute(
            "SELECT runner_id, tick_index, scheduled_at, driver_id, state, "
            "driver_record_sequence, driver_receipt_sequence, driver_state, "
            "driver_outcome, driver_reason, driver_receipt_at FROM runner_ticks "
            "WHERE runner_id=? AND tick_index=?",
            (self.config.runner_id, index),
        ).fetchone()
        if row is None:
            raise RunnerError("runner tick record is missing")
        try:
            tick_record = RunnerTickRecord(
                runner_id=_validated_identifier(row[0], "runner-", "runner identity"),
                tick_index=_validated_nonnegative(row[1]),
                scheduled_at=_parsed_moment(row[2], "scheduled tick time"),
                driver_id=_validated_identifier(row[3], "driver-", "driver identity"),
                state=TickState(row[4]),
                driver_record_sequence=(
                    _validated_count(row[5]) if row[5] is not None else None
                ),
                driver_receipt_sequence=(
                    _validated_count(row[6]) if row[6] is not None else None
                ),
                driver_state=DriverState(row[7]) if row[7] else None,
                driver_outcome=DriverOutcome(row[8]) if row[8] else None,
                driver_reason=DriverReason(row[9]) if row[9] else None,
                driver_receipt_at=(
                    _parsed_moment(row[10], "driver receipt time") if row[10] else None
                ),
            )
        except (TypeError, ValueError):
            raise RunnerError("persisted runner tick is malformed") from None
        expected = derive_driver_tick(self.config, index)
        if (
            tick_record.runner_id != self.config.runner_id
            or tick_record.scheduled_at != self.config.tick_timestamps[index]
            or tick_record.driver_id != expected.driver_id
        ):
            raise RunnerError("persisted runner tick identity is malformed")
        return tick_record

    def _require_same_config(self, record: RunnerRecord) -> None:
        if (
            record.maximum_tick_count != self.config.maximum_tick_count
            or record.session_date != self.config.session_date
            or record.mode is not self.config.mode
            or record.reason_setting is not self.config.reason_setting
        ):
            raise RunnerError("runner identity is bound to another configuration")
        stored = self._connection.execute(
            "SELECT driver_store_id FROM runner_runs WHERE runner_id=?",
            (record.runner_id,),
        ).fetchone()
        if stored != (_store_id(self.config.driver_database_path),):
            raise RunnerError("runner identity is bound to another driver store")
        rows = self._connection.execute(
            "SELECT tick_index, scheduled_at, driver_id FROM runner_ticks "
            "WHERE runner_id=? ORDER BY tick_index",
            (record.runner_id,),
        ).fetchall()
        expected = [
            (index, stamp.isoformat(), derive_driver_tick(self.config, index).driver_id)
            for index, stamp in enumerate(self.config.tick_timestamps)
        ]
        if rows != expected:
            raise RunnerError("runner identity is bound to another tick schedule")
        self._validate_transition_journal(record)
        self._validate_current_driver(record)

    def _validate_current_driver(self, record: RunnerRecord) -> None:
        expected: str | None = None
        if record.state is RunnerState.TICK_STARTED:
            if record.next_tick_index >= len(self.config.tick_timestamps):
                raise RunnerError("started runner index is outside the schedule")
            expected = derive_driver_tick(
                self.config, record.next_tick_index
            ).driver_id
        elif record.state in {
            RunnerState.FAILED_CLOSED,
            RunnerState.AMBIGUOUS_RESTART,
            RunnerState.EXPIRED,
        } and record.next_tick_index < len(self.config.tick_timestamps):
            current_tick = self.load_tick(record.next_tick_index)
            if current_tick.state is TickState.STARTED:
                expected = derive_driver_tick(
                    self.config, record.next_tick_index
                ).driver_id
        if expected is None and record.completed_tick_count > 0:
            expected = derive_driver_tick(
                self.config, record.completed_tick_count - 1
            ).driver_id
        if record.current_driver_id != expected:
            raise RunnerError("current driver identity conflicts with runner progress")

    def _validate_transition_journal(self, record: RunnerRecord) -> None:
        rows = self._connection.execute(
            "SELECT transition_id, sequence, source_state, target_state "
            "FROM runner_transitions WHERE runner_id=? ORDER BY sequence",
            (record.runner_id,),
        ).fetchall()
        if len(rows) != record.transition_sequence:
            raise RunnerError("runner transition journal is incomplete")
        previous: RunnerState | None = None
        for expected_sequence, row in enumerate(rows, 1):
            try:
                source = RunnerState(row[2]) if row[2] else None
                target = RunnerState(row[3])
            except ValueError:
                raise RunnerError("runner transition journal is malformed") from None
            if (
                row[0] != _transition_id(record.runner_id, expected_sequence)
                or row[1] != expected_sequence
                or source is not previous
                or (source is not None and target not in ALLOWED_TRANSITIONS[source])
            ):
                raise RunnerError("runner transition journal is malformed")
            previous = target
        if (
            previous is not record.state
            or rows[-1][0] != record.last_transition_id
        ):
            raise RunnerError("runner transition journal does not match current state")

    def _validate_progress(self, record: RunnerRecord) -> None:
        if record.completed_tick_count == 0:
            return
        for index in range(record.completed_tick_count):
            self._validate_completed_tick(index)

    def _validate_completed_tick(self, index: int) -> None:
        tick_record = self.load_tick(index)
        if tick_record.state is not TickState.COMPLETED:
            raise RunnerError("completed runner progress is missing tick evidence")
        driver_tick = derive_driver_tick(self.config, index)
        try:
            driver_record = self.driver.load_record(driver_tick.driver_id)
            driver_receipt = self.driver.load_receipt(driver_tick.driver_id)
        except Exception:
            raise RunnerError("completed driver evidence is unreadable") from None
        if not _driver_record_is_exact(driver_record, driver_tick):
            raise RunnerError("completed driver evidence is not exact")
        if (
            tick_record.driver_record_sequence != driver_record.sequence
            or tick_record.driver_state is not driver_record.state
            or tick_record.driver_outcome is not driver_record.outcome
            or tick_record.driver_reason is not driver_record.reason
        ):
            raise RunnerError("completed runner receipt conflicts with driver evidence")
        if driver_receipt is None:
            if (
                tick_record.driver_receipt_sequence is not None
                or tick_record.driver_receipt_at is not None
                or driver_record.outcome
                in {
                    DriverOutcome.WAIT,
                    DriverOutcome.OFFLINE_COMPLETE,
                    DriverOutcome.COMPLETED_NO_ACTION,
                    DriverOutcome.OPERATOR_REVIEW_REQUIRED,
                    DriverOutcome.SUSPENDED,
                }
            ):
                raise RunnerError("completed driver receipt is missing")
        elif (
            not _driver_receipt_is_exact(driver_receipt, driver_record)
            or tick_record.driver_receipt_sequence != driver_receipt.sequence
            or tick_record.driver_receipt_at != driver_receipt.persisted_at
        ):
            raise RunnerError("completed driver receipt is not exact")

    def _state_time(self, record: RunnerRecord) -> datetime:
        if record.next_tick_index < len(self.config.tick_timestamps):
            return self.config.tick_timestamps[record.next_tick_index]
        return record.updated_at

    def _initial_time(self) -> datetime:
        if self.config.tick_timestamps:
            return self.config.tick_timestamps[0]
        return datetime.combine(self.config.session_date, datetime.min.time(), timezone.utc)

    def _result(self, record: RunnerRecord) -> RunnerResult:
        if not record.is_terminal or record.outcome is None or record.reason is None:
            raise RunnerError("runner has no terminal result")
        return RunnerResult(
            runner_id=record.runner_id,
            state=record.state,
            outcome=record.outcome,
            reason=record.reason,
            completed_tick_count=record.completed_tick_count,
            next_tick_index=record.next_tick_index,
            transition_sequence=record.transition_sequence,
            current_driver_id=record.current_driver_id,
        )

    def _rollback(self) -> None:
        if self._connection.in_transaction:
            try:
                self._connection.execute("ROLLBACK")
            except sqlite3.Error:
                pass


def derive_driver_tick(config: RunnerConfig, index: int) -> DriverTick:
    """Derive stable, constrained driver identities for one scheduled tick."""
    config.validate()
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(
        config.tick_timestamps
    ):
        raise RunnerError("tick index is outside the configured schedule")
    moment = config.tick_timestamps[index]
    identity_material = "|".join(
        (
            config.runner_id,
            _store_id(config.runner_database_path),
            _store_id(config.driver_database_path),
            config.session_date.isoformat(),
            str(index),
            moment.isoformat(),
        )
    )
    token = hashlib.sha256(identity_material.encode("ascii")).hexdigest()[:24]
    return DriverTick(
        driver_id=f"driver-{token}",
        cycle_id=f"cycle-{token}",
        scheduler_run_id=f"scheduler-{token}",
        controller_id=f"controller-{token}",
        driver_transition_id=f"driver-transition-{token}",
        scheduler_transition_id=f"scheduler-transition-{token}",
        controller_transition_id=f"controller-transition-{token}",
        scheduler_restart_transition_id=f"scheduler-restart-{token}",
        session_date=config.session_date,
        now=moment,
        expires_at=moment + timedelta(days=1),
    )


def _driver_record_is_exact(record: DriverRecord | None, tick: DriverTick) -> bool:
    return bool(
        record is not None
        and record.driver_id == tick.driver_id
        and record.cycle_id == tick.cycle_id
        and record.scheduler_run_id == tick.scheduler_run_id
        and record.controller_id == tick.controller_id
        and record.driver_transition_id == tick.driver_transition_id
        and record.scheduler_transition_id == tick.scheduler_transition_id
        and record.controller_transition_id == tick.controller_transition_id
        and record.scheduler_restart_transition_id == tick.scheduler_restart_transition_id
        and record.session_date == tick.session_date
        and record.created_at == tick.now
        and record.expires_at == tick.expires_at
        and record.state in {
            DriverState.COMPLETED_NO_ACTION,
            DriverState.FAILED_CLOSED,
            DriverState.AMBIGUOUS_RESTART,
            DriverState.EXPIRED,
        }
        and record.outcome is not None
        and record.reason is not None
    )


def _driver_receipt_is_exact(
    receipt: DriverReceipt | None, record: DriverRecord
) -> bool:
    return bool(
        receipt is not None
        and receipt.driver_id == record.driver_id
        and receipt.cycle_id == record.cycle_id
        and receipt.scheduler_run_id == record.scheduler_run_id
        and receipt.controller_id == record.controller_id
        and receipt.outcome is record.outcome
        and receipt.reason is DriverReason.ORCHESTRATOR_RESULT
        and receipt.orchestrator_outcome is record.orchestrator_outcome
        and receipt.orchestrator_reason is record.orchestrator_reason
        and receipt.sequence + 1 == record.sequence
        and receipt.persisted_at == record.updated_at
        and receipt.permits_automated_action is False
    )


def _result_matches_record(result: DriverResult, record: DriverRecord) -> bool:
    return bool(
        isinstance(result, DriverResult)
        and result.state is record.state
        and result.outcome is record.outcome
        and result.reason is record.reason
        and result.orchestrator_outcome is record.orchestrator_outcome
        and result.orchestrator_reason is record.orchestrator_reason
        and result.permits_automated_action is False
    )


def _validate_record_invariants(record: RunnerRecord, tick_count: int) -> None:
    if (
        record.maximum_tick_count < tick_count
        or record.next_tick_index > tick_count
        or record.completed_tick_count > tick_count
        or record.completed_tick_count != record.next_tick_index
    ):
        raise RunnerError("persisted runner counters are malformed")
    has_result = record.outcome is not None and record.reason is not None
    if (record.is_terminal or record.state is RunnerState.STOP_REQUESTED) != has_result:
        raise RunnerError("persisted runner terminal fields are malformed")
    if record.state is RunnerState.TICK_STARTED and record.current_driver_id is None:
        raise RunnerError("started runner is missing its driver identity")
    if record.state is RunnerState.TICK_COMPLETED and record.completed_tick_count < 1:
        raise RunnerError("completed tick state is missing progress")
    if record.state is RunnerState.COMPLETED:
        if record.reason is RunnerReason.EMPTY_SCHEDULE and tick_count != 0:
            raise RunnerError("empty completion conflicts with the tick schedule")
        if record.outcome is RunnerOutcome.COMPLETED_BOUND and record.reason is not RunnerReason.EMPTY_SCHEDULE:
            if tick_count == 0 or record.completed_tick_count != tick_count:
                raise RunnerError("bounded completion did not exhaust its schedule")
        if record.outcome in {
            RunnerOutcome.STOPPED_ON_REVIEW,
            RunnerOutcome.STOPPED_ON_SUSPENSION,
        } and record.completed_tick_count < 1:
            raise RunnerError("runner stop is missing completed tick evidence")


def _transition_id(runner_id: str, sequence: int) -> str:
    token = hashlib.sha256(f"{runner_id}|transition|{sequence}".encode("ascii")).hexdigest()[:24]
    return f"runner-transition-{token}"


def _store_id(path: Path) -> str:
    token = hashlib.sha256(
        os.path.normcase(os.fspath(path.resolve(strict=False))).encode("utf-8")
    ).hexdigest()[:32]
    return f"store-{token}"


def _validated_database_path(path: object, label: str) -> Path:
    if not isinstance(path, Path):
        raise RunnerError(f"{label} path is invalid")
    if os.fspath(path) == ":memory:" or not path.is_absolute():
        raise RunnerError(f"{label} path is not an explicit durable path")
    resolved = path.resolve(strict=False)
    try:
        if os.path.commonpath([os.fspath(resolved), os.fspath(REPOSITORY_ROOT)]) == os.fspath(
            REPOSITORY_ROOT
        ):
            raise RunnerError(f"{label} path may not use repository data")
    except ValueError:
        pass
    if any(
        part.casefold() in {"prod", "production", "prod-data", "production-data"}
        or part.casefold().startswith(("prod-", "prod_", "production-", "production_"))
        for part in resolved.parts
    ):
        raise RunnerError(f"{label} path may not use production data")
    if resolved.suffix.casefold() not in {".sqlite", ".sqlite3", ".db"}:
        raise RunnerError(f"{label} path is malformed")
    return resolved


def _validated_identifier(value: object, prefix: str, label: str) -> str:
    if not isinstance(value, str) or not value.startswith(prefix):
        raise RunnerError(f"{label} is not constrained")
    if value != value.strip() or len(value) > 96:
        raise RunnerError(f"{label} is malformed")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise RunnerError(f"{label} has an unsupported character")
    return value


def _validated_moment(value: object, label: str) -> datetime:
    if not isinstance(value, datetime):
        raise RunnerError(f"{label} is malformed")
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise RunnerError(f"{label} is not timezone aware")
    return value


def _parsed_moment(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise RunnerError(f"persisted {label} is missing")
    try:
        return _validated_moment(datetime.fromisoformat(value), label)
    except ValueError:
        raise RunnerError(f"persisted {label} is malformed") from None


def _validated_count(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise RunnerError("persisted count is malformed")
    return value


def _validated_nonnegative(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise RunnerError("persisted count is malformed")
    return value


__all__ = [
    "ALLOWED_TRANSITIONS",
    "MAXIMUM_TICKS",
    "TERMINAL_STATES",
    "BoundedOfflineShadowRunner",
    "ReasonSetting",
    "RunnerConfig",
    "RunnerError",
    "RunnerMode",
    "RunnerOutcome",
    "RunnerReason",
    "RunnerRecord",
    "RunnerResult",
    "RunnerState",
    "RunnerTickRecord",
    "TickState",
    "derive_driver_tick",
]
