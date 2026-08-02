"""Durable action ledger — idempotency that survives a restart.

`risk_limits.ActionLedger` remembers spent idempotency keys in memory. That is
enough to stop a loop replaying an action inside one process, and useless the
moment the process dies. A runtime that restarts mid-session comes back with
an empty ledger, and the very first thing an unattended scheduler does after a
crash is retry — which is precisely when duplicate protection has to work.

This is the same contract backed by SQLite, so a key spent before the crash is
still spent after it.

## Why the database enforces uniqueness, not this module

The primary-key constraint does the work. A check-then-insert in Python has a
window between the check and the insert, and two processes racing through that
window both win. `INSERT` against a `PRIMARY KEY` has no window: the second one
fails, and a failure is exactly what a duplicate should produce.

## Repository data is refused, not merely avoided

Opening a production database under `data/` is prohibited, and a module that
merely *doesn't happen to* do it is one caller-supplied path away from doing
it. The path is checked and refused, so the prohibition is enforced where the
file would actually be opened rather than left to whoever calls this.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from src.risk_limits import ActionLedgerBase, RiskLimitError


class DurableLedgerError(RiskLimitError):
    """A refused path or a rejected write. Never carries account data."""


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REPOSITORY_DATA_DIRECTORY = os.path.normcase(
    os.path.abspath(os.fspath(REPOSITORY_ROOT / "data"))
)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS spent_actions (
    idempotency_key  TEXT PRIMARY KEY,
    approved_quantity INTEGER NOT NULL CHECK (approved_quantity >= 0),
    recorded_sequence INTEGER NOT NULL
);
"""


def is_repository_data_path(path: Path) -> bool:
    """True when `path` lies inside the repository's production data directory."""
    try:
        resolved = os.path.normcase(os.path.abspath(os.fspath(path)))
    except (TypeError, ValueError):
        return False
    return (
        resolved == REPOSITORY_DATA_DIRECTORY
        or os.path.commonpath([resolved, REPOSITORY_DATA_DIRECTORY])
        == REPOSITORY_DATA_DIRECTORY
    )


class DurableActionLedger(ActionLedgerBase):
    """A spent-key ledger that outlives the process.

    Interchangeable with the in-memory ledger: `risk_limits.evaluate` accepts
    either, because both are `ActionLedgerBase`. That matters — a durable
    ledger nobody can pass to the risk check would be decoration.

    Like the in-memory one it has **no removal method**. "Forget this key" is
    indistinguishable from "let it happen again", and a durable ledger makes
    that worse rather than better: the whole point is that the memory outlives
    the thing that might want to clear it.
    """

    def __init__(self, database_path: Path) -> None:
        if not isinstance(database_path, Path):
            raise DurableLedgerError("a database path is required")
        if is_repository_data_path(database_path):
            raise DurableLedgerError(
                "the ledger may not be opened inside repository data"
            )
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(os.fspath(database_path), isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        self._connection.executescript(_SCHEMA)
        self._assert_schema_version()

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM ledger_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO ledger_meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            # Guessing at an unknown layout is how a ledger silently forgets.
            raise DurableLedgerError("ledger schema version is unsupported")

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableActionLedger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the ActionLedgerBase contract ------------------------------------

    def has_seen(self, key: str) -> bool:
        if not isinstance(key, str) or not key.strip():
            raise DurableLedgerError("an idempotency key is required")
        row = self._connection.execute(
            "SELECT 1 FROM spent_actions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return row is not None

    def record(self, key: str, approved_quantity: int) -> None:
        if not isinstance(key, str) or not key.strip():
            raise DurableLedgerError("an idempotency key is required")
        if not isinstance(approved_quantity, int) or isinstance(
            approved_quantity, bool
        ):
            raise DurableLedgerError("approved quantity must be an int")
        if approved_quantity < 0:
            raise DurableLedgerError("approved quantity must not be negative")
        try:
            self._connection.execute(
                "INSERT INTO spent_actions "
                "(idempotency_key, approved_quantity, recorded_sequence) "
                "VALUES (?, ?, (SELECT COALESCE(MAX(recorded_sequence), 0) + 1 "
                "FROM spent_actions))",
                (key, approved_quantity),
            )
        except sqlite3.IntegrityError as exc:
            # The primary key refused it. No check-then-insert window, so this
            # is correct even against a concurrent writer.
            raise DurableLedgerError("action key has already been used") from exc

    @property
    def approved_quantity(self) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(SUM(approved_quantity), 0) FROM spent_actions"
        ).fetchone()
        return int(row[0])

    @property
    def recorded_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM spent_actions"
        ).fetchone()
        return int(row[0])


__all__ = [
    "REPOSITORY_DATA_DIRECTORY",
    "SCHEMA_VERSION",
    "DurableActionLedger",
    "DurableLedgerError",
    "is_repository_data_path",
]
