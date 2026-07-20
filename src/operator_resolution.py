from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

try:
    from .broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        condition_satisfied,
    )
    from .broker_saga_orchestrator import (
        OrchestrationCrashPoint,
    )
    from .broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from .trade_state import utc_now
except ImportError:
    from broker_action_saga import (
        BrokerActionSagaError,
        BrokerActionSagaStore,
        SagaActionRecord,
        SagaActionStatus,
        condition_satisfied,
    )
    from broker_saga_orchestrator import (
        OrchestrationCrashPoint,
    )
    from broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from trade_state import utc_now


class OperatorResolutionError(
    BrokerActionSagaError
):
    """Raised when operator resolution cannot proceed safely."""


class ResolutionDecision(str, Enum):
    PENDING_EVIDENCE = "pending_evidence"
    EFFECT_CONFIRMED = "effect_confirmed"
    NO_EFFECT_CONFIRMED = "no_effect_confirmed"
    REMAINS_AMBIGUOUS = "remains_ambiguous"
    TERMINAL_FAILURE = "terminal_failure"
    SUPERSEDED = "superseded"


OPEN_DECISIONS = {
    ResolutionDecision.PENDING_EVIDENCE,
    ResolutionDecision.REMAINS_AMBIGUOUS,
}

_PAYLOAD_CONSISTENCY_KEYS = (
    "symbol",
    "side",
    "type",
    "quantity",
    "stop_price",
    "limit_price",
    "trigger",
    "time_in_force",
)


@dataclass(frozen=True)
class OperatorResolutionRecord:
    resolution_id: str
    action_id: str
    trade_id: str
    strategy: str
    symbol: str
    original_failure_code: str | None
    original_failure_reason: str | None
    required_evidence_type: str
    account_fingerprint: str
    expected_payload_digest: str
    expected_ref_id: str
    expected_broker_order_id: str | None
    created_at: datetime
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    acknowledgment_note: str | None
    evidence_observation_id: str | None
    evidence_snapshot_digest: str | None
    evidence_fresh_at: datetime | None
    evidence_classification: str | None
    decision: ResolutionDecision
    resolved_at: datetime | None
    failure_code: str | None
    failure_reason: str | None
    version: int


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def classify_resolution_evidence(
    record: OperatorResolutionRecord,
    action: SagaActionRecord,
    *,
    snapshot: BrokerSnapshot,
    evaluated_at: datetime,
    observation: dict[str, Any],
    maximum_snapshot_age: timedelta = (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE
    ),
) -> tuple[str, str]:
    """
    Classifies operator-resolution evidence.

    Pure and deterministic. Returns a
    (classification, reason) pair where the
    classification is one of "effect_confirmed",
    "no_effect_confirmed", or "remains_ambiguous".
    Only fresh, complete, account-matched,
    payload-consistent, non-conflicting evidence can
    confirm anything.
    """
    validation = validate_snapshot(
        snapshot,
        evaluated_at=evaluated_at,
        maximum_age=maximum_snapshot_age,
    )
    if not validation.valid:
        return (
            "remains_ambiguous",
            validation.failure_reason
            or "The snapshot evidence is invalid.",
        )
    if (
        snapshot.metadata.account_fingerprint
        != record.account_fingerprint
    ):
        return (
            "remains_ambiguous",
            "The snapshot account fingerprint "
            "does not match the commissioned "
            "account.",
        )

    observed_symbol = str(
        observation.get("symbol", "")
    ).strip().upper()
    if (
        observed_symbol
        and observed_symbol != record.symbol
    ):
        return (
            "remains_ambiguous",
            "The evidence symbol does not match "
            "the trade.",
        )

    observed_ref = observation.get("ref_id")
    if (
        observed_ref is not None
        and str(observed_ref)
        != record.expected_ref_id
    ):
        return (
            "remains_ambiguous",
            "The evidence ref_id does not match "
            "the expected stable ref_id.",
        )

    observed_order_id = observation.get(
        "broker_order_id"
    )
    if (
        observed_order_id is not None
        and record.expected_broker_order_id
        and str(observed_order_id)
        != record.expected_broker_order_id
    ):
        return (
            "remains_ambiguous",
            "The evidence broker order ID does "
            "not match the expected order.",
        )

    if observation.get(
        "conflicting_active_orders"
    ):
        return (
            "remains_ambiguous",
            "Conflicting active orders make the "
            "evidence internally inconsistent.",
        )

    payload = json.loads(action.payload_json)
    expected_subset = {
        key: payload[key]
        for key in _PAYLOAD_CONSISTENCY_KEYS
        if key in payload and key in observation
    }
    if expected_subset and not (
        condition_satisfied(
            expected_subset, observation
        )
    ):
        return (
            "remains_ambiguous",
            "The evidence does not match the "
            "persisted canonical payload.",
        )

    effect_present = observation.get(
        "effect_present"
    )
    if effect_present is True:
        return (
            "effect_confirmed",
            "Fresh evidence proves the "
            "broker-side effect occurred.",
        )
    if effect_present is False:
        return (
            "no_effect_confirmed",
            "Fresh complete evidence proves no "
            "broker-side effect occurred.",
        )
    return (
        "remains_ambiguous",
        "The evidence does not establish whether "
        "a broker-side effect occurred.",
    )


