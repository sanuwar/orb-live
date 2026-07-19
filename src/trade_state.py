from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

try:
    from .broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        SnapshotValidation,
        validate_snapshot,
    )
except ImportError:
    from broker_snapshot import (
        DEFAULT_MAXIMUM_SNAPSHOT_AGE,
        BrokerSnapshot,
        SnapshotValidation,
        validate_snapshot,
    )


DEFAULT_DB_PATH = Path("data/trade_state.sqlite")


class TradeStateError(RuntimeError):
    pass


class TradeNotFoundError(TradeStateError):
    pass


class DuplicateEntryError(TradeStateError):
    pass


class InvalidTransitionError(TradeStateError):
    pass


class EntryHaltedError(TradeStateError):
    pass


class TradeStatus(str, Enum):
    RESERVED = "reserved"
    ENTRY_PENDING = "entry_pending"
    ENTRY_PARTIALLY_FILLED = "entry_partially_filled"
    PROTECTION_PENDING = "protection_pending"
    RECOVERY_REQUIRED = "recovery_required"
    OPEN_UNPROTECTED = "open_unprotected"
    STOP_LINKED = "stop_linked"
    OPEN_PROTECTED = "open_protected"
    STOP_RECOVERY_REQUIRED = (
        "stop_recovery_required"
    )
    EXIT_PENDING = "exit_pending"
    EXIT_PARTIALLY_FILLED = (
        "exit_partially_filled"
    )
    EXIT_RECOVERY_REQUIRED = (
        "exit_recovery_required"
    )
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ERROR = "error"


ACTIVE_STATUSES = {
    TradeStatus.RESERVED,
    TradeStatus.ENTRY_PENDING,
    TradeStatus.ENTRY_PARTIALLY_FILLED,
    TradeStatus.PROTECTION_PENDING,
    TradeStatus.RECOVERY_REQUIRED,
    TradeStatus.OPEN_UNPROTECTED,
    TradeStatus.STOP_LINKED,
    TradeStatus.OPEN_PROTECTED,
    TradeStatus.STOP_RECOVERY_REQUIRED,
    TradeStatus.EXIT_PENDING,
    TradeStatus.EXIT_PARTIALLY_FILLED,
    TradeStatus.EXIT_RECOVERY_REQUIRED,
}


