"""Canonical protection-observation record (Milestone 13, Issue 1).

PURE. OFFLINE. No broker, network, credential, token, clock, or database
access. This module defines the one canonical, immutable shape a protection
observation may take, so the ledger (Issue 2), the promotion rubric
(Issue 3), and the grounding assessment engine (Issue 4) all consume the same
reviewed schema rather than each re-deriving their own (never build a second
opinion — `C:\\dev\\orb-live-unattended-delivery-roadmap.md`, principle 12).

This module makes exactly one decision about authority, and it is a refusal:
an instance of `ProtectionObservationRecord` can never represent a broker write.
`write_count` is validated to be exactly zero at construction — there is no
constructor path that produces an instance claiming otherwise. Everything
else here is classification and evidence, not authority. This type has no
field named `protection_grounded`, `activation_approved`,
`permits_automated_action`, `live_ready`, or `unattended_ready`, and nothing
downstream may synthesize one from it.

## What this is not

- Not a promotion decision. See `protection_promotion_policy.py` (Issue 3).
- Not a grounding assessment. See `protection_grounding_assessment.py`
  (Issue 4).
- Not evidentiary qualification (stale / contaminated / insufficient
  *relative to a rubric, across many runs*). `qualification_status` on this
  record is a STRUCTURAL property only — "this object passed its own
  internal validation" — never an evidentiary judgement. Evidentiary
  qualification is computed later, from many observations together, by the
  ledger and the rubric, and is never written back onto this immutable
  record.

## Privacy

No field on this record may carry a credential, a token, a raw account
number, a raw order identifier, a raw position identifier, or a broker
response body. Symbol, position, protective-order, broker-session, and
account identity are represented only as caller-supplied pseudonymous
references (fixed-length hex digests) — this module does not compute them
from raw broker values, it only validates that whatever was supplied looks
like a reference and not a raw identifier. `privacy_redaction_status` is
validated to be `VERIFIED_REDACTED` at construction; there is no constructor
path that produces an instance with an unverified privacy status, mirroring
`ReadOnlyObservationPlan.manufactures_observation` in
`src/read_only_broker_state.py`, which is unrepresentable as `True`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

from src.broker_identity import FINGERPRINT_PATTERN
from src.live_order_state_model import ProtectiveCoverage, normalize_state
from src.order_lifecycle_model import MappingProvenance
from src.read_only_broker_state import SnapshotFreshness


class ProtectionObservationError(ValueError):
    """An observation record was malformed. Never a broker condition."""


# --------------------------------------------------------------------------
# Identifier and digest patterns.
# --------------------------------------------------------------------------

#: Matches `src/one_share_commissioning.py:RUN_ID_PATTERN`. Reused here rather
#: than re-derived so a run identifier means the same thing everywhere it is
#: checked.
OPAQUE_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")

#: A SHA-256 hex digest, used for every pseudonymous reference on this record
#: (symbol, position, protective order, broker session) and for the evidence
#: digest itself. Sixty-four lowercase hex characters, nothing else — never a
#: raw broker identifier, which has no reason to take this shape.
HEX_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")

#: A full commit SHA, as recorded elsewhere in the repository's exact-SHA
#: conventions (`.github/workflows/tests.yml`).
RELEASE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

_DIGEST_DOMAIN = b"orb-live/protection-observation-model/v1"


def _validate_opaque_token(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or OPAQUE_TOKEN_PATTERN.fullmatch(value) is None:
        raise ProtectionObservationError(
            f"{field_name} must be a short alphanumeric token, "
            "matching the repository's run-identifier shape"
        )
    return value


def _validate_hex_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or HEX_DIGEST_PATTERN.fullmatch(value) is None:
        raise ProtectionObservationError(
            f"{field_name} must be a 64-character lowercase SHA-256 hex "
            "digest — a reference, never a raw identifier"
        )
    return value


def _validate_release_sha(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or RELEASE_SHA_PATTERN.fullmatch(value) is None:
        raise ProtectionObservationError(
            f"{field_name} must be a 40-character lowercase commit SHA"
        )
    return value


def _validate_implementation_digest(value: Any, *, field_name: str) -> str:
    if not isinstance(value, str) or FINGERPRINT_PATTERN.fullmatch(value) is None:
        raise ProtectionObservationError(
            f"{field_name} must be a 64-character lowercase SHA-256 hex "
            "digest"
        )
    return value


def _validate_positive_int(value: Any, *, field_name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProtectionObservationError(f"{field_name} must be a non-negative int")
    return value


def _validate_finite_float(value: Any, *, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtectionObservationError(f"{field_name} must be a number")
    numeric = float(value)
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        raise ProtectionObservationError(f"{field_name} must be finite")
    return numeric


# --------------------------------------------------------------------------
# Evidence-source classification.
# --------------------------------------------------------------------------


class EvidenceSourceClass(str, Enum):
    """Where an observation actually came from.

    Only `LIVE_READ_ONLY_BROKER_OBSERVATION` may ever be eligible for future
    broker-authoritative grounding. Every other class is test evidence,
    imported history, or an explicit operator statement, and none of them may
    ever satisfy a live-grounding requirement — that boundary is enforced by
    the promotion rubric (Issue 3), not by this module alone, but this module
    is where the classification is first recorded and never silently
    reclassified.
    """

    LIVE_READ_ONLY_BROKER_OBSERVATION = "LIVE_READ_ONLY_BROKER_OBSERVATION"
    OFFLINE_REPLAY = "OFFLINE_REPLAY"
    SIMULATED_BROKER = "SIMULATED_BROKER"
    TEST_FIXTURE = "TEST_FIXTURE"
    OPERATOR_DECLARATION = "OPERATOR_DECLARATION"
    IMPORTED_HISTORICAL_RECORD = "IMPORTED_HISTORICAL_RECORD"
    UNKNOWN_SOURCE = "UNKNOWN_SOURCE"


#: The only source class eligible for future broker-authoritative grounding.
#: A frozenset, not a boolean method, so eligibility is data a caller can
#: inspect rather than a private judgement call embedded in an if-statement
#: somewhere else.
BROKER_AUTHORITATIVE_SOURCE_CLASSES: frozenset[EvidenceSourceClass] = frozenset(
    {EvidenceSourceClass.LIVE_READ_ONLY_BROKER_OBSERVATION}
)

#: Source classes that are structurally incapable of ever satisfying a live
#: grounding requirement, no matter how many are collected. Simulation and
#: fixtures validate software behaviour; they do not validate a broker.
TEST_ONLY_SOURCE_CLASSES: frozenset[EvidenceSourceClass] = frozenset(
    {
        EvidenceSourceClass.OFFLINE_REPLAY,
        EvidenceSourceClass.SIMULATED_BROKER,
        EvidenceSourceClass.TEST_FIXTURE,
    }
)


class AdapterIdentity(str, Enum):
    """Which production adapter produced this observation, if any.

    `ROBINHOOD_COMMISSIONING_ADAPTER` is enumerated deliberately: it is the
    write-capable adapter (`src/robinhood_commissioning_adapter.py`), and an
    observation claiming to come from it while also claiming a read-only
    evidence source is a contamination signal this module can detect
    structurally rather than trusting the caller's classification alone.
    """

    READ_ONLY_OBSERVATION_RUNTIME = "READ_ONLY_OBSERVATION_RUNTIME"
    ROBINHOOD_COMMISSIONING_ADAPTER = "ROBINHOOD_COMMISSIONING_ADAPTER"
    SIMULATED_BROKER_TRANSPORT = "SIMULATED_BROKER_TRANSPORT"
    TEST_DOUBLE = "TEST_DOUBLE"
    UNKNOWN = "UNKNOWN"


#: Adapter identities consistent with a genuinely live, read-only observation.
LIVE_READ_ONLY_ADAPTER_IDENTITIES: frozenset[AdapterIdentity] = frozenset(
    {AdapterIdentity.READ_ONLY_OBSERVATION_RUNTIME}
)


class ObservationClockSource(str, Enum):
    """How `observed_at_monotonic` and `broker_reported_timestamp` were
    obtained. This module never reads a clock; every timestamp is supplied by
    the caller, matching the offline stack's no-clock-reads discipline."""

    INJECTED_MONOTONIC = "INJECTED_MONOTONIC"
    INJECTED_WALL_CLOCK = "INJECTED_WALL_CLOCK"
    UNKNOWN = "UNKNOWN"


