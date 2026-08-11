"""Protection-grounding assessment engine (M13 Issue 4). ADVISORY ONLY.

Consumes a ledger query result (Issue 2), the versioned promotion policy
(Issue 3), and the caller's current identities, and produces one complete,
immutable, deterministic assessment explaining exactly which observations
were included, which were excluded and why, and what the promotion rubric
concluded about the batch as a whole.

## This module adds one thing Issue 3 does not: an explanation layer

`protection_promotion_policy.evaluate_promotion_eligibility` is, and
remains, the single source of truth for "what does this batch of evidence
mean" — this module calls it exactly once per assessment and never
re-derives its verdict. What this module adds is the missing per-observation
detail: for each candidate observation, whether it was included, excluded,
or merely informational, and the exact reason why — something the
batch-level rubric never needed to expose. Where a per-observation check
already exists in Issue 3 (staleness) or Issue 1 (canonical encoding), this
module reuses the exported, public version rather than re-deriving it
(`is_observation_stale`, `canonical_bytes`) — never build a second opinion.

## The five states this module must never confuse

1. A broker-authoritative observation exists — a fact about one
   `LedgerObservationSummary`.
2. Some observations are technically eligible evidence — this module's
   per-observation `ObservationDispositionReason.INCLUDED` classification.
3. Evidence satisfies the configured rubric sufficiently for review —
   `ProtectionGroundingAssessment.recommendation`, still just an opinion.
4. Human approval occurred — belongs to Issue 6 of the milestone handoff
   (`PROTECTION_PROMOTION_REVIEW.md`), a document a human fills in. Nothing
   here produces that record.
5. Runtime authority changed — `ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES`.
   Nothing in this module, or in Issues 1-5 of this milestone, may write to
   it.

`recommendation` may be `ELIGIBLE_FOR_REVIEW` or `REVIEW_REQUIRED`. Neither
ever means `APPROVED_FOR_ACTIVE_PROTECTION`, and the historical batch
assessment remains hard-coded non-grounding. M20 adds a separate function in
this same reviewed module for one current, validated broker snapshot; only
that narrower result may report the factual state `protection_grounded=true`.
It still cannot promote execution authority or mutate runtime state.

## No mutation, no broker access

Pure function of its arguments. No I/O, no clock read (`generated_at_
monotonic` is caller-injected, exactly like `evaluated_at_monotonic` in
Issue 3), no ledger write, no broker call, no network call, no credential
access.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from types import MappingProxyType, SimpleNamespace
from typing import Any, Mapping, Sequence

from src.broker_snapshot import BrokerSnapshot, validate_snapshot
from src.live_order_state_model import (
    ProtectiveCoverage,
    assess_protective_coverage,
)
from src.protection_observation_ledger import LedgerObservationSummary
from src.protection_observation_model import (
    BROKER_AUTHORITATIVE_SOURCE_CLASSES,
    EvidenceSourceClass,
    HEX_DIGEST_PATTERN,
    ObservationConsistencyStatus,
    ObservationContaminationStatus,
    PrivacyRedactionStatus,
    QuantityRelationship,
    canonical_bytes,
)
from src.protection_promotion_policy import (
    DEFAULT_PROTECTION_PROMOTION_POLICY,
    PromotionOutcome,
    ProtectionPromotionPolicy,
    evaluate_promotion_eligibility,
    is_observation_stale,
)
from src.read_only_broker_state import SnapshotFreshness


class ProtectionGroundingAssessmentError(ValueError):
    """A malformed input to the assessment engine. Never a broker condition."""


ASSESSMENT_ENGINE_VERSION = "m13-grounding-assessment-v1"

AUTHORITATIVE_GROUNDING_VERSION = "m20-authoritative-working-stop-v1"
# Deliberately local to factual grounding. This does not populate
# live_order_state_model.ADAPTER_PERMITTED_ACTIVE_PROTECTION_STATES and grants
# no adapter or automated-action authority. The read-only account reader
# reports only open orders; of those labels, M19 reconciliation identifies
# confirmed as STOP_WORKING. Earlier new/unconfirmed and queued/locating
# acknowledgements remain non-grounding.
AUTHORITATIVE_GROUNDING_ACTIVE_STATES: frozenset[str] = frozenset(
    {"confirmed"}
)
AUTHORITY_CEILING = "ATTENDED_COMMISSIONING_ONLY"

_DIGEST_DOMAIN = b"orb-live/protection-grounding-assessment/v1"
_AUTHORITATIVE_DIGEST_DOMAIN = (
    b"orb-live/authoritative-protection-grounding/v1"
)


# --------------------------------------------------------------------------
# Per-observation disposition.
# --------------------------------------------------------------------------


class ObservationDisposition(str, Enum):
    """What happened to one candidate observation in this assessment."""

    INCLUDED = "INCLUDED"
    EXCLUDED_BLOCKING = "EXCLUDED_BLOCKING"
    EXCLUDED_INFORMATIONAL = "EXCLUDED_INFORMATIONAL"


class ObservationDispositionReason(str, Enum):
    """Why one observation received its disposition.

    Names deliberately mirror `PromotionOutcome` where the underlying check
    is the same one-observation predicate — a reader who knows one
    vocabulary already knows the other. `INCLUDED` and `NOT_PROTECTIVE` have
    no `PromotionOutcome` counterpart because they describe a single
    observation, not a batch verdict.
    """

    INCLUDED = "INCLUDED"
    NOT_PROTECTIVE = "NOT_PROTECTIVE"
    NONZERO_WRITE_EVIDENCE = "NONZERO_WRITE_EVIDENCE"
    PRIVACY_UNVERIFIED = "PRIVACY_UNVERIFIED"
    CONTAMINATED_EVIDENCE = "CONTAMINATED_EVIDENCE"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    RELEASE_MISMATCH = "RELEASE_MISMATCH"
    IMPLEMENTATION_MISMATCH = "IMPLEMENTATION_MISMATCH"
    SOURCE_INELIGIBLE = "SOURCE_INELIGIBLE"
    STALE_EVIDENCE = "STALE_EVIDENCE"
    COVERAGE_INCOMPLETE = "COVERAGE_INCOMPLETE"
    PROTECTION_UNVERIFIABLE = "PROTECTION_UNVERIFIABLE"


#: Reasons that represent a genuine evidence-quality problem — the
#: observation itself is suspect, not merely uninteresting.
BLOCKING_DISPOSITION_REASONS: frozenset[ObservationDispositionReason] = frozenset(
    {
        ObservationDispositionReason.NONZERO_WRITE_EVIDENCE,
        ObservationDispositionReason.PRIVACY_UNVERIFIED,
        ObservationDispositionReason.CONTAMINATED_EVIDENCE,
        ObservationDispositionReason.ACCOUNT_MISMATCH,
        ObservationDispositionReason.RELEASE_MISMATCH,
        ObservationDispositionReason.IMPLEMENTATION_MISMATCH,
        ObservationDispositionReason.SOURCE_INELIGIBLE,
        ObservationDispositionReason.STALE_EVIDENCE,
        ObservationDispositionReason.COVERAGE_INCOMPLETE,
        ObservationDispositionReason.PROTECTION_UNVERIFIABLE,
    }
)

#: Reasons that are simply not relevant to protective evidence — not a
#: quality problem. A flat observation is a legitimate, valid record; per
#: the milestone's decision on Q1, it is never treated as a failure.
INFORMATIONAL_DISPOSITION_REASONS: frozenset[ObservationDispositionReason] = frozenset(
    {ObservationDispositionReason.NOT_PROTECTIVE}
)


def _classify_observation(
    observation: LedgerObservationSummary,
    *,
    policy: ProtectionPromotionPolicy,
    evaluated_at_monotonic: float,
    expected_release_identity: str,
    expected_implementation_digest: str,
    expected_account_fingerprint_reference: str,
) -> ObservationDispositionReason:
    """The same priority order Issue 3 applies batch-wide, applied here to
    one observation, so a reader can trust the two always agree about what
    a check means."""
    if policy.require_zero_write_proof and observation.write_count != 0:
        return ObservationDispositionReason.NONZERO_WRITE_EVIDENCE
    if (
        policy.require_privacy_verification
        and observation.privacy_redaction_status
        is not PrivacyRedactionStatus.VERIFIED_REDACTED
    ):
        return ObservationDispositionReason.PRIVACY_UNVERIFIED
    if (
        policy.reject_contaminated_evidence
        and observation.contamination_status
        is ObservationContaminationStatus.CONTAMINATED
    ):
        return ObservationDispositionReason.CONTAMINATED_EVIDENCE
    if (
        policy.require_account_fingerprint_consistency
        and observation.account_fingerprint_reference
        != expected_account_fingerprint_reference
    ):
        return ObservationDispositionReason.ACCOUNT_MISMATCH
    if (
        policy.require_release_consistency
        and observation.release_identity != expected_release_identity
    ):
        return ObservationDispositionReason.RELEASE_MISMATCH
    if (
        policy.require_implementation_digest_consistency
        and observation.implementation_digest != expected_implementation_digest
    ):
        return ObservationDispositionReason.IMPLEMENTATION_MISMATCH
    if (
        policy.require_broker_authoritative_source
        and observation.evidence_source_class not in BROKER_AUTHORITATIVE_SOURCE_CLASSES
    ):
        return ObservationDispositionReason.SOURCE_INELIGIBLE
    if policy.reject_stale_evidence and is_observation_stale(
        observation, evaluated_at_monotonic=evaluated_at_monotonic, policy=policy
    ):
        return ObservationDispositionReason.STALE_EVIDENCE

    if observation.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED:
        return ObservationDispositionReason.NOT_PROTECTIVE

    if (
        policy.require_exact_quantity_coverage
        and observation.quantity_relationship is not QuantityRelationship.EXACT_COVERAGE
    ):
        return ObservationDispositionReason.COVERAGE_INCOMPLETE

    if (
        observation.consistency_status
        is ObservationConsistencyStatus.INCONSISTENT_WITHIN_OBSERVATION
    ):
        return ObservationDispositionReason.PROTECTION_UNVERIFIABLE

    return ObservationDispositionReason.INCLUDED


def _disposition_for_reason(
    reason: ObservationDispositionReason,
) -> ObservationDisposition:
    if reason is ObservationDispositionReason.INCLUDED:
        return ObservationDisposition.INCLUDED
    if reason in INFORMATIONAL_DISPOSITION_REASONS:
        return ObservationDisposition.EXCLUDED_INFORMATIONAL
    return ObservationDisposition.EXCLUDED_BLOCKING


# --------------------------------------------------------------------------
# Per-dimension analyses.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionAnalysis:
    """A pass/fail summary for one evidentiary dimension across the batch."""

    passed: bool
    checked_count: int
    failing_observation_ids: tuple[str, ...]


@dataclass(frozen=True)
class ActiveStateAnalysis:
    passed: bool
    protective_observation_count: int
    flat_observation_count: int


@dataclass(frozen=True)
class ConflictingEvidenceAnalysis:
    passed: bool
    conflict_detected: bool
    mixed_protective_and_flat: bool


@dataclass(frozen=True)
class IndependenceAnalysis:
    passed: bool
    distinct_run_count: int
    distinct_observation_time_count: int
    minimum_required_runs: int
    minimum_required_observation_times: int


# --------------------------------------------------------------------------
# Operator review context: advisory metadata only.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class OperatorReviewContext:
    """Advisory metadata about a pending or completed human review.

    Structurally incapable of granting authority: there is no `approved`
    field, no activation field, and no protection-grounding field on this
    type, and there never will be — approval is Issue 6's
    `PROTECTION_PROMOTION_REVIEW.md`, a document a human fills in outside
    any code path.
    """

    review_requested: bool = False
    reviewer_reference: str | None = None
    review_note_reference: str | None = None
    review_requested_at_monotonic: float | None = None
    evidence_package_identity: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "reviewer_reference",
            "review_note_reference",
            "evidence_package_identity",
        ):
            value = getattr(self, name)
            if value is not None and HEX_DIGEST_PATTERN.fullmatch(value) is None:
                raise ProtectionGroundingAssessmentError(
                    f"{name} must be a 64-character lowercase SHA-256 hex "
                    "digest reference, or None — never free-form text"
                )
        if self.review_requested_at_monotonic is not None and (
            isinstance(self.review_requested_at_monotonic, bool)
            or not isinstance(self.review_requested_at_monotonic, (int, float))
        ):
            raise ProtectionGroundingAssessmentError(
                "review_requested_at_monotonic must be a number or None"
            )


# --------------------------------------------------------------------------
# The assessment.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProtectionGroundingAssessment:
    """One complete, immutable, deterministic grounding assessment.

    `promotion_permitted` and `protection_grounded` are hard-coded `False`
    on every instance, independent of `recommendation` — this is the actual
    "never grants authority" guarantee for this type, matching Issue 3's
    `PromotionAssessment.promotion_permitted` pattern.
    """

    assessment_id: str
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
    exclusion_reasons: Mapping[str, str]

    source_authority_analysis: DimensionAnalysis
    freshness_analysis: DimensionAnalysis
    account_consistency_analysis: DimensionAnalysis
    release_consistency_analysis: DimensionAnalysis
    implementation_consistency_analysis: DimensionAnalysis
    privacy_analysis: DimensionAnalysis
    zero_write_analysis: DimensionAnalysis
    quantity_coverage_analysis: DimensionAnalysis
    active_state_analysis: ActiveStateAnalysis
    conflicting_evidence_analysis: ConflictingEvidenceAnalysis
    contamination_analysis: DimensionAnalysis
    independence_analysis: IndependenceAnalysis

    distinct_run_count: int
    distinct_observation_time_count: int
    broker_authoritative_observation_count: int
    qualifying_observation_count: int

    recommendation: PromotionOutcome
    reason_codes: tuple[str, ...]
    operator_review_context: OperatorReviewContext | None
    assessment_digest: str

    promotion_permitted: bool = False
    protection_grounded: bool = False

    def __post_init__(self) -> None:
        if self.promotion_permitted:
            raise ProtectionGroundingAssessmentError(
                "a grounding assessment may never assert promotion_permitted"
            )
        if self.protection_grounded:
            raise ProtectionGroundingAssessmentError(
                "a grounding assessment may never assert protection_grounded"
            )


def _dimension(
    *, passed: bool, checked_count: int, failing_ids: Sequence[str]
) -> DimensionAnalysis:
    return DimensionAnalysis(
        passed=passed,
        checked_count=checked_count,
        failing_observation_ids=tuple(sorted(failing_ids)),
    )


def evaluate_protection_grounding(
    observations: Sequence[LedgerObservationSummary],
    *,
    policy: ProtectionPromotionPolicy = DEFAULT_PROTECTION_PROMOTION_POLICY,
    evaluated_at_monotonic: float,
    expected_release_identity: str,
    expected_implementation_digest: str,
    expected_account_fingerprint_reference: str,
    conflict_detected: bool = False,
    operator_presence_attested: bool = False,
    operator_review_context: OperatorReviewContext | None = None,
) -> ProtectionGroundingAssessment:
    """Produce one deterministic grounding assessment.

    `observations` is ordinarily a ledger query result (Issue 2) — e.g.
    `ledger.observations_for_run(run_id)` or a caller-filtered subset of
    `ledger.all_observations()`. Iteration order of the input never affects
    the result: every list is sorted by `observation_id` before use, and
    the underlying batch verdict comes from
    `protection_promotion_policy.evaluate_promotion_eligibility`, which is
    itself insertion-order-independent.
    """
    if not isinstance(policy, ProtectionPromotionPolicy):
        raise ProtectionGroundingAssessmentError(
            "policy must be a ProtectionPromotionPolicy"
        )
    if (
        not isinstance(evaluated_at_monotonic, (int, float))
        or isinstance(evaluated_at_monotonic, bool)
    ):
        raise ProtectionGroundingAssessmentError(
            "evaluated_at_monotonic must be a number"
        )
    for value, name in (
        (expected_release_identity, "expected_release_identity"),
        (expected_implementation_digest, "expected_implementation_digest"),
        (
            expected_account_fingerprint_reference,
            "expected_account_fingerprint_reference",
        ),
    ):
        if not isinstance(value, str) or not value:
            raise ProtectionGroundingAssessmentError(
                f"{name} must be a non-blank string"
            )
    if operator_review_context is not None and not isinstance(
        operator_review_context, OperatorReviewContext
    ):
        raise ProtectionGroundingAssessmentError(
            "operator_review_context must be an OperatorReviewContext or None"
        )

    ordered = tuple(sorted(observations, key=lambda o: o.observation_id))

    batch = evaluate_promotion_eligibility(
        ordered,
        policy=policy,
        evaluated_at_monotonic=evaluated_at_monotonic,
        expected_release_identity=expected_release_identity,
        expected_implementation_digest=expected_implementation_digest,
        expected_account_fingerprint_reference=(
            expected_account_fingerprint_reference
        ),
        conflict_detected=conflict_detected,
        operator_presence_attested=operator_presence_attested,
    )

    dispositions: dict[str, tuple[ObservationDisposition, ObservationDispositionReason]] = {}
    for observation in ordered:
        reason = _classify_observation(
            observation,
            policy=policy,
            evaluated_at_monotonic=evaluated_at_monotonic,
            expected_release_identity=expected_release_identity,
            expected_implementation_digest=expected_implementation_digest,
            expected_account_fingerprint_reference=(
                expected_account_fingerprint_reference
            ),
        )
        dispositions[observation.observation_id] = (
            _disposition_for_reason(reason),
            reason,
        )

    candidate_ids = tuple(o.observation_id for o in ordered)
    included_ids = tuple(
        oid
        for oid in candidate_ids
        if dispositions[oid][0] is ObservationDisposition.INCLUDED
    )
    excluded_ids = tuple(
        oid
        for oid in candidate_ids
        if dispositions[oid][0] is ObservationDisposition.EXCLUDED_BLOCKING
    )
    informational_ids = tuple(
        oid
        for oid in candidate_ids
        if dispositions[oid][0] is ObservationDisposition.EXCLUDED_INFORMATIONAL
    )
    exclusion_reasons = MappingProxyType(
        {
            oid: dispositions[oid][1].value
            for oid in candidate_ids
            if dispositions[oid][0] is not ObservationDisposition.INCLUDED
        }
    )

    def failing(reason: ObservationDispositionReason) -> tuple[str, ...]:
        return tuple(
            sorted(
                oid
                for oid in candidate_ids
                if dispositions[oid][1] is reason
            )
        )

    protective = [
        o
        for o in ordered
        if o.normalized_protection_state is not ProtectiveCoverage.NOT_PROTECTED
    ]
    flat = [
        o
        for o in ordered
        if o.normalized_protection_state is ProtectiveCoverage.NOT_PROTECTED
    ]

    source_authority_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.SOURCE_INELIGIBLE),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.SOURCE_INELIGIBLE),
    )
    freshness_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.STALE_EVIDENCE),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.STALE_EVIDENCE),
    )
    account_consistency_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.ACCOUNT_MISMATCH),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.ACCOUNT_MISMATCH),
    )
    release_consistency_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.RELEASE_MISMATCH),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.RELEASE_MISMATCH),
    )
    implementation_consistency_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.IMPLEMENTATION_MISMATCH),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.IMPLEMENTATION_MISMATCH),
    )
    privacy_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.PRIVACY_UNVERIFIED),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.PRIVACY_UNVERIFIED),
    )
    zero_write_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.NONZERO_WRITE_EVIDENCE),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.NONZERO_WRITE_EVIDENCE),
    )
    quantity_coverage_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.COVERAGE_INCOMPLETE),
        checked_count=len(protective),
        failing_ids=failing(ObservationDispositionReason.COVERAGE_INCOMPLETE),
    )
    contamination_analysis = _dimension(
        passed=not failing(ObservationDispositionReason.CONTAMINATED_EVIDENCE),
        checked_count=len(candidate_ids),
        failing_ids=failing(ObservationDispositionReason.CONTAMINATED_EVIDENCE),
    )

    active_state_analysis = ActiveStateAnalysis(
        passed=bool(protective) or not policy.require_active_protection_state,
        protective_observation_count=len(protective),
        flat_observation_count=len(flat),
    )
    mixed = bool(protective) and bool(flat)
    conflicting_evidence_analysis = ConflictingEvidenceAnalysis(
        passed=not conflict_detected and not (mixed and policy.reject_contradictory_evidence),
        conflict_detected=conflict_detected,
        mixed_protective_and_flat=mixed,
    )
    distinct_runs = {o.run_id for o in protective}
    distinct_times = {o.observed_at_monotonic for o in protective}
    independence_analysis = IndependenceAnalysis(
        passed=(
            len(distinct_runs) >= policy.minimum_distinct_eligible_live_runs
            and len(distinct_times) >= policy.minimum_distinct_observation_times
        ),
        distinct_run_count=len(distinct_runs),
        distinct_observation_time_count=len(distinct_times),
        minimum_required_runs=policy.minimum_distinct_eligible_live_runs,
        minimum_required_observation_times=policy.minimum_distinct_observation_times,
    )

    broker_authoritative_count = sum(
        1
        for o in ordered
        if o.evidence_source_class in BROKER_AUTHORITATIVE_SOURCE_CLASSES
    )

    reason_codes = tuple(
        sorted(
            {batch.outcome.value}
            | {reason.value for _, reason in dispositions.values()}
        )
    )

    payload = {
        "assessment_version": ASSESSMENT_ENGINE_VERSION,
        "policy_version": policy.policy_version,
        "release_identity": expected_release_identity,
        "implementation_digest": expected_implementation_digest,
        "account_fingerprint_reference": expected_account_fingerprint_reference,
        "included_observation_ids": list(included_ids),
        "excluded_observation_ids": list(excluded_ids),
        "informational_observation_ids": list(informational_ids),
        "exclusion_reasons": [
            f"{oid}={exclusion_reasons[oid]}" for oid in sorted(exclusion_reasons)
        ],
        "recommendation": batch.outcome.value,
        "reason_codes": list(reason_codes),
    }
    assessment_digest = hashlib.sha256(
        _DIGEST_DOMAIN + canonical_bytes(payload)
    ).hexdigest()

    return ProtectionGroundingAssessment(
        assessment_id=assessment_digest,
        assessment_version=ASSESSMENT_ENGINE_VERSION,
        generated_at_monotonic=float(evaluated_at_monotonic),
        policy_version=policy.policy_version,
        release_identity=expected_release_identity,
        implementation_digest=expected_implementation_digest,
        account_fingerprint_reference=expected_account_fingerprint_reference,
        candidate_observation_ids=candidate_ids,
        included_observation_ids=included_ids,
        excluded_observation_ids=excluded_ids,
        informational_observation_ids=informational_ids,
        exclusion_reasons=exclusion_reasons,
        source_authority_analysis=source_authority_analysis,
        freshness_analysis=freshness_analysis,
        account_consistency_analysis=account_consistency_analysis,
        release_consistency_analysis=release_consistency_analysis,
        implementation_consistency_analysis=implementation_consistency_analysis,
        privacy_analysis=privacy_analysis,
        zero_write_analysis=zero_write_analysis,
        quantity_coverage_analysis=quantity_coverage_analysis,
        active_state_analysis=active_state_analysis,
        conflicting_evidence_analysis=conflicting_evidence_analysis,
        contamination_analysis=contamination_analysis,
        independence_analysis=independence_analysis,
        distinct_run_count=len(distinct_runs),
        distinct_observation_time_count=len(distinct_times),
        broker_authoritative_observation_count=broker_authoritative_count,
        qualifying_observation_count=len(included_ids),
        recommendation=batch.outcome,
        reason_codes=reason_codes,
        operator_review_context=operator_review_context,
        assessment_digest=assessment_digest,
    )



# --------------------------------------------------------------------------
# M20: broker-authoritative runtime grounding.
# --------------------------------------------------------------------------


class AuthoritativeGroundingOutcome(str, Enum):
    GROUNDED = "GROUNDED"
    NOT_GROUNDED = "NOT_GROUNDED"
    NOT_REQUIRED = "NOT_REQUIRED"


class AuthoritativeGroundingReason(str, Enum):
    GROUNDED = "GROUNDED"
    POSITION_FLAT = "POSITION_FLAT"
    SNAPSHOT_INVALID = "SNAPSHOT_INVALID"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    ACCOUNT_MISMATCH = "ACCOUNT_MISMATCH"
    SOURCE_MISMATCH = "SOURCE_MISMATCH"
    IMPLEMENTATION_MISMATCH = "IMPLEMENTATION_MISMATCH"
    EVIDENCE_SOURCE_INELIGIBLE = "EVIDENCE_SOURCE_INELIGIBLE"
    POSITION_AMBIGUOUS = "POSITION_AMBIGUOUS"
    POSITION_IDENTITY_MISMATCH = "POSITION_IDENTITY_MISMATCH"
    POSITION_QUANTITY_MISMATCH = "POSITION_QUANTITY_MISMATCH"
    BROKER_ORDER_INVALID = "BROKER_ORDER_INVALID"
    STOP_NOT_FOUND = "STOP_NOT_FOUND"
    STOP_IDENTITY_MISMATCH = "STOP_IDENTITY_MISMATCH"
    STOP_STATE_NOT_ACTIVE = "STOP_STATE_NOT_ACTIVE"
    STOP_QUANTITY_MISMATCH = "STOP_QUANTITY_MISMATCH"
    STOP_PRICE_MISMATCH = "STOP_PRICE_MISMATCH"
    CONFLICTING_ACTIVE_STOPS = "CONFLICTING_ACTIVE_STOPS"
    UNSUPPORTED_REPLACEMENT_LINEAGE = "UNSUPPORTED_REPLACEMENT_LINEAGE"


@dataclass(frozen=True)
class AuthoritativeProtectionGroundingRequest:
    source_candidate_id: str
    source_handoff_id: str
    source_protection_plan_id: str
    symbol: str
    expected_stop_order_id: str
    expected_position_quantity: Decimal
    expected_stop_price: Decimal
    release_identity: str
    implementation_digest: str
    account_fingerprint_reference: str
    evidence_source_class: EvidenceSourceClass

    def __post_init__(self) -> None:
        for name in (
            "source_candidate_id",
            "source_handoff_id",
            "source_protection_plan_id",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or HEX_DIGEST_PATTERN.fullmatch(value) is None
            ):
                raise ProtectionGroundingAssessmentError(
                    f"{name} must be a canonical SHA-256 identity"
                )
        if (
            not isinstance(self.expected_stop_order_id, str)
            or not self.expected_stop_order_id.strip()
        ):
            raise ProtectionGroundingAssessmentError(
                "expected_stop_order_id must be a non-blank string"
            )
        if (
            not isinstance(self.symbol, str)
            or not self.symbol.strip()
            or self.symbol != self.symbol.strip().upper()
        ):
            raise ProtectionGroundingAssessmentError(
                "symbol must be a normalized uppercase symbol"
            )
        for name in ("expected_position_quantity", "expected_stop_price"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
                raise ProtectionGroundingAssessmentError(
                    f"{name} must be a positive finite Decimal"
                )
        if (
            not isinstance(self.release_identity, str)
            or len(self.release_identity) != 40
            or any(ch not in "0123456789abcdef" for ch in self.release_identity)
        ):
            raise ProtectionGroundingAssessmentError(
                "release_identity must be a lowercase 40-character SHA"
            )
        for name in (
            "implementation_digest",
            "account_fingerprint_reference",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or HEX_DIGEST_PATTERN.fullmatch(value) is None
            ):
                raise ProtectionGroundingAssessmentError(
                    f"{name} must be a lowercase SHA-256 digest"
                )
        if not isinstance(self.evidence_source_class, EvidenceSourceClass):
            raise ProtectionGroundingAssessmentError(
                "evidence_source_class must be an EvidenceSourceClass"
            )


@dataclass(frozen=True)
class AuthoritativeProtectionGroundingAssessment:
    assessment_id: str
    assessment_digest: str
    assessment_version: str
    assessed_at: datetime
    evidence_source_class: EvidenceSourceClass
    source_candidate_id: str
    source_handoff_id: str
    source_protection_plan_id: str
    release_identity: str
    implementation_digest: str
    account_fingerprint_reference: str
    broker_snapshot_identity: str
    broker_snapshot_timestamp: datetime | None
    position_symbol: str
    authoritative_position_quantity: str
    protective_stop_reference: str | None
    stop_state: str | None
    protective_quantity: str | None
    stop_price: str | None
    expected_stop_price: str
    accepted_stop_states: tuple[str, ...]
    coverage_assessment: str
    outcome: AuthoritativeGroundingOutcome
    reason_codes: tuple[AuthoritativeGroundingReason, ...]
    promotion_permitted: bool
    protection_grounded: bool
    broker_write_count: int
    execution_authorized: bool
    activation_approved: bool
    permits_automated_action: bool
    live_ready: bool
    unattended_ready: bool
    authority_ceiling: str

    def __post_init__(self) -> None:
        if self.assessment_id != self.assessment_digest:
            raise ProtectionGroundingAssessmentError(
                "assessment identity must equal its canonical digest"
            )
        if self.assessment_digest != compute_authoritative_grounding_digest(self):
            raise ProtectionGroundingAssessmentError(
                "authoritative grounding digest does not reconstruct"
            )
        if self.assessed_at.tzinfo is None:
            raise ProtectionGroundingAssessmentError(
                "assessed_at must include a timezone"
            )
        expected_grounded = (
            self.outcome is AuthoritativeGroundingOutcome.GROUNDED
            and self.evidence_source_class
            in BROKER_AUTHORITATIVE_SOURCE_CLASSES
            and self.reason_codes == (AuthoritativeGroundingReason.GROUNDED,)
        )
        if self.protection_grounded is not expected_grounded:
            raise ProtectionGroundingAssessmentError(
                "protection_grounded does not match authoritative evidence"
            )
        if (
            self.promotion_permitted
            or self.broker_write_count != 0
            or self.execution_authorized
            or self.activation_approved
            or self.permits_automated_action
            or self.live_ready
            or self.unattended_ready
        ):
            raise ProtectionGroundingAssessmentError(
                "grounding evidence cannot grant execution authority"
            )
        if self.authority_ceiling != AUTHORITY_CEILING:
            raise ProtectionGroundingAssessmentError("authority ceiling drift")


def _authoritative_result_payload(value: Any) -> dict[str, object]:
    return {
        "assessment_version": value.assessment_version,
        "assessed_at": value.assessed_at.isoformat(),
        "evidence_source_class": value.evidence_source_class.value,
        "source_candidate_id": value.source_candidate_id,
        "source_handoff_id": value.source_handoff_id,
        "source_protection_plan_id": value.source_protection_plan_id,
        "release_identity": value.release_identity,
        "implementation_digest": value.implementation_digest,
        "account_fingerprint_reference": value.account_fingerprint_reference,
        "broker_snapshot_identity": value.broker_snapshot_identity,
        "broker_snapshot_timestamp": (
            None
            if value.broker_snapshot_timestamp is None
            else value.broker_snapshot_timestamp.isoformat()
        ),
        "position_symbol": value.position_symbol,
        "authoritative_position_quantity": value.authoritative_position_quantity,
        "protective_stop_reference": value.protective_stop_reference,
        "stop_state": value.stop_state,
        "protective_quantity": value.protective_quantity,
        "stop_price": value.stop_price,
        "expected_stop_price": value.expected_stop_price,
        "accepted_stop_states": list(value.accepted_stop_states),
        "coverage_assessment": value.coverage_assessment,
        "outcome": value.outcome.value,
        "reason_codes": [reason.value for reason in value.reason_codes],
        "promotion_permitted": value.promotion_permitted,
        "protection_grounded": value.protection_grounded,
        "broker_write_count": value.broker_write_count,
        "execution_authorized": value.execution_authorized,
        "activation_approved": value.activation_approved,
        "permits_automated_action": value.permits_automated_action,
        "live_ready": value.live_ready,
        "unattended_ready": value.unattended_ready,
        "authority_ceiling": value.authority_ceiling,
    }


def compute_authoritative_grounding_digest(
    value: AuthoritativeProtectionGroundingAssessment,
) -> str:
    if not isinstance(value, AuthoritativeProtectionGroundingAssessment):
        raise TypeError(
            "value must be an AuthoritativeProtectionGroundingAssessment"
        )
    return hashlib.sha256(
        _AUTHORITATIVE_DIGEST_DOMAIN
        + canonical_bytes(_authoritative_result_payload(value))
    ).hexdigest()


def _safe_order_reference(order_id: str) -> str:
    return hashlib.sha256(
        _AUTHORITATIVE_DIGEST_DOMAIN
        + b"\0protective-order\0"
        + order_id.encode("utf-8")
    ).hexdigest()


def evaluate_authoritative_working_stop_grounding(
    request: AuthoritativeProtectionGroundingRequest,
    *,
    snapshot: BrokerSnapshot,
    assessed_at: datetime,
    expected_release_identity: str,
    expected_implementation_digest: str,
    expected_account_fingerprint_reference: str,
) -> AuthoritativeProtectionGroundingAssessment:
    """Assess current working-stop coverage from one authoritative snapshot.

    The function is pure over already-read evidence. It performs no broker,
    network, ledger, or clock access. A result can ground protection only for
    the explicit live read-only evidence source; fixtures can exercise every
    branch but remain ineligible.
    """
    if not isinstance(request, AuthoritativeProtectionGroundingRequest):
        raise ProtectionGroundingAssessmentError(
            "request must be an AuthoritativeProtectionGroundingRequest"
        )
    if not isinstance(snapshot, BrokerSnapshot):
        raise ProtectionGroundingAssessmentError(
            "snapshot must be a BrokerSnapshot"
        )
    if not isinstance(assessed_at, datetime) or assessed_at.tzinfo is None:
        raise ProtectionGroundingAssessmentError(
            "assessed_at must include a timezone"
        )

    reasons: list[AuthoritativeGroundingReason] = []
    try:
        validation = validate_snapshot(snapshot, evaluated_at=assessed_at)
        snapshot_identity = validation.snapshot_digest
        snapshot_timestamp = snapshot.metadata.completed_at
    except (AttributeError, InvalidOperation, TypeError, ValueError):
        validation = None
        snapshot_timestamp = None
        snapshot_identity = hashlib.sha256(
            _AUTHORITATIVE_DIGEST_DOMAIN
            + canonical_bytes(
                {
                    "source_candidate_id": request.source_candidate_id,
                    "source_protection_plan_id": request.source_protection_plan_id,
                    "assessed_at": assessed_at.isoformat(),
                    "invalid_snapshot": True,
                }
            )
        ).hexdigest()
        reasons.append(AuthoritativeGroundingReason.SNAPSHOT_INVALID)

    if validation is not None and not validation.valid:
        reasons.append(
            AuthoritativeGroundingReason.SNAPSHOT_STALE
            if validation.result_code == "stale_snapshot"
            else AuthoritativeGroundingReason.SNAPSHOT_INVALID
        )

    if request.release_identity != expected_release_identity:
        reasons.append(AuthoritativeGroundingReason.SOURCE_MISMATCH)
    if request.implementation_digest != expected_implementation_digest:
        reasons.append(AuthoritativeGroundingReason.IMPLEMENTATION_MISMATCH)
    if (
        request.account_fingerprint_reference
        != expected_account_fingerprint_reference
        or snapshot.metadata.account_fingerprint
        != expected_account_fingerprint_reference
    ):
        reasons.append(AuthoritativeGroundingReason.ACCOUNT_MISMATCH)

    state = snapshot.account_state
    positions = tuple(
        position
        for position in state.positions
        if str(position.symbol).upper() == request.symbol
    )
    position_quantity = Decimal("0")
    position_flat = False
    if len(positions) > 1:
        reasons.append(AuthoritativeGroundingReason.POSITION_AMBIGUOUS)
    elif not positions:
        position_flat = True
    else:
        position = positions[0]
        try:
            position_quantity = Decimal(str(position.quantity))
        except (InvalidOperation, TypeError, ValueError):
            reasons.append(
                AuthoritativeGroundingReason.POSITION_QUANTITY_MISMATCH
            )
        else:
            if not position_quantity.is_finite():
                reasons.append(
                    AuthoritativeGroundingReason.POSITION_QUANTITY_MISMATCH
                )
            elif position_quantity == 0:
                position_flat = True
            elif (
                position_quantity < 0
                or str(position.position_type).lower() != "long"
            ):
                reasons.append(
                    AuthoritativeGroundingReason.POSITION_IDENTITY_MISMATCH
                )
            elif position_quantity != request.expected_position_quantity:
                reasons.append(
                    AuthoritativeGroundingReason.POSITION_QUANTITY_MISMATCH
                )

    stop = None
    stop_reference = None
    stop_state = None
    protective_quantity = None
    stop_price = None
    coverage_value = "NOT_EVALUATED"

    if position_flat and not reasons:
        outcome = AuthoritativeGroundingOutcome.NOT_REQUIRED
        reasons.append(AuthoritativeGroundingReason.POSITION_FLAT)
    else:
        orders = tuple(state.open_orders)
        matching_ids = tuple(
            order
            for order in orders
            if order.order_id == request.expected_stop_order_id
        )
        active_protective = tuple(
            order
            for order in orders
            if str(order.symbol).upper() == request.symbol
            and str(order.side).lower() == "sell"
            and str(order.trigger).lower() == "stop"
        )
        if len(active_protective) > 1:
            reasons.append(
                AuthoritativeGroundingReason.CONFLICTING_ACTIVE_STOPS
            )
        if len(matching_ids) != 1:
            reasons.append(AuthoritativeGroundingReason.STOP_NOT_FOUND)
        else:
            stop = matching_ids[0]
            stop_reference = _safe_order_reference(stop.order_id)
            stop_state = str(stop.state).strip().lower()
            protective_quantity = (
                None if stop.quantity is None else format(stop.quantity, "f")
            )
            stop_price = (
                None
                if stop.stop_price is None
                else format(stop.stop_price, "f")
            )
            if (
                str(stop.symbol).upper() != request.symbol
                or str(stop.side).lower() != "sell"
                or str(stop.trigger).lower() != "stop"
                or str(stop.order_type).lower() != "stop_market"
                or str(stop.placed_agent).lower() != "agentic"
            ):
                reasons.append(
                    AuthoritativeGroundingReason.STOP_IDENTITY_MISMATCH
                )
            if stop_state == "replaced":
                reasons.append(
                    AuthoritativeGroundingReason
                    .UNSUPPORTED_REPLACEMENT_LINEAGE
                )
            elif stop_state not in AUTHORITATIVE_GROUNDING_ACTIVE_STATES:
                reasons.append(
                    AuthoritativeGroundingReason.STOP_STATE_NOT_ACTIVE
                )
            if (
                stop.stop_price is None
                or stop.stop_price != request.expected_stop_price
            ):
                reasons.append(
                    AuthoritativeGroundingReason.STOP_PRICE_MISMATCH
                )
            try:
                coverage = assess_protective_coverage(
                    state=stop.state,
                    side=stop.side,
                    trigger=stop.trigger,
                    stop_price=stop.stop_price,
                    stop_submitted_quantity=stop.quantity,
                    stop_cumulative_quantity=stop.cumulative_quantity,
                    verified_remaining_long_quantity=position_quantity,
                    permitted_active_protection_states=(
                        AUTHORITATIVE_GROUNDING_ACTIVE_STATES
                    ),
                )
            except (InvalidOperation, TypeError, ValueError):
                coverage = None
                reasons.append(
                    AuthoritativeGroundingReason.BROKER_ORDER_INVALID
                )
            if coverage is not None:
                coverage_value = coverage.coverage.value
            if (
                coverage is not None
                and coverage.coverage
                is not ProtectiveCoverage.VERIFIED_ACTIVE_PROTECTION
            ):
                if any(
                    gap.value
                    in {
                        "QUANTITY_DISAGREEMENT",
                        "MALFORMED_QUANTITY",
                        "NO_POSITION_TO_PROTECT",
                    }
                    for gap in coverage.gaps
                ):
                    reasons.append(
                        AuthoritativeGroundingReason.STOP_QUANTITY_MISMATCH
                    )
                elif not any(
                    reason
                    in {
                        AuthoritativeGroundingReason.STOP_IDENTITY_MISMATCH,
                        AuthoritativeGroundingReason.STOP_STATE_NOT_ACTIVE,
                        AuthoritativeGroundingReason.STOP_PRICE_MISMATCH,
                    }
                    for reason in reasons
                ):
                    reasons.append(
                        AuthoritativeGroundingReason.STOP_STATE_NOT_ACTIVE
                    )

        if (
            request.evidence_source_class
            not in BROKER_AUTHORITATIVE_SOURCE_CLASSES
        ):
            reasons.append(
                AuthoritativeGroundingReason.EVIDENCE_SOURCE_INELIGIBLE
            )

        unique_reasons = tuple(dict.fromkeys(reasons))
        if not unique_reasons:
            outcome = AuthoritativeGroundingOutcome.GROUNDED
            unique_reasons = (AuthoritativeGroundingReason.GROUNDED,)
        else:
            outcome = AuthoritativeGroundingOutcome.NOT_GROUNDED
        reasons = list(unique_reasons)

    grounded = outcome is AuthoritativeGroundingOutcome.GROUNDED
    values = {
        "assessment_version": AUTHORITATIVE_GROUNDING_VERSION,
        "assessed_at": assessed_at,
        "evidence_source_class": request.evidence_source_class,
        "source_candidate_id": request.source_candidate_id,
        "source_handoff_id": request.source_handoff_id,
        "source_protection_plan_id": request.source_protection_plan_id,
        "release_identity": request.release_identity,
        "implementation_digest": request.implementation_digest,
        "account_fingerprint_reference": request.account_fingerprint_reference,
        "broker_snapshot_identity": snapshot_identity,
        "broker_snapshot_timestamp": snapshot_timestamp,
        "position_symbol": request.symbol,
        "authoritative_position_quantity": format(position_quantity, "f"),
        "protective_stop_reference": stop_reference,
        "stop_state": stop_state,
        "protective_quantity": protective_quantity,
        "stop_price": stop_price,
        "expected_stop_price": format(request.expected_stop_price, "f"),
        "accepted_stop_states": tuple(
            sorted(AUTHORITATIVE_GROUNDING_ACTIVE_STATES)
        ),
        "coverage_assessment": coverage_value,
        "outcome": outcome,
        "reason_codes": tuple(reasons),
        "promotion_permitted": False,
        "protection_grounded": grounded,
        "broker_write_count": 0,
        "execution_authorized": False,
        "activation_approved": False,
        "permits_automated_action": False,
        "live_ready": False,
        "unattended_ready": False,
        "authority_ceiling": AUTHORITY_CEILING,
    }
    placeholder = SimpleNamespace(**values)
    digest = hashlib.sha256(
        _AUTHORITATIVE_DIGEST_DOMAIN
        + canonical_bytes(_authoritative_result_payload(placeholder))
    ).hexdigest()
    return AuthoritativeProtectionGroundingAssessment(
        assessment_id=digest,
        assessment_digest=digest,
        **values,
    )

__all__ = [
    "ProtectionGroundingAssessmentError",
    "ASSESSMENT_ENGINE_VERSION",
    "ObservationDisposition",
    "ObservationDispositionReason",
    "BLOCKING_DISPOSITION_REASONS",
    "INFORMATIONAL_DISPOSITION_REASONS",
    "DimensionAnalysis",
    "ActiveStateAnalysis",
    "ConflictingEvidenceAnalysis",
    "IndependenceAnalysis",
    "OperatorReviewContext",
    "ProtectionGroundingAssessment",
    "evaluate_protection_grounding",
    "AUTHORITATIVE_GROUNDING_VERSION",
    "AUTHORITATIVE_GROUNDING_ACTIVE_STATES",
    "AuthoritativeGroundingOutcome",
    "AuthoritativeGroundingReason",
    "AuthoritativeProtectionGroundingRequest",
    "AuthoritativeProtectionGroundingAssessment",
    "compute_authoritative_grounding_digest",
    "evaluate_authoritative_working_stop_grounding",
]
