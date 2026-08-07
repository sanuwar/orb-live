"""Durable, disposable, non-authoritative store for grounding assessments.

M13 Issue 7. Records the `ProtectionGroundingAssessment` (Issue 4) that
`src/protection_checkpoint_integration.py` already computes from an
already-completed checkpoint result, so the read-only operator application
has real, persisted M13 evidence to display -- never a live recomputation
inside the operator process.

## What this module is NOT

Not authoritative, exactly like `src/protection_observation_ledger.py`: a
read/query index over assessments that already happened, disposable and
rebuildable via `record_assessment()` re-delivery (idempotent, since
`assessment_id` is itself a content digest -- see
`protection_grounding_assessment.evaluate_protection_grounding`). Recording
an assessment here never grants authority: there is no method on this module
that can set `protection_grounded`, `activation_approved`, or touch
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`.

## Repository data is refused, not merely avoided

Same precedent as the observation ledger: opening a database under
repository `data/` is checked and refused at the point the file would
actually be opened.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from src.offline_path_safety import is_repository_data_path
from src.protection_grounding_assessment import ProtectionGroundingAssessment
from src.protection_promotion_policy import PromotionOutcome


class ProtectionAssessmentStoreError(RuntimeError):
    """A refused path or a malformed record. Never a broker condition, and
    never carries private account data."""


SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS protection_assessment_meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS protection_assessments (
    assessment_id                  TEXT PRIMARY KEY,
    recorded_sequence              INTEGER NOT NULL,
    assessment_version              TEXT NOT NULL,
    generated_at_monotonic          REAL NOT NULL,
    policy_version                  TEXT NOT NULL,
    release_identity                TEXT NOT NULL,
    implementation_digest           TEXT NOT NULL,
    account_fingerprint_reference   TEXT NOT NULL,
    candidate_observation_ids       TEXT NOT NULL,
    included_observation_ids        TEXT NOT NULL,
    excluded_observation_ids        TEXT NOT NULL,
    informational_observation_ids   TEXT NOT NULL,
    exclusion_reasons               TEXT NOT NULL,
    distinct_run_count              INTEGER NOT NULL,
    distinct_observation_time_count INTEGER NOT NULL,
    broker_authoritative_observation_count INTEGER NOT NULL,
    qualifying_observation_count    INTEGER NOT NULL,
    recommendation                  TEXT NOT NULL,
    reason_codes                    TEXT NOT NULL,
    assessment_digest               TEXT NOT NULL,
    source_authority_passed          INTEGER NOT NULL CHECK (source_authority_passed IN (0, 1)),
    freshness_passed                 INTEGER NOT NULL CHECK (freshness_passed IN (0, 1)),
    account_consistency_passed       INTEGER NOT NULL CHECK (account_consistency_passed IN (0, 1)),
    release_consistency_passed       INTEGER NOT NULL CHECK (release_consistency_passed IN (0, 1)),
    implementation_consistency_passed INTEGER NOT NULL CHECK (implementation_consistency_passed IN (0, 1)),
    privacy_passed                   INTEGER NOT NULL CHECK (privacy_passed IN (0, 1)),
    zero_write_passed                INTEGER NOT NULL CHECK (zero_write_passed IN (0, 1)),
    quantity_coverage_passed         INTEGER NOT NULL CHECK (quantity_coverage_passed IN (0, 1)),
    contamination_passed             INTEGER NOT NULL CHECK (contamination_passed IN (0, 1))
);
CREATE INDEX IF NOT EXISTS protection_assessments_by_recommendation
    ON protection_assessments(recommendation);
CREATE INDEX IF NOT EXISTS protection_assessments_by_policy_version
    ON protection_assessments(policy_version);
"""

_SEP = "\x1f"


def _join(values: tuple) -> str:
    return _SEP.join(values)


def _split(value: str) -> tuple[str, ...]:
    return tuple(value.split(_SEP)) if value else ()


def _join_pairs(mapping) -> str:
    return _SEP.join(f"{key}={value}" for key, value in sorted(mapping.items()))


def _split_pairs(value: str) -> dict[str, str]:
    if not value:
        return {}
    pairs = {}
    for item in value.split(_SEP):
        key, _, val = item.partition("=")
        pairs[key] = val
    return pairs