class ProtectiveOrderType(str, Enum):
    STOP_MARKET = "STOP_MARKET"
    STOP_LIMIT = "STOP_LIMIT"
    UNKNOWN = "UNKNOWN"


class ProtectiveSide(str, Enum):
    SELL = "SELL"
    BUY = "BUY"
    UNKNOWN = "UNKNOWN"


class QuantityRelationship(str, Enum):
    """Bucketed, never a raw quantity — matches
    `read_only_broker_state.count_bucket`'s bucketing convention."""

    EXACT_COVERAGE = "EXACT_COVERAGE"
    PARTIAL_COVERAGE = "PARTIAL_COVERAGE"
    OVER_COVERAGE = "OVER_COVERAGE"
    NO_POSITION = "NO_POSITION"
    UNKNOWN = "UNKNOWN"


class PrivacyRedactionStatus(str, Enum):
    """Only `VERIFIED_REDACTED` may ever appear on a constructed instance.

    `UNVERIFIED` exists for documentation and for tests of the guard itself;
    no code path in this module can produce an instance carrying it.
    """

    VERIFIED_REDACTED = "VERIFIED_REDACTED"
    UNVERIFIED = "UNVERIFIED"


class ObservationConsistencyStatus(str, Enum):
    """Whether this one observation was internally self-consistent —
    corresponds to the F2 confirmation-read comparison in
    `read_only_observation_runtime.py`, not to cross-run agreement, which the
    ledger and rubric assess separately."""

    CONSISTENT = "CONSISTENT"
    INCONSISTENT_WITHIN_OBSERVATION = "INCONSISTENT_WITHIN_OBSERVATION"
    UNKNOWN = "UNKNOWN"


