"""Canonical protection-observation record (Milestone 13, Issue 1).

Every test here is pure and local. Nothing constructs a session, reaches a
broker, or touches the network. `tmp_path` is not needed — this module has
no I/O of any kind.
"""

from __future__ import annotations

import hashlib

import pytest

from src.live_order_state_model import ProtectiveCoverage
from src.order_lifecycle_model import MappingProvenance
from src.protection_observation_model import (
    AdapterIdentity,
    BROKER_AUTHORITATIVE_SOURCE_CLASSES,
    EvidenceSourceClass,
    LIVE_READ_ONLY_ADAPTER_IDENTITIES,
    ObservationClockSource,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    ObservationQualificationStatus,
    ObservationReasonCode,
    PrivacyRedactionStatus,
    ProtectionObservationError,
    ProtectionObservationRecord,
    ProtectiveOrderType,
    ProtectiveSide,
    QuantityRelationship,
    TEST_ONLY_SOURCE_CLASSES,
    verify_evidence_digest,
)
from src.read_only_broker_state import SnapshotFreshness


def _digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def _valid_kwargs(**overrides):
    base = dict(
        observation_id="obs-0001",
        run_id="run-0001",
        observation_sequence=1,
        observed_at_monotonic=100.0,
        observation_clock_source=ObservationClockSource.INJECTED_MONOTONIC,
        release_identity="9e90e5cf22674bceddb257f11689c1041d83f2d9",
        implementation_digest=_digest("implementation"),
        checkpoint_version="v1",
        account_fingerprint_reference=_digest("account"),
        broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
        broker_session_identity_reference=_digest("session"),
        symbol_reference=_digest("symbol"),
        position_reference=_digest("position"),
        protective_order_reference=_digest("order"),
        protective_order_type=ProtectiveOrderType.STOP_MARKET,
        protective_side=ProtectiveSide.SELL,
        quantity_relationship=QuantityRelationship.EXACT_COVERAGE,
        observed_order_state="confirmed",
        broker_reported_timestamp=99.5,
        normalized_protection_state=ProtectiveCoverage.INDETERMINATE,
        mapping_provenance=MappingProvenance.ABSENT,
        evidence_source_class=EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION,
        read_count=3,
        write_count=0,
        privacy_redaction_status=PrivacyRedactionStatus.VERIFIED_REDACTED,
        data_freshness=SnapshotFreshness.FRESH,
        consistency_status=ObservationConsistencyStatus.CONSISTENT,
        contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
    )
    base.update(overrides)
    return base


def test_valid_live_read_only_observation_constructs():
    obs = ProtectionObservationRecord(**_valid_kwargs())
    assert obs.qualification_status is ObservationQualificationStatus.STRUCTURALLY_VALID
    assert obs.write_count == 0
    assert obs.is_broker_authoritative_eligible is True
    assert ObservationReasonCode.SOURCE_BROKER_AUTHORITATIVE in obs.reason_codes


def test_no_protective_order_is_a_valid_normal_observation():
    obs = ProtectionObservationRecord(
        **_valid_kwargs(
            normalized_protection_state=ProtectiveCoverage.NOT_PROTECTED,
            protective_order_reference=None,
            observed_order_state="cancelled",
        )
    )
    assert ObservationReasonCode.NO_PROTECTIVE_ORDER_OBSERVED in obs.reason_codes


@pytest.mark.parametrize(
    "field_name,bad_value",
    [
        ("observation_id", "has a space"),
        ("run_id", ""),
        ("observation_sequence", 0),
        ("observation_sequence", -1),
        ("release_identity", "not-a-sha"),
        ("implementation_digest", "short"),
        ("checkpoint_version", ""),
        ("account_fingerprint_reference", "not64hex"),
        ("symbol_reference", "not64hex"),
        ("position_reference", "not64hex"),
        ("protective_order_reference", "not64hex"),
        ("observed_order_state", ""),
        ("observed_order_state", None),
        ("read_count", -1),
        ("read_count", "3"),
    ],
)
def test_invalid_identifiers_and_fields_are_rejected(field_name, bad_value):
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(**_valid_kwargs(**{field_name: bad_value}))


def test_invalid_timestamp_is_rejected():
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(
            **_valid_kwargs(observed_at_monotonic=float("nan"))
        )
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(
            **_valid_kwargs(observed_at_monotonic=float("inf"))
        )
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(**_valid_kwargs(observed_at_monotonic="now"))


def test_missing_or_wrong_type_privacy_status_is_rejected():
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(**_valid_kwargs(privacy_redaction_status="VERIFIED_REDACTED"))
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(
            **_valid_kwargs(privacy_redaction_status=PrivacyRedactionStatus.UNVERIFIED)
        )


