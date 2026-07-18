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
    EXIT_PENDING = "exit_pending"
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
    TradeStatus.EXIT_PENDING,
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
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.OPEN_PROTECTED: {
        TradeStatus.STOP_LINKED,
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.EXIT_PENDING: {
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.OPEN_PROTECTED,
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
        return max(
            0,
            self.filled_quantity
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
                  AND status IN (?, ?)
                ORDER BY created_at, trade_id
                """,
                (
                    strategy,
                    TradeStatus.STOP_LINKED.value,
                    TradeStatus.OPEN_PROTECTED.value,
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
            new_status=TradeStatus.STOP_LINKED,
            event_type="protective_stop_linked",
            updates={
                "protective_stop_order_id":
                    broker_order_id,
                "protected_quantity": 0,
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
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        if not broker_order_id.strip():
            raise TradeStateError(
                "Exit order ID is required."
            )

        return self._transition(
            trade_id,
            new_status=TradeStatus.EXIT_PENDING,
            event_type="exit_order_attached",
            updates={
                "exit_order_id":
                    broker_order_id,
            },
            details={
                "broker_order_id":
                    broker_order_id,
            },
            occurred_at=occurred_at,
        )

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
            WHERE status = ?
              AND session_date >= ?
              AND session_date <= ?
        """

        parameters: list[Any] = [
            TradeStatus.CLOSED.value,
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
            WHERE status = ?
        """

        parameters: list[Any] = [
            TradeStatus.CLOSED.value
        ]

        if strategy:
            query += " AND strategy = ?"
            parameters.append(strategy)

        query += """
            ORDER BY closed_at, trade_id
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
                "The broker position is missing while the linked entry order remains open.",
            )

        if entry_still_open:
            return failure(
                "entry_order_still_open",
                "The linked entry order still has an active broker remainder.",
            )

        if trade.filled_quantity <= 0:
            return failure(
                "invalid_local_fill_quantity",
                "Local filled quantity must be positive before protection can be confirmed.",
            )

        if position.quantity != Decimal(
            trade.filled_quantity
        ):
            return failure(
                "position_quantity_mismatch",
                "Broker position quantity does not match the local filled quantity.",
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
                    "The linked protective stop is not present in the broker snapshot."
                    if not linked_orders
                    else "The broker snapshot contains duplicate linked stop evidence."
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
                "Linked stop is not in a valid active broker state.",
            ),
            (
                stop.stop_price is not None
                and stop.stop_price > 0,
                "stop_price_invalid",
                "Linked stop does not have a valid positive stop price.",
            ),
            (
                stop.cumulative_quantity == 0,
                "stop_partially_filled",
                "A partially filled protective stop cannot prove full active protection.",
            ),
            (
                stop.quantity == position.quantity,
                "stop_quantity_mismatch",
                "Linked stop quantity does not cover the full broker position.",
            ),
            (
                stop.placed_agent.strip().lower()
                == "agentic",
                "stop_not_agentic",
                "Linked stop was not placed by the agentic broker channel.",
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
                and order.side.strip().lower()
                == "sell"
                and order.state.strip().lower()
                in active_entry_states
            )
        ]

        if conflicting_sells:
            return failure(
                "conflicting_sell_order",
                "Another active sell order conflicts with exact protective-stop proof.",
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
                self._protection_reconciliation_outcome(
                    trade,
                    snapshot.account_state,
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

            if (
                outcome["confirmed"]
                and outcome["flat"]
            ):
                if trade.status in {
                    TradeStatus.ENTRY_PARTIALLY_FILLED,
                    TradeStatus.PROTECTION_PENDING,
                    TradeStatus.RECOVERY_REQUIRED,
                    TradeStatus.OPEN_UNPROTECTED,
                    TradeStatus.STOP_LINKED,
                    TradeStatus.OPEN_PROTECTED,
                }:
                    updated = self._transition(
                        trade.trade_id,
                        new_status=TradeStatus.ERROR,
                        event_type=(
                            "entry_recovery_flat_confirmed"
                        ),
                        updates={
                            "protected_quantity": 0,
                            "required_recovery_action":
                                EntryRecoveryAction
                                .NONE.value,
                            "last_completed_recovery_step":
                                EntryRecoveryStep
                                .FLAT_CONFIRMED.value,
                            "operator_ack_required": 0,
                            "last_protection_reconciliation_id":
                                metadata.reconciliation_id,
                            "protection_confirmed_at":
                                timestamp,
                        },
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
                        halt_key=(
                            f"trade:{trade.trade_id}"
                        ),
                        occurred_at=timestamp,
                    )

            elif outcome["confirmed"]:
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
                            linked.filled_quantity,
                        "required_recovery_action":
                            EntryRecoveryAction
                            .NONE.value,
                        "last_completed_recovery_step":
                            EntryRecoveryStep
                            .PROTECTION_CONFIRMED.value,
                        "recovery_failure_reason": None,
                        "operator_ack_required": 0,
                        "last_protection_reconciliation_id":
                            metadata.reconciliation_id,
                        "protection_confirmed_at":
                            timestamp,
                    },
                    details={
                        "reconciliation_id":
                            metadata.reconciliation_id,
                        "protected_quantity":
                            linked.filled_quantity,
                    },
                    occurred_at=timestamp,
                )
                new_status = updated.status

                with self._connect() as connection:
                    self._clear_entry_halt(
                        connection,
                        halt_key=(
                            f"trade:{trade.trade_id}"
                        ),
                        occurred_at=timestamp,
                    )

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
                    TradeStatus.EXIT_PENDING,
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

            if (
                trade.filled_quantity > 0
                and position is not None
                and position.quantity
                != Decimal(trade.filled_quantity)
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
                != trade.filled_quantity
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
                    TradeStatus.EXIT_PENDING,
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