class ObservationContaminationStatus(str, Enum):
    NOT_CONTAMINATED = "NOT_CONTAMINATED"
    CONTAMINATED = "CONTAMINATED"
    UNKNOWN = "UNKNOWN"


class ObservationQualificationStatus(str, Enum):
    """A STRUCTURAL property only: did this record pass its own validation.

    `STRUCTURALLY_INVALID` never appears on a constructed instance — an
    invalid record raises `ProtectionObservationError` instead of being
    constructed with this value. It exists so the enum documents the
    rejected case rather than only the accepted one.
    """

    STRUCTURALLY_VALID = "STRUCTURALLY_VALID"
    STRUCTURALLY_INVALID = "STRUCTURALLY_INVALID"


class ObservationReasonCode(str, Enum):
    """Explanatory reason codes. Descriptive only — never authority-bearing."""

    NO_PROTECTIVE_ORDER_OBSERVED = "NO_PROTECTIVE_ORDER_OBSERVED"
    PROTECTIVE_ORDER_OBSERVED = "PROTECTIVE_ORDER_OBSERVED"
    SOURCE_NOT_BROKER_AUTHORITATIVE = "SOURCE_NOT_BROKER_AUTHORITATIVE"
    SOURCE_BROKER_AUTHORITATIVE = "SOURCE_BROKER_AUTHORITATIVE"
    ADAPTER_SOURCE_MISMATCH = "ADAPTER_SOURCE_MISMATCH"
    STATE_INDETERMINATE = "STATE_INDETERMINATE"
    STATE_TERMINAL = "STATE_TERMINAL"
    QUANTITY_COVERAGE_INCOMPLETE = "QUANTITY_COVERAGE_INCOMPLETE"
    DATA_NOT_FRESH = "DATA_NOT_FRESH"
    WITHIN_OBSERVATION_INCONSISTENCY = "WITHIN_OBSERVATION_INCONSISTENCY"


