"""M13 Issue 7: read-only projections of protection evidence for the
operator application.

Consumes the existing `ProtectionObservationLedger` (Issue 2) and the new
`ProtectionAssessmentStore` (Issue 7) unchanged -- this module performs no
broker call, no checkpoint execution, and no promotion/grounding
recomputation of its own. It is a query/filter/paginate layer only, exactly
like `src/operator_app_store.py`'s existing `list_snapshots`/`list_events`
helpers.

Every dict this module returns is already built from allowlisted, privacy-
safe fields on `LedgerObservationSummary` / `AssessmentSummary` -- neither of
those types carries a raw account number, order ID, position ID, token, or
session material, so nothing here can leak one either.
"""

from __future__ import annotations

from pathlib import Path

from src.protection_assessment_store import (
    AssessmentSummary,
    ProtectionAssessmentStore,
    ProtectionAssessmentStoreError,
)
from src.protection_observation_ledger import (
    LedgerObservationSummary,
    ProtectionLedgerError,
    ProtectionObservationLedger,
)
from src.read_only_broker_state import SnapshotFreshness


class ProtectionEvidenceQueryError(ValueError):
    """A malformed filter or pagination request. Never a broker condition."""


#: Filters accepted by `list_protection_observations`.
OBSERVATION_FILTERS = frozenset(
    {"source_class", "normalized_state", "freshness", "run_id"}
)

#: Filters accepted by `list_protection_assessments`.
ASSESSMENT_FILTERS = frozenset({"recommendation", "policy_version"})


def _paginate(items: list, *, page: int, page_size: int) -> tuple[list, int]:
    if page < 1 or page_size < 1 or page_size > 200:
        raise ProtectionEvidenceQueryError("pagination is invalid")
    total = len(items)
    start = (page - 1) * page_size
    return items[start : start + page_size], total


def observation_to_dict(summary: LedgerObservationSummary) -> dict[str, object]:
    return {
        "observation_id": summary.observation_id,
        "run_id": summary.run_id,
        "observation_sequence": summary.observation_sequence,
        "recorded_sequence": summary.recorded_sequence,
        "observed_at_monotonic": summary.observed_at_monotonic,
        "release_identity": summary.release_identity,
        "implementation_digest": summary.implementation_digest,
        "account_fingerprint_reference": summary.account_fingerprint_reference,
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


def list_protection_observations(
    ledger_path: Path,
    *,
    source_class: str | None = None,
    normalized_state: str | None = None,
    freshness: str | None = None,
    run_id: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[LedgerObservationSummary], int]:
    """Filtered, paginated cross-run observation query.

    Reuses `ProtectionObservationLedger.all_observations()` unchanged; the
    caller-supplied `run_id` for a query is not itself the ledger's "open
    run" identity -- a throwaway identity is used to open the ledger for a
    read-only query, matching this module's read-only contract.
    """
    try:
        with ProtectionObservationLedger(
            ledger_path, "operator-evidence-query"
        ) as ledger:
            items = ledger.all_observations()
    except ProtectionLedgerError as exc:
        raise ProtectionEvidenceQueryError(str(exc)) from exc

    if source_class is not None:
        items = [i for i in items if i.evidence_source_class.value == source_class]
    if normalized_state is not None:
        items = [
            i for i in items if i.normalized_protection_state.value == normalized_state
        ]
    if freshness is not None:
        try:
            wanted = SnapshotFreshness(freshness)
        except ValueError as exc:
            raise ProtectionEvidenceQueryError("freshness filter is invalid") from exc
        items = [i for i in items if i.data_freshness is wanted]
    if run_id is not None:
        items = [i for i in items if i.run_id == run_id]

    return _paginate(items, page=page, page_size=page_size)


def get_protection_observation(
    ledger_path: Path, observation_id: str
) -> LedgerObservationSummary:
    try:
        with ProtectionObservationLedger(
            ledger_path, "operator-evidence-query"
        ) as ledger:
            items = ledger.all_observations()
    except ProtectionLedgerError as exc:
        raise ProtectionEvidenceQueryError(str(exc)) from exc
    for item in items:
        if item.observation_id == observation_id:
            return item
    raise ProtectionEvidenceQueryError("observation is not present")


def linked_assessment_ids(
    assessment_store_path: Path, observation_id: str
) -> tuple[str, ...]:
    """Assessment IDs whose candidate evidence set included this
    observation. Descriptive only -- never a promotion recomputation."""
    try:
        with ProtectionAssessmentStore(assessment_store_path) as store:
            assessments = store.all_assessments()
    except ProtectionAssessmentStoreError:
        return ()
    return tuple(
        a.assessment_id
        for a in assessments
        if observation_id in a.candidate_observation_ids
    )


def list_protection_assessments(
    assessment_store_path: Path,
    *,
    recommendation: str | None = None,
    policy_version: str | None = None,
    page: int = 1,
    page_size: int = 25,
) -> tuple[list[AssessmentSummary], int]:
    try:
        with ProtectionAssessmentStore(assessment_store_path) as store:
            items = store.all_assessments()
    except ProtectionAssessmentStoreError as exc:
        raise ProtectionEvidenceQueryError(str(exc)) from exc

    if recommendation is not None:
        items = [i for i in items if i.recommendation.value == recommendation]
    if policy_version is not None:
        items = [i for i in items if i.policy_version == policy_version]

    return _paginate(items, page=page, page_size=page_size)


def get_protection_assessment(
    assessment_store_path: Path, assessment_id: str
) -> AssessmentSummary:
    try:
        with ProtectionAssessmentStore(assessment_store_path) as store:
            return store.get_assessment(assessment_id)
    except ProtectionAssessmentStoreError as exc:
        raise ProtectionEvidenceQueryError(str(exc)) from exc


__all__ = [
    "ProtectionEvidenceQueryError",
    "OBSERVATION_FILTERS",
    "ASSESSMENT_FILTERS",
    "observation_to_dict",
    "list_protection_observations",
    "get_protection_observation",
    "linked_assessment_ids",
    "list_protection_assessments",
    "get_protection_assessment",
]