def test_unverified_privacy_status_can_never_be_constructed():
    """Structural proof, in the style of the ACTIVATION_APPROVALS-is-empty
    test: no combination of otherwise-valid inputs can produce an instance
    whose privacy_redaction_status is not VERIFIED_REDACTED."""
    for status in PrivacyRedactionStatus:
        if status is PrivacyRedactionStatus.VERIFIED_REDACTED:
            ProtectionObservationRecord(**_valid_kwargs(privacy_redaction_status=status))
        else:
            with pytest.raises(ProtectionObservationError):
                ProtectionObservationRecord(
                    **_valid_kwargs(privacy_redaction_status=status)
                )


def test_nonzero_write_count_can_never_be_constructed():
    for write_count in (1, 2, 100):
        with pytest.raises(ProtectionObservationError):
            ProtectionObservationRecord(**_valid_kwargs(write_count=write_count))


def test_unsupported_evidence_source_is_rejected():
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(**_valid_kwargs(evidence_source_class="LIVE"))


def test_simulated_and_fixture_sources_are_not_broker_authoritative_eligible():
    for source in TEST_ONLY_SOURCE_CLASSES:
        obs = ProtectionObservationRecord(
            **_valid_kwargs(
                evidence_source_class=source,
                broker_adapter_identity=AdapterIdentity.SIMULATED_BROKER_TRANSPORT,
            )
        )
        assert obs.is_broker_authoritative_eligible is False
        assert source not in BROKER_AUTHORITATIVE_SOURCE_CLASSES


def test_adapter_source_mismatch_requires_contaminated_status():
    # Adapter says live, source class says not — must be flagged contaminated.
    with pytest.raises(ProtectionObservationError):
        ProtectionObservationRecord(
            **_valid_kwargs(
                broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
                evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
                contamination_status=ObservationContaminationStatus.NOT_CONTAMINATED,
            )
        )
    # Declaring it contaminated is accepted.
    obs = ProtectionObservationRecord(
        **_valid_kwargs(
            broker_adapter_identity=AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME,
            evidence_source_class=EvidenceSourceClass.SIMULATED_BROKER,
            contamination_status=ObservationContaminationStatus.CONTAMINATED,
        )
    )
    assert ObservationReasonCode.ADAPTER_SOURCE_MISMATCH in obs.reason_codes
    assert obs.is_broker_authoritative_eligible is False


def test_adapter_identity_consistency_constant_matches_source_classes():
    assert LIVE_READ_ONLY_ADAPTER_IDENTITIES == frozenset(
        {AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME}
    )


def test_evidence_digest_is_stable_across_equal_reconstruction():
    kwargs = _valid_kwargs()
    first = ProtectionObservationRecord(**kwargs)
    second = ProtectionObservationRecord(**kwargs)
    assert first.evidence_digest == second.evidence_digest
    assert verify_evidence_digest(first)
    assert verify_evidence_digest(second)


def test_canonical_bytes_and_encode_canonical_value_are_public_and_reusable():
    """M13 Issue 4's grounding-assessment engine needs the same canonical
    encoding for its own digest (never build a second opinion). Promoted
    from module-private in this issue; this regression test pins the
    public names and confirms determinism directly, independent of the
    observation record's own digest tests above."""
    from src.protection_observation_model import canonical_bytes, encode_canonical_value

    assert encode_canonical_value(None) == "\x00"
    assert encode_canonical_value(True) == "1"
    assert encode_canonical_value(False) == "0"
    assert encode_canonical_value("x") == "x"
    assert encode_canonical_value([1, "a"]) == "1\x1ea"

    payload = {"b": 2, "a": 1}
    assert canonical_bytes(payload) == canonical_bytes({"a": 1, "b": 2})
    assert canonical_bytes(payload) != canonical_bytes({"a": 1, "b": 3})


def test_evidence_digest_changes_when_a_field_changes():
    baseline = ProtectionObservationRecord(**_valid_kwargs())
    changed = ProtectionObservationRecord(**_valid_kwargs(read_count=4))
    assert baseline.evidence_digest != changed.evidence_digest


def test_record_is_frozen_and_cannot_be_mutated():
    obs = ProtectionObservationRecord(**_valid_kwargs())
    with pytest.raises(Exception):
        obs.read_count = 99  # type: ignore[misc]


def test_canonical_dict_is_json_serializable_by_the_caller():
    obs = ProtectionObservationRecord(**_valid_kwargs())
    import json

    payload = json.loads(json.dumps(obs.to_canonical_dict()))
    assert payload["observation_id"] == "obs-0001"
    assert payload["write_count"] == 0
    assert payload["qualification_status"] == "STRUCTURALLY_VALID"


def test_observed_order_state_is_normalized():
    obs = ProtectionObservationRecord(
        **_valid_kwargs(observed_order_state="  Confirmed  ")
    )
    assert obs.observed_order_state == "confirmed"