ALLOWED_TRANSITIONS = {
    TradeStatus.RESERVED: {
        TradeStatus.ENTRY_PENDING,
        TradeStatus.CANCELLED,
        TradeStatus.ERROR,
    },
    TradeStatus.ENTRY_PENDING: {
        TradeStatus.ENTRY_PARTIALLY_FILLED,
        TradeStatus.PROTECTION_PENDING,
        TradeStatus.RECOVERY_REQUIRED,
        TradeStatus.CANCELLED,
        TradeStatus.ERROR,
    },
    TradeStatus.ENTRY_PARTIALLY_FILLED: {
        TradeStatus.ENTRY_PARTIALLY_FILLED,
        TradeStatus.PROTECTION_PENDING,
        TradeStatus.RECOVERY_REQUIRED,
        TradeStatus.STOP_LINKED,
        TradeStatus.ERROR,
    },
    TradeStatus.PROTECTION_PENDING: {
        TradeStatus.PROTECTION_PENDING,
        TradeStatus.RECOVERY_REQUIRED,
        TradeStatus.STOP_LINKED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.RECOVERY_REQUIRED: {
        TradeStatus.RECOVERY_REQUIRED,
        TradeStatus.PROTECTION_PENDING,
        TradeStatus.STOP_LINKED,
        TradeStatus.ERROR,
    },
    TradeStatus.OPEN_UNPROTECTED: {
        TradeStatus.STOP_LINKED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.STOP_LINKED: {
        TradeStatus.STOP_LINKED,
        TradeStatus.OPEN_PROTECTED,
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.EXIT_RECOVERY_REQUIRED,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.OPEN_PROTECTED: {
        TradeStatus.STOP_LINKED,
        TradeStatus.STOP_RECOVERY_REQUIRED,
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.STOP_RECOVERY_REQUIRED: {
        TradeStatus.STOP_RECOVERY_REQUIRED,
        TradeStatus.STOP_LINKED,
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.OPEN_PROTECTED,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.EXIT_PENDING: {
        TradeStatus.EXIT_PENDING,
        TradeStatus.EXIT_PARTIALLY_FILLED,
        TradeStatus.EXIT_RECOVERY_REQUIRED,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.EXIT_PARTIALLY_FILLED: {
        TradeStatus.EXIT_PARTIALLY_FILLED,
        TradeStatus.EXIT_RECOVERY_REQUIRED,
        TradeStatus.STOP_LINKED,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.EXIT_RECOVERY_REQUIRED: {
        TradeStatus.EXIT_RECOVERY_REQUIRED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.STOP_LINKED,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.CLOSED: set(),
    TradeStatus.CANCELLED: set(),
    TradeStatus.ERROR: set(),
}


class EntryRecoveryAction(str, Enum):
    NONE = "none"
    WAIT = "wait"
    CANCEL_REMAINDER_THEN_PROTECT = (
        "cancel_remainder_then_protect"
    )
    PROTECT_FILLED_QUANTITY = (
        "protect_filled_quantity"
    )
    RECONCILE_BROKER_STATE = (
        "reconcile_broker_state"
    )


class EntryRecoveryStep(str, Enum):
    OBSERVATION_RECORDED = "observation_recorded"
    PARTIAL_FILL_CLASSIFIED = (
        "partial_fill_classified"
    )
    FULL_FILL_CLASSIFIED = "full_fill_classified"
    AMBIGUOUS_STATE_RECORDED = (
        "ambiguous_state_recorded"
    )
    FLAT_CONFIRMED = "flat_confirmed"
    PROTECTIVE_STOP_LINKED = (
        "protective_stop_linked"
    )
    PROTECTION_CONFIRMED = (
        "protection_confirmed"
    )


class StopRecoveryAction(str, Enum):
    NONE = "none"
    RECONCILE_BROKER_STATE = (
        "reconcile_broker_state"
    )
    PROTECT_REMAINING_POSITION = (
        "protect_remaining_position"
    )
    REPLACE_PROTECTION_OR_FLATTEN = (
        "replace_protection_or_flatten"
    )
    OPERATOR_INTERVENTION = (
        "operator_intervention"
    )


class StopRecoveryStep(str, Enum):
    OBSERVATION_RECORDED = "observation_recorded"
    ACTIVE_CONFIRMED = "active_confirmed"
    PARTIAL_FILL_RECORDED = (
        "partial_fill_recorded"
    )
    FULL_FILL_RECORDED = "full_fill_recorded"
    PROTECTION_LOST_RECORDED = (
        "protection_lost_recorded"
    )
    INCONSISTENT_STATE_RECORDED = (
        "inconsistent_state_recorded"
    )
    FLAT_CONFIRMED = "flat_confirmed"
    PROTECTION_CONFIRMED = (
        "protection_confirmed"
    )


class ExitIntentType(str, Enum):
    PROFIT_TARGET = "profit_target"
    TIME_EXIT = "time_exit"
    STOP_RECOVERY = "stop_recovery"
    OPERATOR_EXIT = "operator_exit"


class ExitRecoveryAction(str, Enum):
    NONE = "none"
    WAIT = "wait"
    RECONCILE_BROKER_STATE = (
        "reconcile_broker_state"
    )
    PROTECT_REMAINING_POSITION = (
        "protect_remaining_position"
    )
    OPERATOR_INTERVENTION = (
        "operator_intervention"
    )


class ExitRecoveryStep(str, Enum):
    INTENT_LINKED = "intent_linked"
    OBSERVATION_RECORDED = "observation_recorded"
    PARTIAL_FILL_RECORDED = "partial_fill_recorded"
    FULL_FILL_RECORDED = "full_fill_recorded"
    TERMINAL_RECORDED = "terminal_recorded"
    INCONSISTENT_STATE_RECORDED = (
        "inconsistent_state_recorded"
    )
    FLAT_CONFIRMED = "flat_confirmed"
    PROTECTION_CONFIRMED = "protection_confirmed"


class BrokerActionAuthorizationStatus(str, Enum):
    AUTHORIZED = "authorized"
    CLAIMED = "claimed"
    INVALIDATED = "invalidated"


@dataclass(frozen=True)
class BrokerActionAuthorizationRecord:
    authorization_id: str
    strategy: str
    trade_id: str
    purpose: str
    plan_digest: str
    source_trade_version: int
    reconciliation_id: str
    snapshot_digest: str
    account_fingerprint: str
    status: BrokerActionAuthorizationStatus
    authorized_at: datetime
    expires_at: datetime
    claim_id: str | None
    claimed_at: datetime | None
    invalidated_at: datetime | None
    invalidation_reason: str | None
    version: int


@dataclass(frozen=True)
class EntryHaltState:
    strategy: str
    active: bool
    reasons: tuple[str, ...]
    operator_ack_required: bool


@dataclass(frozen=True)
class TradeRecord:
    trade_id: str
    strategy: str
    symbol: str
    session_date: date
    status: TradeStatus
    quantity: int
    filled_quantity: int
    planned_entry: Decimal
    initial_stop: Decimal
    target_price: Decimal
    average_entry_price: Decimal | None
    average_exit_price: Decimal | None
    realized_pnl: Decimal
    entry_order_id: str | None
    protective_stop_order_id: str | None
    exit_order_id: str | None
    close_reason: str | None
    protected_quantity: int
    last_broker_observation_at: datetime | None
    required_recovery_action: EntryRecoveryAction
    last_completed_recovery_step: str | None
    recovery_ref_id: str | None
    recovery_attempt_count: int
    recovery_failure_reason: str | None
    operator_ack_required: bool
    last_entry_observation_key: str | None
    last_protection_reconciliation_id: str | None
    protection_confirmed_at: datetime | None
    stop_original_position_quantity: int | None
    stop_requested_quantity: int | None
    stop_executed_quantity: int
    remaining_broker_position_quantity: int | None
    last_stop_state: str | None
    average_stop_fill_price: Decimal | None
    required_stop_recovery_action: StopRecoveryAction
    last_completed_stop_recovery_step: str | None
    stop_recovery_ref_id: str | None
    stop_recovery_attempt_count: int
    stop_recovery_failure_reason: str | None
    stop_operator_ack_required: bool
    last_stop_observation_key: str | None
    last_stop_reconciliation_id: str | None
    exit_requested_quantity: int | None
    exit_close_reason: str | None
    exit_intent_type: ExitIntentType | None
    exit_intended_price: Decimal | None
    exit_recovery_ref_id: str | None
    exit_linked_at: datetime | None
    last_exit_observation_at: datetime | None
    last_exit_reconciliation_id: str | None
    exit_attempt_base_quantity: int
    exit_attempt_base_proceeds: Decimal
    exit_attempt_base_fees: Decimal
    exit_executed_quantity: int
    exit_order_fees: Decimal
    cumulative_exited_quantity: int
    exit_remaining_position_quantity: int | None
    cumulative_average_exit_price: Decimal | None
    cumulative_exit_proceeds: Decimal
    cumulative_exit_fees: Decimal
    cumulative_realized_pnl: Decimal
    required_exit_recovery_action: ExitRecoveryAction
    last_completed_exit_recovery_step: str | None
    exit_recovery_attempt_count: int
    exit_recovery_failure_reason: str | None
    exit_operator_ack_required: bool
    last_exit_observation_key: str | None
    created_at: datetime
    updated_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    version: int

    @property
    def remaining_entry_quantity(self) -> int:
        return max(
            0,
            self.quantity - self.filled_quantity,
        )

    @property
    def unprotected_quantity(self) -> int:
        current_quantity = (
            self.exit_remaining_position_quantity
            if self.exit_remaining_position_quantity
            is not None
            else (
                self.remaining_broker_position_quantity
                if self.remaining_broker_position_quantity
                is not None
                else self.filled_quantity
            )
        )
        return max(
            0,
            current_quantity
            - self.protected_quantity,
        )


@dataclass(frozen=True)
class ReconciliationIssue:
    severity: str
    code: str
    message: str
    symbol: str = ""
    trade_id: str = ""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_decimal(value: Decimal | None) -> str | None:
    if value is None:
        return None

    return format(value, "f")


def parse_decimal(value: Any) -> Decimal:
    if value in {None, ""}:
        return Decimal("0")

    return Decimal(str(value))


def parse_optional_decimal(
    value: Any,
) -> Decimal | None:
    if value in {None, ""}:
        return None

    return Decimal(str(value))


def parse_datetime(
    value: str | None,
) -> datetime | None:
    if not value:
        return None

    return datetime.fromisoformat(value)


class TradeStateStore:
    """
    Persistent local trade state and audit ledger.

    This module contains no broker API calls and cannot
    review, place, replace, cancel, or submit an order.
    """

    def __init__(
        self,
        db_path: str | Path,
    ):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path
        )
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
                CREATE TABLE IF NOT EXISTS trades (
                    trade_id TEXT PRIMARY KEY,
                    strategy TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    session_date TEXT NOT NULL,
                    status TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    filled_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    planned_entry TEXT NOT NULL,
                    initial_stop TEXT NOT NULL,
                    target_price TEXT NOT NULL,
                    average_entry_price TEXT,
                    average_exit_price TEXT,
                    realized_pnl TEXT NOT NULL
                        DEFAULT '0',
                    entry_order_id TEXT,
                    protective_stop_order_id TEXT,
                    exit_order_id TEXT,
                    close_reason TEXT,
                    protected_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    last_broker_observation_at TEXT,
                    required_recovery_action TEXT NOT NULL
                        DEFAULT 'none',
                    last_completed_recovery_step TEXT,
                    recovery_ref_id TEXT,
                    recovery_attempt_count INTEGER NOT NULL
                        DEFAULT 0,
                    recovery_failure_reason TEXT,
                    operator_ack_required INTEGER NOT NULL
                        DEFAULT 0,
                    last_entry_observation_key TEXT,
                    last_protection_reconciliation_id TEXT,
                    protection_confirmed_at TEXT,
                    stop_original_position_quantity INTEGER,
                    stop_requested_quantity INTEGER,
                    stop_executed_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    remaining_broker_position_quantity INTEGER,
                    last_stop_state TEXT,
                    average_stop_fill_price TEXT,
                    required_stop_recovery_action TEXT NOT NULL
                        DEFAULT 'none',
                    last_completed_stop_recovery_step TEXT,
                    stop_recovery_ref_id TEXT,
                    stop_recovery_attempt_count INTEGER NOT NULL
                        DEFAULT 0,
                    stop_recovery_failure_reason TEXT,
                    stop_operator_ack_required INTEGER NOT NULL
                        DEFAULT 0,
                    last_stop_observation_key TEXT,
                    last_stop_reconciliation_id TEXT,
                    exit_requested_quantity INTEGER,
                    exit_close_reason TEXT,
                    exit_intent_type TEXT,
                    exit_intended_price TEXT,
                    exit_recovery_ref_id TEXT,
                    exit_linked_at TEXT,
                    last_exit_observation_at TEXT,
                    last_exit_reconciliation_id TEXT,
                    exit_attempt_base_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    exit_attempt_base_proceeds TEXT NOT NULL
                        DEFAULT '0',
                    exit_attempt_base_fees TEXT NOT NULL
                        DEFAULT '0',
                    exit_executed_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    exit_order_fees TEXT NOT NULL
                        DEFAULT '0',
                    cumulative_exited_quantity INTEGER NOT NULL
                        DEFAULT 0,
                    exit_remaining_position_quantity INTEGER,
                    cumulative_average_exit_price TEXT,
                    cumulative_exit_proceeds TEXT NOT NULL
                        DEFAULT '0',
                    cumulative_exit_fees TEXT NOT NULL
                        DEFAULT '0',
                    cumulative_realized_pnl TEXT NOT NULL
                        DEFAULT '0',
                    required_exit_recovery_action TEXT NOT NULL
                        DEFAULT 'none',
                    last_completed_exit_recovery_step TEXT,
                    exit_recovery_attempt_count INTEGER NOT NULL
                        DEFAULT 0,
                    exit_recovery_failure_reason TEXT,
                    exit_operator_ack_required INTEGER NOT NULL
                        DEFAULT 0,
                    last_exit_observation_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    opened_at TEXT,
                    closed_at TEXT,
                    version INTEGER NOT NULL DEFAULT 1,

                    UNIQUE (
                        strategy,
                        symbol,
                        session_date
                    )
                );

                CREATE INDEX IF NOT EXISTS
                    idx_trades_status
                ON trades(status);

                CREATE INDEX IF NOT EXISTS
                    idx_trades_session_date
                ON trades(session_date);

                CREATE TABLE IF NOT EXISTS
                    trade_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        trade_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TABLE IF NOT EXISTS
                    order_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        trade_id TEXT NOT NULL,
                        broker_order_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        order_state TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity TEXT,
                        filled_quantity TEXT,
                        limit_price TEXT,
                        stop_price TEXT,
                        observation_key TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TABLE IF NOT EXISTS
                    entry_recovery_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        trade_id TEXT NOT NULL,
                        observation_key TEXT NOT NULL,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        required_action TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        UNIQUE(trade_id, observation_key),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TABLE IF NOT EXISTS
                    protective_stop_recovery_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        trade_id TEXT NOT NULL,
                        observation_key TEXT NOT NULL,
                        broker_order_id TEXT,
                        broker_state TEXT,
                        requested_quantity INTEGER,
                        executed_quantity INTEGER NOT NULL,
                        remaining_quantity INTEGER,
                        average_fill_price TEXT,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        required_action TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        UNIQUE(trade_id, observation_key),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TABLE IF NOT EXISTS
                    exit_recovery_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        trade_id TEXT NOT NULL,
                        observation_key TEXT NOT NULL,
                        event_source TEXT NOT NULL,
                        broker_order_id TEXT,
                        broker_state TEXT,
                        requested_quantity INTEGER,
                        executed_quantity INTEGER NOT NULL,
                        executed_delta INTEGER NOT NULL,
                        remaining_quantity INTEGER,
                        cumulative_average_price TEXT,
                        cumulative_proceeds TEXT NOT NULL,
                        cumulative_fees TEXT NOT NULL,
                        cumulative_realized_pnl TEXT NOT NULL,
                        protection_result TEXT,
                        reconciliation_id TEXT,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        required_action TEXT NOT NULL,
                        failure_code TEXT,
                        failure_reason TEXT,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        UNIQUE(trade_id, observation_key),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TABLE IF NOT EXISTS
                    broker_action_authorizations (
                        authorization_id TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        trade_id TEXT NOT NULL,
                        purpose TEXT NOT NULL,
                        plan_digest TEXT NOT NULL,
                        source_trade_version INTEGER NOT NULL,
                        reconciliation_id TEXT NOT NULL,
                        snapshot_digest TEXT NOT NULL,
                        account_fingerprint TEXT NOT NULL,
                        status TEXT NOT NULL,
                        authorized_at TEXT NOT NULL,
                        expires_at TEXT NOT NULL,
                        claim_id TEXT,
                        claimed_at TEXT,
                        invalidated_at TEXT,
                        invalidation_reason TEXT,
                        version INTEGER NOT NULL DEFAULT 1,
                        UNIQUE (
                            trade_id,
                            source_trade_version,
                            reconciliation_id,
                            plan_digest
                        ),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_broker_action_authorizations_trade_status
                ON broker_action_authorizations(
                    trade_id, status
                );

                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_one_active_broker_action_authorization
                ON broker_action_authorizations(trade_id)
                WHERE status IN ('authorized', 'claimed');

                CREATE TABLE IF NOT EXISTS
                    broker_action_authorization_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        authorization_id TEXT NOT NULL,
                        event_type TEXT NOT NULL,
                        old_status TEXT,
                        new_status TEXT NOT NULL,
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(authorization_id)
                            REFERENCES broker_action_authorizations(
                                authorization_id
                            )
                    );
                CREATE TABLE IF NOT EXISTS
                    entry_halts (
                        halt_key TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        trade_id TEXT,
                        active INTEGER NOT NULL,
                        reason TEXT NOT NULL,
                        operator_ack_required INTEGER
                            NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        cleared_at TEXT,
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE INDEX IF NOT EXISTS
                    idx_entry_halts_strategy_active
                ON entry_halts(strategy, active);

                CREATE TABLE IF NOT EXISTS
                    broker_reconciliation_runs (
                        reconciliation_id TEXT PRIMARY KEY,
                        strategy TEXT NOT NULL,
                        account_fingerprint TEXT NOT NULL,
                        started_at TEXT NOT NULL,
                        positions_read_at TEXT NOT NULL,
                        orders_read_at TEXT NOT NULL,
                        completed_at TEXT NOT NULL,
                        positions_complete INTEGER NOT NULL,
                        orders_complete INTEGER NOT NULL,
                        snapshot_age_seconds TEXT,
                        maximum_age_seconds TEXT NOT NULL,
                        snapshot_digest TEXT NOT NULL,
                        snapshot_valid INTEGER NOT NULL,
                        safety_confirmed INTEGER NOT NULL,
                        result_code TEXT NOT NULL,
                        failure_reason TEXT,
                        occurred_at TEXT NOT NULL
                    );

                CREATE TABLE IF NOT EXISTS
                    protection_reconciliation_events (
                        event_id INTEGER PRIMARY KEY
                            AUTOINCREMENT,
                        reconciliation_id TEXT NOT NULL,
                        trade_id TEXT NOT NULL,
                        linked_stop_order_id TEXT,
                        position_quantity TEXT,
                        stop_quantity TEXT,
                        old_status TEXT NOT NULL,
                        new_status TEXT NOT NULL,
                        confirmed INTEGER NOT NULL,
                        result_code TEXT NOT NULL,
                        failure_reason TEXT,
                        occurred_at TEXT NOT NULL,
                        UNIQUE(reconciliation_id, trade_id),
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

                CREATE TRIGGER IF NOT EXISTS
                    prevent_broker_reconciliation_run_update
                BEFORE UPDATE ON broker_reconciliation_runs
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_reconciliation_runs are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_broker_reconciliation_run_delete
                BEFORE DELETE ON broker_reconciliation_runs
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_reconciliation_runs are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_protection_reconciliation_event_update
                BEFORE UPDATE ON protection_reconciliation_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'protection_reconciliation_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_protection_reconciliation_event_delete
                BEFORE DELETE ON protection_reconciliation_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'protection_reconciliation_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_exit_recovery_event_update
                BEFORE UPDATE ON exit_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'exit_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_exit_recovery_event_delete
                BEFORE DELETE ON exit_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'exit_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_stop_recovery_event_update
                BEFORE UPDATE ON protective_stop_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'protective_stop_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_stop_recovery_event_delete
                BEFORE DELETE ON protective_stop_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'protective_stop_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_entry_recovery_event_update
                BEFORE UPDATE ON entry_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'entry_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_entry_recovery_event_delete
                BEFORE DELETE ON entry_recovery_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'entry_recovery_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_broker_action_authorization_event_update
                BEFORE UPDATE ON broker_action_authorization_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_action_authorization_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_broker_action_authorization_event_delete
                BEFORE DELETE ON broker_action_authorization_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'broker_action_authorization_events are append-only'
                    );
                END;
                CREATE TRIGGER IF NOT EXISTS
                    prevent_trade_event_update
                BEFORE UPDATE ON trade_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'trade_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_trade_event_delete
                BEFORE DELETE ON trade_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'trade_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_order_event_update
                BEFORE UPDATE ON order_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'order_events are append-only'
                    );
                END;

                CREATE TRIGGER IF NOT EXISTS
                    prevent_order_event_delete
                BEFORE DELETE ON order_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'order_events are append-only'
                    );
                END;
                """
            )

            trade_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(trades)"
                )
            }

            migrations = {
                "protected_quantity":
                    "INTEGER NOT NULL DEFAULT 0",
                "last_broker_observation_at":
                    "TEXT",
                "required_recovery_action":
                    "TEXT NOT NULL DEFAULT 'none'",
                "last_completed_recovery_step":
                    "TEXT",
                "recovery_ref_id": "TEXT",
                "recovery_attempt_count":
                    "INTEGER NOT NULL DEFAULT 0",
                "recovery_failure_reason": "TEXT",
                "operator_ack_required":
                    "INTEGER NOT NULL DEFAULT 0",
                "last_entry_observation_key":
                    "TEXT",
                "last_protection_reconciliation_id":
                    "TEXT",
                "protection_confirmed_at": "TEXT",
                "stop_original_position_quantity":
                    "INTEGER",
                "stop_requested_quantity": "INTEGER",
                "stop_executed_quantity":
                    "INTEGER NOT NULL DEFAULT 0",
                "remaining_broker_position_quantity":
                    "INTEGER",
                "last_stop_state": "TEXT",
                "average_stop_fill_price": "TEXT",
                "required_stop_recovery_action":
                    "TEXT NOT NULL DEFAULT 'none'",
                "last_completed_stop_recovery_step":
                    "TEXT",
                "stop_recovery_ref_id": "TEXT",
                "stop_recovery_attempt_count":
                    "INTEGER NOT NULL DEFAULT 0",
                "stop_recovery_failure_reason": "TEXT",
                "stop_operator_ack_required":
                    "INTEGER NOT NULL DEFAULT 0",
                "last_stop_observation_key": "TEXT",
                "last_stop_reconciliation_id": "TEXT",
                "exit_requested_quantity": "INTEGER",
                "exit_close_reason": "TEXT",
                "exit_intent_type": "TEXT",
                "exit_intended_price": "TEXT",
                "exit_recovery_ref_id": "TEXT",
                "exit_linked_at": "TEXT",
                "last_exit_observation_at": "TEXT",
                "last_exit_reconciliation_id": "TEXT",
                "exit_attempt_base_quantity":
                    "INTEGER NOT NULL DEFAULT 0",
                "exit_attempt_base_proceeds":
                    "TEXT NOT NULL DEFAULT '0'",
                "exit_attempt_base_fees":
                    "TEXT NOT NULL DEFAULT '0'",
                "exit_executed_quantity":
                    "INTEGER NOT NULL DEFAULT 0",
                "exit_order_fees":
                    "TEXT NOT NULL DEFAULT '0'",
                "cumulative_exited_quantity":
                    "INTEGER NOT NULL DEFAULT 0",
                "exit_remaining_position_quantity":
                    "INTEGER",
                "cumulative_average_exit_price": "TEXT",
                "cumulative_exit_proceeds":
                    "TEXT NOT NULL DEFAULT '0'",
                "cumulative_exit_fees":
                    "TEXT NOT NULL DEFAULT '0'",
                "cumulative_realized_pnl":
                    "TEXT NOT NULL DEFAULT '0'",
                "required_exit_recovery_action":
                    "TEXT NOT NULL DEFAULT 'none'",
                "last_completed_exit_recovery_step":
                    "TEXT",
                "exit_recovery_attempt_count":
                    "INTEGER NOT NULL DEFAULT 0",
                "exit_recovery_failure_reason": "TEXT",
                "exit_operator_ack_required":
                    "INTEGER NOT NULL DEFAULT 0",
                "last_exit_observation_key": "TEXT",
            }

            for column, definition in (
                migrations.items()
            ):
                if column not in trade_columns:
                    connection.execute(
                        "ALTER TABLE trades "
                        f"ADD COLUMN {column} "
                        f"{definition}"
                    )

            legacy_timestamp = utc_now().isoformat()
            legacy_exit_rows = connection.execute(
                """
                SELECT trade_id, strategy
                FROM trades
                WHERE status = ?
                  AND exit_close_reason IS NULL
                """,
                (TradeStatus.EXIT_PENDING.value,),
            ).fetchall()

            for legacy_row in legacy_exit_rows:
                connection.execute(
                    """
                    UPDATE trades
                    SET status = ?,
                        required_exit_recovery_action = ?,
                        exit_recovery_failure_reason = ?,
                        exit_operator_ack_required = 1
                    WHERE trade_id = ?
                    """,
                    (
                        TradeStatus.EXIT_RECOVERY_REQUIRED.value,
                        ExitRecoveryAction.OPERATOR_INTERVENTION.value,
                        "Legacy exit intent is incomplete; fresh broker reconciliation and operator intervention are required.",
                        legacy_row["trade_id"],
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO entry_halts (
                        halt_key, strategy, trade_id, active,
                        reason, operator_ack_required,
                        created_at, updated_at, cleared_at
                    )
                    VALUES (?, ?, ?, 1, ?, 1, ?, ?, NULL)
                    ON CONFLICT(halt_key) DO UPDATE SET
                        active = 1,
                        operator_ack_required = 1,
                        updated_at = excluded.updated_at,
                        cleared_at = NULL
                    """,
                    (
                        f"trade:{legacy_row['trade_id']}",
                        legacy_row["strategy"],
                        legacy_row["trade_id"],
                        "Legacy EXIT_PENDING state lacks durable exit intent.",
                        legacy_timestamp,
                        legacy_timestamp,
                    ),
                )

            order_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(order_events)"
                )
            }

            if "observation_key" not in order_columns:
                connection.execute(
                    "ALTER TABLE order_events "
                    "ADD COLUMN observation_key TEXT"
                )

            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_order_events_observation_key
                ON order_events(
                    trade_id,
                    role,
                    observation_key
                )
                WHERE observation_key IS NOT NULL
                """
            )

    @staticmethod
    def _row_to_broker_action_authorization(
        row: sqlite3.Row,
    ) -> BrokerActionAuthorizationRecord:
        return BrokerActionAuthorizationRecord(
            authorization_id=row["authorization_id"],
            strategy=row["strategy"],
            trade_id=row["trade_id"],
            purpose=row["purpose"],
            plan_digest=row["plan_digest"],
            source_trade_version=int(
                row["source_trade_version"]
            ),
            reconciliation_id=row["reconciliation_id"],
            snapshot_digest=row["snapshot_digest"],
            account_fingerprint=row["account_fingerprint"],
            status=BrokerActionAuthorizationStatus(
                row["status"]
            ),
            authorized_at=parse_datetime(
                row["authorized_at"]
            ),
            expires_at=parse_datetime(row["expires_at"]),
            claim_id=row["claim_id"],
            claimed_at=parse_datetime(row["claimed_at"]),
            invalidated_at=parse_datetime(
                row["invalidated_at"]
            ),
            invalidation_reason=row["invalidation_reason"],
            version=int(row["version"]),
        )

    @staticmethod
    def _row_to_trade(
        row: sqlite3.Row,
    ) -> TradeRecord:
        return TradeRecord(
            trade_id=row["trade_id"],
            strategy=row["strategy"],
            symbol=row["symbol"],
            session_date=date.fromisoformat(
                row["session_date"]
            ),
            status=TradeStatus(row["status"]),
            quantity=int(row["quantity"]),
            filled_quantity=int(
                row["filled_quantity"]
            ),
            planned_entry=parse_decimal(
                row["planned_entry"]
            ),
            initial_stop=parse_decimal(
                row["initial_stop"]
            ),
            target_price=parse_decimal(
                row["target_price"]
            ),
            average_entry_price=(
                parse_optional_decimal(
                    row["average_entry_price"]
                )
            ),
            average_exit_price=(
                parse_optional_decimal(
                    row["average_exit_price"]
                )
            ),
            realized_pnl=parse_decimal(
                row["realized_pnl"]
            ),
            entry_order_id=row["entry_order_id"],
            protective_stop_order_id=(
                row["protective_stop_order_id"]
            ),
            exit_order_id=row["exit_order_id"],
            close_reason=row["close_reason"],
            protected_quantity=int(
                row["protected_quantity"]
            ),
            last_broker_observation_at=(
                parse_datetime(
                    row[
                        "last_broker_observation_at"
                    ]
                )
            ),
            required_recovery_action=(
                EntryRecoveryAction(
                    row[
                        "required_recovery_action"
                    ]
                )
            ),
            last_completed_recovery_step=(
                row[
                    "last_completed_recovery_step"
                ]
            ),
            recovery_ref_id=row["recovery_ref_id"],
            recovery_attempt_count=int(
                row["recovery_attempt_count"]
            ),
            recovery_failure_reason=(
                row["recovery_failure_reason"]
            ),
            operator_ack_required=bool(
                row["operator_ack_required"]
            ),
            last_entry_observation_key=(
                row["last_entry_observation_key"]
            ),
            last_protection_reconciliation_id=(
                row["last_protection_reconciliation_id"]
            ),
            protection_confirmed_at=parse_datetime(
                row["protection_confirmed_at"]
            ),
            stop_original_position_quantity=(
                int(row["stop_original_position_quantity"])
                if row["stop_original_position_quantity"]
                is not None
                else None
            ),
            stop_requested_quantity=(
                int(row["stop_requested_quantity"])
                if row["stop_requested_quantity"]
                is not None
                else None
            ),
            stop_executed_quantity=int(
                row["stop_executed_quantity"]
            ),
            remaining_broker_position_quantity=(
                int(row["remaining_broker_position_quantity"])
                if row["remaining_broker_position_quantity"]
                is not None
                else None
            ),
            last_stop_state=row["last_stop_state"],
            average_stop_fill_price=(
                parse_optional_decimal(
                    row["average_stop_fill_price"]
                )
            ),
            required_stop_recovery_action=(
                StopRecoveryAction(
                    row["required_stop_recovery_action"]
                )
            ),
            last_completed_stop_recovery_step=(
                row["last_completed_stop_recovery_step"]
            ),
            stop_recovery_ref_id=(
                row["stop_recovery_ref_id"]
            ),
            stop_recovery_attempt_count=int(
                row["stop_recovery_attempt_count"]
            ),
            stop_recovery_failure_reason=(
                row["stop_recovery_failure_reason"]
            ),
            stop_operator_ack_required=bool(
                row["stop_operator_ack_required"]
            ),
            last_stop_observation_key=(
                row["last_stop_observation_key"]
            ),
            last_stop_reconciliation_id=(
                row["last_stop_reconciliation_id"]
            ),
            exit_requested_quantity=(
                int(row["exit_requested_quantity"])
                if row["exit_requested_quantity"] is not None
                else None
            ),
            exit_close_reason=row["exit_close_reason"],
            exit_intent_type=(
                ExitIntentType(row["exit_intent_type"])
                if row["exit_intent_type"]
                else None
            ),
            exit_intended_price=(
                parse_optional_decimal(
                    row["exit_intended_price"]
                )
            ),
            exit_recovery_ref_id=(
                row["exit_recovery_ref_id"]
            ),
            exit_linked_at=parse_datetime(
                row["exit_linked_at"]
            ),
            last_exit_observation_at=parse_datetime(
                row["last_exit_observation_at"]
            ),
            last_exit_reconciliation_id=(
                row["last_exit_reconciliation_id"]
            ),
            exit_attempt_base_quantity=int(
                row["exit_attempt_base_quantity"]
            ),
            exit_attempt_base_proceeds=parse_decimal(
                row["exit_attempt_base_proceeds"]
            ),
            exit_attempt_base_fees=parse_decimal(
                row["exit_attempt_base_fees"]
            ),
            exit_executed_quantity=int(
                row["exit_executed_quantity"]
            ),
            exit_order_fees=parse_decimal(
                row["exit_order_fees"]
            ),
            cumulative_exited_quantity=int(
                row["cumulative_exited_quantity"]
            ),
            exit_remaining_position_quantity=(
                int(row["exit_remaining_position_quantity"])
                if row["exit_remaining_position_quantity"] is not None
                else None
            ),
            cumulative_average_exit_price=(
                parse_optional_decimal(
                    row["cumulative_average_exit_price"]
                )
            ),
            cumulative_exit_proceeds=parse_decimal(
                row["cumulative_exit_proceeds"]
            ),
            cumulative_exit_fees=parse_decimal(
                row["cumulative_exit_fees"]
            ),
            cumulative_realized_pnl=parse_decimal(
                row["cumulative_realized_pnl"]
            ),
            required_exit_recovery_action=(
                ExitRecoveryAction(
                    row["required_exit_recovery_action"]
                )
            ),
            last_completed_exit_recovery_step=(
                row["last_completed_exit_recovery_step"]
            ),
            exit_recovery_attempt_count=int(
                row["exit_recovery_attempt_count"]
            ),
            exit_recovery_failure_reason=(
                row["exit_recovery_failure_reason"]
            ),
            exit_operator_ack_required=bool(
                row["exit_operator_ack_required"]
            ),
            last_exit_observation_key=(
                row["last_exit_observation_key"]
            ),
            created_at=datetime.fromisoformat(
                row["created_at"]
            ),
            updated_at=datetime.fromisoformat(
                row["updated_at"]
            ),
            opened_at=parse_datetime(
                row["opened_at"]
            ),
            closed_at=parse_datetime(
                row["closed_at"]
            ),
            version=int(row["version"]),
        )

    @staticmethod
    def _validate_plan(
        *,
        quantity: int,
        planned_entry: Decimal,
        initial_stop: Decimal,
        target_price: Decimal,
    ) -> None:
        if quantity <= 0:
            raise TradeStateError(
                "Trade quantity must be positive."
            )

        if planned_entry <= 0:
            raise TradeStateError(
                "Planned entry must be positive."
            )

        if initial_stop <= 0:
            raise TradeStateError(
                "Initial stop must be positive."
            )

        if target_price <= 0:
            raise TradeStateError(
                "Target price must be positive."
            )

        if initial_stop >= planned_entry:
            raise TradeStateError(
                "Initial stop must be below "
                "planned entry for a long trade."
            )

        if target_price <= planned_entry:
            raise TradeStateError(
                "Target price must be above "
                "planned entry for a long trade."
            )

    def _insert_trade_event(
        self,
        connection: sqlite3.Connection,
        *,
        trade_id: str,
        event_type: str,
        old_status: TradeStatus | None,
        new_status: TradeStatus | None,
        occurred_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO trade_events (
                trade_id,
                event_type,
                old_status,
                new_status,
                occurred_at,
                details_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                trade_id,
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


    def _activate_entry_halt(
        self,
        connection: sqlite3.Connection,
        *,
        halt_key: str,
        strategy: str,
        trade_id: str | None,
        reason: str,
        operator_ack_required: bool,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO entry_halts (
                halt_key,
                strategy,
                trade_id,
                active,
                reason,
                operator_ack_required,
                created_at,
                updated_at,
                cleared_at
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
                halt_key,
                strategy,
                trade_id,
                reason,
                int(operator_ack_required),
                occurred_at.isoformat(),
                occurred_at.isoformat(),
            ),
        )

    def _clear_entry_halt(
        self,
        connection: sqlite3.Connection,
        *,
        halt_key: str,
        occurred_at: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE entry_halts
            SET active = 0,
                updated_at = ?,
                cleared_at = ?
            WHERE halt_key = ?
              AND active = 1
            """,
            (
                occurred_at.isoformat(),
                occurred_at.isoformat(),
                halt_key,
            ),
        )

    def get_entry_halt(
        self,
        strategy: str,
    ) -> EntryHaltState:
        strategy = strategy.strip()

        if not strategy:
            raise TradeStateError(
                "Strategy name is required."
            )

        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT reason, operator_ack_required
                FROM entry_halts
                WHERE strategy = ?
                  AND active = 1
                ORDER BY created_at, halt_key
                """,
                (strategy,),
            ).fetchall()

        return EntryHaltState(
            strategy=strategy,
            active=bool(rows),
            reasons=tuple(
                row["reason"]
                for row in rows
            ),
            operator_ack_required=any(
                bool(row["operator_ack_required"])
                for row in rows
            ),
        )

    def is_entry_halted(
        self,
        strategy: str,
    ) -> bool:
        return self.get_entry_halt(
            strategy
        ).active

    def begin_startup_reconciliation(
        self,
        strategy: str,
        *,
        occurred_at: datetime | None = None,
    ) -> EntryHaltState:
        strategy = strategy.strip()
        timestamp = occurred_at or utc_now()

        if not strategy:
            raise TradeStateError(
                "Strategy name is required."
            )

        with self._connect() as connection:
            self._activate_entry_halt(
                connection,
                halt_key=f"startup:{strategy}",
                strategy=strategy,
                trade_id=None,
                reason=(
                    "Startup reconciliation is incomplete."
                ),
                operator_ack_required=False,
                occurred_at=timestamp,
            )
            rows = connection.execute(
                """
                SELECT *
                FROM trades
                WHERE strategy = ?
                  AND status IN (?, ?, ?, ?, ?, ?)
                ORDER BY created_at, trade_id
                """,
                (
                    strategy,
                    TradeStatus.STOP_LINKED.value,
                    TradeStatus.OPEN_PROTECTED.value,
                    TradeStatus.STOP_RECOVERY_REQUIRED.value,
                    TradeStatus.EXIT_PENDING.value,
                    TradeStatus.EXIT_PARTIALLY_FILLED.value,
                    TradeStatus.EXIT_RECOVERY_REQUIRED.value,
                ),
            ).fetchall()

        for row in rows:
            trade = self._row_to_trade(row)

            with self._connect() as connection:
                self._activate_entry_halt(
                    connection,
                    halt_key=f"trade:{trade.trade_id}",
                    strategy=strategy,
                    trade_id=trade.trade_id,
                    reason=(
                        "Fresh complete broker proof is "
                        "required for the linked protective "
                        "stop after startup."
                    ),
                    operator_ack_required=False,
                    occurred_at=timestamp,
                )

            if trade.status == TradeStatus.OPEN_PROTECTED:
                self._transition(
                    trade.trade_id,
                    new_status=TradeStatus.STOP_LINKED,
                    event_type=(
                        "protection_proof_expired_at_startup"
                    ),
                    updates={
                        "protected_quantity": 0,
                        "required_recovery_action":
                            EntryRecoveryAction
                            .RECONCILE_BROKER_STATE.value,
                        "last_protection_reconciliation_id":
                            None,
                        "protection_confirmed_at": None,
                    },
                    details={
                        "reason": (
                            "Fresh broker proof is required "
                            "for every startup cycle."
                        )
                    },
                    occurred_at=timestamp,
                )

        return self.get_entry_halt(strategy)

    def persist_entry_observation(
        self,
        trade_id: str,
        *,
        status: TradeStatus,
        filled_quantity: int,
        average_entry_price: Decimal | None,
        required_action: EntryRecoveryAction,
        last_completed_step: EntryRecoveryStep,
        observation_key: str,
        observed_at: datetime,
        failure_reason: str | None = None,
        operator_ack_required: bool = False,
        recovery_ref_id: str | None = None,
    ) -> tuple[TradeRecord, bool]:
        if observed_at.tzinfo is None:
            raise TradeStateError(
                "Broker observation timestamp must "
                "include a timezone."
            )

        if not observation_key.strip():
            raise TradeStateError(
                "Entry observation key is required."
            )

        recovery_statuses = {
            TradeStatus.ENTRY_PENDING,
            TradeStatus.ENTRY_PARTIALLY_FILLED,
            TradeStatus.PROTECTION_PENDING,
            TradeStatus.RECOVERY_REQUIRED,
        }

        if status not in recovery_statuses:
            raise TradeStateError(
                "Unsupported entry recovery status."
            )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ).fetchone()

            if row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )

            current = self._row_to_trade(row)

            observation_seen = connection.execute(
                """
                SELECT 1
                FROM entry_recovery_events
                WHERE trade_id = ?
                  AND observation_key = ?
                LIMIT 1
                """,
                (
                    trade_id,
                    observation_key,
                ),
            ).fetchone()

            if (
                current.last_entry_observation_key
                == observation_key
                or observation_seen is not None
            ):
                return current, False

            if current.status not in recovery_statuses:
                raise InvalidTransitionError(
                    "Entry recovery observations can "
                    "only update an entry lifecycle "
                    "trade."
                )

            effective_status = status
            effective_filled = filled_quantity
            effective_average = average_entry_price
            effective_action = required_action
            effective_step = last_completed_step
            effective_failure = failure_reason
            effective_operator_ack = (
                operator_ack_required
            )

            if (
                filled_quantity < 0
                or filled_quantity > current.quantity
                or filled_quantity
                < current.filled_quantity
            ):
                effective_status = (
                    TradeStatus.RECOVERY_REQUIRED
                )
                effective_filled = (
                    current.filled_quantity
                )
                effective_average = (
                    current.average_entry_price
                )
                effective_action = (
                    EntryRecoveryAction
                    .RECONCILE_BROKER_STATE
                )
                effective_step = (
                    EntryRecoveryStep
                    .AMBIGUOUS_STATE_RECORDED
                )
                effective_failure = (
                    "Broker cumulative quantity is "
                    "inconsistent with local entry "
                    "state."
                )
                effective_operator_ack = True

            elif (
                filled_quantity > 0
                and (
                    average_entry_price is None
                    or average_entry_price <= 0
                )
            ):
                effective_status = (
                    TradeStatus.RECOVERY_REQUIRED
                )
                effective_action = (
                    EntryRecoveryAction
                    .RECONCILE_BROKER_STATE
                )
                effective_step = (
                    EntryRecoveryStep
                    .AMBIGUOUS_STATE_RECORDED
                )
                effective_failure = (
                    "Broker reports a filled entry "
                    "quantity without a valid average "
                    "fill price."
                )
                effective_operator_ack = True

            if (
                current.last_broker_observation_at
                is not None
                and observed_at
                < current.last_broker_observation_at
            ):
                effective_status = (
                    TradeStatus.RECOVERY_REQUIRED
                )
                effective_filled = max(
                    current.filled_quantity,
                    effective_filled,
                )
                effective_average = (
                    current.average_entry_price
                    or effective_average
                )
                effective_action = (
                    EntryRecoveryAction
                    .RECONCILE_BROKER_STATE
                )
                effective_step = (
                    EntryRecoveryStep
                    .AMBIGUOUS_STATE_RECORDED
                )
                effective_failure = (
                    "A stale broker entry observation "
                    "was received."
                )
                effective_operator_ack = True

            if (
                effective_status != current.status
                and effective_status
                not in ALLOWED_TRANSITIONS[
                    current.status
                ]
            ):
                raise InvalidTransitionError(
                    f"Cannot transition "
                    f"{current.status.value} to "
                    f"{effective_status.value}."
                )

            reference = (
                recovery_ref_id
                or (
                    "entry-recovery-"
                    f"{observation_key[:24]}"
                )
            )
            timestamp = utc_now()
            opened_at = current.opened_at

            if (
                effective_filled > 0
                and opened_at is None
            ):
                opened_at = observed_at

            cursor = connection.execute(
                """
                UPDATE trades
                SET status = ?,
                    filled_quantity = ?,
                    average_entry_price = ?,
                    last_broker_observation_at = ?,
                    required_recovery_action = ?,
                    last_completed_recovery_step = ?,
                    recovery_ref_id = ?,
                    recovery_failure_reason = ?,
                    operator_ack_required = ?,
                    last_entry_observation_key = ?,
                    opened_at = ?,
                    updated_at = ?,
                    version = version + 1
                WHERE trade_id = ?
                  AND version = ?
                """,
                (
                    effective_status.value,
                    effective_filled,
                    serialize_decimal(
                        effective_average
                    ),
                    observed_at.isoformat(),
                    effective_action.value,
                    effective_step.value,
                    reference,
                    effective_failure,
                    int(effective_operator_ack),
                    observation_key,
                    (
                        opened_at.isoformat()
                        if opened_at
                        else None
                    ),
                    timestamp.isoformat(),
                    trade_id,
                    current.version,
                ),
            )

            if cursor.rowcount != 1:
                raise TradeStateError(
                    "Trade state changed concurrently; "
                    "entry recovery failed closed."
                )

            self._insert_trade_event(
                connection,
                trade_id=trade_id,
                event_type="entry_recovery_observed",
                old_status=current.status,
                new_status=effective_status,
                occurred_at=timestamp,
                details={
                    "filled_quantity":
                        effective_filled,
                    "required_action":
                        effective_action.value,
                    "recovery_ref_id": reference,
                },
            )
            connection.execute(
                """
                INSERT INTO entry_recovery_events (
                    trade_id,
                    observation_key,
                    old_status,
                    new_status,
                    required_action,
                    occurred_at,
                    details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    observation_key,
                    current.status.value,
                    effective_status.value,
                    effective_action.value,
                    timestamp.isoformat(),
                    json.dumps(
                        {
                            "filled_quantity":
                                effective_filled,
                            "requested_quantity":
                                current.quantity,
                            "protected_quantity":
                                current.protected_quantity,
                            "average_entry_price":
                                effective_average,
                            "intended_stop_price":
                                current.initial_stop,
                            "failure_reason":
                                effective_failure,
                            "operator_ack_required":
                                effective_operator_ack,
                            "recovery_ref_id":
                                reference,
                        },
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

            halt_required = (
                effective_filled
                > current.protected_quantity
                or effective_status
                in {
                    TradeStatus
                    .ENTRY_PARTIALLY_FILLED,
                    TradeStatus.PROTECTION_PENDING,
                    TradeStatus.RECOVERY_REQUIRED,
                }
                or effective_action
                not in {
                    EntryRecoveryAction.NONE,
                    EntryRecoveryAction.WAIT,
                }
            )

            if halt_required:
                self._activate_entry_halt(
                    connection,
                    halt_key=f"trade:{trade_id}",
                    strategy=current.strategy,
                    trade_id=trade_id,
                    reason=(
                        effective_failure
                        or (
                            "Entry recovery or broker-"
                            "held protection is pending."
                        )
                    ),
                    operator_ack_required=(
                        effective_operator_ack
                    ),
                    occurred_at=timestamp,
                )

        return self.get_trade(trade_id), True

    def halt_entry_recovery(
        self,
        trade_id: str,
        *,
        reason: str,
        observation_key: str,
        observed_at: datetime | None = None,
    ) -> tuple[TradeRecord, bool]:
        current = self.get_trade(trade_id)

        return self.persist_entry_observation(
            trade_id,
            status=TradeStatus.RECOVERY_REQUIRED,
            filled_quantity=(
                current.filled_quantity
            ),
            average_entry_price=(
                current.average_entry_price
            ),
            required_action=(
                EntryRecoveryAction
                .RECONCILE_BROKER_STATE
            ),
            last_completed_step=(
                EntryRecoveryStep
                .AMBIGUOUS_STATE_RECORDED
            ),
            observation_key=observation_key,
            observed_at=(
                observed_at or utc_now()
            ),
            failure_reason=reason,
            operator_ack_required=True,
        )

    def reserve_entry(
        self,
        *,
        strategy: str,
        symbol: str,
        session_date: date,
        quantity: int,
        planned_entry: Decimal,
        initial_stop: Decimal,
        target_price: Decimal,
        trade_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        self._validate_plan(
            quantity=quantity,
            planned_entry=planned_entry,
            initial_stop=initial_stop,
            target_price=target_price,
        )

        strategy = strategy.strip()
        symbol = symbol.strip().upper()

        if not strategy:
            raise TradeStateError(
                "Strategy name is required."
            )

        if not symbol:
            raise TradeStateError(
                "Symbol is required."
            )

        trade_id = trade_id or str(
            uuid.uuid4()
        )
        timestamp = occurred_at or utc_now()

        try:
            with self._connect() as connection:
                active_halt = connection.execute(
                    """
                    SELECT 1
                    FROM entry_halts
                    WHERE strategy = ?
                      AND active = 1
                    LIMIT 1
                    """,
                    (strategy,),
                ).fetchone()

                if active_halt is not None:
                    raise EntryHaltedError(
                        "New entries are blocked by a "
                        "durable strategy recovery halt."
                    )

                connection.execute(
                    """
                    INSERT INTO trades (
                        trade_id,
                        strategy,
                        symbol,
                        session_date,
                        status,
                        quantity,
                        filled_quantity,
                        planned_entry,
                        initial_stop,
                        target_price,
                        realized_pnl,
                        created_at,
                        updated_at,
                        version
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, 0,
                        ?, ?, ?, '0', ?, ?, 1
                    )
                    """,
                    (
                        trade_id,
                        strategy,
                        symbol,
                        session_date.isoformat(),
                        TradeStatus.RESERVED.value,
                        quantity,
                        serialize_decimal(
                            planned_entry
                        ),
                        serialize_decimal(
                            initial_stop
                        ),
                        serialize_decimal(
                            target_price
                        ),
                        timestamp.isoformat(),
                        timestamp.isoformat(),
                    ),
                )

                self._insert_trade_event(
                    connection,
                    trade_id=trade_id,
                    event_type="entry_reserved",
                    old_status=None,
                    new_status=(
                        TradeStatus.RESERVED
                    ),
                    occurred_at=timestamp,
                    details={
                        "quantity": quantity,
                        "planned_entry":
                            planned_entry,
                        "initial_stop":
                            initial_stop,
                        "target_price":
                            target_price,
                    },
                )

        except sqlite3.IntegrityError as error:
            if "UNIQUE constraint failed" in str(
                error
            ):
                raise DuplicateEntryError(
                    f"{strategy} already reserved "
                    f"{symbol} for {session_date}."
                ) from error

            raise

        return self.get_trade(trade_id)

    def get_broker_action_authorization(
        self,
        authorization_id: str,
    ) -> BrokerActionAuthorizationRecord:
        authorization_id = authorization_id.strip()
        if not authorization_id:
            raise TradeStateError(
                "Broker action authorization ID is required."
            )

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM broker_action_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()

        if row is None:
            raise TradeStateError(
                "Broker action authorization was not found."
            )
        return self._row_to_broker_action_authorization(row)

    def count_broker_action_authorizations(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_action_authorizations
                """
            ).fetchone()
        return int(row["count_value"])

    def count_broker_action_authorization_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM broker_action_authorization_events
                """
            ).fetchone()
        return int(row["count_value"])

    @staticmethod
    def _insert_broker_action_authorization_event(
        connection: sqlite3.Connection,
        *,
        authorization_id: str,
        event_type: str,
        old_status: BrokerActionAuthorizationStatus | None,
        new_status: BrokerActionAuthorizationStatus,
        occurred_at: datetime,
        details: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO broker_action_authorization_events (
                authorization_id, event_type, old_status,
                new_status, occurred_at, details_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                authorization_id,
                event_type,
                old_status.value if old_status else None,
                new_status.value,
                occurred_at.isoformat(),
                json.dumps(
                    details,
                    sort_keys=True,
                    default=str,
                ),
            ),
        )

    def authorize_broker_action_plan(
        self,
        *,
        strategy: str,
        trade_id: str,
        purpose: str,
        plan_digest: str,
        source_trade_version: int,
        reconciliation_id: str,
        snapshot_digest: str,
        account_fingerprint: str,
        authorized_at: datetime,
        expires_at: datetime,
    ) -> tuple[BrokerActionAuthorizationRecord, bool]:
        strategy = strategy.strip()
        trade_id = trade_id.strip()
        purpose = purpose.strip()
        plan_digest = plan_digest.strip().lower()
        reconciliation_id = reconciliation_id.strip()
        snapshot_digest = snapshot_digest.strip().lower()
        account_fingerprint = account_fingerprint.strip().lower()

        if not strategy or not trade_id or not reconciliation_id:
            raise TradeStateError(
                "Strategy, trade, and reconciliation IDs are required for broker action authorization."
            )
        if purpose not in {
            "entry",
            "protect_filled_entry",
            "replace_protective_stop",
            "exit_position",
        }:
            raise TradeStateError(
                "Broker action purpose is unsupported or invalid."
            )
        for value, label in (
            (plan_digest, "plan"),
            (snapshot_digest, "snapshot"),
            (account_fingerprint, "account"),
        ):
            if (
                len(value) != 64
                or any(character not in "0123456789abcdef" for character in value)
            ):
                raise TradeStateError(
                    f"Broker action {label} digest is invalid."
                )
        if source_trade_version < 1:
            raise TradeStateError(
                "Source trade version must be positive."
            )
        if (
            authorized_at.tzinfo is None
            or expires_at.tzinfo is None
            or expires_at <= authorized_at
        ):
            raise TradeStateError(
                "Broker action authorization timestamps are invalid."
            )

        authorization_id = str(uuid.uuid4())
        with self._connect() as connection:
            trade_row = connection.execute(
                "SELECT * FROM trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()
            if trade_row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )
            current = self._row_to_trade(trade_row)
            if current.strategy != strategy:
                raise TradeStateError(
                    "Trade strategy does not match broker action authorization."
                )
            if current.version != source_trade_version:
                raise TradeStateError(
                    "Trade changed before broker action authorization could be persisted."
                )
            if purpose == "entry":
                active_halt = connection.execute(
                    """
                    SELECT 1
                    FROM entry_halts
                    WHERE strategy = ?
                      AND active = 1
                    LIMIT 1
                    """,
                    (strategy,),
                ).fetchone()
                if active_halt is not None:
                    raise EntryHaltedError(
                        "Entry authorization is blocked by a durable strategy halt."
                    )

            existing = connection.execute(
                """
                SELECT *
                FROM broker_action_authorizations
                WHERE trade_id = ?
                  AND source_trade_version = ?
                  AND reconciliation_id = ?
                  AND plan_digest = ?
                """,
                (
                    trade_id,
                    source_trade_version,
                    reconciliation_id,
                    plan_digest,
                ),
            ).fetchone()
            if existing is not None:
                persisted = (
                    self._row_to_broker_action_authorization(
                        existing
                    )
                )
                if (
                    persisted.strategy != strategy
                    or persisted.purpose != purpose
                    or persisted.snapshot_digest
                    != snapshot_digest
                    or persisted.account_fingerprint
                    != account_fingerprint
                ):
                    raise TradeStateError(
                        "Broker action authorization retry does not match the persisted evidence."
                    )
                return (
                    persisted,
                    False,
                )

            conflicting = connection.execute(
                """
                SELECT 1
                FROM broker_action_authorizations
                WHERE trade_id = ?
                  AND status IN (?, ?)
                LIMIT 1
                """,
                (
                    trade_id,
                    BrokerActionAuthorizationStatus.AUTHORIZED.value,
                    BrokerActionAuthorizationStatus.CLAIMED.value,
                ),
            ).fetchone()
            if conflicting is not None:
                raise TradeStateError(
                    "A different active broker action authorization already exists for this trade."
                )

            connection.execute(
                """
                INSERT INTO broker_action_authorizations (
                    authorization_id, strategy, trade_id,
                    purpose, plan_digest, source_trade_version,
                    reconciliation_id, snapshot_digest,
                    account_fingerprint, status,
                    authorized_at, expires_at,
                    claim_id, claimed_at,
                    invalidated_at, invalidation_reason,
                    version
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    NULL, NULL, NULL, NULL, 1
                )
                """,
                (
                    authorization_id,
                    strategy,
                    trade_id,
                    purpose,
                    plan_digest,
                    source_trade_version,
                    reconciliation_id,
                    snapshot_digest,
                    account_fingerprint,
                    BrokerActionAuthorizationStatus.AUTHORIZED.value,
                    authorized_at.isoformat(),
                    expires_at.isoformat(),
                ),
            )
            self._insert_broker_action_authorization_event(
                connection,
                authorization_id=authorization_id,
                event_type="broker_action_authorized",
                old_status=None,
                new_status=(
                    BrokerActionAuthorizationStatus.AUTHORIZED
                ),
                occurred_at=authorized_at,
                details={
                    "strategy": strategy,
                    "trade_id": trade_id,
                    "purpose": purpose,
                    "plan_digest": plan_digest,
                    "source_trade_version": source_trade_version,
                    "reconciliation_id": reconciliation_id,
                    "snapshot_digest": snapshot_digest,
                    "account_fingerprint": account_fingerprint,
                    "expires_at": expires_at,
                },
            )

        return (
            self.get_broker_action_authorization(
                authorization_id
            ),
            True,
        )

    def claim_broker_action_authorization(
        self,
        authorization_id: str,
        *,
        plan_digest: str,
        claim_id: str,
        occurred_at: datetime | None = None,
    ) -> tuple[BrokerActionAuthorizationRecord, bool]:
        authorization_id = authorization_id.strip()
        plan_digest = plan_digest.strip().lower()
        claim_id = claim_id.strip()
        timestamp = occurred_at or utc_now()
        if not authorization_id or not claim_id:
            raise TradeStateError(
                "Authorization and claim IDs are required."
            )
        if (
            len(plan_digest) != 64
            or any(
                character not in "0123456789abcdef"
                for character in plan_digest
            )
        ):
            raise TradeStateError(
                "Claimed broker action plan digest is invalid."
            )
        if timestamp.tzinfo is None:
            raise TradeStateError(
                "Broker action claim timestamp must be timezone-aware."
            )

        invalidation_reason: str | None = None
        claimed = False
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM broker_action_authorizations
                WHERE authorization_id = ?
                """,
                (authorization_id,),
            ).fetchone()
            if row is None:
                raise TradeStateError(
                    "Broker action authorization was not found."
                )
            current = self._row_to_broker_action_authorization(
                row
            )

            if (
                current.status
                == BrokerActionAuthorizationStatus.INVALIDATED
            ):
                raise TradeStateError(
                    "Broker action authorization is invalidated."
                )

            active_entry_halt = None
            if current.purpose == "entry":
                active_entry_halt = connection.execute(
                    """
                    SELECT 1
                    FROM entry_halts
                    WHERE strategy = ?
                      AND active = 1
                    LIMIT 1
                    """,
                    (current.strategy,),
                ).fetchone()
                if (
                    active_entry_halt is not None
                    and current.status
                    == BrokerActionAuthorizationStatus.CLAIMED
                ):
                    raise EntryHaltedError(
                        "Claim replay is blocked by a durable strategy halt."
                    )

            if (
                current.status
                == BrokerActionAuthorizationStatus.CLAIMED
            ):
                if (
                    current.claim_id == claim_id
                    and current.plan_digest == plan_digest
                ):
                    return current, False
                raise TradeStateError(
                    "Broker action authorization was already claimed with different evidence or by a different attempt."
                )
            if active_entry_halt is not None:
                invalidation_reason = (
                    "Entry claim is blocked by a durable strategy halt."
                )

            trade_row = connection.execute(
                "SELECT * FROM trades WHERE trade_id = ?",
                (current.trade_id,),
            ).fetchone()
            if trade_row is None:
                invalidation_reason = "Authorized trade no longer exists."
            else:
                trade = self._row_to_trade(trade_row)
                if trade.strategy != current.strategy:
                    invalidation_reason = (
                        "Authorized trade strategy changed."
                    )
                elif trade.version != current.source_trade_version:
                    invalidation_reason = (
                        "Authorized trade version is stale."
                    )
                elif plan_digest != current.plan_digest:
                    invalidation_reason = (
                        "Claimed plan digest does not match authorization."
                    )
                elif timestamp < current.authorized_at:
                    invalidation_reason = (
                        "Broker action claim predates its authorization."
                    )
                elif timestamp >= current.expires_at:
                    invalidation_reason = (
                        "Broker action authorization expired."
                    )

            if invalidation_reason is not None:
                connection.execute(
                    """
                    UPDATE broker_action_authorizations
                    SET status = ?, invalidated_at = ?,
                        invalidation_reason = ?,
                        version = version + 1
                    WHERE authorization_id = ?
                      AND version = ?
                    """,
                    (
                        BrokerActionAuthorizationStatus
                        .INVALIDATED.value,
                        timestamp.isoformat(),
                        invalidation_reason,
                        authorization_id,
                        current.version,
                    ),
                )
                self._insert_broker_action_authorization_event(
                    connection,
                    authorization_id=authorization_id,
                    event_type=(
                        "broker_action_authorization_invalidated"
                    ),
                    old_status=current.status,
                    new_status=(
                        BrokerActionAuthorizationStatus.INVALIDATED
                    ),
                    occurred_at=timestamp,
                    details={
                        "reason": invalidation_reason,
                    },
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE broker_action_authorizations
                    SET status = ?, claim_id = ?,
                        claimed_at = ?, version = version + 1
                    WHERE authorization_id = ?
                      AND status = ?
                      AND version = ?
                    """,
                    (
                        BrokerActionAuthorizationStatus.CLAIMED.value,
                        claim_id,
                        timestamp.isoformat(),
                        authorization_id,
                        BrokerActionAuthorizationStatus.AUTHORIZED.value,
                        current.version,
                    ),
                )
                if cursor.rowcount != 1:
                    raise TradeStateError(
                        "Broker action authorization changed concurrently."
                    )
                self._insert_broker_action_authorization_event(
                    connection,
                    authorization_id=authorization_id,
                    event_type="broker_action_authorization_claimed",
                    old_status=current.status,
                    new_status=(
                        BrokerActionAuthorizationStatus.CLAIMED
                    ),
                    occurred_at=timestamp,
                    details={"claim_id": claim_id},
                )
                claimed = True

        if invalidation_reason is not None:
            raise TradeStateError(invalidation_reason)
        return (
            self.get_broker_action_authorization(
                authorization_id
            ),
            claimed,
        )

    def get_trade(
        self,
        trade_id: str,
    ) -> TradeRecord:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ).fetchone()

        if row is None:
            raise TradeNotFoundError(
                f"Trade not found: {trade_id}"
            )

        return self._row_to_trade(row)

    def list_active_trades(
        self,
    ) -> tuple[TradeRecord, ...]:
        placeholders = ", ".join(
            "?"
            for _ in ACTIVE_STATUSES
        )

        values = tuple(
            status.value
            for status in ACTIVE_STATUSES
        )

        with self._connect() as connection:
            rows = connection.execute(
                f"""
                SELECT *
                FROM trades
                WHERE status IN ({placeholders})
                ORDER BY created_at
                """,
                values,
            ).fetchall()

        return tuple(
            self._row_to_trade(row)
            for row in rows
        )

    def entry_count_for_day(
        self,
        *,
        strategy: str,
        session_date: date,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM trades
                WHERE strategy = ?
                  AND session_date = ?
                  AND status != ?
                """,
                (
                    strategy,
                    session_date.isoformat(),
                    TradeStatus.CANCELLED.value,
                ),
            ).fetchone()

        return int(row["count_value"])

    def _transition(
        self,
        trade_id: str,
        *,
        new_status: TradeStatus,
        event_type: str,
        updates: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        timestamp = occurred_at or utc_now()
        updates = updates or {}

        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT *
                FROM trades
                WHERE trade_id = ?
                """,
                (trade_id,),
            ).fetchone()

            if row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )

            current = self._row_to_trade(row)

            if (
                new_status
                not in ALLOWED_TRANSITIONS[
                    current.status
                ]
            ):
                raise InvalidTransitionError(
                    f"Cannot transition "
                    f"{current.status.value} to "
                    f"{new_status.value}."
                )

            permitted_columns = {
                "filled_quantity",
                "average_entry_price",
                "average_exit_price",
                "realized_pnl",
                "entry_order_id",
                "protective_stop_order_id",
                "exit_order_id",
                "close_reason",
                "opened_at",
                "closed_at",
                "protected_quantity",
                "last_broker_observation_at",
                "required_recovery_action",
                "last_completed_recovery_step",
                "recovery_ref_id",
                "recovery_attempt_count",
                "recovery_failure_reason",
                "operator_ack_required",
                "last_entry_observation_key",
                "last_protection_reconciliation_id",
                "protection_confirmed_at",
                "stop_original_position_quantity",
                "stop_requested_quantity",
                "stop_executed_quantity",
                "remaining_broker_position_quantity",
                "last_stop_state",
                "average_stop_fill_price",
                "required_stop_recovery_action",
                "last_completed_stop_recovery_step",
                "stop_recovery_ref_id",
                "stop_recovery_attempt_count",
                "stop_recovery_failure_reason",
                "stop_operator_ack_required",
                "last_stop_observation_key",
                "last_stop_reconciliation_id",
                "exit_requested_quantity",
                "exit_close_reason",
                "exit_intent_type",
                "exit_intended_price",
                "exit_recovery_ref_id",
                "exit_linked_at",
                "last_exit_observation_at",
                "last_exit_reconciliation_id",
                "exit_attempt_base_quantity",
                "exit_attempt_base_proceeds",
                "exit_attempt_base_fees",
                "exit_executed_quantity",
                "exit_order_fees",
                "cumulative_exited_quantity",
                "exit_remaining_position_quantity",
                "cumulative_average_exit_price",
                "cumulative_exit_proceeds",
                "cumulative_exit_fees",
                "cumulative_realized_pnl",
                "required_exit_recovery_action",
                "last_completed_exit_recovery_step",
                "exit_recovery_attempt_count",
                "exit_recovery_failure_reason",
                "exit_operator_ack_required",
                "last_exit_observation_key",
            }

            unknown_columns = (
                set(updates)
                - permitted_columns
            )

            if unknown_columns:
                raise TradeStateError(
                    "Unsupported trade update "
                    f"columns: "
                    f"{sorted(unknown_columns)}"
                )

            assignments = [
                "status = ?",
                "updated_at = ?",
                "version = version + 1",
            ]

            parameters: list[Any] = [
                new_status.value,
                timestamp.isoformat(),
            ]

            for column, value in updates.items():
                assignments.append(
                    f"{column} = ?"
                )

                if isinstance(value, Decimal):
                    parameters.append(
                        serialize_decimal(value)
                    )
                elif isinstance(value, datetime):
                    parameters.append(
                        value.isoformat()
                    )
                else:
                    parameters.append(value)

            parameters.extend(
                [
                    trade_id,
                    current.version,
                ]
            )

            cursor = connection.execute(
                f"""
                UPDATE trades
                SET {", ".join(assignments)}
                WHERE trade_id = ?
                  AND version = ?
                """,
                parameters,
            )

            if cursor.rowcount != 1:
                raise TradeStateError(
                    "Trade state changed concurrently; "
                    "operation failed closed."
                )

            self._insert_trade_event(
                connection,
                trade_id=trade_id,
                event_type=event_type,
                old_status=current.status,
                new_status=new_status,
                occurred_at=timestamp,
                details=details,
            )

        return self.get_trade(trade_id)

    def attach_entry_order(
        self,
        trade_id: str,
        *,
        broker_order_id: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        if not broker_order_id.strip():
            raise TradeStateError(
                "Broker order ID is required."
            )

        return self._transition(
            trade_id,
            new_status=(
                TradeStatus.ENTRY_PENDING
            ),
            event_type="entry_order_attached",
            updates={
                "entry_order_id":
                    broker_order_id,
            },
            details={
                "broker_order_id":
                    broker_order_id,
            },
            occurred_at=occurred_at,
        )

    def mark_entry_filled(
        self,
        trade_id: str,
        *,
        filled_quantity: int,
        average_entry_price: Decimal,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        current = self.get_trade(trade_id)

        if filled_quantity != current.quantity:
            raise TradeStateError(
                "Partial entry fills must use the "
                "durable recovery observation path."
            )

        if average_entry_price <= 0:
            raise TradeStateError(
                "Average entry price must be positive."
            )

        timestamp = occurred_at or utc_now()
        observation_key = (
            "confirmed-full-fill:"
            f"{trade_id}:"
            f"{filled_quantity}:"
            f"{average_entry_price}"
        )

        trade, _ = self.persist_entry_observation(
            trade_id,
            status=TradeStatus.PROTECTION_PENDING,
            filled_quantity=filled_quantity,
            average_entry_price=(
                average_entry_price
            ),
            required_action=(
                EntryRecoveryAction
                .PROTECT_FILLED_QUANTITY
            ),
            last_completed_step=(
                EntryRecoveryStep
                .FULL_FILL_CLASSIFIED
            ),
            observation_key=observation_key,
            observed_at=timestamp,
        )

        return trade

    def attach_protective_stop(
        self,
        trade_id: str,
        *,
        broker_order_id: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        broker_order_id = broker_order_id.strip()

        if not broker_order_id:
            raise TradeStateError(
                "Protective stop order ID "
                "is required."
            )

        current = self.get_trade(trade_id)

        if (
            current.status
            == TradeStatus.OPEN_PROTECTED
            and current.protective_stop_order_id
            == broker_order_id
        ):
            return current

        if (
            current.status == TradeStatus.STOP_LINKED
            and current.protective_stop_order_id
            == broker_order_id
        ):
            return current

        if current.status not in {
            TradeStatus.ENTRY_PARTIALLY_FILLED,
            TradeStatus.PROTECTION_PENDING,
            TradeStatus.RECOVERY_REQUIRED,
            TradeStatus.OPEN_UNPROTECTED,
            TradeStatus.STOP_LINKED,
            TradeStatus.STOP_RECOVERY_REQUIRED,
            TradeStatus.EXIT_PARTIALLY_FILLED,
            TradeStatus.EXIT_RECOVERY_REQUIRED,
        }:
            raise InvalidTransitionError(
                "A protective stop can only be "
                "linked to an open unprotected trade."
            )

        if current.filled_quantity <= 0:
            raise TradeStateError(
                "A protective stop cannot be linked "
                "without a positive confirmed fill."
            )

        timestamp = occurred_at or utc_now()

        with self._connect() as connection:
            self._activate_entry_halt(
                connection,
                halt_key=f"trade:{trade_id}",
                strategy=current.strategy,
                trade_id=trade_id,
                reason=(
                    "A protective stop is linked locally "
                    "but has not been confirmed by a fresh "
                    "complete broker snapshot."
                ),
                operator_ack_required=False,
                occurred_at=timestamp,
            )

        return self._transition(
            trade_id,
            new_status=(
                current.status
                if current.status in {
                    TradeStatus.EXIT_PARTIALLY_FILLED,
                    TradeStatus.EXIT_RECOVERY_REQUIRED,
                }
                else TradeStatus.STOP_LINKED
            ),
            event_type=(
                "exit_remaining_stop_linked"
                if current.status in {
                    TradeStatus.EXIT_PARTIALLY_FILLED,
                    TradeStatus.EXIT_RECOVERY_REQUIRED,
                }
                else "protective_stop_linked"
            ),
            updates={
                "protective_stop_order_id":
                    broker_order_id,
                "protected_quantity": 0,
                "remaining_broker_position_quantity": (
                    current.exit_remaining_position_quantity
                    if current.exit_remaining_position_quantity
                    is not None
                    else current.remaining_broker_position_quantity
                ),
                "required_recovery_action":
                    EntryRecoveryAction
                    .RECONCILE_BROKER_STATE.value,
                "last_completed_recovery_step":
                    EntryRecoveryStep
                    .PROTECTIVE_STOP_LINKED.value,
                "recovery_failure_reason": None,
                "operator_ack_required": 0,
                "last_protection_reconciliation_id":
                    None,
                "protection_confirmed_at": None,
            },
            details={
                "proof_state": (
                    "linked_locally_broker_confirmation_"
                    "required"
                ),
            },
            occurred_at=timestamp,
        )

    def persist_stop_recovery_observation(
        self,
        trade_id: str,
        *,
        observation_key: str,
        broker_order_id: str | None,
        broker_state: str | None,
        requested_quantity: int | None,
        executed_quantity: int,
        remaining_order_quantity: int | None,
        average_fill_price: Decimal | None,
        required_action: StopRecoveryAction,
        last_completed_step: StopRecoveryStep,
        failure_reason: str,
        operator_ack_required: bool,
        observed_at: datetime | None = None,
    ) -> tuple[TradeRecord, bool]:
        observation_key = observation_key.strip()
        timestamp = observed_at or utc_now()

        if not observation_key:
            raise TradeStateError(
                "A stable stop observation key is required."
            )

        if executed_quantity < 0:
            raise TradeStateError(
                "Executed stop quantity cannot be negative."
            )

        for quantity, label in (
            (requested_quantity, "requested"),
            (remaining_order_quantity, "remaining"),
        ):
            if quantity is not None and quantity < 0:
                raise TradeStateError(
                    f"Stop {label} quantity cannot be negative."
                )

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM protective_stop_recovery_events
                WHERE trade_id = ?
                  AND observation_key = ?
                """,
                (trade_id, observation_key),
            ).fetchone()

            if existing is not None:
                return self.get_trade(trade_id), False

            row = connection.execute(
                "SELECT * FROM trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()

            if row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )

            current = self._row_to_trade(row)

            if current.status not in {
                TradeStatus.OPEN_PROTECTED,
                TradeStatus.STOP_LINKED,
                TradeStatus.STOP_RECOVERY_REQUIRED,
            }:
                raise InvalidTransitionError(
                    "Protective-stop recovery evidence can "
                    "only be persisted for a linked stop."
                )

            original_quantity = (
                current.stop_original_position_quantity
                if current.stop_original_position_quantity
                is not None
                else current.filled_quantity
            )

            cursor = connection.execute(
                """
                UPDATE trades
                SET status = ?,
                    protected_quantity = 0,
                    stop_original_position_quantity = ?,
                    stop_requested_quantity = ?,
                    stop_executed_quantity = ?,
                    remaining_broker_position_quantity = NULL,
                    last_stop_state = ?,
                    average_stop_fill_price = ?,
                    required_stop_recovery_action = ?,
                    last_completed_stop_recovery_step = ?,
                    stop_recovery_ref_id = ?,
                    stop_recovery_failure_reason = ?,
                    stop_operator_ack_required = ?,
                    last_stop_observation_key = ?,
                    last_stop_reconciliation_id = NULL,
                    protection_confirmed_at = NULL,
                    updated_at = ?,
                    version = version + 1
                WHERE trade_id = ?
                  AND version = ?
                """,
                (
                    TradeStatus.STOP_RECOVERY_REQUIRED.value,
                    original_quantity,
                    requested_quantity,
                    executed_quantity,
                    broker_state,
                    serialize_decimal(average_fill_price),
                    required_action.value,
                    last_completed_step.value,
                    observation_key,
                    failure_reason,
                    int(operator_ack_required),
                    observation_key,
                    timestamp.isoformat(),
                    trade_id,
                    current.version,
                ),
            )

            if cursor.rowcount != 1:
                raise TradeStateError(
                    "Trade state changed concurrently; "
                    "stop recovery failed closed."
                )

            details = {
                "broker_order_id": broker_order_id,
                "broker_state": broker_state,
                "requested_quantity": requested_quantity,
                "executed_quantity": executed_quantity,
                "remaining_order_quantity": remaining_order_quantity,
                "average_fill_price": average_fill_price,
                "required_action": required_action.value,
                "failure_reason": failure_reason,
            }

            self._insert_trade_event(
                connection,
                trade_id=trade_id,
                event_type="protective_stop_recovery_required",
                old_status=current.status,
                new_status=TradeStatus.STOP_RECOVERY_REQUIRED,
                occurred_at=timestamp,
                details=details,
            )

            connection.execute(
                """
                INSERT INTO protective_stop_recovery_events (
                    trade_id,
                    observation_key,
                    broker_order_id,
                    broker_state,
                    requested_quantity,
                    executed_quantity,
                    remaining_quantity,
                    average_fill_price,
                    old_status,
                    new_status,
                    required_action,
                    occurred_at,
                    details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trade_id,
                    observation_key,
                    broker_order_id,
                    broker_state,
                    requested_quantity,
                    executed_quantity,
                    remaining_order_quantity,
                    serialize_decimal(average_fill_price),
                    current.status.value,
                    TradeStatus.STOP_RECOVERY_REQUIRED.value,
                    required_action.value,
                    timestamp.isoformat(),
                    json.dumps(
                        details,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

            self._activate_entry_halt(
                connection,
                halt_key=f"trade:{trade_id}",
                strategy=current.strategy,
                trade_id=trade_id,
                reason=failure_reason,
                operator_ack_required=operator_ack_required,
                occurred_at=timestamp,
            )

        return self.get_trade(trade_id), True

    def mark_protective_stop_inactive(
        self,
        trade_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        updated = self._transition(
            trade_id,
            new_status=(
                TradeStatus.OPEN_UNPROTECTED
            ),
            event_type=(
                "protective_stop_inactive"
            ),
            updates={
                "protective_stop_order_id":
                    None,
                "protected_quantity": 0,
                "last_protection_reconciliation_id":
                    None,
                "protection_confirmed_at": None,
            },
            details={"reason": reason},
            occurred_at=occurred_at,
        )

        timestamp = occurred_at or utc_now()

        with self._connect() as connection:
            self._activate_entry_halt(
                connection,
                halt_key=f"trade:{trade_id}",
                strategy=updated.strategy,
                trade_id=trade_id,
                reason=(
                    "A filled position may be "
                    "unprotected because its broker "
                    "stop became inactive."
                ),
                operator_ack_required=True,
                occurred_at=timestamp,
            )

        return self.get_trade(trade_id)

    def begin_exit(
        self,
        trade_id: str,
        *,
        broker_order_id: str,
        requested_quantity: int,
        close_reason: str,
        intent_type: ExitIntentType | str,
        intended_price: Decimal | None = None,
        recovery_ref_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        broker_order_id = broker_order_id.strip()
        close_reason = close_reason.strip()
        timestamp = occurred_at or utc_now()

        if not broker_order_id:
            raise TradeStateError(
                "Exit order ID is required."
            )

        if not close_reason:
            raise TradeStateError(
                "A durable exit close reason is required."
            )

        try:
            parsed_intent = (
                intent_type
                if isinstance(intent_type, ExitIntentType)
                else ExitIntentType(str(intent_type).strip())
            )
        except ValueError as error:
            raise TradeStateError(
                "Exit intent type is missing or invalid."
            ) from error

        if intended_price is not None and intended_price <= 0:
            raise TradeStateError(
                "Intended exit price must be positive."
            )

        if (
            recovery_ref_id is not None
            and not recovery_ref_id.strip()
        ):
            raise TradeStateError(
                "Exit recovery reference cannot be blank."
            )

        current = self.get_trade(trade_id)

        if current.protective_stop_order_id:
            raise TradeStateError(
                "A separate exit cannot be linked while "
                "a protective stop remains linked."
            )

        if current.average_entry_price is None:
            raise TradeStateError(
                "Trade has no confirmed entry price."
            )

        base_quantity = current.cumulative_exited_quantity
        base_proceeds = current.cumulative_exit_proceeds
        base_fees = current.cumulative_exit_fees

        if (
            base_quantity == 0
            and current.stop_executed_quantity > 0
        ):
            if current.average_stop_fill_price is None:
                raise TradeStateError(
                    "Prior stop execution lacks an "
                    "average price; exit intent fails closed."
                )

            base_quantity = current.stop_executed_quantity
            base_proceeds = (
                current.average_stop_fill_price
                * Decimal(base_quantity)
            )
            base_fees = Decimal("0")

        remaining = current.filled_quantity - base_quantity

        if remaining <= 0:
            raise TradeStateError(
                "No remaining position is available to exit."
            )

        if requested_quantity != remaining:
            raise TradeStateError(
                "Requested exit quantity must equal the "
                "current remaining position."
            )

        reference = (
            recovery_ref_id.strip()
            if recovery_ref_id
            else str(uuid.uuid4())
        )
        validated_version = current.version

        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()

            if row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )

            current = self._row_to_trade(row)

            if current.version != validated_version:
                raise TradeStateError(
                    "Trade state changed during exit validation; "
                    "exit intent failed closed."
                )

            if (
                TradeStatus.EXIT_PENDING
                not in ALLOWED_TRANSITIONS[current.status]
            ):
                raise InvalidTransitionError(
                    f"Cannot begin exit from "
                    f"{current.status.value}."
                )

            cursor = connection.execute(
                """
                UPDATE trades
                SET status = ?,
                    exit_order_id = ?,
                    exit_requested_quantity = ?,
                    exit_close_reason = ?,
                    exit_intent_type = ?,
                    exit_intended_price = ?,
                    exit_recovery_ref_id = ?,
                    exit_linked_at = ?,
                    last_exit_observation_at = NULL,
                    last_exit_reconciliation_id = NULL,
                    exit_attempt_base_quantity = ?,
                    exit_attempt_base_proceeds = ?,
                    exit_attempt_base_fees = ?,
                    exit_executed_quantity = 0,
                    exit_order_fees = '0',
                    cumulative_exited_quantity = ?,
                    exit_remaining_position_quantity = ?,
                    cumulative_average_exit_price = ?,
                    cumulative_exit_proceeds = ?,
                    cumulative_exit_fees = ?,
                    cumulative_realized_pnl = ?,
                    realized_pnl = ?,
                    required_exit_recovery_action = ?,
                    last_completed_exit_recovery_step = ?,
                    exit_recovery_attempt_count =
                        exit_recovery_attempt_count + 1,
                    exit_recovery_failure_reason = NULL,
                    exit_operator_ack_required = 0,
                    last_exit_observation_key = NULL,
                    protected_quantity = 0,
                    protection_confirmed_at = NULL,
                    updated_at = ?,
                    version = version + 1
                WHERE trade_id = ?
                  AND version = ?
                """,
                (
                    TradeStatus.EXIT_PENDING.value,
                    broker_order_id,
                    requested_quantity,
                    close_reason,
                    parsed_intent.value,
                    serialize_decimal(intended_price),
                    reference,
                    timestamp.isoformat(),
                    base_quantity,
                    serialize_decimal(base_proceeds),
                    serialize_decimal(base_fees),
                    base_quantity,
                    remaining,
                    serialize_decimal(
                        (
                            base_proceeds
                            / Decimal(base_quantity)
                        )
                        if base_quantity > 0
                        else None
                    ),
                    serialize_decimal(base_proceeds),
                    serialize_decimal(base_fees),
                    serialize_decimal(
                        base_proceeds
                        - current.average_entry_price
                        * Decimal(base_quantity)
                    ),
                    serialize_decimal(
                        base_proceeds
                        - current.average_entry_price
                        * Decimal(base_quantity)
                    ),
                    ExitRecoveryAction.WAIT.value,
                    ExitRecoveryStep.INTENT_LINKED.value,
                    timestamp.isoformat(),
                    trade_id,
                    current.version,
                ),
            )

            if cursor.rowcount != 1:
                raise TradeStateError(
                    "Trade state changed concurrently; "
                    "exit linkage failed closed."
                )

            self._insert_trade_event(
                connection,
                trade_id=trade_id,
                event_type="exit_intent_linked",
                old_status=current.status,
                new_status=TradeStatus.EXIT_PENDING,
                occurred_at=timestamp,
                details={
                    "requested_quantity": requested_quantity,
                    "close_reason": close_reason,
                    "intent_type": parsed_intent.value,
                    "intended_price": intended_price,
                    "recovery_ref_id": reference,
                    "accounting_base_quantity":
                        base_quantity,
                },
            )
            self._activate_entry_halt(
                connection,
                halt_key=f"trade:{trade_id}",
                strategy=current.strategy,
                trade_id=trade_id,
                reason=(
                    "A separate exit order is pending; "
                    "remaining exposure is not proven "
                    "protected by a fresh snapshot."
                ),
                operator_ack_required=False,
                occurred_at=timestamp,
            )

        return self.get_trade(trade_id)

    def persist_exit_observation(
        self,
        trade_id: str,
        *,
        observation_key: str,
        broker_order_id: str | None,
        broker_state: str | None,
        requested_quantity: int | None,
        executed_quantity: int,
        average_fill_price: Decimal | None,
        order_fees: Decimal,
        new_status: TradeStatus,
        required_action: ExitRecoveryAction,
        last_completed_step: ExitRecoveryStep,
        failure_code: str | None,
        failure_reason: str | None,
        operator_ack_required: bool,
        observed_at: datetime | None = None,
    ) -> tuple[TradeRecord, bool]:
        observation_key = observation_key.strip()
        timestamp = observed_at or utc_now()

        if not observation_key:
            raise TradeStateError(
                "A stable exit observation key is required."
            )

        if executed_quantity < 0 or order_fees < 0:
            raise TradeStateError(
                "Exit quantity and fees cannot be negative."
            )

        if new_status not in {
            TradeStatus.EXIT_PENDING,
            TradeStatus.EXIT_PARTIALLY_FILLED,
            TradeStatus.EXIT_RECOVERY_REQUIRED,
        }:
            raise TradeStateError(
                "Invalid durable exit observation status."
            )

        with self._connect() as connection:
            existing = connection.execute(
                """
                SELECT 1
                FROM exit_recovery_events
                WHERE trade_id = ?
                  AND observation_key = ?
                """,
                (trade_id, observation_key),
            ).fetchone()

            if existing is not None:
                return self.get_trade(trade_id), False

            row = connection.execute(
                "SELECT * FROM trades WHERE trade_id = ?",
                (trade_id,),
            ).fetchone()

            if row is None:
                raise TradeNotFoundError(
                    f"Trade not found: {trade_id}"
                )

            current = self._row_to_trade(row)

            if current.status not in {
                TradeStatus.EXIT_PENDING,
                TradeStatus.EXIT_PARTIALLY_FILLED,
                TradeStatus.EXIT_RECOVERY_REQUIRED,
            }:
                raise InvalidTransitionError(
                    "Exit observations require a durable "
                    "exit lifecycle state."
                )

            if current.exit_operator_ack_required:
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                operator_ack_required = True
                if failure_reason is None:
                    failure_reason = (
                        current.exit_recovery_failure_reason
                    )

            if not current.exit_close_reason:
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "missing_durable_exit_intent"
                failure_reason = (
                    "Durable exit close reason is missing."
                )
                operator_ack_required = True

            observation_is_accountable = True
            reported_total_exited = (
                current.exit_attempt_base_quantity
                + executed_quantity
            )

            if executed_quantity < current.exit_executed_quantity:
                observation_is_accountable = False
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "exit_quantity_decreased"
                failure_reason = (
                    "Broker cumulative exit quantity "
                    "decreased between observations."
                )
                operator_ack_required = True

            if (
                requested_quantity is not None
                and current.exit_requested_quantity
                is not None
                and requested_quantity
                != current.exit_requested_quantity
            ):
                observation_is_accountable = False
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "exit_requested_quantity_mismatch"
                failure_reason = (
                    "Broker requested exit quantity does "
                    "not match durable exit intent."
                )
                operator_ack_required = True

            if (
                current.exit_requested_quantity is not None
                and executed_quantity
                > current.exit_requested_quantity
            ):
                observation_is_accountable = False
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "exit_execution_exceeds_request"
                failure_reason = (
                    "Exit execution exceeds requested "
                    "quantity; oversell risk exists."
                )
                operator_ack_required = True

            if reported_total_exited > current.filled_quantity:
                observation_is_accountable = False
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "exit_execution_exceeds_position"
                failure_reason = (
                    "Total sell execution exceeds the "
                    "original filled position."
                )
                operator_ack_required = True

            if (
                executed_quantity > 0
                and (
                    average_fill_price is None
                    or average_fill_price <= 0
                )
            ):
                observation_is_accountable = False
                new_status = TradeStatus.EXIT_RECOVERY_REQUIRED
                required_action = (
                    ExitRecoveryAction.OPERATOR_INTERVENTION
                )
                last_completed_step = (
                    ExitRecoveryStep
                    .INCONSISTENT_STATE_RECORDED
                )
                failure_code = "exit_average_price_missing"
                failure_reason = (
                    "Positive exit execution has no "
                    "valid broker-confirmed average price."
                )
                operator_ack_required = True

            accounting_executed_quantity = (
                executed_quantity
                if observation_is_accountable
                else current.exit_executed_quantity
            )
            accounted_order_fees = (
                order_fees
                if observation_is_accountable
                else current.exit_order_fees
            )
            executed_delta = (
                accounting_executed_quantity
                - current.exit_executed_quantity
            )
            total_exited = (
                current.exit_attempt_base_quantity
                + accounting_executed_quantity
            )

            if (
                observation_is_accountable
                and accounting_executed_quantity > 0
            ):
                assert average_fill_price is not None
                proceeds = (
                    current.exit_attempt_base_proceeds
                    + average_fill_price
                    * Decimal(accounting_executed_quantity)
                )
                cumulative_average = (
                    proceeds / Decimal(total_exited)
                )
                cumulative_pnl = (
                    proceeds
                    - current.average_entry_price
                    * Decimal(total_exited)
                )
            elif not observation_is_accountable:
                proceeds = current.cumulative_exit_proceeds
                cumulative_average = (
                    current.cumulative_average_exit_price
                )
                cumulative_pnl = (
                    current.cumulative_realized_pnl
                )
            else:
                proceeds = current.exit_attempt_base_proceeds
                cumulative_average = (
                    current.cumulative_average_exit_price
                )
                cumulative_pnl = (
                    current.cumulative_realized_pnl
                )
            total_fees = (
                current.exit_attempt_base_fees
                + accounted_order_fees
            )
            remaining = max(
                0,
                current.filled_quantity - total_exited,
            )
            protected_quantity = min(
                current.protected_quantity,
                remaining,
            )

            cursor = connection.execute(
                """
                UPDATE trades
                SET status = ?,
                    exit_executed_quantity = ?,
                    exit_order_fees = ?,
                    cumulative_exited_quantity = ?,
                    exit_remaining_position_quantity = ?,
                    cumulative_average_exit_price = ?,
                    cumulative_exit_proceeds = ?,
                    cumulative_exit_fees = ?,
                    cumulative_realized_pnl = ?,
                    realized_pnl = ?,
                    protected_quantity = ?,
                    required_exit_recovery_action = ?,
                    last_completed_exit_recovery_step = ?,
                    exit_recovery_failure_reason = ?,
                    exit_operator_ack_required = ?,
                    last_exit_observation_key = ?,
                    last_exit_observation_at = ?,
                    last_exit_reconciliation_id = NULL,
                    updated_at = ?,
                    version = version + 1
                WHERE trade_id = ?
                  AND version = ?
                """,
                (
                    new_status.value,
                    accounting_executed_quantity,
                    serialize_decimal(accounted_order_fees),
                    total_exited,
                    remaining,
                    serialize_decimal(cumulative_average),
                    serialize_decimal(proceeds),
                    serialize_decimal(total_fees),
                    serialize_decimal(cumulative_pnl),
                    serialize_decimal(cumulative_pnl),
                    protected_quantity,
                    required_action.value,
                    last_completed_step.value,
                    failure_reason,
                    int(operator_ack_required),
                    observation_key,
                    timestamp.isoformat(),
                    timestamp.isoformat(),
                    trade_id,
                    current.version,
                ),
            )

            if cursor.rowcount != 1:
                raise TradeStateError(
                    "Trade state changed concurrently; "
                    "exit recovery failed closed."
                )

            details = {
                "broker_order_id": broker_order_id,
                "broker_state": broker_state,
                "requested_quantity": requested_quantity,
                "reported_executed_quantity": executed_quantity,
                "accounted_executed_quantity":
                    accounting_executed_quantity,
                "reported_average_fill_price":
                    average_fill_price,
                "reported_order_fees": order_fees,
                "observation_is_accountable":
                    observation_is_accountable,
                "executed_delta": executed_delta,
                "total_exited_quantity": total_exited,
                "remaining_quantity": remaining,
                "cumulative_average_price":
                    cumulative_average,
                "cumulative_proceeds": proceeds,
                "cumulative_fees": total_fees,
                "cumulative_realized_pnl":
                    cumulative_pnl,
                "failure_code": failure_code,
                "failure_reason": failure_reason,
            }

            self._insert_trade_event(
                connection,
                trade_id=trade_id,
                event_type="exit_observation_persisted",
                old_status=current.status,
                new_status=new_status,
                occurred_at=timestamp,
                details=details,
            )
            connection.execute(
                """
                INSERT INTO exit_recovery_events (
                    trade_id, observation_key,
                    event_source, broker_order_id,
                    broker_state, requested_quantity,
                    executed_quantity, executed_delta,
                    remaining_quantity,
                    cumulative_average_price,
                    cumulative_proceeds, cumulative_fees,
                    cumulative_realized_pnl,
                    protection_result, reconciliation_id,
                    old_status, new_status,
                    required_action, failure_code,
                    failure_reason, occurred_at,
                    details_json
                )
                VALUES (
                    ?, ?, 'order_observation', ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, NULL, NULL,
                    ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    trade_id,
                    observation_key,
                    broker_order_id,
                    broker_state,
                    requested_quantity,
                    executed_quantity,
                    executed_delta,
                    remaining,
                    serialize_decimal(cumulative_average),
                    serialize_decimal(proceeds),
                    serialize_decimal(total_fees),
                    serialize_decimal(cumulative_pnl),
                    current.status.value,
                    new_status.value,
                    required_action.value,
                    failure_code,
                    failure_reason,
                    timestamp.isoformat(),
                    json.dumps(
                        details,
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )
            self._activate_entry_halt(
                connection,
                halt_key=f"trade:{trade_id}",
                strategy=current.strategy,
                trade_id=trade_id,
                reason=(
                    failure_reason
                    or "Exit lifecycle remains active; "
                    "fresh broker reconciliation is required."
                ),
                operator_ack_required=(
                    operator_ack_required
                ),
                occurred_at=timestamp,
            )

        return self.get_trade(trade_id), True

    def close_trade(
        self,
        trade_id: str,
        *,
        average_exit_price: Decimal,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        current = self.get_trade(trade_id)

        if current.average_entry_price is None:
            raise TradeStateError(
                "Trade has no confirmed entry price."
            )

        if current.filled_quantity <= 0:
            raise TradeStateError(
                "Trade has no confirmed filled quantity."
            )

        if average_exit_price <= 0:
            raise TradeStateError(
                "Average exit price must be positive."
            )

        if current.status in {
            TradeStatus.EXIT_PENDING,
            TradeStatus.EXIT_PARTIALLY_FILLED,
            TradeStatus.EXIT_RECOVERY_REQUIRED,
        }:
            exit_close_proved = (
                current.last_exit_reconciliation_id is not None
                and current.last_completed_exit_recovery_step
                == ExitRecoveryStep.FLAT_CONFIRMED.value
                and current.cumulative_exited_quantity
                == current.filled_quantity
                and current.cumulative_average_exit_price
                == average_exit_price
                and current.exit_close_reason == reason
            )
            if not exit_close_proved:
                raise TradeStateError(
                    "Durable exit closure requires fresh flat-snapshot "
                    "proof with complete matching accounting."
                )

        realized_pnl = (
            average_exit_price
            - current.average_entry_price
        ) * Decimal(
            current.filled_quantity
        )

        timestamp = occurred_at or utc_now()

        closed = self._transition(
            trade_id,
            new_status=TradeStatus.CLOSED,
            event_type="trade_closed",
            updates={
                "average_exit_price":
                    average_exit_price,
                "realized_pnl":
                    realized_pnl,
                "close_reason": reason,
                "closed_at": timestamp,
                "protective_stop_order_id":
                    None,
            },
            details={
                "average_exit_price":
                    average_exit_price,
                "realized_pnl":
                    realized_pnl,
                "reason": reason,
            },
            occurred_at=timestamp,
        )

        return closed

    def cancel_unfilled_trade(
        self,
        trade_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        current = self.get_trade(trade_id)

        if current.filled_quantity != 0:
            raise TradeStateError(
                "A filled trade cannot be cancelled "
                "as an unfilled trade."
            )

        return self._transition(
            trade_id,
            new_status=TradeStatus.CANCELLED,
            event_type="unfilled_trade_cancelled",
            updates={
                "close_reason": reason,
                "closed_at":
                    occurred_at or utc_now(),
            },
            details={"reason": reason},
            occurred_at=occurred_at,
        )

    def record_order_event(
        self,
        *,
        trade_id: str,
        broker_order_id: str,
        role: str,
        order_state: str,
        side: str,
        quantity: Decimal | None = None,
        filled_quantity: Decimal | None = None,
        limit_price: Decimal | None = None,
        stop_price: Decimal | None = None,
        details: dict[str, Any] | None = None,
        observation_key: str | None = None,
        occurred_at: datetime | None = None,
    ) -> bool:
        self.get_trade(trade_id)

        timestamp = occurred_at or utc_now()

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO order_events (
                    trade_id,
                    broker_order_id,
                    role,
                    order_state,
                    side,
                    quantity,
                    filled_quantity,
                    limit_price,
                    stop_price,
                    observation_key,
                    occurred_at,
                    details_json
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?
                )
                """,
                (
                    trade_id,
                    broker_order_id,
                    role,
                    order_state,
                    side,
                    serialize_decimal(quantity),
                    serialize_decimal(
                        filled_quantity
                    ),
                    serialize_decimal(limit_price),
                    serialize_decimal(stop_price),
                    observation_key,
                    timestamp.isoformat(),
                    json.dumps(
                        details or {},
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

        return cursor.rowcount == 1

    def count_entry_recovery_events(
        self,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM entry_recovery_events
                """
            ).fetchone()

        return int(row["count_value"])

    def count_exit_recovery_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM exit_recovery_events
                """
            ).fetchone()

        return int(row["count_value"])
    def realized_pnl_between(
        self,
        *,
        start_date: date,
        end_date: date,
        strategy: str | None = None,
    ) -> Decimal:
        if end_date < start_date:
            raise TradeStateError(
                "End date cannot be before "
                "start date."
            )

        query = """
            SELECT realized_pnl
            FROM trades
            WHERE session_date >= ?
              AND session_date <= ?
        """

        parameters: list[Any] = [
            start_date.isoformat(),
            end_date.isoformat(),
        ]

        if strategy:
            query += " AND strategy = ?"
            parameters.append(strategy)

        with self._connect() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        return sum(
            (
                parse_decimal(
                    row["realized_pnl"]
                )
                for row in rows
            ),
            Decimal("0"),
        )

    def daily_pnl(
        self,
        session_date: date,
        *,
        strategy: str | None = None,
    ) -> Decimal:
        return self.realized_pnl_between(
            start_date=session_date,
            end_date=session_date,
            strategy=strategy,
        )

    def weekly_pnl(
        self,
        session_date: date,
        *,
        strategy: str | None = None,
    ) -> Decimal:
        week_start = (
            session_date
            - timedelta(
                days=session_date.weekday()
            )
        )
        week_end = week_start + timedelta(
            days=6
        )

        return self.realized_pnl_between(
            start_date=week_start,
            end_date=week_end,
            strategy=strategy,
        )

    def current_drawdown(
        self,
        *,
        strategy: str | None = None,
    ) -> Decimal:
        query = """
            SELECT realized_pnl
            FROM trades
            WHERE realized_pnl != '0'
        """

        parameters: list[Any] = []

        if strategy:
            query += " AND strategy = ?"
            parameters.append(strategy)

        query += """
            ORDER BY COALESCE(closed_at, updated_at), trade_id
        """

        with self._connect() as connection:
            rows = connection.execute(
                query,
                parameters,
            ).fetchall()

        cumulative = Decimal("0")
        peak = Decimal("0")

        for row in rows:
            cumulative += parse_decimal(
                row["realized_pnl"]
            )
            peak = max(peak, cumulative)

        return max(
            Decimal("0"),
            peak - cumulative,
        )


    def startup_reconciliation_issues(
        self,
        *,
        strategy: str,
        account_state: Any,
    ) -> tuple[ReconciliationIssue, ...]:
        strategy = strategy.strip()

        if not strategy:
            raise TradeStateError(
                "Strategy name is required."
            )

        strategy_trade_ids = {
            trade.trade_id
            for trade in self.list_active_trades()
            if trade.strategy == strategy
        }

        issues = [
            issue
            for issue in self.reconcile(
                account_state
            )
            if (
                not issue.trade_id
                or issue.trade_id
                in strategy_trade_ids
            )
        ]

        with self._connect() as connection:
            unresolved = connection.execute(
                """
                SELECT trade_id, reason
                FROM entry_halts
                WHERE strategy = ?
                  AND active = 1
                  AND halt_key LIKE 'trade:%'
                ORDER BY created_at
                """,
                (strategy,),
            ).fetchall()

        for row in unresolved:
            issues.append(
                ReconciliationIssue(
                    severity="critical",
                    code="entry_recovery_unresolved",
                    message=row["reason"],
                    trade_id=(
                        row["trade_id"] or ""
                    ),
                )
            )

        return tuple(issues)

    def _existing_reconciliation_run(
        self,
        reconciliation_id: str,
    ) -> sqlite3.Row | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT *
                FROM broker_reconciliation_runs
                WHERE reconciliation_id = ?
                """,
                (reconciliation_id,),
            ).fetchone()

    def _record_reconciliation_run(
        self,
        *,
        strategy: str,
        snapshot: BrokerSnapshot,
        validation: SnapshotValidation,
        safety_confirmed: bool,
        result_code: str,
        failure_reason: str | None,
        occurred_at: datetime,
    ) -> None:
        metadata = snapshot.metadata

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO broker_reconciliation_runs (
                    reconciliation_id,
                    strategy,
                    account_fingerprint,
                    started_at,
                    positions_read_at,
                    orders_read_at,
                    completed_at,
                    positions_complete,
                    orders_complete,
                    snapshot_age_seconds,
                    maximum_age_seconds,
                    snapshot_digest,
                    snapshot_valid,
                    safety_confirmed,
                    result_code,
                    failure_reason,
                    occurred_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    metadata.reconciliation_id,
                    strategy,
                    metadata.account_fingerprint,
                    metadata.started_at.isoformat(),
                    metadata.positions_read_at.isoformat(),
                    metadata.orders_read_at.isoformat(),
                    metadata.completed_at.isoformat(),
                    int(metadata.positions_complete),
                    int(metadata.orders_complete),
                    (
                        serialize_decimal(
                            validation
                            .snapshot_age_seconds
                        )
                    ),
                    serialize_decimal(
                        validation
                        .maximum_age_seconds
                    ),
                    validation.snapshot_digest,
                    int(validation.valid),
                    int(safety_confirmed),
                    result_code,
                    failure_reason,
                    occurred_at.isoformat(),
                ),
            )

    def _record_protection_reconciliation_event(
        self,
        *,
        reconciliation_id: str,
        trade: TradeRecord,
        new_status: TradeStatus,
        confirmed: bool,
        result_code: str,
        failure_reason: str | None,
        position_quantity: Decimal | None,
        stop_quantity: Decimal | None,
        occurred_at: datetime,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO
                    protection_reconciliation_events (
                        reconciliation_id,
                        trade_id,
                        linked_stop_order_id,
                        position_quantity,
                        stop_quantity,
                        old_status,
                        new_status,
                        confirmed,
                        result_code,
                        failure_reason,
                        occurred_at
                    )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reconciliation_id,
                    trade.trade_id,
                    trade.protective_stop_order_id,
                    serialize_decimal(position_quantity),
                    serialize_decimal(stop_quantity),
                    trade.status.value,
                    new_status.value,
                    int(confirmed),
                    result_code,
                    failure_reason,
                    occurred_at.isoformat(),
                ),
            )

    @staticmethod
    def _protection_reconciliation_outcome(
        trade: TradeRecord,
        account_state: Any,
    ) -> dict[str, Any]:
        active_entry_states = {
            "new",
            "queued",
            "confirmed",
            "unconfirmed",
            "partially_filled",
            "pending_cancelled",
            "locating",
        }
        valid_stop_states = {
            "new",
            "queued",
            "confirmed",
            "unconfirmed",
            "locating",
        }
        position = next(
            (
                item
                for item in account_state.positions
                if (
                    item.symbol == trade.symbol
                    and item.quantity != 0
                )
            ),
            None,
        )
        entry_still_open = any(
            order.order_id == trade.entry_order_id
            and order.side.strip().lower() == "buy"
            and order.state.strip().lower()
            in active_entry_states
            for order in account_state.open_orders
        )

        if position is None and not entry_still_open:
            return {
                "confirmed": True,
                "flat": True,
                "result_code": "flat_confirmed",
                "failure_reason": None,
                "position_quantity": None,
                "stop_quantity": None,
            }

        def failure(
            code: str,
            reason: str,
            *,
            stop_quantity: Decimal | None = None,
        ) -> dict[str, Any]:
            return {
                "confirmed": False,
                "flat": False,
                "result_code": code,
                "failure_reason": reason,
                "position_quantity": (
                    position.quantity
                    if position is not None
                    else None
                ),
                "stop_quantity": stop_quantity,
            }

        if position is None:
            return failure(
                "broker_position_missing",
                "The broker position is missing while "
                "the linked entry order remains open.",
            )

        if entry_still_open:
            return failure(
                "entry_order_still_open",
                "The linked entry order still has an "
                "active broker remainder.",
            )

        if trade.filled_quantity <= 0:
            return failure(
                "invalid_local_fill_quantity",
                "Local filled quantity must be positive "
                "before protection can be confirmed.",
            )

        if (
            position.quantity <= 0
            or position.quantity
            != position.quantity.to_integral_value()
        ):
            return failure(
                "invalid_broker_position_quantity",
                "Broker position quantity must be a "
                "positive whole-share value.",
            )

        original_quantity = (
            trade.stop_original_position_quantity
            if trade.stop_original_position_quantity
            is not None
            else trade.filled_quantity
        )

        if position.quantity > Decimal(original_quantity):
            return failure(
                "position_exceeds_original_quantity",
                "Broker position exceeds the original "
                "covered position; state is inconsistent.",
            )

        expected_local_position = (
            trade.remaining_broker_position_quantity
            if trade.remaining_broker_position_quantity
            is not None
            else trade.filled_quantity
        )

        if (
            trade.status
            != TradeStatus.STOP_RECOVERY_REQUIRED
            and position.quantity
            != Decimal(expected_local_position)
        ):
            return failure(
                "position_quantity_mismatch",
                "Broker position quantity does not match "
                "the local filled quantity.",
            )

        if not trade.protective_stop_order_id:
            return failure(
                "missing_linked_stop",
                "No protective stop order is linked locally.",
            )

        linked_orders = [
            order
            for order in account_state.open_orders
            if order.order_id
            == trade.protective_stop_order_id
        ]

        if len(linked_orders) != 1:
            return failure(
                (
                    "linked_stop_missing"
                    if not linked_orders
                    else "duplicate_linked_stop"
                ),
                (
                    "The linked protective stop is not "
                    "present in the broker snapshot."
                    if not linked_orders
                    else "The broker snapshot contains "
                    "duplicate linked stop evidence."
                ),
            )

        stop = linked_orders[0]
        stop_quantity = stop.quantity

        checks = (
            (
                stop.symbol == trade.symbol,
                "stop_symbol_mismatch",
                "Linked stop symbol does not match the trade.",
            ),
            (
                stop.side.strip().lower() == "sell",
                "stop_side_mismatch",
                "Linked stop is not a sell order.",
            ),
            (
                stop.trigger.strip().lower() == "stop",
                "stop_trigger_mismatch",
                "Linked order is not stop-triggered.",
            ),
            (
                stop.order_type.strip().lower()
                == "stop_market",
                "stop_type_mismatch",
                "Linked stop is not a stop-market order.",
            ),
            (
                stop.state.strip().lower()
                in valid_stop_states,
                "stop_state_invalid",
                "Linked stop is not in a valid active "
                "broker state.",
            ),
            (
                stop.stop_price is not None
                and stop.stop_price > 0,
                "stop_price_invalid",
                "Linked stop does not have a valid "
                "positive stop price.",
            ),
            (
                stop.cumulative_quantity == 0,
                "stop_partially_filled",
                "A partially filled protective stop "
                "cannot prove active protection.",
            ),
            (
                stop.quantity == position.quantity,
                (
                    "stop_undersized"
                    if stop.quantity < position.quantity
                    else "stop_oversized"
                ),
                "Linked stop quantity must exactly cover "
                "the broker position.",
            ),
            (
                stop.placed_agent.strip().lower()
                == "agentic",
                "stop_not_agentic",
                "Linked stop was not placed by the "
                "agentic broker channel.",
            ),
        )

        for passed, code, reason in checks:
            if not passed:
                return failure(
                    code,
                    reason,
                    stop_quantity=stop_quantity,
                )

        conflicting_sells = [
            order
            for order in account_state.open_orders
            if (
                order.order_id != stop.order_id
                and order.symbol == trade.symbol
                and order.side.strip().lower() == "sell"
                and order.state.strip().lower()
                in active_entry_states
            )
        ]

        if conflicting_sells:
            return failure(
                "conflicting_sell_order",
                "Another active sell order conflicts "
                "with exact protective-stop proof.",
                stop_quantity=stop_quantity,
            )

        return {
            "confirmed": True,
            "flat": False,
            "result_code": "full_protection_confirmed",
            "failure_reason": None,
            "position_quantity": position.quantity,
            "stop_quantity": stop_quantity,
        }

    def _record_exit_reconciliation_event(
        self,
        *,
        reconciliation_id: str,
        trade: TradeRecord,
        new_status: TradeStatus,
        outcome: dict[str, Any],
        occurred_at: datetime,
    ) -> None:
        observation_key = f"reconciliation:{reconciliation_id}"
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO exit_recovery_events (
                    trade_id, observation_key, event_source,
                    broker_order_id, broker_state,
                    requested_quantity, executed_quantity,
                    executed_delta, remaining_quantity,
                    cumulative_average_price,
                    cumulative_proceeds, cumulative_fees,
                    cumulative_realized_pnl,
                    protection_result, reconciliation_id,
                    old_status, new_status, required_action,
                    failure_code, failure_reason,
                    occurred_at, details_json
                )
                VALUES (
                    ?, ?, 'broker_reconciliation', ?, NULL,
                    ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?
                )
                """,
                (
                    trade.trade_id,
                    observation_key,
                    trade.exit_order_id,
                    trade.exit_requested_quantity,
                    trade.exit_executed_quantity,
                    (
                        int(outcome["position_quantity"])
                        if outcome["position_quantity"] is not None
                        and outcome["position_quantity"] >= 0
                        and outcome["position_quantity"]
                        == outcome["position_quantity"].to_integral_value()
                        else None
                    ),
                    serialize_decimal(
                        trade.cumulative_average_exit_price
                    ),
                    serialize_decimal(
                        trade.cumulative_exit_proceeds
                    ),
                    serialize_decimal(
                        trade.cumulative_exit_fees
                    ),
                    serialize_decimal(
                        trade.cumulative_realized_pnl
                    ),
                    outcome["result_code"],
                    reconciliation_id,
                    trade.status.value,
                    new_status.value,
                    outcome["recovery_action"].value,
                    (
                        None
                        if outcome["confirmed"]
                        else outcome["result_code"]
                    ),
                    outcome["failure_reason"],
                    occurred_at.isoformat(),
                    json.dumps(
                        {
                            "reconciliation_id": reconciliation_id,
                            "confirmed": outcome["confirmed"],
                            "flat": outcome["flat"],
                            "result_code": outcome["result_code"],
                            "position_quantity": outcome[
                                "position_quantity"
                            ],
                            "stop_quantity": outcome[
                                "stop_quantity"
                            ],
                        },
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

    @staticmethod
    def _exit_reconciliation_outcome(
        trade: TradeRecord,
        account_state: Any,
    ) -> dict[str, Any]:
        active_states = {
            "new",
            "queued",
            "confirmed",
            "unconfirmed",
            "partially_filled",
            "pending_cancel",
            "pending_cancelled",
            "locating",
            "open",
            "submitted",
            "accepted",
        }
        valid_stop_states = {
            "new",
            "queued",
            "confirmed",
            "unconfirmed",
            "locating",
            "open",
            "submitted",
            "accepted",
        }
        position = next(
            (
                item
                for item in account_state.positions
                if item.symbol == trade.symbol
                and item.quantity != 0
            ),
            None,
        )
        entry_still_open = any(
            order.order_id == trade.entry_order_id
            and order.side.strip().lower() == "buy"
            and order.state.strip().lower() in active_states
            for order in account_state.open_orders
        )

        def result(
            *,
            confirmed: bool,
            flat: bool,
            code: str,
            reason: str | None,
            stop_quantity: Decimal | None = None,
            action: ExitRecoveryAction,
        ) -> dict[str, Any]:
            return {
                "confirmed": confirmed,
                "flat": flat,
                "result_code": code,
                "failure_reason": reason,
                "position_quantity": (
                    position.quantity
                    if position is not None
                    else None
                ),
                "stop_quantity": stop_quantity,
                "recovery_action": action,
            }

        if position is None and not entry_still_open:
            accounting_complete = (
                bool(trade.exit_close_reason)
                and trade.exit_intent_type is not None
                and bool(trade.exit_recovery_ref_id)
                and trade.cumulative_exited_quantity
                == trade.filled_quantity
                and trade.cumulative_average_exit_price
                is not None
                and trade.cumulative_average_exit_price > 0
                and trade.cumulative_exit_proceeds > 0
                and trade.exit_attempt_base_quantity
                + trade.exit_executed_quantity
                == trade.cumulative_exited_quantity
            )
            return result(
                confirmed=accounting_complete,
                flat=True,
                code=(
                    "exit_flat_accounting_complete"
                    if accounting_complete
                    else "exit_flat_accounting_incomplete"
                ),
                reason=(
                    None
                    if accounting_complete
                    else (
                        "Broker is flat, but durable exit quantity, "
                        "price, or intent accounting is incomplete."
                    )
                ),
                action=(
                    ExitRecoveryAction.NONE
                    if accounting_complete
                    else ExitRecoveryAction.OPERATOR_INTERVENTION
                ),
            )

        if position is None:
            return result(
                confirmed=False,
                flat=False,
                code="exit_entry_order_still_open",
                reason=(
                    "The linked entry order remains active while no "
                    "broker position is present."
                ),
                action=ExitRecoveryAction.OPERATOR_INTERVENTION,
            )

        if entry_still_open:
            return result(
                confirmed=False,
                flat=False,
                code="exit_entry_order_still_open",
                reason=(
                    "The linked entry order has an active broker "
                    "remainder during exit recovery."
                ),
                action=ExitRecoveryAction.OPERATOR_INTERVENTION,
            )

        if (
            position.quantity <= 0
            or position.quantity
            != position.quantity.to_integral_value()
        ):
            return result(
                confirmed=False,
                flat=False,
                code="invalid_exit_position_quantity",
                reason=(
                    "Exit-recovery broker position must be a positive "
                    "whole-share quantity."
                ),
                action=ExitRecoveryAction.OPERATOR_INTERVENTION,
            )

        expected_remaining = (
            trade.filled_quantity
            - trade.cumulative_exited_quantity
        )
        if (
            expected_remaining <= 0
            or position.quantity != Decimal(expected_remaining)
        ):
            return result(
                confirmed=False,
                flat=False,
                code="exit_position_quantity_mismatch",
                reason=(
                    "Broker position does not equal the durable "
                    "remaining exit quantity."
                ),
                action=ExitRecoveryAction.OPERATOR_INTERVENTION,
            )

        active_exit = any(
            order.order_id == trade.exit_order_id
            and order.side.strip().lower() == "sell"
            and order.state.strip().lower() in active_states
            for order in account_state.open_orders
        )
        if active_exit:
            return result(
                confirmed=False,
                flat=False,
                code="exit_order_still_active",
                reason=(
                    "The linked exit remains active and conflicts with "
                    "proof of exact remaining protection."
                ),
                action=ExitRecoveryAction.RECONCILE_BROKER_STATE,
            )

        if not trade.protective_stop_order_id:
            return result(
                confirmed=False,
                flat=False,
                code="exit_remaining_stop_missing",
                reason=(
                    "No protective stop is linked for the remaining "
                    "broker position."
                ),
                action=ExitRecoveryAction.PROTECT_REMAINING_POSITION,
            )

        linked = [
            order
            for order in account_state.open_orders
            if order.order_id == trade.protective_stop_order_id
        ]
        if len(linked) != 1:
            return result(
                confirmed=False,
                flat=False,
                code=(
                    "exit_remaining_stop_missing"
                    if not linked
                    else "exit_duplicate_linked_stop"
                ),
                reason=(
                    "Fresh snapshot does not contain exactly one "
                    "linked protective stop."
                ),
                action=ExitRecoveryAction.PROTECT_REMAINING_POSITION,
            )

        stop = linked[0]
        checks = (
            (
                stop.symbol == trade.symbol,
                "exit_stop_symbol_mismatch",
            ),
            (
                stop.side.strip().lower() == "sell",
                "exit_stop_side_mismatch",
            ),
            (
                stop.trigger.strip().lower() == "stop",
                "exit_stop_trigger_mismatch",
            ),
            (
                stop.order_type.strip().lower() == "stop_market",
                "exit_stop_type_mismatch",
            ),
            (
                stop.state.strip().lower() in valid_stop_states,
                "exit_stop_state_invalid",
            ),
            (
                stop.stop_price is not None and stop.stop_price > 0,
                "exit_stop_price_invalid",
            ),
            (
                stop.cumulative_quantity == 0,
                "exit_stop_has_execution",
            ),
            (
                stop.quantity == position.quantity,
                "exit_stop_quantity_mismatch",
            ),
            (
                stop.placed_agent.strip().lower() == "agentic",
                "exit_stop_not_agentic",
            ),
        )
        for passed, code in checks:
            if not passed:
                return result(
                    confirmed=False,
                    flat=False,
                    code=code,
                    reason=(
                        "The linked protective stop cannot prove exact "
                        "active coverage of the remaining position."
                    ),
                    stop_quantity=stop.quantity,
                    action=(
                        ExitRecoveryAction
                        .PROTECT_REMAINING_POSITION
                    ),
                )

        conflicting_sells = [
            order
            for order in account_state.open_orders
            if order.order_id != stop.order_id
            and order.symbol == trade.symbol
            and order.side.strip().lower() == "sell"
            and order.state.strip().lower() in active_states
        ]
        if conflicting_sells:
            return result(
                confirmed=False,
                flat=False,
                code="exit_conflicting_sell_order",
                reason=(
                    "Another active sell order conflicts with exact "
                    "remaining protection."
                ),
                stop_quantity=stop.quantity,
                action=ExitRecoveryAction.OPERATOR_INTERVENTION,
            )

        return result(
            confirmed=True,
            flat=False,
            code="exit_remaining_protection_confirmed",
            reason=None,
            stop_quantity=stop.quantity,
            action=ExitRecoveryAction.NONE,
        )
    def apply_startup_reconciliation(
        self,
        *,
        strategy: str,
        snapshot: BrokerSnapshot,
        occurred_at: datetime | None = None,
        maximum_snapshot_age: timedelta = (
            DEFAULT_MAXIMUM_SNAPSHOT_AGE
        ),
    ) -> tuple[ReconciliationIssue, ...]:
        strategy = strategy.strip()
        timestamp = occurred_at or utc_now()

        if not strategy:
            raise TradeStateError(
                "Strategy name is required."
            )

        if not isinstance(snapshot, BrokerSnapshot):
            self.begin_startup_reconciliation(
                strategy,
                occurred_at=timestamp,
            )
            raise TradeStateError(
                "A BrokerSnapshot with durable proof "
                "metadata is required for startup "
                "reconciliation."
            )

        validation = validate_snapshot(
            snapshot,
            evaluated_at=timestamp,
            maximum_age=maximum_snapshot_age,
        )
        metadata = snapshot.metadata
        existing = self._existing_reconciliation_run(
            metadata.reconciliation_id
        )

        if existing is not None:
            if (
                existing["strategy"] != strategy
                or existing["snapshot_digest"]
                != validation.snapshot_digest
                or existing["account_fingerprint"]
                != metadata.account_fingerprint
            ):
                self.begin_startup_reconciliation(
                    strategy,
                    occurred_at=timestamp,
                )
                raise TradeStateError(
                    "A reconciliation ID was reused with "
                    "different broker evidence; trading "
                    "remains halted."
                )

            return self.startup_reconciliation_issues(
                strategy=strategy,
                account_state=snapshot.account_state,
            )

        self.begin_startup_reconciliation(
            strategy,
            occurred_at=timestamp,
        )

        with self._connect() as connection:
            halted_rows = connection.execute(
                """
                SELECT trades.*
                FROM trades
                JOIN entry_halts
                  ON entry_halts.trade_id
                     = trades.trade_id
                WHERE entry_halts.strategy = ?
                  AND entry_halts.active = 1
                  AND entry_halts.trade_id IS NOT NULL
                ORDER BY trades.created_at,
                         trades.trade_id
                """,
                (strategy,),
            ).fetchall()

        halted_trades = tuple(
            self._row_to_trade(row)
            for row in halted_rows
        )

        def exit_proof_required(trade: TradeRecord) -> bool:
            return (
                trade.status in {
                    TradeStatus.EXIT_PENDING,
                    TradeStatus.EXIT_PARTIALLY_FILLED,
                    TradeStatus.EXIT_RECOVERY_REQUIRED,
                }
                or (
                    bool(trade.exit_recovery_ref_id)
                    and trade.cumulative_exited_quantity > 0
                    and trade.status in {
                        TradeStatus.STOP_LINKED,
                        TradeStatus.OPEN_PROTECTED,
                    }
                )
            )
        if not validation.valid:
            self._record_reconciliation_run(
                strategy=strategy,
                snapshot=snapshot,
                validation=validation,
                safety_confirmed=False,
                result_code=validation.result_code,
                failure_reason=(
                    validation.failure_reason
                ),
                occurred_at=timestamp,
            )

            for trade in halted_trades:
                if exit_proof_required(trade):
                    invalid_outcome = {
                        "confirmed": False,
                        "flat": False,
                        "result_code": validation.result_code,
                        "failure_reason": validation.failure_reason,
                        "position_quantity": None,
                        "stop_quantity": None,
                        "recovery_action": (
                            ExitRecoveryAction
                            .RECONCILE_BROKER_STATE
                        ),
                    }
                    self._record_exit_reconciliation_event(
                        reconciliation_id=(
                            metadata.reconciliation_id
                        ),
                        trade=trade,
                        new_status=trade.status,
                        outcome=invalid_outcome,
                        occurred_at=timestamp,
                    )
                else:
                    self._record_protection_reconciliation_event(
                        reconciliation_id=(
                            metadata.reconciliation_id
                        ),
                        trade=trade,
                        new_status=trade.status,
                        confirmed=False,
                        result_code=validation.result_code,
                        failure_reason=(
                            validation.failure_reason
                        ),
                        position_quantity=None,
                        stop_quantity=None,
                        occurred_at=timestamp,
                    )
            issues = list(
                self.startup_reconciliation_issues(
                    strategy=strategy,
                    account_state=snapshot.account_state,
                )
            )
            issues.append(
                ReconciliationIssue(
                    severity="critical",
                    code=validation.result_code,
                    message=(
                        validation.failure_reason
                        or "Broker snapshot is invalid."
                    ),
                )
            )
            return tuple(issues)

        outcomes = tuple(
            (
                trade,
                (
                    self._exit_reconciliation_outcome(
                        trade,
                        snapshot.account_state,
                    )
                    if exit_proof_required(trade)
                    else self._protection_reconciliation_outcome(
                        trade,
                        snapshot.account_state,
                    )
                ),
            )
            for trade in halted_trades
        )
        safety_confirmed = all(
            outcome["confirmed"]
            for _, outcome in outcomes
        )

        self._record_reconciliation_run(
            strategy=strategy,
            snapshot=snapshot,
            validation=validation,
            safety_confirmed=safety_confirmed,
            result_code=(
                "safety_confirmed"
                if safety_confirmed
                else "safety_unresolved"
            ),
            failure_reason=(
                None
                if safety_confirmed
                else (
                    "At least one halted trade was not "
                    "proved flat or fully protected."
                )
            ),
            occurred_at=timestamp,
        )

        for trade, outcome in outcomes:
            new_status = trade.status

            if exit_proof_required(trade):
                if outcome["confirmed"] and outcome["flat"]:
                    proved = self._transition(
                        trade.trade_id,
                        new_status=trade.status,
                        event_type="exit_flat_proved",
                        updates={
                            "exit_remaining_position_quantity": 0,
                            "last_exit_reconciliation_id":
                                metadata.reconciliation_id,
                            "required_exit_recovery_action":
                                ExitRecoveryAction.NONE.value,
                            "last_completed_exit_recovery_step":
                                ExitRecoveryStep.FLAT_CONFIRMED.value,
                            "exit_recovery_failure_reason": None,
                            "exit_operator_ack_required": 0,
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                            "proof": (
                                "Fresh complete broker snapshot proved "
                                "flat with complete durable accounting."
                            ),
                        },
                        occurred_at=timestamp,
                    )
                    assert proved.cumulative_average_exit_price is not None
                    assert proved.exit_close_reason is not None
                    closed = self.close_trade(
                        trade.trade_id,
                        average_exit_price=(
                            proved.cumulative_average_exit_price
                        ),
                        reason=proved.exit_close_reason,
                        occurred_at=timestamp,
                    )
                    new_status = closed.status
                    with self._connect() as connection:
                        self._clear_entry_halt(
                            connection,
                            halt_key=f"trade:{trade.trade_id}",
                            occurred_at=timestamp,
                        )

                elif outcome["confirmed"]:
                    position_quantity = int(
                        outcome["position_quantity"]
                    )
                    linked = self._transition(
                        trade.trade_id,
                        new_status=TradeStatus.STOP_LINKED,
                        event_type=(
                            "exit_remaining_stop_linked_for_"
                            "reconciliation"
                        ),
                        updates={
                            "protected_quantity": 0,
                            "remaining_broker_position_quantity":
                                position_quantity,
                            "exit_remaining_position_quantity":
                                position_quantity,
                            "last_exit_reconciliation_id":
                                metadata.reconciliation_id,
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                        },
                        occurred_at=timestamp,
                    )
                    protected = self._transition(
                        linked.trade_id,
                        new_status=TradeStatus.OPEN_PROTECTED,
                        event_type=(
                            "exit_remaining_protection_confirmed"
                        ),
                        updates={
                            "protected_quantity": position_quantity,
                            "remaining_broker_position_quantity":
                                position_quantity,
                            "exit_remaining_position_quantity":
                                position_quantity,
                            "required_exit_recovery_action":
                                ExitRecoveryAction.NONE.value,
                            "last_completed_exit_recovery_step":
                                ExitRecoveryStep
                                .PROTECTION_CONFIRMED.value,
                            "exit_recovery_failure_reason": None,
                            "exit_operator_ack_required": 0,
                            "last_exit_reconciliation_id":
                                metadata.reconciliation_id,
                            "required_recovery_action":
                                EntryRecoveryAction.NONE.value,
                            "last_completed_recovery_step":
                                EntryRecoveryStep
                                .PROTECTION_CONFIRMED.value,
                            "recovery_failure_reason": None,
                            "operator_ack_required": 0,
                            "last_protection_reconciliation_id":
                                metadata.reconciliation_id,
                            "protection_confirmed_at": timestamp,
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                            "protected_quantity": position_quantity,
                        },
                        occurred_at=timestamp,
                    )
                    new_status = protected.status
                    with self._connect() as connection:
                        self._clear_entry_halt(
                            connection,
                            halt_key=f"trade:{trade.trade_id}",
                            occurred_at=timestamp,
                        )

                else:
                    broker_quantity = outcome["position_quantity"]
                    persisted_quantity = None
                    if (
                        broker_quantity is not None
                        and broker_quantity >= 0
                        and broker_quantity
                        == broker_quantity.to_integral_value()
                    ):
                        persisted_quantity = int(broker_quantity)

                    unresolved = self._transition(
                        trade.trade_id,
                        new_status=(
                            TradeStatus.EXIT_RECOVERY_REQUIRED
                        ),
                        event_type="exit_recovery_unresolved",
                        updates={
                            "exit_remaining_position_quantity":
                                persisted_quantity,
                            "last_exit_reconciliation_id":
                                metadata.reconciliation_id,
                            "required_exit_recovery_action":
                                outcome["recovery_action"].value,
                            "exit_recovery_failure_reason":
                                outcome["failure_reason"],
                            "exit_operator_ack_required": int(
                                trade.exit_operator_ack_required
                                or outcome["recovery_action"]
                                == ExitRecoveryAction
                                .OPERATOR_INTERVENTION
                            ),
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                            "result_code": outcome["result_code"],
                            "failure_reason":
                                outcome["failure_reason"],
                        },
                        occurred_at=timestamp,
                    )
                    new_status = unresolved.status

                self._record_exit_reconciliation_event(
                    reconciliation_id=metadata.reconciliation_id,
                    trade=trade,
                    new_status=new_status,
                    outcome=outcome,
                    occurred_at=timestamp,
                )
                continue
            if outcome["confirmed"] and outcome["flat"]:
                full_stop_close = (
                    trade.status
                    == TradeStatus.STOP_RECOVERY_REQUIRED
                    and trade.last_completed_stop_recovery_step
                    == StopRecoveryStep.FULL_FILL_RECORDED.value
                    and trade.stop_original_position_quantity
                    is not None
                    and trade.stop_executed_quantity
                    == trade.stop_original_position_quantity
                    and trade.average_stop_fill_price
                    is not None
                )

                if full_stop_close:
                    self._transition(
                        trade.trade_id,
                        new_status=(
                            TradeStatus.STOP_RECOVERY_REQUIRED
                        ),
                        event_type=(
                            "protective_stop_flat_proved"
                        ),
                        updates={
                            "remaining_broker_position_quantity": 0,
                            "last_stop_reconciliation_id":
                                metadata.reconciliation_id,
                            "last_completed_stop_recovery_step":
                                StopRecoveryStep
                                .FLAT_CONFIRMED.value,
                            "stop_recovery_failure_reason":
                                None,
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                            "proof": (
                                "Fresh complete broker "
                                "snapshot proved flat."
                            ),
                        },
                        occurred_at=timestamp,
                    )
                    updated = self.close_trade(
                        trade.trade_id,
                        average_exit_price=(
                            trade.average_stop_fill_price
                        ),
                        reason="protective_stop",
                        occurred_at=timestamp,
                    )
                    new_status = updated.status

                elif trade.status in {
                    TradeStatus.ENTRY_PARTIALLY_FILLED,
                    TradeStatus.PROTECTION_PENDING,
                    TradeStatus.RECOVERY_REQUIRED,
                    TradeStatus.OPEN_UNPROTECTED,
                    TradeStatus.STOP_LINKED,
                    TradeStatus.OPEN_PROTECTED,
                    TradeStatus.STOP_RECOVERY_REQUIRED,
                }:
                    updates = {
                        "protected_quantity": 0,
                        "required_recovery_action":
                            EntryRecoveryAction.NONE.value,
                        "last_completed_recovery_step":
                            EntryRecoveryStep
                            .FLAT_CONFIRMED.value,
                        "operator_ack_required": 0,
                        "last_protection_reconciliation_id":
                            metadata.reconciliation_id,
                        "protection_confirmed_at":
                            timestamp,
                    }

                    if (
                        trade.status
                        == TradeStatus.STOP_RECOVERY_REQUIRED
                    ):
                        updates.update(
                            {
                                "remaining_broker_position_quantity": 0,
                                "required_stop_recovery_action":
                                    StopRecoveryAction
                                    .NONE.value,
                                "last_completed_stop_recovery_step":
                                    StopRecoveryStep
                                    .FLAT_CONFIRMED.value,
                                "stop_recovery_failure_reason":
                                    None,
                                "stop_operator_ack_required":
                                    0,
                                "last_stop_reconciliation_id":
                                    metadata.reconciliation_id,
                            }
                        )

                    updated = self._transition(
                        trade.trade_id,
                        new_status=TradeStatus.ERROR,
                        event_type=(
                            "recovery_flat_confirmed"
                        ),
                        updates=updates,
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                            "proof": (
                                "No broker position and no "
                                "open linked entry order."
                            ),
                        },
                        occurred_at=timestamp,
                    )
                    new_status = updated.status

                with self._connect() as connection:
                    self._clear_entry_halt(
                        connection,
                        halt_key=f"trade:{trade.trade_id}",
                        occurred_at=timestamp,
                    )

            elif outcome["confirmed"]:
                position_quantity = int(
                    outcome["position_quantity"]
                )
                linked = trade

                if trade.status != TradeStatus.STOP_LINKED:
                    linked = self._transition(
                        trade.trade_id,
                        new_status=TradeStatus.STOP_LINKED,
                        event_type=(
                            "protective_stop_linked_for_"
                            "reconciliation"
                        ),
                        updates={
                            "protected_quantity": 0,
                            "remaining_broker_position_quantity":
                                position_quantity,
                            "last_stop_reconciliation_id":
                                metadata.reconciliation_id,
                        },
                        details={
                            "reconciliation_id":
                                metadata.reconciliation_id,
                        },
                        occurred_at=timestamp,
                    )

                updated = self._transition(
                    linked.trade_id,
                    new_status=TradeStatus.OPEN_PROTECTED,
                    event_type=(
                        "broker_snapshot_protection_"
                        "confirmed"
                    ),
                    updates={
                        "protected_quantity":
                            position_quantity,
                        "remaining_broker_position_quantity":
                            position_quantity,
                        "required_recovery_action":
                            EntryRecoveryAction.NONE.value,
                        "last_completed_recovery_step":
                            EntryRecoveryStep
                            .PROTECTION_CONFIRMED.value,
                        "recovery_failure_reason": None,
                        "operator_ack_required": 0,
                        "last_protection_reconciliation_id":
                            metadata.reconciliation_id,
                        "protection_confirmed_at":
                            timestamp,
                        "required_stop_recovery_action":
                            StopRecoveryAction.NONE.value,
                        "last_completed_stop_recovery_step":
                            StopRecoveryStep
                            .PROTECTION_CONFIRMED.value,
                        "stop_recovery_failure_reason":
                            None,
                        "stop_operator_ack_required": 0,
                        "last_stop_reconciliation_id":
                            metadata.reconciliation_id,
                        "exit_remaining_position_quantity": (
                            position_quantity
                            if trade.exit_recovery_ref_id
                            else trade.exit_remaining_position_quantity
                        ),
                        "required_exit_recovery_action": (
                            ExitRecoveryAction.NONE.value
                            if trade.exit_recovery_ref_id
                            else trade.required_exit_recovery_action.value
                        ),
                        "last_completed_exit_recovery_step": (
                            ExitRecoveryStep
                            .PROTECTION_CONFIRMED.value
                            if trade.exit_recovery_ref_id
                            else trade
                            .last_completed_exit_recovery_step
                        ),
                        "exit_recovery_failure_reason": (
                            None
                            if trade.exit_recovery_ref_id
                            else trade.exit_recovery_failure_reason
                        ),
                        "exit_operator_ack_required": (
                            0
                            if trade.exit_recovery_ref_id
                            else int(
                                trade.exit_operator_ack_required
                            )
                        ),
                        "last_exit_reconciliation_id": (
                            metadata.reconciliation_id
                            if trade.exit_recovery_ref_id
                            else trade.last_exit_reconciliation_id
                        ),
                    },
                    details={
                        "reconciliation_id":
                            metadata.reconciliation_id,
                        "protected_quantity":
                            position_quantity,
                    },
                    occurred_at=timestamp,
                )
                new_status = updated.status

                with self._connect() as connection:
                    self._clear_entry_halt(
                        connection,
                        halt_key=f"trade:{trade.trade_id}",
                        occurred_at=timestamp,
                    )

            elif (
                trade.status
                == TradeStatus.STOP_RECOVERY_REQUIRED
            ):
                broker_quantity = outcome[
                    "position_quantity"
                ]
                persisted_quantity = None

                if (
                    broker_quantity is not None
                    and broker_quantity >= 0
                    and broker_quantity
                    == broker_quantity.to_integral_value()
                ):
                    persisted_quantity = int(
                        broker_quantity
                    )

                updated = self._transition(
                    trade.trade_id,
                    new_status=(
                        TradeStatus.STOP_RECOVERY_REQUIRED
                    ),
                    event_type=(
                        "protective_stop_recovery_unresolved"
                    ),
                    updates={
                        "remaining_broker_position_quantity":
                            persisted_quantity,
                        "last_stop_reconciliation_id":
                            metadata.reconciliation_id,
                        "stop_recovery_failure_reason":
                            outcome["failure_reason"],
                        "stop_operator_ack_required":
                            int(
                                trade.stop_operator_ack_required
                                or outcome["result_code"]
                                in {
                                    "position_exceeds_original_quantity",
                                    "stop_oversized",
                                    "invalid_broker_position_quantity",
                                }
                            ),
                    },
                    details={
                        "reconciliation_id":
                            metadata.reconciliation_id,
                        "result_code":
                            outcome["result_code"],
                        "failure_reason":
                            outcome["failure_reason"],
                    },
                    occurred_at=timestamp,
                )
                new_status = updated.status

            self._record_protection_reconciliation_event(
                reconciliation_id=(
                    metadata.reconciliation_id
                ),
                trade=trade,
                new_status=new_status,
                confirmed=outcome["confirmed"],
                result_code=outcome["result_code"],
                failure_reason=(
                    outcome["failure_reason"]
                ),
                position_quantity=(
                    outcome["position_quantity"]
                ),
                stop_quantity=(
                    outcome["stop_quantity"]
                ),
                occurred_at=timestamp,
            )

        issues = self.startup_reconciliation_issues(
            strategy=strategy,
            account_state=snapshot.account_state,
        )

        with self._connect() as connection:
            unresolved_trade_halt = (
                connection.execute(
                    """
                    SELECT 1
                    FROM entry_halts
                    WHERE strategy = ?
                      AND active = 1
                      AND halt_key LIKE 'trade:%'
                    LIMIT 1
                    """,
                    (strategy,),
                ).fetchone()
            )
            critical_issue = any(
                issue.severity == "critical"
                for issue in issues
            )

            if (
                unresolved_trade_halt is None
                and not critical_issue
            ):
                self._clear_entry_halt(
                    connection,
                    halt_key=f"startup:{strategy}",
                    occurred_at=timestamp,
                )
            else:
                self._activate_entry_halt(
                    connection,
                    halt_key=f"startup:{strategy}",
                    strategy=strategy,
                    trade_id=None,
                    reason=(
                        "Startup reconciliation did not "
                        "prove all positions flat or "
                        "fully protected."
                    ),
                    operator_ack_required=critical_issue,
                    occurred_at=timestamp,
                )

        return issues
    def reconcile(
        self,
        account_state: Any,
    ) -> tuple[ReconciliationIssue, ...]:
        issues: list[ReconciliationIssue] = []
        active_trades = self.list_active_trades()

        broker_positions = {
            position.symbol: position
            for position in account_state.positions
            if position.quantity != 0
        }

        broker_orders = {
            order.order_id: order
            for order in account_state.open_orders
        }

        local_symbols_with_positions = {
            trade.symbol
            for trade in active_trades
            if (
                trade.filled_quantity > 0
                and trade.status in {
                    TradeStatus
                    .ENTRY_PARTIALLY_FILLED,
                    TradeStatus
                    .PROTECTION_PENDING,
                    TradeStatus.RECOVERY_REQUIRED,
                    TradeStatus.OPEN_UNPROTECTED,
                    TradeStatus.STOP_LINKED,
                    TradeStatus.OPEN_PROTECTED,
                    TradeStatus.STOP_RECOVERY_REQUIRED,
                    TradeStatus.EXIT_PENDING,
                    TradeStatus.EXIT_PARTIALLY_FILLED,
                    TradeStatus.EXIT_RECOVERY_REQUIRED,
                }
            )
        }

        linked_entry_order_ids = {
            trade.entry_order_id
            for trade in active_trades
            if trade.entry_order_id
        }

        for trade in active_trades:
            if trade.status in {
                TradeStatus.ENTRY_PARTIALLY_FILLED,
                TradeStatus.PROTECTION_PENDING,
                TradeStatus.RECOVERY_REQUIRED,
                TradeStatus.STOP_LINKED,
                TradeStatus.STOP_RECOVERY_REQUIRED,
                TradeStatus.EXIT_PENDING,
                TradeStatus.EXIT_PARTIALLY_FILLED,
                TradeStatus.EXIT_RECOVERY_REQUIRED,
            }:
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code="entry_recovery_pending",
                        message=(
                            "Entry fill recovery or "
                            "broker-held protection "
                            "remains unresolved."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

            if trade.status in {
                TradeStatus.OPEN_UNPROTECTED,
                TradeStatus.STOP_LINKED,
                TradeStatus.STOP_RECOVERY_REQUIRED,
                TradeStatus.EXIT_PENDING,
                TradeStatus.EXIT_PARTIALLY_FILLED,
                TradeStatus.EXIT_RECOVERY_REQUIRED,
            }:
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code=(
                            "linked_stop_unconfirmed"
                            if trade.status
                            == TradeStatus.STOP_LINKED
                            else "open_unprotected"
                        ),
                        message=(
                            "Local trade is open without "
                            "fresh complete broker proof "
                            "of a protective stop."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

            if (
                trade.status
                == TradeStatus.OPEN_PROTECTED
            ):
                stop_id = (
                    trade.protective_stop_order_id
                )

                if (
                    not trade
                    .last_protection_reconciliation_id
                    or trade.protection_confirmed_at
                    is None
                ):
                    issues.append(
                        ReconciliationIssue(
                            severity="critical",
                            code="protection_proof_missing",
                            message=(
                                "Protected local trade has "
                                "no durable broker snapshot "
                                "proof."
                            ),
                            symbol=trade.symbol,
                            trade_id=trade.trade_id,
                        )
                    )

                if not stop_id:
                    issues.append(
                        ReconciliationIssue(
                            severity="critical",
                            code=(
                                "missing_stop_id"
                            ),
                            message=(
                                "Protected local trade has "
                                "no protective stop ID."
                            ),
                            symbol=trade.symbol,
                            trade_id=trade.trade_id,
                        )
                    )
                elif stop_id not in broker_orders:
                    issues.append(
                        ReconciliationIssue(
                            severity="critical",
                            code=(
                                "stop_not_open_at_broker"
                            ),
                            message=(
                                "Local protective stop is "
                                "not present in broker "
                                "open orders."
                            ),
                            symbol=trade.symbol,
                            trade_id=trade.trade_id,
                        )
                    )

            position = broker_positions.get(
                trade.symbol
            )

            expected_position_quantity = (
                trade.exit_remaining_position_quantity
                if trade.exit_remaining_position_quantity
                is not None
                else (
                    trade.remaining_broker_position_quantity
                    if trade.remaining_broker_position_quantity
                    is not None
                    else trade.filled_quantity
                )
            )

            if (
                trade.filled_quantity > 0
                and position is not None
                and position.quantity
                != Decimal(expected_position_quantity)
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code="position_quantity_mismatch",
                        message=(
                            "Broker position quantity "
                            "does not match the locally "
                            "observed filled quantity."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

            if (
                trade.status
                == TradeStatus.OPEN_PROTECTED
                and trade.protected_quantity
                != expected_position_quantity
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code=(
                            "protected_quantity_mismatch"
                        ),
                        message=(
                            "Locally confirmed protected "
                            "quantity does not equal the "
                            "open filled quantity."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

            if (
                trade.status
                == TradeStatus.OPEN_PROTECTED
                and trade.protective_stop_order_id
                in broker_orders
            ):
                broker_stop = broker_orders[
                    trade.protective_stop_order_id
                ]

                if (
                    broker_stop.quantity
                    != Decimal(
                        trade.protected_quantity
                    )
                ):
                    issues.append(
                        ReconciliationIssue(
                            severity="critical",
                            code=(
                                "broker_stop_quantity_"
                                "mismatch"
                            ),
                            message=(
                                "Broker protective-stop "
                                "quantity does not equal "
                                "the locally protected "
                                "quantity."
                            ),
                            symbol=trade.symbol,
                            trade_id=trade.trade_id,
                        )
                    )

            if (
                trade.filled_quantity > 0
                and trade.status
                in {
                    TradeStatus
                    .ENTRY_PARTIALLY_FILLED,
                    TradeStatus
                    .PROTECTION_PENDING,
                    TradeStatus.RECOVERY_REQUIRED,
                    TradeStatus.OPEN_UNPROTECTED,
                    TradeStatus.STOP_LINKED,
                    TradeStatus.OPEN_PROTECTED,
                    TradeStatus.STOP_RECOVERY_REQUIRED,
                    TradeStatus.EXIT_PENDING,
                    TradeStatus.EXIT_PARTIALLY_FILLED,
                    TradeStatus.EXIT_RECOVERY_REQUIRED,
                }
                and trade.symbol
                not in broker_positions
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code=(
                            "local_position_missing"
                        ),
                        message=(
                            "Local state shows an open "
                            "trade but the broker reports "
                            "no matching position."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

            if (
                trade.status
                == TradeStatus.ENTRY_PENDING
                and trade.entry_order_id
                and trade.entry_order_id
                not in broker_orders
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code=(
                            "entry_order_not_open"
                        ),
                        message=(
                            "Pending local entry order is "
                            "not present in broker open "
                            "orders and requires status "
                            "verification."
                        ),
                        symbol=trade.symbol,
                        trade_id=trade.trade_id,
                    )
                )

        for symbol in broker_positions:
            if symbol not in (
                local_symbols_with_positions
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code="untracked_position",
                        message=(
                            "Broker reports a position "
                            "not represented by an active "
                            "local trade."
                        ),
                        symbol=symbol,
                    )
                )

        for order in account_state.open_orders:
            if (
                order.side == "buy"
                and order.order_id
                not in linked_entry_order_ids
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code=(
                            "untracked_entry_order"
                        ),
                        message=(
                            "Broker reports an open buy "
                            "order not linked to an active "
                            "local trade."
                        ),
                        symbol=order.symbol,
                    )
                )

        return tuple(issues)

    def count_trade_events(
        self,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM trade_events
                """
            ).fetchone()

        return int(row["count_value"])

    def count_order_events(
        self,
    ) -> int:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count_value
                FROM order_events
                """
            ).fetchone()

        return int(row["count_value"])


def main() -> None:
    store = TradeStateStore(DEFAULT_DB_PATH)

    print("\nTRADE-STATE LEDGER\n")
    print(f"Database: {store.db_path}")
    print(
        f"Active trades: "
        f"{len(store.list_active_trades())}"
    )
    print(
        f"Trade events: "
        f"{store.count_trade_events()}"
    )
    print(
        f"Order events: "
        f"{store.count_order_events()}"
    )
    print(
        "Broker write capability: False"
    )


if __name__ == "__main__":
    main()
