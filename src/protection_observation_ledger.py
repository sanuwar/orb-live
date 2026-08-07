"""Durable, append-only cross-run protection-observation ledger (M13 Issue 2).

Each read-only checkpoint run (Milestone 9's grounding checkpoint) produces
standalone evidence today: nothing accumulates across runs, which is exactly
why repeated checkpoints cannot converge on a promotion decision (see
`C:\\dev\\orb-live-current-state-assessment.md` section 25, item 1(b), and
`src/protection_observation_model.py`'s module docstring). This module is
that accumulation layer.

## What this module is

A queryable INDEX over `ProtectionObservationRecord`s (Issue 1), backed by
SQLite for the same reason `src/durable_action_ledger.py` is: a key accepted
before a crash must still be accepted after it, and `INSERT` against a
`PRIMARY KEY` has no check-then-insert race window.

## What this module is NOT

Not authoritative. Per the milestone's safety invariants, SQLite here
remains disposable, rebuildable, and non-authoritative — a read/query index,
never the source of truth. The authoritative record of an observation is the
`ProtectionObservationRecord` itself (and, operationally, the sanitized
evidence artifact written outside the repository by the checkpoint launcher
per the existing `AtomicEvidenceStore` convention). If this database file is
lost, it can be rebuilt by replaying the same stream of
`ProtectionObservationRecord`s through `append()` again — see `rebuild()`.

Recording an observation here never grants authority. There is no method on
this class that can set `protection_grounded`, touch
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`, or otherwise change what any
runtime component is permitted to do. That is the promotion rubric's job
(Issue 3) and, ultimately, a human's (Issue 6 of the milestone handoff).

## Repository data is refused, not merely avoided

Same precedent as `durable_action_ledger.py`: opening a database under
repository `data/` is checked and refused at the point the file would
actually be opened.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Iterable, Iterator

from src.live_order_state_model import ProtectiveCoverage
from src.offline_path_safety import is_repository_data_path
from src.protection_observation_model import (
    AdapterIdentity,
    EvidenceSourceClass,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    PrivacyRedactionStatus,
    ProtectionObservationRecord,
    QuantityRelationship,
)
from src.read_only_broker_state import SnapshotFreshness


class ProtectionLedgerError(RuntimeError):
    """A refused path, a misused run, or a rejected write. Never a broker
    condition, and never carries private account data."""


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent

#: Bumped to 2 in M13 Issue 3 to add `observed_at_monotonic`,
#: `quantity_relationship`, and `write_count` — the promotion rubric needs
#: distinct observation times, quantity-coverage classification, and an
#: independent zero-write check that does not merely trust the model
#: layer's own guarantee, and a version
#: bump (not a silent ALTER) is this repository's existing precedent for a
#: layout change: `durable_action_ledger.py`'s own comment is direct about
#: why — "guessing at an unknown layout is how a ledger silently forgets."
#: A database created under version 1 refuses to open under version 2; it is
#: disposable, non-authoritative, and rebuildable from its authoritative
#: record stream via `rebuild()`.
SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS protection_ledger_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protection_observation_runs (
    run_id          TEXT PRIMARY KEY,
    closed          INTEGER NOT NULL DEFAULT 0 CHECK (closed IN (0, 1)),
    opened_sequence INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS protection_observations (
    observation_id                TEXT PRIMARY KEY,
    run_id                        TEXT NOT NULL
        REFERENCES protection_observation_runs(run_id),
    observation_sequence          INTEGER NOT NULL,
    recorded_sequence             INTEGER NOT NULL,
    observed_at_monotonic         REAL NOT NULL,
    release_identity              TEXT NOT NULL,
    implementation_digest         TEXT NOT NULL,
    account_fingerprint_reference TEXT NOT NULL,
    broker_adapter_identity       TEXT NOT NULL,
    evidence_source_class         TEXT NOT NULL,
    normalized_protection_state   TEXT NOT NULL,
    quantity_relationship          TEXT NOT NULL,
    write_count                    INTEGER NOT NULL CHECK (write_count >= 0),
    data_freshness                TEXT NOT NULL,
    consistency_status            TEXT NOT NULL,
    contamination_status          TEXT NOT NULL,
    privacy_redaction_status      TEXT NOT NULL,
    evidence_digest                TEXT NOT NULL,
    append_state                   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS protection_observations_by_run
    ON protection_observations(run_id);
CREATE INDEX IF NOT EXISTS protection_observations_by_source
    ON protection_observations(evidence_source_class);
CREATE INDEX IF NOT EXISTS protection_observations_by_account
    ON protection_observations(account_fingerprint_reference);
CREATE INDEX IF NOT EXISTS protection_observations_by_implementation
    ON protection_observations(implementation_digest);
CREATE TABLE IF NOT EXISTS protection_observation_conflicts (
    conflict_sequence        INTEGER PRIMARY KEY AUTOINCREMENT,
    observation_id           TEXT NOT NULL,
    run_id                   TEXT NOT NULL,
    attempted_evidence_digest TEXT NOT NULL,
    existing_evidence_digest  TEXT NOT NULL,
    recorded_sequence         INTEGER NOT NULL
);
"""


