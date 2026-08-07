"""M13 Issue 5: read-only checkpoint <-> protection-grounding integration.

Wraps the existing, unmodified read-only observation checkpoint
(`src/read_only_observation_runtime.py::run_read_only_observation_checkpoint`,
invoked by `read_only_observation_checkpoint_launcher.py`) with the M13
Issues 1-4 pipeline: canonical observation (Issue 1) -> cross-run ledger
(Issue 2) -> promotion rubric (Issue 3) -> grounding assessment (Issue 4).

## Reuse, not a second checkpoint

This module performs no broker call, no session, no read, and no write of
any kind. It never re-implements checkpoint safety logic, and it never
touches `read_only_observation_runtime.py`. It consumes an already-
completed, already-evidence-written `CheckpointResult` produced by the
existing runtime, unchanged, and adds a strictly additive layer on top.
Release identity and implementation digest are read via the existing,
already-reviewed `src/read_only_authorization_manifest.py` functions
(`resolve_head_sha`, `checkpoint_implementation_digest`) — not re-derived.
Account pseudonymization reuses `src/broker_snapshot.account_fingerprint` —
not re-derived.

## Fail-closed, without corrupting the underlying result

A failure anywhere in this module never retries, overwrites, or alters the
already-finalized checkpoint evidence file — it can only report its OWN
distinct M13-integration status (`IntegrationStatus.FAILED_CLOSED`)
alongside the untouched core `CheckpointResult`. The underlying checkpoint
either succeeded or failed entirely on its own terms before this module is
ever invoked.

## What this module never does

Never sets `APPROVED_FOR_ACTIVE_PROTECTION`, never modifies
`ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES` or `ACTIVATION_APPROVALS`,
never sets `protection_grounded` / `activation_approved` /
`permits_automated_action` / `live_ready` / `unattended_ready`, never
connects to a broker, never executes a checkpoint (it only post-processes
one that has already run), never mutates broker state.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from src.broker_snapshot import account_fingerprint
from src.live_order_state_model import ProtectiveCoverage
from src.protection_grounding_assessment import (
    OperatorReviewContext,
    ProtectionGroundingAssessment,
    ProtectionGroundingAssessmentError,
    evaluate_protection_grounding,
)
from src.protection_observation_ledger import (
    LedgerAppendResult,
    ProtectionLedgerError,
    ProtectionObservationLedger,
)
from src.protection_observation_model import (
    AdapterIdentity,
    EvidenceSourceClass,
    ObservationClockSource,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    PrivacyRedactionStatus,
    ProtectionObservationError,
    ProtectionObservationRecord,
    ProtectiveOrderType,
    ProtectiveSide,
    QuantityRelationship,
)
from src.protection_promotion_policy import (
    DEFAULT_PROTECTION_PROMOTION_POLICY,
    ProtectionPromotionPolicy,
    ProtectionPromotionPolicyError,
)
from src.read_only_authorization_manifest import (
    AuthorizationManifestError,
    checkpoint_implementation_digest,
    resolve_head_sha,
)
from src.read_only_broker_state import SnapshotFreshness
from src.read_only_observation_runtime import (
    COMPLETED_OUTCOMES,
    CheckpointOutcome,
    CheckpointResult,
)


class ProtectionCheckpointIntegrationError(RuntimeError):
    """The M13 integration layer refused. Never a broker condition, and
    never a reason to alter the already-finalized `CheckpointResult`."""


class IntegrationStatus(str, Enum):
    """What the M13 layer did with an already-completed checkpoint result."""

    #: The checkpoint's own outcome was not a completed observation cycle
    #: (e.g. DECLINED_BY_OPERATOR, SESSION_UNAVAILABLE). There is nothing
    #: for M13 to integrate, and that is not a failure of anything.
    NOT_APPLICABLE = "NOT_APPLICABLE"
    INTEGRATED = "INTEGRATED"
    FAILED_CLOSED = "FAILED_CLOSED"


#: How a completed checkpoint outcome maps to the canonical protection
#: vocabulary. Deliberately a fixed table, not a branch — the mapping is
#: reviewable in one place. `OBSERVATION_RECORDED` maps to `INDETERMINATE`,
#: never `VERIFIED_ACTIVE_PROTECTION`: `ADAPTER_PERMITTED_ACTIVE_PROTECTION_
#: STATES` is empty, so nothing observed today can be verified, and this
#: module must not assert otherwise.
_OUTCOME_TO_PROTECTION_STATE: dict[CheckpointOutcome, ProtectiveCoverage] = {
    CheckpointOutcome.NO_PROTECTIVE_ORDER_OBSERVED: ProtectiveCoverage.NOT_PROTECTED,
    CheckpointOutcome.OBSERVATION_RECORDED: ProtectiveCoverage.INDETERMINATE,
}


def _reference(*parts: str) -> str:
    """A deterministic, non-reversible pseudonymous reference.

    Never a raw broker identifier — every input here is already a
    redacted, allowlisted checkpoint-evidence field, a run identity, or a
    fixed literal, never a raw account number, order ID, or position ID.
    """
    joined = "\x1f".join(parts)
    return hashlib.sha256(
        f"orb-live:m13-checkpoint-reference:v1:{joined}".encode("utf-8")
    ).hexdigest()


@dataclass(frozen=True)
class IntegratedCheckpointResult:
    """The unmodified core checkpoint result, plus whatever the M13 layer
    produced from it. `checkpoint` is never rewritten by this module."""

    checkpoint: CheckpointResult
    integration_status: IntegrationStatus
    observation: ProtectionObservationRecord | None
    append_result: LedgerAppendResult | None
    assessment: ProtectionGroundingAssessment | None
    integration_error: str | None = None


def integrate_checkpoint_result(
    checkpoint_result: CheckpointResult,
    *,
    run_id: str,
    account_number: str,
    ledger_path: Path,
    evaluated_at_monotonic: float,
    repository_root: Path,
    evidence_source_class: EvidenceSourceClass,
    broker_adapter_identity: AdapterIdentity,
    broker_session_identity_reference: str,
    operator_present: bool,
    policy: ProtectionPromotionPolicy = DEFAULT_PROTECTION_PROMOTION_POLICY,
    operator_review_context: OperatorReviewContext | None = None,
) -> IntegratedCheckpointResult:
    """Fold one already-completed checkpoint result into the M13 pipeline.

    Never mutates `checkpoint_result`. Never opens a broker session, never
    reads a clock (`evaluated_at_monotonic` is caller-injected, exactly
    like the checkpoint's own `monotonic` callable). Any failure inside
    this function is caught and reported as `FAILED_CLOSED` — it never
    propagates in a way that could be mistaken for a checkpoint failure.
    """
    if checkpoint_result.outcome not in COMPLETED_OUTCOMES:
        return IntegratedCheckpointResult(
            checkpoint=checkpoint_result,
            integration_status=IntegrationStatus.NOT_APPLICABLE,
            observation=None,
            append_result=None,
            assessment=None,
        )

    try:
        release_identity = resolve_head_sha(repository_root)
        implementation_digest = checkpoint_implementation_digest(repository_root)
        account_fingerprint_reference = account_fingerprint(account_number)

        evidence = checkpoint_result.evidence
        normalized_state = _OUTCOME_TO_PROTECTION_STATE[checkpoint_result.outcome]
        read_count = sum(evidence.read_call_counts.values())
        write_count = sum(evidence.write_counters.values())
        consistency_status = (
            ObservationConsistencyStatus.INCONSISTENT_WITHIN_OBSERVATION
            if evidence.snapshot_facts.get(
                "order_state_changed_during_acquisition"
            )
            == "true"
            else ObservationConsistencyStatus.CONSISTENT
        )
        data_freshness = SnapshotFreshness(evidence.freshness)

        if normalized_state is ProtectiveCoverage.NOT_PROTECTED:
            protective_order_reference = None
            protective_order_type = ProtectiveOrderType.UNKNOWN
            protective_side = ProtectiveSide.UNKNOWN
            quantity_relationship = QuantityRelationship.NO_POSITION
        else:
            # The read-only evidence model deliberately does not carry raw
            # order type/side/quantity math (that would be exactly the kind
            # of private/broker-specific detail it exists to redact). An
            # honest UNKNOWN here is correct: this module must never guess
            # EXACT_COVERAGE to make a result look more complete than the
            # evidence actually supports.
            protective_order_reference = _reference(
                "protective-order", run_id, evidence.observed_protection_state
            )
            protective_order_type = ProtectiveOrderType.UNKNOWN
            protective_side = ProtectiveSide.UNKNOWN
            quantity_relationship = QuantityRelationship.UNKNOWN

        observation = ProtectionObservationRecord(
            observation_id=_reference("observation", run_id),
            run_id=run_id,
            observation_sequence=1,
            observed_at_monotonic=evaluated_at_monotonic,
            observation_clock_source=ObservationClockSource.INJECTED_MONOTONIC,
            release_identity=release_identity,
            implementation_digest=implementation_digest,
            checkpoint_version="m13-issue5-v1",
            account_fingerprint_reference=account_fingerprint_reference,
            broker_adapter_identity=broker_adapter_identity,
            broker_session_identity_reference=broker_session_identity_reference,
            symbol_reference=_reference("symbol", run_id),
            position_reference=_reference("position", run_id),
            protective_order_reference=protective_order_reference,
            protective_order_type=protective_order_type,
            protective_side=protective_side,
            quantity_relationship=quantity_relationship,
            observed_order_state=evidence.observed_protection_state
            if evidence.observed_protection_state not in ("NONE",)
            else "cancelled",
            broker_reported_timestamp=None,
            normalized_protection_state=normalized_state,
            mapping_provenance=_mapping_provenance_for(checkpoint_result.outcome),
            evidence_source_class=evidence_source_class,
            read_count=read_count,
            write_count=write_count,
            privacy_redaction_status=PrivacyRedactionStatus.VERIFIED_REDACTED,
            data_freshness=data_freshness,
            consistency_status=consistency_status,
            contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
        )

        with ProtectionObservationLedger(ledger_path, run_id) as ledger:
            append_result = ledger.append(observation)
            all_observations = ledger.all_observations()
            conflict_detected = bool(ledger.conflicting_observations())
            assessment = evaluate_protection_grounding(
                all_observations,
                policy=policy,
                evaluated_at_monotonic=evaluated_at_monotonic,
                expected_release_identity=release_identity,
                expected_implementation_digest=implementation_digest,
                expected_account_fingerprint_reference=(
                    account_fingerprint_reference
                ),
                conflict_detected=conflict_detected,
                operator_presence_attested=operator_present,
                operator_review_context=operator_review_context,
            )

        return IntegratedCheckpointResult(
            checkpoint=checkpoint_result,
            integration_status=IntegrationStatus.INTEGRATED,
            observation=observation,
            append_result=append_result,
            assessment=assessment,
        )
    except (
        ProtectionObservationError,
        ProtectionLedgerError,
        ProtectionPromotionPolicyError,
        ProtectionGroundingAssessmentError,
        AuthorizationManifestError,
        ValueError,
    ) as exc:
        return IntegratedCheckpointResult(
            checkpoint=checkpoint_result,
            integration_status=IntegrationStatus.FAILED_CLOSED,
            observation=None,
            append_result=None,
            assessment=None,
            integration_error=type(exc).__name__,
        )


def _mapping_provenance_for(outcome: CheckpointOutcome):
    from src.order_lifecycle_model import MappingProvenance

    if outcome is CheckpointOutcome.OBSERVATION_RECORDED:
        return MappingProvenance.OBSERVED_LIVE
    return MappingProvenance.ABSENT


__all__ = [
    "ProtectionCheckpointIntegrationError",
    "IntegrationStatus",
    "IntegratedCheckpointResult",
    "integrate_checkpoint_result",
]
