"""Durable heartbeat and liveness evaluation for the bounded offline runner.

This module consumes already-decided runner outcomes and injected timestamps.
It never runs the runner, reads a clock, contacts a broker, or clears a durable
suspension.  Its only controller-facing output is a typed request to suspend.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path

from src.durable_suspension_controller import CAUSE_SEVERITY, SuspensionCause
from src.offline_shadow_runner import RunnerOutcome


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


class LivenessError(RuntimeError):
    """A fixed, privacy-safe liveness refusal."""


class LivenessState(str, Enum):
    UNKNOWN = "UNKNOWN"
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    STALE = "STALE"
    MISSED_CYCLE = "MISSED_CYCLE"
    SUSPENSION_REQUIRED = "SUSPENSION_REQUIRED"
    FAILED_CLOSED = "FAILED_CLOSED"


class LivenessReason(str, Enum):
    NO_HEARTBEAT = "NO_HEARTBEAT"
    HEARTBEAT_CURRENT = "HEARTBEAT_CURRENT"
    HEARTBEAT_STALE = "HEARTBEAT_STALE"
    EXPECTED_TICK_MISSED = "EXPECTED_TICK_MISSED"
    RUNNER_FAILED = "RUNNER_FAILED"
    RUNNER_AMBIGUOUS = "RUNNER_AMBIGUOUS"
    RECEIPT_INVALID = "RECEIPT_INVALID"
    CLOCK_INVALID = "CLOCK_INVALID"
    CONFIG_INVALID = "CONFIG_INVALID"
    SEQUENCE_REGRESSION = "SEQUENCE_REGRESSION"
    DUPLICATE_HEARTBEAT = "DUPLICATE_HEARTBEAT"
    UNKNOWN_REASON = "UNKNOWN_REASON"


class StoredRunnerOutcome(str, Enum):
    NO_RESULT = "NO_RESULT"
    COMPLETED_BOUND = RunnerOutcome.COMPLETED_BOUND.value
    STOPPED_ON_REVIEW = RunnerOutcome.STOPPED_ON_REVIEW.value
    STOPPED_ON_SUSPENSION = RunnerOutcome.STOPPED_ON_SUSPENSION.value
    STOPPED_ON_FAILURE = RunnerOutcome.STOPPED_ON_FAILURE.value
    STOPPED_ON_AMBIGUITY = RunnerOutcome.STOPPED_ON_AMBIGUITY.value
    EXPIRED = RunnerOutcome.EXPIRED.value
    FAILED_CLOSED = RunnerOutcome.FAILED_CLOSED.value


@dataclass(frozen=True)
class FreshnessPolicy:
    expected_interval: timedelta
    allowed_lateness: timedelta
    maximum_observation_age: timedelta
    maximum_consecutive_missed_cycles: int

    def validate(self) -> None:
        for label, value, allow_zero in (
            ("expected interval", self.expected_interval, False),
            ("allowed lateness", self.allowed_lateness, True),
            ("maximum observation age", self.maximum_observation_age, False),
        ):
            if not isinstance(value, timedelta):
                raise LivenessError(f"{label} is invalid")
            if value < timedelta(0) or (not allow_zero and value == timedelta(0)):
                raise LivenessError(f"{label} is invalid")
        if self.maximum_observation_age < self.allowed_lateness:
            raise LivenessError("maximum observation age is invalid")
        if (
            not isinstance(self.maximum_consecutive_missed_cycles, int)
            or isinstance(self.maximum_consecutive_missed_cycles, bool)
            or self.maximum_consecutive_missed_cycles < 1
        ):
            raise LivenessError("maximum missed cycles is invalid")


@dataclass(frozen=True)
class RunnerHeartbeat:
    runner_id: str
    heartbeat_sequence: int
    expected_cycle_timestamp: datetime
    observed_completion_timestamp: datetime
    runner_outcome: RunnerOutcome

    def validate(self) -> None:
        _identifier(self.runner_id, "runner-", "runner identity")
        _positive(self.heartbeat_sequence, "heartbeat sequence")
        _moment(self.expected_cycle_timestamp, "expected cycle timestamp")
        _moment(self.observed_completion_timestamp, "observed completion timestamp")
        if not isinstance(self.runner_outcome, RunnerOutcome):
            raise LivenessError("runner outcome is invalid")


@dataclass(frozen=True)
class LivenessRecord:
    runner_id: str
    heartbeat_sequence: int | None
    expected_cycle_timestamp: datetime
    observed_completion_timestamp: datetime | None
    runner_outcome: StoredRunnerOutcome
    state: LivenessState
    reason: LivenessReason
    evaluation_time: datetime
    transition_id: str
    transition_sequence: int
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.runner_id, "runner-", "runner identity")
        if self.heartbeat_sequence is not None:
            _positive(self.heartbeat_sequence, "heartbeat sequence")
        _moment(self.expected_cycle_timestamp, "expected cycle timestamp")
        if self.observed_completion_timestamp is not None:
            _moment(self.observed_completion_timestamp, "observed completion timestamp")
        if not isinstance(self.runner_outcome, StoredRunnerOutcome):
            raise LivenessError("stored runner outcome is invalid")
        if not isinstance(self.state, LivenessState) or not isinstance(
            self.reason, LivenessReason
        ):
            raise LivenessError("liveness state or reason is invalid")
        _moment(self.evaluation_time, "evaluation time")
        _identifier(self.transition_id, "liveness-transition-", "transition identity")
        _positive(self.transition_sequence, "transition sequence")
        if self.heartbeat_sequence is None and self.runner_outcome is not StoredRunnerOutcome.NO_RESULT:
            raise LivenessError("runner outcome lacks heartbeat identity")
        if self.heartbeat_sequence is not None and self.runner_outcome is StoredRunnerOutcome.NO_RESULT:
            raise LivenessError("heartbeat lacks runner outcome")
        if (self.heartbeat_sequence is None) != (
            self.observed_completion_timestamp is None
        ):
            raise LivenessError("heartbeat identity and observation conflict")
        allowed_reasons = {
            LivenessState.UNKNOWN: {LivenessReason.NO_HEARTBEAT},
            LivenessState.HEALTHY: {LivenessReason.HEARTBEAT_CURRENT},
            LivenessState.DEGRADED: {
                LivenessReason.DUPLICATE_HEARTBEAT,
                LivenessReason.UNKNOWN_REASON,
            },
            LivenessState.STALE: {LivenessReason.HEARTBEAT_STALE},
            LivenessState.MISSED_CYCLE: {LivenessReason.EXPECTED_TICK_MISSED},
            LivenessState.SUSPENSION_REQUIRED: {
                LivenessReason.EXPECTED_TICK_MISSED,
                LivenessReason.RUNNER_AMBIGUOUS,
            },
            LivenessState.FAILED_CLOSED: {
                LivenessReason.RUNNER_FAILED,
                LivenessReason.RECEIPT_INVALID,
                LivenessReason.CLOCK_INVALID,
                LivenessReason.CONFIG_INVALID,
                LivenessReason.SEQUENCE_REGRESSION,
                LivenessReason.DUPLICATE_HEARTBEAT,
            },
        }
        if self.reason not in allowed_reasons[self.state]:
            raise LivenessError("liveness state and reason conflict")
        if self.state is LivenessState.HEALTHY and self.heartbeat_sequence is None:
            raise LivenessError("healthy liveness requires heartbeat evidence")
        required_projection = {
            StoredRunnerOutcome.STOPPED_ON_AMBIGUITY: (
                LivenessState.SUSPENSION_REQUIRED,
                LivenessReason.RUNNER_AMBIGUOUS,
            ),
            StoredRunnerOutcome.STOPPED_ON_FAILURE: (
                LivenessState.FAILED_CLOSED,
                LivenessReason.RUNNER_FAILED,
            ),
            StoredRunnerOutcome.FAILED_CLOSED: (
                LivenessState.FAILED_CLOSED,
                LivenessReason.RUNNER_FAILED,
            ),
            StoredRunnerOutcome.STOPPED_ON_SUSPENSION: (
                LivenessState.FAILED_CLOSED,
                LivenessReason.RUNNER_FAILED,
            ),
            StoredRunnerOutcome.EXPIRED: (
                LivenessState.STALE,
                LivenessReason.HEARTBEAT_STALE,
            ),
        }
        invariant_failure_reasons = {
            LivenessReason.RECEIPT_INVALID,
            LivenessReason.CLOCK_INVALID,
            LivenessReason.CONFIG_INVALID,
            LivenessReason.SEQUENCE_REGRESSION,
            LivenessReason.DUPLICATE_HEARTBEAT,
        }
        if self.runner_outcome in required_projection:
            exact = (self.state, self.reason) == required_projection[self.runner_outcome]
            invariant_failure = (
                self.state is LivenessState.FAILED_CLOSED
                and self.reason in invariant_failure_reasons
            )
            if not exact and not invariant_failure:
                raise LivenessError("runner outcome conflicts with liveness projection")
        if self.runner_outcome is StoredRunnerOutcome.NO_RESULT and self.state in {
            LivenessState.HEALTHY,
            LivenessState.DEGRADED,
            LivenessState.STALE,
            LivenessState.MISSED_CYCLE,
        }:
            raise LivenessError("liveness state requires a runner result")
        if self.permits_automated_action is not False:
            raise LivenessError("liveness never authorizes action")


@dataclass(frozen=True)
class LivenessSuspensionRequest:
    runner_id: str
    request_suspension: bool
    cause: SuspensionCause
    reason: LivenessReason
    transition_id: str
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        _identifier(self.runner_id, "runner-", "runner identity")
        _identifier(self.transition_id, "liveness-transition-", "transition identity")
        if not isinstance(self.request_suspension, bool):
            raise LivenessError("suspension request is invalid")
        if not isinstance(self.cause, SuspensionCause):
            raise LivenessError("suspension cause is invalid")
        if not isinstance(self.reason, LivenessReason):
            raise LivenessError("liveness reason is invalid")
        if self.request_suspension != (self.cause is not SuspensionCause.NONE):
            raise LivenessError("suspension request and cause conflict")
        if self.permits_automated_action is not False:
            raise LivenessError("liveness never authorizes action")


@dataclass(frozen=True)
class LivenessDecision:
    record: LivenessRecord
    consecutive_missed_cycles: int
    suspension: LivenessSuspensionRequest
    permits_automated_action: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.record, LivenessRecord):
            raise LivenessError("liveness record is invalid")
        if (
            not isinstance(self.consecutive_missed_cycles, int)
            or isinstance(self.consecutive_missed_cycles, bool)
            or self.consecutive_missed_cycles < 0
        ):
            raise LivenessError("missed cycle count is invalid")
        if not isinstance(self.suspension, LivenessSuspensionRequest):
            raise LivenessError("suspension request is invalid")
        if (
            self.suspension.runner_id != self.record.runner_id
            or self.suspension.transition_id != self.record.transition_id
            or self.suspension.reason is not self.record.reason
        ):
            raise LivenessError("suspension request is bound to another record")
        if self.record.state in {
            LivenessState.STALE,
            LivenessState.SUSPENSION_REQUIRED,
            LivenessState.FAILED_CLOSED,
        } and not self.suspension.request_suspension:
            raise LivenessError("unsafe liveness state requires suspension")
        if self.permits_automated_action is not False:
            raise LivenessError("liveness never authorizes action")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS liveness_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS liveness_runs (
    runner_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    reason TEXT NOT NULL,
    last_heartbeat_sequence INTEGER,
    consecutive_missed_cycles INTEGER NOT NULL CHECK (consecutive_missed_cycles >= 0),
    suspension_required INTEGER NOT NULL DEFAULT 0 CHECK (suspension_required IN (0, 1)),
    suspension_cause TEXT NOT NULL,
    transition_sequence INTEGER NOT NULL CHECK (transition_sequence >= 1),
    last_transition_id TEXT NOT NULL UNIQUE,
    last_expected_at TEXT NOT NULL,
    last_observed_at TEXT,
    last_evaluated_at TEXT NOT NULL,
    permits_automated_action INTEGER NOT NULL DEFAULT 0 CHECK (permits_automated_action = 0)
);
CREATE TABLE IF NOT EXISTS heartbeat_records (
    transition_id TEXT PRIMARY KEY,
    runner_id TEXT NOT NULL,
    heartbeat_sequence INTEGER,
    expected_cycle_timestamp TEXT NOT NULL,
    observed_completion_timestamp TEXT,
    runner_outcome TEXT NOT NULL,
    liveness_state TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    evaluation_time TEXT NOT NULL,
    transition_sequence INTEGER NOT NULL CHECK (transition_sequence >= 1),
    permits_automated_action INTEGER NOT NULL DEFAULT 0 CHECK (permits_automated_action = 0),
    UNIQUE (runner_id, transition_sequence),
    FOREIGN KEY (runner_id) REFERENCES liveness_runs(runner_id)
);
CREATE TABLE IF NOT EXISTS liveness_checkpoints (
    transition_id TEXT PRIMARY KEY,
    runner_id TEXT NOT NULL,
    transition_sequence INTEGER NOT NULL CHECK (transition_sequence >= 1),
    last_heartbeat_sequence INTEGER,
    consecutive_missed_cycles INTEGER NOT NULL CHECK (consecutive_missed_cycles >= 0),
    suspension_required INTEGER NOT NULL CHECK (suspension_required IN (0, 1)),
    suspension_cause TEXT NOT NULL,
    last_expected_at TEXT NOT NULL,
    last_evaluated_at TEXT NOT NULL,
    UNIQUE (runner_id, transition_sequence),
    FOREIGN KEY (transition_id) REFERENCES heartbeat_records(transition_id)
);
"""