@dataclass(frozen=True)
class AssessmentSummary:
    """A privacy-safe, read-only projection of one stored assessment row.

    Every field is already an enum, a reference, a bool, or a bounded
    scalar on `ProtectionGroundingAssessment` -- this summary adds nothing
    new and removes nothing that was not already safe to expose.
    """

    assessment_id: str
    recorded_sequence: int
    assessment_version: str
    generated_at_monotonic: float
    policy_version: str
    release_identity: str
    implementation_digest: str
    account_fingerprint_reference: str
    candidate_observation_ids: tuple[str, ...]
    included_observation_ids: tuple[str, ...]
    excluded_observation_ids: tuple[str, ...]
    informational_observation_ids: tuple[str, ...]
    exclusion_reasons: dict[str, str]
    distinct_run_count: int
    distinct_observation_time_count: int
    broker_authoritative_observation_count: int
    qualifying_observation_count: int
    recommendation: PromotionOutcome
    reason_codes: tuple[str, ...]
    assessment_digest: str
    source_authority_passed: bool
    freshness_passed: bool
    account_consistency_passed: bool
    release_consistency_passed: bool
    implementation_consistency_passed: bool
    privacy_passed: bool
    zero_write_passed: bool
    quantity_coverage_passed: bool
    contamination_passed: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "assessment_id": self.assessment_id,
            "recorded_sequence": self.recorded_sequence,
            "assessment_version": self.assessment_version,
            "generated_at_monotonic": self.generated_at_monotonic,
            "policy_version": self.policy_version,
            "release_identity": self.release_identity,
            "implementation_digest": self.implementation_digest,
            "account_fingerprint_reference": self.account_fingerprint_reference,
            "candidate_observation_ids": list(self.candidate_observation_ids),
            "included_observation_ids": list(self.included_observation_ids),
            "excluded_observation_ids": list(self.excluded_observation_ids),
            "informational_observation_ids": list(self.informational_observation_ids),
            "exclusion_reasons": dict(self.exclusion_reasons),
            "distinct_run_count": self.distinct_run_count,
            "distinct_observation_time_count": self.distinct_observation_time_count,
            "broker_authoritative_observation_count": (
                self.broker_authoritative_observation_count
            ),
            "qualifying_observation_count": self.qualifying_observation_count,
            "recommendation": self.recommendation.value,
            "reason_codes": list(self.reason_codes),
            "assessment_digest": self.assessment_digest,
            "source_authority_passed": self.source_authority_passed,
            "freshness_passed": self.freshness_passed,
            "account_consistency_passed": self.account_consistency_passed,
            "release_consistency_passed": self.release_consistency_passed,
            "implementation_consistency_passed": self.implementation_consistency_passed,
            "privacy_passed": self.privacy_passed,
            "zero_write_passed": self.zero_write_passed,
            "quantity_coverage_passed": self.quantity_coverage_passed,
            "contamination_passed": self.contamination_passed,
            "promotion_permitted": False,
            "protection_grounded": False,
        }


_COLUMNS = (
    "assessment_id", "recorded_sequence", "assessment_version",
    "generated_at_monotonic", "policy_version", "release_identity",
    "implementation_digest", "account_fingerprint_reference",
    "candidate_observation_ids", "included_observation_ids",
    "excluded_observation_ids", "informational_observation_ids",
    "exclusion_reasons", "distinct_run_count",
    "distinct_observation_time_count",
    "broker_authoritative_observation_count", "qualifying_observation_count",
    "recommendation", "reason_codes", "assessment_digest",
    "source_authority_passed", "freshness_passed",
    "account_consistency_passed", "release_consistency_passed",
    "implementation_consistency_passed", "privacy_passed",
    "zero_write_passed", "quantity_coverage_passed", "contamination_passed",
)


def _row_to_summary(row: tuple) -> AssessmentSummary:
    values = dict(zip(_COLUMNS, row))
    return AssessmentSummary(
        assessment_id=values["assessment_id"],
        recorded_sequence=int(values["recorded_sequence"]),
        assessment_version=values["assessment_version"],
        generated_at_monotonic=float(values["generated_at_monotonic"]),
        policy_version=values["policy_version"],
        release_identity=values["release_identity"],
        implementation_digest=values["implementation_digest"],
        account_fingerprint_reference=values["account_fingerprint_reference"],
        candidate_observation_ids=_split(values["candidate_observation_ids"]),
        included_observation_ids=_split(values["included_observation_ids"]),
        excluded_observation_ids=_split(values["excluded_observation_ids"]),
        informational_observation_ids=_split(
            values["informational_observation_ids"]
        ),
        exclusion_reasons=_split_pairs(values["exclusion_reasons"]),
        distinct_run_count=int(values["distinct_run_count"]),
        distinct_observation_time_count=int(
            values["distinct_observation_time_count"]
        ),
        broker_authoritative_observation_count=int(
            values["broker_authoritative_observation_count"]
        ),
        qualifying_observation_count=int(values["qualifying_observation_count"]),
        recommendation=PromotionOutcome(values["recommendation"]),
        reason_codes=_split(values["reason_codes"]),
        assessment_digest=values["assessment_digest"],
        source_authority_passed=bool(values["source_authority_passed"]),
        freshness_passed=bool(values["freshness_passed"]),
        account_consistency_passed=bool(values["account_consistency_passed"]),
        release_consistency_passed=bool(values["release_consistency_passed"]),
        implementation_consistency_passed=bool(
            values["implementation_consistency_passed"]
        ),
        privacy_passed=bool(values["privacy_passed"]),
        zero_write_passed=bool(values["zero_write_passed"]),
        quantity_coverage_passed=bool(values["quantity_coverage_passed"]),
        contamination_passed=bool(values["contamination_passed"]),
    )


