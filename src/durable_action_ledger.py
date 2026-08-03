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

## Two scopes, deliberately different

Review raised that approved exposure was a lifetime total, so a long-lived
ledger would eventually refuse everything. Scoping it to a session fixes that
— but scoping *keys* the same way would be a serious mistake, because a key
spent yesterday would become reusable today, which is precisely the replay a
durable ledger exists to prevent.

So the two are scoped differently, and the difference is the point:

* **Idempotency keys are global and permanent.** A key spent in any session is
  spent in every session, forever. There is no scope in which it comes back.
* **Approved exposure is per session.** Each session starts with fresh
  headroom, because a position ceiling describes what may be held now, not the
  sum of everything ever approved.

## Sessions close; they do not reopen

A closed session accepts no further records, and its identifier can never be
used again — reusing one would silently graft new activity onto a reviewed
history. Closure is the only lifecycle transition, there is no delete, no
reset, and no reopen. Archival means marking closed, not removing rows.
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

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ledger_sessions (
    session_id     TEXT PRIMARY KEY,
    closed         INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    opened_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS spent_actions (
    idempotency_key  TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL REFERENCES ledger_sessions(session_id),
    approved_quantity INTEGER NOT NULL CHECK (approved_quantity >= 0),
    recorded_sequence INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS spent_actions_by_session
    ON spent_actions(session_id);
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


def _validated_identifier(value: object, label: str) -> str:
    """A session or key identifier that is safe to store and correlate by."""
    if not isinstance(value, str) or not value.strip():
        raise DurableLedgerError(f"a {label} is required")
    if value != value.strip():
        raise DurableLedgerError(f"{label} has surrounding whitespace")
    if len(value) > 128:
        raise DurableLedgerError(f"{label} is too long")
    return value


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

    def __init__(self, database_path: Path, session_id: str) -> None:
        if not isinstance(database_path, Path):
            raise DurableLedgerError("a database path is required")
        if is_repository_data_path(database_path):
            raise DurableLedgerError(
                "the ledger may not be opened inside repository data"
            )
        self.session_id = _validated_identifier(session_id, "session identity")
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(os.fspath(database_path), isolation_level=None)
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            self._connection.executescript(_SCHEMA)
            self._assert_schema_version()
            self._open_or_resume_session()
        except Exception:
            # A refusal during construction must not leave a live handle on
            # the file. Leaking one would keep a lock on a database the
            # caller believes it never opened.
            self._connection.close()
            raise

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

    def _open_or_resume_session(self) -> None:
        """Resume an open session, or start a new one. Never reopen a closed one."""
        row = self._connection.execute(
            "SELECT closed FROM ledger_sessions WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO ledger_sessions (session_id, closed, opened_sequence) "
                "VALUES (?, 0, (SELECT COALESCE(MAX(opened_sequence), 0) + 1 "
                "FROM ledger_sessions))",
                (self.session_id,),
            )
            return
        if row[0] == 1:
            # Reusing a closed identity would graft new activity onto a
            # reviewed history, which is worse than refusing to start.
            raise DurableLedgerError("session identity is closed and may not reopen")

    def close_session(self) -> None:
        """Mark this session closed. Idempotent, and never reversible.

        Closing is the only lifecycle transition. There is no reopen, because
        a session that can reopen is not a boundary, and no delete, because
        removing the row would make every key it held reusable.
        """
        self._connection.execute(
            "UPDATE ledger_sessions SET closed = 1 WHERE session_id = ?",
            (self.session_id,),
        )

    @property
    def session_is_closed(self) -> bool:
        row = self._connection.execute(
            "SELECT closed FROM ledger_sessions WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "DurableActionLedger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- the ActionLedgerBase contract ------------------------------------

    def has_seen(self, key: str) -> bool:
        """Global, not session-scoped, and deliberately so.

        A key spent in any session is spent in every session. Scoping this
        would make a key from a previous session reusable now, which is
        exactly the replay a durable ledger exists to prevent.
        """
        _validated_identifier(key, "idempotency key")
        row = self._connection.execute(
            "SELECT 1 FROM spent_actions WHERE idempotency_key = ?", (key,)
        ).fetchone()
        return row is not None

    def record(self, key: str, approved_quantity: int) -> None:
        _validated_identifier(key, "idempotency key")
        if self.session_is_closed:
            raise DurableLedgerError("session is closed and accepts no records")
        if not isinstance(approved_quantity, int) or isinstance(
            approved_quantity, bool
        ):
            raise DurableLedgerError("approved quantity must be an int")
        if approved_quantity < 0:
            raise DurableLedgerError("approved quantity must not be negative")
        try:
            self._connection.execute(
                "INSERT INTO spent_actions "
                "(idempotency_key, session_id, approved_quantity, recorded_sequence) "
                "VALUES (?, ?, ?, (SELECT COALESCE(MAX(recorded_sequence), 0) + 1 "
                "FROM spent_actions))",
                (key, self.session_id, approved_quantity),
            )
        except sqlite3.IntegrityError as exc:
            # The primary key refused it. No check-then-insert window, so this
            # is correct even against a concurrent writer.
            raise DurableLedgerError("action key has already been used") from exc

    @property
    def approved_quantity(self) -> int:
        """Exposure approved **in this session**.

        A position ceiling describes what may be held now, not the sum of
        everything ever approved, so this resets with each new session while
        keys never do.
        """
        row = self._connection.execute(
            "SELECT COALESCE(SUM(approved_quantity), 0) FROM spent_actions "
            "WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()
        return int(row[0])

    @property
    def lifetime_approved_quantity(self) -> int:
        """Every session's exposure. Reporting only; nothing gates on it."""
        row = self._connection.execute(
            "SELECT COALESCE(SUM(approved_quantity), 0) FROM spent_actions"
        ).fetchone()
        return int(row[0])

    @property
    def recorded_count(self) -> int:
        """Keys spent in this session."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM spent_actions WHERE session_id = ?",
            (self.session_id,),
        ).fetchone()
        return int(row[0])

    @property
    def lifetime_recorded_count(self) -> int:
        row = self._connection.execute(
            "SELECT COUNT(*) FROM spent_actions"
        ).fetchone()
        return int(row[0])

    def sanitized_facts(self) -> dict[str, str]:
        """Privacy-safe projection. Correlates by nothing.

        The session identity is accepted for correlation inside the process
        and never appears here: an operator-chosen run name is exactly the
        sort of string that ends up carrying an account number.
        """
        return {
            "session_closed": "true" if self.session_is_closed else "false",
            "session_key_count_bucket": (
                "none" if self.recorded_count == 0
                else "one" if self.recorded_count == 1
                else "many"
            ),
        }


__all__ = [
    "REPOSITORY_DATA_DIRECTORY",
    "SCHEMA_VERSION",
    "DurableActionLedger",
    "DurableLedgerError",
    "is_repository_data_path",
]
