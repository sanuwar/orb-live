from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Iterable

try:
    from .broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from .trade_state import (
        TradeStateError,
        TradeStateStore,
        utc_now,
    )
except ImportError:
    from broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        validate_snapshot,
    )
    from trade_state import (
        TradeStateError,
        TradeStateStore,
        utc_now,
    )


DEFAULT_CLAIM_TTL = timedelta(seconds=30)

REF_ID_PREFIX = "orb-saga"


class BrokerActionSagaError(TradeStateError):
    """Raised when a durable broker saga operation cannot proceed safely."""


class SagaActionNotFoundError(BrokerActionSagaError):
    pass


class SagaHaltedError(BrokerActionSagaError):
    pass


class SagaActionType(str, Enum):
    PLACE_ENTRY = "place_entry"
    CANCEL_ENTRY_REMAINDER = "cancel_entry_remainder"
    PLACE_INITIAL_PROTECTIVE_STOP = (
        "place_initial_protective_stop"
    )
    CANCEL_PROTECTIVE_STOP = "cancel_protective_stop"
    PLACE_REPLACEMENT_PROTECTIVE_STOP = (
        "place_replacement_protective_stop"
    )
    PLACE_EXIT = "place_exit"
    CANCEL_EXIT = "cancel_exit"
    FLATTEN_POSITION = "flatten_position"
    RECONCILE_ONLY = "reconcile_only"
    OPERATOR_INTERVENTION = "operator_intervention"


BROKER_WRITE_ACTION_TYPES = {
    SagaActionType.PLACE_ENTRY,
    SagaActionType.CANCEL_ENTRY_REMAINDER,
    SagaActionType.PLACE_INITIAL_PROTECTIVE_STOP,
    SagaActionType.CANCEL_PROTECTIVE_STOP,
    SagaActionType.PLACE_REPLACEMENT_PROTECTIVE_STOP,
    SagaActionType.PLACE_EXIT,
    SagaActionType.CANCEL_EXIT,
    SagaActionType.FLATTEN_POSITION,
}


class SagaActionStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    BLOCKED = "blocked"
    CLAIMED = "claimed"
    SUBMISSION_INTENT_RECORDED = (
        "submission_intent_recorded"
    )
    OUTCOME_UNKNOWN = "outcome_unknown"
    AWAITING_BROKER_CONFIRMATION = (
        "awaiting_broker_confirmation"
    )
    CONFIRMED_SUCCEEDED = "confirmed_succeeded"
    CONFIRMED_NOT_PERFORMED = (
        "confirmed_not_performed"
    )
    RETRYABLE_FAILURE = "retryable_failure"
    TERMINAL_FAILURE = "terminal_failure"
    CANCELLED = "cancelled"
    OPERATOR_REQUIRED = "operator_required"


ACTIVE_SAGA_STATUSES = {
    SagaActionStatus.PLANNED,
    SagaActionStatus.READY,
    SagaActionStatus.BLOCKED,
    SagaActionStatus.CLAIMED,
    SagaActionStatus.SUBMISSION_INTENT_RECORDED,
    SagaActionStatus.OUTCOME_UNKNOWN,
    SagaActionStatus.AWAITING_BROKER_CONFIRMATION,
    SagaActionStatus.CONFIRMED_NOT_PERFORMED,
    SagaActionStatus.RETRYABLE_FAILURE,
    SagaActionStatus.OPERATOR_REQUIRED,
}

TERMINAL_SAGA_STATUSES = {
    SagaActionStatus.CONFIRMED_SUCCEEDED,
    SagaActionStatus.TERMINAL_FAILURE,
    SagaActionStatus.CANCELLED,
}

FAILED_DEPENDENCY_STATUSES = {
    SagaActionStatus.TERMINAL_FAILURE,
    SagaActionStatus.CANCELLED,
    SagaActionStatus.OPERATOR_REQUIRED,
}