class OperatorResolutionStore:
    """
    Durable, evidence-based resolution workflow for
    OPERATOR_REQUIRED saga actions.

    Operator acknowledgment is recorded separately
    from broker proof and can never change a saga
    outcome, clear a halt, or permit a retry. Only
    fresh machine-readable evidence can resolve a
    condition, through the saga store's own
    evidence-validated transition.
    """

    def __init__(
        self,
        saga_store: BrokerActionSagaStore,
        *,
        crash_injector=None,
    ):
        if not isinstance(
            saga_store, BrokerActionSagaStore
        ):
            raise OperatorResolutionError(
                "A BrokerActionSagaStore is "
                "required."
            )
        self.saga = saga_store
        self.db_path = saga_store.db_path
        self.crash_injector = crash_injector
        self._initialize()

    def _crash(self, point) -> None:
        if self.crash_injector is not None:
            self.crash_injector.check(point)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute(
            "PRAGMA foreign_keys = ON"
        )
        connection.execute(
            "PRAGMA journal_mode = WAL"
        )
        connection.execute(
            "PRAGMA busy_timeout = 5000"
        )
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS
                    operator_resolutions (
                        resolution_id TEXT
                            PRIMARY KEY,
                        action_id TEXT NOT NULL,
                        trade_id TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        original_failure_code TEXT,
                        original_failure_reason
                            TEXT,
                        required_evidence_type
                            TEXT NOT NULL,
                        account_fingerprint
                            TEXT NOT NULL,
                        expected_payload_digest
                            TEXT NOT NULL,
                        expected_ref_id
                            TEXT NOT NULL,
                        expected_broker_order_id
                            TEXT,
                        created_at TEXT NOT NULL,
                        acknowledged_at TEXT,
                        acknowledged_by TEXT,
                        acknowledgment_note TEXT,
                        evidence_observation_id
                            TEXT,
                        evidence_snapshot_digest
                            TEXT,
                        evidence_fresh_at TEXT,
                        evidence_classification
                            TEXT,
                        decision TEXT NOT NULL,
                        resolved_at TEXT,
                        failure_code TEXT,
                        failure_reason TEXT,
                        version INTEGER NOT NULL
                            DEFAULT 1,
                        FOREIGN KEY(action_id)
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            ),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(
                                trade_id
                            )
                    );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_one_open_resolution_per_action
                ON operator_resolutions(action_id)
                WHERE decision IN (
                    'pending_evidence',
                    'remains_ambiguous'
                );

                CREATE TABLE IF NOT EXISTS
                    operator_resolution_events (
                        event_id INTEGER
                            PRIMARY KEY
                            AUTOINCREMENT,
                        resolution_id TEXT
                            NOT NULL,
                        event_type TEXT NOT NULL,
                        old_decision TEXT,
                        new_decision TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(resolution_id)
                            REFERENCES
                            operator_resolutions(
                                resolution_id
                            )
                    );

                CREATE TRIGGER IF NOT EXISTS
                    prevent_operator_resolution_event_update
                BEFORE UPDATE
                ON operator_resolution_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'operator_resolution_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_operator_resolution_event_delete
                BEFORE DELETE
                ON operator_resolution_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'operator_resolution_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_operator_resolution_delete
                BEFORE DELETE
                ON operator_resolutions
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'operator_resolutions are append-only'
                    );
                END;
                """
            )

    @staticmethod
    def _row_to_resolution(
        row: sqlite3.Row,
    ) -> OperatorResolutionRecord:
        return OperatorResolutionRecord(
            resolution_id=row["resolution_id"],
            action_id=row["action_id"],
            trade_id=row["trade_id"],
            strategy=row["strategy"],
            symbol=row["symbol"],
            original_failure_code=(
                row["original_failure_code"]
            ),
            original_failure_reason=(
                row["original_failure_reason"]
            ),
            required_evidence_type=(
                row["required_evidence_type"]
            ),
            account_fingerprint=(
                row["account_fingerprint"]
            ),
            expected_payload_digest=(
                row["expected_payload_digest"]
            ),
            expected_ref_id=(
                row["expected_ref_id"]
            ),
            expected_broker_order_id=(
                row["expected_broker_order_id"]
            ),
            created_at=_parse_datetime(
                row["created_at"]
            ),
            acknowledged_at=_parse_datetime(
                row["acknowledged_at"]
            ),
            acknowledged_by=(
                row["acknowledged_by"]
            ),
            acknowledgment_note=(
                row["acknowledgment_note"]
            ),
            evidence_observation_id=(
                row["evidence_observation_id"]
            ),
            evidence_snapshot_digest=(
                row["evidence_snapshot_digest"]
            ),
            evidence_fresh_at=_parse_datetime(
                row["evidence_fresh_at"]
            ),
            evidence_classification=(
                row["evidence_classification"]
            ),
            decision=ResolutionDecision(
                row["decision"]
            ),
            resolved_at=_parse_datetime(
                row["resolved_at"]
            ),
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
            version=int(row["version"]),
        )

    def _load_resolution_row(
        self,
        connection: sqlite3.Connection,
        resolution_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM operator_resolutions
            WHERE resolution_id = ?
            """,
            (resolution_id,),
        ).fetchone()
        if row is None:
            raise OperatorResolutionError(
                f"Operator resolution not found: "
                f"{resolution_id}"
            )
        return row

    def _insert_resolution_event(
        self,
        connection: sqlite3.Connection,
        *,
        resolution_id: str,
        event_type: str,
        old_decision: ResolutionDecision | None,
        new_decision: ResolutionDecision | None,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO
                operator_resolution_events (
                    resolution_id, event_type,
                    old_decision, new_decision,
                    occurred_at, details_json
                )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                resolution_id,
                event_type,
                (
                    old_decision.value
                    if old_decision
                    else None
                ),
                (
                    new_decision.value
                    if new_decision
                    else None
                ),
                occurred_at.isoformat(),
                json.dumps(
                    details or {},
                    sort_keys=True,
                    default=str,
                ),
            ),
        )

    def _update_resolution(
        self,
        connection: sqlite3.Connection,
        *,
        current: OperatorResolutionRecord,
        new_decision: ResolutionDecision,
        event_type: str,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
        set_clauses: dict[str, Any] | None = None,
    ) -> None:
        assignments = {
            "decision": new_decision.value,
        }
        assignments.update(set_clauses or {})
        set_sql = ", ".join(
            f"{column} = ?"
            for column in assignments
        )
        parameters = list(assignments.values())
        parameters.extend(
            [
                current.resolution_id,
                current.version,
            ]
        )
        cursor = connection.execute(
            f"""
            UPDATE operator_resolutions
            SET {set_sql}, version = version + 1
            WHERE resolution_id = ?
              AND version = ?
            """,
            parameters,
        )
        if cursor.rowcount != 1:
            raise OperatorResolutionError(
                "Operator resolution changed "
                "concurrently."
            )
        self._insert_resolution_event(
            connection,
            resolution_id=current.resolution_id,
            event_type=event_type,
            old_decision=current.decision,
            new_decision=new_decision,
            occurred_at=occurred_at,
            details=details,
        )

    def get_resolution(
        self,
        resolution_id: str,
    ) -> OperatorResolutionRecord:
        with self._connect() as connection:
            row = self._load_resolution_row(
                connection, resolution_id
            )
        return self._row_to_resolution(row)

    def list_resolutions(
        self,
        action_id: str,
    ) -> list[OperatorResolutionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM operator_resolutions
                WHERE action_id = ?
                ORDER BY created_at,
                         resolution_id
                """,
                (action_id,),
            ).fetchall()
        return [
            self._row_to_resolution(row)
            for row in rows
        ]

    def count_resolution_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM operator_resolution_events
                """
            ).fetchone()
        return int(row["count_value"])

    def count_resolutions(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM operator_resolutions
                """
            ).fetchone()
        return int(row["count_value"])

    def open_resolution(
        self,
        action_id: str,
        *,
        required_evidence_type: str,
        occurred_at: datetime | None = None,
    ) -> tuple[OperatorResolutionRecord, bool]:
        timestamp = occurred_at or utc_now()
        required_evidence_type = (
            required_evidence_type.strip()
        )
        if not required_evidence_type:
            raise OperatorResolutionError(
                "A required evidence type is "
                "needed to open a resolution."
            )

        action = self.saga.get_action(action_id)
        if (
            action.status
            != SagaActionStatus.OPERATOR_REQUIRED
        ):
            raise OperatorResolutionError(
                "Operator resolutions can only be "
                "opened for OPERATOR_REQUIRED "
                "saga actions."
            )
        trade = (
            self.saga.trade_store.get_trade(
                action.trade_id
            )
        )

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT *
                FROM operator_resolutions
                WHERE action_id = ?
                  AND decision IN (
                    'pending_evidence',
                    'remains_ambiguous'
                  )
                """,
                (action_id,),
            ).fetchone()
            if existing is not None:
                return (
                    self._row_to_resolution(
                        existing
                    ),
                    False,
                )

            resolution_id = str(uuid.uuid4())
            connection.execute(
                """
                INSERT INTO operator_resolutions (
                    resolution_id, action_id,
                    trade_id, strategy, symbol,
                    original_failure_code,
                    original_failure_reason,
                    required_evidence_type,
                    account_fingerprint,
                    expected_payload_digest,
                    expected_ref_id,
                    expected_broker_order_id,
                    created_at,
                    acknowledged_at,
                    acknowledged_by,
                    acknowledgment_note,
                    evidence_observation_id,
                    evidence_snapshot_digest,
                    evidence_fresh_at,
                    evidence_classification,
                    decision, resolved_at,
                    failure_code, failure_reason,
                    version
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, NULL, NULL, NULL,
                    NULL, NULL, NULL, NULL, ?,
                    NULL, NULL, NULL, 1
                )
                """,
                (
                    resolution_id,
                    action.action_id,
                    action.trade_id,
                    action.strategy,
                    trade.symbol,
                    action.failure_code,
                    action.failure_reason,
                    required_evidence_type,
                    action.account_fingerprint,
                    action.payload_digest,
                    action.ref_id,
                    (
                        action
                        .resulting_broker_order_id
                        or action
                        .source_broker_order_id
                    ),
                    timestamp.isoformat(),
                    ResolutionDecision
                    .PENDING_EVIDENCE.value,
                ),
            )
            self._insert_resolution_event(
                connection,
                resolution_id=resolution_id,
                event_type="resolution_opened",
                old_decision=None,
                new_decision=(
                    ResolutionDecision
                    .PENDING_EVIDENCE
                ),
                occurred_at=timestamp,
                details={
                    "action_id": action.action_id,
                    "required_evidence_type": (
                        required_evidence_type
                    ),
                },
            )

        return (
            self.get_resolution(resolution_id),
            True,
        )

    def acknowledge(
        self,
        resolution_id: str,
        *,
        operator_label: str,
        note: str,
        occurred_at: datetime | None = None,
    ) -> OperatorResolutionRecord:
        """
        Records that a human reviewed the
        situation. Never changes saga status,
        never clears a halt, never permits a
        retry, and never proves a broker outcome.
        """
        timestamp = occurred_at or utc_now()
        operator_label = operator_label.strip()
        if not operator_label:
            raise OperatorResolutionError(
                "A non-sensitive operator label "
                "is required."
            )

        with self._connect() as connection:
            current = self._row_to_resolution(
                self._load_resolution_row(
                    connection, resolution_id
                )
            )
            if (
                current.decision
                not in OPEN_DECISIONS
            ):
                raise OperatorResolutionError(
                    "Only an open resolution can "
                    "be acknowledged."
                )
            self._update_resolution(
                connection,
                current=current,
                new_decision=current.decision,
                event_type=(
                    "resolution_acknowledged"
                ),
                occurred_at=timestamp,
                details={
                    "acknowledged_by": (
                        operator_label
                    ),
                    "note": note,
                },
                set_clauses={
                    "acknowledged_at": (
                        timestamp.isoformat()
                    ),
                    "acknowledged_by": (
                        operator_label
                    ),
                    "acknowledgment_note": note,
                },
            )
            self._crash(
                OrchestrationCrashPoint
                .DURING_ACK_PERSISTENCE
            )

        self.saga.acknowledge_operator(
            current.action_id,
            note=(
                f"[{operator_label}] {note}"
            ),
            occurred_at=timestamp,
        )
        return self.get_resolution(resolution_id)

    def submit_evidence(
        self,
        resolution_id: str,
        *,
        strategy: str,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        observation: dict[str, Any],
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> OperatorResolutionRecord:
        timestamp = occurred_at or evaluated_at
        if not isinstance(observation, dict):
            raise OperatorResolutionError(
                "Resolution evidence must be a "
                "machine-readable observation."
            )

        record = self.get_resolution(
            resolution_id
        )
        if strategy.strip() != record.strategy:
            raise OperatorResolutionError(
                "Another strategy cannot resolve "
                "this operator condition."
            )
        if record.decision not in OPEN_DECISIONS:
            raise OperatorResolutionError(
                "Only an open resolution can "
                "receive evidence."
            )
        action = self.saga.get_action(
            record.action_id
        )

        classification, reason = (
            classify_resolution_evidence(
                record,
                action,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                observation=observation,
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
            )
        )
        decision = ResolutionDecision(
            classification
            if classification
            != "remains_ambiguous"
            else "remains_ambiguous"
        )

        with self._connect() as connection:
            current = self._row_to_resolution(
                self._load_resolution_row(
                    connection, resolution_id
                )
            )
            set_clauses: dict[str, Any] = {
                "evidence_observation_id": (
                    str(
                        observation.get(
                            "observation_id", ""
                        )
                    )
                    or None
                ),
                "evidence_snapshot_digest": (
                    validate_snapshot(
                        snapshot,
                        evaluated_at=evaluated_at,
                        maximum_age=(
                            maximum_snapshot_age
                        ),
                    ).snapshot_digest
                ),
                "evidence_fresh_at": (
                    snapshot.metadata
                    .completed_at.isoformat()
                ),
                "evidence_classification": (
                    classification
                ),
            }
            if decision not in OPEN_DECISIONS:
                set_clauses["resolved_at"] = (
                    timestamp.isoformat()
                )
            self._update_resolution(
                connection,
                current=current,
                new_decision=decision,
                event_type=(
                    "resolution_evidence_recorded"
                ),
                occurred_at=timestamp,
                details={
                    "classification": (
                        classification
                    ),
                    "reason": reason,
                    "effect_present": (
                        observation.get(
                            "effect_present"
                        )
                    ),
                },
                set_clauses=set_clauses,
            )

        self._crash(
            OrchestrationCrashPoint
            .AFTER_EVIDENCE_BEFORE_SAGA_TRANSITION
        )

        if decision == (
            ResolutionDecision.EFFECT_CONFIRMED
        ):
            saga_observation: dict[str, Any] = {
                "effect_present": True,
            }
            observed_order_id = observation.get(
                "broker_order_id"
            )
            if observed_order_id:
                saga_observation[
                    "resulting_broker_order_id"
                ] = str(observed_order_id)
            self.saga.resolve_operator_required(
                record.action_id,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                observation=saga_observation,
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
                occurred_at=timestamp,
            )
        elif decision == (
            ResolutionDecision.NO_EFFECT_CONFIRMED
        ):
            self.saga.resolve_operator_required(
                record.action_id,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                observation={
                    "effect_present": False,
                },
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
                occurred_at=timestamp,
            )

        self._crash(
            OrchestrationCrashPoint
            .AFTER_SAGA_TRANSITION_BEFORE_ROUTING
        )
        return self.get_resolution(resolution_id)

    def apply_decided_resolution(
        self,
        resolution_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> OperatorResolutionRecord:
        """
        Restart recovery: re-applies a decided
        resolution whose saga transition was lost
        to a crash. Requires fresh evidence again;
        if the action already transitioned, this is
        a no-op.
        """
        timestamp = occurred_at or evaluated_at
        record = self.get_resolution(
            resolution_id
        )
        if record.decision not in {
            ResolutionDecision.EFFECT_CONFIRMED,
            ResolutionDecision
            .NO_EFFECT_CONFIRMED,
        }:
            raise OperatorResolutionError(
                "Only a decided evidence-based "
                "resolution can be re-applied."
            )
        action = self.saga.get_action(
            record.action_id
        )
        if (
            action.status
            != SagaActionStatus.OPERATOR_REQUIRED
        ):
            return record

        effect = (
            record.decision
            == ResolutionDecision.EFFECT_CONFIRMED
        )
        self.saga.resolve_operator_required(
            record.action_id,
            snapshot=snapshot,
            evaluated_at=evaluated_at,
            observation={
                "effect_present": effect,
            },
            maximum_snapshot_age=(
                maximum_snapshot_age
            ),
            occurred_at=timestamp,
        )
        return self.get_resolution(resolution_id)

    def mark_terminal(
        self,
        resolution_id: str,
        *,
        failure_code: str,
        failure_reason: str,
        occurred_at: datetime | None = None,
    ) -> OperatorResolutionRecord:
        timestamp = occurred_at or utc_now()
        with self._connect() as connection:
            current = self._row_to_resolution(
                self._load_resolution_row(
                    connection, resolution_id
                )
            )
            if (
                current.decision
                not in OPEN_DECISIONS
            ):
                raise OperatorResolutionError(
                    "Only an open resolution can "
                    "become terminal."
                )
            self._update_resolution(
                connection,
                current=current,
                new_decision=(
                    ResolutionDecision
                    .TERMINAL_FAILURE
                ),
                event_type=(
                    "resolution_marked_terminal"
                ),
                occurred_at=timestamp,
                details={
                    "failure_code": failure_code,
                    "failure_reason": (
                        failure_reason
                    ),
                },
                set_clauses={
                    "failure_code": failure_code,
                    "failure_reason": (
                        failure_reason
                    ),
                    "resolved_at": (
                        timestamp.isoformat()
                    ),
                },
            )
        return self.get_resolution(resolution_id)

    def supersede(
        self,
        resolution_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> OperatorResolutionRecord:
        timestamp = occurred_at or utc_now()
        reason = reason.strip()
        if not reason:
            raise OperatorResolutionError(
                "A durable supersession reason is "
                "required."
            )
        with self._connect() as connection:
            current = self._row_to_resolution(
                self._load_resolution_row(
                    connection, resolution_id
                )
            )
            if (
                current.decision
                not in OPEN_DECISIONS
            ):
                raise OperatorResolutionError(
                    "Only an open resolution can "
                    "be superseded."
                )
            self._update_resolution(
                connection,
                current=current,
                new_decision=(
                    ResolutionDecision.SUPERSEDED
                ),
                event_type=(
                    "resolution_superseded"
                ),
                occurred_at=timestamp,
                details={"reason": reason},
                set_clauses={
                    "failure_reason": reason,
                    "resolved_at": (
                        timestamp.isoformat()
                    ),
                },
            )
        return self.get_resolution(resolution_id)


def main() -> None:
    print("\nOPERATOR RESOLUTION\n")
    print("SIMULATION ONLY")
    print(
        "Durable, evidence-based resolution of "
        "OPERATOR_REQUIRED saga actions. "
        "Acknowledgment is audit-only; only fresh "
        "machine-readable broker evidence can "
        "resolve a condition."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