@dataclass(frozen=True)
class ProtectionObservationRecord:
    """One canonical, immutable, privacy-safe protection observation.

    Construction is the only validation gate: every invariant below is
    checked in `__post_init__`, and a record that violates any of them
    cannot exist as a Python object, let alone reach the ledger.
    """

    # Identity.
    observation_id: str
    run_id: str
    observation_sequence: int

    # When and how it was observed. Never read live; always supplied.
    observed_at_monotonic: float
    observation_clock_source: ObservationClockSource

    # Release and implementation identity.
    release_identity: str
    implementation_digest: str
    checkpoint_version: str

    # Pseudonymous references only. Never a raw broker identifier.
    account_fingerprint_reference: str
    broker_adapter_identity: AdapterIdentity
    broker_session_identity_reference: str
    symbol_reference: str
    position_reference: str
    protective_order_reference: str | None

    # Order classification. Bucketed and enum-only — never a raw price,
    # quantity, or broker order identifier.
    protective_order_type: ProtectiveOrderType
    protective_side: ProtectiveSide
    quantity_relationship: QuantityRelationship
    observed_order_state: str
    broker_reported_timestamp: float | None

    # Normalized interpretation, reusing the existing reviewed vocabulary
    # rather than redefining it (see `src/live_order_state_model.py` and
    # `src/order_lifecycle_model.py`).
    normalized_protection_state: ProtectiveCoverage
    mapping_provenance: MappingProvenance

    # Evidence classification.
    evidence_source_class: EvidenceSourceClass
    read_count: int
    write_count: int
    privacy_redaction_status: PrivacyRedactionStatus
    data_freshness: SnapshotFreshness
    consistency_status: ObservationConsistencyStatus
    contamination_status: ObservationContaminationStatus

    # Derived, never caller-authoritative.
    qualification_status: ObservationQualificationStatus = field(
        default=ObservationQualificationStatus.STRUCTURALLY_VALID, init=False
    )
    reason_codes: tuple[ObservationReasonCode, ...] = field(default=(), init=False)
    evidence_digest: str = field(default="", init=False)

    def __post_init__(self) -> None:
        _validate_opaque_token(self.observation_id, field_name="observation_id")
        _validate_opaque_token(self.run_id, field_name="run_id")
        if (
            not isinstance(self.observation_sequence, int)
            or isinstance(self.observation_sequence, bool)
            or self.observation_sequence < 1
        ):
            raise ProtectionObservationError(
                "observation_sequence must be a positive int"
            )

        _validate_finite_float(
            self.observed_at_monotonic, field_name="observed_at_monotonic"
        )
        if not isinstance(self.observation_clock_source, ObservationClockSource):
            raise ProtectionObservationError(
                "observation_clock_source must be an ObservationClockSource"
            )

        _validate_release_sha(self.release_identity, field_name="release_identity")
        _validate_implementation_digest(
            self.implementation_digest, field_name="implementation_digest"
        )
        if (
            not isinstance(self.checkpoint_version, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}", self.checkpoint_version)
        ):
            raise ProtectionObservationError(
                "checkpoint_version must be a short version token"
            )

        _validate_hex_digest(
            self.account_fingerprint_reference,
            field_name="account_fingerprint_reference",
        )
        if not isinstance(self.broker_adapter_identity, AdapterIdentity):
            raise ProtectionObservationError(
                "broker_adapter_identity must be an AdapterIdentity"
            )
        _validate_hex_digest(
            self.broker_session_identity_reference,
            field_name="broker_session_identity_reference",
        )
        _validate_hex_digest(self.symbol_reference, field_name="symbol_reference")
        _validate_hex_digest(self.position_reference, field_name="position_reference")
        if self.protective_order_reference is not None:
            _validate_hex_digest(
                self.protective_order_reference,
                field_name="protective_order_reference",
            )

        if not isinstance(self.protective_order_type, ProtectiveOrderType):
            raise ProtectionObservationError(
                "protective_order_type must be a ProtectiveOrderType"
            )
        if not isinstance(self.protective_side, ProtectiveSide):
            raise ProtectionObservationError(
                "protective_side must be a ProtectiveSide"
            )
        if not isinstance(self.quantity_relationship, QuantityRelationship):
            raise ProtectionObservationError(
                "quantity_relationship must be a QuantityRelationship"
            )

        normalized = normalize_state(self.observed_order_state)
        if not normalized:
            raise ProtectionObservationError(
                "observed_order_state must be a non-blank normalized state"
            )
        object.__setattr__(self, "observed_order_state", normalized)

        if self.broker_reported_timestamp is not None:
            _validate_finite_float(
                self.broker_reported_timestamp,
                field_name="broker_reported_timestamp",
            )

        if not isinstance(self.normalized_protection_state, ProtectiveCoverage):
            raise ProtectionObservationError(
                "normalized_protection_state must be a ProtectiveCoverage"
            )
        if not isinstance(self.mapping_provenance, MappingProvenance):
            raise ProtectionObservationError(
                "mapping_provenance must be a MappingProvenance"
            )

        if not isinstance(self.evidence_source_class, EvidenceSourceClass):
            raise ProtectionObservationError(
                "evidence_source_class must be an EvidenceSourceClass"
            )

        _validate_positive_int(self.read_count, field_name="read_count")
        write_count = _validate_positive_int(self.write_count, field_name="write_count")
        if write_count != 0:
            # Structural, not conventional: this type cannot represent a
            # broker write. There is no configuration or override that
            # changes this.
            raise ProtectionObservationError(
                "a protection observation may never record a non-zero "
                "write_count — this type cannot represent a broker write"
            )

        if not isinstance(self.privacy_redaction_status, PrivacyRedactionStatus):
            raise ProtectionObservationError(
                "privacy_redaction_status must be a PrivacyRedactionStatus"
            )
        if self.privacy_redaction_status is not PrivacyRedactionStatus.VERIFIED_REDACTED:
            raise ProtectionObservationError(
                "an observation may only be constructed once its privacy "
                "redaction has been verified"
            )

        if not isinstance(self.data_freshness, SnapshotFreshness):
            raise ProtectionObservationError(
                "data_freshness must be a SnapshotFreshness"
            )
        if not isinstance(self.consistency_status, ObservationConsistencyStatus):
            raise ProtectionObservationError(
                "consistency_status must be an ObservationConsistencyStatus"
            )
        if not isinstance(
            self.contamination_status, ObservationContaminationStatus
        ):
            raise ProtectionObservationError(
                "contamination_status must be an ObservationContaminationStatus"
            )

        # Structural contamination cross-check: a caller may not claim
        # NOT_CONTAMINATED when the adapter identity and evidence-source
        # class disagree about whether this was genuinely a live read-only
        # broker observation. The caller's classification is trusted for
        # everything this module cannot itself verify; this one combination
        # it can, so it does.
        adapter_says_live = (
            self.broker_adapter_identity in LIVE_READ_ONLY_ADAPTER_IDENTITIES
        )
        source_says_live = (
            self.evidence_source_class
            in BROKER_AUTHORITATIVE_SOURCE_CLASSES
        )
        mismatch = adapter_says_live != source_says_live
        if (
            mismatch
            and self.contamination_status
            is not ObservationContaminationStatus.CONTAMINATED
        ):
            raise ProtectionObservationError(
                "broker_adapter_identity and evidence_source_class disagree "
                "about whether this is a live read-only observation; "
                "contamination_status must record CONTAMINATED"
            )

        reasons: list[ObservationReasonCode] = []
        if self.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED:
            reasons.append(ObservationReasonCode.NO_PROTECTIVE_ORDER_OBSERVED)
        elif (
            self.normalized_protection_state
            is ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
        ):
            reasons.append(ObservationReasonCode.PROTECTIVE_ORDER_OBSERVED)
        else:
            reasons.append(ObservationReasonCode.STATE_INDETERMINATE)

        if source_says_live:
            reasons.append(ObservationReasonCode.SOURCE_BROKER_AUTHORITATIVE)
        else:
            reasons.append(ObservationReasonCode.SOURCE_NOT_BROKER_AUTHORITATIVE)

        if mismatch:
            reasons.append(ObservationReasonCode.ADAPTER_SOURCE_MISMATCH)

        if self.quantity_relationship in (
            QuantityRelationship.PARTIAL_COVERAGE,
            QuantityRelationship.OVER_COVERAGE,
        ):
            reasons.append(ObservationReasonCode.QUANTITY_COVERAGE_INCOMPLETE)

        if self.data_freshness is not SnapshotFreshness.FRESH:
            reasons.append(ObservationReasonCode.DATA_NOT_FRESH)

        if (
            self.consistency_status
            is ObservationConsistencyStatus.INCONSISTENT_WITHIN_OBSERVATION
        ):
            reasons.append(ObservationReasonCode.WITHIN_OBSERVATION_INCONSISTENCY)

        object.__setattr__(self, "reason_codes", tuple(reasons))
        object.__setattr__(self, "evidence_digest", _compute_evidence_digest(self))

    @property
    def is_broker_authoritative_eligible(self) -> bool:
        """Whether this observation's source *class* could ever count toward
        live grounding. Does not by itself mean it should — the promotion
        rubric (Issue 3) applies the full evidentiary test across many
        observations."""
        return (
            self.evidence_source_class in BROKER_AUTHORITATIVE_SOURCE_CLASSES
            and self.contamination_status
            is ObservationContaminationStatus.NOT_CONTAMINATED
        )

    def to_canonical_dict(self) -> dict[str, Any]:
        """A deterministic, privacy-safe, allowlisted projection.

        Every value here is already an enum member, a bucket, a reference, or
        a bounded scalar — nothing added here can introduce a free-form or
        private field, because every field on this type is already one of
        those things.
        """
        return {
            "observation_id": self.observation_id,
            "run_id": self.run_id,
            "observation_sequence": self.observation_sequence,
            "observed_at_monotonic": self.observed_at_monotonic,
            "observation_clock_source": self.observation_clock_source.value,
            "release_identity": self.release_identity,
            "implementation_digest": self.implementation_digest,
            "checkpoint_version": self.checkpoint_version,
            "account_fingerprint_reference": self.account_fingerprint_reference,
            "broker_adapter_identity": self.broker_adapter_identity.value,
            "broker_session_identity_reference": (
                self.broker_session_identity_reference
            ),
            "symbol_reference": self.symbol_reference,
            "position_reference": self.position_reference,
            "protective_order_reference": self.protective_order_reference,
            "protective_order_type": self.protective_order_type.value,
            "protective_side": self.protective_side.value,
            "quantity_relationship": self.quantity_relationship.value,
            "observed_order_state": self.observed_order_state,
            "broker_reported_timestamp": self.broker_reported_timestamp,
            "normalized_protection_state": self.normalized_protection_state.value,
            "mapping_provenance": self.mapping_provenance.value,
            "evidence_source_class": self.evidence_source_class.value,
            "read_count": self.read_count,
            "write_count": self.write_count,
            "privacy_redaction_status": self.privacy_redaction_status.value,
            "data_freshness": self.data_freshness.value,
            "consistency_status": self.consistency_status.value,
            "contamination_status": self.contamination_status.value,
            "qualification_status": self.qualification_status.value,
            "reason_codes": [code.value for code in self.reason_codes],
        }