class LedgerAppendState(str, Enum):
    """The explicit, fixed vocabulary of what an append attempt did.

    Every one of these still records the observation (except
    `DUPLICATE_CONFLICT`, which is refused precisely because recording it
    would silently overwrite the original, and `INVALID_OBSERVATION`, which
    never had a well-formed record to record). Recording a stale,
    ineligible, or contaminated observation is not a failure — it is honest
    evidence the promotion rubric (Issue 3) needs in order to correctly
    exclude it later. Hiding bad evidence would be worse than storing it.
    """

    ACCEPTED_OBSERVATION = "ACCEPTED_OBSERVATION"
    DUPLICATE_EQUIVALENT = "DUPLICATE_EQUIVALENT"
    DUPLICATE_CONFLICT = "DUPLICATE_CONFLICT"
    STALE_OBSERVATION = "STALE_OBSERVATION"
    INELIGIBLE_SOURCE = "INELIGIBLE_SOURCE"
    CONTAMINATED_EVIDENCE = "CONTAMINATED_EVIDENCE"
    INVALID_OBSERVATION = "INVALID_OBSERVATION"


#: States under which a row was actually written to `protection_observations`.
#: `DUPLICATE_EQUIVALENT` refers back to the row already written by the first
#: append; `DUPLICATE_CONFLICT` and `INVALID_OBSERVATION` never write one.
STATES_WITH_A_STORED_ROW: frozenset[LedgerAppendState] = frozenset(
    {
        LedgerAppendState.ACCEPTED_OBSERVATION,
        LedgerAppendState.STALE_OBSERVATION,
        LedgerAppendState.INELIGIBLE_SOURCE,
        LedgerAppendState.CONTAMINATED_EVIDENCE,
    }
)


@dataclass(frozen=True)
class LedgerAppendResult:
    state: LedgerAppendState
    observation_id: str
    run_id: str
    recorded_sequence: int | None
    reason: str = ""


@dataclass(frozen=True)
class LedgerObservationSummary:
    """A privacy-safe, read-only projection of one stored row.

    Every field is already an enum, a reference, or a bounded scalar on
    `ProtectionObservationRecord` — this summary adds nothing new and
    removes nothing that was not already safe to expose.
    """

    observation_id: str
    run_id: str
    observation_sequence: int
    recorded_sequence: int
    observed_at_monotonic: float
    release_identity: str
    implementation_digest: str
    account_fingerprint_reference: str
    broker_adapter_identity: AdapterIdentity
    evidence_source_class: EvidenceSourceClass
    normalized_protection_state: ProtectiveCoverage
    quantity_relationship: QuantityRelationship
    write_count: int
    data_freshness: SnapshotFreshness
    consistency_status: ObservationConsistencyStatus
    contamination_status: ObservationContaminationStatus
    privacy_redaction_status: PrivacyRedactionStatus
    evidence_digest: str
    append_state: LedgerAppendState


@dataclass(frozen=True)
class LedgerConflictSummary:
    observation_id: str
    run_id: str
    attempted_evidence_digest: str
    existing_evidence_digest: str
    recorded_sequence: int


def _validated_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtectionLedgerError(f"a {label} is required")
    if value != value.strip():
        raise ProtectionLedgerError(f"{label} has surrounding whitespace")
    if len(value) > 128:
        raise ProtectionLedgerError(f"{label} is too long")
    return value


_COLUMNS = (
    "observation_id",
    "run_id",
    "observation_sequence",
    "recorded_sequence",
    "observed_at_monotonic",
    "release_identity",
    "implementation_digest",
    "account_fingerprint_reference",
    "broker_adapter_identity",
    "evidence_source_class",
    "normalized_protection_state",
    "quantity_relationship",
    "write_count",
    "data_freshness",
    "consistency_status",
    "contamination_status",
    "privacy_redaction_status",
    "evidence_digest",
    "append_state",
)


