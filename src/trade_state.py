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


DEFAULT_DB_PATH = Path("data/trade_state.sqlite")


class TradeStateError(RuntimeError):
    pass


class TradeNotFoundError(TradeStateError):
    pass


class DuplicateEntryError(TradeStateError):
    pass


class InvalidTransitionError(TradeStateError):
    pass


class TradeStatus(str, Enum):
    RESERVED = "reserved"
    ENTRY_PENDING = "entry_pending"
    OPEN_UNPROTECTED = "open_unprotected"
    OPEN_PROTECTED = "open_protected"
    EXIT_PENDING = "exit_pending"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    ERROR = "error"


ACTIVE_STATUSES = {
    TradeStatus.RESERVED,
    TradeStatus.ENTRY_PENDING,
    TradeStatus.OPEN_UNPROTECTED,
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
        TradeStatus.OPEN_UNPROTECTED,
        TradeStatus.CANCELLED,
        TradeStatus.ERROR,
    },
    TradeStatus.OPEN_UNPROTECTED: {
        TradeStatus.OPEN_PROTECTED,
        TradeStatus.EXIT_PENDING,
        TradeStatus.CLOSED,
        TradeStatus.ERROR,
    },
    TradeStatus.OPEN_PROTECTED: {
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
    created_at: datetime
    updated_at: datetime
    opened_at: datetime | None
    closed_at: datetime | None
    version: int


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
        db_path: str | Path = DEFAULT_DB_PATH,
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
                        occurred_at TEXT NOT NULL,
                        details_json TEXT NOT NULL,
                        FOREIGN KEY(trade_id)
                            REFERENCES trades(trade_id)
                    );

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
                "Partial entry fills are not accepted "
                "by this state transition. Reconcile "
                "and protect the actual filled quantity "
                "before continuing."
            )

        if average_entry_price <= 0:
            raise TradeStateError(
                "Average entry price must be positive."
            )

        timestamp = occurred_at or utc_now()

        return self._transition(
            trade_id,
            new_status=(
                TradeStatus.OPEN_UNPROTECTED
            ),
            event_type="entry_filled",
            updates={
                "filled_quantity":
                    filled_quantity,
                "average_entry_price":
                    average_entry_price,
                "opened_at": timestamp,
            },
            details={
                "filled_quantity":
                    filled_quantity,
                "average_entry_price":
                    average_entry_price,
            },
            occurred_at=timestamp,
        )

    def attach_protective_stop(
        self,
        trade_id: str,
        *,
        broker_order_id: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        if not broker_order_id.strip():
            raise TradeStateError(
                "Protective stop order ID "
                "is required."
            )

        current = self.get_trade(trade_id)

        if current.status not in {
            TradeStatus.OPEN_UNPROTECTED,
            TradeStatus.OPEN_PROTECTED,
        }:
            raise InvalidTransitionError(
                "A protective stop can only be "
                "attached to an open trade."
            )

        if (
            current.status
            == TradeStatus.OPEN_PROTECTED
        ):
            return current

        return self._transition(
            trade_id,
            new_status=(
                TradeStatus.OPEN_PROTECTED
            ),
            event_type=(
                "protective_stop_attached"
            ),
            updates={
                "protective_stop_order_id":
                    broker_order_id,
            },
            details={
                "broker_order_id":
                    broker_order_id,
            },
            occurred_at=occurred_at,
        )

    def mark_protective_stop_inactive(
        self,
        trade_id: str,
        *,
        reason: str,
        occurred_at: datetime | None = None,
    ) -> TradeRecord:
        return self._transition(
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
            },
            details={"reason": reason},
            occurred_at=occurred_at,
        )

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

        return self._transition(
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
        occurred_at: datetime | None = None,
    ) -> None:
        self.get_trade(trade_id)

        timestamp = occurred_at or utc_now()

        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO order_events (
                    trade_id,
                    broker_order_id,
                    role,
                    order_state,
                    side,
                    quantity,
                    filled_quantity,
                    limit_price,
                    stop_price,
                    occurred_at,
                    details_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    timestamp.isoformat(),
                    json.dumps(
                        details or {},
                        sort_keys=True,
                        default=str,
                    ),
                ),
            )

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
            if trade.status in {
                TradeStatus.OPEN_UNPROTECTED,
                TradeStatus.OPEN_PROTECTED,
                TradeStatus.EXIT_PENDING,
            }
        }

        linked_entry_order_ids = {
            trade.entry_order_id
            for trade in active_trades
            if trade.entry_order_id
        }

        for trade in active_trades:
            if (
                trade.status
                == TradeStatus.OPEN_UNPROTECTED
            ):
                issues.append(
                    ReconciliationIssue(
                        severity="critical",
                        code="open_unprotected",
                        message=(
                            "Local trade is open without "
                            "a confirmed protective stop."
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

            if (
                trade.status
                in {
                    TradeStatus.OPEN_UNPROTECTED,
                    TradeStatus.OPEN_PROTECTED,
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
                        severity="warning",
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
                        severity="warning",
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
    store = TradeStateStore()

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