class DurableRunnerLiveness:
    def __init__(self, database_path: Path, *, policy: FreshnessPolicy) -> None:
        if not isinstance(database_path, Path) or os.fspath(database_path) == ":memory:":
            raise LivenessError("a durable liveness database path is required")
        if not database_path.is_absolute():
            raise LivenessError("liveness database path must be explicit")
        resolved = database_path.resolve(strict=False)
        try:
            if os.path.commonpath([os.fspath(resolved), os.fspath(REPOSITORY_ROOT)]) == os.fspath(REPOSITORY_ROOT):
                raise LivenessError("liveness database may not use repository data")
        except ValueError:
            pass
        if not isinstance(policy, FreshnessPolicy):
            raise LivenessError("a typed freshness policy is required")
        policy.validate()
        self.database_path = database_path
        self.policy = policy
        try:
            database_path.parent.mkdir(parents=True, exist_ok=True)
            self._connection = sqlite3.connect(os.fspath(database_path), isolation_level=None)
            self._connection.execute("PRAGMA journal_mode=WAL")
            self._connection.execute("PRAGMA synchronous=FULL")
            self._connection.execute("PRAGMA foreign_keys=ON")
            self._connection.executescript(_SCHEMA)
            self._schema_version()
        except LivenessError:
            raise
        except Exception:
            raise LivenessError("liveness initialization failed") from None

    def _schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM liveness_meta WHERE key='schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO liveness_meta (key, value) VALUES ('schema_version', '1')"
            )
        elif row[0] != "1":
            raise LivenessError("liveness schema version is unsupported")
        signature = hashlib.sha256(
            "|".join(
                (
                    str(self.policy.expected_interval.total_seconds()),
                    str(self.policy.allowed_lateness.total_seconds()),
                    str(self.policy.maximum_observation_age.total_seconds()),
                    str(self.policy.maximum_consecutive_missed_cycles),
                )
            ).encode("ascii")
        ).hexdigest()
        stored = self._connection.execute(
            "SELECT value FROM liveness_meta WHERE key='policy_signature'"
        ).fetchone()
        if stored is None:
            self._connection.execute(
                "INSERT INTO liveness_meta (key, value) VALUES ('policy_signature', ?)",
                (signature,),
            )
        elif stored[0] != signature:
            raise LivenessError("freshness policy does not match durable state")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableRunnerLiveness":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def evaluate(
        self,
        runner_id: str,
        *,
        heartbeat: RunnerHeartbeat | None,
        expected_cycle_timestamp: datetime,
        evaluation_time: datetime,
        alert_delivery_confirmed: bool | None,
        transition_id: str,
    ) -> LivenessDecision:
        _identifier(runner_id, "runner-", "runner identity")
        _identifier(transition_id, "liveness-transition-", "transition identity")
        expected = _moment(expected_cycle_timestamp, "expected cycle timestamp")
        evaluated = _moment(evaluation_time, "evaluation time")
        if (
            alert_delivery_confirmed is not True
            and alert_delivery_confirmed is not False
            and alert_delivery_confirmed is not None
        ):
            raise LivenessError("alert delivery evidence is invalid")
        if heartbeat is not None:
            if not isinstance(heartbeat, RunnerHeartbeat):
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.RECEIPT_INVALID,
                )
            try:
                heartbeat.validate()
            except LivenessError:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.RECEIPT_INVALID,
                )
            if (
                heartbeat.runner_id != runner_id
                or heartbeat.expected_cycle_timestamp != expected
            ):
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.RECEIPT_INVALID,
                    heartbeat=heartbeat,
                )
        duplicate_transition = self.load_record(transition_id)
        if duplicate_transition is not None:
            raise LivenessError("transition identity is already used")

        current = self._load_run(runner_id)
        if expected > evaluated:
            return self._fail_closed(
                runner_id, expected, evaluated, transition_id,
                LivenessReason.CLOCK_INVALID, heartbeat=heartbeat,
            )
        if heartbeat is None:
            if current and evaluated < current[9]:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.CLOCK_INVALID,
                )
            if current and expected != current[8] + self.policy.expected_interval:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.CONFIG_INVALID,
                )
            misses = (current[3] if current else 0) + 1
            if misses >= self.policy.maximum_consecutive_missed_cycles:
                return self._persist(
                    runner_id, None, expected, None, StoredRunnerOutcome.NO_RESULT,
                    LivenessState.SUSPENSION_REQUIRED,
                    LivenessReason.EXPECTED_TICK_MISSED, evaluated, transition_id,
                    misses, True, SuspensionCause.MONITORING_UNHEALTHY,
                )
            return self._persist(
                runner_id, None, expected, None, StoredRunnerOutcome.NO_RESULT,
                LivenessState.UNKNOWN, LivenessReason.NO_HEARTBEAT, evaluated,
                transition_id, misses,
                bool(current and current[4]),
                current[5] if current and current[4] else SuspensionCause.NONE,
            )

        observed = heartbeat.observed_completion_timestamp
        if expected > evaluated or observed > evaluated or observed < expected:
            return self._fail_closed(
                runner_id, expected, evaluated, transition_id,
                LivenessReason.CLOCK_INVALID, heartbeat=heartbeat,
            )
        if current and current[2] is not None:
            last_sequence = current[2]
            if heartbeat.heartbeat_sequence < last_sequence:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.SEQUENCE_REGRESSION, heartbeat=heartbeat,
                )
            if heartbeat.heartbeat_sequence == last_sequence:
                if evaluated < current[9]:
                    return self._fail_closed(
                        runner_id, expected, evaluated, transition_id,
                        LivenessReason.CLOCK_INVALID, heartbeat=heartbeat,
                    )
                prior = self._canonical_heartbeat(runner_id, last_sequence)
                if prior is None or not _same_heartbeat(prior, heartbeat):
                    return self._fail_closed(
                        runner_id, expected, evaluated, transition_id,
                        LivenessReason.DUPLICATE_HEARTBEAT, heartbeat=heartbeat,
                    )
                return self._persist(
                    runner_id, heartbeat.heartbeat_sequence, expected, observed,
                    StoredRunnerOutcome(heartbeat.runner_outcome.value),
                    LivenessState.DEGRADED, LivenessReason.DUPLICATE_HEARTBEAT,
                    evaluated, transition_id, current[3], bool(current[4]),
                    current[5] if current[4] else SuspensionCause.NONE,
                )

        if current:
            if evaluated < current[9]:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.CLOCK_INVALID, heartbeat=heartbeat,
                )
            expected_delta = expected - current[8]
            if (
                expected_delta <= timedelta(0)
                or expected_delta % self.policy.expected_interval != timedelta(0)
            ):
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.CONFIG_INVALID, heartbeat=heartbeat,
                )
            expected_steps = expected_delta // self.policy.expected_interval
            sequence_steps = (
                heartbeat.heartbeat_sequence - current[2]
                if current[2] is not None
                else 1
            )
            if expected_steps != sequence_steps:
                return self._fail_closed(
                    runner_id, expected, evaluated, transition_id,
                    LivenessReason.CONFIG_INVALID, heartbeat=heartbeat,
                )

        outcome = heartbeat.runner_outcome
        if outcome is RunnerOutcome.STOPPED_ON_AMBIGUITY:
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value), LivenessState.SUSPENSION_REQUIRED,
                LivenessReason.RUNNER_AMBIGUOUS, evaluated, transition_id, 0, True,
                SuspensionCause.RESTART_AMBIGUITY,
            )
        if outcome in {
            RunnerOutcome.STOPPED_ON_FAILURE,
            RunnerOutcome.FAILED_CLOSED,
            RunnerOutcome.STOPPED_ON_SUSPENSION,
        }:
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value), LivenessState.FAILED_CLOSED,
                LivenessReason.RUNNER_FAILED, evaluated, transition_id, 0, True,
                SuspensionCause.MONITORING_UNHEALTHY,
            )
        if outcome is RunnerOutcome.EXPIRED:
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value), LivenessState.STALE,
                LivenessReason.HEARTBEAT_STALE, evaluated, transition_id, 0, True,
                SuspensionCause.STALE_DATA,
            )

        age = evaluated - observed
        lateness = observed - expected
        prior_misses = current[3] if current else 0
        sequence_gap = (
            heartbeat.heartbeat_sequence - current[2] - 1
            if current and current[2] is not None
            else 0
        )
        if age > self.policy.maximum_observation_age:
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value), LivenessState.STALE,
                LivenessReason.HEARTBEAT_STALE, evaluated, transition_id,
                prior_misses + max(1, sequence_gap), True, SuspensionCause.STALE_DATA,
            )
        if lateness > self.policy.allowed_lateness or sequence_gap > 0:
            misses = prior_misses + max(1, sequence_gap)
            required = misses >= self.policy.maximum_consecutive_missed_cycles
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value),
                LivenessState.SUSPENSION_REQUIRED if required else LivenessState.MISSED_CYCLE,
                LivenessReason.EXPECTED_TICK_MISSED, evaluated, transition_id,
                misses, required or bool(current and current[4]),
                (
                    current[5]
                    if current and current[4]
                    else SuspensionCause.MONITORING_UNHEALTHY
                    if required
                    else SuspensionCause.NONE
                ),
            )
        if alert_delivery_confirmed is not True:
            return self._persist(
                runner_id, heartbeat.heartbeat_sequence, expected, observed,
                StoredRunnerOutcome(outcome.value), LivenessState.DEGRADED,
                LivenessReason.UNKNOWN_REASON, evaluated, transition_id, 0,
                bool(current and current[4]),
                current[5] if current and current[4] else SuspensionCause.NONE,
            )
        return self._persist(
            runner_id, heartbeat.heartbeat_sequence, expected, observed,
            StoredRunnerOutcome(outcome.value), LivenessState.HEALTHY,
            LivenessReason.HEARTBEAT_CURRENT, evaluated, transition_id, 0,
            bool(current and current[4]),
            current[5] if current and current[4] else SuspensionCause.NONE,
        )

    def _fail_closed(
        self, runner_id, expected, evaluated, transition_id, reason, *, heartbeat=None
    ) -> LivenessDecision:
        current = self._load_run(runner_id)
        return self._persist(
            runner_id,
            heartbeat.heartbeat_sequence if isinstance(heartbeat, RunnerHeartbeat) else None,
            expected,
            heartbeat.observed_completion_timestamp if isinstance(heartbeat, RunnerHeartbeat) else None,
            StoredRunnerOutcome(heartbeat.runner_outcome.value)
            if isinstance(heartbeat, RunnerHeartbeat) and isinstance(heartbeat.runner_outcome, RunnerOutcome)
            else StoredRunnerOutcome.NO_RESULT,
            LivenessState.FAILED_CLOSED, reason, evaluated, transition_id,
            current[3] if current else 0, True,
            SuspensionCause.INTERNAL_INVARIANT_FAILURE, advance_sequence=False,
        )

    def _persist(
        self, runner_id, heartbeat_sequence, expected, observed, runner_outcome,
        state, reason, evaluated, transition_id, misses, suspension_required,
        suspension_cause, *, advance_sequence=True,
    ) -> LivenessDecision:
        current = self._load_run(runner_id)
        sequence = (current[6] if current else 0) + 1
        latched = bool(suspension_required or (current and current[4]))
        if current and current[4]:
            cause = current[5]
            if (
                suspension_required
                and CAUSE_SEVERITY[suspension_cause] > CAUSE_SEVERITY[cause]
            ):
                cause = suspension_cause
        else:
            cause = suspension_cause
        if latched and cause is SuspensionCause.NONE:
            cause = SuspensionCause.INTERNAL_INVARIANT_FAILURE
        durable_expected = (
            expected if advance_sequence or current is None else current[8]
        )
        durable_evaluated = (
            evaluated if advance_sequence or current is None else current[9]
        )
        durable_heartbeat_sequence = (
            heartbeat_sequence if advance_sequence else current[2] if current else None
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._connection.execute("PRAGMA defer_foreign_keys=ON")
            self._connection.execute(
                "INSERT INTO heartbeat_records (transition_id, runner_id, "
                "heartbeat_sequence, expected_cycle_timestamp, "
                "observed_completion_timestamp, runner_outcome, liveness_state, "
                "reason_code, evaluation_time, transition_sequence) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_id, runner_id, heartbeat_sequence,
                    expected.isoformat(), observed.isoformat() if observed else None,
                    runner_outcome.value, state.value, reason.value,
                    evaluated.isoformat(), sequence,
                ),
            )
            self._connection.execute(
                "INSERT INTO liveness_runs (runner_id, state, reason, "
                "last_heartbeat_sequence, consecutive_missed_cycles, "
                "suspension_required, suspension_cause, transition_sequence, "
                "last_transition_id, last_expected_at, last_observed_at, "
                "last_evaluated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(runner_id) DO UPDATE SET state=excluded.state, "
                "reason=excluded.reason, last_heartbeat_sequence=CASE "
                "WHEN excluded.last_heartbeat_sequence IS NULL THEN liveness_runs.last_heartbeat_sequence "
                "WHEN liveness_runs.last_heartbeat_sequence IS NULL THEN excluded.last_heartbeat_sequence "
                "WHEN excluded.last_heartbeat_sequence > liveness_runs.last_heartbeat_sequence "
                "THEN excluded.last_heartbeat_sequence ELSE liveness_runs.last_heartbeat_sequence END, "
                "consecutive_missed_cycles=excluded.consecutive_missed_cycles, "
                "suspension_required=excluded.suspension_required, "
                "suspension_cause=excluded.suspension_cause, "
                "transition_sequence=excluded.transition_sequence, "
                "last_transition_id=excluded.last_transition_id, "
                "last_expected_at=excluded.last_expected_at, "
                "last_observed_at=excluded.last_observed_at, "
                "last_evaluated_at=excluded.last_evaluated_at",
                (
                    runner_id, state.value, reason.value,
                    heartbeat_sequence if advance_sequence else None, misses,
                    int(latched), cause.value, sequence, transition_id,
                    durable_expected.isoformat(), observed.isoformat() if observed else None,
                    durable_evaluated.isoformat(),
                ),
            )
            self._connection.execute(
                "INSERT INTO liveness_checkpoints (transition_id, runner_id, "
                "transition_sequence, last_heartbeat_sequence, "
                "consecutive_missed_cycles, suspension_required, "
                "suspension_cause, last_expected_at, last_evaluated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    transition_id, runner_id, sequence,
                    durable_heartbeat_sequence, misses, int(latched), cause.value,
                    durable_expected.isoformat(), durable_evaluated.isoformat(),
                ),
            )
            self._connection.execute("COMMIT")
        except Exception:
            if self._connection.in_transaction:
                try:
                    self._connection.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
            raise LivenessError("liveness persistence failed") from None
        record = self.load_record(transition_id)
        return self._decision_from_record(record)

    def _load_run(self, runner_id: str):
        row = self._connection.execute(
            "SELECT state, reason, last_heartbeat_sequence, "
            "consecutive_missed_cycles, suspension_required, suspension_cause, "
            "transition_sequence, last_transition_id, last_expected_at, "
            "last_evaluated_at FROM liveness_runs "
            "WHERE runner_id=?", (runner_id,),
        ).fetchone()
        if row is None:
            return None
        checkpoint = self._connection.execute(
            "SELECT transition_id, transition_sequence, last_heartbeat_sequence, "
            "consecutive_missed_cycles, suspension_required, suspension_cause, "
            "last_expected_at, last_evaluated_at FROM liveness_checkpoints "
            "WHERE runner_id=? ORDER BY transition_sequence DESC LIMIT 1",
            (runner_id,),
        ).fetchone()
        if checkpoint is None:
            raise LivenessError("persisted liveness checkpoint is missing")
        try:
            misses = _nonnegative(row[3], "missed cycle count")
            sequence = _positive(row[6], "transition sequence")
            transition_id = _identifier(
                row[7], "liveness-transition-", "transition identity"
            )
            last_heartbeat = (
                _positive(row[2], "heartbeat sequence") if row[2] is not None else None
            )
            state = LivenessState(row[0])
            reason = LivenessReason(row[1])
            suspended = row[4] in (1, True)
            cause = SuspensionCause(row[5])
            if suspended != (cause is not SuspensionCause.NONE):
                raise ValueError
            checkpoint_values = (
                _identifier(checkpoint[0], "liveness-transition-", "transition identity"),
                _positive(checkpoint[1], "transition sequence"),
                _positive(checkpoint[2], "heartbeat sequence") if checkpoint[2] is not None else None,
                _nonnegative(checkpoint[3], "missed cycle count"),
                checkpoint[4] in (1, True),
                SuspensionCause(checkpoint[5]),
                _parse(checkpoint[6], "last expected cycle timestamp"),
                _parse(checkpoint[7], "last evaluation time"),
            )
            summary_values = (
                transition_id, sequence, last_heartbeat, misses, suspended, cause,
                _parse(row[8], "last expected cycle timestamp"),
                _parse(row[9], "last evaluation time"),
            )
            if checkpoint_values != summary_values:
                raise ValueError
            return (
                state, reason, last_heartbeat, misses, suspended, cause,
                sequence, transition_id,
                _parse(row[8], "last expected cycle timestamp"),
                _parse(row[9], "last evaluation time"),
            )
        except (LivenessError, TypeError, ValueError):
            try:
                checkpoint_transition = _identifier(
                    checkpoint[0], "liveness-transition-", "transition identity"
                )
                checkpoint_sequence = _positive(
                    checkpoint[1], "transition sequence"
                )
                return (
                    LivenessState.FAILED_CLOSED,
                    LivenessReason.RECEIPT_INVALID,
                    _positive(checkpoint[2], "heartbeat sequence") if checkpoint[2] is not None else None,
                    _nonnegative(checkpoint[3], "missed cycle count"),
                    True,
                    SuspensionCause.INTERNAL_INVARIANT_FAILURE,
                    checkpoint_sequence,
                    checkpoint_transition,
                    _parse(checkpoint[6], "last expected cycle timestamp"),
                    _parse(checkpoint[7], "last evaluation time"),
                )
            except LivenessError:
                raise LivenessError("persisted liveness state is malformed") from None

    def _canonical_heartbeat(self, runner_id: str, sequence: int) -> LivenessRecord | None:
        row = self._connection.execute(
            "SELECT transition_id FROM heartbeat_records WHERE runner_id=? "
            "AND heartbeat_sequence=? ORDER BY transition_sequence LIMIT 1",
            (runner_id, sequence),
        ).fetchone()
        return self.load_record(row[0]) if row else None

    def load_record(self, transition_id: str) -> LivenessRecord | None:
        _identifier(transition_id, "liveness-transition-", "transition identity")
        row = self._connection.execute(
            "SELECT runner_id, heartbeat_sequence, expected_cycle_timestamp, "
            "observed_completion_timestamp, runner_outcome, liveness_state, "
            "reason_code, evaluation_time, transition_id, transition_sequence "
            "FROM heartbeat_records WHERE transition_id=?", (transition_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            return LivenessRecord(
                runner_id=_identifier(row[0], "runner-", "runner identity"),
                heartbeat_sequence=_positive(row[1], "heartbeat sequence") if row[1] is not None else None,
                expected_cycle_timestamp=_parse(row[2], "expected cycle timestamp"),
                observed_completion_timestamp=_parse(row[3], "observed completion timestamp") if row[3] else None,
                runner_outcome=StoredRunnerOutcome(row[4]),
                state=LivenessState(row[5]), reason=LivenessReason(row[6]),
                evaluation_time=_parse(row[7], "evaluation time"),
                transition_id=_identifier(row[8], "liveness-transition-", "transition identity"),
                transition_sequence=_positive(row[9], "transition sequence"),
            )
        except (TypeError, ValueError):
            raise LivenessError("persisted heartbeat record is malformed") from None

    def _decision_from_record(self, record: LivenessRecord) -> LivenessDecision:
        current = self._load_run(record.runner_id)
        if current is None:
            raise LivenessError("liveness run is missing")
        required = current[4]
        request = LivenessSuspensionRequest(
            runner_id=record.runner_id,
            request_suspension=required,
            cause=current[5] if required else SuspensionCause.NONE,
            reason=record.reason,
            transition_id=record.transition_id,
        )
        return LivenessDecision(record, current[3], request)


def _same_attempt(record, runner_id, heartbeat, expected, evaluated) -> bool:
    return bool(
        record.runner_id == runner_id
        and record.expected_cycle_timestamp == expected
        and record.evaluation_time == evaluated
        and (
            (heartbeat is None and record.heartbeat_sequence is None)
            or (
                isinstance(heartbeat, RunnerHeartbeat)
                and record.heartbeat_sequence == heartbeat.heartbeat_sequence
                and record.observed_completion_timestamp == heartbeat.observed_completion_timestamp
                and record.runner_outcome.value == heartbeat.runner_outcome.value
            )
        )
    )


def _same_heartbeat(record: LivenessRecord, heartbeat: RunnerHeartbeat) -> bool:
    return bool(
        record.runner_id == heartbeat.runner_id
        and record.heartbeat_sequence == heartbeat.heartbeat_sequence
        and record.expected_cycle_timestamp == heartbeat.expected_cycle_timestamp
        and record.observed_completion_timestamp == heartbeat.observed_completion_timestamp
        and record.runner_outcome.value == heartbeat.runner_outcome.value
    )


def _identifier(value, prefix, label):
    if not isinstance(value, str) or not value.startswith(prefix):
        raise LivenessError(f"{label} is not constrained")
    if value != value.strip() or len(value) > 96:
        raise LivenessError(f"{label} is malformed")
    if not value.replace("-", "").replace("_", "").isalnum():
        raise LivenessError(f"{label} has an unsupported character")
    return value


def _moment(value, label):
    if not isinstance(value, datetime) or value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise LivenessError(f"{label} is not timezone aware")
    return value


def _parse(value, label):
    if not isinstance(value, str) or not value:
        raise LivenessError(f"persisted {label} is missing")
    try:
        return _moment(datetime.fromisoformat(value), label)
    except ValueError:
        raise LivenessError(f"persisted {label} is malformed") from None


def _positive(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise LivenessError(f"{label} is invalid")
    return value


def _nonnegative(value, label):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise LivenessError(f"{label} is invalid")
    return value


__all__ = [
    "DurableRunnerLiveness", "FreshnessPolicy", "LivenessDecision",
    "LivenessError", "LivenessReason", "LivenessRecord", "LivenessState",
    "LivenessSuspensionRequest", "RunnerHeartbeat", "StoredRunnerOutcome",
]
