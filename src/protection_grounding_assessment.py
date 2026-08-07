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
ever means `APPROVED_FOR_ACTIVE_PROTECTION`, and neither ever means
`protection_grounded=true` — both are explicit fields on this type, hard-
coded `False` on every instance, independent of what `recommendation` says.

## No mutation, no broker access

Pure function of its arguments. No I/O, no clock read (`generated_at_
monotonic` is caller-injected, exactly like `evaluated_at_monotonic` in
Issue 3), no ledger write, no broker call, no network call, no credential
access.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from src.live_order_state_model import ProtectiveCoverage
from src.protection_observation_ledger import LedgerObservationSummary
from src.protection_observation_model import (
    BROKER_AUTHORITATIVE_SOURCE_CLASSES,
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

_DIGEST_DOMAIN = b"orb-live/protection-grounding-assessment/v1"


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
]