SAGA_TRANSITIONS = {
    SagaActionStatus.PLANNED: {
        SagaActionStatus.READY,
        SagaActionStatus.BLOCKED,
        SagaActionStatus.CANCELLED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.READY: {
        SagaActionStatus.READY,
        SagaActionStatus.BLOCKED,
        SagaActionStatus.CLAIMED,
        SagaActionStatus.CANCELLED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.BLOCKED: {
        SagaActionStatus.READY,
        SagaActionStatus.BLOCKED,
        SagaActionStatus.CANCELLED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.CLAIMED: {
        SagaActionStatus.SUBMISSION_INTENT_RECORDED,
        SagaActionStatus.READY,
        SagaActionStatus.OUTCOME_UNKNOWN,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.SUBMISSION_INTENT_RECORDED: {
        SagaActionStatus.OUTCOME_UNKNOWN,
        SagaActionStatus.AWAITING_BROKER_CONFIRMATION,
        SagaActionStatus.RETRYABLE_FAILURE,
        SagaActionStatus.TERMINAL_FAILURE,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.OUTCOME_UNKNOWN: {
        SagaActionStatus.AWAITING_BROKER_CONFIRMATION,
        SagaActionStatus.CONFIRMED_NOT_PERFORMED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.AWAITING_BROKER_CONFIRMATION: {
        SagaActionStatus.CONFIRMED_SUCCEEDED,
        SagaActionStatus.OUTCOME_UNKNOWN,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.CONFIRMED_NOT_PERFORMED: {
        SagaActionStatus.READY,
        SagaActionStatus.CANCELLED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.RETRYABLE_FAILURE: {
        SagaActionStatus.READY,
        SagaActionStatus.TERMINAL_FAILURE,
        SagaActionStatus.CANCELLED,
        SagaActionStatus.OPERATOR_REQUIRED,
    },
    SagaActionStatus.CONFIRMED_SUCCEEDED: set(),
    SagaActionStatus.TERMINAL_FAILURE: set(),
    SagaActionStatus.CANCELLED: set(),
    SagaActionStatus.OPERATOR_REQUIRED: {
        SagaActionStatus
        .AWAITING_BROKER_CONFIRMATION,
        SagaActionStatus.CONFIRMED_NOT_PERFORMED,
    },
}

RETRYABLE_STATUSES = {
    SagaActionStatus.CONFIRMED_NOT_PERFORMED,
    SagaActionStatus.RETRYABLE_FAILURE,
}

ATTEMPT_CLASSIFICATIONS = {
    "acknowledged": (
        SagaActionStatus.AWAITING_BROKER_CONFIRMATION
    ),
    "timeout": SagaActionStatus.OUTCOME_UNKNOWN,
    "rejected_retryable": (
        SagaActionStatus.RETRYABLE_FAILURE
    ),
    "rejected_terminal": (
        SagaActionStatus.TERMINAL_FAILURE
    ),
}

_FORBIDDEN_PAYLOAD_KEYS = {
    "account_number",
    "account",
    "credentials",
    "token",
    "ref_id",
}

def _is_numeric_payload_field(key: str) -> bool:
    normalized = key.strip().lower()
    return normalized.endswith(
        "quantity"
    ) or normalized.endswith("price")


def normalized_decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"

    return format(value.normalize(), "f")


def _canonical_value(key: str, value: Any) -> Any:
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, Enum):
        value = value.value
    if isinstance(value, Decimal):
        return normalized_decimal_text(value)
    if isinstance(value, float):
        return normalized_decimal_text(
            Decimal(str(value))
        )
    if isinstance(value, int):
        if _is_numeric_payload_field(key):
            return normalized_decimal_text(
                Decimal(value)
            )
        return value
    if isinstance(value, str):
        if _is_numeric_payload_field(key):
            try:
                return normalized_decimal_text(
                    Decimal(value.strip())
                )
            except InvalidOperation:
                raise BrokerActionSagaError(
                    "Broker saga payload contains an "
                    f"invalid numeric value for {key}."
                )
        return value
    if isinstance(value, dict):
        return {
            str(item_key): _canonical_value(
                str(item_key), item_value
            )
            for item_key, item_value in sorted(
                value.items(),
                key=lambda pair: str(pair[0]),
            )
        }
    if isinstance(value, (list, tuple)):
        return [
            _canonical_value(key, item)
            for item in value
        ]
    raise BrokerActionSagaError(
        "Broker saga payload contains a "
        "non-canonical value."
    )


def canonical_action_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, dict) or not payload:
        raise BrokerActionSagaError(
            "Broker saga payload must be a "
            "non-empty mapping."
        )

    for key in payload:
        normalized_key = str(key).strip().lower()
        if (
            normalized_key in _FORBIDDEN_PAYLOAD_KEYS
            or normalized_key.endswith("_at")
            or normalized_key.endswith("timestamp")
        ):
            raise BrokerActionSagaError(
                "Broker saga payloads must not "
                "contain account numbers, "
                "credentials, ref_ids, or local "
                "timestamps."
            )

    return {
        str(key): _canonical_value(str(key), value)
        for key, value in sorted(
            payload.items(),
            key=lambda pair: str(pair[0]),
        )
    }


def canonical_payload_json(
    payload: dict[str, Any],
) -> str:
    return json.dumps(
        canonical_action_payload(payload),
        sort_keys=True,
        separators=(",", ":"),
    )


def action_payload_digest(
    payload: dict[str, Any],
) -> str:
    return hashlib.sha256(
        canonical_payload_json(payload).encode(
            "utf-8"
        )
    ).hexdigest()


def generate_ref_id() -> str:
    return f"{REF_ID_PREFIX}-{uuid.uuid4()}"


def condition_satisfied(
    expected: dict[str, Any],
    observation: dict[str, Any],
) -> bool:
    """
    Every machine-readable expected fact must be
    present and exactly matched by the observation.
    """
    if not isinstance(expected, dict):
        return False
    if not isinstance(observation, dict):
        return False

    for key, value in expected.items():
        if key not in observation:
            return False
        observed = observation[key]
        if isinstance(value, dict):
            if not condition_satisfied(
                value, observed
            ):
                return False
            continue
        if _canonical_value(
            str(key), value
        ) != _canonical_value(str(key), observed):
            return False

    return True


def _require_hex_digest(
    value: str,
    label: str,
) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(
        character not in "0123456789abcdef"
        for character in normalized
    ):
        raise BrokerActionSagaError(
            f"Broker saga {label} digest is invalid."
        )
    return normalized


def _require_aware(
    value: datetime,
    label: str,
) -> datetime:
    if value.tzinfo is None:
        raise BrokerActionSagaError(
            f"Broker saga {label} timestamp must be "
            "timezone-aware."
        )
    return value


@dataclass(frozen=True)
class SagaActionRecord:
    action_id: str
    strategy: str
    trade_id: str
    authorization_id: str | None
    action_type: SagaActionType
    status: SagaActionStatus
    side: str | None
    order_type: str | None
    trigger_type: str | None
    requested_quantity: Decimal | None
    limit_price: Decimal | None
    stop_price: Decimal | None
    time_in_force: str | None
    market_hours: str | None
    close_reason: str | None
    source_broker_order_id: str | None
    resulting_broker_order_id: str | None
    ref_id: str
    payload_json: str
    payload_digest: str
    logical_action_key: str
    recovery_reference: str | None
    prerequisite_reconciliation_id: str
    prerequisite_snapshot_digest: str
    account_fingerprint: str
    expected_precondition: dict[str, Any]
    expected_postcondition: dict[str, Any]
    blocked_reason: str | None
    confirmation_reconciliation_id: str | None
    confirmation_snapshot_digest: str | None
    created_at: datetime
    ready_at: datetime | None
    claim_owner: str | None
    claimed_at: datetime | None
    claim_expires_at: datetime | None
    submission_intent_at: datetime | None
    last_attempt_at: datetime | None
    attempt_count: int
    last_observation_at: datetime | None
    confirmed_at: datetime | None
    failure_code: str | None
    failure_reason: str | None
    operator_ack_required: bool
    version: int


@dataclass(frozen=True)
class SagaActionAttemptRecord:
    attempt_id: str
    action_id: str
    attempt_number: int
    claim_owner: str
    started_at: datetime
    submission_intent_at: datetime | None
    ref_id: str
    payload_digest: str
    response_classification: str | None
    resulting_broker_order_id: str | None
    completed_at: datetime | None
    result: str | None
    failure_code: str | None
    failure_reason: str | None
    proof_reference: str | None
    finalized: bool


@dataclass(frozen=True)
class BrokerActionHaltState:
    strategy: str
    active: bool
    reasons: tuple[str, ...]
    operator_ack_required: bool


def _parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _parse_optional_decimal(
    value: Any,
) -> Decimal | None:
    if value in {None, ""}:
        return None
    return Decimal(str(value))


class BrokerActionSagaStore:
    """
    Durable, restart-safe broker-action saga ledger.

    This module persists intended broker actions only.
    It contains no broker client, no transport, no
    network access, and cannot review, place, replace,
    cancel, or submit an order.
    """

    def __init__(self, trade_store: TradeStateStore):
        if not isinstance(
            trade_store, TradeStateStore
        ):
            raise BrokerActionSagaError(
                "A TradeStateStore is required to "
                "host the broker action saga."
            )
        self.trade_store = trade_store
        self.db_path = trade_store.db_path
        self._initialize()

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
                    broker_saga_actions (
                        action_id TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        trade_id TEXT NOT NULL,
                        authorization_id TEXT,
                        action_type TEXT NOT NULL,
                        status TEXT NOT NULL,
                        side TEXT,
                        order_type TEXT,
                        trigger_type TEXT,
                        requested_quantity TEXT,
                        limit_price TEXT,
                        stop_price TEXT,
                        time_in_force TEXT,
                        market_hours TEXT,
                        close_reason TEXT,
                        source_broker_order_id TEXT,
                        resulting_broker_order_id TEXT,
                        ref_id TEXT NOT NULL UNIQUE,
                        payload_json TEXT NOT NULL,
                        payload_digest TEXT NOT NULL,
                        logical_action_key TEXT NOT NULL,
                        recovery_reference TEXT,
                        prerequisite_reconciliation_id
                            TEXT NOT NULL,
                        prerequisite_snapshot_digest
                            TEXT NOT NULL,
                        account_fingerprint TEXT NOT NULL,
                        expected_precondition_json
                            TEXT NOT NULL,
                        expected_postcondition_json
                            TEXT NOT NULL,
                        blocked_reason TEXT,
                        confirmation_reconciliation_id TEXT,
                        confirmation_snapshot_digest TEXT,
                        created_at TEXT NOT NULL,
                        ready_at TEXT,
                        claim_owner TEXT,
                        claimed_at TEXT,
                        claim_expires_at TEXT,
                        submission_intent_at TEXT,
                        last_attempt_at TEXT,
                        attempt_count INTEGER NOT NULL
                            DEFAULT 0,
                        last_observation_at TEXT,
                        confirmed_at TEXT,
                        failure_code TEXT,
                        failure_reason TEXT,
                        operator_ack_required INTEGER
                            NOT NULL DEFAULT 0,
                        version INTEGER NOT NULL
                            DEFAULT 1,
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id),
                        FOREIGN KEY(authorization_id)
                            REFERENCES
                            broker_action_authorizations(
                                authorization_id
                            )
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_broker_saga_actions_trade_status
                ON broker_saga_actions(
                    trade_id, status
                );

                CREATE INDEX IF NOT EXISTS
                    idx_broker_saga_actions_strategy_status
                ON broker_saga_actions(
                    strategy, status
                );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_one_active_saga_action_per_key
                ON broker_saga_actions(
                    trade_id, logical_action_key
                )
                WHERE status IN (
                    'planned',
                    'ready',
                    'blocked',
                    'claimed',
                    'submission_intent_recorded',
                    'outcome_unknown',
                    'awaiting_broker_confirmation',
                    'confirmed_not_performed',
                    'retryable_failure',
                    'operator_required'
                );

                CREATE TABLE IF NOT EXISTS
                    broker_saga_action_attempts (
                        attempt_id TEXT PRIMARY KEY,
                        action_id TEXT NOT NULL,
                        attempt_number INTEGER NOT NULL,
                        claim_owner TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        submission_intent_at TEXT,
                        ref_id TEXT NOT NULL,
                        payload_digest TEXT NOT NULL,
                        response_classification TEXT,
                        resulting_broker_order_id TEXT,
                        completed_at TEXT,
                        result TEXT,
                        failure_code TEXT,
                        failure_reason TEXT,
                        proof_reference TEXT,
                        finalized INTEGER NOT NULL
                            DEFAULT 0,
                        UNIQUE(
                            action_id, attempt_number
                        ),
                        FOREIGN KEY(action_id)
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            )
                    );

                CREATE TABLE IF NOT EXISTS
                    broker_saga_action_dependencies (
                        action_id TEXT NOT NULL,
                        depends_on_action_id
                            TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        PRIMARY KEY(
                            action_id,
                            depends_on_action_id
                        ),
                        FOREIGN KEY(action_id)
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            ),
                        FOREIGN KEY(
                            depends_on_action_id
                        )
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            )
                    );

                CREATE TABLE IF NOT EXISTS
                    broker_saga_action_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        action_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(action_id)
                            REFERENCES
                            broker_saga_actions(
                                action_id
                            )
                    );

                CREATE TABLE IF NOT EXISTS
                    broker_action_halts (
                        halt_key TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        trade_id TEXT,
                        action_id TEXT,
                        active INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        operator_ack_required INTEGER
                            NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        cleared_at TEXT
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_broker_action_halts_strategy
                ON broker_action_halts(
                    strategy, active
                );

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_event_update
                BEFORE UPDATE
                ON broker_saga_action_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_action_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_event_delete
                BEFORE DELETE
                ON broker_saga_action_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_action_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_attempt_delete
                BEFORE DELETE
                ON broker_saga_action_attempts
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_action_attempts are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_final_saga_attempt_update
                BEFORE UPDATE
                ON broker_saga_action_attempts
                WHEN OLD.finalized = 1
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'finalized broker_saga_action_attempts cannot change'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_saga_dependency_delete
                BEFORE DELETE
                ON broker_saga_action_dependencies
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_saga_action_dependencies are append-only'
                    );
                END;
                """
            )

    @staticmethod
    def _row_to_action(
        row: sqlite3.Row,
    ) -> SagaActionRecord:
        return SagaActionRecord(
            action_id=row["action_id"],
            strategy=row["strategy"],
            trade_id=row["trade_id"],
            authorization_id=row["authorization_id"],
            action_type=SagaActionType(
                row["action_type"]
            ),
            status=SagaActionStatus(row["status"]),
            side=row["side"],
            order_type=row["order_type"],
            trigger_type=row["trigger_type"],
            requested_quantity=(
                _parse_optional_decimal(
                    row["requested_quantity"]
                )
            ),
            limit_price=_parse_optional_decimal(
                row["limit_price"]
            ),
            stop_price=_parse_optional_decimal(
                row["stop_price"]
            ),
            time_in_force=row["time_in_force"],
            market_hours=row["market_hours"],
            close_reason=row["close_reason"],
            source_broker_order_id=(
                row["source_broker_order_id"]
            ),
            resulting_broker_order_id=(
                row["resulting_broker_order_id"]
            ),
            ref_id=row["ref_id"],
            payload_json=row["payload_json"],
            payload_digest=row["payload_digest"],
            logical_action_key=(
                row["logical_action_key"]
            ),
            recovery_reference=(
                row["recovery_reference"]
            ),
            prerequisite_reconciliation_id=(
                row["prerequisite_reconciliation_id"]
            ),
            prerequisite_snapshot_digest=(
                row["prerequisite_snapshot_digest"]
            ),
            account_fingerprint=(
                row["account_fingerprint"]
            ),
            expected_precondition=json.loads(
                row["expected_precondition_json"]
            ),
            expected_postcondition=json.loads(
                row["expected_postcondition_json"]
            ),
            blocked_reason=row["blocked_reason"],
            confirmation_reconciliation_id=(
                row["confirmation_reconciliation_id"]
            ),
            confirmation_snapshot_digest=(
                row["confirmation_snapshot_digest"]
            ),
            created_at=_parse_datetime(
                row["created_at"]
            ),
            ready_at=_parse_datetime(
                row["ready_at"]
            ),
            claim_owner=row["claim_owner"],
            claimed_at=_parse_datetime(
                row["claimed_at"]
            ),
            claim_expires_at=_parse_datetime(
                row["claim_expires_at"]
            ),
            submission_intent_at=_parse_datetime(
                row["submission_intent_at"]
            ),
            last_attempt_at=_parse_datetime(
                row["last_attempt_at"]
            ),
            attempt_count=int(
                row["attempt_count"]
            ),
            last_observation_at=_parse_datetime(
                row["last_observation_at"]
            ),
            confirmed_at=_parse_datetime(
                row["confirmed_at"]
            ),
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
            operator_ack_required=bool(
                row["operator_ack_required"]
            ),
            version=int(row["version"]),
        )

    @staticmethod
    def _row_to_attempt(
        row: sqlite3.Row,
    ) -> SagaActionAttemptRecord:
        return SagaActionAttemptRecord(
            attempt_id=row["attempt_id"],
            action_id=row["action_id"],
            attempt_number=int(
                row["attempt_number"]
            ),
            claim_owner=row["claim_owner"],
            started_at=_parse_datetime(
                row["started_at"]
            ),
            submission_intent_at=_parse_datetime(
                row["submission_intent_at"]
            ),
            ref_id=row["ref_id"],
            payload_digest=row["payload_digest"],
            response_classification=(
                row["response_classification"]
            ),
            resulting_broker_order_id=(
                row["resulting_broker_order_id"]
            ),
            completed_at=_parse_datetime(
                row["completed_at"]
            ),
            result=row["result"],
            failure_code=row["failure_code"],
            failure_reason=row["failure_reason"],
            proof_reference=row["proof_reference"],
            finalized=bool(row["finalized"]),
        )

    def _load_action_row(
        self,
        connection: sqlite3.Connection,
        action_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT *
            FROM broker_saga_actions
            WHERE action_id = ?
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            raise SagaActionNotFoundError(
                f"Broker saga action not found: "
                f"{action_id}"
            )
        return row

    def _insert_event(
        self,
        connection: sqlite3.Connection,
        *,
        action_id: str,
        event_type: str,
        old_status: SagaActionStatus | None,
        new_status: SagaActionStatus | None,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO broker_saga_action_events (
                action_id, event_type, old_status,
                new_status, occurred_at, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                event_type,
                (
                    old_status.value
                    if old_status
                    else None
                ),
                (
                    new_status.value
                    if new_status
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

    def _transition(
        self,
        connection: sqlite3.Connection,
        *,
        current: SagaActionRecord,
        new_status: SagaActionStatus,
        event_type: str,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
        set_clauses: dict[str, Any] | None = None,
    ) -> None:
        if new_status not in SAGA_TRANSITIONS[
            current.status
        ]:
            raise BrokerActionSagaError(
                "Broker saga action cannot move from "
                f"{current.status.value} to "
                f"{new_status.value}."
            )

        assignments = {
            "status": new_status.value,
        }
        assignments.update(set_clauses or {})
        set_sql = ", ".join(
            f"{column} = ?"
            for column in assignments
        )
        parameters = list(assignments.values())
        parameters.extend(
            [
                current.action_id,
                current.status.value,
                current.version,
            ]
        )
        cursor = connection.execute(
            f"""
            UPDATE broker_saga_actions
            SET {set_sql}, version = version + 1
            WHERE action_id = ?
              AND status = ?
              AND version = ?
            """,
            parameters,
        )
        if cursor.rowcount != 1:
            raise BrokerActionSagaError(
                "Broker saga action changed "
                "concurrently."
            )
        self._insert_event(
            connection,
            action_id=current.action_id,
            event_type=event_type,
            old_status=current.status,
            new_status=new_status,
            occurred_at=occurred_at,
            details=details,
        )

    def _activate_halts(
        self,
        connection: sqlite3.Connection,
        *,
        strategy: str,
        trade_id: str | None,
        action_id: str | None,
        reason: str,
        occurred_at: datetime,
        operator_ack_required: bool = True,
        halt_key: str | None = None,
    ) -> None:
        key = halt_key or (
            f"saga-action:{action_id}"
            if action_id
            else f"saga-strategy:{strategy}"
        )
        timestamp = occurred_at.isoformat()
        connection.execute(
            """
            INSERT INTO broker_action_halts (
                halt_key, strategy, trade_id,
                action_id, active, reason,
                operator_ack_required,
                created_at, updated_at, cleared_at
            )
            VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, NULL)
            ON CONFLICT(halt_key) DO UPDATE SET
                active = 1,
                reason = excluded.reason,
                operator_ack_required = (
                    broker_action_halts
                        .operator_ack_required
                    OR excluded.operator_ack_required
                ),
                updated_at = excluded.updated_at,
                cleared_at = NULL
            """,
            (
                key,
                strategy,
                trade_id,
                action_id,
                reason,
                int(operator_ack_required),
                timestamp,
                timestamp,
            ),
        )
        connection.execute(
            """
            INSERT INTO entry_halts (
                halt_key, strategy, trade_id,
                active, reason,
                operator_ack_required,
                created_at, updated_at, cleared_at
            )
            VALUES (?, ?, ?, 1, ?, ?, ?, ?, NULL)
            ON CONFLICT(halt_key) DO UPDATE SET
                active = 1,
                reason = excluded.reason,
                operator_ack_required = (
                    entry_halts.operator_ack_required
                    OR excluded.operator_ack_required
                ),
                updated_at = excluded.updated_at,
                cleared_at = NULL
            """,
            (
                key,
                strategy,
                trade_id,
                reason,
                int(operator_ack_required),
                timestamp,
                timestamp,
            ),
        )

    def _strategy_halt_active(
        self,
        connection: sqlite3.Connection,
        strategy: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM entry_halts
            WHERE strategy = ?
              AND active = 1
            LIMIT 1
            """,
            (strategy,),
        ).fetchone()
        return row is not None

    def _action_halt_active(
        self,
        connection: sqlite3.Connection,
        strategy: str,
    ) -> bool:
        row = connection.execute(
            """
            SELECT 1
            FROM broker_action_halts
            WHERE strategy = ?
              AND active = 1
            LIMIT 1
            """,
            (strategy,),
        ).fetchone()
        return row is not None

    def plan_action(
        self,
        *,
        strategy: str,
        trade_id: str,
        action_type: SagaActionType,
        logical_action_key: str,
        payload: dict[str, Any],
        reconciliation_id: str,
        snapshot_digest: str,
        account_fingerprint: str,
        expected_precondition: dict[str, Any],
        expected_postcondition: dict[str, Any],
        authorization_id: str | None = None,
        side: str | None = None,
        order_type: str | None = None,
        trigger_type: str | None = None,
        requested_quantity: Decimal | int | None = (
            None
        ),
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        time_in_force: str | None = None,
        market_hours: str | None = None,
        close_reason: str | None = None,
        source_broker_order_id: str | None = None,
        recovery_reference: str | None = None,
        depends_on: Iterable[str] = (),
        occurred_at: datetime | None = None,
    ) -> tuple[SagaActionRecord, bool]:
        strategy = strategy.strip()
        trade_id = trade_id.strip()
        logical_action_key = (
            logical_action_key.strip()
        )
        reconciliation_id = reconciliation_id.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "planning")

        if (
            not strategy
            or not trade_id
            or not logical_action_key
            or not reconciliation_id
        ):
            raise BrokerActionSagaError(
                "Strategy, trade ID, logical action "
                "key, and reconciliation ID are "
                "required to plan a broker saga "
                "action."
            )
        if not isinstance(
            action_type, SagaActionType
        ):
            raise BrokerActionSagaError(
                "Broker saga action type is invalid."
            )
        snapshot_digest = _require_hex_digest(
            snapshot_digest, "snapshot"
        )
        account_fingerprint = _require_hex_digest(
            account_fingerprint, "account"
        )
        if not isinstance(
            expected_precondition, dict
        ) or not isinstance(
            expected_postcondition, dict
        ):
            raise BrokerActionSagaError(
                "Expected preconditions and "
                "postconditions must be "
                "machine-readable mappings."
            )
        if (
            action_type in BROKER_WRITE_ACTION_TYPES
            and not (authorization_id or "").strip()
        ):
            raise BrokerActionSagaError(
                "Broker-write saga actions require "
                "an upstream broker action "
                "authorization."
            )
        if requested_quantity is not None:
            requested_quantity = Decimal(
                requested_quantity
            )
            if requested_quantity <= 0:
                raise BrokerActionSagaError(
                    "Requested saga action quantity "
                    "must be positive."
                )

        payload_json = canonical_payload_json(
            payload
        )
        payload_digest = hashlib.sha256(
            payload_json.encode("utf-8")
        ).hexdigest()
        dependency_ids = tuple(
            dict.fromkeys(
                item.strip()
                for item in depends_on
                if item and item.strip()
            )
        )

        with self._connect() as connection:
            trade_row = connection.execute(
                """
                SELECT trade_id, strategy
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ).fetchone()
            if trade_row is None:
                raise BrokerActionSagaError(
                    f"Trade not found: {trade_id}"
                )
            if trade_row["strategy"] != strategy:
                raise BrokerActionSagaError(
                    "Trade strategy does not match "
                    "the planned saga action."
                )

            if authorization_id:
                authorization_id = (
                    authorization_id.strip()
                )
                authorization_row = (
                    connection.execute(
                        """
                        SELECT *
                        FROM
                            broker_action_authorizations
                        WHERE authorization_id = ?
                        """,
                        (authorization_id,),
                    ).fetchone()
                )
                if authorization_row is None:
                    raise BrokerActionSagaError(
                        "Broker action authorization "
                        "was not found for the saga "
                        "action."
                    )
                if (
                    authorization_row["trade_id"]
                    != trade_id
                    or authorization_row["strategy"]
                    != strategy
                    or authorization_row[
                        "account_fingerprint"
                    ]
                    != account_fingerprint
                ):
                    raise BrokerActionSagaError(
                        "Broker action authorization "
                        "does not match the planned "
                        "saga action."
                    )
                if (
                    authorization_row["status"]
                    == "invalidated"
                ):
                    raise BrokerActionSagaError(
                        "An invalidated authorization "
                        "cannot materialize saga "
                        "actions."
                    )

            existing = connection.execute(
                """
                SELECT *
                FROM broker_saga_actions
                WHERE trade_id = ?
                  AND logical_action_key = ?
                ORDER BY created_at DESC, action_id
                LIMIT 1
                """,
                (trade_id, logical_action_key),
            ).fetchone()
            if existing is not None:
                persisted = self._row_to_action(
                    existing
                )
                if (
                    persisted.payload_digest
                    == payload_digest
                    and persisted.action_type
                    == action_type
                ):
                    return persisted, False
                raise BrokerActionSagaError(
                    "A different payload cannot "
                    "reuse an existing logical "
                    "broker saga action."
                )

            for dependency_id in dependency_ids:
                dependency_row = connection.execute(
                    """
                    SELECT trade_id
                    FROM broker_saga_actions
                    WHERE action_id = ?
                    """,
                    (dependency_id,),
                ).fetchone()
                if dependency_row is None:
                    raise BrokerActionSagaError(
                        "Broker saga dependency was "
                        "not found: "
                        f"{dependency_id}"
                    )
                if (
                    dependency_row["trade_id"]
                    != trade_id
                ):
                    raise BrokerActionSagaError(
                        "Broker saga dependencies "
                        "must belong to the same "
                        "trade."
                    )

            action_id = str(uuid.uuid4())
            ref_id = generate_ref_id()
            connection.execute(
                """
                INSERT INTO broker_saga_actions (
                    action_id, strategy, trade_id,
                    authorization_id, action_type,
                    status, side, order_type,
                    trigger_type, requested_quantity,
                    limit_price, stop_price,
                    time_in_force, market_hours,
                    close_reason,
                    source_broker_order_id,
                    resulting_broker_order_id,
                    ref_id, payload_json,
                    payload_digest,
                    logical_action_key,
                    recovery_reference,
                    prerequisite_reconciliation_id,
                    prerequisite_snapshot_digest,
                    account_fingerprint,
                    expected_precondition_json,
                    expected_postcondition_json,
                    blocked_reason,
                    confirmation_reconciliation_id,
                    confirmation_snapshot_digest,
                    created_at, ready_at,
                    claim_owner, claimed_at,
                    claim_expires_at,
                    submission_intent_at,
                    last_attempt_at, attempt_count,
                    last_observation_at,
                    confirmed_at, failure_code,
                    failure_reason,
                    operator_ack_required, version
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, NULL, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, NULL, NULL,
                    NULL, ?, NULL, NULL, NULL, NULL,
                    NULL, NULL, 0, NULL, NULL, NULL,
                    NULL, 0, 1
                )
                """,
                (
                    action_id,
                    strategy,
                    trade_id,
                    authorization_id,
                    action_type.value,
                    SagaActionStatus.PLANNED.value,
                    side,
                    order_type,
                    trigger_type,
                    (
                        normalized_decimal_text(
                            requested_quantity
                        )
                        if requested_quantity
                        is not None
                        else None
                    ),
                    (
                        normalized_decimal_text(
                            limit_price
                        )
                        if limit_price is not None
                        else None
                    ),
                    (
                        normalized_decimal_text(
                            stop_price
                        )
                        if stop_price is not None
                        else None
                    ),
                    time_in_force,
                    market_hours,
                    close_reason,
                    source_broker_order_id,
                    ref_id,
                    payload_json,
                    payload_digest,
                    logical_action_key,
                    recovery_reference,
                    reconciliation_id,
                    snapshot_digest,
                    account_fingerprint,
                    json.dumps(
                        expected_precondition,
                        sort_keys=True,
                        default=str,
                    ),
                    json.dumps(
                        expected_postcondition,
                        sort_keys=True,
                        default=str,
                    ),
                    timestamp.isoformat(),
                ),
            )
            for dependency_id in dependency_ids:
                connection.execute(
                    """
                    INSERT INTO
                        broker_saga_action_dependencies (
                            action_id,
                            depends_on_action_id,
                            created_at
                        )
                    VALUES (?, ?, ?)
                    """,
                    (
                        action_id,
                        dependency_id,
                        timestamp.isoformat(),
                    ),
                )
            self._insert_event(
                connection,
                action_id=action_id,
                event_type="saga_action_planned",
                old_status=None,
                new_status=SagaActionStatus.PLANNED,
                occurred_at=timestamp,
                details={
                    "action_type": action_type.value,
                    "logical_action_key": (
                        logical_action_key
                    ),
                    "payload_digest": payload_digest,
                    "ref_id": ref_id,
                    "authorization_id": (
                        authorization_id
                    ),
                    "depends_on": list(
                        dependency_ids
                    ),
                },
            )

        return self.get_action(action_id), True

    def get_action(
        self,
        action_id: str,
    ) -> SagaActionRecord:
        with self._connect() as connection:
            row = self._load_action_row(
                connection, action_id
            )
        return self._row_to_action(row)

    def list_actions(
        self,
        trade_id: str,
    ) -> list[SagaActionRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM broker_saga_actions
                WHERE trade_id = ?
                ORDER BY created_at, action_id
                """,
                (trade_id,),
            ).fetchall()
        return [
            self._row_to_action(row)
            for row in rows
        ]

    def get_attempts(
        self,
        action_id: str,
    ) -> list[SagaActionAttemptRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT *
                FROM broker_saga_action_attempts
                WHERE action_id = ?
                ORDER BY attempt_number
                """,
                (action_id,),
            ).fetchall()
        return [
            self._row_to_attempt(row)
            for row in rows
        ]

    def get_dependencies(
        self,
        action_id: str,
    ) -> tuple[str, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT depends_on_action_id
                FROM
                    broker_saga_action_dependencies
                WHERE action_id = ?
                ORDER BY depends_on_action_id
                """,
                (action_id,),
            ).fetchall()
        return tuple(
            row["depends_on_action_id"]
            for row in rows
        )

    def count_saga_actions(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_saga_actions
                """
            ).fetchone()
        return int(row["count_value"])

    def count_saga_action_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_saga_action_events
                """
            ).fetchone()
        return int(row["count_value"])

    def count_saga_attempts(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_saga_action_attempts
                """
            ).fetchone()
        return int(row["count_value"])

    def evaluate_readiness(
        self,
        action_id: str,
        *,
        occurred_at: datetime | None = None,
        precondition_observation: (
            dict[str, Any] | None
        ) = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "readiness")

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if current.status not in {
                SagaActionStatus.PLANNED,
                SagaActionStatus.READY,
                SagaActionStatus.BLOCKED,
            }:
                raise BrokerActionSagaError(
                    "Readiness can only be evaluated "
                    "for planned, ready, or blocked "
                    "saga actions."
                )

            blocked_reason: str | None = None

            if self._action_halt_active(
                connection, current.strategy
            ):
                blocked_reason = (
                    "A durable broker-action halt "
                    "is active."
                )
            elif (
                current.action_type
                == SagaActionType.PLACE_ENTRY
                and self._strategy_halt_active(
                    connection, current.strategy
                )
            ):
                blocked_reason = (
                    "A durable strategy halt is "
                    "active."
                )

            if blocked_reason is None:
                dependency_rows = (
                    connection.execute(
                        """
                        SELECT
                            dependency.
                                depends_on_action_id
                                AS dependency_id,
                            upstream.status AS status
                        FROM
                            broker_saga_action_dependencies
                                AS dependency
                        JOIN broker_saga_actions
                            AS upstream
                        ON upstream.action_id =
                            dependency
                                .depends_on_action_id
                        WHERE dependency.action_id = ?
                        """,
                        (action_id,),
                    ).fetchall()
                )
                for dependency_row in (
                    dependency_rows
                ):
                    dependency_status = (
                        SagaActionStatus(
                            dependency_row["status"]
                        )
                    )
                    if (
                        dependency_status
                        in FAILED_DEPENDENCY_STATUSES
                    ):
                        blocked_reason = (
                            "A required saga "
                            "dependency failed: "
                            + dependency_row[
                                "dependency_id"
                            ]
                        )
                        break
                    if (
                        dependency_status
                        != SagaActionStatus
                        .CONFIRMED_SUCCEEDED
                    ):
                        blocked_reason = (
                            "A required saga "
                            "dependency is not "
                            "confirmed: "
                            + dependency_row[
                                "dependency_id"
                            ]
                        )
                        break

            if (
                blocked_reason is None
                and current.expected_precondition
            ):
                if not condition_satisfied(
                    current.expected_precondition,
                    precondition_observation or {},
                ):
                    blocked_reason = (
                        "The durable action "
                        "precondition is not "
                        "satisfied."
                    )

            if blocked_reason is None:
                if (
                    current.status
                    == SagaActionStatus.READY
                ):
                    return current
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus.READY
                    ),
                    event_type="saga_action_ready",
                    occurred_at=timestamp,
                    set_clauses={
                        "ready_at": (
                            timestamp.isoformat()
                        ),
                        "blocked_reason": None,
                    },
                )
            else:
                if (
                    current.status
                    == SagaActionStatus.BLOCKED
                    and current.blocked_reason
                    == blocked_reason
                ):
                    return current
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus.BLOCKED
                    ),
                    event_type="saga_action_blocked",
                    occurred_at=timestamp,
                    details={
                        "reason": blocked_reason,
                    },
                    set_clauses={
                        "blocked_reason": (
                            blocked_reason
                        ),
                    },
                )

        return self.get_action(action_id)

    def claim_action(
        self,
        action_id: str,
        *,
        owner: str,
        ttl: timedelta = DEFAULT_CLAIM_TTL,
        occurred_at: datetime | None = None,
    ) -> tuple[SagaActionRecord, bool]:
        owner = owner.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "claim")
        if not owner:
            raise BrokerActionSagaError(
                "A claim owner is required."
            )
        if ttl <= timedelta(0):
            raise BrokerActionSagaError(
                "Claim TTL must be positive."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )

            if (
                current.status
                == SagaActionStatus.CLAIMED
            ):
                expired = (
                    current.claim_expires_at
                    is not None
                    and timestamp
                    >= current.claim_expires_at
                )
                if (
                    not expired
                    and current.claim_owner == owner
                ):
                    return current, False
                if not expired:
                    raise BrokerActionSagaError(
                        "An unexpired claim by "
                        "another owner blocks this "
                        "saga action."
                    )
                raise BrokerActionSagaError(
                    "An expired saga claim requires "
                    "explicit recovery before "
                    "reclaiming."
                )

            if (
                current.status
                != SagaActionStatus.READY
            ):
                raise BrokerActionSagaError(
                    "Only READY saga actions can be "
                    "claimed."
                )

            if self._action_halt_active(
                connection, current.strategy
            ):
                raise SagaHaltedError(
                    "A durable broker-action halt "
                    "blocks saga claims."
                )

            self._transition(
                connection,
                current=current,
                new_status=SagaActionStatus.CLAIMED,
                event_type="saga_action_claimed",
                occurred_at=timestamp,
                details={"claim_owner": owner},
                set_clauses={
                    "claim_owner": owner,
                    "claimed_at": (
                        timestamp.isoformat()
                    ),
                    "claim_expires_at": (
                        (
                            timestamp + ttl
                        ).isoformat()
                    ),
                },
            )

        return self.get_action(action_id), True

    def release_claim(
        self,
        action_id: str,
        *,
        owner: str,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        owner = owner.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "claim release")

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus.CLAIMED
                or current.claim_owner != owner
            ):
                raise BrokerActionSagaError(
                    "Only the claim owner may "
                    "release an active saga claim."
                )
            self._transition(
                connection,
                current=current,
                new_status=SagaActionStatus.READY,
                event_type=(
                    "saga_claim_released"
                ),
                occurred_at=timestamp,
                details={"claim_owner": owner},
                set_clauses={
                    "claim_owner": None,
                    "claimed_at": None,
                    "claim_expires_at": None,
                },
            )

        return self.get_action(action_id)

    def recover_expired_claim(
        self,
        action_id: str,
        *,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "claim recovery"
        )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if current.status not in {
                SagaActionStatus.CLAIMED,
                SagaActionStatus
                .SUBMISSION_INTENT_RECORDED,
            }:
                raise BrokerActionSagaError(
                    "Only claimed saga actions can "
                    "recover from claim expiration."
                )
            if (
                current.claim_expires_at is None
                or timestamp
                < current.claim_expires_at
            ):
                raise BrokerActionSagaError(
                    "The saga claim has not expired."
                )

            if (
                current.submission_intent_at
                is not None
            ):
                self._finalize_open_attempt(
                    connection,
                    action_id=action_id,
                    occurred_at=timestamp,
                    classification="unknown",
                    result="unknown",
                    failure_code=(
                        "claim_expired_after_intent"
                    ),
                    failure_reason=(
                        "The claim expired after "
                        "submission intent was "
                        "persisted."
                    ),
                )
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus
                        .OUTCOME_UNKNOWN
                    ),
                    event_type=(
                        "saga_outcome_unknown"
                    ),
                    occurred_at=timestamp,
                    details={
                        "reason": (
                            "Claim expired after "
                            "submission intent."
                        ),
                    },
                )
                self._activate_halts(
                    connection,
                    strategy=current.strategy,
                    trade_id=current.trade_id,
                    action_id=current.action_id,
                    reason=(
                        "A saga action outcome is "
                        "unknown after claim "
                        "expiration."
                    ),
                    occurred_at=timestamp,
                )
            else:
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus.READY
                    ),
                    event_type=(
                        "saga_claim_expired_released"
                    ),
                    occurred_at=timestamp,
                    details={
                        "previous_owner": (
                            current.claim_owner
                        ),
                    },
                    set_clauses={
                        "claim_owner": None,
                        "claimed_at": None,
                        "claim_expires_at": None,
                    },
                )

        return self.get_action(action_id)

    def _finalize_open_attempt(
        self,
        connection: sqlite3.Connection,
        *,
        action_id: str,
        occurred_at: datetime,
        classification: str,
        result: str,
        resulting_broker_order_id: str | None = (
            None
        ),
        failure_code: str | None = None,
        failure_reason: str | None = None,
        proof_reference: str | None = None,
    ) -> bool:
        row = connection.execute(
            """
            SELECT attempt_id
            FROM broker_saga_action_attempts
            WHERE action_id = ?
              AND finalized = 0
            ORDER BY attempt_number DESC
            LIMIT 1
            """,
            (action_id,),
        ).fetchone()
        if row is None:
            return False
        connection.execute(
            """
            UPDATE broker_saga_action_attempts
            SET response_classification = ?,
                resulting_broker_order_id = ?,
                completed_at = ?,
                result = ?,
                failure_code = ?,
                failure_reason = ?,
                proof_reference = ?,
                finalized = 1
            WHERE attempt_id = ?
              AND finalized = 0
            """,
            (
                classification,
                resulting_broker_order_id,
                occurred_at.isoformat(),
                result,
                failure_code,
                failure_reason,
                proof_reference,
                row["attempt_id"],
            ),
        )
        return True

    def record_submission_intent(
        self,
        action_id: str,
        *,
        owner: str,
        occurred_at: datetime | None = None,
    ) -> tuple[
        SagaActionRecord, SagaActionAttemptRecord
    ]:
        owner = owner.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "submission intent"
        )
        if not owner:
            raise BrokerActionSagaError(
                "A claim owner is required to "
                "record submission intent."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus.CLAIMED
                or current.claim_owner != owner
            ):
                raise BrokerActionSagaError(
                    "Submission intent requires an "
                    "active claim by the same owner."
                )
            if (
                current.claim_expires_at is None
                or timestamp
                >= current.claim_expires_at
            ):
                raise BrokerActionSagaError(
                    "Submission intent cannot be "
                    "recorded on an expired claim."
                )
            if self._action_halt_active(
                connection, current.strategy
            ):
                raise SagaHaltedError(
                    "A durable broker-action halt "
                    "blocks submission intent."
                )

            attempt_id = str(uuid.uuid4())
            attempt_number = (
                current.attempt_count + 1
            )
            connection.execute(
                """
                INSERT INTO
                    broker_saga_action_attempts (
                        attempt_id, action_id,
                        attempt_number, claim_owner,
                        started_at,
                        submission_intent_at,
                        ref_id, payload_digest,
                        response_classification,
                        resulting_broker_order_id,
                        completed_at, result,
                        failure_code, failure_reason,
                        proof_reference, finalized
                    )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, NULL,
                    NULL, NULL, NULL, NULL, NULL,
                    NULL, 0
                )
                """,
                (
                    attempt_id,
                    action_id,
                    attempt_number,
                    owner,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    current.ref_id,
                    current.payload_digest,
                ),
            )
            self._transition(
                connection,
                current=current,
                new_status=(
                    SagaActionStatus
                    .SUBMISSION_INTENT_RECORDED
                ),
                event_type=(
                    "saga_submission_intent_recorded"
                ),
                occurred_at=timestamp,
                details={
                    "attempt_id": attempt_id,
                    "attempt_number": (
                        attempt_number
                    ),
                    "ref_id": current.ref_id,
                    "payload_digest": (
                        current.payload_digest
                    ),
                },
                set_clauses={
                    "submission_intent_at": (
                        timestamp.isoformat()
                    ),
                    "last_attempt_at": (
                        timestamp.isoformat()
                    ),
                    "attempt_count": (
                        attempt_number
                    ),
                },
            )

        attempts = self.get_attempts(action_id)
        return (
            self.get_action(action_id),
            attempts[-1],
        )

    def finalize_attempt(
        self,
        action_id: str,
        *,
        owner: str,
        classification: str,
        resulting_broker_order_id: str | None = (
            None
        ),
        failure_code: str | None = None,
        failure_reason: str | None = None,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        owner = owner.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "attempt finalization"
        )
        if (
            classification
            not in ATTEMPT_CLASSIFICATIONS
        ):
            raise BrokerActionSagaError(
                "Simulated attempt classification "
                "is unsupported."
            )

        new_status = ATTEMPT_CLASSIFICATIONS[
            classification
        ]

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus
                .SUBMISSION_INTENT_RECORDED
                or current.claim_owner != owner
            ):
                raise BrokerActionSagaError(
                    "Attempt finalization requires "
                    "a recorded submission intent "
                    "by the same claim owner."
                )

            finalized = self._finalize_open_attempt(
                connection,
                action_id=action_id,
                occurred_at=timestamp,
                classification=classification,
                result=(
                    "unknown"
                    if classification == "timeout"
                    else classification
                ),
                resulting_broker_order_id=(
                    resulting_broker_order_id
                ),
                failure_code=failure_code,
                failure_reason=failure_reason,
            )
            if not finalized:
                raise BrokerActionSagaError(
                    "No open saga attempt exists to "
                    "finalize."
                )

            set_clauses: dict[str, Any] = {
                "last_attempt_at": (
                    timestamp.isoformat()
                ),
            }
            if resulting_broker_order_id:
                set_clauses[
                    "resulting_broker_order_id"
                ] = resulting_broker_order_id
            if classification in {
                "rejected_retryable",
                "rejected_terminal",
            }:
                set_clauses["failure_code"] = (
                    failure_code
                )
                set_clauses["failure_reason"] = (
                    failure_reason
                )

            self._transition(
                connection,
                current=current,
                new_status=new_status,
                event_type=(
                    "saga_attempt_finalized"
                ),
                occurred_at=timestamp,
                details={
                    "classification": (
                        classification
                    ),
                    "resulting_broker_order_id": (
                        resulting_broker_order_id
                    ),
                    "failure_code": failure_code,
                    "failure_reason": (
                        failure_reason
                    ),
                },
                set_clauses=set_clauses,
            )

            if (
                new_status
                == SagaActionStatus.OUTCOME_UNKNOWN
            ):
                self._activate_halts(
                    connection,
                    strategy=current.strategy,
                    trade_id=current.trade_id,
                    action_id=current.action_id,
                    reason=(
                        "A simulated saga "
                        "submission has an unknown "
                        "outcome."
                    ),
                    occurred_at=timestamp,
                )

        return self.get_action(action_id)

    def mark_outcome_unknown(
        self,
        action_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "unknown outcome"
        )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if current.status not in {
                SagaActionStatus
                .SUBMISSION_INTENT_RECORDED,
                SagaActionStatus
                .AWAITING_BROKER_CONFIRMATION,
            }:
                raise BrokerActionSagaError(
                    "Only submitted saga actions "
                    "can become OUTCOME_UNKNOWN."
                )
            self._finalize_open_attempt(
                connection,
                action_id=action_id,
                occurred_at=timestamp,
                classification="unknown",
                result="unknown",
                failure_reason=reason,
            )
            self._transition(
                connection,
                current=current,
                new_status=(
                    SagaActionStatus.OUTCOME_UNKNOWN
                ),
                event_type="saga_outcome_unknown",
                occurred_at=timestamp,
                details={"reason": reason},
            )
            self._activate_halts(
                connection,
                strategy=current.strategy,
                trade_id=current.trade_id,
                action_id=current.action_id,
                reason=(
                    "A saga action outcome is "
                    "unknown: " + reason
                ),
                occurred_at=timestamp,
            )

        return self.get_action(action_id)

    def _validated_proof(
        self,
        current: SagaActionRecord,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        maximum_snapshot_age: timedelta,
    ):
        _require_aware(
            evaluated_at, "proof evaluation"
        )
        validation = validate_snapshot(
            snapshot,
            evaluated_at=evaluated_at,
            maximum_age=maximum_snapshot_age,
        )
        if not validation.valid:
            raise BrokerActionSagaError(
                validation.failure_reason
                or "Broker snapshot proof is "
                "invalid."
            )
        if (
            snapshot.metadata.account_fingerprint
            != current.account_fingerprint
        ):
            raise BrokerActionSagaError(
                "Broker snapshot proof does not "
                "match the commissioned account "
                "fingerprint."
            )
        return validation

    def resolve_unknown_outcome(
        self,
        action_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        observation: dict[str, Any],
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or evaluated_at
        _require_aware(
            timestamp, "unknown resolution"
        )
        if not isinstance(observation, dict):
            raise BrokerActionSagaError(
                "Unknown-outcome resolution "
                "requires a machine-readable "
                "observation."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus.OUTCOME_UNKNOWN
            ):
                raise BrokerActionSagaError(
                    "Only OUTCOME_UNKNOWN saga "
                    "actions can be resolved with "
                    "fresh evidence."
                )
            validation = self._validated_proof(
                current,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
            )

            effect_present = observation.get(
                "effect_present"
            )
            details = {
                "reconciliation_id": (
                    snapshot.metadata
                    .reconciliation_id
                ),
                "snapshot_digest": (
                    validation.snapshot_digest
                ),
                "effect_present": effect_present,
            }
            if effect_present is True:
                set_clauses: dict[str, Any] = {
                    "last_observation_at": (
                        timestamp.isoformat()
                    ),
                }
                observed_order_id = observation.get(
                    "resulting_broker_order_id"
                )
                if observed_order_id:
                    set_clauses[
                        "resulting_broker_order_id"
                    ] = str(observed_order_id)
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus
                        .AWAITING_BROKER_CONFIRMATION
                    ),
                    event_type=(
                        "saga_unknown_resolved_performed"
                    ),
                    occurred_at=timestamp,
                    details=details,
                    set_clauses=set_clauses,
                )
            elif effect_present is False:
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus
                        .CONFIRMED_NOT_PERFORMED
                    ),
                    event_type=(
                        "saga_unknown_resolved_not_performed"
                    ),
                    occurred_at=timestamp,
                    details=details,
                    set_clauses={
                        "last_observation_at": (
                            timestamp.isoformat()
                        ),
                    },
                )
            else:
                self._insert_event(
                    connection,
                    action_id=action_id,
                    event_type=(
                        "saga_unknown_still_ambiguous"
                    ),
                    old_status=current.status,
                    new_status=current.status,
                    occurred_at=timestamp,
                    details=details,
                )
                return current

        return self.get_action(action_id)

    def resolve_operator_required(
        self,
        action_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        observation: dict[str, Any],
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        """
        Resolves an OPERATOR_REQUIRED action with fresh
        broker evidence only.

        Operator acknowledgment can never reach this
        transition; it requires a validated fresh,
        complete, account-matched snapshot plus an
        explicit machine-readable effect observation.
        """
        timestamp = occurred_at or evaluated_at
        _require_aware(
            timestamp, "operator resolution"
        )
        if not isinstance(observation, dict):
            raise BrokerActionSagaError(
                "Operator resolution requires a "
                "machine-readable observation."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus.OPERATOR_REQUIRED
            ):
                raise BrokerActionSagaError(
                    "Only OPERATOR_REQUIRED saga "
                    "actions can be resolved with "
                    "operator evidence."
                )
            validation = self._validated_proof(
                current,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
            )

            effect_present = observation.get(
                "effect_present"
            )
            details = {
                "reconciliation_id": (
                    snapshot.metadata
                    .reconciliation_id
                ),
                "snapshot_digest": (
                    validation.snapshot_digest
                ),
                "effect_present": effect_present,
            }
            if effect_present is True:
                set_clauses: dict[str, Any] = {
                    "last_observation_at": (
                        timestamp.isoformat()
                    ),
                }
                observed_order_id = observation.get(
                    "resulting_broker_order_id"
                )
                if observed_order_id:
                    set_clauses[
                        "resulting_broker_order_id"
                    ] = str(observed_order_id)
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus
                        .AWAITING_BROKER_CONFIRMATION
                    ),
                    event_type=(
                        "saga_operator_resolved_performed"
                    ),
                    occurred_at=timestamp,
                    details=details,
                    set_clauses=set_clauses,
                )
            elif effect_present is False:
                self._transition(
                    connection,
                    current=current,
                    new_status=(
                        SagaActionStatus
                        .CONFIRMED_NOT_PERFORMED
                    ),
                    event_type=(
                        "saga_operator_resolved_not_performed"
                    ),
                    occurred_at=timestamp,
                    details=details,
                    set_clauses={
                        "last_observation_at": (
                            timestamp.isoformat()
                        ),
                    },
                )
            else:
                self._insert_event(
                    connection,
                    action_id=action_id,
                    event_type=(
                        "saga_operator_evidence_ambiguous"
                    ),
                    old_status=current.status,
                    new_status=current.status,
                    occurred_at=timestamp,
                    details=details,
                )
                return current

        return self.get_action(action_id)

    def confirm_action_succeeded(
        self,
        action_id: str,
        *,
        snapshot: BrokerSnapshot,
        evaluated_at: datetime,
        observation: dict[str, Any],
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or evaluated_at
        _require_aware(timestamp, "confirmation")
        if not isinstance(observation, dict):
            raise BrokerActionSagaError(
                "Success confirmation requires a "
                "machine-readable observation."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                != SagaActionStatus
                .AWAITING_BROKER_CONFIRMATION
            ):
                raise BrokerActionSagaError(
                    "Only saga actions awaiting "
                    "broker confirmation can be "
                    "confirmed."
                )
            validation = self._validated_proof(
                current,
                snapshot=snapshot,
                evaluated_at=evaluated_at,
                maximum_snapshot_age=(
                    maximum_snapshot_age
                ),
            )
            if not condition_satisfied(
                current.expected_postcondition,
                observation,
            ):
                raise BrokerActionSagaError(
                    "Fresh broker evidence does not "
                    "prove the expected "
                    "postcondition."
                )

            set_clauses: dict[str, Any] = {
                "confirmed_at": (
                    timestamp.isoformat()
                ),
                "last_observation_at": (
                    timestamp.isoformat()
                ),
                "confirmation_reconciliation_id": (
                    snapshot.metadata
                    .reconciliation_id
                ),
                "confirmation_snapshot_digest": (
                    validation.snapshot_digest
                ),
            }
            observed_order_id = observation.get(
                "resulting_broker_order_id"
            )
            if observed_order_id:
                set_clauses[
                    "resulting_broker_order_id"
                ] = str(observed_order_id)

            self._transition(
                connection,
                current=current,
                new_status=(
                    SagaActionStatus
                    .CONFIRMED_SUCCEEDED
                ),
                event_type=(
                    "saga_action_confirmed_succeeded"
                ),
                occurred_at=timestamp,
                details={
                    "reconciliation_id": (
                        snapshot.metadata
                        .reconciliation_id
                    ),
                    "snapshot_digest": (
                        validation.snapshot_digest
                    ),
                },
                set_clauses=set_clauses,
            )
            self._finalize_open_attempt(
                connection,
                action_id=action_id,
                occurred_at=timestamp,
                classification="confirmed",
                result="confirmed_succeeded",
                proof_reference=(
                    snapshot.metadata
                    .reconciliation_id
                ),
            )

        return self.get_action(action_id)

    def approve_retry(
        self,
        action_id: str,
        *,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "retry approval")

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if (
                current.status
                not in RETRYABLE_STATUSES
            ):
                raise BrokerActionSagaError(
                    "Controlled retry is only "
                    "permitted after proof of no "
                    "effect or a retryable failure."
                )
            if self._action_halt_active(
                connection, current.strategy
            ):
                raise SagaHaltedError(
                    "A durable broker-action halt "
                    "must be cleared before a "
                    "controlled retry."
                )
            self._transition(
                connection,
                current=current,
                new_status=SagaActionStatus.READY,
                event_type=(
                    "saga_controlled_retry_approved"
                ),
                occurred_at=timestamp,
                details={
                    "ref_id": current.ref_id,
                    "attempt_count": (
                        current.attempt_count
                    ),
                },
                set_clauses={
                    "claim_owner": None,
                    "claimed_at": None,
                    "claim_expires_at": None,
                    "blocked_reason": None,
                    "failure_code": None,
                    "failure_reason": None,
                },
            )

        return self.get_action(action_id)

    def cancel_action(
        self,
        action_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "cancellation")
        reason = reason.strip()
        if not reason:
            raise BrokerActionSagaError(
                "A durable cancellation reason is "
                "required."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            if current.status not in {
                SagaActionStatus.PLANNED,
                SagaActionStatus.READY,
                SagaActionStatus.BLOCKED,
                SagaActionStatus
                .CONFIRMED_NOT_PERFORMED,
                SagaActionStatus.RETRYABLE_FAILURE,
            }:
                raise BrokerActionSagaError(
                    "Only unsubmitted or provably "
                    "unperformed saga actions can "
                    "be cancelled."
                )
            self._transition(
                connection,
                current=current,
                new_status=(
                    SagaActionStatus.CANCELLED
                ),
                event_type="saga_action_cancelled",
                occurred_at=timestamp,
                details={"reason": reason},
                set_clauses={
                    "failure_code": "cancelled",
                    "failure_reason": reason,
                },
            )

        return self.get_action(action_id)

    def require_operator(
        self,
        action_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "operator escalation"
        )
        reason = reason.strip()
        if not reason:
            raise BrokerActionSagaError(
                "An operator escalation reason is "
                "required."
            )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            self._transition(
                connection,
                current=current,
                new_status=(
                    SagaActionStatus
                    .OPERATOR_REQUIRED
                ),
                event_type=(
                    "saga_operator_required"
                ),
                occurred_at=timestamp,
                details={"reason": reason},
                set_clauses={
                    "operator_ack_required": 1,
                    "failure_reason": reason,
                },
            )
            self._activate_halts(
                connection,
                strategy=current.strategy,
                trade_id=current.trade_id,
                action_id=current.action_id,
                reason=(
                    "Operator intervention is "
                    "required: " + reason
                ),
                occurred_at=timestamp,
            )

        return self.get_action(action_id)

    def acknowledge_operator(
        self,
        action_id: str,
        *,
        note: str,
        occurred_at: datetime | None = None,
    ) -> SagaActionRecord:
        """
        Records a durable operator acknowledgment.

        Acknowledgment alone never proves a broker
        outcome and never changes action status or
        clears a halt.
        """
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "operator acknowledgment"
        )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            self._insert_event(
                connection,
                action_id=action_id,
                event_type=(
                    "saga_operator_acknowledged"
                ),
                old_status=current.status,
                new_status=current.status,
                occurred_at=timestamp,
                details={"note": note},
            )

        return self.get_action(action_id)

    def verify_action_integrity(
        self,
        action_id: str,
        *,
        occurred_at: datetime | None = None,
    ) -> bool:
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "integrity verification"
        )

        with self._connect() as connection:
            current = self._row_to_action(
                self._load_action_row(
                    connection, action_id
                )
            )
            recomputed = hashlib.sha256(
                current.payload_json.encode("utf-8")
            ).hexdigest()
            failures: list[str] = []
            if recomputed != current.payload_digest:
                failures.append(
                    "The persisted payload does "
                    "not match its digest."
                )
            attempt_rows = connection.execute(
                """
                SELECT ref_id, payload_digest
                FROM broker_saga_action_attempts
                WHERE action_id = ?
                """,
                (action_id,),
            ).fetchall()
            for attempt_row in attempt_rows:
                if (
                    attempt_row["ref_id"]
                    != current.ref_id
                    or attempt_row["payload_digest"]
                    != current.payload_digest
                ):
                    failures.append(
                        "A persisted attempt does "
                        "not match the action "
                        "ref_id and payload digest."
                    )
                    break

        if failures:
            with self._connect() as connection:
                self._activate_halts(
                    connection,
                    strategy=current.strategy,
                    trade_id=current.trade_id,
                    action_id=current.action_id,
                    reason="; ".join(failures),
                    occurred_at=timestamp,
                )
            raise BrokerActionSagaError(
                "; ".join(failures)
            )

        return True

    def activate_broker_action_halt(
        self,
        *,
        strategy: str,
        reason: str,
        trade_id: str | None = None,
        action_id: str | None = None,
        halt_key: str | None = None,
        operator_ack_required: bool = False,
        occurred_at: datetime | None = None,
    ) -> None:
        strategy = strategy.strip()
        reason = reason.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(
            timestamp, "halt activation"
        )
        if not strategy or not reason:
            raise BrokerActionSagaError(
                "A strategy and reason are required "
                "to activate a broker-action halt."
            )

        with self._connect() as connection:
            self._activate_halts(
                connection,
                strategy=strategy,
                trade_id=trade_id,
                action_id=action_id,
                reason=reason,
                occurred_at=timestamp,
                operator_ack_required=(
                    operator_ack_required
                ),
                halt_key=halt_key,
            )

    def get_broker_action_halt(
        self,
        strategy: str,
    ) -> BrokerActionHaltState:
        strategy = strategy.strip()
        if not strategy:
            raise BrokerActionSagaError(
                "Strategy name is required."
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT reason,
                       operator_ack_required
                FROM broker_action_halts
                WHERE strategy = ?
                  AND active = 1
                ORDER BY created_at, halt_key
                """,
                (strategy,),
            ).fetchall()

        return BrokerActionHaltState(
            strategy=strategy,
            active=bool(rows),
            reasons=tuple(
                row["reason"] for row in rows
            ),
            operator_ack_required=any(
                bool(row["operator_ack_required"])
                for row in rows
            ),
        )

    def is_broker_action_halted(
        self,
        strategy: str,
    ) -> bool:
        return self.get_broker_action_halt(
            strategy
        ).active

    def clear_broker_action_halt(
        self,
        *,
        strategy: str,
        halt_key: str,
        occurred_at: datetime | None = None,
    ) -> None:
        strategy = strategy.strip()
        halt_key = halt_key.strip()
        timestamp = occurred_at or utc_now()
        _require_aware(timestamp, "halt clearing")

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM broker_action_halts
                WHERE halt_key = ?
                """,
                (halt_key,),
            ).fetchone()
            if row is None or not row["active"]:
                raise BrokerActionSagaError(
                    "No active broker-action halt "
                    "matches the requested key."
                )
            if row["strategy"] != strategy:
                raise BrokerActionSagaError(
                    "A broker-action halt can only "
                    "be cleared by its own strategy."
                )
            if row["action_id"]:
                action_row = connection.execute(
                    """
                    SELECT status
                    FROM broker_saga_actions
                    WHERE action_id = ?
                    """,
                    (row["action_id"],),
                ).fetchone()
                if (
                    action_row is not None
                    and action_row["status"]
                    in {
                        SagaActionStatus
                        .OUTCOME_UNKNOWN.value,
                        SagaActionStatus
                        .OPERATOR_REQUIRED.value,
                    }
                ):
                    raise BrokerActionSagaError(
                        "A halt cannot be cleared "
                        "while its saga action "
                        "outcome remains unknown or "
                        "requires unresolved "
                        "operator evidence."
                    )
            connection.execute(
                """
                UPDATE broker_action_halts
                SET active = 0,
                    updated_at = ?,
                    cleared_at = ?
                WHERE halt_key = ?
                  AND active = 1
                """,
                (
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    halt_key,
                ),
            )


def main() -> None:
    print("\nDURABLE BROKER ACTION SAGA\n")
    print(
        "Every future broker operation is persisted "
        "as a durable, idempotent, restart-safe "
        "saga action with a stable ref_id, canonical "
        "payload digest, claims, append-only "
        "attempts, dependencies, and fail-closed "
        "unknown-outcome handling."
    )
    print("Broker write capability: False")


if __name__ == "__main__":
    main()
