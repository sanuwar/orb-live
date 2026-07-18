from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "trading_journal.sqlite"


class Journal:
    """Append-only SQLite journal for signals, risk decisions, orders, and exits."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_utc TEXT NOT NULL,
                    strategy_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    config_hash TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_event_updates
                BEFORE UPDATE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'Trading journal is append-only');
                END;
                """
            )

            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS prevent_event_deletes
                BEFORE DELETE ON events
                BEGIN
                    SELECT RAISE(ABORT, 'Trading journal is append-only');
                END;
                """
            )

    @staticmethod
    def config_hash(config: dict[str, Any]) -> str:
        serialized = json.dumps(
            config,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def append_event(
        self,
        *,
        strategy_id: str,
        event_type: str,
        config_hash: str,
        payload: dict[str, Any],
        symbol: str | None = None,
    ) -> int:
        timestamp = datetime.now(timezone.utc).isoformat()

        payload_json = json.dumps(
            payload,
            sort_keys=True,
            default=str,
        )

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO events (
                    timestamp_utc,
                    strategy_id,
                    event_type,
                    symbol,
                    config_hash,
                    payload_json
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    timestamp,
                    strategy_id,
                    event_type,
                    symbol,
                    config_hash,
                    payload_json,
                ),
            )

            event_id = cursor.lastrowid

        if event_id is None:
            raise RuntimeError("Journal event was not assigned an ID.")

        return int(event_id)

    def count_events(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM events"
            ).fetchone()

        return int(row[0])


if __name__ == "__main__":
    try:
        from .config import load_config
    except ImportError:
        from config import load_config

    config = load_config()
    journal = Journal()
    hash_value = journal.config_hash(config)

    event_id = journal.append_event(
        strategy_id=config["strategy"]["name"],
        event_type="journal_test",
        symbol=None,
        config_hash=hash_value,
        payload={
            "message": "Trading journal initialized successfully.",
            "trading_enabled": config["strategy"]["enabled"],
        },
    )

    print(f"Journal path: {journal.db_path}")
    print(f"Event written: {event_id}")
    print(f"Total events: {journal.count_events()}")
    print(f"Config hash: {hash_value[:12]}...")