def encode_canonical_value(value: Any) -> str:
    """Deterministic, dependency-free scalar/sequence encoding.

    Deliberately hand-rolled rather than built on a generic serialization
    library (matching `src/canonical_source_hash.py`'s own precedent of a
    fixed, explicit byte representation): the digest must mean the same
    thing on every Python version and never drift with a library's
    formatting choices.

    Public (not module-private) because M13 Issue 4's grounding-assessment
    engine needs the identical canonical encoding for its own digest, and
    the repository's own principle is "never build a second opinion" — a
    second hand-rolled encoder would risk two digests silently drifting
    apart. Each caller still supplies its own domain-separation tag when
    hashing, so digests from different modules never collide.
    """
    if value is None:
        return "\x00"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\x1e".join(encode_canonical_value(item) for item in value)
    raise ProtectionObservationError(
        f"unsupported canonical value type: {type(value)!r}"
    )


def canonical_bytes(payload: Mapping[str, Any]) -> bytes:
    """Deterministic byte encoding of a flat-ish mapping. See
    `encode_canonical_value` for why this is public and hand-rolled."""
    fields = "\x1f".join(
        f"{key}\x01{encode_canonical_value(payload[key])}"
        for key in sorted(payload)
    )
    return fields.encode("utf-8")


def _compute_evidence_digest(observation: ProtectionObservationRecord) -> str:
    """SHA-256 over the canonical field encoding, domain-separated.

    Excludes `evidence_digest` itself (not yet set when this runs) and
    `qualification_status` (structural, constant, non-evidentiary).
    """
    payload = observation.to_canonical_dict()
    payload.pop("qualification_status", None)
    return hashlib.sha256(
        _DIGEST_DOMAIN + canonical_bytes(payload)
    ).hexdigest()


def verify_evidence_digest(observation: ProtectionObservationRecord) -> bool:
    """Independently recompute and compare. True unless the record was
    tampered with after construction (which frozen dataclasses already
    resist, but this gives an explicit, testable proof)."""
    return observation.evidence_digest == _compute_evidence_digest(observation)


__all__ = [
    "ProtectionObservationError",
    "OPAQUE_TOKEN_PATTERN",
    "HEX_DIGEST_PATTERN",
    "RELEASE_SHA_PATTERN",
    "EvidenceSourceClass",
    "BROKER_AUTHORITATIVE_SOURCE_CLASSES",
    "TEST_ONLY_SOURCE_CLASSES",
    "AdapterIdentity",
    "LIVE_READ_ONLY_ADAPTER_IDENTITIES",
    "ObservationClockSource",
    "ProtectiveOrderType",
    "ProtectiveSide",
    "QuantityRelationship",
    "PrivacyRedactionStatus",
    "ObservationConsistencyStatus",
    "ObservationContaminationStatus",
    "ObservationQualificationStatus",
    "ObservationReasonCode",
    "ProtectionObservationRecord",
    "verify_evidence_digest",
    "encode_canonical_value",
    "canonical_bytes",
]