def _row_to_summary(row: tuple) -> LedgerObservationSummary:
    values = dict(zip(_COLUMNS, row))
    return LedgerObservationSummary(
        observation_id=values["observation_id"],
        run_id=values["run_id"],
        observation_sequence=int(values["observation_sequence"]),
        recorded_sequence=int(values["recorded_sequence"]),
        observed_at_monotonic=float(values["observed_at_monotonic"]),
        release_identity=values["release_identity"],
        implementation_digest=values["implementation_digest"],
        account_fingerprint_reference=values["account_fingerprint_reference"],
        broker_adapter_identity=AdapterIdentity(values["broker_adapter_identity"]),
        evidence_source_class=EvidenceSourceClass(values["evidence_source_class"]),
        normalized_protection_state=ProtectiveCoverage(
            values["normalized_protection_state"]
        ),
        quantity_relationship=QuantityRelationship(
            values["quantity_relationship"]
        ),
        write_count=int(values["write_count"]),
        data_freshness=SnapshotFreshness(values["data_freshness"]),
        consistency_status=ObservationConsistencyStatus(
            values["consistency_status"]
        ),
        contamination_status=ObservationContaminationStatus(
            values["contamination_status"]
        ),
        privacy_redaction_status=PrivacyRedactionStatus(
            values["privacy_redaction_status"]
        ),
        evidence_digest=values["evidence_digest"],
        append_state=LedgerAppendState(values["append_state"]),
    )