class ProtectionAssessmentStore:
    """Cross-run, append-only, non-authoritative assessment index."""

    def __init__(self, database_path: Path) -> None:
        if not isinstance(database_path, Path):
            raise ProtectionAssessmentStoreError("a database path is required")
        if is_repository_data_path(database_path):
            raise ProtectionAssessmentStoreError(
                "the protection assessment store may not be opened inside "
                "repository data"
            )
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
        except Exception:
            self._connection.close()
            raise

    def _assert_schema_version(self) -> None:
        row = self._connection.execute(
            "SELECT value FROM protection_assessment_meta "
            "WHERE key = 'schema_version'"
        ).fetchone()
        if row is None:
            self._connection.execute(
                "INSERT INTO protection_assessment_meta (key, value) "
                "VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            return
        if row[0] != str(SCHEMA_VERSION):
            raise ProtectionAssessmentStoreError(
                "protection assessment store schema version is unsupported"
            )

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "ProtectionAssessmentStore":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def record_assessment(
        self, assessment: ProtectionGroundingAssessment
    ) -> AssessmentSummary:
        """Append one assessment. Idempotent on `assessment_id` re-delivery
        (it is a content digest, so re-recording an identical assessment is
        always safe and never overwrites a differing row under conflict)."""
        if not isinstance(assessment, ProtectionGroundingAssessment):
            raise ProtectionAssessmentStoreError(
                "only a ProtectionGroundingAssessment may be recorded"
            )
        existing = self._connection.execute(
            "SELECT assessment_digest FROM protection_assessments "
            "WHERE assessment_id = ?",
            (assessment.assessment_id,),
        ).fetchone()
        if existing is not None:
            if existing[0] != assessment.assessment_digest:
                raise ProtectionAssessmentStoreError(
                    "an assessment with this id already exists with a "
                    "different assessment digest; the original was not "
                    "modified"
                )
            return self.get_assessment(assessment.assessment_id)

        remaining_placeholders = ", ".join("?" * (len(_COLUMNS) - 2))
        self._connection.execute(
            "INSERT INTO protection_assessments (" + ", ".join(_COLUMNS) + ") "
            "VALUES (?, (SELECT COALESCE(MAX(recorded_sequence), 0) + 1 "
            "FROM protection_assessments), " + remaining_placeholders + ")",
            (
                assessment.assessment_id,
                assessment.assessment_version,
                assessment.generated_at_monotonic,
                assessment.policy_version,
                assessment.release_identity,
                assessment.implementation_digest,
                assessment.account_fingerprint_reference,
                _join(assessment.candidate_observation_ids),
                _join(assessment.included_observation_ids),
                _join(assessment.excluded_observation_ids),
                _join(assessment.informational_observation_ids),
                _join_pairs(assessment.exclusion_reasons),
                assessment.distinct_run_count,
                assessment.distinct_observation_time_count,
                assessment.broker_authoritative_observation_count,
                assessment.qualifying_observation_count,
                assessment.recommendation.value,
                _join(assessment.reason_codes),
                assessment.assessment_digest,
                int(assessment.source_authority_analysis.passed),
                int(assessment.freshness_analysis.passed),
                int(assessment.account_consistency_analysis.passed),
                int(assessment.release_consistency_analysis.passed),
                int(assessment.implementation_consistency_analysis.passed),
                int(assessment.privacy_analysis.passed),
                int(assessment.zero_write_analysis.passed),
                int(assessment.quantity_coverage_analysis.passed),
                int(assessment.contamination_analysis.passed),
            ),
        )
        return self.get_assessment(assessment.assessment_id)

    def get_assessment(self, assessment_id: str) -> AssessmentSummary:
        row = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_assessments "
            "WHERE assessment_id = ?",
            (assessment_id,),
        ).fetchone()
        if row is None:
            raise ProtectionAssessmentStoreError("assessment is not present")
        return _row_to_summary(row)

    def all_assessments(self) -> list[AssessmentSummary]:
        rows = self._connection.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM protection_assessments "
            "ORDER BY recorded_sequence"
        ).fetchall()
        return [_row_to_summary(row) for row in rows]


__all__ = [
    "ProtectionAssessmentStoreError",
    "SCHEMA_VERSION",
    "AssessmentSummary",
    "ProtectionAssessmentStore",
]
