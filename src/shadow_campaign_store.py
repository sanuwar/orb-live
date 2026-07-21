"""
Append-only, non-executable shadow campaign store (Milestone 6).

This store persists ONLY hypothetical strategy evidence. It is completely
independent of every executable lifecycle store: it never reads from or
writes to TradeStateStore, broker_action_saga tables, order-lifecycle
tables, reconciliation stores, operator-resolution stores, or any broker
executor state, and it imports none of those modules.

Every persisted record is structurally non-executable:

- a database CHECK constraint forbids any row with ``non_executable`` set
  to anything other than ``1``;
- every correlation id carries the :data:`NON_EXECUTABLE_PREFIX`, which no
  broker submission path accepts and which is never shaped like a broker
  ref id;
- append-only triggers reject every UPDATE and DELETE;
- the store exposes no method that can hand an action to a transport.

All hypothetical risk state is RECONSTRUCTED from the append-only event
log, never from mutable counters. Daily counters are keyed by the
US/Eastern trading date; the trading week runs Monday 00:00 America/
New_York through the following Monday 00:00 America/New_York,
end-exclusive, with the week key derived from the US/Eastern trading date.
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")

# The repository data directory is refused for every store path, exactly
# as the simulation runner refuses it, so a shadow campaign can never be
# written beside production databases.
_REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(
        os.fspath(Path(__file__).resolve().parents[1] / "data")
    )
)

# Correlation ids for hypothetical actions carry this prefix. It is not a
# broker ref id (those are bare UUIDs or ``orb-saga-…``), and no broker
# submission path accepts it. It marks a record as permanently
# non-submittable.
NON_EXECUTABLE_PREFIX = "HYPO-NONEXEC-"

# A stable namespace for deterministic correlation ids so that a replay of
# the same bounded run derives the same id and de-duplicates.
_CORRELATION_NAMESPACE = uuid.UUID(
    "6d9f5c2e-0e0a-4c9a-9b7a-2f6f1c2d3e4b"
)

EVENT_CAMPAIGN_STARTED = "campaign_started"
EVENT_HYPOTHETICAL_ENTRY = "hypothetical_entry"
EVENT_HYPOTHETICAL_STOP_TO_BREAKEVEN = "hypothetical_stop_to_breakeven"
EVENT_HYPOTHETICAL_EXIT = "hypothetical_exit"
EVENT_HYPOTHETICAL_REJECTION = "hypothetical_rejection"
EVENT_MARKET_OBSERVATION = "market_observation"

# Exit kinds that realise a hypothetical profit-and-loss figure.
EXIT_KIND_STOP = "stop"
EXIT_KIND_BREAKEVEN = "breakeven"
EXIT_KIND_TARGET = "target"
EXIT_KIND_TIME = "time"


class ShadowCampaignStoreError(RuntimeError):
    """Raised when the shadow campaign store cannot proceed safely."""


def week_key_for(trading_date: date) -> str:
    """
    The Monday-keyed trading week for a US/Eastern trading date.

    The trading week runs Monday 00:00 through the following Monday 00:00
    America/New_York, end-exclusive. The key is the ISO date of that
    week's Monday, derived from the US/Eastern trading date, so daylight
    saving transitions are handled through timezone-aware dates rather
    than fixed UTC offsets.
    """

    monday = trading_date - timedelta(days=trading_date.weekday())
    return monday.isoformat()


def non_executable_correlation_id(
    *,
    shadow_campaign_id: str,
    trading_date: date,
    symbol: str,
    ordinal: int,
) -> str:
    """
    A deterministic, non-submittable correlation id for a hypothetical
    entry. Deterministic so that a replay derives the same id and the
    unique dedup key prevents a duplicate row.
    """

    seed = (
        f"{shadow_campaign_id}|{trading_date.isoformat()}|"
        f"{symbol.upper().strip()}|{int(ordinal)}"
    )
    digest = uuid.uuid5(_CORRELATION_NAMESPACE, seed)
    return f"{NON_EXECUTABLE_PREFIX}{digest}"


def _decimal_text(value: Any) -> str:
    return format(Decimal(str(value)), "f")


def _is_repository_data_path(raw: Any) -> bool:
    if isinstance(raw, int):
        return False

    try:
        text = os.fspath(raw)
    except TypeError:
        return False

    if isinstance(text, bytes):
        text = os.fsdecode(text)

    if text == ":memory:":
        return False

    candidate = os.path.normcase(os.path.abspath(text))

    try:
        return (
            os.path.commonpath(
                [candidate, _REPOSITORY_DATA_DIRECTORY]
            )
            == _REPOSITORY_DATA_DIRECTORY
        )
    except ValueError:
        return False


@dataclass(frozen=True)
class OpenHypotheticalPosition:
    """A single reconstructed, open hypothetical position."""

    correlation_id: str
    symbol: str
    trading_date: date
    shares: int
    entry_price: Decimal
    original_stop_price: Decimal
    current_stop_price: Decimal
    target_price: Decimal
    risk_per_share: Decimal
    breakeven_moved: bool


@dataclass(frozen=True)
class CampaignState:
    """
    Hypothetical risk state reconstructed entirely from append-only
    events. Never derived from a mutable counter.
    """

    shadow_campaign_id: str
    as_of_trading_date: date
    entries_today: int
    open_positions: int
    daily_realized_pnl: Decimal
    weekly_realized_pnl: Decimal
    campaign_realized_pnl: Decimal
    campaign_drawdown: Decimal
    open_position: OpenHypotheticalPosition | None
    entries_today_by_symbol: Mapping[str, int] = field(
        default_factory=dict
    )

    def entries_for_symbol(self, symbol: str) -> int:
        return self.entries_today_by_symbol.get(
            symbol.upper().strip(), 0
        )


class ShadowCampaignStore:
    """
    Append-only SQLite store for non-executable hypothetical evidence.

    The database path is always supplied explicitly and is refused if it
    resolves inside the repository data directory, so hypothetical
    evidence can never be written beside production databases. There is no
    default path.
    """

    def __init__(self, db_path: str | Path):
        if db_path is None:
            raise ShadowCampaignStoreError(
                "An explicit shadow campaign database path is required."
            )

        if _is_repository_data_path(db_path):
            raise ShadowCampaignStoreError(
                "The shadow campaign store refuses any database inside "
                "the repository data directory."
            )

        self.db_path = db_path
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.execute("PRAGMA journal_mode=WAL;")
        connection.execute("PRAGMA synchronous=FULL;")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS shadow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT NOT NULL UNIQUE,
                    shadow_campaign_id TEXT NOT NULL,
                    shadow_run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    non_executable INTEGER NOT NULL DEFAULT 1
                        CHECK (non_executable = 1),
                    symbol TEXT,
                    trading_date TEXT NOT NULL,
                    week_key TEXT NOT NULL,
                    observed_at TEXT,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS shadow_events_campaign_idx
                ON shadow_events (shadow_campaign_id, id)
                """
            )

            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_shadow_event_updates
                BEFORE UPDATE ON shadow_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'Shadow campaign store is append-only'
                    );
                END;
                """
            )

            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_shadow_event_deletes
                BEFORE DELETE ON shadow_events
                BEGIN
                    SELECT RAISE(
                        ABORT,
                        'Shadow campaign store is append-only'
                    );
                END;
                """
            )

    # ------------------------------------------------------------------
    # append-only writes
    # ------------------------------------------------------------------

    def record_event(
        self,
        *,
        shadow_campaign_id: str,
        shadow_run_id: str,
        event_type: str,
        correlation_id: str,
        trading_date: date,
        dedup_key: str,
        symbol: str | None = None,
        observed_at: datetime | None = None,
        payload: Mapping[str, Any] | None = None,
    ) -> tuple[int | None, bool]:
        """
        Append one non-executable event. Idempotent by ``dedup_key``.

        Returns ``(event_id, inserted)``. On a duplicate ``dedup_key`` the
        row is ignored and ``(existing_id, False)`` is returned, so a
        replay or restart never produces a duplicate shadow event.
        """

        if not shadow_campaign_id or not shadow_campaign_id.strip():
            raise ShadowCampaignStoreError(
                "A shadow_campaign_id is required."
            )

        if not shadow_run_id or not shadow_run_id.strip():
            raise ShadowCampaignStoreError(
                "A shadow_run_id is required."
            )

        if not correlation_id.startswith(NON_EXECUTABLE_PREFIX):
            raise ShadowCampaignStoreError(
                "Every shadow correlation id must be marked "
                "non-executable."
            )

        if observed_at is not None and observed_at.tzinfo is None:
            raise ShadowCampaignStoreError(
                "observed_at must be timezone-aware."
            )

        payload_json = json.dumps(
            dict(payload or {}),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        created_at = datetime.now(ET).isoformat()
        observed_text = (
            observed_at.astimezone(ET).isoformat()
            if observed_at is not None
            else None
        )
        normalized_symbol = (
            symbol.upper().strip() if symbol else None
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO shadow_events (
                    dedup_key,
                    shadow_campaign_id,
                    shadow_run_id,
                    event_type,
                    correlation_id,
                    non_executable,
                    symbol,
                    trading_date,
                    week_key,
                    observed_at,
                    payload_json,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?)
                """,
                (
                    dedup_key,
                    shadow_campaign_id,
                    shadow_run_id,
                    event_type,
                    correlation_id,
                    normalized_symbol,
                    trading_date.isoformat(),
                    week_key_for(trading_date),
                    observed_text,
                    payload_json,
                    created_at,
                ),
            )

            if cursor.rowcount:
                return int(cursor.lastrowid), True

            row = connection.execute(
                "SELECT id FROM shadow_events WHERE dedup_key = ?",
                (dedup_key,),
            ).fetchone()

        return (int(row[0]) if row else None), False

    def start_campaign(
        self,
        *,
        shadow_campaign_id: str,
        shadow_run_id: str,
        trading_date: date,
    ) -> tuple[int | None, bool]:
        """
        Record that a campaign has been explicitly started.

        Starting a new campaign is always an explicit operator action;
        this method never resets any existing campaign's state. A campaign
        is a distinct ``shadow_campaign_id`` with its own event history.
        """

        correlation_id = (
            f"{NON_EXECUTABLE_PREFIX}campaign-{shadow_campaign_id}"
        )
        return self.record_event(
            shadow_campaign_id=shadow_campaign_id,
            shadow_run_id=shadow_run_id,
            event_type=EVENT_CAMPAIGN_STARTED,
            correlation_id=correlation_id,
            trading_date=trading_date,
            dedup_key=f"{shadow_campaign_id}|campaign_started",
            payload={"note": "Campaign started by explicit operator action."},
        )

    # ------------------------------------------------------------------
    # read-only reconstruction
    # ------------------------------------------------------------------

    def _campaign_rows(
        self, shadow_campaign_id: str
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT * FROM shadow_events
                WHERE shadow_campaign_id = ?
                ORDER BY id ASC
                """,
                (shadow_campaign_id,),
            ).fetchall()

        return [dict(row) for row in rows]

    def event_count(self, shadow_campaign_id: str | None = None) -> int:
        with self._connect() as connection:
            if shadow_campaign_id is None:
                row = connection.execute(
                    "SELECT COUNT(*) FROM shadow_events"
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT COUNT(*) FROM shadow_events "
                    "WHERE shadow_campaign_id = ?",
                    (shadow_campaign_id,),
                ).fetchone()

        return int(row[0])

    def reconstruct_campaign_state(
        self,
        *,
        shadow_campaign_id: str,
        as_of_trading_date: date,
        as_of_datetime: datetime | None = None,
    ) -> CampaignState:
        """
        Reconstruct hypothetical risk state from the append-only log.

        The result equals the running state that produced the events:
        every counter is folded from events, none is read from a mutable
        field. Daily counters use the US/Eastern trading date; weekly
        counters use the Monday-keyed trading week.

        When ``as_of_datetime`` is supplied, only events observed at or
        before that instant are folded in. This makes a replay
        deterministic: a bounded run reconstructing state part-way through
        never sees an event that a later cycle of an earlier run recorded,
        so re-running the same plan reproduces the same decisions without
        duplicating any event.
        """

        rows = self._campaign_rows(shadow_campaign_id)

        if as_of_datetime is not None:
            if as_of_datetime.tzinfo is None:
                raise ShadowCampaignStoreError(
                    "as_of_datetime must be timezone-aware."
                )
            bound = as_of_datetime.astimezone(ET)
            rows = [
                row
                for row in rows
                if row["observed_at"] is None
                or datetime.fromisoformat(row["observed_at"]) <= bound
            ]

        as_of_week = week_key_for(as_of_trading_date)
        as_of_date_text = as_of_trading_date.isoformat()

        entries_today = 0
        entries_today_by_symbol: dict[str, int] = {}
        open_positions: dict[str, dict[str, Any]] = {}
        breakeven_moved: set[str] = set()

        campaign_realized = Decimal("0")
        daily_realized = Decimal("0")
        weekly_realized = Decimal("0")

        peak = Decimal("0")
        drawdown = Decimal("0")

        for row in rows:
            event_type = row["event_type"]
            payload = json.loads(row["payload_json"])
            row_date = row["trading_date"]
            row_week = row["week_key"]
            correlation_id = row["correlation_id"]

            if event_type == EVENT_HYPOTHETICAL_ENTRY:
                if row_date == as_of_date_text:
                    entries_today += 1
                    symbol = (row["symbol"] or "").upper()
                    entries_today_by_symbol[symbol] = (
                        entries_today_by_symbol.get(symbol, 0) + 1
                    )

                open_positions[correlation_id] = {
                    "correlation_id": correlation_id,
                    "symbol": row["symbol"] or "",
                    "trading_date": date.fromisoformat(row_date),
                    "shares": int(payload["shares"]),
                    "entry_price": Decimal(str(payload["entry_price"])),
                    "original_stop_price": Decimal(
                        str(payload["stop_price"])
                    ),
                    "current_stop_price": Decimal(
                        str(payload["stop_price"])
                    ),
                    "target_price": Decimal(str(payload["target_price"])),
                    "risk_per_share": Decimal(
                        str(payload["risk_per_share"])
                    ),
                }

            elif event_type == EVENT_HYPOTHETICAL_STOP_TO_BREAKEVEN:
                target = payload.get("correlation_id", correlation_id)
                breakeven_moved.add(target)
                if target in open_positions:
                    open_positions[target]["current_stop_price"] = (
                        Decimal(str(payload["new_stop_price"]))
                    )

            elif event_type == EVENT_HYPOTHETICAL_EXIT:
                target = payload.get("correlation_id", correlation_id)
                open_positions.pop(target, None)
                breakeven_moved.discard(target)

                realized = Decimal(str(payload["realized_pnl"]))
                campaign_realized += realized

                if row_date == as_of_date_text:
                    daily_realized += realized
                if row_week == as_of_week:
                    weekly_realized += realized

                peak = max(peak, campaign_realized)
                drawdown = max(drawdown, peak - campaign_realized)

        open_position = None
        if open_positions:
            # At most one hypothetical position is ever open; take the
            # earliest still-open entry deterministically.
            first = next(iter(open_positions.values()))
            open_position = OpenHypotheticalPosition(
                correlation_id=first["correlation_id"],
                symbol=first["symbol"],
                trading_date=first["trading_date"],
                shares=first["shares"],
                entry_price=first["entry_price"],
                original_stop_price=first["original_stop_price"],
                current_stop_price=first["current_stop_price"],
                target_price=first["target_price"],
                risk_per_share=first["risk_per_share"],
                breakeven_moved=(
                    first["correlation_id"] in breakeven_moved
                ),
            )

        return CampaignState(
            shadow_campaign_id=shadow_campaign_id,
            as_of_trading_date=as_of_trading_date,
            entries_today=entries_today,
            open_positions=len(open_positions),
            daily_realized_pnl=daily_realized,
            weekly_realized_pnl=weekly_realized,
            campaign_realized_pnl=campaign_realized,
            campaign_drawdown=drawdown,
            open_position=open_position,
            entries_today_by_symbol=entries_today_by_symbol,
        )
