"""Deterministic fixtures for protection-observation tests (M13 Issue 2).

Shared by `tests/test_protection_observation_ledger.py` and later M13 test
modules (promotion rubric, assessment engine, checkpoint integration) so
every scenario means the same thing everywhere it is used. Pure: no I/O, no
clock, no broker.
"""

from __future__ import annotations

import hashlib

from src.live_order_state_model import ProtectiveCoverage
from src.order_lifecycle_model import MappingProvenance
from src.protection_observation_model import (
    AdapterIdentity,
    ObservationClockSource,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    EvidenceSourceClass,
    PrivacyRedactionStatus,
    ProtectionObservationRecord,
    ProtectiveOrderType,
    ProtectiveSide,
    QuantityRelationship,
)
from src.read_only_broker_state import SnapshotFreshness

RELEASE_SHA = "9e90e5cf22674bceddb257f11689c1041d83f2d9"


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def make_observation(
    *,
    observation_id: str,
    run_id: str,
    observation_sequence: int = 1,
    observed_at_monotonic: float = 100.0,
    account_fingerprint_reference: str | None = None,
    implementation_digest: str | None = None,
    normalized_protection_state: ProtectiveCoverage = ProtectiveCoverage.NOT_PROTECTED,
    observed_order_state: str = "cancelled",
    evidence_source_class: EvidenceSourceClass = (
        EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION
    ),
    broker_adapter_identity: AdapterIdentity = (
        AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME
    ),
    data_freshness: SnapshotFreshness = SnapshotFreshness.FRESH,
    consistency_status: ObservationConsistencyStatus = (
        ObservationConsistencyStatus.CONSISTENT
    ),
    contamination_status: ObservationContaminationStatus = (
        ObservationContaminationStatus.NOT_CONTAMINATED
    ),
    protective_order_reference: str | None = None,
    quantity_relationship: QuantityRelationship = QuantityRelationship.NO_POSITION,
    read_count: int = 3,
) -> ProtectionObservationRecord:
    return ProtectionObservationRecord(
        observation_id=observation_id,
        run_id=run_id,
        observation_sequence=observation_sequence,
        observed_at_monotonic=observed_at_monotonic,
        observation_clock_source=ObservationClockSource.INJECTED_MONOTONIC,
        release_identity=RELEASE_SHA,
        implementation_digest=implementation_digest or digest("implementation"),
        checkpoint_version="v1",
        account_fingerprint_reference=(
            account_fingerprint_reference or digest("account")
        ),
        broker_adapter_identity=broker_adapter_identity,
        broker_session_identity_reference=digest(f"session-{run_id}"),
        symbol_reference=digest("AAPL"),
        position_reference=digest(f"position-{run_id}"),
        protective_order_reference=protective_order_reference,
        protective_order_type=(
            ProtectiveOrderType.STOP_MARKET
            if protective_order_reference
            else ProtectiveOrderType.UNKNOWN
        ),
        protective_side=(
            ProtectiveSide.SELL if protective_order_reference else ProtectiveSide.UNKNOWN
        ),
        quantity_relationship=quantity_relationship,
        observed_order_state=observed_order_state,
        broker_reported_timestamp=observed_at_monotonic - 0.5,
        normalized_protection_state=normalized_protection_state,
        mapping_provenance=MappingProvenance.ABSENT,
        evidence_source_class=evidence_source_class,
        read_count=read_count,
        write_count=0,
        privacy_redaction_status=PrivacyRedactionStatus.VERIFIED_REDACTED,
        data_freshness=data_freshness,
        consistency_status=consistency_status,
        contamination_status=contamination_status,
    )


def no_protective_order(run_id: str, observation_id: str = "obs-none") -> (
    ProtectionObservationRecord
):
    """Scenario: NO_PROTECTIVE_ORDER_OBSERVED — the account was flat."""
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
        observed_order_state="cancelled",
        quantity_relationship=QuantityRelationship.NO_POSITION,
    )


def active_protective_order(run_id: str, observation_id: str = "obs-active") -> (
    ProtectionObservationRecord
):
    """Scenario: a stop is resting and covers the position exactly."""
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest(f"order-{observation_id}"),
        quantity_relationship=QuantityRelationship.EXACT_COVERAGE,
    )


def stale_protective_order(run_id: str, observation_id: str = "obs-stale") -> (
    ProtectionObservationRecord
):
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest(f"order-{observation_id}"),
        quantity_relationship=QuantityRelationship.EXACT_COVERAGE,
        data_freshness=SnapshotFreshness.STALE,
    )


def terminal_protective_order(run_id: str, observation_id: str = "obs-terminal") -> (
    ProtectionObservationRecord
):
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
        observed_order_state="filled",
        quantity_relationship=QuantityRelationship.NO_POSITION,
    )


def partial_quantity_coverage(
    run_id: str, observation_id: str = "obs-partial"
) -> ProtectionObservationRecord:
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest(f"order-{observation_id}"),
        quantity_relationship=QuantityRelationship.PARTIAL_COVERAGE,
    )


def over_coverage(run_id: str, observation_id: str = "obs-over") -> (
    ProtectionObservationRecord
):
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest(f"order-{observation_id}"),
        quantity_relationship=QuantityRelationship.OVER_COVERAGE,
    )


def duplicate_protection(run_id: str) -> tuple[
    ProtectionObservationRecord, ProtectionObservationRecord
]:
    """Two distinct protective-order references observed in one cycle."""
    return (
        active_protective_order(run_id, observation_id="obs-dup-a"),
        make_observation(
            observation_id="obs-dup-b",
            run_id=run_id,
            normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
            observed_order_state="confirmed",
            protective_order_reference=digest("order-dup-b"),
            quantity_relationship=QuantityRelationship.OVER_COVERAGE,
        ),
    )


def conflicting_states(run_id: str) -> tuple[
    ProtectionObservationRecord, ProtectionObservationRecord
]:
    """Two observations of the same position disagreeing about protection."""
    return (
        active_protective_order(run_id, observation_id="obs-conflict-a"),
        no_protective_order(run_id, observation_id="obs-conflict-b"),
    )


def mismatched_account_fingerprint(
    run_id: str, observation_id: str = "obs-account-mismatch"
) -> ProtectionObservationRecord:
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        account_fingerprint_reference=digest("a-different-account"),
    )


def mismatched_implementation_digest(
    run_id: str, observation_id: str = "obs-digest-mismatch"
) -> ProtectionObservationRecord:
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        implementation_digest=digest("a-different-implementation"),
    )


def simulated_evidence(
    run_id: str, observation_id: str = "obs-simulated"
) -> ProtectionObservationRecord:
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
        broker_adapter_identity=AdapterIdentity.SIMULATED_BROKER_TRANSPORT,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest("order-simulated"),
        quantity_relationship=QuantityRelationship.EXACT_COVERAGE,
    )


def imported_evidence(
    run_id: str, observation_id: str = "obs-imported"
) -> ProtectionObservationRecord:
    return make_observation(
        observation_id=observation_id,
        run_id=run_id,
        evidence_source_class=EvidenceSourceClass.IMPORTED_HISTORICAL_RECORD,
        broker_adapter_identity=AdapterIdentity.UNKNOWN,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        observed_order_state="confirmed",
        protective_order_reference=digest("order-imported"),
        quantity_relationship=QuantityRelationship.EXACT_COVERAGE,
    )