class ProtectionObservationLedger:
    """Cross-run, append-only, non-authoritative evidence index.

    Mirrors `src/durable_action_ledger.py`'s run/session lifecycle: a run
    (like a session there) opens once, accepts appends, and closes — closing
    is the only lifecycle transition, there is no delete, and a closed run
    identity may never reopen or accept a further append.
    """

    def __init__(self, database_path: Path, run_id: str) -> None:
        if not isinstance(database_path, Path):
            raise ProtectionLedgerError("a database path is required")
        if is_repository_data_path(database_path):
            raise ProtectionLedgerError(
                "the protection observation ledger may not be opened "
                "inside repository data"
            )
        self.run_id = _validated_identifier(run_id, "run identity")
        self.database_path = database_path
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            os.fspath(database_path), isolation_level=None
        )
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=FULL")
        try:
            self._connection.executescript(_SCHEMA)
            self._assert_schema_version()
            self._open_or_resume_run()
        except Exception:
            self._connection.close()
            raise

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM protection_ledger_meta WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO protection_ledger_meta (key, value) "
                "VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            raise ProtectionLedgerError(
                "protection observation ledger schema version is unsupported"
            )

    def _open_or_resume_run(self) -> None:
        row = self._connection.execute(
            "SELECT closed FROM protection_observation_runs WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO protection_observation_runs "
                "(run_id, closed, opened_sequence) VALUES (?, 0, "
                "(SELECT COALESCE(MAX(opened_sequence), 0) + 1 "
                "FROM protection_observation_runs))",
                (self.run_id,),
            )
            return
        if row[0] == 1:
            raise ProtectionLedgerError(
                "run identity is closed and may not reopen"
            )

    def close_run(self) -> None:
        """Mark this run closed. Idempotent, and never reversible."""
        self._connection.execute(
            "UPDATE protection_observation_runs SET closed = 1 WHERE run_id = ?",
            (self.run_id,),
        )

    @property
    def run_is_closed(self) -> bool:
        row = self._connection.execute(
            "SELECT closed FROM protection_observation_runs WHERE run_id = ?",
            (self.run_id,),
        ).fetchone()
        return bool(row and row[0] == 1)

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ProtectionObservationLedger":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- append -------------------------------------------------------

    def append(self, record: ProtectionObservationRecord) -> LedgerAppendResult:
        """Append one observation. Never overwrites a prior row.

        Idempotent for byte/digest-equivalent re-delivery of the same
        `observation_id` (`DUPLICATE_EQUIVALENT`); refuses, without
        modifying the original row, a re-delivery under the same
        `observation_id` whose evidence digest disagrees
        (`DUPLICATE_CONFLICT`) — and records the conflict itself for later
        audit rather than discarding it silently.
        """
        if not isinstance(record, ProtectionObservationRecord):
            raise ProtectionLedgerError(
                "only a ProtectionObservationRecord may be appended"
            )
        if record.run_id != self.run_id:
            return LedgerAppendResult(
                state=LedgerAppendState.INVALID_OBSERVATION,
                observation_id=record.observation_id,
                run_id=record.run_id,
                recorded_sequence=None,
                reason="observation run_id does not match the ledger's open run",
            )
        if self.run_is_closed:
            raise ProtectionLedgerError("run is closed and accepts no records")

        existing = self._connection.execute(
            "SELECT evidence_digest, recorded_sequence FROM protection_observations "
            "WHERE observation_id = ?",
            (record.observation_id,),
        ).fetchone()
        if existing is not None:
            existing_digest, existing_sequence = existing
            if existing_digest == record.evidence_digest:
                return LedgerAppendResult(
                    state=LedgerAppendState.DUPLICATE_EQUIVALENT,
                    observation_id=record.observation_id,
                    run_id=record.run_id,
                    recorded_sequence=int(existing_sequence),
                    reason="identical evidence digest already recorded",
                )
            self._connection.execute(
                "INSERT INTO protection_observation_conflicts "
                "(observation_id, run_id, attempted_evidence_digest, "
                "existing_evidence_digest, recorded_sequence) "
                "VALUES (?, ?, ?, ?, (SELECT COALESCE(MAX(recorded_sequence), 0) "
                "+ 1 FROM protection_observations))",
                (
                    record.observation_id,
                    record.run_id,
                    record.evidence_digest,
                    existing_digest,
                ),
            )
            return LedgerAppendResult(
                state=LedgerAppendState.DUPLICATE_CONFLICT,
                observation_id=record.observation_id,
                run_id=record.run_id,
                recorded_sequence=None,
                reason=(
                    "an observation with this id already exists with a "
                    "different evidence digest; the original was not "
                    "modified"
                ),
            )

        if record.contamination_status is ObservationContaminationStatus.CONTAMINATED:
            append_state = LedgerAppendState.CONTAMINATED_EVIDENCE
        elif record.evidence_source_class is EvidenceSourceClass.UNKNOWN_SOURCE:
            append_state = LedgerAppendState.INELIGIBLE_SOURCE
        elif record.data_freshness is not SnapshotFreshness.FRESH:
            append_state = LedgerAppendState.STALE_OBSERVATION
        else:
            append_state = LedgerAppendState.ACCEPTED_OBSERVATION

        try:
            self._connection.execute(
                "INSERT INTO protection_observations ("
                "observation_id, run_id, observation_sequence, "
                "recorded_sequence, observed_at_monotonic, release_identity, "
                "implementation_digest, account_fingerprint_reference, "
                "broker_adapter_identity, evidence_source_class, "
                "normalized_protection_state, quantity_relationship, "
                "write_count, data_freshness, consistency_status, "
                "contamination_status, privacy_redaction_status, "
                "evidence_digest, append_state"
                ") VALUES (?, ?, ?, "
                "(SELECT COALESCE(MAX(recorded_sequence), 0) + 1 "
                "FROM protection_observations), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    record.observation_id,
                    record.run_id,
                    record.observation_sequence,
                    record.observed_at_monotonic,
                    record.release_identity,
                    record.implementation_digest,
                    record.account_fingerprint_reference,
                    record.broker_adapter_identity.value,
                    record.evidence_source_class.value,
                    record.normalized_protection_state.value,
                    record.quantity_relationship.value,
                    record.write_count,
                    record.data_freshness.value,
                    record.consistency_status.value,
                    record.contamination_status.value,
                    record.privacy_redaction_status.value,
                    record.evidence_digest,
                    append_state.value,
                ),
            )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - race guard
            raise ProtectionLedgerError(
                "observation id has already been used"
            ) from exc

        recorded_sequence = self._connection.execute(
            "SELECT recorded_sequence FROM protection_observations "
            "WHERE observation_id = ?",
            (record.observation_id,),
        ).fetchone()[0]

        return LedgerAppendResult(
            state=append_state,
            observation_id=record.observation_id,
            run_id=record.run_id,
            recorded_sequence=int(recorded_sequence),
        )

    def rebuild(
        self, records: Iterable[ProtectionObservationRecord]
    ) -> list[LedgerAppendResult]:
        """Replay a stream of previously-produced records.

        This database file is disposable and non-authoritative. If it is
        lost, it is rebuilt by replaying the same records again — every
        replay of an already-recorded observation returns
        `DUPLICATE_EQUIVALENT`, so rebuilding twice is safe.
        """
        return [self.append(record) for record in records]

    # -- query ----------------------------------------------------------

    def observations_for_run(self, run_id: str) -> list[LedgerObservationSummary]:
        _validated_identifier(run_id, "run identity")
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE run_id = ? ORDER BY observation_sequence",
            (run_id,),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def all_observations(self) -> list[LedgerObservationSummary]:
        """Cross-run query, in deterministic recorded order."""
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "ORDER BY recorded_sequence"
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def stale_observations(self) -> list[LedgerObservationSummary]:
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE data_freshness != ? ORDER BY recorded_sequence",
            (SnapshotFreshness.FRESH.value,),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def conflicting_observations(self) -> list[LedgerConflictSummary]:
        rows = self._connection.execute(
            "SELECT observation_id, run_id, attempted_evidence_digest, "
            "existing_evidence_digest, recorded_sequence "
            "FROM protection_observation_conflicts ORDER BY conflict_sequence"
        ).fetchall()
        return [
            LedgerConflictSummary(
                observation_id=row[0],
                run_id=row[1],
                attempted_evidence_digest=row[2],
                existing_evidence_digest=row[3],
                recorded_sequence=int(row[4]),
            )
            for row in rows
        ]

    def observations_by_source(
        self, source: EvidenceSourceClass
    ) -> list[LedgerObservationSummary]:
        if not isinstance(source, EvidenceSourceClass):
            raise ProtectionLedgerError("source must be an EvidenceSourceClass")
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE evidence_source_class = ? ORDER BY recorded_sequence",
            (source.value,),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def observations_by_account(
        self, account_fingerprint_reference: str
    ) -> list[LedgerObservationSummary]:
        _validated_identifier(
            account_fingerprint_reference, "account fingerprint reference"
        )
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE account_fingerprint_reference = ? ORDER BY recorded_sequence",
            (account_fingerprint_reference,),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def observations_by_implementation(
        self, implementation_digest: str
    ) -> list[LedgerObservationSummary]:
        _validated_identifier(implementation_digest, "implementation digest")
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE implementation_digest = ? ORDER BY recorded_sequence",
            (implementation_digest,),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    def broker_authoritative_observations(self) -> list[LedgerObservationSummary]:
        """Convenience filter: accepted, not contaminated, live-source rows.

        Still not a promotion decision — merely the subset of recorded
        evidence the promotion rubric (Issue 3) would ever need to consider
        in the first place.
        """
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_observations "
            "WHERE evidence_source_class = ? AND contamination_status = ? "
            "AND append_state = ? ORDER BY recorded_sequence",
            (
                EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION.value,
                ObservationContaminationStatus.NOT_CONTAMINATED.value,
                LedgerAppendState.ACCEPTED_OBSERVATION.value,
            ),
        ).fetchall()
        return [_row_to_summary(row) for row in rows]

    # -- export -----------------------------------------------------------

    def export_summaries(self) -> list[dict[str, str | int]]:
        """A privacy-safe, allowlisted projection suitable for an operator
        export. Every value is already an enum, a reference, or a bounded
        scalar — nothing here can carry free-form or private content."""
        return [
            {
                "observation_id": summary.observation_id,
                "run_id": summary.run_id,
                "observation_sequence": summary.observation_sequence,
                "recorded_sequence": summary.recorded_sequence,
                "observed_at_monotonic": summary.observed_at_monotonic,
                "release_identity": summary.release_identity,
                "implementation_digest": summary.implementation_digest,
                "account_fingerprint_reference": (
                    summary.account_fingerprint_reference
                ),
                "broker_adapter_identity": summary.broker_adapter_identity.value,
                "evidence_source_class": summary.evidence_source_class.value,
                "normalized_protection_state": (
                    summary.normalized_protection_state.value
                ),
                "quantity_relationship": summary.quantity_relationship.value,
                "write_count": summary.write_count,
                "data_freshness": summary.data_freshness.value,
                "consistency_status": summary.consistency_status.value,
                "contamination_status": summary.contamination_status.value,
                "privacy_redaction_status": summary.privacy_redaction_status.value,
                "evidence_digest": summary.evidence_digest,
                "append_state": summary.append_state.value,
            }
            for summary in self.all_observations()
        ]

    def sanitized_facts(self) -> dict[str, str]:
        """A bucketed, privacy-safe summary of ledger size. No identifiers."""
        total = len(self.all_observations())
        return {
            "run_closed": "true" if self.run_is_closed else "false",
            "total_observation_count_bucket": (
                "none" if total == 0 else "one" if total == 1 else "many"
            ),
            "conflict_count_bucket": (
                "none"
                if not self.conflicting_observations()
                else "one"
                if len(self.conflicting_observations()) == 1
                else "many"
            ),
        }


__all__ = [
    "ProtectionLedgerError",
    "SCHEMA_VERSION",
    "LedgerAppendState",
    "STATES_WITH_A_STORED_ROW",
    "LedgerAppendResult",
    "LedgerObservationSummary",
    "LedgerConflictSummary",
    "ProtectionObservationLedger",
]
